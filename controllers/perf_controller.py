# controllers/perf_controller.py
"""性能检测控制器"""
import csv
import logging
import os
import time
from datetime import datetime

from PyQt6.QtCore import QObject, QThread, pyqtSignal, QTimer, Qt

from models.perf_model import (
    PerfModel, PerfSession, PerfThreshold, PerfBaseline, compare_stats
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
                 duration_minutes=None, parent=None):
        super().__init__(parent)
        self.device_service = device_service
        self.package = package
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
                self._service.collect_sample(self.package, self.metrics)
            except Exception:
                pass
        return True

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

            try:
                sample = self._service.collect_sample(self.package, self.metrics)
                self.sample_ready.emit(sample)
            except Exception as e:
                logger.exception("采样失败")
                self.error_occurred.emit(str(e))

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
        # 截图自动循环状态
        self._screenshot_loop_timer = None
        self._screenshot_busy = False
        # 抓包状态
        self._capture_worker = None

        # 采样统计刷新定时器
        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(1000)
        self._stats_timer.timeout.connect(self._refresh_stats)

        # 堆转储 / 截图结果回主线程处理（toast / 状态文字）
        self.hprof_finished.connect(self._on_hprof_finished)
        self.screenshot_finished.connect(self._on_screenshot_finished)

        self._connect_signals()
        self._check_compat_on_startup()

    # ------------------------------------------------------------------
    def _connect_signals(self):
        self.view.start_requested.connect(self._on_start)
        self.view.pause_requested.connect(self._on_pause)
        self.view.resume_requested.connect(self._on_resume)
        self.view.stop_requested.connect(self._on_stop)
        self.view.app_list_refresh_requested.connect(self._on_refresh_apps)
        self.view.export_csv_requested.connect(self._on_export_csv)
        self.view.export_json_requested.connect(self._on_export_json)
        self.view.launch_test_requested.connect(self._on_launch_test)
        self.view.baseline_requested.connect(self._on_save_baseline)
        self.view.baseline_manager_requested.connect(self.open_baseline_manager)
        self.view.clear_requested.connect(self._on_clear_requested)
        self.view.threshold_config_requested.connect(self._on_threshold_config)
        self.view.report_requested.connect(self._on_generate_report)
        # 堆转储 / 抓包 / 截图（性能检测页工具卡片）
        self.view.hprof_toggle_requested.connect(self._on_hprof_toggle)
        self.view.hprof_once_requested.connect(self._on_hprof_once)
        self.view.hprof_interval_changed.connect(self._on_hprof_interval_changed)
        self.view.packet_toggle_requested.connect(self._on_packet_toggle)
        self.view.screenshot_toggle_requested.connect(self._on_screenshot_toggle)
        self.view.screenshot_once_requested.connect(self._take_screenshot_now)
        self.view.screenshot_interval_changed.connect(
            self._on_screenshot_interval_changed)

    # ------------------------------------------------------------------
    def _check_compat_on_startup(self):
        """设备由主窗口统一维护，这里只做轮询，等设备就绪再拉应用"""
        self._last_device_serial = None
        self._device_watch_timer = QTimer(self)
        self._device_watch_timer.setInterval(2000)
        self._device_watch_timer.timeout.connect(self._maybe_refresh_apps)
        self._device_watch_timer.start()

    def _maybe_refresh_apps(self):
        """当主窗口连接/切换设备时，自动刷新应用列表"""
        if not self.device_service.device:
            if self._last_device_serial is not None:
                self._last_device_serial = None
                self.view.update_app_list([])  # 清空
            return
        current = self.device_service.serial
        if current and current != self._last_device_serial:
            self._last_device_serial = current
            self._on_refresh_apps()

    def _on_refresh_apps(self):
        """刷新应用列表 + 更新指标可用性"""
        if not self.device_service.device:
            return
        try:
            compat = AndroidCompat(self.device_service.device)
            packages = compat.list_all_packages()
            self.view.update_app_list(packages)

            # 更新指标可用性
            available = {}
            reasons = {}
            for key in ('cpu', 'mem', 'fps', 'traffic'):
                ok = compat.is_metric_available(key)
                available[key] = ok
                if not ok:
                    reasons[key] = compat.get_unavailable_reason(key)
            self.view.update_metric_availability(available, reasons)
        except Exception as e:
            logger.exception("刷新应用列表失败")

    # ------------------------------------------------------------------
    # 开始/暂停/继续/停止
    # ------------------------------------------------------------------
    def _on_start(self, payload: dict):
        package = payload['package']
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

        # 检查应用是否运行
        try:
            compat = AndroidCompat(self.device_service.device)
            if not compat.is_package_running(package):
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
            start_time=datetime.now().isoformat(),
            sample_interval=interval,
            metrics=metrics,
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

        # 截图自动循环：同堆转储口径
        if self.view.screenshot_enable_check.isChecked():
            self._start_screenshot_loop()

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

    def _on_pause(self):
        if self.perf_worker:
            self.perf_worker.pause()
        self.view.set_state(self.view.STATE_PAUSED)
        self._stats_timer.stop()

    def _on_resume(self):
        if self.perf_worker:
            self.perf_worker.resume()
        self.view.set_state(self.view.STATE_RUNNING)
        self._stats_timer.start()

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
        self._stop_screenshot_loop()

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

    def _on_clear_requested(self):
        """清空当前会话已采集的样本（视图已清卡片，控制器负责清数据）"""
        if self.current_session is not None:
            self.current_session.samples.clear()
            self.current_session.alerts.clear()
        self._alert_cache = {k: False for k in (self.current_session.metrics if self.current_session else [])}
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
            elif not triggered and self._alert_cache.get(key, False):
                # 恢复正常
                self._alert_cache[key] = False
                self.view.set_alert(key, False)

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

                output_dir = _Settings.get_output_dir()
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                safe_serial = serial.replace(':', '_')
                device_dir = os.path.join(output_dir, safe_serial)
                os.makedirs(device_dir, exist_ok=True)
                remote_path = f"/data/local/tmp/heap_{timestamp}.hprof"
                local_path = os.path.join(device_dir, f"heap_{timestamp}.hprof")

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
            show_toast(self.view, "堆转储完成")
        else:
            show_toast(self.view, "堆转储失败", duration=4000)

    # ------------------------------------------------------------------
    # 截图卡片（自动循环 + 立即截图）
    # ------------------------------------------------------------------
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
        screenshot_dir = os.path.join(
            _Settings.get_output_dir(), serial_safe, "screenshots")
        os.makedirs(screenshot_dir, exist_ok=True)

        import subprocess
        import sys as _sys
        import threading
        from utils.adb_path import get_adb_path

        def worker():
            creationflags = subprocess.CREATE_NO_WINDOW if _sys.platform == 'win32' else 0
            try:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                local_path = os.path.join(
                    screenshot_dir, f"screenshot_{timestamp}.png")
                # exec-out screencap -p：PNG 字节流直写本地文件，
                # 不经过设备端临时文件（对比堆转储少一步 pull/rm）
                with open(local_path, 'wb') as f:
                    subprocess.run(
                        [get_adb_path(), "-s", serial, "exec-out", "screencap", "-p"],
                        stdout=f, check=True, timeout=20,
                        creationflags=creationflags,
                    )
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
        """
        if status == "success":
            self.view.set_screenshot_status(f"最近：{os.path.basename(message)}")
        else:
            self.view.set_screenshot_status(f"❌ {message}", error=True)
            show_toast(self.view, "截图失败", duration=4000)

    def _on_packet_toggle(self, capturing: bool):
        """抓包开始/停止"""
        if capturing:
            self._start_capture()
        else:
            self._stop_capture()

    def _start_capture(self):
        if not self.device_service or not self.device_service.device:
            show_toast(self.view, "设备未连接")
            self.view.set_packet_capturing(False)
            return
        from views.adb_dialogs.packet_capture_dialog import (
            TcpdumpDeployThread, CaptureWorker)
        from utils.settings import Settings as _Settings

        serial = self.device_service.serial
        output_dir = _Settings.get_output_dir()

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
        from views.adb_dialogs.packet_capture_dialog import CaptureWorker
        from utils.settings import Settings as _Settings
        if not ok:
            self.view.set_packet_capturing(False)
            self.view.set_packet_status(f"无法抓包：{message}")
            WarningDialog.show_warning(self.view, "抓包不可用", message)
            return
        if self._capture_worker and self._capture_worker.isRunning():
            return
        self._capture_worker = CaptureWorker(
            self.device_service.serial, _Settings.get_output_dir(), "")
        self._capture_worker.finished_signal.connect(self._on_capture_finished)
        self._capture_worker.error_signal.connect(
            lambda m: self.view.set_packet_status(f"❌ {m}"))
        self._capture_worker.start()
        self.view.set_packet_status("抓包中…")

    def _on_capture_finished(self, success: bool, result: str):
        if success:
            self.view.set_packet_capturing(False)
            self.view.set_packet_status(f"完成：{os.path.basename(result)}")
            if self.logs_view:
                self.logs_view.add_log(f"[抓包] 完成：{result}", "success")
        else:
            self.view.set_packet_capturing(False)
            self.view.set_packet_status(f"❌ {result}")

    def _stop_capture(self):
        if self._capture_worker and self._capture_worker.isRunning():
            self._capture_worker.stop()
            self.view.set_packet_status("正在停止…")

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
    def _on_launch_test(self, payload: dict):
        package = payload['package']
        if not self.device_service.device:
            show_toast(self.view, "请先连接设备")
            return

        from views.perf_dialogs import LaunchTestDialog

        try:
            compat = AndroidCompat(self.device_service.device)
            service = PerfService(self.device_service.device, compat)

            # 支持「重新测试」：在对话框内点重新测试就再跑一轮
            while True:
                # 先停掉，避免影响冷启动测量
                cold_ms = service.measure_cold_launch(package)
                warm_ms = service.measure_warm_launch(package)

                # 持久化到模型（历史趋势 + 基线对比都基于它）
                self.model.add_launch_record(package, cold_ms, warm_ms)
                history = self.model.get_launch_history(package)
                baseline = self.model.get_launch_baseline(package)

                dlg = LaunchTestDialog(
                    package, cold_ms, warm_ms,
                    history=history, baseline=baseline,
                    baseline_setter=lambda c, w: self.model.set_launch_baseline(package, c, w),
                    parent=self.view
                )
                dlg.exec()

                # 结果写进底部「虫师日志」，不进自动化执行日志区
                from utils import log_colors
                self.log_emitted.emit(
                    f"<span style='color:{log_colors.log_color(log_colors.RESULT)};'>"
                    f"[性能] 启动测试: 冷启动 {cold_ms} ms, 热启动 {warm_ms} ms</span>"
                )

                # 点「重新测试」则继续循环，否则结束
                if not dlg.retest_requested:
                    break
        except Exception as e:
            ErrorDialog.show_error(self.view, "启动测试失败", str(e))

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
                    writer.writerow([
                        f"{s.timestamp:.3f}", f"{s.cpu_percent:.2f}",
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

        worse = [r for r in rows if r['status'] == 'worse']

        def _fmt(r, digits=2):
            return (f"{r['label']}：基线 {r['base']:.{digits}f} → "
                    f"本次 {r['cur']:.{digits}f}（{r['change_pct']:+.1f}%）")

        if worse:
            detail = "\n".join(f"• {_fmt(r)}" for r in worse)
            full_detail = (
                f"完整对比（共 {len(rows)} 项，其中明显劣化 {len(worse)} 项）：\n"
                + "\n".join(f"• {_fmt(r)}" for r in rows)
            )
            WarningDialog.show_warning(
                self.view,
                "性能劣化告警",
                f"对比基线：{baseline.name}\n\n"
                f"检测到 {len(worse)} 项指标明显劣化：\n\n{detail}",
                full_detail
            )

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
        )
        self.model.add_baseline(baseline)
        show_toast(self.view, f"基线 '{name}' 已保存")

    def _on_threshold_config(self):
        from views.perf_dialogs import PerfThresholdDialog
        dlg = PerfThresholdDialog(self.model.threshold, self.view)
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