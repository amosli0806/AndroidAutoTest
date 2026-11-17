# mcp_server/server.py
"""虫师 MCP Server（P0）：把 device_service 暴露成 stdio 工具。"""
from __future__ import annotations

import asyncio
import base64
import html
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from mcp.server.mcpserver import MCPServer

from models.api_model import ApiModel
from models.element_model import ElementModel
from models.execution_model import ExecutionModel
from models.knowledge_model import CATEGORIES, KnowledgeModel
from models.perf_model import PerfBaseline, PerfModel, compare_stats
from models.project_model import ProjectModel
from models.step_model import Step, StepModel
from models.suite_model import SuiteModel
from models.voice_model import VOICE_SUITE_PREFIX, VoiceModel
from services.api_service import ApiService
from services.ai_service import AIService
from services.case_runner import CaseRunner
from services.device_service import DeviceService
from services.logcat_stream import LogcatStream
from services.monkey_runner import MonkeyRunner
from services.perf_runner import ALLOWED_METRICS, ALLOWED_PROBES, PerfRunner
from services.screenrecord_runner import ScreenRecordRunner
from services.tcpdump_runner import CAPTURE_MAX_BYTES, CAPTURE_MAX_SECONDS, TcpdumpRunner
from services.weak_net import WeakNetRunner
from services.voice_feedback_service import VoiceFeedbackService
from services.voice_service import get_voice_service
from utils.adb_path import get_adb_path
from utils.app_paths import data_path
from utils.gui_presence import probe_gui
from utils.perf_report import PerfReportGenerator
from utils.serial_helper import safe_serial
from utils.settings import Settings
from utils.step_params import LOCATION_TYPE_OPTIONS
from utils.version import APP_VERSION

from mcp_server import device_jobs
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
_DEVICE_JOB_EXEMPT = {
    "device_list", "adb_device_info",
    "monkey_status", "monkey_stop",
    "screenrecord_stop", "logcat_stop", "logcat_read",
    "perf_status", "perf_stop", "tcpdump_stop",
    "perf_baseline_save", "perf_baseline_delete", "perf_compare",
    "knowledge_list", "knowledge_read", "knowledge_upsert_note",
    "knowledge_delete_note", "voice_settings_get", "voice_settings_update",
}

_DETACHED_CHECKED = False
_DETACHED_LOCK = threading.Lock()


def _reconcile_detached_jobs() -> None:
    """MCP 重启后第一次调用时，用设备端 PID 核对遗留的常驻任务。"""
    global _DETACHED_CHECKED
    with _DETACHED_LOCK:
        if _DETACHED_CHECKED:
            return
        _DETACHED_CHECKED = True
    if not device_jobs.has_detached():
        return
    try:
        serial = RUNTIME.ensure_device(RUNTIME.serial).serial
        runner = MonkeyRunner(serial)
        device_jobs.reconcile_detached(
            lambda job: runner.alive((job.get("meta") or {}).get("pid", "")))
    except Exception:
        with _DETACHED_LOCK:
            _DETACHED_CHECKED = False


def _execute(name: str, params: dict, fn, mutating: bool = False,
             hold_lock: bool = True) -> str:
    """统一包装。

    hold_lock=False 给安装/堆转储这类可能跑一两分钟的工具：占用
    RUNTIME.lock 会把 ui_find、device_list 一起堵死，所以只在检查阶段持锁。
    """
    started = time.time()
    try:
        data = None
        with RUNTIME.lock:
            device_jobs.init()
            _reconcile_detached_jobs()
            if device_jobs.has_active() and name not in _DEVICE_JOB_EXEMPT:
                kinds = "/".join(device_jobs.active_kinds())
                raise ToolFailure("DEVICE_BUSY", f"设备任务执行中：{kinds}",
                                  "等任务结束后再操作；安装/堆转储期间不要并发操作设备")
            if _case_running() and name not in (
                    "device_list", "list_cases", "draft_status", "explore_and_draft",
                    "perf_sessions", "perf_baselines", "perf_report",
                    "voice_list_cases", "voice_verify", "api_list",
                    "element_list", "suite_list", "report_list", "report_read",
                    "ai_analyze_failure",
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
            if hold_lock:
                data = fn() or {}
        if not hold_lock:
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
        "DESTRUCTIVE_DENIED": "确认风险后显式传 allow_destructive=true；默认不执行清空/安装/卸载、电源键、熄屏/改方向、停止/重启应用",
        "PERF_SESSION_NOT_FOUND": "先调用 perf_sessions 查看可用 session_id",
        "ADB_FAILED": "确认设备在线、路径存在且当前账号有读取权限",
        "PROCESS_NOT_FOUND": "先启动应用（launch_app），再确认包名正确",
        "JOB_NOT_FOUND": "先调用 monkey_start / screenrecord_start / logcat_start 获取 run_id",
        "CAPTURE_UNAVAILABLE": "tcpdump 需要 root；未 root 的设备无法抓包，可换已 root 设备或先 adb root",
        "WEAK_NET_UNAVAILABLE": "弱网模拟需要 root 与 tc；先 adb root，再确认设备有 tc / netem 支持",
        "BASELINE_EXISTS": "同名基线已存在；确认后传 overwrite=true 覆盖",
        "BASELINE_NOT_FOUND": "先调用 perf_baselines 查看已有基线",
        "PROJECT_NOT_FOUND": "先调用 knowledge_list 查看知识库项目",
        "NOTE_NOT_FOUND": "先调用 knowledge_list 或 knowledge_read 确认笔记 id",
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
                    retry_count: int = 0, retry_delay: int = 0,
                    allow_destructive: bool = False) -> dict:
    if _case_running():
        raise ToolFailure("DEVICE_BUSY", "已有 run_case 正在执行",
                          "先调用 run_case_status 查看，或 run_case_stop 停止")
    if device_jobs.has_active():
        kinds = "/".join(device_jobs.active_kinds())
        raise ToolFailure("DEVICE_BUSY", f"设备任务执行中：{kinds}",
                          "等安装/堆转储等任务结束后再执行用例")
    target = _resolve_case(case_name, case_id)
    project = ProjectModel()
    step_model = StepModel()
    found = _destructive_steps(step_model, [target["case_id"]])
    _ensure_destructive_allowed(found, allow_destructive, "用例")
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
                   wait: bool = True, timeout_s: float = 60.0,
                   allow_destructive: bool = False) -> str:
    started = time.time()
    try:
        info = probe_gui()
        if info is not None:
            raise ToolFailure("PERMISSION_DENIED", "虫师界面正在运行，MCP 当前为只读模式",
                              "请先关闭虫师界面后再执行 run_case")
        job = _start_case_job(case_name, case_id, stop_on_failure,
                              loop_count, retry_count, retry_delay,
                              allow_destructive)
        if wait and timeout_s and timeout_s > 0:
            job["done"].wait(float(timeout_s))
        snap = _job_snapshot(job)
        audit_write("run_case", {"case_name": case_name, "case_id": case_id,
                                 "allow_destructive": allow_destructive},
                    True, "", int((time.time() - started) * 1000))
        return ok(snap)
    except ToolFailure as e:
        audit_write("run_case", {"case_name": case_name, "case_id": case_id,
                                 "allow_destructive": allow_destructive},
                    False, e.code, int((time.time() - started) * 1000))
        return err(e.code, e.message, e.hint)
    except Exception as e:
        code = classify_error(e)
        audit_write("run_case", {"case_name": case_name, "case_id": case_id,
                                 "allow_destructive": allow_destructive},
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


# ----------------------------------------------------------------------
# 批次 1：ADB 只读
# ----------------------------------------------------------------------
def _adb_device_info() -> dict:
    svc = RUNTIME.ensure_device(RUNTIME.serial)
    serial = svc.serial
    battery = _adb_shell(serial, "dumpsys battery")
    battery_info = {}
    for key in ("level", "status", "health", "temperature", "voltage"):
        m = re.search(rf"{key}:\s*(.+)", battery)
        if m:
            battery_info[key] = m.group(1).strip()
    return {
        "serial": serial,
        "model": _adb_shell(serial, "getprop ro.product.model"),
        "android": _adb_shell(serial, "getprop ro.build.version.release"),
        "characteristics": _adb_shell(serial, "getprop ro.build.characteristics"),
        "resolution": _adb_shell(serial, "wm size"),
        "density": _adb_shell(serial, "wm density"),
        "battery": battery_info,
        "storage_data": _adb_shell(serial, "df -h /data"),
        "network": _adb_shell(serial, "ip -f inet addr show"),
    }


def _adb_logcat_snapshot(tags: Optional[str] = None, lines: int = 200) -> dict:
    svc = RUNTIME.ensure_device(RUNTIME.serial)
    serial = svc.serial
    lines = max(1, min(5000, int(lines or 200)))
    cmd = [get_adb_path(), "-s", serial, "logcat", "-d", "-v", "time",
           "-t", str(lines)]
    tag_list = [t for t in re.split(r"[,，\s]+", tags or "") if t]
    if tag_list:
        cmd += ["-s", *tag_list]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15,
                           encoding="utf-8", errors="replace",
                           creationflags=subprocess.CREATE_NO_WINDOW
                           if sys.platform == "win32" else 0)
    except Exception as e:
        raise ToolFailure("ADB_FAILED", f"logcat 读取失败：{e}", "确认设备在线")
    text = r.stdout or r.stderr or ""
    folder = data_path("mcp_logs")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(
        folder, f"logcat_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    tail = "\n".join(text.splitlines()[-100:])
    return {"path": path, "lines": len(text.splitlines()), "tail": tail}


def _adb_pull(remote_path: str, local_name: Optional[str] = None) -> dict:
    if not remote_path or remote_path.startswith("-"):
        raise ToolFailure("INTERNAL_ERROR", "remote_path 不能为空或以 - 开头")
    svc = RUNTIME.ensure_device(RUNTIME.serial)
    serial = svc.serial
    folder = os.path.join(Settings.get_output_dir(),
                          safe_serial(serial or ""), "mcp_pull")
    os.makedirs(folder, exist_ok=True)
    name = (local_name or os.path.basename(remote_path.rstrip("/\\")) or "pull.bin")
    name = re.sub(r"[\\/:*?\"<>|]+", "_", name)
    local = os.path.join(folder, name)
    if os.path.exists(local):
        stem, ext = os.path.splitext(name)
        local = os.path.join(folder, f"{stem}_{datetime.now().strftime('%H%M%S')}{ext}")
    try:
        r = subprocess.run(
            [get_adb_path(), "-s", serial, "pull", remote_path, local],
            capture_output=True, text=True, timeout=60,
            encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW
            if sys.platform == "win32" else 0)
    except Exception as e:
        raise ToolFailure("ADB_FAILED", f"pull 失败：{e}", "确认设备在线")
    out = (r.stdout or "") + (r.stderr or "")
    if not os.path.exists(local) and "error" in out.lower():
        raise ToolFailure("ADB_FAILED", out.strip()[:500], "确认路径存在且可读")
    return {
        "remote": remote_path,
        "path": local,
        "size": os.path.getsize(local) if os.path.exists(local) else 0,
        "output": out.strip()[:500],
    }


# ----------------------------------------------------------------------
# 批次 1：性能 / 语音 / 接口 / 元素库 / 报告（只读）
# ----------------------------------------------------------------------
def _perf_sessions(limit: int = 20) -> dict:
    model = PerfModel()
    sessions = sorted(model.sessions,
                      key=lambda s: s.start_time or "", reverse=True)[:max(1, min(100, limit))]
    out = []
    for s in sessions:
        out.append({
            "session_id": s.id,
            "name": s.name,
            "serial": s.device_serial,
            "package": s.app_package,
            "processes": list(s.app_processes or []),
            "start_time": s.start_time,
            "end_time": s.end_time,
            "sample_interval": s.sample_interval,
            "metrics": list(s.metrics or []),
            "samples": len(s.samples or []),
            "suite": s.suite_name,
            "case_names": list(s.case_names or []),
            "alerts": len(s.alerts or []),
        })
    return {"count": len(out), "sessions": out}


def _perf_baselines() -> dict:
    model = PerfModel()
    out = []
    for b in model.baselines:
        out.append({
            "name": b.name,
            "session_id": b.session_id,
            "package": b.app_package,
            "serial": b.device_serial,
            "created_at": b.created_at,
            "metrics": b.metrics,
        })
    return {"count": len(out), "baselines": out}


def _perf_report(session_id: str, output_dir: Optional[str] = None) -> dict:
    model = PerfModel()
    session = model.get_session(session_id)
    if session is None:
        raise ToolFailure("PERF_SESSION_NOT_FOUND", f"性能会话不存在：{session_id}",
                          "先调用 perf_sessions 查看 session_id")
    base = output_dir or Settings.get_output_dir()
    folder = os.path.join(base, safe_serial(session.device_serial or ""))
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"perf_report_{session.name}.html")
    baseline = model.find_baseline_for_package(session.app_package, session.metrics)
    PerfReportGenerator.generate(session, path, baseline=baseline)
    return {"path": path, "size": os.path.getsize(path), "session_id": session_id}


def _voice_list_cases(group: Optional[str] = None, include_steps: bool = False) -> dict:
    model = VoiceModel()
    group_names = {g.id: g.name for g in model.groups}
    gid = None
    if group:
        gid = next((g.id for g in model.groups if group == g.name), None)
        if gid is None:
            gid = next((g.id for g in model.groups if group in g.name), None)
    out = []
    for case in model.cases:
        if gid and case.group_id != gid:
            continue
        steps = list(case.steps or [])
        item = {
            "case_id": case.id,
            "name": case.name,
            "group": group_names.get(case.group_id, case.group_id),
            "step_count": len(steps),
            "phrase_count": sum(1 for s in steps if s.kind == "phrase"),
            "verify_count": sum(1 for s in steps if s.kind != "phrase"),
        }
        if include_steps:
            item["steps"] = [asdict(s) for s in steps]
        out.append(item)
    return {"count": len(out), "cases": out}


def _api_list(group: Optional[str] = None, include_details: bool = False) -> dict:
    model = ApiModel()
    group_names = {g.id: g.name for g in model.groups}
    gid = None
    if group:
        gid = next((g.id for g in model.groups if group == g.name), None)
        if gid is None:
            gid = next((g.id for g in model.groups if group in g.name), None)
    out = []
    for case in model.cases:
        if gid and case.group_id != gid:
            continue
        item = {
            "case_id": case.id,
            "name": case.name,
            "group": group_names.get(case.group_id, case.group_id),
            "method": case.method,
            "url": case.url,
            "assert_count": len(case.asserts or []),
        }
        if include_details:
            item.update({
                "headers": case.headers,
                "body_type": case.body_type,
                "body": case.body[:2000],
                "asserts": [asdict(a) for a in (case.asserts or [])],
            })
        out.append(item)
    return {
        "count": len(out),
        "cases": out,
        "environments": [
            {"name": e.name, "base_url": e.base_url,
             "variables": [v[0] for v in (e.variables or []) if v]}
            for e in model.envs
        ],
        "current_env": getattr(model, "current_env", ""),
    }


def _element_list(app: Optional[str] = None, module: Optional[str] = None,
                  search: Optional[str] = None, limit: int = 200) -> dict:
    elements = ElementModel().get_elements()
    app_v = (app or "").strip()
    module_v = (module or "").strip()
    search_v = (search or "").strip().lower()
    out = []
    for e in elements:
        if app_v and e.app != app_v:
            continue
        if module_v and e.module != module_v:
            continue
        if search_v and search_v not in " ".join(
                [e.name or "", e.app or "", e.module or "",
                 e.loc_type or "", e.loc_value or ""]).lower():
            continue
        out.append({
            "element_id": e.id, "name": e.name, "app": e.app,
            "module": e.module, "loc_type": e.loc_type,
            "loc_value": e.loc_value, "remark": e.remark,
        })
        if len(out) >= max(1, min(500, int(limit or 200))):
            break
    return {"count": len(out), "elements": out}


def _report_root() -> Path:
    return Path(Settings.get_output_dir()).resolve()


def _report_list(limit: int = 30) -> dict:
    root = _report_root()
    out = []
    if root.exists():
        for p in root.rglob("*.html"):
            if not p.name.startswith(("TestReport_", "perf_report_", "ApiReport_")):
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            out.append({
                "path": str(p), "name": p.name, "size": st.st_size,
                "mtime": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
                "type": ("perf" if p.name.startswith("perf_report_")
                         else "api" if p.name.startswith("ApiReport_")
                         else "auto"),
            })
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return {"count": len(out[:max(1, min(100, limit))]),
            "reports": out[:max(1, min(100, limit))]}


def _report_read(path: str, max_chars: int = 12000) -> dict:
    root = _report_root()
    p = Path(path).resolve()
    try:
        if os.path.commonpath([str(root), str(p)]) != str(root):
            raise ValueError("outside root")
    except Exception:
        raise ToolFailure("PERMISSION_DENIED", "只能读取输出目录下的报告",
                          "先调用 report_list 获取路径")
    if not p.exists() or p.suffix.lower() != ".html":
        raise ToolFailure("INTERNAL_ERROR", "报告文件不存在或不是 HTML",
                          "先调用 report_list 获取路径")
    raw = p.read_text(encoding="utf-8", errors="ignore")
    text = re.sub(r"<script[\s\S]*?</script>", " ", raw, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", html.unescape(text)).strip()
    limit = max(500, min(50000, int(max_chars or 12000)))
    return {"path": str(p), "chars": len(text), "text": text[:limit],
            "truncated": len(text) > limit}


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


def _press_key(key: str, allow_destructive: bool = False) -> dict:
    key_name = _KEY_MAP.get((key or "").lower())
    if not key_name:
        raise ToolFailure("INTERNAL_ERROR", f"未知按键: {key}",
                          "key 用 back/home/menu/volume_up/volume_down/power/camera")
    if key_name in _HIGH_RISK_KEYS and not allow_destructive:
        raise ToolFailure("DESTRUCTIVE_DENIED", "电源键会熄屏或弹出电源菜单",
                          "确认风险后传 allow_destructive=true")
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
_DESTRUCTIVE_APP_ACTIONS = {"清空应用数据", "安装应用", "卸载应用"}
# 不可逆或会明显打断执行环境的步骤：
#   停止/重启应用会中断当前界面；电源键、熄屏、改方向会让后续步骤整体失效
_DISRUPTIVE_APP_ACTIONS = {"停止应用", "重启应用"}
_HIGH_RISK_KEYS = {"电源"}
_HIGH_RISK_SCREEN_ACTIONS = {"熄灭屏幕", "旋转屏幕",
                             "固定方向(竖屏)", "固定方向(横屏)"}


def _high_risk_reason(step_type: str, params: dict) -> str:
    """返回该步骤的风险说明；空串表示无需授权。"""
    params = params or {}
    if step_type == "app_mgr":
        action = str(params.get("action") or "")
        if action in _DESTRUCTIVE_APP_ACTIONS:
            return action
        if action in _DISRUPTIVE_APP_ACTIONS:
            return action
    elif step_type == "physical_key":
        key_name = str(params.get("keyName") or "")
        if key_name in _HIGH_RISK_KEYS:
            return f"{key_name}键"
    elif step_type == "screen_ctrl":
        action = str(params.get("action") or "")
        if action in _HIGH_RISK_SCREEN_ACTIONS:
            return action
    return ""


def _destructive_steps(step_model, case_ids) -> list[dict]:
    found = []
    for cid in case_ids:
        for step in step_model.get_steps_for_case(cid) or []:
            reason = _high_risk_reason(step.type, step.params or {})
            if reason:
                found.append({
                    "case_id": cid,
                    "step": step.name or step.type,
                    "action": reason,
                })
    return found


def _destructive_clean_steps(clean: list[dict]) -> list[dict]:
    found = []
    for item in clean:
        reason = _high_risk_reason(item.get("type"),
                                   item.get("params") or {})
        if reason:
            found.append({"step": item.get("name", "step"),
                          "action": reason})
    return found


def _ensure_destructive_allowed(found: list[dict], allow: bool, context: str):
    if not found or allow:
        return
    sample = "、".join(f"{x['action']}（{x['step']}）" for x in found[:5])
    raise ToolFailure(
        "DESTRUCTIVE_DENIED",
        f"{context}包含危险步骤：{sample}",
        "确认风险后显式传 allow_destructive=true 再执行",
    )


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
               overwrite: bool = False,
               allow_destructive: bool = False) -> dict:
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
    _ensure_destructive_allowed(
        _destructive_clean_steps(clean), allow_destructive, "保存的用例")

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
                     retry_delay: int = 0,
                     allow_destructive: bool = False) -> dict:
    if _case_running():
        raise ToolFailure("DEVICE_BUSY", "已有 run_case / run_suite 正在执行",
                          "先调用 run_case_status 查看，或 run_case_stop 停止")
    if device_jobs.has_active():
        kinds = "/".join(device_jobs.active_kinds())
        raise ToolFailure("DEVICE_BUSY", f"设备任务执行中：{kinds}",
                          "等安装/堆转储等任务结束后再执行套件")
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
    found = _destructive_steps(step_model, valid_ids)
    _ensure_destructive_allowed(found, allow_destructive, "套件")
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
                    timeout_s: float = 60.0,
                    allow_destructive: bool = False) -> str:
    started = time.time()
    try:
        info = probe_gui()
        if info is not None:
            raise ToolFailure("PERMISSION_DENIED", "虫师界面正在运行，MCP 当前为只读模式",
                              "请先关闭虫师界面后再执行 run_suite")
        job = _start_suite_job(suite_name, stop_on_failure,
                               loop_count, retry_count, retry_delay,
                               allow_destructive)
        if wait and timeout_s and timeout_s > 0:
            job["done"].wait(float(timeout_s))
        snap = _job_snapshot(job)
        audit_write("run_suite", {"suite_name": suite_name,
                                  "allow_destructive": allow_destructive},
                    True, "", int((time.time() - started) * 1000))
        return ok(snap)
    except ToolFailure as e:
        audit_write("run_suite", {"suite_name": suite_name,
                                  "allow_destructive": allow_destructive},
                    False, e.code, int((time.time() - started) * 1000))
        return err(e.code, e.message, e.hint)
    except Exception as e:
        code = classify_error(e)
        audit_write("run_suite", {"suite_name": suite_name,
                                  "allow_destructive": allow_destructive},
                    False, code, int((time.time() - started) * 1000))
        return err(code, str(e), _hint_for(code))


# ----------------------------------------------------------------------
# 批次 2：可控写操作（语音 / 接口 / 元素库 / 套件 / 用例 / 推送 / AI）
# ----------------------------------------------------------------------
def _voice_speak(text: str, after_delay: float = 0.0) -> dict:
    text = (text or "").strip()
    if not text:
        raise ToolFailure("INTERNAL_ERROR", "播报文案不能为空")
    cfg = dict(VoiceModel().settings or {})
    svc = get_voice_service()
    try:
        svc.apply_cfg(cfg)
    except Exception:
        pass
    elapsed = svc.speak(text)
    delay = max(0.0, float(after_delay or 0))
    if delay:
        time.sleep(delay)
    return {
        "spoken": True,
        "chars": len(text),
        "speak_seconds": round(float(elapsed or 0), 2),
        "after_delay": delay,
        "engine": cfg.get("engine", ""),
        "voice_id": cfg.get("voice_id", ""),
    }


def _voice_verify(tag: Optional[str] = None,
                  success_keywords: Optional[list] = None,
                  fail_keywords: Optional[list] = None) -> dict:
    cfg = dict(VoiceModel().settings or {})
    verify = dict(cfg.get("verify") or {})
    tag = tag or verify.get("log_tag", "")
    success = success_keywords if success_keywords is not None else verify.get(
        "success_keywords", [])
    fail = fail_keywords if fail_keywords is not None else verify.get(
        "fail_keywords", [])
    svc = VoiceFeedbackService(RUNTIME.serial)
    lines = svc.capture(tag)
    result, hits = VoiceFeedbackService.judge(lines, success, fail)
    return {
        "result": result,
        "tag": tag,
        "line_count": len(lines),
        "matched": list(hits or [])[:10],
        "success_keywords": list(success or []),
        "fail_keywords": list(fail or []),
    }


def _api_run(case_id: Optional[str] = None, case_name: Optional[str] = None,
             env_name: Optional[str] = None,
             allow_destructive: bool = False) -> dict:
    model = ApiModel()
    case = None
    if case_id:
        case = model.get_case(case_id)
    elif case_name:
        exact = [c for c in model.cases if c.name == case_name]
        pool = exact or [c for c in model.cases
                         if case_name.lower() in c.name.lower()]
        if len(pool) > 1:
            raise ToolFailure("CASE_AMBIGUOUS", f"匹配到 {len(pool)} 个接口",
                              "改用 case_id")
        case = pool[0] if pool else None
    if case is None:
        raise ToolFailure("CASE_NOT_FOUND", "接口不存在",
                          "先调用 api_list 查看 case_id")
    method = (case.method or "GET").upper()
    if method not in ("GET", "HEAD", "OPTIONS") and not allow_destructive:
        raise ToolFailure(
            "DESTRUCTIVE_DENIED", f"接口 {method} 可能有副作用：{case.name}",
            "确认风险后传 allow_destructive=true")
    env = model.get_env(env_name) if env_name else model.get_env(None)
    result = ApiService().run_case(case, env)
    try:
        data = result.to_dict()
    except Exception:
        data = {"ok": result.ok, "status_code": result.status_code,
                "elapsed_ms": result.elapsed_ms, "error": result.error}
    return data


def _element_upsert(app: str, module: str, name: str, loc_type: str,
                    loc_value: str, remark: str = "") -> dict:
    if not all((app, module, name, loc_type, loc_value)):
        raise ToolFailure("INTERNAL_ERROR", "app/module/name/loc_type/loc_value 不能为空")
    if loc_type not in LOCATION_TYPE_OPTIONS:
        raise ToolFailure("INTERNAL_ERROR", f"不支持的定位方式：{loc_type}",
                          f"可选：{' / '.join(LOCATION_TYPE_OPTIONS)}")
    stats = ElementModel().upsert_elements([
        (app.strip(), module.strip(), name.strip(), loc_type,
         loc_value.strip(), remark or "")
    ])
    return {"created": stats["created"], "overwritten": stats["overwritten"],
            "app": app, "module": module, "name": name}


def _suite_list() -> dict:
    project = ProjectModel()
    names = {}
    for node in project.get_all_cases():
        names[node.id] = node.name
    out = []
    for suite in SuiteModel().suites:
        out.append({
            "name": suite.name,
            "case_ids": list(suite.case_ids or []),
            "cases": [names.get(cid, cid) for cid in (suite.case_ids or [])],
        })
    return {"count": len(out), "suites": out}


def _suite_save(name: str, case_ids: list, overwrite: bool = False) -> dict:
    name = (name or "").strip()
    if not name or not case_ids:
        raise ToolFailure("INTERNAL_ERROR", "suite_name / case_ids 不能为空")
    project = ProjectModel()
    valid = []
    for cid in case_ids:
        node = project.get_node_by_id(cid)
        if node is None or node.type != "case":
            raise ToolFailure("CASE_NOT_FOUND", f"用例不存在：{cid}",
                              "先调用 list_cases")
        valid.append(cid)
    model = SuiteModel()
    existing = model.get_suite_by_name(name)
    if existing and not overwrite:
        raise ToolFailure("CASE_EXISTS", f"套件已存在：{name}",
                          "传 overwrite=true 覆盖")
    if existing:
        model.update_suite(name, valid)
        action = "updated"
    else:
        model.create_suite(name, valid)
        action = "created"
    return {"name": name, "case_ids": valid, "action": action}


def _suite_delete(name: str, allow_destructive: bool = False) -> dict:
    if not allow_destructive:
        raise ToolFailure("DESTRUCTIVE_DENIED", f"删除套件需要显式授权：{name}",
                          "确认后传 allow_destructive=true")
    ok_deleted = SuiteModel().delete_suite(name)
    if not ok_deleted:
        raise ToolFailure("SUITE_NOT_FOUND", f"套件不存在：{name}")
    return {"deleted": True, "name": name}


def _case_rename(case_id: str, new_name: str) -> dict:
    new_name = (new_name or "").strip()
    if not new_name:
        raise ToolFailure("INTERNAL_ERROR", "new_name 不能为空")
    project = ProjectModel()
    node = project.get_node_by_id(case_id)
    if node is None or node.type != "case":
        raise ToolFailure("CASE_NOT_FOUND", f"用例不存在：{case_id}")
    old = node.name
    node.name = new_name
    project.save()
    return {"case_id": case_id, "old_name": old, "name": new_name}


def _case_copy(case_id: str, new_name: Optional[str] = None,
               target_module: Optional[str] = None) -> dict:
    project = ProjectModel()
    node = project.get_node_by_id(case_id)
    if node is None or node.type != "case":
        raise ToolFailure("CASE_NOT_FOUND", f"用例不存在：{case_id}")
    parent, _, _ = project.get_parent_and_index(case_id)
    if parent is None or parent.type != "folder":
        raise ToolFailure("INTERNAL_ERROR", "用例不在功能模块下，无法复制")
    target = parent
    if target_module:
        target = next((n for n in parent.children
                       if n.type == "folder" and n.name == target_module), None)
        if target is None:
            # 同项目下找模块
            project_node = parent
            while project_node and project_node.type != "project":
                project_node, _, _ = project.get_parent_and_index(project_node.id)
            if project_node:
                target = next((n for n in project_node.children
                               if n.type == "folder" and n.name == target_module), None)
        if target is None:
            raise ToolFailure("INTERNAL_ERROR", f"目标模块不存在：{target_module}")
    name = (new_name or f"{node.name} 副本").strip()
    new_case = project.create_case(target.id, name)
    step_model = StepModel()
    for step in step_model.get_steps_for_case(case_id):
        step_model.add_step_to_case(
            new_case.id, Step(0, step.type, step.name, dict(step.params or {})))
    return {"case_id": new_case.id, "name": new_case.name,
            "source": case_id, "steps": len(step_model.get_steps_for_case(new_case.id))}


def _case_delete(case_id: str, allow_destructive: bool = False) -> dict:
    if not allow_destructive:
        raise ToolFailure("DESTRUCTIVE_DENIED", f"删除用例需要显式授权：{case_id}",
                          "确认后传 allow_destructive=true")
    project = ProjectModel()
    node = project.get_node_by_id(case_id)
    if node is None or node.type != "case":
        raise ToolFailure("CASE_NOT_FOUND", f"用例不存在：{case_id}")
    step_model = StepModel()
    for sid in list(step_model.case_steps.get(case_id, [])):
        step_model._steps.pop(sid, None)
    step_model.case_steps.pop(case_id, None)
    step_model.save()
    SuiteModel().remove_case_from_all_suites(case_id)
    project.delete_node(case_id)
    return {"deleted": True, "case_id": case_id}


def _step_move(case_id: str, from_index: int, to_index: int) -> dict:
    step_model = StepModel()
    order = step_model.case_steps.get(case_id, [])
    if not order:
        raise ToolFailure("CASE_NOT_FOUND", f"用例没有步骤：{case_id}")
    if not (0 <= from_index < len(order) and 0 <= to_index < len(order)):
        raise ToolFailure("INTERNAL_ERROR",
                          f"索引越界：0..{len(order) - 1}")
    step_model.move_step(case_id, from_index, to_index)
    return {"case_id": case_id, "from": from_index, "to": to_index,
            "count": len(step_model.case_steps.get(case_id, []))}


def _step_update(case_id: str, step_index: int, name: Optional[str] = None,
                 params: Optional[dict] = None) -> dict:
    step_model = StepModel()
    steps = step_model.get_steps_for_case(case_id)
    if not steps:
        raise ToolFailure("CASE_NOT_FOUND", f"用例没有步骤：{case_id}")
    if not (1 <= step_index <= len(steps)):
        raise ToolFailure("INTERNAL_ERROR", f"step_index 范围 1..{len(steps)}")
    step_model.update_step(steps[step_index - 1].id, name=name, params=params)
    return {"case_id": case_id, "step_index": step_index, "updated": True}


def _adb_push(local_path: str, remote_path: str) -> dict:
    if not os.path.isfile(local_path):
        raise ToolFailure("INTERNAL_ERROR", f"本地文件不存在：{local_path}")
    if not remote_path or remote_path.startswith("-"):
        raise ToolFailure("INTERNAL_ERROR", "remote_path 不能为空或以 - 开头")
    svc = RUNTIME.ensure_device(RUNTIME.serial)
    serial = svc.serial
    try:
        r = subprocess.run(
            [get_adb_path(), "-s", serial, "push", local_path, remote_path],
            capture_output=True, text=True, timeout=60,
            encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW
            if sys.platform == "win32" else 0)
    except Exception as e:
        raise ToolFailure("ADB_FAILED", f"push 失败：{e}", "确认设备在线")
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    if r.returncode != 0:
        raise ToolFailure("ADB_FAILED", out[:500], "确认远端目录可写")
    return {"local": local_path, "remote": remote_path,
            "size": os.path.getsize(local_path), "output": out[:500]}


# ----------------------------------------------------------------------
# 批次 3.1：设备影响型（安装 / 卸载 / 清数据 / 堆转储 / 重启）
# ----------------------------------------------------------------------
_PACKAGE_RE = re.compile(r"^[A-Za-z0-9._]+$")

_INSTALL_HINTS = {
    "INSTALL_FAILED_ALREADY_EXISTS": "应用已存在；确认后传 replace=true",
    "INSTALL_FAILED_VERSION_DOWNGRADE": "版本降级被拒绝；确认后传 replace=true",
    "INSTALL_FAILED_UPDATE_INCOMPATIBLE": "签名不一致；先 app_uninstall 再安装",
    "INSTALL_FAILED_INSUFFICIENT_STORAGE": "设备存储空间不足",
    "INSTALL_FAILED_INVALID_APK": "APK 无效或已损坏",
    "INSTALL_PARSE_FAILED": "APK 解析失败；确认文件完整且架构匹配",
    "INSTALL_FAILED_TEST_ONLY": "测试包需要 -t，已默认携带",
}


def _validate_package(package: str) -> str:
    package = (package or "").strip()
    if not _PACKAGE_RE.match(package):
        raise ToolFailure("INTERNAL_ERROR", f"包名不合法：{package!r}",
                          "只允许字母、数字、点和下划线")
    return package


def _adb_run(serial: str, args: list, timeout: float = 60):
    try:
        return subprocess.run(
            [get_adb_path(), "-s", serial, *args],
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW
            if sys.platform == "win32" else 0)
    except subprocess.TimeoutExpired:
        raise ToolFailure("TIMEOUT", f"adb {' '.join(args[:3])} 超时（{int(timeout)} 秒）",
                          "确认设备在线；必要时先重启 adb")
    except Exception as e:
        raise ToolFailure("ADB_FAILED", f"adb 执行失败：{e}", "确认设备在线")


def _jobbed(kind: str, meta: dict, fn, timeout: float) -> dict:
    wrapped = device_jobs.run_blocking(kind, fn, timeout, meta=meta)
    if wrapped.get("status") != "succeeded":
        raise ToolFailure("TIMEOUT", wrapped.get("error") or "任务超时",
                          "查看 data/mcp_jobs 下的状态文件；确认设备在线")
    data = dict(wrapped.get("result") or {})
    data["run_id"] = wrapped.get("run_id")
    return data


def _app_install(apk_path: str, replace: bool = True,
                 grant_permissions: bool = True,
                 allow_destructive: bool = False) -> dict:
    if not allow_destructive:
        raise ToolFailure("DESTRUCTIVE_DENIED", f"安装应用需要显式授权：{apk_path}",
                          "确认风险后传 allow_destructive=true")
    apk_path = os.path.abspath((apk_path or "").strip())
    if not os.path.isfile(apk_path) or not apk_path.lower().endswith(".apk"):
        raise ToolFailure("INTERNAL_ERROR", f"APK 不存在：{apk_path}",
                          "传本机 apk 的绝对路径")
    serial = RUNTIME.ensure_device(RUNTIME.serial).serial
    args = ["install"]
    if replace:
        args.append("-r")
    args.extend(["-t", "-d"])
    if grant_permissions:
        args.append("-g")
    args.append(apk_path)

    def run():
        r = _adb_run(serial, args, timeout=120)
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        if r.returncode != 0 or "Failure" in out:
            hint = next((h for k, h in _INSTALL_HINTS.items() if k in out),
                        "确认 APK 与设备架构匹配、签名正常")
            raise ToolFailure("ADB_FAILED", out[:800] or "安装失败", hint)
        return {"apk": apk_path, "size": os.path.getsize(apk_path),
                "replace": replace, "grant_permissions": grant_permissions,
                "output": out[:500]}

    return _jobbed("app_install", {"apk": apk_path, "serial": serial},
                   run, timeout=135)


def _app_uninstall(package: str, keep_data: bool = False,
                   allow_destructive: bool = False) -> dict:
    package = _validate_package(package)
    if not allow_destructive:
        raise ToolFailure("DESTRUCTIVE_DENIED", f"卸载应用需要显式授权：{package}",
                          "确认风险后传 allow_destructive=true")
    serial = RUNTIME.ensure_device(RUNTIME.serial).serial
    args = ["uninstall"] + (["-k"] if keep_data else []) + [package]

    def run():
        r = _adb_run(serial, args, timeout=60)
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        if r.returncode != 0 or "Success" not in out:
            hint = ("设备上没有该应用" if "not installed" in out.lower()
                    else "确认包名正确；系统应用可能不允许卸载")
            raise ToolFailure("ADB_FAILED", out[:500] or "卸载失败", hint)
        return {"package": package, "keep_data": keep_data, "output": out[:200]}

    return _jobbed("app_uninstall", {"package": package, "serial": serial},
                   run, timeout=70)


def _app_clear_data(package: str, allow_destructive: bool = False) -> dict:
    package = _validate_package(package)
    if not allow_destructive:
        raise ToolFailure("DESTRUCTIVE_DENIED", f"清空应用数据需要显式授权：{package}",
                          "确认风险后传 allow_destructive=true")
    serial = RUNTIME.ensure_device(RUNTIME.serial).serial

    def run():
        r = _adb_run(serial, ["shell", "pm", "clear", package], timeout=30)
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        if r.returncode != 0 or "Success" not in out:
            raise ToolFailure("ADB_FAILED", out[:300] or "清空失败",
                              "确认包名正确且应用已安装")
        return {"package": package, "output": out[:200]}

    return _jobbed("app_clear_data", {"package": package, "serial": serial},
                   run, timeout=40)


def _hprof_dump(package: str, pull_to_host: bool = True) -> dict:
    package = _validate_package(package)
    serial = RUNTIME.ensure_device(RUNTIME.serial).serial
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    device_path = f"/data/local/tmp/heap_{package}_{stamp}.hprof"

    def run():
        pid_out = _adb_run(serial, ["shell", "pidof", package], timeout=10)
        pids = (pid_out.stdout or "").split()
        if pid_out.returncode != 0 or not pids:
            raise ToolFailure("PROCESS_NOT_FOUND", f"进程不存在：{package}",
                              "先启动应用（launch_app），确认包名正确")
        pid = pids[0]
        dump = _adb_run(serial, ["shell", "am", "dumpheap", pid, device_path],
                        timeout=60)
        dump_out = ((dump.stdout or "") + (dump.stderr or "")).strip()
        if dump.returncode != 0:
            raise ToolFailure("ADB_FAILED", dump_out[:500] or "堆转储失败",
                              "确认应用可调试；部分系统应用不允许 dumpheap")
        size = 0
        for _ in range(20):
            stat = _adb_run(serial, ["shell", "stat", "-c", "%s", device_path],
                            timeout=10)
            text = (stat.stdout or "").strip()
            if stat.returncode == 0 and text.isdigit() and int(text) > 0:
                size = int(text)
                break
            time.sleep(1)
        if size <= 0:
            raise ToolFailure("ADB_FAILED", "堆转储文件未生成",
                              f"检查设备端 {device_path} 是否有写权限")
        if not pull_to_host:
            return {"package": package, "pid": pid, "device_path": device_path,
                    "size": size, "pulled": False}

        folder = os.path.join(Settings.get_output_dir(), safe_serial(serial),
                              "hprof")
        os.makedirs(folder, exist_ok=True)
        local = os.path.join(folder, f"heap_{package}_{stamp}.hprof")
        pulled = _adb_run(serial, ["pull", device_path, local], timeout=120)
        if pulled.returncode != 0 or not os.path.isfile(local):
            raise ToolFailure(
                "ADB_FAILED",
                ((pulled.stdout or "") + (pulled.stderr or "")).strip()[:500],
                f"文件保留在设备 {device_path}，可稍后用 adb_pull 手动拉取")
        _adb_run(serial, ["shell", "rm", "-f", device_path], timeout=15)
        return {"package": package, "pid": pid, "device_path": device_path,
                "path": local, "size": os.path.getsize(local), "pulled": True}

    return _jobbed("hprof_dump", {"package": package, "serial": serial},
                   run, timeout=220)


def _device_reboot(mode: str = "system",
                   allow_destructive: bool = False) -> dict:
    mode = (mode or "system").strip().lower()
    if mode != "system":
        raise ToolFailure("INTERNAL_ERROR", f"仅支持 mode=system，收到：{mode}",
                          "recovery / bootloader 不开放，请用工具箱手动执行")
    if not allow_destructive:
        raise ToolFailure("DESTRUCTIVE_DENIED", "重启设备需要显式授权",
                          "确认风险后传 allow_destructive=true")
    serial = RUNTIME.ensure_device(RUNTIME.serial).serial

    def run():
        r = _adb_run(serial, ["reboot"], timeout=15)
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        if r.returncode != 0:
            raise ToolFailure("ADB_FAILED", out[:300] or "重启命令下发失败",
                              "确认设备在线且 adb 有权限")
        return {"serial": serial, "mode": mode, "accepted": True,
                "note": "设备将在约 30 ~ 60 秒内重启，之后需重新确认 adb 连接"}

    return _jobbed("device_reboot", {"serial": serial, "mode": mode},
                   run, timeout=25)


# ----------------------------------------------------------------------
# 批次 3.2：Monkey / 录屏 / 实时 logcat（run_id + stop + 自动收尾）
# ----------------------------------------------------------------------
_ACTIVE_JOB_STATUS = ("running", "stopping", "detached")


def _job_or_fail(run_id: str) -> dict:
    job = device_jobs.get((run_id or "").strip())
    if job is None:
        raise ToolFailure("JOB_NOT_FOUND", f"任务不存在：{run_id}",
                          "先调用对应的 start 工具获取 run_id")
    return job


def _runner_for(job: dict, factory):
    raw_job = device_jobs.raw(job["run_id"]) or {}
    runner = raw_job.get("_runner")
    if runner is not None:
        return runner
    serial = (job.get("meta") or {}).get("serial") or \
        RUNTIME.ensure_device(RUNTIME.serial).serial
    return factory(serial)


def _monkey_start(packages: list, events: int = 1000, throttle_ms: int = 800,
                  seed: Optional[int] = None, duration_h: float = 24.0,
                  verbose: int = 0, flags: Optional[dict] = None,
                  pcts: Optional[dict] = None,
                  use_whitelist: bool = False) -> dict:
    try:
        packages = MonkeyRunner.validate_packages(packages or [])
    except ValueError as e:
        raise ToolFailure("INTERNAL_ERROR", str(e))
    events = int(events or 0)
    if not (1 <= events <= 10_000_000):
        raise ToolFailure("INTERNAL_ERROR", f"events 范围 1..10000000：{events}")
    throttle_ms = int(throttle_ms or 0)
    if not (0 <= throttle_ms <= 10_000):
        raise ToolFailure("INTERNAL_ERROR", f"throttle_ms 范围 0..10000：{throttle_ms}")
    verbose = int(verbose or 0)
    if not (0 <= verbose <= 2):
        raise ToolFailure("INTERNAL_ERROR", f"verbose 范围 0..2：{verbose}")
    duration_h = float(duration_h or 24)
    if not (0.01 <= duration_h <= 72):
        raise ToolFailure("INTERNAL_ERROR", f"duration_h 范围 0.01..72：{duration_h}")
    bad = [k for k in (flags or {})
           if k not in ("ignore_crashes", "ignore_timeouts", "ignore_security",
                        "ignore_native", "monitor_native")]
    if bad:
        raise ToolFailure("INTERNAL_ERROR", f"未知的 flags：{', '.join(bad)}",
                          "可选：ignore_crashes / ignore_timeouts / "
                          "ignore_security / ignore_native / monitor_native")
    bad_pcts = [k for k in (pcts or {}) if k not in MonkeyRunner.DEFAULT_PCTS]
    if bad_pcts:
        raise ToolFailure("INTERNAL_ERROR", f"未知的事件比例：{', '.join(bad_pcts)}",
                          "可选：touch / motion / trackball / nav / majornav / "
                          "appswitch / flip / anyevent / syskeys")
    for key, value in (pcts or {}).items():
        if not (0 <= int(value) <= 100):
            raise ToolFailure("INTERNAL_ERROR", f"pct {key} 范围 0..100：{value}")

    serial = RUNTIME.ensure_device(RUNTIME.serial).serial
    duration_s = int(duration_h * 3600)
    job = device_jobs.create(
        "monkey", mode="device",
        meta={"serial": serial, "packages": packages, "events": events,
              "duration_h": duration_h, "use_whitelist": bool(use_whitelist)},
        deadline_ts=time.time() + duration_s + 300)
    runner = MonkeyRunner(serial)
    try:
        info = runner.start(packages, events,
                            int(seed if seed is not None
                                else int(time.time() * 1000) % 1_000_000),
                            throttle_ms, verbose, duration_s,
                            tag=job["run_id"], flags=flags, pcts=pcts,
                            use_whitelist=use_whitelist)
    except Exception as e:
        device_jobs.finish(job, "failed", error=str(e))
        raise ToolFailure("ADB_FAILED", str(e),
                          "确认设备在线、包名已安装；Monkey 若已在跑先 monkey_stop")

    pid = info["pid"]
    whitelist_remote = info.get("whitelist_remote", "")
    raw_job = device_jobs.raw(job["run_id"])
    raw_job["_runner"] = runner
    device_jobs.update_meta(job["run_id"], {
        "pid": pid, "log_path": info["log_path"],
        "device_timeout": info["device_timeout"],
        "whitelist_remote": whitelist_remote})

    def _stop(reason: str) -> dict:
        res = runner.stop(pid, whitelist_remote)
        status = "stopped" if reason == "user" else "timeout"
        device_jobs.finish(raw_job, status, result=res, stop_reason=reason)
        return res

    device_jobs.set_stop(job["run_id"], _stop)
    return {"run_id": job["run_id"], "status": "running", "pid": pid,
            "packages": packages, "events": events,
            "throttle_ms": throttle_ms, "duration_h": duration_h,
            "device_timeout": info["device_timeout"],
            "use_whitelist": bool(use_whitelist),
            "device_log": info["log_path"],
            "deadline_s": duration_s}


def _monkey_status(run_id: str) -> dict:
    job = _job_or_fail(run_id)
    meta = job.get("meta") or {}
    runner = _runner_for(job, MonkeyRunner)
    pid = str(meta.get("pid") or "")
    alive = runner.alive(pid) if pid else bool(runner.running_pid())
    tail = runner.tail(meta.get("log_path") or "", 20)
    status = job.get("status")
    raw_job = device_jobs.raw(job["run_id"])
    if status == "detached" and alive:
        device_jobs.mark_running(job["run_id"])
        status = "running"
    elif status in ("running", "detached") and not alive:
        if meta.get("whitelist_remote"):
            runner.remove_whitelist(meta["whitelist_remote"])
        device_jobs.finish(raw_job, "finished",
                           result={"alive": False, "tail": tail},
                           stop_reason="process_exited")
        status = "finished"
    return {"run_id": job["run_id"], "status": status, "alive": alive,
            "pid": pid, "packages": meta.get("packages", []),
            "events": meta.get("events"), "tail": tail,
            "device_log": meta.get("log_path", "")}


def _monkey_stop(run_id: str) -> dict:
    job = _job_or_fail(run_id)
    if job.get("status") not in _ACTIVE_JOB_STATUS:
        return {"run_id": job["run_id"], "status": job.get("status"),
                "message": "任务已结束，无需停止"}
    raw_job = device_jobs.raw(job["run_id"]) or {}
    if raw_job.get("_stop_fn"):
        device_jobs.request_stop(job["run_id"], "user")
    else:
        # MCP 重启后重新接上：按 meta 里的设备 PID 停
        runner = _runner_for(job, MonkeyRunner)
        meta = job.get("meta") or {}
        res = runner.stop(str(meta.get("pid") or ""),
                          meta.get("whitelist_remote") or "")
        device_jobs.finish(raw_job, "stopped", result=res, stop_reason="user")
    device_jobs.wait(job["run_id"], 20)
    snap = device_jobs.get(job["run_id"]) or job
    return {"run_id": job["run_id"], "status": snap.get("status"),
            "result": snap.get("result")}


def _screenrecord_start(duration_s: int = 180,
                        bitrate_mbps: float = 8.0) -> dict:
    duration_s = int(duration_s or 180)
    if not (5 <= duration_s <= 180):
        raise ToolFailure("INTERNAL_ERROR", f"duration_s 范围 5..180：{duration_s}")
    bitrate_mbps = float(bitrate_mbps or 8)
    if not (0.5 <= bitrate_mbps <= 50):
        raise ToolFailure("INTERNAL_ERROR", f"bitrate_mbps 范围 0.5..50：{bitrate_mbps}")

    serial = RUNTIME.ensure_device(RUNTIME.serial).serial
    runner = ScreenRecordRunner(serial, Settings.get_output_dir())
    job = device_jobs.create(
        "screenrecord", mode="host",
        meta={"serial": serial, "duration_s": duration_s,
              "bitrate_mbps": bitrate_mbps},
        deadline_ts=time.time() + duration_s + 60)
    try:
        info = runner.start(duration_s, bitrate_mbps)
    except Exception as e:
        device_jobs.finish(job, "failed", error=str(e))
        raise ToolFailure("ADB_FAILED", str(e),
                          "确认设备在线；已有录屏时先 screenrecord_stop")

    raw_job = device_jobs.raw(job["run_id"])
    raw_job["_runner"] = runner
    device_jobs.update_meta(job["run_id"], {
        "path": info["path"], "remote_path": info["remote_path"]})

    def _finish_from_runner(status: str, reason: str = ""):
        try:
            art = runner.finalize()
        except Exception as e:
            device_jobs.finish(raw_job, "failed", error=str(e),
                               stop_reason=reason)
            return {"error": str(e)}
        device_jobs.finish(raw_job, status, result=art, stop_reason=reason)
        return art

    def _monitor():
        rc = runner.wait(duration_s + 60)
        if raw_job.get("status") != "running":
            return
        _finish_from_runner("succeeded" if rc == 0 else "finished")

    def _stop(reason: str) -> dict:
        try:
            art = runner.stop()
        except Exception as e:
            device_jobs.finish(raw_job, "failed", error=str(e),
                               stop_reason=reason)
            return {"error": str(e)}
        device_jobs.finish(raw_job, "stopped", result=art, stop_reason=reason)
        return art

    device_jobs.set_stop(job["run_id"], _stop)
    threading.Thread(target=_monitor, daemon=True,
                     name=f"mcp-sr-{job['run_id']}").start()
    return {"run_id": job["run_id"], "status": "running",
            "path": info["path"], "device_path": info["remote_path"],
            "duration_s": duration_s, "bitrate_mbps": bitrate_mbps}


def _screenrecord_stop(run_id: str) -> dict:
    job = _job_or_fail(run_id)
    status = job.get("status")
    meta = job.get("meta") or {}
    if status in _ACTIVE_JOB_STATUS:
        device_jobs.request_stop(run_id, "user")
        device_jobs.wait(run_id, 90)
        snap = device_jobs.get(run_id) or job
    else:
        snap = job
    if status == "stale":
        # MCP 退出导致主机侧中断：清掉设备端可能残留的录屏进程和文件
        try:
            runner = ScreenRecordRunner(
                meta.get("serial") or "", Settings.get_output_dir())
            for pid in runner.running_pids():
                runner._adb(["shell", "kill", "-9", pid], timeout=5)
            if meta.get("remote_path"):
                runner._adb(["shell", "rm", "-f", meta["remote_path"]],
                            timeout=15)
        except Exception:
            pass
    return {"run_id": run_id, "status": snap.get("status"),
            "path": meta.get("path", ""),
            "size": (snap.get("result") or {}).get("size", 0),
            "stop_reason": snap.get("stop_reason", "")}


def _logcat_start(tags: Optional[list] = None, max_lines: int = 20000,
                  max_seconds: int = 300,
                  clear_first: bool = False) -> dict:
    max_lines = int(max_lines or 20000)
    if not (100 <= max_lines <= 200000):
        raise ToolFailure("INTERNAL_ERROR", f"max_lines 范围 100..200000：{max_lines}")
    max_seconds = int(max_seconds or 300)
    if not (5 <= max_seconds <= 3600):
        raise ToolFailure("INTERNAL_ERROR", f"max_seconds 范围 5..3600：{max_seconds}")

    serial = RUNTIME.ensure_device(RUNTIME.serial).serial
    runner = LogcatStream(serial, Settings.get_output_dir())
    job = device_jobs.create(
        "logcat", mode="host",
        meta={"serial": serial, "tags": list(tags or [])},
        deadline_ts=time.time() + max_seconds + 30)
    try:
        info = runner.start(tags, max_lines, max_seconds, clear_first)
    except Exception as e:
        device_jobs.finish(job, "failed", error=str(e))
        raise ToolFailure("ADB_FAILED", str(e), "确认设备在线且 logcat 可用")

    raw_job = device_jobs.raw(job["run_id"])
    raw_job["_runner"] = runner
    device_jobs.update_meta(job["run_id"], {"path": info["path"]})

    def _monitor():
        runner.wait(max_seconds + 15)
        if raw_job.get("status") != "running":
            return
        try:
            stats = runner.stop()
        except Exception as e:
            device_jobs.finish(raw_job, "failed", error=str(e))
            return
        device_jobs.finish(raw_job, "succeeded", result=stats)

    def _stop(reason: str) -> dict:
        try:
            stats = runner.stop()
        except Exception as e:
            device_jobs.finish(raw_job, "failed", error=str(e),
                               stop_reason=reason)
            return {"error": str(e)}
        device_jobs.finish(raw_job, "stopped", result=stats,
                           stop_reason=reason)
        return stats

    device_jobs.set_stop(job["run_id"], _stop)
    threading.Thread(target=_monitor, daemon=True,
                     name=f"mcp-logcat-{job['run_id']}").start()
    return {"run_id": job["run_id"], "status": "running",
            "path": info["path"], "tags": info["tags"],
            "max_lines": max_lines, "max_seconds": max_seconds}


def _logcat_stop(run_id: str) -> dict:
    job = _job_or_fail(run_id)
    status = job.get("status")
    meta = job.get("meta") or {}
    if status in _ACTIVE_JOB_STATUS:
        device_jobs.request_stop(run_id, "user")
        device_jobs.wait(run_id, 30)
        snap = device_jobs.get(run_id) or job
    else:
        snap = job
    result = snap.get("result") or {}
    path = meta.get("path") or result.get("path", "")
    lines = result.get("lines", 0)
    reason = result.get("stop_reason") or snap.get("stop_reason", "")
    if status == "stale" and path:
        try:
            runner = LogcatStream(
                meta.get("serial") or "", Settings.get_output_dir())
            runner._clean_remote()
        except Exception:
            pass
        if not lines and os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    lines = sum(1 for _ in f)
            except Exception:
                lines = 0
    return {"run_id": run_id, "status": snap.get("status"),
            "path": path, "lines": lines,
            "stop_reason": reason}


def _logcat_read(run_id: str, lines: int = 200) -> dict:
    job = _job_or_fail(run_id)
    meta = job.get("meta") or {}
    runner = _runner_for(
        job, lambda serial: LogcatStream(serial, Settings.get_output_dir()))
    tail = runner.tail(lines)
    path = meta.get("path") or (job.get("result") or {}).get("path", "")
    return {"run_id": run_id, "status": job.get("status"),
            "path": path, "tail": tail,
            "line_count": (job.get("result") or {}).get("lines", 0)}


# ----------------------------------------------------------------------
# 批次 3.3：性能采集 / tcpdump
# ----------------------------------------------------------------------
def _perf_start(package: str, processes: Optional[list] = None,
                metrics: Optional[list] = None,
                probes: Optional[list] = None, interval: float = 5.0,
                duration_minutes: float = 60.0,
                suite_name: Optional[str] = None,
                case_ids: Optional[list] = None,
                loop: int = 1,
                stop_on_failure: bool = False,
                suite_type: str = "case") -> dict:
    if not package:
        raise ToolFailure("INTERNAL_ERROR", "package 不能为空")
    metric_list = list(metrics or list(ALLOWED_METRICS))
    bad = [m for m in metric_list if m not in ALLOWED_METRICS]
    if bad:
        raise ToolFailure("INTERNAL_ERROR", f"不支持的指标：{', '.join(bad)}",
                          f"可选：{' / '.join(ALLOWED_METRICS)}")
    probe_list = list(probes or [])
    bad = [p for p in probe_list if p not in ALLOWED_PROBES]
    if bad:
        raise ToolFailure("INTERNAL_ERROR", f"不支持的探针：{', '.join(bad)}",
                          f"可选：{' / '.join(ALLOWED_PROBES)}")
    duration_minutes = float(duration_minutes or 0)
    if not (0 < duration_minutes <= 180):
        raise ToolFailure("INTERNAL_ERROR",
                          f"duration_minutes 范围 0~180：{duration_minutes}")

    svc = RUNTIME.ensure_device(RUNTIME.serial)
    scenario = None
    suite_type = (suite_type or "case").strip().lower()
    if suite_type not in ("case", "voice"):
        raise ToolFailure("INTERNAL_ERROR",
                          f"suite_type 只支持 case / voice：{suite_type}")
    target_ids = [c for c in (case_ids or []) if c]
    if suite_name or target_ids:
        if suite_name and suite_type == "voice":
            name = suite_name
            if name.startswith(VOICE_SUITE_PREFIX):
                name = name[len(VOICE_SUITE_PREFIX):]
            voice_model = VoiceModel()
            suite = voice_model.get_suite(name)
            if suite is None:
                raise ToolFailure("SUITE_NOT_FOUND",
                                  f"语音套件不存在：{suite_name}",
                                  "先调用 voice_list_cases 确认套件名")
            target_ids = [cid for cid in (suite.case_ids or [])
                          if voice_model.get_case(cid)]
            if not target_ids:
                raise ToolFailure("SUITE_NOT_FOUND", "语音套件里没有有效用例")
            suite_name = f"{VOICE_SUITE_PREFIX}{name}"
        elif suite_name:
            suite = SuiteModel().get_suite_by_name(suite_name)
            if suite is None:
                raise ToolFailure("SUITE_NOT_FOUND", f"套件不存在：{suite_name}",
                                  "先在执行页保存套件，或检查套件名")
            target_ids = list(suite.case_ids or [])
        if suite_type == "case":
            project = ProjectModel()
            valid = [cid for cid in target_ids
                     if (project.get_node_by_id(cid) is not None
                         and project.get_node_by_id(cid).type == "case")]
            if not valid:
                raise ToolFailure("SUITE_NOT_FOUND", "场景里没有有效用例",
                                  "检查套件引用的用例是否已被删除")
        else:
            valid = target_ids
        loop = int(loop or 1)
        if not (1 <= loop <= 99):
            raise ToolFailure("INTERNAL_ERROR", f"loop 范围 1..99：{loop}")
        scenario = {"type": suite_type,
                    "suite_name": suite_name or "",
                    "case_ids": valid, "loop": loop,
                    "stop_on_failure": bool(stop_on_failure)}

    runner = PerfRunner(svc.device, svc.serial, Settings.get_output_dir(),
                        device_service=svc)
    job = device_jobs.create(
        "perf", mode="host",
        meta={"serial": svc.serial, "package": package,
              "metrics": metric_list, "probes": probe_list,
              "interval": interval,
              "duration_minutes": duration_minutes,
              "suite_name": suite_name or ""},
        deadline_ts=time.time() + duration_minutes * 60 + 120)
    try:
        info = runner.start(package, processes, metric_list, probe_list,
                            interval, duration_minutes, scenario)
    except ValueError as e:
        device_jobs.finish(job, "failed", error=str(e))
        code = ("PROCESS_NOT_FOUND" if "未在运行" in str(e)
                else "INTERNAL_ERROR")
        raise ToolFailure(code, str(e),
                          "先启动应用再采集" if code == "PROCESS_NOT_FOUND" else "")
    except Exception as e:
        device_jobs.finish(job, "failed", error=str(e))
        raise ToolFailure("ADB_FAILED", str(e), "确认设备在线且应用可读")

    raw_job = device_jobs.raw(job["run_id"])
    raw_job["_runner"] = runner
    device_jobs.update_meta(job["run_id"], {
        "session_id": info["session_id"], "session_name": info["name"]})

    def _stop(reason: str) -> dict:
        summary = runner.stop()
        status = "stopped" if reason == "user" else "finished"
        device_jobs.finish(raw_job, status, result=summary, stop_reason=reason)
        return summary

    def _monitor():
        runner.wait()
        if raw_job.get("status") != "running":
            return
        summary = runner.stop()
        device_jobs.finish(raw_job, "succeeded", result=summary,
                           stop_reason=summary.get("stop_reason", ""))

    device_jobs.set_stop(job["run_id"], _stop)
    threading.Thread(target=_monitor, daemon=True,
                     name=f"mcp-perf-{job['run_id']}").start()
    return {"run_id": job["run_id"], "status": "running", **info}


def _perf_status(run_id: str) -> dict:
    job = _job_or_fail(run_id)
    raw_job = device_jobs.raw(run_id) or {}
    runner = raw_job.get("_runner")
    data = {"run_id": run_id, "status": job.get("status"),
            "session_id": (job.get("meta") or {}).get("session_id", "")}
    if runner is not None:
        data.update(runner.status())
    elif job.get("status") == "stale":
        data["note"] = "MCP 进程退出导致采集中断，会话可能未落盘"
    return data


def _perf_stop(run_id: str) -> dict:
    job = _job_or_fail(run_id)
    raw_job = device_jobs.raw(run_id) or {}
    if job.get("status") in _ACTIVE_JOB_STATUS and not raw_job.get("_stop_fn"):
        # MCP 进程退出导致采集中断：没有线程可停，直接标记 stale
        device_jobs.finish(raw_job, "stale",
                           error="MCP 进程已退出，性能采集已中断",
                           stop_reason="mcp_restart")
        snap = device_jobs.get(run_id) or job
        return {"run_id": run_id, "status": snap.get("status"),
                "message": "MCP 进程已退出，采集线程不存在；会话可能未落盘"}
    if job.get("status") not in _ACTIVE_JOB_STATUS:
        return {"run_id": run_id, "status": job.get("status"),
                "summary": job.get("result") or {},
                "message": "任务已结束，无需停止"}
    device_jobs.request_stop(run_id, "user")
    device_jobs.wait(run_id, 60)
    snap = device_jobs.get(run_id) or job
    return {"run_id": run_id, "status": snap.get("status"),
            "summary": snap.get("result") or {},
            "stop_reason": snap.get("stop_reason", "")}


def _default_tcpdump_path() -> str:
    adb_dir = os.path.dirname(get_adb_path())
    for path in (os.path.join(adb_dir, "tcpdump"),
                 os.path.join(os.path.dirname(os.path.dirname(
                     os.path.abspath(__file__))), "tools", "tcpdump")):
        if os.path.isfile(path):
            return path
    return os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "tools", "tcpdump")


def _tcpdump_start(local_tcpdump_path: Optional[str] = None,
                   filter_expr: str = "", interface: str = "any",
                   snaplen: int = 0,
                   max_seconds: int = CAPTURE_MAX_SECONDS,
                   max_bytes: int = CAPTURE_MAX_BYTES,
                   max_segments: int = 6,
                   subdir: str = "capture") -> dict:
    max_seconds = int(max_seconds if max_seconds is not None
                      else CAPTURE_MAX_SECONDS)
    if not (0 <= max_seconds <= 3600):
        raise ToolFailure("INTERNAL_ERROR", f"max_seconds 范围 0..3600：{max_seconds}")
    max_bytes = int(max_bytes if max_bytes is not None else CAPTURE_MAX_BYTES)
    if not (0 <= max_bytes <= 1024 * 1024 * 1024):
        raise ToolFailure("INTERNAL_ERROR", "max_bytes 范围 0..1GB")
    max_segments = int(max_segments or 1)
    if not (1 <= max_segments <= 20):
        raise ToolFailure("INTERNAL_ERROR", f"max_segments 范围 1..20：{max_segments}")
    path = local_tcpdump_path or _default_tcpdump_path()
    if local_tcpdump_path and not os.path.isfile(path):
        raise ToolFailure("INTERNAL_ERROR", f"本地 tcpdump 不存在：{path}")

    svc = RUNTIME.ensure_device(RUNTIME.serial)
    runner = TcpdumpRunner(svc.serial, Settings.get_output_dir())
    job = device_jobs.create(
        "tcpdump", mode="host",
        meta={"serial": svc.serial, "filter": filter_expr,
              "interface": interface, "max_seconds": max_seconds,
              "max_bytes": max_bytes},
        deadline_ts=time.time() + (max_seconds or 3600) + 60)
    try:
        info = runner.start(path, filter_expr, interface, snaplen,
                            max_seconds, max_bytes, subdir, max_segments)
    except Exception as e:
        device_jobs.finish(job, "failed", error=str(e))
        code = ("CAPTURE_UNAVAILABLE" if "未 root" in str(e)
                else "ADB_FAILED")
        raise ToolFailure(code, str(e), _hint_for(code))

    raw_job = device_jobs.raw(job["run_id"])
    raw_job["_runner"] = runner

    def _stop(reason: str) -> dict:
        result = runner.stop()
        device_jobs.finish(raw_job,
                           "stopped" if reason == "user" else "finished",
                           result=result, stop_reason=reason)
        return result

    def _monitor():
        runner.wait()
        if raw_job.get("status") != "running":
            return
        result = runner.stop()
        device_jobs.finish(raw_job, "succeeded", result=result,
                           stop_reason=result.get("stop_reason", ""))

    device_jobs.set_stop(job["run_id"], _stop)
    threading.Thread(target=_monitor, daemon=True,
                     name=f"mcp-tcpdump-{job['run_id']}").start()
    return {"run_id": job["run_id"], "status": "running", **info}


def _tcpdump_stop(run_id: str) -> dict:
    job = _job_or_fail(run_id)
    raw_job = device_jobs.raw(run_id) or {}
    if job.get("status") in _ACTIVE_JOB_STATUS and not raw_job.get("_stop_fn"):
        device_jobs.finish(raw_job, "stale",
                           error="MCP 进程已退出，抓包已中断",
                           stop_reason="mcp_restart")
        snap = device_jobs.get(run_id) or job
        return {"run_id": run_id, "status": snap.get("status"),
                "message": "MCP 进程已退出，抓包线程不存在；已生成的 pcap 仍在输出目录"}
    if job.get("status") not in _ACTIVE_JOB_STATUS:
        return {"run_id": run_id, "status": job.get("status"),
                "result": job.get("result") or {},
                "message": "任务已结束，无需停止"}
    device_jobs.request_stop(run_id, "user")
    device_jobs.wait(run_id, 30)
    snap = device_jobs.get(run_id) or job
    return {"run_id": run_id, "status": snap.get("status"),
            "result": snap.get("result") or {},
            "stop_reason": snap.get("stop_reason", "")}


# ----------------------------------------------------------------------
# 批次 3.4：弱网模拟
# ----------------------------------------------------------------------
_WEAK_IFACE_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")


def _weak_net_error(e: Exception) -> ToolFailure:
    text = str(e) or e.__class__.__name__
    code = "WEAK_NET_UNAVAILABLE" if (
        isinstance(e, PermissionError) or "root" in text.lower()
        or "tc " in text.lower() or "netem" in text.lower()
        or "rtnetlink" in text.lower()) else "ADB_FAILED"
    return ToolFailure(code, text, _hint_for(code))


def _weak_net_apply(delay_ms: int = 0, loss_pct: float = 0.0,
                    rate_kbps: int = 0, interface: str = "wlan0",
                    ttl_s: int = 600,
                    allow_destructive: bool = False) -> dict:
    if not allow_destructive:
        raise ToolFailure("DESTRUCTIVE_DENIED",
                          f"弱网模拟会改变设备网络状态：{interface}",
                          "确认风险后传 allow_destructive=true")
    interface = (interface or "").strip()
    if not _WEAK_IFACE_RE.match(interface):
        raise ToolFailure("INTERNAL_ERROR", f"网卡名不合法：{interface!r}")
    delay_ms = int(delay_ms or 0)
    loss_pct = float(loss_pct or 0)
    rate_kbps = int(rate_kbps or 0)
    ttl_s = int(ttl_s or 0)
    if not (0 <= delay_ms <= 5000):
        raise ToolFailure("INTERNAL_ERROR", f"delay_ms 范围 0..5000：{delay_ms}")
    if not (0 <= loss_pct <= 100):
        raise ToolFailure("INTERNAL_ERROR", f"loss_pct 范围 0..100：{loss_pct}")
    if not (0 <= rate_kbps <= 1_000_000):
        raise ToolFailure("INTERNAL_ERROR", f"rate_kbps 范围 0..1000000：{rate_kbps}")
    if not (30 <= ttl_s <= 3600):
        raise ToolFailure("INTERNAL_ERROR", f"ttl_s 范围 30..3600：{ttl_s}")
    if delay_ms == 0 and loss_pct == 0 and rate_kbps == 0:
        raise ToolFailure("INTERNAL_ERROR",
                          "至少填一项：delay_ms / loss_pct / rate_kbps")

    serial = RUNTIME.ensure_device(RUNTIME.serial).serial
    runner = WeakNetRunner(serial)

    def run():
        result = runner.apply(interface, delay_ms, loss_pct, rate_kbps, ttl_s)
        result["qdisc"] = runner.show(interface)
        return result

    try:
        return _jobbed("weak_net_apply",
                       {"serial": serial, "interface": interface,
                        "delay_ms": delay_ms, "loss_pct": loss_pct,
                        "rate_kbps": rate_kbps, "ttl_s": ttl_s},
                       run, timeout=90)
    except ToolFailure:
        raise
    except Exception as e:
        raise _weak_net_error(e)


def _weak_net_clear(interface: str = "wlan0") -> dict:
    interface = (interface or "").strip()
    if not _WEAK_IFACE_RE.match(interface):
        raise ToolFailure("INTERNAL_ERROR", f"网卡名不合法：{interface!r}")
    serial = RUNTIME.ensure_device(RUNTIME.serial).serial
    runner = WeakNetRunner(serial)

    def run():
        result = runner.clear(interface)
        result["qdisc"] = runner.show(interface)
        return result

    try:
        return _jobbed("weak_net_clear",
                       {"serial": serial, "interface": interface},
                       run, timeout=60)
    except ToolFailure:
        raise
    except Exception as e:
        raise _weak_net_error(e)


# ----------------------------------------------------------------------
# 批次 4.2：性能基线写入与对比
# ----------------------------------------------------------------------
def _perf_baseline_save(session_id: str, name: str,
                        overwrite: bool = False) -> dict:
    name = (name or "").strip()
    if not name:
        raise ToolFailure("INTERNAL_ERROR", "基线名称不能为空")
    model = PerfModel()
    session = model.get_session((session_id or "").strip())
    if session is None:
        raise ToolFailure("PERF_SESSION_NOT_FOUND", f"会话不存在：{session_id}",
                          "先调用 perf_sessions")
    stats = session.get_stats()
    if not stats:
        raise ToolFailure("INTERNAL_ERROR", "该会话没有可用的统计数据")
    existing = model.get_baseline(name)
    if existing is not None and not overwrite:
        raise ToolFailure("BASELINE_EXISTS", f"基线已存在：{name}",
                          "传 overwrite=true 覆盖")
    if existing is None:
        baseline = PerfBaseline(
            name=name, session_id=session.id,
            app_package=session.app_package,
            device_serial=session.device_serial,
            metrics=stats,
            created_at=datetime.now().isoformat(),
            scopes=dict(getattr(session, "scopes", {}) or {}))
        model.baselines.append(baseline)
        action = "created"
    else:
        existing.session_id = session.id
        existing.app_package = session.app_package
        existing.device_serial = session.device_serial
        existing.metrics = stats
        existing.scopes = dict(getattr(session, "scopes", {}) or {})
        existing.created_at = datetime.now().isoformat()
        action = "updated"
    model.save_baselines()
    return {"name": name, "session_id": session.id, "action": action,
            "app_package": session.app_package,
            "metric_keys": sorted(stats.keys())}


def _perf_baseline_delete(name: str, allow_destructive: bool = False) -> dict:
    name = (name or "").strip()
    if not allow_destructive:
        raise ToolFailure("DESTRUCTIVE_DENIED", f"删除基线需要显式授权：{name}",
                          "确认后传 allow_destructive=true")
    model = PerfModel()
    if model.get_baseline(name) is None:
        raise ToolFailure("BASELINE_NOT_FOUND", f"基线不存在：{name}",
                          "先调用 perf_baselines")
    model.remove_baseline(name)
    return {"deleted": True, "name": name}


def _perf_compare(session_id: str, baseline_name: str,
                  threshold_pct: float = 0.1) -> dict:
    threshold_pct = float(threshold_pct or 0.1)
    if not (0 <= threshold_pct <= 1):
        raise ToolFailure("INTERNAL_ERROR",
                          f"threshold_pct 范围 0..1：{threshold_pct}")
    model = PerfModel()
    baseline = model.get_baseline((baseline_name or "").strip())
    if baseline is None:
        raise ToolFailure("BASELINE_NOT_FOUND", f"基线不存在：{baseline_name}",
                          "先调用 perf_baselines")
    session = model.get_session((session_id or "").strip())
    if session is None:
        raise ToolFailure("PERF_SESSION_NOT_FOUND", f"会话不存在：{session_id}",
                          "先调用 perf_sessions")
    rows = compare_stats(baseline.metrics, session.get_stats(), threshold_pct)
    counts = {"worse": 0, "better": 0, "stable": 0}
    for row in rows:
        counts[row.get("status", "stable")] = counts.get(
            row.get("status", "stable"), 0) + 1
    return {"session_id": session.id, "baseline": baseline.name,
            "threshold_pct": threshold_pct, "rows": rows,
            "summary": counts,
            "session_package": session.app_package,
            "baseline_package": baseline.app_package}


# ----------------------------------------------------------------------
# 批次 4.3：知识库读写
# ----------------------------------------------------------------------
def _kb_project(model: KnowledgeModel, project: Optional[str]):
    project = (project or "").strip()
    if not project:
        return None
    found = model.get_project(project)
    if found is not None:
        return found
    return next((p for p in model.get_projects() if p.name == project), None)


def _knowledge_list(project: Optional[str] = None,
                    category: Optional[str] = None,
                    search: Optional[str] = None) -> dict:
    model = KnowledgeModel()
    target = _kb_project(model, project)
    if project and target is None:
        raise ToolFailure("PROJECT_NOT_FOUND", f"知识库项目不存在：{project}",
                          "先调用 knowledge_list 查看现有项目")
    project_id = target.id if target is not None else None
    notes = model.get_notes(project_id, category or None)
    keyword = (search or "").strip().lower()
    if keyword:
        notes = [n for n in notes
                 if keyword in f"{n.title} {n.body} {' '.join(n.tags)}".lower()]
    names = {p.id: p.name for p in model.get_projects()}
    return {
        "projects": [{"project_id": p.id, "name": p.name,
                      "note_count": model.count_notes(p.id)}
                     for p in model.get_projects()],
        "count": len(notes),
        "notes": [{
            "note_id": n.id, "project_id": n.project_id,
            "project_name": names.get(n.project_id, ""),
            "category": n.category, "title": n.title,
            "tags": list(n.tags), "updated": n.updated,
            "preview": (n.body or "").strip().replace("\n", " ")[:160],
        } for n in notes],
        "categories": list(CATEGORIES),
    }


def _knowledge_read(note_id: str) -> dict:
    model = KnowledgeModel()
    note = model.get_note((note_id or "").strip())
    if note is None:
        raise ToolFailure("NOTE_NOT_FOUND", f"笔记不存在：{note_id}",
                          "先调用 knowledge_list")
    project = model.get_project(note.project_id)
    data = note.to_dict()
    data["note_id"] = note.id
    data["project_name"] = project.name if project is not None else ""
    return data


def _knowledge_upsert_note(project: Optional[str] = None,
                           category: Optional[str] = None,
                           title: Optional[str] = None,
                           body: Optional[str] = None,
                           tags: Optional[list] = None,
                           note_id: Optional[str] = None) -> dict:
    model = KnowledgeModel()
    if note_id:
        note = model.get_note((note_id or "").strip())
        if note is None:
            raise ToolFailure("NOTE_NOT_FOUND", f"笔记不存在：{note_id}",
                              "新建时不要传 note_id")
        changes = {}
        if category:
            changes["category"] = category
        if title is not None:
            changes["title"] = title
        if body is not None:
            changes["body"] = body
        if tags is not None:
            changes["tags"] = list(tags)
        if project:
            target = _kb_project(model, project)
            if target is None:
                raise ToolFailure("PROJECT_NOT_FOUND",
                                  f"知识库项目不存在：{project}")
            changes["project_id"] = target.id
        if not model.update_note(note.id, **changes):
            raise ToolFailure("INTERNAL_ERROR", "更新笔记失败")
        action = "updated"
    else:
        if not project:
            raise ToolFailure("INTERNAL_ERROR", "新建笔记需要 project")
        target = _kb_project(model, project)
        if target is None:
            target = model.add_project(str(project).strip())
        note = model.add_note(target.id, category or CATEGORIES[1],
                              title or "", body or "", tags or [])
        if note is None:
            raise ToolFailure("INTERNAL_ERROR", "创建笔记失败")
        action = "created"
    note = model.get_note(note.id)
    project = model.get_project(note.project_id)
    data = note.to_dict()
    data["note_id"] = note.id
    data["project_name"] = project.name if project is not None else ""
    data["action"] = action
    return data


def _knowledge_delete_note(note_id: str,
                           allow_destructive: bool = False) -> dict:
    note_id = (note_id or "").strip()
    if not allow_destructive:
        raise ToolFailure("DESTRUCTIVE_DENIED", f"删除笔记需要显式授权：{note_id}",
                          "确认后传 allow_destructive=true")
    model = KnowledgeModel()
    if model.get_note(note_id) is None:
        raise ToolFailure("NOTE_NOT_FOUND", f"笔记不存在：{note_id}")
    model.delete_note(note_id)
    return {"deleted": True, "note_id": note_id}


# ----------------------------------------------------------------------
# 设置页：语音播报配置
# ----------------------------------------------------------------------
def _voice_settings_snapshot() -> dict:
    vm = VoiceModel()
    settings = dict(vm.settings or {})
    return {
        "engine": settings.get("engine", ""),
        "voice_id": settings.get("voice_id", ""),
        "device_id": settings.get("device_id", ""),
        "rate": settings.get("rate", 0),
        "loop": settings.get("loop", 1),
        "wake_word": settings.get("wake_word", ""),
        "verify": dict(settings.get("verify") or {}),
        "car_profile": getattr(vm, "current_car_profile", ""),
    }


def _voice_settings_get() -> dict:
    return _voice_settings_snapshot()


def _voice_settings_update(engine: Optional[str] = None,
                           voice_id: Optional[str] = None,
                           device_id: Optional[str] = None,
                           rate: Optional[int] = None,
                           loop: Optional[int] = None,
                           wake_word: Optional[str] = None,
                           verify: Optional[dict] = None) -> dict:
    changes = {}
    if engine is not None:
        changes["engine"] = str(engine)
    if voice_id is not None:
        changes["voice_id"] = str(voice_id)
    if device_id is not None:
        changes["device_id"] = str(device_id)
    if rate is not None:
        rate = int(rate)
        if not (-10 <= rate <= 10):
            raise ToolFailure("INTERNAL_ERROR", f"rate 范围 -10..10：{rate}")
        changes["rate"] = rate
    if loop is not None:
        loop = int(loop)
        if not (1 <= loop <= 99):
            raise ToolFailure("INTERNAL_ERROR", f"loop 范围 1..99：{loop}")
        changes["loop"] = loop
    if wake_word is not None:
        changes["wake_word"] = str(wake_word).strip()
    if verify is not None:
        if not isinstance(verify, dict):
            raise ToolFailure("INTERNAL_ERROR", "verify 必须是对象")
        allowed = {"enabled", "log_tag", "success_keywords",
                   "fail_keywords", "wake"}
        bad = [k for k in verify if k not in allowed]
        if bad:
            raise ToolFailure("INTERNAL_ERROR", f"verify 不支持的键：{', '.join(bad)}")
        current = dict(VoiceModel().settings.get("verify") or {})
        current.update(verify)
        changes["verify"] = current
    if not changes:
        return _voice_settings_snapshot()
    VoiceModel().set_settings(**changes)
    return _voice_settings_snapshot()


def _ai_analyze_failure(step: dict, prev_steps: Optional[list] = None,
                        error_msg: str = "", screenshot_path: Optional[str] = None,
                        logcat_snippet: Optional[str] = None) -> dict:
    suggestion = AIService().analyze_failure(
        step or {}, prev_steps or [], error_msg or "",
        screenshot_path=screenshot_path, logcat_snippet=logcat_snippet)
    if suggestion is None:
        return {"enabled": False, "message": "AI 未启用或调用失败"}
    try:
        return asdict(suggestion)
    except Exception:
        return {"summary": suggestion.summary, "cause": suggestion.cause}


# ----------------------------------------------------------------------
# MCP 工具注册（async + to_thread，避免阻塞 asyncio 事件循环）
# ----------------------------------------------------------------------
async def _run(name: str, params: dict, fn, mutating: bool = False,
               hold_lock: bool = True) -> str:
    return await asyncio.to_thread(_execute, name, params, fn, mutating, hold_lock)


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


@server.tool(name="press_key",
             description="按系统键：back/home/menu/volume_up/volume_down/power/camera；power 需授权")
async def press_key(key: str, allow_destructive: bool = False) -> str:
    return await _run("press_key", {"key": key,
                                    "allow_destructive": allow_destructive},
                      lambda: _press_key(key, allow_destructive),
                      mutating=True)


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
                   wait: bool = True, timeout_s: float = 60.0,
                   allow_destructive: bool = False) -> str:
    return await asyncio.to_thread(
        _run_case_tool, case_name, case_id, stop_on_failure,
        loop_count, retry_count, retry_delay, wait, timeout_s,
        allow_destructive)


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
                    overwrite: bool = False,
                    allow_destructive: bool = False) -> str:
    params = {"module": module, "case_name": case_name,
              "project_name": project_name, "draft_id": draft_id,
              "overwrite": overwrite, "allow_destructive": allow_destructive,
              "steps": steps}
    return await _run("save_case", params,
                      lambda: _save_case(project_name, module, case_name,
                                         steps, draft_id, overwrite,
                                         allow_destructive),
                      mutating=True)


@server.tool(name="run_suite",
             description="执行一个已保存的自动套件；wait=true 最多等 timeout_s，超时返回 run_id")
async def run_suite(suite_name: str, stop_on_failure: bool = False,
                    loop_count: int = 1, retry_count: int = 0,
                    retry_delay: int = 0, wait: bool = True,
                    timeout_s: float = 60.0,
                    allow_destructive: bool = False) -> str:
    return await asyncio.to_thread(
        _run_suite_tool, suite_name, stop_on_failure, loop_count,
        retry_count, retry_delay, wait, timeout_s, allow_destructive)


# ----------------------------------------------------------------------
# 批次 1：ADB / 性能 / 语音 / 接口 / 元素库 / 报告（只读）
# ----------------------------------------------------------------------
@server.tool(name="adb_device_info", description="读取设备硬件/系统/电池/存储/网络信息（只读）")
async def adb_device_info() -> str:
    return await _run("adb_device_info", {}, _adb_device_info)


@server.tool(name="adb_logcat_snapshot", description="拉取当前 logcat 快照（-d，不清日志），返回文件路径和尾部")
async def adb_logcat_snapshot(tags: Optional[str] = None, lines: int = 200) -> str:
    return await _run("adb_logcat_snapshot", {"tags": tags, "lines": lines},
                      lambda: _adb_logcat_snapshot(tags, lines))


@server.tool(name="adb_pull", description="从设备拉取一个文件到输出目录（只读设备）")
async def adb_pull(remote_path: str, local_name: Optional[str] = None) -> str:
    return await _run("adb_pull", {"remote_path": remote_path,
                                   "local_name": local_name},
                      lambda: _adb_pull(remote_path, local_name))


@server.tool(name="perf_sessions", description="列出性能检测历史会话（只读）")
async def perf_sessions(limit: int = 20) -> str:
    return await _run("perf_sessions", {"limit": limit},
                      lambda: _perf_sessions(limit))


@server.tool(name="perf_baselines", description="列出性能基线（只读）")
async def perf_baselines() -> str:
    return await _run("perf_baselines", {}, _perf_baselines)


@server.tool(name="perf_report", description="为指定性能会话生成 HTML 报告，返回路径")
async def perf_report(session_id: str, output_dir: Optional[str] = None) -> str:
    return await _run("perf_report", {"session_id": session_id,
                                      "output_dir": output_dir},
                      lambda: _perf_report(session_id, output_dir))


@server.tool(name="voice_list_cases", description="列出语音播报用例（只读）")
async def voice_list_cases(group: Optional[str] = None,
                           include_steps: bool = False) -> str:
    return await _run("voice_list_cases", {"group": group,
                                           "include_steps": include_steps},
                      lambda: _voice_list_cases(group, include_steps))


@server.tool(name="api_list", description="列出接口自动化的分组/接口/环境（只读）")
async def api_list(group: Optional[str] = None,
                   include_details: bool = False) -> str:
    return await _run("api_list", {"group": group,
                                   "include_details": include_details},
                      lambda: _api_list(group, include_details))


@server.tool(name="element_list", description="列出应用元素库（可按应用/模块/关键字过滤）")
async def element_list(app: Optional[str] = None, module: Optional[str] = None,
                       search: Optional[str] = None, limit: int = 200) -> str:
    return await _run("element_list", {"app": app, "module": module,
                                       "search": search, "limit": limit},
                      lambda: _element_list(app, module, search, limit))


@server.tool(name="report_list", description="列出输出目录下的测试/性能/接口 HTML 报告")
async def report_list(limit: int = 30) -> str:
    return await _run("report_list", {"limit": limit},
                      lambda: _report_list(limit))


@server.tool(name="report_read", description="读取输出目录下报告的文字内容（去掉 HTML 标签）")
async def report_read(path: str, max_chars: int = 12000) -> str:
    return await _run("report_read", {"path": path, "max_chars": max_chars},
                      lambda: _report_read(path, max_chars))


# ----------------------------------------------------------------------
# 批次 2：语音 / 接口 / 元素库 / 套件 / 用例 / 推送 / AI
# ----------------------------------------------------------------------
@server.tool(name="voice_speak", description="用本机 TTS 播报一段文字（可控写）")
async def voice_speak(text: str, after_delay: float = 0.0) -> str:
    return await _run("voice_speak", {"text": text, "after_delay": after_delay},
                      lambda: _voice_speak(text, after_delay))


@server.tool(name="voice_verify", description="抓 logcat 按关键词做语音回执验证（只读）")
async def voice_verify(tag: Optional[str] = None,
                       success_keywords: Optional[list] = None,
                       fail_keywords: Optional[list] = None) -> str:
    return await _run("voice_verify", {"tag": tag,
                                       "success_keywords": success_keywords,
                                       "fail_keywords": fail_keywords},
                      lambda: _voice_verify(tag, success_keywords, fail_keywords))


@server.tool(name="api_run", description="执行一个已保存接口；非 GET/HEAD/OPTIONS 需 allow_destructive")
async def api_run(case_id: Optional[str] = None, case_name: Optional[str] = None,
                  env_name: Optional[str] = None,
                  allow_destructive: bool = False) -> str:
    return await _run("api_run", {"case_id": case_id, "case_name": case_name,
                                  "env_name": env_name,
                                  "allow_destructive": allow_destructive},
                      lambda: _api_run(case_id, case_name, env_name,
                                       allow_destructive))


@server.tool(name="element_upsert", description="新增或覆盖一条元素库记录（写 data/）")
async def element_upsert(app: str, module: str, name: str, loc_type: str,
                         loc_value: str, remark: str = "") -> str:
    return await _run("element_upsert", {"app": app, "module": module,
                                         "name": name, "loc_type": loc_type,
                                         "loc_value": loc_value, "remark": remark},
                      lambda: _element_upsert(app, module, name, loc_type,
                                              loc_value, remark),
                      mutating=True)


@server.tool(name="suite_list", description="列出已保存的自动套件（只读）")
async def suite_list() -> str:
    return await _run("suite_list", {}, _suite_list)


@server.tool(name="suite_save", description="创建或覆盖自动套件（写 data/）")
async def suite_save(name: str, case_ids: list, overwrite: bool = False) -> str:
    return await _run("suite_save", {"name": name, "case_ids": case_ids,
                                     "overwrite": overwrite},
                      lambda: _suite_save(name, case_ids, overwrite),
                      mutating=True)


@server.tool(name="suite_delete", description="删除自动套件；需 allow_destructive=true")
async def suite_delete(name: str, allow_destructive: bool = False) -> str:
    return await _run("suite_delete", {"name": name,
                                       "allow_destructive": allow_destructive},
                      lambda: _suite_delete(name, allow_destructive),
                      mutating=True)


@server.tool(name="case_rename", description="重命名用例（写 data/）")
async def case_rename(case_id: str, new_name: str) -> str:
    return await _run("case_rename", {"case_id": case_id, "new_name": new_name},
                      lambda: _case_rename(case_id, new_name), mutating=True)


@server.tool(name="case_copy", description="复制用例及步骤，可指定目标模块/新名称")
async def case_copy(case_id: str, new_name: Optional[str] = None,
                    target_module: Optional[str] = None) -> str:
    return await _run("case_copy", {"case_id": case_id, "new_name": new_name,
                                    "target_module": target_module},
                      lambda: _case_copy(case_id, new_name, target_module),
                      mutating=True)


@server.tool(name="case_delete", description="删除用例及步骤；需 allow_destructive=true")
async def case_delete(case_id: str, allow_destructive: bool = False) -> str:
    return await _run("case_delete", {"case_id": case_id,
                                      "allow_destructive": allow_destructive},
                      lambda: _case_delete(case_id, allow_destructive),
                      mutating=True)


@server.tool(name="step_move", description="调整用例内步骤顺序（0-based）")
async def step_move(case_id: str, from_index: int, to_index: int) -> str:
    return await _run("step_move", {"case_id": case_id,
                                    "from_index": from_index,
                                    "to_index": to_index},
                      lambda: _step_move(case_id, from_index, to_index),
                      mutating=True)


@server.tool(name="step_update", description="修改用例某一步的名称/参数（1-based）")
async def step_update(case_id: str, step_index: int,
                      name: Optional[str] = None,
                      params: Optional[dict] = None) -> str:
    return await _run("step_update", {"case_id": case_id,
                                      "step_index": step_index,
                                      "name": name, "params": params},
                      lambda: _step_update(case_id, step_index, name, params),
                      mutating=True)


@server.tool(name="adb_push", description="把本机文件推送到设备（写设备）")
async def adb_push(local_path: str, remote_path: str) -> str:
    return await _run("adb_push", {"local_path": local_path,
                                   "remote_path": remote_path},
                      lambda: _adb_push(local_path, remote_path),
                      mutating=True)


@server.tool(name="ai_analyze_failure", description="用已配置的 AI 分析一次失败（只读）")
async def ai_analyze_failure(step: dict, prev_steps: Optional[list] = None,
                             error_msg: str = "",
                             screenshot_path: Optional[str] = None,
                             logcat_snippet: Optional[str] = None) -> str:
    return await _run("ai_analyze_failure",
                      {"step": step, "prev_steps": prev_steps,
                       "error_msg": error_msg,
                       "screenshot_path": screenshot_path,
                       "logcat_snippet": logcat_snippet},
                      lambda: _ai_analyze_failure(step, prev_steps, error_msg,
                                                  screenshot_path, logcat_snippet))


# ----------------------------------------------------------------------
# 批次 3.1：设备影响型（安装 / 卸载 / 清数据 / 堆转储 / 重启）
# ----------------------------------------------------------------------
@server.tool(name="app_install", description="安装本机 APK；需 allow_destructive=true（GUI 需退出）")
async def app_install(apk_path: str, replace: bool = True,
                      grant_permissions: bool = True,
                      allow_destructive: bool = False) -> str:
    return await _run("app_install",
                      {"apk_path": apk_path, "replace": replace,
                       "grant_permissions": grant_permissions,
                       "allow_destructive": allow_destructive},
                      lambda: _app_install(apk_path, replace,
                                           grant_permissions, allow_destructive),
                      mutating=True, hold_lock=False)


@server.tool(name="app_uninstall", description="卸载应用；需 allow_destructive=true（GUI 需退出）")
async def app_uninstall(package: str, keep_data: bool = False,
                        allow_destructive: bool = False) -> str:
    return await _run("app_uninstall",
                      {"package": package, "keep_data": keep_data,
                       "allow_destructive": allow_destructive},
                      lambda: _app_uninstall(package, keep_data,
                                             allow_destructive),
                      mutating=True, hold_lock=False)


@server.tool(name="app_clear_data", description="清空应用数据；需 allow_destructive=true（GUI 需退出）")
async def app_clear_data(package: str,
                         allow_destructive: bool = False) -> str:
    return await _run("app_clear_data",
                      {"package": package,
                       "allow_destructive": allow_destructive},
                      lambda: _app_clear_data(package, allow_destructive),
                      mutating=True, hold_lock=False)


@server.tool(name="hprof_dump", description="对指定应用做一次堆转储并拉回本机（免门禁，有硬超时）")
async def hprof_dump(package: str, pull_to_host: bool = True) -> str:
    return await _run("hprof_dump",
                      {"package": package, "pull_to_host": pull_to_host},
                      lambda: _hprof_dump(package, pull_to_host),
                      mutating=True, hold_lock=False)


@server.tool(name="device_reboot", description="重启设备（仅 system）；需 allow_destructive=true")
async def device_reboot(mode: str = "system",
                        allow_destructive: bool = False) -> str:
    return await _run("device_reboot",
                      {"mode": mode, "allow_destructive": allow_destructive},
                      lambda: _device_reboot(mode, allow_destructive),
                      mutating=True, hold_lock=False)


# ----------------------------------------------------------------------
# 批次 3.2：Monkey / 录屏 / 实时 logcat
# ----------------------------------------------------------------------
@server.tool(name="monkey_start",
             description="启动设备端常驻 Monkey，立即返回 run_id；白名单顺序按 packages 传入")
async def monkey_start(packages: list, events: int = 1000,
                       throttle_ms: int = 800, seed: Optional[int] = None,
                       duration_h: float = 24.0, verbose: int = 0,
                       flags: Optional[dict] = None,
                       pcts: Optional[dict] = None,
                       use_whitelist: bool = False) -> str:
    return await _run("monkey_start",
                      {"packages": packages, "events": events,
                       "throttle_ms": throttle_ms, "seed": seed,
                       "duration_h": duration_h, "verbose": verbose,
                       "flags": flags, "pcts": pcts,
                       "use_whitelist": use_whitelist},
                      lambda: _monkey_start(packages, events, throttle_ms,
                                            seed, duration_h, verbose, flags,
                                            pcts, use_whitelist),
                      mutating=True, hold_lock=False)


@server.tool(name="monkey_status",
             description="查看 Monkey 任务：设备端进程是否存活 + 日志尾部")
async def monkey_status(run_id: str) -> str:
    return await _run("monkey_status", {"run_id": run_id},
                      lambda: _monkey_status(run_id), hold_lock=False)


@server.tool(name="monkey_stop", description="停止 Monkey（按 run_id，支持重启后重新接上）")
async def monkey_stop(run_id: str) -> str:
    return await _run("monkey_stop", {"run_id": run_id},
                      lambda: _monkey_stop(run_id),
                      mutating=True, hold_lock=False)


@server.tool(name="screenrecord_start",
             description="开始录屏（最长 180 秒），返回 run_id；结束自动拉回本机")
async def screenrecord_start(duration_s: int = 180,
                             bitrate_mbps: float = 8.0) -> str:
    return await _run("screenrecord_start",
                      {"duration_s": duration_s,
                       "bitrate_mbps": bitrate_mbps},
                      lambda: _screenrecord_start(duration_s, bitrate_mbps),
                      mutating=True, hold_lock=False)


@server.tool(name="screenrecord_stop",
             description="停止录屏并拉回文件（返回本地路径与大小）")
async def screenrecord_stop(run_id: str) -> str:
    return await _run("screenrecord_stop", {"run_id": run_id},
                      lambda: _screenrecord_stop(run_id),
                      mutating=True, hold_lock=False)


@server.tool(name="logcat_start",
             description="开始实时 logcat 落盘，返回 run_id；按行数或秒数自动停止")
async def logcat_start(tags: Optional[list] = None, max_lines: int = 20000,
                       max_seconds: int = 300,
                       clear_first: bool = False) -> str:
    return await _run("logcat_start",
                      {"tags": tags, "max_lines": max_lines,
                       "max_seconds": max_seconds,
                       "clear_first": clear_first},
                      lambda: _logcat_start(tags, max_lines, max_seconds,
                                            clear_first),
                      mutating=True, hold_lock=False)


@server.tool(name="logcat_stop", description="停止实时 logcat（返回文件路径与行数）")
async def logcat_stop(run_id: str) -> str:
    return await _run("logcat_stop", {"run_id": run_id},
                      lambda: _logcat_stop(run_id),
                      mutating=True, hold_lock=False)


@server.tool(name="logcat_read", description="读取实时 logcat 任务的尾部内容")
async def logcat_read(run_id: str, lines: int = 200) -> str:
    return await _run("logcat_read", {"run_id": run_id, "lines": lines},
                      lambda: _logcat_read(run_id, lines), hold_lock=False)


# ----------------------------------------------------------------------
# 批次 3.3：性能采集 / tcpdump
# ----------------------------------------------------------------------
@server.tool(name="perf_start",
             description="开始性能采集（默认 60 分钟）；可传 suite_name 在采集期间跑套件")
async def perf_start(package: str, processes: Optional[list] = None,
                     metrics: Optional[list] = None,
                     probes: Optional[list] = None, interval: float = 5.0,
                     duration_minutes: float = 60.0,
                     suite_name: Optional[str] = None,
                     case_ids: Optional[list] = None,
                     loop: int = 1,
                     stop_on_failure: bool = False,
                     suite_type: str = "case") -> str:
    return await _run("perf_start",
                      {"package": package, "processes": processes,
                       "metrics": metrics, "probes": probes,
                       "interval": interval,
                       "duration_minutes": duration_minutes,
                       "suite_name": suite_name, "case_ids": case_ids,
                       "loop": loop, "stop_on_failure": stop_on_failure,
                       "suite_type": suite_type},
                      lambda: _perf_start(package, processes, metrics,
                                          probes, interval, duration_minutes,
                                          suite_name, case_ids, loop,
                                          stop_on_failure, suite_type),
                      mutating=True, hold_lock=False)


@server.tool(name="perf_status",
             description="查看性能采集进度：样本数、最近一条数据、探针采样数")
async def perf_status(run_id: str) -> str:
    return await _run("perf_status", {"run_id": run_id},
                      lambda: _perf_status(run_id), hold_lock=False)


@server.tool(name="perf_stop",
             description="停止性能采集并落盘会话（返回样本数与统计摘要）")
async def perf_stop(run_id: str) -> str:
    return await _run("perf_stop", {"run_id": run_id},
                      lambda: _perf_stop(run_id),
                      mutating=True, hold_lock=False)


@server.tool(name="tcpdump_start",
             description="开始抓包（需设备 root；默认 10 分钟或 100MB）")
async def tcpdump_start(local_tcpdump_path: Optional[str] = None,
                        filter_expr: str = "", interface: str = "any",
                        snaplen: int = 0,
                        max_seconds: int = CAPTURE_MAX_SECONDS,
                        max_bytes: int = CAPTURE_MAX_BYTES,
                        max_segments: int = 6,
                        subdir: str = "capture") -> str:
    params = {"local_tcpdump_path": local_tcpdump_path,
              "filter_expr": filter_expr, "interface": interface,
              "snaplen": snaplen, "max_seconds": max_seconds,
              "max_bytes": max_bytes, "max_segments": max_segments,
              "subdir": subdir}
    return await _run("tcpdump_start", params,
                      lambda: _tcpdump_start(local_tcpdump_path, filter_expr,
                                             interface, snaplen, max_seconds,
                                             max_bytes, max_segments, subdir),
                      mutating=True, hold_lock=False)


@server.tool(name="tcpdump_stop",
             description="停止抓包（返回 pcap 文件列表与总大小）")
async def tcpdump_stop(run_id: str) -> str:
    return await _run("tcpdump_stop", {"run_id": run_id},
                      lambda: _tcpdump_stop(run_id),
                      mutating=True, hold_lock=False)


@server.tool(name="weak_net_apply",
             description="下发弱网规则（延迟/丢包/带宽，需 root）；带 TTL 自动清除")
async def weak_net_apply(delay_ms: int = 0, loss_pct: float = 0.0,
                         rate_kbps: int = 0, interface: str = "wlan0",
                         ttl_s: int = 600,
                         allow_destructive: bool = False) -> str:
    return await _run("weak_net_apply",
                      {"delay_ms": delay_ms, "loss_pct": loss_pct,
                       "rate_kbps": rate_kbps, "interface": interface,
                       "ttl_s": ttl_s,
                       "allow_destructive": allow_destructive},
                      lambda: _weak_net_apply(delay_ms, loss_pct, rate_kbps,
                                              interface, ttl_s,
                                              allow_destructive),
                      mutating=True, hold_lock=False)


@server.tool(name="weak_net_clear",
             description="清除弱网规则（恢复该网卡的默认队列）")
async def weak_net_clear(interface: str = "wlan0") -> str:
    return await _run("weak_net_clear", {"interface": interface},
                      lambda: _weak_net_clear(interface),
                      mutating=True, hold_lock=False)


# ----------------------------------------------------------------------
# 批次 4.2 / 4.3 / 设置页语音配置
# ----------------------------------------------------------------------
@server.tool(name="perf_baseline_save",
             description="把一次性能会话保存为基线（同名需 overwrite=true）")
async def perf_baseline_save(session_id: str, name: str,
                             overwrite: bool = False) -> str:
    return await _run("perf_baseline_save",
                      {"session_id": session_id, "name": name,
                       "overwrite": overwrite},
                      lambda: _perf_baseline_save(session_id, name, overwrite),
                      mutating=True)


@server.tool(name="perf_baseline_delete",
             description="删除性能基线；需 allow_destructive=true")
async def perf_baseline_delete(name: str,
                               allow_destructive: bool = False) -> str:
    return await _run("perf_baseline_delete",
                      {"name": name, "allow_destructive": allow_destructive},
                      lambda: _perf_baseline_delete(name, allow_destructive),
                      mutating=True)


@server.tool(name="perf_compare",
             description="把指定会话与基线对比，返回逐项变化与 worse/better 统计")
async def perf_compare(session_id: str, baseline_name: str,
                       threshold_pct: float = 0.1) -> str:
    return await _run("perf_compare",
                      {"session_id": session_id,
                       "baseline_name": baseline_name,
                       "threshold_pct": threshold_pct},
                      lambda: _perf_compare(session_id, baseline_name,
                                            threshold_pct))


@server.tool(name="knowledge_list",
             description="列出知识库项目与笔记（可按项目/分类/关键字过滤）")
async def knowledge_list(project: Optional[str] = None,
                         category: Optional[str] = None,
                         search: Optional[str] = None) -> str:
    return await _run("knowledge_list",
                      {"project": project, "category": category,
                       "search": search},
                      lambda: _knowledge_list(project, category, search))


@server.tool(name="knowledge_read", description="读取一条知识库笔记的完整正文")
async def knowledge_read(note_id: str) -> str:
    return await _run("knowledge_read", {"note_id": note_id},
                      lambda: _knowledge_read(note_id))


@server.tool(name="knowledge_upsert_note",
             description="新建或更新知识库笔记（不传 note_id 即新建）")
async def knowledge_upsert_note(project: Optional[str] = None,
                                category: Optional[str] = None,
                                title: Optional[str] = None,
                                body: Optional[str] = None,
                                tags: Optional[list] = None,
                                note_id: Optional[str] = None) -> str:
    return await _run("knowledge_upsert_note",
                      {"project": project, "category": category,
                       "title": title, "body": body, "tags": tags,
                       "note_id": note_id},
                      lambda: _knowledge_upsert_note(project, category, title,
                                                     body, tags, note_id),
                      mutating=True)


@server.tool(name="knowledge_delete_note",
             description="删除知识库笔记；需 allow_destructive=true")
async def knowledge_delete_note(note_id: str,
                                allow_destructive: bool = False) -> str:
    return await _run("knowledge_delete_note",
                      {"note_id": note_id,
                       "allow_destructive": allow_destructive},
                      lambda: _knowledge_delete_note(note_id,
                                                     allow_destructive),
                      mutating=True)


@server.tool(name="voice_settings_get",
             description="读取设置页的语音播报配置（引擎/音色/语速/唤醒词/回执验证）")
async def voice_settings_get() -> str:
    return await _run("voice_settings_get", {}, _voice_settings_get)


@server.tool(name="voice_settings_update",
             description="更新语音播报配置（只写传入的字段；GUI 需退出）")
async def voice_settings_update(engine: Optional[str] = None,
                                voice_id: Optional[str] = None,
                                device_id: Optional[str] = None,
                                rate: Optional[int] = None,
                                loop: Optional[int] = None,
                                wake_word: Optional[str] = None,
                                verify: Optional[dict] = None) -> str:
    return await _run("voice_settings_update",
                      {"engine": engine, "voice_id": voice_id,
                       "device_id": device_id, "rate": rate, "loop": loop,
                       "wake_word": wake_word, "verify": verify},
                      lambda: _voice_settings_update(engine, voice_id,
                                                     device_id, rate, loop,
                                                     wake_word, verify),
                      mutating=True)


def main():
    device_jobs.init()
    setup_stdio_logging()
    server.run("stdio")


if __name__ == "__main__":
    main()
