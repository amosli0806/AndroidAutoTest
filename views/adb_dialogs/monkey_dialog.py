# views/adb_dialogs/monkey_dialog.py
"""Monkey 测试对话框（PyQt6 + 主题适配）"""
import subprocess
import sys
import threading
import re
import time

import qtawesome as qta
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLineEdit, QSpinBox, QPushButton, QLabel,
    QGroupBox, QCheckBox, QComboBox,
    QScrollArea, QWidget, QListWidget, QListWidgetItem
)
from PyQt6.QtCore import Qt, QTimer

from utils.adb_path import get_adb_path
from utils.theme import ThemeMode
from utils.settings import Settings, THEME_MODE_DARK
from utils.toast import show_toast
from utils.dialogs import WarningDialog, ErrorDialog


class MonkeyDialog(QDialog):
    """Monkey 测试配置与执行"""

    def __init__(self, device_service, parent=None):
        super().__init__(parent)
        self.device_service = device_service
        self._theme_mode = ThemeMode.LIGHT
        self.process = None
        self.monkey_pid = None
        self.monkey_running = False
        self.monitor_thread = None

        self.setWindowTitle("Monkey 测试")
        self.resize(540, 640)

        self.setup_ui()
        self.apply_theme()
        self._check_running_monkey()

    # ------------------------------------------------------------------
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("🐵 Monkey 测试")
        title.setObjectName("titleLabel")
        layout.addWidget(title)

        # 基础参数
        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.event_count = QSpinBox()
        self.event_count.setRange(1, 100000000)
        self.event_count.setValue(50000000)
        form.addRow("事件数量:", self.event_count)

        self.seed = QSpinBox()
        self.seed.setRange(0, 999)
        self.seed.setValue(733)
        form.addRow("随机种子:", self.seed)

        # 包名 + 选择按钮
        pkg_layout = QHBoxLayout()
        self.pkg = QLineEdit()
        self.pkg.setPlaceholderText("留空则测试所有应用。多个包名用英文逗号分隔")
        self.pkg.setText("com.baidu.naviauto")
        pkg_layout.addWidget(self.pkg, 1)

        self.select_pkg_btn = QPushButton("选择")
        self.select_pkg_btn.setObjectName("selectBtn")
        self.select_pkg_btn.setFixedHeight(32)
        self.select_pkg_btn.setFixedWidth(80)
        self.select_pkg_btn.clicked.connect(self._choose_packages)
        pkg_layout.addWidget(self.select_pkg_btn)
        form.addRow("包名:", pkg_layout)

        layout.addLayout(form)

        # 高级选项
        self.advanced_group = QGroupBox("高级选项")
        self.advanced_group.setCheckable(True)
        self.advanced_group.setChecked(True)
        adv_layout = QVBoxLayout(self.advanced_group)
        adv_layout.setContentsMargins(8, 20, 8, 8)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setMinimumHeight(280)

        container = QWidget()
        c_layout = QVBoxLayout(container)
        c_layout.setContentsMargins(4, 4, 4, 4)
        c_layout.setSpacing(8)

        # 事件间隔
        throttle_form = QFormLayout()
        self.throttle = QSpinBox()
        self.throttle.setRange(0, 10000)
        self.throttle.setValue(500)
        self.throttle.setSuffix(" 毫秒")
        throttle_form.addRow("事件间隔:", self.throttle)
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

        # 忽略异常
        ignore_row1 = QHBoxLayout()
        self.ignore_crashes = QCheckBox("忽略崩溃")
        self.ignore_crashes.setChecked(True)
        ignore_row1.addWidget(self.ignore_crashes)
        self.ignore_timeouts = QCheckBox("忽略超时")
        self.ignore_timeouts.setChecked(True)
        ignore_row1.addWidget(self.ignore_timeouts)
        c_layout.addLayout(ignore_row1)

        ignore_row2 = QHBoxLayout()
        self.ignore_security = QCheckBox("忽略安全异常")
        self.ignore_security.setChecked(True)
        ignore_row2.addWidget(self.ignore_security)
        self.ignore_native = QCheckBox("忽略原生崩溃")
        self.ignore_native.setChecked(True)
        ignore_row2.addWidget(self.ignore_native)
        c_layout.addLayout(ignore_row2)

        ignore_row3 = QHBoxLayout()
        self.monitor_native = QCheckBox("监控原生崩溃")
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
        layout.addWidget(self.advanced_group, 1)

        # 按钮行
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)
        btn_layout.addStretch()

        self.start_btn = QPushButton("开始")
        self.start_btn.setObjectName("startBtn")
        self.start_btn.setFixedHeight(34)
        self.start_btn.setMinimumWidth(140)
        self.start_btn.clicked.connect(self._start_monkey)
        btn_layout.addWidget(self.start_btn)

        self.stop_btn = QPushButton("停止")
        self.stop_btn.setObjectName("stopBtn")
        self.stop_btn.setFixedHeight(34)
        self.stop_btn.setMinimumWidth(140)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop_monkey)
        btn_layout.addWidget(self.stop_btn)

        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        # 状态
        self.status = QLabel("就绪")
        self.status.setObjectName("statusLabel")
        layout.addWidget(self.status)

    def _make_pct_spin(self, value):
        s = QSpinBox()
        s.setRange(0, 100)
        s.setValue(value)
        return s

    # ------------------------------------------------------------------
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
            self.status.setText(f"Monkey 正在运行中 (PID: {pid})")
            self._start_monitor_thread()
        else:
            self.monkey_running = False
            self.monkey_pid = None
            self.start_btn.setEnabled(True)
            self.start_btn.setText("开始")
            self.stop_btn.setEnabled(False)
            self.status.setText("就绪")

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
        self.status.setText("Monkey 已结束")

    # ------------------------------------------------------------------
    def _choose_packages(self):
        """弹出选择对话框，多选应用"""
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
            WarningDialog.show_warning(self, "提示", "设备未连接")
            return

        if self.monkey_running:
            WarningDialog.show_warning(self, "提示", "Monkey 已在运行，请先停止后再启动")
            return

        event_count = self.event_count.value()
        seed = self.seed.value()
        pkg_str = self.pkg.text().strip()

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
        self.status.setText("正在启动 Monkey...")

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

                QTimer.singleShot(0, lambda: self.status.setText(
                    f"Monkey 运行中 (PID: {self.monkey_pid or '未知'})"
                ))

                self._start_monitor_thread()
                if self.process:
                    self.process.wait()
            except Exception as e:
                QTimer.singleShot(0, lambda: self.status.setText(f"启动失败: {e}"))
                self.monkey_running = False
                self.monkey_pid = None
                QTimer.singleShot(0, self._on_monkey_stopped)

        threading.Thread(target=run, daemon=True).start()

    def _stop_monkey(self):
        if not self.monkey_running:
            show_toast(self, "当前没有正在运行的 Monkey", duration=1500)
            return

        if self.monkey_pid:
            self._run_adb_cmd(["shell", "kill", "-9", str(self.monkey_pid)], timeout=5)
        else:
            self._run_adb_cmd(["shell", "pkill", "monkey"], timeout=5)

        self.monkey_running = False
        self.monkey_pid = None
        self._on_monkey_stopped()

    def closeEvent(self, event):
        if self.monkey_running:
            from utils.dialogs import ConfirmDeleteDialog
            if not ConfirmDeleteDialog.ask(
                self,
                title="确认关闭",
                message="Monkey 正在运行，关闭窗口会同时停止 Monkey。确定吗？"
            ):
                event.ignore()
                return
            self._stop_monkey()
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
            bg = "#ffffff"
            text = "#333333"
            border = "#d0d0d0"
            input_bg = "#ffffff"
            input_border = "#d0d0d0"
            focus_color = "#1976d2"
            title_color = "#1a1a1a"
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
            QDialog QLabel#tipLabel {{
                background-color: {tip_bg};
                color: {tip_text};
                border-left: 4px solid {tip_border};
                border-radius: 4px;
                padding: 6px 10px;
                font-size: 12px;
            }}
            QDialog QLabel#statusLabel {{
                color: {status_color};
                font-size: 12px;
                padding: 4px 0;
            }}
            QDialog QLineEdit,
            QDialog QSpinBox,
            QDialog QComboBox {{
                background-color: {input_bg};
                color: {text};
                border: 1px solid {input_border};
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 13px;
                min-height: 22px;
            }}
            QDialog QLineEdit:focus,
            QDialog QSpinBox:focus,
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
            QDialog QGroupBox {{
                color: {text};
                border: 1px solid {group_border};
                border-radius: 6px;
                margin-top: 8px;
                padding-top: 8px;
                font-size: 13px;
                font-weight: 500;
            }}
            QDialog QGroupBox::title {{
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 4px;
                color: {text};
            }}
            QDialog QCheckBox {{
                color: {text};
                background: transparent;
                font-size: 13px;
            }}
            QDialog QScrollArea {{
                background: transparent;
                border: none;
            }}
            QDialog QScrollArea > QWidget > QWidget {{
                background: transparent;
            }}
            QDialog QPushButton#selectBtn {{
                background-color: {select_bg};
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: 500;
            }}
            QDialog QPushButton#selectBtn:hover {{
                background-color: {select_hover};
            }}
            QDialog QPushButton#startBtn {{
                background-color: {start_bg};
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: 500;
                font-size: 14px;
            }}
            QDialog QPushButton#startBtn:hover {{
                background-color: {start_hover};
            }}
            QDialog QPushButton#startBtn:disabled {{
                background-color: {disabled_bg};
                color: {disabled_fg};
            }}
            QDialog QPushButton#stopBtn {{
                background-color: {stop_bg};
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: 500;
                font-size: 14px;
            }}
            QDialog QPushButton#stopBtn:hover {{
                background-color: {stop_hover};
            }}
            QDialog QPushButton#stopBtn:disabled {{
                background-color: {disabled_bg};
                color: {disabled_fg};
            }}
            QDialog QPushButton#okBtn {{
                background-color: #27ae60;
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: 500;
            }}
            QDialog QPushButton#okBtn:hover {{
                background-color: #2ecc71;
            }}
        """)