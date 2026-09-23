# main.py
import sys
import os
import time
import threading
from PyQt6.QtWidgets import (
    QApplication, QMessageBox, QWidget, QLabel,
    QVBoxLayout, QHBoxLayout, QPushButton
)
from PyQt6.QtGui import QPalette, QColor, QIcon, QFont
from PyQt6.QtCore import QTimer, Qt, QThread, QObject, pyqtSignal, pyqtSlot, QProcess

from utils.adb_path import get_adb_path
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
from services.update_service import (
    check_for_update, is_enabled as update_check_enabled,
    pending_staged, cleanup_staging, updater_exe_in,
)
from utils.toast import show_toast
from utils.app_paths import migrate_legacy_data_files, get_app_dir
from utils import log_colors
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


# ---------- 启动封面 ----------
# 打包后（main.spec 里的 Splash）bootloader 会在 Python 解释器启动之前就弹出封面，
# 盖住导入模块 / 建界面 / 拉起 adb / 连设备这段时间；这里只负责把进度文字刷成
# 人话，并在主界面出来时把它关掉。
#
# 为什么必须 try/except 包起来：开发环境直接 `python main.py` 跑时根本没有
# pyi_splash 这个模块（它是打包运行时才注入的），不该因此报错。
def splash_update(text: str) -> None:
    """更新启动封面上的进度文字；非打包运行时是空操作。"""
    try:
        import pyi_splash
        pyi_splash.update_text(text)
    except Exception:
        pass


def splash_close() -> None:
    """关掉启动封面（幂等；非打包运行时是空操作）。"""
    try:
        import pyi_splash
        pyi_splash.close()
    except Exception:
        pass


def ensure_adb_server(timeout: float = 20.0) -> bool:
    """确保 adb server 可用，返回是否成功。

    为什么必须有这一步：强杀过 adb（任务管理器结束进程 / 安全软件拦截 / 崩溃）
    之后，第一次 adb 调用可能要十几秒才能把 server 重新拉起来，而
    DeviceService.get_devices() 的 timeout=5 到点会把子进程**杀掉**，留下一个
    半启动的 server；此后 track-devices 每轮都撞在这个坏状态上秒退，表现就是
    消息面板里「已恢复 / 已中断」成对刷。启动时先显式 start-server 把它拉稳。
    """
    import subprocess
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
    try:
        r = subprocess.run(
            [get_adb_path(), 'start-server'],
            capture_output=True, text=True, timeout=timeout,
            encoding='utf-8', errors='replace', creationflags=creationflags,
        )
        out = f"{r.stdout or ''}{r.stderr or ''}".strip().splitlines()
        ok = r.returncode == 0
        logger.info("adb start-server %s%s", "成功" if ok else f"失败(退出码={r.returncode})",
                    f": {out[-1][:160]}" if out else "")
        return ok
    except Exception as e:
        logger.warning("adb start-server 异常: %s: %s", type(e).__name__, e)
        return False


class _UiSlot(QObject):
    """把后台线程的信号投递回 GUI 线程执行。

    `signal.connect(普通函数)` 在 PyQt 里是在**发信号的线程**里同步调用的，
    而 devices_changed 的处理会碰界面（update_device_list），也内含最长 5 秒的
    adb 子进程等待 —— 既不该在 watcher 线程改界面，也会把自己的重连周期堵住。
    包成 QObject 上的 @pyqtSlot 后走 Qt 的自动连接，跨线程自动排队。
    """

    def __init__(self, handler, parent=None):
        super().__init__(parent)
        self._handler = handler

    @pyqtSlot()
    def run(self):
        try:
            self._handler()
        except Exception as e:
            logger.warning("设备刷新失败: %s: %s", type(e).__name__, e)


class _DeviceScanSignal(QObject):
    """启动时后台预扫设备的结果投递点。

    预扫必须在后台线程里做（原因见 main() 里 _start_device_link 的注释），但结果
    得回到 GUI 线程才能碰界面；跨线程 emit 走 Qt 的队列投递，和 _UiSlot 同一套机制。
    """

    devices_ready = pyqtSignal(list)


class _UpdateCheckSignal(QObject):
    """检查更新的结果投递点（后台线程 -> GUI 线程），与 _DeviceScanSignal 同一套机制。"""

    checked = pyqtSignal(object)


class DeviceWatcher(QThread):
    """监听 adb 设备变化（adb track-devices 长连接）"""
    devices_changed = pyqtSignal()
    watcher_status = pyqtSignal(bool)   # True=正常连接，False=断开重连中
    link_error = pyqtSignal(str)        # 失败原因：只写日志，不进消息中心免得刷屏

    # 连续失败时退避（秒）：server 处于坏状态时没必要每 5 秒敲一次
    RETRY_DELAYS = (1.0, 2.0, 5.0, 10.0)

    def __init__(self, adb_path, parent=None):
        super().__init__(parent)
        self.adb_path = adb_path
        self._stop = False

    def run(self):
        import subprocess
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
        attempt = 0

        while not self._stop:
            proc = None
            try:
                proc = subprocess.Popen(
                    [self.adb_path, 'track-devices'],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    creationflags=creationflags,
                )

                # 关键：**先读到数据才算连上**，连上之后才报「已恢复」。
                # 原来 Popen 之后立刻 emit(True)，而 server 正在重建时这个进程会秒退，
                # 于是每轮都冒一对「已恢复 / 已中断」—— 消息面板看着像不停抽风。
                connected = False
                while not self._stop:
                    header = proc.stdout.read(4)
                    if not header or len(header) < 4:
                        break
                    try:
                        n = int(header, 16)
                    except ValueError:
                        break
                    if not connected:
                        connected = True
                        attempt = 0
                        self.watcher_status.emit(True)
                    proc.stdout.read(n)
                    self.devices_changed.emit()
            except Exception as e:
                self.link_error.emit(f"{type(e).__name__}: {e}")
            finally:
                # 断开：无论正常或异常都通知一次（消息中心按状态翻转去重）
                self.watcher_status.emit(False)
                if proc is not None:
                    self._terminate_and_log(proc)

            if self._stop:
                return
            delay = self.RETRY_DELAYS[min(attempt, len(self.RETRY_DELAYS) - 1)]
            attempt += 1
            # 分片等待，便于快速响应 stop
            for _ in range(int(delay * 10)):
                if self._stop:
                    return
                time.sleep(0.1)

    @staticmethod
    def _terminate_and_log(proc):
        """收尾：结束子进程，并把退出码 / stderr 记进 app_debug.log。

        以前 stderr 丢进 DEVNULL，强杀 adb 之后常见的
        "error: protocol fault (couldn't read status): connection reset"
        这类真原因就再也查不到了。流先断、进程还活着的情况也要记（先收尸再读）。
        """
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except Exception:
                    pass
            if proc.poll() is None:      # 还没死透：别读 stderr，会阻塞
                return
            err = b""
            if proc.stderr is not None:
                err = proc.stderr.read() or b""
            text = err.decode("utf-8", "replace").strip()
            logger.warning("track-devices 结束: 退出码=%s, stderr=%s",
                           proc.returncode, text[:200] or "(空)")
        except Exception:
            pass

    def stop(self):
        self._stop = True

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


# 回收统计的零值（没跑到清理时用它兜底）
EMPTY_CLEANUP_STAT = {'keys': 0, 'steps': 0, 'freed': 0, 'suite_refs': 0}


def cleanup_unused_data(project_model, step_model, suite_model):
    """回收无用步骤数据 + 套件里的失效引用，返回统计；**当前不能清理时返回 None**。

    steps_data.json 只增不减：删除项目/功能模块（旧代码，见 project_controller 的注释）
    和导入用例都会留下没人引用的步骤对象。实测一份用了几个月的文件里 8265 个步骤只有
    63 个还有人引用，占 1.39 MB 的 99%；而这份文件**每改一个步骤就整个重写一遍**，
    残留越多编辑越慢（实测单次 save 12.8 ms -> 0.1 ms）。

    用例清单为空时一律返回 None、什么都不删 —— 清理的依据就是"哪些用例还存在"，
    project_data.json 一旦读失败，拿空清单去清会把 steps_data.json 全删光。
    """
    if not project_model.root_nodes:
        return None
    valid_case_ids = {case.id for case in project_model.get_all_cases()}
    stat = step_model.gc_orphan_steps(valid_case_ids)
    stat['suite_refs'] = suite_model.remove_missing_cases(valid_case_ids)
    return stat


def main():
    # 封面上的第一句人话（在它之前显示的是 spec 里的 text_default「正在启动…」，
    # 因为模块导入阶段 main() 还没开始跑）
    splash_update("正在加载数据…")

    # ---------- 统一 adb 二进制 ----------
    # uiautomator2 的设备操作最终都走 adbutils，而 adbutils 解析 adb 的优先级是：
    #   ADBUTILS_ADB_PATH 环境变量 > PATH 里的 adb > 它自带的 adb
    # 这里把 adbutils 钉到内置 adb，保证整个进程（长连接 / 设备列表 / 用例执行）
    # 只用同一个二进制 —— 本机就装着三份 adb（内置 36.0.0、PATH 与 Android SDK
    # 各一份 37.0.0），混用会让"用哪个 adb"这件事变得不可预期。
    # 注：**包版本号不同并不会让 client 杀掉 server**（实测 36/37 互相连同一个
    # server 都正常，版本比对看的是协议号 1.0.41，不是 platform-tools 包版本），
    # 所以别再拿"版本不一致会杀 server"当理由 —— 长连接断开的真原因见
    # DeviceWatcher 的注释（server 重建期 spawn 秒退）。
    # 必须放在任何一次 adb 调用之前；adb_path() 是每次调用时现读环境变量的，
    # 不缓存，所以在这里设置就来得及。
    _bundled_adb = get_adb_path()
    if os.path.isfile(_bundled_adb):
        os.environ["ADBUTILS_ADB_PATH"] = _bundled_adb

    def global_exception_hook(exc_type, exc_value, exc_tb):
        logging.getLogger(__name__).critical(
            "Unhandled exception",
            exc_info=(exc_type, exc_value, exc_tb)
        )
        # 先把封面关掉再弹错误框：封面是 always_on_top，留着会把错误框压在下面
        splash_close()
        ErrorDialog.show_error(None, "程序错误", f"发生未捕获异常：\n{exc_value}\n\n详情请查看 app_debug.log")

    sys.excepthook = global_exception_hook

    # 数据文件统一收进「程序目录/data/」，必须在读取任何数据之前迁移完成
    migrate_legacy_data_files()


    # ---------- Windows：不要设置显式 AppUserModelID（实测结论，别再加回来）----------
    # 曾经在这里调用 SetCurrentProcessExplicitAppUserModelID("chongshi.autotest.desktop")，
    # 实测它在 Win11 上正是「任务栏显示通用图标」的直接原因：
    #   * 窗口一旦挂上显式 AUMID，任务栏按钮的图标就不再取窗口图标（WM_GETICON），
    #     也不取 exe 内嵌图标，而是按 AUMID 去找注册了该 ID 的快捷方式（.lnk）的图标；
    #   * 本应用是绿色免安装的，系统里永远不会有这样的快捷方式 -> 任务栏显示
    #     Windows 通用的「无图标窗口」占位图（白窗口+图片缩略图那种）。
    #   * 对照实验（同一窗口图标、仅切换 AUMID）：无 AUMID -> 任务栏回退显示 exe 图标；
    #     有 AUMID -> 通用图标。exe 已通过 main.spec 的 icon= 嵌入应用图标，
    #     去掉 AUMID 后任务栏/固定到任务栏/分组全部正常。
    # 若将来要做通知中心、跳转列表等真正依赖 AUMID 的功能，正确做法是随安装
    # 创建一个带 System.AppUserModel.ID 属性的快捷方式，而不是在进程里裸设 ID。

    # 关键：必须在 QApplication 创建之前设置
    # 否则 QWebEngineView 首次创建时会触发窗口重建（看起来像应用重启）
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)

    app = QApplication(sys.argv)
    splash_update("正在初始化界面…")
    # ---------- 让所有 QDialog 的标题栏自动跟随主题 ----------
    from PyQt6.QtWidgets import QDialog
    from utils.win_dark_title import set_dark_title_bar
    from utils.settings import Settings, THEME_MODE_DARK

    def _install_dialog_dark_title_patcher():
        """给 QDialog.showEvent 打补丁：每次对话框显示时，
        根据当前生效主题（含'跟随系统'）自动设置 Windows 标题栏深浅。
        解决：夜间模式下部分使用原生标题栏的对话框标题栏仍为白色。"""
        _orig_show = QDialog.showEvent

        def _patched_show(self, event):
            _orig_show(self, event)
            try:
                is_dark = (Settings.get_theme_mode() == THEME_MODE_DARK)
                set_dark_title_bar(self, is_dark)
            except Exception as e:
                print(f"[dark_title] 对话框标题栏适配失败: {e}")

        QDialog.showEvent = _patched_show

    _install_dialog_dark_title_patcher()
    # ---------- 设置应用程序图标 ----------
    # 打包后资源在 sys._MEIPASS 下（onedir 时就是 _internal/），开发环境在项目根目录；
    # 与 help_view 取图片用的是同一套判断。**别再退回 os.path.dirname(__file__)**：
    # 开发环境 __file__ 可能是相对路径（'main.py'），拼出来的路径依赖当前工作目录，
    # 换个目录启动就静默找不到图标（任务栏/标题栏变成通用图标）。
    _base_dir = (sys._MEIPASS if getattr(sys, "frozen", False)
                 else os.path.dirname(os.path.abspath(__file__)))
    icon_path = os.path.join(_base_dir, "resources", "icons", "app_icon.ico")
    if os.path.exists(icon_path):
        app_icon = QIcon(icon_path)
        app.setWindowIcon(app_icon)
    else:
        # 记进 app_debug.log：打包漏了 resources 时，这里就是唯一线索
        logger.warning("未找到图标文件: %s（任务栏/标题栏会显示成通用图标）", icon_path)

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

    # ---------- 存量数据自愈：节点 id 撞车 ----------
    # 老版本的项目树节点 id 用 id(name)（字符串的内存地址）生成，地址会被回收复用，
    # 于是两个节点能撞成同一个 id。症状是"重命名 A，B 被改了""复制 X 出来叫 Y 副本"
    # —— 因为 get_node_by_id 只认遍历里第一个命中的节点（详见 new_node_id 的注释）。
    # 生成逻辑已改成唯一 id，这里把**已经写进文件**的重复 id 一并修掉；没有重复时是空操作。
    _dup_id_fixes = project_model.repair_duplicate_ids()
    for _old_id, _new_id in _dup_id_fixes:
        # 修复前这些节点共用同一份步骤，复制一份过去，界面看起来和修复前一致
        step_model.copy_steps_for_case(_old_id, _new_id)
    if _dup_id_fixes:
        logger.warning("发现 %d 个重复 id 的节点，已自动修复: %s",
                       len(_dup_id_fixes), _dup_id_fixes)

    # ---------- 存量数据自愈 2：回收无用步骤 / 失效套件引用 ----------
    # 启动时清一次；之后各删除/导入路径也会自己清（见 StepModel.gc_orphan_steps）
    _gc_stat = cleanup_unused_data(project_model, step_model, suite_model) or EMPTY_CLEANUP_STAT
    if _gc_stat['steps'] or _gc_stat['keys'] or _gc_stat['suite_refs']:
        logger.warning(
            "启动自愈：回收无用步骤 %d 个 / 失效用例映射 %d 条 / 失效套件引用 %d 条，"
            "steps_data.json 省下 %.1f KB",
            _gc_stat['steps'], _gc_stat['keys'], _gc_stat['suite_refs'],
            _gc_stat['freed'] / 1024)

    # ---------- 主窗口 ----------
    main_window = MainWindow()
    if os.path.exists(icon_path):
        main_window.setWindowIcon(QIcon(icon_path))
    main_window.set_models(project_model, step_model)
    # 设置页「数据维护 → 清理无用数据」的入口，与启动自愈同一套逻辑
    main_window.set_data_cleanup_handler(
        lambda: cleanup_unused_data(project_model, step_model, suite_model))

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

    # 元素库快捷键注入
    def _elem_add():
        element_manager._on_add()
    def _elem_edit():
        element_manager._on_edit()
    def _elem_delete():
        element_manager._on_delete()
    def _elem_verify():
        ids = element_manager._get_selected_element_ids()
        if len(ids) == 1:
            element_manager.verify_element_signal.emit(ids[0])

    main_window._elem_shortcut_handlers = {
        "elem_add": _elem_add,
        "elem_edit": _elem_edit,
        "elem_delete": _elem_delete,
        "elem_verify": _elem_verify,
    }

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

    # 执行状态 → 捕虫师风险管控
    exec_controller.execution_state_changed.connect(
        adb_toolbox_controller.set_main_running
    )

    # ADB 工具箱日志 → 底部日志面板
    # 日志颜色是写死在 HTML 行内样式里的，切换主题后需要按新配色重新渲染，
    # 所以这里额外保留一份原始条目（上限与面板的 setMaximumBlockCount 一致）
    main_window._bottom_log_entries = []
    _BOTTOM_LOG_LIMIT = MainWindow.BOTTOM_LOG_MAX_BLOCKS   # 仅虫师日志有行数上限

    def append_bottom_log(text: str):
        if hasattr(main_window, "_bottom_log_text") and main_window._bottom_log_text:
            main_window._bottom_log_entries.append(text)
            if len(main_window._bottom_log_entries) > _BOTTOM_LOG_LIMIT:
                del main_window._bottom_log_entries[:-_BOTTOM_LOG_LIMIT]
            # 当前若在底部面板看 Crash / ANR，只缓存日志，不打断当前内容
            if main_window._bottom_panel_kind != "log":
                return
            main_window._bottom_log_text.append(log_colors.recolor(text))
            # 滚到底（行数限制由 setMaximumBlockCount 自动处理）
            sb = main_window._bottom_log_text.verticalScrollBar()
            sb.setValue(sb.maximum())

    adb_toolbox_controller.log_emitted.connect(append_bottom_log)

    # Crash / ANR / 硬件信息 内容 → 底部日志区（内联展示，不再弹对话框）
    adb_toolbox_controller.crash_log_ready.connect(
        lambda text: main_window.set_bottom_panel_content("crash", text))
    adb_toolbox_controller.anr_log_ready.connect(
        lambda text: main_window.set_bottom_panel_content("anr", text))
    adb_toolbox_controller.device_info_ready.connect(
        lambda text: main_window.set_bottom_panel_content("device_info", text))

    # 7. 底部捕虫师日志面板
    from PyQt6.QtWidgets import QWidget as _QW, QVBoxLayout as _QVL, QTextEdit as _QTE, QLabel as _QL
    from PyQt6.QtGui import QFont as _QFont

    bottom_log_widget = _QW()
    bottom_log_widget.setObjectName("BottomLogPanel")
    bl_layout = _QVL(bottom_log_widget)
    bl_layout.setContentsMargins(10, 6, 10, 10)
    bl_layout.setSpacing(4)

    bl_title = _QL("🐞 虫师日志")
    bl_title.setObjectName("BottomLogTitle")
    bl_layout.addWidget(bl_title)

    bottom_log_text = _QTE()
    bottom_log_text.setObjectName("BottomLogText")
    bottom_log_text.setReadOnly(True)
    bottom_log_text.setFont(_QFont("Consolas", 10))
    bottom_log_text.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
    # 限制最大行数：超过后自动丢弃最旧的行（Qt 原生支持，高效）
    # Crash / ANR 模式会在 show_bottom_panel 里把上限改为 0（不限），这里只作用于虫师日志
    bottom_log_text.document().setMaximumBlockCount(MainWindow.BOTTOM_LOG_MAX_BLOCKS)

    # 内容容器：0 = 文本区（日志 / 硬件信息 / Crash / ANR），1 = 消息中心。
    # 之所以要换成堆叠容器而不是继续共用这个 QTextEdit：消息列表需要每行的
    # 动态动作按钮与右键菜单，纯文本控件做不到。
    from PyQt6.QtWidgets import QStackedWidget as _QSW
    from services.notification_service import NotificationService
    from views.notification_center_view import NotificationCenterView

    notification_service = NotificationService(main_window)
    notification_view = NotificationCenterView(notification_service, main_window)

    bottom_stack = _QSW()
    bottom_stack.setObjectName("BottomPanelStack")
    bottom_stack.addWidget(bottom_log_text)
    bottom_stack.addWidget(notification_view)
    bl_layout.addWidget(bottom_stack, 1)

    # 保存引用供后续使用
    main_window._bottom_log_text = bottom_log_text
    main_window._bottom_log_title = bl_title
    main_window.set_bottom_log_placeholder(bottom_log_widget)
    main_window.set_bottom_panel_stack(bottom_stack, notification_view)
    main_window.set_notification_service(notification_service)

    # ---------- 消息中心接线 ----------
    # 纯新增：上方所有既有信号、弹窗与 toast 行为一律不动，消息中心只做留痕
    from controllers.notification_controller import NotificationController

    notification_controller = NotificationController(
        service=notification_service,
        main_window=main_window,
    )
    notification_view.action_triggered.connect(notification_controller.route)

    # 用例执行完成（用户手动触发；定时任务的收尾走 TaskController，不会重复）
    exec_controller.execution_finished.connect(
        notification_controller.on_execution_finished)
    # 定时任务执行结果 / 未能执行
    task_controller.task_finished.connect(notification_controller.on_task_finished)
    task_controller.task_skipped.connect(notification_controller.on_task_skipped)
    # ADB 长命令失败
    adb_toolbox_controller.pool.command_finished.connect(
        notification_controller.on_adb_command_finished)
    # AI 失败分析完成
    logs_view.ai_analysis_done.connect(notification_controller.on_ai_analyzed)

    # ---------- Weditor ----------
    weditor_svc = WeditorService()
    main_window.set_weditor_service(weditor_svc)

    # ---------- 设备刷新 ----------
    last_devices = []
    # 首次刷新只建立基线，不产生「设备已连接」消息（否则每次启动都会冒一条）
    _device_baseline_ready = False

    def apply_device_list(devices, do_connect=True):
        """设备列表到手之后要做的事（**必须在 GUI 线程**）：更新下拉框/状态栏、
        连设备、同步定时任务状态、给消息中心留痕。

        do_connect=False 专供「启动时后台预扫已经连过设备」这条路径，
        避免同一个设备白连一次（u2.connect 一次要一两秒）。
        """
        nonlocal last_devices, _device_baseline_ready
        if devices == last_devices:
            if not _device_baseline_ready:
                _device_baseline_ready = True
            return

        previous = last_devices
        main_window.update_device_list(devices)
        if devices:
            try:
                if do_connect:
                    device_svc.connect(devices[0])
                if not previous:
                    task_controller.reset_offline_tasks()
            except Exception as e:
                pass
        else:
            task_controller.mark_tasks_offline()
            # 设备断开：通知工具箱清理残留任务
            try:
                adb_toolbox_controller.set_main_running(False)
            except Exception:
                pass
        last_devices = devices
        # 消息中心：设备增删留痕（纯新增，首次刷新只建基线）
        if _device_baseline_ready:
            notification_controller.on_devices_changed(previous, devices)
        else:
            _device_baseline_ready = True

    def refresh_devices():
        """同步刷新设备列表（GUI 线程）：刷新按钮 / 兜底轮询 / track-devices 回调都走这里。

        注意这里是**同步阻塞**的：get_devices() 是一次 adb 子进程，connect() 还要
        一两秒。调用方都是已经显示主界面之后的路径（用户点刷新、5 秒兜底轮询、
        长连接推送变化），不会拖慢启动。
        """
        apply_device_list(device_svc.get_devices())

    main_window.refresh_devices_signal.connect(refresh_devices)
    # ---------- 设备链路初始化：延后到主界面显示之后 + 放到后台线程 ----------
    # 这一段（adb start-server / 查设备 / u2 connect）全是同步阻塞调用：
    #   ensure_adb_server()  —— adb server 被强杀过时要把 daemon 重新拉起来，实测最长十几秒
    #   get_devices()+connect() —— 正常也要 ~2s（推 u2.jar、ping、拉起 uiautomator server）
    # 原来它们堵在 showMaximized() 之前，用户就得多等这么久才看到主界面 ——
    # 实测一次「adb 需冷启动」的启动：全程 13.7s，其中这一段占了 8.5s。
    #
    # 两个「为什么」：
    # 1. 为什么挪到界面显示之后：主界面先出来，设备下拉框晚一两秒填上。主窗口的初始
    #    状态本来就是「未检测到设备 / ○ 未连接设备」，与 update_device_list([]) 完全
    #    一致，所以这段空窗期没有任何视觉倒退。
    # 2. 为什么必须在后台线程：adb server 冷启动那十几秒若堵在 GUI 线程上，刚显示出来
    #    的主窗口会假死，Windows 超过 5 秒就会把它标成「无响应」并置灰，比多等一会儿
    #    还难看。结果一律通过信号投递回 GUI 线程再碰界面。
    #
    # 顺序与延后之前保持一致：start-server 拉稳 -> 查设备 -> 连 u2 -> 最后才挂长连接
    # （server 正在重建时 track-devices 会秒退，见 DeviceWatcher 注释）。
    # ensure_adb_server 那条「先拉稳 server 再查设备」的不变量也保住了：get_devices()
    # 仍排在它后面，只是换了个线程。
    scan_signal = _DeviceScanSignal(main_window)

    # 事件驱动：监听 adb 设备变化（track-devices 长连接）
    device_watcher = DeviceWatcher(get_adb_path())
    # devices_changed 的处理会碰界面、还内含最长 5 秒的 adb 等待，必须回到 GUI 线程；
    # 直接 connect(普通函数) 会在 watcher 线程里同步执行（详见 _UiSlot 注释）
    refresh_bridge = _UiSlot(refresh_devices, main_window)
    device_watcher.devices_changed.connect(refresh_bridge.run)
    # 长连接状态 -> 消息中心留痕。要连在 start() 之前，
    # 否则会漏掉启动后第一次 watcher_status
    device_watcher.watcher_status.connect(
        notification_controller.on_adb_watcher_status)
    # 失败原因（退出码 / stderr）写进 app_debug.log
    device_watcher.link_error.connect(
        lambda msg: logger.warning("track-devices: %s", msg))
    main_window._device_watcher = device_watcher      # 保引用防 GC
    main_window._device_refresh_bridge = refresh_bridge

    # 兜底轮询：常开，不要依赖长连接"断开"来触发。
    #
    # 原实现只在 track-devices 长连接断开时才启用轮询，但「连接没断、只是 adb
    # 没推送变化」这条路径是漏的 —— 表现就是设备已经拔掉，下拉框和状态栏还停在
    # 旧设备 ID 上，一直不更新。所以改成无条件低频轮询：每次只是一次
    # `adb devices` 子进程（几十毫秒，get_devices 内部带 5 秒超时），
    # 换来几秒内 UI 状态一定收敛。长连接仍然是快路径。
    fallback_timer = QTimer()
    fallback_timer.timeout.connect(refresh_devices)

    def _on_startup_scan(devices):
        """后台预扫结果落到界面（GUI 线程），随后才挂上长连接与兜底轮询。"""
        # 设备已经在后台线程里连好了，这里不要再连一次
        apply_device_list(devices, do_connect=False)
        # 长连接排在这里（而不是预扫之前）启动：server 正在重建时 track-devices 会秒退
        device_watcher.start()
        fallback_timer.start(5000)

    # 显式声明排队投递：预扫在后台线程里 emit，这里必须回到 GUI 线程再碰界面
    scan_signal.devices_ready.connect(_on_startup_scan, Qt.ConnectionType.QueuedConnection)

    def _start_device_link():
        """在后台线程里完成 adb server / 设备列表 / u2 连接（主界面显示后由定时器触发）。"""

        def _scan():
            try:
                # 先把 adb server 拉稳再查设备：强杀过 adb 之后第一次调用可能要十几秒，
                # get_devices() 的 timeout=5 会把半启动的 server 子进程杀掉、留下坏状态，
                # 此后 track-devices 每轮都撞上去秒退（详见 ensure_adb_server 的注释）
                ensure_adb_server()
                try:
                    devices = device_svc.get_devices()
                except Exception as e:
                    logger.warning("启动预扫设备失败: %s: %s", type(e).__name__, e)
                    devices = []
                if devices:
                    try:
                        device_svc.connect(devices[0])
                    except Exception as e:
                        logger.warning("启动预连设备失败: %s: %s", type(e).__name__, e)
                scan_signal.devices_ready.emit(devices)
            except Exception as e:
                logger.warning("启动预扫异常: %s: %s", type(e).__name__, e)
                scan_signal.devices_ready.emit([])

        threading.Thread(
            target=_scan, name="device-link-init", daemon=True).start()

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

    # 性能工具卡片：堆转储 / 抓包（入口在性能检测页，能力复用 ADB 工具箱控制器）
    perf_view.hprof_requested.connect(adb_toolbox_controller._action_hprof)
    perf_view.packet_requested.connect(adb_toolbox_controller._action_packet)

    # 消息中心：性能采集完成 / 阈值异常（纯新增）
    perf_controller.perf_finished.connect(notification_controller.on_perf_finished)
    perf_controller.perf_alert.connect(notification_controller.on_perf_alert)

    # ---------- 语音播报 ----------
    from models.voice_model import VoiceModel
    from services.voice_service import get_voice_service
    from views.voice_view import VoiceView

    voice_model = VoiceModel()
    voice_service = get_voice_service()
    # 把持久化的播报配置（音色/语速/音量/输出设备）套到引擎上：
    # 用例里的「语音播报」步骤要用用户调好的这套配置，
    # 不能因为没打开过语音页就退回系统默认音色和扬声器。
    if voice_service.is_available():
        try:
            voice_service.apply_cfg(voice_model.settings)
        except Exception as e:
            print(f"[voice] 套用播报配置失败: {e}")

    # 语音页用的是独立的「语音用例」（voice_data.json），
    # 与自动化编辑页的用例互不影响，所以这里只给它模型和语音服务
    voice_view = VoiceView(model=voice_model, service=voice_service)
    main_window.set_voice_view(voice_view)

    # ---------- 检查更新（启动后台探测 + 菜单手动触发） ----------
    # 三条硬约束：
    # 1. 绝不拖慢启动：延后 4 秒、且在后台 daemon 线程里跑（启动路径刚从 13.7s 优化到 6.3s，
    #    不能再往里面塞同步网络请求）；
    # 2. 失败静默：公司网络访问不到 GitHub 是常态，只在 app_debug.log 里留痕，
    #    不弹框、不打断用户（手动点的时候才给反馈）；
    # 3. 碰界面的动作一律在 GUI 线程做（结果用信号排队投递回来）。
    update_signal = _UpdateCheckSignal(main_window)

    def _check_update(manual=False):
        if not update_check_enabled():
            if manual:
                # 手动点时必须给出可操作的答案，而不是"点了没反应"
                QMessageBox.information(
                    main_window, "检查更新",
                    "还没有配置更新仓库。\n\n"
                    "请把 services/update_service.py 里的 UPDATE_REPO 填成 "
                    "\"owner/repo\"，然后重新打包。")
            return
        if manual:
            show_toast(main_window, "正在检查更新…", duration=1500)
            main_window.set_update_action_enabled(False)

        def _work():
            result = check_for_update()
            update_signal.checked.emit((result, manual))

        threading.Thread(target=_work, name="update-check", daemon=True).start()

    def _on_update_checked(payload):
        result, manual = payload
        main_window.set_update_action_enabled(True)
        if result.status == "update_available":
            # 有新版本就亮红点（不做"忽略此版本"那套 —— 已按需求去掉）
            main_window.set_update_available(True)
            if manual:
                from views.dialogs.update_dialog import (
                    UpdateAvailableDialog, ACTION_DOWNLOAD, ACTION_APPLY)
                action = UpdateAvailableDialog.ask(
                    result.info, main_window,
                    can_install=_can_auto_install,
                    already_staged=bool(_staged_now()))
                if action == ACTION_DOWNLOAD:
                    _download_and_install(result.info)
                elif action == ACTION_APPLY:
                    _apply_staged()
        elif manual:
            if result.status == "up_to_date":
                show_toast(main_window, f"已是最新版本 V{result.current_version}",
                           duration=2500)
            else:
                show_toast(main_window, f"检查更新失败：{result.error}", duration=3000)

    # ---------- 自动更新：下载 -> 确认 -> 交给独立更新器 ----------
    # 更新器是安装目录外的一个独立进程（updater.exe，随更新包分发），
    # 因为 Windows 下正在运行的 exe 和已映射的 DLL 既不能删也不能覆盖 —— 只能由
    # "另一个进程"在我们退出后动手。它会把 _internal 与 exe 换掉、保留 data/，失败能回滚。
    _can_auto_install = bool(getattr(sys, "frozen", False))   # 开发模式没有"安装目录"，不提供

    def _staged_now() -> str:
        """上次已下载解压好、但用户点了「稍后」的更新包。"""
        try:
            return pending_staged()
        except Exception as e:
            logger.warning("检查暂存更新失败: %s: %s", type(e).__name__, e)
            return ""

    def _launch_updater_and_quit(staging: str) -> bool:
        updater = updater_exe_in(staging)
        if not os.path.isfile(updater):
            ErrorDialog.show_error(main_window, "更新失败",
                                   f"更新器不存在：\n{updater}")
            return False
        install_dir = get_app_dir()
        args = ["--src", staging, "--dst", install_dir, "--pid", str(os.getpid())]
        logger.info("启动更新器：%s %s", updater, args)
        if not QProcess.startDetached(updater, args, install_dir):
            ErrorDialog.show_error(main_window, "更新失败",
                                   f"无法启动更新器：\n{updater}")
            return False
        # 退出自己：更新器会等这个进程真正结束再动文件
        QTimer.singleShot(300, app.quit)
        return True

    def _confirm_and_apply(staging: str, version: str) -> None:
        ans = QMessageBox.question(
            main_window, "更新已就绪",
            f"V{version} 已准备完成。\n\n"
            f"现在重启虫师完成更新？（重启过程几秒，你的项目与用例数据不受影响）",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes)
        if ans != QMessageBox.StandardButton.Yes:
            show_toast(main_window,
                       "更新包已保留，下次点「检查更新」可直接重启生效",
                       duration=4000)
            return
        _launch_updater_and_quit(staging)

    def _apply_staged() -> None:
        staging = _staged_now()
        if not staging:
            show_toast(main_window, "没有已下载好的更新包", duration=2500)
            return
        version = os.path.basename(staging)
        _confirm_and_apply(staging, version)

    def _download_and_install(info) -> None:
        from views.dialogs.update_progress_dialog import UpdateProgressDialog
        dlg = UpdateProgressDialog(info, main_window)
        if not dlg.exec_and_prepare():
            if dlg.error:
                ErrorDialog.show_error(
                    main_window, "更新失败",
                    f"{dlg.error}\n\n你也可以点「打开 Release 页面」手动下载。")
            return
        _confirm_and_apply(dlg.staging, info.version)

    # 显式排队投递：检查在后台线程里 emit，这里必须回到 GUI 线程再碰界面
    update_signal.checked.connect(_on_update_checked, Qt.ConnectionType.QueuedConnection)
    main_window.check_update_requested.connect(lambda: _check_update(manual=True))

    # ---------- 启动 ----------
    splash_update("正在准备主界面…")
    main_window.showMaximized()
    # 主界面已经出来，封面使命完成。延 150ms 再关：让 Qt 先把首帧画出来，
    # 否则会先露出一瞬间的白屏（封面是 always_on_top，压在主窗口上面直到这里关掉）。
    QTimer.singleShot(150, splash_close)
    # 设备链路（adb server / 设备列表 / u2 连接）等界面先显示出来再在后台线程里做，
    # 别让它堵在进入主界面的路上 —— 详见 _start_device_link 上方的注释
    QTimer.singleShot(250, _start_device_link)
    # 检查更新排在最晚：既不阻塞启动，也不跟设备初始化抢网络/CPU
    QTimer.singleShot(4000, lambda: _check_update(manual=False))
    # 清掉上次更新留下的暂存残留（正在等重启生效的那一份要保留）；
    # 删目录是纯 IO，放后台线程，别在 GUI 线程里等
    QTimer.singleShot(7000, lambda: threading.Thread(
        target=lambda: cleanup_staging(_staged_now()),
        name="update-staging-cleanup", daemon=True).start())
    _startup_notes = []
    if _dup_id_fixes:
        _startup_notes.append(f"修复 {len(_dup_id_fixes)} 个 id 重复的节点")
    if _gc_stat['steps'] or _gc_stat['keys'] or _gc_stat['suite_refs']:
        _freed = _gc_stat['freed']
        _freed_text = (f"{_freed / 1024 / 1024:.1f} MB" if _freed >= 1024 * 1024
                       else f"{_freed / 1024:.0f} KB")
        _startup_notes.append(f"回收 {_gc_stat['steps']} 个无用步骤（省下 {_freed_text}）")
    if _startup_notes:
        show_toast(
            main_window,
            "启动自愈：" + "；".join(_startup_notes) + "（详见 app_debug.log）",
            duration=5000,
        )
    sys.exit(app.exec())


if __name__ == "__main__":
    main()