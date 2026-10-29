# controllers/perf_controller.py
"""性能检测控制器"""
import csv
import logging
import os
import time
from datetime import datetime

from PyQt6.QtCore import QObject, QThread, pyqtSignal, QTimer, Qt

from models.perf_model import (
    PerfModel, PerfSession, PerfThreshold, PerfBaseline, compare_stats,
    DEFAULT_SCOPES, SCOPE_LABELS, scope_conflict_note,
)
from services.perf_compat import AndroidCompat
from services.perf_service import PerfService
from utils.toast import show_toast
from utils.dialogs import WarningDialog, ErrorDialog

logger = logging.getLogger(__name__)


class PerfWorker(QThread):
    """后台采集线程"""
    sample_ready = pyqtSignal(object)       # PerfSample
    error_occurred = pyqtSignal(str)
    finished_all = pyqtSignal()
    # 设备连接中断 / 恢复。采集线程本身会自愈（等主窗口重连后继续采），
    # 这两个信号只负责让界面「看得见」发生了什么。
    link_lost = pyqtSignal()
    link_restored = pyqtSignal()

    def __init__(self, device_service, package, metrics, interval,
                 duration_minutes=None, parent=None, processes=None):
        super().__init__(parent)
        self.device_service = device_service
        self.package = package
        # 目标进程列表（主进程在第一个）：CPU / 内存按这些进程求和
        self.packages = list(processes) if processes else [package]
        self.metrics = metrics
        self.interval = max(0.1, interval)
        # 监控时长上限（分钟）：None = 不限时；到点后线程自然退出并 emit finished_all
        self.duration_minutes = duration_minutes
        self._running = True
        self._paused = False
        # 当前采集 Service 绑定的设备对象。掉线后 device_service.device 会变成
        # None（disconnect）或换一个新对象（重连），据此判断是否需要重建，
        # 避免拿失效的旧对象一直空采（见 _ensure_service）。
        self._service = None
        self._device_obj = None
        self._link_down = False
        # 连续多少轮「一项数据都没采到」才判定链路有问题（先容忍偶发抖动）
        self._consecutive_failed = 0

    # 连续失败到该次数就动手探活 + 重建连接
    LINK_FAIL_THRESHOLD = 3

    def pause(self):
        self._paused = True

    def resume(self):
        self._paused = False

    def stop(self):
        self._running = False

    def _ensure_service(self) -> bool:
        """按当前设备对象解析/重建采集 Service。

        返回 True 表示设备就绪、可以采样；False 表示设备缺失（调用方应等待重连）。

        为什么要每轮重取：采集线程原先只在启动时解析一次 device 并缓存，
        掉线后（如 Android Studio 启动会重启 adb server，设备短暂从 adb devices
        消失 → 主窗口判定掉线 → device_svc.disconnect()）手里还是失效的旧对象，
        每次采样都抛异常，且不会自愈。改成惰性重取后：设备缺失就等待，
        主窗口重连拿到新对象后自动重建 Service 继续采集。
        （与 DeviceService.perform 里的惰性重连是同一套思路。）
        """
        current = self.device_service.device
        if current is None:
            return False
        if self._service is None or current is not self._device_obj:
            self._device_obj = current
            compat = AndroidCompat(current)
            self._service = PerfService(current, compat)
            # 首次采样有些指标（FPS）需要预热
            try:
                self._service.collect_sample(self.packages, self.metrics)
            except Exception:
                pass
        return True

    def _try_recover_link(self) -> bool:
        """探活 + 重建设备通道。返回 True 表示已恢复。

        为什么要这一步：adb server 被外部工具（如 Android Studio）重启时，
        `adb devices` 前后可能完全一致 → 主窗口的掉线判定不触发 → 不会重连 →
        device_service.device 一直是个「非 None 却已失效」的旧对象。旧逻辑只在
        device is None 时才自愈，这种「假在线」就一直空采（内存卡片「当前 0MB」
        而峰值正常，就是这么来的）。这里用内置 adb 探活，在线就把对象重建。
        """
        serial = getattr(self.device_service, 'serial', None)
        try:
            if not self.device_service.check_device_online(serial):
                return False
            self.device_service.connect(serial)
            # 置空 → 下一轮 _ensure_service 会用新对象重建 Service
            self._service = None
            self._device_obj = None
            logger.info("[PerfMonitor] 设备仍在线，已重建连接通道")
            return True
        except Exception as e:
            logger.warning("[PerfMonitor] 尝试重建连接失败: %s", e)
            return False

    def _recover_or_report_link(self):
        """连续多轮采不到数据时的处置：**先自救，确实掉线才报**。"""
        if self._try_recover_link():
            self._consecutive_failed = 0
            if self._link_down:
                self._link_down = False
                self.link_restored.emit()
            return
        if not self._link_down:
            self._link_down = True
            self.link_lost.emit()

    def run(self):
        # 监控时长上限：按墙钟计算（含暂停/掉线等待期间），到点自动停
        deadline = (time.time() + self.duration_minutes * 60
                    if self.duration_minutes else None)

        while self._running:
            if deadline is not None and time.time() >= deadline:
                self._running = False
                break

            if self._paused:
                time.sleep(0.2)
                continue

            # 设备掉线：不采样，等主窗口重连（自愈）。掉线/恢复各提示一次。
            if not self._ensure_service():
                if not self._link_down:
                    self._link_down = True
                    self.link_lost.emit()
                time.sleep(0.2)
                continue
            if self._link_down:
                self._link_down = False
                self.link_restored.emit()

            start = time.time()

            sample = None
            try:
                sample = self._service.collect_sample(self.packages, self.metrics)
            except Exception as e:
                logger.exception("采样失败")
                self.error_occurred.emit(str(e))

            if sample is None:
                # 本轮一项数据都没采到（全指标失败 / 抛异常）：先怀疑通道
                self._consecutive_failed += 1
                if self._consecutive_failed >= self.LINK_FAIL_THRESHOLD:
                    self._recover_or_report_link()
            else:
                self._consecutive_failed = 0
                self.sample_ready.emit(sample)
                if self._link_down:
                    self._link_down = False
                    self.link_restored.emit()

            # 计算下次采样时间的等待时长
            elapsed = time.time() - start
            wait = self.interval - elapsed
            # 若采集本身耗时超过间隔，则至少间隔 0.05s 再采（避免过载）
            if wait < 0.05:
                wait = 0.05

            # 分片等待，便于快速响应 stop
            end_time = time.time() + wait
            while self._running and time.time() < end_time:
                time.sleep(0.05)

        self.finished_all.emit()


class ScenarioWorker(QThread):
    """场景化模式：执行用例并驱动采集"""
    progress = pyqtSignal(str, str)         # (message, log_type)
    finished_all = pyqtSignal()

    def __init__(self, case_ids, project_model, step_model,
                 device_service, loop_count=1, stop_on_fail=False, parent=None):
        super().__init__(parent)
        self.case_ids = case_ids
        self.project_model = project_model
        self.step_model = step_model
        self.device_service = device_service
        self.loop_count = loop_count
        self.stop_on_fail = stop_on_fail
        self._abort = False
        # 内部真正干活的 ExecutionWorker（run() 里创建）。必须存引用：
        # 「停止」要把中止请求转发给它，否则车机端会一直跑到循环次数用尽。
        self._worker = None

    def stop(self):
        """用户点「停止」：让内部执行线程在当前步骤跑完后收尾退出。

        原来只设了 self._abort，而 run() 里的 ExecutionWorker 是个局部变量、
        这个标志也从没被任何地方读过 —— 于是「停止」对场景化完全无效，
        车机端的自动化会继续执行（2026-10-10 用户反馈）。
        """
        if self._abort:
            return
        self._abort = True
        if self._worker is not None:
            self._worker.request_abort()

    def run(self):
        from controllers.execution_controller import ExecutionWorker
        # 直接复用项目里的 ExecutionWorker 逻辑
        worker = ExecutionWorker(
            self.case_ids,
            self.project_model,
            self.step_model,
            self.device_service,
            None,   # exec_model 传 None 无所谓，因为我们不收集日志到 exec_model
            loop_count=self.loop_count,
            stop_on_fail=self.stop_on_fail,
        )
        self._worker = worker
        if self._abort:
            # stop() 比 run() 先到的竞态：补一次转发，别让本轮白跑完
            worker.request_abort()
        # 重定向 progress 信号
        worker.progress.connect(lambda m, t: self.progress.emit(m, t))
        try:
            worker.run()
        finally:
            self._worker = None
        self.finished_all.emit()


class PerfController(QObject):
    """性能检测控制器"""

    # 消息中心用（纯新增）：采集完成 / 阈值异常
    perf_finished = pyqtSignal(int)   # 采样点数
    perf_alert = pyqtSignal(str)      # 异常文案

    # 堆转储结果回投主线程（worker 线程不直接碰 GUI，避免跨线程操作导致界面未响应）
    hprof_finished = pyqtSignal(str, str)   # (status: 'success'/'error', message)
    smaps_finished = pyqtSignal(str, str)   # (status: 'success'/'error', message)
    record_finished = pyqtSignal(str, str)  # (status: 'success'/'error', message)
    capture_ready_checked = pyqtSignal(bool, str)   # 抓包能力前置探测结果 (ok, reason)
    # 截图结果回投主线程（状态文字 / 失败 toast）
    screenshot_finished = pyqtSignal(str, str)   # (status: 'success'/'error', message)
    # 启动测试等结果 -> 底部「虫师日志」（main.py 里连 append_bottom_log）
    log_emitted = pyqtSignal(str)

    def __init__(self, perf_model: PerfModel, perf_view,
                 device_service, project_model, step_model, suite_model,
                 logs_view=None, parent=None):
        super().__init__(parent)
        self.model = perf_model
        self.view = perf_view
        self.device_service = device_service
        self.project_model = project_model
        self.step_model = step_model
        self.suite_model = suite_model
        self.logs_view = logs_view

        self.current_session = None
        self.perf_worker = None
        self.scenario_worker = None
        self._alert_cache = {}  # {metric: bool}
        self._last_worker_error = None  # 采样异常去重（同一条只提示一次）

        # 堆转储自动循环状态
        self._hprof_loop_timer = None
        self._hprof_dumping = False
        # 内存明细（smaps）自动循环 / 抓取状态
        self._smaps_loop_timer = None
        self._smaps_dumping = False
        # 录屏自动循环 / 录制状态
        self._record_loop_timer = None
        self._recording = False
        # 抓包能力前置探测（置灰用）：None=未探测，True/False=结论
        self._capture_ready = None
        self._capture_ready_reason = ""
        self._capture_probed_serial = ""
        self._capture_probing = False
        # 截图自动循环状态
        self._screenshot_loop_timer = None
        self._screenshot_busy = False
        # 抓包状态
        self._capture_worker = None
        # 本次抓包是不是「随监控自动拉起」的（决定失败时弹模态还是轻提示）
        self._capture_auto_started = False
        # 部署 tcpdump 期间收到过「停止」：部署线程完成时不要再把抓包拉起来
        # （自动拉起 + 用户秒停 时最容易撞上，否则会出现"监控已停、抓包还在跑"）
        self._capture_stop_requested = False

        # 旁路探针（系统内存 / 后续的整机 CPU、网卡流量）：key -> probe 实例
        # 注意：必须挂在 Controller 上 —— 2026-10-25 曾误加进 PerfWorker，
        # 结果开始监控直接报 'PerfController' object has no attribute '_probes'
        self._probes = {}
        self._probe_timers = {}

        # 采样统计刷新定时器
        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(1000)
        self._stats_timer.timeout.connect(self._refresh_stats)

        # 堆转储 / 截图结果回主线程处理（toast / 状态文字）
        self.hprof_finished.connect(self._on_hprof_finished)
        self.smaps_finished.connect(self._on_smaps_finished)
        self.record_finished.connect(self._on_record_finished)
        self.capture_ready_checked.connect(self._on_capture_ready_checked)
        self.screenshot_finished.connect(self._on_screenshot_finished)

        self._connect_signals()
        self._check_compat_on_startup()

    # ------------------------------------------------------------------
    def _connect_signals(self):
        self.view.start_requested.connect(self._on_start)
        self.view.stop_requested.connect(self._on_stop)
        self.view.app_list_refresh_requested.connect(self._on_refresh_apps)
        self.view.export_csv_requested.connect(self._on_export_csv)
        self.view.export_json_requested.connect(self._on_export_json)
        self.view.baseline_requested.connect(self._on_save_baseline)
        self.view.baseline_manager_requested.connect(self.open_baseline_manager)
        self.view.clear_requested.connect(self._on_clear_requested)
        self.view.threshold_config_requested.connect(self._on_threshold_config)
        self.view.report_requested.connect(self._on_generate_report)
        # 堆转储 / 抓包 / 截图（性能检测页工具卡片）
        self.view.hprof_toggle_requested.connect(self._on_hprof_toggle)
        self.view.hprof_once_requested.connect(self._on_hprof_once)
        self.view.hprof_interval_changed.connect(self._on_hprof_interval_changed)
        self.view.smaps_once_requested.connect(self._dump_smaps_now)
        self.view.smaps_interval_changed.connect(self._on_smaps_interval_changed)
        self.view.record_once_requested.connect(self._record_now)
        self.view.record_toggle_requested.connect(self._on_record_toggle)
        self.view.record_interval_changed.connect(self._on_record_cfg_changed)
        self.view.record_duration_changed.connect(self._on_record_cfg_changed)
        self.view.packet_toggle_requested.connect(self._on_packet_toggle)
        self.view.packet_config_requested.connect(self._on_capture_config)
        self.view.screenshot_toggle_requested.connect(self._on_screenshot_toggle)
        self.view.screenshot_once_requested.connect(self._take_screenshot_now)
        self.view.screenshot_interval_changed.connect(
            self._on_screenshot_interval_changed)

    # ------------------------------------------------------------------
    def _check_compat_on_startup(self):
        """设备由主窗口统一维护，这里只做轮询，等设备就绪再拉应用"""
        self._last_device_serial = None
        # 应用列表没拿到之前的剩余重试次数（见 _maybe_refresh_apps）
        self._apps_retry_left = 0
        self._device_watch_timer = QTimer(self)
        self._device_watch_timer.setInterval(2000)
        self._device_watch_timer.timeout.connect(self._maybe_refresh_apps)
        self._device_watch_timer.start()

    # 拉应用列表失败后的自动重试次数（每 2 秒一次）
    APP_LIST_RETRY = 3

    def _maybe_refresh_apps(self):
        """设备已就绪时保证应用列表被拉到 —— 失败会重试，不会一直空着。

        旧逻辑只在 serial 变化时拉一次，且**先记账再拉取**：那一次如果失败，
        后续轮询都判定「设备没变」而跳过 → 列表永远停在「未检测到应用」，
        只能靠用户手动点「刷新」或拔插设备。连接瞬间的通道抖动就会撞上这个坑。
        """
        if not self.device_service.device:
            if self._last_device_serial is not None:
                self._last_device_serial = None
                self._apps_retry_left = 0
                self.view.update_app_list([])  # 清空
            return

        current = self.device_service.serial
        if current and current != self._last_device_serial:
            self._last_device_serial = current
            self._apps_retry_left = self.APP_LIST_RETRY
            self._on_refresh_apps()
            return

        # 设备就绪后顺手探一次抓包能力（结果用于置灰，每台设备只探一次）
        if current and getattr(self.view, '_apps_loaded', False):
            self._probe_capture_capability()

        # 同一台设备、但列表还没拿到 → 继续重试（限次）
        if current and not getattr(self.view, '_apps_loaded', False) and self._apps_retry_left > 0:
            self._apps_retry_left -= 1
            logger.debug("[PerfController] 应用列表为空，自动重试（剩 %d 次）", self._apps_retry_left)
            self._on_refresh_apps()

    def _on_refresh_apps(self):
        """刷新应用列表 + 更新指标可用性。

        用 checked 版拿到「命令是否成功」——两条 pm 命令都失败时，空列表**不代表
        设备上没有应用**，此时把下拉框提示换成「点刷新重试」，不再让用户误以为
        真的没应用（2026-10-11 修）。
        """
        if not self.device_service.device:
            return
        try:
            compat = AndroidCompat(self.device_service.device)
            ok, packages = compat.list_app_processes_checked()
            self.view.update_app_list(packages, load_failed=not ok)
            if ok and packages:
                self._apps_retry_left = 0      # 拿到了，别再重试
            if not ok:
                logger.warning("[PerfController] 应用列表读取失败（通道异常/超时）")

            # 更新指标可用性
            available = {}
            reasons = {}
            for key in ('cpu', 'mem', 'fps', 'traffic'):
                ok_m = compat.is_metric_available(key)
                available[key] = ok_m
                if not ok_m:
                    reasons[key] = compat.get_unavailable_reason(key)
            self.view.update_metric_availability(available, reasons)
        except Exception as e:
            logger.exception("刷新应用列表失败")

    # ------------------------------------------------------------------
    # 开始/暂停/继续/停止
    # ------------------------------------------------------------------
    def _on_start(self, payload: dict):
        package = payload['package']
        # 目标进程列表（主进程在第一个）：没勾多进程时就是单个进程
        processes = []
        for p in (payload.get('processes') or []):
            if p and p not in processes:
                processes.append(p)
        # 主进程必须在第一个：FPS / 流量 / 线程按它采集
        if package in processes:
            processes.remove(package)
        processes.insert(0, package)
        metrics = payload['metrics']
        interval = payload['interval']
        mode = payload['mode']

        # 设备由主窗口统一维护，这里只做校验
        if not self.device_service.device:
            ErrorDialog.show_error(
                self.view, "未连接设备",
                "请先在顶部工具栏中选择并连接设备，再开始性能采集"
            )
            return

        # 检查应用是否运行。
        # 关键：pidof **查询失败**（通道异常/超时）不能等同于「应用没运行」——
        # 以前两者都走 shell_text 吞成空串，于是通道一抖就弹「应用未在运行」，
        # 而车机上应用明明跑着（2026-10-11 修）。这里用 checked 版区分开。
        try:
            compat = AndroidCompat(self.device_service.device)
            ok, running = compat.is_package_running_checked(package)
            if not ok:
                # 查不到就先探活：设备在线说明是短暂抖动，让用户重试即可；
                # 设备真的不在线才提示重连。
                if not self.device_service.check_device_online():
                    ErrorDialog.show_error(
                        self.view, "设备连接异常",
                        "与设备的连接已中断，采集无法开始。\n"
                        "请检查 USB 连接或重新连接设备后重试。"
                    )
                    return
                WarningDialog.show_warning(
                    self.view, "读取失败",
                    f"暂时读不到应用 {package} 的运行状态（设备通道忙或刚重连）。\n"
                    "请稍后重试；若持续失败，可在顶部工具栏重新选择设备。"
                )
                return
            if not running:
                WarningDialog.show_warning(
                    self.view, "提示",
                    f"应用 {package} 未在运行，请先启动应用再开始采集"
                )
                return
        except Exception:
            pass

        # 创建会话
        session = PerfSession(
            id=self.model.gen_session_id(),
            name=time.strftime("%Y%m%d_%H%M%S"),
            device_serial=self.device_service.serial or "",
            app_package=package,
            app_processes=processes,
            start_time=datetime.now().isoformat(),
            sample_interval=interval,
            metrics=metrics,
            # 登记本轮的统计口径（选谁算谁 / 流量按整个应用），供报告与基线对比识别
            scopes=dict(DEFAULT_SCOPES),
        )
        # 设备信息（分辨率 / 屏幕密度 / 安卓版本）一次性采集，用于报告的「基本信息」
        try:
            _compat = AndroidCompat(self.device_service.device)
            _svc = PerfService(self.device_service.device, _compat)
            _info = _svc.get_device_info()
            session.screen_resolution = _info.get('resolution', '')
            session.screen_density = _info.get('density', '')
            session.android_version = _info.get('android', '') or _compat.version
        except Exception as e:
            logger.debug("采集设备信息失败（不影响采集）: %s", e)
        self.current_session = session
        self._alert_cache = {k: False for k in metrics}
        self._last_worker_error = None

        # 清空视图数据
        for card in self.view._cards.values():
            if hasattr(card, 'clear_data'):
                card.clear_data()
            elif hasattr(card, 'reset'):
                card.reset()

        # 启动采集线程
        self.perf_worker = PerfWorker(
            self.device_service, package, metrics, interval,
            duration_minutes=payload.get('duration_minutes'),
            processes=processes,
        )
        self.perf_worker.sample_ready.connect(self._on_sample)
        self.perf_worker.error_occurred.connect(self._on_worker_error)
        # 设备掉线/恢复：只做提示，采集线程自己会等重连后继续
        self.perf_worker.link_lost.connect(self._on_link_lost)
        self.perf_worker.link_restored.connect(self._on_link_restored)
        # 线程自然结束（达到预设监控时长）→ 自动走完整停止收尾；
        # 用户手动停止时 worker 已被置 None，晚到的信号会被 _on_worker_finished 幂等拦截
        self.perf_worker.finished_all.connect(self._on_worker_finished)
        self.perf_worker.start()

        # 启动统计定时器
        self._stats_timer.start()

        # 堆转储自动循环：勾选了「自动循环」则随监控一起启动（第一次转储在一个完整间隔后）
        if self.view.hprof_enable_check.isChecked():
            self._start_hprof_loop()

        # 内存明细自动循环：同堆转储口径
        if self.view.smaps_loop_enabled():
            self._start_smaps_loop()

        # 截图自动循环：同堆转储口径
        if self.view.screenshot_enable_check.isChecked():
            self._start_screenshot_loop()

        # 录屏自动循环：同截图口径（间隔必须大于单段时长，见 _start_record_loop）
        if self.view.record_loop_enabled():
            self._start_record_loop()

        # 抓包：勾选了「抓包」卡片就随监控一起开始
        # （停止那半边早就有了，见 _on_stop 里的 _stop_capture；此前只缺这半边，
        #  所以界面看着像"抓包和监控没关系"，2026-10-14 用户反馈）
        self._auto_start_capture_if_enabled()

        # 旁路探针（整机口径）：勾选了就随监控一起启动，各自跑后台线程
        self._start_probes(session)

        # 根据模式更新视图状态
        if mode == 'scenario':
            # 场景化：同时启动用例执行线程
            suite_name = payload['suite']
            suite = self.suite_model.get_suite_by_name(suite_name)
            if not suite:
                self._on_stop()
                WarningDialog.show_warning(self.view, "套件不存在", suite_name)
                return

            case_ids = []
            for cid in suite.case_ids:
                node = self.project_model.get_node_by_id(cid)
                if node:
                    case_ids.append(cid)
            if not case_ids:
                self._on_stop()
                WarningDialog.show_warning(self.view, "套件无有效用例", suite_name)
                return

            self.current_session.suite_name = suite_name
            self.current_session.case_ids = case_ids
            self.current_session.case_names = [
                self.project_model.get_node_by_id(c).name
                for c in case_ids if self.project_model.get_node_by_id(c)
            ]

            self.scenario_worker = ScenarioWorker(
                case_ids,
                self.project_model,
                self.step_model,
                self.device_service,
                loop_count=payload['loop'],
                stop_on_fail=payload['stop_on_fail'],
            )
            self.scenario_worker.progress.connect(self._on_scenario_progress)
            self.scenario_worker.finished_all.connect(self._on_scenario_finished)
            self.scenario_worker.start()

            self.view.set_state(self.view.STATE_SCENARIO)
            if self.logs_view:
                self.logs_view.add_log(
                    f"[性能+场景] 开始执行套件 '{suite_name}' 并采集性能数据", "info"
                )
        else:
            self.view.set_state(self.view.STATE_RUNNING)

    def _on_stop(self):
        # 幂等：已经停止则直接返回，避免场景化结束信号与用户点击重复触发
        if self.current_session is None and self.perf_worker is None \
                and self.scenario_worker is None:
            return
        # 停止采集
        if self.perf_worker:
            self.perf_worker.stop()
            self.perf_worker.wait(2000)
            self.perf_worker = None

        # 停止场景化执行：必须把中止请求转发给内部真正干活的 ExecutionWorker
        # （见 ScenarioWorker.stop 的说明），否则车机端会一直跑到循环次数用尽
        if self.scenario_worker:
            self.scenario_worker.stop()
            self.scenario_worker.wait(2000)
            self.scenario_worker = None

        self._stats_timer.stop()
        # 堆转储/截图自动循环随监控结束而停止（勾选状态保留，作为下次监控的预设）
        self._stop_hprof_loop()
        self._stop_smaps_loop()
        self._stop_screenshot_loop()
        self._stop_record_loop()
        # 抓包也随监控结束收尾：否则它会一直抓到时长/大小上限，甚至用户以为早就停了
        self._stop_capture()
        # 旁路探针统一收尾（P1 起的 meminfo_sys / top_sys / netdev 等在这里登记）
        self._finalize_probes()

        # 结束会话
        if self.current_session:
            self.current_session.end_time = datetime.now().isoformat()
            # 只有采到数据才保存
            if self.current_session.samples:
                self.model.add_session(self.current_session)
                # 刷新最终统计
                self._refresh_stats(final=True)
                show_toast(
                    self.view,
                    f"采集完成，共 {len(self.current_session.samples)} 个采样点"
                )
                # 消息中心：采集完成留痕（纯新增信号）
                self.perf_finished.emit(len(self.current_session.samples))
                # 基线自动回归对比：跑完自动比对同应用基线、判定劣化
                self._auto_compare_baseline(self.current_session)
            self.current_session = None

        self.view.set_state(self.view.STATE_IDLE)

    # ------------------------------------------------------------------
    # 旁路探针（系统内存等）
    # ------------------------------------------------------------------
    def _start_probes(self, session):
        """按勾选启动整机探针；整机 CPU / 线程随 CPU 自动启动。"""
        try:
            want_sys_mem = bool(getattr(self.view, "sys_mem_check", None)
                                and self.view.sys_mem_check.isChecked())
        except Exception:
            want_sys_mem = False
        if want_sys_mem:
            from services.perf_probes import MemInfoSysProbe
            self._start_probe(MemInfoSysProbe(), session)
        try:
            want_netdev = bool(getattr(self.view, "netdev_check", None)
                               and self.view.netdev_check.isChecked())
        except Exception:
            want_netdev = False
        try:
            want_iotop = bool(getattr(self.view, "iotop_check", None)
                              and self.view.iotop_check.isChecked())
        except Exception:
            want_iotop = False
        if want_netdev:
            from services.perf_probes import NetDevProbe
            self._start_probe(NetDevProbe(), session)
        if want_iotop:
            from services.perf_probes import IoTopProbe
            self._start_probe(IoTopProbe(), session)

        # 整机 CPU / 线程是 CPU 分析的附属数据：只要监控 CPU 就自动带起来，
        # 不再有独立复选框和卡片（2026-10-27 用户要求，对齐百度项目的 CPU 报告）
        if 'cpu' in (getattr(session, "metrics", None) or []):
            from services.perf_probes import ThreadsProbe, TopSysProbe
            interval = self._cpu_side_interval(session)
            self._start_probe(TopSysProbe(), session, interval=interval)
            self._start_probe(ThreadsProbe(), session, interval=interval,
                              pid_provider=self._make_pid_provider(session))

        # FPS 按显示屏/图层采集（含应用自建的仪表副屏），只要监控 FPS 就自动带起来
        if 'fps' in (getattr(session, "metrics", None) or []):
            from services.perf_probes import FpsDisplayProbe
            probe = FpsDisplayProbe()
            probe.package = session.app_package
            self._start_probe(probe, session,
                              interval=self._cpu_side_interval(session),
                              pid_provider=self._make_pid_provider(session))

    @staticmethod
    def _cpu_side_interval(session) -> float:
        """整机 CPU / 线程的采样间隔：跟随主采样，但不快于 5 秒。

        线程一次要读 200+ 个 `/proc/<pid>/task/*/stat`，跟 100ms/1s 的主节奏
        会把 adb 通道抢满；5 秒也是百度那套的默认节奏。
        """
        try:
            base = float(getattr(session, "sample_interval", 5.0) or 5.0)
        except (TypeError, ValueError):
            base = 5.0
        return max(base, 5.0)

    def _make_pid_provider(self, session):
        """给线程探针提供「所选进程」的 PID（精确匹配，不合并子进程）。

        单独建一个 PerfService 只为复用 _find_app_pids 的精确匹配逻辑：
        采集线程里的那个实例可能还没创建，而且跑在另一个线程里。
        """
        from services.perf_service import PerfService as _PerfService
        resolver = _PerfService(self.device_service.device, None)

        def _get():
            if not getattr(self.device_service, "device", None):
                return ""
            try:
                pids = resolver._find_app_pids(session.app_package)
            except Exception:
                return ""
            return pids[0] if pids else ""

        return _get

    def _start_probe(self, probe, session, pid_provider=None, interval=None):
        """绑定环境并启动后台采集；界面用 QTimer 读快照，不阻塞 GUI。"""
        base = self._device_export_dir()
        # 目录不预建：探针落盘时才创建（预建会留下空文件夹，用户会以为"没数据"）
        out_dir = os.path.join(base, "perf", getattr(session, "name", "session"), probe.key)
        probe.bind(self.device_service.device, self.device_service.serial, out_dir,
                   pid_provider=pid_provider)
        # 间隔优先级：显式传入（CPU 附属采集） > 卡片下拉 > 探针自带默认
        if interval is None:
            try:
                interval = float(self.view.probe_interval(probe.key))
            except Exception:
                interval = 0.0
        probe.start(interval or None)

        self._probes[probe.key] = probe
        timer = QTimer(self)
        timer.timeout.connect(lambda: self._refresh_probe(probe))
        timer.start(1000)                    # 1 秒刷一次界面即可，采样节奏由探针自己掌握
        self._probe_timers[probe.key] = timer
        logger.info("[Perf] 探针 %s 已启动（间隔 %.1fs，产物 %s）",
                    probe.key, probe._interval, out_dir)

    def _refresh_probe(self, probe):
        """把探针最新快照推给界面（失败只记日志，不打扰用户）。"""
        try:
            self.view.update_probe(probe.key, probe.latest())
        except Exception as e:
            logger.warning("[Perf] 刷新探针 %s 卡片失败: %s", probe.key, e)

    def _finalize_probes(self):
        """统一收尾所有旁路探针（`_probes`，P1 起登记）。

        顺序很关键：**先全部 stop**（发停止信号），**再统一 wait**（等落盘）——
        逐个 stop+wait 会出现"停 A 时 A 还在写文件，B 已经在抢同一个 adb 通道"。
        探针机制还没落地时 `_probes` 为空，这个函数是零成本空转。
        """
        for key, timer in dict(getattr(self, "_probe_timers", {}) or {}).items():
            try:
                timer.stop()
            except Exception:
                pass
        self._probe_timers = {}

        probes = dict(getattr(self, "_probes", None) or {})
        if not probes:
            return

        # 先把采集到的序列挂到会话上（报告要用），再停止
        if self.current_session is not None:
            for key, probe in probes.items():
                try:
                    series = probe.samples()
                except Exception:
                    series = []
                if not series:
                    continue
                if key == "sys_mem":
                    self.current_session.sys_mem_samples = series
                elif key == "sys_cpu":
                    self.current_session.sys_cpu_samples = series
                elif key == "netdev":
                    self.current_session.netdev_samples = series
                elif key == "iotop":
                    self.current_session.iotop_samples = series
                elif key == "threads":
                    self.current_session.thread_samples = series
                    try:
                        # 完整排行（全部线程）单独带出来，报告不再从 Top N 样本里推
                        self.current_session.thread_rank = probe.rank()
                    except Exception:
                        pass
                elif key == "fps_display":
                    self.current_session.fps_display_samples = series
                    try:
                        # 逐条日志事件（百度同款统计口径）
                        self.current_session.fps_events = probe.events()
                    except Exception:
                        pass
        for key, probe in probes.items():
            try:
                probe.stop()
            except Exception as e:
                logger.warning("[Perf] 停止探针 %s 失败: %s", key, e)
        for key, probe in probes.items():
            try:
                probe.wait(timeout=15)
                # 停止后再取一次事件：日志流可能还有最后几条没落库
                if key == "fps_display" and self.current_session is not None:
                    try:
                        self.current_session.fps_events = probe.events()
                    except Exception:
                        pass
                if getattr(probe, "artifact", ""):
                    logger.info("[Perf] 探针 %s 产物：%s", key, probe.artifact)
            except Exception as e:
                logger.warning("[Perf] 等待探针 %s 落盘失败: %s", key, e)
        if hasattr(self, "_probes"):
            self._probes.clear()

    def _on_clear_requested(self):
        """清空当前会话已采集的样本（视图已清卡片，控制器负责清数据）"""
        if self.current_session is not None:
            self.current_session.samples.clear()
            self.current_session.alerts.clear()
        self._alert_cache = {k: False for k in (self.current_session.metrics if self.current_session else [])}
        self._alert_hprof_done = False      # 三期：内存超阈值自动转储每会话只做一次
    # ------------------------------------------------------------------
    def _on_sample(self, sample):
        if not self.current_session:
            return
        self.current_session.samples.append(sample)
        self.view.append_sample(sample)
        # 阈值检查
        self._check_threshold(sample)

    def _check_threshold(self, sample):
        th = self.model.threshold
        if not th.enabled:
            return

        checks = [
            ('cpu', sample.cpu_percent, th.cpu_max, '>'),
            ('mem', sample.mem_pss_mb, th.mem_max, '>'),
            ('fps', sample.fps, th.fps_min, '<'),
        ]
        for key, val, limit, op in checks:
            if key not in self.current_session.metrics:
                continue
            triggered = (val > limit) if op == '>' else (val < limit)
            if triggered and not self._alert_cache.get(key, False):
                # 触发告警
                self._alert_cache[key] = True
                self.view.set_alert(key, True)
                msg = f"⚠ {key.upper()} 异常: {val:.1f}（阈值 {limit}）"
                self.current_session.alerts.append((sample.timestamp, key, msg))
                self.view.add_alert_log(msg)
                # 消息中心：阈值异常留痕（_alert_cache 保证同一指标一次会话只报一次）
                self.perf_alert.emit(msg)
                # 三期：内存首次越过阈值 → 自动转储一次，抓住「涨上去那一刻」
                if key == 'mem' and getattr(th, 'auto_hprof_on_alert', False) \
                        and not getattr(self, '_alert_hprof_done', True):
                    self._alert_hprof_done = True
                    self._auto_hprof_on_alert(val, limit)
            elif not triggered and self._alert_cache.get(key, False):
                # 恢复正常
                self._alert_cache[key] = False
                self.view.set_alert(key, False)

    def _auto_hprof_on_alert(self, value: float, limit: float):
        """内存越过阈值时自动转储一次（每会话一次，见 _alert_hprof_done）。"""
        logger.info("[Perf] 内存超阈值（%.0f MB > %.0f MB），自动触发堆转储", value, limit)
        try:
            self.view.add_alert_log(f"⚙ 内存超阈值（{value:.0f}MB），自动执行堆转储")
        except Exception:
            pass
        if self.logs_view:
            self.logs_view.add_log("[性能] 内存超阈值，自动堆转储", "info")
        self._dump_hprof_now()

    def _refresh_stats(self, final=False):
        if not self.current_session:
            return
        stats = self.current_session.get_stats()
        self.view.update_stats(stats)

    def _on_link_lost(self):
        """设备连接中断：采集已暂停，等主窗口重连后线程自动继续。"""
        logger.warning("[PerfController] 设备连接中断，采集暂停等待重连")
        from utils import log_colors
        self.log_emitted.emit(
            f"<span style='color:{log_colors.log_color(log_colors.WARNING)};'>"
            f"[性能] ⚠ 设备连接中断，采集已暂停，等待重连…</span>"
        )
        if self.logs_view:
            self.logs_view.add_log(
                "[性能] 设备连接中断，采集已暂停，等待重连…", "warning")
        try:
            self.view.add_alert_log("⚠ 设备连接中断 · 采集已暂停")
        except Exception:
            pass

    def _on_link_restored(self):
        """设备已重连：采集自动恢复。"""
        logger.info("[PerfController] 设备已重连，采集恢复")
        self._last_worker_error = None
        from utils import log_colors
        self.log_emitted.emit(
            f"<span style='color:{log_colors.log_color(log_colors.SUCCESS)};'>"
            f"[性能] ✅ 设备已重连，采集已恢复</span>"
        )
        if self.logs_view:
            self.logs_view.add_log("[性能] 设备已重连，采集已恢复", "success")

    def _on_worker_error(self, msg):
        logger.warning(f"[PerfController] 采集错误: {msg}")
        # 同一条错误只提示一次，避免每轮采样刷屏
        if msg == self._last_worker_error:
            return
        self._last_worker_error = msg
        from utils import log_colors
        self.log_emitted.emit(
            f"<span style='color:{log_colors.log_color(log_colors.ERROR)};'>"
            f"[性能] 采集异常：{msg}</span>"
        )
        if self.logs_view:
            self.logs_view.add_log(f"[性能] 采集异常：{msg}", "warning")

    def _on_worker_finished(self):
        """采集线程自然结束（达到预设监控时长）→ 走完整停止收尾。

        手动停止路径：_on_stop 先把 perf_worker 置 None，线程退出后晚到的
        finished_all 信号在这里被幂等拦截，不会重复收尾。
        """
        if self.perf_worker is None:
            return
        minutes = self.view.duration_spin.value()
        if self.logs_view:
            self.logs_view.add_log(
                f"[性能] 已达到预设监控时长（{minutes} 分钟），自动停止采集",
                "info",
            )
        show_toast(self.view, f"已达到监控时长（{minutes} 分钟），自动停止")
        self._on_stop()

    # ------------------------------------------------------------------
    # 堆转储 / 抓包（性能检测页工具卡片）
    # ------------------------------------------------------------------
    def _on_hprof_toggle(self, enabled: bool):
        """堆转储自动循环开关。

        勾选只是「预设」：监控未开始时不启动循环；监控进行中切换勾选则实时生效。
        """
        if not enabled:
            self._stop_hprof_loop()
        elif self.perf_worker is not None:
            self._start_hprof_loop()

    def _on_hprof_interval_changed(self):
        """间隔变更：若循环正在跑，重启定时器以套用新间隔"""
        if self._hprof_loop_timer is not None:
            self._hprof_loop_timer.setInterval(
                int(self.view.get_hprof_interval() * 1000))

    def _on_hprof_once(self):
        """手动触发一次堆转储（与循环互不干扰）"""
        self._dump_hprof_now()

    def _start_hprof_loop(self):
        package = self.view.get_selected_package()
        if not package:
            show_toast(self.view, "请先在左上角选择要监控的应用")
            self.view.hprof_enable_check.blockSignals(True)
            self.view.hprof_enable_check.setChecked(False)
            self.view.hprof_enable_check.blockSignals(False)
            return
        if self._hprof_loop_timer is None:
            self._hprof_loop_timer = QTimer(self)
            self._hprof_loop_timer.timeout.connect(self._dump_hprof_now)
        # 不立即执行：等一个完整间隔后触发第一次转储
        self._hprof_loop_timer.start(
            int(self.view.get_hprof_interval() * 1000))

    def _stop_hprof_loop(self):
        if self._hprof_loop_timer is not None:
            self._hprof_loop_timer.stop()

    def _dump_hprof_now(self):
        """对当前所选应用执行一次堆转储（后台线程，防重入）"""
        if self._hprof_dumping:
            return
        package = self.view.get_selected_package()
        if not package:
            show_toast(self.view, "请先在左上角选择要监控的应用")
            return
        if not self.device_service or not self.device_service.device:
            show_toast(self.view, "设备未连接")
            return
        self._hprof_dumping = True

        import subprocess, sys as _sys
        from utils.adb_path import get_adb_path
        from utils.settings import Settings as _Settings
        serial = self.device_service.serial
        adb = get_adb_path()
        # 目录在起线程前算好：线程里读 current_session 可能已被停止流程清空
        heap_dir = self._session_artifact_dir("heap")

        def worker():
            creationflags = subprocess.CREATE_NO_WINDOW if _sys.platform == 'win32' else 0
            try:
                # 由包名直接定位进程 PID（不再弹进程选择框）
                pid = None
                try:
                    r = subprocess.run(
                        [adb, "-s", serial, "shell", "pidof", package],
                        capture_output=True, text=True, timeout=5,
                        creationflags=creationflags,
                        encoding='utf-8', errors='replace',
                    )
                    out = (r.stdout or "").strip()
                    if out:
                        pid = out.split()[0]
                except Exception:
                    pid = None
                if not pid:
                    self.hprof_finished.emit(
                        "error", f"应用「{package}」未在运行")
                    return

                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                remote_path = f"/data/local/tmp/heap_{timestamp}.hprof"
                local_path = os.path.join(heap_dir, f"heap_{timestamp}.hprof")

                subprocess.run(
                    [adb, "-s", serial, "shell", "am", "dumpheap",
                     str(pid), remote_path],
                    check=True, timeout=60, creationflags=creationflags,
                    capture_output=True, text=True,
                    encoding='utf-8', errors='replace',
                )
                subprocess.run(
                    [adb, "-s", serial, "pull", remote_path, local_path],
                    check=True, timeout=120, creationflags=creationflags,
                    capture_output=True, text=True,
                    encoding='utf-8', errors='replace',
                )
                subprocess.run(
                    [adb, "-s", serial, "shell", "rm", remote_path],
                    timeout=5, creationflags=creationflags,
                    capture_output=True, text=True,
                    encoding='utf-8', errors='replace',
                )
                self.hprof_finished.emit("success", local_path)
            except subprocess.CalledProcessError as e:
                # dumpheap/pull 失败：清掉设备侧可能残留的 0 字节空文件，避免堆积垃圾
                try:
                    subprocess.run(
                        [adb, "-s", serial, "shell", "rm", remote_path],
                        timeout=5, creationflags=creationflags,
                    )
                except Exception:
                    pass
                self.hprof_finished.emit(
                    "error", self._hprof_fail_reason(e))
            except Exception as e:
                try:
                    subprocess.run(
                        [adb, "-s", serial, "shell", "rm", remote_path],
                        timeout=5, creationflags=creationflags,
                    )
                except Exception:
                    pass
                self.hprof_finished.emit("error", str(e))
            finally:
                self._hprof_dumping = False

        import threading
        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def _hprof_fail_reason(e: 'subprocess.CalledProcessError') -> str:
        """把 dumpheap/pull 的失败原因提炼成一句可读文案（日志用）"""
        err_text = (e.stderr or "") + (e.stdout or "") + (e.output or "")
        if "not debuggable" in err_text:
            return "应用不可调试，无法转储堆"
        # 取 stderr 里最有信息量的一行：优先异常详情行，跳过 "Exception occurred..." 标头
        lines = [ln.strip() for ln in (e.stderr or "").splitlines() if ln.strip()]
        for ln in lines:
            if "Exception" in ln and "occurred" not in ln:
                return ln
        if lines:
            return lines[0]
        return f"adb 命令执行失败（退出码 {e.returncode}）"

    def _on_hprof_finished(self, status: str, message: str):
        """堆转储结果回投到主线程：只弹 toast 结论，不写日志、不展示异常信息"""
        if status == "success":
            self._add_event("heap", os.path.basename(message))
            show_toast(self.view, "堆转储完成")
        else:
            show_toast(self.view, "堆转储失败", duration=4000)

    # ------------------------------------------------------------------
    # 截图卡片（自动循环 + 立即截图）
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # 内存明细（smaps）卡片：抓 /proc/<pid>/smaps 回本地
    # ------------------------------------------------------------------
    def _on_smaps_interval_changed(self):
        """间隔改了：循环在跑就按新间隔重排（不用停机重开）"""
        if self._smaps_loop_timer is not None and self._smaps_loop_timer.isActive():
            self._smaps_loop_timer.start(int(self.view.get_smaps_interval() * 1000))

    def _start_smaps_loop(self):
        package = self.view.get_selected_package()
        if not package:
            show_toast(self.view, "请先在左上角选择要监控的应用")
            self.view.smaps_enable_check.blockSignals(True)
            self.view.smaps_enable_check.setChecked(False)
            self.view.smaps_enable_check.blockSignals(False)
            return
        if self._smaps_loop_timer is None:
            self._smaps_loop_timer = QTimer(self)
            self._smaps_loop_timer.timeout.connect(self._dump_smaps_now)
        self._smaps_loop_timer.start(int(self.view.get_smaps_interval() * 1000))

    def _stop_smaps_loop(self):
        if self._smaps_loop_timer is not None:
            self._smaps_loop_timer.stop()

    def _smaps_out_dir(self) -> str:
        return self._session_artifact_dir("smaps")

    def _dump_smaps_now(self):
        """抓一次所选进程的 smaps（后台线程，防重入）。

        用 `adb exec-out cat` 直接把设备文件流到本地，不落设备临时文件——
        smaps 动辄十几 MB，装卸两次没必要，也避免残留。
        """
        if self._smaps_dumping:
            return
        package = self.view.get_selected_package()
        if not package:
            show_toast(self.view, "请先在左上角选择要监控的应用")
            return
        if not self.device_service or not self.device_service.device:
            show_toast(self.view, "设备未连接")
            return
        self._smaps_dumping = True

        import subprocess, sys as _sys, threading
        from utils.adb_path import get_adb_path
        serial = self.device_service.serial
        adb = get_adb_path()
        out_dir = self._smaps_out_dir()
        path = os.path.join(out_dir, time.strftime("smaps_%Y%m%d_%H%M%S.txt"))
        creationflags = subprocess.CREATE_NO_WINDOW if _sys.platform == 'win32' else 0

        def _read_once(cmd):
            """执行一条读 smaps 的命令：成功返回 (True, 字节数)，失败返回 (False, 原因)"""
            with open(path, "wb") as f:
                proc = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE,
                                      timeout=180, creationflags=creationflags)
            size = os.path.getsize(path) if os.path.exists(path) else 0
            if proc.returncode == 0 and size > 1024:
                return True, size
            # 失败原因：先看 stderr；adb exec-out 有时把远端 stderr 并进 stdout（文件里），
            # 所以再读文件头 —— 这就是"输出异常（42 字节）"看不到原因的原因
            reason = (proc.stderr or b"").decode("utf-8", "replace").strip()
            if not reason:
                try:
                    with open(path, "rb") as fh:
                        reason = fh.read(300).decode("utf-8", "replace").strip()
                except OSError:
                    reason = ""
            return False, reason

        def _human_reason(reasons) -> str:
            """把三条路径的原始报错翻译成一句能指导操作的话。

            取"最可行动"的那条：Permission denied 一律归为"需要 root"，
            不要被后面 run-as 的 "not debuggable" 覆盖掉。
            """
            # 提示要短：toast 只给结论，原始报错留在虫师日志里（logger.info 已记录）
            joined = " | ".join(r for r in reasons if r)
            if "Permission denied" in joined or "not debuggable" in joined:
                return "需要 root 或 debug 包"
            if "No such file" in joined:
                return "进程已退出"
            tail = reasons[-1] if reasons and reasons[-1] else "未知原因"
            return tail[:40]

        def worker():
            try:
                r = subprocess.run(
                    [adb, "-s", serial, "shell", "pidof", package],
                    capture_output=True, text=True, timeout=6,
                    creationflags=creationflags, encoding="utf-8", errors="replace")
                pids = (r.stdout or "").split()
                if not pids:
                    self.smaps_finished.emit("error", f"应用「{package}」未在运行")
                    return
                pid = pids[0]

                # 三条路径依次尝试：直读 → root → run-as（可调试包）。
                # /proc/<pid>/smaps 在 Android 上默认只有 root 能读，shell 读别的应用必被拒。
                attempts = (
                    ("直读", [adb, "-s", serial, "exec-out", "cat", f"/proc/{pid}/smaps"]),
                    ("root", [adb, "-s", serial, "shell", f"su -c 'cat /proc/{pid}/smaps'"]),
                    ("run-as", [adb, "-s", serial, "shell",
                                f"run-as {package} cat /proc/{pid}/smaps"]),
                )
                reasons = []
                for label, cmd in attempts:
                    ok, info = _read_once(cmd)
                    if ok:
                        logger.info("[Perf] smaps 已保存（%s）：%s（%.1f MB）",
                                    label, path, info / 1048576.0)
                        self.smaps_finished.emit("success", path)
                        return
                    reasons.append(info)
                    logger.info("[Perf] smaps %s 失败：%s", label, info)
                try:
                    os.remove(path)      # 失败时不要留个只有报错的空文件
                except OSError:
                    pass
                self.smaps_finished.emit("error", _human_reason(reasons))
            except Exception as e:
                logger.warning("[Perf] smaps 抓取失败：%s", e)
                try:
                    os.remove(path)
                except OSError:
                    pass
                self.smaps_finished.emit("error", str(e))
            finally:
                self._smaps_dumping = False

        threading.Thread(target=worker, daemon=True).start()

    def _on_smaps_finished(self, status: str, message: str):
        if status == "success":
            self._add_event("smaps", os.path.basename(message))
            size = os.path.getsize(message) / 1048576.0 if os.path.exists(message) else 0.0
            show_toast(self.view, f"内存明细已保存（{size:.1f} MB）", duration=3000)
        else:
            show_toast(self.view, f"内存明细抓取失败：{message}", duration=4000)

    # ------------------------------------------------------------------
    # 录屏：screenrecord 定时录一段 → 拉回本地
    # ------------------------------------------------------------------
    def _on_record_toggle(self, checked: bool):
        if not checked:
            self._stop_record_loop()
        elif self.perf_worker is not None:
            # 监控中勾上：立即生效；空闲时勾上则等下次开始监控
            self._start_record_loop()

    def _on_record_cfg_changed(self):
        """间隔/时长改了：循环在跑就按新间隔重排"""
        if self._record_loop_timer is not None and self._record_loop_timer.isActive():
            self._record_loop_timer.start(int(self.view.get_record_interval() * 1000))

    def _start_record_loop(self):
        if not self.view.record_config_ok():
            show_toast(self.view, "间隔需大于单段时长")
            self.view.record_enable_check.blockSignals(True)
            self.view.record_enable_check.setChecked(False)
            self.view.record_enable_check.blockSignals(False)
            return
        if self._record_loop_timer is None:
            self._record_loop_timer = QTimer(self)
            self._record_loop_timer.timeout.connect(self._record_now)
        # 不立即录：等一个完整间隔
        self._record_loop_timer.start(int(self.view.get_record_interval() * 1000))

    def _stop_record_loop(self):
        if self._record_loop_timer is not None:
            self._record_loop_timer.stop()

    def _record_now(self):
        """录一段屏幕视频（后台线程，防重入）。

        screenrecord 是**阻塞**命令（跑满时长才返回），所以整段放在后台线程里，
        录完再 adb pull 回本地并删掉设备上的临时文件。
        """
        if self._recording:
            return
        if not self.device_service or not self.device_service.serial:
            show_toast(self.view, "设备未连接")
            return
        self._recording = True

        import subprocess
        import sys as _sys
        import threading
        from utils.adb_path import get_adb_path
        from utils.settings import Settings as _Settings

        serial = self.device_service.serial
        adb = get_adb_path()
        duration = max(5, min(180, int(self.view.get_record_duration())))
        rec_dir = self._session_artifact_dir("screencap")
        name = time.strftime("record_%Y%m%d_%H%M%S.mp4")
        remote = f"/data/local/tmp/{name}"
        local = os.path.join(rec_dir, name)

        def _human(err: str) -> str:
            low = (err or "").lower()
            if "unable to configure" in low:
                return "设备不支持录屏（编解码器不可用）"
            if "inaccessible" in low or "not found" in low:
                return "设备没有 screenrecord"
            if "permission denied" in low:
                return "没有录屏权限"
            return (err or "").strip()[:60] or "未知原因"

        def worker():
            creationflags = subprocess.CREATE_NO_WINDOW if _sys.platform == 'win32' else 0
            try:
                r = subprocess.run(
                    [adb, "-s", serial, "shell",
                     f"screenrecord --time-limit {duration} --bit-rate 8000000 {remote}"],
                    capture_output=True, text=True, timeout=duration + 40,
                    creationflags=creationflags, encoding="utf-8", errors="replace")
                r2 = subprocess.run(
                    [adb, "-s", serial, "pull", remote, local],
                    capture_output=True, text=True, timeout=180,
                    creationflags=creationflags, encoding="utf-8", errors="replace")
                subprocess.run([adb, "-s", serial, "shell", "rm", "-f", remote],
                               capture_output=True, timeout=20,
                               creationflags=creationflags)
                size = os.path.getsize(local) if os.path.exists(local) else 0
                if r.returncode != 0 or size < 4096:
                    try:
                        os.remove(local)
                    except OSError:
                        pass
                    err = (r.stderr or "") + (r2.stderr or "")
                    self.record_finished.emit("error", _human(err))
                    return
                logger.info("[Perf] 录屏已保存：%s（%.1f MB）", local, size / 1048576.0)
                self.record_finished.emit("success", local)
            except Exception as e:
                logger.warning("[Perf] 录屏失败：%s", e)
                try:
                    os.remove(local)
                except OSError:
                    pass
                self.record_finished.emit("error", str(e)[:60])
            finally:
                self._recording = False

        threading.Thread(target=worker, daemon=True).start()

    def _on_record_finished(self, status: str, message: str):
        if status == "success":
            self._add_event("record", os.path.basename(message))
            size = os.path.getsize(message) / 1048576.0 if os.path.exists(message) else 0.0
            show_toast(self.view, f"录屏已保存（{size:.1f} MB）", duration=3000)
        else:
            show_toast(self.view, f"录屏失败：{message}", duration=4000)

    def _on_screenshot_toggle(self, checked: bool):
        """截图自动循环勾选状态变化"""
        if not checked:
            self._stop_screenshot_loop()
        elif self.perf_worker is not None:
            self._start_screenshot_loop()

    def _on_screenshot_interval_changed(self):
        """间隔变更：若循环正在跑，重启定时器以套用新间隔"""
        if self._screenshot_loop_timer is not None:
            self._screenshot_loop_timer.setInterval(
                int(self.view.get_screenshot_interval() * 1000))

    def _start_screenshot_loop(self):
        if not self.view.get_selected_package():
            show_toast(self.view, "请先在左上角选择要监控的应用")
            self.view.screenshot_enable_check.blockSignals(True)
            self.view.screenshot_enable_check.setChecked(False)
            self.view.screenshot_enable_check.blockSignals(False)
            return
        if self._screenshot_loop_timer is None:
            self._screenshot_loop_timer = QTimer(self)
            self._screenshot_loop_timer.timeout.connect(self._take_screenshot_now)
        # 不立即执行：等一个完整间隔后触发第一次截图
        self._screenshot_loop_timer.start(
            int(self.view.get_screenshot_interval() * 1000))

    def _stop_screenshot_loop(self):
        if self._screenshot_loop_timer is not None:
            self._screenshot_loop_timer.stop()

    def _take_screenshot_now(self):
        """截一次设备屏幕（后台线程，防重入）"""
        if self._screenshot_busy:
            return
        device = getattr(self.device_service, 'device', None) if self.device_service else None
        if not device:
            show_toast(self.view, "设备未连接")
            return
        self._screenshot_busy = True

        serial = self.device_service.serial or "device"
        serial_safe = serial.replace(':', '_')
        from utils.settings import Settings as _Settings
        screenshot_dir = self._session_artifact_dir("screenshots")

        import subprocess
        import sys as _sys
        import threading
        from utils.adb_path import get_adb_path

        def worker():
            creationflags = subprocess.CREATE_NO_WINDOW if _sys.platform == 'win32' else 0

            def grab(path):
                # exec-out screencap -p：PNG 字节流直写本地文件，
                # 不经过设备端临时文件（对比堆转储少一步 pull/rm）。
                # 'wb' 会截断上一次重试留下的半截数据
                with open(path, 'wb') as f:
                    subprocess.run(
                        [get_adb_path(), "-s", serial, "exec-out", "screencap", "-p"],
                        stdout=f, check=True, timeout=20,
                        creationflags=creationflags,
                    )

            try:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                local_path = os.path.join(
                    screenshot_dir, f"screenshot_{timestamp}.png")
                # 车机忙时（例如同时在 pull 大日志）首次可能超时：隔 0.5s
                # 重试一次，仍失败才报错。**只对超时重试** —— adb 不存在、
                # 退出码非 0 这类错误重试也没用。
                try:
                    grab(local_path)
                except subprocess.TimeoutExpired:
                    time.sleep(0.5)
                    grab(local_path)
                if os.path.getsize(local_path) == 0:
                    os.remove(local_path)
                    raise RuntimeError("截图数据为空")
                self.screenshot_finished.emit("success", local_path)
            except Exception as e:
                self.screenshot_finished.emit("error", str(e))
            finally:
                self._screenshot_busy = False

        threading.Thread(target=worker, daemon=True).start()

    def _on_screenshot_finished(self, status: str, message: str):
        """截图结果回投到主线程：状态文字常驻卡片，失败另弹 toast。

        循环截图间隔可能很短，成功时不弹 toast，避免打扰。

        失败时**不把原始异常塞进面板**：`subprocess.TimeoutExpired` 的 str()
        带着完整的 adb 命令行，既看不懂又会把整行撑爆、挤乱布局。
        面板只留一句短状态，完整原因写进「虫师日志」。
        """
        if status == "success":
            self._add_event("screenshot", os.path.basename(message))
            self.view.set_screenshot_status(f"最近：{os.path.basename(message)}")
        else:
            self.view.set_screenshot_status("最近：截图失败")
            if self.logs_view:
                self.logs_view.add_log(f"[性能] 截图失败：{message}", "error")
            show_toast(self.view, "截图失败", duration=4000)

    def _on_packet_toggle(self, capturing: bool):
        """抓包开始/停止（卡片按钮的手动开关）"""
        if capturing:
            self._start_capture()
        else:
            self._stop_capture()

    def _auto_start_capture_if_enabled(self):
        """「开始监控」时按「抓包」勾选状态决定是否自动开始抓包。

        单独成方法：这半边的联动是随监控启动走的，抽出来才好单测
        （见 tools 里的自测思路：不启动完整监控会话也能验证"勾了就抓、没勾就不抓"）。
        """
        if not self.view.packet_visible_check.isChecked():
            return
        if not self.view.packet_available():
            # 前置探测已判定抓不了（未 root 等）：不打扰，只留一条状态
            self.view.set_packet_status("未开始：设备不支持抓包")
            return
        self._start_capture(auto=True)

    def _on_capture_config(self):
        """打开「抓包参数」对话框；确定后落盘并刷新按钮提示"""
        from views.perf_dialogs import CaptureSettingsDialog
        from services.packet_capture import load_capture_config, save_capture_config
        dialog = CaptureSettingsDialog(load_capture_config(), self.view)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        save_capture_config(dialog.get_config())
        self.view.refresh_packet_tooltip()
        show_toast(self.view, "抓包参数已保存")

    # ------------------------------------------------------------------
    # 抓包防呆：开抓之前先确认「这台设备能不能抓」
    # ------------------------------------------------------------------
    def _probe_capture_capability(self):
        """只读探测抓包能力，结果用来把「开始抓包」按钮置灰（而不是点开才报错）。

        每台设备只探一次（按序列号缓存）；不 push、不部署，纯查询。
        """
        if self._capture_probing or self._capture_worker is not None:
            return
        if not self.device_service or not self.device_service.serial:
            self.view.set_packet_available(False, "设备未连接")
            return
        serial = self.device_service.serial
        if serial == self._capture_probed_serial and self._capture_ready is not None:
            # 已有结论：直接把状态刷回界面（设备重连后按钮可能被重新启用）
            self.view.set_packet_available(self._capture_ready, self._capture_ready_reason)
            return

        self._capture_probing = True
        import threading
        from utils.adb_path import get_adb_path
        adb = get_adb_path()
        local_tcpdump = os.path.join(os.path.dirname(adb), "tcpdump")
        if not os.path.exists(local_tcpdump):
            local_tcpdump = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "tcpdump")

        def worker():
            from services.packet_capture import probe_capture_ready
            try:
                ok, reason = probe_capture_ready(serial, local_tcpdump)
            except Exception as e:
                ok, reason = False, str(e)
            self._capture_ready = ok
            self._capture_ready_reason = reason
            self._capture_probed_serial = serial
            self._capture_probing = False
            # 回主线程改界面
            self.capture_ready_checked.emit(ok, reason)

        threading.Thread(target=worker, daemon=True).start()

    def _on_capture_ready_checked(self, ok: bool, reason: str):
        self.view.set_packet_available(ok, reason)

    # 本机剩余空间低于该值就不开抓包（pcap 写在本机磁盘上，不是设备）
    CAPTURE_MIN_FREE_BYTES = 500 * 1024 * 1024

    def _check_disk_space_for_capture(self) -> bool:
        """抓包前检查本机剩余空间，不够就别开（避免写满盘）。"""
        import shutil
        from utils.settings import Settings as _Settings
        try:
            free = shutil.disk_usage(_Settings.get_output_dir()).free
        except Exception:
            return True          # 查不到就不拦，别把正常路径卡死
        if free < self.CAPTURE_MIN_FREE_BYTES:
            show_toast(self.view,
                       f"磁盘剩余 {free / 1048576:.0f} MB，不足 500 MB，已取消抓包",
                       duration=5000)
            return False
        return True

    def _start_capture(self, auto: bool = False):
        """auto=True 表示随「开始监控」自动拉起（失败时用轻提示，不弹模态打断采集）"""
        self._capture_auto_started = auto
        self._capture_stop_requested = False
        if not self.device_service or not self.device_service.device:
            show_toast(self.view, "设备未连接")
            self.view.set_packet_capturing(False)
            return
        if not self._check_disk_space_for_capture():
            self.view.set_packet_capturing(False)
            self.view.set_packet_status("已取消：磁盘空间不足")
            return
        from services.packet_capture import TcpdumpDeployThread

        serial = self.device_service.serial

        # 先部署 tcpdump（含 root 检测），就绪后再启动抓包
        from utils.adb_path import get_adb_path
        adb = get_adb_path()
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        tcpdump_path = os.path.join(os.path.dirname(adb), "tcpdump")
        if not os.path.exists(tcpdump_path):
            tcpdump_path = os.path.join(project_root, "tools", "tcpdump")

        self._deploy_thread = TcpdumpDeployThread(serial, tcpdump_path)
        self._deploy_thread.finished_with.connect(self._on_capture_deployed)
        self._deploy_thread.start()
        self.view.set_packet_status("准备 tcpdump…")

    def _on_capture_deployed(self, ok: bool, message: str):
        from services.packet_capture import CaptureWorker, load_capture_config
        from utils.settings import Settings as _Settings
        auto = getattr(self, '_capture_auto_started', False)
        if getattr(self, '_capture_stop_requested', False):
            # 部署期间已经点过「停止」（或监控已结束）：不要拉起抓包
            self._capture_stop_requested = False
            self.view.set_packet_capturing(False)
            self.view.set_packet_status("已停止")
            return
        if not ok:
            self.view.set_packet_capturing(False)
            self.view.set_packet_status(f"无法抓包：{message}")
            if auto:
                # 自动拉起失败：状态栏已经写清楚了，不再弹模态打断刚开始的采集
                show_toast(self.view, f"抓包未开始：{message.splitlines()[0]}", duration=5000)
            else:
                WarningDialog.show_warning(self.view, "抓包不可用", message)
            return
        if self._capture_worker and self._capture_worker.isRunning():
            return
        deploy = getattr(self, '_deploy_thread', None)
        cfg = load_capture_config()
        cap_name = self.current_session.name if self.current_session else "手动"
        self._capture_worker = CaptureWorker(
            self.device_service.serial, _Settings.get_output_dir(), cfg['filter'],
            device_path=getattr(deploy, 'device_path', "/data/local/tmp/tcpdump"),
            interface=cfg['interface'], snaplen=cfg['snaplen'],
            max_seconds=cfg['max_seconds'], max_bytes=cfg['max_bytes'],
            subdir=os.path.join("perf", cap_name, "capture"))
        self._capture_worker.finished_signal.connect(self._on_capture_finished)
        self._capture_worker.error_signal.connect(
            lambda m: self.view.set_packet_status(f"❌ {m}"))
        self._capture_worker.start()
        # 自动拉起时按钮本来是未选中的，这里统一回写，保证按钮/状态与实际一致
        self.view.set_packet_capturing(True)

    def _on_capture_finished(self, success: bool, result: str):
        from services.packet_capture import capture_limit_text
        worker = self._capture_worker
        self._capture_worker = None
        reason = getattr(worker, 'stop_reason', '') if worker else ''
        # 自动停止时把原因讲清楚，避免用户以为是自己点停的 / 是异常
        limit_txt = capture_limit_text(reason)
        note = f'（{limit_txt}，已自动停止）' if limit_txt else ''
        if success:
            segs = getattr(worker, "segments", None) or []
            seg_note = f"，共 {len(segs)} 段" if len(segs) > 1 else ""
            self._add_event("capture", os.path.basename(result) + seg_note)
            self.view.set_packet_capturing(False)
            self.view.set_packet_status(f"完成：{os.path.basename(result)}{seg_note}{note}")
            if self.logs_view:
                self.logs_view.add_log(f"[抓包] 完成：{result}{note}", "success")
            if limit_txt:
                show_toast(self.view, f"抓包{limit_txt}，已自动停止并保存", duration=4000)
        else:
            self.view.set_packet_capturing(False)
            self.view.set_packet_status(f"❌ {result}")

    def _stop_capture(self):
        """停止抓包（用户手动点击 / 随监控结束自动收尾）。

        在部署 tcpdump 阶段（worker 尚未创建）或已结束时，只把按钮与状态复位；
        同时立一个闸门，避免部署线程稍后完成又把抓包拉起来。
        """
        self._capture_stop_requested = True
        if self._capture_worker and self._capture_worker.isRunning():
            self._capture_worker.stop()
            self.view.set_packet_status("正在停止…")
        else:
            self.view.set_packet_capturing(False)

    # ------------------------------------------------------------------
    def _on_scenario_progress(self, message, log_type):
        if self.logs_view:
            self.logs_view.add_log(message, log_type)

    def _on_scenario_finished(self):
        """场景化执行完成，停止采集"""
        if self.view.get_state() == self.view.STATE_SCENARIO:
            self._on_stop()
            show_toast(self.view, "场景化测试完成")

    # ------------------------------------------------------------------
    # 启动测试
    # ------------------------------------------------------------------
# ---------- 新增：打开基线管理 ----------
    def open_baseline_manager(self):
        from views.perf_dialogs import PerfBaselineDialog
        dlg = PerfBaselineDialog(self.model, self.view)
        dlg.exec()


    # ------------------------------------------------------------------
    # 导入导出
    # ------------------------------------------------------------------
    def _device_export_dir(self) -> str:
        """导出落盘目录：设置输出目录 / 设备序列号（冒号转下划线）。

        性能报告/CSV/JSON 都按设备归档，避免多台设备的数据混在一起。
        设备序列号取不到（如无设备历史会话）时回退到输出目录根下。
        """
        from utils.settings import Settings
        base = Settings.get_output_dir()
        serial = None
        try:
            if self.device_service is not None:
                serial = self.device_service.serial
        except Exception:
            serial = None
        if serial:
            base = os.path.join(base, serial.replace(':', '_'))
        os.makedirs(base, exist_ok=True)
        return base

    def _session_artifact_dir(self, subdir: str) -> str:
        """产物归档目录：<输出目录>/<序列号>/perf/<会话>/<subdir>。

        堆转储 / 抓包 / 截图 / 录屏 / smaps 统一落这里，报告与「诊断产物」才能按会话回溯。
        没有正在进行的会话（脱离监控点「立即 XX」）时归到「手动」目录。
        """
        name = self.current_session.name if self.current_session else "手动"
        path = os.path.join(self._device_export_dir(), "perf", name, subdir)
        os.makedirs(path, exist_ok=True)
        return path

    # 诊断事件类型（与报告 EVENT_META 保持一致）
    EVENT_TYPES = ("heap", "capture", "screenshot", "record", "smaps")

    def _add_event(self, etype: str, file: str, note: str = ""):
        """给当前会话记一条诊断事件（产物落地了，报告「诊断产物」和曲线竖线要用）。

        脱离监控手动操作时 current_session 为 None，不记（归「手动」目录的产物不属任何会话）。
        """
        if self.current_session is None or etype not in self.EVENT_TYPES:
            return
        self.current_session.events.append({
            "ts": time.time(), "type": etype, "file": file, "note": note,
        })

    def _on_export_csv(self):
        if not self.current_session or not self.current_session.samples:
            # 尝试用最后一个会话
            if self.model.sessions:
                session = self.model.sessions[-1]
            else:
                show_toast(self.view, "无数据可导出")
                return
        else:
            session = self.current_session

        from PyQt6.QtWidgets import QFileDialog
        from utils.settings import Settings
        path, _ = QFileDialog.getSaveFileName(
            self.view, "导出 CSV",
            os.path.join(self._device_export_dir(), f"perf_{session.name}.csv"),
            "CSV Files (*.csv)"
        )
        if not path:
            return

        try:
            with open(path, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'timestamp', 'cpu_percent', 'mem_pss_mb',
                    'mem_java_mb', 'mem_native_mb', 'mem_graphics_mb',
                    'mem_stack_mb', 'mem_code_mb', 'mem_others_mb',
                    'fps', 'rx_bytes', 'tx_bytes'
                ])
                for s in session.samples:
                    bd = getattr(s, 'mem_breakdown', {}) or {}
                    # 没采到 CPU 的样本写空单元格，不写 0 —— 0 会被读成"真的不占 CPU"
                    cpu_txt = ("" if any(e.startswith('cpu:')
                                         for e in getattr(s, 'errors', ()))
                               else f"{s.cpu_percent:.2f}")
                    writer.writerow([
                        f"{s.timestamp:.3f}", cpu_txt,
                        f"{s.mem_pss_mb:.2f}",
                        f"{bd.get('Java', 0.0):.2f}",
                        f"{bd.get('Native', 0.0):.2f}",
                        f"{bd.get('Graphics', 0.0):.2f}",
                        f"{bd.get('Stack', 0.0):.2f}",
                        f"{bd.get('Code', 0.0):.2f}",
                        f"{bd.get('Others', 0.0):.2f}",
                        s.fps,
                        s.rx_bytes, s.tx_bytes
                    ])
            # 诊断产物清单：与主 CSV 并列写一个 <名称>_诊断产物.csv，方便一并归档
            if getattr(session, 'events', None):
                manifest = os.path.splitext(path)[0] + "_诊断产物.csv"
                _labels = {"heap": "堆转储", "capture": "抓包", "screenshot": "截图",
                           "record": "录屏", "smaps": "内存明细"}
                t0 = session.samples[0].timestamp if session.samples else 0.0
                with open(manifest, 'w', newline='', encoding='utf-8-sig') as mf:
                    w2 = csv.writer(mf)
                    w2.writerow(['相对秒', '类型', '文件', '备注'])
                    for e in sorted(session.events, key=lambda x: x.get('ts', 0)):
                        rel = (e.get('ts', 0) - t0) if t0 else 0.0
                        w2.writerow([f"{rel:.1f}",
                                     _labels.get(e.get('type'), e.get('type')),
                                     e.get('file', ''), e.get('note', '')])
            show_toast(self.view, "导出成功")
        except Exception as e:
            ErrorDialog.show_error(self.view, "导出失败", str(e))

    def _on_export_json(self):
        if not self.current_session and not self.model.sessions:
            show_toast(self.view, "无数据可导出")
            return
        session = self.current_session or self.model.sessions[-1]

        from PyQt6.QtWidgets import QFileDialog
        import json
        path, _ = QFileDialog.getSaveFileName(
            self.view, "导出 JSON",
            os.path.join(self._device_export_dir(), f"perf_{session.name}.json"),
            "JSON Files (*.json)"
        )
        if not path:
            return

        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(session.to_dict(), f, ensure_ascii=False, indent=2)
            show_toast(self.view, "导出成功")
        except Exception as e:
            ErrorDialog.show_error(self.view, "导出失败", str(e))

    def _on_generate_report(self):
        if not self.current_session and not self.model.sessions:
            show_toast(self.view, "无数据可生成报告")
            return
        session = self.current_session or self.model.sessions[-1]

        from utils.perf_report import PerfReportGenerator
        from utils.settings import Settings
        from PyQt6.QtWidgets import QFileDialog
        import os

        # 默认存到「设置 → 输出目录 / 设备号」，与其他报告/导出一致、按设备归档
        default_path = os.path.join(
            self._device_export_dir(),
            f"perf_report_{session.name}.html"
        )
        path, _ = QFileDialog.getSaveFileName(
            self.view, "保存性能报告", default_path, "HTML Files (*.html)"
        )
        if not path:
            return

        try:
            baseline = self.model.find_baseline_for_package(
                session.app_package, session.metrics)
            PerfReportGenerator.generate(session, path, baseline=baseline)
            show_toast(self.view, "报告已生成")
            # 可选：打开所在文件夹
            import subprocess, sys
            folder = os.path.dirname(path)
            if sys.platform == "win32":
                os.startfile(folder)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])
        except Exception as e:
            ErrorDialog.show_error(self.view, "报告生成失败", str(e))

    def _auto_compare_baseline(self, session):
        """采集结束自动比对同应用基线，判定性能劣化。

        无基线或无法对比时静默跳过；有劣化时弹窗提示，无劣化不打扰用户。
        对比结果只走弹窗，不写入自动化执行日志区。
        """
        baseline = self.model.find_baseline_for_package(
            session.app_package, session.metrics)
        if baseline is None:
            return
        stats = session.get_stats()
        if not stats:
            return
        rows = compare_stats(baseline.metrics, stats)
        if not rows:
            return

        def _fmt(r, digits=2):
            return (f"{r['label']}：基线 {r['base']:.{digits}f} → "
                    f"本次 {r['cur']:.{digits}f}（{r['change_pct']:+.1f}%）")

        # 先查口径：口径不同 / 旧基线没记口径时，数值不可直接比，也不判定劣化
        note = self._scope_conflicts(baseline, session)
        if note:
            logger.warning("[Perf] 基线与本次口径不一致：%s", note)
            WarningDialog.show_warning(
                self.view,
                "基线与本次口径不一致",
                f"对比基线：{baseline.name}\n\n{note}",
                "完整数据（仅供查看，不判定改善 / 劣化）：\n"
                + "\n".join(f"• {_fmt(r)}" for r in rows),
            )
            return

        worse = [r for r in rows if r['status'] == 'worse']

        if worse:
            # 弹窗只放前 5 条，避免几十行把窗口撑爆；完整清单在下方滚动区
            preview = worse[:5]
            detail = "\n".join(f"• {_fmt(r)}" for r in preview)
            more = len(worse) - len(preview)
            if more > 0:
                detail += f"\n…其余 {more} 项明显劣化见下方完整对比"
            full_detail = (
                f"完整对比（共 {len(rows)} 项，其中明显劣化 {len(worse)} 项）：\n"
                + "\n".join(f"• {_fmt(r)}" for r in rows)
            )
            WarningDialog.show_warning(
                self.view,
                "性能劣化告警",
                f"对比基线：{baseline.name}\n\n"
                f"检测到 {len(worse)} 项指标明显劣化（共对比 {len(rows)} 项）：\n\n{detail}",
                full_detail
            )

    @staticmethod
    def _scope_conflicts(baseline, session):
        """对比前查口径（实现见 models.perf_model.scope_conflict_note）。"""
        return scope_conflict_note(getattr(baseline, "scopes", None),
                                   getattr(session, "scopes", None))

    def _on_save_baseline(self):
        if not self.current_session and not self.model.sessions:
            show_toast(self.view, "无数据可保存")
            return
        session = self.current_session or self.model.sessions[-1]

        from utils.dialogs import InputDialog
        name, ok = InputDialog.get_text(
            self.view,
            title="保存基线",
            label="输入基线名称:",
            placeholder="例如：主图态_8核_华为"
        )
        if not ok or not name:
            return
        name = name.strip()
        if self.model.get_baseline(name):
            show_toast(self.view, f"基线 '{name}' 已存在")
            return

        baseline = PerfBaseline(
            name=name,
            session_id=session.id,
            app_package=session.app_package,
            device_serial=session.device_serial,
            metrics=session.get_stats(),
            created_at=datetime.now().isoformat(),
            scopes=dict(getattr(session, "scopes", {}) or {}),
        )
        self.model.add_baseline(baseline)
        show_toast(self.view, f"基线 '{name}' 已保存")

    def _on_threshold_config(self):
        from views.perf_dialogs import PerfThresholdDialog
        # 带上最近一次会话的统计，对话框里显示「本次会话峰值」作为设定参考
        ref = {}
        if self.model.sessions:
            try:
                ref = self.model.sessions[-1].get_stats()
            except Exception:
                ref = {}
        dlg = PerfThresholdDialog(self.model.threshold, self.view, reference=ref)
        if dlg.exec() == dlg.DialogCode.Accepted:
            self.model.set_threshold(self.model.threshold)
            show_toast(self.view, "阈值已保存")

    # ------------------------------------------------------------------
    def apply_theme(self, theme_mode):
        """由主窗口调用"""
        import os as _os
        from utils.settings import Settings
        try:
            wp = Settings.get_wallpaper_path()
            has_wp = bool(wp and _os.path.exists(wp))
        except Exception:
            has_wp = False
        self.view.apply_theme(theme_mode, has_wp)