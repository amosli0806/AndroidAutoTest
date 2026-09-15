# views/adb_dialogs/crash_log_dialog.py
"""崩溃日志对话框（PyQt6 + 主题适配）"""
import subprocess
import sys
import threading

import qtawesome as qta
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTextEdit,
    QPushButton, QLabel, QApplication, QFileDialog,
    QSizePolicy
)
from PyQt6.QtCore import Qt, pyqtSignal

from utils.adb_path import get_adb_path
from utils.theme import ThemeMode
from utils.settings import Settings, THEME_MODE_DARK
from utils.toast import show_toast
from utils.dialogs import ErrorDialog


class CrashLogDialog(QDialog):
    """查看设备的崩溃日志（logcat -b crash）"""

    log_ready = pyqtSignal(str)

    def __init__(self, device_service, parent=None):
        super().__init__(parent)
        self.device_service = device_service
        self._theme_mode = ThemeMode.LIGHT

        self.setWindowTitle("崩溃日志")
        self.resize(720, 540)
        self.setSizeGripEnabled(True)

        self.setup_ui()
        self.log_ready.connect(self._on_log_ready)
        self.apply_theme()

        self._load_logs()

    # ------------------------------------------------------------------
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        self.title = QLabel("💥 崩溃日志 (logcat -b crash)")
        self.title.setObjectName("titleLabel")
        layout.addWidget(self.title)

        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setObjectName("logText")
        self.text_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self.text_edit, 1)

        # 按钮行
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.copy_btn = QPushButton("复制")
        self.copy_btn.setObjectName("copyBtn")
        self.copy_btn.setFixedHeight(34)
        self.copy_btn.setMinimumWidth(140)
        self.copy_btn.setEnabled(False)
        self.copy_btn.clicked.connect(self._on_copy)
        btn_layout.addWidget(self.copy_btn)

        btn_layout.addSpacing(10)

        self.save_btn = QPushButton("保存到文件")
        self.save_btn.setObjectName("saveBtn")
        self.save_btn.setFixedHeight(34)
        self.save_btn.setMinimumWidth(140)
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self._on_save)
        btn_layout.addWidget(self.save_btn)

        btn_layout.addSpacing(10)

        self.close_btn = QPushButton("关闭")
        self.close_btn.setObjectName("closeBtn")
        self.close_btn.setFixedHeight(34)
        self.close_btn.setMinimumWidth(140)
        self.close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(self.close_btn)

        btn_layout.addStretch()
        layout.addLayout(btn_layout)

    # ------------------------------------------------------------------
    def _load_logs(self):
        if not self.device_service or not self.device_service.serial:
            self.log_ready.emit("⚠️ 设备未连接，无法读取崩溃日志。")
            return

        def worker():
            adb = get_adb_path()
            serial = self.device_service.serial
            try:
                result = subprocess.run(
                    [adb, "-s", serial, "logcat", "-b", "crash", "-d"],
                    capture_output=True, text=True, timeout=30,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
                    encoding='utf-8', errors='replace',
                )
                log = result.stdout or ""
                if not log.strip():
                    log = "没有崩溃日志"
                self.log_ready.emit(log)
            except Exception as e:
                self.log_ready.emit(f"读取崩溃日志失败: {e}")

        threading.Thread(target=worker, daemon=True).start()

    def _on_log_ready(self, text: str):
        self.text_edit.setText(text)
        has_valid = bool(text.strip()) and text.strip() != "没有崩溃日志" \
                     and not text.startswith("读取崩溃日志失败") \
                     and not text.startswith("⚠️")

        self.copy_btn.setEnabled(has_valid)
        self.save_btn.setEnabled(has_valid)

        if has_valid:
            self.copy_btn.setIcon(qta.icon('fa6s.copy', color='white'))
            self.save_btn.setIcon(qta.icon('fa6s.floppy-disk', color='white'))
        else:
            self.copy_btn.setIcon(qta.icon('fa6s.copy', color='#999'))
            self.save_btn.setIcon(qta.icon('fa6s.floppy-disk', color='#999'))

    def _on_copy(self):
        text = self.text_edit.toPlainText()
        if not text.strip():
            return
        QApplication.clipboard().setText(text)
        show_toast(self, "已复制到剪贴板", duration=1500)

    def _on_save(self):
        text = self.text_edit.toPlainText()
        if not text.strip():
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "保存崩溃日志", "crash_log.txt", "文本文件 (*.txt)"
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            show_toast(self, f"已保存至 {path}", duration=2500)
        except Exception as e:
            ErrorDialog.show_error(self, "保存失败", str(e))

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
            text_bg = "#1e1e1e"
            text_border = "#3a3a3a"
            title_color = "#ffffff"
            copy_bg = "#3498db"
            copy_hover = "#5dade2"
            save_bg = "#27ae60"
            save_hover = "#2ecc71"
            close_bg = "#555"
            close_fg = "#eeeeee"
            close_hover = "#666"
        else:
            bg = "#ffffff"
            text = "#333333"
            border = "#d0d0d0"
            text_bg = "#fafbfc"
            text_border = "#e0e0e0"
            title_color = "#1a1a1a"
            copy_bg = "#3498db"
            copy_hover = "#5dade2"
            save_bg = "#27ae60"
            save_hover = "#2ecc71"
            close_bg = "#f0f0f0"
            close_fg = "#333333"
            close_hover = "#e0e0e0"

        self.setStyleSheet(f"""
            QDialog {{
                background-color: {bg};
            }}
            QDialog QLabel#titleLabel {{
                font-size: 16px;
                font-weight: bold;
                color: {title_color};
                background: transparent;
                padding-bottom: 4px;
            }}
            QDialog QTextEdit#logText {{
                background-color: {text_bg};
                color: {text};
                border: 1px solid {text_border};
                border-radius: 6px;
                padding: 8px;
                font-family: Consolas, monospace;
                font-size: 12px;
            }}
            QDialog QPushButton#copyBtn {{
                background-color: {copy_bg};
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: 500;
            }}
            QDialog QPushButton#copyBtn:hover {{
                background-color: {copy_hover};
            }}
            QDialog QPushButton#copyBtn:disabled {{
                background-color: {close_bg};
                color: #999;
            }}
            QDialog QPushButton#saveBtn {{
                background-color: {save_bg};
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: 500;
            }}
            QDialog QPushButton#saveBtn:hover {{
                background-color: {save_hover};
            }}
            QDialog QPushButton#saveBtn:disabled {{
                background-color: {close_bg};
                color: #999;
            }}
            QDialog QPushButton#closeBtn {{
                background-color: {close_bg};
                color: {close_fg};
                border: 1px solid {border};
                border-radius: 6px;
                font-weight: 500;
            }}
            QDialog QPushButton#closeBtn:hover {{
                background-color: {close_hover};
            }}
        """)