# views/adb_toolbox_view.py
"""ADB 工具箱主视图（PyQt6 + 主题适配 + 圆角分组）"""
import qtawesome as qta
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QLineEdit, QLabel, QListWidget,
    QListWidgetItem, QCheckBox, QSplitter, QGroupBox,
    QToolButton, QMenu, QFrame, QSizePolicy, QScrollArea,
    QStyleOptionButton, QStyle
)
from PyQt6.QtCore import Qt, pyqtSignal, QSize, QTimer, QEvent
from PyQt6.QtGui import QFont, QAction, QIcon, QPainter, QPen, QColor

from utils.theme import ThemeMode
from utils.settings import Settings, THEME_MODE_DARK
from utils.dialogs import WarningDialog
from utils.toast import show_toast
from views.adb_panels.common import thin_scrollbar_qss
from views.adb_panels.weak_network_panel import WeakNetworkPanel
from views.adb_panels.monkey_panel import MonkeyPanel


# ============================================================
# 带明显边框的复选框
# ============================================================
class BorderedCheckBox(QCheckBox):
    """保留系统默认绘制（勾选时有 √），额外叠加明显的边框。
    与 perf_view / execute_view 里同名控件一致。"""

    def paintEvent(self, event):
        super().paintEvent(event)
        try:
            opt = QStyleOptionButton()
            self.initStyleOption(opt)
            rect = self.style().subElementRect(
                QStyle.SubElement.SE_CheckBoxIndicator, opt, self
            )
            if rect.isValid() and rect.width() > 0:
                painter = QPainter(self)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setPen(QPen(QColor(140, 140, 140), 1))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(rect.adjusted(0, 0, -1, -1))
                painter.end()
        except Exception:
            pass

class ClickableCheckBox(BorderedCheckBox):
    """整行可点的复选框：点击 widget 任意位置都能切换状态"""

    def mousePressEvent(self, event):
        # 左键点击任意位置都切换，不区分 indicator / text / 空白
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle()
            event.accept()
            return
        super().mousePressEvent(event)
# ============================================================
# 命令列表项组件
# ============================================================
class AdbCommandItemWidget(QWidget):
    """单条命令列表项：勾选框 + 名称 + 执行/停止按钮"""

    execute_clicked = pyqtSignal(object)   # Command
    stop_clicked = pyqtSignal(object)
    check_changed = pyqtSignal(int, bool)

    def __init__(self, command, checked=False, parent=None):
        super().__init__(parent)
        self.setObjectName("CommandItemWidget")
        self.command = command
        self.state = 'idle'
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._theme_mode = ThemeMode.LIGHT
        self._has_wallpaper = False
        self._checked = checked
        # 让 QSS 的 background/border-radius 生效
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)  # 右边距 10 → 6
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        # 固定高度：内容 26 + 上下 padding 8×2 = 42px
        self.setFixedHeight(42)

        self.checkbox = ClickableCheckBox(command.name)
        self.checkbox.setChecked(checked)
        self.checkbox.setFixedHeight(26)
        self.checkbox.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.checkbox.toggled.connect(self._on_check)
        layout.addWidget(
            self.checkbox, 1, Qt.AlignmentFlag.AlignVCenter
        )

        self.exec_btn = QPushButton("执行")
        self.exec_btn.setObjectName("ItemExecBtn")
        self.exec_btn.setFixedSize(84, 26)  # 72 → 84，容纳图标 + "启动中..."文案
        self.exec_btn.clicked.connect(self._on_click)
        layout.addWidget(
            self.exec_btn, 0, Qt.AlignmentFlag.AlignVCenter
        )

        self._apply_style()

    def _on_check(self, checked):
        self._checked = checked
        self._apply_checked_style()  # 立即刷新背景色
        self.check_changed.emit(self.command.id, checked)

    def _on_click(self):
        if self.state == 'idle':
            self.set_state('starting')
            # 延迟 120ms 再发信号，让 UI 先把"启动中..."渲染出来
            QTimer.singleShot(300, lambda: self.execute_clicked.emit(self.command))
        elif self.state == 'running':
            self.set_state('stopping')
            QTimer.singleShot(2000, lambda: self.stop_clicked.emit(self.command))

    def is_checked(self):
        return self.checkbox.isChecked()

    def mousePressEvent(self, event):
        """点击卡片空白区域 = 切换复选框；点击执行按钮/复选框本身 = 交给 Qt"""
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.pos()
            # 点在执行按钮或复选框区域内 → 不处理，让 Qt 分发
            if (not self.exec_btn.geometry().contains(pos)
                    and not self.checkbox.geometry().contains(pos)):
                self.checkbox.toggle()
                return  # 避免事件继续传递导致重复触发
        super().mousePressEvent(event)

    def set_state(self, state: str):
        self.state = state
        if state == 'idle':
            self.exec_btn.setText("执行")
            self.exec_btn.setEnabled(True)
        elif state == 'starting':
            self.exec_btn.setText("启动中")
            self.exec_btn.setEnabled(False)
        elif state == 'running':
            self.exec_btn.setText("停止")
            self.exec_btn.setEnabled(True)
        elif state == 'stopping':
            self.exec_btn.setText("停止中")
            self.exec_btn.setEnabled(False)
        self._apply_style()

    def _apply_style(self):
        """按钮样式 + 图标（每次状态变化重新应用）"""
        if self.state == 'idle':
            bg, hover, pressed = "#27ae60", "#2ecc71", "#1e8449"
            self.exec_btn.setIcon(qta.icon('fa6s.play', color='white'))
        elif self.state == 'running':
            bg, hover, pressed = "#e74c3c", "#f05a4a", "#c0392b"
            self.exec_btn.setIcon(qta.icon('fa6s.stop', color='white'))
        else:
            # starting / stopping：橙色 + 无图标
            bg, hover, pressed = "#f39c12", "#f5b041", "#d68910"
            self.exec_btn.setIcon(QIcon())

        self.exec_btn.setStyleSheet(f"""
            QPushButton#ItemExecBtn {{
                background-color: {bg};
                color: white;
                border: none;
                border-radius: 5px;
                font-size: 12px;
                font-weight: 500;
            }}
            QPushButton#ItemExecBtn:hover {{ background-color: {hover}; }}
            QPushButton#ItemExecBtn:pressed {{ background-color: {pressed}; }}
            QPushButton#ItemExecBtn:disabled {{ background-color: #999; color: #ddd; }}
        """)

    def _apply_checked_style(self):
        """根据勾选状态 + 主题，刷新整个 item 的背景色

        有壁纸时统一用半透明底：否则卡片是一块块实心白/黑，把壁纸盖得很割裂。
        """
        is_dark = (self._theme_mode == ThemeMode.DARK)
        wp = self._has_wallpaper

        if is_dark:
            # 0.85 叠在中央区域那层罩上后壁纸基本看不见，降到 0.7 与 ProjectTreeView 对齐
            item_bg = "rgba(43, 43, 43, 0.7)" if wp else "#2b2b2b"
            item_border = "#3a3a3a"
            item_hover = "rgba(255, 255, 255, 0.06)"
            checked_bg = "rgba(74, 58, 32, 0.9)" if wp else "#4a3a20"       # 深橙
            checked_border = "#8a6a40"
            checked_hover = "rgba(90, 74, 42, 0.9)" if wp else "#5a4a2a"
            text_color = "#eeeeee"
        else:
            item_bg = "rgba(255, 255, 255, 0.7)" if wp else "#ffffff"
            item_border = "#e0e0e0"
            item_hover = "rgba(240, 244, 248, 0.9)" if wp else "#f0f4f8"
            checked_bg = "rgba(255, 224, 178, 0.9)" if wp else "#ffe0b2"       # 浅橙
            checked_border = "#ffb74d"
            checked_hover = "rgba(255, 214, 153, 0.9)" if wp else "#ffd699"
            text_color = "#333333"

        if self._checked:
            bg, border, hover = checked_bg, checked_border, checked_hover
        else:
            bg, border, hover = item_bg, item_border, item_hover

        self.setStyleSheet(f"""
            #CommandItemWidget {{
                background-color: {bg};
                border: 1px solid {border};
                border-radius: 6px;
            }}
            #CommandItemWidget:hover {{
                background-color: {hover};
            }}
            #CommandItemWidget QCheckBox {{
                color: {text_color};
                background: transparent;
                font-size: 13px;
                padding: 2px 0;
                spacing: 6px;
            }}
        """)

    def apply_theme(self, theme_mode: ThemeMode, has_wallpaper: bool = False):
        """主题适配：item 卡片整体 + 复选框文字"""
        self._theme_mode = theme_mode
        self._has_wallpaper = has_wallpaper
        self._apply_checked_style()
        self._apply_style()



# ============================================================
# 主视图
# ============================================================
class AdbToolboxView(QWidget):
    """ADB 工具箱主界面"""

    # 信号
    execute_selected_requested = pyqtSignal(list)
    execute_command_requested = pyqtSignal(object)
    stop_command_requested = pyqtSignal(object)
    search_requested = pyqtSignal(str)
    # 内嵌面板（弱网等）的结果文案 -> 底部"虫师日志"
    log_message = pyqtSignal(str)
    export_commands_requested = pyqtSignal()
    import_commands_requested = pyqtSignal()
    display_commands_requested = pyqtSignal()
    add_command_requested = pyqtSignal()
    edit_command_requested = pyqtSignal(object)  # 传当前选中的 Command
    delete_commands_requested = pyqtSignal(list)  # 传当前勾选的 Command 列表

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("AdbToolboxView")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._theme_mode = ThemeMode.LIGHT
        self._has_wallpaper = False
        self._item_widgets = {}
        self._command_manager = None
        self.command_menu = None
        self._executing_selected_ids = set()  # 执行选中时，正在跑的命令 ID 集合

        # 节流 timer：拖动分栏时避免频繁重排
        self._item_resize_timer = QTimer(self)
        self._item_resize_timer.setSingleShot(True)
        self._item_resize_timer.setInterval(30)
        self._item_resize_timer.timeout.connect(self._refresh_item_geometry)

        self.setup_ui()

        # 内嵌弱网 / Monkey 面板的结果 -> 底部"虫师日志"
        self.weak_network_panel.log_message.connect(self.log_message.emit)
        self.monkey_panel.log_message.connect(self.log_message.emit)

        # 监听 command_list 视口的尺寸变化（拖动分栏 / 窗口缩放都会触发）
        self.command_list.viewport().installEventFilter(self)

        self.apply_theme()

    # ------------------------------------------------------------------
    def setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)  # ← 去掉外边距
        root.setSpacing(10)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(4)  # ← 8 改 4
        splitter.setChildrenCollapsible(False)
        splitter.setStyleSheet("QSplitter::handle { background: transparent; }")

        # ==================== 左：指令管理 ====================
        self.left_group = QGroupBox()
        self.left_group.setObjectName("LeftGroup")
        left_layout = QVBoxLayout(self.left_group)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        # 标题行：指令管理 ▾ + 执行选中
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)

        self.menu_btn = QToolButton()
        self.menu_btn.setObjectName("CommandMenuBtn")
        self.menu_btn.setText("指令管理 ▾")
        self.menu_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.menu_btn.setCursor(Qt.CursorShape.PointingHandCursor)

        menu = QMenu(self.menu_btn)
        menu.setObjectName("CommandMenu")

        # 导入 / 导出
        act_export = QAction("导出指令", self)
        act_export.setIcon(qta.icon('fa6s.file-export', color='#555555'))
        act_export.triggered.connect(self.export_commands_requested.emit)
        menu.addAction(act_export)

        act_import = QAction("导入指令", self)
        act_import.setIcon(qta.icon('fa6s.file-import', color='#555555'))
        act_import.triggered.connect(self.import_commands_requested.emit)
        menu.addAction(act_import)

        menu.addSeparator()

        # 展示 / 增 / 改 / 删
        act_display = QAction("展示指令", self)
        act_display.setIcon(qta.icon('fa6s.eye', color='#555555'))
        act_display.triggered.connect(self.display_commands_requested.emit)
        menu.addAction(act_display)

        act_add = QAction("增加指令", self)
        act_add.setIcon(qta.icon('fa6s.plus', color='#555555'))
        act_add.triggered.connect(self.add_command_requested.emit)
        menu.addAction(act_add)

        act_edit = QAction("编辑指令", self)
        act_edit.setIcon(qta.icon('fa6s.pen', color='#555555'))
        act_edit.triggered.connect(self._on_edit_command_clicked)
        menu.addAction(act_edit)

        act_delete = QAction("删除指令", self)
        act_delete.setIcon(qta.icon('fa6s.trash-can', color='#555555'))
        act_delete.triggered.connect(self._on_delete_command_clicked)
        menu.addAction(act_delete)

        self.menu_btn.setMenu(menu)
        self.command_menu = menu  # 供主题切换用

        title_row.addWidget(self.menu_btn)
        title_row.addStretch()

        self.execute_selected_btn = QPushButton("执行选中")
        self.execute_selected_btn.setObjectName("ExecuteSelBtn")
        self.execute_selected_btn.setFixedHeight(26)
        self.execute_selected_btn.setMinimumWidth(90)
        self.execute_selected_btn.clicked.connect(self._on_execute_selected)
        title_row.addWidget(self.execute_selected_btn)

        left_layout.addLayout(title_row)

        # 命令列表
        self.command_list = QListWidget()
        self.command_list.setObjectName("CommandList")
        self.command_list.setSpacing(0)
        self.command_list.setFrameShape(QFrame.Shape.NoFrame)
        left_layout.addWidget(self.command_list, 1)

        splitter.addWidget(self.left_group)

        # ==================== 右：快捷功能 ====================
        self.right_group = QGroupBox()
        self.right_group.setObjectName("RightGroup")
        right_layout = QVBoxLayout(self.right_group)
        right_layout.setContentsMargins(12, 12, 12, 12)
        right_layout.setSpacing(12)

        # 搜索行
        search_row = QHBoxLayout()
        search_row.setSpacing(8)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索预设/自定义/ADB 库中的指令")
        self.search_edit.setFixedHeight(32)
        self.search_edit.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.search_edit.returnPressed.connect(self._on_search)
        search_row.addWidget(self.search_edit, 1)

        self.search_btn = QPushButton("查找")
        self.search_btn.setObjectName("SearchBtn")
        self.search_btn.setFixedSize(80, 32)
        self.search_btn.setIcon(qta.icon('fa6s.magnifying-glass', color='white'))
        self.search_btn.clicked.connect(self._on_search)
        search_row.addWidget(self.search_btn)

        right_layout.addLayout(search_row)

        # 搜索框下方：弱网（上）、Monkey 测试（下）——内嵌面板，不再弹对话框
        # 外层面板整体可滚动，窗口偏小时不会把两块内容挤没
        self.tool_scroll = QScrollArea()
        self.tool_scroll.setObjectName("ToolScroll")
        self.tool_scroll.setWidgetResizable(True)
        self.tool_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.tool_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        tools_host = QWidget()
        tools_host.setObjectName("ToolPanelsHost")
        tools_layout = QVBoxLayout(tools_host)
        tools_layout.setContentsMargins(0, 0, 0, 0)
        tools_layout.setSpacing(12)

        self.weak_network_panel = WeakNetworkPanel()
        tools_layout.addWidget(self.weak_network_panel)

        self.monkey_panel = MonkeyPanel()
        tools_layout.addWidget(self.monkey_panel)

        tools_layout.addStretch()
        self.tool_scroll.setWidget(tools_host)
        right_layout.addWidget(self.tool_scroll, 1)

        splitter.addWidget(self.right_group)
        splitter.setSizes([420, 640])

        root.addWidget(splitter, 1)

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------
    def set_device_service(self, device_service):
        """面板由控制器注入设备服务后才具备执行能力"""
        self.weak_network_panel.set_device_service(device_service)
        self.monkey_panel.set_device_service(device_service)

    def focus_weak_network(self):
        """快捷键入口：滚到弱网面板并聚焦网卡输入框"""
        self.tool_scroll.ensureWidgetVisible(self.weak_network_panel)
        self.weak_network_panel.iface_combo.setFocus()

    def focus_monkey(self):
        """快捷键入口：滚到 Monkey 面板并聚焦包名输入框"""
        self.tool_scroll.ensureWidgetVisible(self.monkey_panel)
        self.monkey_panel.pkg.setFocus()

    def stop_monkey_if_running(self):
        """退出/清理时兜底停掉 Monkey"""
        self.monkey_panel._stop_monkey_on_quit()

    def set_command_manager(self, manager):
        self._command_manager = manager


    def refresh_commands(self):
        if not self._command_manager:
            return

        self.command_list.clear()
        self._item_widgets.clear()

        checked_ids = set(Settings.load_checked_commands())

        for cmd in self._command_manager.get_all_commands():
            item = QListWidgetItem()
            widget = AdbCommandItemWidget(cmd, checked=(cmd.id in checked_ids))
            widget.execute_clicked.connect(self.execute_command_requested.emit)
            widget.stop_clicked.connect(self.stop_command_requested.emit)
            widget.check_changed.connect(self._on_check_changed)
            widget.apply_theme(self._theme_mode)

            # item 高 52、widget 高 42 → 上下各 5px 空隙，形成间距
            item.setSizeHint(QSize(0, 47))
            self.command_list.addItem(item)
            self.command_list.setItemWidget(item, widget)
            self._item_widgets[cmd.id] = widget

        # 应用上次保存的显示过滤（空列表/None 表示不过滤）
        saved_ids = Settings.load_display_command_ids()
        if saved_ids:
            self.filter_commands(saved_ids)
        else:
            self.filter_commands(None)

        # 首次刷新时视口宽度未定，延迟两次重排 item 宽度
        QTimer.singleShot(0, self._refresh_item_geometry)
        QTimer.singleShot(80, self._refresh_item_geometry)

    def _refresh_item_geometry(self):
        """把所有 item widget 的宽度对齐到 list viewport 宽度"""
        if self.command_list.count() == 0:
            return
        vp_width = self.command_list.viewport().width()
        if vp_width <= 0:
            return

        for i in range(self.command_list.count()):
            item = self.command_list.item(i)
            if not item:
                continue
            widget = self.command_list.itemWidget(item)
            if not widget:
                continue
            # 让 widget 与 viewport 等宽（高度由 setFixedHeight 保证）
            widget.setFixedWidth(vp_width)

        self.command_list.doItemsLayout()
        self.command_list.updateGeometry()

    def eventFilter(self, obj, event):
        # command_list 视口尺寸变化 → 节流后重排 item 宽度
        if (obj is self.command_list.viewport()
                and event.type() == QEvent.Type.Resize):
            self._item_resize_timer.start()
        return super().eventFilter(obj, event)

    def set_command_state(self, cmd_id: int, state: str):
        widget = self._item_widgets.get(cmd_id)
        if widget:
            widget.set_state(state)

        # "执行选中"场景：命令回到 idle 时从待完成集合中移除
        if state == 'idle' and cmd_id in self._executing_selected_ids:
            self._executing_selected_ids.discard(cmd_id)
            self._update_execute_selected_btn()

    def get_selected_commands(self):
        if not self._command_manager:
            return []
        selected = []
        for cmd in self._command_manager.get_all_commands():
            widget = self._item_widgets.get(cmd.id)
            if widget and widget.is_checked():
                selected.append(cmd)
        return selected

    def get_running_ids(self) -> set:
        running = set()
        for cmd_id, widget in self._item_widgets.items():
            if widget.state in ('starting', 'running'):
                running.add(cmd_id)
        return running

    def set_output_dir(self, path: str):
        # 保留方法（兼容旧调用），但不再有 UI 元素
        self._current_output_dir = path

    def _on_check_changed(self, cmd_id: int, checked: bool):
        checked_ids = []
        for cid, widget in self._item_widgets.items():
            if widget.is_checked():
                checked_ids.append(cid)
        Settings.save_checked_commands(checked_ids)

    def _on_execute_selected(self):
        cmds = self.get_selected_commands()
        if not cmds:
            show_toast(self, "请先勾选至少一条指令", duration=2000)
            return
        self._executing_selected_ids = {c.id for c in cmds}
        self._update_execute_selected_btn()
        self.execute_selected_requested.emit(cmds)

    def _update_execute_selected_btn(self):
        """根据是否有正在跑的命令，切换"执行选中 / 执行中..."状态"""
        if self._executing_selected_ids:
            self.execute_selected_btn.setText("执行中...")
            self.execute_selected_btn.setEnabled(False)
        else:
            self.execute_selected_btn.setText("执行选中")
            self.execute_selected_btn.setEnabled(True)

    def reset_execute_selected_state(self):
        """Controller 因故拒绝执行时，清除"执行中..."状态并恢复按钮"""
        self._executing_selected_ids.clear()
        self._update_execute_selected_btn()

    def _on_search(self):
        kw = self.search_edit.text().strip()
        if not kw:
            show_toast(self, "请输入搜索关键词", duration=2000)
            return
        self.search_requested.emit(kw)

    def _get_target_command(self):
        """返回 (cmd, status)。status 取值：
        - 'ok'       : 找到唯一勾选
        - 'none'     : 没有勾选
        - 'multiple' : 勾选了多个
        """
        if not self._command_manager:
            return None, "none"

        checked = []
        for cmd in self._command_manager.get_all_commands():
            widget = self._item_widgets.get(cmd.id)
            if widget and widget.is_checked():
                checked.append(cmd)

        if len(checked) == 1:
            return checked[0], "ok"
        if len(checked) > 1:
            return None, "multiple"
        return None, "none"

    def _on_edit_command_clicked(self):
        cmd, status = self._get_target_command()
        if status == "none":
            show_toast(self, "请先勾选至少一条指令",
                       duration=2000)
            return
        if status == "multiple":
            show_toast(self, "编辑操作只能勾选一条指令，请先取消其他勾选",
                       duration=2000)
            return
        self.edit_command_requested.emit(cmd)

    def _on_delete_command_clicked(self):
        checked = self.get_selected_commands()
        if checked:
            self.delete_commands_requested.emit(checked)
            return
        show_toast(self, "请先勾选至少一条指令", duration=2000)

    def filter_commands(self, visible_ids=None):
        """根据 ID 列表隐藏未选中的命令；visible_ids=None 表示全部显示"""
        for i in range(self.command_list.count()):
            item = self.command_list.item(i)
            if not item:
                continue
            widget = self.command_list.itemWidget(item)
            if not widget:
                continue
            cmd = getattr(widget, 'command', None)
            if cmd is None:
                item.setHidden(False)
                continue
            if visible_ids is None:
                item.setHidden(False)
            else:
                item.setHidden(cmd.id not in visible_ids)
    # ------------------------------------------------------------------
    # 主题
    # ------------------------------------------------------------------
    def apply_theme(self, theme_mode: ThemeMode = None, has_wallpaper: bool = None):
        if theme_mode is None:
            theme_mode = (ThemeMode.DARK
                          if Settings.get_theme_mode() == THEME_MODE_DARK
                          else ThemeMode.LIGHT)

        # 主窗口调用 sub_view.apply_theme(mode) 时不会传 has_wallpaper，
        # 因此这里自己检测一次，保证有壁纸时背景透明，跟项目管理区域一致。
        if has_wallpaper is None:
            import os as _os
            try:
                wp_path = Settings.get_wallpaper_path()
                has_wallpaper = bool(wp_path and _os.path.exists(wp_path))
            except Exception:
                has_wallpaper = False

        self._theme_mode = theme_mode
        self._has_wallpaper = has_wallpaper
        is_dark = (theme_mode == ThemeMode.DARK)

        if is_dark:
            # 有壁纸时与「项目管理」一致：分组框、列表底全透，只留中央区域那一层罩；
            # 命令行卡片自己保留 0.7 半透明底（同 ProjectTreeView），避免整块实色把壁纸盖住
            group_bg = "transparent" if has_wallpaper else "#191a1c"
            group_border = "#555"
            title_color = "#ffffff"
            text = "#eeeeee"
            list_bg = "transparent" if has_wallpaper else "#323232"
            item_hover = "rgba(255, 255, 255, 0.06)"
            input_bg = "#3c3c3c"
            input_border = "#555"
            focus_color = "#90caf9"
            search_bg = "#3498db"
            search_hover = "#5dade2"
            menu_btn_color = "#ffffff"
            sb_bg = "#3a3a3a"
            sb_handle = "#666666"
            # 根控件背景别写 transparent：Qt 会把它的调色板整份算成全黑，
            # 挂在视图下面的 QMessageBox / QFileDialog 继承后内容就看不见了。
            # 用 alpha=0 的具体颜色，视觉上一样全透，调色板正常。
            self_bg = "rgba(50, 50, 50, 0)"
        else:
            group_bg = "transparent" if has_wallpaper else "#ffffff"
            group_border = "#d0d0d0"
            title_color = "#1a1a1a"
            text = "#333333"
            list_bg = "transparent" if has_wallpaper else "#e8eaed"
            item_hover = "rgba(0, 0, 0, 0.04)"
            input_bg = "#ffffff"
            input_border = "#d0d0d0"
            focus_color = "#1976d2"
            search_bg = "#3498db"
            search_hover = "#5dade2"
            menu_btn_color = "#333333"
            sb_bg = "#e0e0e0"
            sb_handle = "#c0c0c0"
            self_bg = "rgba(232, 234, 237, 0)"

        self.setStyleSheet(f"""
            #AdbToolboxView {{
                background-color: {self_bg};
            }}
            #AdbToolboxView QGroupBox#LeftGroup,
            #AdbToolboxView QGroupBox#RightGroup {{
                background-color: {group_bg};
                border: 1px solid {group_border};
                border-radius: 8px;
                padding: 6px;
                margin-top: 0;
            }}
            #AdbToolboxView QToolButton#CommandMenuBtn {{
                background: transparent;
                border: none;
                color: {title_color};
                font-weight: bold;
                font-size: 16px;
                padding: 0;
            }}
            #AdbToolboxView QToolButton#CommandMenuBtn::menu-indicator {{
                image: none;
                width: 0px;
                height: 0px;
            }}
            #AdbToolboxView QPushButton#ExecuteSelBtn {{
                background-color: #27ae60;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 0 12px;
                font-weight: 500;
                font-size: 12px;
            }}
            #AdbToolboxView QPushButton#ExecuteSelBtn:hover {{
                background-color: #2ecc71;
            }}
            #AdbToolboxView QPushButton#ExecuteSelBtn:disabled {{
                background-color: #f39c12;
                color: white;
            }}
            /* 滚动区视口默认会用调色板底色（不透明白底）把壁纸挡掉，这里放开：
               壁纸由各面板自己的 0.85 半透明底做柔和过渡 */
            #AdbToolboxView QScrollArea#ToolScroll,
            #AdbToolboxView QScrollArea#ToolScroll > QWidget > QWidget,
            #AdbToolboxView QWidget#ToolPanelsHost {{
                background-color: {self_bg};
            }}
            #AdbToolboxView QPushButton#SearchBtn {{
                background-color: {search_bg};
                color: white;
                border: none;
                border-radius: 4px;
                font-weight: 500;
                font-size: 12px;
            }}
            #AdbToolboxView QPushButton#SearchBtn:hover {{
                background-color: {search_hover};
            }}
            #AdbToolboxView QLineEdit {{
                background-color: {input_bg};
                color: {text};
                border: 1px solid {input_border};
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 13px;
            }}
            #AdbToolboxView QLineEdit:focus {{
                border-color: {focus_color};
            }}
            #AdbToolboxView QListWidget#CommandList {{
                background-color: {list_bg};
                color: {text};
                border: 1px solid {group_border};
                border-radius: 6px;
                outline: none;
                padding: 4px;
            }}
            #AdbToolboxView QListWidget#CommandList::item {{
                background: transparent;
                border: none;
                padding: 0px;
                margin: 0px;
            }}
            #AdbToolboxView QListWidget#CommandList::item:hover {{
                background: transparent;
            }}
            #AdbToolboxView QListWidget#CommandList::item:selected {{
                background: transparent;
            }}
            #AdbToolboxView QListWidget#CommandList QScrollBar:vertical {{
                width: 6px;
                background: {sb_bg};
                border-radius: 3px;
                margin: 0px;
            }}
            #AdbToolboxView QListWidget#CommandList QScrollBar::handle:vertical {{
                background: {sb_handle};
                border-radius: 3px;
                min-height: 20px;
            }}
            #AdbToolboxView QListWidget#CommandList QScrollBar::add-line:vertical,
            #AdbToolboxView QListWidget#CommandList QScrollBar::sub-line:vertical {{
                height: 0px;
                width: 0px;
                background: transparent;
                border: none;
            }}
            #AdbToolboxView QListWidget#CommandList QScrollBar::add-page:vertical,
            #AdbToolboxView QListWidget#CommandList QScrollBar::sub-page:vertical {{
                background: transparent;
            }}
            #AdbToolboxView QListWidget#CommandList QScrollBar::up-arrow:vertical,
            #AdbToolboxView QListWidget#CommandList QScrollBar::down-arrow:vertical {{
                background: transparent;
                border: none;
                width: 0px;
                height: 0px;
            }}
{thin_scrollbar_qss("#AdbToolboxView QScrollArea#ToolScroll", sb_bg, sb_handle)}        """)

        # 下拉菜单主题
        self._apply_command_menu_theme(is_dark)

        # 内嵌面板（弱网 / Monkey）主题
        self.weak_network_panel.apply_theme(theme_mode, has_wallpaper)
        self.monkey_panel.apply_theme(theme_mode, has_wallpaper)

        # 命令项主题刷新（同样要带上壁纸标记）
        for widget in self._item_widgets.values():
            widget.apply_theme(theme_mode, has_wallpaper)

    def _apply_command_menu_theme(self, is_dark: bool):
        """指令管理下拉菜单主题（参考项目管理 ProjectMenu 的风格）"""
        if self.command_menu is None:
            return

        # 关键：去掉系统窗口装饰 + 允许透明背景，让 QSS 的圆角四角真正生效
        self.command_menu.setWindowFlags(
            Qt.WindowType.Popup
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.command_menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        if is_dark:
            menu_bg = "#2b2d30"
            menu_border = "#4a4a4a"
            menu_text = "#dddddd"
            menu_hover_bg = "#1e3a5f"
            menu_hover_text = "#ffffff"
            sep_color = "#4a4a4a"
        else:
            menu_bg = "#ffffff"
            menu_border = "#d0d0d0"
            menu_text = "#333333"
            menu_hover_bg = "#e8f0fe"
            menu_hover_text = "#1976d2"
            sep_color = "#e0e0e0"

        self.command_menu.setStyleSheet(f"""
            QMenu#CommandMenu {{
                background-color: {menu_bg};
                border: 1px solid {menu_border};
                border-radius: 8px;
                padding: 6px;
            }}
            QMenu#CommandMenu::item {{
                background: transparent;
                padding: 7px 28px 7px 32px;
                margin: 1px 2px;
                border-radius: 5px;
                color: {menu_text};
                font-size: 13px;
            }}
            QMenu#CommandMenu::item:selected {{
                background-color: {menu_hover_bg};
                color: {menu_hover_text};
            }}
            QMenu#CommandMenu::separator {{
                height: 1px;
                background: {sep_color};
                margin: 4px 8px;
            }}
            QMenu#CommandMenu::icon {{
                left: 10px;
            }}
        """)

        # 图标颜色
        icon_color = "#bbbbbb" if is_dark else "#555555"
        icon_map = {
            "导出指令": 'fa6s.file-export',
            "导入指令": 'fa6s.file-import',
            "展示指令": 'fa6s.eye',
            "增加指令": 'fa6s.plus',
            "编辑指令": 'fa6s.pen',
            "删除指令": 'fa6s.trash-can',
        }
        for act in self.command_menu.actions():
            txt = act.text()
            if txt in icon_map:
                act.setIcon(qta.icon(icon_map[txt], color=icon_color))