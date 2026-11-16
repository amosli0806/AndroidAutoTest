# controllers/execution_controller.py
import os
import threading
from datetime import datetime
from PyQt6.QtCore import QObject, QThread, pyqtSignal

from models.execution_model import ExecutionModel
from models.project_model import ProjectModel
from models.step_model import StepModel
from utils.dialogs import ErrorDialog
from utils.app_paths import open_containing_folder
from utils.toast import show_toast
from views.execute_view import ExecuteView
from views.logs_view import LogsView
from services.case_runner import CaseRunner
from services.device_service import DeviceService
from utils.report_generator import ReportGenerator
from utils.theme import ThemeMode


class ExecutionWorker(QObject):
    """Qt 适配器：把 CaseRunner 的进度回调转成信号。

    真正的循环 / 重试 / 数据驱动逻辑在 services/case_runner.py，
    MCP 侧直接调 CaseRunner，不再经过 QObject。
    """
    progress = pyqtSignal(str, str)
    finished = pyqtSignal()

    def __init__(self, case_ids, project_model, step_model, device_service, exec_model,
                 loop_count=1, stop_on_fail=False,
                 retry_count=0, retry_delay=0, retry_only_timeout=True):
        super().__init__()
        self.case_ids = case_ids
        self.project_model = project_model
        self.step_model = step_model
        self.device = device_service
        self.exec_model = exec_model
        self.loop_count = loop_count
        self.stop_on_fail = stop_on_fail
        self.retry_count = retry_count
        self.retry_delay = retry_delay
        self.retry_only_timeout = retry_only_timeout
        self._abort_event = threading.Event()
        self.results = None

    def request_abort(self):
        """请求中止本轮执行；runner 在每个用例/步骤边界检查这个 Event。"""
        self._abort_event.set()

    def run(self):
        try:
            runner = CaseRunner(
                self.case_ids,
                self.project_model,
                self.step_model,
                self.device,
                self.exec_model,
                loop_count=self.loop_count,
                stop_on_fail=self.stop_on_fail,
                retry_count=self.retry_count,
                retry_delay=self.retry_delay,
                retry_only_timeout=self.retry_only_timeout,
                on_progress=lambda message, level: self.progress.emit(message, level),
                abort_event=self._abort_event,
            )
            self.results = runner.run()
        except Exception as e:  # 兜底：绝不让线程停在「没有 finished」的状态
            self.progress.emit(f"  ✗ 执行器异常：{e}", "error")
        finally:
            self.finished.emit()


class ExecutionController(QObject):
    # 执行状态变化信号：True=开始执行，False=执行结束
    execution_state_changed = pyqtSignal(bool)
    # 本轮执行的统计（通过数, 失败数）——纯新增，供消息中心留痕，不影响既有流程
    execution_finished = pyqtSignal(int, int)

    def __init__(self, exec_model: ExecutionModel, project_model: ProjectModel,
                 step_model: StepModel, exec_view: ExecuteView, logs_view: LogsView,
                 device_service: DeviceService):
        super().__init__()
        self.exec_model = exec_model
        self.project_model = project_model
        self.step_model = step_model
        self.exec_view = exec_view
        self.logs_view = logs_view
        self.device = device_service

        self.exec_view.set_model(project_model)
        self.exec_view.execute_selected.connect(self._execute_cases)
        # 执行页的「停止」（执行中那个按钮）→ 中止本轮
        self.exec_view.stop_requested.connect(self.stop_execution)

        self.logs_view.set_model(exec_model)
        self.thread = None
        self.worker = None
        self._stop_requested = False

    def apply_theme(self, theme_mode: ThemeMode):
        """应用主题到控制器（占位方法，保持接口一致性）"""
        # 控制器不涉及界面样式，无需实际操作
        # 但可以转发给关联的视图
        if self.exec_view and hasattr(self.exec_view, 'apply_theme'):
            self.exec_view.apply_theme(theme_mode)
        if self.logs_view and hasattr(self.logs_view, 'apply_theme'):
            self.logs_view.apply_theme(theme_mode)

    def _execute_cases(self, case_ids):
        if self.thread and self.thread.isRunning():
            return

        loop_count = self.exec_view.loop_spin.value()
        stop_on_fail = self.exec_view.stop_on_fail_check.isChecked()

        filtered_cases = []
        for node_id in case_ids:
            node = self.project_model.get_node_by_id(node_id)
            if node and node.type == 'case':
                filtered_cases.append(node_id)

        if not filtered_cases:
            self.logs_view.add_log("没有可执行的用例", "warning")
            self.exec_view.set_executing(False)
            return

        # 前置检查：未连接设备时不再一刀切拒绝 —— 纯语音/等待的用例要能跑
        # （测试环境版本连不上设备时也要能输出语音）。
        # 但只要用例里有需要设备操作的步骤，仍然直接拒绝，
        # 避免整段用例跑完才在日志里看到满屏失败。
        if not self.device.check_device_online():
            names = self._device_dependent_step_names(filtered_cases)
            if names:
                tail = " 等" if len(names) >= 3 else ""
                self.logs_view.add_log(
                    f"⚠ 未检测到设备：本用例含需要设备操作的步骤"
                    f"（{'、'.join(names)}{tail}），请先在顶部工具栏连接设备后再执行",
                    "warning"
                )
                self.exec_view.set_executing(False)
                return
            self.logs_view.add_log(
                "ℹ 未连接设备，本次只执行语音播报/等待类步骤（不截图、不断言）",
                "info"
            )

        self.exec_model.reset()
        self.logs_view.update_stats()
        self._stop_requested = False      # 新一轮执行，清掉上一轮的停止标记

        self.thread = QThread()
        self.worker = ExecutionWorker(
            filtered_cases,
            self.project_model,
            self.step_model,
            self.device,
            self.exec_model,
            loop_count=loop_count,
            stop_on_fail=stop_on_fail,
            retry_count=self.exec_view.retry_spin.value(),
            retry_delay=self.exec_view.retry_delay_spin.value(),
            retry_only_timeout=(self.exec_view.retry_cond_combo.currentText() == "仅超时类"),
        )
        self.worker.moveToThread(self.thread)

        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_finished)
        self.thread.started.connect(self.worker.run)
        self.thread.start()

        self.exec_view.set_executing(True)
        self.execution_state_changed.emit(True)

    def stop_execution(self):
        """用户点了「停止」：让正在跑的这一轮收尾退出。

        只作用于「执行页手动执行」这条路径；定时任务不接这个入口（按需求不需要）。
        """
        if self.worker is None or self._stop_requested:
            return
        self._stop_requested = True
        self.worker.request_abort()
        self.logs_view.add_log(
            "⏹ 已请求停止：当前步骤跑完就停下，后续用例不再执行", "warning")

    def _device_dependent_step_names(self, case_ids, limit=3):
        """挑出这些用例里「需要设备」的步骤名，用来把拒绝原因说清楚。

        用步骤名而不是类型名：步骤名是用户自己起的，比 'click' 之类好定位。
        """
        names = []
        for case_id in case_ids:
            for step in self.step_model.get_steps_for_case(case_id) or []:
                if self.device.step_needs_device(step.type):
                    names.append(step.name or step.type)
                    if len(names) >= limit:
                        return names
        return names

    def _on_progress(self, message, log_type):
        self.logs_view.add_log(message, log_type)

        # 通过日志类型和内容判断用例通过/失败
        if log_type == 'success' and '用例' in message and '执行通过' in message:
            self.exec_model.increment_pass()
        elif log_type == 'error' and '用例' in message and '执行失败' in message:
            self.exec_model.increment_fail()
        self.logs_view.update_stats()

    def _on_finished(self):
        self.exec_view.set_report_enabled(True)
        self.exec_view.set_executing(False)
        self.thread.quit()
        self.thread.wait()
        self.thread = None
        self.worker = None
        self.execution_state_changed.emit(False)

        # 消息中心：把本轮统计抛出去。注意这里只在「用户手动执行」的收尾路径上，
        # 定时任务的收尾走 TaskController，避免同一次执行产生两条消息
        try:
            stats = self.exec_model.get_stats()
            self.execution_finished.emit(stats[0], stats[1])
        except Exception:
            pass

        # 有失败上下文时，让日志视图把「AI 分析」按钮亮起来
        if self.exec_model.failure_contexts:
            if hasattr(self.logs_view, "set_ai_available"):
                self.logs_view.set_ai_available(True)

    def _report_output_dir(self) -> str:
        """报告输出目录：**设置里的输出目录 / 设备序列号**。

        与性能检测的截图归档同一套约定（`{输出目录}/{序列号}/screenshots`）——
        多台设备都往一个目录里丢报告，事后根本分不清哪份是哪台车的。
        未连接设备（没有序列号）时退回输出目录本身。
        """
        from utils.settings import Settings
        from utils.serial_helper import safe_serial
        base = Settings.get_output_dir()
        serial = (getattr(self.device, "serial", "") or "").strip()
        return os.path.join(base, safe_serial(serial)) if serial else base

    def generate_report(self):
        # 报告存到「设置 → 输出目录 / 设备序列号」（与截图/ADB 导出等一致），不再是项目下的 reports/
        output_dir = self._report_output_dir()
        os.makedirs(output_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_path = os.path.join(output_dir, f"TestReport_{timestamp}.html")
        try:
            report_path = ReportGenerator.generate(self.exec_model, file_path)
            # 与性能检测「生成报告」保持一致：轻提示 + 自动打开所在文件夹
            show_toast(self.exec_view, "报告已生成")
            open_containing_folder(report_path)

        except Exception as e:
            ErrorDialog.show_error(
                self.exec_view,
                "报告生成失败",
                f"生成测试报告时发生错误：\n{str(e)}"
            )