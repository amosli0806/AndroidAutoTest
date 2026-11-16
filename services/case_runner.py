# services/case_runner.py
"""用例执行器：不依赖 Qt 的纯执行层。

为什么单独抽出来：
    原来这套循环写在 `controllers/execution_controller.py::ExecutionWorker` 里，
    Worker 是 QObject、结果靠 pyqtSignal 回投，MCP 的 asyncio 进程没法直接复用。
    这里只保留「循环 / 重试 / 数据驱动 / 失败上下文」这些业务逻辑，
    GUI 的 ExecutionWorker 变成薄适配器，MCP 用 asyncio.to_thread 调同一个入口。

约定：
    * 不 import PyQt6 / views / controllers；
    * 进度通过 `on_progress(message, level)` 回调，GUI 适配器再转成信号；
    * 中止通过 `threading.Event`，每个用例、每个步骤开始前检查；
    * 返回结构化 `RunResult`，MCP 不需要解析日志文本。
"""
from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

from models.execution_model import FailureContext
from utils.test_data import read_file
from utils.vars import clear_row_values, set_row_values

ProgressCallback = Optional[Callable[[str, str], None]]


@dataclass
class StepResult:
    index: int
    name: str
    type: str
    ok: bool
    message: str = ""
    screenshot: Optional[str] = None


@dataclass
class CaseResult:
    case_id: str
    case_name: str
    ok: bool
    steps: list[StepResult] = field(default_factory=list)
    duration_ms: int = 0
    error: str = ""
    data_group: str = ""
    skipped: bool = False


@dataclass
class RunResult:
    cases: list[CaseResult] = field(default_factory=list)
    aborted: bool = False


class CaseRunner:
    """跑一批用例；同一个实例只执行一次 `run()`。"""

    def __init__(
        self,
        case_ids,
        project_model,
        step_model,
        device_service,
        exec_model=None,
        *,
        loop_count: int = 1,
        stop_on_fail: bool = False,
        retry_count: int = 0,
        retry_delay: int = 0,
        retry_only_timeout: bool = True,
        on_progress: ProgressCallback = None,
        abort_event: Optional[threading.Event] = None,
    ):
        self.case_ids = list(case_ids or [])
        self.project_model = project_model
        self.step_model = step_model
        self.device = device_service
        self.exec_model = exec_model
        self.loop_count = max(1, int(loop_count or 1))
        self.stop_on_fail = bool(stop_on_fail)
        self.retry_count = max(0, int(retry_count or 0))
        self.retry_delay = max(0, int(retry_delay or 0))
        self.retry_only_timeout = bool(retry_only_timeout)
        self.on_progress = on_progress
        self.abort_event = abort_event or threading.Event()
        # stop_on_fail 触发的中止是内部状态，不能和「用户点停止」混为一谈
        self._stop_on_fail_abort = False

    # ------------------------------------------------------------------
    def _emit(self, message: str, level: str = "info"):
        if self.on_progress is not None:
            try:
                self.on_progress(message, level)
            except Exception:
                # 进度回调失败绝不能打断执行
                pass

    def _should_abort(self) -> bool:
        return self._stop_on_fail_abort or self.abort_event.is_set()

    @property
    def aborted_by_user(self) -> bool:
        return self.abort_event.is_set()

    # ------------------------------------------------------------------
    def run(self) -> RunResult:
        result = RunResult()

        for loop_idx in range(self.loop_count):
            if self._should_abort():
                break
            if loop_idx > 0:
                self._emit(f"--- 第 {loop_idx + 1} 轮执行 ---", "info")

            for idx, case_id in enumerate(self.case_ids):
                if self._should_abort():
                    break

                case_node = self.project_model.get_node_by_id(case_id)
                case_name = case_node.name if case_node else f"用例{case_id}"
                self._emit(
                    f"▶ [{idx + 1}/{len(self.case_ids)}] 用例 \"{case_name}\" 开始执行",
                    "info",
                )

                # 数据驱动：绑了数据文件就以数据行为准、忽略循环次数（只有第 1 轮跑）。
                # 读取失败按该用例失败处理，不拖垮整轮。
                try:
                    data_rows = self._load_data_rows(case_node)
                except Exception as e:
                    error_msg = self._format_error(e)
                    self._emit(f"  ✗ 数据文件加载失败：{error_msg}", "error")
                    self._emit(f"  ✗ 用例 \"{case_name}\" 执行失败", "error")
                    result.cases.append(CaseResult(
                        case_id=case_id, case_name=case_name, ok=False,
                        error=error_msg, duration_ms=0,
                    ))
                    if self.stop_on_fail:
                        self._emit("  ⛔ 因失败停止标志，终止后续执行", "warning")
                        self._stop_on_fail_abort = True
                        break
                    continue

                if data_rows is None:
                    result.cases.append(self._run_case(case_id, case_name, loop_idx, None))
                elif loop_idx > 0:
                    continue  # 数据驱动只在第 1 轮跑
                else:
                    total_groups = len(data_rows)
                    for g, row in enumerate(data_rows, 1):
                        if self._should_abort():
                            break
                        self._emit(f"  · 数据 {g}/{total_groups}", "info")
                        set_row_values(row)
                        try:
                            result.cases.append(self._run_case(
                                case_id, case_name, loop_idx,
                                f"[数据 {g}/{total_groups}]",
                            ))
                        finally:
                            clear_row_values()

        if self.aborted_by_user:
            self._emit("⏹ 已按你的请求停止本轮执行", "warning")
        result.aborted = self._should_abort()
        return result

    # ------------------------------------------------------------------
    def _should_retry(self, error_msg: str) -> bool:
        """这个失败要不要重试。仅超时类时：等待/连接超时重试；点不到元素不算（白等）。"""
        if not self.retry_only_timeout:
            return True
        return ("超时" in error_msg) or ("timeout" in error_msg.lower())

    def _load_data_rows(self, case_node):
        """读该用例的数据文件 → [{表头: 值}, ...]；没绑数据文件返回 None。"""
        path = (getattr(case_node, "data_file", "") or "").strip() if case_node else ""
        if not path:
            return None
        if not os.path.exists(path):
            raise ValueError(f"数据文件不存在：{path}")
        rows = read_file(path)
        if not rows:
            raise ValueError("数据文件里没有数据行")
        return rows

    def _format_error(self, e) -> str:
        # 如果是 DeviceService 抛出的友好错误（RuntimeError），直接返回其消息
        if isinstance(e, RuntimeError):
            return str(e)

        error_str = str(e)
        if hasattr(e, "args") and len(e.args) > 0:
            arg = e.args[0]
            if isinstance(arg, tuple) and len(arg) >= 3:
                loc_dict = arg[2]
                if isinstance(loc_dict, dict):
                    conditions = []
                    if "text" in loc_dict and loc_dict["text"]:
                        conditions.append(f"文本='{loc_dict['text']}'")
                    if "resourceId" in loc_dict and loc_dict["resourceId"]:
                        conditions.append(f"资源ID='{loc_dict['resourceId']}'")
                    if "description" in loc_dict and loc_dict["description"]:
                        conditions.append(f"描述='{loc_dict['description']}'")
                    if "className" in loc_dict and loc_dict["className"]:
                        conditions.append(f"类名='{loc_dict['className']}'")
                    if "xpath" in loc_dict and loc_dict["xpath"]:
                        conditions.append(f"XPath='{loc_dict['xpath']}'")
                    if "textContains" in loc_dict and loc_dict["textContains"]:
                        conditions.append(f"文本包含='{loc_dict['textContains']}'")
                    if conditions:
                        condition_str = "，".join(conditions)
                        return f"未找到元素：{condition_str}"
            elif isinstance(arg, dict):
                data = arg.get("data", "")
                method = arg.get("method", "")
                if data:
                    if "timeout" in str(arg).lower() or "wait" in str(arg).lower():
                        return f"等待超时，未找到元素：{data}"
                    return f"操作失败：{data}"
                elif method:
                    return f"{method} 操作失败"
        selector_match = re.search(r"Selector\s*\[([^\]]*)\]", error_str)
        if selector_match:
            selector_text = selector_match.group(0)
            if "timeout" in error_str.lower() or "wait" in error_str.lower():
                return f"等待超时，未找到元素：{selector_text}"
            return f"未找到元素：{selector_text}"
        if "timeout" in error_str.lower():
            return f"等待超时：{error_str}"
        elif "not found" in error_str.lower() or "no such element" in error_str.lower():
            return f"未找到元素：{error_str}"
        return error_str

    # ------------------------------------------------------------------
    def _run_case(self, case_id, case_name, loop_idx, group_label) -> CaseResult:
        """跑一个用例（或数据驱动里的某一组）。group_label 如 '[数据 2/3]'，None 表示普通用例。"""
        started = time.time()
        display_name = case_name + ((" " + group_label) if group_label else "")
        case_result = CaseResult(
            case_id=case_id,
            case_name=display_name,
            ok=False,
            data_group=group_label or "",
        )

        # 用显式标志记录本用例结果，避免依赖异常穿透 —— 只要有一处 except
        # 吞掉了异常，「用例通过」就会被错误地打出来。
        case_failed = False

        try:
            steps = self.step_model.get_steps_for_case(case_id)
            if not steps:
                self._emit(f"  ⚠ 用例 \"{display_name}\" 没有步骤，跳过", "warning")
                case_result.skipped = True
                return case_result

            for step_idx, step in enumerate(steps, 1):
                if self._should_abort():
                    break
                attempt = 0
                step_failed = False
                # 下一步传给 perform：若它是等待类，就跳过本步的固定间隔，
                # 否则「点击 + 等待 2 秒」会变成 2 + 2 = 4 秒。
                next_step = steps[step_idx] if step_idx < len(steps) else None
                while True:
                    attempt += 1
                    try:
                        self.device.perform(step, next_step=next_step)

                        # 变量替换是「未定义就原样保留」，这里必须点出来，
                        # 否则用户只会在失败信息里看到一个没被替换的 ${xxx}。
                        missing_vars = getattr(self.device, "last_var_missing", None)
                        if missing_vars:
                            names = "、".join(f"${{{m}}}" for m in missing_vars)
                            self._emit(
                                f"  ⚠ 步骤 {step_idx} 有未定义变量：{names}"
                                f"（已按原样保留，请在「设置 → 变量」里补上）",
                                "warning",
                            )

                        suspect = getattr(self.device, "last_click_suspect", None)
                        if suspect:
                            cx, cy, dv = suspect
                            hint = (getattr(self.device, "last_click_hint", "") or "").strip()
                            extra = (f"；{hint}" if hint else
                                     " —— 建议核对定位值，或用「应用可视化」看下当时界面")
                            self._emit(
                                f"  ⚠ 步骤 {step_idx} 点击 ({cx},{cy}) 后界面几乎没变化"
                                f"（差异 {dv}），可能没点中目标{extra}",
                                "warning",
                            )

                        if step.type == "assert":
                            self._emit(f"  ✅ 断言：{step.name} 通过", "success")
                        case_result.steps.append(StepResult(
                            index=step_idx,
                            name=step.name or step.type,
                            type=step.type,
                            ok=True,
                        ))
                        break
                    except Exception as e:
                        error_msg = self._format_error(e)
                        # 失败重试：仅超时类（或所有失败）才重试；点不到元素重试
                        # 多半还是点不到，白等，默认不重试。
                        if attempt <= self.retry_count and self._should_retry(error_msg):
                            self._emit(
                                f"  ↻ 步骤 {step_idx} 失败，{self.retry_delay} 秒后"
                                f"第 {attempt} 次重试（{error_msg[:36]}…）",
                                "warning",
                            )
                            time.sleep(self.retry_delay)
                            continue

                        # 最终失败
                        shot_path = None
                        if getattr(self.device, "last_screenshot", None):
                            shot_path = self.device.last_screenshot
                            if self.exec_model is not None:
                                self.exec_model.screenshot_paths.append(shot_path)
                            self.device.last_screenshot = None

                        step_info = f"步骤 {step_idx} \"{step.name}\""
                        self._emit(f"  ✗ {step_info} 失败：{error_msg}", "error")
                        case_result.steps.append(StepResult(
                            index=step_idx,
                            name=step.name or step.type,
                            type=step.type,
                            ok=False,
                            message=error_msg,
                            screenshot=shot_path,
                        ))

                        # 收集失败上下文（不调 AI，只留数据；AI 由用户按需触发）
                        if self.exec_model is not None:
                            try:
                                self.exec_model.failure_contexts.append(FailureContext(
                                    case_id=case_id,
                                    case_name=display_name,
                                    step_index=step_idx,
                                    step_type=step.type,
                                    step_name=step.name,
                                    step_params=dict(step.params or {}),
                                    prev_steps=[
                                        {"type": s.type, "name": s.name,
                                         "params": dict(s.params or {})}
                                        for s in steps[max(0, step_idx - 3):step_idx - 1]
                                    ],
                                    error_msg=error_msg,
                                    screenshot_path=shot_path,
                                    loop_index=loop_idx,
                                    timestamp=datetime.now().strftime("%H:%M:%S"),
                                ))
                            except Exception:
                                pass

                        step_failed = True
                        break
                if step_failed:
                    # 显式标记失败并终止当前用例，不再执行后续步骤
                    case_failed = True
                    break
        except Exception as e:
            # 兜底：获取步骤等外层环节出错（例如模型层异常）
            error_msg = self._format_error(e)
            self._emit(
                f"  ✗ 用例 \"{display_name}\" 执行失败：{error_msg}", "error")
            case_result.error = error_msg
            case_failed = True

        # 中途被停止：这个用例没跑完，既不算通过也不算失败 —— 半截用例记成
        # 「执行通过」会直接骗人。已经失败的用例照常走下面的失败记账。
        if self._should_abort() and not case_failed:
            self._emit(f"  ⏹ 用例 \"{display_name}\" 未执行完（已停止）", "warning")
            case_result.skipped = True
            case_result.duration_ms = int((time.time() - started) * 1000)
            return case_result

        if case_failed:
            case_result.ok = False
            self._emit(f"  ✗ 用例 \"{display_name}\" 执行失败", "error")
            if self.stop_on_fail:
                self._emit("  ⛔ 因失败停止标志，终止后续执行", "warning")
                self._stop_on_fail_abort = True
        else:
            case_result.ok = True
            self._emit(f"  ✓ 用例 \"{display_name}\" 执行通过", "success")

        case_result.duration_ms = int((time.time() - started) * 1000)
        return case_result


def run_cases(case_ids, project_model, step_model, device_service, exec_model=None,
              *, loop_count=1, stop_on_fail=False, retry_count=0, retry_delay=0,
              retry_only_timeout=True, on_progress=None, abort_event=None) -> RunResult:
    """便捷入口：MCP 侧直接 `asyncio.to_thread(run_cases, ...)`。"""
    return CaseRunner(
        case_ids, project_model, step_model, device_service, exec_model,
        loop_count=loop_count,
        stop_on_fail=stop_on_fail,
        retry_count=retry_count,
        retry_delay=retry_delay,
        retry_only_timeout=retry_only_timeout,
        on_progress=on_progress,
        abort_event=abort_event,
    ).run()
