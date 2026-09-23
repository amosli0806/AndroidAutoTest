# views/adb_dialogs/process_selector_dialog.py
"""进程/应用选择对话框（用于堆转储等功能）"""
import subprocess
import sys
import threading
import re

import qtawesome as qta
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QListWidget,
    QListWidgetItem, QPushButton, QLabel, QLineEdit
)
from PyQt6.QtCore import Qt, pyqtSignal

from utils.adb_path import get_adb_path
from utils.theme import ThemeMode
from utils.settings import Settings, THEME_MODE_DARK


class ProcessSelectorDialog(QDialog):
    """从设备上选择应用（列出所有已安装包名）"""

    apps_loaded = pyqtSignal(list)  # 后台加载完包名 -> 主线程填充

    def __init__(self, device_service, parent=None):
        super().__init__(parent)
        self.device_service = device_service
        self._theme_mode = ThemeMode.LIGHT
        self.all_apps = []
        self._selected_pkg = None

        self.setWindowTitle("选择应用")
        self.resize(520, 560)

        self.setup_ui()
        self.apps_loaded.connect(self._populate_apps)
        self.apply_theme()

        self._load_apps()

    # ------------------------------------------------------------------
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        # 搜索行
        search_layout = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("🔍 搜索包名...")
        self.search_edit.textChanged.connect(self._on_search_changed)
        search_layout.addWidget(self.search_edit, 1)

        self.clear_btn = QPushButton("清空")
        self.clear_btn.setObjectName("clearBtn")
        self.clear_btn.setFixedWidth(60)
        self.clear_btn.clicked.connect(lambda: self.search_edit.clear())
        search_layout.addWidget(self.clear_btn)

        layout.addLayout(search_layout)

        # 计数标签
        self.count_label = QLabel("加载中...")
        self.count_label.setObjectName("countLabel")
        layout.addWidget(self.count_label)

        # 列表
        self.list_widget = QListWidget()
        self.list_widget.setObjectName("appList")
        self.list_widget.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self.list_widget, 1)

        # 按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.ok_btn = QPushButton("确定")
        self.ok_btn.setObjectName("okBtn")
        self.ok_btn.setFixedHeight(34)
        self.ok_btn.setMinimumWidth(140)
        self.ok_btn.setEnabled(False)
        self.ok_btn.clicked.connect(self._on_ok)
        btn_layout.addWidget(self.ok_btn)

        btn_layout.addSpacing(10)

        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setObjectName("cancelBtn")
        self.cancel_btn.setFixedHeight(34)
        self.cancel_btn.setMinimumWidth(140)
        self.cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self.cancel_btn)

        btn_layout.addStretch()
        layout.addLayout(btn_layout)

    # ------------------------------------------------------------------
    def _load_apps(self):
        if not self.device_service or not self.device_service.serial:
            self.count_label.setText("⚠️ 设备未连接")
            return

        def worker():
            adb = get_adb_path()
            serial = self.device_service.serial
            try:
                result = subprocess.run(
                    [adb, "-s", serial, "shell", "pm", "list", "packages"],
                    capture_output=True, text=True, timeout=15,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
                    encoding='utf-8', errors='replace',
                )
                packages = []
                for line in (result.stdout or "").splitlines():
                    if line.startswith("package:"):
                        pkg = line[8:].strip()
                        if pkg:
                            packages.append(pkg)
                packages.sort()
                self.apps_loaded.emit(packages)
            except Exception:
                self.apps_loaded.emit([])

        threading.Thread(target=worker, daemon=True).start()

    def _populate_apps(self, packages: list):
        self.all_apps = packages
        self.list_widget.clear()
        if not packages:
            self.count_label.setText("未找到应用")
            return
        for pkg in packages:
            item = QListWidgetItem(pkg)
            item.setData(Qt.ItemDataRole.UserRole, pkg)
            self.list_widget.addItem(item)
        self.count_label.setText(f"共 {len(packages)} 个应用")
        self._apply_filter(self.search_edit.text())

    def _on_search_changed(self, text):
        self._apply_filter(text)

    def _apply_filter(self, keyword: str):
        kw = keyword.lower().strip()
        visible = 0
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            pkg = item.data(Qt.ItemDataRole.UserRole) or ""
            if kw:
                hidden = kw not in pkg.lower()
            else:
                hidden = False
            item.setHidden(hidden)
            if not hidden:
                visible += 1
        total = self.list_widget.count()
        if kw and visible < total:
            self.count_label.setText(f"共 {total} 个应用（显示 {visible} 个）")
        else:
            self.count_label.setText(f"共 {total} 个应用")

    def _on_selection_changed(self):
        self.ok_btn.setEnabled(bool(self.list_widget.selectedItems()))

    def _on_ok(self):
        selected = self.list_widget.selectedItems()
        if not selected:
            return
        self._selected_pkg = selected[0].data(Qt.ItemDataRole.UserRole)
        self.accept()

    # ------------------------------------------------------------------
    def get_selected_pkg(self) -> str:
        """返回用户选中的包名"""
        return self._selected_pkg

    def get_selected_pid(self) -> str:
        """返回 PID（如果有正在运行的进程），否则返回包名。

        与捕虫师原版行为一致：优先返回 PID，找不到进程时返回包名。
        """
        pkg = self._selected_pkg
        if not pkg:
            return None
        if not self.device_service or not self.device_service.serial:
            return pkg

        adb = get_adb_path()
        serial = self.device_service.serial
        try:
            result = subprocess.run(
                [adb, "-s", serial, "shell", "pgrep", "-f", pkg],
                capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
                encoding='utf-8', errors='replace',
            )
            out = (result.stdout or "").strip()
            if result.returncode == 0 and out:
                pid = out.split('\n')[0].strip()
                if pid.isdigit():
                    return pid
        except Exception:
            pass
        return pkg

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
            list_bg = "#1e1e1e"
            list_sel = "#1e3a5f"
            search_bg = "#3c3c3c"
            search_border = "#555"
            focus_color = "#90caf9"
            title_color = "#ffffff"
            count_color = "#aaaaaa"
            clear_bg = "#555"
            clear_fg = "#eeeeee"
            clear_hover = "#666"
            cancel_bg = "#555"
            cancel_fg = "#eeeeee"
            cancel_hover = "#666"
        else:
            bg = "#ffffff"
            text = "#333333"
            border = "#d0d0d0"
            list_bg = "#fafbfc"
            list_sel = "#e3f2fd"
            search_bg = "#ffffff"
            search_border = "#d0d0d0"
            focus_color = "#1976d2"
            title_color = "#1a1a1a"
            count_color = "#888888"
            clear_bg = "#f0f0f0"
            clear_fg = "#333333"
            clear_hover = "#e0e0e0"
            cancel_bg = "#f0f0f0"
            cancel_fg = "#333333"
            cancel_hover = "#e0e0e0"

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
            QDialog QLabel#countLabel {{
                color: {count_color};
                font-size: 12px;
                padding: 2px 0;
            }}
            QDialog QLineEdit {{
                background-color: {search_bg};
                color: {text};
                border: 1px solid {search_border};
                border-radius: 4px;
                padding: 6px 10px;
                font-size: 13px;
            }}
            QDialog QLineEdit:focus {{
                border-color: {focus_color};
            }}
            QDialog QListWidget#appList {{
                background-color: {list_bg};
                color: {text};
                border: 1px solid {border};
                border-radius: 6px;
                outline: none;
                padding: 4px;
                font-size: 13px;
            }}
            QDialog QListWidget#appList::item {{
                background: transparent;
                padding: 6px 8px;
                border-radius: 4px;
                margin: 1px 2px;
            }}
            QDialog QListWidget#appList::item:hover {{
                background: {list_sel};
            }}
            QDialog QListWidget#appList::item:selected {{
                background: {list_sel};
                color: {text};
            }}
            QDialog QPushButton#clearBtn {{
                background-color: {clear_bg};
                color: {clear_fg};
                border: 1px solid {border};
                border-radius: 4px;
                font-size: 12px;
            }}
            QDialog QPushButton#clearBtn:hover {{
                background-color: {clear_hover};
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
            QDialog QPushButton#okBtn:disabled {{
                background-color: {clear_bg};
                color: {count_color};
            }}
            QDialog QPushButton#cancelBtn {{
                background-color: {cancel_bg};
                color: {cancel_fg};
                border: 1px solid {border};
                border-radius: 6px;
                font-weight: 500;
            }}
            QDialog QPushButton#cancelBtn:hover {{
                background-color: {cancel_hover};
            }}
        """)