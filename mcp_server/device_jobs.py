# mcp_server/device_jobs.py
"""设备影响型任务的运行时：run_id、状态落盘、超时与互斥。

为什么单独一层：安装、hprof、Monkey、抓包这类操作既不能塞进 `_JOBS`
（那是 run_case 的执行队列），也不该让每次工具调用各自造一套状态。
统一在这里生成 run_id、记录起止与结果，并落盘到 `data/mcp_jobs/<run_id>.json`，
3.2 之后的长任务（可 stop、MCP 重启后重连）直接复用同一份状态。

3.1 只用到 `run_blocking`：把单发操作放进工作线程，外层再兜一层超时，
避免某个 adb 调用卡住整个 MCP 事件循环。真正的停止钩子留给 3.2。
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime
from typing import Callable, Optional

from utils.app_paths import data_path

JOBS_DIR = data_path("mcp_jobs")
MAX_KEPT_FILES = 200

_JOBS: dict[str, dict] = {}
_LOCK = threading.RLock()
_INITED = False


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _job_path(run_id: str) -> str:
    return os.path.join(JOBS_DIR, f"{run_id}.json")


def _persist(job: dict) -> None:
    """先写临时文件再替换：MCP 被强杀时不留下半截 JSON。"""
    try:
        os.makedirs(JOBS_DIR, exist_ok=True)
        path = _job_path(job["run_id"])
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(job, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        # 状态落盘失败不该让设备操作本身失败
        pass


def _prune() -> None:
    try:
        files = [os.path.join(JOBS_DIR, n) for n in os.listdir(JOBS_DIR)
                 if n.endswith(".json")]
        if len(files) <= MAX_KEPT_FILES:
            return
        files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        for path in files[MAX_KEPT_FILES:]:
            try:
                os.remove(path)
            except OSError:
                pass
    except Exception:
        pass


def init() -> None:
    """启动时把上次遗留的 running 任务标记为 stale。

    主机侧任务随 MCP 进程一起死掉，状态文件里不可能还是 running；
    留下来只会让智能体误以为还能 stop。
    """
    global _INITED
    with _LOCK:
        if _INITED:
            return
        _INITED = True
    try:
        if not os.path.isdir(JOBS_DIR):
            return
        for name in os.listdir(JOBS_DIR):
            if not name.endswith(".json"):
                continue
            path = os.path.join(JOBS_DIR, name)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    job = json.load(f)
            except Exception:
                continue
            if job.get("status") != "running":
                continue
            job["status"] = "stale"
            job["stop_reason"] = "mcp_restart"
            job["finished_at"] = job.get("finished_at") or _now()
            job["error"] = job.get("error") or "MCP 进程已退出，任务状态未知"
            _persist(job)
            with _LOCK:
                _JOBS[job["run_id"]] = job
    except Exception:
        pass


def create(kind: str, meta: Optional[dict] = None) -> dict:
    init()
    run_id = f"job_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
    job = {
        "run_id": run_id,
        "kind": kind,
        "status": "running",
        "started_at": _now(),
        "finished_at": "",
        "deadline_s": 0,
        "stop_reason": "",
        "error": "",
        "result": None,
        "meta": dict(meta or {}),
    }
    with _LOCK:
        _JOBS[run_id] = job
    _persist(job)
    _prune()
    return job


def finish(job: dict, status: str, result=None,
           error: str = "", stop_reason: str = "") -> dict:
    with _LOCK:
        job["status"] = status
        job["finished_at"] = _now()
        job["result"] = result
        job["error"] = error or ""
        job["stop_reason"] = stop_reason or job.get("stop_reason", "")
        job["duration_ms"] = int(
            (time.time() - _parse_ts(job.get("started_at"))) * 1000)
    _persist(job)
    return job


def _parse_ts(value: str) -> float:
    try:
        return datetime.fromisoformat(value).timestamp()
    except Exception:
        return time.time()


def snapshot(job: dict) -> dict:
    with _LOCK:
        return {
            "run_id": job.get("run_id"),
            "kind": job.get("kind"),
            "status": job.get("status"),
            "started_at": job.get("started_at"),
            "finished_at": job.get("finished_at"),
            "duration_ms": job.get("duration_ms", 0),
            "stop_reason": job.get("stop_reason", ""),
            "error": job.get("error", ""),
            "result": job.get("result"),
            "meta": job.get("meta", {}),
        }


def get(run_id: str) -> Optional[dict]:
    with _LOCK:
        job = _JOBS.get(run_id)
        if job is not None:
            return snapshot(job)
    path = _job_path(run_id)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def list_jobs(limit: int = 20) -> list[dict]:
    with _LOCK:
        jobs = [snapshot(j) for j in _JOBS.values()]
    if len(jobs) < limit and os.path.isdir(JOBS_DIR):
        seen = {j.get("run_id") for j in jobs}
        extra = []
        for name in os.listdir(JOBS_DIR):
            if not name.endswith(".json") or name[:-5] in seen:
                continue
            job = get(name[:-5])
            if job:
                extra.append(job)
        extra.sort(key=lambda j: j.get("started_at") or "", reverse=True)
        jobs.extend(extra[:max(0, limit - len(jobs))])
    jobs.sort(key=lambda j: j.get("started_at") or "", reverse=True)
    return jobs[:max(1, min(200, int(limit or 20)))]


def has_active(kinds: Optional[set] = None) -> bool:
    with _LOCK:
        for job in _JOBS.values():
            if job.get("status") != "running":
                continue
            if kinds is None or job.get("kind") in kinds:
                return True
    return False


def active_kinds() -> list[str]:
    with _LOCK:
        return sorted({j.get("kind") for j in _JOBS.values()
                       if j.get("status") == "running"})


def run_blocking(kind: str, fn: Callable[[], dict], timeout_s: float,
                 meta: Optional[dict] = None) -> dict:
    """在工作线程里跑单发操作，外层再兜一次超时。

    注意：超时不会强杀线程内已经启动的 adb 子进程，调用方自己仍要给
    `subprocess.run` 设更短的 timeout。这里只负责别让工具调用无限挂住。
    """
    job = create(kind, meta)
    done = threading.Event()
    box: dict = {}

    def _call():
        try:
            box["result"] = fn()
        except BaseException as e:  # noqa: BLE001 - 原样交给上层分类
            box["error"] = e
        finally:
            done.set()

    thread = threading.Thread(target=_call, daemon=True,
                              name=f"mcp-{kind}")
    thread.start()
    if not done.wait(max(1.0, float(timeout_s))):
        finish(job, "timeout", error=f"任务超时（{int(timeout_s)} 秒）",
               stop_reason="timeout")
        return {"run_id": job["run_id"], "status": "timeout",
                "error": f"任务超时（{int(timeout_s)} 秒）", "result": None}

    if "error" in box:
        error = box["error"]
        message = str(error) or error.__class__.__name__
        finish(job, "failed", error=message)
        raise error

    result = box.get("result") or {}
    finish(job, "succeeded", result=result)
    return {"run_id": job["run_id"], "status": "succeeded",
            "error": "", "result": result}
