# views/main_window.py
import json
import os

import qtawesome as qta
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QSplitter, QToolBar, QToolButton,
    QMenu, QComboBox, QLabel, QPushButton, QGroupBox,
    QStatusBar, QSizePolicy, QMessageBox, QFileDialog,
    QDialog, QTextEdit, QApplication, QLineEdit,
    QStackedWidget, QWidgetAction, QGraphicsOpacityEffect, QFrame, QToolTip,
    QDockWidget
)
from PyQt6.QtGui import QAction, QIcon, QPixmap, QPainterPath, QRegion
from PyQt6.QtCore import Qt, pyqtSignal, QThread, QObject, QUrl, QSize, QPoint, QEvent, QRectF, QTimer
from PyQt6.QtWebEngineWidgets import QWebEngineView

from models.project_model import TreeNode
from models.step_model import Step
from utils.settings import Settings, THEME_MODE_SYSTEM, THEME_MODE_LIGHT, THEME_MODE_DARK
from utils.win_dark_title import set_dark_title_bar
from utils.theme import Theme, ThemeMode
from utils.toast import show_toast
from utils.dialogs import WarningDialog, ErrorDialog
from views.help_view import HelpView
from views.dialogs.settings_dialog import SettingsDialog


class WeditorWorker(QObject):
    success = pyqtSignal(int)
    error = pyqtSignal(str)

    def __init__(self, service, port=17310):
        super().__init__()
        self.service = service
        self.port = port

    def run(self):
        try:
            result_port = self.service._start_process_and_wait(self.port)
            if result_port:
                self.success.emit(result_port)
            else:
                self.error.emit("启动 weditor 失败，请检查是否已安装 weditor (pip install weditor)")
        except Exception as e:
            self.error.emit(str(e))

class RoundedWebEngineView(QWebEngineView):
    """带圆角的 QWebEngineView。
    QWebEngineView 本身不支持 QSS 的 border-radius，只能用 setMask 实现。"""

    def __init__(self, radius=8, parent=None):
        super().__init__(parent)
        self._radius = radius

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_rounded_mask()

    def _apply_rounded_mask(self):
        try:
            w = self.width()
            h = self.height()
            if w <= 0 or h <= 0:
                return
            path = QPainterPath()
            path.addRoundedRect(QRectF(0, 0, w, h), self._radius, self._radius)
            poly = path.toFillPolygon().toPolygon()
            self.setMask(QRegion(poly))
        except Exception as e:
            print(f"[RoundedWebEngineView] mask 应用失败: {e}")

class RoundedDockWidget(QDockWidget):
    """带圆角的最外层 DockWidget。
    QDockWidget 不支持 QSS 的 border-radius（标题栏由 Qt 自绘），
    只能用 setMask 实现整体圆角。
    但悬浮时 setMask 会裁掉标题栏与关闭按钮，需 clearMask。"""

    def __init__(self, title, parent=None, radius=8):
        super().__init__(title, parent)
        self._radius = radius
        self.topLevelChanged.connect(self._on_top_level_changed)

    def _on_top_level_changed(self, floating):
        """悬浮 ↔ 停靠切换时，切换是否应用 mask"""
        if floating:
            # 悬浮时清掉 mask，否则关闭按钮和标题栏区域被裁掉
            self.clearMask()
        else:
            self._apply_rounded_mask()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self.isFloating():
            self._apply_rounded_mask()

    def showEvent(self, event):
        super().showEvent(event)
        if not self.isFloating():
            self._apply_rounded_mask()

    def _apply_rounded_mask(self):
        try:
            w = self.width()
            h = self.height()
            if w <= 0 or h <= 0:
                return
            path = QPainterPath()
            path.addRoundedRect(QRectF(0, 0, w, h), self._radius, self._radius)
            poly = path.toFillPolygon().toPolygon()
            self.setMask(QRegion(poly))
        except Exception as e:
            print(f"[RoundedDockWidget] mask 应用失败: {e}")

class MainWindow(QMainWindow):
    refresh_devices_signal = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setObjectName("MainWindow")
        self.setWindowTitle("虫师")
        self.device_service = None
        self.weditor_service = None
        self.visualize_button = None
        self.project_model = None
        self.step_model = None
        self.element_controller = None
        self.project_controller = None
        self.step_controller = None
        self.thread = None
        self.worker = None
        self.record_btn = None
        self.step_generate_btn = None
        self.step_search_input = None
        self.step_list = None
        self.logs_view = None
        self.bottom_placeholder = None
        self.main_splitter = None
        self.stacked_widget = None
        self.left_toolbar = None
        self.right_toolbar = None
        self.log_action = None
        self.ai_action = None
        self.help_action = None
        self.device_status_label = None
        self.nav_actions = []
        self.nav_icons = []
        self.visualize_widget = None
        self._visualize_container = None
        self.visualize_dock = None
        self._perf_view = None
        self._adb_toolbox_controller = None
        self.weak_network_banner = None
        self._element_container = None
        self._logs_view_ref = None
        self._execute_view_ref = None
        self._task_frame_ref = None
        self.main_menu = None
        self._main_menu_actions = {}
        self._project_menu_btn = None
        self._project_menu = None
        self._welcome_tip_rows = []
        self._sub_views = []
        self.left_toolbar_buttons = []
        self.right_toolbar_buttons = []
        self.setup_ui()
        self.setup_wallpaper()
        self.load_wallpaper()
        self.setup_tab_icons()

        self.switch_view(6)
        self.apply_theme()

        # 记录上一次检测到的系统主题（用于轮询比对）
        self._last_system_theme = None
        try:
            self._last_system_theme = Settings._detect_system_theme()
        except Exception:
            pass

        # 1) Qt 6.5+ 原生信号（可能不触发，作为可选的快速路径）
        try:
            self._style_hints = self.styleHints()
            self._style_hints.colorSchemeChanged.connect(self._on_system_color_scheme_changed)
        except (AttributeError, Exception):
            self._style_hints = None

        # 2) 兜底：定时轮询系统主题（每 3 秒），确保"跟随系统"能实时响应
        self._system_theme_timer = QTimer(self)
        self._system_theme_timer.timeout.connect(self._poll_system_theme)
        self._system_theme_timer.start(3000)

    def _poll_system_theme(self):
        """轮询系统主题（仅在'跟随系统'模式下才实际切换）"""
        try:
            raw = Settings.get_raw_theme_mode()
        except Exception:
            return
        if raw != THEME_MODE_SYSTEM:
            return
        try:
            current = Settings._detect_system_theme()
        except Exception:
            return
        if self._last_system_theme is None:
            self._last_system_theme = current
            return
        if current != self._last_system_theme:
            print(f"[系统主题变化] {self._last_system_theme} → {current}，重新应用主题")
            self._last_system_theme = current
            self.apply_theme()

    def _on_system_color_scheme_changed(self, *args):
        """系统颜色方案变化时，若用户选择'跟随系统'则重新应用主题"""
        try:
            raw = Settings.get_raw_theme_mode()
        except Exception:
            return
        if raw == THEME_MODE_SYSTEM:
            self.apply_theme()

    def _apply_main_menu_theme(self, is_dark):
        """应用主题到右上角齿轮菜单（参考 PyCharm 风格）"""
        if self.main_menu is None:
            return

        # 关键：去掉系统窗口装饰 + 允许透明背景，让 QSS 的圆角四角真正生效
        self.main_menu.setWindowFlags(
            Qt.WindowType.Popup
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.main_menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        if is_dark:
            bg = "#2b2d30"
            border = "#4a4a4a"
            text_color = "#dddddd"
            hover_bg = "#1e3a5f"
            hover_text = "#ffffff"
            sep_color = "#4a4a4a"
            icon_color = "#bbbbbb"
        else:
            bg = "#ffffff"
            border = "#d0d0d0"
            text_color = "#333333"
            hover_bg = "#e8f0fe"
            hover_text = "#1976d2"
            sep_color = "#e0e0e0"
            icon_color = "#555555"

        self.main_menu.setStyleSheet(f"""
            QMenu#MainMenu {{
                background-color: {bg};
                border: 1px solid {border};
                border-radius: 8px;
                padding: 6px;
            }}
            QMenu#MainMenu::item {{
                background: transparent;
                padding: 7px 28px 7px 32px;
                margin: 1px 2px;
                border-radius: 5px;
                color: {text_color};
                font-size: 13px;
            }}
            QMenu#MainMenu::item:selected {{
                background-color: {hover_bg};
                color: {hover_text};
            }}
            QMenu#MainMenu::item:disabled {{
                color: #888888;
            }}
            QMenu#MainMenu::separator {{
                height: 1px;
                background: {sep_color};
                margin: 4px 8px;
            }}
            QMenu#MainMenu::icon {{
                left: 10px;
            }}
        """)
        # 图标颜色更新部分不变
        icon_map = {
            'settings': 'fa6s.gear',
            'about': 'fa6s.circle-info',
        }
        for key, action in self._main_menu_actions.items():
            if key in icon_map:
                action.setIcon(qta.icon(icon_map[key], color=icon_color))

    def _apply_visualize_dock_theme(self, is_dark, has_wallpaper=False):
        """应用主题到应用可视化 Dock（标题栏 + 边框 + 悬浮/关闭按钮）"""
        if self.visualize_dock is None:
            return

        if is_dark:
            dock_bg = "#2c2c2c"
            title_bg = "#252526"
            title_fg = "#eeeeee"
            border = "#4a4a4a"
            hover_bg = "rgba(255, 255, 255, 0.08)"
            icon_color = "#cccccc"
        else:
            dock_bg = "#ffffff"
            title_bg = "#f0f2f5"
            title_fg = "#333333"
            border = "#d0d0d0"
            hover_bg = "rgba(0, 0, 0, 0.06)"
            icon_color = "#666666"

        self.visualize_dock.setStyleSheet(f"""
            QDockWidget {{
                background-color: {dock_bg};
                border: 1px solid {border};
                border-radius: 8px;
                font-size: 13px;
            }}
            QDockWidget::title {{
                background-color: {title_bg};
                color: {title_fg};
                padding: 6px 10px;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                font-weight: 600;
                font-size: 13px;
                text-align: left;
            }}
            QDockWidget::close-button,
            QDockWidget::float-button {{
                background: transparent;
                border: none;
                padding: 3px;
                icon-size: 12px;
            }}
            QDockWidget::close-button:hover,
            QDockWidget::float-button:hover {{
                background-color: {hover_bg};
                border-radius: 3px;
            }}
        """)

    def _apply_project_menu_theme(self, is_dark):
        """应用主题到项目下拉按钮及其菜单（参照 PyCharm 风格）"""
        if self._project_menu_btn is None:
            return

        if is_dark:
            btn_color = "#ffffff"
            menu_bg = "#2b2d30"
            menu_border = "#4a4a4a"
            menu_text = "#dddddd"
            menu_hover_bg = "#1e3a5f"
            menu_hover_text = "#ffffff"
            sep_color = "#4a4a4a"
            icon_color = "#bbbbbb"
        else:
            btn_color = "#333333"
            menu_bg = "#ffffff"
            menu_border = "#d0d0d0"
            menu_text = "#333333"
            menu_hover_bg = "#e8f0fe"
            menu_hover_text = "#1976d2"
            sep_color = "#e0e0e0"
            icon_color = "#555555"

        # 按钮样式：仿 PyCharm 的项目 ▾——透明背景，悬停浅色底
        self._project_menu_btn.setStyleSheet(f"""
            QToolButton#ProjectMenuBtn {{
                background: transparent;
                border: none;
                color: {btn_color};
                font-weight: bold;
                font-size: 16px;
                padding: 0px 0px;
            }}
            QToolButton#ProjectMenuBtn::menu-indicator {{
                image: none;
                width: 0px;
                height: 0px;
            }}
        """)

        if self._project_menu is not None:
            self._project_menu.setWindowFlags(
                Qt.WindowType.Popup
                | Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.NoDropShadowWindowHint
            )
            self._project_menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self._project_menu.setStyleSheet(f"""
                QMenu#ProjectMenu {{
                    background-color: {menu_bg};
                    border: 1px solid {menu_border};
                    border-radius: 8px;
                    padding: 6px;
                }}
                QMenu#ProjectMenu::item {{
                    background: transparent;
                    padding: 7px 28px 7px 32px;
                    margin: 1px 2px;
                    border-radius: 5px;
                    color: {menu_text};
                    font-size: 13px;
                }}
                QMenu#ProjectMenu::item:selected {{
                    background-color: {menu_hover_bg};
                    color: {menu_hover_text};
                }}
                QMenu#ProjectMenu::separator {{
                    height: 1px;
                    background: {sep_color};
                    margin: 4px 8px;
                }}
                QMenu#ProjectMenu::icon {{
                    left: 10px;
                }}
            """)

            # 更新菜单图标颜色
            for action in self._project_menu.actions():
                txt = action.text()
                if txt == "导入用例":
                    action.setIcon(qta.icon('fa6s.file-import', color=icon_color))
                elif txt == "导出用例":
                    action.setIcon(qta.icon('fa6s.file-export', color=icon_color))

    def _apply_welcome_page_theme(self, is_dark, has_wallpaper=False):
        """应用主题到欢迎页（参照 PyCharm 欢迎页风格）
        有壁纸时卡片 transparent，让 centralWidget 罩层 + 壁纸透出。"""
        welcome_widget = None
        for i in range(self.stacked_widget.count()):
            page = self.stacked_widget.widget(i)
            if page is not None:
                w = page.findChild(QWidget, "WelcomeWidget")
                if w is not None:
                    welcome_widget = w
                    break
        if welcome_widget is None:
            return

        if is_dark:
            bg = "transparent" if has_wallpaper else "#191a1c"
            border = "#888" if has_wallpaper else "#555"
            title_color = "#ffffff"
            subtitle_color = "#888888"
            tips_title_color = "#bbbbbb"
            tip_label_color = "#eeeeee"
            tip_desc_color = "#888888"
            icon_color = "#bbbbbb"
        else:
            bg = "transparent" if has_wallpaper else "#ffffff"
            border = "#b0b0b0" if has_wallpaper else "#d0d0d0"
            title_color = "#1a1a1a"
            subtitle_color = "#999999"
            tips_title_color = "#666666"
            tip_label_color = "#333333"
            tip_desc_color = "#888888"
            icon_color = "#666666"

        # ---------- 应用 QSS ----------
        welcome_widget.setStyleSheet(f"""
            #WelcomeWidget {{
                background-color: {bg};
                border: 1px solid {border};
                border-radius: 12px;
            }}
            QLabel#WelcomeTitle {{
                color: {title_color};
                font-size: 28px;
                font-weight: 600;
                background: transparent;
            }}
            QLabel#WelcomeSubtitle {{
                color: {subtitle_color};
                font-size: 14px;
                background: transparent;
            }}
            QLabel#WelcomeTipsTitle {{
                color: {tips_title_color};
                font-size: 13px;
                font-weight: 500;
                background: transparent;
            }}
            QLabel#WelcomeTipLabel {{
                color: {tip_label_color};
                font-size: 13px;
                font-weight: 500;
                background: transparent;
            }}
            QLabel#WelcomeTipDesc {{
                color: {tip_desc_color};
                font-size: 13px;
                background: transparent;
            }}
            QWidget#WelcomeTipRow {{
                background: transparent;
            }}
            QWidget#WelcomeTipsContainer {{
                background: transparent;
            }}
        """)

        # ---------- 更新每行前面的图标颜色 ----------
        try:
            for icon_label, icon_name in getattr(self, '_welcome_tip_rows', []):
                try:
                    icon_label.setPixmap(qta.icon(icon_name, color=icon_color).pixmap(16, 16))
                except Exception:
                    pass
        except Exception:
            pass

    def setup_tab_icons(self):
        pass

    def set_models(self, project_model, step_model):
        self.project_model = project_model
        self.step_model = step_model

    def set_project_controller(self, controller):
        self.project_controller = controller

    def set_step_controller(self, controller):
        self.step_controller = controller
        if self.record_btn:
            self.record_btn.clicked.connect(controller.toggle_recording)
            self._update_record_button_state()

    def set_element_controller(self, controller):
        self.element_controller = controller

    def _update_record_button_state(self):
        if not self.record_btn:
            return
        if self.step_controller and self.step_controller.current_case_id:
            self.record_btn.setEnabled(True)
            if self.step_generate_btn:
                self.step_generate_btn.setEnabled(True)
            if getattr(self.step_controller, '_recording', False):
                self.record_btn.setIcon(qta.icon('fa6s.circle', color='#ff0000'))
            else:
                self.record_btn.setIcon(qta.icon('fa6s.circle', color='#00cc00'))
        else:
            self.record_btn.setEnabled(False)
            if self.step_generate_btn:
                self.step_generate_btn.setEnabled(False)
            self.record_btn.setIcon(qta.icon('fa6s.circle', color='#888888'))

    def setup_ui(self):
        # 主窗口本身透明（为了壁纸透出）
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet("QMainWindow { background: transparent; }")

        central = QWidget()
        central.setObjectName("centralWidget")
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ---------- 顶部工具栏 ----------
        toolbar = QToolBar("主工具栏", self)
        self.toolbar = toolbar
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        toolbar.setFixedHeight(40)
        toolbar.setStyleSheet("""
            QToolBar {
                background: #e9eaee;
                border: none;
                spacing: 4px;
            }
            QToolBar::separator {
                background: #c0c0c0;
                width: 1px;
                margin: 8px 4px;
            }
            QToolBar QComboBox {
                background: white;
                border: 1px solid #d0d0d0;
                border-radius: 4px;
                padding: 2px 8px;
                min-height: 22px;
                max-height: 26px;
                margin-left: 10px;
            }
            QToolBar QComboBox:hover { border-color: #1976d2; }
            QToolBar QPushButton {
                background: #1976d2;
                color: white;
                border: 1px solid #1976d2;
                border-radius: 4px;
                padding: 0px 12px;
                font-weight: 500;
                min-height: 26px;
                max-height: 26px;
            }
            QToolBar QPushButton:hover { background: #1565c0; border-color: #1565c0; }
            QToolBar QPushButton#menuBtn {
                background: transparent;
                border: none;
                min-width: 28px;
                max-width: 28px;
                min-height: 28px;
                max-height: 28px;
            }
            QToolBar QPushButton#menuBtn:hover { background: rgba(0,0,0,0.05); border-radius: 3px; }
        """)
        self.addToolBar(toolbar)

        device_layout = QHBoxLayout()
        device_layout.setSpacing(6)
        # 左侧留 10px 间距，让下拉框不贴顶部工具栏左边缘
        device_layout.setContentsMargins(0, 0, 0, 0)
        self.device_combo = QComboBox()
        self.device_combo.setMinimumWidth(160)
        # 关键：设置 QListView 作为 view，让 QSS 的下拉面板样式生效
        from PyQt6.QtWidgets import QListView as _QListView
        _device_view = _QListView()
        _device_view.setAutoFillBackground(False)
        _device_view.setStyleSheet("QListView { background: transparent; border: none; }")
        self.device_combo.setView(_device_view)
        self.device_combo.addItem("未检测到设备")
        device_layout.addWidget(self.device_combo)

        refresh_btn = QPushButton("刷新")
        refresh_btn.setIcon(qta.icon('fa6s.rotate', color='white'))
        refresh_btn.clicked.connect(self.refresh_devices_signal.emit)
        device_layout.addWidget(refresh_btn)

        device_widget = QWidget()
        device_widget.setLayout(device_layout)
        device_layout.setContentsMargins(0, 0, 0, 0)
        # 与 combo/button 一致的外部高度，让 QToolBar 垂直居中时对齐
        device_widget.setFixedHeight(28)
        toolbar.addWidget(device_widget)

        toolbar.addSeparator()

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        toolbar.addWidget(spacer)

        self.menu_btn = QPushButton()
        self.menu_btn.setObjectName("menuBtn")
        self.menu_btn.setIcon(qta.icon('fa6s.gear', color='#a3a6b0'))
        self.menu_btn.setToolTip("菜单")
        self.menu_btn.setFixedSize(32, 32)
        # 隐藏菜单指示器（下拉箭头）
        self.menu_btn.setStyleSheet(
            "QPushButton#menuBtn::menu-indicator { image: none; width: 0px; height: 0px; }"
        )
        self.main_menu = QMenu()
        self.main_menu.setObjectName("MainMenu")
        self._main_menu_actions = {}

        self._main_menu_actions['settings'] = self.main_menu.addAction(
            qta.icon('fa6s.gear', color='#555555'), "设置",
            lambda: self._on_menu_action("settings"))
        self.main_menu.addSeparator()
        self._main_menu_actions['about'] = self.main_menu.addAction(
            qta.icon('fa6s.circle-info', color='#555555'), "关于",
            lambda: self._on_menu_action("about"))

        self.menu_btn.setMenu(self.main_menu)
        toolbar.addWidget(self.menu_btn)

        # 菜单按钮右侧留白，让悬停效果不贴右边缘
        right_spacer = QWidget()
        right_spacer.setFixedWidth(8)
        toolbar.addWidget(right_spacer)

        # ---------- 主区域垂直分割器 ----------
        self.main_splitter = QSplitter(Qt.Vertical)
        self.main_splitter.setStyleSheet("background: transparent;")

        self.stacked_widget = QStackedWidget()
        self.stacked_widget.setStyleSheet("background: transparent;")
        for _ in range(9):
            self.stacked_widget.addWidget(QWidget())

        # 欢迎页（参照 PyCharm 欢迎页设计）
        welcome_wrapper = QWidget()
        welcome_wrapper.setObjectName("WelcomeWrapper")
        welcome_wrapper.setStyleSheet("#WelcomeWrapper { background: transparent; }")
        wrapper_layout = QVBoxLayout(welcome_wrapper)
        # 留出 20px 边距，让内部圆角卡片能露出来
        wrapper_layout.setContentsMargins(0, 0, 0, 0)
        wrapper_layout.setSpacing(0)

        welcome_widget = QWidget()
        welcome_widget.setObjectName("WelcomeWidget")
        welcome_widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        welcome_layout = QVBoxLayout(welcome_widget)
        welcome_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        welcome_layout.setContentsMargins(60, 60, 60, 60)
        welcome_layout.setSpacing(12)

        # 主标题
        welcome_title = QLabel("🐞 欢迎使用虫师")
        welcome_title.setObjectName("WelcomeTitle")
        welcome_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        welcome_layout.addWidget(welcome_title)

        # 副标题
        welcome_subtitle = QLabel("让自动化触手可及")
        welcome_subtitle.setObjectName("WelcomeSubtitle")
        welcome_subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        welcome_layout.addWidget(welcome_subtitle)

        welcome_layout.addSpacing(28)

        # 快速开始提示
        welcome_tips_title = QLabel("快速开始")
        welcome_tips_title.setObjectName("WelcomeTipsTitle")
        welcome_tips_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        welcome_layout.addWidget(welcome_tips_title)

        welcome_layout.addSpacing(10)

        # 用固定宽度容器包裹 tips，容器居中、内部内容左对齐
        tips_container = QWidget()
        tips_container.setObjectName("WelcomeTipsContainer")
        tips_container.setFixedWidth(520)
        tips_layout = QVBoxLayout(tips_container)
        tips_layout.setContentsMargins(80, 0, 0, 0)
        tips_layout.setSpacing(10)

        self._welcome_tip_rows = []
        tips = [
            ("fa6s.pen-to-square", "创建用例", "「自动化编辑」→ 右键项目树 → 创建用例"),
            ("fa6s.file-import", "导入 / 导出用例", "顶部「项目 ▾」→ 导入 / 导出用例"),
            ("fa6s.list", "管理元素", "「应用元素库」→ 新增 / 编辑元素"),
            ("fa6s.mobile-screen", "连接设备", "顶部工具栏 → 刷新设备"),
        ]
        for icon_name, label_text, desc_text in tips:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(10)

            icon_label = QLabel()
            icon_label.setObjectName("WelcomeTipIcon")
            icon_label.setFixedSize(18, 18)
            icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            row.addWidget(icon_label)
            self._welcome_tip_rows.append((icon_label, icon_name))

            label = QLabel(label_text)
            label.setObjectName("WelcomeTipLabel")
            label.setFixedWidth(110)
            label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            row.addWidget(label)

            desc = QLabel(desc_text)
            desc.setObjectName("WelcomeTipDesc")
            desc.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            row.addWidget(desc)

            row.addStretch()
            row_widget = QWidget()
            row_widget.setObjectName("WelcomeTipRow")
            row_widget.setLayout(row)
            tips_layout.addWidget(row_widget)

        welcome_layout.addWidget(tips_container, alignment=Qt.AlignmentFlag.AlignHCenter)

        wrapper_layout.addWidget(welcome_widget)
        self.stacked_widget.insertWidget(6, welcome_wrapper)

        self.main_splitter.addWidget(self.stacked_widget)

        # 底部占位（日志面板）
        self.bottom_placeholder = QLabel("捕虫师日志功能开发中")
        self.bottom_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.bottom_placeholder.setStyleSheet("color: #999; font-size: 16px; background: #e9eaee;")
        self.bottom_placeholder.setVisible(False)
        self.main_splitter.addWidget(self.bottom_placeholder)

        self.main_splitter.setSizes([1, 0])
        self.main_splitter.setChildrenCollapsible(False)

        # 弱网提示横幅（默认隐藏）
        self.weak_network_banner = QLabel("⚠️ 弱网模拟生效中")
        self.weak_network_banner.setObjectName("WeakNetworkBanner")
        self.weak_network_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.weak_network_banner.setFixedHeight(26)
        self.weak_network_banner.setStyleSheet(
            "background-color: #f39c12; color: white; font-weight: 600;"
            "font-size: 13px;"
        )
        self.weak_network_banner.setVisible(False)
        main_layout.addWidget(self.weak_network_banner)

        main_layout.addWidget(self.main_splitter)

        # ---------- 左侧功能导航 ----------
        self.left_toolbar = QToolBar("功能导航", self)
        self.left_toolbar.setOrientation(Qt.Vertical)
        self.left_toolbar.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.left_toolbar.setIconSize(QSize(20, 20))
        self.left_toolbar.setFixedWidth(40)
        self.left_toolbar.setStyleSheet("""
            QToolBar {
                background: #e9eaee;
                border: none;
                spacing: 1px;
            }
            QToolButton {
                border: none;
                border-radius: 3px;
                padding: 3px 1px;
                margin: 1px 2px;
                background: transparent;
                color: #a3a6b0;
            }
            QToolButton:hover {
                background: rgba(0,0,0,0.05);
            }
            QToolButton:checked {
                background: #1976d2;
            }
            QToolButton:checked:hover {
                background: #1565c0;
            }
        """)
        self.addToolBar(Qt.LeftToolBarArea, self.left_toolbar)

        # ========== 新增：为左侧工具栏安装事件过滤器 ==========
        self.left_toolbar.installEventFilter(self)

        nav_items = [
            ("ADB工具箱", 'fa6s.screwdriver-wrench', 0),
            ("自动化编辑", 'fa6s.pen-to-square', 1),
            ("应用可视化", 'fa6s.eye', 2),
            ("自动化执行", 'fa6s.play', 3),
            ("应用元素库", 'fa6s.list', 4),
            ("接口自动化", 'fa6s.plug', 7),
            ("性能检测", 'fa6s.gauge-high', 8),
        ]
        self.nav_actions = []
        self.nav_icons = []
        for text, icon, idx in nav_items:
            action = QAction(qta.icon(icon, color='#a3a6b0'), text, self)
            action.setCheckable(True)
            action.triggered.connect(lambda checked, i=idx: self.switch_view(i))
            self.left_toolbar.addAction(action)
            self.nav_actions.append(action)
            self.nav_icons.append(icon)

        spacer_widget = QWidget()
        spacer_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        spacer_action = QWidgetAction(self)
        spacer_action.setDefaultWidget(spacer_widget)
        self.left_toolbar.addAction(spacer_action)

        self.log_action = QAction(qta.icon('fa6s.terminal', color='#a3a6b0'), "日志", self)
        self.log_action.setCheckable(True)
        self.log_action.setToolTip("显示/隐藏捕虫师日志面板 (Ctrl+L)")
        self.log_action.triggered.connect(self.toggle_bottom_log)
        self.left_toolbar.addAction(self.log_action)

        # ---------- 右侧辅助工具栏 ----------
        self.right_toolbar = QToolBar("辅助工具", self)
        self.right_toolbar.setOrientation(Qt.Vertical)
        self.right_toolbar.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.right_toolbar.setIconSize(QSize(20, 20))
        self.right_toolbar.setFixedWidth(40)
        self.right_toolbar.setStyleSheet("""
            QToolBar {
                background: #e9eaee;
                border: none;
                spacing: 1px;
            }
            QToolButton {
                border: none;
                border-radius: 3px;
                padding: 3px 1px;
                margin: 1px 2px;
                background: transparent;
                color: #a3a6b0;
            }
            QToolButton:hover {
                background: rgba(0,0,0,0.05);
            }
            QToolButton:checked {
                background: #1976d2;
            }
            QToolButton:checked:hover {
                background: #1565c0;
            }
        """)
        self.addToolBar(Qt.RightToolBarArea, self.right_toolbar)

        # ========== 新增：为右侧工具栏安装事件过滤器 ==========
        self.right_toolbar.installEventFilter(self)

        msg_action = QAction(qta.icon('fa6s.bell', color='#a3a6b0'), "消息", self)
        msg_action.triggered.connect(lambda: show_toast(self, "消息功能开发中", duration=2000))
        self.right_toolbar.addAction(msg_action)

        self.ai_action = QAction(qta.icon('fa6s.robot', color='#a3a6b0'), "AI 助手", self)
        self.ai_action.setCheckable(True)
        self.ai_action.triggered.connect(self.toggle_ai_panel)
        self.right_toolbar.addAction(self.ai_action)

        self.help_action = QAction(qta.icon('fa6s.circle-question', color='#a3a6b0'), "帮助中心", self)
        self.help_action.setCheckable(True)
        self.help_action.triggered.connect(lambda: self.switch_view(5))
        self.right_toolbar.addAction(self.help_action)
        # ---------- 为左侧和右侧工具栏按钮安装事件过滤器 ----------
        for action in self.left_toolbar.actions():
            btn = self.left_toolbar.widgetForAction(action)
            if btn:
                btn.installEventFilter(self)
                self.left_toolbar_buttons.append(btn)

        for action in self.right_toolbar.actions():
            btn = self.right_toolbar.widgetForAction(action)
            if btn:
                btn.installEventFilter(self)
                self.right_toolbar_buttons.append(btn)
        # ---------- 状态栏 ----------
        self.statusBar().setFixedHeight(30)
        self.statusBar().setStyleSheet("background: #e9eaee; color: #333;")
        self.statusBar().show()
        # 初始化为未连接状态（与 update_device_list 保持一致）
        self.device_status_label = QLabel("○ 未连接设备")
        self.device_status_label.setStyleSheet("padding: 2px 8px; color: #c0392b; background: transparent;")
        self.statusBar().addWidget(self.device_status_label)

        # ---------- 应用可视化 Dock ----------
        self.setup_visualize_dock()

    # ---------- 应用可视化 Dock ----------
    def setup_visualize_dock(self):
        """创建应用可视化 Dock（默认隐藏，由左侧按钮开关）"""
        # 用 RoundedDockWidget 替代 QDockWidget，最外层带圆角
        self.visualize_dock = RoundedDockWidget("应用可视化", self, radius=8)
        self.visualize_dock.setObjectName("VisualizeDock")
        self.visualize_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea |
            Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.visualize_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable |
            QDockWidget.DockWidgetFeature.DockWidgetFloatable |
            QDockWidget.DockWidgetFeature.DockWidgetClosable
        )

        # 内部容器（后续 set_visualize_view 会把真正的可视化 widget 塞进来）
        dock_content = QWidget()
        dock_content.setObjectName("VisualizeDockContent")
        # dock_content 透明，让圆角外露出的部分透出 dock 底色
        dock_content.setStyleSheet(
            "#VisualizeDockContent { background: transparent; }"
        )
        dock_layout = QVBoxLayout(dock_content)
        # 四周边距，让内部容器的圆角露出来
        dock_layout.setContentsMargins(6, 6, 6, 6)
        dock_layout.setSpacing(0)
        self.visualize_dock.setWidget(dock_content)

        # 添加到右侧
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.visualize_dock)
        self.visualize_dock.resize(500, self.height())
        self.visualize_dock.hide()

        # 联动左侧按钮状态
        self.visualize_dock.visibilityChanged.connect(self._on_visualize_dock_visibility)

    def _on_visualize_dock_visibility(self, visible):
        """dock 显示/隐藏时，同步左侧按钮的选中与图标状态"""
        if len(self.nav_actions) > 2:
            action = self.nav_actions[2]
            action.setChecked(visible)
            color = 'white' if visible else '#a3a6b0'
            action.setIcon(qta.icon(self.nav_icons[2], color=color))

    def toggle_visualize_dock(self, checked):
        """显示或隐藏可视化 Dock；首次显示时自动启动 weditor"""
        if self.visualize_dock is None:
            return
        if checked:
            # 以浮动窗口方式显示，避开对主界面布局的挤压
            if not self.visualize_dock.isFloating():
                self.visualize_dock.setFloating(True)

            # 首次浮动时给一个居中的合理尺寸和位置
            if not getattr(self, '_visualize_dock_positioned', False):
                self.visualize_dock.resize(1620, 780)
                # 相对主窗口右对齐居中
                main_geo = self.geometry()
                x = main_geo.right() - self.visualize_dock.width() - 60
                y = main_geo.top() + 80
                self.visualize_dock.move(x, y)
                self._visualize_dock_positioned = True

            self.visualize_dock.show()
            self.visualize_dock.raise_()

            # 首次打开时，若无 weditor 进程则自动启动
            if self.weditor_service is not None:
                if getattr(self.weditor_service, 'process', None) is None:
                    self._start_weditor()
        else:
            self.visualize_dock.hide()

    # ---------- 壁纸相关 ----------
    def setup_wallpaper(self):
        # 父控件改为 MainWindow，让壁纸覆盖包括工具栏在内的整个窗口
        self.wallpaper_label = QLabel(self)
        self.wallpaper_label.setObjectName("wallpaper")
        self.wallpaper_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.wallpaper_label.setStyleSheet("background: transparent;")
        self.wallpaper_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.wallpaper_label.setGeometry(self.rect())
        self.wallpaper_label.lower()
        self.wallpaper_label.show()
        # 壁纸实际是否成功加载（决定 _has_wallpaper() 的返回值）
        self._wallpaper_loaded = False

    def load_wallpaper(self):
        path = Settings.get_wallpaper_path()
        if path:
            # 交给 set_wallpaper 判断：成功就换，失败则保持现状
            opacity = Settings.get_wallpaper_opacity()
            self.set_wallpaper(path, opacity)
        else:
            # 路径为空（用户主动移除），才真正清空壁纸
            self.remove_wallpaper()

    def set_wallpaper(self, path, opacity=100):
        # 失败时保持现状：不清 label、不改 _wallpaper_loaded
        # 全部改动都放在确认 pixmap 有效之后再执行
        if not os.path.exists(path):
            print(f"壁纸文件不存在: {path}")
            show_toast(self, "壁纸文件不存在，已保持当前壁纸", duration=2500)
            return
        pixmap = QPixmap(path)
        if pixmap.isNull():
            print(f"加载壁纸失败: {path}")
            show_toast(
                self,
                "壁纸加载失败（文件可能已损坏或格式不支持），已保持当前壁纸",
                duration=3000
            )
            return

        # 以下为成功分支：更新 label + 状态标记
        rect = self.rect()
        scaled_pixmap = pixmap.scaled(
            rect.size(),
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation
        )
        self.wallpaper_label.setPixmap(scaled_pixmap)
        self.wallpaper_label.setScaledContents(False)
        self.wallpaper_label.setGeometry(rect)
        effect = QGraphicsOpacityEffect()
        effect.setOpacity(opacity / 100.0)
        self.wallpaper_label.setGraphicsEffect(effect)
        self.wallpaper_label.show()
        self.wallpaper_label.lower()
        # 标记加载成功
        self._wallpaper_loaded = True

    def remove_wallpaper(self):
        self.wallpaper_label.clear()
        self.wallpaper_label.setGraphicsEffect(None)
        self.wallpaper_label.hide()
        self._wallpaper_loaded = False
        Settings.set_wallpaper_path('')

    def _has_wallpaper(self):
        # 以"实际加载状态"为准，避免文件存在但加载失败时样式错乱
        return getattr(self, '_wallpaper_loaded', False)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if getattr(self, '_wallpaper_loaded', False) and hasattr(self, 'wallpaper_label'):
            rect = self.rect()
            self.wallpaper_label.setGeometry(rect)
            path = Settings.get_wallpaper_path()
            if path and os.path.exists(path):
                pixmap = QPixmap(path)
                if not pixmap.isNull():
                    scaled = pixmap.scaled(
                        rect.size(),
                        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                        Qt.TransformationMode.SmoothTransformation
                    )
                    self.wallpaper_label.setPixmap(scaled)

    # ---------- 主题应用 ----------
    def apply_theme(self, theme_mode=None):
        if theme_mode is None:
            theme_mode = Settings.get_theme_mode()

        if theme_mode == THEME_MODE_DARK:
            mode = ThemeMode.DARK
            Theme.apply_theme_to_widget(self, mode)
            for view in self._sub_views:
                if view and hasattr(view, 'apply_theme'):
                    try:
                        view.apply_theme(mode)
                    except Exception as e:
                        print(f"[apply_theme] {view} 报错: {e}")
                else:
                    Theme.apply_theme_to_widget(view, mode)

            # 显式通知步骤列表刷新（滚动条样式 + 清空卡片）
            # 注意：step_list 没有注册到 _sub_views 里，必须在这里单独调用
            if self.step_list is not None:
                try:
                    self.step_list.apply_theme(mode)
                except Exception as e:
                    print(f"[apply_theme] step_list.apply_theme 出错: {e}")
                if hasattr(self.step_list, '_apply_scrollbar_style'):
                    self.step_list._apply_scrollbar_style(mode)

            for i in range(self.stacked_widget.count()):
                widget = self.stacked_widget.widget(i)
                if widget and widget.objectName():
                    Theme.apply_theme_to_widget(widget, mode)

            # 工具栏和状态栏：有壁纸时半透明，无壁纸时不透明
            has_wallpaper = self._has_wallpaper()
            toolbar_bg = "rgba(60, 60, 60, 0.7)" if has_wallpaper else "#3c3c3c"

            self.toolbar.setStyleSheet(f"""
                QToolBar {{
                    background: {toolbar_bg};
                    border: none;
                    spacing: 4px;
                }}
                QToolBar::separator {{
                    background: #6a6a6a;
                    width: 1px;
                    margin: 8px 4px;
                }}
                QToolBar QComboBox {{
                    background: rgba(85, 85, 85, 0.9);
                    color: #eee;
                    border: 1px solid #777;
                    border-radius: 4px;
                    padding: 2px 8px;
                    min-height: 22px;
                    max-height: 26px;
                    margin-left: 10px;
                }}
                QToolBar QPushButton {{
                    background: rgba(25, 118, 210, 0.9);
                    color: white;
                    border: 1px solid rgba(25, 118, 210, 0.9);
                    border-radius: 4px;
                    padding: 0px 12px;
                    font-weight: 500;
                    min-height: 26px;
                    max-height: 26px;
                }}
                QToolBar QPushButton:hover {{ background: rgba(21, 101, 192, 0.95); border-color: rgba(21, 101, 192, 0.95); }}
                QToolBar QPushButton#menuBtn {{
                    background: transparent;
                    border: none;
                    min-width: 28px;
                    max-width: 28px;
                    min-height: 28px;
                    max-height: 28px;
                }}
                QToolBar QPushButton#menuBtn:hover {{ background: rgba(255, 255, 255, 0.08); border-radius: 3px; }}
                QToolBar QPushButton#menuBtn::menu-indicator {{ image: none; width: 0px; height: 0px; }}
                QToolBar QLabel {{ color: #eee; background: transparent; }}
            """)
            # 菜单按钮图标颜色适配暗色
            if hasattr(self, 'menu_btn') and self.menu_btn is not None:
                self.menu_btn.setIcon(qta.icon('fa6s.gear', color='#a3a6b0'))

            sidebar_style_dark = f"""
                QToolBar {{ background: {toolbar_bg}; border: none; spacing: 1px; }}
                QToolButton {{
                    border: none; border-radius: 3px;
                    padding: 3px 1px; margin: 1px 2px;
                    background: transparent; color: #ccc;
                }}
                QToolButton:hover {{ background: rgba(255, 255, 255, 0.08); }}
                QToolButton:checked {{ background: #555; }}
            """
            self.left_toolbar.setStyleSheet(sidebar_style_dark)
            self.right_toolbar.setStyleSheet(sidebar_style_dark)
            self.statusBar().setStyleSheet(f"background: {toolbar_bg}; color: #ccc;")

            # 设备下拉框样式
            self.device_combo.setStyleSheet("""
                QComboBox {
                    background-color: #555;
                    color: #eee;
                    border: 1px solid #777;
                    border-radius: 4px;
                    padding: 2px 8px;
                    min-height: 22px;
                    max-height: 26px;
                }
                QComboBox QAbstractItemView {
                    background-color: #3c3c3c;
                    color: #eee;
                    border: 1px solid #555;
                    border-radius: 6px;
                    selection-background-color: #90caf9;
                    selection-color: #1e1e1e;
                    outline: none;
                    padding: 4px;
                    margin: 0px;
                }
                QComboBox QAbstractItemView::item {
                    background-color: #3c3c3c;
                    color: #eee;
                    min-height: 24px;
                    padding: 4px 10px;
                    margin: 1px 2px;
                    border: none;
                    border-radius: 4px;
                }
                QComboBox QAbstractItemView::item:hover {
                    background-color: #64b5f6;
                    color: #1e1e1e;
                }
                QComboBox QAbstractItemView::item:selected {
                    background-color: #90caf9;
                    color: #1e1e1e;
                }
            """)

            # 中央区域背景：有壁纸使用与工具栏一致的半透明色，无壁纸纯色
            if has_wallpaper:
                self.centralWidget().setStyleSheet("background: rgba(60, 60, 60, 0.7);")
            else:
                self.centralWidget().setStyleSheet("background: #3c3c3c;")
            self.stacked_widget.setStyleSheet("background: transparent;")

            # 清空之前可能设置的背景，保证所有子页面都透明
            for i in range(self.stacked_widget.count()):
                widget = self.stacked_widget.widget(i)
                if widget and widget.objectName() == "":
                    widget.setStyleSheet("background: transparent;")
                elif widget and widget.objectName():
                    # 部分视图已由 Theme 应用样式，不强制覆盖
                    pass

            # 为自动化编辑页面的三个组设置背景
            # 有壁纸时透明（由 centralWidget 提供统一深色罩），无壁纸时纯色
            for group in self.findChildren(QGroupBox):
                if group.objectName() in ("EditProjectGroup", "EditStepGroup", "EditActionGroup"):
                    if has_wallpaper:
                        group.setStyleSheet("""
                            QGroupBox {
                                background-color: transparent;
                                border: 1px solid rgba(85, 85, 85, 0.9);
                                border-radius: 8px;
                                padding: 6px;
                            }
                            QGroupBox::title {
                                color: #ffffff;
                            }
                        """)
                    else:
                        group.setStyleSheet("""
                            QGroupBox {
                                background-color: #191a1c;
                                border: 1px solid #555;
                                border-radius: 8px;
                                padding: 6px;
                            }
                            QGroupBox::title {
                                color: #ffffff;
                            }
                        """)
            # 三个区域标题文字颜色适配暗色
            for lbl in self.findChildren(QLabel, "GroupTitleLabel"):
                lbl.setStyleSheet(
                    "font-weight: bold; font-size: 16px; background: transparent; color: #ffffff;"
                )

            if self._visualize_container is not None:
                self._visualize_container.setStyleSheet(
                    "#VisualizeContainer { background-color: #2c2c2c; }"
                )
            self._apply_visualize_dock_theme(is_dark=True, has_wallpaper=has_wallpaper)
            # 执行页三面板（日志/执行/定时任务）壁纸适配
            self._apply_execute_page_theme(has_wallpaper, is_dark=True)
            # 捕虫师日志面板
            self._apply_bottom_log_theme(is_dark=True, has_wallpaper=has_wallpaper)

            # 应用元素库容器背景（与项目管理一致：无壁纸纯色，有壁纸 transparent 由 centralWidget 罩层透出）
            if self._element_container is not None:
                if has_wallpaper:
                    self._element_container.setStyleSheet("""
                        #ElementManagerContainer {
                            background: transparent;
                            border: 1px solid rgba(85, 85, 85, 0.9);
                            border-radius: 8px;
                        }
                    """)
                else:
                    self._element_container.setStyleSheet("""
                        #ElementManagerContainer {
                            background: #191a1c;
                            border: 1px solid #555;
                            border-radius: 8px;
                        }
                    """)
            # 步骤搜索框夜间样式
            if getattr(self, 'step_search_input', None) is not None:
                self.step_search_input.setStyleSheet("""
                    QLineEdit {
                        background: #3c3c3c;
                        border: 1px solid #555;
                        border-radius: 4px;
                        padding: 2px 8px;
                        color: #eee;
                    }
                    QLineEdit:focus {
                        border-color: #90caf9;
                    }
                """)

            # 主菜单主题
            self._apply_main_menu_theme(is_dark=True)
            # "项目 ▾"下拉按钮及菜单主题
            self._apply_project_menu_theme(is_dark=True)
            # 欢迎页主题
            self._apply_welcome_page_theme(is_dark=True, has_wallpaper=has_wallpaper)
            # Windows 原生标题栏跟随深色
            set_dark_title_bar(self, True)
            if self._perf_view is not None:
                try:
                    self._perf_view.apply_theme(mode if theme_mode == THEME_MODE_DARK else ThemeMode.LIGHT,
                                                has_wallpaper)
                except Exception as e:
                    print(f"[apply_theme] perf_view 报错: {e}")
            return

        # ---------- 亮色主题（保持原有不透明样式） ----------
        mode = ThemeMode.LIGHT
        Theme.apply_theme_to_widget(self, mode)

        for view in self._sub_views:
            if view and hasattr(view, 'apply_theme'):
                try:
                    view.apply_theme(mode)
                except Exception as e:
                    print(f"[apply_theme] {view} 报错: {e}")
            else:
                Theme.apply_theme_to_widget(view, mode)

        # 显式通知步骤列表刷新（滚动条样式 + 清空卡片）
        # 注意：step_list 没有注册到 _sub_views 里，必须在这里单独调用
        if self.step_list is not None:
            try:
                self.step_list.apply_theme(mode)
            except Exception as e:
                print(f"[apply_theme] step_list.apply_theme 出错: {e}")
            if hasattr(self.step_list, '_apply_scrollbar_style'):
                self.step_list._apply_scrollbar_style(mode)

        for i in range(self.stacked_widget.count()):
            widget = self.stacked_widget.widget(i)
            if widget and widget.objectName():
                Theme.apply_theme_to_widget(widget, mode)

        has_wallpaper = self._has_wallpaper()
        toolbar_bg = "rgba(233, 234, 238, 0.7)" if has_wallpaper else "#e9eaee"

        central = self.centralWidget()
        if central:
            if has_wallpaper:
                # 有壁纸：中央区域使用与工具栏一致的半透明色，让圆角内外一致
                central.setStyleSheet("background: rgba(233, 234, 238, 0.7);")
            else:
                # 没有壁纸时用 #e9eaee
                central.setStyleSheet("background: #e9eaee;")
        self.stacked_widget.setStyleSheet("background: transparent;")

        self.toolbar.setStyleSheet(f"""
            QToolBar {{
                background: {toolbar_bg};
                border: none;
                spacing: 4px;
            }}
            QToolBar::separator {{
                background: #c0c0c0;
                width: 1px;
                margin: 8px 4px;
            }}
            QToolBar QComboBox {{
                background: rgba(255, 255, 255, 0.9);
                border: 1px solid #d0d0d0;
                border-radius: 4px;
                padding: 2px 8px;
                min-height: 22px;
                max-height: 26px;
                margin-left: 10px;
            }}
            QToolBar QComboBox:hover {{ border-color: #1976d2; }}
            QToolBar QPushButton {{
                background: rgba(25, 118, 210, 0.9);
                color: white;
                border: 1px solid rgba(25, 118, 210, 0.9);
                border-radius: 4px;
                padding: 0px 12px;
                font-weight: 500;
                min-height: 26px;
                max-height: 26px;
            }}
            QToolBar QPushButton:hover {{ background: rgba(21, 101, 192, 0.95); border-color: rgba(21, 101, 192, 0.95); }}
            QToolBar QPushButton#menuBtn {{
                background: transparent;
                border: none;
                min-width: 28px;
                max-width: 28px;
                min-height: 28px;
                max-height: 28px;
            }}
            QToolBar QPushButton#menuBtn:hover {{ background: rgba(0,0,0,0.05); border-radius: 3px; }}
            QToolBar QPushButton#menuBtn::menu-indicator {{ image: none; width: 0px; height: 0px; }}
            QToolBar QLabel {{ color: #333; background: transparent; }}
        """)
        # 菜单按钮图标颜色适配主题
        if hasattr(self, 'menu_btn') and self.menu_btn is not None:
            self.menu_btn.setIcon(qta.icon('fa6s.gear', color='#a3a6b0'))

        sidebar_style = f"""
            QToolBar {{
                background: {toolbar_bg};
                border: none;
                spacing: 1px;
            }}
            QToolButton {{
                border: none;
                border-radius: 3px;
                padding: 3px 1px;
                margin: 1px 2px;
                background: transparent;
                color: #a3a6b0;
            }}
            QToolButton:hover {{
                background: rgba(0,0,0,0.05);
            }}
            QToolButton:checked {{
                background: #1976d2;
                color: white;
            }}
            QToolButton:checked:hover {{
                background: #1565c0;
            }}
            QToolButton:disabled {{
                color: #ccc;
            }}
        """
        self.left_toolbar.setStyleSheet(sidebar_style)
        self.right_toolbar.setStyleSheet(sidebar_style)

        self.statusBar().setStyleSheet(f"background: {toolbar_bg}; color: #333;")
        self.device_combo.setStyleSheet("""
            QComboBox {
                background-color: white;
                color: #333;
                border: 1px solid #d0d0d0;
                border-radius: 4px;
                padding: 2px 8px;
                min-height: 22px;
                max-height: 26px;
            }
            QComboBox:hover { border-color: #1976d2; }
            QComboBox QAbstractItemView {
                background-color: white;
                color: #333;
                border: 1px solid #d0d0d0;
                border-radius: 6px;
                selection-background-color: #1976d2;
                selection-color: white;
                outline: none;
                padding: 4px;
                margin: 0px;
            }
            QComboBox QAbstractItemView::item {
                background-color: white;
                color: #333;
                min-height: 24px;
                padding: 4px 10px;
                margin: 1px 2px;
                border: none;
                border-radius: 4px;
            }
            QComboBox QAbstractItemView::item:hover {
                background-color: #e8f0fe;
                color: #1976d2;
            }
            QComboBox QAbstractItemView::item:selected {
                background-color: #1976d2;
                color: white;
            }
        """)

        # 为自动化编辑页面的三个组应用亮色主题
        # 有壁纸时透明（由 centralWidget 提供统一白罩），无壁纸时纯白
        for group in self.findChildren(QGroupBox):
            if group.objectName() in ("EditProjectGroup", "EditStepGroup", "EditActionGroup"):
                if has_wallpaper:
                    group.setStyleSheet("""
                        QGroupBox {
                            background-color: transparent;
                            border: 1px solid rgba(208, 208, 208, 0.9);
                            border-radius: 8px;
                            padding: 6px;
                        }
                        QGroupBox::title {
                            color: #333;
                        }
                    """)
                else:
                    group.setStyleSheet("""
                        QGroupBox {
                            background-color: #ffffff;
                            border: 1px solid #d0d0d0;
                            border-radius: 8px;
                            padding: 6px;
                        }
                        QGroupBox::title {
                            color: #333;
                        }
                    """)

        # 三个区域标题文字颜色适配亮色
        for lbl in self.findChildren(QLabel, "GroupTitleLabel"):
            lbl.setStyleSheet(
                "font-weight: bold; font-size: 16px; background: transparent; color: #333;"
            )
        # 应用元素库容器背景（与项目管理一致：无壁纸 #ffffff，有壁纸 transparent 由 centralWidget 罩层透出）
        if self._element_container is not None:
            if has_wallpaper:
                self._element_container.setStyleSheet("""
                    #ElementManagerContainer {
                        background: transparent;
                        border: 1px solid rgba(208, 208, 208, 0.9);
                        border-radius: 8px;
                    }
                """)
            else:
                self._element_container.setStyleSheet("""
                    #ElementManagerContainer {
                        background: #ffffff;
                        border: 1px solid #d0d0d0;
                        border-radius: 8px;
                    }
                """)
        # 可视化页容器：不透明背景，遮挡壁纸
        if self._visualize_container is not None:
            self._visualize_container.setStyleSheet(
                "#VisualizeContainer { background-color: #e9eaee; }"
            )
        self._apply_visualize_dock_theme(is_dark=False, has_wallpaper=has_wallpaper)
            # 执行页三面板（日志/执行/定时任务）壁纸适配
        self._apply_execute_page_theme(has_wallpaper, is_dark=False)
        # 捕虫师日志面板
        self._apply_bottom_log_theme(is_dark=False, has_wallpaper=has_wallpaper)
        # 步骤搜索框亮色样式
        if getattr(self, 'step_search_input', None) is not None:
            self.step_search_input.setStyleSheet("""
                QLineEdit {
                    background: white;
                    border: 1px solid #d0d0d0;
                    border-radius: 4px;
                    padding: 2px 8px;
                    color: #333;
                }
                QLineEdit:focus {
                    border-color: #1976d2;
                }
            """)

        # 主菜单主题
        self._apply_main_menu_theme(is_dark=False)
        # "项目 ▾"下拉按钮及菜单主题
        self._apply_project_menu_theme(is_dark=False)
        # 欢迎页主题
        self._apply_welcome_page_theme(is_dark=False, has_wallpaper=has_wallpaper)
        # Windows 原生标题栏跟随浅色
        set_dark_title_bar(self, False)
        if self._perf_view is not None:
            try:
                self._perf_view.apply_theme(mode if theme_mode == THEME_MODE_DARK else ThemeMode.LIGHT, has_wallpaper)
            except Exception as e:
                print(f"[apply_theme] perf_view 报错: {e}")


    def register_sub_view(self, view):
        if view and view not in self._sub_views:
            self._sub_views.append(view)

    # ---------- 视图切换 ----------
    def switch_view(self, index):
        # 应用可视化：不再切页，改为开关 Dock
        if index == 2:
            if self.visualize_dock is not None:
                self.toggle_visualize_dock(not self.visualize_dock.isVisible())
            return

        self.stacked_widget.setCurrentIndex(index)
        for i, action in enumerate(self.nav_actions):
            if i == 2:
                # 可视化按钮的状态由 dock 的可见性控制，跳过
                continue
            if i == index:
                action.setIcon(qta.icon(self.nav_icons[i], color='white'))
            else:
                action.setIcon(qta.icon(self.nav_icons[i], color='#a3a6b0'))
        if index == 5:
            for i, action in enumerate(self.nav_actions):
                if i == 2:
                    continue
                action.setChecked(False)
                # 关键：图标颜色也同步改回灰色，否则会停留在白色
                action.setIcon(qta.icon(self.nav_icons[i], color='#a3a6b0'))
            self.help_action.setChecked(True)
            self.help_action.setIcon(qta.icon('fa6s.circle-question', color='white'))
            self.ai_action.setChecked(False)
            self.ai_action.setIcon(qta.icon('fa6s.robot', color='#a3a6b0'))
            self.statusBar().showMessage("当前功能: 帮助中心", 2000)
        elif index == 6:
            for i, action in enumerate(self.nav_actions):
                if i == 2:
                    continue
                action.setChecked(False)
                action.setIcon(qta.icon(self.nav_icons[i], color='#a3a6b0'))
            self.help_action.setChecked(False)
            self.help_action.setIcon(qta.icon('fa6s.circle-question', color='#a3a6b0'))
            self.ai_action.setChecked(False)
            self.ai_action.setIcon(qta.icon('fa6s.robot', color='#a3a6b0'))
            self.statusBar().showMessage("欢迎", 2000)
        else:
            for i, action in enumerate(self.nav_actions):
                if i == 2:
                    continue
                action.setChecked(i == index)
            self.help_action.setChecked(False)
            self.help_action.setIcon(qta.icon('fa6s.circle-question', color='#a3a6b0'))
            self.ai_action.setChecked(False)
            self.ai_action.setIcon(qta.icon('fa6s.robot', color='#a3a6b0'))
            if index < len(self.nav_actions):
                self.statusBar().showMessage(f"当前功能: {self.nav_actions[index].text()}", 2000)
            # 占位页面（接口自动化 / 性能检测）
            if index in (7, 8):
                from PyQt6.QtWidgets import QWidget as _QW, QVBoxLayout as _QVL
                page = self.stacked_widget.widget(index)
                if page is not None and page.layout() is None:
                    layout = _QVL(page)
                    label = QLabel("功能开发中，敬请期待")
                    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    label.setStyleSheet("color: #888; font-size: 20px; background: transparent;")
                    layout.addWidget(label)

    # ---------- 底部日志面板 ----------
    def set_bottom_log_placeholder(self, widget):
        old = self.main_splitter.widget(1)
        if old:
            old.deleteLater()

        # 外层容器：负责圆角 + 边框 + 背景
        container = QFrame()
        container.setObjectName("BottomLogContainer")
        container.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(12, 12, 12, 12)
        container_layout.setSpacing(0)

        # 内层内容：透明，让外层背景透出
        widget.setObjectName("BottomLogContent")
        widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        widget.setStyleSheet("#BottomLogContent { background: transparent; }")
        container_layout.addWidget(widget)

        self.main_splitter.insertWidget(1, container)
        container.setVisible(False)
        self.bottom_placeholder = container
        self.bottom_placeholder_inner = widget
        self.register_sub_view(widget)

    def toggle_bottom_log(self, checked):
        if not self.bottom_placeholder:
            return
        self.bottom_placeholder.setVisible(checked)
        self.log_action.setChecked(checked)
        self.log_action.setIcon(qta.icon('fa6s.terminal', color='white' if checked else '#a3a6b0'))
        if checked:
            total = self.main_splitter.height()
            self.main_splitter.setSizes([int(total * 0.7), int(total * 0.3)])
        else:
            self.main_splitter.setSizes([1, 0])

    # ---------- AI 面板 ----------
    def toggle_ai_panel(self, checked):
        if checked:
            self.ai_action.setIcon(qta.icon('fa6s.robot', color='white'))
            self.help_action.setChecked(False)
            self.help_action.setIcon(qta.icon('fa6s.circle-question', color='#a3a6b0'))
            self.ai_action.setChecked(True)
        else:
            self.ai_action.setIcon(qta.icon('fa6s.robot', color='#a3a6b0'))
            self.ai_action.setChecked(False)
        QMessageBox.information(self, "AI 助手", "AI 功能开发中，敬请期待")
        self.ai_action.setChecked(False)
        self.ai_action.setIcon(qta.icon('fa6s.robot', color='#a3a6b0'))

    # ---------- 设置对话框 ----------
    def on_settings(self):
        dlg = SettingsDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            # 顺序不能反：先加载壁纸更新 _wallpaper_loaded，
            # 再 apply_theme 让 centralWidget 按有壁纸渲染半透明背景
            self.load_wallpaper()
            self.apply_theme()

    # ---------- 设置各种视图 ----------
    def set_edit_views(self, project_tree, step_list, action_card):
        project_tree.setObjectName("ProjectTreeView")
        step_list.setObjectName("StepListView")

        container = QWidget()
        container.setStyleSheet("background: transparent;")
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setStyleSheet("""
            QSplitter {
                background: transparent;
                border: none;
            }
            QSplitter::handle {
                background: transparent;
                border: none;
                width: 2px;
            }
            QSplitter::handle:hover {
                background: transparent;
            }
            QSplitter::handle:pressed {
                background: transparent;
            }
        """)
        splitter.setHandleWidth(4)
        splitter.setChildrenCollapsible(False)

        def create_group_with_title(title, hint, widget, extra_widget=None):
            group = QGroupBox()
            group.setFlat(False)
            group.setStyleSheet("""
                QGroupBox {
                    background: #ffffff;
                    border: 1px solid #d0d0d0;
                    border-radius: 8px;
                    padding: 6px;
                }
            """)
            g_layout = QVBoxLayout(group)
            g_layout.setContentsMargins(0, 0, 0, 0)
            title_layout = QHBoxLayout()
            title_label = QLabel(title)
            title_label.setObjectName("GroupTitleLabel")
            title_label.setStyleSheet("font-weight: bold; font-size: 16px; background: transparent; color: #333;")
            hint_label = QLabel(hint)
            hint_label.setStyleSheet("color: #999; font-size: 12px; background: transparent;")
            title_layout.addWidget(title_label)
            title_layout.addWidget(hint_label)
            title_layout.addStretch()
            if extra_widget:
                title_layout.addWidget(extra_widget)
            g_layout.addLayout(title_layout)
            g_layout.addWidget(widget)
            return group

        # ---------- 项目管理：标题本身就是"项目 ▾"下拉按钮（参照 PyCharm）----------
        project_menu_btn = QToolButton()
        project_menu_btn.setObjectName("ProjectMenuBtn")
        project_menu_btn.setText("项目管理 ▾")
        project_menu_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        project_menu_btn.setCursor(Qt.CursorShape.PointingHandCursor)

        project_menu = QMenu(project_menu_btn)
        project_menu.setObjectName("ProjectMenu")
        project_menu.addAction(
            qta.icon('fa6s.file-import', color='#555555'), "导入用例",
            lambda: self._on_menu_action("import_cases"))
        project_menu.addAction(
            qta.icon('fa6s.file-export', color='#555555'), "导出用例",
            lambda: self._on_menu_action("export_cases"))
        project_menu_btn.setMenu(project_menu)

        self._project_menu_btn = project_menu_btn
        self._project_menu = project_menu

        # 用自定义标题：只放下拉按钮，去掉"项目管理"冗余文字
        project_group = QGroupBox()
        project_group.setFlat(False)
        project_group.setStyleSheet("""
            QGroupBox {
                background: #ffffff;
                border: 1px solid #d0d0d0;
                border-radius: 8px;
                padding: 6px;
            }
        """)
        g_layout = QVBoxLayout(project_group)
        g_layout.setContentsMargins(0, 0, 0, 0)

        title_layout = QHBoxLayout()
        title_layout.setContentsMargins(0, 0, 0, 0)
        # 直接把"项目 ▾"按钮当作标题
        title_layout.addWidget(project_menu_btn)
        hint_label = QLabel("点击节点可查看用例")
        hint_label.setStyleSheet("color: #999; font-size: 12px; background: transparent;")
        title_layout.addWidget(hint_label)
        title_layout.addStretch()
        g_layout.addLayout(title_layout)
        g_layout.addWidget(project_tree)

        project_group.setObjectName("EditProjectGroup")
        self.step_list = step_list
        step_group = QGroupBox()
        step_group.setObjectName("EditStepGroup")
        step_group.setFlat(False)
        step_group.setStyleSheet("""
            QGroupBox {
                background: #ffffff;
                border: 1px solid #d0d0d0;
                border-radius: 8px;
                
                padding: 6px;
            }
        """)
        step_layout = QVBoxLayout(step_group)
        step_layout.setContentsMargins(0, 0, 0, 0)

        title_layout = QHBoxLayout()
        title_label = QLabel("步骤列表")
        title_label.setObjectName("GroupTitleLabel")
        title_label.setStyleSheet("font-weight: bold; font-size: 16px; background: transparent; color: #333;")
        title_layout.addWidget(title_label)
        title_layout.addStretch()

        self.step_search_input = QLineEdit()
        self.step_search_input.setPlaceholderText("搜索具体步骤或文案描述一键生成步骤...")
        self.step_search_input.setFixedHeight(20)
        self.step_search_input.setStyleSheet("""
            QLineEdit {
                background: white;
                border: 1px solid #d0d0d0;
                border-radius: 4px;
                padding: 2px 8px;
                color: #333;
            }
        """)
        self.step_search_input.textChanged.connect(self._on_step_search)
        title_layout.addWidget(self.step_search_input)

        self.step_generate_btn = QPushButton("生成")
        self.step_generate_btn.setFixedHeight(20)
        self.step_generate_btn.setEnabled(False)
        self.step_generate_btn.setStyleSheet("""
            QPushButton {
                background: #1976d2;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 0 12px;
                font-weight: 500;
            }
            QPushButton:hover { background: #1565c0; }
            QPushButton:disabled { background: #b0b0b0; color: #e0e0e0; }
        """)
        self.step_generate_btn.clicked.connect(self._on_step_generate)
        title_layout.addWidget(self.step_generate_btn)

        self.record_btn = QPushButton()
        self.record_btn.setFixedSize(20, 20)
        self.record_btn.setEnabled(False)
        self.record_btn.setIcon(qta.icon('fa6s.circle', color='#888888'))
        self.record_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                padding: 0px;
            }
        """)
        title_layout.addWidget(self.record_btn)

        step_layout.addLayout(title_layout)
        step_layout.addWidget(step_list)

        action_group = create_group_with_title("动作卡片", "填写参数后点击添加按钮", action_card)
        action_group.setObjectName("EditActionGroup")
        splitter.addWidget(project_group)
        splitter.addWidget(step_group)
        splitter.addWidget(action_group)
        splitter.setSizes([200, 400, 400])

        layout.addWidget(splitter)
        old = self.stacked_widget.widget(1)
        self.stacked_widget.removeWidget(old)
        old.deleteLater()
        self.stacked_widget.insertWidget(1, container)

        self.register_sub_view(project_tree)
        self.register_sub_view(step_list)
        self.register_sub_view(action_card)
        self.register_sub_view(container)

        if self.step_controller:
            self.record_btn.clicked.connect(self.step_controller.toggle_recording)
            self._update_record_button_state()

        self.apply_theme()

    def set_visualize_view(self, widget):
        if self._visualize_container is None:
            container = QWidget()
            container.setObjectName("VisualizeContainer")
            # 让 QSS 的 background/border-radius 生效
            container.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            # 可视化面板不透明，遮挡壁纸；带圆角
            if Settings.get_theme_mode() == THEME_MODE_DARK:
                container.setStyleSheet(
                    "#VisualizeContainer { background-color: #2c2c2c; }"
                )
            else:
                container.setStyleSheet(
                    "#VisualizeContainer { background-color: #e9eaee; }"
                )
            layout = QVBoxLayout(container)
            layout.setContentsMargins(0, 0, 0, 0)
            self._visualize_container = container
            self.visualize_button = QPushButton("启动 weditor")
            self.visualize_button.setIcon(qta.icon('fa6s.eye', color='white'))
            self.visualize_button.setStyleSheet("""
                QPushButton {
                    background: #1976d2;
                    color: white;
                    border: none;
                    padding: 20px 40px;
                    border-radius: 8px;
                    font-size: 16px;
                    font-weight: 600;
                }
                QPushButton:hover { background: #1565c0; }
            """)
            self.visualize_button.clicked.connect(self._start_weditor)
            layout.addWidget(self.visualize_button, alignment=Qt.AlignmentFlag.AlignCenter)
        else:
            layout = self._visualize_container.layout()
            while layout.count():
                item = layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            layout.addWidget(widget,
                             alignment=Qt.AlignmentFlag.AlignCenter if isinstance(widget, QPushButton) else None)

        # 关键改动：把可视化 container 塞进 Dock，而不是 stacked_widget
        if self.visualize_dock is not None:
            dock_content = self.visualize_dock.widget()
            if dock_content is None:
                dock_content = QWidget()
                dock_layout = QVBoxLayout(dock_content)
                dock_layout.setContentsMargins(0, 0, 0, 0)
                self.visualize_dock.setWidget(dock_content)
            dock_layout = dock_content.layout()
            # 清空 dock 里旧内容（但不要 deleteLater，因为 container 还要复用）
            while dock_layout.count():
                item = dock_layout.takeAt(0)
                w = item.widget()
                if w is not None:
                    w.setParent(None)
            dock_layout.addWidget(self._visualize_container)
            self._visualize_container.show()

        self.register_sub_view(self._visualize_container)
        self.apply_theme()

    def set_execute_view_with_logs(self, execute_view, logs_view):
        execute_view.setObjectName("ExecuteView")
        logs_view.setObjectName("LogsView")

        container = QWidget()
        container.setObjectName("ExecutePageContainer")
        container.setStyleSheet("#ExecutePageContainer { background: transparent; }")
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setStyleSheet("""
            QSplitter {
                background: transparent;
                border: none;
            }
            QSplitter::handle {
                background: transparent;
                border: none;
                width: 4px;
            }
            QSplitter::handle:hover {
                background: transparent;
            }
            QSplitter::handle:pressed {
                background: transparent;
            }
        """)
        splitter.setHandleWidth(4)
        splitter.setChildrenCollapsible(False)

        splitter.addWidget(logs_view)
        splitter.addWidget(execute_view)

        task_frame = QFrame()
        task_frame.setObjectName("TaskFrame")
        task_frame_layout = QVBoxLayout(task_frame)
        task_frame_layout.setContentsMargins(0, 0, 0, 0)

        task_view = execute_view.get_task_view()
        if task_view:
            task_view.setObjectName("TaskView")
            task_frame_layout.addWidget(task_view)
            self.register_sub_view(task_view)
        else:
            placeholder = QLabel("未加载定时任务")
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            placeholder.setStyleSheet("color: #999; background: transparent;")
            task_frame_layout.addWidget(placeholder)

        splitter.addWidget(task_frame)

        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 1)
        splitter.setChildrenCollapsible(False)

        layout.addWidget(splitter)
        old = self.stacked_widget.widget(3)
        self.stacked_widget.removeWidget(old)
        old.deleteLater()
        self.stacked_widget.insertWidget(3, container)

        # 保存引用，供 apply_theme 统一处理壁纸适配
        self._logs_view_ref = logs_view
        self._execute_view_ref = execute_view
        self._task_frame_ref = task_frame

        self.register_sub_view(execute_view)
        self.register_sub_view(logs_view)
        self.register_sub_view(container)
        self.apply_theme()

    def _apply_execute_page_theme(self, has_wallpaper, is_dark):
        """执行页三面板的背景色
        参考项目管理区域：无壁纸纯色，有壁纸 transparent（由 centralWidget 罩层透出）；
        内部视图有壁纸时用半透明色。"""
        if is_dark:
            # 有壁纸时面板透明，让 centralWidget 罩层 + 壁纸透出
            panel_bg = "transparent" if has_wallpaper else "#191a1c"
            panel_border = "#555"
            text_color = "#eee"
            # 内部视图背景
            inner_view_bg = "rgba(55, 55, 55, 0.85)" if has_wallpaper else "#373737"
            # 滚动条配色（与项目树一致）
            sb_bg = "rgba(58, 58, 58, 0.5)"
            sb_handle = "#666"
        else:
            panel_bg = "transparent" if has_wallpaper else "#ffffff"
            panel_border = "#d0d0d0"
            text_color = "#333"
            inner_view_bg = "rgba(232, 234, 237, 0.85)" if has_wallpaper else "#e8eaed"
            sb_bg = "#e0e0e0"
            sb_handle = "#c0c0c0"

        # 日志面板
        if self._logs_view_ref is not None:
            self._logs_view_ref.setStyleSheet(f"""
                #LogsView {{
                    background-color: {panel_bg};
                    border: 1px solid {panel_border};
                    border-radius: 12px;
                }}
                #LogsView QTextEdit {{
                    padding: 4px;
                    border: none;
                    background-color: transparent;
                    color: {text_color};
                    font-family: Consolas, monospace;
                    font-size: 10pt;
                }}
                #LogsView QTextEdit QScrollBar:vertical {{
                    width: 6px;
                    background: {sb_bg};
                    border-radius: 3px;
                    margin: 0px;
                }}
                #LogsView QTextEdit QScrollBar::handle:vertical {{
                    background: {sb_handle};
                    border-radius: 3px;
                    min-height: 20px;
                }}
                #LogsView QTextEdit QScrollBar::add-line:vertical,
                #LogsView QTextEdit QScrollBar::sub-line:vertical {{
                    height: 0px;
                    width: 0px;
                }}
                #LogsView QTextEdit QScrollBar::add-page:vertical,
                #LogsView QTextEdit QScrollBar::sub-page:vertical {{
                    background: transparent;
                }}
                #LogsView QLabel {{
                    color: {text_color};
                    background: transparent;
                }}
            """)

        # 执行面板
        if self._execute_view_ref is not None:
            selected_bg = '#1e3a5f' if is_dark else '#d0e4f7'
            selected_fg = '#ffffff' if is_dark else '#1a1a1a'
            hover_bg = 'rgba(74, 74, 74, 0.6)' if is_dark else '#dfe2e6'
            self._execute_view_ref.setStyleSheet(f"""
                #ExecuteView {{
                    background-color: {panel_bg};
                    border: 1px solid {panel_border};
                    border-radius: 12px;
                    padding: 8px;
                }}
                #ExecuteView QTreeView {{
                    padding: 4px;
                    margin: 10px;
                    border: 1px solid {panel_border};
                    border-radius: 4px;
                    background-color: {inner_view_bg};
                    outline: none;
                    /* 关键：禁用 Qt 用 palette.Highlight 画选中/branch 背景 */
                    selection-background-color: transparent;
                    selection-color: transparent;
                }}
                #ExecuteView QTreeView::item {{
                    height: 30px;
                    color: {text_color};
                    border: none;
                    outline: none;
                }}
                #ExecuteView QTreeView::item:selected,
                #ExecuteView QTreeView::item:selected:active,
                #ExecuteView QTreeView::item:selected:!active,
                #ExecuteView QTreeView::item:selected:focus {{
                    background-color: {selected_bg};
                    color: {selected_fg};
                    border: none;
                    outline: none;
                }}
                #ExecuteView QTreeView::item:hover:!selected {{
                    background-color: {hover_bg};
                }}
                #ExecuteView QTreeView::branch {{
                    background: transparent;
                    border: none;
                }}
                #ExecuteView QTreeView::branch:selected,
                #ExecuteView QTreeView::branch:selected:active,
                #ExecuteView QTreeView::branch:selected:!active {{
                    background: transparent;
                    border: none;
                }}
                /* 滚动条（与项目树一致） */
                #ExecuteView QTreeView QScrollBar:vertical {{
                    width: 6px;
                    background: {sb_bg};
                    border-radius: 3px;
                    margin: 0px;
                }}
                #ExecuteView QTreeView QScrollBar::handle:vertical {{
                    background: {sb_handle};
                    border-radius: 3px;
                    min-height: 20px;
                }}
                #ExecuteView QTreeView QScrollBar::add-line:vertical,
                #ExecuteView QTreeView QScrollBar::sub-line:vertical {{
                    height: 0px;
                    width: 0px;
                }}
                #ExecuteView QTreeView QScrollBar::add-page:vertical,
                #ExecuteView QTreeView QScrollBar::sub-page:vertical {{
                    background: transparent;
                }}
                #ExecuteView QLabel {{
                    color: {text_color};
                    background: transparent;
                }}
            """)

        # 定时任务面板：外层 TaskFrame 透明，让 TaskView 自己的 QSS 生效
        if self._task_frame_ref is not None:
            self._task_frame_ref.setStyleSheet(
                f" #TaskFrame {{ background: {panel_bg}; "
                f"border: 1px solid {panel_border}; border-radius: 12px; }}"
            )
            # palette 层面：
            # - Base 给复选框用（半透明白/深灰）
            # - Highlight 设透明，彻底干掉 branch 左侧那条纯蓝竖条
            from PyQt6.QtGui import QPalette, QColor
            tree_view = getattr(self._execute_view_ref, 'tree_view', None)
            if tree_view is not None:
                pal = tree_view.palette()
                if is_dark:
                    if has_wallpaper:
                        base_color = QColor(60, 60, 60, 200)
                    else:
                        base_color = QColor(60, 60, 60)
                else:
                    if has_wallpaper:
                        base_color = QColor(255, 255, 255, 220)
                    else:
                        base_color = QColor(255, 255, 255)

                # 复选框背景
                pal.setColor(QPalette.ColorRole.Base, base_color)
                # 关键：把 Highlight 设成全透明，branch 就不会画蓝条
                pal.setColor(QPalette.ColorRole.Highlight, QColor(0, 0, 0, 0))
                pal.setColor(QPalette.ColorRole.HighlightedText, QColor(0, 0, 0, 0))

                tree_view.setPalette(pal)
                vp = tree_view.viewport()
                if vp is not None:
                    vp.setPalette(pal)

    def _apply_bottom_log_theme(self, is_dark, has_wallpaper=False):
        """捕虫师日志面板：圆角 + 主题背景（兼容壁纸）"""
        if self.bottom_placeholder is None:
            return

        if is_dark:
            bg = "transparent" if has_wallpaper else "#191a1c"
            border = "rgba(136, 136, 136, 0.9)" if has_wallpaper else "#4a4a4a"
            text = "#eeeeee"
        else:
            bg = "transparent" if has_wallpaper else "#ffffff"
            border = "rgba(176, 176, 176, 0.9)" if has_wallpaper else "#d0d0d0"
            text = "#333333"

        self.bottom_placeholder.setStyleSheet(f"""
            #BottomLogContainer {{
                background-color: {bg};
                border: 1px solid {border};
                border-radius: 8px;
            }}
            #BottomLogContent {{
                background: transparent;
                color: {text};
                font-size: 16px;
            }}
        """)

    def set_element_manager_view(self, element_manager):
        element_manager.setObjectName("ElementManagerView")
        # 外层容器：带圆角和边框（背景色由 apply_theme 根据主题动态设置）
        container = QWidget()
        container.setObjectName("ElementManagerContainer")
        container.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        container.setStyleSheet("""
            #ElementManagerContainer {
                background: #ffffff;
                border: 1px solid #d0d0d0;
                border-radius: 8px;
            }
        """)
        container_layout = QVBoxLayout(container)
        # 内边距 1px，避免表格内容贴到圆角边缘被裁
        container_layout.setContentsMargins(1, 1, 1, 1)

        # 内层容器：透明，包裹 element_manager
        inner_container = QWidget()
        inner_container.setObjectName("ElementManagerInner")
        inner_container.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        inner_container.setStyleSheet(
            "#ElementManagerInner { background: transparent; }"
        )
        inner_layout = QVBoxLayout(inner_container)
        inner_layout.setContentsMargins(0, 0, 0, 0)
        inner_layout.addWidget(element_manager)

        container_layout.addWidget(inner_container)

        self._element_container = container

        old = self.stacked_widget.widget(4)
        self.stacked_widget.removeWidget(old)
        old.deleteLater()
        self.stacked_widget.insertWidget(4, container)

        self.register_sub_view(element_manager)
        self.register_sub_view(container)
        self.apply_theme()

    def set_adb_toolbox_view(self, toolbox_widget):
        container = QWidget()
        container.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(toolbox_widget)
        old = self.stacked_widget.widget(0)
        self.stacked_widget.removeWidget(old)
        old.deleteLater()
        self.stacked_widget.insertWidget(0, container)

        self.register_sub_view(container)
        self.apply_theme()

    def set_adb_toolbox_controller(self, controller):
        """挂载 ADB 工具箱控制器，并连接弱网信号到横幅"""
        self._adb_toolbox_controller = controller
        if controller is not None:
            controller.weak_network_changed.connect(self._on_weak_network_changed)

    def _on_weak_network_changed(self, active: bool):
        """弱网状态变化：显示/隐藏主界面顶部横幅"""
        if self.weak_network_banner is not None:
            self.weak_network_banner.setVisible(active)

    def set_help_view(self, help_view):
        help_view.setObjectName("HelpView")
        # 创建透明无边框的外层容器，仅用于包裹 help_view，不会显示任何边框或背景
        container = QFrame()
        container.setObjectName("HelpContainer")
        container.setStyleSheet("QFrame#HelpContainer { background-color: transparent; border: none; }")
        # 设置布局，边距为0，让 help_view 紧贴容器
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(help_view)

        old = self.stacked_widget.widget(5)
        self.stacked_widget.removeWidget(old)
        old.deleteLater()
        self.stacked_widget.insertWidget(5, container)
        # 注册 help_view 以便主题切换时能更新其内部样式
        self.register_sub_view(help_view)
        # 显式应用主题，确保 help_view 内部两个容器获得正确的边框样式
        self.apply_theme()
    # ---------- Weditor ----------
    def set_weditor_service(self, service):
        self.weditor_service = service

    def _start_weditor(self):
        if not self.weditor_service:
            WarningDialog.show_warning(self, "提示", "weditor 服务未初始化")
            return

        # 避免重复启动
        if self.thread is not None and self.thread.isRunning():
            return

        # 立即清空容器内容（去掉"启动 weditor"按钮），后续由 web_view 填充
        if self._visualize_container is not None:
            try:
                layout = self._visualize_container.layout()
                while layout.count():
                    item = layout.takeAt(0)
                    if item.widget():
                        item.widget().deleteLater()
            except RuntimeError:
                pass
        # 清空 Python 侧引用（避免后续误用已删除的 C++ 对象）
        self.visualize_button = None

        # 用 Toast 提示替代容器内文字（容器保持不变，等加载完成直接切换）
        show_toast(
            self.visualize_dock,
            "正在启动 weditor，请稍候...",
            duration=2500
        )

        QApplication.processEvents()

        self.thread = QThread()
        self.worker = WeditorWorker(self.weditor_service)
        self.worker.moveToThread(self.thread)

        self.worker.success.connect(self._on_weditor_success)
        self.worker.error.connect(self._on_weditor_error)
        self.thread.started.connect(self.worker.run)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.start()

    def _set_container_message(self, text, color="#888"):
        """在可视化容器里显示一条提示文字（清空已有内容）"""
        if self._visualize_container is None:
            return
        try:
            layout = self._visualize_container.layout()
            while layout.count():
                item = layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            label = QLabel(text)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setStyleSheet(
                f"color: {color}; font-size: 14px; background: transparent;"
            )
            layout.addWidget(label)
        except RuntimeError:
            pass

    def _on_weditor_success(self, port):
        self._cleanup_thread()

        try:
            web_view = QWebEngineView()
            web_view.setParent(self._visualize_container)
            web_view.hide()

            def on_loaded(ok):
                if not ok:
                    return
                try:
                    if self._visualize_container:
                        layout = self._visualize_container.layout()
                        while layout.count():
                            item = layout.takeAt(0)
                            if item.widget():
                                item.widget().deleteLater()
                        web_view.setParent(self._visualize_container)
                        layout.addWidget(web_view)
                        web_view.show()
                        self._visualize_container.update()
                except RuntimeError:
                    pass

            web_view.loadFinished.connect(on_loaded)
            web_view.load(QUrl(f"http://localhost:{port}"))
        except Exception as e:
            self._set_container_message(f"加载 weditor 失败: {e}", color="red")

    def _on_weditor_error(self, err_msg):
        self._cleanup_thread()
        self._set_container_message(f"weditor 启动失败: {err_msg}", color="red")

    def _cleanup_thread(self):
        if self.thread and self.thread.isRunning():
            self.thread.quit()
            self.thread.wait()
        self.thread = None
        self.worker = None

    # ---------- 设备 ----------
    def set_device_service(self, service):
        self.device_service = service

    def update_device_list(self, devices):
        self.device_combo.clear()
        if not devices:
            self.device_combo.addItem("未检测到设备")
            self.device_status_label.setText("○ 未连接设备")
            self.device_status_label.setStyleSheet("padding: 2px 8px; color: #c0392b; background: transparent;")
        else:
            for dev in devices:
                self.device_combo.addItem(dev)
            if devices:
                self.device_status_label.setText(f"● 设备已连接: {devices[0]}")
                self.device_status_label.setStyleSheet("padding: 2px 8px; color: #27ae60; background: transparent;")

    # ---------- 菜单操作 ----------
    def _on_menu_action(self, action):
        if action == "import_cases":
            file_path, _ = QFileDialog.getOpenFileName(self, "导入用例", "", "JSON Files (*.json)")
            if file_path:
                self._import_cases(file_path)
        elif action == "export_cases":
            if not self.project_model or not self.step_model:
                WarningDialog.show_warning(self, "提示", "模型未初始化，无法导出")
                return
            file_path, _ = QFileDialog.getSaveFileName(
                self,
                "导出用例",
                "cases_export.json",
                "JSON Files (*.json)"
            )
            if not file_path:
                return
            try:
                export_data = {
                    "project": [node.to_dict() for node in self.project_model.root_nodes],
                    "steps": {
                        "id_counter": self.step_model._id_counter,
                        "case_steps": self.step_model.case_steps,
                        "steps": [step.to_dict() for step in self.step_model._steps.values()]
                    }
                }
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(export_data, f, ensure_ascii=False, indent=2)
                show_toast(message="导出用例成功")
            except Exception as e:
                ErrorDialog.show_error(self, "导出失败", f"导出用例时发生错误:\n{str(e)}")
        elif action == "about":
            self._show_about_dialog()
        elif action == "settings":
            self.on_settings()

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.ToolTip:
            if hasattr(self, 'left_toolbar_buttons') and obj in self.left_toolbar_buttons:
                pos = event.globalPos()
                tooltip_text = obj.toolTip()
                if tooltip_text:
                    QToolTip.showText(pos + QPoint(21, -30), tooltip_text, obj)
                    return True
            elif hasattr(self, 'right_toolbar_buttons') and obj in self.right_toolbar_buttons:
                pos = event.globalPos()
                tooltip_text = obj.toolTip()
                if tooltip_text:
                    QToolTip.showText(pos - QPoint(21, 25), tooltip_text, obj)
                    return True
        return super().eventFilter(obj, event)

    def _import_cases(self, file_path):
        if not self.project_model or not self.step_model:
            WarningDialog.show_warning(self, "提示", "模型未初始化，无法导入")
            return
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if 'project' not in data or 'steps' not in data:
                raise ValueError("无效的文件格式")
            imported_projects = data['project']
            imported_steps_data = data['steps']
            imported_case_steps = imported_steps_data.get('case_steps', {})
            old_to_new_id = {}
            max_id = self.step_model._id_counter
            for step_dict in imported_steps_data.get('steps', []):
                new_id = max_id
                max_id += 1
                step = Step(
                    id=new_id,
                    type=step_dict['type'],
                    name=step_dict['name'],
                    params=step_dict.get('params', {})
                )
                self.step_model._steps[new_id] = step
                old_to_new_id[step_dict['id']] = new_id
            self.step_model._id_counter = max_id
            imported_case_count = 0
            skipped_case_count = 0

            def find_child(parent, name, node_type):
                for child in parent.children:
                    if child.name == name and child.type == node_type:
                        return child
                return None

            def import_node(node_data, parent_node):
                nonlocal imported_case_count, skipped_case_count
                node_type = node_data['type']
                node_name = node_data['name']
                if parent_node is None:
                    existing = None
                    for root in self.project_model.root_nodes:
                        if root.name == node_name and root.type == node_type:
                            existing = root
                            break
                    if existing:
                        for child_data in node_data.get('children', []):
                            import_node(child_data, existing)
                    else:
                        new_node = TreeNode(
                            id=f"proj_{id(node_name)}",
                            name=node_name,
                            type=node_type,
                            children=[],
                            expanded=node_data.get('expanded', True),
                            selected=False
                        )
                        self.project_model.root_nodes.append(new_node)
                        for child_data in node_data.get('children', []):
                            import_node(child_data, new_node)
                else:
                    existing = find_child(parent_node, node_name, node_type)
                    if existing:
                        if node_type == 'case':
                            skipped_case_count += 1
                            return
                        for child_data in node_data.get('children', []):
                            import_node(child_data, existing)
                    else:
                        if node_type == 'case':
                            imported_case_count += 1
                            new_node = TreeNode(
                                id=f"case_{id(node_name)}",
                                name=node_name,
                                type='case',
                                children=[],
                                expanded=True,
                                selected=False
                            )
                            parent_node.children.append(new_node)
                            old_case_id = node_data.get('id')
                            if old_case_id and old_case_id in imported_case_steps:
                                old_step_ids = imported_case_steps[old_case_id]
                                new_step_ids = []
                                for old_sid in old_step_ids:
                                    if old_sid in old_to_new_id:
                                        new_step_ids.append(old_to_new_id[old_sid])
                                if new_step_ids:
                                    self.step_model.case_steps[new_node.id] = new_step_ids
                            for child_data in node_data.get('children', []):
                                import_node(child_data, new_node)
                        else:
                            new_node = TreeNode(
                                id=f"folder_{id(node_name)}",
                                name=node_name,
                                type='folder',
                                children=[],
                                expanded=node_data.get('expanded', True),
                                selected=False
                            )
                            parent_node.children.append(new_node)
                            for child_data in node_data.get('children', []):
                                import_node(child_data, new_node)

            for project_data in imported_projects:
                import_node(project_data, None)

            self.project_model.save()
            self.step_model.save()

            if self.project_controller:
                self.project_controller.view.refresh()
                if self.project_controller.execute_view:
                    self.project_controller.execute_view.refresh()
            if self.step_controller:
                self.step_controller.set_current_case(None)
                self.step_controller.refresh_steps()

            show_toast(message="导入成功")
        except ValueError as e:
            ErrorDialog.show_error(self, "导入失败", str(e))
        except json.JSONDecodeError as e:
            ErrorDialog.show_error(self, "导入失败", f"JSON 解析错误:\n{str(e)}")
        except Exception as e:
            ErrorDialog.show_error(self, "导入失败", f"导入用例时发生错误:\n{str(e)}")

    def _on_step_search(self, text):
        if hasattr(self, 'step_list') and self.step_list:
            if text is None or text.strip() == '':
                if hasattr(self.step_list, '_original_steps') and self.step_list._original_steps:
                    self.step_list.set_steps(self.step_list._original_steps)
                else:
                    self.step_list.set_steps(self.step_list._all_steps)
            else:
                self.step_list.filter_steps(text)

    def _on_step_generate(self):
        if not self.step_controller or not self.step_controller.current_case_id:
            return
        text = self.step_search_input.text().strip()
        if not text:
            show_toast(message="请输入操作描述")
            return
        self.step_controller.generate_steps_from_text(text)
        self.step_search_input.clear()

    def set_perf_view(self, perf_view):
        """挂载性能检测视图到 index 8"""
        perf_view.setObjectName("PerfView")
        # 外层包裹：圆角边框
        container = QFrame()
        container.setObjectName("PerfContainer")
        container.setStyleSheet(
            "QFrame#PerfContainer { background: transparent; border: none; }"
        )
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(perf_view)

        old = self.stacked_widget.widget(8)
        self.stacked_widget.removeWidget(old)
        old.deleteLater()
        self.stacked_widget.insertWidget(8, container)
        self.register_sub_view(perf_view)
        self._perf_view = perf_view
        self.apply_theme()

    def _show_about_dialog(self):
        from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                                     QPushButton, QFrame, QApplication)
        from PyQt6.QtCore import Qt as _Qt

        dialog = QDialog(self)
        dialog.setObjectName("AboutDialog")
        dialog.setWindowTitle("关于 虫师")
        dialog.setFixedSize(520, 320)
        dialog.setModal(True)

        # 主题判断
        is_dark = (Settings.get_theme_mode() == THEME_MODE_DARK)

        if is_dark:
            bg = "#3c3c3c"
            border = "#555"
            title_color = "#ffffff"
            body_color = "#dddddd"
            dim_color = "#999999"
            close_bg = "#555"
            close_fg = "#eeeeee"
            close_hover = "#666666"
        else:
            bg = "#ffffff"
            border = "#d0d0d0"
            title_color = "#1a1a1a"
            body_color = "#555555"
            dim_color = "#999999"
            close_bg = "#f0f0f0"
            close_fg = "#333333"
            close_hover = "#e0e0e0"

        dialog.setStyleSheet(f"""
            #AboutDialog {{
                background-color: {bg};
            }}
            #AboutDialog QLabel {{
                background: transparent;
                color: {body_color};
                font-size: 13px;
            }}
            #AboutDialog QLabel#AboutTitle {{
                color: {title_color};
                font-size: 22px;
                font-weight: bold;
            }}
            #AboutDialog QLabel#AboutSubtitle {{
                color: {dim_color};
                font-size: 12px;
            }}
            #AboutDialog QFrame#AboutDivider {{
                background-color: {border};
                max-height: 1px;
            }}
            #AboutDialog QPushButton#AboutCopyBtn {{
                background-color: #1976d2;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 6px 18px;
                font-size: 13px;
                font-weight: 500;
            }}
            #AboutDialog QPushButton#AboutCopyBtn:hover {{
                background-color: #1565c0;
            }}
            #AboutDialog QPushButton#AboutCloseBtn {{
                background-color: {close_bg};
                color: {close_fg};
                border: none;
                border-radius: 6px;
                padding: 6px 18px;
                font-size: 13px;
                font-weight: 500;
            }}
            #AboutDialog QPushButton#AboutCloseBtn:hover {{
                background-color: {close_hover};
            }}
        """)

        root = QVBoxLayout(dialog)
        root.setContentsMargins(28, 24, 28, 20)
        root.setSpacing(14)

        # ---------- 顶部：图标 + 信息 ----------
        top = QHBoxLayout()
        top.setSpacing(20)

        icon_label = QLabel()
        icon_label.setFixedSize(64, 64)
        icon_label.setAlignment(_Qt.AlignmentFlag.AlignCenter)
        icon = self.windowIcon()
        if not icon.isNull():
            icon_label.setPixmap(icon.pixmap(64, 64))
        top.addWidget(icon_label, alignment=_Qt.AlignmentFlag.AlignTop)

        info_v = QVBoxLayout()
        info_v.setSpacing(4)

        title = QLabel("虫师")
        title.setObjectName("AboutTitle")
        info_v.addWidget(title)

        sub = QLabel("V1.0.1 · Build 2026-09-12")
        sub.setObjectName("AboutSubtitle")
        info_v.addWidget(sub)

        info_v.addSpacing(10)

        info_v.addWidget(QLabel("作者：李金钊"))
        info_v.addWidget(QLabel("技术栈：Python 3.13 · PyQt6 · uiautomator2"))
        info_v.addWidget(QLabel("weditor · qtawesome"))

        info_v.addStretch()
        top.addLayout(info_v, 1)

        root.addLayout(top)
        root.addSpacing(6)

        # ---------- 分隔线 ----------
        divider = QFrame()
        divider.setObjectName("AboutDivider")
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setFixedHeight(1)
        root.addWidget(divider)

        # ---------- 版权 ----------
        copyright_label = QLabel("© 2026 李金钊 · 虫师团队 · 让自动化触手可及")
        copyright_label.setObjectName("AboutSubtitle")
        copyright_label.setAlignment(_Qt.AlignmentFlag.AlignCenter)
        root.addWidget(copyright_label)

        # ---------- 底部按钮 ----------
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        copy_btn = QPushButton("复制并关闭")
        copy_btn.setObjectName("AboutCopyBtn")
        copy_btn.setFixedHeight(34)
        copy_btn.setMinimumWidth(110)
        btn_layout.addWidget(copy_btn)

        close_btn = QPushButton("关闭")
        close_btn.setObjectName("AboutCloseBtn")
        close_btn.setFixedHeight(34)
        close_btn.setMinimumWidth(100)
        close_btn.clicked.connect(dialog.accept)
        btn_layout.addWidget(close_btn)

        root.addLayout(btn_layout)

        # ---------- 复制并关闭逻辑 ----------
        def _copy_and_close():
            info = (
                "虫师\n"
                "版本：V1.0.1 (Build: 2026-09-12 23:26:35)\n"
                "作者：李金钊\n"
                "技术栈：Python 3.13 · PyQt6 · uiautomator2 · weditor · qtawesome\n"
                "© 2026 李金钊 · 虫师团队"
            )
            QApplication.clipboard().setText(info)
            show_toast(dialog, "信息已复制到剪贴板", duration=1500)
            dialog.accept()

        copy_btn.clicked.connect(_copy_and_close)

        dialog.exec()