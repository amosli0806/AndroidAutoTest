# controllers/adb_toolbox_controller.py
"""ADB 工具箱控制器"""
import os
import sys
import subprocess
import threading
import hashlib
from datetime import datetime

from PyQt6.QtWidgets import QFileDialog, QMessageBox, QInputDialog, QApplication
from PyQt6.QtCore import QObject, pyqtSignal

from services.command_manager import CommandManager
from services.adb_command_pool import AdbCommandPool
from utils.adb_path import get_adb_path
from utils.settings import Settings
from utils.toast import show_toast
from utils.dialogs import WarningDialog, ErrorDialog

# 弹窗
from views.adb_dialogs.wireless_dialog import WirelessDialog
from views.adb_dialogs.screen_mirror_dialog import ScreenMirrorDialog
from views.adb_dialogs.app_manager_dialog import AppManagerDialog
from views.adb_dialogs.device_info_dialog import DeviceInfoDialog
from views.adb_dialogs.process_selector_dialog import ProcessSelectorDialog
from views.adb_dialogs.monkey_dialog import MonkeyDialog
from views.adb_dialogs.crash_log_dialog import CrashLogDialog
from views.adb_dialogs.anr_analyzer_dialog import AnrAnalyzerDialog
from views.adb_dialogs.weak_network_dialog import WeakNetworkDialog
from views.adb_dialogs.packet_capture_dialog import PacketCaptureDialog
from views.adb_dialogs.memory_monitor_dialog import MemoryMonitorDialog
from views.adb_dialogs.push_progress_dialog import PushProgressDialog
from views.adb_dialogs.search_results_dialog import SearchResultsDialog


class AdbToolboxController(QObject):
    """ADB 工具箱控制器"""

    # 弱网状态变化信号（用于主窗口显示横幅）
    weak_network_changed = pyqtSignal(bool)

    def __init__(self, view, device_service, parent=None):
        super().__init__(parent)
        self.view = view
        self.device_service = device_service
        self.command_manager = CommandManager()
        self.pool = AdbCommandPool(self)

        # 初始化视图
        self.view.set_command_manager(self.command_manager)
        self.view.set_output_dir(Settings.get_output_dir())
        self.view.refresh_commands()

        self._connect_signals()

    # ------------------------------------------------------------------
    def _connect_signals(self):
        # View → Controller
        self.view.refresh_requested.connect(self._on_refresh)
        self.view.execute_selected_requested.connect(self._on_execute_selected)
        self.view.execute_command_requested.connect(self._on_execute_single)
        self.view.stop_command_requested.connect(self._on_stop)
        self.view.search_requested.connect(self._on_search)
        self.view.browse_output_dir_requested.connect(self._on_browse_output_dir)
        self.view.clear_log_requested.connect(self.view.clear_log)
        self.view.quick_action_requested.connect(self._on_quick_action)

        # Pool → View
        self.pool.command_output.connect(self._on_pool_output)
        self.pool.command_started.connect(self._on_pool_started)
        self.pool.command_finished.connect(self._on_pool_finished)
        self.pool.command_stopped.connect(self._on_pool_stopped)

    # ------------------------------------------------------------------
    # 主项目状态通知（由主窗口调用）
    # ------------------------------------------------------------------
    def set_main_running(self, running: bool):
        """主项目开始/结束执行用例时通知命令池"""
        self.pool.set_main_running(running)

    # ------------------------------------------------------------------
    def _get_serial(self):
        return self.device_service.serial if self.device_service else None

    # ------------------------------------------------------------------
    # 命令相关
    # ------------------------------------------------------------------
    def _on_refresh(self):
        self.command_manager.load()
        self.view.refresh_commands()
        self.view.append_log("<span style='color:#1565c0;'>🔄 命令列表已刷新</span>")

    def _on_execute_selected(self, commands):
        serial = self._get_serial()
        if not serial:
            WarningDialog.show_warning(self.view, "提示", "未检测到设备，请先连接")
            return

        running_ids = self.view.get_running_ids()
        running_selected = [c for c in commands if c.id in running_ids]
        if running_selected:
            names = "、".join(c.name for c in running_selected)
            WarningDialog.show_warning(
                self.view, "提示",
                f"以下命令正在运行中，请先停止：\n{names}"
            )
            return

        output_dir = self.view.path_edit.text()
        for cmd in commands:
            self.view.set_command_state(cmd.id, 'starting')
            self.pool.execute_command(cmd, serial, output_dir, tag='user')

    def _on_execute_single(self, command):
        serial = self._get_serial()
        if not serial:
            WarningDialog.show_warning(self.view, "提示", "未检测到设备")
            self.view.set_command_state(command.id, 'idle')
            return
        output_dir = self.view.path_edit.text()
        self.view.set_command_state(command.id, 'starting')
        self.pool.execute_command(command, serial, output_dir, tag='user')

    def _on_stop(self, command):
        self.pool.stop_command(command.id)

    # ------------------------------------------------------------------
    # Pool 回调
    # ------------------------------------------------------------------
    def _on_pool_output(self, text, tag):
        self.view.append_log(text)

    def _on_pool_started(self, cmd_id, cmd_name, tag):
        self.view.set_command_state(cmd_id, 'running')

    def _on_pool_finished(self, cmd_id, cmd_name, success, tag):
        self.view.set_command_state(cmd_id, 'idle')
        if success:
            self.view.append_log(
                f"<span style='color:#2e7d32;'>✅ 命令 {cmd_name} 执行成功</span>"
            )
        else:
            self.view.append_log(
                f"<span style='color:#c62828;'>❌ 命令 {cmd_name} 执行失败</span>"
            )

    def _on_pool_stopped(self, cmd_id, cmd_name, tag):
        self.view.set_command_state(cmd_id, 'idle')

    # ------------------------------------------------------------------
    # 搜索
    # ------------------------------------------------------------------
    def _on_search(self, keyword):
        results = self.command_manager.search_all(keyword)
        total = (len(results.get("preset", [])) +
                 len(results.get("custom", [])) +
                 len(results.get("adb_library", [])))
        if total == 0:
            WarningDialog.show_warning(
                self.view, "搜索", f"未找到与「{keyword}」相关的命令"
            )
            return
        dlg = SearchResultsDialog(results, self.view)
        dlg.set_keyword(keyword)
        dlg.execute_command.connect(self._on_execute_single)
        dlg.copy_command.connect(self._on_copy_command)
        dlg.exec()

    def _on_copy_command(self, text):
        QApplication.clipboard().setText(text)
        self.view.append_log("<span style='color:#1565c0;'>📋 命令已复制到剪贴板</span>")

    # ------------------------------------------------------------------
    # 输出目录
    # ------------------------------------------------------------------
    def _on_browse_output_dir(self):
        dir_path = QFileDialog.getExistingDirectory(
            self.view, "选择输出目录", self.view.path_edit.text()
        )
        if dir_path:
            self.view.set_output_dir(dir_path)
            Settings.set_output_dir(dir_path)

    # ------------------------------------------------------------------
    # 快捷动作分发
    # ------------------------------------------------------------------
    def _on_quick_action(self, key):
        # 不需要设备的动作
        if key == "md5":
            self._action_md5()
            return

        # 其他动作都需要设备
        if not self._get_serial():
            WarningDialog.show_warning(self.view, "提示", "未检测到设备，请先连接")
            return

        handlers = {
            "wireless": self._action_wireless,
            "scrcpy": self._action_scrcpy,
            "install": self._action_install,
            "push": self._action_push,
            "app_manager": self._action_app_manager,
            "device_info": self._action_device_info,
            "hprof": self._action_hprof,
            "monkey": self._action_monkey,
            "crash": self._action_crash,
            "anr": self._action_anr,
            "weak_network": self._action_weak_network,
            "packet": self._action_packet,
            "memory": self._action_memory,
        }
        handler = handlers.get(key)
        if handler:
            handler()

    # ------------------------------------------------------------------
    # 各快捷动作实现
    # ------------------------------------------------------------------
    def _action_wireless(self):
        dlg = WirelessDialog(self.device_service, self.view)
        dlg.exec()

    def _action_scrcpy(self):
        output_dir = self.view.path_edit.text()
        # 非模态窗口，不阻塞主界面
        dlg = ScreenMirrorDialog(self.device_service, output_dir, self.view)
        dlg.show()

    def _action_install(self):
        path, _ = QFileDialog.getOpenFileName(
            self.view, "选择 APK 文件", "", "APK 文件 (*.apk);;所有文件 (*.*)"
        )
        if not path:
            return

        filename = os.path.basename(path)
        serial = self._get_serial()
        adb = get_adb_path()
        self.view.append_log(
            f"<span style='color:#1565c0;'>📦 开始安装: {filename}</span>"
        )

        def worker():
            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            try:
                result = subprocess.run(
                    [adb, "-s", serial, "install", "-r", "-t", "-d", "-g", path],
                    capture_output=True, text=True, timeout=120,
                    creationflags=creationflags,
                    encoding='utf-8', errors='replace',
                )
                output = (result.stdout or "") + (result.stderr or "")
                if result.returncode == 0:
                    self.view.append_log(
                        f"<span style='color:#2e7d32;'>✅ 安装成功: {filename}</span>"
                    )
                else:
                    friendly = self._parse_install_error(output)
                    self.view.append_log(
                        f"<span style='color:#c62828;'>❌ 安装失败: {filename}</span>"
                    )
                    if friendly:
                        self.view.append_log(
                            f"<span style='color:#c62828;'>⚠️ {friendly}</span>"
                        )
            except subprocess.TimeoutExpired:
                self.view.append_log(
                    "<span style='color:#c62828;'>❌ 安装超时（120 秒）</span>"
                )
            except Exception as e:
                self.view.append_log(
                    f"<span style='color:#c62828;'>❌ 安装异常: {e}</span>"
                )

        threading.Thread(target=worker, daemon=True).start()

    def _parse_install_error(self, output: str) -> str:
        errors = {
            "INSTALL_FAILED_UPDATE_INCOMPATIBLE": "签名不一致，请先卸载旧版本",
            "INSTALL_FAILED_ALREADY_EXISTS": "应用已存在，且无法覆盖安装",
            "INSTALL_FAILED_INVALID_APK": "APK 文件无效或已损坏",
            "INSTALL_FAILED_INSUFFICIENT_STORAGE": "设备存储空间不足",
            "INSTALL_FAILED_DUPLICATE_PERMISSION": "权限声明重复",
            "INSTALL_FAILED_NO_MATCHING_ABIS": "APK 架构与设备不兼容",
            "INSTALL_FAILED_MISSING_SHARED_LIBRARY": "缺少依赖共享库",
            "INSTALL_FAILED_OLDER_SDK": "设备 Android 版本过低",
            "INSTALL_FAILED_VERIFICATION_FAILURE": "APK 验证失败",
        }
        for k, v in errors.items():
            if k in output:
                return v
        return ""

    def _action_push(self):
        # 选文件 / 文件夹
        msg = QMessageBox(self.view)
        msg.setWindowTitle("推送")
        msg.setText("请选择要 push 的内容类型")
        file_btn = msg.addButton("文件", QMessageBox.ButtonRole.ActionRole)
        folder_btn = msg.addButton("文件夹", QMessageBox.ButtonRole.ActionRole)
        msg.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        msg.exec()

        clicked = msg.clickedButton()
        if clicked == file_btn:
            path, _ = QFileDialog.getOpenFileName(self.view, "选择文件")
        elif clicked == folder_btn:
            path = QFileDialog.getExistingDirectory(self.view, "选择文件夹")
        else:
            return
        if not path:
            return

        # 输入远程路径
        remote, ok = QInputDialog.getText(
            self.view, "远程路径",
            "请输入远程路径（默认 /sdcard/）：",
            text="/sdcard/"
        )
        if not ok or not remote.strip():
            return
        remote = remote.strip()
        if os.path.isdir(path) and not remote.endswith('/'):
            remote += '/'

        dlg = PushProgressDialog(self.device_service, path, remote, self.view)
        dlg.log_message.connect(self.view.append_log)
        dlg.exec()

    def _action_app_manager(self):
        dlg = AppManagerDialog(self.device_service, self.view)
        dlg.exec()

    def _action_device_info(self):
        dlg = DeviceInfoDialog(self.device_service, self.view)
        dlg.exec()

    def _action_hprof(self):
        dlg = ProcessSelectorDialog(self.device_service, self.view)
        if dlg.exec() == dlg.DialogCode.Accepted:
            pid = dlg.get_selected_pid()
            if pid:
                self._dump_hprof(pid)

    def _dump_hprof(self, pid):
        serial = self._get_serial()
        adb = get_adb_path()
        output_dir = self.view.path_edit.text()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_serial = serial.replace(':', '_')
        device_dir = os.path.join(output_dir, safe_serial)
        os.makedirs(device_dir, exist_ok=True)
        remote_path = f"/data/local/tmp/heap_{timestamp}.hprof"
        local_path = os.path.join(device_dir, f"heap_{timestamp}.hprof")

        self.view.append_log(
            f"<span style='color:#1565c0;'>🔍 开始堆转储 PID: {pid}</span>"
        )

        def worker():
            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            try:
                subprocess.run(
                    [adb, "-s", serial, "shell", "am", "dumpheap",
                     str(pid), remote_path],
                    check=True, timeout=30, creationflags=creationflags,
                )
                subprocess.run(
                    [adb, "-s", serial, "pull", remote_path, local_path],
                    check=True, timeout=60, creationflags=creationflags,
                )
                subprocess.run(
                    [adb, "-s", serial, "shell", "rm", remote_path],
                    timeout=5, creationflags=creationflags,
                )
                self.view.append_log(
                    f"<span style='color:#2e7d32;'>✅ 堆转储完成: {local_path}</span>"
                )
            except Exception as e:
                self.view.append_log(
                    f"<span style='color:#c62828;'>❌ 堆转储失败: {e}</span>"
                )

        threading.Thread(target=worker, daemon=True).start()

    def _action_monkey(self):
        dlg = MonkeyDialog(self.device_service, self.view)
        dlg.exec()

    def _action_crash(self):
        dlg = CrashLogDialog(self.device_service, self.view)
        dlg.exec()

    def _action_anr(self):
        dlg = AnrAnalyzerDialog(self.device_service, self.view)
        dlg.exec()

    def _action_md5(self):
        path, _ = QFileDialog.getOpenFileName(
            self.view, "选择 APK 文件", "",
            "APK 文件 (*.apk);;所有文件 (*.*)"
        )
        if not path:
            return
        try:
            md5 = hashlib.md5()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    md5.update(chunk)
            value = md5.hexdigest()
            filename = os.path.basename(path)

            msg = QMessageBox(self.view)
            msg.setWindowTitle("MD5 结果")
            msg.setText(f"文件：{filename}\n\nMD5：\n{value}")
            copy_btn = msg.addButton("复制到剪贴板", QMessageBox.ButtonRole.ActionRole)
            msg.addButton("关闭", QMessageBox.ButtonRole.RejectRole)
            msg.exec()
            if msg.clickedButton() == copy_btn:
                QApplication.clipboard().setText(value)
                self.view.append_log(
                    f"<span style='color:#1565c0;'>📋 MD5 已复制: {value}</span>"
                )
        except Exception as e:
            ErrorDialog.show_error(self.view, "计算失败", str(e))

    def _action_weak_network(self):
        dlg = WeakNetworkDialog(self.device_service, self.view)
        dlg.weak_network_changed.connect(self.weak_network_changed.emit)
        dlg.exec()

    def _action_packet(self):
        output_dir = self.view.path_edit.text()
        dlg = PacketCaptureDialog(self.device_service, output_dir, self.view)
        dlg.exec()

    def _action_memory(self):
        output_dir = self.view.path_edit.text()
        dlg = MemoryMonitorDialog(self.device_service, output_dir, self.view)
        dlg.exec()

    # ------------------------------------------------------------------
    def apply_theme(self, theme_mode, has_wallpaper=False):
        self.view.apply_theme(theme_mode, has_wallpaper)