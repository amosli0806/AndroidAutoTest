# views/adb_dialogs/custom_command_dialog.py
"""新增/编辑自定义指令对话框（PyQt6 + 主题适配）"""
from PyQt6.QtWidgets import (
    QLabel, QDialog, QVBoxLayout, QFormLayout,
    QLineEdit, QTextEdit, QCheckBox, QSpinBox,
    QPushButton, QHBoxLayout
)
from PyQt6.QtCore import Qt

from models.command import Command
from utils.toast import show_toast
from utils.theme import ThemeMode
from utils.settings import Settings, THEME_MODE_DARK
from PyQt6.QtWidgets import QStyleOptionButton, QStyle
from PyQt6.QtGui import QPainter, QPen, QColor


class BorderedCheckBox(QCheckBox):
    """复选框：保留 Fusion 默认绘制（勾选时有 √），额外叠加明显的边框。
    与 execute_view / perf_view / task_view 中同名控件保持一致。"""

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

class CustomCommandDialog(QDialog):
    """自定义指令对话框：新增或编辑"""

    def __init__(self, parent=None, command: Command = None):
        super().__init__(parent)
        self.command = command
        self._theme_mode = ThemeMode.LIGHT

        self.setWindowTitle("新增指令" if not command else "编辑指令")
        self.setModal(True)
        self.resize(530, 520)
        self.setup_ui()

        if command:
            self.load_command()

        # 初始应用主题
        self.apply_theme()

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # ---- 注意事项标签 ----
        self.hint_label = QLabel(
            "💡 注意事项：\n"
            "1. pull 命令示例：pull 本地文件夹路径 {output_dir}/{safe_device_serial}/\n"
            "2. 请勿在命令中包含设备选择参数（如 -s），程序会自动处理"
        )
        self.hint_label.setWordWrap(True)
        self.hint_label.setObjectName("hintLabel")
        layout.addWidget(self.hint_label)

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # ---- 功能名称 ----
        self.name_edit = QLineEdit()
        self.name_edit.setMaxLength(18)
        self.name_edit.setPlaceholderText("最多18个汉字")
        form.addRow("功能名称:", self.name_edit)

        # ---- ADB 命令内容 ----
        self.command_edit = QTextEdit()
        self.command_edit.setPlaceholderText("输入 ADB 命令，多条用 ; 分隔")
        self.command_edit.setMinimumHeight(80)
        form.addRow("命令内容:", self.command_edit)

        # ---- 功能描述 ----
        self.desc_edit = QLineEdit()
        self.desc_edit.setPlaceholderText("可选，简要描述该命令的用途")
        form.addRow("功能描述:", self.desc_edit)

        # ---- 定时执行 ----
        self.timer_check = BorderedCheckBox("定时执行")
        form.addRow("", self.timer_check)

        self.interval_spin = QSpinBox()
        self.interval_spin.setSuffix(" 秒")
        self.interval_spin.setMaximum(10800)
        self.interval_spin.setValue(3600)
        self.interval_spin.setEnabled(False)
        self.timer_check.toggled.connect(self.interval_spin.setEnabled)
        form.addRow("执行间隔:", self.interval_spin)

        # ---- 循环执行 ----
        self.loop_check = BorderedCheckBox("循环执行")
        form.addRow("", self.loop_check)

        self.loop_count_spin = QSpinBox()
        self.loop_count_spin.setMinimum(1)
        self.loop_count_spin.setMaximum(500000)
        self.loop_count_spin.setValue(20)
        self.loop_count_spin.setEnabled(False)
        self.loop_check.toggled.connect(self.loop_count_spin.setEnabled)
        form.addRow("循环次数:", self.loop_count_spin)

        # ---- 显示执行/停止按钮 ----
        self.stop_btn_check = BorderedCheckBox("显示执行/停止按钮")
        self.stop_btn_check.setChecked(True)
        form.addRow("", self.stop_btn_check)

        # ---- 保存输出到文件 ----
        self.save_output_check = BorderedCheckBox("保存输出到文件")
        form.addRow("", self.save_output_check)

        # ---- 无超时执行 ----
        self.no_timeout_check = BorderedCheckBox("无超时执行（适用于长时间运行的命令）")
        self.no_timeout_check.setToolTip("勾选后该命令不受 30 秒超时限制")
        form.addRow("", self.no_timeout_check)

        # ---- 预置命令提示 ----
        self.preset_hint = QLabel("（预置命令不可修改循环/定时设置）")
        self.preset_hint.setObjectName("presetHint")
        self.preset_hint.setVisible(False)
        form.addRow("", self.preset_hint)

        layout.addLayout(form)

        # ---- 按钮区 ----
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.ok_btn = QPushButton("确定")
        self.ok_btn.setObjectName("okBtn")
        self.ok_btn.setFixedHeight(32)
        self.ok_btn.setMinimumWidth(140)
        self.ok_btn.clicked.connect(self._on_ok_clicked)
        btn_layout.addWidget(self.ok_btn)

        btn_layout.addSpacing(10)

        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setObjectName("cancelBtn")
        self.cancel_btn.setFixedHeight(32)
        self.cancel_btn.setMinimumWidth(140)
        self.cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self.cancel_btn)

        btn_layout.addStretch()
        layout.addLayout(btn_layout)

    # ------------------------------------------------------------------
    # 加载已有命令
    # ------------------------------------------------------------------
    def load_command(self):
        self.name_edit.setText(self.command.name)
        self.command_edit.setPlainText(self.command.command_text)
        self.desc_edit.setText(self.command.description)
        if self.command.interval:
            self.timer_check.setChecked(True)
            self.interval_spin.setValue(self.command.interval)
        if self.command.loop_count:
            self.loop_check.setChecked(True)
            self.loop_count_spin.setValue(self.command.loop_count)
        self.stop_btn_check.setChecked(self.command.show_stop_button)
        self.save_output_check.setChecked(self.command.save_output)
        self.no_timeout_check.setChecked(self.command.no_timeout)
        if self.command.is_preset:
            self._apply_preset_restrictions()

    def _apply_preset_restrictions(self):
        self.timer_check.setEnabled(False)
        self.interval_spin.setEnabled(False)
        self.loop_check.setEnabled(False)
        self.loop_count_spin.setEnabled(False)
        self.preset_hint.setVisible(True)

    def _on_ok_clicked(self):
        """确定前先做校验：失败不关闭对话框"""
        name = self.name_edit.text().strip()
        cmd_text = self.command_edit.toPlainText().strip()
        if not name:
            show_toast(self, "指令名称不能为空", duration=2000)
            return
        if not cmd_text:
            show_toast(self, "命令内容不能为空", duration=2000)
            return
        self.accept()
    # ------------------------------------------------------------------
    # 获取结果
    # ------------------------------------------------------------------
    def get_command(self) -> Command:
        name = self.name_edit.text().strip()
        cmd_text = self.command_edit.toPlainText().strip()
        desc = self.desc_edit.text().strip()

        interval = self.interval_spin.value() if self.timer_check.isChecked() else None
        loop_count = self.loop_count_spin.value() if self.loop_check.isChecked() else None

        show_stop = self.stop_btn_check.isChecked()
        save_output = self.save_output_check.isChecked()
        no_timeout = self.no_timeout_check.isChecked()

        cmd_id = self.command.id if self.command else None
        is_preset = self.command.is_preset if self.command else False

        if is_preset:
            interval = None
            loop_count = None

        return Command(
            id=cmd_id,
            name=name,
            command_text=cmd_text,
            description=desc,
            interval=interval,
            loop_count=loop_count,
            show_stop_button=show_stop,
            is_preset=is_preset,
            save_output=save_output,
            no_timeout=no_timeout
        )

    # ------------------------------------------------------------------
    # 主题适配
    # ------------------------------------------------------------------
    def apply_theme(self, theme_mode: ThemeMode = None):
        """应用主题（日夜模式 + 壁纸）"""
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
            hint_bg = "#3a2a1a"
            hint_border = "#ff9800"
            hint_text = "#ffb74d"
            preset_text = "#999999"
            btn_cancel_bg = "#555"
            btn_cancel_fg = "#eeeeee"
            btn_cancel_hover = "#666"
        else:
            bg = "#ffffff"
            text = "#333333"
            border = "#d0d0d0"
            input_bg = "#ffffff"
            input_border = "#d0d0d0"
            hint_bg = "#fff3e0"
            hint_border = "#ff9800"
            hint_text = "#e65100"
            preset_text = "#999999"
            btn_cancel_bg = "#f0f0f0"
            btn_cancel_fg = "#333333"
            btn_cancel_hover = "#e0e0e0"

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
                padding: 8px 12px;
                font-weight: bold;
            }}
            QDialog QLabel#presetHint {{
                color: {preset_text};
                font-size: 12px;
            }}
            QDialog QLineEdit,
            QDialog QTextEdit,
            QDialog QSpinBox {{
                background-color: {input_bg};
                color: {text};
                border: 1px solid {input_border};
                border-radius: 4px;
                padding: 5px 8px;
                font-size: 13px;
            }}
            QDialog QLineEdit:focus,
            QDialog QTextEdit:focus,
            QDialog QSpinBox:focus {{
                border-color: {hint_border};
            }}
            QDialog QCheckBox {{
                color: {text};
                background: transparent;
                font-size: 13px;
            }}
            QDialog QPushButton#okBtn {{
                background-color: #27ae60;
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: 500;
                font-size: 14px;
            }}
            QDialog QPushButton#okBtn:hover {{
                background-color: #2ecc71;
            }}
            QDialog QPushButton#okBtn:pressed {{
                background-color: #1e8449;
            }}
            QDialog QPushButton#cancelBtn {{
                background-color: {btn_cancel_bg};
                color: {btn_cancel_fg};
                border: 1px solid {border};
                border-radius: 6px;
                font-weight: 500;
                font-size: 14px;
            }}
            QDialog QPushButton#cancelBtn:hover {{
                background-color: {btn_cancel_hover};
            }}
        """)