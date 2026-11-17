# services/perf_runner.py
"""性能采集执行器（无 Qt 依赖）。

从 PerfController / PerfWorker 里抽出来的纯执行部分，GUI 与 MCP 共用同一套
采样与探针逻辑：

* 主采样：`PerfService.collect_sample()` 按 interval 采 cpu / mem / fps / traffic；
* 旁路探针：sys_mem / netdev / iotop 按需启动；cpu 自动带 sys_cpu + threads，
  fps 自动带 fps_display（与 GUI 规则一致）；
* 会话：写入 `data/perf_data.json`，`perf_sessions` / `perf_report` 能直接看到；
* 停止：先挂探头序列到会话，再统一 stop + wait，最后落盘。

与 GUI 层的差别：这里不处理界面刷新、阈值告警弹窗、基线对比，也不做设备
掉线自愈（MCP 进程退出时任务即结束，符合 3.3 的策略）。
"""
from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Optional

from models.perf_model import DEFAULT_SCOPES, PerfModel, PerfSession
from services.perf_compat import AndroidCompat
from services.perf_probes import (
    FpsDisplayProbe, IoTopProbe, MemInfoSysProbe, NetDevProbe,
    ThreadsProbe, TopSysProbe,
)
from services.perf_service import PerfService

ALLOWED_METRICS = ("cpu", "mem", "fps", "traffic")
ALLOWED_PROBES = ("sys_mem", "netdev", "iotop")
MAX_DURATION_MINUTES = 180


class PerfRunner:
    def __init__(self, device, serial: str, output_dir: str,
                 device_service=None):
        self.device = device
        self.serial = serial or ""
        self.output_dir = output_dir
        self.device_service = device_service
        self.session: Optional[PerfSession] = None
        self.metrics: list = []
        self.probes: dict = {}
        self.packages: list = []
        self.interval = 5.0
        self.duration_minutes = 0.0
        self.samples = 0
        self.errors = 0
        self.last_error = ""
        self.stop_reason = ""
        self._stop_evt = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._service: Optional[PerfService] = None
        self._started_ts = 0.0
        self._last_sample = None
        self._last_save = 0.0
        self._finalized = False
        self._summary_cache: dict = {}
        self._scenario_thread: Optional[threading.Thread] = None
        self._scenario_abort: Optional[threading.Event] = None
        self._scenario_state = ""
        self._scenario_result: dict = {}

    # ---------- 启动 ----------
    def start(self, package: str, processes: Optional[list],
              metrics: Optional[list], probes: Optional[list],
              interval: float = 5.0, duration_minutes: float = 60.0,
              scenario: Optional[dict] = None) -> dict:
        metrics = [m for m in (metrics or ALLOWED_METRICS)
                   if m in ALLOWED_METRICS]
        if not metrics:
            raise ValueError(f"metrics 不能为空，可选：{' / '.join(ALLOWED_METRICS)}")
        probes = [p for p in (probes or []) if p in ALLOWED_PROBES]
        interval = max(0.5, min(60.0, float(interval or 5.0)))
        duration_minutes = float(duration_minutes or 0)
        if duration_minutes < 0 or duration_minutes > MAX_DURATION_MINUTES:
            raise ValueError(f"duration_minutes 范围 0..{MAX_DURATION_MINUTES}")

        compat = AndroidCompat(self.device)
        ok, running = compat.is_package_running_checked(package)
        if ok and not running:
            raise ValueError(f"应用 {package} 未在运行，先启动再采集")

        normalized = []
        for name in (processes or []):
            name = (name or "").strip()
            if name and name not in normalized:
                normalized.append(name)
        if package in normalized:
            normalized.remove(package)
        normalized.insert(0, package)

        service = PerfService(self.device, compat)
        session = PerfSession(
            id=PerfModel().gen_session_id(),
            name=time.strftime("%Y%m%d_%H%M%S"),
            device_serial=self.serial,
            app_package=package,
            app_processes=normalized,
            start_time=datetime.now().isoformat(),
            sample_interval=interval,
            metrics=metrics,
            scopes=dict(DEFAULT_SCOPES),
        )
        try:
            info = service.get_device_info()
            session.screen_resolution = info.get("resolution", "")
            session.screen_density = info.get("density", "")
            session.android_version = info.get("android", "") or compat.version
        except Exception:
            pass

        scenario = dict(scenario or {})
        case_ids = [c for c in (scenario.get("case_ids") or []) if c]
        if case_ids:
            from models.project_model import ProjectModel
            project = ProjectModel()
            names = []
            for cid in case_ids:
                node = project.get_node_by_id(cid)
                if node is not None and node.type == "case":
                    names.append(node.name)
            scenario["case_ids"] = case_ids
            session.suite_name = str(scenario.get("suite_name") or "")
            session.case_ids = case_ids
            session.case_names = names

        self.session = session
        self.metrics = metrics
        self.packages = normalized
        self.interval = interval
        self.duration_minutes = duration_minutes
        self._service = service
        self._started_ts = time.time()
        self._stop_evt.clear()
        self._finalized = False

        self._start_probes(probes)
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="mcp-perf-runner")
        self._thread.start()
        if scenario.get("case_ids"):
            self._start_scenario(scenario)
        return {
            "session_id": session.id,
            "name": session.name,
            "package": package,
            "processes": normalized,
            "metrics": metrics,
            "probes": probes,
            "interval": interval,
            "duration_minutes": duration_minutes,
            "started_at": session.start_time,
            "scenario": {
                "suite_name": session.suite_name,
                "case_ids": session.case_ids,
                "case_names": session.case_names,
                "loop": int(scenario.get("loop") or 1),
                "stop_on_failure": bool(scenario.get("stop_on_failure")),
            } if session.case_ids else None,
        }

    # ---------- 场景化：采集期间跑用例 ----------
    def _start_scenario(self, scenario: dict) -> None:
        if self.device_service is None:
            raise ValueError("场景化模式需要 device_service")
        from models.execution_model import ExecutionModel
        from models.project_model import ProjectModel
        from models.step_model import StepModel
        from services.case_runner import CaseRunner

        case_ids = list(scenario.get("case_ids") or [])
        loop = max(1, int(scenario.get("loop") or 1))
        stop_on_fail = bool(scenario.get("stop_on_failure"))
        self._scenario_abort = threading.Event()
        self._scenario_state = "running"
        self._scenario_result = {}

        def work():
            try:
                runner = CaseRunner(
                    case_ids, ProjectModel(), StepModel(), self.device_service,
                    ExecutionModel(), loop_count=loop, stop_on_fail=stop_on_fail,
                    abort_event=self._scenario_abort)
                result = runner.run()
                self._scenario_result = {
                    "cases": [
                        {"case_id": c.case_id, "case_name": c.case_name,
                         "ok": c.ok, "error": c.error,
                         "duration_ms": c.duration_ms}
                        for c in result.cases
                    ],
                    "aborted": bool(result.aborted),
                }
                self._scenario_state = ("aborted" if result.aborted
                                        else "finished")
            except Exception as e:
                self._scenario_result = {"error": str(e)}
                self._scenario_state = "error"

        self._scenario_thread = threading.Thread(
            target=work, daemon=True, name="mcp-perf-scenario")
        self._scenario_thread.start()

    def _start_probes(self, wanted: list) -> None:
        def _interval() -> float:
            # 线程探针要读 200+ 个 task/stat，跟主采样会把 adb 通道抢满
            return max(self.interval, 5.0)

        pid_provider = None
        if "cpu" in self.metrics or "fps" in self.metrics:
            resolver = PerfService(self.device, None)

            def pid_provider():
                try:
                    pids = resolver._find_app_pids(self.session.app_package)
                except Exception:
                    return ""
                return pids[0] if pids else ""

        def _start(probe, interval=None, with_pid=False):
            if interval is None:
                # 可选探针保持自己的默认节奏：sys_mem 的 dumpsys 一次 3~5 秒，
                # 跟着 2 秒的主采样跑会把 u2 通道打满、每次都超时
                interval = max(self.interval,
                               float(getattr(probe, "default_interval", 5.0)))
            out_dir = self._artifact_dir(probe.key)
            probe.bind(self.device, self.serial, out_dir,
                       pid_provider=pid_provider if with_pid else None)
            probe.start(interval)
            self.probes[probe.key] = probe

        if "sys_mem" in wanted:
            _start(MemInfoSysProbe())
        if "netdev" in wanted:
            _start(NetDevProbe())
        if "iotop" in wanted:
            _start(IoTopProbe())
        if "cpu" in self.metrics:
            _start(TopSysProbe(), _interval())
            _start(ThreadsProbe(), _interval(), with_pid=True)
        if "fps" in self.metrics:
            probe = FpsDisplayProbe()
            probe.package = self.session.app_package
            _start(probe, _interval(), with_pid=True)

    def _artifact_dir(self, key: str) -> str:
        base = self.output_dir
        if self.serial:
            base = f"{base}/{self.serial}"
        return f"{base}/perf/{self.session.name}/{key}"

    # ---------- 采样循环 ----------
    def _loop(self) -> None:
        deadline = (self._started_ts + self.duration_minutes * 60
                    if self.duration_minutes else None)
        while not self._stop_evt.is_set():
            if deadline is not None and time.time() >= deadline:
                self.stop_reason = "duration"
                break
            started = time.time()
            try:
                sample = self._service.collect_sample(self.packages, self.metrics)
            except Exception as e:
                sample = None
                self.last_error = str(e)
            if sample is None:
                self.errors += 1
            else:
                self.errors = 0
                self.session.samples.append(sample)
                self.samples += 1
                self._last_sample = sample
                self._maybe_save()
            elapsed = time.time() - started
            wait = max(0.05, self.interval - elapsed)
            self._stop_evt.wait(wait)
        if not self.stop_reason:
            self.stop_reason = "user"

    def _maybe_save(self) -> None:
        now = time.time()
        if now - self._last_save < 30:
            return
        self._last_save = now
        self._save_session()

    def _save_session(self) -> None:
        if self.session is None:
            return
        try:
            model = PerfModel()
            existing = model.get_session(self.session.id)
            if existing is None:
                model.sessions.append(self.session)
            else:
                model.sessions[model.sessions.index(existing)] = self.session
            model.save()
        except Exception:
            pass

    # ---------- 状态 ----------
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def wait(self, timeout: Optional[float] = None) -> bool:
        """等待采样线程结束；返回是否已结束。"""
        thread = self._thread
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

    def status(self) -> dict:
        sample = self._last_sample
        latest = {}
        if sample is not None:
            try:
                latest = sample.to_dict()
            except Exception:
                latest = {}
        probes = {}
        for key, probe in self.probes.items():
            try:
                probes[key] = len(probe.samples())
            except Exception:
                probes[key] = 0
        return {
            "session_id": self.session.id if self.session else "",
            "running": self.running(),
            "samples": self.samples,
            "errors": self.errors,
            "last_error": self.last_error,
            "elapsed_s": int(time.time() - self._started_ts) if self._started_ts else 0,
            "duration_minutes": self.duration_minutes,
            "latest": {k: latest.get(k) for k in
                       ("cpu_percent", "mem_pss_mb", "mem_res_mb", "fps",
                        "rx_bytes", "tx_bytes")
                       if k in latest},
            "probe_samples": probes,
            "stop_reason": self.stop_reason,
            "scenario": self._scenario_status(),
        }

    def _scenario_status(self) -> Optional[dict]:
        if self._scenario_thread is None and not self._scenario_state:
            return None
        return {
            "state": self._scenario_state,
            "running": bool(self._scenario_thread
                            and self._scenario_thread.is_alive()),
            "result": self._scenario_result,
        }

    # ---------- 停止 ----------
    def stop(self) -> dict:
        self._stop_evt.set()
        if self._scenario_abort is not None:
            self._scenario_abort.set()
        if self._scenario_thread is not None:
            self._scenario_thread.join(timeout=20)
        if self._thread is not None:
            self._thread.join(timeout=20)
            self._thread = None
        if self._finalized:
            return self._summary()
        self._finalized = True

        # 先挂探头序列，再统一 stop + wait（与 GUI 的收尾顺序一致）
        for key, probe in self.probes.items():
            try:
                series = probe.samples()
            except Exception:
                series = []
            if series:
                field = {
                    "sys_mem": "sys_mem_samples",
                    "sys_cpu": "sys_cpu_samples",
                    "netdev": "netdev_samples",
                    "iotop": "iotop_samples",
                    "threads": "thread_samples",
                    "fps_display": "fps_display_samples",
                }.get(key)
                if field:
                    setattr(self.session, field, series)
                if key == "threads":
                    try:
                        self.session.thread_rank = probe.rank()
                    except Exception:
                        pass
                if key == "fps_display":
                    try:
                        self.session.fps_events = probe.events()
                    except Exception:
                        pass
        for probe in self.probes.values():
            try:
                probe.stop()
            except Exception:
                pass
        for key, probe in self.probes.items():
            try:
                probe.wait(timeout=15)
            except Exception:
                pass
            if key == "fps_display":
                try:
                    self.session.fps_events = probe.events()
                except Exception:
                    pass

        self.session.end_time = datetime.now().isoformat()
        if self.session.samples or any(
                getattr(self.session, f) for f in
                ("sys_mem_samples", "sys_cpu_samples", "netdev_samples",
                 "iotop_samples", "thread_samples", "fps_display_samples")):
            self._save_session()
        probes = {key: len(getattr(probe, "samples")())
                  for key, probe in self.probes.items()}
        self.probes = {}
        self._summary_cache = {
            "session_id": self.session.id,
            "name": self.session.name,
            "samples": self.samples,
            "duration_s": int(time.time() - self._started_ts),
            "stop_reason": self.stop_reason or "user",
            "probe_samples": probes,
            "artifacts": {},
            "scenario": self._scenario_status(),
        }
        return self._summary()

    def _summary(self) -> dict:
        data = dict(self._summary_cache or {})
        if self.session is not None:
            try:
                data["stats"] = self.session.get_stats()
            except Exception:
                data["stats"] = {}
            data["end_time"] = self.session.end_time
        return data
