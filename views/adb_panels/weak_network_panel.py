# views/adb_panels/weak_network_panel.py
"""弱网模拟面板

原「弱网模拟对话框」的内嵌版：逻辑不变，去掉窗口相关代码，
样式选择器由 QDialog 改为 #WeakNetworkPanel，避免影响页面内其它控件。
"""
import subprocess
import sys
import threading

import qtawesome as qta
from PyQt6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGridLayout,
    QSpinBox, QDoubleSpinBox, QPushButton, QLabel,
    QComboBox, QTextEdit, QGroupBox
)
from PyQt6.QtCore import Qt, pyqtSignal

from utils.adb_path import get_adb_path
from utils.theme import ThemeMode
from utils.settings import Settings, THEME_MODE_DARK
from utils.toast import show_toast
from utils.dialogs import WarningDialog, ConfirmDeleteDialog
from views.adb_panels.common import (form_label, panel_chrome_qss, panel_header,
                                     panel_separator, disable_context_menus)


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

# 应用/清除的结果（子线程通过信号回传，主线程据此更新界面/弹窗）
RESULT_OK = "ok"
RESULT_ERROR = "error"
RESULT_NO_ROOT = "no_root"
RESULT_NO_TC = "no_tc"


class WeakNetworkPanel(QWidget):
    """弱网模拟（tc netem）"""

    # 结果写进主窗口底部"虫师日志"（原对话框是弹 Toast / 面板状态文案）
    log_message = pyqtSignal(str)
    # 子线程 -> 主线程：应用/清除完成（不做跨线程 UI 操作）
    _apply_finished = pyqtSignal(str, str)   # (结果, 详情)
    _clear_finished = pyqtSignal(str, str)

    def __init__(self, device_service=None, parent=None):
        super().__init__(parent)
        self.device_service = device_service
        self._theme_mode = ThemeMode.LIGHT
        self.tc_path = None
        self._processing = False

        self.setObjectName("WeakNetworkPanel")

        self.setup_ui()
        self._apply_finished.connect(self._on_apply_finished)
        self._clear_finished.connect(self._on_clear_finished)
        self.apply_theme()
        self._reset_network_rule()   # 静默清理可能残留的规则

    # ------------------------------------------------------------------
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)

        # 标题行：标题 + 一行说明（把原来那块橙色告警压成灰字说明）
        header_row = panel_header(
            "弱网模拟",
            "需设备已 root 且内核支持 netem；仅对指定网卡生效，断开 WiFi 或重启后规则自动清除"
        )

        # 应用 / 清除按钮放在标题行右侧（说明文字 stretch=1 会自动把它们推到最右）
        self.apply_btn = QPushButton("应用")
        self.apply_btn.setObjectName("applyBtn")
        self.apply_btn.setFixedHeight(30)
        self.apply_btn.setMinimumWidth(110)
        self.apply_btn.clicked.connect(self._apply_limit)
        header_row.addWidget(self.apply_btn)

        self.clear_btn = QPushButton("清除")
        self.clear_btn.setObjectName("clearBtn")
        self.clear_btn.setFixedHeight(30)
        self.clear_btn.setMinimumWidth(110)
        self.clear_btn.clicked.connect(self._clear_limit)
        header_row.addWidget(self.clear_btn)

        layout.addLayout(header_row)
        layout.addWidget(panel_separator(self))

        # ---- 参数：两列排布，比单列表单紧凑一半 ----
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)

        # 网卡 | 场景模板 + 场景说明
        self.iface_combo = QComboBox()
        self.iface_combo.setEditable(True)
        self.iface_combo.addItems(["wlan0", "eth0", "rmnet0"])
        self.iface_combo.setToolTip("通常 wlan0 为 WiFi、eth0 为以太网/TSU")
        grid.addWidget(form_label("网卡:"), 0, 0)
        grid.addWidget(self.iface_combo, 0, 1)

        self.scene_combo = QComboBox()
        for name in SCENE_PRESETS:
            self.scene_combo.addItem(name)
        self.scene_combo.currentIndexChanged.connect(self._on_scene_selected)
        scene_cell = QWidget()
        scene_row = QHBoxLayout(scene_cell)
        scene_row.setContentsMargins(0, 0, 0, 0)
        scene_row.setSpacing(6)
        scene_row.addWidget(self.scene_combo, 1)
        self.help_btn = QPushButton("场景说明")
        self.help_btn.setObjectName("helpBtn")
        self.help_btn.setFixedHeight(32)  # 与同行输入框等高
        self.help_btn.setMinimumWidth(88)
        self.help_btn.clicked.connect(self._show_scene_help)
        scene_row.addWidget(self.help_btn)
        grid.addWidget(form_label("场景模板:"), 0, 2)
        grid.addWidget(scene_cell, 0, 3)

        # 延迟 | 丢包率
        self.delay_spin = QSpinBox()
        self.delay_spin.setRange(0, 2000)
        self.delay_spin.setSuffix(" ms")
        self.delay_spin.setValue(200)
        grid.addWidget(form_label("延迟:"), 1, 0)
        grid.addWidget(self.delay_spin, 1, 1)

        self.loss_spin = QDoubleSpinBox()
        self.loss_spin.setRange(0, 100)
        self.loss_spin.setSuffix(" %")
        self.loss_spin.setSingleStep(0.5)
        self.loss_spin.setValue(5.0)
        grid.addWidget(form_label("丢包率:"), 1, 2)
        grid.addWidget(self.loss_spin, 1, 3)

        # 带宽限制（应用/清除按钮已移到标题行右侧）
        self.bandwidth_spin = QSpinBox()
        self.bandwidth_spin.setRange(0, 100000)
        self.bandwidth_spin.setSuffix(" kbit")
        self.bandwidth_spin.setSpecialValueText("不限")
        self.bandwidth_spin.setValue(0)
        grid.addWidget(form_label("带宽限制:"), 2, 0)
        grid.addWidget(self.bandwidth_spin, 2, 1)

        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        layout.addLayout(grid)

        # 状态/结果不在这里显示：应用/清除结果写进"虫师日志"
        self.delay_spin.valueChanged.connect(self._reset_to_custom)
        self.loss_spin.valueChanged.connect(self._reset_to_custom)
        self.bandwidth_spin.valueChanged.connect(self._reset_to_custom)

        # 面板里所有输入控件禁用右键编辑菜单
        disable_context_menus(self)

    # ------------------------------------------------------------------
    def set_device_service(self, device_service):
        """面板先构建、后注入设备服务（视图先于控制器创建）"""
        self.device_service = device_service
        self._reset_network_rule()   # 与原对话框一致：打开时静默清理残留规则

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

    def _reset_to_custom(self):
        if self.scene_combo.currentText() != "自定义":
            self.scene_combo.blockSignals(True)
            self.scene_combo.setCurrentText("自定义")
            self.scene_combo.blockSignals(False)

    def _show_scene_help(self):
        # 表头/边框配色必须跟随主题：表格正文颜色来自全局调色板（夜间=白字），
        # 若表头仍写死浅色背景（旧行为 background:#eee），白字落进浅底就看不清了。
        is_dark = (self._theme_mode == ThemeMode.DARK)
        if is_dark:
            hdr_bg, hdr_fg = "#3c3c3c", "#eeeeee"
            grid_c = "#5a5a5a"
        else:
            hdr_bg, hdr_fg = "#eeeeee", "#333333"
            grid_c = "#bbbbbb"

        dlg = QDialog(self)
        dlg.setWindowTitle("场景说明")
        dlg.resize(520, 430)
        lay = QVBoxLayout(dlg)
        text = QTextEdit()
        text.setReadOnly(True)
        headers = ("场景", "延迟(ms)", "丢包率(%)", "带宽(kbit)", "典型效果")
        rows = [
            ("2G (GPRS/EDGE)", "300~500", "5~10", "50~100", "仅文本，图片加载缓慢"),
            ("3G (HSPA)", "100~200", "2~5", "500~2000", "网页可打开，视频缓冲频繁"),
            ("4G (LTE)", "30~70", "0.5~1", "5000~30000", "流畅视频，大部分应用正常"),
            ("高延迟", "200~500", "0", "不限", "模拟跨国链路，响应迟钝"),
            ("高丢包", "0", "15~30", "不限", "模拟信号差，数据丢失严重"),
            ("弱信号", "200~400", "10~20", "100~500", "综合模拟信号不稳定"),
        ]
        hdr_html = "".join(
            f'<th style="background:{hdr_bg}; color:{hdr_fg};">{h}</th>' for h in headers)
        body_html = "".join(
            "<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
        text.setHtml(f"""
        <h3>📊 弱网场景参考表</h3>
        <table border="1" cellpadding="6"
               style="border-collapse: collapse; width:100%; border-color:{grid_c};">
        <tr>{hdr_html}</tr>
        {body_html}
        </table>
        <p>💡 数值为典型参考，实际效果因设备和网络环境而异。</p>
        """)
        lay.addWidget(text)
        dlg.exec()

    # ------------------------------------------------------------------
    def _reset_network_rule(self):
        """静默清理规则（初始化时调用）

        里面全是 adb 调用（root 检查 + tc），放主线程会在启动时卡住界面，所以丢子线程，
        清理结果不需要反馈给用户。
        """
        iface = self.iface_combo.currentText().strip()
        if not iface or not self._get_serial():
            return

        def worker():
            if not self._check_root():
                return
            self._run_tc_cmd(f"qdisc del dev {iface} root 2>/dev/null")

        threading.Thread(target=worker, daemon=True).start()

    def _apply_limit(self):
        if self._processing:
            return

        serial = self._get_serial()
        if not serial:
            # 未接设备这类轻量提示用 Toast（弹对话框打断操作流，过重）
            show_toast(self.window(), "⚠️ 设备未连接，请先连接设备", duration=2000)
            return

        iface = self.iface_combo.currentText().strip()
        if not iface:
            show_toast(self.window(), "⚠️ 请输入网卡名称", duration=2000)
            return

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

        self._processing = True
        self.apply_btn.setEnabled(False)

        def worker():
            """根/ tc 预检 + 下发全在子线程：这些也是 adb 调用，放主线程会卡界面；
            界面更新一律通过信号回主线程（跨线程碰控件/Toast 会卡死界面）"""
            if not self._check_root():
                self._apply_finished.emit(RESULT_NO_ROOT, "")
                return
            if not self._get_tc_path():
                self._apply_finished.emit(RESULT_NO_TC, "")
                return
            # 清理旧规则
            self._run_tc_cmd(f"qdisc del dev {iface} root 2>/dev/null")
            for cmd in commands:
                out, err, rc = self._run_tc_cmd(cmd)
                if rc != 0:
                    self._apply_finished.emit(RESULT_ERROR, err or out)
                    return
            self._apply_finished.emit(RESULT_OK, "")

        threading.Thread(target=worker, daemon=True).start()

    def _on_apply_finished(self, result: str, message: str):
        self.apply_btn.setEnabled(True)
        self._processing = False
        if result == RESULT_OK:
            self.log_message.emit(f"✅ 弱网规则已生效：{self._rule_desc()}")
        elif result == RESULT_NO_ROOT:
            WarningDialog.show_warning(
                self,
                "权限不足",
                "设备未 root 或 ADB 无 root 权限，无法使用弱网模拟。\n"
                "请确保设备已 root 并授予 ADB root 权限。"
            )
        elif result == RESULT_NO_TC:
            WarningDialog.show_warning(
                self, "缺少 tc",
                "设备上找不到 tc 命令，可能内核未编译 netem 支持。"
            )
        else:
            self.log_message.emit(f"❌ 弱网规则应用失败：{message}")

    def _clear_limit(self):
        if self._processing:
            return

        serial = self._get_serial()
        if not serial:
            show_toast(self.window(), "⚠️ 设备未连接，请先连接设备", duration=2000)
            return

        iface = self.iface_combo.currentText().strip()
        if not iface:
            return

        self._processing = True
        self.clear_btn.setEnabled(False)

        def worker():
            """同上：root 预检和 adb 下发都在子线程里跑，结果回主线程处理"""
            if not self._check_root():
                self._clear_finished.emit(RESULT_NO_ROOT, "")
                return
            out, err, rc = self._run_tc_cmd(f"qdisc del dev {iface} root 2>/dev/null")
            detail = err or out
            # 本来就没有规则，也算清除成功
            if rc != 0 and ("No such file" in detail or "Cannot find device" in detail):
                self._clear_finished.emit(RESULT_OK, "当前无限制规则")
                return
            self._clear_finished.emit(RESULT_OK if rc == 0 else RESULT_ERROR, detail)

        threading.Thread(target=worker, daemon=True).start()

    def _on_clear_finished(self, result: str, message: str):
        self.clear_btn.setEnabled(True)
        self._processing = False
        iface = self.iface_combo.currentText().strip()
        if result == RESULT_NO_ROOT:
            WarningDialog.show_warning(self, "权限不足", "清除弱网规则需要 root 权限")
        elif result == RESULT_OK:
            if message == "当前无限制规则":
                self.log_message.emit(f"ℹ️ 弱网规则清除：{iface} 当前没有限制规则")
            else:
                self.log_message.emit(f"✅ 弱网规则已清除：{iface}")
        else:
            self.log_message.emit(f"❌ 弱网规则清除失败：{message}")

    def _rule_desc(self) -> str:
        """当前参数的可读描述（写日志用）"""
        iface = self.iface_combo.currentText().strip()
        parts = [f"网卡 {iface}"]
        delay = self.delay_spin.value()
        loss = self.loss_spin.value()
        bandwidth = self.bandwidth_spin.value()
        if delay > 0:
            parts.append(f"延迟 {delay}ms")
        if loss > 0:
            parts.append(f"丢包 {loss}%")
        if bandwidth > 0:
            parts.append(f"带宽 {bandwidth}kbit")
        else:
            parts.append("带宽 不限")
        return "，".join(parts)

    # ------------------------------------------------------------------
    def apply_theme(self, theme_mode: ThemeMode = None, has_wallpaper: bool = False):
        if theme_mode is None:
            theme_mode = (ThemeMode.DARK
                          if Settings.get_theme_mode() == THEME_MODE_DARK
                          else ThemeMode.LIGHT)
        self._theme_mode = theme_mode
        is_dark = (theme_mode == ThemeMode.DARK)

        if is_dark:
            bg = "rgba(45, 45, 45, 0.85)" if has_wallpaper else "#2d2d2d"
            text = "#eeeeee"
            border = "#555"
            input_bg = "#3c3c3c"
            input_border = "#555"
            focus_color = "#90caf9"
            title_color = "#ffffff"
            hint_color = "#9aa0a6"
            help_bg = "#3498db"
            help_hover = "#5dade2"
            apply_bg = "#27ae60"
            apply_hover = "#2ecc71"
            clear_bg = "#d68910"
            clear_hover = "#f39c12"
            disabled_bg = "#444"
            disabled_fg = "#777"
        else:
            bg = "rgba(255, 255, 255, 0.85)" if has_wallpaper else "#ffffff"
            text = "#333333"
            border = "#d0d0d0"
            input_bg = "#ffffff"
            input_border = "#d0d0d0"
            focus_color = "#1976d2"
            title_color = "#1a1a1a"
            hint_color = "#8a8f98"
            help_bg = "#3498db"
            help_hover = "#5dade2"
            apply_bg = "#27ae60"
            apply_hover = "#2ecc71"
            clear_bg = "#f39c12"
            clear_hover = "#f5b041"
            disabled_bg = "#e0e0e0"
            disabled_fg = "#a0a0a0"

        self.setStyleSheet(f"""
            #WeakNetworkPanel {{
                background-color: {bg};
                border: 1px solid {border};
                border-radius: 8px;
            }}
            #WeakNetworkPanel QLabel {{
                color: {text};
                background: transparent;
                font-size: 13px;
            }}
            #WeakNetworkPanel QLabel#titleLabel {{
                font-size: 16px;
                font-weight: bold;
                color: {title_color};
                padding-bottom: 4px;
            }}
{panel_chrome_qss("#WeakNetworkPanel", text, hint_color, border)}
            #WeakNetworkPanel QSpinBox,
            #WeakNetworkPanel QDoubleSpinBox,
            #WeakNetworkPanel QComboBox {{
                background-color: {input_bg};
                color: {text};
                border: 1px solid {input_border};
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 13px;
                min-height: 22px;
            }}
            #WeakNetworkPanel QSpinBox:focus,
            #WeakNetworkPanel QDoubleSpinBox:focus,
            #WeakNetworkPanel QComboBox:focus {{
                border-color: {focus_color};
            }}
            #WeakNetworkPanel QComboBox QAbstractItemView {{
                background-color: {input_bg};
                color: {text};
                border: 1px solid {input_border};
                selection-background-color: {focus_color};
                selection-color: white;
                outline: none;
            }}
            #WeakNetworkPanel QPushButton#helpBtn {{
                background-color: {help_bg};
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: 500;
            }}
            #WeakNetworkPanel QPushButton#helpBtn:hover {{
                background-color: {help_hover};
            }}
            #WeakNetworkPanel QPushButton#applyBtn {{
                background-color: {apply_bg};
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: 500;
                font-size: 14px;
            }}
            #WeakNetworkPanel QPushButton#applyBtn:hover {{
                background-color: {apply_hover};
            }}
            #WeakNetworkPanel QPushButton#applyBtn:disabled {{
                background-color: {disabled_bg};
                color: {disabled_fg};
            }}
            #WeakNetworkPanel QPushButton#clearBtn {{
                background-color: {clear_bg};
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: 500;
                font-size: 14px;
            }}
            #WeakNetworkPanel QPushButton#clearBtn:hover {{
                background-color: {clear_hover};
            }}
            #WeakNetworkPanel QPushButton#clearBtn:disabled {{
                background-color: {disabled_bg};
                color: {disabled_fg};
            }}
        """)