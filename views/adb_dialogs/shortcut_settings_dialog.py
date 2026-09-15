# views/adb_dialogs/shortcut_settings_dialog.py
"""快捷键设置对话框（PyQt6 + 主题适配）"""
import qtawesome as qta
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QPushButton,
    QHBoxLayout, QKeySequenceEdit, QScrollArea, QWidget,
    QLabel, QFrame
)
from PyQt6.QtCore import pyqtSignal, Qt

from utils.settings import Settings, THEME_MODE_DARK, DEFAULT_SHORTCUTS
from utils.theme import ThemeMode
from utils.dialogs import WarningDialog


# 展示名映射（key -> 中文）
_ACTION_LABELS = {
    "refresh_devices": "刷新设备",
    "execute_selected": "执行选中",
    "clear_log": "清空日志",
    "search_commands": "搜索指令",
    "add_command": "新增指令",
    "edit_command": "编辑指令",
    "delete_command": "删除指令",
    "wireless": "无线联调",
    "scrcpy": "设备投屏",
    "install_app": "安装应用",
    "push_file": "推送文件",
    "app_manager": "应用管理",
    "device_info": "硬件信息",
    "hprof_dump": "堆转储",
    "monkey": "Monkey",
    "crash_log": "崩溃日志",
    "anr_analyzer": "ANR 分析",
    "md5_query": "MD5 查询",
    "weak_network": "弱网模拟",
    "export_commands": "导出指令",
    "import_commands": "导入指令",
    "help_center": "帮助中心",
    "packet_capture": "网络抓包",
    "about": "关于",
}


class ShortcutSettingsDialog(QDialog):
    """快捷键设置"""

    shortcuts_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._theme_mode = ThemeMode.LIGHT

        self.setWindowTitle("快捷键设置")
        self.setModal(True)
        self.resize(520, 620)

        self.setup_ui()
        self.load_shortcuts()
        self.apply_theme()

    # ------------------------------------------------------------------
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        title = QLabel("⌨️ 快捷键设置")
        title.setObjectName("titleLabel")
        layout.addWidget(title)

        hint = QLabel("提示：留空表示不绑定快捷键；重复绑定时会覆盖旧值。")
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # 滚动容器
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        container = QWidget()
        form = QFormLayout(container)
        form.setSpacing(10)
        form.setContentsMargins(4, 4, 4, 4)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.key_edits = {}
        for key in DEFAULT_SHORTCUTS.keys():
            label = _ACTION_LABELS.get(key, key)
            edit = QKeySequenceEdit()
            edit.setMaximumWidth(220)
            self.key_edits[key] = edit
            form.addRow(f"{label}:", edit)

        scroll.setWidget(container)
        layout.addWidget(scroll, 1)

        # 按钮行
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.save_btn = QPushButton("保存")
        self.save_btn.setObjectName("saveBtn")
        self.save_btn.setFixedHeight(34)
        self.save_btn.setMinimumWidth(140)
        self.save_btn.setIcon(qta.icon('fa6s.floppy-disk', color='white'))
        self.save_btn.clicked.connect(self.save)
        btn_layout.addWidget(self.save_btn)

        btn_layout.addSpacing(10)

        self.reset_btn = QPushButton("恢复默认")
        self.reset_btn.setObjectName("resetBtn")
        self.reset_btn.setFixedHeight(34)
        self.reset_btn.setMinimumWidth(140)
        self.reset_btn.setIcon(qta.icon('fa6s.arrow-rotate-left', color='white'))
        self.reset_btn.clicked.connect(self.reset_defaults)
        btn_layout.addWidget(self.reset_btn)

        btn_layout.addStretch()
        layout.addLayout(btn_layout)

    # ------------------------------------------------------------------
    def load_shortcuts(self):
        shortcuts = Settings.get_shortcuts()
        for key, edit in self.key_edits.items():
            ks = shortcuts.get(key, "")
            if ks:
                edit.setKeySequence(ks)

    def save(self):
        # 收集所有有值的快捷键
        new_shortcuts = {}
        for key, edit in self.key_edits.items():
            ks = edit.keySequence().toString()
            if ks:
                new_shortcuts[key] = ks

        # 冲突检测（同一个键绑定给多个动作）
        key_to_actions = {}
        for action, ks in new_shortcuts.items():
            key_to_actions.setdefault(ks, []).append(action)

        conflicts = {k: v for k, v in key_to_actions.items() if len(v) > 1}
        if conflicts:
            lines = []
            for ks, actions in conflicts.items():
                labels = "、".join(_ACTION_LABELS.get(a, a) for a in actions)
                lines.append(f"{ks} → {labels}")
            WarningDialog.show_warning(
                self, "快捷键冲突",
                "以下快捷键被多个动作使用，请先修改：\n\n" + "\n".join(lines)
            )
            return

        # 保存
        settings = Settings.load()
        settings["shortcuts"] = new_shortcuts
        Settings.save(settings)

        self.shortcuts_changed.emit()
        self.accept()

    def reset_defaults(self):
        from utils.dialogs import ConfirmDeleteDialog
        if not ConfirmDeleteDialog.ask(
            self,
            title="恢复默认",
            message="确定将所有快捷键恢复为默认值吗？",
            detail="当前自定义的绑定会丢失。"
        ):
            return
        Settings.reset_shortcuts()
        self.load_shortcuts()

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
            input_bg = "#3c3c3c"
            input_border = "#555"
            focus_color = "#90caf9"
            hint_bg = "#3a2a1a"
            hint_border = "#ff9800"
            hint_text = "#ffb74d"
            reset_bg = "#555"
            reset_fg = "#eeeeee"
            reset_hover = "#666"
        else:
            bg = "#ffffff"
            text = "#333333"
            border = "#d0d0d0"
            input_bg = "#ffffff"
            input_border = "#d0d0d0"
            focus_color = "#1976d2"
            hint_bg = "#fff3e0"
            hint_border = "#ff9800"
            hint_text = "#e65100"
            reset_bg = "#f39c12"
            reset_fg = "#ffffff"
            reset_hover = "#f5b041"

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
            QDialog QKeySequenceEdit {{
                background-color: {input_bg};
                color: {text};
                border: 1px solid {input_border};
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 13px;
            }}
            QDialog QKeySequenceEdit:focus {{
                border-color: {focus_color};
            }}
            QDialog QScrollArea {{
                background: transparent;
                border: none;
            }}
            QDialog QScrollArea > QWidget > QWidget {{
                background: transparent;
            }}
            QDialog QPushButton#saveBtn {{
                background-color: #27ae60;
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: 500;
            }}
            QDialog QPushButton#saveBtn:hover {{
                background-color: #2ecc71;
            }}
            QDialog QPushButton#resetBtn {{
                background-color: {reset_bg};
                color: {reset_fg};
                border: none;
                border-radius: 6px;
                font-weight: 500;
            }}
            QDialog QPushButton#resetBtn:hover {{
                background-color: {reset_hover};
            }}
        """)