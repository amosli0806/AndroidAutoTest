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
                                     panel_separator, disable_context_menus,
                                     apply_panel_btn_size)


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

# ---- root 可行性探测的结论 ----
# 为什么要单独探：「已 root」和「能不能 root」是两回事。一台 userdebug 车机当场就能
# `adb root` 拿到权限，而正式版（user）机型基本没戏 —— 提前说清楚，用户才不会在一台
# 根本 root 不了的车机上白折腾（2026-10-16 实测过一台 user 版车机，面板只报「未 root」，
# 让人以为"再 root 一下就行"，其实没有可行路径）。
# 只用只读命令判断，**绝不顺手执行 `adb root`** —— 那会重启 adbd、让设备短暂断连，
# 这种有副作用的动作要由用户自己决定（文案里告诉他怎么做）。
ROOT_ALREADY = "already"          # 已经是 root，直接用
ROOT_ADB_ROOT = "adb_root"        # userdebug / eng / 模拟器：adb root 就能拿到
ROOT_SU_UNGRANTED = "su"          # 装了 su / root 管理器，但当前 shell 未被授权
ROOT_NO_WAY = "no_way"            # 正式版且没发现 su：没有可行的 root 路径
ROOT_UNKNOWN = "unknown"          # 通道异常，判断不了

# 「点应用时报未 root」那个弹窗里的一句话：**必须短**（2026-10-17 用户反馈字太多）。
# 想细看「为什么不可用 / 依据 / 下一步怎么做」，走面板上那个「详情」弹窗
# （见 ROOT_DETAIL_SUMMARY / ROOT_DETAIL_STEP），别往提示里堆。
ROOT_FEASIBILITY_MESSAGES = {
    ROOT_ALREADY: "本设备已是 root",
    ROOT_ADB_ROOT: "可以（调试版），执行 adb root 后重试",
    ROOT_SU_UNGRANTED: "可以（已装 root），手机上授权后重试",
    ROOT_NO_WAY: "不能（正式版且无 su），建议换台设备",
    ROOT_UNKNOWN: "未探明，可点「重新检测」",
}

# ---- 状态行的一句话结论 ----
# 面板是窄栏：原来两行长文案要占掉两行高度，太浪费（2026-10-16 用户反馈）。
# 现在只在一行里给结论，「为什么 / 依据 / 下一步」全部收进「详情」弹窗。
CAP_SHORT = {
    CAP_OK: "✅ 弱网可用",
    CAP_NO_TC: "⚠ 不可用 · 设备里没有 tc 命令",
    CAP_NO_NETEM: "⚠ 延迟 / 丢包不可用（内核没有 netem）· 仍可限带宽",
}
# 未 root 时补一句处境：用户一眼就能看出还有没有戏（能不能 root 差别很大）
ROOT_SHORT_TAIL = {
    ROOT_ADB_ROOT: "可 adb root 后重试",
    ROOT_SU_UNGRANTED: "手机上授权 root 后重试",
    ROOT_NO_WAY: "本机无法 root",
    ROOT_UNKNOWN: "能否 root 未探明",
}

# 「详情」弹窗里的紧凑两行：结论 / 下一步。
# 为什么这么短：WarningDialog 的详情区高度是固定的（实测视口只有 40~60px，约两行），
# 超了会出现滚动条、末行被切掉。所以原始依据（build.type=user 这类）挪到「详情」
# 按钮的悬停提示里，弹窗正文只留"能不能 root + 下一步"两句人话。
ROOT_DETAIL_SUMMARY = {
    ROOT_ALREADY: "已是 root",
    ROOT_ADB_ROOT: "可以（调试版系统）",
    ROOT_SU_UNGRANTED: "可以（检测到 su，待授权）",
    ROOT_NO_WAY: "不能（正式版且无 su）",
    ROOT_UNKNOWN: "未探明（设备通道异常）",
}
ROOT_DETAIL_STEP = {
    ROOT_ADB_ROOT: "adb root 后点「重新检测」",
    ROOT_SU_UNGRANTED: "手机上授权 root 后点「重新检测」",
    ROOT_NO_WAY: "换台设备，或先解锁刷机后再试",
    ROOT_UNKNOWN: "确认设备在线后点「重新检测」",
}

# 「详情」按钮的基础提示（探测到依据后会在后面追加一行"判断依据"）
DETAIL_BTN_TIP = "查看不可用的原因与下一步怎么做"


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
    # 子线程 -> 主线程：root 可行性探测结果（结论 + 依据）
    _root_ready = pyqtSignal(str, str)

    def __init__(self, device_service=None, parent=None):
        super().__init__(parent)
        self.device_service = device_service
        self._theme_mode = ThemeMode.LIGHT
        self.tc_path = None
        self._processing = False
        # netem 可用性：None = 还没探测过
        self._netem_ok = None
        self._capability_reason = None
        self._root_level = None
        self._root_evidence = ""
        self._probed_serial = None

        self.setObjectName("WeakNetworkPanel")
        # 让 QSS 里的 background-color / border / border-radius 真正生效。
        # QWidget 子类默认**不画**样式表背景（Qt 只对 QFrame 之类自动处理），
        # 漏了这句就会出现"面板没有自己的底和圆角边框、和页面糊成一整块"（2026-10-16 用户反馈）。
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self.setup_ui()
        self._apply_finished.connect(self._on_apply_finished)
        self._clear_finished.connect(self._on_clear_finished)
        self._capability_ready.connect(self._on_capability_ready)
        self._root_ready.connect(self._on_root_ready)
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
        self.apply_btn.clicked.connect(self._apply_limit)
        header_row.addWidget(self.apply_btn)

        self.clear_btn = QPushButton("清除")
        self.clear_btn.setObjectName("clearBtn")
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
        # 「详情」按钮：只在不做延迟/丢包时出现，点开是完整说明（原因 / 依据 / 下一步）。
        # 为什么要收进弹窗：面板是窄栏，两行长文案实打实占掉两行高度（2026-10-16 用户反馈），
        # 而"为什么不可用 / 下一步怎么做"其实是低频、需要时再看的内容。
        self.detail_btn = QPushButton("详情")
        self.detail_btn.setObjectName("helpBtn")     # 复用面板里已有的小按钮样式
        self.detail_btn.setToolTip(DETAIL_BTN_TIP)
        self.detail_btn.clicked.connect(self._show_detail)
        self.detail_btn.setVisible(False)
        cap_row.addWidget(self.detail_btn)
        # 「重新检测」按钮：设备刚连上、或刚 root 完，探测结论会变（serial 没变，
        # 定时器看不出来），给用户一个手动刷新的入口
        self.redetect_btn = QPushButton("重新检测")
        self.redetect_btn.setObjectName("helpBtn")   # 复用面板里已有的小按钮样式
        self.redetect_btn.setToolTip("设备刚连上 / 刚 root 完，点这里重新检测本机能力与能否 root")
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

        # 面板内按钮统一尺寸（与「场景说明」一致）：集中在此处设置，
        # 新增按钮时补进这一行即可，别在各处零散写 setFixedHeight/MinimumWidth
        apply_panel_btn_size(self.apply_btn, self.clear_btn, self.help_btn,
                             self.detail_btn, self.redetect_btn)

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
        """后台探测弱网能力 + root 可行性；同一设备且已有结论时跳过（force=True 强制重探）。"""
        serial = self._get_serial()
        if not serial:
            self.capability_label.setText("未连接设备，无法检测内核能力（连上后会自动检测）")
            self._capability_reason = None
            self._root_level = None
            self._root_evidence = ""
            self.detail_btn.setVisible(False)
            return
        if not force and serial == self._probed_serial and self._netem_ok is not None:
            return
        self._probed_serial = serial
        self._netem_ok = None
        self._capability_reason = None
        self._root_level = None          # 换设备/重探时先清掉旧结论，免得状态行显示过时处境
        self._root_evidence = ""
        self.capability_label.setText("正在检测本机弱网能力…")
        self.detail_btn.setToolTip(DETAIL_BTN_TIP)
        self.detail_btn.setVisible(False)   # 重新探测期间先收起旧结论的入口

        def worker():
            # 一次探测两件事：能不能用（能力） + 能不能拿到 root（可行性）。
            # `id` 只发一次，结果两次复用（root 判定是能力判定的前置条件）。
            is_root = self._check_root()
            self._capability_ready.emit(self._probe_capability_reason(is_root))
            self._root_ready.emit(*self._probe_root_feasibility(is_root))

        threading.Thread(target=worker, daemon=True).start()

    def _probe_capability_reason(self, is_root: bool = None) -> str:
        """判断这台设备到底能不能做弱网，并**区分原因**。

        顺序很关键：先 root、再 tc、最后才是 netem。
        否则没 root 的设备会被误报成"内核没有 netem"，让人白折腾内核。

        is_root 由调用方传入可省掉一次 `id`（弱网面板一次探测里能力与 root 可行性共用）。
        """
        if is_root is None:
            is_root = self._check_root()
        if not is_root:
            return CAP_NO_ROOT
        if not self._get_tc_path():
            return CAP_NO_TC
        return CAP_OK if self._probe_netem() else CAP_NO_NETEM

    # ------------------------------------------------------------------
    # root 可行性探测（只读，不执行 adb root）
    # ------------------------------------------------------------------
    def _probe_root_feasibility(self, is_root: bool = None):
        """判断「这台设备能不能拿到 root」，返回 (结论, 依据)。

        判据全部只读（id / getprop / 找 su），**不会真的去 root** ——
        `adb root` 会重启 adbd、让设备短暂断连，这种有副作用的动作不能藏在"检测"里。
        依据一并回传，用户能自己复核这个结论是怎么来的。
        """
        out, _, rc = self._run_adb_shell("id", timeout=5)
        if rc != 0:
            # 连 `id` 都跑不通：多半是通道异常/掉线，别硬给结论
            return ROOT_UNKNOWN, ""
        if is_root is None:
            is_root = "uid=0" in out
        if is_root or "uid=0" in out:
            return ROOT_ALREADY, "adb shell id → " + (out.strip() or "uid=0")

        props = self._get_props(("ro.build.type", "ro.debuggable",
                                 "ro.secure", "ro.kernel.qemu"))
        build_type = props.get("ro.build.type", "")
        debuggable = props.get("ro.debuggable", "")
        secure = props.get("ro.secure", "")
        qemu = props.get("ro.kernel.qemu", "")

        # 调试版系统：`adb root` 就能把 adbd 提成 root
        if build_type in ("userdebug", "eng") or debuggable == "1" or secure == "0":
            evidence = (f"build.type={build_type or '?'} / ro.debuggable={debuggable or '?'}"
                        f" / ro.secure={secure or '?'}")
            return ROOT_ADB_ROOT, evidence
        # 模拟器通常自带 root 开关
        if qemu == "1":
            return ROOT_ADB_ROOT, "ro.kernel.qemu=1（模拟器）"

        # 装了 Magisk / SuperSU 之类：有 su，但当前 shell 没被授权
        su = self._find_su()
        if su:
            return ROOT_SU_UNGRANTED, f"发现 su：{su}"

        return ROOT_NO_WAY, f"build.type={build_type or 'user'} / 未发现 su"

    def _get_props(self, keys) -> dict:
        """一次 `getprop` 读回关心的几个属性（逐条发 adb 太慢）。

        解析用字符串切分而不是正则：getprop 的输出格式固定为 `[key]: [value]`，
        切一刀就够，也省得为这点小事引入 re。
        """
        out, _, rc = self._run_adb_shell("getprop", timeout=8)
        if rc != 0 or not out:
            return {}
        wanted = set(keys)
        props = {}
        for line in out.splitlines():
            line = line.strip()
            if not line.startswith("[") or "]: [" not in line:
                continue
            key, _, rest = line.partition("]: [")
            key = key[1:].strip()
            if key in wanted:
                props[key] = rest[:-1].strip() if rest.endswith("]") else rest.strip()
        return props

    def _find_su(self) -> str:
        """找 su 二进制（只看在不在，不尝试提权 —— 提权会在手机上弹授权框）。

        为什么要找：有 su 说明设备装了 root 管理器，只是当前这条 ADB 通道没被授权，
        和「正式版且无 su」是两种完全不同的处境，给的下一步建议也不一样。
        """
        for cand in ("su", "/system/bin/su", "/system/xbin/su", "/sbin/su",
                     "/su/bin/su", "/debug_ramdisk/su"):
            out, _, rc = self._run_adb_shell(f"command -v {cand} 2>/dev/null")
            found = out.strip().splitlines()
            if rc == 0 and found:
                return found[0].strip()
        return ""

    def _on_root_ready(self, level: str, evidence: str):
        """root 可行性结论（与能力结论同一次探测回传）。

        不再单独占一行：结论并进状态行（「未 root（本机无法 root）」），
        完整说明进「详情」弹窗 —— 面板是窄栏，一行能说清就不占两行。
        """
        self._root_level = level
        self._root_evidence = evidence
        # 原始依据（build.type=user 这类）不进弹窗正文（放不下），挂到按钮悬停提示上：
        # 弹窗说人话，排障要看细节时悬停即可。
        self.detail_btn.setToolTip(
            DETAIL_BTN_TIP + (f"\n判断依据：{evidence}" if evidence else ""))
        if self._capability_reason is not None:
            self.capability_label.setText(self._short_status())

    def _short_status(self) -> str:
        """状态行的一句话结论（详情走「详情」弹窗）"""
        reason = self._capability_reason
        if reason == CAP_NO_ROOT:
            tail = ROOT_SHORT_TAIL.get(self._root_level or "", "")
            return "⚠ 不可用 · 设备未 root" + (f"（{tail}）" if tail else "")
        return CAP_SHORT.get(reason, CAPABILITY_MESSAGES.get(reason or "", ""))

    def _show_detail(self):
        """「详情」：把原因、依据、下一步一次说清，面板因此只留一行结论"""
        WarningDialog.show_warning(
            self,
            "弱网模拟不可用",
            CAPABILITY_MESSAGES.get(self._capability_reason or "",
                                    "本设备当前无法做弱网模拟"),
            self._detail_text(),
        )

    def _detail_text(self) -> str:
        """「详情」弹窗的展开内容：能否 root + 下一步，紧凑两行。

        用短句是刻意的：弹窗详情区高度固定（约两行），长句会被切成滚动条。
        原始依据不进正文，放在「详情」按钮的悬停提示里（见 _on_root_ready）。
        """
        level = self._root_level or ""
        lines = []
        summary = ROOT_DETAIL_SUMMARY.get(level, "")
        if summary:
            lines.append("能否 root：" + summary)
        step = ROOT_DETAIL_STEP.get(level, "") or self._next_step_hint()
        if step:
            lines.append("下一步：" + step)
        return "\n".join(lines)

    def _next_step_hint(self) -> str:
        """纯设备侧问题（tc / netem）的下一步；root 相关的建议见 ROOT_DETAIL_STEP"""
        if self._capability_reason == CAP_NO_TC:
            return "该设备系统里没有 tc 命令"
        if self._capability_reason == CAP_NO_NETEM:
            return "延迟 / 丢包内核不支持，只能用「带宽限制」"
        return ""

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
        # 状态行只放一句话结论；完整说明（原因 / 依据 / 下一步）去「详情」弹窗
        self.capability_label.setText(self._short_status())
        self.detail_btn.setVisible(reason != CAP_OK)

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
            spin.setToolTip(message)   # 悬停也能看到完整原因，不必非得点「详情」
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
            # 文案压到两行（2026-10-17 用户反馈字太多）：一句为什么 + 一句能不能 root。
            # 详细说明（原因 / 依据 / 下一步）在面板的「详情」按钮里。
            hint = ROOT_FEASIBILITY_MESSAGES.get(self._root_level or "", "")
            message = ("弱网模拟需要 root，本设备暂无 root 权限。"
                       + (f"\n能否 root：{hint}" if hint else ""))
            WarningDialog.show_warning(self, "权限不足", message)

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
            # 卡片底色取「次级表面色」而不是纯白：浅色主题下右栏分组本身就是 #ffffff，
            # 面板再来个纯白就成了白对白、只能靠一条边框分辨（2026-10-16 用户反馈"像一整块"）。
            # 与性能检测页的工具卡片（#e8eaed）保持同一套观感。
            bg = "rgba(255, 255, 255, 0.85)" if has_wallpaper else "#e8eaed"
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