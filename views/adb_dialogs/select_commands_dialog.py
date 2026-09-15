# views/adb_dialogs/select_commands_dialog.py
"""自定义显示命令对话框（PyQt6 + 主题适配）"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout,
    QListWidget, QListWidgetItem, QCheckBox,
    QPushButton, QLabel, QWidget
)
from PyQt6.QtCore import Qt

from services.command_manager import CommandManager
from utils.theme import ThemeMode
from utils.settings import Settings, THEME_MODE_DARK


class SelectCommandsDialog(QDialog):
    """选择要在主列表中显示的命令"""

    def __init__(self, command_manager: CommandManager, parent=None):
        super().__init__(parent)
        self.command_manager = command_manager
        self._theme_mode = ThemeMode.LIGHT
        self.checkboxes = []

        self.setWindowTitle("自定义显示命令")
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

        title = QLabel("显示命令")
        title.setObjectName("titleLabel")
        layout.addWidget(title)

        tip = QLabel("勾选您希望在左侧列表中显示的命令（取消勾选则隐藏）")
        tip.setObjectName("hintLabel")
        tip.setWordWrap(True)
        layout.addWidget(tip)

        self.list_widget = QListWidget()
        self.list_widget.setObjectName("commandList")
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

        # 读取当前已勾选的 ID
        checked_ids = Settings.load_checked_commands()
        saved_ids = Settings.load_display_command_ids()
        # 如果之前没保存过任何过滤，视为全选
        if not saved_ids and not checked_ids:
            saved_ids = None  # None 表示"全选"

        for cmd in self.command_manager.get_all_commands():
            item = QListWidgetItem(self.list_widget)
            label = f"{cmd.name}  {'[预设]' if cmd.is_preset else '[自定义]'}"

            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(6, 4, 6, 4)
            cb = QCheckBox(label)
            if saved_ids is None:
                cb.setChecked(True)
            else:
                cb.setChecked(cmd.id in saved_ids)
            row_layout.addWidget(cb)
            row_layout.addStretch()

            item.setSizeHint(row.sizeHint())
            self.list_widget.setItemWidget(item, row)
            self.checkboxes.append((cmd.id, cb))

        self._update_toggle_button_text()

    def _update_toggle_button_text(self):
        if not self.checkboxes:
            return
        all_checked = all(cb.isChecked() for _, cb in self.checkboxes)
        self.toggle_all_btn.setText("全不选" if all_checked else "全选")

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

        if theme_mode == ThemeMode.DARK:
            bg = "#2d2d2d"
            text = "#eeeeee"
            border = "#555"
            list_bg = "#323232"
            item_hover = "#3a3a3a"
            hint_bg = "#1e3a5f"
            hint_border = "#90caf9"
            hint_text = "#90caf9"
            toggle_bg = "#1976d2"
            toggle_fg = "#ffffff"
            toggle_hover = "#1565c0"
        else:
            bg = "#ffffff"
            text = "#333333"
            border = "#d0d0d0"
            list_bg = "#f8f9fa"
            item_hover = "#e8f0fe"
            hint_bg = "#e3f2fd"
            hint_border = "#1976d2"
            hint_text = "#1976d2"
            toggle_bg = "#3498db"
            toggle_fg = "#ffffff"
            toggle_hover = "#5dade2"

        self.setStyleSheet(f"""
            QDialog {{
                background-color: {bg};
            }}
            QDialog QLabel {{
                color: {text};
                background: transparent;
                font-size: 13px;
            }}
            QDialog QLabel#titleLabel {{
                font-size: 16px;
                font-weight: bold;
                color: {text};
                padding-bottom: 4px;
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
                padding: 0px;
                border-bottom: 1px solid {border};
            }}
            QDialog QListWidget#commandList::item:selected {{
                background: {item_hover};
            }}
            QDialog QCheckBox {{
                color: {text};
                background: transparent;
                font-size: 13px;
                padding: 2px 0;
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
        """)