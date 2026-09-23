# views/adb_dialogs/select_commands_dialog.py
"""自定义显示命令对话框（PyQt6 + 主题适配 + 卡片列表）"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout,
    QListWidget, QListWidgetItem, QCheckBox,
    QPushButton, QLabel, QWidget
)
from PyQt6.QtCore import Qt, QSize

from services.command_manager import CommandManager
from utils.theme import ThemeMode
from utils.settings import Settings, THEME_MODE_DARK
from views.adb_toolbox_view import ClickableCheckBox


class RowWidget(QWidget):
    """卡片行：点击卡片任意位置 = 切换复选框

    object_name 让复用方给自己的 QSS 定位用（「展示卡片」对话框把它设成
    ActionCardCheckRow，样式取值与本文件里的 CommandCheckRow 一致）。
    """

    def __init__(self, checkbox: ClickableCheckBox, object_name: str = "CommandCheckRow", parent=None):
        super().__init__(parent)
        self.setObjectName(object_name)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.checkbox = checkbox

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.pos()
            # 点在复选框之外 → 手动 toggle
            if not self.checkbox.geometry().contains(pos):
                self.checkbox.toggle()
                event.accept()
                return
        super().mousePressEvent(event)


class SelectCommandsDialog(QDialog):
    """选择要在主列表中显示的命令"""

    def __init__(self, command_manager: CommandManager, parent=None):
        super().__init__(parent)
        self.command_manager = command_manager
        self._theme_mode = ThemeMode.LIGHT
        self.checkboxes = []

        self.setWindowTitle("展示指令")
        self.setModal(True)
        self.resize(440, 560)

        self.setup_ui()
        self.load_commands()
        self.apply_theme()

    # ------------------------------------------------------------------
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        tip = QLabel("勾选您希望在左侧列表中显示的命令（取消勾选则隐藏）")
        tip.setObjectName("hintLabel")
        tip.setWordWrap(True)
        layout.addWidget(tip)

        self.list_widget = QListWidget()
        self.list_widget.setObjectName("commandList")
        self.list_widget.setSpacing(0)
        layout.addWidget(self.list_widget, 1)

        # 按钮
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)
        btn_layout.addStretch()

        self.toggle_all_btn = QPushButton("全选")
        self.toggle_all_btn.setObjectName("toggleBtn")
        self.toggle_all_btn.setFixedHeight(34)
        self.toggle_all_btn.setMinimumWidth(140)
        self.toggle_all_btn.clicked.connect(self.toggle_all)
        btn_layout.addWidget(self.toggle_all_btn)

        self.ok_btn = QPushButton("确定")
        self.ok_btn.setObjectName("okBtn")
        self.ok_btn.setFixedHeight(34)
        self.ok_btn.setMinimumWidth(140)
        self.ok_btn.clicked.connect(self.accept)
        btn_layout.addWidget(self.ok_btn)

        btn_layout.addStretch()
        layout.addLayout(btn_layout)

    # ------------------------------------------------------------------
    def load_commands(self):
        self.checkboxes.clear()
        self.list_widget.clear()

        # 读取上次保存的"显示过滤"列表
        # None 或 [] 表示"无过滤"，即全选
        saved_ids = Settings.load_display_command_ids()
        if not saved_ids:
            saved_ids = None  # None 表示"全选"

        for cmd in self.command_manager.get_all_commands():
            item = QListWidgetItem(self.list_widget)

            label_text = f"{cmd.name}  {'[预设]' if cmd.is_preset else '[自定义]'}"
            cb = ClickableCheckBox(label_text)
            cb.setFixedHeight(24)
            if saved_ids is None:
                cb.setChecked(True)
            else:
                cb.setChecked(cmd.id in saved_ids)

            # 卡片容器：点击空白区域也能切换
            row = RowWidget(cb)
            row.setFixedHeight(38)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(12, 6, 12, 6)
            row_layout.setSpacing(8)
            row_layout.addWidget(cb, 1, Qt.AlignmentFlag.AlignVCenter)

            # item 高 46，卡片高 38 → 上下各 4px 间距
            item.setSizeHint(QSize(0, 43))
            self.list_widget.setItemWidget(item, row)
            self.checkboxes.append((cmd.id, cb))

        self._update_toggle_button_text()

    def _update_toggle_button_text(self):
        if not self.checkboxes:
            return
        all_checked = all(cb.isChecked() for _, cb in self.checkboxes)
        self.toggle_all_btn.setText("取消全选" if all_checked else "全选")

    def toggle_all(self):
        if not self.checkboxes:
            return
        all_checked = all(cb.isChecked() for _, cb in self.checkboxes)
        new_state = not all_checked
        for _, cb in self.checkboxes:
            cb.setChecked(new_state)
        self._update_toggle_button_text()

    def get_selected_ids(self):
        return [cmd_id for cmd_id, cb in self.checkboxes if cb.isChecked()]

    # ------------------------------------------------------------------
    def apply_theme(self, theme_mode: ThemeMode = None):
        if theme_mode is None:
            theme_mode = (ThemeMode.DARK
                          if Settings.get_theme_mode() == THEME_MODE_DARK
                          else ThemeMode.LIGHT)
        self._theme_mode = theme_mode
        is_dark = (theme_mode == ThemeMode.DARK)

        if is_dark:
            bg = "#2d2d2d"
            text = "#eeeeee"
            border = "#555"
            list_bg = "#232323"
            hint_bg = "#1e3a5f"
            hint_border = "#90caf9"
            hint_text = "#90caf9"
            toggle_bg = "#1976d2"
            toggle_fg = "#ffffff"
            toggle_hover = "#1565c0"
            card_bg = "#2b2b2b"
            card_border = "#3a3a3a"
            card_hover = "#333333"
        else:
            bg = "#ffffff"
            text = "#333333"
            border = "#d0d0d0"
            list_bg = "#f8f9fa"
            hint_bg = "#e3f2fd"
            hint_border = "#1976d2"
            hint_text = "#1976d2"
            toggle_bg = "#3498db"
            toggle_fg = "#ffffff"
            toggle_hover = "#5dade2"
            card_bg = "#ffffff"
            card_border = "#e0e0e0"
            card_hover = "#f0f4f8"

        self.setStyleSheet(f"""
            QDialog {{
                background-color: {bg};
            }}
            QDialog QLabel {{
                color: {text};
                background: transparent;
                font-size: 13px;
            }}
            QDialog QLabel#hintLabel {{
                background-color: {hint_bg};
                color: {hint_text};
                border-left: 4px solid {hint_border};
                border-radius: 4px;
                padding: 6px 10px;
                font-size: 12px;
            }}
            QDialog QListWidget#commandList {{
                background-color: {list_bg};
                border: 1px solid {border};
                border-radius: 6px;
                outline: none;
                padding: 4px;
            }}
            QDialog QListWidget#commandList::item {{
                background: transparent;
                border: none;
                padding: 0px;
                margin: 0px;
            }}
            QDialog QListWidget#commandList::item:selected {{
                background: transparent;
            }}

            /* 卡片式 item（与 ADB 工具箱一致） */
            QDialog QWidget#CommandCheckRow {{
                background-color: {card_bg};
                border: 1px solid {card_border};
                border-radius: 6px;
            }}
            QDialog QWidget#CommandCheckRow:hover {{
                background-color: {card_hover};
            }}
            QDialog QWidget#CommandCheckRow QCheckBox {{
                color: {text};
                background: transparent;
                font-size: 13px;
                spacing: 6px;
                padding: 0px;
            }}

            QDialog QPushButton#toggleBtn {{
                background-color: {toggle_bg};
                color: {toggle_fg};
                border: none;
                border-radius: 6px;
                font-weight: 500;
            }}
            QDialog QPushButton#toggleBtn:hover {{
                background-color: {toggle_hover};
            }}
            QDialog QPushButton#okBtn {{
                background-color: #27ae60;
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: 500;
            }}
            QDialog QPushButton#okBtn:hover {{
                background-color: #2ecc71;
            }}

            /* 滚动条：细样式 */
            QDialog QListWidget#commandList QScrollBar:vertical {{
                width: 6px;
                background: {'#3a3a3a' if is_dark else '#e0e0e0'};
                border-radius: 3px;
                margin: 0px;
            }}
            QDialog QListWidget#commandList QScrollBar::handle:vertical {{
                background: {'#666' if is_dark else '#c0c0c0'};
                border-radius: 3px;
                min-height: 20px;
            }}
            QDialog QListWidget#commandList QScrollBar::add-line:vertical,
            QDialog QListWidget#commandList QScrollBar::sub-line:vertical {{
                height: 0px;
                width: 0px;
            }}
            QDialog QListWidget#commandList QScrollBar::add-page:vertical,
            QDialog QListWidget#commandList QScrollBar::sub-page:vertical {{
                background: transparent;
            }}
        """)