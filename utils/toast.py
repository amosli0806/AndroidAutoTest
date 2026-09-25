# utils/toast.py
"""轻提示 Toast。

线程安全：show_toast 可以从**任意线程**调用。QWidget 只能在 GUI 线程创建，
在 worker 线程（QThread/线程池）里直接创建 Toast 会产生未定义行为——
窗口标志失效（弹标题栏）、透明背景失效（白底）、样式错乱（2026-09-25 用户
实测：设备插拔时闪白色横条窗、标题栏显示 "python"）。
非 GUI 线程调用时会自动经信号队列投递回 GUI 线程执行。
"""
from PyQt6.QtWidgets import QWidget, QLabel, QVBoxLayout, QApplication, QMainWindow
from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, QPoint, QObject, pyqtSignal, QThread

_current_toast = None


class _ToastBridge(QObject):
    """跨线程 toast 投递桥：信号在创建它的线程（GUI）上接收，队列投递保证
    worker 线程的调用最终在 GUI 线程里真正创建 Toast。"""
    _show_requested = pyqtSignal(str, int)

    def __init__(self):
        super().__init__()
        self._show_requested.connect(self._on_show)

    def _on_show(self, message, duration):
        _create_toast(None, message, duration)   # GUI 线程：定位交给顶层窗口


_bridge = None


def _get_bridge():
    global _bridge
    if _bridge is None:
        _bridge = _ToastBridge()
    return _bridge


def _on_gui_thread():
    app = QApplication.instance()
    return app is not None and QThread.currentThread() is app.thread()


def show_toast(parent=None, message="", duration=2000):
    global _current_toast
    close_current_toast()
    # 兼容旧调用：如果 parent 是字符串，说明只传了消息，parent 应为 None
    if isinstance(parent, str):
        message = parent
        parent = None

    # 线程安全闸门：非 GUI 线程调用时，parent 引用跨线程传递不可靠（可能已销毁），
    # 统一转投 GUI 线程、以顶层窗口为基准定位（toast 消息本身才是重点）。
    if not _on_gui_thread():
        _get_bridge()._show_requested.emit(message, duration)
        return
    _create_toast(parent, message, duration)


def _create_toast(parent, message, duration):
    global _current_toast
    if parent is None:
        parent = QApplication.activeWindow()
    if parent is None:
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, QMainWindow):
                parent = widget
                break

    # 传入的父控件若当前不可见，通常是一个还没显示过的功能页：它还没被布局撑开
    # （尺寸仍是 640x480 之类的默认值），坐标也还是旧的，以它为基准算出来的
    # toast 位置会飘到界面上奇怪的地方。这种情况改挂到顶层窗口上，落点才稳定。
    # 注意只在"不可见"时才改，可见父控件的定位结果保持不变。
    if parent is not None and not parent.isVisible():
        top_level = parent.window()
        if top_level is not None and top_level.isVisible():
            parent = top_level

    # **归一到顶层窗口 + 作为子控件浮层**（不再创建独立顶层窗口）：
    # Windows + PyQt6.11 下，独立 Tool 窗口即使设了 FramelessWindowHint，
    # 窗口管理器仍会给它套标题栏（标题显示进程名 "python"，用户实测）。
    # 改成主窗口的子控件浮层后：无标题栏、不进任务栏、不闪独立窗，
    # 且随主窗口移动/最小化，行为与系统 toast 语义一致。
    if parent is not None:
        parent = parent.window()

    _current_toast = Toast(parent, message, duration)


class Toast(QWidget):
    """轻提示 Toast，主窗口内底部居中浮层，带滑入/滑出动画。

    定位用 parent 的局部坐标（不再 mapToGlobal 到屏幕）——因为 Toast 现在是
    主窗口的**子控件**，坐标天然跟随主窗口，窗口管理器完全不经手。
    """

    def __init__(self, parent=None, message="", duration=2000):
        super().__init__(parent)
        self.parent = parent
        self.duration = duration
        self.message = message
        self._is_closing = False

        # 作为主窗口内的子控件浮层：明确普通 Widget 类型（绝不做顶层窗口）
        self.setWindowFlags(Qt.WindowType.Widget)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self.setFixedWidth(450)
        self.setFixedHeight(80)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.label = QLabel(message, self)
        self.label.setObjectName("ToastLabel")
        self.label.setStyleSheet("""
            QLabel {
                color: white;
                font-size: 14px;
                font-weight: 500;
                background-color: rgba(30, 30, 30, 0.92);
                border-radius: 8px;
                padding: 10px 20px;
            }
        """)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setWordWrap(True)
        layout.addWidget(self.label)

        self.adjustSize()

        # 定位：parent 局部坐标，底部居中上浮 50px（Toast 现为 parent 的子控件）
        if parent is not None:
            parent_rect = parent.rect()
            self.target_x = (parent_rect.width() - self.width()) // 2
            self.target_y = parent_rect.height() - self.height() - 50
        else:
            screen = QApplication.primaryScreen().geometry()
            self.target_x = screen.center().x() - self.width() // 2
            self.target_y = screen.bottom() - self.height() - 50

        self.move(self.target_x, self.target_y + 100)
        self.setWindowOpacity(1.0)
        self.raise_()   # 置于主窗口内所有控件之上

        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._start_fade_out)
        self.timer.start(duration)

        self.slide_in = QPropertyAnimation(self, b"pos")
        self.slide_in.setDuration(300)
        self.slide_in.setStartValue(self.pos())
        self.slide_in.setEndValue(QPoint(self.target_x, self.target_y))
        self.slide_in.setEasingCurve(QEasingCurve.Type.OutCubic)

        self.fade_in = QPropertyAnimation(self, b"windowOpacity")
        self.fade_in.setDuration(300)
        self.fade_in.setStartValue(0.0)
        self.fade_in.setEndValue(1.0)
        self.fade_in.setEasingCurve(QEasingCurve.Type.OutQuad)

        self.slide_out = QPropertyAnimation(self, b"pos")
        self.slide_out.setDuration(300)
        self.slide_out.setStartValue(QPoint(self.target_x, self.target_y))
        self.slide_out.setEndValue(QPoint(self.target_x, self.target_y + 100))
        self.slide_out.setEasingCurve(QEasingCurve.Type.InCubic)

        self.fade_out = QPropertyAnimation(self, b"windowOpacity")
        self.fade_out.setDuration(300)
        self.fade_out.setStartValue(1.0)
        self.fade_out.setEndValue(0.0)
        self.fade_out.setEasingCurve(QEasingCurve.Type.OutQuad)

        self.slide_out.finished.connect(self._safe_close)

        self.show()
        self.raise_()
        self.fade_in.start()

    def _start_fade_out(self):
        if not self._is_closing:
            self.fade_out.start()
            self.slide_out.start()

    def _safe_close(self):
        if not self._is_closing:
            self._is_closing = True
            self.close()

    def closeEvent(self, event):
        global _current_toast
        if _current_toast is self:
            _current_toast = None
        self.timer.stop()
        for anim in [self.slide_in, self.fade_in, self.slide_out, self.fade_out]:
            if anim and anim.state() == QPropertyAnimation.State.Running:
                anim.stop()
        super().closeEvent(event)


def close_current_toast():
    global _current_toast
    if _current_toast is not None and not _current_toast._is_closing:
        _current_toast.timer.stop()
        for anim in [_current_toast.slide_in, _current_toast.fade_in,
                     _current_toast.slide_out, _current_toast.fade_out]:
            if anim and anim.state() == QPropertyAnimation.State.Running:
                anim.stop()
        _current_toast.close()
        _current_toast = None
