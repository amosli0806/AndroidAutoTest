# views/adb_dialogs/wireless_dialog.py
"""无线联调对话框（PyQt6 + 主题适配）"""
import subprocess
import sys

import qtawesome as qta
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QFrame
)
from PyQt6.QtCore import Qt

from utils.adb_path import get_adb_path
from utils.wireless_manager import WirelessManager
from utils.theme import ThemeMode
from utils.settings import Settings, THEME_MODE_DARK
from utils.toast import show_toast
from utils.dialogs import WarningDialog, ErrorDialog


class WirelessDialog(QDialog):
    """无线联调：获取 IP + 连接 / 断开"""

    def __init__(self, device_service, parent=None):
        super().__init__(parent)
        self.device_service = device_service
        self.wireless_manager = WirelessManager()
        self._theme_mode = ThemeMode.LIGHT

        self.setWindowTitle("无线联调")
        self.setModal(True)
        self.resize(520, 260)

        self.setup_ui()
        self.apply_theme()
        self._update_state()

    # ------------------------------------------------------------------
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        title = QLabel("📶 无线联调")
        title.setObjectName("titleLabel")
        layout.addWidget(title)

        # 版本警告横幅（默认隐藏）
        self.warning_label = QLabel(
            "⚠️ Android 12 及以上不支持本功能。请在开发者选项中开启「无线调试」并使用系统配对功能。"
        )
        self.warning_label.setObjectName("warningLabel")
        self.warning_label.setWordWrap(True)
        self.warning_label.hide()
        layout.addWidget(self.warning_label)

        # IP 输入行
        ip_layout = QHBoxLayout()
        ip_layout.setSpacing(8)

        ip_label = QLabel("设备 IP:")
        ip_label.setFixedWidth(60)
        ip_layout.addWidget(ip_label)

        self.ip_edit = QLineEdit()
        self.ip_edit.setPlaceholderText("例如 192.168.1.100")
        self.ip_edit.textChanged.connect(self._on_ip_changed)
        ip_layout.addWidget(self.ip_edit, 1)

        self.get_ip_btn = QPushButton("获取")
        self.get_ip_btn.setObjectName("getImeBtn")
        self.get_ip_btn.setFixedWidth(90)
        self.get_ip_btn.setFixedHeight(32)
        self.get_ip_btn.clicked.connect(self._on_get_ip)
        ip_layout.addWidget(self.get_ip_btn)

        layout.addLayout(ip_layout)

        # 按钮行
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)
        btn_layout.addStretch()

        self.connect_btn = QPushButton("连接")
        self.connect_btn.setObjectName("connectBtn")
        self.connect_btn.setFixedHeight(34)
        self.connect_btn.setMinimumWidth(160)
        self.connect_btn.clicked.connect(self._on_connect)
        btn_layout.addWidget(self.connect_btn)

        self.disconnect_btn = QPushButton("断开")
        self.disconnect_btn.setObjectName("disconnectBtn")
        self.disconnect_btn.setFixedHeight(34)
        self.disconnect_btn.setMinimumWidth(160)
        self.disconnect_btn.clicked.connect(self._on_disconnect)
        btn_layout.addWidget(self.disconnect_btn)

        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        # 状态标签
        self.status_label = QLabel("")
        self.status_label.setObjectName("statusLabel")
        layout.addWidget(self.status_label)

        layout.addStretch()

    # ------------------------------------------------------------------
    def _get_serial(self):
        return self.device_service.serial if self.device_service else None

    def _is_wireless_connected(self) -> bool:
        serial = self._get_serial()
        return bool(serial and ':' in serial)

    def _get_device_android_version(self) -> str:
        serial = self._get_serial()
        if not serial:
            return ""
        try:
            adb = get_adb_path()
            result = subprocess.run(
                [adb, "-s", serial, "shell", "getprop", "ro.build.version.release"],
                capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
                encoding='utf-8', errors='replace',
            )
            return (result.stdout or "").strip()
        except Exception:
            return ""

    def _update_state(self):
        serial = self._get_serial()

        # 未连接设备时整体禁用
        if not serial:
            self.get_ip_btn.setEnabled(False)
            self.connect_btn.setEnabled(False)
            self.disconnect_btn.setEnabled(False)
            self.status_label.setText("⚠️ 未检测到设备，请先在顶部工具栏连接设备")
            return

        # Android 12+ 警告
        version = self._get_device_android_version()
        is_android_12_plus = False
        try:
            major = int(version.split('.')[0]) if version else 0
            is_android_12_plus = major >= 12
        except Exception:
            pass

        if is_android_12_plus:
            self.warning_label.show()
            self.get_ip_btn.setEnabled(False)
            self.connect_btn.setEnabled(False)
            self.disconnect_btn.setEnabled(False)
            self.status_label.setText("")
            return
        else:
            self.warning_label.hide()

        # 已连接无线设备
        if self._is_wireless_connected():
            ip = serial.split(':')[0]
            self.ip_edit.setText(ip)
            self.ip_edit.setEnabled(False)
            self.get_ip_btn.setEnabled(False)
            self.connect_btn.setEnabled(False)
            self.disconnect_btn.setEnabled(True)
            self.status_label.setText("已连接无线设备")
        else:
            self.ip_edit.setEnabled(True)
            self.get_ip_btn.setEnabled(True)
            self.connect_btn.setEnabled(bool(self.ip_edit.text().strip()))
            self.disconnect_btn.setEnabled(False)
            self.status_label.setText("未连接无线设备")

    def _on_ip_changed(self, _text):
        if self._is_wireless_connected():
            return
        if self.connect_btn.isEnabled() or not self.ip_edit.text().strip():
            self.connect_btn.setEnabled(bool(self.ip_edit.text().strip()))

    def _on_get_ip(self):
        serial = self._get_serial()
        if not serial:
            show_toast(self, "设备未连接", duration=1500)
            return
        ip = self.wireless_manager.get_device_ip(serial)
        if ip:
            self.ip_edit.setText(ip)
            self.connect_btn.setEnabled(True)
            self.status_label.setText("已获取 IP，可点击连接")
        else:
            ErrorDialog.show_error(self, "获取失败", "无法获取设备 IP，请检查设备是否已连接 Wi-Fi")

    def _on_connect(self):
        ip = self.ip_edit.text().strip()
        if not ip:
            WarningDialog.show_warning(self, "提示", "请先输入 IP 地址")
            return

        serial = self._get_serial()
        ok, msg = self.wireless_manager.connect(ip, serial=serial)
        if ok:
            show_toast(self, "连接成功", duration=2000)
            self._update_state()
        else:
            ErrorDialog.show_error(self, "连接失败", msg)

    def _on_disconnect(self):
        serial = self._get_serial()
        ip = serial.split(':')[0] if serial and ':' in serial else None
        ok, msg = self.wireless_manager.disconnect(ip)
        if ok:
            show_toast(self, "已断开", duration=2000)
            self._update_state()
        else:
            ErrorDialog.show_error(self, "断开失败", msg)

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
            title_color = "#ffffff"
            warn_bg = "#3a2a1a"
            warn_border = "#ff9800"
            warn_text = "#ffb74d"
            status_color = "#aaaaaa"
            get_bg = "#3498db"
            get_hover = "#5dade2"
            connect_bg = "#27ae60"
            connect_hover = "#2ecc71"
            disconnect_bg = "#c0392b"
            disconnect_hover = "#e74c3c"
            disabled_bg = "#444"
            disabled_fg = "#777"
        else:
            bg = "#ffffff"
            text = "#333333"
            border = "#d0d0d0"
            input_bg = "#ffffff"
            input_border = "#d0d0d0"
            focus_color = "#1976d2"
            title_color = "#1a1a1a"
            warn_bg = "#fff3e0"
            warn_border = "#ff9800"
            warn_text = "#e65100"
            status_color = "#7f8c8d"
            get_bg = "#3498db"
            get_hover = "#5dade2"
            connect_bg = "#27ae60"
            connect_hover = "#2ecc71"
            disconnect_bg = "#e74c3c"
            disconnect_hover = "#f05a4a"
            disabled_bg = "#e0e0e0"
            disabled_fg = "#a0a0a0"

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
            QDialog QLabel#warningLabel {{
                background-color: {warn_bg};
                color: {warn_text};
                border-left: 4px solid {warn_border};
                border-radius: 4px;
                padding: 8px 12px;
                font-size: 12px;
            }}
            QDialog QLabel#statusLabel {{
                color: {status_color};
                font-size: 12px;
                padding-top: 4px;
            }}
            QDialog QLineEdit {{
                background-color: {input_bg};
                color: {text};
                border: 1px solid {input_border};
                border-radius: 4px;
                padding: 6px 10px;
                font-size: 13px;
            }}
            QDialog QLineEdit:focus {{
                border-color: {focus_color};
            }}
            QDialog QPushButton#getImeBtn {{
                background-color: {get_bg};
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: 500;
            }}
            QDialog QPushButton#getImeBtn:hover {{
                background-color: {get_hover};
            }}
            QDialog QPushButton#getImeBtn:disabled {{
                background-color: {disabled_bg};
                color: {disabled_fg};
            }}
            QDialog QPushButton#connectBtn {{
                background-color: {connect_bg};
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: 500;
                font-size: 14px;
            }}
            QDialog QPushButton#connectBtn:hover {{
                background-color: {connect_hover};
            }}
            QDialog QPushButton#connectBtn:disabled {{
                background-color: {disabled_bg};
                color: {disabled_fg};
            }}
            QDialog QPushButton#disconnectBtn {{
                background-color: {disconnect_bg};
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: 500;
                font-size: 14px;
            }}
            QDialog QPushButton#disconnectBtn:hover {{
                background-color: {disconnect_hover};
            }}
            QDialog QPushButton#disconnectBtn:disabled {{
                background-color: {disabled_bg};
                color: {disabled_fg};
            }}
        """)