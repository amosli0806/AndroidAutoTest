# controllers/perf_controller.py
"""性能检测控制器"""
import csv
import logging
import time
from datetime import datetime

from PyQt6.QtCore import QObject, QThread, pyqtSignal, QTimer, Qt

from models.perf_model import (
    PerfModel, PerfSession, PerfThreshold, PerfBaseline
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

    def __init__(self, device_service, package, metrics, interval, parent=None):
        super().__init__(parent)
        self.device_service = device_service
        self.package = package
        self.metrics = metrics
        self.interval = max(0.1, interval)
        self._running = True
        self._paused = False

    def pause(self):
        self._paused = True

    def resume(self):
        self._paused = False

    def stop(self):
        self._running = False

    def run(self):
        # 每次线程开始前，重置 Service 的内部状态
        compat = AndroidCompat(self.device_service.device)
        service = PerfService(self.device_service.device, compat)

        # 首次采样有些指标（FPS）需要预热
        try:
            service.collect_sample(self.package, self.metrics)
        except Exception:
            pass

        while self._running:
            if self._paused:
                time.sleep(0.2)
                continue

            start = time.time()

            try:
                sample = service.collect_sample(self.package, self.metrics)
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
        # 重定向 progress 信号
        worker.progress.connect(lambda m, t: self.progress.emit(m, t))
        worker.run()
        self.finished_all.emit()


class PerfController(QObject):
    """性能检测控制器"""

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
        self._last_jank_count = None  # 用于计算卡顿增量

        # 采样统计刷新定时器
        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(1000)
        self._stats_timer.timeout.connect(self._refresh_stats)

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
            packages = compat.list_third_party_packages()
            self.view.update_app_list(packages)

            # 更新指标可用性
            available = {}
            reasons = {}
            for key in ('cpu', 'mem', 'fps', 'traffic', 'jank'):
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
        self.current_session = session
        self._alert_cache = {k: False for k in metrics}
        self._last_jank_count = None  # 重置卡顿增量基准

        # 清空视图数据
        for card in self.view._cards.values():
            if hasattr(card, 'clear_data'):
                card.clear_data()
            elif hasattr(card, 'reset'):
                card.reset()

        # 启动采集线程
        self.perf_worker = PerfWorker(
            self.device_service, package, metrics, interval
        )
        self.perf_worker.sample_ready.connect(self._on_sample)
        self.perf_worker.error_occurred.connect(self._on_worker_error)
        self.perf_worker.start()

        # 启动统计定时器
        self._stats_timer.start()

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

        # 停止场景化执行
        if self.scenario_worker:
            self.scenario_worker._abort = True
            self.scenario_worker.wait(2000)
            self.scenario_worker = None

        self._stats_timer.stop()

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
            elif not triggered and self._alert_cache.get(key, False):
                # 恢复正常
                self._alert_cache[key] = False
                self.view.set_alert(key, False)

        # 卡顿单独处理：用"本次采样期间的增量"对比阈值
        if 'jank' in self.current_session.metrics:
            current_jank = sample.jank_count
            if self._last_jank_count is None:
                # 首个采样点，只记录基准，不判断
                self._last_jank_count = current_jank
            else:
                delta = current_jank - self._last_jank_count
                self._last_jank_count = current_jank
                # App 重启导致 jank_count 被重置时，delta 会为负数，跳过本次判断
                if delta < 0:
                    pass
                else:
                    triggered = (delta > th.jank_max)
                    if triggered and not self._alert_cache.get('jank', False):
                        self._alert_cache['jank'] = True
                        self.view.set_alert('jank', True)
                        msg = f"⚠ JANK 异常: 本周期新增 {delta} 次（阈值 {th.jank_max}）"
                        self.current_session.alerts.append((sample.timestamp, 'jank', msg))
                        self.view.add_alert_log(msg)
                        if self.logs_view:
                            self.logs_view.add_log(f"[性能告警] {msg}", "warning")
                    elif not triggered and self._alert_cache.get('jank', False):
                        self._alert_cache['jank'] = False
                        self.view.set_alert('jank', False)

    def _refresh_stats(self, final=False):
        if not self.current_session:
            return
        stats = self.current_session.get_stats()
        self.view.update_stats(stats)

    def _on_worker_error(self, msg):
        logger.warning(f"[PerfController] 采集错误: {msg}")

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

        try:
            compat = AndroidCompat(self.device_service.device)
            service = PerfService(self.device_service.device, compat)

            # 先停掉，避免影响冷启动测量
            cold_ms = service.measure_cold_launch(package)
            warm_ms = service.measure_warm_launch(package)

            # 保存到历史
            if not hasattr(self, '_launch_history'):
                self._launch_history = []
            self._launch_history.append({
                'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                'cold': cold_ms if cold_ms > 0 else '-',
                'warm': warm_ms if warm_ms > 0 else '-',
            })

            from views.perf_dialogs import LaunchTestDialog
            dlg = LaunchTestDialog(package, cold_ms, warm_ms,
                                   self._launch_history, self.view)
            dlg.exec()

            if self.logs_view:
                self.logs_view.add_log(
                    f"[性能] 启动测试: 冷启动 {cold_ms} ms, 热启动 {warm_ms} ms",
                    "info"
                )
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
        path, _ = QFileDialog.getSaveFileName(
            self.view, "导出 CSV",
            f"perf_{session.name}.csv", "CSV Files (*.csv)"
        )
        if not path:
            return

        try:
            with open(path, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'timestamp', 'cpu_percent', 'mem_pss_mb',
                    'fps', 'jank_count', 'rx_bytes', 'tx_bytes'
                ])
                for s in session.samples:
                    writer.writerow([
                        f"{s.timestamp:.3f}", f"{s.cpu_percent:.2f}",
                        f"{s.mem_pss_mb:.2f}", s.fps, s.jank_count,
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
            f"perf_{session.name}.json", "JSON Files (*.json)"
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
        from PyQt6.QtWidgets import QFileDialog
        import os

        reports_dir = os.path.join(os.getcwd(), "reports")
        os.makedirs(reports_dir, exist_ok=True)
        default_path = os.path.join(
            reports_dir,
            f"perf_report_{session.name}.html"
        )
        path, _ = QFileDialog.getSaveFileName(
            self.view, "保存性能报告", default_path, "HTML Files (*.html)"
        )
        if not path:
            return

        try:
            PerfReportGenerator.generate(session, path)
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