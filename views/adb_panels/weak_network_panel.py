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
from PyQt6.QtCore import Qt, QTimer, pyqtSignal

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

# netem 缺失时的统一话术（错误提示与面板提示共用，避免两处说法不一致）。
# 注意：**绝不能写成「请把延迟/丢包调成 0」** —— 面板会把这两项禁用，用户根本调不了，
# 那是个死循环（2026-10-16 用户指出）。所以由程序置 0，文案只说结果。
NETEM_MISSING_HINT = (
    "本设备内核没有 netem 模块（sch_netem）：延迟 / 丢包无法模拟"
    "（两项已置 0 并禁用），只能用「带宽限制」"
)
# netem 缺失、且连带宽也没填时的提示
NETEM_MISSING_NO_BW_HINT = (
    "本设备内核没有 netem 模块，延迟 / 丢包不可用；请填「带宽限制」后重试"
)

# ---- 能力探测的结论 ----
# 为什么要分这么细：一开始只判"netem 能不能加"，结果在**没 root**的设备上也报
# 「内核没有 netem」——原因说错了，用户会去折腾内核（2026-10-16 实测：
# 一台 Android 12 车机 adb 是 uid=2000(shell)，所有 qdisc 都报 Operation not permitted，
# 跟 netem 一点关系没有）。
CAP_OK = "ok"
CAP_NO_ROOT = "no_root"
CAP_NO_TC = "no_tc"
CAP_NO_NETEM = "no_netem"

CAPABILITY_MESSAGES = {
    CAP_OK: "✅ 本设备支持 netem：延迟 / 丢包 / 带宽限制均可使用",
    CAP_NO_ROOT: ("⚠ 设备未 root（ADB 也没有 root 权限）：弱网模拟需要 root，"
                  "延迟 / 丢包 / 带宽限制都不可用"),
    CAP_NO_TC: "⚠ 设备上没有 tc 命令，无法做弱网模拟",
    CAP_NO_NETEM: "⚠ " + NETEM_MISSING_HINT,
}


def friendly_tc_error(raw: str) -> str:
    """把 tc 的原始报错翻成人话。

    为什么需要：`RTNETLINK answers: No such file or directory` 这句话，
    用户（和当初的我们）第一反应都是"网络/文件问题"，实际含义是**内核不认识这个
    qdisc 类型** —— 在 Android 车机上十有八九是内核没编 netem（2026-10-16 实测：
    一台 Android 11 车机内核 5.4.147-qgki 上，lo 上加 netem 也报这句，
    /proc/modules、/sys/module、全盘 find 都找不到 sch_netem）。
    """
    text = (raw or "").strip()
    low = text.lower()
    if "rtnetlink" in low and ("no such file or directory" in low
                               or "operation not supported" in low):
        return NETEM_MISSING_HINT
    if "cannot find device" in low:
        return f"网卡不存在，请改成设备上真实存在的网卡（设备返回：{text}）"
    if "not permitted" in low:
        return "权限不足，弱网模拟需要 root"
    if "exclusivity flag on" in low or "file exists" in low:
        return "该网卡上已有队列规则，请先点「清除」再应用"
    return text


def _netem_qdisc(iface: str, parent: str, delay: int, loss: float) -> str:
    """组装一条 netem 规则；parent 是 "root" 或 "parent 1:11 handle 10:" """
    cmd = f"qdisc add dev {iface} {parent} netem"
    if delay > 0:
        cmd += f" delay {delay}ms"
    if loss > 0:
        cmd += f" loss {loss}%"
    return cmd


def build_tc_commands(iface: str, delay: int, loss: float, bandwidth: int,
                      netem_ok: bool):
    """按参数与内核能力组装 tc 命令，返回 (命令列表, 错误文案)。

    抽成纯函数（不碰界面、不发 adb）是为了能单测：内核有没有 netem 会走不同分支。

    两条路径：
      * 要限带宽 -> htb 做限速；**只有需要延迟/丢包时才在 htb 类下挂 netem 子队列**
        （htb 的类自带 pfifo 叶子，所以纯限带宽不需要任何额外 qdisc ——
         这样没有 netem 的内核上「限带宽」照样能用）
      * 不限带宽 -> 直接 root 上挂 netem

    内核没有 netem 时：**延迟/丢包直接忽略**（做不到，报错只会把用户卡死），
    按带宽下发；连带宽都没填才提示。界面上那两项此时已被置 0 并禁用，正常走不到这里。
    """
    needs_netem = delay > 0 or loss > 0
    if needs_netem and not netem_ok:
        needs_netem = False

    if not needs_netem and bandwidth <= 0:
        # 区分"什么都没填"和"填了但本机做不到"，提示不一样
        return [], (NETEM_MISSING_NO_BW_HINT
                    if (delay > 0 or loss > 0) else "请至少填一项：延迟 / 丢包率 / 带宽限制")

    commands = []
    if bandwidth > 0:
        commands.append(f"qdisc add dev {iface} root handle 1: htb default 11")
        commands.append(
            f"class add dev {iface} parent 1: classid 1:1 htb rate {bandwidth}kbit")
        commands.append(
            f"class add dev {iface} parent 1:1 classid 1:11 htb rate {bandwidth}kbit")
        if needs_netem:
            commands.append(_netem_qdisc(iface, "parent 1:11 handle 10:", delay, loss))
    else:
        commands.append(_netem_qdisc(iface, "root", delay, loss))
    return commands, ""




class WeakNetworkPanel(QWidget):
    """弱网模拟（tc netem）"""

    # 结果写进主窗口底部"虫师日志"（原对话框是弹 Toast / 面板状态文案）
    log_message = pyqtSignal(str)
    # 子线程 -> 主线程：应用/清除完成（不做跨线程 UI 操作）
    _apply_finished = pyqtSignal(str, str)   # (结果, 详情)
    _clear_finished = pyqtSignal(str, str)
    # 子线程 -> 主线程：弱网能力探测结果（CAP_* 常量之一）
    _capability_ready = pyqtSignal(str)

    def __init__(self, device_service=None, parent=None):
        super().__init__(parent)
        self.device_service = device_service
        self._theme_mode = ThemeMode.LIGHT
        self.tc_path = None
        self._processing = False
        # netem 可用性：None = 还没探测过
        self._netem_ok = None
        self._capability_reason = None
        self._probed_serial = None

        self.setObjectName("WeakNetworkPanel")

        self.setup_ui()
        self._apply_finished.connect(self._on_apply_finished)
        self._clear_finished.connect(self._on_clear_finished)
        self._capability_ready.connect(self._on_capability_ready)
        self.apply_theme()
        self._reset_network_rule()   # 静默清理可能残留的规则
        # 初始也要探一次：否则面板在「被显示」之前一直停在「正在检测…」，
        # 而 serial 从 None 到 None 不算变化，定时器不会替它纠正（2026-10-16）
        self._probe_capability()

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

        # 内核能力提示：进面板时探测一次 netem，把"这台设备能不能做延迟/丢包"直接写出来，
        # 而不是等用户填好参数点了应用才报错（2026-10-16 加）
        cap_row = QHBoxLayout()
        cap_row.setContentsMargins(0, 0, 0, 0)
        cap_row.setSpacing(6)
        self.capability_label = QLabel("正在检测本机弱网能力…")
        self.capability_label.setObjectName("panelHint")
        self.capability_label.setWordWrap(True)
        cap_row.addWidget(self.capability_label, 1)
        # 「重新检测」按钮：设备刚连上、或刚 root 完，探测结论会变（serial 没变，
        # 定时器看不出来），给用户一个手动刷新的入口
        self.redetect_btn = QPushButton("重新检测")
        self.redetect_btn.setObjectName("helpBtn")   # 复用面板里已有的小按钮样式
        self.redetect_btn.setFixedHeight(26)
        self.redetect_btn.setMinimumWidth(80)
        self.redetect_btn.setToolTip("设备刚连上 / 刚 root 完，点这里重新检测本机能力")
        self.redetect_btn.clicked.connect(lambda: self._probe_capability(force=True))
        cap_row.addWidget(self.redetect_btn)
        layout.addLayout(cap_row)

        # 设备变更轮询：DeviceService 是个普通对象、没有变更信号，只能定时比一下 serial。
        # 只比字符串（不发 adb），发现变了才重探能力 —— 解决"连上设备后面板还写着未连接"。
        self._last_seen_serial = self._get_serial()
        self._serial_watch = QTimer(self)
        self._serial_watch.setInterval(2000)
        self._serial_watch.timeout.connect(self._watch_serial)
        self._serial_watch.start()

        # 状态/结果不在这里显示：应用/清除结果写进"虫师日志"
        self.delay_spin.valueChanged.connect(self._reset_to_custom)
        self.loss_spin.valueChanged.connect(self._reset_to_custom)
        self.bandwidth_spin.valueChanged.connect(self._reset_to_custom)

        # 面板里所有输入控件禁用右键编辑菜单
        disable_context_menus(self)

    # ------------------------------------------------------------------
    # netem 能力探测
    # ------------------------------------------------------------------
    def showEvent(self, event):
        """面板每次显示时确认一次能力（切设备后回到本面板要重新判定）"""
        super().showEvent(event)
        self._probe_capability()

    def _watch_serial(self):
        """设备连接状态变了就重探能力（只比字符串，不发 adb）"""
        serial = self._get_serial()
        if serial == self._last_seen_serial:
            return
        self._last_seen_serial = serial
        self._probe_capability(force=True)

    def _probe_capability(self, force: bool = False):
        """后台探测弱网能力；同一个设备且已有结论时跳过（force=True 强制重探）。"""
        serial = self._get_serial()
        if not serial:
            self.capability_label.setText("未连接设备，无法检测内核能力（连上后会自动检测）")
            return
        if not force and serial == self._probed_serial and self._netem_ok is not None:
            return
        self._probed_serial = serial
        self._netem_ok = None
        self.capability_label.setText("正在检测本机弱网能力…")

        def worker():
            self._capability_ready.emit(self._probe_capability_reason())

        threading.Thread(target=worker, daemon=True).start()

    def _probe_capability_reason(self) -> str:
        """判断这台设备到底能不能做弱网，并**区分原因**。

        顺序很关键：先 root、再 tc、最后才是 netem。
        否则没 root 的设备会被误报成"内核没有 netem"，让人白折腾内核。
        """
        if not self._check_root():
            return CAP_NO_ROOT
        if not self._get_tc_path():
            return CAP_NO_TC
        return CAP_OK if self._probe_netem() else CAP_NO_NETEM

    def _probe_netem(self) -> bool:
        """用 tc 实试一次，判断内核有没有 netem（前提：已经 root 且有 tc）。

        为什么不查 /proc/modules 或 /sys/module：netem 可以是**编译进内核**的
        （不是模块），查模块会把"支持"误判成"不支持"。而 tc 加不上就是真的加不上。

        为什么在 lo 上试：回环一定存在，且不带任何参数（不加延迟/丢包）、试完立即删除，
        对业务网卡零影响。先 del 一次是为了避免 lo 上已有队列导致 add 报 EEXIST 而误判。
        """
        if not self._get_tc_path():
            return False
        self._run_tc_cmd("qdisc del dev lo root 2>/dev/null")
        out, err, rc = self._run_tc_cmd("qdisc add dev lo root netem 2>&1")
        ok = rc == 0 and "RTNETLINK" not in (out + err)
        self._run_tc_cmd("qdisc del dev lo root 2>/dev/null")
        return ok

    def _on_capability_ready(self, reason: str):
        self._capability_reason = reason
        self._netem_ok = (reason == CAP_OK)
        message = CAPABILITY_MESSAGES.get(reason, reason)
        self.capability_label.setText(message)

        if self._netem_ok:
            for spin in (self.delay_spin, self.loss_spin):
                spin.setEnabled(True)
                spin.setToolTip("")
            return

        # 不能做延迟/丢包：把两项**置 0 再禁用**。
        # 顺序不能反 —— 只禁用不置 0，界面会停在"20% 丢包但改不了"的状态，
        # 而提示又让人去调成 0，等于让用户去点一个点不动的控件（2026-10-16 用户指出）。
        for spin in (self.delay_spin, self.loss_spin):
            spin.setValue(0)
            spin.setEnabled(False)
            spin.setToolTip(message)
        self.log_message.emit(f"ℹ️ 弱网模拟：{message}")

    # ------------------------------------------------------------------
    def set_device_service(self, device_service):
        """面板先构建、后注入设备服务（视图先于控制器创建）"""
        self.device_service = device_service
        self._reset_network_rule()   # 与原对话框一致：打开时静默清理残留规则
        # 换了设备要重新判定内核能力（可能是另一台车机）
        self._probe_capability(force=True)

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

        delay = preset.get("delay", 0)
        loss = preset.get("loss", 0.0)
        self.delay_spin.setValue(delay)
        self.loss_spin.setValue(loss)
        self.bandwidth_spin.setValue(preset.get("bandwidth", 0))

        self.delay_spin.blockSignals(False)
        self.loss_spin.blockSignals(False)
        self.bandwidth_spin.blockSignals(False)

        # 本设备没有 netem：预设里的延迟/丢包做不到。这里必须再置一次 0 ——
        # 上面的 setValue 会往"已禁用"的控件里塞值，界面就会显示"20% 丢包但改不了"，
        # 用户既调不动也不知道它其实不生效（2026-10-16 用户指出）。
        if self._netem_ok is False and (delay or loss):
            self.delay_spin.setValue(0)
            self.loss_spin.setValue(0)
            self.log_message.emit(
                f"ℹ️ 场景「{name}」的延迟/丢包已忽略（本设备无 netem），只保留带宽限制")

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

        self._processing = True
        self.apply_btn.setEnabled(False)

        def worker():
            """根/ tc 预检 + 下发全在子线程：这些也是 adb 调用，放主线程会卡界面；
            界面更新一律通过信号回主线回传（跨线程碰控件/Toast 会卡死界面）"""
            if not self._check_root():
                self._apply_finished.emit(RESULT_NO_ROOT, "")
                return
            if not self._get_tc_path():
                self._apply_finished.emit(RESULT_NO_TC, "")
                return
            # 组装命令放在子线程里：可能要先探一次能力（也是 adb 调用）
            netem_ok = self._netem_ok
            if netem_ok is None:
                reason = self._probe_capability_reason()
                netem_ok = (reason == CAP_OK)
                self._capability_ready.emit(reason)
            commands, error = build_tc_commands(iface, delay, loss, bandwidth, netem_ok)
            if error:
                self._apply_finished.emit(RESULT_ERROR, error)
                return
            # 清理旧规则
            self._run_tc_cmd(f"qdisc del dev {iface} root 2>/dev/null")
            for cmd in commands:
                out, err, rc = self._run_tc_cmd(cmd)
                if rc != 0:
                    self._apply_finished.emit(RESULT_ERROR, friendly_tc_error(err or out))
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
        if self._netem_ok is False:
            # 说清这条规则是"降级"来的，免得用户以为延迟/丢包也生效了
            parts.append("（本设备无 netem，仅按带宽限速）")
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
            #WeakNetworkPanel QPushButton#helpBtn:pressed {{
                background-color: #216692;
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
            #WeakNetworkPanel QPushButton#applyBtn:pressed {{
                background-color: #1f8b4c;
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
            #WeakNetworkPanel QPushButton#clearBtn:pressed {{
                background-color: #b9770e;
            }}
            #WeakNetworkPanel QPushButton#clearBtn:disabled {{
                background-color: {disabled_bg};
                color: {disabled_fg};
            }}
        """)