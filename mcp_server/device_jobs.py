# mcp_server/device_jobs.py
"""设备影响型任务的运行时：run_id、状态落盘、超时、停止与互斥。

两类任务：

* **host**：进程内持有 adb 子进程（录屏、实时 logcat、tcpdump、性能采集）。
  MCP 退出任务即中断；重启后状态标记 `stale`。
* **device**：`nohup` 常驻设备（Monkey），与本进程解耦。MCP / Comate 重启后
  状态标记 `detached`，可按 meta 里的设备 PID 重新接上或停止。

3.1 的单发操作走 `run_blocking`；3.2 起的长任务走 `create` +
`set_stop` + `finish`，watchdog 线程负责到点自动停。
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
_ACTIVE_STATUS = ("running", "stopping", "detached")

_JOBS: dict[str, dict] = {}
_LOCK = threading.RLock()
_INITED = False
_WATCHDOG_STARTED = False
_WATCHDOG_INTERVAL = 5.0


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _job_path(run_id: str) -> str:
    return os.path.join(JOBS_DIR, f"{run_id}.json")


def _serializable(job: dict) -> dict:
    text = {k: v for k, v in job.items() if not k.startswith("_")}
    text.pop("done", None)
    text.pop("abort", None)
    return text


def _persist(job: dict) -> None:
    """先写临时文件再替换：MCP 被强杀时不留下半截 JSON。"""
    try:
        os.makedirs(JOBS_DIR, exist_ok=True)
        path = _job_path(job["run_id"])
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_serializable(job), f, ensure_ascii=False, indent=2)
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
    """启动时整理上次遗留状态。

    * host 任务随进程一起死掉：`running` → `stale`
    * device 任务可能还在跑：`running` → `detached`，保留 meta 供重新接上
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
            if job.get("mode") == "device":
                job["status"] = "detached"
                job["stop_reason"] = "mcp_restart"
            else:
                job["status"] = "stale"
                job["stop_reason"] = "mcp_restart"
                job["finished_at"] = job.get("finished_at") or _now()
                job["error"] = job.get("error") or "MCP 进程已退出，任务状态未知"
            _persist(job)
            with _LOCK:
                _JOBS[job["run_id"]] = job
    except Exception:
        pass


def create(kind: str, meta: Optional[dict] = None, mode: str = "host",
           deadline_ts: float = 0.0) -> dict:
    init()
    run_id = f"job_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
    job = {
        "run_id": run_id,
        "kind": kind,
        "mode": mode,
        "status": "running",
        "started_at": _now(),
        "finished_at": "",
        "deadline_ts": float(deadline_ts or 0),
        "stop_reason": "",
        "error": "",
        "result": None,
        "meta": dict(meta or {}),
        "_stop_fn": None,
        "done": threading.Event(),
    }
    with _LOCK:
        _JOBS[run_id] = job
    _persist(job)
    _prune()
    if deadline_ts:
        _ensure_watchdog()
    return job


def update_meta(run_id: str, extra: dict) -> dict:
    with _LOCK:
        job = _JOBS.get(run_id)
        if job is None:
            return {}
        job.setdefault("meta", {}).update(extra or {})
    _persist(job)
    return snapshot(job)


def set_stop(run_id: str, fn: Callable[[str], dict]) -> None:
    with _LOCK:
        job = _JOBS.get(run_id)
        if job is not None:
            job["_stop_fn"] = fn


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
        done = job.get("done")
    _persist(job)
    if done is not None:
        done.set()
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
            "mode": job.get("mode", "host"),
            "status": job.get("status"),
            "started_at": job.get("started_at"),
            "finished_at": job.get("finished_at"),
            "duration_ms": job.get("duration_ms", 0),
            "deadline_ts": job.get("deadline_ts", 0),
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
            job = json.load(f)
        with _LOCK:
            _JOBS.setdefault(run_id, job)
        return job
    except Exception:
        return None


def raw(run_id: str) -> Optional[dict]:
    """取内存里的任务对象（含 _stop_fn / _runner），供工具做收尾。"""
    with _LOCK:
        return _JOBS.get(run_id)


def mark_running(run_id: str) -> None:
    with _LOCK:
        job = _JOBS.get(run_id)
        if job is None or job.get("status") != "detached":
            return
        job["status"] = "running"
        job["stop_reason"] = ""
    _persist(job)


def reconcile_detached(alive: Callable[[dict], bool]) -> list[str]:
    """重连后用设备端 PID 核对 detached 任务；已经不在的标记 finished。"""
    with _LOCK:
        jobs = [j for j in _JOBS.values() if j.get("status") == "detached"]
    finished = []
    for job in jobs:
        try:
            still_alive = bool(alive(snapshot(job)))
        except Exception:
            continue
        if still_alive:
            continue
        finish(job, "finished",
               result={"note": "重连时设备端进程已不存在"},
               stop_reason="reattach_not_found")
        finished.append(job["run_id"])
    return finished


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
            if job.get("status") not in _ACTIVE_STATUS:
                continue
            if kinds is None or job.get("kind") in kinds:
                return True
    return False


def has_detached() -> bool:
    with _LOCK:
        return any(j.get("status") == "detached" for j in _JOBS.values())


def active_kinds() -> list[str]:
    with _LOCK:
        return sorted({j.get("kind") for j in _JOBS.values()
                       if j.get("status") in _ACTIVE_STATUS})


def wait(run_id: str, timeout: float = 30.0) -> bool:
    """等待任务结束；返回是否在超时前结束。"""
    with _LOCK:
        job = _JOBS.get(run_id)
        done = job.get("done") if job else None
    if done is None:
        return True
    return done.wait(max(0.1, float(timeout)))


def request_stop(run_id: str, reason: str = "user") -> dict:
    """请求停止：调用停止钩子，状态先转 stopping；收尾由启动方/监视线程完成。"""
    with _LOCK:
        job = _JOBS.get(run_id)
        if job is None:
            return {"ok": False, "message": f"任务不存在：{run_id}"}
        if job.get("status") not in ("running", "detached"):
            return {"ok": True, "status": job.get("status"),
                    "message": "任务已结束，无需停止", "snapshot": snapshot(job)}
        fn = job.get("_stop_fn")
        job["status"] = "stopping"
        job["stop_reason"] = reason
        job["done"] = job.get("done") or threading.Event()
    _persist(job)
    if fn is None:
        # 常驻设备任务：调用方自己按 meta 里的 PID 停
        return {"ok": True, "status": "stopping", "detached": True,
                "snapshot": snapshot(job)}
    try:
        result = fn(reason) or {}
    except Exception as e:
        finish(job, "failed", error=str(e), stop_reason=reason)
        return {"ok": False, "status": "failed", "message": str(e),
                "snapshot": snapshot(job)}
    return {"ok": True, "status": job.get("status"),
            "result": result, "snapshot": snapshot(job)}


def _watchdog() -> None:
    while True:
        time.sleep(_WATCHDOG_INTERVAL)
        now = time.time()
        due = []
        with _LOCK:
            for job in _JOBS.values():
                if job.get("status") != "running":
                    continue
                deadline = float(job.get("deadline_ts") or 0)
                if deadline and now >= deadline:
                    due.append(job["run_id"])
        for run_id in due:
            request_stop(run_id, reason="timeout")


def _ensure_watchdog() -> None:
    global _WATCHDOG_STARTED
    with _LOCK:
        if _WATCHDOG_STARTED:
            return
        _WATCHDOG_STARTED = True
    threading.Thread(target=_watchdog, name="mcp-job-watchdog",
                     daemon=True).start()


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
