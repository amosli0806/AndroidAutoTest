# controllers/execution_controller.py
import os
import re
import subprocess
from datetime import datetime
from PyQt6.QtCore import QObject, QThread, pyqtSignal
from PyQt6.QtWidgets import QMessageBox

from models.execution_model import ExecutionModel
from models.project_model import ProjectModel
from models.step_model import StepModel
from utils.dialogs import ErrorDialog
from views.execute_view import ExecuteView
from views.logs_view import LogsView
from services.device_service import DeviceService
from utils.report_generator import ReportGenerator
from utils.theme import ThemeMode


class ExecutionWorker(QObject):
    progress = pyqtSignal(str, str)
    finished = pyqtSignal()

    def __init__(self, case_ids, project_model, step_model, device_service, exec_model,
                 loop_count=1, stop_on_fail=False):
        super().__init__()
        self.case_ids = case_ids
        self.project_model = project_model
        self.step_model = step_model
        self.device = device_service
        self.exec_model = exec_model
        self.loop_count = loop_count
        self.stop_on_fail = stop_on_fail
        self._abort = False

    def _format_error(self, e):
        # 如果是 DeviceService 抛出的友好错误（RuntimeError），直接返回其消息
        if isinstance(e, RuntimeError):
            return str(e)

        error_str = str(e)
        # 原有解析逻辑
        if hasattr(e, 'args') and len(e.args) > 0:
            arg = e.args[0]
            if isinstance(arg, tuple) and len(arg) >= 3:
                loc_dict = arg[2]
                if isinstance(loc_dict, dict):
                    conditions = []
                    if 'text' in loc_dict and loc_dict['text']:
                        conditions.append(f"文本='{loc_dict['text']}'")
                    if 'resourceId' in loc_dict and loc_dict['resourceId']:
                        conditions.append(f"资源ID='{loc_dict['resourceId']}'")
                    if 'description' in loc_dict and loc_dict['description']:
                        conditions.append(f"描述='{loc_dict['description']}'")
                    if 'className' in loc_dict and loc_dict['className']:
                        conditions.append(f"类名='{loc_dict['className']}'")
                    if 'xpath' in loc_dict and loc_dict['xpath']:
                        conditions.append(f"XPath='{loc_dict['xpath']}'")
                    if 'textContains' in loc_dict and loc_dict['textContains']:
                        conditions.append(f"文本包含='{loc_dict['textContains']}'")
                    if conditions:
                        condition_str = '，'.join(conditions)
                        return f"未找到元素：{condition_str}"
            elif isinstance(arg, dict):
                data = arg.get('data', '')
                method = arg.get('method', '')
                if data:
                    if 'timeout' in str(arg).lower() or 'wait' in str(arg).lower():
                        return f"等待超时，未找到元素：{data}"
                    else:
                        return f"操作失败：{data}"
                elif method:
                    return f"{method} 操作失败"
        selector_match = re.search(r"Selector\s*\[([^\]]*)\]", error_str)
        if selector_match:
            selector_text = selector_match.group(0)
            if 'timeout' in error_str.lower() or 'wait' in error_str.lower():
                return f"等待超时，未找到元素：{selector_text}"
            else:
                return f"未找到元素：{selector_text}"
        if 'timeout' in error_str.lower():
            return f"等待超时：{error_str}"
        elif 'not found' in error_str.lower() or 'no such element' in error_str.lower():
            return f"未找到元素：{error_str}"
        else:
            return error_str

    def run(self):
        total_loops = self.loop_count
        for loop_idx in range(total_loops):
            if self._abort:
                break
            if loop_idx > 0:
                self.progress.emit(f"--- 第 {loop_idx+1} 轮执行 ---", "info")
            for idx, case_id in enumerate(self.case_ids):
                if self._abort:
                    break
                case_node = self.project_model.get_node_by_id(case_id)
                case_name = case_node.name if case_node else f"用例{case_id}"
                self.progress.emit(f"▶ [{idx+1}/{len(self.case_ids)}] 用例 \"{case_name}\" 开始执行", "info")
                try:
                    steps = self.step_model.get_steps_for_case(case_id)
                    if not steps:
                        self.progress.emit(f"  ⚠ 用例 \"{case_name}\" 没有步骤，跳过", "warning")
                        continue
                    for step_idx, step in enumerate(steps, 1):
                        try:
                            self.device.perform(step)
                            if step.type == 'assert':
                                self.progress.emit(f"  ✅ 断言：{step.name} 通过", "success")
                        except Exception as e:
                            # 截图（如果设备服务支持）
                            if hasattr(self.device, 'last_screenshot') and self.device.last_screenshot:
                                self.exec_model.screenshot_paths.append(self.device.last_screenshot)
                                self.device.last_screenshot = None
                            step_info = f"步骤 {step_idx} \"{step.name}\""
                            error_msg = self._format_error(e)
                            self.progress.emit(f"  ✗ {step_info} 失败：{error_msg}", "error")
                            raise
                    self.progress.emit(f"  ✓ 用例 \"{case_name}\" 执行通过", "success")
                except Exception:
                    self.progress.emit(f"  ✗ 用例 \"{case_name}\" 执行失败", "error")
                    if self.stop_on_fail:
                        self.progress.emit("  ⛔ 因失败停止标志，终止后续执行", "warning")
                        self._abort = True
                        break
        self.finished.emit()


class ExecutionController(QObject):
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

        self.logs_view.set_model(exec_model)
        self.thread = None
        self.worker = None

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

        self.exec_model.reset()
        self.logs_view.update_stats()

        filtered_cases = []
        for node_id in case_ids:
            node = self.project_model.get_node_by_id(node_id)
            if node and node.type == 'case':
                filtered_cases.append(node_id)

        if not filtered_cases:
            self.logs_view.add_log("没有可执行的用例", "warning")
            self.exec_view.set_executing(False)
            return

        self.thread = QThread()
        self.worker = ExecutionWorker(
            filtered_cases,
            self.project_model,
            self.step_model,
            self.device,
            self.exec_model,
            loop_count=loop_count,
            stop_on_fail=stop_on_fail
        )
        self.worker.moveToThread(self.thread)

        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_finished)
        self.thread.started.connect(self.worker.run)
        self.thread.start()

        self.exec_view.set_executing(True)

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

    def generate_report(self):
        os.makedirs("reports", exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_path = f"reports/TestReport_{timestamp}.html"
        try:
            report_path = ReportGenerator.generate(self.exec_model, file_path)

            msg_box = QMessageBox(self.exec_view)
            msg_box.setWindowTitle("报告已生成")
            msg_box.setText(f"测试报告已保存到：\n{report_path}\n\n选择操作：")

            open_folder_btn = msg_box.addButton("📂 打开文件夹", QMessageBox.ButtonRole.ActionRole)
            ok_btn = msg_box.addButton("OK", QMessageBox.ButtonRole.AcceptRole)

            msg_box.exec()

            clicked = msg_box.clickedButton()
            if clicked == open_folder_btn:
                folder = os.path.dirname(report_path)
                if os.name == 'nt':
                    os.startfile(folder)
                else:
                    subprocess.Popen(['xdg-open', folder])

        except Exception as e:
            ErrorDialog.show_error(
                self.exec_view,
                "报告生成失败",
                f"生成测试报告时发生错误：\n{str(e)}"
            )