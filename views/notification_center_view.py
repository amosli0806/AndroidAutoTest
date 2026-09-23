# views/notification_center_view.py
"""消息中心面板：作为底部面板的一个 kind，与 log / crash / anr / device_info 同级。

与「虫师日志」的边界见 models/notification_model.py 的文件头说明。这里刻意
**不复用** QTextEdit + utils.log_colors 那套 HTML 行内染色 —— 「未读态 / 右键
菜单 / 动态动作按钮 / 单击跳转」这些语义在纯文本控件里无处安放。

本视图只负责展示与分发：动作以数据形式通过 action_triggered 抛给外部路由
（controllers/notification_controller.py），自身不做任何页面切换。
"""
from typing import List

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QMenu, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

from models.notification_model import Notification
from utils.theme import ThemeMode


class _MessageRow(QFrame):
    """一行消息：级别圆点 + 时间 + 标题/详情 + 动态动作按钮。"""

    activated = pyqtSignal(dict)      # 要执行的 action dict
    read_requested = pyqtSignal(int)  # 请求把这条标为已读（「忽略」动作 / 右键菜单）

    def __init__(self, item: Notification, parent=None):
        super().__init__(parent)
        self.item = item
        self.setObjectName("MessageRow")
        # 未读态通过动态属性 + QSS 表达，避免每行各自 setStyleSheet
        self.setProperty("unread", "true" if not item.read else "false")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(item.detail or item.title)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(8)

        dot = QLabel("●")
        dot.setObjectName("MessageDot")
        dot.setProperty("level", item.level)
        dot.setFixedWidth(14)
        layout.addWidget(dot)

        time_label = QLabel(f"[{item.time}]")
        time_label.setObjectName("MessageTime")
        time_label.setFixedWidth(74)
        layout.addWidget(time_label)

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(1)

        title = QLabel(item.title)
        title.setObjectName("MessageTitle")
        text_layout.addWidget(title)

        if item.detail:
            detail = QLabel(item.detail)
            detail.setObjectName("MessageDetail")
            text_layout.addWidget(detail)

        layout.addLayout(text_layout, 1)

        for act in item.actions:
            btn = QPushButton(act.get("label", "查看"))
            btn.setObjectName("MessageActionBtn")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.clicked.connect(lambda _, a=act: self._on_action_clicked(a))
            layout.addWidget(btn)

    # ---------------- 交互 ----------------
    def _on_action_clicked(self, act: dict):
        # 「忽略」是本视图内部语义（标为已读），不往路由层抛
        if act.get("action") == "dismiss":
            self.read_requested.emit(self.item.id)
        else:
            self.activated.emit(act)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            primary = self.item.primary_action
            if primary is not None and primary.get("action") != "dismiss":
                self.activated.emit(primary)
        super().mousePressEvent(event)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        act_read = None
        if not self.item.read:
            act_read = menu.addAction("标为已读")
        act_copy = menu.addAction("复制内容")

        chosen = menu.exec(event.globalPos())
        if chosen is None:
            return
        if act_read is not None and chosen is act_read:
            self.read_requested.emit(self.item.id)
        elif chosen is act_copy:
            text = self.item.title
            if self.item.detail:
                text += "\n" + self.item.detail
            QApplication.clipboard().setText(text)


class NotificationCenterView(QWidget):
    """消息中心面板本体。"""

    action_triggered = pyqtSignal(dict)

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.setObjectName("NotificationCenterView")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._service = service
        self._rows: List[_MessageRow] = []


        self._build_ui()
        # store_changed 覆盖「新增 / 单条已读 / 全部已读 / 清空」四种变化
        self._service.store_changed.connect(self.refresh)
        # 面板正开着时到达的新消息直接算已读（用户就在看这块区域）
        self._service.message_added.connect(self._on_message_added)
        self.refresh()

    # ---------------- 构建 ----------------
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(4)

        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(6)

        self.summary = QLabel("共 0 条")
        self.summary.setObjectName("MessageSummary")
        bar.addWidget(self.summary)
        bar.addStretch()

        # 不再提供「全部已读」：打开面板即已全部标为已读（见 on_panel_shown），
        # 而面板关闭时这个按钮又点不到，留着就是死功能
        self.clear_btn = QPushButton("清空")
        self.clear_btn.setObjectName("MessageToolBtn")
        self.clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.clear_btn.clicked.connect(self._service.clear)
        bar.addWidget(self.clear_btn)
        layout.addLayout(bar)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("MessageScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # 让面板自身的圆角背景透上来（面板可能铺在壁纸上）
        self.scroll.viewport().setAutoFillBackground(False)

        self._container = QWidget()
        self._container.setObjectName("MessageList")
        self._container.setAutoFillBackground(False)
        self._list_layout = QVBoxLayout(self._container)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(0)

        self.scroll.setWidget(self._container)
        layout.addWidget(self.scroll, 1)

    # ---------------- 刷新 ----------------
    def refresh(self):
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._rows = []

        items = self._service.all()
        unread = self._service.unread_count
        suffix = f" · 未读 {unread}" if unread else ""
        self.summary.setText(f"共 {len(items)} 条{suffix}")

        if not items:
            empty = QLabel("暂无消息")
            empty.setObjectName("MessageEmpty")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            # 上下各留一份弹性空间，空态文案才会在面板里垂直居中
            self._list_layout.addStretch()
            self._list_layout.addWidget(empty)
            self._list_layout.addStretch()
            return

        for notification in items:
            row = _MessageRow(notification, self._container)
            row.activated.connect(self.action_triggered)
            row.read_requested.connect(self._service.mark_read)
            self._list_layout.addWidget(row)
            self._rows.append(row)
        self._list_layout.addStretch()

    def on_panel_shown(self):
        """面板打开时的处理：按方案约定，打开即全部标为已读（角标随之清零）。

        若以后想改成「收起面板时才落已读」，把这次调用挪到 main_window 收起
        面板的路径上即可，本视图无需改动。
        """
        if self._service.unread_count:
            self._service.mark_all_read()

    def _on_message_added(self, notification):
        # isVisible() 在「面板已展开且当前正是消息页」时才为真，
        # 恰好就是"用户正看着消息列表"的语义
        if self.isVisible() and not notification.read:
            self._service.mark_read(notification.id)

    # ---------------- 主题 ----------------
    def apply_theme(self, theme_mode: ThemeMode):
        is_dark = (theme_mode == ThemeMode.DARK)

        if is_dark:
            text, muted = "#e8e8e8", "#9aa0a6"
            row_hover, row_border = "#33353a", "#3a3c42"
            btn_bg, btn_hover, btn_border = "#3a3c42", "#46484f", "#4a4c53"
            scroll_handle = "#5a5c63"
        else:
            text, muted = "#333333", "#8a9099"
            row_hover, row_border = "#f2f5fa", "#e6e8ec"
            btn_bg, btn_hover, btn_border = "#eef0f4", "#e2e6ec", "#d8dce3"
            scroll_handle = "#c4c8cf"

        self.setStyleSheet(f"""
            #NotificationCenterView {{
                background: transparent;
            }}
            #NotificationCenterView QLabel#MessageSummary {{
                color: {muted};
                font-size: 11px;
                background: transparent;
            }}
            #NotificationCenterView QPushButton#MessageToolBtn {{
                background: {btn_bg};
                color: {text};
                border: 1px solid {btn_border};
                border-radius: 3px;
                padding: 2px 10px;
                font-size: 11px;
            }}
            #NotificationCenterView QPushButton#MessageToolBtn:hover {{
                background: {btn_hover};
            }}
            #NotificationCenterView QScrollArea#MessageScroll {{
                background: transparent;
                border: none;
            }}
            #NotificationCenterView QWidget#MessageList {{
                background: transparent;
            }}
            #NotificationCenterView QScrollBar:vertical {{
                width: 6px;
                background: transparent;
                border-radius: 3px;
            }}
            #NotificationCenterView QScrollBar::handle:vertical {{
                background: {scroll_handle};
                border-radius: 3px;
                min-height: 20px;
            }}
            #NotificationCenterView QScrollBar::add-line:vertical,
            #NotificationCenterView QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
            #NotificationCenterView QScrollBar::add-page:vertical,
            #NotificationCenterView QScrollBar::sub-page:vertical {{
                background: transparent;
            }}
            #NotificationCenterView QFrame#MessageRow {{
                background: transparent;
                border: none;
                border-bottom: 1px solid {row_border};
            }}
            #NotificationCenterView QFrame#MessageRow:hover {{
                background: {row_hover};
            }}
            #NotificationCenterView QLabel#MessageDot {{
                background: transparent;
                font-size: 13px;
            }}
            #NotificationCenterView QLabel#MessageDot[level="error"] {{ color: #e53935; }}
            #NotificationCenterView QLabel#MessageDot[level="warning"] {{ color: #f9a825; }}
            #NotificationCenterView QLabel#MessageDot[level="info"] {{ color: #1e88e5; }}
            #NotificationCenterView QLabel#MessageTime {{
                color: {muted};
                font-family: Consolas, monospace;
                font-size: 11px;
                background: transparent;
            }}
            #NotificationCenterView QLabel#MessageTitle {{
                color: {text};
                font-size: 12px;
                background: transparent;
            }}
            #NotificationCenterView QLabel#MessageTitle[unread="true"] {{
                font-weight: bold;
            }}
            #NotificationCenterView QLabel#MessageDetail {{
                color: {muted};
                font-size: 11px;
                background: transparent;
            }}
            #NotificationCenterView QLabel#MessageEmpty {{
                color: {muted};
                font-size: 13px;
                background: transparent;
            }}
            #NotificationCenterView QPushButton#MessageActionBtn {{
                background: {btn_bg};
                color: {text};
                border: 1px solid {btn_border};
                border-radius: 3px;
                padding: 2px 10px;
                font-size: 11px;
            }}
            #NotificationCenterView QPushButton#MessageActionBtn:hover {{
                background: {btn_hover};
            }}
        """)
