# views/adb_panels/monkey_panel.py
"""Monkey 测试面板

原「Monkey 测试对话框」的内嵌版：逻辑不变，去掉窗口相关代码，
样式选择器由 QDialog 改为 #MonkeyPanel，避免影响页面内其它控件。
"""
import subprocess
import sys
import threading
import re
import time
import os
import tempfile

import qtawesome as qta
from PyQt6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGridLayout,
    QLineEdit, QSpinBox, QPushButton, QLabel,
    QGroupBox, QCheckBox, QComboBox, QApplication,
    QScrollArea, QWidget, QListWidget, QListWidgetItem
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal

from utils.adb_path import get_adb_path
from utils.theme import ThemeMode
from utils.settings import Settings, THEME_MODE_DARK
from utils.toast import show_toast
from views.adb_panels.common import (form_label, panel_chrome_qss, panel_header,
                                     panel_separator, thin_scrollbar_qss,
                                     disable_context_menus, BorderedCheckBox,
                                     apply_panel_btn_size)
from utils.dialogs import WarningDialog, ErrorDialog
from utils.wheel_guard import WheelScrollGuard


class MonkeyPanel(QWidget):
    """Monkey 测试配置与执行"""

    # 状态提示转发到主窗口底部"虫师日志"（面板内不再显示状态行）
    log_message = pyqtSignal(str)

    def __init__(self, device_service=None, parent=None):
        super().__init__(parent)
        self.device_service = device_service
        self._theme_mode = ThemeMode.LIGHT
        self.process = None
        self.monkey_pid = None
        self.monkey_running = False
        self.monitor_thread = None
        self._whitelist_remote = None  # 白名单文件方式时设备侧的文件路径

        # 上次用过的参数（2026-10-31）：整屏参数都记忆，重启后原样带出。
        # _cfg_loading 期间控件在灌初值，此时不要触发回写，否则一开面板就写一遍配置。
        self._monkey_cfg = Settings.get_monkey_config()
        self._cfg_loading = True
        self._cfg_timer = QTimer(self)
        self._cfg_timer.setSingleShot(True)
        self._cfg_timer.setInterval(600)     # 连续拖动数字框时只在停手后写一次
        self._cfg_timer.timeout.connect(self._save_config)

        self.setObjectName("MonkeyPanel")
        # 让 QSS 里的 background-color / border / border-radius 真正生效。
        # QWidget 子类默认**不画**样式表背景（Qt 只对 QFrame 之类自动处理），
        # 漏了这句就会出现"面板没有自己的底和圆角边框、和页面糊成一整块"（2026-10-16 用户反馈）。
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self.setup_ui()
        self.apply_theme()
        self._check_running_monkey()

        # 退出应用时兜底停掉 Monkey
        _app = QApplication.instance()
        if _app is not None:
            _app.aboutToQuit.connect(self._stop_monkey_on_quit)

    # ------------------------------------------------------------------
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)

        # 标题行
        header_row = panel_header(
            "Monkey 测试",
            "随机事件压测。受限车机不生效时，"
            "可把「启动方式」切换为白名单文件"
        )

        # 开始 / 停止按钮放在标题行右侧
        # 「开始」始终是绿色「开始」、**不切成「启动中...」也不置灰**：monkey 一跑
        # 就是几十分钟，一直挂着「启动中...」看着像卡住了。运行中再点开始，
        # 由 _start_monkey 里的探活 + 提示兜住（不会重复启动）。
        self.start_btn = QPushButton("开始")
        self.start_btn.setObjectName("startBtn")
        self.start_btn.clicked.connect(self._start_monkey)
        header_row.addWidget(self.start_btn)

        self.stop_btn = QPushButton("停止")
        self.stop_btn.setObjectName("stopBtn")
        # 「停止」**始终可点**，不置灰：设备上可能跑着一个不是虫师启动的 monkey
        # （连上来之前它就在跑 / 上一次会话残留），置灰的话用户就没法停了。
        # 点击时再探测真实进程，没有就只提示一句（见 _stop_monkey）。
        self.stop_btn.clicked.connect(self._stop_monkey)
        header_row.addWidget(self.stop_btn)

        layout.addLayout(header_row)
        layout.addWidget(panel_separator(self))

        # 基础参数：两列排布
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)

        self.event_count = QSpinBox()
        self.event_count.setRange(1, 100000000)
        self.event_count.setValue(self._monkey_cfg['event_count'])
        grid.addWidget(form_label("事件数量:"), 0, 0)
        grid.addWidget(self.event_count, 0, 1)

        self.seed = QSpinBox()
        self.seed.setRange(0, 999)
        self.seed.setValue(self._monkey_cfg['seed'])
        grid.addWidget(form_label("随机种子:"), 0, 2)
        grid.addWidget(self.seed, 0, 3)

        # 包名 + 选择按钮（跨满整行）
        self.pkg = QLineEdit()
        self.pkg.setPlaceholderText("留空则测试所有应用。多个包名用英文逗号分隔")
        # 默认值从本机设置读（上次用过的包名），不把具体应用写死在代码里
        self.pkg.setText(self._monkey_cfg['pkg'])

        self.select_pkg_btn = QPushButton("选择")
        self.select_pkg_btn.setObjectName("selectBtn")
        self.select_pkg_btn.clicked.connect(self._choose_packages)

        pkg_cell = QWidget()
        pkg_row = QHBoxLayout(pkg_cell)
        pkg_row.setContentsMargins(0, 0, 0, 0)
        pkg_row.setSpacing(6)
        pkg_row.addWidget(self.pkg, 1)
        pkg_row.addWidget(self.select_pkg_btn)
        grid.addWidget(form_label("包名:"), 1, 0)
        grid.addWidget(pkg_cell, 1, 1, 1, 3)

        # 启动方式：部分受限车机 ROM 对 -p 参数不生效（点开始后设备没动作），
        # 白名单文件方式（--pkg-whitelist-file）在这类系统上通常可用
        grid.addWidget(form_label("启动方式:"), 2, 0)
        self.start_mode = QComboBox()
        self.start_mode.addItem("-p 参数（默认）", "param")
        self.start_mode.addItem("白名单文件（兼容受限系统）", "whitelist")
        self.start_mode.setToolTip(
            "白名单文件方式：把上面选择（可多选）的包名写入白名单文件推送到设备，\n"
            "以 --pkg-whitelist-file 启动 Monkey。\n"
            "若使用默认 -p 参数点「开始」后设备没动作，可切换此方式试试。")
        idx = self.start_mode.findData(self._monkey_cfg['start_mode'])
        self.start_mode.setCurrentIndex(idx if idx >= 0 else 0)
        grid.addWidget(self.start_mode, 2, 1, 1, 3)

        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        layout.addLayout(grid)

        # 高级选项
        self.advanced_group = QGroupBox("高级选项")
        self.advanced_group.setCheckable(True)
        # 默认不勾选（首次），勾选状态本身也会被记住
        self.advanced_group.setChecked(self._monkey_cfg['advanced'])
        adv_layout = QVBoxLayout(self.advanced_group)
        adv_layout.setContentsMargins(8, 20, 8, 8)

        scroll = QScrollArea()
        scroll.setObjectName("MonkeyAdvScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setMinimumHeight(200)

        container = QWidget()
        c_layout = QVBoxLayout(container)
        c_layout.setContentsMargins(4, 4, 4, 4)
        c_layout.setSpacing(8)

        # 事件间隔
        throttle_form = QFormLayout()
        self.throttle = QSpinBox()
        self.throttle.setRange(0, 10000)
        self.throttle.setValue(self._monkey_cfg['throttle'])
        self.throttle.setSuffix(" 毫秒")
        throttle_form.addRow("操作间隔:", self.throttle)
        c_layout.addLayout(throttle_form)

        # 事件百分比
        pct_form = QFormLayout()
        pct_form.setSpacing(6)
        self.pct_touch = self._make_pct_spin(self._monkey_cfg['pct_touch'])
        pct_form.addRow("触摸事件%:", self.pct_touch)
        self.pct_motion = self._make_pct_spin(self._monkey_cfg['pct_motion'])
        pct_form.addRow("滑动事件%:", self.pct_motion)
        self.pct_trackball = self._make_pct_spin(self._monkey_cfg['pct_trackball'])
        pct_form.addRow("轨迹球%:", self.pct_trackball)
        self.pct_nav = self._make_pct_spin(self._monkey_cfg['pct_nav'])
        pct_form.addRow("导航事件%:", self.pct_nav)
        self.pct_majornav = self._make_pct_spin(self._monkey_cfg['pct_majornav'])
        pct_form.addRow("主要导航%:", self.pct_majornav)
        self.pct_appswitch = self._make_pct_spin(self._monkey_cfg['pct_appswitch'])
        pct_form.addRow("应用切换%:", self.pct_appswitch)
        self.pct_flip = self._make_pct_spin(self._monkey_cfg['pct_flip'])
        pct_form.addRow("翻转事件%:", self.pct_flip)
        self.pct_anyevent = self._make_pct_spin(self._monkey_cfg['pct_anyevent'])
        pct_form.addRow("其他事件%:", self.pct_anyevent)
        self.pct_syskeys = self._make_pct_spin(self._monkey_cfg['pct_syskeys'])
        pct_form.addRow("系统按键%:", self.pct_syskeys)
        c_layout.addLayout(pct_form)

        # 忽略异常（用带明显边框的复选框，否则深色底上几乎看不出是可勾选项）
        ignore_row1 = QHBoxLayout()
        self.ignore_crashes = BorderedCheckBox("忽略崩溃")
        self.ignore_crashes.setChecked(self._monkey_cfg['ignore_crashes'])
        ignore_row1.addWidget(self.ignore_crashes)
        self.ignore_timeouts = BorderedCheckBox("忽略超时")
        self.ignore_timeouts.setChecked(self._monkey_cfg['ignore_timeouts'])
        ignore_row1.addWidget(self.ignore_timeouts)
        c_layout.addLayout(ignore_row1)

        ignore_row2 = QHBoxLayout()
        self.ignore_security = BorderedCheckBox("忽略安全异常")
        self.ignore_security.setChecked(self._monkey_cfg['ignore_security'])
        ignore_row2.addWidget(self.ignore_security)
        self.ignore_native = BorderedCheckBox("忽略原生崩溃")
        self.ignore_native.setChecked(self._monkey_cfg['ignore_native'])
        ignore_row2.addWidget(self.ignore_native)
        c_layout.addLayout(ignore_row2)

        ignore_row3 = QHBoxLayout()
        self.monitor_native = BorderedCheckBox("监控原生崩溃")
        self.monitor_native.setChecked(self._monkey_cfg['monitor_native'])
        ignore_row3.addWidget(self.monitor_native)
        ignore_row3.addStretch()
        c_layout.addLayout(ignore_row3)

        # 详细程度
        verbosity_form = QFormLayout()
        self.verbosity = QComboBox()
        self.verbosity.addItems(["0 (默认)", "1 (-v)", "2 (-v -v)", "3 (-v -v -v)"])
        self.verbosity.setCurrentIndex(self._monkey_cfg['verbosity'])
        verbosity_form.addRow("详细程度:", self.verbosity)
        c_layout.addLayout(verbosity_form)

        tip = QLabel("提示：各事件百分比总和不必为100，剩余比例将由系统自动分配。")
        tip.setObjectName("tipLabel")
        tip.setWordWrap(True)
        c_layout.addWidget(tip)

        scroll.setWidget(container)
        adv_layout.addWidget(scroll)
        layout.addWidget(self.advanced_group)

        # 状态提示已不再在面板内占一行，改为发信号到主窗口底部"虫师日志"

        # 滚轮守卫：面板里的下拉 / 数字框**没获得焦点**时，滚轮交给滚动区。
        # 面板本身可能被放进外层滚动区（ADB 工具箱的 tool_scroll），高级选项里
        # 又有一层自己的滚动区 —— 守卫会在事件发生时找「最近的、还能滚的那个」，
        # 所以内层滚到头后滚轮会顺势交给外层，不会卡住（见 utils/wheel_guard.py）。
        self._wheel_guard = WheelScrollGuard(self)
        self._wheel_guard.watch(self)

        # 面板内按钮统一尺寸（与「场景说明」一致）：集中在此处设置，
        # 新增按钮时补进这一行即可，别在各处零散写 setFixedHeight/MinimumWidth
        apply_panel_btn_size(self.start_btn, self.stop_btn, self.select_pkg_btn)

        # 面板里所有输入控件禁用右键编辑菜单
        disable_context_menus(self)

        # ---- 参数记忆（2026-10-31）----
        # 灌完初值再连接信号，避免构造期把默认值写回配置
        for spin in (self.event_count, self.seed, self.throttle,
                     self.pct_touch, self.pct_motion, self.pct_trackball,
                     self.pct_nav, self.pct_majornav, self.pct_appswitch,
                     self.pct_flip, self.pct_anyevent, self.pct_syskeys):
            spin.valueChanged.connect(self._on_cfg_changed)
        for combo in (self.start_mode, self.verbosity):
            combo.currentIndexChanged.connect(self._on_cfg_changed)
        for box in (self.ignore_crashes, self.ignore_timeouts,
                    self.ignore_security, self.ignore_native,
                    self.monitor_native, self.advanced_group):
            box.toggled.connect(self._on_cfg_changed)
        self.pkg.textChanged.connect(self._on_cfg_changed)
        self._cfg_loading = False

    def _log(self, text: str):
        """状态提示 -> 主窗口底部"虫师日志"（空文本直接忽略）"""
        if text:
            self.log_message.emit(text)

    def _make_pct_spin(self, value):
        s = QSpinBox()
        s.setRange(0, 100)
        s.setValue(value)
        return s

    # ---- 参数记忆（2026-10-31）：整屏参数都存进 data/config.json ----
    def _collect_config(self) -> dict:
        """把面板上所有可记忆参数收成一个 dict（键与 Settings.MONKEY_DEFAULTS 对齐）。"""
        return {
            "pkg": self.pkg.text().strip(),
            "event_count": self.event_count.value(),
            "seed": self.seed.value(),
            "start_mode": self.start_mode.currentData() or "param",
            "advanced": self.advanced_group.isChecked(),
            "throttle": self.throttle.value(),
            "pct_touch": self.pct_touch.value(),
            "pct_motion": self.pct_motion.value(),
            "pct_trackball": self.pct_trackball.value(),
            "pct_nav": self.pct_nav.value(),
            "pct_majornav": self.pct_majornav.value(),
            "pct_appswitch": self.pct_appswitch.value(),
            "pct_flip": self.pct_flip.value(),
            "pct_anyevent": self.pct_anyevent.value(),
            "pct_syskeys": self.pct_syskeys.value(),
            "ignore_crashes": self.ignore_crashes.isChecked(),
            "ignore_timeouts": self.ignore_timeouts.isChecked(),
            "ignore_security": self.ignore_security.isChecked(),
            "ignore_native": self.ignore_native.isChecked(),
            "monitor_native": self.monitor_native.isChecked(),
            "verbosity": self.verbosity.currentIndex(),
        }

    def _on_cfg_changed(self, *_):
        """任一参数变化 → 防抖 600ms 后写盘（拖动数字框只写最后一次）。"""
        if getattr(self, "_cfg_loading", False):
            return
        self._cfg_timer.start()

    def _save_config(self):
        """把当前参数写进 data/config.json；异常只提示，不影响正在进行的操作。"""
        if getattr(self, "_cfg_loading", False):
            return
        try:
            cfg = self._collect_config()
            Settings.set_monkey_config(cfg)
            self._monkey_cfg = cfg
        except Exception as e:                 # noqa: BLE001 - 存不上也要能继续跑
            self._log(f"⚠️ 保存 Monkey 参数失败：{type(e).__name__}: {e}")

    # ------------------------------------------------------------------
    def set_device_service(self, device_service):
        """面板先构建、后注入设备服务（视图先于控制器创建）"""
        self.device_service = device_service
        self._check_running_monkey()

    def _get_serial(self):
        return self.device_service.serial if self.device_service else None

    def _run_adb_cmd(self, cmd, timeout=10):
        serial = self._get_serial()
        if not serial:
            return ""
        try:
            result = subprocess.run(
                [get_adb_path(), "-s", serial] + cmd,
                capture_output=True, text=True, timeout=timeout,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
                encoding='utf-8', errors='replace',
            )
            return result.stdout or ""
        except Exception:
            return ""

    def _remove_whitelist_file(self, remote_path):
        """删除设备侧 Monkey 白名单文件（尽力而为，失败忽略）。

        纯 subprocess 调用、不碰 UI，后台线程可直接调用。
        """
        if not remote_path:
            return
        serial = self._get_serial()
        if not serial:
            return
        try:
            subprocess.run(
                [get_adb_path(), "-s", serial, "shell", "rm", "-f", remote_path],
                capture_output=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    def _check_running_monkey(self):
        out = self._run_adb_cmd(["shell", "pgrep", "monkey"], timeout=5)
        if out.strip():
            pid = out.strip().split('\n')[0]
            self.monkey_pid = pid
            self.monkey_running = True
            self._log(f"Monkey 正在运行中 (PID: {pid})")
            self._start_monitor_thread()
        else:
            self.monkey_running = False
            self.monkey_pid = None

    # monitor 线程「连续探不到 monkey pid」的次数上限（10 × 0.5s = 5 秒）。
    # 超过就认为 monkey 没跑起来 / 已秒退，收尾并清状态。
    _PID_MISS_LIMIT = 10

    # monkey 输出只保留开头这么多行（够看失败原因，长跑 + 高 verbose 也不会撑爆内存）
    _MONKEY_HEAD_LINES = 80

    # monkey 失败时的关键行，如 "** No activities found to run, monkey aborted."
    _MONKEY_FAIL_RE = re.compile(r'^\s*\*\*\s*(.+?)\s*$', re.M)
    # 设备侧日志系统会给每行尾巴挂个 "sLogQueue.size=N"，提炼原因时去掉
    _MONKEY_NOISE_RE = re.compile(r'\s*sLogQueue\.size=\d+\s*$')

    def _monkey_failure_reason(self, output: str, returncode) -> str:
        """从 monkey 的输出里提炼失败原因；提不出来就退回最后一行 / 退出码。"""
        def _clean(s: str) -> str:
            return self._MONKEY_NOISE_RE.sub('', s).strip()

        m = self._MONKEY_FAIL_RE.search(output or '')
        if m:
            return _clean(m.group(1))
        lines = [ln for ln in (output or '').splitlines() if ln.strip()]
        if lines:
            return _clean(lines[-1])[:140]
        return f"monkey 进程已退出（退出码 {returncode}）"

    def _start_monitor_thread(self):
        if self.monitor_thread and self.monitor_thread.is_alive():
            return

        def monitor():
            pid_miss = 0
            while self.monkey_running:
                if not self.monkey_pid:
                    out = self._run_adb_cmd(["shell", "pgrep", "monkey"], timeout=3)
                    if out.strip():
                        self.monkey_pid = out.strip().split('\n')[0]
                        pid_miss = 0
                    else:
                        # 一直探不到 pid：monkey 多半没起来（例如目标包没有可启动的
                        # Activity）或已秒退。**必须有上限**——原实现这里会无限循环，
                        # monkey_running 永远停在 True，表现就是「点开始说在运行、
                        # 点停止说没在运行」的鬼打墙。
                        pid_miss += 1
                        if pid_miss >= self._PID_MISS_LIMIT:
                            self.monkey_running = False
                            self._on_monkey_stopped()
                            break
                    time.sleep(0.5)
                    continue

                out = self._run_adb_cmd(
                    ["shell", "ps", "-p", str(self.monkey_pid)], timeout=5
                )
                if not re.search(rf'\b{self.monkey_pid}\b', out):
                    # Monkey 结束：顺手清理设备侧白名单文件（后台线程，无 UI 操作）
                    if self._whitelist_remote:
                        self._remove_whitelist_file(self._whitelist_remote)
                        self._whitelist_remote = None
                    self.monkey_running = False
                    self.monkey_pid = None
                    self._on_monkey_stopped()
                    break
                time.sleep(2)

        self.monitor_thread = threading.Thread(target=monitor, daemon=True)
        self.monitor_thread.start()

    def _on_monkey_stopped(self):
        """Monkey 收尾。**只记日志**，所以可以直接从后台线程调用
        （_log 只是 emit 信号，Qt 会投递回 GUI 线程）。"""
        self._log("Monkey 已结束")

    # ------------------------------------------------------------------
    def _choose_packages(self):
        """弹出选择对话框，多选应用"""
        if not self._get_serial():
            # 先单独判设备连接：未接设备给 Toast，别让它落进下面的「无法获取应用列表」对话框
            show_toast(self.window(), "⚠️ 设备未连接，请先连接设备", duration=2000)
            return
        third = self._run_adb_cmd(["shell", "pm", "list", "packages", "-3"], timeout=10)
        system = self._run_adb_cmd(["shell", "pm", "list", "packages", "-s"], timeout=10)
        apps = []
        for out in (third, system):
            for line in out.splitlines():
                if line.startswith("package:"):
                    apps.append(line[8:].strip())
        apps.sort()

        if not apps:
            WarningDialog.show_warning(self, "提示", "无法获取应用列表，请检查设备连接")
            return

        # 简单选择对话框
        dlg = QDialog(self)
        dlg.setWindowTitle("选择应用")
        dlg.resize(500, 460)
        lay = QVBoxLayout(dlg)

        search = QLineEdit()
        search.setPlaceholderText("🔍 搜索包名...")
        lay.addWidget(search)

        lst = QListWidget()
        lst.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        for a in apps:
            item = QListWidgetItem(a)
            item.setData(Qt.ItemDataRole.UserRole, a)
            lst.addItem(item)
        lay.addWidget(lst, 1)

        def on_search(text):
            kw = text.lower().strip()
            for i in range(lst.count()):
                item = lst.item(i)
                pkg = item.data(Qt.ItemDataRole.UserRole) or ""
                item.setHidden(bool(kw) and kw not in pkg.lower())

        search.textChanged.connect(on_search)

        btn = QPushButton("确定")
        btn.setObjectName("okBtn")
        btn.setFixedHeight(32)
        lay.addWidget(btn)

        def on_ok():
            selected = [item.text() for item in lst.selectedItems()]
            if selected:
                self.pkg.setText(','.join(selected))
            dlg.accept()

        btn.clicked.connect(on_ok)
        dlg.exec()

    # ------------------------------------------------------------------
    def _start_monkey(self):
        serial = self._get_serial()
        if not serial:
            # 未接设备这类轻量提示用 Toast（弹对话框打断操作流，过重）
            show_toast(self.window(), "⚠️ 设备未连接，请先连接设备", duration=2000)
            return

        # 「开始」按钮不再置灰（运行中也能点），所以这里必须自己兜住重复启动：
        # 本地 monkey_running 可能滞后（连上来之前设备上就在跑 monkey），
        # 先探一次真实进程，免得在已运行的情况下又起一个。
        if not self.monkey_running:
            out = self._run_adb_cmd(["shell", "pgrep", "monkey"], timeout=5)
            if out.strip():
                self.monkey_running = True
                self.monkey_pid = out.strip().split('\n')[0]

        if self.monkey_running:
            WarningDialog.show_warning(self, "提示", "Monkey 已在运行，请先停止后再启动")
            return

        event_count = self.event_count.value()
        seed = self.seed.value()
        pkg_str = self.pkg.text().strip()
        # 记住这次用的全部参数，下次打开面板原样带出（存本机 data/config.json）。
        # 这里**同步**存一次：防抖是 600ms，用户调完参数立刻点开始可能还没落盘。
        self._save_config()
        start_mode = self.start_mode.currentData() or 'param'

        pkgs = [p.strip() for p in pkg_str.split(',') if p.strip()] if pkg_str else []

        # 白名单文件方式：文件里必须有包（空白名单 monkey 没有可跑的包，会直接退出）
        if start_mode == 'whitelist' and not pkgs:
            WarningDialog.show_warning(
                self, "提示", "白名单文件方式需要至少选择一个包名")
            return

        for p in pkgs:
            if not re.match(r'^[a-zA-Z0-9_.]+$', p):
                WarningDialog.show_warning(self, "错误", f"包名 {p} 格式不正确")
                return

        # 校验包名存在
        for p in pkgs:
            out = self._run_adb_cmd(["shell", "pm", "list", "packages", p], timeout=5)
            if not out.strip():
                WarningDialog.show_warning(self, "错误", f"包名 {p} 不存在于设备上")
                return

        # 这里**不做**「有没有可启动入口（CATEGORY_LAUNCHER）」的预检：白名单方式的
        # 价值之一就是让这类包也能顺带跑 monkey，拦下来反而挡住了能用的场景。
        # 真跑不起来的话，启动后会把 monkey 自己的失败原因如实报出来（见 run()）。

        # 组装 monkey 命令。
        # 白名单文件方式：把多选的包名写进白名单文件推到设备，以
        # --pkg-whitelist-file 启动 —— 部分受限车机 ROM 对 -p 参数不生效
        # （点开始后设备没动作），这种方式在这类系统上通常可用。
        whitelist_remote = None
        cmd = ["shell", "monkey"]
        if start_mode == 'whitelist':
            whitelist_remote = "/data/local/tmp/chongshi_monkey_whitelist.txt"
            cmd.extend(["--pkg-whitelist-file", whitelist_remote])
        else:
            for p in pkgs:
                cmd.extend(["-p", p])
        cmd.extend(["-s", str(seed)])

        if self.advanced_group.isChecked():
            if self.throttle.value() > 0:
                cmd.extend(["--throttle", str(self.throttle.value())])

            pcts = [
                ("--pct-touch", self.pct_touch.value()),
                ("--pct-motion", self.pct_motion.value()),
                ("--pct-trackball", self.pct_trackball.value()),
                ("--pct-nav", self.pct_nav.value()),
                ("--pct-majornav", self.pct_majornav.value()),
                ("--pct-appswitch", self.pct_appswitch.value()),
                ("--pct-flip", self.pct_flip.value()),
                ("--pct-anyevent", self.pct_anyevent.value()),
                ("--pct-syskeys", self.pct_syskeys.value()),
            ]
            for flag, val in pcts:
                if val > 0:
                    cmd.extend([flag, str(val)])

            if self.ignore_crashes.isChecked():
                cmd.append("--ignore-crashes")
            if self.ignore_timeouts.isChecked():
                cmd.append("--ignore-timeouts")
            if self.ignore_security.isChecked():
                cmd.append("--ignore-security-exceptions")
            if self.ignore_native.isChecked():
                cmd.append("--ignore-native-crashes")
            if self.monitor_native.isChecked():
                cmd.append("--monitor-native-crashes")

            verbosity = self.verbosity.currentIndex()
            for _ in range(verbosity):
                cmd.append("-v")

        cmd.append(str(event_count))

        # 状态更新
        self.monkey_running = True
        self._whitelist_remote = whitelist_remote
        if start_mode == 'whitelist':
            self._log(f"白名单文件方式：{len(pkgs)} 个包名，正在推送到设备...")
        else:
            self._log("正在启动 Monkey...")

        full_cmd = [get_adb_path(), "-s", serial] + cmd
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0

        def run():
            try:
                if whitelist_remote:
                    # 白名单文件：每行一个包名，写到系统临时目录后推到设备
                    local_txt = os.path.join(
                        tempfile.gettempdir(), "chongshi_monkey_whitelist.txt")
                    with open(local_txt, 'w', encoding='utf-8') as f:
                        f.write('\n'.join(pkgs) + '\n')
                    push = subprocess.run(
                        [get_adb_path(), "-s", serial, "push",
                         local_txt, whitelist_remote],
                        capture_output=True, text=True, timeout=15,
                        creationflags=creationflags,
                        encoding='utf-8', errors='replace',
                    )
                    if push.returncode != 0:
                        raise RuntimeError(
                            "白名单文件推送失败: "
                            f"{(push.stderr or push.stdout or '').strip()}")

                # 捕获 monkey 的输出（只留开头一段，避免长跑 + 高 verbose 时
                # 撑爆内存）：它起不来时会秒退并打印原因，实测这台车机上是
                # 「** No activities found to run, monkey aborted.」，退出码 252、
                # 0.6s 就退。以前 stdout/stderr 全丢进 DEVNULL，原因看不到，
                # 虫师只能傻等 pid（1.5s + 最多 6×0.5s）——用户看到的就是「启动特别慢」。
                self._monkey_head = []
                self.process = subprocess.Popen(
                    full_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True, encoding='utf-8', errors='replace',
                    creationflags=creationflags,
                )

                def _drain():
                    try:
                        for ln in self.process.stdout:
                            if len(self._monkey_head) < self._MONKEY_HEAD_LINES:
                                self._monkey_head.append(ln.rstrip())
                    except Exception:
                        pass

                _drain_thread = threading.Thread(target=_drain, daemon=True)
                _drain_thread.start()

                # 起不来的话 monkey 通常 <1s 就退：先给它一点时间，再看是否还活着
                time.sleep(0.8)
                if self.process.poll() is not None:
                    _drain_thread.join(timeout=2)      # 等输出读完
                    reason = self._monkey_failure_reason(
                        "\n".join(self._monkey_head), self.process.returncode)
                    self._log(f"Monkey 启动失败：{reason}")
                    self.monkey_running = False
                    self.monkey_pid = None
                    self._whitelist_remote = None
                    self._on_monkey_stopped()
                    return

                # 还活着 → 找 monkey PID（轮询间隔 0.5s → 0.2s，早点确认）
                for _ in range(15):
                    out = self._run_adb_cmd(["shell", "pgrep", "monkey"], timeout=3)
                    if out.strip():
                        self.monkey_pid = out.strip().split('\n')[0]
                        break
                    if self.process.poll() is not None:
                        break
                    time.sleep(0.2)

                if self.monkey_pid:
                    self._log(f"Monkey 运行中 (PID: {self.monkey_pid})")
                else:
                    # 探不到 pid：多半没起来。如实说，别写成「运行中」骗人
                    # （monitor 线程会在 _PID_MISS_LIMIT 次后自动收尾）。
                    self._log("未探测到 Monkey 进程，可能未成功启动")

                self._start_monitor_thread()
                if self.process:
                    self.process.wait()
                # Monkey 自然结束：清理设备侧白名单文件
                self._remove_whitelist_file(whitelist_remote)
            except Exception as e:
                self._remove_whitelist_file(whitelist_remote)
                self._log(f"启动失败: {e}")
                self.monkey_running = False
                self.monkey_pid = None
                self._whitelist_remote = None
                self._on_monkey_stopped()

        # 后台线程里**不要**用 QTimer.singleShot 来回主线程：普通 threading.Thread
        # 没有事件分发器，那种定时器永远不会触发（实测：主线程能触发，普通线程不行）。
        # _log 只是 emit 一个信号、_on_monkey_stopped 现在也只记日志，都能直接从
        # 任意线程调用 —— Qt 会用队列连接把信号投递回 GUI 线程。
        threading.Thread(target=run, daemon=True).start()

    def _stop_monkey(self):
        # 不依赖本地 monkey_running / monkey_pid：设备上可能跑着一个**不是虫师
        # 启动**的 monkey（连上来之前它就在跑、或上一次会话残留），本地状态是
        # False 但它确实在。所以先探一次真实进程，探到就杀。
        pid = self.monkey_pid
        if not pid:
            out = self._run_adb_cmd(["shell", "pgrep", "monkey"], timeout=5)
            pid = out.strip().split('\n')[0] if out.strip() else None

        if not pid:
            # 设备上确实没有：顺手把本地状态一并归零。否则本地还留着
            # monkey_running=True，就会出现「点开始说在运行、点停止说没在运行」
            # 这种两边不一致的鬼打墙（2026-10-08 用户实测报过）。
            self.monkey_running = False
            self.monkey_pid = None
            show_toast(self.window(), "当前没有正在运行的 Monkey", duration=1500)
            return

        self._run_adb_cmd(["shell", "kill", "-9", str(pid)], timeout=5)

        # 白名单方式启动时，顺手删掉设备侧的白名单文件
        if self._whitelist_remote:
            self._remove_whitelist_file(self._whitelist_remote)
            self._whitelist_remote = None

        self.monkey_running = False
        self.monkey_pid = None
        self._on_monkey_stopped()

    def _stop_monkey_on_quit(self):
        """面板常驻，没有"关闭对话框"这一步：退出应用时静默停掉 Monkey，
        避免设备上留下还在跑的进程（原实现是关闭对话框时弹确认）。"""
        if self.monkey_running:
            self._stop_monkey()

    # ------------------------------------------------------------------
    def apply_theme(self, theme_mode: ThemeMode = None, has_wallpaper: bool = False):
        if theme_mode is None:
            theme_mode = (ThemeMode.DARK
                          if Settings.get_theme_mode() == THEME_MODE_DARK
                          else ThemeMode.LIGHT)
        self._theme_mode = theme_mode
        is_dark = (theme_mode == ThemeMode.DARK)

        if is_dark:
            # 有壁纸时用 0.85 半透明底，让壁纸透出来（与其它页面一致）
            bg = "rgba(45, 45, 45, 0.85)" if has_wallpaper else "#2d2d2d"
            text = "#eeeeee"
            border = "#555"
            input_bg = "#3c3c3c"
            input_border = "#555"
            focus_color = "#90caf9"
            title_color = "#ffffff"
            hint_color = "#9aa0a6"
            sb_bg = "#3a3a3a"
            sb_handle = "#666666"
            group_border = "#444"
            tip_bg = "#3a2a1a"
            tip_border = "#ff9800"
            tip_text = "#ffb74d"
            status_color = "#aaaaaa"
            select_bg = "#3498db"
            select_hover = "#5dade2"
            start_bg = "#27ae60"
            start_hover = "#2ecc71"
            stop_bg = "#c0392b"
            stop_hover = "#e74c3c"
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
            sb_bg = "#e0e0e0"
            sb_handle = "#c0c0c0"
            group_border = "#d0d0d0"
            tip_bg = "#fff3e0"
            tip_border = "#ff9800"
            tip_text = "#e65100"
            status_color = "#7f8c8d"
            select_bg = "#3498db"
            select_hover = "#5dade2"
            start_bg = "#27ae60"
            start_hover = "#2ecc71"
            stop_bg = "#e74c3c"
            stop_hover = "#f05a4a"
            disabled_bg = "#e0e0e0"
            disabled_fg = "#a0a0a0"

        self.setStyleSheet(f"""
            #MonkeyPanel {{
                background-color: {bg};
                border: 1px solid {border};
                border-radius: 8px;
            }}
            #MonkeyPanel QLabel {{
                color: {text};
                background: transparent;
                font-size: 13px;
            }}
{panel_chrome_qss("#MonkeyPanel", text, hint_color, border)}
{thin_scrollbar_qss("#MonkeyPanel QScrollArea#MonkeyAdvScroll", sb_bg, sb_handle)}
            #MonkeyPanel QLabel#titleLabel {{
                font-size: 16px;
                font-weight: bold;
                color: {title_color};
                padding-bottom: 4px;
            }}
            #MonkeyPanel QLabel#tipLabel {{
                background-color: {tip_bg};
                color: {tip_text};
                border-left: 4px solid {tip_border};
                border-radius: 4px;
                padding: 6px 10px;
                font-size: 12px;
            }}
            #MonkeyPanel QLabel#statusLabel {{
                color: {status_color};
                font-size: 12px;
                padding: 4px 0;
            }}
            #MonkeyPanel QLineEdit,
            #MonkeyPanel QSpinBox,
            #MonkeyPanel QComboBox {{
                background-color: {input_bg};
                color: {text};
                border: 1px solid {input_border};
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 13px;
                min-height: 22px;
            }}
            #MonkeyPanel QLineEdit:focus,
            #MonkeyPanel QSpinBox:focus,
            #MonkeyPanel QComboBox:focus {{
                border-color: {focus_color};
            }}
            #MonkeyPanel QComboBox QAbstractItemView {{
                background-color: {input_bg};
                color: {text};
                border: 1px solid {input_border};
                selection-background-color: {focus_color};
                selection-color: white;
                outline: none;
            }}
            #MonkeyPanel QGroupBox {{
                color: {text};
                border: 1px solid {group_border};
                border-radius: 6px;
                margin-top: 8px;
                padding-top: 8px;
                font-size: 13px;
                font-weight: 500;
            }}
            #MonkeyPanel QGroupBox::title {{
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 4px;
                color: {text};
            }}
            #MonkeyPanel QCheckBox {{
                color: {text};
                background: transparent;
                font-size: 13px;
            }}
            #MonkeyPanel QScrollArea {{
                background: transparent;
                border: none;
            }}
            #MonkeyPanel QScrollArea > QWidget > QWidget {{
                background: transparent;
            }}
            #MonkeyPanel QPushButton#selectBtn {{
                background-color: {select_bg};
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: 500;
            }}
            #MonkeyPanel QPushButton#selectBtn:hover {{
                background-color: {select_hover};
            }}
            #MonkeyPanel QPushButton#selectBtn:pressed {{
                background-color: #216692;
            }}
            #MonkeyPanel QPushButton#startBtn {{
                background-color: {start_bg};
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: 500;
                font-size: 14px;
            }}
            #MonkeyPanel QPushButton#startBtn:hover {{
                background-color: {start_hover};
            }}
            #MonkeyPanel QPushButton#startBtn:pressed {{
                background-color: #1f8b4c;
            }}
            #MonkeyPanel QPushButton#startBtn:disabled {{
                background-color: {disabled_bg};
                color: {disabled_fg};
            }}
            #MonkeyPanel QPushButton#stopBtn {{
                background-color: {stop_bg};
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: 500;
                font-size: 14px;
            }}
            #MonkeyPanel QPushButton#stopBtn:hover {{
                background-color: {stop_hover};
            }}
            #MonkeyPanel QPushButton#stopBtn:pressed {{
                background-color: #96281b;
            }}
            #MonkeyPanel QPushButton#stopBtn:disabled {{
                background-color: {disabled_bg};
                color: {disabled_fg};
            }}
            #MonkeyPanel QPushButton#okBtn {{
                background-color: #27ae60;
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: 500;
            }}
            #MonkeyPanel QPushButton#okBtn:hover {{
                background-color: #2ecc71;
            }}
            #MonkeyPanel QPushButton#okBtn:pressed {{
                background-color: #1f8b4c;
            }}
        """)