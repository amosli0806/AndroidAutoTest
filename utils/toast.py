# utils/toast.py
from PyQt6.QtWidgets import QWidget, QLabel, QVBoxLayout, QApplication, QMainWindow
from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, QPoint

_current_toast = None

class Toast(QWidget):
    """轻提示 Toast，底部居中，带滑入/滑出动画"""

    def __init__(self, parent=None, message="", duration=2000):
        super().__init__(parent)
        self.parent = parent
        self.duration = duration
        self.message = message
        self._is_closing = False

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self.setFixedWidth(450)
        self.setFixedHeight(80)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 12, 20, 12)

        self.label = QLabel(message, self)
        self.label.setStyleSheet("""
            QLabel {
                color: white;
                font-size: 14px;
                font-weight: 500;
                background-color: rgba(0, 0, 0, 0.85);
                border-radius: 8px;
                padding: 10px 20px;
            }
        """)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setWordWrap(True)
        layout.addWidget(self.label)

        self.adjustSize()

        if parent:
            parent_rect = parent.rect()
            parent_center = parent.mapToGlobal(parent_rect.center())
            self.target_x = parent_center.x() - self.width() // 2
            self.target_y = parent.mapToGlobal(parent_rect.bottomRight()).y() - self.height() - 50
        else:
            screen = QApplication.primaryScreen().geometry()
            self.target_x = screen.center().x() - self.width() // 2
            self.target_y = screen.bottom() - self.height() - 50

        self.move(self.target_x, self.target_y + 100)
        self.setWindowOpacity(0.0)

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

        self.slide_in.start()
        self.fade_in.start()

        self.show()

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


def show_toast(parent=None, message="", duration=2000):
    global _current_toast
    close_current_toast()
    # 兼容旧调用：如果 parent 是字符串，说明只传了消息，parent 应为 None
    if isinstance(parent, str):
        message = parent
        parent = None
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

    _current_toast = Toast(parent, message, duration)