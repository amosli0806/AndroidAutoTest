# views/adb_dialogs/device_info_dialog.py
"""设备信息对话框（PyQt6 + 主题适配）"""
import subprocess
import sys
import threading
import re

import qtawesome as qta
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
    QTextEdit, QLabel, QApplication
)
from PyQt6.QtCore import Qt, pyqtSignal

from utils.adb_path import get_adb_path
from utils.theme import ThemeMode
from utils.settings import Settings, THEME_MODE_DARK
from utils.toast import show_toast


class DeviceInfoDialog(QDialog):
    """读取并显示设备信息"""

    info_ready = pyqtSignal(str)      # 后台线程读完 -> 主线程更新 UI

    def __init__(self, device_service, parent=None):
        super().__init__(parent)
        self.device_service = device_service
        self._theme_mode = ThemeMode.LIGHT

        self.setWindowTitle("设备信息")
        self.resize(560, 500)

        self.setup_ui()
        self.info_ready.connect(self._on_info_ready)
        self.apply_theme()

        # 初始占位
        self.info_text.setText("⏳ 信息获取中...")
        self.info_text.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._load_info()

    # ------------------------------------------------------------------
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        title = QLabel("📱 设备信息")
        title.setObjectName("titleLabel")
        layout.addWidget(title)

        self.info_text = QTextEdit()
        self.info_text.setReadOnly(True)
        self.info_text.setObjectName("infoText")
        layout.addWidget(self.info_text, 1)

        # 按钮行
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.copy_btn = QPushButton("复制")
        self.copy_btn.setObjectName("copyBtn")
        self.copy_btn.setFixedHeight(34)
        self.copy_btn.setMinimumWidth(140)
        self.copy_btn.setIcon(qta.icon('fa6s.copy', color='white'))
        self.copy_btn.clicked.connect(self._on_copy)
        btn_layout.addWidget(self.copy_btn)

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
    def _load_info(self):
        if not self.device_service or not self.device_service.serial:
            self.info_ready.emit("⚠️ 设备未连接，无法读取设备信息。")
            return

        def worker():
            info = {}
            errors = []
            adb = get_adb_path()
            serial = self.device_service.serial

            def run(cmd: str, timeout=5) -> str:
                try:
                    result = subprocess.run(
                        [adb, "-s", serial] + cmd.split(),
                        capture_output=True, text=True, timeout=timeout,
                        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
                        encoding='utf-8', errors='replace',
                    )
                    return result.stdout or ""
                except Exception:
                    return ""

            # 分辨率
            try:
                out = run("shell wm size")
                m = re.search(r'(\d+x\d+)', out)
                info['分辨率'] = m.group(1) if m else "未知"
            except Exception as e:
                errors.append(f"分辨率: {e}")
                info['分辨率'] = "获取失败"

            # 屏幕密度
            try:
                out = run("shell wm density")
                m = re.search(r'(\d+)', out)
                info['屏幕密度'] = m.group(1) if m else "未知"
            except Exception as e:
                errors.append(f"密度: {e}")
                info['屏幕密度'] = "获取失败"

            # 芯片型号
            info['芯片型号'] = run("shell getprop ro.board.platform").strip() or "未知"
            # CPU 型号
            info['CPU型号'] = run("shell getprop ro.product.board").strip() or "未知"

            # CPU 核心数
            try:
                out = run("shell cat /proc/cpuinfo")
                cores = re.findall(r'processor\s*:', out)
                info['CPU核心数'] = str(len(cores)) if cores else "未知"
            except Exception as e:
                errors.append(f"核心数: {e}")
                info['CPU核心数'] = "获取失败"

            # 总内存
            try:
                out = run("shell cat /proc/meminfo")
                m = re.search(r'MemTotal:\s+(\d+) kB', out)
                info['总内存(MB)'] = str(round(int(m.group(1)) / 1024)) if m else "未知"
            except Exception as e:
                errors.append(f"内存: {e}")
                info['总内存(MB)'] = "获取失败"

            # 安卓版本
            info['安卓版本'] = run("shell getprop ro.build.version.release").strip() or "未知"

            # 分区空间
            try:
                out = run("shell df /data /system")
                for line in out.splitlines():
                    if '/data' in line:
                        parts = line.split()
                        if len(parts) >= 4:
                            info['数据分区可用'] = parts[3]
                    elif '/system' in line:
                        parts = line.split()
                        if len(parts) >= 4:
                            info['系统分区可用'] = parts[3]
            except Exception as e:
                errors.append(f"分区: {e}")

            # 组装文本
            lines = []
            for k, v in info.items():
                lines.append(f"{k}: {v}")
            if errors:
                lines.append("")
                lines.append("[错误信息]")
                lines.extend(errors)

            self.info_ready.emit("\n".join(lines))

        threading.Thread(target=worker, daemon=True).start()

    def _on_info_ready(self, text: str):
        self.info_text.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.info_text.setText(text)

    def _on_copy(self):
        text = self.info_text.toPlainText()
        if not text.strip():
            return
        QApplication.clipboard().setText(text)
        show_toast(self, "已复制到剪贴板", duration=1500)

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
            close_bg = "#f0f0f0"
            close_fg = "#333333"
            close_hover = "#e0e0e0"

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
                color: {title_color};
                padding-bottom: 4px;
            }}
            QDialog QTextEdit#infoText {{
                background-color: {text_bg};
                color: {text};
                border: 1px solid {text_border};
                border-radius: 6px;
                padding: 10px;
                font-family: Consolas, "Microsoft YaHei", monospace;
                font-size: 13px;
            }}
            QDialog QPushButton#copyBtn {{
                background-color: #3498db;
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: 500;
            }}
            QDialog QPushButton#copyBtn:hover {{
                background-color: #5dade2;
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