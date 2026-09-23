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
                                     disable_context_menus, BorderedCheckBox)
from utils.dialogs import WarningDialog, ErrorDialog


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

        self.setObjectName("MonkeyPanel")

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
            "随机事件压测；留空包名表示全设备，事件比例可在「高级选项」里调整"
        )

        # 开始 / 停止按钮放在标题行右侧
        self.start_btn = QPushButton("开始")
        self.start_btn.setObjectName("startBtn")
        self.start_btn.setFixedHeight(30)
        self.start_btn.setMinimumWidth(110)
        self.start_btn.clicked.connect(self._start_monkey)
        header_row.addWidget(self.start_btn)

        self.stop_btn = QPushButton("停止")
        self.stop_btn.setObjectName("stopBtn")
        self.stop_btn.setFixedHeight(30)
        self.stop_btn.setMinimumWidth(110)
        self.stop_btn.setEnabled(False)
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
        self.event_count.setValue(50000000)
        grid.addWidget(form_label("事件数量:"), 0, 0)
        grid.addWidget(self.event_count, 0, 1)

        self.seed = QSpinBox()
        self.seed.setRange(0, 999)
        self.seed.setValue(733)
        grid.addWidget(form_label("随机种子:"), 0, 2)
        grid.addWidget(self.seed, 0, 3)

        # 包名 + 选择按钮（跨满整行）
        self.pkg = QLineEdit()
        self.pkg.setPlaceholderText("留空则测试所有应用。多个包名用英文逗号分隔")
        # 默认值从本机设置读（上次用过的包名），不把具体应用写死在代码里
        self.pkg.setText(Settings.get_monkey_package())

        self.select_pkg_btn = QPushButton("选择")
        self.select_pkg_btn.setObjectName("selectBtn")
        self.select_pkg_btn.setFixedHeight(32)  # 与同行输入框等高
        self.select_pkg_btn.setMinimumWidth(72)
        self.select_pkg_btn.clicked.connect(self._choose_packages)

        pkg_cell = QWidget()
        pkg_row = QHBoxLayout(pkg_cell)
        pkg_row.setContentsMargins(0, 0, 0, 0)
        pkg_row.setSpacing(6)
        pkg_row.addWidget(self.pkg, 1)
        pkg_row.addWidget(self.select_pkg_btn)
        grid.addWidget(form_label("包名:"), 1, 0)
        grid.addWidget(pkg_cell, 1, 1, 1, 3)

        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        layout.addLayout(grid)

        # 高级选项
        self.advanced_group = QGroupBox("高级选项")
        self.advanced_group.setCheckable(True)
        self.advanced_group.setChecked(True)
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
        self.throttle.setValue(800)
        self.throttle.setSuffix(" 毫秒")
        throttle_form.addRow("操作间隔:", self.throttle)
        c_layout.addLayout(throttle_form)

        # 事件百分比
        pct_form = QFormLayout()
        pct_form.setSpacing(6)
        self.pct_touch = self._make_pct_spin(95)
        pct_form.addRow("触摸事件%:", self.pct_touch)
        self.pct_motion = self._make_pct_spin(5)
        pct_form.addRow("滑动事件%:", self.pct_motion)
        self.pct_trackball = self._make_pct_spin(0)
        pct_form.addRow("轨迹球%:", self.pct_trackball)
        self.pct_nav = self._make_pct_spin(0)
        pct_form.addRow("导航事件%:", self.pct_nav)
        self.pct_majornav = self._make_pct_spin(0)
        pct_form.addRow("主要导航%:", self.pct_majornav)
        self.pct_appswitch = self._make_pct_spin(0)
        pct_form.addRow("应用切换%:", self.pct_appswitch)
        self.pct_flip = self._make_pct_spin(0)
        pct_form.addRow("翻转事件%:", self.pct_flip)
        self.pct_anyevent = self._make_pct_spin(0)
        pct_form.addRow("其他事件%:", self.pct_anyevent)
        self.pct_syskeys = self._make_pct_spin(0)
        pct_form.addRow("系统按键%:", self.pct_syskeys)
        c_layout.addLayout(pct_form)

        # 忽略异常（用带明显边框的复选框，否则深色底上几乎看不出是可勾选项）
        ignore_row1 = QHBoxLayout()
        self.ignore_crashes = BorderedCheckBox("忽略崩溃")
        self.ignore_crashes.setChecked(True)
        ignore_row1.addWidget(self.ignore_crashes)
        self.ignore_timeouts = BorderedCheckBox("忽略超时")
        self.ignore_timeouts.setChecked(True)
        ignore_row1.addWidget(self.ignore_timeouts)
        c_layout.addLayout(ignore_row1)

        ignore_row2 = QHBoxLayout()
        self.ignore_security = BorderedCheckBox("忽略安全异常")
        self.ignore_security.setChecked(True)
        ignore_row2.addWidget(self.ignore_security)
        self.ignore_native = BorderedCheckBox("忽略原生崩溃")
        self.ignore_native.setChecked(True)
        ignore_row2.addWidget(self.ignore_native)
        c_layout.addLayout(ignore_row2)

        ignore_row3 = QHBoxLayout()
        self.monitor_native = BorderedCheckBox("监控原生崩溃")
        self.monitor_native.setChecked(True)
        ignore_row3.addWidget(self.monitor_native)
        ignore_row3.addStretch()
        c_layout.addLayout(ignore_row3)

        # 详细程度
        verbosity_form = QFormLayout()
        self.verbosity = QComboBox()
        self.verbosity.addItems(["0 (默认)", "1 (-v)", "2 (-v -v)", "3 (-v -v -v)"])
        self.verbosity.setCurrentIndex(3)
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

        # 面板里所有输入控件禁用右键编辑菜单
        disable_context_menus(self)

    def _log(self, text: str):
        """状态提示 -> 主窗口底部"虫师日志"（空文本直接忽略）"""
        if text:
            self.log_message.emit(text)

    def _make_pct_spin(self, value):
        s = QSpinBox()
        s.setRange(0, 100)
        s.setValue(value)
        return s

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

    # ------------------------------------------------------------------
    def _check_running_monkey(self):
        out = self._run_adb_cmd(["shell", "pgrep", "monkey"], timeout=5)
        if out.strip():
            pid = out.strip().split('\n')[0]
            self.monkey_pid = pid
            self.monkey_running = True
            self.start_btn.setEnabled(False)
            self.start_btn.setText("启动中...")
            self.stop_btn.setEnabled(True)
            self._log(f"Monkey 正在运行中 (PID: {pid})")
            self._start_monitor_thread()
        else:
            self.monkey_running = False
            self.monkey_pid = None
            self.start_btn.setEnabled(True)
            self.start_btn.setText("开始")
            self.stop_btn.setEnabled(False)

    def _start_monitor_thread(self):
        if self.monitor_thread and self.monitor_thread.is_alive():
            return

        def monitor():
            while self.monkey_running:
                if not self.monkey_pid:
                    out = self._run_adb_cmd(["shell", "pgrep", "monkey"], timeout=3)
                    if out.strip():
                        self.monkey_pid = out.strip().split('\n')[0]
                    time.sleep(0.5)
                    continue

                out = self._run_adb_cmd(
                    ["shell", "ps", "-p", str(self.monkey_pid)], timeout=5
                )
                if not re.search(rf'\b{self.monkey_pid}\b', out):
                    self.monkey_running = False
                    self.monkey_pid = None
                    # 回主线程更新 UI
                    QTimer.singleShot(0, self._on_monkey_stopped)
                    break
                time.sleep(2)

        self.monitor_thread = threading.Thread(target=monitor, daemon=True)
        self.monitor_thread.start()

    def _on_monkey_stopped(self):
        self.start_btn.setEnabled(True)
        self.start_btn.setText("开始")
        self.stop_btn.setEnabled(False)
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

        if self.monkey_running:
            WarningDialog.show_warning(self, "提示", "Monkey 已在运行，请先停止后再启动")
            return

        event_count = self.event_count.value()
        seed = self.seed.value()
        pkg_str = self.pkg.text().strip()
        # 记住这次用的包名，下次打开面板直接带出来（存在本机 data/config.json）
        Settings.set_monkey_package(pkg_str)

        pkgs = [p.strip() for p in pkg_str.split(',') if p.strip()] if pkg_str else []

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

        # 组装 monkey 命令
        cmd = ["shell", "monkey"]
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
        self.start_btn.setEnabled(False)
        self.start_btn.setText("启动中...")
        self.stop_btn.setEnabled(True)
        self._log("正在启动 Monkey...")

        full_cmd = [get_adb_path(), "-s", serial] + cmd
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0

        def run():
            try:
                self.process = subprocess.Popen(
                    full_cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=creationflags,
                )

                time.sleep(1.5)
                # 找 monkey PID
                for _ in range(6):
                    out = self._run_adb_cmd(["shell", "pgrep", "monkey"], timeout=3)
                    if out.strip():
                        self.monkey_pid = out.strip().split('\n')[0]
                        break
                    time.sleep(0.5)

                QTimer.singleShot(0, lambda: self._log(
                    f"Monkey 运行中 (PID: {self.monkey_pid or '未知'})"
                ))

                self._start_monitor_thread()
                if self.process:
                    self.process.wait()
            except Exception as e:
                QTimer.singleShot(0, lambda: self._log(f"启动失败: {e}"))
                self.monkey_running = False
                self.monkey_pid = None
                QTimer.singleShot(0, self._on_monkey_stopped)

        threading.Thread(target=run, daemon=True).start()

    def _stop_monkey(self):
        if not self.monkey_running:
            show_toast(self.window(), "当前没有正在运行的 Monkey", duration=1500)
            return

        if self.monkey_pid:
            self._run_adb_cmd(["shell", "kill", "-9", str(self.monkey_pid)], timeout=5)
        else:
            self._run_adb_cmd(["shell", "pkill", "monkey"], timeout=5)

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
            bg = "rgba(255, 255, 255, 0.85)" if has_wallpaper else "#ffffff"
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
        """)