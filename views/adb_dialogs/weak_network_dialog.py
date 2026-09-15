# views/adb_dialogs/weak_network_dialog.py
"""弱网模拟对话框（PyQt6 + 主题适配）"""
import subprocess
import sys
import threading

import qtawesome as qta
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QSpinBox, QDoubleSpinBox, QPushButton, QLabel,
    QComboBox, QTextEdit, QGroupBox
)
from PyQt6.QtCore import Qt, pyqtSignal

from utils.adb_path import get_adb_path
from utils.theme import ThemeMode
from utils.settings import Settings, THEME_MODE_DARK
from utils.toast import show_toast
from utils.dialogs import WarningDialog, ConfirmDeleteDialog


# 预设场景
SCENE_PRESETS = {
    "自定义": None,
    "2G (GPRS/EDGE)": {"delay": 300, "loss": 5.0, "bandwidth": 50},
    "3G (HSPA)": {"delay": 100, "loss": 2.0, "bandwidth": 500},
    "4G (LTE)": {"delay": 50, "loss": 1.0, "bandwidth": 5000},
    "高延迟 (200ms)": {"delay": 200, "loss": 0.0, "bandwidth": 0},
    "高丢包 (20%)": {"delay": 0, "loss": 20.0, "bandwidth": 0},
    "弱信号 (高延迟+高丢包)": {"delay": 300, "loss": 15.0, "bandwidth": 200},
}


class WeakNetworkDialog(QDialog):
    """弱网模拟（tc netem）"""

    weak_network_changed = pyqtSignal(bool)  # (是否启用)

    def __init__(self, device_service, parent=None):
        super().__init__(parent)
        self.device_service = device_service
        self._theme_mode = ThemeMode.LIGHT
        self.tc_path = None
        self._processing = False

        self.setWindowTitle("弱网模拟")
        self.resize(520, 460)

        self.setup_ui()
        self.apply_theme()
        self._reset_network_rule()   # 静默清理可能残留的规则

    # ------------------------------------------------------------------
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("🌩️ 弱网模拟 (tc netem)")
        title.setObjectName("titleLabel")
        layout.addWidget(title)

        warning = QLabel(
            "⚠️ 弱网模拟需要设备已 root，且内核支持 netem。"
            "仅对指定网卡生效，断开 WiFi 或重启后规则会自动清除。"
        )
        warning.setObjectName("warningLabel")
        warning.setWordWrap(True)
        layout.addWidget(warning)

        # ---- 表单 ----
        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # 网卡
        self.iface_combo = QComboBox()
        self.iface_combo.setEditable(True)
        self.iface_combo.addItems(["wlan0", "eth0", "rmnet0"])
        self.iface_combo.setToolTip("通常 wlan0 为 WiFi、eth0 为以太网/TSU")
        form.addRow("网卡:", self.iface_combo)

        # 场景
        self.scene_combo = QComboBox()
        for name in SCENE_PRESETS:
            self.scene_combo.addItem(name)
        self.scene_combo.currentIndexChanged.connect(self._on_scene_selected)
        scene_row = QHBoxLayout()
        scene_row.addWidget(self.scene_combo, 1)

        self.help_btn = QPushButton("场景说明")
        self.help_btn.setObjectName("helpBtn")
        self.help_btn.setFixedWidth(110)
        self.help_btn.setFixedHeight(30)
        self.help_btn.clicked.connect(self._show_scene_help)
        scene_row.addWidget(self.help_btn)
        form.addRow("场景模板:", scene_row)

        # 延迟
        self.delay_spin = QSpinBox()
        self.delay_spin.setRange(0, 2000)
        self.delay_spin.setSuffix(" ms")
        self.delay_spin.setValue(200)
        form.addRow("延迟:", self.delay_spin)

        # 丢包
        self.loss_spin = QDoubleSpinBox()
        self.loss_spin.setRange(0, 100)
        self.loss_spin.setSuffix(" %")
        self.loss_spin.setSingleStep(0.5)
        self.loss_spin.setValue(5.0)
        form.addRow("丢包率:", self.loss_spin)

        # 带宽
        self.bandwidth_spin = QSpinBox()
        self.bandwidth_spin.setRange(0, 100000)
        self.bandwidth_spin.setSuffix(" kbit")
        self.bandwidth_spin.setSpecialValueText("不限")
        self.bandwidth_spin.setValue(0)
        form.addRow("带宽限制:", self.bandwidth_spin)

        layout.addLayout(form)

        # 提示：参数修改时切回自定义
        self.delay_spin.valueChanged.connect(self._reset_to_custom)
        self.loss_spin.valueChanged.connect(self._reset_to_custom)
        self.bandwidth_spin.valueChanged.connect(self._reset_to_custom)

        # 状态
        self.status_label = QLabel("就绪")
        self.status_label.setObjectName("statusLabel")
        layout.addWidget(self.status_label)

        # 按钮行
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)
        btn_layout.addStretch()

        self.apply_btn = QPushButton("应用")
        self.apply_btn.setObjectName("applyBtn")
        self.apply_btn.setFixedHeight(34)
        self.apply_btn.setMinimumWidth(150)
        self.apply_btn.clicked.connect(self._apply_limit)
        btn_layout.addWidget(self.apply_btn)

        self.clear_btn = QPushButton("清除")
        self.clear_btn.setObjectName("clearBtn")
        self.clear_btn.setFixedHeight(34)
        self.clear_btn.setMinimumWidth(150)
        self.clear_btn.clicked.connect(self._clear_limit)
        btn_layout.addWidget(self.clear_btn)

        btn_layout.addStretch()
        layout.addLayout(btn_layout)

    # ------------------------------------------------------------------
    def _get_serial(self):
        return self.device_service.serial if self.device_service else None

    def _run_adb_shell(self, cmd: str, timeout=10):
        serial = self._get_serial()
        if not serial:
            return "", "设备未连接", -1
        try:
            result = subprocess.run(
                [get_adb_path(), "-s", serial, "shell"] + cmd.split(),
                capture_output=True, text=True, timeout=timeout,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
                encoding='utf-8', errors='replace',
            )
            return (result.stdout or "").strip(), (result.stderr or "").strip(), result.returncode
        except subprocess.TimeoutExpired:
            return "", "Timeout", -1
        except Exception as e:
            return "", str(e), -1

    def _check_root(self) -> bool:
        out, _, rc = self._run_adb_shell("id", timeout=5)
        return rc == 0 and "uid=0" in out

    def _get_tc_path(self):
        if self.tc_path:
            return self.tc_path
        for cand in ["tc", "/system/bin/tc", "/system/xbin/tc"]:
            out, _, rc = self._run_adb_shell(f"command -v {cand} 2>/dev/null")
            if rc == 0 and out:
                self.tc_path = out.strip()
                return self.tc_path
        # 兜底：直接试 tc
        out, _, rc = self._run_adb_shell("tc -help 2>/dev/null")
        if rc == 0 or "Usage" in out:
            self.tc_path = "tc"
            return self.tc_path
        return None

    def _run_tc_cmd(self, cmd_args: str):
        tc = self._get_tc_path()
        if not tc:
            return "", "未找到 tc 命令，请确认设备已 root 且支持 netem", -1
        return self._run_adb_shell(f"{tc} {cmd_args}")

    # ------------------------------------------------------------------
    def _on_scene_selected(self, index):
        name = self.scene_combo.currentText()
        preset = SCENE_PRESETS.get(name)
        if preset is None:
            return

        # 阻塞信号避免循环触发
        self.delay_spin.blockSignals(True)
        self.loss_spin.blockSignals(True)
        self.bandwidth_spin.blockSignals(True)

        self.delay_spin.setValue(preset.get("delay", 0))
        self.loss_spin.setValue(preset.get("loss", 0.0))
        self.bandwidth_spin.setValue(preset.get("bandwidth", 0))

        self.delay_spin.blockSignals(False)
        self.loss_spin.blockSignals(False)
        self.bandwidth_spin.blockSignals(False)

        self.status_label.setText(f"已加载场景: {name}")

    def _reset_to_custom(self):
        if self.scene_combo.currentText() != "自定义":
            self.scene_combo.blockSignals(True)
            self.scene_combo.setCurrentText("自定义")
            self.scene_combo.blockSignals(False)

    def _show_scene_help(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("场景说明")
        dlg.resize(520, 430)
        lay = QVBoxLayout(dlg)
        text = QTextEdit()
        text.setReadOnly(True)
        text.setHtml("""
        <h3>📊 弱网场景参考表</h3>
        <table border="1" cellpadding="6" style="border-collapse: collapse; width:100%;">
        <tr style="background:#eee;">
            <th>场景</th><th>延迟(ms)</th><th>丢包率(%)</th><th>带宽(kbit)</th><th>典型效果</th>
        </tr>
        <tr><td>2G (GPRS/EDGE)</td><td>300~500</td><td>5~10</td><td>50~100</td><td>仅文本，图片加载缓慢</td></tr>
        <tr><td>3G (HSPA)</td><td>100~200</td><td>2~5</td><td>500~2000</td><td>网页可打开，视频缓冲频繁</td></tr>
        <tr><td>4G (LTE)</td><td>30~70</td><td>0.5~1</td><td>5000~30000</td><td>流畅视频，大部分应用正常</td></tr>
        <tr><td>高延迟</td><td>200~500</td><td>0</td><td>不限</td><td>模拟跨国链路，响应迟钝</td></tr>
        <tr><td>高丢包</td><td>0</td><td>15~30</td><td>不限</td><td>模拟信号差，数据丢失严重</td></tr>
        <tr><td>弱信号</td><td>200~400</td><td>10~20</td><td>100~500</td><td>综合模拟信号不稳定</td></tr>
        </table>
        <p>💡 数值为典型参考，实际效果因设备和网络环境而异。</p>
        """)
        lay.addWidget(text)
        dlg.exec()

    # ------------------------------------------------------------------
    def _reset_network_rule(self):
        """静默清理规则（初始化时调用）"""
        iface = self.iface_combo.currentText().strip()
        if not iface or not self._get_serial():
            return
        if not self._check_root():
            return
        self._run_tc_cmd(f"qdisc del dev {iface} root 2>/dev/null")

    def _apply_limit(self):
        if self._processing:
            return
        self._processing = True

        serial = self._get_serial()
        if not serial:
            WarningDialog.show_warning(self, "提示", "设备未连接")
            self._processing = False
            return

        iface = self.iface_combo.currentText().strip()
        if not iface:
            WarningDialog.show_warning(self, "提示", "请输入网卡名称")
            self._processing = False
            return

        # 检查 root
        if not self._check_root():
            WarningDialog.show_warning(
                self,
                "权限不足",
                "设备未 root 或 ADB 无 root 权限，无法使用弱网模拟。\n"
                "请确保设备已 root 并授予 ADB root 权限。"
            )
            self._processing = False
            return

        # 检查 tc
        if not self._get_tc_path():
            WarningDialog.show_warning(
                self, "缺少 tc",
                "设备上找不到 tc 命令，可能内核未编译 netem 支持。"
            )
            self._processing = False
            return

        # 清理旧规则
        self._run_tc_cmd(f"qdisc del dev {iface} root 2>/dev/null")

        delay = self.delay_spin.value()
        loss = self.loss_spin.value()
        bandwidth = self.bandwidth_spin.value()

        # 组装命令
        commands = []
        if bandwidth > 0:
            commands.append(f"qdisc add dev {iface} root handle 1: htb default 11")
            commands.append(f"class add dev {iface} parent 1: classid 1:1 htb rate {bandwidth}kbit")
            commands.append(f"class add dev {iface} parent 1:1 classid 1:11 htb rate {bandwidth}kbit")
            netem = f"qdisc add dev {iface} parent 1:11 handle 10: netem"
            if delay > 0:
                netem += f" delay {delay}ms"
            if loss > 0:
                netem += f" loss {loss}%"
            commands.append(netem)
        else:
            netem = f"qdisc add dev {iface} root netem"
            if delay > 0:
                netem += f" delay {delay}ms"
            if loss > 0:
                netem += f" loss {loss}%"
            commands.append(netem)

        self.apply_btn.setEnabled(False)
        self.status_label.setText("正在应用...")

        def worker():
            for cmd in commands:
                out, err, rc = self._run_tc_cmd(cmd)
                if rc != 0:
                    self.status_label.setText(f"应用失败: {err or out}")
                    self.apply_btn.setEnabled(True)
                    self._processing = False
                    return

            self.status_label.setText("规则已生效")
            show_toast(self, "弱网规则已生效", duration=2000)
            self.weak_network_changed.emit(True)
            self.apply_btn.setEnabled(True)
            self._processing = False

        threading.Thread(target=worker, daemon=True).start()

    def _clear_limit(self):
        if self._processing:
            return
        self._processing = True

        serial = self._get_serial()
        if not serial:
            WarningDialog.show_warning(self, "提示", "设备未连接")
            self._processing = False
            return

        iface = self.iface_combo.currentText().strip()
        if not iface:
            self._processing = False
            return

        if not self._check_root():
            WarningDialog.show_warning(self, "权限不足", "需要 root 权限")
            self._processing = False
            return

        self.clear_btn.setEnabled(False)
        self.status_label.setText("正在清除...")

        def worker():
            out, err, rc = self._run_tc_cmd(f"qdisc del dev {iface} root 2>/dev/null")
            self.clear_btn.setEnabled(True)

            if rc == 0:
                self.status_label.setText("规则已清除")
                show_toast(self, "网络限制已清除", duration=1500)
                self.weak_network_changed.emit(False)
            else:
                # 无规则也算成功
                if "No such file" in (err or out) or "Cannot find device" in (err or out):
                    self.status_label.setText("当前无限制规则")
                    self.weak_network_changed.emit(False)
                else:
                    self.status_label.setText(f"清除失败: {err or out}")
            self._processing = False

        threading.Thread(target=worker, daemon=True).start()

    def closeEvent(self, event):
        # 关闭时不自动清除规则（用户可能希望规则持续生效）
        # 但提示用户
        event.accept()

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
            help_bg = "#3498db"
            help_hover = "#5dade2"
            apply_bg = "#27ae60"
            apply_hover = "#2ecc71"
            clear_bg = "#d68910"
            clear_hover = "#f39c12"
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
            help_bg = "#3498db"
            help_hover = "#5dade2"
            apply_bg = "#27ae60"
            apply_hover = "#2ecc71"
            clear_bg = "#f39c12"
            clear_hover = "#f5b041"
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
                padding: 4px 0;
            }}
            QDialog QSpinBox,
            QDialog QDoubleSpinBox,
            QDialog QComboBox {{
                background-color: {input_bg};
                color: {text};
                border: 1px solid {input_border};
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 13px;
                min-height: 22px;
            }}
            QDialog QSpinBox:focus,
            QDialog QDoubleSpinBox:focus,
            QDialog QComboBox:focus {{
                border-color: {focus_color};
            }}
            QDialog QComboBox QAbstractItemView {{
                background-color: {input_bg};
                color: {text};
                border: 1px solid {input_border};
                selection-background-color: {focus_color};
                selection-color: white;
                outline: none;
            }}
            QDialog QPushButton#helpBtn {{
                background-color: {help_bg};
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: 500;
            }}
            QDialog QPushButton#helpBtn:hover {{
                background-color: {help_hover};
            }}
            QDialog QPushButton#applyBtn {{
                background-color: {apply_bg};
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: 500;
                font-size: 14px;
            }}
            QDialog QPushButton#applyBtn:hover {{
                background-color: {apply_hover};
            }}
            QDialog QPushButton#applyBtn:disabled {{
                background-color: {disabled_bg};
                color: {disabled_fg};
            }}
            QDialog QPushButton#clearBtn {{
                background-color: {clear_bg};
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: 500;
                font-size: 14px;
            }}
            QDialog QPushButton#clearBtn:hover {{
                background-color: {clear_hover};
            }}
            QDialog QPushButton#clearBtn:disabled {{
                background-color: {disabled_bg};
                color: {disabled_fg};
            }}
        """)