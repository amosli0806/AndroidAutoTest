# views/adb_dialogs/anr_analyzer_dialog.py
"""ANR 日志分析对话框（PyQt6 + 主题适配）"""
import os
import re
import sys
import subprocess
import threading
import zipfile
import tempfile

import qtawesome as qta
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget,
    QWidget, QPushButton, QTextEdit, QFileDialog, QLabel,
    QSizePolicy
)
from PyQt6.QtCore import Qt, pyqtSignal

from utils.adb_path import get_adb_path
from utils.theme import ThemeMode
from utils.settings import Settings, THEME_MODE_DARK
from utils.toast import show_toast
from utils.dialogs import WarningDialog, ErrorDialog


class AnrAnalyzerDialog(QDialog):
    """ANR 分析：从设备拉取 / 从本地导入"""

    device_status_signal = pyqtSignal(str)
    device_list_signal = pyqtSignal(str)
    result_signal = pyqtSignal(str)

    def __init__(self, device_service, parent=None):
        super().__init__(parent)
        self.device_service = device_service
        self._theme_mode = ThemeMode.LIGHT
        self._local_file = None

        self.setWindowTitle("ANR 日志分析")
        self.resize(760, 620)

        self.setup_ui()
        self.device_status_signal.connect(self._on_status)
        self.device_list_signal.connect(self._on_device_list)
        self.result_signal.connect(self._on_result)
        self.apply_theme()

        self._refresh_anr_list()

    # ------------------------------------------------------------------
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("🔍 ANR 日志分析")
        title.setObjectName("titleLabel")
        layout.addWidget(title)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("mainTabs")

        self.tabs.addTab(self._build_device_tab(), "从设备拉取")
        self.tabs.addTab(self._build_local_tab(), "从本地导入")
        layout.addWidget(self.tabs, 1)

        # 结果展示
        result_label = QLabel("分析结果")
        result_label.setObjectName("sectionLabel")
        layout.addWidget(result_label)

        self.result_text = QTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setObjectName("resultText")
        self.result_text.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.result_text.setPlaceholderText("解析后的 ANR 信息会显示在这里")
        layout.addWidget(self.result_text, 1)

        # 底部按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.save_btn = QPushButton("保存报告")
        self.save_btn.setObjectName("saveBtn")
        self.save_btn.setFixedHeight(34)
        self.save_btn.setMinimumWidth(140)
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self._on_save)
        btn_layout.addWidget(self.save_btn)

        btn_layout.addSpacing(10)

        self.close_btn = QPushButton("关闭")
        self.close_btn.setObjectName("closeBtn")
        self.close_btn.setFixedHeight(34)
        self.close_btn.setMinimumWidth(140)
        self.close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(self.close_btn)

        btn_layout.addStretch()
        layout.addLayout(btn_layout)

    def _build_device_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        self.device_status_label = QLabel("设备连接状态: 检查中...")
        self.device_status_label.setObjectName("statusLabel")
        layout.addWidget(self.device_status_label)

        self.device_list_text = QTextEdit()
        self.device_list_text.setReadOnly(True)
        self.device_list_text.setObjectName("listText")
        self.device_list_text.setPlaceholderText("/data/anr/ 目录下的文件列表")
        layout.addWidget(self.device_list_text, 1)

        btn_layout = QHBoxLayout()
        self.refresh_btn = QPushButton("刷新列表")
        self.refresh_btn.setObjectName("refreshBtn")
        self.refresh_btn.setFixedHeight(32)
        self.refresh_btn.clicked.connect(self._refresh_anr_list)
        btn_layout.addWidget(self.refresh_btn)

        self.pull_btn = QPushButton("拉取并分析最新 ANR")
        self.pull_btn.setObjectName("pullBtn")
        self.pull_btn.setFixedHeight(32)
        self.pull_btn.clicked.connect(self._pull_and_analyze)
        btn_layout.addWidget(self.pull_btn)

        btn_layout.addStretch()
        layout.addLayout(btn_layout)
        return w

    def _build_local_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        self.local_file_label = QLabel("未选择文件")
        self.local_file_label.setObjectName("statusLabel")
        self.local_file_label.setWordWrap(True)
        layout.addWidget(self.local_file_label)

        btn_layout = QHBoxLayout()
        self.select_file_btn = QPushButton("选择 ANR 文件或 bugreport.zip")
        self.select_file_btn.setObjectName("selectBtn")
        self.select_file_btn.setFixedHeight(32)
        self.select_file_btn.clicked.connect(self._on_select_file)
        btn_layout.addWidget(self.select_file_btn)

        self.analyze_local_btn = QPushButton("分析")
        self.analyze_local_btn.setObjectName("analyzeBtn")
        self.analyze_local_btn.setFixedHeight(32)
        self.analyze_local_btn.setEnabled(False)
        self.analyze_local_btn.clicked.connect(self._analyze_local)
        btn_layout.addWidget(self.analyze_local_btn)

        btn_layout.addStretch()
        layout.addLayout(btn_layout)
        layout.addStretch()
        return w

    # ------------------------------------------------------------------
    def _get_serial(self):
        return self.device_service.serial if self.device_service else None

    # ---------- 从设备 ----------
    def _refresh_anr_list(self):
        serial = self._get_serial()
        if not serial:
            self.device_status_signal.emit("⚠️ 设备未连接")
            self.device_list_signal.emit("")
            return

        def worker():
            adb = get_adb_path()
            try:
                # 检查设备状态
                state = subprocess.run(
                    [adb, "-s", serial, "get-state"],
                    capture_output=True, text=True, timeout=3,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
                )
                if (state.stdout or "").strip() != "device":
                    self.device_status_signal.emit("设备未连接或未授权")
                    self.device_list_signal.emit("")
                    return
                self.device_status_signal.emit("设备已连接")

                # 列 /data/anr/
                ls = subprocess.run(
                    [adb, "-s", serial, "shell", "ls", "-l", "/data/anr/"],
                    capture_output=True, text=True, timeout=5,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
                    encoding='utf-8', errors='replace',
                )
                if ls.returncode != 0:
                    self.device_list_signal.emit(
                        "无法访问 /data/anr/，可能需要 root 权限\n"
                        f"stderr: {ls.stderr}"
                    )
                else:
                    out = (ls.stdout or "").strip()
                    self.device_list_signal.emit(out if out else "未找到 ANR 文件")
            except Exception as e:
                self.device_status_signal.emit(f"获取失败: {e}")
                self.device_list_signal.emit("")

        threading.Thread(target=worker, daemon=True).start()

    def _pull_and_analyze(self):
        serial = self._get_serial()
        if not serial:
            WarningDialog.show_warning(self, "提示", "设备未连接")
            return

        def worker():
            adb = get_adb_path()
            try:
                # 取最新文件
                ls = subprocess.run(
                    [adb, "-s", serial, "shell", "ls", "-t", "/data/anr/"],
                    capture_output=True, text=True, timeout=5,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
                    encoding='utf-8', errors='replace',
                )
                if ls.returncode != 0:
                    self.result_signal.emit("无法访问 /data/anr/，可能需要 root 权限")
                    return
                files = (ls.stdout or "").splitlines()
                if not files:
                    self.result_signal.emit("未找到 ANR 文件")
                    return
                latest = files[0].strip()
                remote_path = f"/data/anr/{latest}"
                local_path = tempfile.mktemp(suffix=".txt")

                pull = subprocess.run(
                    [adb, "-s", serial, "pull", remote_path, local_path],
                    capture_output=True, text=True, timeout=60,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
                )
                if pull.returncode != 0:
                    self.result_signal.emit(f"拉取失败: {pull.stderr}")
                    return

                content = self._read_file(local_path)
                try:
                    os.remove(local_path)
                except Exception:
                    pass

                parsed = self._parse_anr(content)
                self.result_signal.emit(f"[文件: {latest}]\n\n{parsed}")
            except subprocess.CalledProcessError as e:
                self.result_signal.emit(
                    f"拉取失败：权限不足，请尝试 adb root 后重试\n{e}"
                )
            except Exception as e:
                self.result_signal.emit(f"拉取或分析失败: {e}")

        threading.Thread(target=worker, daemon=True).start()

    # ---------- 从本地 ----------
    def _on_select_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 ANR 文件或 bugreport.zip", "",
            "ANR 文件 (*.txt *.trace);;Bugreport (*.zip);;所有文件 (*.*)"
        )
        if path:
            self._local_file = path
            self.local_file_label.setText(f"已选择: {path}")
            self.analyze_local_btn.setEnabled(True)

    def _analyze_local(self):
        if not self._local_file:
            return

        def worker():
            try:
                if self._local_file.endswith(".zip"):
                    content = self._extract_from_zip(self._local_file)
                else:
                    content = self._read_file(self._local_file)

                if not content:
                    self.result_signal.emit("无法解析文件内容")
                    return
                parsed = self._parse_anr(content)
                self.result_signal.emit(parsed)
            except Exception as e:
                self.result_signal.emit(f"分析失败: {e}")

        threading.Thread(target=worker, daemon=True).start()

    # ---------- 工具 ----------
    def _on_status(self, text: str):
        self.device_status_label.setText(f"设备连接状态: {text}")

    def _on_device_list(self, text: str):
        self.device_list_text.setPlainText(text)

    def _on_result(self, text: str):
        self.result_text.setPlainText(text)
        has_valid = bool(text.strip()) and "未检测到 ANR" not in text \
                     and not text.startswith("拉取失败") \
                     and not text.startswith("分析失败")
        self.save_btn.setEnabled(has_valid)

    def _read_file(self, path: str) -> str:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except Exception:
            return ""

    def _extract_from_zip(self, zip_path: str) -> str:
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                anr_files = [
                    f for f in zf.namelist()
                    if "anr" in f.lower() and f.endswith((".txt", ".trace"))
                ]
                if not anr_files:
                    return "未在压缩包中找到 ANR 文件"
                with zf.open(anr_files[0]) as f:
                    return f.read().decode("utf-8", errors="replace")
        except Exception as e:
            return f"解压失败: {e}"

    def _parse_anr(self, content: str) -> str:
        result = []

        subject = re.search(r'Subject:\s*(.*)', content)
        if subject:
            result.append(f"【ANR 主题】\n{subject.group(1)}\n")

        pid_match = re.search(r'PID:\s*(\d+)', content)
        if pid_match:
            result.append(f"PID: {pid_match.group(1)}")

        proc_match = re.search(r'Process:\s*(.*)', content)
        if proc_match:
            result.append(f"进程: {proc_match.group(1)}")

        cpu_section = re.search(
            r'CPU usage from.*?\n(.*?)(?=\n\n|\Z)', content, re.DOTALL
        )
        if cpu_section:
            result.append("\n【CPU 使用情况】")
            result.append(cpu_section.group(1).strip())

        main_thread = re.search(
            r'"main" prio=.*?\n(.*?)(?=\n\n|\Z)', content, re.DOTALL
        )
        if main_thread:
            result.append("\n【主线程堆栈】")
            result.append(main_thread.group(1).strip())
        else:
            stack_start = content.find("DALVIK THREADS")
            if stack_start != -1:
                sub = content[stack_start:]
                m = re.search(
                    r'"main" .*?\n(.*?)(?=\n\n|\Z)', sub, re.DOTALL
                )
                if m:
                    result.append("\n【主线程堆栈】")
                    result.append(m.group(1).strip())

        if len(result) <= 2:
            result = [
                "【ANR 分析结果】",
                "未检测到 ANR 相关信息，可能当前文件不包含 ANR 记录。",
            ]
        return "\n".join(result)

    def _on_save(self):
        text = self.result_text.toPlainText()
        if not text.strip():
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "保存报告", "anr_report.txt", "文本文件 (*.txt)"
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            show_toast(self, f"已保存至 {path}", duration=2500)
        except Exception as e:
            ErrorDialog.show_error(self, "保存失败", str(e))

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
            text_bg = "#1e1e1e"
            text_border = "#3a3a3a"
            title_color = "#ffffff"
            status_color = "#aaaaaa"
            section_color = "#cccccc"
            tab_bg = "#2d2d2d"
            tab_pane = "#232323"
            tab_sel = "#1976d2"
            refresh_bg = "#3498db"
            refresh_hover = "#5dade2"
            pull_bg = "#27ae60"
            pull_hover = "#2ecc71"
            select_bg = "#3498db"
            select_hover = "#5dade2"
            analyze_bg = "#27ae60"
            analyze_hover = "#2ecc71"
            save_bg = "#3498db"
            save_hover = "#5dade2"
            close_bg = "#555"
            close_fg = "#eeeeee"
            close_hover = "#666"
            disabled_bg = "#444"
            disabled_fg = "#777"
        else:
            bg = "#ffffff"
            text = "#333333"
            border = "#d0d0d0"
            text_bg = "#fafbfc"
            text_border = "#e0e0e0"
            title_color = "#1a1a1a"
            status_color = "#7f8c8d"
            section_color = "#555555"
            tab_bg = "#ffffff"
            tab_pane = "#ffffff"
            tab_sel = "#1976d2"
            refresh_bg = "#3498db"
            refresh_hover = "#5dade2"
            pull_bg = "#27ae60"
            pull_hover = "#2ecc71"
            select_bg = "#3498db"
            select_hover = "#5dade2"
            analyze_bg = "#27ae60"
            analyze_hover = "#2ecc71"
            save_bg = "#3498db"
            save_hover = "#5dade2"
            close_bg = "#f0f0f0"
            close_fg = "#333333"
            close_hover = "#e0e0e0"
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
            QDialog QLabel#statusLabel {{
                color: {status_color};
                font-size: 12px;
                padding: 2px 0;
            }}
            QDialog QLabel#sectionLabel {{
                color: {section_color};
                font-size: 13px;
                font-weight: bold;
                padding: 4px 0;
            }}
            QDialog QTabWidget#mainTabs::pane {{
                background-color: {tab_pane};
                border: 1px solid {border};
                border-radius: 6px;
                top: -1px;
            }}
            QDialog QTabWidget#mainTabs QTabBar::tab {{
                background-color: {tab_bg};
                color: {text};
                border: 1px solid {border};
                padding: 6px 14px;
                font-size: 13px;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
            }}
            QDialog QTabWidget#mainTabs QTabBar::tab:selected {{
                background-color: {tab_sel};
                color: white;
                border-color: {tab_sel};
            }}
            QDialog QTextEdit#listText,
            QDialog QTextEdit#resultText {{
                background-color: {text_bg};
                color: {text};
                border: 1px solid {text_border};
                border-radius: 6px;
                padding: 8px;
                font-family: Consolas, monospace;
                font-size: 12px;
            }}
            QDialog QPushButton#refreshBtn,
            QDialog QPushButton#selectBtn {{
                background-color: {refresh_bg};
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: 500;
                min-width: 130px;
            }}
            QDialog QPushButton#refreshBtn:hover,
            QDialog QPushButton#selectBtn:hover {{
                background-color: {refresh_hover};
            }}
            QDialog QPushButton#pullBtn,
            QDialog QPushButton#analyzeBtn {{
                background-color: {pull_bg};
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: 500;
                min-width: 130px;
            }}
            QDialog QPushButton#pullBtn:hover,
            QDialog QPushButton#analyzeBtn:hover {{
                background-color: {pull_hover};
            }}
            QDialog QPushButton#analyzeBtn:disabled {{
                background-color: {disabled_bg};
                color: {disabled_fg};
            }}
            QDialog QPushButton#saveBtn {{
                background-color: {save_bg};
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: 500;
            }}
            QDialog QPushButton#saveBtn:hover {{
                background-color: {save_hover};
            }}
            QDialog QPushButton#saveBtn:disabled {{
                background-color: {disabled_bg};
                color: {disabled_fg};
            }}
            QDialog QPushButton#closeBtn {{
                background-color: {close_bg};
                color: {close_fg};
                border: 1px solid {border};
                border-radius: 6px;
                font-weight: 500;
            }}
            QDialog QPushButton#closeBtn:hover {{
                background-color: {close_hover};
            }}
        """)