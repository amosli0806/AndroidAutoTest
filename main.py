# main.py
import sys
import os
import threading
from PyQt6.QtWidgets import (
    QApplication, QMessageBox, QWidget, QLabel,
    QVBoxLayout, QHBoxLayout, QPushButton
)
from PyQt6.QtGui import QPalette, QColor, QIcon, QFont
from PyQt6.QtCore import QTimer, Qt, QThread, pyqtSignal

from utils.dialogs import ErrorDialog
from views.main_window import MainWindow
from models.project_model import ProjectModel
from models.step_model import StepModel
from models.execution_model import ExecutionModel
from models.element_model import ElementModel
from models.suite_model import SuiteModel
from models.task_model import TaskModel
from views.project_tree_view import ProjectTreeView
from views.step_list_view import StepListView
from views.action_card_view import ActionCardView
from views.execute_view import ExecuteView
from views.logs_view import LogsView
from views.element_manager_view import ElementManagerView
from views.task_view import TaskView
from views.help_view import HelpView
from controllers.project_controller import ProjectController
from controllers.step_controller import StepController
from controllers.execution_controller import ExecutionController
from controllers.element_controller import ElementController
from services.device_service import DeviceService
from services.weditor_service import WeditorService
from utils.toast import show_toast
from utils.settings import Settings, THEME_MODE_SYSTEM, THEME_MODE_DARK
from utils.theme import Theme, ThemeMode
import logging
from models.perf_model import PerfModel
from views.perf_view import PerfView
from controllers.perf_controller import PerfController


logging.basicConfig(
    filename='app_debug.log',
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 全局验证线程
_verify_thread = None
_verify_lock = threading.Lock()


class VerifyWorker(QThread):
    """后台验证线程，避免阻塞 UI"""
    result_ready = pyqtSignal(str)

    def __init__(self, device, elem, parent, device_service):
        super().__init__(parent)
        self.device = device
        self.elem = elem
        self.device_service = device_service
        self._cancel = False

    def run(self):
        if not self.device_service.check_device_online():
            self.result_ready.emit("⚠️ 请先连接设备")
            return

        try:
            loc_type = self.elem.loc_type
            loc_value = self.elem.loc_value
            exists = False

            if self._cancel:
                return

            timeout = 0.1
            if loc_type == '资源ID':
                exists = self.device(resourceId=loc_value).exists(timeout=timeout)
            elif loc_type == '文本':
                exists = self.device(text=loc_value).exists(timeout=timeout)
            elif loc_type == '描述':
                exists = self.device(description=loc_value).exists(timeout=timeout)
            elif loc_type == 'XPath':
                xpath_obj = self.device.xpath(loc_value)
                if hasattr(xpath_obj, 'exists'):
                    if callable(xpath_obj.exists):
                        exists = xpath_obj.exists(timeout=timeout)
                    else:
                        exists = xpath_obj.exists
                else:
                    exists = False
            elif loc_type == '坐标':
                self.result_ready.emit("⚠️ 坐标定位无法直接验证")
                return
            else:
                self.result_ready.emit("⚠️ 不支持的定位方式")
                return

            if self._cancel:
                return

            if exists:
                self.result_ready.emit("✅ 元素存在")
            else:
                self.result_ready.emit("❌ 元素不存在")
        except Exception as e:
            error_msg = str(e).lower()
            if "connection" in error_msg or "device" in error_msg or "offline" in error_msg:
                self.result_ready.emit("⚠️ 请先连接设备")
            else:
                self.result_ready.emit("⚠️ 验证出错")

    def cancel(self):
        self._cancel = True


def main():
    def global_exception_hook(exc_type, exc_value, exc_tb):
        logging.getLogger(__name__).critical(
            "Unhandled exception",
            exc_info=(exc_type, exc_value, exc_tb)
        )
        ErrorDialog.show_error(None, "程序错误", f"发生未捕获异常：\n{exc_value}\n\n详情请查看 app_debug.log")

    sys.excepthook = global_exception_hook

    # 关键：必须在 QApplication 创建之前设置
    # 否则 QWebEngineView 首次创建时会触发窗口重建（看起来像应用重启）
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)

    app = QApplication(sys.argv)

    # ---------- 设置应用程序图标 ----------
    icon_path = os.path.join(os.path.dirname(__file__), "resources", "icons", "app_icon.ico")
    if os.path.exists(icon_path):
        app_icon = QIcon(icon_path)
        app.setWindowIcon(app_icon)
    else:
        print(f"警告：未找到图标文件 {icon_path}")

    # ---------- 设置全局外观（仅保留基础调色板，不设置全局样式表） ----------
    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(245, 246, 250))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(0, 0, 0))
    palette.setColor(QPalette.ColorRole.Base, QColor(255, 255, 255))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(235, 238, 242))
    palette.setColor(QPalette.ColorRole.Text, QColor(0, 0, 0))
    palette.setColor(QPalette.ColorRole.Button, QColor(240, 240, 240))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(0, 0, 0))
    palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 0, 0))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(25, 118, 210))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    app.setPalette(palette)

    # ---------- 移除全局样式表，由主题系统控制 ----------
    # 仅保留 QToolTip 样式（在主题中未覆盖）
    app.setStyleSheet("""
        QToolTip {
            background-color: white;
            color: black;
            border: 1px solid #ccc;
            padding: 4px;
            font-size: 11px;
        }
    """)

    # ---------- 初始化所有模型 ----------
    project_model = ProjectModel()
    step_model = StepModel()
    exec_model = ExecutionModel()
    element_model = ElementModel()
    suite_model = SuiteModel()
    task_model = TaskModel()

    # ---------- 主窗口 ----------
    main_window = MainWindow()
    if os.path.exists(icon_path):
        main_window.setWindowIcon(QIcon(icon_path))
    main_window.set_models(project_model, step_model)

    # ---------- 视图 ----------
    project_tree = ProjectTreeView()
    step_list = StepListView()
    action_card = ActionCardView()
    execute_view = ExecuteView()
    logs_view = LogsView()
    element_manager = ElementManagerView(element_model)
    help_view = HelpView()

    # ---------- 设置套件模型 ----------
    execute_view.set_suite_model(suite_model)

    # ---------- 控制器 ----------
    step_controller = StepController(step_model, step_list, action_card)
    project_controller = ProjectController(project_model, project_tree, step_controller, execute_view, main_window)
    element_controller = ElementController(element_model, step_model)

    project_controller.set_suite_model(suite_model)

    step_controller.set_element_controller(element_controller)
    action_card.set_element_controller(element_controller)
    step_list.set_element_controller(element_controller)
    element_manager.element_changed.connect(step_list.on_element_changed)

    main_window.set_project_controller(project_controller)
    main_window.set_step_controller(step_controller)
    main_window.set_element_controller(element_controller)

    # ---------- 设备服务 ----------
    device_svc = DeviceService()
    device_svc.set_element_controller(element_controller)
    device_svc.set_step_interval(2)
    step_controller.set_device_service(device_svc)

    # ---------- 执行控制器 ----------
    exec_controller = ExecutionController(
        exec_model, project_model, step_model,
        execute_view, logs_view, device_svc
    )
    execute_view.generate_report_signal.connect(exec_controller.generate_report)

    # ---------- 定时任务 ----------
    task_view = TaskView(task_model, suite_model)
    task_view.set_project_model(project_model)
    task_view.set_device_service(device_svc)
    task_view.set_execution_controller(exec_controller)
    task_view.set_logs_view(logs_view)

    from controllers.task_controller import TaskController
    task_controller = TaskController(
        task_model=task_model,
        task_view=task_view,
        suite_model=suite_model,
        project_model=project_model,
        step_model=step_model,
        device_service=device_svc,
        exec_controller=exec_controller,
        logs_view=logs_view
    )
    execute_view.set_task_view(task_view)
    task_view.start_scheduler()

    # ---------- 设置主窗口各视图 ----------
    # 1. 自动化编辑
    main_window.set_edit_views(project_tree, step_list, action_card)

    # 2. 自动化执行（传入日志视图）
    main_window.set_execute_view_with_logs(execute_view, logs_view)

    # 3. 应用元素库
    main_window.set_element_manager_view(element_manager)

    # 4. 应用可视化（传入 None，由主窗口创建默认启动按钮）
    main_window.set_visualize_view(None)

    # 5. 帮助中心
    main_window.set_help_view(help_view)

    # 6. ADB工具箱
    from views.adb_toolbox_view import AdbToolboxView
    from controllers.adb_toolbox_controller import AdbToolboxController

    adb_toolbox_view = AdbToolboxView()
    adb_toolbox_controller = AdbToolboxController(
        view=adb_toolbox_view,
        device_service=device_svc,
    )
    main_window.set_adb_toolbox_view(adb_toolbox_view)
    main_window.set_adb_toolbox_controller(adb_toolbox_controller)
    main_window.register_sub_view(adb_toolbox_view)

    # 7. 底部日志占位（为捕虫师日志预留）
    bottom_placeholder = QLabel("捕虫师日志功能开发中")
    bottom_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
    main_window.set_bottom_log_placeholder(bottom_placeholder)

    # ---------- Weditor ----------
    weditor_svc = WeditorService()
    main_window.set_weditor_service(weditor_svc)

    # ---------- 设备刷新 ----------
    last_devices = []

    def refresh_devices():
        nonlocal last_devices
        devices = device_svc.get_devices()
        if devices != last_devices:
            main_window.update_device_list(devices)
            if devices:
                try:
                    device_svc.connect(devices[0])
                    if not last_devices:
                        task_controller.reset_offline_tasks()
                    if perf_controller is not None:
                        QTimer.singleShot(300, perf_controller._maybe_refresh_apps)
                except Exception as e:
                    pass
            else:
                task_controller.mark_tasks_offline()
            last_devices = devices

    main_window.refresh_devices_signal.connect(refresh_devices)
    refresh_devices()

    timer = QTimer()
    timer.timeout.connect(refresh_devices)
    timer.start(3000)

    main_window.set_device_service(device_svc)

    # ---------- 元素验证功能 ----------
    def handle_verify_result(message):
        show_toast(main_window, message, duration=2000)

    def verify_element(elem_id):
        global _verify_thread, _verify_lock

        if not device_svc:
            show_toast(main_window, "⚠️ 设备服务未初始化", duration=2000)
            return

        elem = element_controller.get_element_by_id(elem_id)
        if not elem:
            show_toast(main_window, "⚠️ 元素不存在", duration=2000)
            return

        show_toast(main_window, "正在验证...", duration=1500)

        with _verify_lock:
            if _verify_thread is not None and _verify_thread.isRunning():
                _verify_thread.cancel()
                _verify_thread.quit()
                _verify_thread.wait()
            _verify_thread = VerifyWorker(device_svc.device, elem, main_window, device_svc)
            _verify_thread.result_ready.connect(handle_verify_result)
            _verify_thread.finished.connect(lambda: setattr(_verify_thread, 'deleteLater', None))
            _verify_thread.start()

    element_manager.verify_element_signal.connect(verify_element)

    # ---------- 应用主题（确保所有视图已注册） ----------
    # 等待主窗口所有视图设置完成后，应用当前主题
    QTimer.singleShot(100, lambda: main_window.apply_theme())
    perf_model = PerfModel()
    perf_view = PerfView()
    perf_controller = PerfController(
        perf_model=perf_model,
        perf_view=perf_view,
        device_service=device_svc,
        project_model=project_model,
        step_model=step_model,
        suite_model=suite_model,
        logs_view=logs_view,
    )
    perf_view.set_suite_model(suite_model)
    perf_view.set_project_model(project_model)

    # 挂到主窗口 index 8（替换原占位）
    main_window.set_perf_view(perf_view)
    main_window.register_sub_view(perf_view)
    # ---------- 启动 ----------
    main_window.showMaximized()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()