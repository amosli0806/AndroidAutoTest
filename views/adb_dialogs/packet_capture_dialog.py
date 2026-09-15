# views/adb_dialogs/packet_capture_dialog.py
"""网络抓包对话框（PyQt6 + 主题适配）"""
import os
import sys
import subprocess
from datetime import datetime

import qtawesome as qta
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QLineEdit, QProgressBar, QComboBox,
    QTextEdit, QFileDialog
)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal

from utils.adb_path import get_adb_path
from utils.theme import ThemeMode
from utils.settings import Settings, THEME_MODE_DARK
from utils.toast import show_toast
from utils.dialogs import WarningDialog, ErrorDialog, ConfirmDeleteDialog


class TcpdumpDeployThread(QThread):
    """后台线程：检查/部署 tcpdump"""
    finished_with = pyqtSignal(bool, str)

    def __init__(self, serial, tcpdump_local_path):
        super().__init__()
        self.serial = serial
        self.tcpdump_local_path = tcpdump_local_path

    def run(self):
        adb = get_adb_path()
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0

        # 1. 检查 root
        try:
            r = subprocess.run(
                [adb, "-s", self.serial, "shell", "id"],
                capture_output=True, text=True, timeout=5,
                creationflags=creationflags, encoding='utf-8', errors='replace',
            )
            if "uid=0" not in (r.stdout or ""):
                self.finished_with.emit(False, "设备未 root，无法抓包。请 root 后再试。")
                return
        except Exception as e:
            self.finished_with.emit(False, f"检测 root 失败: {e}")
            return

        # 2. 检查设备上是否已有
        try:
            r = subprocess.run(
                [adb, "-s", self.serial, "shell", "ls", "/data/local/tmp/tcpdump"],
                capture_output=True, timeout=5, creationflags=creationflags,
            )
            if r.returncode == 0:
                self.finished_with.emit(True, "tcpdump 已就绪")
                return
        except Exception:
            pass

        # 3. 本地文件存在？
        if not os.path.exists(self.tcpdump_local_path):
            self.finished_with.emit(
                False,
                f"未找到 tcpdump 工具，请将 tcpdump 放到:\n{self.tcpdump_local_path}"
            )
            return

        # 4. push + chmod
        try:
            subprocess.run(
                [adb, "-s", self.serial, "push", self.tcpdump_local_path,
                 "/data/local/tmp/tcpdump"],
                check=True, timeout=30, creationflags=creationflags,
            )
            subprocess.run(
                [adb, "-s", self.serial, "shell", "chmod", "+x",
                 "/data/local/tmp/tcpdump"],
                check=True, timeout=5, creationflags=creationflags,
            )
            self.finished_with.emit(True, "tcpdump 部署成功")
        except Exception as e:
            self.finished_with.emit(False, f"部署失败: {e}")


class CaptureWorker(QThread):
    """后台线程：执行 tcpdump 抓包，实时输出到 pcap 文件"""
    started_signal = pyqtSignal(str)          # 文件路径
    finished_signal = pyqtSignal(bool, str)   # (成功, 结果)
    error_signal = pyqtSignal(str)
    size_updated = pyqtSignal(int)            # 文件大小

    def __init__(self, serial, output_dir, filter_expr=""):
        super().__init__()
        self.serial = serial
        self.output_dir = output_dir
        self.filter_expr = filter_expr
        self._is_running = True
        self.process = None
        self.capture_file = None
        self.file_handle = None

    def run(self):
        adb = get_adb_path()
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0

        # 准备输出文件
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_serial = self.serial.replace(':', '_')
        device_dir = os.path.join(self.output_dir, safe_serial)
        try:
            os.makedirs(device_dir, exist_ok=True)
        except Exception as e:
            self.error_signal.emit(f"无法创建目录: {e}")
            self.finished_signal.emit(False, "创建目录失败")
            return

        self.capture_file = os.path.join(device_dir, f"capture_{timestamp}.pcap")
        try:
            self.file_handle = open(self.capture_file, 'wb')
        except Exception as e:
            self.error_signal.emit(f"无法创建文件: {e}")
            self.finished_signal.emit(False, "创建文件失败")
            return

        cmd = [adb, "-s", self.serial, "shell", "/data/local/tmp/tcpdump",
               "-i", "any", "-s", "0", "-w", "-", "-U"]
        if self.filter_expr.strip():
            cmd.extend(["-f", self.filter_expr])

        try:
            self.process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
        except Exception as e:
            self.error_signal.emit(f"启动 tcpdump 失败: {e}")
            if self.file_handle:
                self.file_handle.close()
            self.finished_signal.emit(False, "启动失败")
            return

        self.started_signal.emit(self.capture_file)

        try:
            while self._is_running:
                data = self.process.stdout.read(8192)
                if not data:
                    break
                self.file_handle.write(data)
                self.file_handle.flush()
                size = self.file_handle.tell()
                if size % (1024 * 1024) < 8192:
                    self.size_updated.emit(size)
        except Exception as e:
            self.error_signal.emit(f"读取数据异常: {e}")
        finally:
            self._cleanup()

    def _cleanup(self):
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=3)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
            self.process = None
        if self.file_handle:
            try:
                self.file_handle.close()
            except Exception:
                pass
            self.file_handle = None
        if self.capture_file and os.path.exists(self.capture_file):
            self.size_updated.emit(os.path.getsize(self.capture_file))
        self.finished_signal.emit(True, self.capture_file)

    def stop(self):
        self._is_running = False
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
            except Exception:
                pass


class PacketCaptureDialog(QDialog):
    """网络抓包对话框"""

    def __init__(self, device_service, output_dir, parent=None):
        super().__init__(parent)
        self.device_service = device_service
        self.output_dir = output_dir
        self._theme_mode = ThemeMode.LIGHT
        self.capture_worker = None

        self.setWindowTitle("网络抓包")
        self.resize(600, 400)

        self.setup_ui()
        self.apply_theme()

        # 开始部署 tcpdump
        self._deploy_tcpdump()

    # ------------------------------------------------------------------
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("📡 网络抓包 (tcpdump)")
        title.setObjectName("titleLabel")
        layout.addWidget(title)

        warning = QLabel(
            "⚠️ 抓包需要设备已 root，且工具已部署到 /data/local/tmp/tcpdump。"
            "抓取结果保存为 pcap 文件，可用 Wireshark 分析。"
        )
        warning.setObjectName("warningLabel")
        warning.setWordWrap(True)
        layout.addWidget(warning)

        # 场景 + 过滤语法帮助
        row = QHBoxLayout()
        row.addWidget(QLabel("场景模板:"))
        self.scene_combo = QComboBox()
        self.scene_combo.addItem("自定义", "")
        self.scene_combo.addItem("HTTP/HTTPS 流量", "tcp port 80 or tcp port 443")
        self.scene_combo.addItem("DNS 查询", "udp port 53")
        self.scene_combo.addItem("ICMP (ping)", "icmp")
        self.scene_combo.addItem("WiFi 全流量（排除ARP）", "ip and not arp")
        self.scene_combo.addItem("指定主机 (需修改IP)", "host 192.168.1.100")
        self.scene_combo.addItem("排除某IP", "not host 8.8.8.8")
        self.scene_combo.currentIndexChanged.connect(self._on_scene_changed)
        row.addWidget(self.scene_combo, 1)

        self.help_btn = QPushButton("过滤语法")
        self.help_btn.setObjectName("helpBtn")
        self.help_btn.setFixedWidth(110)
        self.help_btn.setFixedHeight(32)
        self.help_btn.clicked.connect(self._show_filter_help)
        row.addWidget(self.help_btn)
        layout.addLayout(row)

        # 过滤表达式
        filt_row = QHBoxLayout()
        filt_row.addWidget(QLabel("过滤表达式:"))
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("例如 host 8.8.8.8 or port 80（留空 = 全抓）")
        filt_row.addWidget(self.filter_edit, 1)
        layout.addLayout(filt_row)

        # 状态 + 文件大小
        status_row = QHBoxLayout()
        self.status_label = QLabel("正在准备 tcpdump...")
        self.status_label.setObjectName("statusLabel")
        status_row.addWidget(self.status_label, 1)

        self.size_label = QLabel("已捕获: 0 KB")
        self.size_label.setObjectName("sizeLabel")
        status_row.addWidget(self.size_label, 0)
        layout.addLayout(status_row)

        # 按钮行
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self.start_btn = QPushButton("开始")
        self.start_btn.setObjectName("startBtn")
        self.start_btn.setFixedHeight(34)
        self.start_btn.setMinimumWidth(140)
        self.start_btn.clicked.connect(self._start_capture)
        btn_row.addWidget(self.start_btn)

        self.stop_btn = QPushButton("停止")
        self.stop_btn.setObjectName("stopBtn")
        self.stop_btn.setFixedHeight(34)
        self.stop_btn.setMinimumWidth(140)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop_capture)
        btn_row.addWidget(self.stop_btn)

        self.open_btn = QPushButton("打开文件位置")
        self.open_btn.setObjectName("openBtn")
        self.open_btn.setFixedHeight(34)
        self.open_btn.setMinimumWidth(140)
        self.open_btn.setEnabled(False)
        self.open_btn.clicked.connect(self._open_file_location)
        btn_row.addWidget(self.open_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        # 大小刷新定时器（备用，主要靠信号）
        self.size_timer = QTimer()
        self.size_timer.timeout.connect(self._update_file_size)

    # ------------------------------------------------------------------
    def _get_serial(self):
        return self.device_service.serial if self.device_service else None

    def _deploy_tcpdump(self):
        serial = self._get_serial()
        if not serial:
            self.status_label.setText("⚠️ 设备未连接")
            self._set_buttons(deploying=True)
            return

        # 项目根目录下 tools/tcpdump
        from utils.adb_path import get_adb_path
        adb_path = get_adb_path()
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        # adb_path 可能在 tools/ 里
        tcpdump_path = os.path.join(os.path.dirname(adb_path), "tcpdump")
        # 兜底：项目根 tools/tcpdump
        if not os.path.exists(tcpdump_path):
            tcpdump_path = os.path.join(project_root, "tools", "tcpdump")

        self.deploy_thread = TcpdumpDeployThread(serial, tcpdump_path)
        self.deploy_thread.finished_with.connect(self._on_deploy_done)
        self.deploy_thread.start()
        self._set_buttons(deploying=True)

    def _on_deploy_done(self, success: bool, message: str):
        if success:
            self.status_label.setText("就绪")
            self._set_buttons(deploying=False)
        else:
            self.status_label.setText(f"❌ {message}")
            self._set_buttons(deploying=True)
            WarningDialog.show_warning(self, "初始化失败", message)

    def _set_buttons(self, deploying=False, capturing=False, stopped=False):
        """更新按钮的启用状态（不用图标切换，简化）"""
        if deploying:
            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(False)
            self.open_btn.setEnabled(False)
        elif capturing:
            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(True)
            self.open_btn.setEnabled(False)
        elif stopped:
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self.open_btn.setEnabled(True)
        else:
            # 就绪
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self.open_btn.setEnabled(False)

    def _on_scene_changed(self, index):
        expr = self.scene_combo.currentData()
        if expr:
            self.filter_edit.setText(expr)
        else:
            self.filter_edit.clear()

    def _show_filter_help(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("过滤语法帮助")
        dlg.resize(480, 520)
        lay = QVBoxLayout(dlg)

        text = QTextEdit()
        text.setReadOnly(True)
        text.setHtml("""
        <h3>📖 tcpdump 过滤语法速查</h3>
        <p><b>基本结构：</b> [协议] [方向] [类型] [值]</p>
        <table border="1" cellpadding="5" style="border-collapse: collapse;">
        <tr><th>示例</th><th>说明</th></tr>
        <tr><td><code>host 192.168.1.100</code></td><td>指定主机 IP</td></tr>
        <tr><td><code>port 80</code></td><td>指定端口</td></tr>
        <tr><td><code>tcp</code></td><td>仅 TCP 包</td></tr>
        <tr><td><code>udp</code></td><td>仅 UDP 包</td></tr>
        <tr><td><code>icmp</code></td><td>仅 ICMP (ping)</td></tr>
        <tr><td><code>src host 10.0.0.1</code></td><td>源地址</td></tr>
        <tr><td><code>dst port 53</code></td><td>目的端口</td></tr>
        <tr><td><code>not arp</code></td><td>排除 ARP 包</td></tr>
        <tr><td><code>host 1.2.3.4 and tcp</code></td><td>与</td></tr>
        <tr><td><code>host 1.2.3.4 or host 5.6.7.8</code></td><td>或</td></tr>
        </table>
        <p>💡 留空表示抓取所有流量，可能产生较大文件。</p>
        """)
        lay.addWidget(text)
        dlg.exec()

    def _start_capture(self):
        serial = self._get_serial()
        if not serial:
            WarningDialog.show_warning(self, "提示", "设备未连接")
            return

        if self.capture_worker and self.capture_worker.isRunning():
            return

        filter_expr = self.filter_edit.text().strip()
        self.capture_worker = CaptureWorker(serial, self.output_dir, filter_expr)
        self.capture_worker.started_signal.connect(self._on_started)
        self.capture_worker.error_signal.connect(self._on_error)
        self.capture_worker.finished_signal.connect(self._on_finished)
        self.capture_worker.size_updated.connect(self._on_size)

        self._set_buttons(capturing=True)
        self.status_label.setText("正在启动抓包...")
        self.size_label.setText("已捕获: 0 KB")
        self.capture_worker.start()
        self.size_timer.start(1000)

    def _stop_capture(self):
        if self.capture_worker and self.capture_worker.isRunning():
            self.capture_worker.stop()
            self.status_label.setText("正在停止...")
            self.stop_btn.setEnabled(False)

    def _on_started(self, path: str):
        self.status_label.setText(f"抓包中... 文件: {os.path.basename(path)}")

    def _on_error(self, msg: str):
        self.status_label.setText(f"❌ {msg}")

    def _on_finished(self, success: bool, result: str):
        self.size_timer.stop()
        if success:
            self._set_buttons(stopped=True)
            self.status_label.setText(f"✅ 抓包完成: {os.path.basename(result)}")
            show_toast(self, "抓包完成", duration=2000)
        else:
            self._set_buttons(stopped=False)
            self.status_label.setText(f"❌ {result}")

    def _on_size(self, size: int):
        kb = size / 1024
        mb = kb / 1024
        if mb >= 1:
            self.size_label.setText(f"已捕获: {mb:.2f} MB")
        else:
            self.size_label.setText(f"已捕获: {kb:.1f} KB")

    def _update_file_size(self):
        if self.capture_worker and self.capture_worker.capture_file:
            try:
                size = os.path.getsize(self.capture_worker.capture_file)
                self._on_size(size)
            except Exception:
                pass

    def _open_file_location(self):
        if not self.capture_worker or not self.capture_worker.capture_file:
            return
        path = self.capture_worker.capture_file
        if not os.path.exists(path):
            return
        folder = os.path.dirname(path)
        if sys.platform == 'win32':
            os.startfile(folder)
        elif sys.platform == 'darwin':
            subprocess.run(["open", folder])
        else:
            subprocess.run(["xdg-open", folder])

    def closeEvent(self, event):
        if self.capture_worker and self.capture_worker.isRunning():
            if not ConfirmDeleteDialog.ask(
                self,
                title="确认关闭",
                message="抓包进行中，关闭窗口会停止抓包。确定吗？"
            ):
                event.ignore()
                return
            self.capture_worker.stop()
            self.capture_worker.wait(3000)
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
            start_bg = "#27ae60"
            start_hover = "#2ecc71"
            stop_bg = "#c0392b"
            stop_hover = "#e74c3c"
            open_bg = "#3498db"
            open_hover = "#5dade2"
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
            start_bg = "#27ae60"
            start_hover = "#2ecc71"
            stop_bg = "#e74c3c"
            stop_hover = "#f05a4a"
            open_bg = "#3498db"
            open_hover = "#5dade2"
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
            QDialog QLabel#sizeLabel {{
                color: {status_color};
                font-size: 12px;
            }}
            QDialog QLineEdit, QDialog QComboBox {{
                background-color: {input_bg};
                color: {text};
                border: 1px solid {input_border};
                border-radius: 4px;
                padding: 5px 8px;
                font-size: 13px;
                min-height: 22px;
            }}
            QDialog QLineEdit:focus, QDialog QComboBox:focus {{
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
            QDialog QPushButton#startBtn {{
                background-color: {start_bg};
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: 500;
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
            }}
            QDialog QPushButton#stopBtn:hover {{
                background-color: {stop_hover};
            }}
            QDialog QPushButton#stopBtn:disabled {{
                background-color: {disabled_bg};
                color: {disabled_fg};
            }}
            QDialog QPushButton#openBtn {{
                background-color: {open_bg};
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: 500;
            }}
            QDialog QPushButton#openBtn:hover {{
                background-color: {open_hover};
            }}
            QDialog QPushButton#openBtn:disabled {{
                background-color: {disabled_bg};
                color: {disabled_fg};
            }}
        """)