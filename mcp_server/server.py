# mcp_server/server.py
"""虫师 MCP Server（P0）：把 device_service 暴露成 stdio 工具。"""
from __future__ import annotations

import asyncio
import base64
import os
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict
from datetime import datetime
from typing import Any, Dict, Optional

from mcp.server.mcpserver import MCPServer

from models.execution_model import ExecutionModel
from models.project_model import ProjectModel
from models.step_model import Step, StepModel
from models.suite_model import SuiteModel
from services.case_runner import CaseRunner
from services.device_service import DeviceService
from utils.adb_path import get_adb_path
from utils.app_paths import data_path
from utils.gui_presence import probe_gui
from utils.serial_helper import safe_serial
from utils.settings import Settings
from utils.version import APP_VERSION

from mcp_server.audit import write as audit_write
from mcp_server.logging_setup import setup_stdio_logging
from mcp_server.runtime import RUNTIME, search_nodes, summary_nodes
from mcp_server.schema import ToolFailure, classify_error, err, ok

server = MCPServer(
    name="chongshi",
    title="虫师",
    version=APP_VERSION,
    instructions=(
        "虫师是 Android/车机自动化执行层。优先用 ui_find 找元素，再按 element_index 点击；"
        "同一屏内可复用缓存的 index，界面变化后重新 ui_find；等待用 wait_until，不要 sleep。"
        "虫师界面正在运行时会自动降级为只读，写操作返回 PERMISSION_DENIED。"
    ),
)


# ----------------------------------------------------------------------
# 通用执行包装：GUI 只读保护、错误码、审计
# ----------------------------------------------------------------------
def _execute(name: str, params: dict, fn, mutating: bool = False) -> str:
    started = time.time()
    try:
        with RUNTIME.lock:
            if _case_running() and name not in ("device_list", "list_cases",
                                                "draft_status", "explore_and_draft",
                                                "run_case_status", "run_case_stop"):
                raise ToolFailure("DEVICE_BUSY", "run_case 正在执行，设备暂时被占用",
                                  "等用例结束，或先调用 run_case_stop")
            if mutating:
                info = probe_gui()
                if info is not None:
                    pid = getattr(info, "pid", 0) or 0
                    raise ToolFailure(
                        "PERMISSION_DENIED",
                        "虫师界面正在运行，MCP 当前为只读模式",
                        f"请先关闭虫师界面（PID {pid}）后再执行写操作",
                    )
            data = fn() or {}
        audit_write(name, params, True, "", int((time.time() - started) * 1000))
        return ok(data)
    except ToolFailure as e:
        audit_write(name, params, False, e.code, int((time.time() - started) * 1000))
        return err(e.code, e.message, e.hint)
    except Exception as e:
        code = classify_error(e)
        audit_write(name, params, False, code, int((time.time() - started) * 1000))
        return err(code, str(e), _hint_for(code))


def _hint_for(code: str) -> str:
    return {
        "NO_DEVICE": "先调用 device_list，确认 USB / 无线调试已连接并授权",
        "DUMP_FAILED": "界面可能持续动画或为自绘 UI；改用 screenshot + 图像定位",
        "ELEMENT_NOT_FOUND": "先用 ui_find 查看相近文本/ID；或用 screenshot 看现场",
        "ELEMENT_AMBIGUOUS": "用 ui_find 返回的候选列表选择 index，不要自己猜",
        "TIMEOUT": "检查元素是否真的出现；必要时增大 timeout",
        "PERMISSION_DENIED": "先关闭虫师界面；MCP 与 GUI 同时开时只允许只读工具",
        "CASE_NOT_FOUND": "先调用 list_cases 查看用例 id / 名称",
        "CASE_AMBIGUOUS": "用例名重复，改用 case_id 或在 module 里指定模块",
        "CASE_EXISTS": "换一个 case_name，或传 overwrite=true 覆盖",
        "SUITE_NOT_FOUND": "先在执行页保存套件，或核对套件名 / 用例是否被删除",
        "DEVICE_BUSY": "run_case / run_suite 正在执行；等它结束或先调用 run_case_stop",
        "ASSERT_FAILED": "查看返回的截图路径 / 失败步骤，必要时用 screenshot 看现场",
    }.get(code, "查看 data/mcp.log；如是参数问题请修正后重试")


# ----------------------------------------------------------------------
# run_case 后台任务（避免一次工具调用阻塞几分钟）
# ----------------------------------------------------------------------
_JOBS: Dict[str, dict] = {}
# RLock：_run_case_status / _run_case_stop 会在持锁时调用 _job_snapshot，
# 普通 Lock 会在这里自锁死，进而把整个 MCP server 卡住。
_JOBS_LOCK = threading.RLock()

# 草稿：explore_and_draft 开一个会话，后续动作工具自动记录
_DRAFTS: Dict[str, dict] = {}
_DRAFT_LOCK = threading.RLock()
_ACTIVE_DRAFT: Optional[str] = None


def _case_running() -> bool:
    with _JOBS_LOCK:
        return any(j.get("status") == "running" for j in _JOBS.values())


def _job_snapshot(job: dict) -> dict:
    with _JOBS_LOCK:
        return {
            "run_id": job["run_id"],
            "status": job["status"],
            "case": job.get("case"),
            "started_at": job.get("started_at", ""),
            "finished_at": job.get("finished_at", ""),
            "error": job.get("error", ""),
            "result": job.get("result"),
            "logs": list(job.get("logs", []))[-100:],
        }


def _draft_snapshot(draft: dict) -> dict:
    with _DRAFT_LOCK:
        return {
            "draft_id": draft["draft_id"],
            "goal": draft.get("goal", ""),
            "steps": list(draft.get("steps", [])),
            "max_steps": draft.get("max_steps", 0),
            "truncated": bool(draft.get("truncated", False)),
            "created_at": draft.get("created_at", ""),
        }


def _record_step(step_type: str, name: str, params: dict):
    """当前有草稿会话时，把一次成功的动作追加到草稿。"""
    global _ACTIVE_DRAFT
    with _DRAFT_LOCK:
        if not _ACTIVE_DRAFT:
            return
        draft = _DRAFTS.get(_ACTIVE_DRAFT)
        if draft is None:
            return
        if len(draft["steps"]) >= draft["max_steps"]:
            draft["truncated"] = True
            return
        draft["steps"].append({
            "type": step_type,
            "name": name,
            "params": dict(params),
        })


def _start_draft(goal: str, max_steps: int = 30) -> dict:
    global _ACTIVE_DRAFT
    goal = (goal or "").strip()
    if not goal:
        raise ToolFailure("INTERNAL_ERROR", "goal 不能为空",
                          "例如：打开搜索并进入搜索页，再返回首页")
    draft_id = uuid.uuid4().hex[:12]
    draft = {
        "draft_id": draft_id,
        "goal": goal,
        "steps": [],
        "max_steps": max(1, min(100, int(max_steps or 30))),
        "truncated": False,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    with _DRAFT_LOCK:
        _DRAFTS.clear()  # 只保留当前草稿，避免 Agent 误存旧会话
        _DRAFTS[draft_id] = draft
        _ACTIVE_DRAFT = draft_id
    return _draft_snapshot(draft)


def _draft_locator(candidate: dict, timeout: float = 10.0) -> dict:
    loc = (candidate or {}).get("locators") or {}
    if loc.get("资源ID"):
        params = {"locationType": "资源ID", "locationValue": loc["资源ID"]}
        if candidate.get("same_id_count", 1) > 1:
            params["instance"] = int(candidate.get("instance") or 0)
    elif loc.get("文本"):
        params = {"locationType": "文本", "locationValue": loc["文本"]}
    elif loc.get("描述"):
        params = {"locationType": "描述", "locationValue": loc["描述"]}
    elif loc.get("XPath"):
        params = {"locationType": "XPath", "locationValue": loc["XPath"]}
    else:
        center = candidate.get("center") or [0, 0]
        params = {"locationType": "坐标", "locationValue": f"{center[0]},{center[1]}"}
    params["timeout"] = float(timeout)
    return params


def _draft_name(prefix: str, candidate: dict) -> str:
    text = (candidate or {}).get("text") or ""
    rid = (candidate or {}).get("resource_id") or ""
    tail = rid.rsplit("/", 1)[-1] if rid else ""
    label = text or tail or "坐标"
    return f"{prefix} - {label[:24]}"


def _walk_cases(nodes, project_name="", module_name=""):
    for node in nodes:
        if node.type == "project":
            yield from _walk_cases(node.children, node.name, module_name)
        elif node.type == "folder":
            yield from _walk_cases(node.children, project_name, node.name)
        elif node.type == "case":
            yield {
                "case_id": node.id,
                "name": node.name,
                "project": project_name,
                "module": module_name,
                "data_file": node.data_file or "",
            }


def _list_cases(module: Optional[str] = None) -> dict:
    project = ProjectModel()
    cases = []
    for item in _walk_cases(project.root_nodes):
        if module and module not in (item["module"] or ""):
            continue
        cases.append(item)
    return {"count": len(cases), "cases": cases}


def _resolve_case(case_name: Optional[str], case_id: Optional[str]) -> dict:
    project = ProjectModel()
    cases = list(_walk_cases(project.root_nodes))
    if case_id:
        exact = [c for c in cases if c["case_id"] == str(case_id)]
        if exact:
            return exact[0]
        raise ToolFailure("CASE_NOT_FOUND", f"用例 id 不存在：{case_id}",
                          "先调用 list_cases")
    name = (case_name or "").strip()
    if not name:
        raise ToolFailure("INTERNAL_ERROR", "case_name / case_id 至少提供一个")
    exact = [c for c in cases if c["name"] == name]
    pool = exact or [c for c in cases if name.lower() in c["name"].lower()]
    if not pool:
        raise ToolFailure("CASE_NOT_FOUND", f"没有找到用例：{name}",
                          "先调用 list_cases 查看现有用例")
    if len(pool) > 1:
        names = "、".join(f"{c['module']}/{c['name']}({c['case_id']})" for c in pool[:5])
        raise ToolFailure("CASE_AMBIGUOUS", f"匹配到 {len(pool)} 条用例：{names}",
                          "改用 case_id，或用更精确的用例名")
    return pool[0]


def _start_case_job(case_name: Optional[str], case_id: Optional[str],
                    stop_on_failure: bool = False, loop_count: int = 1,
                    retry_count: int = 0, retry_delay: int = 0) -> dict:
    if _case_running():
        raise ToolFailure("DEVICE_BUSY", "已有 run_case 正在执行",
                          "先调用 run_case_status 查看，或 run_case_stop 停止")
    target = _resolve_case(case_name, case_id)
    project = ProjectModel()
    step_model = StepModel()
    exec_model = ExecutionModel()
    abort = threading.Event()
    run_id = uuid.uuid4().hex[:12]
    job = {
        "run_id": run_id,
        "status": "running",
        "case": target,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "finished_at": "",
        "error": "",
        "result": None,
        "logs": [],
        "abort": abort,
        "done": threading.Event(),
    }
    with _JOBS_LOCK:
        _JOBS[run_id] = job

    def progress(message, level="info"):
        with _JOBS_LOCK:
            logs = job["logs"]
            logs.append({"level": level, "message": message})
            if len(logs) > 500:
                del logs[:200]

    def work():
        try:
            svc = RUNTIME.ensure_device(RUNTIME.serial)
            runner = CaseRunner(
                [target["case_id"]], project, step_model, svc, exec_model,
                loop_count=loop_count, stop_on_fail=stop_on_failure,
                retry_count=retry_count, retry_delay=retry_delay,
                on_progress=progress, abort_event=abort,
            )
            result = runner.run()
            with _JOBS_LOCK:
                job["result"] = asdict(result)
                job["status"] = "stopped" if result.aborted else "done"
        except Exception as e:
            with _JOBS_LOCK:
                job["status"] = "error"
                job["error"] = str(e)
        finally:
            with _JOBS_LOCK:
                job["finished_at"] = datetime.now().isoformat(timespec="seconds")
            job["done"].set()

    threading.Thread(target=work, name=f"mcp-run-{run_id}", daemon=True).start()
    return job


def _run_case_tool(case_name: Optional[str] = None, case_id: Optional[str] = None,
                   stop_on_failure: bool = False, loop_count: int = 1,
                   retry_count: int = 0, retry_delay: int = 0,
                   wait: bool = True, timeout_s: float = 60.0) -> str:
    started = time.time()
    try:
        info = probe_gui()
        if info is not None:
            raise ToolFailure("PERMISSION_DENIED", "虫师界面正在运行，MCP 当前为只读模式",
                              "请先关闭虫师界面后再执行 run_case")
        job = _start_case_job(case_name, case_id, stop_on_failure,
                              loop_count, retry_count, retry_delay)
        if wait and timeout_s and timeout_s > 0:
            job["done"].wait(float(timeout_s))
        snap = _job_snapshot(job)
        audit_write("run_case", {"case_name": case_name, "case_id": case_id},
                    True, "", int((time.time() - started) * 1000))
        return ok(snap)
    except ToolFailure as e:
        audit_write("run_case", {"case_name": case_name, "case_id": case_id},
                    False, e.code, int((time.time() - started) * 1000))
        return err(e.code, e.message, e.hint)
    except Exception as e:
        code = classify_error(e)
        audit_write("run_case", {"case_name": case_name, "case_id": case_id},
                    False, code, int((time.time() - started) * 1000))
        return err(code, str(e), _hint_for(code))


# ----------------------------------------------------------------------
# 小工具
# ----------------------------------------------------------------------
def _step(step_type: str, **params) -> Step:
    return Step(0, step_type, step_type, params)


def _candidate(index: int) -> dict:
    if not RUNTIME.candidates:
        raise ToolFailure("ELEMENT_NOT_FOUND", "还没有 ui_find 结果",
                          "先调用 ui_find，再用返回的 element_index")
    if index < 1 or index > len(RUNTIME.candidates):
        raise ToolFailure("ELEMENT_AMBIGUOUS",
                          f"element_index {index} 超范围（当前 {len(RUNTIME.candidates)} 个）",
                          "用最近一次 ui_find 返回的 index")
    return RUNTIME.candidates[index - 1]


def _locator_from_args(*, element_index: Optional[int] = None,
                       x: Optional[int] = None, y: Optional[int] = None,
                       text: Optional[str] = None,
                       resource_id: Optional[str] = None,
                       description: Optional[str] = None,
                       xpath: Optional[str] = None,
                       instance: Optional[int] = None) -> Dict[str, Any]:
    if element_index is not None:
        c = _candidate(int(element_index))
        return {"locationType": "坐标",
                "locationValue": f"{c['center'][0]},{c['center'][1]}",
                "_by_index": c}
    if x is not None or y is not None:
        if x is None or y is None:
            raise ToolFailure("INTERNAL_ERROR", "x/y 必须同时提供")
        return {"locationType": "坐标", "locationValue": f"{int(x)},{int(y)}"}
    if resource_id:
        p = {"locationType": "资源ID", "locationValue": resource_id}
        if instance is not None:
            p["instance"] = int(instance)
        return p
    if text:
        return {"locationType": "文本", "locationValue": text}
    if description:
        return {"locationType": "描述", "locationValue": description}
    if xpath:
        return {"locationType": "XPath", "locationValue": xpath}
    raise ToolFailure("INTERNAL_ERROR", "缺少定位条件",
                      "提供 element_index / x,y / text / resource_id / description / xpath 之一")


def _adb_shell(serial: str, shell_cmd: str) -> str:
    try:
        r = subprocess.run(
            [get_adb_path(), "-s", serial, "shell", shell_cmd],
            capture_output=True, text=True, timeout=3,
            encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        return (r.stdout or "").strip()
    except Exception:
        return ""


# ----------------------------------------------------------------------
# P0：设备 / 截图
# ----------------------------------------------------------------------
def _device_list() -> dict:
    svc = RUNTIME.service or DeviceService()
    serials = svc.get_devices()
    devices = []
    for serial in serials:
        model = _adb_shell(serial, "getprop ro.product.model")
        version = _adb_shell(serial, "getprop ro.build.version.release")
        chars = _adb_shell(serial, "getprop ro.build.characteristics")
        size = _adb_shell(serial, "wm size")
        devices.append({
            "serial": serial,
            "model": model,
            "android": version,
            "resolution": size.split(":")[-1].strip() if size else "",
            "is_car": "automotive" in chars.lower(),
        })
    return {"devices": devices, "current": RUNTIME.serial or ""}


def _screenshot(inline: bool = False, region: Optional[str] = None) -> dict:
    svc = RUNTIME.ensure_device(RUNTIME.serial)
    folder = os.path.join(Settings.get_output_dir(),
                          safe_serial(svc.serial or ""), "screenshots")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"mcp_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]}.png")
    try:
        svc.device.screenshot(path)
    except Exception as e:
        raise ToolFailure("INTERNAL_ERROR", f"截图失败：{e}", "确认设备在线")
    if region:
        try:
            from PIL import Image
            x, y, w, h = [int(v.strip()) for v in region.split(",")[:4]]
            img = Image.open(path)
            img.crop((x, y, x + w, y + h)).save(path)
        except Exception as e:
            raise ToolFailure("INTERNAL_ERROR", f"region 解析/裁剪失败：{e}",
                              "region 格式：x,y,w,h")
    out = {"path": path}
    if inline:
        with open(path, "rb") as f:
            out["inline_base64"] = base64.b64encode(f.read()).decode("ascii")
    return out


# ----------------------------------------------------------------------
# P0：看 → 找 → 点 → 验
# ----------------------------------------------------------------------
def _ui_find(text: Optional[str] = None, resource_id: Optional[str] = None,
             description: Optional[str] = None, contains: bool = False,
             clickable_only: bool = False, limit: int = 20) -> dict:
    RUNTIME.ensure_device(RUNTIME.serial)
    refreshed = RUNTIME.dump(force=False)
    candidates = search_nodes(
        RUNTIME.root, text=text, resource_id=resource_id,
        description=description, contains=contains,
        clickable_only=clickable_only, limit=limit,
    )
    RUNTIME.candidates = candidates
    return {
        "device": RUNTIME.serial or "",
        "screen": RUNTIME.screen(),
        "dump_ms": RUNTIME.dump_ms,
        "dumped_at": RUNTIME.dumped_at_iso(),
        "cached": not refreshed,
        "candidates": candidates,
    }


def _tap(element_index: Optional[int] = None, x: Optional[int] = None,
          y: Optional[int] = None, text: Optional[str] = None,
          resource_id: Optional[str] = None, description: Optional[str] = None,
          xpath: Optional[str] = None, instance: Optional[int] = None,
          timeout: float = 10.0) -> dict:
    svc = RUNTIME.ensure_device(RUNTIME.serial)
    params = _locator_from_args(
        element_index=element_index, x=x, y=y, text=text,
        resource_id=resource_id, description=description,
        xpath=xpath, instance=instance,
    )
    by_index = params.pop("_by_index", None)
    params["timeout"] = float(timeout)
    svc.perform(_step("click", **params))
    suspect = getattr(svc, "last_click_suspect", None)
    RUNTIME.invalidate()
    out = {"clicked": True, "target": params["locationValue"]}
    if by_index:
        out["candidate"] = {
            "text": by_index.get("text", ""),
            "resource_id": by_index.get("resource_id", ""),
            "center": by_index.get("center"),
        }
    if suspect:
        out["warning"] = f"点击 ({suspect[0]},{suspect[1]}) 后界面几乎没变化（差异 {suspect[2]}）"
    if by_index:
        draft_params = _draft_locator(by_index, timeout)
        draft_name = _draft_name("点击", by_index)
    else:
        draft_params = {k: v for k, v in params.items()}
        draft_name = f"点击 - {params.get('locationValue', '')[:24]}"
    _record_step("click", draft_name, draft_params)
    return out


def _input_text(text: str, element_index: Optional[int] = None,
                x: Optional[int] = None, y: Optional[int] = None,
                resource_id: Optional[str] = None,
                description: Optional[str] = None,
                xpath: Optional[str] = None,
                instance: Optional[int] = None,
                timeout: float = 10.0) -> dict:
    if not text:
        raise ToolFailure("INTERNAL_ERROR", "text 不能为空")
    svc = RUNTIME.ensure_device(RUNTIME.serial)
    params = _locator_from_args(
        element_index=element_index, x=x, y=y, text=None,
        resource_id=resource_id, description=description,
        xpath=xpath, instance=instance,
    )
    by_index = params.pop("_by_index", None)
    params["text"] = text
    params["timeout"] = float(timeout)
    svc.perform(_step("input", **params))
    RUNTIME.invalidate()
    if by_index:
        draft_params = _draft_locator(by_index, timeout)
        draft_name = _draft_name("输入", by_index)
    else:
        draft_params = {k: v for k, v in params.items()}
        draft_name = f"输入 - {params.get('locationValue', '')[:24]}"
    draft_params["text"] = text
    _record_step("input", draft_name, draft_params)
    return {"input": True, "chars": len(text)}


def _swipe(direction: Optional[str] = None,
           x1: Optional[int] = None, y1: Optional[int] = None,
           x2: Optional[int] = None, y2: Optional[int] = None,
           duration_ms: int = 500) -> dict:
    svc = RUNTIME.ensure_device(RUNTIME.serial)
    if direction:
        w, h = RUNTIME.screen().get("w", 0), RUNTIME.screen().get("h", 0)
        w, h = (w or 1080), (h or 1920)
        d = direction.lower()
        if d in ("up", "上", "上滑"):
            coords = (w // 2, int(h * 0.75), w // 2, int(h * 0.25))
        elif d in ("down", "下", "下滑"):
            coords = (w // 2, int(h * 0.25), w // 2, int(h * 0.75))
        elif d in ("left", "左", "左滑"):
            coords = (int(w * 0.75), h // 2, int(w * 0.25), h // 2)
        elif d in ("right", "右", "右滑"):
            coords = (int(w * 0.25), h // 2, int(w * 0.75), h // 2)
        else:
            raise ToolFailure("INTERNAL_ERROR", f"未知滑动方向: {direction}",
                              "direction 用 up/down/left/right，或传 x1,y1,x2,y2")
        x1, y1, x2, y2 = coords
    if None in (x1, y1, x2, y2):
        raise ToolFailure("INTERNAL_ERROR", "缺少滑动坐标",
                          "传 direction，或同时传 x1,y1,x2,y2")
    svc.device.swipe(int(x1), int(y1), int(x2), int(y2),
                     duration=max(0.05, float(duration_ms) / 1000.0))
    RUNTIME.invalidate()
    direction_cn = {"up": "上滑", "down": "下滑", "left": "左滑", "right": "右滑"}.get(
        (direction or "").lower(), "自定义坐标")
    _record_step("swipe", f"滑动 - {direction_cn}", {
        "direction": direction_cn,
        "startX": int(x1), "startY": int(y1),
        "endX": int(x2), "endY": int(y2),
    })
    return {"swiped": True, "from": [int(x1), int(y1)], "to": [int(x2), int(y2)]}


def _wait_until(cond: str = "appear", text: Optional[str] = None,
                resource_id: Optional[str] = None, description: Optional[str] = None,
                xpath: Optional[str] = None, timeout_s: float = 10.0) -> dict:
    mapping = {"appear": "元素出现", "disappear": "元素消失", "clickable": "元素可点击"}
    wait_type = mapping.get((cond or "").lower())
    if not wait_type:
        raise ToolFailure("INTERNAL_ERROR", f"未知等待条件: {cond}",
                          "cond 用 appear / disappear / clickable")
    svc = RUNTIME.ensure_device(RUNTIME.serial)
    params = _locator_from_args(text=text, resource_id=resource_id,
                                description=description, xpath=xpath)
    params.pop("_by_index", None)
    if params.get("locationType") == "坐标":
        raise ToolFailure("INTERNAL_ERROR", "等待语义不支持坐标", "用 text/resource_id/description/xpath")
    params["wait_type"] = wait_type
    params["timeout"] = float(timeout_s)
    svc.perform(_step("wait_for", **params))
    RUNTIME.invalidate()
    _record_step("wait_for", f"等待 - {wait_type}", dict(params))
    return {"waited": True, "cond": cond, "timeout_s": float(timeout_s)}


def _assert_ui(assert_type: str = "exists", text: Optional[str] = None,
               resource_id: Optional[str] = None, description: Optional[str] = None,
               xpath: Optional[str] = None, expected: Optional[str] = None,
               image: Optional[str] = None, element_index: Optional[int] = None,
               timeout_s: float = 5.0) -> dict:
    t = (assert_type or "").lower()
    if t in ("exists", "absent", "image"):
        if t == "image":
            if not image:
                raise ToolFailure("INTERNAL_ERROR", "image 不能为空",
                                  "先截图框选生成模板，再传模板路径")
            params = {"locationType": "图像", "locationValue": image}
            a_type = "元素存在"
        elif image:
            params = {"locationType": "图像", "locationValue": image}
            a_type = "元素存在" if t == "exists" else "元素不存在"
        elif element_index is not None:
            c = _candidate(int(element_index))
            loc = c.get("locators") or {}
            if loc.get("资源ID"):
                params = {"locationType": "资源ID", "locationValue": loc["资源ID"],
                          "instance": c.get("instance", 0)}
            elif loc.get("文本"):
                params = {"locationType": "文本", "locationValue": loc["文本"]}
            elif loc.get("描述"):
                params = {"locationType": "描述", "locationValue": loc["描述"]}
            elif loc.get("XPath"):
                params = {"locationType": "XPath", "locationValue": loc["XPath"]}
            else:
                raise ToolFailure("INTERNAL_ERROR", "该候选没有可断言的语义定位",
                                  "改用 text / resource_id / image")
            a_type = "元素存在" if t == "exists" else "元素不存在"
        else:
            params = _locator_from_args(text=text, resource_id=resource_id,
                                        description=description, xpath=xpath)
            params.pop("_by_index", None)
            a_type = "元素存在" if t == "exists" else "元素不存在"
    elif t in ("text_equals", "text_contains"):
        params = _locator_from_args(text=text, resource_id=resource_id,
                                    description=description, xpath=xpath)
        params.pop("_by_index", None)
        if params.get("locationType") == "坐标":
            raise ToolFailure("INTERNAL_ERROR", "文本断言不支持坐标")
        a_type = "文本等于" if t == "text_equals" else "文本包含"
        if expected is None:
            raise ToolFailure("INTERNAL_ERROR", "expected 不能为空")
    else:
        raise ToolFailure("INTERNAL_ERROR", f"未知断言类型: {assert_type}",
                          "用 exists/absent/text_equals/text_contains/image")

    svc = RUNTIME.ensure_device(RUNTIME.serial)
    params["assert_type"] = a_type
    params["timeout"] = float(timeout_s)
    if expected is not None:
        params["expected_value"] = expected
    try:
        svc.perform(_step("assert", **params))
    except AssertionError as e:
        raise ToolFailure("ASSERT_FAILED", str(e), "查看返回的截图路径 / 相近元素")
    _record_step("assert", f"断言 - {a_type}", dict(params))
    return {"passed": True, "assert_type": assert_type}


# ----------------------------------------------------------------------
# P1：层级摘要 / 系统按键 / 应用启停 / 用例
# ----------------------------------------------------------------------
def _ui_dump(mode: str = "summary", max_nodes: int = 200) -> dict:
    RUNTIME.ensure_device(RUNTIME.serial)
    refreshed = RUNTIME.dump(force=False)
    mode = (mode or "summary").lower()
    if mode == "full":
        folder = data_path("mcp_dumps")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"ui_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]}.xml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(RUNTIME.xml or "")
        return {
            "mode": "full",
            "path": path,
            "dump_ms": RUNTIME.dump_ms,
            "dumped_at": RUNTIME.dumped_at_iso(),
            "cached": not refreshed,
        }
    if mode != "summary":
        raise ToolFailure("INTERNAL_ERROR", f"未知 ui_dump 模式: {mode}",
                          "mode 用 summary 或 full")
    return {
        "mode": "summary",
        "nodes": summary_nodes(RUNTIME.root, max_nodes=max_nodes),
        "dump_ms": RUNTIME.dump_ms,
        "dumped_at": RUNTIME.dumped_at_iso(),
        "cached": not refreshed,
    }


_KEY_MAP = {
    "back": "返回", "home": "主页", "menu": "菜单",
    "volume_up": "音量+", "volume_down": "音量-",
    "power": "电源", "camera": "相机",
}


def _press_key(key: str) -> dict:
    key_name = _KEY_MAP.get((key or "").lower())
    if not key_name:
        raise ToolFailure("INTERNAL_ERROR", f"未知按键: {key}",
                          "key 用 back/home/menu/volume_up/volume_down/power/camera")
    svc = RUNTIME.ensure_device(RUNTIME.serial)
    svc.perform(_step("physical_key", keyName=key_name))
    RUNTIME.invalidate()
    _record_step("physical_key", f"按键 - {key_name}", {"keyName": key_name})
    return {"pressed": key_name}


_APP_ACTION = {"start": "启动应用", "stop": "停止应用", "restart": "重启应用"}


def _launch_app(package: str, action: str = "start",
                activity: Optional[str] = None) -> dict:
    if not package:
        raise ToolFailure("INTERNAL_ERROR", "package 不能为空")
    action_name = _APP_ACTION.get((action or "").lower())
    if not action_name:
        raise ToolFailure("INTERNAL_ERROR", f"未知应用操作: {action}",
                          "action 用 start/stop/restart（不提供清空数据/卸载）")
    svc = RUNTIME.ensure_device(RUNTIME.serial)
    if activity and action_name == "启动应用":
        try:
            svc.device.app_start(package, activity=activity)
        except Exception as e:
            raise ToolFailure("INTERNAL_ERROR", f"启动应用失败：{e}",
                              "检查 package / activity 是否正确")
    else:
        svc.perform(_step("app_mgr", action=action_name, packageName=package))
    RUNTIME.invalidate()
    _record_step("app_mgr", f"应用管理 - {action_name}", {
        "action": action_name, "packageName": package,
    })
    return {"package": package, "action": action_name, "activity": activity or ""}


def _run_case_status(run_id: str) -> dict:
    with _JOBS_LOCK:
        job = _JOBS.get(run_id)
        if job is None:
            raise ToolFailure("CASE_NOT_FOUND", f"run_id 不存在：{run_id}",
                              "用 run_case 返回的 run_id")
        return _job_snapshot(job)


def _run_case_stop(run_id: str) -> dict:
    with _JOBS_LOCK:
        job = _JOBS.get(run_id)
        if job is None:
            raise ToolFailure("CASE_NOT_FOUND", f"run_id 不存在：{run_id}",
                              "用 run_case 返回的 run_id")
        job["abort"].set()
        return _job_snapshot(job)


# ----------------------------------------------------------------------
# P2：草稿 / 保存用例 / 套件
# ----------------------------------------------------------------------
_LOCATOR_STEP_TYPES = {"click", "input", "wait_for", "assert",
                       "long_press", "double_click"}
_VALID_STEP_TYPES = {
    "wait", "wait_for", "click", "app_mgr", "input", "swipe", "assert",
    "physical_key", "screen_ctrl", "long_press", "flick", "double_click",
    "multi_swipe", "drag_drop", "gesture_zoom", "screenshot", "voice",
}


def _validate_draft_steps(steps) -> list[dict]:
    if not isinstance(steps, list) or not steps:
        raise ToolFailure("INTERNAL_ERROR", "没有可保存的步骤",
                          "先 explore_and_draft，或直接传 steps[]")
    clean = []
    for i, item in enumerate(steps, 1):
        if not isinstance(item, dict):
            raise ToolFailure("INTERNAL_ERROR", f"第 {i} 步不是对象")
        typ = str(item.get("type") or "").strip()
        if typ not in _VALID_STEP_TYPES:
            raise ToolFailure("INTERNAL_ERROR", f"第 {i} 步类型不支持：{typ}")
        params = dict(item.get("params") or {})
        if typ in _LOCATOR_STEP_TYPES:
            if not params.get("locationType") or not params.get("locationValue"):
                raise ToolFailure("INTERNAL_ERROR",
                                  f"第 {i} 步 {typ} 缺少定位方式/定位值")
        if typ == "wait_for" and not params.get("wait_type"):
            raise ToolFailure("INTERNAL_ERROR", f"第 {i} 步等待方式为空")
        if typ == "assert" and not params.get("assert_type"):
            raise ToolFailure("INTERNAL_ERROR", f"第 {i} 步断言类型为空")
        clean.append({
            "type": typ,
            "name": str(item.get("name") or typ),
            "params": params,
        })
    return clean


def _explore_and_draft(goal: str, max_steps: int = 30) -> dict:
    draft = _start_draft(goal, max_steps)
    draft["next"] = (
        "用 ui_find 找到控件后，调用 tap/input_text/swipe/press_key/wait_until/assert；"
        "成功的动作会自动记入草稿。最后调用 save_case(draft_id=...) 落库。"
    )
    return draft


def _draft_status(draft_id: Optional[str] = None) -> dict:
    with _DRAFT_LOCK:
        did = draft_id or _ACTIVE_DRAFT or ""
        draft = _DRAFTS.get(did)
        if draft is None:
            raise ToolFailure("INTERNAL_ERROR", "没有可用的草稿",
                              "先调用 explore_and_draft")
        return _draft_snapshot(draft)


def _save_case(project_name: Optional[str], module: str, case_name: str,
               steps=None, draft_id: Optional[str] = None,
               overwrite: bool = False) -> dict:
    global _ACTIVE_DRAFT
    module = (module or "").strip()
    case_name = (case_name or "").strip()
    if not module or not case_name:
        raise ToolFailure("INTERNAL_ERROR", "module / case_name 不能为空")
    if steps is None:
        with _DRAFT_LOCK:
            draft = _DRAFTS.get(draft_id or _ACTIVE_DRAFT or "")
            if draft is None:
                raise ToolFailure("INTERNAL_ERROR", "没有可用的草稿",
                                  "先 explore_and_draft 并执行动作")
            steps = draft["steps"]
    clean = _validate_draft_steps(steps)

    project_model = ProjectModel()
    project = None
    if project_name:
        project = next((n for n in project_model.root_nodes
                        if n.type == "project" and n.name == project_name), None)
        if project is None:
            project = project_model.create_project(project_name)
    else:
        project = next((n for n in project_model.root_nodes if n.type == "project"), None)
        if project is None:
            project = project_model.create_project("MCP")

    folder = next((n for n in project.children
                   if n.type == "folder" and n.name == module), None)
    if folder is None:
        folder = project_model.create_folder(project.id, module)

    existing = next((n for n in folder.children
                     if n.type == "case" and n.name == case_name), None)
    if existing is not None and not overwrite:
        raise ToolFailure("CASE_EXISTS", f"用例已存在：{module}/{case_name}",
                          "传 overwrite=true 覆盖，或换一个 case_name")
    step_model = StepModel()
    if existing is not None:
        case = existing
        for sid in list(step_model.case_steps.get(case.id, [])):
            step_model._steps.pop(sid, None)
        step_model.case_steps[case.id] = []
        step_model.save()
    else:
        case = project_model.create_case(folder.id, case_name)

    for item in clean:
        step_model.add_step_to_case(
            case.id, Step(0, item["type"], item["name"], item["params"]))
    with _DRAFT_LOCK:
        used = draft_id or _ACTIVE_DRAFT
        if used:
            _DRAFTS.pop(used, None)
            if _ACTIVE_DRAFT == used:
                _ACTIVE_DRAFT = None
    return {
        "case_id": case.id,
        "case_name": case.name,
        "module": module,
        "steps": len(clean),
        "overwritten": bool(existing),
    }


def _start_suite_job(suite_name: str, stop_on_failure: bool = False,
                     loop_count: int = 1, retry_count: int = 0,
                     retry_delay: int = 0) -> dict:
    if _case_running():
        raise ToolFailure("DEVICE_BUSY", "已有 run_case / run_suite 正在执行",
                          "先调用 run_case_status 查看，或 run_case_stop 停止")
    suite = SuiteModel().get_suite_by_name(suite_name)
    if suite is None:
        raise ToolFailure("SUITE_NOT_FOUND", f"套件不存在：{suite_name}",
                          "先在执行页保存套件，或检查套件名")
    project = ProjectModel()
    valid_ids = []
    for cid in suite.case_ids:
        node = project.get_node_by_id(cid)
        if node is not None and node.type == "case":
            valid_ids.append(cid)
    if not valid_ids:
        raise ToolFailure("SUITE_NOT_FOUND", f"套件里没有有效用例：{suite_name}",
                          "检查套件是否引用了已删除的用例")
    step_model = StepModel()
    exec_model = ExecutionModel()
    abort = threading.Event()
    run_id = uuid.uuid4().hex[:12]
    job = {
        "run_id": run_id,
        "status": "running",
        "case": {"suite": suite_name, "case_count": len(valid_ids)},
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "finished_at": "",
        "error": "",
        "result": None,
        "logs": [],
        "abort": abort,
        "done": threading.Event(),
    }
    with _JOBS_LOCK:
        _JOBS[run_id] = job

    def progress(message, level="info"):
        with _JOBS_LOCK:
            logs = job["logs"]
            logs.append({"level": level, "message": message})
            if len(logs) > 500:
                del logs[:200]

    def work():
        try:
            svc = RUNTIME.ensure_device(RUNTIME.serial)
            runner = CaseRunner(
                valid_ids, project, step_model, svc, exec_model,
                loop_count=loop_count, stop_on_fail=stop_on_failure,
                retry_count=retry_count, retry_delay=retry_delay,
                on_progress=progress, abort_event=abort,
            )
            result = runner.run()
            with _JOBS_LOCK:
                job["result"] = asdict(result)
                job["status"] = "stopped" if result.aborted else "done"
        except Exception as e:
            with _JOBS_LOCK:
                job["status"] = "error"
                job["error"] = str(e)
        finally:
            with _JOBS_LOCK:
                job["finished_at"] = datetime.now().isoformat(timespec="seconds")
            job["done"].set()

    threading.Thread(target=work, name=f"mcp-suite-{run_id}", daemon=True).start()
    return job


def _run_suite_tool(suite_name: str, stop_on_failure: bool = False,
                    loop_count: int = 1, retry_count: int = 0,
                    retry_delay: int = 0, wait: bool = True,
                    timeout_s: float = 60.0) -> str:
    started = time.time()
    try:
        info = probe_gui()
        if info is not None:
            raise ToolFailure("PERMISSION_DENIED", "虫师界面正在运行，MCP 当前为只读模式",
                              "请先关闭虫师界面后再执行 run_suite")
        job = _start_suite_job(suite_name, stop_on_failure,
                               loop_count, retry_count, retry_delay)
        if wait and timeout_s and timeout_s > 0:
            job["done"].wait(float(timeout_s))
        snap = _job_snapshot(job)
        audit_write("run_suite", {"suite_name": suite_name}, True, "",
                    int((time.time() - started) * 1000))
        return ok(snap)
    except ToolFailure as e:
        audit_write("run_suite", {"suite_name": suite_name}, False, e.code,
                    int((time.time() - started) * 1000))
        return err(e.code, e.message, e.hint)
    except Exception as e:
        code = classify_error(e)
        audit_write("run_suite", {"suite_name": suite_name}, False, code,
                    int((time.time() - started) * 1000))
        return err(code, str(e), _hint_for(code))


# ----------------------------------------------------------------------
# MCP 工具注册（async + to_thread，避免阻塞 asyncio 事件循环）
# ----------------------------------------------------------------------
async def _run(name: str, params: dict, fn, mutating: bool = False) -> str:
    return await asyncio.to_thread(_execute, name, params, fn, mutating)


@server.tool(name="device_list", description="列出已连接设备（序列号 / 型号 / 分辨率 / 是否车机）")
async def device_list() -> str:
    return await _run("device_list", {}, _device_list)


@server.tool(name="screenshot", description="截取当前设备屏幕；默认只返回文件路径，inline=true 才返回 base64")
async def screenshot(inline: bool = False, region: Optional[str] = None) -> str:
    return await _run("screenshot", {"inline": inline, "region": region},
                      lambda: _screenshot(inline, region))


@server.tool(name="ui_find", description="一次 dump 后在本地搜索控件，返回候选 + 定位值；这是点击前必须先用的工具")
async def ui_find(text: Optional[str] = None, resource_id: Optional[str] = None,
                  description: Optional[str] = None, contains: bool = False,
                  clickable_only: bool = False, limit: int = 20) -> str:
    params = {"text": text, "resource_id": resource_id,
              "description": description, "contains": contains,
              "clickable_only": clickable_only, "limit": limit}
    return await _run("ui_find", params,
                      lambda: _ui_find(text, resource_id, description,
                                       contains, clickable_only, limit))


@server.tool(name="tap", description="点击控件：优先用 ui_find 的 element_index，也可传坐标/文本/资源ID")
async def tap(element_index: Optional[int] = None, x: Optional[int] = None,
              y: Optional[int] = None, text: Optional[str] = None,
              resource_id: Optional[str] = None, description: Optional[str] = None,
              xpath: Optional[str] = None, instance: Optional[int] = None,
              timeout: float = 10.0) -> str:
    params = {"element_index": element_index, "x": x, "y": y, "text": text,
              "resource_id": resource_id, "description": description,
              "xpath": xpath, "instance": instance, "timeout": timeout}
    return await _run("tap", params,
                      lambda: _tap(element_index, x, y, text, resource_id,
                                   description, xpath, instance, timeout),
                      mutating=True)


@server.tool(name="input_text", description="向控件输入文字；定位方式同 tap")
async def input_text(text: str, element_index: Optional[int] = None,
                     x: Optional[int] = None, y: Optional[int] = None,
                     resource_id: Optional[str] = None,
                     description: Optional[str] = None,
                     xpath: Optional[str] = None,
                     instance: Optional[int] = None,
                     timeout: float = 10.0) -> str:
    params = {"text": text, "element_index": element_index, "x": x, "y": y,
              "resource_id": resource_id, "description": description,
              "xpath": xpath, "instance": instance, "timeout": timeout}
    return await _run("input_text", params,
                      lambda: _input_text(text, element_index, x, y, resource_id,
                                          description, xpath, instance, timeout),
                      mutating=True)


@server.tool(name="swipe", description="滑动：传 direction（up/down/left/right）或 x1,y1,x2,y2")
async def swipe(direction: Optional[str] = None, x1: Optional[int] = None,
                y1: Optional[int] = None, x2: Optional[int] = None,
                y2: Optional[int] = None, duration_ms: int = 500) -> str:
    params = {"direction": direction, "x1": x1, "y1": y1, "x2": x2,
              "y2": y2, "duration_ms": duration_ms}
    return await _run("swipe", params,
                      lambda: _swipe(direction, x1, y1, x2, y2, duration_ms),
                      mutating=True)


@server.tool(name="wait_until", description="显式等待元素出现/消失/可点击，替代固定 sleep")
async def wait_until(cond: str = "appear", text: Optional[str] = None,
                     resource_id: Optional[str] = None,
                     description: Optional[str] = None,
                     xpath: Optional[str] = None,
                     timeout_s: float = 10.0) -> str:
    params = {"cond": cond, "text": text, "resource_id": resource_id,
              "description": description, "xpath": xpath, "timeout_s": timeout_s}
    return await _run("wait_until", params,
                      lambda: _wait_until(cond, text, resource_id, description,
                                          xpath, timeout_s))


@server.tool(name="assert", description="断言元素存在/不存在、文本等于/包含、图像匹配；失败返回现场证据")
async def assert_ui(assert_type: str = "exists", text: Optional[str] = None,
                    resource_id: Optional[str] = None,
                    description: Optional[str] = None,
                    xpath: Optional[str] = None,
                    expected: Optional[str] = None,
                    image: Optional[str] = None,
                    element_index: Optional[int] = None,
                    timeout_s: float = 5.0) -> str:
    params = {"assert_type": assert_type, "text": text, "resource_id": resource_id,
              "description": description, "xpath": xpath, "expected": expected,
              "image": image, "element_index": element_index,
              "timeout_s": timeout_s}
    return await _run("assert", params,
                      lambda: _assert_ui(assert_type, text, resource_id, description,
                                         xpath, expected, image, element_index,
                                         timeout_s))


@server.tool(name="ui_dump", description="导出当前层级：summary 返回可点击/有文案节点；full 返回 XML 文件路径")
async def ui_dump(mode: str = "summary", max_nodes: int = 200) -> str:
    return await _run("ui_dump", {"mode": mode, "max_nodes": max_nodes},
                      lambda: _ui_dump(mode, max_nodes))


@server.tool(name="press_key", description="按系统键：back/home/menu/volume_up/volume_down/power/camera")
async def press_key(key: str) -> str:
    return await _run("press_key", {"key": key},
                      lambda: _press_key(key), mutating=True)


@server.tool(name="launch_app", description="启动/停止/重启应用（不提供清空数据、卸载）")
async def launch_app(package: str, action: str = "start",
                     activity: Optional[str] = None) -> str:
    return await _run("launch_app",
                      {"package": package, "action": action, "activity": activity},
                      lambda: _launch_app(package, action, activity),
                      mutating=True)


@server.tool(name="list_cases", description="列出虫师用例树（可按模块过滤），只读")
async def list_cases(module: Optional[str] = None) -> str:
    return await _run("list_cases", {"module": module},
                      lambda: _list_cases(module))


@server.tool(name="run_case",
             description="执行一条虫师用例；wait=true 最多等 timeout_s，超时返回 run_id 供轮询")
async def run_case(case_name: Optional[str] = None, case_id: Optional[str] = None,
                   stop_on_failure: bool = False, loop_count: int = 1,
                   retry_count: int = 0, retry_delay: int = 0,
                   wait: bool = True, timeout_s: float = 60.0) -> str:
    return await asyncio.to_thread(
        _run_case_tool, case_name, case_id, stop_on_failure,
        loop_count, retry_count, retry_delay, wait, timeout_s)


@server.tool(name="run_case_status", description="查询 run_case 的进度 / 结果 / 最近日志")
async def run_case_status(run_id: str) -> str:
    return await _run("run_case_status", {"run_id": run_id},
                      lambda: _run_case_status(run_id))


@server.tool(name="run_case_stop", description="请求停止一个正在执行的 run_case")
async def run_case_stop(run_id: str) -> str:
    return await _run("run_case_stop", {"run_id": run_id},
                      lambda: _run_case_stop(run_id))


@server.tool(name="explore_and_draft",
             description="开始一个探索草稿：后续成功的 tap/input/swipe/按键/等待/断言会自动记入草稿")
async def explore_and_draft(goal: str, max_steps: int = 30) -> str:
    return await _run("explore_and_draft", {"goal": goal, "max_steps": max_steps},
                      lambda: _explore_and_draft(goal, max_steps))


@server.tool(name="draft_status", description="查看当前草稿（或指定 draft_id）已记录的动作")
async def draft_status(draft_id: Optional[str] = None) -> str:
    return await _run("draft_status", {"draft_id": draft_id},
                      lambda: _draft_status(draft_id))


@server.tool(name="save_case",
             description="把草稿或显式 steps[] 保存为用例；支持覆盖，保存前做定位校验")
async def save_case(module: str, case_name: str,
                    project_name: Optional[str] = None,
                    steps: Optional[list] = None,
                    draft_id: Optional[str] = None,
                    overwrite: bool = False) -> str:
    params = {"module": module, "case_name": case_name,
              "project_name": project_name, "draft_id": draft_id,
              "overwrite": overwrite, "steps": steps}
    return await _run("save_case", params,
                      lambda: _save_case(project_name, module, case_name,
                                         steps, draft_id, overwrite),
                      mutating=True)


@server.tool(name="run_suite",
             description="执行一个已保存的自动套件；wait=true 最多等 timeout_s，超时返回 run_id")
async def run_suite(suite_name: str, stop_on_failure: bool = False,
                    loop_count: int = 1, retry_count: int = 0,
                    retry_delay: int = 0, wait: bool = True,
                    timeout_s: float = 60.0) -> str:
    return await asyncio.to_thread(
        _run_suite_tool, suite_name, stop_on_failure, loop_count,
        retry_count, retry_delay, wait, timeout_s)


def main():
    setup_stdio_logging()
    server.run("stdio")


if __name__ == "__main__":
    main()
