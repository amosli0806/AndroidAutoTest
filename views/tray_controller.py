# views/tray_controller.py
"""系统托盘：托盘图标 + 右键快捷面板 + 气泡通知 + 最小化到托盘。

职责边界：
  - 托盘图标 / 右键面板 / 气泡由本控制器管理；
  - 「点 × 最小化到托盘」由 MainWindow.closeEvent 拦截后调用 notify_minimized()；
  - 气泡通知订阅消息总线（NotificationService.message_added），只对
    「设备断开 / 定时任务完成 / 性能采集完成 / 用例执行完成 / ADB 中断」
    这类值得打扰的事件弹系统气泡，其余只进应用内消息中心。

右键快捷面板（借鉴火绒的托盘面板样式）：
  - 头部：应用 logo + 名称/副标题 + 「进入」按钮（显示主窗口）；
  - 中部：2×2 功能宫格（输出目录 / 迷你模式 / 检查更新 / 设置）；
  - 底部：文字链接（显示主窗口 / 退出虫师）。
  面板为无边框圆角卡片（Qt.Popup），点击面板外自动关闭，随应用明暗主题换肤。

降级：系统托盘不可用时（无托盘环境的精简系统），本控制器退化为空操作——
最小化到托盘功能会失效，此时 MainWindow.closeEvent 需走正常退出（见 main_window）。
面板构建/弹出失败时，回退到原生右键菜单。
"""
from PyQt6.QtCore import QObject, Qt, QTimer, QPoint, QSize
from PyQt6.QtGui import QIcon, QCursor, QPixmap, QColor
from PyQt6.QtWidgets import (
    QMenu, QSystemTrayIcon, QWidget, QFrame, QVBoxLayout, QHBoxLayout,
    QGridLayout, QLabel, QPushButton, QToolButton, QGraphicsDropShadowEffect,
    QApplication,
)

import qtawesome as qta

from utils.settings import Settings, THEME_MODE_DARK

# 值得弹托盘气泡的消息来源（与 notification_controller 的 SOURCE_* 对齐）
_NOTIFY_SOURCES = {"device", "task", "perf", "execution", "adb"}

_BRAND = "#2B7FFF"          # 品牌蓝（与启动封面波浪一致）
_BRAND_HOVER = "#1a6fed"

# 面板配色：False=亮色 / True=暗色
_PANEL_THEMES = {
    False: dict(
        card="#ffffff", border="#e3e6eb", title="#333333",
        sub="#98a2b3", divider="#eef0f3", tile_hover="#f2f6ff",
        tile_text="#4b5563", link="#6b7280", icon=_BRAND,
    ),
    True: dict(
        card="#2a2b2f", border="#3a3a3a", title="#f0f0f0",
        sub="#8a8f99", divider="#3a3a3a", tile_hover="#33363c",
        tile_text="#c8ccd4", link="#9aa0aa", icon="#6ab0ff",
    ),
}


def _icon(name: str, color: str) -> QIcon:
    """qtawesome 图标，名字写错时兜底为空图标，不让面板建不出来。"""
    try:
        return qta.icon(name, color=color)
    except Exception:
        return QIcon()


class _TrayPanel(QWidget):
    """托盘右键快捷面板：无边框圆角卡片，点击面板外自动关闭。"""

    WIDTH = 310  # 含四周阴影留白

    def __init__(self, controller):
        super().__init__(None, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._ctl = controller
        self._tiles = []  # [(QToolButton, 图标名), ...]
        self._build_ui()

    # ---------------- UI ----------------
    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 16)  # 给投影留出空隙
        card = QFrame()
        card.setObjectName("TrayPanelCard")
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(0, 0, 0, 70))
        card.setGraphicsEffect(shadow)
        outer.addWidget(card)
        self._card = card

        root = QVBoxLayout(card)
        root.setContentsMargins(16, 14, 16, 8)
        root.setSpacing(10)

        # ---- 头部：logo + 名称/副标题 + 进入按钮 ----
        head = QHBoxLayout()
        head.setSpacing(10)
        self._logo = QLabel()
        self._logo.setFixedSize(40, 40)
        self._logo.setStyleSheet("background: transparent;")
        head.addWidget(self._logo)

        name_col = QVBoxLayout()
        name_col.setSpacing(1)
        self._name_label = QLabel("虫师")
        self._name_label.setObjectName("TrayPanelName")
        self._sub_label = QLabel(self._subtitle())
        self._sub_label.setObjectName("TrayPanelSub")
        name_col.addWidget(self._name_label)
        name_col.addWidget(self._sub_label)
        head.addLayout(name_col)
        head.addStretch()

        enter_btn = QPushButton("进入")
        enter_btn.setObjectName("TrayPanelEnter")
        enter_btn.setFixedSize(64, 28)
        enter_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        enter_btn.clicked.connect(lambda: self._run(self._ctl._show_main))
        head.addWidget(enter_btn)
        root.addLayout(head)

        root.addWidget(self._divider())

        # ---- 2×2 功能宫格 ----
        grid = QGridLayout()
        grid.setContentsMargins(0, 2, 0, 2)
        grid.setSpacing(6)
        tiles = [
            ("打开输出目录", "fa6s.folder-open", self._ctl._open_output_dir),
            ("迷你模式", "fa6s.compress", self._ctl._toggle_mini),
            ("检查更新", "fa6s.arrows-rotate", self._ctl._check_update),
            ("设置", "fa6s.gear", self._ctl._open_settings),
        ]
        for i, (text, icon_name, fn) in enumerate(tiles):
            btn = QToolButton()
            btn.setObjectName("TrayPanelTile")
            btn.setText(text)
            btn.setFixedSize(124, 66)
            btn.setIconSize(QSize(22, 22))
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, f=fn: self._run(f))
            grid.addWidget(btn, i // 2, i % 2)
            self._tiles.append((btn, icon_name))
        root.addLayout(grid)

        root.addWidget(self._divider())

        # ---- 底部链接行 ----
        foot = QHBoxLayout()
        foot.setContentsMargins(0, 0, 0, 2)
        for text, fn in (("显示主窗口", self._ctl._show_main),
                         ("退出虫师", self._ctl._quit)):
            link = QPushButton(text)
            link.setObjectName("TrayPanelLink")
            link.setFlat(True)
            link.setCursor(Qt.CursorShape.PointingHandCursor)
            link.clicked.connect(lambda _=False, f=fn: self._run(f))
            foot.addWidget(link)
        root.addLayout(foot)

        self.setFixedWidth(self.WIDTH)

    def _divider(self):
        line = QFrame()
        line.setObjectName("TrayPanelDivider")
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFixedHeight(1)
        return line

    @staticmethod
    def _subtitle():
        try:
            from utils.version import APP_VERSION
            return f"Android 自动化测试工具 v{APP_VERSION}"
        except Exception:
            return "Android 自动化测试工具"

    # ---------------- 主题 ----------------
    def apply_theme(self):
        """按应用当前明暗主题刷新配色与图标（每次弹出前调用）。"""
        dark = Settings.get_theme_mode() == THEME_MODE_DARK
        c = _PANEL_THEMES[dark]
        self._card.setStyleSheet(f"""
            #TrayPanelCard {{
                background: {c['card']};
                border: 1px solid {c['border']};
                border-radius: 10px;
            }}
            #TrayPanelName {{
                color: {c['title']}; font-size: 16px; font-weight: 700;
                background: transparent; border: none;
            }}
            #TrayPanelSub {{
                color: {c['sub']}; font-size: 11px;
                background: transparent; border: none;
            }}
            #TrayPanelEnter {{
                background: {_BRAND}; color: #ffffff; font-size: 13px;
                border: none; border-radius: 14px; padding: 0 14px;
            }}
            #TrayPanelEnter:hover {{ background: {_BRAND_HOVER}; }}
            #TrayPanelTile {{
                background: transparent; color: {c['tile_text']};
                font-size: 12px; border: 1px solid transparent;
                border-radius: 8px; padding-top: 4px;
            }}
            #TrayPanelTile:hover {{
                background: {c['tile_hover']};
                border: 1px solid {c['divider']};
            }}
            #TrayPanelDivider {{
                background: {c['divider']}; border: none; max-height: 1px;
            }}
            #TrayPanelLink {{
                background: transparent; color: {c['link']}; font-size: 12px;
                border: none; border-radius: 6px; padding: 4px 12px;
            }}
            #TrayPanelLink:hover {{ color: {_BRAND}; background: {c['tile_hover']}; }}
        """)
        for btn, icon_name in self._tiles:
            btn.setIcon(_icon(icon_name, c['icon']))

    def _set_logo(self):
        pm = QPixmap()
        app = QApplication.instance()
        if app is not None:
            icon = app.windowIcon()
            if not icon.isNull():
                pm = icon.pixmap(40, 40)
        if not pm.isNull():
            self._logo.setPixmap(pm)

    # ---------------- 行为 ----------------
    def _run(self, fn):
        """先收面板再执行动作，避免弹窗/窗口切换时面板还挂在屏幕上。"""
        self.hide()
        QTimer.singleShot(0, fn)


class TrayController(QObject):
    def __init__(self, main_window, notification_service, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.notification_service = notification_service

        self.available = QSystemTrayIcon.isSystemTrayAvailable()
        self.tray = None
        self._panel = None        # 火绒风格快捷面板（懒加载）
        self._fallback = None     # 原生菜单兜底（面板失败时才用）
        if not self.available:
            return

        icon = QIcon()
        # 复用主窗口已设置的应用图标（main.py 启动时 setWindowIcon 过，
        # 开发/打包两套环境的资源定位都在那边处理好了，这里不重复造轮子）
        try:
            from PyQt6.QtWidgets import QApplication
            app = QApplication.instance()
            if app is not None:
                icon = app.windowIcon()
        except Exception:
            pass
        # 兜底：拿不到就按 main.py 同一套逻辑自己加载（打包 _MEIPASS / 开发项目根目录）
        if icon.isNull():
            try:
                import os
                import sys
                base = (sys._MEIPASS if getattr(sys, "frozen", False)
                        else os.path.dirname(os.path.abspath(__file__)))
                base = os.path.dirname(base)  # views/ -> 项目根
                p = os.path.join(base, "resources", "icons", "app_icon.ico")
                if os.path.exists(p):
                    icon = QIcon(p)
            except Exception:
                pass

        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip("虫师  ·  右键打开快捷面板")

        # 不挂原生 contextMenu：右键弹自绘快捷面板（见 _on_activated）
        self.tray.activated.connect(self._on_activated)

        self.tray.show()

        # 订阅消息总线：设备断开 / 任务完成 / 性能完成等弹气泡
        if notification_service is not None:
            notification_service.message_added.connect(self._on_message)

    # ---------------- 激活 ----------------
    def _on_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self._show_main()
        elif reason == QSystemTrayIcon.ActivationReason.Context:
            self._show_panel()

    def _show_panel(self):
        """右键弹出快捷面板（弹到托盘上方）；失败时回退原生菜单。"""
        try:
            if self._panel is None:
                self._panel = _TrayPanel(self)
            self._panel.apply_theme()
            self._panel._set_logo()
            self._panel.adjustSize()

            cursor = QCursor.pos()
            screen = QApplication.screenAt(cursor)
            if screen is None:
                screen = QApplication.primaryScreen()
            geo = screen.availableGeometry()
            # 面板右缘落在托盘图标附近、整体弹到托盘上方，再夹回屏幕范围内
            x = cursor.x() - self._panel.width() + 40
            y = cursor.y() - self._panel.height() - 12
            x = max(geo.left() + 8, min(x, geo.right() - self._panel.width() - 8))
            y = max(geo.top() + 8, min(y, geo.bottom() - self._panel.height() - 8))
            self._panel.move(QPoint(x, y))

            self._panel.show()
            self._panel.raise_()
            self._panel.activateWindow()
        except Exception as e:
            print(f"[TrayController] 快捷面板弹出失败，回退原生菜单: {e}")
            self._fallback_menu().exec(QCursor.pos())

    def _fallback_menu(self):
        if self._fallback is None:
            menu = QMenu()
            show_action = menu.addAction("显示主窗口")
            show_action.triggered.connect(self._show_main)
            menu.addSeparator()
            open_dir_action = menu.addAction("打开输出目录")
            open_dir_action.triggered.connect(self._open_output_dir)
            mini_action = menu.addAction("迷你模式")
            mini_action.triggered.connect(self._toggle_mini)
            update_action = menu.addAction("检查更新")
            update_action.triggered.connect(self._check_update)
            menu.addSeparator()
            quit_action = menu.addAction("退出")
            quit_action.triggered.connect(self._quit)
            self._fallback = menu
        return self._fallback

    # ---------------- 动作 ----------------
    def _show_main(self):
        w = self.main_window
        # 用 show() 而非 showNormal()：showNormal 会强制还原最大化/全屏状态，
        # 全屏时最小化到托盘、再从托盘恢复，应保持原来的全屏/最大化。
        w.show()
        w.raise_()
        w.activateWindow()

    def _quit(self):
        w = self.main_window
        if hasattr(w, "quit_for_real"):
            w.quit_for_real()
        else:
            from PyQt6.QtWidgets import QApplication
            QApplication.instance().quit()

    def _open_output_dir(self):
        """一键打开输出目录（报告/截图/logcat 所在文件夹）。"""
        import os
        from utils.settings import Settings
        p = Settings.get_output_dir()
        if p and os.path.isdir(p):
            os.startfile(p)

    def _toggle_mini(self):
        """切换迷你模式（复用主窗口已有开关）。"""
        act = getattr(self.main_window, "mini_mode_action", None)
        if act is not None:
            act.trigger()

    def _check_update(self):
        """手动触发检查更新（入口由 main.py 挂在主窗口上）。"""
        fn = getattr(self.main_window, "_check_update_manual", None)
        if fn is not None:
            fn()

    def _open_settings(self):
        """打开设置（复用主窗口入口，保存后主题/壁纸刷新逻辑一致）。"""
        fn = getattr(self.main_window, "on_settings", None)
        if fn is not None:
            fn()

    def notify_minimized(self):
        """主窗口因「最小化到托盘」被隐藏时，弹一条气泡提示去向。"""
        if self.tray is None:
            return
        self.tray.showMessage(
            "虫师仍在后台运行",
            "已最小化到托盘，双击图标可恢复；需要完全退出请右键托盘图标选「退出虫师」。",
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
