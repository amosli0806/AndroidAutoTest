# views/adb_dialogs/app_manager_dialog.py
"""应用管理对话框（PyQt6 + 主题适配）

功能：列出设备上所有应用，支持卸载 / 清除数据 / 强制停止
"""
import subprocess
import sys
import threading

import qtawesome as qta
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QListWidget,
    QListWidgetItem, QPushButton, QLabel, QLineEdit
)
from PyQt6.QtCore import Qt, pyqtSignal

from utils.adb_path import get_adb_path
from utils.theme import ThemeMode
from utils.settings import Settings, THEME_MODE_DARK
from utils.toast import show_toast
from utils.dialogs import ConfirmDeleteDialog, WarningDialog, ErrorDialog


class AppManagerDialog(QDialog):
    """应用管理：列出 / 卸载 / 清数据 / 强停"""

    apps_loaded = pyqtSignal(list)          # 后台加载完包名
    operation_done = pyqtSignal(bool, str)  # 操作完成 (成功, 消息)

    def __init__(self, device_service, parent=None):
        super().__init__(parent)
        self.device_service = device_service
        self._theme_mode = ThemeMode.LIGHT
        self.all_apps = []

        self.setWindowTitle("应用管理")
        self.resize(620, 540)

        self.setup_ui()
        self.apps_loaded.connect(self._populate_apps)
        self.operation_done.connect(self._on_operation_done)
        self.apply_theme()

        self._load_apps()

    # ------------------------------------------------------------------
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("📱 应用管理")
        title.setObjectName("titleLabel")
        layout.addWidget(title)

        # 搜索行
        search_layout = QHBoxLayout()
        search_layout.setSpacing(6)

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

        self.count_label = QLabel("加载中...")
        self.count_label.setObjectName("countLabel")
        layout.addWidget(self.count_label)

        # 列表
        self.list_widget = QListWidget()
        self.list_widget.setObjectName("appList")
        self.list_widget.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self.list_widget, 1)

        # 操作按钮行
        op_layout = QHBoxLayout()
        op_layout.setSpacing(10)

        self.uninstall_btn = QPushButton("卸载")
        self.uninstall_btn.setObjectName("uninstallBtn")
        self.uninstall_btn.setFixedHeight(34)
        self.uninstall_btn.clicked.connect(self._on_uninstall)
        op_layout.addWidget(self.uninstall_btn)

        self.clear_data_btn = QPushButton("清除数据")
        self.clear_data_btn.setObjectName("clearDataBtn")
        self.clear_data_btn.setFixedHeight(34)
        self.clear_data_btn.clicked.connect(self._on_clear_data)
        op_layout.addWidget(self.clear_data_btn)

        self.force_stop_btn = QPushButton("强制停止")
        self.force_stop_btn.setObjectName("forceStopBtn")
        self.force_stop_btn.setFixedHeight(34)
        self.force_stop_btn.clicked.connect(self._on_force_stop)
        op_layout.addWidget(self.force_stop_btn)

        op_layout.addStretch()
        layout.addLayout(op_layout)

        # 关闭按钮
        close_layout = QHBoxLayout()
        close_layout.addStretch()
        self.close_btn = QPushButton("关闭")
        self.close_btn.setObjectName("closeBtn")
        self.close_btn.setFixedHeight(34)
        self.close_btn.setMinimumWidth(140)
        self.close_btn.clicked.connect(self.accept)
        close_layout.addWidget(self.close_btn)
        close_layout.addStretch()
        layout.addLayout(close_layout)

        self._update_buttons_state()

    # ------------------------------------------------------------------
    def _get_serial(self):
        return self.device_service.serial if self.device_service else None

    def _load_apps(self):
        serial = self._get_serial()
        if not serial:
            self.count_label.setText("⚠️ 设备未连接")
            return

        def worker():
            adb = get_adb_path()
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
        self.list_widget.clearSelection()
        self._update_buttons_state()

    def _on_search_changed(self, text):
        self._apply_filter(text)

    def _apply_filter(self, keyword: str):
        kw = keyword.lower().strip()
        visible = 0
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            pkg = item.data(Qt.ItemDataRole.UserRole) or ""
            hidden = bool(kw) and kw not in pkg.lower()
            item.setHidden(hidden)
            if not hidden:
                visible += 1
        total = self.list_widget.count()
        if kw and visible < total:
            self.count_label.setText(f"共 {total} 个应用（显示 {visible} 个）")
        else:
            self.count_label.setText(f"共 {total} 个应用")

    def _on_selection_changed(self):
        self._update_buttons_state()

    def _update_buttons_state(self):
        selected = bool(self.list_widget.selectedItems())
        for btn in (self.uninstall_btn, self.clear_data_btn, self.force_stop_btn):
            btn.setEnabled(selected)

    def _get_selected_pkg(self):
        items = self.list_widget.selectedItems()
        if not items:
            return None
        return items[0].data(Qt.ItemDataRole.UserRole)

    # ------------------------------------------------------------------
    def _on_uninstall(self):
        pkg = self._get_selected_pkg()
        if not pkg:
            return
        if not ConfirmDeleteDialog.ask(
            self,
            title="确认卸载",
            message=f"确定卸载应用 {pkg} 吗？",
            detail="卸载后应用数据将丢失。"
        ):
            return
        self._run_operation("uninstall", pkg)

    def _on_clear_data(self):
        pkg = self._get_selected_pkg()
        if not pkg:
            return
        if not ConfirmDeleteDialog.ask(
            self,
            title="确认清除数据",
            message=f"确定清除 {pkg} 的数据吗？",
            detail="⚠️ 将清空该应用所有用户数据，不可恢复。"
        ):
            return
        self._run_operation("clear", pkg)

    def _on_force_stop(self):
        pkg = self._get_selected_pkg()
        if not pkg:
            return
        if not ConfirmDeleteDialog.ask(
            self,
            title="确认强制停止",
            message=f"确定强制停止 {pkg} 吗？"
        ):
            return
        self._run_operation("stop", pkg)

    def _run_operation(self, op: str, pkg: str):
        serial = self._get_serial()
        if not serial:
            WarningDialog.show_warning(self, "提示", "设备未连接")
            return

        # 禁用三个按钮防止重复点击
        for btn in (self.uninstall_btn, self.clear_data_btn, self.force_stop_btn):
            btn.setEnabled(False)

        def worker():
            adb = get_adb_path()
            if op == "uninstall":
                cmd = [adb, "-s", serial, "uninstall", pkg]
                desc = f"卸载 {pkg}"
            elif op == "clear":
                cmd = [adb, "-s", serial, "shell", "pm", "clear", pkg]
                desc = f"清除数据 {pkg}"
            else:  # stop
                cmd = [adb, "-s", serial, "shell", "am", "force-stop", pkg]
                desc = f"强制停止 {pkg}"

            try:
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=30,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
                    encoding='utf-8', errors='replace',
                )
                if result.returncode == 0:
                    self.operation_done.emit(True, f"{desc} 成功")
                else:
                    err = (result.stderr or result.stdout or "未知错误").strip()
                    self.operation_done.emit(False, f"{desc} 失败：{err[:150]}")
            except Exception as e:
                self.operation_done.emit(False, f"{desc} 异常：{e}")

        threading.Thread(target=worker, daemon=True).start()

    def _on_operation_done(self, success: bool, message: str):
        show_toast(self, message, duration=2500)
        # 卸载后需要重载列表；清数据/强停不需要
        if success and "卸载" in message:
            self._load_apps()
        else:
            self._update_buttons_state()

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
            list_hover = "#2a2a2a"
            search_bg = "#3c3c3c"
            search_border = "#555"
            focus_color = "#90caf9"
            title_color = "#ffffff"
            count_color = "#aaaaaa"
            clear_bg = "#555"
            clear_fg = "#eeeeee"
            clear_hover = "#666"
            uninstall_bg = "#c0392b"
            uninstall_hover = "#e74c3c"
            clear_data_bg = "#d68910"
            clear_data_hover = "#f39c12"
            force_stop_bg = "#1976d2"
            force_stop_hover = "#2196f3"
            close_bg = "#555"
            close_fg = "#eeeeee"
            close_hover = "#666"
            disabled_bg = "#444"
            disabled_fg = "#777"
        else:
            bg = "#ffffff"
            text = "#333333"
            border = "#d0d0d0"
            list_bg = "#fafbfc"
            list_sel = "#e3f2fd"
            list_hover = "#eef1f5"
            search_bg = "#ffffff"
            search_border = "#d0d0d0"
            focus_color = "#1976d2"
            title_color = "#1a1a1a"
            count_color = "#7f8c8d"
            clear_bg = "#f0f0f0"
            clear_fg = "#333333"
            clear_hover = "#e0e0e0"
            uninstall_bg = "#e74c3c"
            uninstall_hover = "#f05a4a"
            clear_data_bg = "#f39c12"
            clear_data_hover = "#f5b041"
            force_stop_bg = "#3498db"
            force_stop_hover = "#5dade2"
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
                background: {list_hover};
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
            QDialog QPushButton#uninstallBtn {{
                background-color: {uninstall_bg};
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: 500;
                min-width: 110px;
            }}
            QDialog QPushButton#uninstallBtn:hover {{
                background-color: {uninstall_hover};
            }}
            QDialog QPushButton#uninstallBtn:disabled {{
                background-color: {disabled_bg};
                color: {disabled_fg};
            }}
            QDialog QPushButton#clearDataBtn {{
                background-color: {clear_data_bg};
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: 500;
                min-width: 110px;
            }}
            QDialog QPushButton#clearDataBtn:hover {{
                background-color: {clear_data_hover};
            }}
            QDialog QPushButton#clearDataBtn:disabled {{
                background-color: {disabled_bg};
                color: {disabled_fg};
            }}
            QDialog QPushButton#forceStopBtn {{
                background-color: {force_stop_bg};
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: 500;
                min-width: 110px;
            }}
            QDialog QPushButton#forceStopBtn:hover {{
                background-color: {force_stop_hover};
            }}
            QDialog QPushButton#forceStopBtn:disabled {{
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