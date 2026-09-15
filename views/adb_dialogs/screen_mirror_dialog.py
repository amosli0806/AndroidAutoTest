# views/adb_dialogs/screen_mirror_dialog.py
"""投屏对话框（scrcpy，PyQt6 + 主题适配）"""
import os
import sys
import subprocess
import threading
import time
from datetime import datetime

import qtawesome as qta
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QComboBox, QSpinBox, QCheckBox, QPushButton,
    QGroupBox, QMessageBox
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal

from utils.adb_path import get_adb_path
from utils.theme import ThemeMode
from utils.settings import Settings, THEME_MODE_DARK
from utils.toast import show_toast
from utils.dialogs import WarningDialog


class ScreenMirrorDialog(QDialog):
    """scrcpy 投屏 + 同步录屏"""

    mirror_stopped = pyqtSignal(str)   # (设备序列号)

    def __init__(self, device_service, output_dir, parent=None):
        super().__init__(parent)
        self.device_service = device_service
        self.output_dir = output_dir
        self._theme_mode = ThemeMode.LIGHT

        serial = device_service.serial if device_service else None
        self.device_serial = serial
        self.safe_serial = serial.replace(':', '_') if serial else 'unknown'

        # 进程
        self.scrcpy_process = None
        self.record_process = None
        self.scrcpy_pid = None

        # 状态
        self.is_mirroring = False
        self.is_recording = False
        self.is_starting = False
        self.is_record_starting = False
        self.is_record_stopping = False
        self.record_completed = False
        self.record_temp_path = "/sdcard/temp_screenrecord.mp4"

        self.setWindowTitle("投屏")
        self.setModal(False)
        self.resize(520, 400)

        self.setup_ui()
        self.apply_theme()
        self._update_ui_state()

        self.check_timer = QTimer(self)
        self.check_timer.setInterval(1000)
        self.check_timer.timeout.connect(self._check_process_status)

        # 检测已有 scrcpy
        self._detect_existing_scrcpy()

    # ------------------------------------------------------------------
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        title = QLabel("📺 投屏")
        title.setObjectName("titleLabel")
        layout.addWidget(title)

        # ---- 投屏参数 ----
        param_group = QGroupBox("投屏参数")
        param_layout = QVBoxLayout(param_group)
        param_layout.setContentsMargins(10, 20, 10, 10)
        param_layout.setSpacing(8)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("分辨率:"))
        self.resolution_combo = QComboBox()
        self.resolution_combo.addItems(["原始", "1920x1080", "1280x720", "854x480"])
        row1.addWidget(self.resolution_combo)

        row1.addSpacing(16)
        row1.addWidget(QLabel("码率:"))
        self.bitrate_spin = QSpinBox()
        self.bitrate_spin.setRange(1, 50)
        self.bitrate_spin.setValue(8)
        self.bitrate_spin.setSuffix(" Mbps")
        row1.addWidget(self.bitrate_spin)
        row1.addStretch()
        param_layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("帧率:"))
        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(10, 120)
        self.fps_spin.setValue(60)
        self.fps_spin.setSuffix(" fps")
        row2.addWidget(self.fps_spin)
        row2.addStretch()
        param_layout.addLayout(row2)
        layout.addWidget(param_group)

        # ---- 投屏选项 ----
        opt_group = QGroupBox("投屏选项")
        opt_layout = QVBoxLayout(opt_group)
        opt_layout.setContentsMargins(10, 20, 10, 10)
        opt_layout.setSpacing(6)

        self.stay_awake_cb = QCheckBox("保持屏幕唤醒")
        self.stay_awake_cb.setChecked(True)
        opt_layout.addWidget(self.stay_awake_cb)

        self.show_touches_cb = QCheckBox("显示触摸操作")
        self.show_touches_cb.setChecked(True)
        opt_layout.addWidget(self.show_touches_cb)

        self.turn_screen_off_cb = QCheckBox("投屏时关闭设备屏幕")
        self.turn_screen_off_cb.setChecked(False)
        opt_layout.addWidget(self.turn_screen_off_cb)
        layout.addWidget(opt_group)

        # ---- 录制状态 ----
        rec_row = QHBoxLayout()
        rec_row.addWidget(QLabel("📹 录制状态:"))
        self.record_status_label = QLabel("未录制")
        self.record_status_label.setObjectName("recordStatus")
        rec_row.addWidget(self.record_status_label)
        rec_row.addStretch()
        layout.addLayout(rec_row)

        # ---- 控制按钮 ----
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)
        btn_layout.addStretch()

        self.start_btn = QPushButton("开始投屏")
        self.start_btn.setObjectName("startBtn")
        self.start_btn.setFixedHeight(34)
        self.start_btn.setMinimumWidth(150)
        self.start_btn.clicked.connect(self._toggle_mirror)
        btn_layout.addWidget(self.start_btn)

        self.record_btn = QPushButton("开始录制")
        self.record_btn.setObjectName("recordBtn")
        self.record_btn.setFixedHeight(34)
        self.record_btn.setMinimumWidth(150)
        self.record_btn.setEnabled(False)
        self.record_btn.clicked.connect(self._toggle_record)
        btn_layout.addWidget(self.record_btn)

        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        # ---- 提示 ----
        self.tip_label = QLabel("💡 点击「开始投屏」启动 scrcpy")
        self.tip_label.setObjectName("tipLabel")
        self.tip_label.setWordWrap(True)
        layout.addWidget(self.tip_label)

    # ------------------------------------------------------------------
    def _detect_existing_scrcpy(self):
        """检测设备上是否已有 scrcpy 进程"""
        if not self.device_serial:
            return
        try:
            result = subprocess.run(
                [get_adb_path(), "-s", self.device_serial, "shell",
                 "pgrep", "-f", f"scrcpy.*-s {self.device_serial}"],
                capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
                encoding='utf-8', errors='replace',
            )
            if result.returncode == 0 and result.stdout.strip():
                pid_str = result.stdout.strip().split('\n')[0]
                if pid_str.isdigit():
                    self.scrcpy_pid = int(pid_str)
                    self.is_mirroring = True
                    self.check_timer.start()
                    self._update_ui_state()
        except Exception:
            pass

    def _is_pid_running(self, pid):
        try:
            result = subprocess.run(
                [get_adb_path(), "-s", self.device_serial, "shell", "ps", "-p", str(pid)],
                capture_output=True, text=True, timeout=3,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
                encoding='utf-8', errors='replace',
            )
            return str(pid) in (result.stdout or "")
        except Exception:
            return False

    def _update_ui_state(self):
        """根据当前状态刷新按钮"""
        # 参数区：投屏中禁用
        param_enabled = not (self.is_mirroring or self.is_starting)
        self.resolution_combo.setEnabled(param_enabled)
        self.bitrate_spin.setEnabled(param_enabled)
        self.fps_spin.setEnabled(param_enabled)
        self.stay_awake_cb.setEnabled(param_enabled)
        self.show_touches_cb.setEnabled(param_enabled)
        self.turn_screen_off_cb.setEnabled(param_enabled)

        # 投屏按钮：始终可点
        self.start_btn.setEnabled(True)

        # 录制按钮
        if self.is_recording:
            self.record_btn.setText("停止录制")
            self.record_btn.setEnabled(True)
        elif self.is_record_starting or self.is_record_stopping:
            self.record_btn.setText("处理中...")
            self.record_btn.setEnabled(False)
        elif self.is_mirroring and not self.is_starting:
            self.record_btn.setText("开始录制")
            self.record_btn.setEnabled(True)
        else:
            self.record_btn.setText("开始录制")
            self.record_btn.setEnabled(False)

        # 录制状态
        if self.is_record_starting:
            self.record_status_label.setText("启动录制中…")
        elif self.is_recording:
            self.record_status_label.setText("● 录制中")
        elif self.is_record_stopping:
            self.record_status_label.setText("停止录制中…")
        elif self.record_completed:
            self.record_status_label.setText("录制完成")
        else:
            self.record_status_label.setText("未录制")

    # ---------- 投屏 ----------
    def _toggle_mirror(self):
        # 判断当前 scrcpy 是否还活着
        process_running = False
        if self.scrcpy_process and self.scrcpy_process.poll() is None:
            process_running = True
        elif self.scrcpy_pid and self._is_pid_running(self.scrcpy_pid):
            process_running = True

        if not process_running:
            self.is_mirroring = False
            self.is_starting = False
            self.scrcpy_pid = None
            self.scrcpy_process = None
            self.check_timer.stop()
            self._update_ui_state()

        if self.is_mirroring or self.is_starting:
            self.tip_label.setText("投屏已启动，请勿重复点击")
            return

        self._start_mirror()

    def _start_mirror(self):
        if not self.device_serial:
            WarningDialog.show_warning(self, "提示", "设备未连接")
            return

        # 清理旧进程
        if self.scrcpy_process and self.scrcpy_process.poll() is None:
            try:
                self.scrcpy_process.terminate()
                self.scrcpy_process.wait(timeout=2)
            except Exception:
                try:
                    self.scrcpy_process.kill()
                except Exception:
                    pass
            self.scrcpy_process = None

        self.scrcpy_pid = None
        self.is_starting = True
        self._update_ui_state()
        self.tip_label.setText("⏳ 正在启动投屏...")

        def run():
            try:
                scrcpy_name = 'scrcpy.exe' if sys.platform == 'win32' else 'scrcpy'
                # 优先用 tools/scrcpy.exe，其次系统 PATH
                project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                scrcpy_local = os.path.join(project_root, "tools", scrcpy_name)
                scrcpy_path = scrcpy_local if os.path.exists(scrcpy_local) else "scrcpy"

                # 检查 scrcpy 是否存在
                if scrcpy_path == "scrcpy":
                    try:
                        subprocess.run([scrcpy_path, "--version"],
                                       capture_output=True, timeout=5, check=True)
                    except Exception:
                        self.tip_label.setText(
                            "❌ 未找到 scrcpy，请将 scrcpy 放到 tools 目录或添加到 PATH"
                        )
                        self.is_starting = False
                        self._update_ui_state()
                        return

                cmd = [scrcpy_path, "-s", self.device_serial]

                resolution = self.resolution_combo.currentText()
                if resolution != "原始":
                    size = int(resolution.split('x')[1])
                    cmd.extend(["--max-size", str(size)])

                cmd.extend(["--video-bit-rate", f"{self.bitrate_spin.value()}M"])
                cmd.extend(["--max-fps", str(self.fps_spin.value())])

                if self.stay_awake_cb.isChecked():
                    cmd.append("--stay-awake")
                if self.show_touches_cb.isChecked():
                    cmd.append("--show-touches")
                if self.turn_screen_off_cb.isChecked():
                    cmd.append("--turn-screen-off")

                # 让 scrcpy 窗口独立
                creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
                # 注意：不隐藏窗口，因为 scrcpy 需要显示窗口
                if sys.platform == 'win32':
                    self.scrcpy_process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=subprocess.CREATE_NEW_CONSOLE,
                    )
                else:
                    self.scrcpy_process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )

                time.sleep(0.8)
                if self.scrcpy_process.poll() is not None:
                    self.tip_label.setText(
                        f"❌ scrcpy 启动失败（返回码 {self.scrcpy_process.returncode}）"
                    )
                    self.is_starting = False
                    self.scrcpy_process = None
                    self._update_ui_state()
                    return

                self.is_mirroring = True
                self.is_starting = False
                self.check_timer.start()
                self.tip_label.setText("✅ 投屏已启动")
                self._update_ui_state()

            except Exception as e:
                self.tip_label.setText(f"❌ 启动失败: {str(e)[:120]}")
                self.is_starting = False
                self.scrcpy_process = None
                self._update_ui_state()

        threading.Thread(target=run, daemon=True).start()

    def _check_process_status(self):
        """每秒检查 scrcpy 是否还在"""
        if not self.is_mirroring:
            return
        if self.scrcpy_process and self.scrcpy_process.poll() is not None:
            self.is_mirroring = False
            self.is_starting = False
            self.check_timer.stop()
            self.scrcpy_process = None
            self.scrcpy_pid = None
            self.tip_label.setText("💡 scrcpy 窗口已关闭，点击「开始投屏」可再次启动")
            self._update_ui_state()

    # ---------- 录制 ----------
    def _toggle_record(self):
        if self.is_recording:
            self._stop_record()
        else:
            self._start_record()

    def _start_record(self):
        if not self.is_mirroring or self.is_recording or self.is_record_starting:
            return

        self.is_record_starting = True
        self.record_completed = False
        self._update_ui_state()
        self.tip_label.setText("🎬 准备开始录制...")

        def run():
            try:
                adb = get_adb_path()
                creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0

                # 清理旧文件
                subprocess.run(
                    [adb, "-s", self.device_serial, "shell", "rm", "-f", self.record_temp_path],
                    capture_output=True, timeout=5, creationflags=creationflags,
                )

                record_cmd = [adb, "-s", self.device_serial, "shell",
                              f"screenrecord --time-limit 180 --size 1920x1080 {self.record_temp_path}"]
                self.record_process = subprocess.Popen(
                    record_cmd,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=creationflags,
                )

                self.is_recording = True
                self.is_record_starting = False

                def monitor():
                    if self.record_process:
                        self.record_process.wait()
                        if self.is_recording:
                            self._finish_recording()

                threading.Thread(target=monitor, daemon=True).start()
                self.tip_label.setText("⏺ 录制中...")
                self._update_ui_state()

            except Exception as e:
                self.is_record_starting = False
                self.tip_label.setText(f"❌ 录制启动失败: {str(e)[:80]}")
                self._update_ui_state()

        threading.Thread(target=run, daemon=True).start()

    def _stop_record(self):
        if not self.is_recording or self.is_record_stopping:
            return
        self.is_record_stopping = True
        self._update_ui_state()
        self.tip_label.setText("⏹ 正在停止录制...")

        def kill_record():
            try:
                if self.record_process and self.record_process.poll() is None:
                    self.record_process.terminate()
                    self.record_process.wait(timeout=5)
            except Exception:
                try:
                    if self.record_process:
                        self.record_process.kill()
                except Exception:
                    pass

            self.is_recording = False
            self.is_record_stopping = False
            self._finish_recording()

        threading.Thread(target=kill_record, daemon=True).start()

    def _finish_recording(self):
        try:
            adb = get_adb_path()
            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            device_dir = os.path.join(self.output_dir, self.safe_serial)
            os.makedirs(device_dir, exist_ok=True)
            local_path = os.path.join(device_dir, f"screenrecord_{timestamp}.mp4")

            pull_result = subprocess.run(
                [adb, "-s", self.device_serial, "pull", self.record_temp_path, local_path],
                capture_output=True, timeout=30, creationflags=creationflags,
            )

            subprocess.run(
                [adb, "-s", self.device_serial, "shell", "rm", "-f", self.record_temp_path],
                capture_output=True, timeout=5, creationflags=creationflags,
            )

            if pull_result.returncode == 0 and os.path.exists(local_path):
                file_size = os.path.getsize(local_path)
                if file_size > 1024:
                    self.record_completed = True
                    self.tip_label.setText(f"✅ 录制完成: {os.path.basename(local_path)}")
                else:
                    try:
                        os.remove(local_path)
                    except Exception:
                        pass
                    self.tip_label.setText("⚠️ 录制文件过小，可能录制失败")
                    self.record_completed = False
            else:
                self.tip_label.setText("❌ 拉取录制文件失败")
                self.record_completed = False

        except Exception as e:
            self.tip_label.setText(f"❌ 录制完成处理失败: {str(e)[:80]}")
            self.record_completed = False

        self.is_recording = False
        self.is_record_starting = False
        self.is_record_stopping = False
        self.record_process = None
        self._update_ui_state()

    # ---------- 关闭 ----------
    def closeEvent(self, event):
        if self.is_recording or self.is_record_starting or self.is_record_stopping:
            from utils.dialogs import ConfirmDeleteDialog
            if not ConfirmDeleteDialog.ask(
                self,
                title="确认关闭",
                message="录制正在进行中，关闭窗口会停止录制并保存文件。确定吗？"
            ):
                event.ignore()
                return
            if self.is_recording:
                self._stop_record()
                time.sleep(0.5)
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
            tip_color = "#999999"
            status_idle = "#999999"
            status_rec = "#e74c3c"
            status_start = "#f39c12"
            status_done = "#2ecc71"
            start_bg = "#27ae60"
            start_hover = "#2ecc71"
            record_bg = "#3498db"
            record_hover = "#5dade2"
            record_stop_bg = "#c0392b"
            record_stop_hover = "#e74c3c"
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
            tip_color = "#7f8c8d"
            status_idle = "#95a5a6"
            status_rec = "#e74c3c"
            status_start = "#f39c12"
            status_done = "#27ae60"
            start_bg = "#27ae60"
            start_hover = "#2ecc71"
            record_bg = "#3498db"
            record_hover = "#5dade2"
            record_stop_bg = "#e74c3c"
            record_stop_hover = "#f05a4a"
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
                color: {tip_color};
                font-size: 12px;
                padding: 4px 0;
            }}
            QDialog QLabel#recordStatus {{
                color: {status_idle};
                font-size: 13px;
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
            QDialog QComboBox,
            QDialog QSpinBox {{
                background-color: {input_bg};
                color: {text};
                border: 1px solid {input_border};
                border-radius: 4px;
                padding: 3px 8px;
                font-size: 13px;
                min-height: 20px;
            }}
            QDialog QComboBox:focus,
            QDialog QSpinBox:focus {{
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
            QDialog QCheckBox {{
                color: {text};
                background: transparent;
                font-size: 13px;
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
            QDialog QPushButton#recordBtn {{
                background-color: {record_bg};
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: 500;
                font-size: 14px;
            }}
            QDialog QPushButton#recordBtn:hover {{
                background-color: {record_hover};
            }}
            QDialog QPushButton#recordBtn:disabled {{
                background-color: {disabled_bg};
                color: {disabled_fg};
            }}
        """)