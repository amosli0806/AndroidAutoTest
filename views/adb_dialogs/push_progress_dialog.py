# views/adb_dialogs/push_progress_dialog.py
"""文件推送进度对话框（PyQt6 + 主题适配）"""
import os
import sys
import subprocess
import threading

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QProgressBar, QPushButton
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal

from utils.adb_path import get_adb_path
from utils.theme import ThemeMode
from utils.settings import Settings, THEME_MODE_DARK
from utils.dialogs import ConfirmDeleteDialog


class PushWorker(QThread):
    """后台推送线程"""

    progress_updated = pyqtSignal(int)         # 0-100
    output_line = pyqtSignal(str)              # 每行输出
    finished_with = pyqtSignal(bool, str)      # (成功, 消息)

    def __init__(self, adb_path, device_serial, local_path, remote_path, parent=None):
        super().__init__(parent)
        self.adb_path = adb_path
        self.device_serial = device_serial
        self.local_path = local_path
        self.remote_path = remote_path
        self._process = None
        self._is_running = True
        self._cancelled = False

    def run(self):
        try:
            startupinfo = None
            creationflags = 0
            if sys.platform == 'win32':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE
                creationflags = subprocess.CREATE_NO_WINDOW

            # 如果目标是目录，先 mkdir
            is_directory = (
                self.remote_path.endswith('/')
                or os.path.isdir(self.local_path)
            )
            if is_directory:
                mkdir_cmd = [self.adb_path, "-s", self.device_serial,
                             "shell", "mkdir", "-p", self.remote_path]
                mkdir_result = subprocess.run(
                    mkdir_cmd, capture_output=True, text=True, timeout=5,
                    startupinfo=startupinfo, creationflags=creationflags,
                )
                if mkdir_result.returncode != 0:
                    self.finished_with.emit(
                        False,
                        f"无法创建远程目录 {self.remote_path}\n{mkdir_result.stderr}"
                    )
                    return

            # 执行 push
            cmd = [self.adb_path, "-s", self.device_serial,
                   "push", self.local_path, self.remote_path]
            self._process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding='utf-8', errors='replace',
                bufsize=1, universal_newlines=True,
                startupinfo=startupinfo, creationflags=creationflags,
            )

            def read_stdout():
                for line in iter(self._process.stdout.readline, ''):
                    if not self._is_running:
                        break
                    line = line.rstrip('\n')
                    if line:
                        self.output_line.emit(line)

            def read_stderr():
                for line in iter(self._process.stderr.readline, ''):
                    if not self._is_running:
                        break
                    line = line.rstrip('\n')
                    if line:
                        self.output_line.emit(f"[STDERR] {line}")

            t_out = threading.Thread(target=read_stdout, daemon=True)
            t_err = threading.Thread(target=read_stderr, daemon=True)
            t_out.start()
            t_err.start()

            returncode = self._process.wait(timeout=600)
            t_out.join(timeout=1)
            t_err.join(timeout=1)

            if self._cancelled:
                self.finished_with.emit(False, "推送已取消")
            elif returncode == 0:
                self.progress_updated.emit(100)
                self.finished_with.emit(True, "推送成功")
            else:
                self.finished_with.emit(False, f"推送失败，返回码：{returncode}")

        except subprocess.TimeoutExpired:
            if self._cancelled:
                self.finished_with.emit(False, "推送已取消")
            else:
                if self._process:
                    self._process.kill()
                self.finished_with.emit(False, "推送超时")
        except Exception as e:
            self.finished_with.emit(False, f"推送异常：{e}")
        finally:
            self._is_running = False

    def stop(self):
        self._is_running = False
        self._cancelled = True
        if self._process and self._process.poll() is None:
            try:
                self._process.terminate()
                self._process.wait(timeout=2)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass


class PushProgressDialog(QDialog):
    """推送进度对话框"""

    log_message = pyqtSignal(str)   # 每行输出信号，由调用方接收并转发到日志

    def __init__(self, device_service, local_path, remote_path, parent=None):
        super().__init__(parent)
        self.device_service = device_service
        self.local_path = local_path
        self.remote_path = remote_path
        self._theme_mode = ThemeMode.LIGHT
        self._success = False

        self.setWindowTitle("正在推送文件")
        self.setModal(True)
        self.resize(520, 220)

        self.setup_ui()
        self.apply_theme()

        # 启动工作线程
        serial = device_service.serial if device_service else None
        if not serial:
            self._on_finished(False, "设备未连接")
            return

        self.worker = PushWorker(
            get_adb_path(), serial, local_path, remote_path, self
        )
        self.worker.progress_updated.connect(self._on_progress)
        self.worker.output_line.connect(self._on_output_line)
        self.worker.finished_with.connect(self._on_finished)
        self.worker.start()

    # ------------------------------------------------------------------
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel("📤 文件推送")
        title.setObjectName("titleLabel")
        layout.addWidget(title)

        self.path_label = QLabel(
            f"正在推送:\n{os.path.basename(self.local_path)} → {self.remote_path}"
        )
        self.path_label.setObjectName("pathLabel")
        self.path_label.setWordWrap(True)
        layout.addWidget(self.path_label)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setObjectName("progressBar")
        self.progress.setTextVisible(True)
        layout.addWidget(self.progress)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.action_btn = QPushButton("取消")
        self.action_btn.setObjectName("cancelBtn")
        self.action_btn.setFixedHeight(34)
        self.action_btn.setMinimumWidth(140)
        self.action_btn.clicked.connect(self._on_action)
        btn_layout.addWidget(self.action_btn)

        btn_layout.addStretch()
        layout.addLayout(btn_layout)

    # ------------------------------------------------------------------
    def _on_progress(self, percent: int):
        self.progress.setValue(percent)

    def _on_output_line(self, line: str):
        self.log_message.emit(f"[推送] {line}")
        # 尝试从输出里解析百分比（如 "32% ..."）
        import re
        m = re.search(r'(\d+)%', line)
        if m:
            try:
                self.progress.setValue(int(m.group(1)))
            except Exception:
                pass

    def _on_finished(self, success: bool, message: str):
        self._success = success
        if success:
            self.progress.setValue(100)
            self.path_label.setText(f"✅ {message}")
        else:
            self.path_label.setText(f"❌ {message}")
            self.log_message.emit(f"[推送失败] {message}")

        # 取消按钮变关闭
        self.action_btn.setText("关闭")
        self.action_btn.setObjectName("closeBtn")
        self.action_btn.setStyleSheet(self._close_btn_qss())
        try:
            self.action_btn.clicked.disconnect()
        except Exception:
            pass
        self.action_btn.clicked.connect(self.accept)

    def _close_btn_qss(self) -> str:
        if self._theme_mode == ThemeMode.DARK:
            return """
                QPushButton {
                    background-color: #555;
                    color: #eeeeee;
                    border: 1px solid #666;
                    border-radius: 6px;
                    font-weight: 500;
                    font-size: 14px;
                }
                QPushButton:hover { background-color: #666; }
            """
        else:
            return """
                QPushButton {
                    background-color: #f0f0f0;
                    color: #333333;
                    border: 1px solid #d0d0d0;
                    border-radius: 6px;
                    font-weight: 500;
                    font-size: 14px;
                }
                QPushButton:hover { background-color: #e0e0e0; }
            """

    def _on_action(self):
        if not self._success and self.worker and self.worker.isRunning():
            if not ConfirmDeleteDialog.ask(
                self,
                title="取消推送",
                message="确定取消本次推送吗？",
                detail="已传输的部分文件将保留。"
            ):
                return
            self.worker.stop()
            self.action_btn.setEnabled(False)
            self.path_label.setText("正在取消...")
        else:
            self.accept()

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            if not ConfirmDeleteDialog.ask(
                self,
                title="取消推送",
                message="推送进行中，确定关闭窗口吗？",
                detail="推送将被取消。"
            ):
                event.ignore()
                return
            self.worker.stop()
            self.worker.wait(3000)
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
            title_color = "#ffffff"
            path_color = "#aaaaaa"
            prog_bg = "#1e1e1e"
            prog_chunk = "#3498db"
            cancel_bg = "#c0392b"
            cancel_hover = "#e74c3c"
            close_bg = "#555"
            close_fg = "#eeeeee"
            close_hover = "#666"
        else:
            bg = "#ffffff"
            text = "#333333"
            border = "#d0d0d0"
            title_color = "#1a1a1a"
            path_color = "#7f8c8d"
            prog_bg = "#f0f0f0"
            prog_chunk = "#3498db"
            cancel_bg = "#e74c3c"
            cancel_hover = "#f05a4a"
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
            QDialog QLabel#pathLabel {{
                color: {path_color};
                font-size: 12px;
                padding: 4px 0;
            }}
            QDialog QProgressBar#progressBar {{
                background-color: {prog_bg};
                border: 1px solid {border};
                border-radius: 4px;
                height: 22px;
                text-align: center;
                color: {text};
                font-size: 12px;
            }}
            QDialog QProgressBar#progressBar::chunk {{
                background-color: {prog_chunk};
                border-radius: 3px;
            }}
            QDialog QPushButton#cancelBtn {{
                background-color: {cancel_bg};
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: 500;
                font-size: 14px;
            }}
            QDialog QPushButton#cancelBtn:hover {{
                background-color: {cancel_hover};
            }}
            QDialog QPushButton#cancelBtn:disabled {{
                background-color: {close_bg};
                color: #999;
            }}
            QDialog QPushButton#closeBtn {{
                background-color: {close_bg};
                color: {close_fg};
                border: 1px solid {border};
                border-radius: 6px;
                font-weight: 500;
                font-size: 14px;
            }}
            QDialog QPushButton#closeBtn:hover {{
                background-color: {close_hover};
            }}
        """)