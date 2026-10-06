# views/tray_controller.py
"""系统托盘：托盘图标 + 右键菜单 + 气泡通知 + 最小化到托盘。

职责边界：
  - 托盘图标 / 菜单 / 气泡由本控制器管理；
  - 「点 × 最小化到托盘」由 MainWindow.closeEvent 拦截后调用 notify_minimized()；
  - 气泡通知订阅消息总线（NotificationService.message_added），只对
    「设备断开 / 定时任务完成 / 性能采集完成 / 用例执行完成 / ADB 中断」
    这类值得打扰的事件弹系统气泡，其余只进应用内消息中心。

降级：系统托盘不可用时（无托盘环境的精简系统），本控制器退化为空操作——
最小化到托盘功能会失效，此时 MainWindow.closeEvent 需走正常退出（见 main_window）。
"""
from PyQt6.QtCore import QObject
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon

from utils.settings import Settings

# 值得弹托盘气泡的消息来源（与 notification_controller 的 SOURCE_* 对齐）
_NOTIFY_SOURCES = {"device", "task", "perf", "execution", "adb"}


class TrayController(QObject):
    def __init__(self, main_window, notification_service, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.notification_service = notification_service

        self.available = QSystemTrayIcon.isSystemTrayAvailable()
        self.tray = None
        if not self.available:
            return

        icon = QIcon()
        # 复用应用图标；拿不到就空图标（托盘仍可点菜单）
        try:
            from utils.app_paths import resource_path
            p = resource_path("resources/icons/app_icon.png")
            from PyQt6.QtGui import QPixmap
            pix = QPixmap(p)
            if not pix.isNull():
                icon = QIcon(pix)
        except Exception:
            pass

        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip("虫师 · Android 自动化测试工具")

        menu = QMenu()
        show_action = menu.addAction("显示主窗口")
        quit_action = menu.addAction("退出")
        show_action.triggered.connect(self._show_main)
        quit_action.triggered.connect(self._quit)
        self.tray.setContextMenu(menu)
        # 双击托盘图标 = 显示主窗口
        self.tray.activated.connect(self._on_activated)

        self.tray.show()

        # 订阅消息总线：设备断开 / 任务完成 / 性能完成等弹气泡
        if notification_service is not None:
            notification_service.message_added.connect(self._on_message)

    # ---------------- 动作 ----------------
    def _on_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self._show_main()

    def _show_main(self):
        w = self.main_window
        w.showNormal()
        w.raise_()
        w.activateWindow()

    def _quit(self):
        w = self.main_window
        if hasattr(w, "quit_for_real"):
            w.quit_for_real()
        else:
            from PyQt6.QtWidgets import QApplication
            QApplication.instance().quit()

    def notify_minimized(self):
        """主窗口因「最小化到托盘」被隐藏时，弹一条气泡提示去向。"""
        if self.tray is None:
            return
        self.tray.showMessage(
            "虫师仍在后台运行",
            "已最小化到托盘，双击图标可恢复；需要完全退出请右键托盘图标选「退出」。",
            QSystemTrayIcon.MessageIcon.Information,
            3000,
        )

    # ---------------- 气泡通知 ----------------
    def _on_message(self, item):
        cfg = Settings.get_notify_config()
        if not cfg.get("tray_enabled", True):
            return
        if self.tray is None:
            return
        # 只对值得打扰的事件弹气泡
        source = getattr(item, "source", "")
        if source not in _NOTIFY_SOURCES:
            return
        # 级别转图标
        level = getattr(item, "level", "info")
        if level in ("error", "warning"):
            icon = QSystemTrayIcon.MessageIcon.Warning
        else:
            icon = QSystemTrayIcon.MessageIcon.Information
        title = getattr(item, "title", "") or "通知"
        detail = getattr(item, "detail", "") or ""
        self.tray.showMessage(title, detail, icon, 4000)
