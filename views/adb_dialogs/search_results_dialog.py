# views/adb_dialogs/search_results_dialog.py
"""搜索结果对话框（PyQt6 + 主题适配）"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QPushButton,
    QWidget, QSizePolicy
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont

import qtawesome as qta

from utils.theme import ThemeMode
from utils.settings import Settings, THEME_MODE_DARK


class SearchResultsDialog(QDialog):
    """展示预设/自定义/命令库的搜索结果"""

    execute_command = pyqtSignal(object)     # Command 对象
    copy_command = pyqtSignal(str)           # 命令文本

    def __init__(self, search_results: dict, parent=None):
        super().__init__(parent)
        self.search_results = search_results
        self._theme_mode = ThemeMode.LIGHT
        self._keyword = ""

        self.setWindowTitle("搜索结果")
        self.resize(640, 500)

        self.setup_ui()
        self.apply_theme()
        self.populate_results()

    # ------------------------------------------------------------------
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        self.keyword_label = QLabel("")
        self.keyword_label.setObjectName("keywordLabel")
        layout.addWidget(self.keyword_label)

        self.list_widget = QListWidget()
        self.list_widget.setObjectName("resultsList")
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self.list_widget, 1)

    def set_keyword(self, keyword: str):
        self._keyword = keyword
        self.keyword_label.setText(f"搜索关键词：{keyword}")

    # ------------------------------------------------------------------
    def populate_results(self):
        self.list_widget.clear()

        for cmd in self.search_results.get("preset", []):
            self._add_command_item(cmd, "预设", True)

        for cmd in self.search_results.get("custom", []):
            self._add_command_item(cmd, "自定义", True)

        for cmd in self.search_results.get("adb_library", []):
            self._add_adb_library_item(cmd)

    def _add_command_item(self, cmd, cmd_type: str, can_execute: bool):
        item = QListWidgetItem()
        widget = self._build_command_widget(cmd, cmd_type, can_execute)
        item.setSizeHint(widget.sizeHint())
        self.list_widget.addItem(item)
        self.list_widget.setItemWidget(item, widget)

    def _build_command_widget(self, cmd, cmd_type: str, can_execute: bool) -> QWidget:
        widget = QWidget()
        widget.setObjectName("resultItem")
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        # 标题行
        name_label = QLabel(f"【{cmd_type}】{cmd.name}")
        name_label.setObjectName("itemTitle")
        f = QFont()
        f.setBold(True)
        name_label.setFont(f)
        layout.addWidget(name_label)

        if cmd.description:
            desc_label = QLabel(cmd.description)
            desc_label.setObjectName("itemDesc")
            desc_label.setWordWrap(True)
            layout.addWidget(desc_label)

        cmd_label = QLabel(f"命令：{cmd.command_text}")
        cmd_label.setObjectName("itemCommand")
        cmd_label.setWordWrap(True)
        cmd_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        layout.addWidget(cmd_label)

        # 按钮行
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        if can_execute:
            exec_btn = QPushButton("执行")
            exec_btn.setObjectName("itemExecBtn")
            exec_btn.setIcon(qta.icon('fa6s.play', color='white'))
            exec_btn.setFixedHeight(28)
            exec_btn.setMinimumWidth(90)
            exec_btn.clicked.connect(lambda: self.execute_command.emit(cmd))
            btn_layout.addWidget(exec_btn)

        layout.addLayout(btn_layout)
        return widget

    def _add_adb_library_item(self, cmd: dict):
        item = QListWidgetItem()
        widget = self._build_adb_widget(cmd)
        item.setSizeHint(widget.sizeHint())
        self.list_widget.addItem(item)
        self.list_widget.setItemWidget(item, widget)

    def _build_adb_widget(self, cmd: dict) -> QWidget:
        widget = QWidget()
        widget.setObjectName("resultItem")
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        # 命令 + 分类
        head = QHBoxLayout()
        cmd_label = QLabel(cmd["command"])
        cmd_label.setObjectName("itemCommand")
        f = QFont()
        f.setFamily("Consolas")
        f.setBold(True)
        cmd_label.setFont(f)
        cmd_label.setWordWrap(True)
        cmd_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        head.addWidget(cmd_label, 1)

        cat_label = QLabel(f"[{cmd['category']}]")
        cat_label.setObjectName("itemCategory")
        cat_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        head.addWidget(cat_label, 0)
        layout.addLayout(head)

        if cmd.get("description"):
            desc_label = QLabel(cmd["description"])
            desc_label.setObjectName("itemDesc")
            desc_label.setWordWrap(True)
            layout.addWidget(desc_label)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        copy_btn = QPushButton("复制")
        copy_btn.setObjectName("itemCopyBtn")
        copy_btn.setIcon(qta.icon('fa6s.copy', color='white'))
        copy_btn.setFixedHeight(28)
        copy_btn.setMinimumWidth(90)
        copy_btn.clicked.connect(lambda: self.copy_command.emit(cmd["command"]))
        btn_layout.addWidget(copy_btn)
        layout.addLayout(btn_layout)

        return widget

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
            sub_text = "#999999"
            cmd_text = "#90caf9"
            border = "#555"
            list_bg = "#232323"
            item_bg = "#2b2b2b"
            item_hover = "#333333"
            title_color = "#ffffff"
            cat_color = "#888888"
            exec_bg = "#27ae60"
            exec_hover = "#2ecc71"
            copy_bg = "#3498db"
            copy_hover = "#5dade2"
        else:
            bg = "#ffffff"
            text = "#333333"
            sub_text = "#7f8c8d"
            cmd_text = "#2980b9"
            border = "#d0d0d0"
            list_bg = "#ffffff"
            item_bg = "#f8f9fa"
            item_hover = "#eef1f5"
            title_color = "#1a1a1a"
            cat_color = "#95a5a6"
            exec_bg = "#27ae60"
            exec_hover = "#2ecc71"
            copy_bg = "#3498db"
            copy_hover = "#5dade2"

        self.setStyleSheet(f"""
            QDialog {{
                background-color: {bg};
            }}
            QDialog QLabel#keywordLabel {{
                color: {title_color};
                font-size: 14px;
                font-weight: bold;
                background: transparent;
                padding: 4px 0;
            }}
            QDialog QListWidget#resultsList {{
                background-color: {list_bg};
                border: 1px solid {border};
                border-radius: 6px;
                outline: none;
                padding: 4px;
            }}
            QDialog QListWidget#resultsList::item {{
                background: transparent;
                border-bottom: 1px solid {border};
                padding: 0px;
            }}
            QDialog QListWidget#resultsList::item:selected {{
                background: {item_hover};
            }}
            QWidget#resultItem {{
                background: {item_bg};
                border-radius: 6px;
            }}
            QWidget#resultItem QLabel#itemTitle {{
                color: {title_color};
                font-size: 14px;
                background: transparent;
            }}
            QWidget#resultItem QLabel#itemDesc {{
                color: {sub_text};
                font-size: 12px;
                background: transparent;
            }}
            QWidget#resultItem QLabel#itemCommand {{
                color: {cmd_text};
                font-size: 12px;
                font-family: Consolas, monospace;
                background: transparent;
            }}
            QWidget#resultItem QLabel#itemCategory {{
                color: {cat_color};
                font-size: 11px;
                background: transparent;
            }}
            QWidget#resultItem QPushButton#itemExecBtn {{
                background-color: {exec_bg};
                color: white;
                border: none;
                border-radius: 6px;
                font-size: 12px;
                font-weight: 500;
            }}
            QWidget#resultItem QPushButton#itemExecBtn:hover {{
                background-color: {exec_hover};
            }}
            QWidget#resultItem QPushButton#itemCopyBtn {{
                background-color: {copy_bg};
                color: white;
                border: none;
                border-radius: 6px;
                font-size: 12px;
                font-weight: 500;
            }}
            QWidget#resultItem QPushButton#itemCopyBtn:hover {{
                background-color: {copy_hover};
            }}
        """)