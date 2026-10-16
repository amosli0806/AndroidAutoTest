# utils/dialogs.py
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                             QPushButton, QFrame, QLineEdit, QGraphicsDropShadowEffect,
                             QScrollArea, QWidget)
from PyQt6.QtCore import Qt, QPoint, QObject, QEvent, QRect
from PyQt6.QtGui import QPixmap, QPainter, QColor, QPen, QBrush, QFont
from utils.theme import Theme, ThemeMode
from utils.settings import Settings, THEME_MODE_DARK


# 对话框按钮的统一尺寸。
# 为什么从 100×34 放大到 120×42：实测（2026-10-16）用户点「确定」的落点常偏下 1~2px
# —— 鼠标箭头的热点在左上角、视觉箭身偏右下，而按钮只有 34 高、下方就是空白，
# 结果点在按钮外的空白区、什么都没发生，看着像"点了没反应、要点好几下"。
# 放大后同样的落点会落在按钮内，容错余量从 ~0 提到 8px 以上。
DIALOG_BTN_W = 120
DIALOG_BTN_H = 42


class _ButtonHotZone(QObject):
    """给「唯一动作」按钮外加一圈热区：点在按钮外一点点，也算点中它。

    为什么需要：鼠标箭头的热点在左上角、视觉箭身偏右下，人手瞄按钮时落点常偏下几像素
    —— 实测（2026-10-16，用户复现 + 埋点日志）点「确定」的落点稳定偏下 2~4px，落在按钮
    外的容器空白上，什么都没发生，看着就像"点了没反应、要点好几下"；光标也因此没进按钮，
    连悬停色都不亮。Qt 的命中测试按控件矩形走，点在外面的事件根本到不了按钮，所以在容器
    上装过滤器把这一圈补回来。

    **只给只有一个按钮的提示弹窗用**（确定/关闭这类唯一动作），有"取消/确定"两个按钮的
    弹窗不装 —— 免得瞄「取消」偏一点反而触发了「确定」。
    """

    MARGIN = 14

    def __init__(self, container: QWidget, button: QPushButton, margin: int = MARGIN):
        super().__init__(container)          # 挂在容器上，随弹窗一起销毁
        self._container = container
        self._button = button
        self._margin = margin
        container.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() != QEvent.Type.MouseButtonPress:
            return False
        if event.button() != Qt.MouseButton.LeftButton or not self._button.isEnabled():
            return False
        pos = event.position().toPoint()     # 容器坐标系
        rect = QRect(self._button.mapTo(self._container, QPoint(0, 0)),
                     self._button.size())
        if rect.contains(pos):
            return False                     # 在按钮上，交给按钮自己处理
        if rect.adjusted(-self._margin, -self._margin,
                         self._margin, self._margin).contains(pos):
            self._button.click()             # 落在按钮外的热区里 → 等价于点中按钮
            return True
        return False


class ConfirmDeleteDialog(QDialog):
    """美观的删除确认对话框（自适应高度 + 大阴影）"""

    def __init__(self, parent, title="确认删除", message="确定要删除吗？", detail=None):
        super().__init__(parent)
        self.setObjectName("ConfirmDeleteDialog")
        self.setWindowTitle(title)
        self.setModal(True)
        self.setFixedWidth(440)
        self.setMinimumHeight(180)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)   # 为阴影预留空间
        main_layout.setSpacing(0)

        container = QFrame()
        container.setObjectName("container")
        # 背景色交由 apply_theme 里动态设置（避免硬编码 white 覆盖夜间主题）
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(20)
        shadow.setOffset(0, 0)
        shadow.setColor(QColor(0, 0, 0, 80))
        container.setGraphicsEffect(shadow)

        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(24, 20, 24, 20)
        container_layout.setSpacing(16)

        # 标题行（图标 + 标题）
        title_layout = QHBoxLayout()
        title_layout.setSpacing(12)
        icon_label = QLabel()
        icon_pixmap = QPixmap(32, 32)
        icon_pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(icon_pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QBrush(QColor(237, 108, 108)))  # 红色警告三角形
        painter.setPen(Qt.PenStyle.NoPen)
        points = [QPoint(16, 2), QPoint(2, 28), QPoint(30, 28)]
        painter.drawPolygon(*points)
        painter.setPen(QPen(QColor(255, 255, 255), 3))
        painter.drawLine(16, 10, 16, 20)
        painter.drawLine(16, 24, 16, 26)
        painter.end()
        icon_label.setPixmap(icon_pixmap)
        title_layout.addWidget(icon_label)

        title_label = QLabel(title)
        title_label.setStyleSheet("font-size: 16px; font-weight: bold;")
        title_label.setWordWrap(True)
        title_layout.addWidget(title_label, 1)
        container_layout.addLayout(title_layout)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("background-color: #e0e0e0; max-height: 1px;")
        container_layout.addWidget(line)

        msg_layout = QVBoxLayout()
        msg_layout.setSpacing(6)
        msg_label = QLabel(message)
        msg_label.setStyleSheet("font-size: 14px;")
        msg_label.setWordWrap(True)
        msg_layout.addWidget(msg_label)
        if detail:
            detail_label = QLabel(detail)
            detail_label.setWordWrap(True)
            detail_label.setStyleSheet(
                "font-size: 13px; color: #888; background: transparent;"
            )
            # 明细放进无框滚动区：内容少时原样展示，内容多时限制高度滚动查看，
            # 避免整窗超出屏幕
            detail_scroll = QScrollArea()
            detail_scroll.setWidgetResizable(True)
            detail_scroll.setFrameShape(QFrame.Shape.NoFrame)
            detail_scroll.setStyleSheet(
                "QScrollArea { background: transparent; border: none; }"
            )
            detail_scroll.viewport().setAutoFillBackground(False)
            detail_scroll.setMaximumHeight(200)
            detail_scroll.setWidget(detail_label)
            msg_layout.addWidget(detail_scroll)
        container_layout.addLayout(msg_layout)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)
        cancel_btn = QPushButton("取消")
        cancel_btn.setObjectName("cancelBtn")
        cancel_btn.setFixedSize(DIALOG_BTN_W, DIALOG_BTN_H)
        cancel_btn.clicked.connect(self.reject)

        confirm_btn = QPushButton("确定删除")
        confirm_btn.setObjectName("confirmBtn")
        confirm_btn.setFixedSize(DIALOG_BTN_W, DIALOG_BTN_H)
        confirm_btn.clicked.connect(self.accept)

        btn_layout.addStretch()
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(confirm_btn)
        container_layout.addLayout(btn_layout)

        main_layout.addWidget(container)

        # 应用主题
        self.apply_theme()

    def apply_theme(self, theme_mode: ThemeMode = None):
        """应用主题到对话框"""
        if theme_mode is None:
            theme_mode = ThemeMode.DARK if Settings.get_theme_mode() == THEME_MODE_DARK else ThemeMode.LIGHT
        Theme.apply_theme_to_widget(self, theme_mode)

    def showEvent(self, event):
        """出现时缩放淡入（只动 opacity + geometry，不触发布局）"""
        super().showEvent(event)
        try:
            from utils.fx import scale_in
            scale_in(self)
        except Exception:
            pass

    @staticmethod
    def ask(parent, title="确认删除", message="确定要删除吗？", detail=None):
        dialog = ConfirmDeleteDialog(parent, title, message, detail)
        return dialog.exec() == QDialog.DialogCode.Accepted


class InputDialog(QDialog):
    """美观的输入对话框（大阴影）"""

    def __init__(self, parent, title="输入", label="请输入名称:", default_text="", placeholder=""):
        super().__init__(parent)
        self.setObjectName("InputDialog")
        self.setWindowTitle(title)
        self.setModal(True)
        self.setFixedSize(420, 210)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(0)

        container = QFrame()
        container.setObjectName("container")
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(20)
        shadow.setOffset(0, 0)
        shadow.setColor(QColor(0, 0, 0, 80))
        container.setGraphicsEffect(shadow)

        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(24, 20, 24, 20)
        container_layout.setSpacing(16)

        title_label = QLabel(title)
        title_label.setStyleSheet("font-size: 16px; font-weight: bold;")
        container_layout.addWidget(title_label)

        label_widget = QLabel(label)
        label_widget.setStyleSheet("font-size: 14px;")
        container_layout.addWidget(label_widget)

        self.input_edit = QLineEdit(default_text)
        self.input_edit.setPlaceholderText(placeholder)
        self.input_edit.setFocus()
        self.input_edit.selectAll()
        container_layout.addWidget(self.input_edit)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)
        cancel_btn = QPushButton("取消")
        cancel_btn.setObjectName("cancelBtn")
        cancel_btn.setFixedSize(DIALOG_BTN_W, DIALOG_BTN_H)
        cancel_btn.clicked.connect(self.reject)

        confirm_btn = QPushButton("确定")
        confirm_btn.setObjectName("confirmBtn")
        confirm_btn.setFixedSize(DIALOG_BTN_W, DIALOG_BTN_H)
        confirm_btn.clicked.connect(self.accept)

        btn_layout.addStretch()
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(confirm_btn)
        container_layout.addLayout(btn_layout)

        main_layout.addWidget(container)

        self.input_edit.returnPressed.connect(self.accept)

        # 应用主题
        self.apply_theme()

    def apply_theme(self, theme_mode: ThemeMode = None):
        """应用主题到对话框"""
        if theme_mode is None:
            theme_mode = ThemeMode.DARK if Settings.get_theme_mode() == THEME_MODE_DARK else ThemeMode.LIGHT
        Theme.apply_theme_to_widget(self, theme_mode)

    def showEvent(self, event):
        super().showEvent(event)
        try:
            from utils.fx import scale_in
            scale_in(self)
        except Exception:
            pass

    @staticmethod
    def get_text(parent, title="输入", label="请输入名称:", default_text="", placeholder=""):
        dialog = InputDialog(parent, title, label, default_text, placeholder)
        result = dialog.exec()
        if result == QDialog.DialogCode.Accepted:
            return dialog.input_edit.text().strip(), True
        return "", False


class WarningDialog(QDialog):
    """美观的警告提示对话框（仅确定按钮 + 大阴影）"""

    def __init__(self, parent, title="提示", message="", detail=None):
        super().__init__(parent)
        self.setObjectName("WarningDialog")
        self.setWindowTitle(title)
        self.setModal(True)
        self.setFixedWidth(420)
        self.setMinimumHeight(160)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(0)

        container = QFrame()
        container.setObjectName("container")
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(20)
        shadow.setOffset(0, 0)
        shadow.setColor(QColor(0, 0, 0, 80))
        container.setGraphicsEffect(shadow)

        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(24, 20, 24, 20)
        container_layout.setSpacing(16)

        # 标题行（警告图标）
        title_layout = QHBoxLayout()
        title_layout.setSpacing(12)
        icon_label = QLabel()
        icon_pixmap = QPixmap(32, 32)
        icon_pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(icon_pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QBrush(QColor(241, 196, 15)))  # 黄色警告
        painter.setPen(Qt.PenStyle.NoPen)
        points = [QPoint(16, 2), QPoint(2, 28), QPoint(30, 28)]
        painter.drawPolygon(*points)
        painter.setPen(QPen(QColor(255, 255, 255), 3))
        painter.drawLine(16, 10, 16, 20)
        painter.drawLine(16, 24, 16, 26)
        painter.end()
        icon_label.setPixmap(icon_pixmap)
        title_layout.addWidget(icon_label)

        title_label = QLabel(title)
        title_label.setStyleSheet("font-size: 16px; font-weight: bold;")
        title_label.setWordWrap(True)
        title_layout.addWidget(title_label, 1)
        container_layout.addLayout(title_layout)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("background-color: #e0e0e0; max-height: 1px;")
        container_layout.addWidget(line)

        msg_layout = QVBoxLayout()
        msg_layout.setSpacing(6)
        msg_label = QLabel(message)
        msg_label.setStyleSheet("font-size: 14px;")
        msg_label.setWordWrap(True)
        msg_layout.addWidget(msg_label)
        if detail:
            detail_label = QLabel(detail)
            detail_label.setWordWrap(True)
            detail_label.setStyleSheet(
                "font-size: 13px; color: #888; background: transparent;"
            )
            # 明细放进无框滚动区：内容少时原样展示，内容多时限制高度滚动查看，
            # 避免整窗超出屏幕
            detail_scroll = QScrollArea()
            detail_scroll.setWidgetResizable(True)
            detail_scroll.setFrameShape(QFrame.Shape.NoFrame)
            detail_scroll.setStyleSheet(
                "QScrollArea { background: transparent; border: none; }"
            )
            detail_scroll.viewport().setAutoFillBackground(False)
            detail_scroll.setMaximumHeight(200)
            detail_scroll.setWidget(detail_label)
            msg_layout.addWidget(detail_scroll)
        container_layout.addLayout(msg_layout)

        btn_layout = QHBoxLayout()
        ok_btn = QPushButton("确定")
        ok_btn.setObjectName("okBtn")
        ok_btn.setFixedSize(DIALOG_BTN_W, DIALOG_BTN_H)
        ok_btn.clicked.connect(self.accept)
        btn_layout.addStretch()
        btn_layout.addWidget(ok_btn)
        container_layout.addLayout(btn_layout)
        # 唯一动作的按钮：外面再套一圈热区，点偏几像素也算点中（见 _ButtonHotZone）
        _ButtonHotZone(container, ok_btn)

        main_layout.addWidget(container)

        # 应用主题
        self.apply_theme()

    def apply_theme(self, theme_mode: ThemeMode = None):
        """应用主题到对话框"""
        if theme_mode is None:
            theme_mode = ThemeMode.DARK if Settings.get_theme_mode() == THEME_MODE_DARK else ThemeMode.LIGHT
        Theme.apply_theme_to_widget(self, theme_mode)

    def showEvent(self, event):
        super().showEvent(event)
        try:
            from utils.fx import scale_in
            scale_in(self)
        except Exception:
            pass

    @staticmethod
    def show_warning(parent, title="提示", message="", detail=None):
        dialog = WarningDialog(parent, title, message, detail)
        dialog.exec()


class ErrorDialog(QDialog):
    """美观的错误提示对话框（仅确定按钮 + 大阴影，红色图标）"""

    def __init__(self, parent, title="错误", message="", detail=None):
        super().__init__(parent)
        self.setObjectName("ErrorDialog")
        self.setWindowTitle(title)
        self.setModal(True)
        self.setFixedWidth(420)
        self.setMinimumHeight(160)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(0)

        container = QFrame()
        container.setObjectName("container")
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(20)
        shadow.setOffset(0, 0)
        shadow.setColor(QColor(0, 0, 0, 80))
        container.setGraphicsEffect(shadow)

        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(24, 20, 24, 20)
        container_layout.setSpacing(16)

        # 标题行（红色错误图标）
        title_layout = QHBoxLayout()
        title_layout.setSpacing(12)
        icon_label = QLabel()
        icon_pixmap = QPixmap(32, 32)
        icon_pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(icon_pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QBrush(QColor(237, 108, 108)))  # 红色
        painter.setPen(Qt.PenStyle.NoPen)
        points = [QPoint(16, 2), QPoint(2, 28), QPoint(30, 28)]
        painter.drawPolygon(*points)
        painter.setPen(QPen(QColor(255, 255, 255), 3))
        painter.drawLine(16, 10, 16, 20)
        painter.drawLine(16, 24, 16, 26)
        painter.end()
        icon_label.setPixmap(icon_pixmap)
        title_layout.addWidget(icon_label)

        title_label = QLabel(title)
        title_label.setStyleSheet("font-size: 16px; font-weight: bold;")
        title_label.setWordWrap(True)
        title_layout.addWidget(title_label, 1)
        container_layout.addLayout(title_layout)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("background-color: #e0e0e0; max-height: 1px;")
        container_layout.addWidget(line)

        msg_layout = QVBoxLayout()
        msg_layout.setSpacing(6)
        msg_label = QLabel(message)
        msg_label.setStyleSheet("font-size: 14px;")
        msg_label.setWordWrap(True)
        msg_layout.addWidget(msg_label)
        if detail:
            detail_label = QLabel(detail)
            detail_label.setWordWrap(True)
            detail_label.setStyleSheet(
                "font-size: 13px; color: #888; background: transparent;"
            )
            # 明细放进无框滚动区：内容少时原样展示，内容多时限制高度滚动查看，
            # 避免整窗超出屏幕
            detail_scroll = QScrollArea()
            detail_scroll.setWidgetResizable(True)
            detail_scroll.setFrameShape(QFrame.Shape.NoFrame)
            detail_scroll.setStyleSheet(
                "QScrollArea { background: transparent; border: none; }"
            )
            detail_scroll.viewport().setAutoFillBackground(False)
            detail_scroll.setMaximumHeight(200)
            detail_scroll.setWidget(detail_label)
            msg_layout.addWidget(detail_scroll)
        container_layout.addLayout(msg_layout)

        btn_layout = QHBoxLayout()
        ok_btn = QPushButton("确定")
        ok_btn.setObjectName("okBtn")
        ok_btn.setFixedSize(DIALOG_BTN_W, DIALOG_BTN_H)
        ok_btn.clicked.connect(self.accept)
        btn_layout.addStretch()
        btn_layout.addWidget(ok_btn)
        container_layout.addLayout(btn_layout)
        # 唯一动作的按钮：外面再套一圈热区，点偏几像素也算点中（见 _ButtonHotZone）
        _ButtonHotZone(container, ok_btn)

        main_layout.addWidget(container)
        self.container = container

        # 应用主题
        self.apply_theme()

    def apply_theme(self, theme_mode: ThemeMode = None):
        """应用主题到对话框"""
        if theme_mode is None:
            theme_mode = ThemeMode.DARK if Settings.get_theme_mode() == THEME_MODE_DARK else ThemeMode.LIGHT
        Theme.apply_theme_to_widget(self, theme_mode)

    def showEvent(self, event):
        super().showEvent(event)
        try:
            from utils.fx import scale_in
            scale_in(self)
        except Exception:
            pass

    @staticmethod
    def show_error(parent, title="错误", message="", detail=None):
        """静态方法：弹出错误对话框，等待用户点击确定"""
        dialog = ErrorDialog(parent, title, message, detail)
        dialog.exec()

class QuestionDialog(QDialog):
    """通用的是/否询问对话框，风格与项目其它对话框一致，跟随主题。"""

    def __init__(self, parent, title="请选择", message="", detail=None,
                 yes_text="是", no_text="否", default_yes=True):
        super().__init__(parent)
        self.setObjectName("QuestionDialog")
        self.setWindowTitle(title)
        self.setModal(True)
        self.setFixedWidth(440)
        self.setMinimumHeight(180)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._result_yes = default_yes

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(0)

        container = QFrame()
        container.setObjectName("container")
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(20)
        shadow.setOffset(0, 0)
        shadow.setColor(QColor(0, 0, 0, 80))
        container.setGraphicsEffect(shadow)

        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(24, 20, 24, 20)
        container_layout.setSpacing(16)

        # 标题行（蓝色问号图标）
        title_layout = QHBoxLayout()
        title_layout.setSpacing(12)
        icon_label = QLabel()
        icon_pixmap = QPixmap(32, 32)
        icon_pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(icon_pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QBrush(QColor(52, 152, 219)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(2, 2, 28, 28)
        painter.setPen(QPen(QColor(255, 255, 255), 3))
        painter.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        painter.drawText(icon_pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "?")
        painter.end()
        icon_label.setPixmap(icon_pixmap)
        title_layout.addWidget(icon_label)

        title_label = QLabel(title)
        title_label.setStyleSheet("font-size: 16px; font-weight: bold;")
        title_label.setWordWrap(True)
        title_layout.addWidget(title_label, 1)
        container_layout.addLayout(title_layout)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("background-color: #e0e0e0; max-height: 1px;")
        container_layout.addWidget(line)

        msg_layout = QVBoxLayout()
        msg_layout.setSpacing(6)
        msg_label = QLabel(message)
        msg_label.setStyleSheet("font-size: 14px;")
        msg_label.setWordWrap(True)
        msg_layout.addWidget(msg_label)
        if detail:
            detail_label = QLabel(detail)
            detail_label.setWordWrap(True)
            detail_label.setStyleSheet(
                "font-size: 13px; color: #888; background: transparent;"
            )
            # 明细放进无框滚动区：内容少时原样展示，内容多时限制高度滚动查看，
            # 避免整窗超出屏幕
            detail_scroll = QScrollArea()
            detail_scroll.setWidgetResizable(True)
            detail_scroll.setFrameShape(QFrame.Shape.NoFrame)
            detail_scroll.setStyleSheet(
                "QScrollArea { background: transparent; border: none; }"
            )
            detail_scroll.viewport().setAutoFillBackground(False)
            detail_scroll.setMaximumHeight(200)
            detail_scroll.setWidget(detail_label)
            msg_layout.addWidget(detail_scroll)
        container_layout.addLayout(msg_layout)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)

        no_btn = QPushButton(no_text)
        no_btn.setObjectName("cancelBtn")
        no_btn.setFixedSize(DIALOG_BTN_W, DIALOG_BTN_H)
        no_btn.clicked.connect(self._on_no)

        yes_btn = QPushButton(yes_text)
        yes_btn.setObjectName("confirmBtn")
        yes_btn.setFixedSize(DIALOG_BTN_W, DIALOG_BTN_H)
        yes_btn.clicked.connect(self._on_yes)

        btn_layout.addStretch()
        btn_layout.addWidget(no_btn)
        btn_layout.addWidget(yes_btn)
        container_layout.addLayout(btn_layout)

        main_layout.addWidget(container)

        self.apply_theme()

    def _on_yes(self):
        self._result_yes = True
        self.accept()

    def _on_no(self):
        self._result_yes = False
        self.reject()

    def apply_theme(self, theme_mode: ThemeMode = None):
        if theme_mode is None:
            theme_mode = ThemeMode.DARK if Settings.get_theme_mode() == THEME_MODE_DARK else ThemeMode.LIGHT
        Theme.apply_theme_to_widget(self, theme_mode)

    def showEvent(self, event):
        super().showEvent(event)
        try:
            from utils.fx import scale_in
            scale_in(self)
        except Exception:
            pass

    @staticmethod
    def ask(parent, title="请选择", message="", detail=None,
            yes_text="是", no_text="否", default_yes=True):
        dlg = QuestionDialog(parent, title, message, detail,
                             yes_text, no_text, default_yes)
        dlg.exec()
        return dlg._result_yes