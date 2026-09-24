# controllers/adb_toolbox_controller.py
"""ADB 工具箱控制器"""
import os
import re
import sys
import subprocess
import threading
import hashlib
import tempfile
from datetime import datetime

from PyQt6.QtWidgets import QFileDialog, QMessageBox, QInputDialog, QApplication
from PyQt6.QtCore import QObject, pyqtSignal

from services.command_manager import CommandManager
from services.adb_command_pool import AdbCommandPool
from services.scrcpy_service import ScrcpyError, ScrcpyService
from utils.adb_path import get_adb_path
from utils.settings import Settings
from utils.toast import show_toast
from utils.dialogs import WarningDialog, ErrorDialog, ConfirmDeleteDialog

# 弹窗
from views.adb_dialogs.wireless_dialog import WirelessDialog
from views.adb_dialogs.process_selector_dialog import ProcessSelectorDialog
from utils import log_colors
from services.anr_parser import parse_anr
from views.adb_dialogs.packet_capture_dialog import PacketCaptureDialog
from views.adb_dialogs.memory_monitor_dialog import MemoryMonitorDialog
from views.adb_dialogs.push_progress_dialog import PushProgressDialog
from views.adb_dialogs.search_results_dialog import SearchResultsDialog


class AdbToolboxController(QObject):
    """ADB 工具箱控制器"""

    # 日志输出信号（转发到主窗口底部日志面板）
    log_emitted = pyqtSignal(str)
    # Crash / ANR / 硬件信息 内容就绪信号（转发到主窗口底部日志区，内联展示）
    crash_log_ready = pyqtSignal(str)
    anr_log_ready = pyqtSignal(str)
    device_info_ready = pyqtSignal(str)
    # 后台线程 -> 主线程的 toast（Qt 不允许在非 GUI 线程直接操作控件）
    toast_requested = pyqtSignal(str)

    # 无法读取 /data/anr/ 时的占位提示：拉取 ANR 一般需要 root
    ANR_ROOT_HINT = (
        "⚠️ 拉取 ANR 需要 root 权限：当前设备读不到 /data/anr/\n\n"
        "处理建议：\n"
        "  · 设备已 root：先执行 adb root，再点一次 ANR 按钮重试\n"
        "  · 设备未 root：该目录不可读，可改用 adb bugreport 抓整包报告后再分析\n"
    )

    def __init__(self, view, device_service, parent=None):
        super().__init__(parent)
        self.view = view
        self.device_service = device_service
        self.command_manager = CommandManager()
        self.pool = AdbCommandPool(self)
        self.scrcpy_service = ScrcpyService()

        # 后台线程只发信号，由主线程弹 toast
        self.toast_requested.connect(lambda msg: show_toast(self.view, msg))

        # 初始化视图
        self.view.set_command_manager(self.command_manager)
        self.view.refresh_commands()
        # 内嵌面板（弱网 / Monkey）需要设备服务才能执行
        self.view.set_device_service(device_service)

        self._connect_signals()

    # ------------------------------------------------------------------
    def _connect_signals(self):
        # View → Controller
        self.view.execute_selected_requested.connect(self._on_execute_selected)
        self.view.execute_command_requested.connect(self._on_execute_single)
        self.view.stop_command_requested.connect(self._on_stop)
        self.view.search_requested.connect(self._on_search)
        # 内嵌面板的结果文案 -> 底部"虫师日志"
        self.view.log_message.connect(self._on_view_log)
        self.view.export_commands_requested.connect(self._on_export_commands)
        self.view.import_commands_requested.connect(self._on_import_commands)
        self.view.display_commands_requested.connect(self._on_display_commands)
        self.view.add_command_requested.connect(self._on_add_command)
        self.view.edit_command_requested.connect(self._on_edit_command)
        self.view.delete_commands_requested.connect(self._on_delete_commands)

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

    def _on_view_log(self, text: str):
        """内嵌面板（弱网等）的结果文案 -> 底部"虫师日志"（统一上色）"""
        self.log_emitted.emit(
            f"<span style='color:{log_colors.log_color(log_colors.RESULT)};'>{text}</span>"
        )


    # ------------------------------------------------------------------
    # 命令相关
    # ------------------------------------------------------------------
    def _on_refresh(self):
        self.command_manager.load()
        self.view.refresh_commands()
        self.log_emitted.emit(f"<span style='color:{log_colors.log_color(log_colors.RESULT)};'>🔄 命令列表已刷新</span>")

    def _start_command(self, command, serial, output_dir) -> bool:
        """按「这条命令现在能不能跑」决定要不要启动它。

        被拦下时（主项目正在执行用例 + 高风险命令）：按钮留在「执行」、原因写进日志，
        返回 False —— 绝不能先把按钮切成「启动中」再没人收尾，那正是"卡在启动中"的原因。
        """
        ok, reason = self.pool.can_execute(command)
        if not ok:
            self.view.set_command_state(command.id, 'idle')
            self.log_emitted.emit(
                f"<span style='color:{log_colors.log_color(log_colors.WARNING)};'>⚠️ {reason}</span>")
            return False
        self.view.set_command_state(command.id, 'starting')
        self.pool.execute_command(command, serial, output_dir, tag='user')
        return True

    def _on_execute_selected(self, commands):
        serial = self._get_serial()
        if not serial:
            show_toast(self.view, "未检测到设备，请先连接", duration=2000)
            self.view.reset_execute_selected_state()  # ← 新增：恢复按钮
            return

        running_ids = self.view.get_running_ids()
        running_selected = [c for c in commands if c.id in running_ids]
        if running_selected:
            names = "、".join(c.name for c in running_selected)
            show_toast(self.view, f"命令正在运行中，请先停止",
                       duration=2500)
            self.view.reset_execute_selected_state()  # ← 新增：恢复按钮
            return

        output_dir = Settings.get_output_dir()
        for cmd in commands:
            self._start_command(cmd, serial, output_dir)

    def _on_execute_single(self, command):
        serial = self._get_serial()
        if not serial:
            show_toast(self.view, "未检测到设备", duration=2000)
            self.view.set_command_state(command.id, 'idle')
            return
        self._start_command(command, serial, Settings.get_output_dir())

    def _on_stop(self, command):
        self.pool.stop_command(command.id)

    # ------------------------------------------------------------------
    # Pool 回调
    # ------------------------------------------------------------------
    def _on_pool_output(self, text, tag):
        self.log_emitted.emit(text)

    def _on_pool_started(self, cmd_id, cmd_name, tag):
        self.view.set_command_state(cmd_id, 'running')

    def _on_pool_finished(self, cmd_id, cmd_name, success, tag):
        self.view.set_command_state(cmd_id, 'idle')
        if success:
            self.log_emitted.emit(
                f"<span style='color:{log_colors.log_color(log_colors.SUCCESS)};'>✅ 命令 {cmd_name} 执行成功</span>"
            )
        else:
            self.log_emitted.emit(
                f"<span style='color:{log_colors.log_color(log_colors.ERROR)};'>❌ 命令 {cmd_name} 执行失败</span>"
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
            show_toast(self.view, f"未找到相关命令",
                       duration=2000)
            return
        dlg = SearchResultsDialog(results, self.view)
        dlg.set_keyword(keyword)
        dlg.execute_command.connect(self._on_execute_single)
        dlg.copy_command.connect(self._on_copy_command)
        dlg.exec()

    def _on_copy_command(self, text):
        QApplication.clipboard().setText(text)
        self.log_emitted.emit(f"<span style='color:{log_colors.log_color(log_colors.RESULT)};'>📋 命令已复制到剪贴板</span>")

    def _on_export_commands(self):
        path, _ = QFileDialog.getSaveFileName(
            self.view, "导出指令", "custom_commands.json", "JSON 文件 (*.json)"
        )
        if not path:
            return
        try:
            self.command_manager.export_custom_commands(path)
            show_toast(self.view, "导出成功", duration=1500)
            self.log_emitted.emit(f"<span style='color:{log_colors.log_color(log_colors.SUCCESS)};'>✅ 已导出指令</span>")
        except Exception as e:
            ErrorDialog.show_error(self.view, "导出失败", str(e))

    def _on_import_commands(self):
        path, _ = QFileDialog.getOpenFileName(
            self.view, "导入指令", "", "JSON 文件 (*.json)"
        )
        if not path:
            return
        try:
            success, errors = self.command_manager.import_custom_commands(path, merge=True)
            if success > 0:
                self.view.refresh_commands()
                show_toast(self.view, "导入成功", duration=1500)
                self.log_emitted.emit(
                    f"<span style='color:{log_colors.log_color(log_colors.SUCCESS)};'>✅ 导入成功（{success} 条）</span>"
                )
            if errors:
                self.log_emitted.emit(
                    f"<span style='color:{log_colors.log_color(log_colors.WARNING)};'>⚠️ 部分跳过: {len(errors)} 条</span>"
                )
        except Exception as e:
            ErrorDialog.show_error(self.view, "导入失败", str(e))

    # ------------------------------------------------------------------
    # 指令管理：展示 / 增 / 改 / 删
    # ------------------------------------------------------------------
    def _on_display_commands(self):
        """展示指令：让用户勾选要在列表中显示的命令（保存到配置）"""
        from views.adb_dialogs.select_commands_dialog import SelectCommandsDialog

        dlg = SelectCommandsDialog(self.command_manager, self.view)
        if dlg.exec() == dlg.DialogCode.Accepted:
            selected_ids = dlg.get_selected_ids()
            all_ids = [c.id for c in self.command_manager.get_all_commands()]

            if set(selected_ids) == set(all_ids):
                # 全选 → 存空列表，表示"无过滤"，将来新增的命令自动显示
                Settings.save_display_command_ids([])
                self.view.filter_commands(None)
            else:
                Settings.save_display_command_ids(selected_ids)
                self.view.filter_commands(selected_ids)

            self.log_emitted.emit(
                f"<span style='color:{log_colors.log_color(log_colors.RESULT)};'>👁 已更新显示指令（{len(selected_ids)} 条）</span>"
            )

    def _on_add_command(self):
        """增加指令"""
        from views.adb_dialogs.custom_command_dialog import CustomCommandDialog

        dlg = CustomCommandDialog(self.view)
        if dlg.exec() == dlg.DialogCode.Accepted:
            cmd = dlg.get_command()
            if not cmd.name or not cmd.name.strip():
                show_toast(self.view, "指令名称不能为空", duration=2000)
                return
            if not cmd.command_text or not cmd.command_text.strip():
                show_toast(self.view, "命令内容不能为空", duration=2000)
                return
            self.command_manager.add_command(cmd)
            self.view.refresh_commands()
            self.log_emitted.emit(
                f"<span style='color:{log_colors.log_color(log_colors.SUCCESS)};'>✅ 已新增指令：{cmd.name}</span>"
            )

    def _on_edit_command(self, cmd):
        """编辑指令（从菜单传入选中的 Command）"""
        from views.adb_dialogs.custom_command_dialog import CustomCommandDialog

        if cmd is None:
            return
        if getattr(cmd, 'is_preset', False):
            show_toast(self.view, "预设指令不可编辑", duration=2000)
            return

        dlg = CustomCommandDialog(self.view, command=cmd)
        if dlg.exec() == dlg.DialogCode.Accepted:
            updated = dlg.get_command()
            self.command_manager.update_command(cmd.id, updated)
            self.view.refresh_commands()
            self.log_emitted.emit(
                f"<span style='color:{log_colors.log_color(log_colors.SUCCESS)};'>✅ 已更新指令：{updated.name}</span>"
            )

    def _on_delete_command(self, cmd):
        """删除指令（从菜单传入选中的 Command）"""
        from utils.dialogs import ConfirmDeleteDialog

        if cmd is None:
            return
        if getattr(cmd, 'is_preset', False):
            show_toast(self.view, "预设指令不可删除", duration=2000)
            return

        if not ConfirmDeleteDialog.ask(
            self.view,
            title="确认删除",
            message=f"确定删除指令「{cmd.name}」吗？",
            detail="删除后不可恢复。"
        ):
            return

        self.command_manager.delete_command(cmd.id)
        self.view.refresh_commands()
        self.log_emitted.emit(
            f"<span style='color:{log_colors.log_color(log_colors.WARNING)};'>🗑 已删除指令：{cmd.name}</span>"
        )


    def _on_delete_commands(self, commands):
        """批量删除指令：跳过预设，逐个删除自定义"""
        if not commands:
            return

        presets = [c for c in commands if getattr(c, 'is_preset', False)]
        customs = [c for c in commands if not getattr(c, 'is_preset', False)]

        if not customs:
            show_toast(self.view, "预设指令不可删除", duration=2000)
            return

        # 构建确认消息
        if len(customs) == 1:
            msg = f"确定删除指令「{customs[0].name}」吗？"
        else:
            names = "、".join(c.name for c in customs[:5])
            if len(customs) > 5:
                names += f" 等 {len(customs)} 条"
            msg = f"确定删除以下 {len(customs)} 条指令吗？\n\n{names}"

        detail = "删除后不可恢复。"
        if presets:
            preset_names = "、".join(c.name for c in presets)
            detail += f"\n\n（预设指令 {preset_names} 将被自动跳过）"

        if not ConfirmDeleteDialog.ask(
            self.view, title="确认删除", message=msg, detail=detail
        ):
            return

        for cmd in customs:
            self.command_manager.delete_command(cmd.id)
        self.view.refresh_commands()
        self.log_emitted.emit(
            f"<span style='color:{log_colors.log_color(log_colors.WARNING)};'>🗑 已删除 {len(customs)} 条指令</span>"
        )
    # ------------------------------------------------------------------
    # 输出目录
    # ------------------------------------------------------------------
    def _on_browse_output_dir(self):
        dir_path = QFileDialog.getExistingDirectory(
            self.view, "选择输出目录", Settings.get_output_dir()
        )
        if dir_path:
            Settings.set_output_dir(dir_path)

    # ------------------------------------------------------------------
    # 快捷动作分发
    # ------------------------------------------------------------------
    def _on_quick_action(self, key):
        # 这几个动作自己处理"无设备"的情况，内容都写进主窗口底部日志区，
        # 避免面板卡在"正在拉取…"：md5 不需要设备，Crash / ANR / 硬件信息 自己给提示
        if key in ("md5", "crash", "anr", "device_info"):
            handlers = {
                "md5": self._action_md5,
                "crash": self._action_crash,
                "anr": self._action_anr,
                "device_info": self._action_device_info,
            }
            handlers[key]()
            return

        # 其他动作都需要设备
        if not self._get_serial():
            show_toast(self.view, "未检测到设备，请先连接", duration=2000)
            return

        handlers = {
            "wireless": self._action_wireless,
            "scrcpy": self._action_scrcpy,
            "install": self._action_install,
            "push": self._action_push,
            "device_info": self._action_device_info,
            "hprof": self._action_hprof,
            "crash": self._action_crash,
            "anr": self._action_anr,
            "packet": self._action_packet,
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
        """一键投屏：不再弹对话框，全部用 toast 反馈。

        点击后先提示「投屏中，请稍后...」，随后在后台线程启动 scrcpy：
        成功则 scrcpy 画面窗口直接出现；失败则 toast 出简短原因。
        """
        serial = self._get_serial()
        if not serial:
            show_toast(self.view, "⚠️ 设备未连接，请先连接设备")
            return

        if self.scrcpy_service.is_mirroring():
            show_toast(self.view, "投屏已在进行中")
            return

        show_toast(self.view, "投屏中，请稍后...")

        def run():
            # start() 里有约 0.8 秒的启动确认等待，不能占住 UI 线程；
            # 失败原因通过信号回到主线程再弹 toast
            try:
                self.scrcpy_service.start(serial)
            except ScrcpyError as e:
                self.toast_requested.emit(f"❌ {e}")
            except Exception as e:
                self.toast_requested.emit(f"❌ 投屏失败：{str(e)[:60]}")

        threading.Thread(target=run, daemon=True).start()

    def _action_install(self):
        path, _ = QFileDialog.getOpenFileName(
            self.view, "选择 APK 文件", "", "APK 文件 (*.apk);;所有文件 (*.*)"
        )
        if not path:
            return

        filename = os.path.basename(path)
        serial = self._get_serial()
        adb = get_adb_path()
        self.log_emitted.emit(
            f"<span style='color:{log_colors.log_color(log_colors.RESULT)};'>📦 开始安装: {filename}</span>"
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
                    self.log_emitted.emit(
                        f"<span style='color:{log_colors.log_color(log_colors.SUCCESS)};'>✅ 安装成功: {filename}</span>"
                    )
                else:
                    friendly = self._parse_install_error(output)
                    self.log_emitted.emit(
                        f"<span style='color:{log_colors.log_color(log_colors.ERROR)};'>❌ 安装失败: {filename}</span>"
                    )
                    if friendly:
                        self.log_emitted.emit(
                            f"<span style='color:{log_colors.log_color(log_colors.ERROR)};'>⚠️ {friendly}</span>"
                        )
            except subprocess.TimeoutExpired:
                self.log_emitted.emit(
                    f"<span style='color:{log_colors.log_color(log_colors.ERROR)};'>❌ 安装超时（120 秒）</span>"
                )
            except Exception as e:
                self.log_emitted.emit(
                    f"<span style='color:{log_colors.log_color(log_colors.ERROR)};'>❌ 安装异常: {e}</span>"
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

    def _action_device_info(self):
        """硬件信息：只取分辨率 / 屏幕密度 / 安卓版本，交给底部日志区展示（内联，不弹对话框）"""
        serial = self._get_serial()
        if not serial:
            self.device_info_ready.emit("⚠️ 设备未连接，无法读取硬件信息。")
            return

        def worker():
            adb = get_adb_path()
            no_window = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0

            def run(cmd: str) -> str:
                try:
                    result = subprocess.run(
                        [adb, "-s", serial] + cmd.split(),
                        capture_output=True, text=True, timeout=5,
                        creationflags=no_window,
                        encoding='utf-8', errors='replace',
                    )
                    return result.stdout or ""
                except Exception:
                    return ""

            size = re.search(r'(\d+x\d+)', run("shell wm size"))
            density = re.search(r'(\d+)', run("shell wm density"))
            release = run("shell getprop ro.build.version.release").strip()

            self.device_info_ready.emit("\n".join([
                f"分辨率: {size.group(1) if size else '未知'}",
                f"屏幕密度: {density.group(1) if density else '未知'}",
                f"安卓版本: {release or '未知'}",
            ]))

        threading.Thread(target=worker, daemon=True).start()

    def _action_hprof(self):
        dlg = ProcessSelectorDialog(self.device_service, self.view)
        if dlg.exec() == dlg.DialogCode.Accepted:
            pid = dlg.get_selected_pid()
            if pid:
                self._dump_hprof(pid)

    def _dump_hprof(self, pid):
        serial = self._get_serial()
        adb = get_adb_path()
        output_dir = Settings.get_output_dir()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_serial = serial.replace(':', '_')
        device_dir = os.path.join(output_dir, safe_serial)
        os.makedirs(device_dir, exist_ok=True)
        remote_path = f"/data/local/tmp/heap_{timestamp}.hprof"
        local_path = os.path.join(device_dir, f"heap_{timestamp}.hprof")

        self.log_emitted.emit(
            f"<span style='color:{log_colors.log_color(log_colors.RESULT)};'>🔍 开始堆转储 PID: {pid}</span>"
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
                self.log_emitted.emit(
                    f"<span style='color:{log_colors.log_color(log_colors.SUCCESS)};'>✅ 堆转储完成: {local_path}</span>"
                )
            except Exception as e:
                self.log_emitted.emit(
                    f"<span style='color:{log_colors.log_color(log_colors.ERROR)};'>❌ 堆转储失败: {e}</span>"
                )

        threading.Thread(target=worker, daemon=True).start()

    def _action_crash(self):
        """Crash 日志：拉取后交给主窗口底部日志区展示（内联，不弹对话框）"""
        serial = self._get_serial()
        if not serial:
            self.crash_log_ready.emit("⚠️ 设备未连接，无法读取崩溃日志。")
            return

        def worker():
            adb = get_adb_path()
            try:
                result = subprocess.run(
                    [adb, "-s", serial, "logcat", "-b", "crash", "-d"],
                    capture_output=True, text=True, timeout=30,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
                    encoding='utf-8', errors='replace',
                )
                log = (result.stdout or "").strip()
                self.crash_log_ready.emit(log if log else "没有崩溃日志")
            except Exception as e:
                self.crash_log_ready.emit(f"读取崩溃日志失败: {e}")

        threading.Thread(target=worker, daemon=True).start()

    def _action_anr(self):
        """ANR 日志：拉取 /data/anr/ 下最新一份并解析后交给底部日志区展示"""
        serial = self._get_serial()
        if not serial:
            self.anr_log_ready.emit("⚠️ 设备未连接，无法拉取 ANR 日志。")
            return

        def worker():
            adb = get_adb_path()
            no_window = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            try:
                ls = subprocess.run(
                    [adb, "-s", serial, "shell", "ls", "-t", "/data/anr/"],
                    capture_output=True, text=True, timeout=5,
                    creationflags=no_window,
                    encoding='utf-8', errors='replace',
                )
                if ls.returncode != 0:
                    # 权限不足：给出 root 占位提示，而不是丢一个 traceback
                    self.anr_log_ready.emit(self.ANR_ROOT_HINT)
                    return
                files = (ls.stdout or "").splitlines()
                if not files:
                    self.anr_log_ready.emit("未找到 ANR 文件：/data/anr/ 下暂无记录")
                    return

                latest = files[0].strip()
                fd, local_path = tempfile.mkstemp(suffix=".txt")
                os.close(fd)
                try:
                    pull = subprocess.run(
                        [adb, "-s", serial, "pull", f"/data/anr/{latest}", local_path],
                        capture_output=True, text=True, timeout=60,
                        creationflags=no_window,
                    )
                    if pull.returncode != 0:
                        self.anr_log_ready.emit(self.ANR_ROOT_HINT)
                        return
                    with open(local_path, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                finally:
                    try:
                        os.remove(local_path)
                    except Exception:
                        pass

                self.anr_log_ready.emit(f"[文件: {latest}]\n\n{parse_anr(content)}")
            except Exception as e:
                self.anr_log_ready.emit(f"拉取或分析 ANR 失败: {e}")

        threading.Thread(target=worker, daemon=True).start()

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
                self.log_emitted.emit(
                    f"<span style='color:{log_colors.log_color(log_colors.RESULT)};'>📋 MD5 已复制: {value}</span>"
                )
        except Exception as e:
            ErrorDialog.show_error(self.view, "计算失败", str(e))

    def _action_packet(self):
        output_dir = Settings.get_output_dir()
        dlg = PacketCaptureDialog(self.device_service, output_dir, self.view)
        dlg.exec()

    def _action_memory(self):
        output_dir = Settings.get_output_dir()
        dlg = MemoryMonitorDialog(self.device_service, output_dir, self.view)
        dlg.exec()

    # ------------------------------------------------------------------
    def apply_theme(self, theme_mode, has_wallpaper=False):
        self.view.apply_theme(theme_mode, has_wallpaper)