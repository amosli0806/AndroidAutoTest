# views/adb_dialogs/memory_monitor_dialog.py
"""内存监控对话框（PyQt6 + 主题适配）"""
import os
import subprocess
import sys
import threading
import time
from datetime import datetime

import qtawesome as qta
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QProgressBar, QTextEdit, QInputDialog
)
from PyQt6.QtCore import Qt, pyqtSignal

from utils.adb_path import get_adb_path
from utils.memory_analyzer import (
    parse_memory_data, _generate_chart, _export_to_excel
)
from utils.theme import ThemeMode
from utils.settings import Settings, THEME_MODE_DARK
from utils.toast import show_toast
from utils.dialogs import WarningDialog, ErrorDialog


# 设备端路径
REMOTE_WORK_DIR = "/data/local/tmp/dumpmeminfo"
REMOTE_SCRIPT_PATH = f"{REMOTE_WORK_DIR}/dump.sh"
REMOTE_OUTPUT_PATH = f"{REMOTE_WORK_DIR}/output.txt"


class MemoryMonitorDialog(QDialog):
    """内存监控：设备端后台采集 + 拉取数据 + 生成报告"""

    log_signal = pyqtSignal(str)

    def __init__(self, device_service, output_dir, parent=None):
        super().__init__(parent)
        self.device_service = device_service
        self.output_dir = output_dir
        self._theme_mode = ThemeMode.LIGHT
        self.local_data_file = None

        self.setWindowTitle("内存监控")
        self.resize(580, 500)

        self.setup_ui()
        self.log_signal.connect(self._append_log)
        self.apply_theme()

    # ------------------------------------------------------------------
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("📊 内存监控")
        title.setObjectName("titleLabel")
        layout.addWidget(title)

        info = QLabel(
            "功能说明：\n"
            "1. 启动监控后，设备后台每 30 秒记录一次应用内存信息。\n"
            "2. 停止监控后，可拉取数据到本地。\n"
            "3. 生成报告会解析数据并输出 HTML 图表 + Excel 文件。\n"
            "⚠️ 需要 root 权限，脚本存放于 /data/local/tmp/"
        )
        info.setObjectName("infoLabel")
        info.setWordWrap(True)
        layout.addWidget(info)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setObjectName("logText")
        layout.addWidget(self.log_text, 1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)  # 无限进度条
        self.progress.setVisible(False)
        self.progress.setObjectName("progressBar")
        layout.addWidget(self.progress)

        # 按钮行
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)

        self.start_btn = QPushButton("启动监控")
        self.start_btn.setObjectName("startBtn")
        self.start_btn.setFixedHeight(34)
        self.start_btn.clicked.connect(self._start_monitor)
        btn_layout.addWidget(self.start_btn)

        self.stop_btn = QPushButton("停止监控")
        self.stop_btn.setObjectName("stopBtn")
        self.stop_btn.setFixedHeight(34)
        self.stop_btn.clicked.connect(self._stop_monitor)
        btn_layout.addWidget(self.stop_btn)

        self.pull_btn = QPushButton("拉取数据")
        self.pull_btn.setObjectName("pullBtn")
        self.pull_btn.setFixedHeight(34)
        self.pull_btn.clicked.connect(self._pull_data)
        btn_layout.addWidget(self.pull_btn)

        self.report_btn = QPushButton("生成报告")
        self.report_btn.setObjectName("reportBtn")
        self.report_btn.setFixedHeight(34)
        self.report_btn.clicked.connect(self._generate_report)
        btn_layout.addWidget(self.report_btn)

        layout.addLayout(btn_layout)

    # ------------------------------------------------------------------
    def _get_serial(self):
        return self.device_service.serial if self.device_service else None

    def _run_adb(self, cmd: list, timeout=15):
        serial = self._get_serial()
        if not serial:
            return -1, "", "设备未连接"
        try:
            result = subprocess.run(
                [get_adb_path(), "-s", serial] + cmd,
                capture_output=True, text=True, timeout=timeout,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
                encoding='utf-8', errors='replace',
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return -1, "", "Timeout"
        except Exception as e:
            return -1, "", str(e)

    def _check_root(self) -> bool:
        rc, out, _ = self._run_adb(["shell", "id"], timeout=5)
        return rc == 0 and "uid=0" in out

    def _append_log(self, text: str):
        self.log_text.append(text)
        # 自动滚到最新
        self.log_text.verticalScrollBar().setValue(
            self.log_text.verticalScrollBar().maximum()
        )

    # ------------------------------------------------------------------
    def _ensure_environment(self) -> bool:
        """确保设备上有 dump.sh 脚本和工作目录"""
        if not self._check_root():
            self._append_log("❌ 设备未 root 或 ADB 无 root 权限")
            return False

        self._append_log("正在准备设备环境...")

        # 1. 确保本地脚本存在
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        local_script = os.path.join(project_root, "resources", "scripts", "dump.sh")
        if not os.path.exists(local_script):
            self._append_log(f"⚠️ 本地脚本不存在: {local_script}，正在自动创建...")
            os.makedirs(os.path.dirname(local_script), exist_ok=True)
            default_script = """#!/system/bin/sh
PACKAGE="com.baidu.naviauto"
OUTPUT_DIR="/data/local/tmp/dumpmeminfo"
mkdir -p $OUTPUT_DIR
cd $OUTPUT_DIR
echo "=== 监控启动于 $(date) ===" >> dump.log
i=0
while true; do
    i=$((i+1))
    date "+%Y-%m-%d %H:%M:%S" >> output.txt
    dumpsys meminfo $PACKAGE >> output.txt 2>> dump.log
    sleep 30
done
"""
            with open(local_script, 'w', encoding='utf-8', newline='\n') as f:
                f.write(default_script)
            try:
                os.chmod(local_script, 0o755)
            except Exception:
                pass
            self._append_log("✅ 已自动创建默认脚本")

        # 2. 创建设备端目录
        rc, _, err = self._run_adb(["shell", "mkdir", "-p", REMOTE_WORK_DIR])
        if rc != 0:
            self._append_log(f"❌ 创建工作目录失败: {err}")
            return False

        # 3. push 脚本
        self._append_log("推送 dump.sh ...")
        rc, _, err = self._run_adb(
            ["push", local_script, REMOTE_SCRIPT_PATH], timeout=30
        )
        if rc != 0:
            self._append_log(f"❌ 推送脚本失败: {err}")
            # 尝试 adb root 后再试一次
            self._append_log("尝试 adb root 后重新推送...")
            self._run_adb(["root"])
            time.sleep(2)
            rc, _, err = self._run_adb(
                ["push", local_script, REMOTE_SCRIPT_PATH], timeout=30
            )
            if rc != 0:
                self._append_log(f"❌ 重新推送失败: {err}")
                return False

        # 4. chmod
        self._run_adb(["shell", "chmod", "755", REMOTE_SCRIPT_PATH])

        # 5. 验证
        rc, out, _ = self._run_adb(
            ["shell", f"test -f {REMOTE_SCRIPT_PATH} && echo OK"]
        )
        if "OK" not in out:
            self._append_log(f"❌ 脚本验证失败: {REMOTE_SCRIPT_PATH}")
            return False

        self._append_log("✅ 环境准备完成")
        return True

    def _get_monitor_pids(self):
        """返回 dump.sh 相关进程 PID"""
        pids = []
        rc, out, _ = self._run_adb(["shell", "pgrep -f 'dump.sh'"], timeout=5)
        if rc == 0 and out.strip():
            for pid in out.splitlines():
                pid = pid.strip()
                if pid.isdigit():
                    pids.append(pid)
        # 兜底：ps 过滤
        if not pids:
            rc, out, _ = self._run_adb(
                ["shell", "ps -A | grep dump.sh | grep -v grep"], timeout=5
            )
            if rc == 0 and out.strip():
                for line in out.splitlines():
                    parts = line.split()
                    if len(parts) >= 2 and parts[1].isdigit():
                        pids.append(parts[1])
        return pids

    def _check_monitor_running(self) -> bool:
        return len(self._get_monitor_pids()) > 0

    # ------------------------------------------------------------------
    def _start_monitor(self):
        def worker():
            if not self._ensure_environment():
                self.log_signal.emit("❌ 环境准备失败，无法启动监控")
                return

            if self._check_monitor_running():
                self.log_signal.emit("⚠️ 监控已在运行中")
                return

            self.log_signal.emit("正在启动监控...")

            # 后台启动脚本
            full_cmd = (
                f"cd {REMOTE_WORK_DIR} && "
                f"nohup sh dump.sh > output.txt 2>&1 < /dev/null &"
            )
            rc, out, err = self._run_adb(["shell", full_cmd], timeout=10)
            if rc != 0:
                self.log_signal.emit(f"⚠️ 启动命令返回非零: {rc}, err: {err}")

            time.sleep(3)

            if self._check_monitor_running():
                pids = self._get_monitor_pids()
                self.log_signal.emit(f"✅ 监控已启动 (PID: {', '.join(pids)})")
                threading.Thread(target=self._verify_output_file, daemon=True).start()
            else:
                self.log_signal.emit("❌ 启动失败，未检测到进程")
                self._read_error_log(f"{REMOTE_WORK_DIR}/dump.log")

        threading.Thread(target=worker, daemon=True).start()

    def _read_error_log(self, log_file):
        rc, out, _ = self._run_adb(
            ["shell", f"cat {log_file} 2>/dev/null"], timeout=5
        )
        if rc == 0 and out.strip():
            self.log_signal.emit(f"📄 启动错误日志:\n{out.strip()}")
        else:
            self.log_signal.emit("📄 无错误日志输出")

    def _verify_output_file(self):
        time.sleep(10)
        rc, out, _ = self._run_adb(
            ["shell", f"ls -l {REMOTE_OUTPUT_PATH} 2>/dev/null || echo NOT_FOUND"],
            timeout=5,
        )
        if "NOT_FOUND" in out:
            self.log_signal.emit("⚠️ 输出文件尚未生成，请检查脚本是否正确执行")
        else:
            self.log_signal.emit(f"✅ 输出文件已创建: {out.strip()}")

    def _stop_monitor(self):
        def worker():
            pids = self._get_monitor_pids()
            if not pids:
                self.log_signal.emit("⚠️ 没有运行中的监控任务")
                return

            self.log_signal.emit(f"正在终止进程: {', '.join(pids)}")
            rc, _, _ = self._run_adb(["shell", "pkill -f 'sh dump.sh'"], timeout=10)
            if rc != 0:
                self.log_signal.emit("⚠️ pkill 失败，尝试 killall...")
                self._run_adb(["shell", "killall dump.sh"], timeout=10)

            time.sleep(1)
            remaining = self._get_monitor_pids()
            if remaining:
                for pid in remaining:
                    self._run_adb(["shell", f"kill -9 {pid}"], timeout=3)
                time.sleep(1)

            if self._check_monitor_running():
                self.log_signal.emit(f"❌ 仍有进程残留: {self._get_monitor_pids()}")
            else:
                self.log_signal.emit("✅ 监控已停止")

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------
    def _pull_data(self):
        serial = self._get_serial()
        if not serial:
            WarningDialog.show_warning(self, "提示", "设备未连接")
            return

        safe_serial = serial.replace(':', '_')
        device_dir = os.path.join(self.output_dir, safe_serial)
        os.makedirs(device_dir, exist_ok=True)
        local_file = os.path.join(device_dir, "memory_output.txt")

        def worker():
            self.log_signal.emit("正在拉取 output.txt ...")
            self.progress.setVisible(True)

            # 先检查设备上文件是否存在
            rc, out, _ = self._run_adb(
                ["shell", f"test -f {REMOTE_OUTPUT_PATH} && echo exists"]
            )
            if "exists" not in out:
                self.log_signal.emit(f"⚠️ 设备上未找到 {REMOTE_OUTPUT_PATH}")
                self.progress.setVisible(False)
                return

            rc, _, err = self._run_adb(
                ["pull", REMOTE_OUTPUT_PATH, local_file], timeout=60
            )
            self.progress.setVisible(False)

            if rc == 0 and os.path.exists(local_file):
                self.local_data_file = local_file
                size_kb = os.path.getsize(local_file) / 1024
                self.log_signal.emit(f"✅ 数据已保存至: {local_file} ({size_kb:.1f} KB)")
            else:
                self.log_signal.emit(f"❌ 拉取失败: {err}")

        threading.Thread(target=worker, daemon=True).start()

    def _generate_report(self):
        if not self.local_data_file or not os.path.exists(self.local_data_file):
            WarningDialog.show_warning(self, "提示", "请先拉取数据文件")
            return

        car_model, ok = QInputDialog.getText(
            self, "车型信息", "请输入车型型号:", text="车型A"
        )
        if not ok or not car_model:
            return

        def worker():
            self.log_signal.emit("正在解析数据并生成报告...")
            self.progress.setVisible(True)

            try:
                data = parse_memory_data(self.local_data_file)
                if not data.get('timestamp'):
                    self.log_signal.emit("❌ 未解析到有效数据")
                    self.progress.setVisible(False)
                    return

                serial = self._get_serial() or "unknown"
                safe_serial = serial.replace(':', '_')
                report_dir = os.path.join(
                    self.output_dir, f"{safe_serial}_memory_reports"
                )
                os.makedirs(report_dir, exist_ok=True)

                start_time = data['timestamp'][0].replace(':', '-')
                end_time = data['timestamp'][-1].replace(':', '-')
                if not start_time:
                    start_time = datetime.now().strftime("%Y%m%d_%H%M%S")
                    end_time = start_time

                base_name = f"{car_model}_{start_time}_{end_time}"
                chart_path = os.path.join(report_dir, f"{base_name}.html")
                excel_path = os.path.join(report_dir, f"{base_name}.xlsx")

                _generate_chart(data, chart_path, car_model)
                _export_to_excel(data, excel_path)

                self.log_signal.emit(
                    f"✅ 报告生成完毕!\n图表: {chart_path}\nExcel: {excel_path}"
                )
                # 打开图表
                import webbrowser
                webbrowser.open(chart_path)
            except Exception as e:
                self.log_signal.emit(f"❌ 生成报告出错: {e}")
            finally:
                self.progress.setVisible(False)

        threading.Thread(target=worker, daemon=True).start()

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
            log_bg = "#1e1e1e"
            log_border = "#3a3a3a"
            title_color = "#ffffff"
            info_color = "#aaaaaa"
            info_bg = "#3a2a1a"
            info_border = "#ff9800"
            info_text = "#ffb74d"
            start_bg = "#27ae60"
            start_hover = "#2ecc71"
            stop_bg = "#c0392b"
            stop_hover = "#e74c3c"
            pull_bg = "#3498db"
            pull_hover = "#5dade2"
            report_bg = "#9b59b6"
            report_hover = "#a569bd"
            prog_bg = "#1e1e1e"
            prog_chunk = "#3498db"
        else:
            bg = "#ffffff"
            text = "#333333"
            border = "#d0d0d0"
            log_bg = "#fafbfc"
            log_border = "#e0e0e0"
            title_color = "#1a1a1a"
            info_color = "#7f8c8d"
            info_bg = "#fff3e0"
            info_border = "#ff9800"
            info_text = "#e65100"
            start_bg = "#27ae60"
            start_hover = "#2ecc71"
            stop_bg = "#e74c3c"
            stop_hover = "#f05a4a"
            pull_bg = "#3498db"
            pull_hover = "#5dade2"
            report_bg = "#9b59b6"
            report_hover = "#a569bd"
            prog_bg = "#f0f0f0"
            prog_chunk = "#3498db"

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
            QDialog QLabel#infoLabel {{
                background-color: {info_bg};
                color: {info_text};
                border-left: 4px solid {info_border};
                border-radius: 4px;
                padding: 8px 12px;
                font-size: 12px;
            }}
            QDialog QTextEdit#logText {{
                background-color: {log_bg};
                color: {text};
                border: 1px solid {log_border};
                border-radius: 6px;
                padding: 8px;
                font-family: Consolas, monospace;
                font-size: 12px;
            }}
            QDialog QProgressBar#progressBar {{
                background-color: {prog_bg};
                border: 1px solid {border};
                border-radius: 4px;
                height: 20px;
            }}
            QDialog QProgressBar#progressBar::chunk {{
                background-color: {prog_chunk};
                border-radius: 3px;
            }}
            QDialog QPushButton#startBtn {{
                background-color: {start_bg};
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: 500;
                font-size: 13px;
            }}
            QDialog QPushButton#startBtn:hover {{
                background-color: {start_hover};
            }}
            QDialog QPushButton#stopBtn {{
                background-color: {stop_bg};
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: 500;
                font-size: 13px;
            }}
            QDialog QPushButton#stopBtn:hover {{
                background-color: {stop_hover};
            }}
            QDialog QPushButton#pullBtn {{
                background-color: {pull_bg};
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: 500;
                font-size: 13px;
            }}
            QDialog QPushButton#pullBtn:hover {{
                background-color: {pull_hover};
            }}
            QDialog QPushButton#reportBtn {{
                background-color: {report_bg};
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: 500;
                font-size: 13px;
            }}
            QDialog QPushButton#reportBtn:hover {{
                background-color: {report_hover};
            }}
        """)