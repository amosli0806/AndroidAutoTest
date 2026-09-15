# views/adb_toolbox_view.py
"""ADB 工具箱主视图（PyQt6 + 主题适配）"""
import os

import qtawesome as qta
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QLineEdit, QLabel, QListWidget,
    QListWidgetItem, QCheckBox, QTextEdit, QSplitter,
    QMessageBox, QFileDialog
)
from PyQt6.QtCore import Qt, pyqtSignal, QSize, QTimer
from PyQt6.QtGui import QFont

from utils.theme import ThemeMode
from utils.settings import Settings, THEME_MODE_DARK


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
        self.command = command
        self.state = 'idle'
        self._theme_mode = ThemeMode.LIGHT

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(6)

        self.checkbox = QCheckBox(command.name)
        self.checkbox.setChecked(checked)
        self.checkbox.toggled.connect(self._on_check)
        self.checkbox.setSizePolicy(self.checkbox.sizePolicy())
        layout.addWidget(self.checkbox, 1)

        self.exec_btn = QPushButton("执行")
        self.exec_btn.setObjectName("itemExecBtn")
        self.exec_btn.setFixedWidth(72)
        self.exec_btn.setFixedHeight(26)
        self.exec_btn.clicked.connect(self._on_click)
        layout.addWidget(self.exec_btn)

    def _on_check(self, checked):
        self.check_changed.emit(self.command.id, checked)

    def _on_click(self):
        if self.state == 'idle':
            self.execute_clicked.emit(self.command)
        elif self.state == 'running':
            self.stop_clicked.emit(self.command)

    def is_checked(self):
        return self.checkbox.isChecked()

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
        self._apply_btn_style()

    def _apply_btn_style(self):
        if self.state == 'idle':
            bg, hover = "#27ae60", "#2ecc71"
        elif self.state == 'running':
            bg, hover = "#e74c3c", "#f05a4a"
        else:
            bg, hover = "#f39c12", "#f5b041"
        self.exec_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {bg};
                color: white;
                border: none;
                border-radius: 5px;
                font-size: 12px;
                font-weight: 500;
            }}
            QPushButton:hover {{ background-color: {hover}; }}
            QPushButton:disabled {{ background-color: #999; color: #ddd; }}
        """)

    def apply_theme(self, theme_mode: ThemeMode):
        self._theme_mode = theme_mode
        # 复选框文字颜色由父级 QSS 控制


# ============================================================
# 主视图
# ============================================================
class AdbToolboxView(QWidget):
    """ADB 工具箱主界面"""

    # 信号
    refresh_requested = pyqtSignal()
    execute_selected_requested = pyqtSignal(list)          # [Command]
    execute_command_requested = pyqtSignal(object)         # Command
    stop_command_requested = pyqtSignal(object)            # Command
    search_requested = pyqtSignal(str)
    browse_output_dir_requested = pyqtSignal()
    clear_log_requested = pyqtSignal()
    quick_action_requested = pyqtSignal(str)               # 快捷动作名

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("AdbToolboxView")
        self._theme_mode = ThemeMode.LIGHT
        self._has_wallpaper = False
        self._item_widgets = {}   # {cmd_id: AdbCommandItemWidget}
        self._current_output_dir = ""
        self._command_manager = None

        self.setup_ui()
        self.apply_theme()

    # ------------------------------------------------------------------
    def setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ============ 顶部工具栏 ============
        top_bar = QWidget()
        top_bar.setObjectName("toolboxTopBar")
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(10, 8, 10, 8)
        top_layout.setSpacing(8)

        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.setObjectName("topRefreshBtn")
        self.refresh_btn.setFixedHeight(28)
        self.refresh_btn.setIcon(qta.icon('fa6s.rotate', color='white'))
        self.refresh_btn.clicked.connect(self.refresh_requested.emit)
        top_layout.addWidget(self.refresh_btn)

        self.execute_selected_btn = QPushButton("执行选中")
        self.execute_selected_btn.setObjectName("executeSelBtn")
        self.execute_selected_btn.setFixedHeight(28)
        self.execute_selected_btn.setIcon(qta.icon('fa6s.play', color='white'))
        self.execute_selected_btn.clicked.connect(self._on_execute_selected)
        top_layout.addWidget(self.execute_selected_btn)

        top_layout.addSpacing(16)

        self.path_edit = QLineEdit()
        self.path_edit.setReadOnly(True)
        self.path_edit.setPlaceholderText("输出目录")
        self.path_edit.setFixedHeight(28)
        top_layout.addWidget(self.path_edit, 1)

        self.browse_btn = QPushButton("浏览")
        self.browse_btn.setObjectName("browseBtn")
        self.browse_btn.setFixedHeight(28)
        self.browse_btn.setIcon(qta.icon('fa6s.folder-open', color='white'))
        self.browse_btn.clicked.connect(self.browse_output_dir_requested.emit)
        top_layout.addWidget(self.browse_btn)

        self.clear_log_btn = QPushButton("清空日志")
        self.clear_log_btn.setObjectName("clearLogBtn")
        self.clear_log_btn.setFixedHeight(28)
        self.clear_log_btn.setIcon(qta.icon('fa6s.trash-can', color='white'))
        self.clear_log_btn.clicked.connect(self.clear_log_requested.emit)
        top_layout.addWidget(self.clear_log_btn)

        root.addWidget(top_bar)

        # ============ 搜索栏 ============
        search_bar = QWidget()
        search_bar.setObjectName("toolboxSearchBar")
        search_layout = QHBoxLayout(search_bar)
        search_layout.setContentsMargins(10, 4, 10, 8)
        search_layout.setSpacing(8)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索预设/自定义/ADB 库中的指令")
        self.search_edit.setFixedHeight(28)
        self.search_edit.returnPressed.connect(self._on_search)
        search_layout.addWidget(self.search_edit, 1)

        self.search_btn = QPushButton("查找")
        self.search_btn.setObjectName("searchBtn")
        self.search_btn.setFixedHeight(28)
        self.search_btn.setIcon(qta.icon('fa6s.magnifying-glass', color='white'))
        self.search_btn.clicked.connect(self._on_search)
        search_layout.addWidget(self.search_btn)

        root.addWidget(search_bar)

        # ============ 主区域（左右分栏） ============
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(4)
        splitter.setChildrenCollapsible(False)
        splitter.setStyleSheet("QSplitter::handle { background: transparent; }")

        # 左：命令列表
        self.command_list = QListWidget()
        self.command_list.setObjectName("adbCommandList")
        self.command_list.setSpacing(2)
        splitter.addWidget(self.command_list)

        # 右：快捷按钮 + 日志
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)

        # 快捷按钮栏
        quick_container = QWidget()
        quick_container.setObjectName("quickContainer")
        quick_grid = QGridLayout(quick_container)
        quick_grid.setContentsMargins(8, 8, 8, 8)
        quick_grid.setSpacing(8)

        quick_actions = [
            ("无线", "wireless", 'fa6s.wifi'),
            ("投屏", "scrcpy", 'fa6s.desktop'),
            ("安装", "install", 'fa6s.box'),
            ("推送", "push", 'fa6s.upload'),
            ("应用管理", "app_manager", 'fa6s.list'),
            ("硬件", "device_info", 'fa6s.microchip'),
            ("堆转储", "hprof", 'fa6s.database'),
            ("Monkey", "monkey", 'fa6s.robot'),
            ("Crash", "crash", 'fa6s.bug'),
            ("ANR", "anr", 'fa6s.hourglass-half'),
            ("MD5", "md5", 'fa6s.key'),
            ("弱网", "weak_network", 'fa6s.signal'),
            ("抓包", "packet", 'fa6s.network-wired'),
            ("内存监控", "memory", 'fa6s.chart-line'),
        ]
        self._quick_buttons = []
        for idx, (text, key, icon_name) in enumerate(quick_actions):
            row, col = idx // 5, idx % 5
            btn = QPushButton(f" {text}")
            btn.setObjectName("quickBtn")
            btn.setIcon(qta.icon(icon_name, color='#3d3d3d'))
            btn.setFixedHeight(32)
            btn.clicked.connect(lambda _, k=key: self.quick_action_requested.emit(k))
            quick_grid.addWidget(btn, row, col)
            self._quick_buttons.append(btn)

        right_layout.addWidget(quick_container)

        # 日志
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setObjectName("adbLogOutput")
        self.log_output.setFont(QFont("Consolas", 10))
        right_layout.addWidget(self.log_output, 1)

        splitter.addWidget(right)
        splitter.setSizes([420, 640])
        root.addWidget(splitter, 1)

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------
    def set_output_dir(self, path: str):
        self._current_output_dir = path
        self.path_edit.setText(path)

    def set_command_manager(self, manager):
        self._command_manager = manager

    def refresh_commands(self):
        """重建命令列表"""
        if not self._command_manager:
            return

        self.command_list.clear()
        self._item_widgets.clear()

        # 读取已勾选 ID
        checked_ids = set(Settings.load_checked_commands())

        for cmd in self._command_manager.get_all_commands():
            item = QListWidgetItem()
            widget = AdbCommandItemWidget(cmd, checked=(cmd.id in checked_ids))
            widget.execute_clicked.connect(self.execute_command_requested.emit)
            widget.stop_clicked.connect(self.stop_command_requested.emit)
            widget.check_changed.connect(self._on_check_changed)
            widget.apply_theme(self._theme_mode)

            item.setSizeHint(widget.sizeHint())
            self.command_list.addItem(item)
            self.command_list.setItemWidget(item, widget)
            self._item_widgets[cmd.id] = widget

    def set_command_state(self, cmd_id: int, state: str):
        """更新指定命令项的 UI 状态"""
        widget = self._item_widgets.get(cmd_id)
        if widget:
            widget.set_state(state)

    def get_selected_commands(self):
        """返回所有勾选的命令"""
        if not self._command_manager:
            return []
        selected = []
        for cmd in self._command_manager.get_all_commands():
            widget = self._item_widgets.get(cmd.id)
            if widget and widget.is_checked():
                selected.append(cmd)
        return selected

    def get_running_ids(self) -> set:
        """返回所有处于 starting/running 的命令 ID"""
        running = set()
        for cmd_id, widget in self._item_widgets.items():
            if widget.state in ('starting', 'running'):
                running.add(cmd_id)
        return running

    def append_log(self, text: str):
        self.log_output.append(text)
        # 限制 500 行
        doc = self.log_output.document()
        while doc.blockCount() > 500:
            cursor = self.log_output.textCursor()
            cursor.movePosition(cursor.MoveOperation.Start)
            cursor.movePosition(cursor.MoveOperation.Down,
                                cursor.MoveMode.KeepAnchor, 1)
            cursor.removeSelectedText()
        # 滚到底
        sb = self.log_output.verticalScrollBar()
        sb.setValue(sb.maximum())

    def clear_log(self):
        self.log_output.clear()

    def _on_check_changed(self, cmd_id: int, checked: bool):
        # 更新保存的勾选状态
        checked_ids = []
        for cid, widget in self._item_widgets.items():
            if widget.is_checked():
                checked_ids.append(cid)
        Settings.save_checked_commands(checked_ids)

    def _on_execute_selected(self):
        cmds = self.get_selected_commands()
        if not cmds:
            return
        self.execute_selected_requested.emit(cmds)

    def _on_search(self):
        kw = self.search_edit.text().strip()
        if kw:
            self.search_requested.emit(kw)

    # ------------------------------------------------------------------
    # 主题适配
    # ------------------------------------------------------------------
    def apply_theme(self, theme_mode: ThemeMode = None, has_wallpaper: bool = False):
        if theme_mode is None:
            theme_mode = (ThemeMode.DARK
                          if Settings.get_theme_mode() == THEME_MODE_DARK
                          else ThemeMode.LIGHT)
        self._theme_mode = theme_mode
        self._has_wallpaper = has_wallpaper
        is_dark = (theme_mode == ThemeMode.DARK)

        if is_dark:
            bg = "transparent" if has_wallpaper else "#191a1c"
            panel_bg = "rgba(50, 50, 50, 0.85)" if has_wallpaper else "#323232"
            panel_border = "#4a4a4a"
            text = "#eeeeee"
            input_bg = "#3c3c3c"
            input_border = "#555"
            focus_color = "#90caf9"
            list_bg = "rgba(30, 30, 30, 0.7)" if has_wallpaper else "#1e1e1e"
            list_sel = "#1e3a5f"
            log_bg = "#1a1a1a"
            log_border = "#3a3a3a"
            quick_bg = "rgba(45, 45, 45, 0.85)" if has_wallpaper else "#2d2d2d"
            quick_btn_bg = "#373737"
            quick_btn_hover = "#454545"
            quick_btn_border = "#555"
            quick_btn_text = "#dddddd"
        else:
            bg = "transparent" if has_wallpaper else "#ffffff"
            panel_bg = "rgba(240, 242, 245, 0.85)" if has_wallpaper else "#f0f2f5"
            panel_border = "#d0d0d0"
            text = "#333333"
            input_bg = "#ffffff"
            input_border = "#d0d0d0"
            focus_color = "#1976d2"
            list_bg = "rgba(250, 251, 252, 0.85)" if has_wallpaper else "#fafbfc"
            list_sel = "#e3f2fd"
            log_bg = "#fafbfc"
            log_border = "#e0e0e0"
            quick_bg = "rgba(242, 238, 233, 0.85)" if has_wallpaper else "#f2eee9"
            quick_btn_bg = "#ffffff"
            quick_btn_hover = "#e8e0d8"
            quick_btn_border = "#d5ccc6"
            quick_btn_text = "#3d3d3d"

        self.setStyleSheet(f"""
            #AdbToolboxView {{
                background-color: {bg};
            }}
            #toolboxTopBar, #toolboxSearchBar {{
                background-color: {panel_bg};
                border-bottom: 1px solid {panel_border};
            }}
            #AdbToolboxView QLabel {{
                color: {text};
                background: transparent;
                font-size: 13px;
            }}
            #AdbToolboxView QLineEdit {{
                background-color: {input_bg};
                color: {text};
                border: 1px solid {input_border};
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 13px;
            }}
            #AdbToolboxView QLineEdit:focus {{
                border-color: {focus_color};
            }}

            /* 顶部按钮 */
            QPushButton#topRefreshBtn,
            QPushButton#browseBtn {{
                background-color: #3498db;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 0 12px;
                font-weight: 500;
                font-size: 12px;
            }}
            QPushButton#topRefreshBtn:hover,
            QPushButton#browseBtn:hover {{ background-color: #5dade2; }}

            QPushButton#executeSelBtn {{
                background-color: #27ae60;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 0 12px;
                font-weight: 500;
                font-size: 12px;
            }}
            QPushButton#executeSelBtn:hover {{ background-color: #2ecc71; }}

            QPushButton#clearLogBtn {{
                background-color: #e74c3c;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 0 12px;
                font-weight: 500;
                font-size: 12px;
            }}
            QPushButton#clearLogBtn:hover {{ background-color: #f05a4a; }}

            QPushButton#searchBtn {{
                background-color: #3498db;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 0 16px;
                font-weight: 500;
                font-size: 12px;
            }}
            QPushButton#searchBtn:hover {{ background-color: #5dade2; }}

            /* 命令列表 */
            QListWidget#adbCommandList {{
                background-color: {list_bg};
                color: {text};
                border: 1px solid {panel_border};
                border-radius: 6px;
                outline: none;
                padding: 4px;
                font-size: 13px;
            }}
            QListWidget#adbCommandList::item {{
                background: transparent;
                border-bottom: 1px solid {panel_border};
                padding: 0px;
            }}
            QListWidget#adbCommandList::item:selected {{
                background: {list_sel};
            }}
            #adbCommandList QCheckBox {{
                color: {text};
                background: transparent;
                font-size: 13px;
                padding: 2px 0;
            }}

            /* 快捷按钮容器 */
            #quickContainer {{
                background-color: {quick_bg};
                border: 1px solid {quick_btn_border};
                border-radius: 8px;
            }}
            QPushButton#quickBtn {{
                background-color: {quick_btn_bg};
                color: {quick_btn_text};
                border: 1px solid {quick_btn_border};
                border-radius: 6px;
                padding: 0 8px;
                font-size: 12px;
            }}
            QPushButton#quickBtn:hover {{
                background-color: {quick_btn_hover};
            }}

            /* 日志区 */
            QTextEdit#adbLogOutput {{
                background-color: {log_bg};
                color: {text};
                border: 1px solid {log_border};
                border-radius: 6px;
                padding: 6px;
                font-family: Consolas, monospace;
                font-size: 12px;
            }}
        """)

        # 刷新列表项的按钮样式
        for widget in self._item_widgets.values():
            widget.apply_theme(theme_mode)