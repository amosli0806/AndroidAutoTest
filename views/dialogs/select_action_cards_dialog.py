# views/dialogs/select_action_cards_dialog.py
"""「展示卡片」对话框：勾选动作卡片区显示哪几张卡片。

观感与交互**照抄「指令管理 → 展示指令」**（views/adb_dialogs/select_commands_dialog.py）：
蓝色提示条 + 卡片式勾选列表 + 「全选 / 确定」，浅色/深色两套取值逐项相同。
原来这里是个 QMenu 下拉，勾选项在菜单里渲染得很糙（勾号细小、没有卡片感），
换成同款对话框后两个"勾选显示范围"的入口长得一样。

行的"点哪儿都能勾"用指令那边的 RowWidget，不另写一份；只把行的 objectName
换成 ActionCardCheckRow（下面 QSS 里同名规则与那边的 CommandCheckRow 取值一致）。
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout,
    QListWidget, QListWidgetItem, QPushButton, QLabel
)
from PyQt6.QtCore import Qt, QSize

from utils.theme import ThemeMode
from utils.settings import Settings, THEME_MODE_DARK
from views.adb_toolbox_view import ClickableCheckBox
from views.adb_dialogs.select_commands_dialog import RowWidget


class SelectActionCardsDialog(QDialog):
    """选择要在「动作卡片」区显示哪些卡片"""

    def __init__(self, card_titles: dict, checked_types=None, parent=None):
        """
        card_titles:   {卡片类型: 标题}，顺序就是卡片区的展示顺序
        checked_types: 当前显示的卡片类型集合；None = 全部显示
        """
        super().__init__(parent)
        self.card_titles = dict(card_titles or {})
        self.checked_types = set(checked_types) if checked_types is not None else set(self.card_titles)
        self._theme_mode = ThemeMode.LIGHT
        self.checkboxes = []

        self.setWindowTitle("展示卡片")
        self.setModal(True)
        self.resize(440, 560)

        self.setup_ui()
        self.load_cards()
        self.apply_theme()

    # ------------------------------------------------------------------
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        tip = QLabel("勾选您希望在右侧卡片区中显示的动作卡片（取消勾选则隐藏）")
        tip.setObjectName("hintLabel")
        tip.setWordWrap(True)
        layout.addWidget(tip)

        self.list_widget = QListWidget()
        self.list_widget.setObjectName("actionCardList")
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
    def load_cards(self):
        self.checkboxes.clear()
        self.list_widget.clear()

        for card_type, title in self.card_titles.items():
            item = QListWidgetItem(self.list_widget)

            cb = ClickableCheckBox(title)
            cb.setFixedHeight(24)
            cb.setChecked(card_type in self.checked_types)

            # 卡片容器：点击空白区域也能切换
            row = RowWidget(cb, object_name="ActionCardCheckRow")
            row.setFixedHeight(38)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(12, 6, 12, 6)
            row_layout.setSpacing(8)
            row_layout.addWidget(cb, 1, Qt.AlignmentFlag.AlignVCenter)

            item.setSizeHint(QSize(0, 43))
            self.list_widget.setItemWidget(item, row)
            self.checkboxes.append((card_type, cb))

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

    def get_selected_types(self) -> list:
        """按卡片区顺序返回勾选的卡片类型"""
        return [card_type for card_type, cb in self.checkboxes if cb.isChecked()]

    # ------------------------------------------------------------------
    def apply_theme(self, theme_mode: ThemeMode = None):
        if theme_mode is None:
            theme_mode = (ThemeMode.DARK
                          if Settings.get_theme_mode() == THEME_MODE_DARK
                          else ThemeMode.LIGHT)
        self._theme_mode = theme_mode
        is_dark = (theme_mode == ThemeMode.DARK)

        # 取值与「指令管理 → 展示指令」逐项相同（那边是 SelectCommandsDialog.apply_theme）
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
            QDialog QListWidget#actionCardList {{
                background-color: {list_bg};
                border: 1px solid {border};
                border-radius: 6px;
                outline: none;
                padding: 4px;
            }}
            QDialog QListWidget#actionCardList::item {{
                background: transparent;
                border: none;
                padding: 0px;
                margin: 0px;
            }}
            QDialog QListWidget#actionCardList::item:selected {{
                background: transparent;
            }}

            /* 卡片式 item（与「展示指令」一致） */
            QDialog QWidget#ActionCardCheckRow {{
                background-color: {card_bg};
                border: 1px solid {card_border};
                border-radius: 6px;
            }}
            QDialog QWidget#ActionCardCheckRow:hover {{
                background-color: {card_hover};
            }}
            QDialog QWidget#ActionCardCheckRow QCheckBox {{
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
            QDialog QListWidget#actionCardList QScrollBar:vertical {{
                width: 6px;
                background: {'#3a3a3a' if is_dark else '#e0e0e0'};
                border-radius: 3px;
                margin: 0px;
            }}
            QDialog QListWidget#actionCardList QScrollBar::handle:vertical {{
                background: {'#666' if is_dark else '#c0c0c0'};
                border-radius: 3px;
                min-height: 20px;
            }}
            QDialog QListWidget#actionCardList QScrollBar::add-line:vertical,
            QDialog QListWidget#actionCardList QScrollBar::sub-line:vertical {{
                height: 0px;
                width: 0px;
            }}
            QDialog QListWidget#actionCardList QScrollBar::add-page:vertical,
            QDialog QListWidget#actionCardList QScrollBar::sub-page:vertical {{
                background: transparent;
            }}
        """)
