# views/adb_dialogs/push_setup_dialog.py
"""推送参数对话框（PyQt6 + 主题适配）

取代原来的三步弹窗（选类型 QMessageBox → 选路径 QFileDialog → 填远程路径
QInputDialog）：本地文件/文件夹、远程路径在一个对话框里一次填完。
没有「取消」按钮——关窗口或按 Esc 即放弃，底部只留一个主操作「推送」。
"""
import os

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QPushButton, QFileDialog
)
from PyQt6.QtCore import Qt

from utils.theme import ThemeMode
from utils.settings import Settings, THEME_MODE_DARK

DEFAULT_REMOTE = "/sdcard/"


class PushSetupDialog(QDialog):
    """推送参数对话框：选本地文件/文件夹 + 远程路径，一次填完。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._theme_mode = ThemeMode.LIGHT
        self.local_path = ""      # exec() 接受后可读
        self.remote_path = ""

        self.setWindowTitle("推送文件")
        self.setModal(True)
        self.setFixedWidth(480)

        self._setup_ui()
        self.apply_theme()

    # ------------------------------------------------------------------
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        title = QLabel("📤 推送文件到设备")
        title.setObjectName("titleLabel")
        layout.addWidget(title)

        hint = QLabel("选择电脑上的文件或文件夹，推送到设备的指定目录")
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(12)

        grid.addWidget(self._make_field_label("本地文件"), 0, 0)
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("尚未选择，点右侧按钮选择文件或文件夹")
        self.path_edit.textChanged.connect(self._on_path_changed)
        grid.addWidget(self.path_edit, 0, 1)

        btn_col = QVBoxLayout()
        btn_col.setSpacing(6)
        self.pick_file_btn = QPushButton("选择文件")
        self.pick_file_btn.setObjectName("secondaryBtn")
        self.pick_file_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pick_file_btn.clicked.connect(self._on_pick_file)
        btn_col.addWidget(self.pick_file_btn)
        self.pick_dir_btn = QPushButton("选择文件夹")
        self.pick_dir_btn.setObjectName("secondaryBtn")
        self.pick_dir_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pick_dir_btn.clicked.connect(self._on_pick_dir)
        btn_col.addWidget(self.pick_dir_btn)
        grid.addLayout(btn_col, 0, 2)

        grid.addWidget(self._make_field_label("远程路径"), 1, 0)
        self.remote_edit = QLineEdit(DEFAULT_REMOTE)
        grid.addWidget(self.remote_edit, 1, 1)

        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)

        layout.addStretch()

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.push_btn = QPushButton("推送")
        self.push_btn.setObjectName("primaryBtn")
        self.push_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.push_btn.setFixedHeight(34)
        self.push_btn.setMinimumWidth(120)
        self.push_btn.setEnabled(False)   # 选了本地文件才能推
        self.push_btn.setDefault(True)
        self.push_btn.clicked.connect(self._on_push)
        btn_row.addWidget(self.push_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

    def _make_field_label(self, text):
        label = QLabel(text)
        label.setObjectName("fieldLabel")
        return label

    # ------------------------------------------------------------------
    def _on_pick_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择文件")
        if path:
            self.path_edit.setText(path)

    def _on_pick_dir(self):
        path = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if path:
            self.path_edit.setText(path)

    def _on_path_changed(self, text):
        self.push_btn.setEnabled(bool(text.strip()))

    def _on_push(self):
        path = self.path_edit.text().strip()
        remote = self.remote_edit.text().strip() or DEFAULT_REMOTE
        if not path or not os.path.exists(path):
            return
        # 文件夹推到目录：远程路径补成目录形式，与旧行为一致
        if os.path.isdir(path) and not remote.endswith('/'):
            remote += '/'
        self.local_path = path
        self.remote_path = remote
        self.accept()

    # ------------------------------------------------------------------
    def apply_theme(self, theme_mode: ThemeMode = None):
        if theme_mode is None:
            theme_mode = (ThemeMode.DARK
                          if Settings.get_theme_mode() == THEME_MODE_DARK
                          else ThemeMode.LIGHT)
        self._theme_mode = theme_mode

        if theme_mode == ThemeMode.DARK:
            bg, text, muted = "#2d2d2d", "#eeeeee", "#aaaaaa"
            border, field_bg = "#555", "#1e1e1e"
            primary_bg, primary_hover = "#3498db", "#2f81b8"
            secondary_bg, secondary_hover = "#3a3a3a", "#454545"
        else:
            bg, text, muted = "#ffffff", "#333333", "#7f8c8d"
            border, field_bg = "#d0d0d0", "#f7f7f7"
            primary_bg, primary_hover = "#1976d2", "#1565c0"
            secondary_bg, secondary_hover = "#f0f0f0", "#e0e0e0"

        self.setStyleSheet(f"""
            QDialog {{ background-color: {bg}; }}
            QDialog QLabel {{
                color: {text}; background: transparent; font-size: 13px;
            }}
            QDialog QLabel#titleLabel {{
                font-size: 16px; font-weight: bold; color: {text};
            }}
            QDialog QLabel#hintLabel {{
                color: {muted}; font-size: 12px;
            }}
            QDialog QLabel#fieldLabel {{ color: {muted}; }}
            QDialog QLineEdit {{
                background-color: {field_bg};
                color: {text};
                border: 1px solid {border};
                border-radius: 6px;
                padding: 6px 8px;
                font-size: 13px;
            }}
            QDialog QLineEdit:focus {{ border-color: {primary_bg}; }}
            QDialog QPushButton#secondaryBtn {{
                background-color: {secondary_bg};
                color: {text};
                border: 1px solid {border};
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 13px;
            }}
            QDialog QPushButton#secondaryBtn:hover {{
                background-color: {secondary_hover};
            }}
            QDialog QPushButton#primaryBtn {{
                background-color: {primary_bg};
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: 500;
                font-size: 14px;
            }}
            QDialog QPushButton#primaryBtn:hover {{
                background-color: {primary_hover};
            }}
            QDialog QPushButton#primaryBtn:disabled {{
                background-color: {secondary_bg};
                color: {muted};
            }}
        """)
