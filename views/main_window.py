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
from PyQt6.QtGui import (
    QAction, QColor, QFont, QIcon, QPainter, QPixmap, QPainterPath, QRegion,
    QShortcut, QKeySequence,
)
from PyQt6.QtCore import Qt, pyqtSignal, QThread, QObject, QUrl, QSize, QPoint, QEvent, QRectF, QTimer
from PyQt6.QtWebEngineWidgets import QWebEngineView

from models.project_model import TreeNode, new_node_id
from models.step_model import Step
from utils.settings import Settings, THEME_MODE_SYSTEM, THEME_MODE_LIGHT, THEME_MODE_DARK
from utils.version import APP_VERSION, BUILD_DATE
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
    # 菜单里点了「检查更新」（含启动时的自动检查由 main.py 触发）。
    # 本类不碰网络：只发信号，谁接谁去查。
    check_update_requested = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setObjectName("MainWindow")
        self.setWindowTitle("虫师")
        self.device_service = None
        self.weditor_service = None
        self.visualize_button = None
        self.project_model = None
        self.step_model = None
        self.data_cleanup_handler = None    # 「清理无用数据」入口，由 main.py 注入
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
        self.msg_action = None
        self._notification_service = None
        self._notification_view = None
        self._bottom_stack = None
        self.device_action = None
        self.crash_action = None
        self.anr_action = None
        self._bottom_panel_kind = "log"          # 底部面板当前展示：log / crash / anr
        self._bottom_panel_contents = {}         # cache：Crash / ANR 拉取结果
        self._bottom_panel_actions = {}  # kind -> QAction
        self.help_action = None
        self.device_status_label = None
        self.nav_actions = []
        self.nav_icons = []
        self.nav_indices = []
        self.visualize_widget = None
        self._visualize_container = None
        self.visualize_dock = None
        self._perf_view = None
        self._voice_view = None
        self._adb_toolbox_controller = None
        self._element_container = None
        self._logs_view_ref = None
        self._execute_view_ref = None
        self._task_frame_ref = None
        self.main_menu = None
        self._main_menu_actions = {}
        self._project_menu_btn = None
        self._project_menu = None
        # 「动作卡片 ▾」下拉入口（菜单里「展示动作」打开勾选对话框）
        self._action_cards_btn = None
        self._action_cards_menu = None
        self.action_card = None
        self._welcome_tip_rows = []
        self._sub_views = []
        self.left_toolbar_buttons = []
        self.right_toolbar_buttons = []
        self.setup_ui()
        self.setup_wallpaper()
        self.load_wallpaper()
        self.setup_tab_icons()

        self.switch_view(self.WELCOME_PAGE_INDEX)
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

    # ------------------------------------------------------------------
    # 「动作卡片 ▾」：勾选卡片区显示哪几张卡片
    # ------------------------------------------------------------------
    def _on_show_action_cards_dialog(self):
        """打开「展示卡片」对话框（观感与「指令管理 → 展示指令」同一套）

        卡片清单与顺序都取自 ActionCardView（就是卡片区的展示顺序），不写死任何
        卡片类型 —— 以后加卡片，对话框自动多一项。取消/关闭 = 不改动。
        """
        if self.action_card is None:
            return
        from views.dialogs.select_action_cards_dialog import SelectActionCardsDialog

        dlg = SelectActionCardsDialog(
            self.action_card.card_titles(),
            self.action_card.visible_card_types(),
            self,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.action_card.set_visible_card_types(dlg.get_selected_types())

    def _apply_action_cards_menu_theme(self, is_dark):
        """「动作卡片 ▾」按钮与菜单的主题。

        取值与「项目管理 ▾」「指令管理 ▾」两处完全相同（那两处的注释也互相指向），
        改一处要同步另外两处。
        """
        if self._action_cards_btn is None:
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

        # 按钮样式：与「项目管理 ▾」一致——透明背景、加粗 16px、去掉 Qt 默认小三角
        self._action_cards_btn.setStyleSheet(f"""
            QToolButton#ActionCardsMenuBtn {{
                background: transparent;
                border: none;
                color: {btn_color};
                font-weight: bold;
                font-size: 16px;
                padding: 0px 0px;
            }}
            QToolButton#ActionCardsMenuBtn::menu-indicator {{
                image: none;
                width: 0px;
                height: 0px;
            }}
        """)

        if self._action_cards_menu is not None:
            # 关键：去掉系统窗口装饰 + 允许透明背景，让 QSS 的圆角四角真正生效
            self._action_cards_menu.setWindowFlags(
                Qt.WindowType.Popup
                | Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.NoDropShadowWindowHint
            )
            self._action_cards_menu.setAttribute(
                Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self._action_cards_menu.setStyleSheet(f"""
                QMenu#ActionCardsMenu {{
                    background-color: {menu_bg};
                    border: 1px solid {menu_border};
                    border-radius: 8px;
                    padding: 6px;
                }}
                QMenu#ActionCardsMenu::item {{
                    background: transparent;
                    padding: 7px 28px 7px 32px;
                    margin: 1px 2px;
                    border-radius: 5px;
                    color: {menu_text};
                    font-size: 13px;
                }}
                QMenu#ActionCardsMenu::item:selected {{
                    background-color: {menu_hover_bg};
                    color: {menu_hover_text};
                }}
                QMenu#ActionCardsMenu::separator {{
                    height: 1px;
                    background: {sep_color};
                    margin: 4px 8px;
                }}
                QMenu#ActionCardsMenu::icon {{
                    left: 10px;
                }}
            """)

            # 图标颜色跟主题走（qta 出的是位图，QSS 改不了）
            for act in self._action_cards_menu.actions():
                if act.text() == "展示动作":
                    act.setIcon(qta.icon('fa6s.eye', color=icon_color))

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

    def set_data_cleanup_handler(self, handler):
        """注入「清理无用数据」的执行入口（main.py 注册，设置页的数据维护页用它）。

        处理器由调用方提供、签名 () -> dict|None，返回回收统计（None = 当前不能清理）。
        """
        self.data_cleanup_handler = handler

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
                /* 清掉通用 QPushButton 的 0 12px 内边距，否则悬浮高亮框会被撑宽 */
                padding: 0px;
                min-width: 28px;
                max-width: 28px;
                min-height: 28px;
                max-height: 28px;
            }
            QToolBar QPushButton#menuBtn:hover { background: rgba(0,0,0,0.05); border-radius: 3px; }
            QToolBar QPushButton#toolIconBtn {
                background: transparent;
                border: none;
                padding: 0px;
                min-width: 28px;
                max-width: 28px;
                min-height: 28px;
                max-height: 28px;
            }
            QToolBar QPushButton#toolIconBtn:hover { background: rgba(0,0,0,0.05); border-radius: 3px; }
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

        # ---------- 顶栏快捷功能按钮（纯图标，不显示文案）----------
        # 只有图标，用途靠 tooltip 说明，所以 tooltip 里带上对应快捷键
        _shortcuts = Settings.get_shortcuts()

        def _quick_icon_btn(icon_name, action_key, shortcut_key, tip):
            key_seq = _shortcuts.get(shortcut_key) or ""
            btn = QPushButton()
            btn.setObjectName("toolIconBtn")
            btn.setIcon(qta.icon(icon_name, color='#a3a6b0'))
            btn.setToolTip(f"{tip} ({key_seq})" if key_seq else tip)
            btn.setFixedSize(32, 32)
            btn.clicked.connect(lambda _, k=action_key: self._dispatch_quick_action(k))
            return btn

        # 分割线右侧：无线 / 投屏，左对齐
        for icon_name, action_key, shortcut_key, tip in [
            ('fa6s.wifi', "wireless", "adb_wireless", "无线联调"),
            ('fa6s.desktop', "scrcpy", "adb_scrcpy", "投屏"),
        ]:
            toolbar.addWidget(_quick_icon_btn(icon_name, action_key, shortcut_key, tip))

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        toolbar.addWidget(spacer)

        # 顶栏右侧：安装 / 推送 / MD5，与菜单按钮一样右对齐
        for icon_name, action_key, shortcut_key, tip in [
            ('fa6s.box', "install", "adb_install", "安装 APK"),
            ('fa6s.upload', "push", "adb_push", "推送文件"),
            ('fa6s.key', "md5", "adb_md5", "MD5 查询"),
        ]:
            toolbar.addWidget(_quick_icon_btn(icon_name, action_key, shortcut_key, tip))

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
        # 检查更新：动作本身只发信号，真正的网络请求由 main.py 接线（这里不碰网络）
        self._update_available = False
        self._main_menu_actions['check_update'] = self.main_menu.addAction(
            qta.icon('fa6s.rotate', color='#555555'), "检查更新",
            self.check_update_requested.emit)
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

        self.stacked_widget = QStackedWidget()
        # 9 个占位 + 下面把欢迎页 insertWidget 到 6，最终共 10 页（index 0..9）。
        # index 9 本来就是空占位，语音播报页直接复用它，不用再加页。
        for _ in range(9):
            self.stacked_widget.addWidget(QWidget())

        # 欢迎页（参照 PyCharm 欢迎页设计）
        welcome_wrapper = QWidget()
        welcome_wrapper.setObjectName("WelcomeWrapper")
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
        tips_layout.setSpacing(5)

        self._welcome_tip_rows = []
        # 按「从零跑通一个用例」的上手顺序排列：
        # 连接设备 → 建用例 → 录制 → 执行，最后两条点出工具页 / 性能页
        tips = [
            ("fa6s.mobile-screen", "连接设备", "顶部工具栏 → 选择设备 → 刷新"),
            ("fa6s.pen-to-square", "创建用例", "「自动化编辑」→ 右键项目树 → 创建用例"),
            ("fa6s.video", "录制步骤", "选中用例 → 点步骤列表上方 ● 开始录制"),
            ("fa6s.play", "执行测试", "「自动化执行」→ 勾选用例 → 执行"),
            ("fa6s.screwdriver-wrench", "ADB 调试", "「ADB 工具箱」→ 指令 / 弱网 / Monkey"),
            ("fa6s.gauge-high", "性能检测", "「性能检测」→ 选应用 → 开始监控"),
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

        # 日志面板由 main.py 通过 set_bottom_log_placeholder() 注入
        self.main_splitter.setChildrenCollapsible(False)

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
            ("语音播报", 'fa6s.microphone', 9),
        ]
        self.nav_actions = []
        self.nav_icons = []
        self.nav_indices = []  # 每个 action 对应的视图索引
        for text, icon, idx in nav_items:
            action = QAction(qta.icon(icon, color='#a3a6b0'), text, self)
            action.setCheckable(True)
            action.triggered.connect(lambda checked, i=idx: self._on_nav_clicked(i, checked))
            self.left_toolbar.addAction(action)
            self.nav_actions.append(action)
            self.nav_icons.append(icon)
            self.nav_indices.append(idx)

        spacer_widget = QWidget()
        spacer_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        spacer_action = QWidgetAction(self)
        spacer_action.setDefaultWidget(spacer_widget)
        self.left_toolbar.addAction(spacer_action)

        # ---------- 左工具栏下方：硬件 / Crash / ANR（选中即在底部日志区展示内容）----------
        _left_shortcuts = Settings.get_shortcuts()

        def _bottom_panel_action(icon_name, kind, tip, shortcut_key):
            action = QAction(qta.icon(icon_name, color='#a3a6b0'), tip, self)
            action.setCheckable(True)
            key_seq = _left_shortcuts.get(shortcut_key) or ""
            action.setToolTip(f"{tip} ({key_seq})" if key_seq else tip)
            action.triggered.connect(
                lambda checked, k=kind: self._on_bottom_panel_action(k, checked))
            self.left_toolbar.addAction(action)
            return action

        self.device_action = _bottom_panel_action(
            'fa6s.microchip', "device_info", "硬件信息", "adb_device_info")
        self.crash_action = _bottom_panel_action(
            'fa6s.bug', "crash", "Crash 日志", "adb_crash")
        self.anr_action = _bottom_panel_action(
            'fa6s.hourglass-half', "anr", "ANR 日志", "adb_anr")

        self.log_action = QAction(qta.icon('fa6s.terminal', color='#a3a6b0'), "日志", self)
        self.log_action.setCheckable(True)
        self.log_action.setToolTip("显示/隐藏虫师日志面板 (Ctrl+L)")
        self.log_action.triggered.connect(self.toggle_bottom_log)
        self.left_toolbar.addAction(self.log_action)

        # 底部面板几个开关共用一块区域，互斥显示
        self._bottom_panel_actions = {
            "log": self.log_action,
            "device_info": self.device_action,
            "crash": self.crash_action,
            "anr": self.anr_action,
        }

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

        # 消息中心：与左侧底部面板开关同构（可开合、与其他 kind 互斥）。
        # 内容由 NotificationCenterView 提供，与虫师日志共用底部面板那块区域。
        self.msg_action = QAction(qta.icon('fa6s.bell', color='#a3a6b0'), "消息", self)
        self.msg_action.setCheckable(True)
        self.msg_action.setToolTip("消息中心")
        self.msg_action.triggered.connect(
            lambda checked: self._on_bottom_panel_action("message", checked)
        )
        self.right_toolbar.addAction(self.msg_action)
        # 复用底部面板那套互斥机制：一次只显示一种内容
        self._bottom_panel_actions["message"] = self.msg_action

        self.help_action = QAction(qta.icon('fa6s.circle-question', color='#a3a6b0'), "帮助中心", self)
        self.help_action.setCheckable(True)
        # 与左侧功能按钮一致：再次点击已选中的按钮 -> 取消选中并回到欢迎页
        self.help_action.triggered.connect(
            lambda checked: self._on_nav_clicked(5, checked)
        )
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
        self.apply_shortcuts()
        # ---------- 应用可视化 Dock ----------
        self.setup_visualize_dock()

    def apply_shortcuts(self):
        """根据配置注册所有快捷键（全局 + 页内 + 快捷功能）"""
        # 清理旧快捷键
        if hasattr(self, '_shortcuts'):
            for sc in self._shortcuts:
                try:
                    sc.activated.disconnect()
                except Exception:
                    pass
                sc.deleteLater()
        self._shortcuts = []

        shortcuts = Settings.get_shortcuts()

        # ---------- 注册辅助函数 ----------
        def register(key, callback, page_index=None):
            """page_index: None 表示全局；否则只在指定页生效"""
            ks_str = shortcuts.get(key, "")
            if not ks_str:
                return
            try:
                sc = QShortcut(QKeySequence(ks_str), self)
                if page_index is None:
                    sc.activated.connect(callback)
                else:
                    def wrapper(idx=page_index, cb=callback):
                        try:
                            if self.stacked_widget.currentIndex() == idx:
                                cb()
                        except Exception:
                            pass
                    sc.activated.connect(wrapper)
                self._shortcuts.append(sc)
            except Exception as e:
                print(f"[apply_shortcuts] 注册 {key}({ks_str}) 失败: {e}")

        # ============ 全局 ============
        register("help_center", lambda: self.switch_view(5))
        register("open_settings", lambda: self.on_settings())
        register("refresh_devices", lambda: self.refresh_devices_signal.emit())
        register("toggle_log_panel", lambda: self.toggle_bottom_log(
            not self.bottom_placeholder.isVisible()
            if self.bottom_placeholder else True))
        register("restore_ime", lambda: self._on_menu_action("restore_ime"))

        # ============ 自动化编辑（index 1）============
        register("toggle_record",
                 lambda: self.step_controller.toggle_recording() if self.step_controller else None,
                 page_index=1)
        register("generate_steps",
                 lambda: self._on_step_generate(),
                 page_index=1)
        register("focus_step_search",
                 lambda: self.step_search_input.setFocus() if self.step_search_input else None,
                 page_index=1)
        register("import_cases",
                 lambda: self._on_menu_action("import_cases"),
                 page_index=1)
        register("export_cases",
                 lambda: self._on_menu_action("export_cases"),
                 page_index=1)

        # ============ 自动化执行（index 3）============
        def _execute_from_view():
            if self._execute_view_ref:
                self._execute_view_ref._execute()
        register("execute_cases", _execute_from_view, page_index=3)

        def _toggle_select_all():
            if not self._execute_view_ref:
                return
            view = self._execute_view_ref
            # 若已全选则取消，否则全选
            from PyQt6.QtCore import Qt as _Qt
            def count(item, total=0, checked=0):
                if item.isCheckable():
                    total += 1
                    if item.checkState() == _Qt.CheckState.Checked:
                        checked += 1
                for i in range(item.rowCount()):
                    total, checked = count(item.child(i), total, checked)
                return total, checked
            t, c = 0, 0
            for i in range(view.model.rowCount()):
                t, c = count(view.model.item(i), t, c)
            if t > 0 and c == t:
                view.deselect_all()
            else:
                view.select_all()
        register("toggle_select_all", _toggle_select_all, page_index=3)

        register("save_suite",
                 lambda: self._execute_view_ref._save_current_as_suite() if self._execute_view_ref else None,
                 page_index=3)
        register("delete_suite",
                 lambda: self._execute_view_ref._delete_selected_suite() if self._execute_view_ref else None,
                 page_index=3)
        register("generate_report",
                 lambda: self._execute_view_ref._on_report_clicked() if self._execute_view_ref else None,
                 page_index=3)

        # ============ ADB 工具箱（index 0）============
        if self._adb_toolbox_controller:
            ctrl = self._adb_toolbox_controller
            view = ctrl.view
            register("adb_search", lambda: view.search_edit.setFocus(), page_index=0)
            register("adb_add_command", lambda: ctrl._on_add_command(), page_index=0)
            register("adb_edit_command",
                     lambda: view._on_edit_command_clicked(), page_index=0)
            register("adb_delete_command",
                     lambda: view._on_delete_command_clicked(), page_index=0)
            register("adb_import_commands",
                     lambda: ctrl._on_import_commands(), page_index=0)
            register("adb_export_commands",
                     lambda: ctrl._on_export_commands(), page_index=0)
            register("adb_execute_selected",
                     lambda: view._on_execute_selected(), page_index=0)

        # ============ ADB 快捷功能（全局）============
        if self._adb_toolbox_controller:
            ctrl = self._adb_toolbox_controller
            for key, action_key in [
                ("adb_wireless", "wireless"),
                ("adb_scrcpy", "scrcpy"),
                ("adb_install", "install"),
                ("adb_push", "push"),
                ("adb_device_info", "device_info"),
                ("adb_hprof", "hprof"),
                ("adb_monkey", "monkey"),
                ("adb_crash", "crash"),
                ("adb_anr", "anr"),
                ("adb_md5", "md5"),
                ("adb_weak_network", "weak_network"),
                ("adb_packet", "packet"),
            ]:
                if action_key in ("device_info", "crash", "anr"):
                    # 这几个功能的内容在底部日志区显示：快捷键与左工具栏按钮走同一条路，
                    # 否则只拉了内容、面板还关着，看起来像没反应
                    register(key, lambda k=action_key: self._on_bottom_panel_action(k, True))
                elif action_key in ("weak_network", "monkey"):
                    # 弱网 / Monkey 已内嵌到 ADB 工具箱页，快捷键改为切页 + 聚焦面板
                    register(key, lambda k=action_key: self._focus_toolbox_panel(k))
                else:
                    register(key,
                             lambda k=action_key: ctrl._on_quick_action(k))

        # ============ 性能检测（index 8）============
        if self._perf_view:
            register("perf_toggle_monitor",
                     lambda: self._perf_view._on_start_clicked()
                     if self._perf_view.get_state() == self._perf_view.STATE_IDLE
                     else self._perf_view.stop_requested.emit(),
                     page_index=8)
            register("perf_toggle_pause",
                     lambda: (self._perf_view.pause_requested.emit()
                              if self._perf_view.get_state() == self._perf_view.STATE_RUNNING
                              else self._perf_view.resume_requested.emit()),
                     page_index=8)
            register("perf_export_csv",
                     lambda: self._perf_view.export_csv_requested.emit(),
                     page_index=8)
            register("perf_save_baseline",
                     lambda: self._perf_view.baseline_requested.emit(),
                     page_index=8)

        # ============ 元素库（index 4）============
        handlers = getattr(self, '_elem_shortcut_handlers', None)
        if handlers:
            register("elem_add", handlers.get("elem_add"), page_index=4)
            register("elem_edit", handlers.get("elem_edit"), page_index=4)
            register("elem_delete", handlers.get("elem_delete"), page_index=4)
            register("elem_verify", handlers.get("elem_verify"), page_index=4)

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
            "#VisualizeDockContent { background-color: rgba(44, 44, 44, 0); }"
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

    def _fit_visualize_dock_to_screen(self):
        """把浮动中的可视化窗口整体（含标题栏/边框）收敛进屏幕可用区域。

        move() 用的是窗口框架坐标，标题栏会额外占掉约一行高度，
        所以在窗口 show() 之后再按 frameGeometry() 校正一次。
        """
        dock = self.visualize_dock
        if dock is None or not dock.isVisible():
            return
        screen = dock.screen() or self.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        avail = screen.availableGeometry()

        # 窗口本身比屏幕还大时先缩小（留出标题栏的余量）
        frame = dock.frameGeometry()
        if frame.width() > avail.width() or frame.height() > avail.height():
            dock.resize(min(dock.width(), max(600, avail.width() - 80)),
                        min(dock.height(), max(400, avail.height() - 120)))
            frame = dock.frameGeometry()

        dx = dy = 0
        if frame.left() < avail.left():
            dx = avail.left() - frame.left()
        elif frame.right() > avail.right():
            dx = avail.right() - frame.right()
        if frame.top() < avail.top():
            dy = avail.top() - frame.top()
        elif frame.bottom() > avail.bottom():
            dy = avail.bottom() - frame.bottom()
        if dx or dy:
            dock.move(dock.x() + dx, dock.y() + dy)

    def toggle_visualize_dock(self, checked):
        """显示或隐藏可视化 Dock；首次显示时自动启动 weditor"""
        if self.visualize_dock is None:
            return
        if checked:
            # 以浮动窗口方式显示，避开对主界面布局的挤压
            if not self.visualize_dock.isFloating():
                self.visualize_dock.setFloating(True)

            # 首次浮动时给一个合适的尺寸和位置
            if not getattr(self, '_visualize_dock_positioned', False):
                # 目标尺寸，并收敛到可用屏幕范围内：小于屏幕时按屏幕算，避免窗口跑到屏幕外
                target_w, target_h = 1180, 760
                screen = self.screen() or QApplication.primaryScreen()
                avail = screen.availableGeometry() if screen is not None else None
                if avail is not None:
                    margin = 40
                    w = min(target_w, max(600, avail.width() - margin * 2))
                    h = min(target_h, max(400, avail.height() - margin * 2))
                else:
                    w, h = target_w, target_h
                self.visualize_dock.resize(w, h)

                # 相对主窗口右对齐居中，同时保证整个窗口落在屏幕内
                main_geo = self.geometry()
                x = main_geo.right() - w - 60
                y = main_geo.top() + 80
                if avail is not None:
                    x = max(avail.left(), min(x, avail.right() - w + 1))
                    y = max(avail.top(), min(y, avail.bottom() - h + 1))
                self.visualize_dock.move(x, y)
                self._visualize_dock_positioned = True

            self.visualize_dock.show()
            self.visualize_dock.raise_()

            # 窗口显示后才能拿到包含标题栏/边框的 frameGeometry，
            # 这里再校正一次，避免浮动窗口（含标题栏）被顶出屏幕
            QTimer.singleShot(0, self._fit_visualize_dock_to_screen)

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
            # 原生弹窗（QMessageBox / QInputDialog / QFileDialog）靠全局调色板跟随主题
            Theme.apply_app_palette(mode)
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
                    /* 清掉通用 QPushButton 的 0 12px 内边距，否则悬浮高亮框会被撑宽 */
                    padding: 0px;
                    min-width: 28px;
                    max-width: 28px;
                    min-height: 28px;
                    max-height: 28px;
                }}
                QToolBar QPushButton#menuBtn:hover {{ background: rgba(255, 255, 255, 0.08); border-radius: 3px; }}
                QToolBar QPushButton#menuBtn::menu-indicator {{ image: none; width: 0px; height: 0px; }}
                QToolBar QPushButton#toolIconBtn {{
                    background: transparent;
                    border: none;
                    padding: 0px;
                    min-width: 28px;
                    max-width: 28px;
                    min-height: 28px;
                    max-height: 28px;
                }}
                QToolBar QPushButton#toolIconBtn:hover {{ background: rgba(255, 255, 255, 0.08); border-radius: 3px; }}
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
            # 必须带上 #centralWidget 选择器：Qt 会把「不带选择器的声明」应用到该
            # 控件及其所有子孙控件，编辑页里每一层容器都会各画一遍半透明底，
            # 叠四五层后壁纸就完全看不出来了。
            if has_wallpaper:
                self.centralWidget().setStyleSheet(
                    "#centralWidget { background: rgba(60, 60, 60, 0.7); }")
            else:
                self.centralWidget().setStyleSheet(
                    "#centralWidget { background: #3c3c3c; }")
    
            # 清空之前可能设置的背景，保证所有子页面都透明
            for i in range(self.stacked_widget.count()):
                widget = self.stacked_widget.widget(i)
                if widget and widget.objectName() == "":
                    # 无名占位页：清掉样式即可（不写 background: transparent，
                    # 那样会把该控件调色板算成全黑并向下继承）
                    widget.setStyleSheet("")
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
                lbl.setStyleSheet(self._group_title_qss("#ffffff"))

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
            # "动作卡片 ▾"下拉按钮及菜单主题（与"项目 ▾"同一套取值）
            self._apply_action_cards_menu_theme(is_dark=True)
            # 欢迎页主题
            self._apply_welcome_page_theme(is_dark=True, has_wallpaper=has_wallpaper)
            # 全局 QToolTip（悬浮提示）跟随主题
            self._apply_tooltip_theme(is_dark=True)
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
        # 原生弹窗（QMessageBox / QInputDialog / QFileDialog）靠全局调色板跟随主题
        Theme.apply_app_palette(mode)
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
            # 同深色分支：选择器不能省，否则会被所有子孙控件继承、逐层叠加
            if has_wallpaper:
                # 有壁纸：中央区域使用与工具栏一致的半透明色，让圆角内外一致
                central.setStyleSheet(
                    "#centralWidget { background: rgba(233, 234, 238, 0.7); }")
            else:
                # 没有壁纸时用 #e9eaee
                central.setStyleSheet("#centralWidget { background: #e9eaee; }")

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
                /* 清掉通用 QPushButton 的 0 12px 内边距，否则悬浮高亮框会被撑宽 */
                padding: 0px;
                min-width: 28px;
                max-width: 28px;
                min-height: 28px;
                max-height: 28px;
            }}
            QToolBar QPushButton#menuBtn:hover {{ background: rgba(0,0,0,0.05); border-radius: 3px; }}
            QToolBar QPushButton#menuBtn::menu-indicator {{ image: none; width: 0px; height: 0px; }}
            QToolBar QPushButton#toolIconBtn {{
                background: transparent;
                border: none;
                padding: 0px;
                min-width: 28px;
                max-width: 28px;
                min-height: 28px;
                max-height: 28px;
            }}
            QToolBar QPushButton#toolIconBtn:hover {{ background: rgba(0,0,0,0.05); border-radius: 3px; }}
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
            lbl.setStyleSheet(self._group_title_qss("#333"))
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
        # "动作卡片 ▾"下拉按钮及菜单主题（与"项目 ▾"同一套取值）
        self._apply_action_cards_menu_theme(is_dark=False)
        # 欢迎页主题
        self._apply_welcome_page_theme(is_dark=False, has_wallpaper=has_wallpaper)
        # 全局 QToolTip（悬浮提示）跟随主题
        self._apply_tooltip_theme(is_dark=False)
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
    # 主区域"欢迎页"在 stacked_widget 里的位置（setup_ui 里 insertWidget(6, ...)）
    WELCOME_PAGE_INDEX = 6

    def _on_nav_clicked(self, index, checked):
        """左侧/右侧功能按钮的点击处理。

        再次点击已选中的按钮时取消选中，主区域回到欢迎页 —— 与"虫师日志"
        按钮那种可开关的行为保持一致。
        应用可视化(index=2)是开关 Dock，不适用这个规则。
        """
        if index == 2:
            self.switch_view(index)
            return
        if checked:
            self.switch_view(index)
        else:
            self.switch_view(self.WELCOME_PAGE_INDEX)

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
            if self.nav_indices[i] == index:
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
            self.statusBar().showMessage("当前功能: 帮助中心", 2000)
        elif index == self.WELCOME_PAGE_INDEX:
            for i, action in enumerate(self.nav_actions):
                if i == 2:
                    continue
                action.setChecked(False)
                action.setIcon(qta.icon(self.nav_icons[i], color='#a3a6b0'))
            self.help_action.setChecked(False)
            self.help_action.setIcon(qta.icon('fa6s.circle-question', color='#a3a6b0'))
            self.statusBar().showMessage("欢迎", 2000)
        else:
            for i, action in enumerate(self.nav_actions):
                if i == 2:
                    continue
                action.setChecked(self.nav_indices[i] == index)
            self.help_action.setChecked(False)
            self.help_action.setIcon(qta.icon('fa6s.circle-question', color='#a3a6b0'))
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
    # 面板标题与图标：三种模式共用同一块区域
    BOTTOM_PANEL_TITLES = {
        "log": "🐞 虫师日志",
        "device_info": "📱 硬件信息",
        "crash": "💥 Crash 日志",
        "anr": "⏳ ANR 日志",
        "message": "🔔 消息",
    }
    BOTTOM_PANEL_ICONS = {
        "log": 'fa6s.terminal',
        "device_info": 'fa6s.microchip',
        "crash": 'fa6s.bug',
        "anr": 'fa6s.hourglass-half',
        "message": 'fa6s.bell',
    }
    # 各模式的占位文案（内容拉取完成前的提示）
    BOTTOM_PANEL_PENDING = {
        "device_info": "正在读取硬件信息…",
        "crash": "正在拉取 Crash 日志…",
        "anr": "正在拉取 ANR 日志…",
    }
    # 虫师日志只保留最近若干行防无限增长；Crash / ANR 不限制行数（0 = 不限）
    BOTTOM_LOG_MAX_BLOCKS = 50

    def set_bottom_log_placeholder(self, widget):
        old = self.main_splitter.widget(1)
        if old:
            old.deleteLater()
        # widget 本身已经带 #BottomLogPanel + #BottomLogTitle + #BottomLogText 结构
        # 不再包装外层，直接放入 splitter，让 _apply_bottom_log_theme 直接作用于它
        self.main_splitter.insertWidget(1, widget)
        widget.setVisible(False)
        self.bottom_placeholder = widget
        self.register_sub_view(widget)

    def toggle_bottom_log(self, checked):
        """日志按钮：显示/隐藏底部面板（内容为虫师日志）"""
        self._on_bottom_panel_action("log", checked)

    def _on_bottom_panel_action(self, kind, checked):
        """底部面板几个开关（日志 / 硬件信息 / Crash / ANR / 消息）共用一块区域，互斥显示

        再次点击已选中的按钮 -> 收起面板（沿用原"日志"按钮的行为）。
        硬件信息 / Crash / ANR 先占位提示，再由控制器拉取内容回填；
        消息面板的内容由 NotificationCenterView 自己维护。
        """
        if not checked:
            self._hide_bottom_panel()
            return
        if kind == "log":
            self.show_bottom_panel("log")
            return
        if kind == "message":
            # 不需要拉取内容，也不该走 ADB 快捷动作分发
            self.show_bottom_panel("message")
            return
        self.show_bottom_panel(kind, content=self.BOTTOM_PANEL_PENDING.get(kind, "正在获取…"))
        self._dispatch_quick_action(kind)

    def _hide_bottom_panel(self):
        if self.bottom_placeholder is not None:
            self.bottom_placeholder.setVisible(False)
        self.main_splitter.setSizes([1, 0])
        self._sync_bottom_panel_actions(None)

    def _sync_bottom_panel_actions(self, active_kind):
        for kind, action in self._bottom_panel_actions.items():
            if action is None:
                continue
            on = (kind == active_kind)
            action.setChecked(on)
            if kind == "message":
                # 铃铛图标带未读角标，必须走专用渲染；
                # 直接用 qta.icon(...) 会把角标覆盖掉，表现为"一打开面板角标就没了"
                action.setIcon(self._render_bell_icon(active=on))
            else:
                action.setIcon(qta.icon(self.BOTTOM_PANEL_ICONS[kind],
                                        color='white' if on else '#a3a6b0'))

    def show_bottom_panel(self, kind, content=None):
        """切换底部面板内容：log / crash / anr / device_info / message 共用同一块区域"""
        if content is not None:
            self._bottom_panel_contents[kind] = content
        if self.bottom_placeholder is None:
            return

        self._bottom_panel_kind = kind
        title = getattr(self, "_bottom_log_title", None)
        if title is not None:
            title.setText(self.BOTTOM_PANEL_TITLES.get(kind, self.BOTTOM_PANEL_TITLES["log"]))

        # 内容容器切页：0 = 文本区（log / crash / anr / device_info），1 = 消息中心
        is_message = (kind == "message")
        if self._bottom_stack is not None:
            self._bottom_stack.setCurrentIndex(1 if is_message else 0)

        text = getattr(self, "_bottom_log_text", None)
        if text is not None and not is_message:
            doc = text.document()
            if kind == "log":
                # 虫师日志只保留最近若干行，避免无限增长
                doc.setMaximumBlockCount(self.BOTTOM_LOG_MAX_BLOCKS)
                # 日志带 HTML 行内配色，需按当前主题重新上色
                from utils import log_colors
                text.clear()
                for html in getattr(self, "_bottom_log_entries", []):
                    text.append(log_colors.recolor(html))
                sb = text.verticalScrollBar()
                sb.setValue(sb.maximum())
            else:
                # 0 = 不限行数：Crash / ANR 动辄上千行，截断会看不到关键堆栈
                doc.setMaximumBlockCount(0)
                text.setPlainText(self._bottom_panel_contents.get(kind) or "（暂无内容）")
                text.verticalScrollBar().setValue(0)

        if is_message and self._notification_view is not None:
            # 打开消息面板即全部标为已读（角标随之清零）
            self._notification_view.on_panel_shown()

        self.bottom_placeholder.setVisible(True)
        self._sync_bottom_panel_actions(kind)
        total = self.main_splitter.height()
        self.main_splitter.setSizes([int(total * 0.7), int(total * 0.3)])

    def set_bottom_panel_content(self, kind, content):
        """Crash / ANR 拉取完成后回填；若用户已切到别的模式，只缓存不抢占视图"""
        self._bottom_panel_contents[kind] = content
        if (self._bottom_panel_kind == kind
                and self.bottom_placeholder is not None
                and self.bottom_placeholder.isVisible()):
            self.show_bottom_panel(kind, content)

    # ---------- 消息中心 ----------
    def set_bottom_panel_stack(self, stack, notification_view=None):
        """注入底部面板的内容容器（QStackedWidget：0 = 文本区，1 = 消息中心）。

        内容区原本是单个 QTextEdit，四种 kind 全靠切文字；加入消息列表后必须换成
        堆叠容器 —— 列表需要每行的动态动作按钮与右键菜单，纯文本控件做不到。
        """
        self._bottom_stack = stack
        if notification_view is not None:
            self._notification_view = notification_view
            self.register_sub_view(notification_view)

    def set_notification_service(self, service):
        """注入消息总线：仅用于驱动铃铛未读角标"""
        self._notification_service = service
        service.unread_changed.connect(self._refresh_bell_badge)
        self._refresh_bell_badge()

    def _has_message_panel_open(self):
        return (self._bottom_panel_kind == "message"
                and self.bottom_placeholder is not None
                and self.bottom_placeholder.isVisible())

    def _render_bell_icon(self, active=False):
        """生成带未读角标的铃铛图标；无未读时就是普通铃铛。

        角标画在图标位图上，而不是叠一个独立控件：两侧工具栏统一是
        QAction + setIcon 的用法，落成位图可以完全不改动工具栏结构。
        未读数按 1-9 显示，超过 9 显示 9+。
        """
        color = 'white' if active else '#a3a6b0'
        unread = (self._notification_service.unread_count
                  if self._notification_service else 0)
        if unread <= 0:
            return qta.icon('fa6s.bell', color=color)

        size = 20
        pixmap = qta.icon('fa6s.bell', color=color).pixmap(size, size)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#f57c00"))
        painter.drawEllipse(QRectF(size - 11, 0, 11, 11))
        painter.setPen(QColor("white"))
        font = QFont()
        font.setPixelSize(8)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(QRectF(size - 11, 0, 11, 11),
                         Qt.AlignmentFlag.AlignCenter,
                         "9+" if unread > 9 else str(unread))
        painter.end()
        return QIcon(pixmap)

    def _refresh_bell_badge(self, *args):
        """未读数变化 / 面板开合后重绘铃铛图标（只动铃铛，不干扰其他按钮状态）"""
        if self.msg_action is None:
            return
        self.msg_action.setIcon(
            self._render_bell_icon(active=self._has_message_panel_open()))

    # ---------- 检查更新：红点角标 ----------
    # 与铃铛未读角标同一套画法（同样画在位图上、同样贴在图标右上角），
    # 只把颜色换成项目里惯用的红 #e74c3c、且不带数字（只表示"有新版本"）。
    # 铃铛那边是 #f57c00 的橙；想让两个角标完全同色，改 UPDATE_BADGE_COLOR 即可。
    UPDATE_BADGE_COLOR = "#e74c3c"
    _BADGE_ICON_SIZE = 20
    _BADGE_DOT_SIZE = 11

    def _render_update_icon(self, icon_name: str, available: bool, color: str = '#555555'):
        """生成带红点角标的图标；无更新时就是普通图标。"""
        icon = qta.icon(icon_name, color=color)
        if not available:
            return icon

        size = self._BADGE_ICON_SIZE
        dot = self._BADGE_DOT_SIZE
        pixmap = icon.pixmap(size, size)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self.UPDATE_BADGE_COLOR))
        painter.drawEllipse(QRectF(size - dot, 0, dot, dot))
        painter.end()
        return QIcon(pixmap)

    def set_update_available(self, available: bool):
        """有新版本时给「检查更新」菜单项和菜单按钮都点上红点。

        必须是"重画图标"而不是叠控件：菜单项是 QAction、按钮是 QPushButton，
        统一走 setIcon 才不用动两边的结构（铃铛未读角标同理）。
        """
        self._update_available = bool(available)
        action = self._main_menu_actions.get('check_update')
        if action is not None:
            action.setIcon(self._render_update_icon('fa6s.rotate', self._update_available))
        if getattr(self, 'menu_btn', None) is not None:
            self.menu_btn.setIcon(
                self._render_update_icon('fa6s.gear', self._update_available,
                                         color='#a3a6b0'))

    def is_update_available(self) -> bool:
        return self._update_available

    def set_update_action_enabled(self, enabled: bool):
        """检查进行中时把菜单项灰掉，避免重复点。"""
        action = self._main_menu_actions.get('check_update')
        if action is not None:
            action.setEnabled(bool(enabled))


    def open_bottom_panel(self, kind):
        """供消息动作调用：打开底部面板的指定 kind（等价于点击对应开关）"""
        self._on_bottom_panel_action(kind, True)

    # ---------- 设置对话框 ----------
    def on_settings(self):
        dlg = SettingsDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.load_wallpaper()
            self.apply_theme()
            self.apply_shortcuts()  # ← 新增：重新注册快捷键
            # 唤醒词可能改过：「语音播报」卡片的默认文案跟着刷新
            # （只在用户没动过那个输入框时才刷，见 ActionCardView.refresh_voice_default）
            if self.action_card is not None:
                self.action_card.refresh_voice_default()

    # ---------- 设置各种视图 ----------
    # 「步骤列表」「动作卡片」的标题是 QLabel，「项目管理」的标题是 QToolButton，
    # 而 QToolButton 自带约 13px 的内部水平边距（QLabel 没有），不对齐会让三处标题
    # 文案的左边距不一致：实测 QLabel 的文案起点 ≈ padding-left + 6px，
    # 所以补 padding-left = 7px 让两者都落在 13px；垂直方向补 3px 与按钮文案齐平。
    GROUP_TITLE_PADDING_LEFT = 7
    GROUP_TITLE_PADDING_TOP = 3

    @classmethod
    def _group_title_qss(cls, color: str) -> str:
        """分组标题样式（三处标题共用，保证与"项目管理 ▾"文案对齐）"""
        return (
            f"font-weight: bold; font-size: 16px; background: transparent; "
            f"color: {color}; "
            f"padding-left: {cls.GROUP_TITLE_PADDING_LEFT}px; "
            f"padding-top: {cls.GROUP_TITLE_PADDING_TOP}px;"
        )

    def set_edit_views(self, project_tree, step_list, action_card):
        project_tree.setObjectName("ProjectTreeView")
        step_list.setObjectName("StepListView")

        container = QWidget()
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

        def create_group_with_title(title, hint, widget, extra_widget=None, title_widget=None):
            """title_widget 给了就用它当标题（比如「动作卡片 ▾」这种下拉按钮），
            否则按 title 文字生成 QLabel 标题 —— 两种情况走同一套布局，观感不会漂"""
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
            if title_widget is not None:
                title_layout.addWidget(title_widget)
            else:
                title_label = QLabel(title)
                title_label.setObjectName("GroupTitleLabel")
                title_label.setStyleSheet(self._group_title_qss("#333"))
                title_layout.addWidget(title_label)
            hint_label = QLabel(hint)
            hint_label.setStyleSheet("color: #999; font-size: 12px; background: transparent;")
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
        title_label.setStyleSheet(self._group_title_qss("#333"))
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

        # ---------- 动作卡片：标题是「动作卡片 ▾」，菜单里「展示动作」打开勾选对话框 ----------
        action_menu_btn = QToolButton()
        action_menu_btn.setObjectName("ActionCardsMenuBtn")
        action_menu_btn.setText("动作卡片 ▾")
        action_menu_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        action_menu_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        action_menu_btn.setToolTip("展示动作：选择卡片区显示哪些动作卡片")

        # 菜单目前只有一项「展示动作」：勾选界面本身是对话框（与「指令管理 → 展示指令」同款），
        # 挂在菜单里而不是点标题直接弹出，是为了跟「项目管理 ▾」「指令管理 ▾」的入口观感一致。
        # 图标用 fa6s.eye，与「展示指令」同一个（都是"控制显示范围"）。
        action_menu = QMenu(action_menu_btn)
        action_menu.setObjectName("ActionCardsMenu")
        act_display_cards = QAction("展示动作", action_menu)
        act_display_cards.setIcon(qta.icon('fa6s.eye', color='#555555'))
        act_display_cards.triggered.connect(self._on_show_action_cards_dialog)
        action_menu.addAction(act_display_cards)
        action_menu_btn.setMenu(action_menu)

        self._action_cards_btn = action_menu_btn
        self._action_cards_menu = action_menu
        self.action_card = action_card

        action_group = create_group_with_title(
            "动作卡片", "填写参数后点击添加按钮", action_card,
            title_widget=action_menu_btn)
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
        container.setStyleSheet("")
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
            # 必须不透明：半透明色会被 QTreeView 的「缩进列 / 内容列」两个单元格
            # 以不同次数叠加，导致同一行出现色差（与项目树 PROJECT_TREE_DARK 同样处理）
            hover_bg = '#4a4a4a' if is_dark else '#dfe2e6'
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
                /* 这里刻意不写 ::branch 规则：只要给 ::branch 指定了任何属性
                   （哪怕只是 background: transparent），Qt 就接管分支列的绘制，
                   从而不再画展开/折叠箭头（执行区域的箭头就是这么丢的）。
                   分支列不出现蓝色色块由上面的 selection-background-color:
                   transparent + 下面 palette.Highlight 置透明两处负责。 */
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
        # 日志是以 HTML span 上色的，行内颜色优先于控件配色，
        # 所以这里同步刷新日志配色表，否则切到夜间模式后日志文字仍是深色
        from utils import log_colors
        log_colors.refresh(is_dark)

        if self.bottom_placeholder is None:
            return

        # 已经输出过的日志把颜色写死在 HTML 行内样式里了，控件样式表改不动它们，
        # 所以这里按新主题把整段日志重新渲染一遍，否则旧日志会一直是旧主题的颜色
        # （当前若在看 Crash / ANR，不要去覆盖它的内容）
        entries = getattr(self, "_bottom_log_entries", None)
        log_text = getattr(self, "_bottom_log_text", None)
        if entries and log_text is not None and self._bottom_panel_kind == "log":
            sb = log_text.verticalScrollBar()
            keep_bottom = sb.value() >= sb.maximum() - 2
            log_text.clear()
            for html in entries:
                log_text.append(log_colors.recolor(html))
            if keep_bottom:
                sb.setValue(sb.maximum())

        if is_dark:
            bg = "transparent" if has_wallpaper else "#191a1c"
            border = "rgba(136, 136, 136, 0.9)" if has_wallpaper else "#4a4a4a"
            text = "#eeeeee"
        else:
            bg = "transparent" if has_wallpaper else "#ffffff"
            border = "rgba(176, 176, 176, 0.9)" if has_wallpaper else "#d0d0d0"
            text = "#333333"

        # 内容容器（QStackedWidget）不能自己铺底色，否则会盖掉面板的圆角背景。
        #
        # 注意：这里只能用 QSS 注释 /* */，不能用 #。
        # # 开头会被解析成一个选择器，从那一行起后面的规则全部失效 —— 表现就是
        # 正文区丢掉 background: transparent 与 border: none，夜间模式底色变浅、
        # 壁纸模式下变成一块不透明方块，四周还会多出一圈边框。
        self.bottom_placeholder.setStyleSheet(f"""
            #BottomLogPanel {{
                background-color: {bg};
                border: 1px solid {border};
                border-radius: 8px;
            }}
            #BottomLogPanel QStackedWidget#BottomPanelStack {{
                background: transparent;
                border: none;
            }}
            #BottomLogPanel #BottomLogTitle {{
                color: {text};
                font-weight: bold;
                font-size: 13px;
                background: transparent;
            }}
            #BottomLogPanel QTextEdit#BottomLogText {{
                background: transparent;
                color: {text};
                border: none;
                font-family: Consolas, monospace;
                font-size: 12px;
            }}
            #BottomLogPanel QTextEdit#BottomLogText QScrollBar:vertical {{
                width: 6px;
                background: transparent;
                border-radius: 3px;
            }}
            #BottomLogPanel QTextEdit#BottomLogText QScrollBar::handle:vertical {{
                background: {'#666' if is_dark else '#c0c0c0'};
                border-radius: 3px;
                min-height: 20px;
            }}
            #BottomLogPanel QTextEdit#BottomLogText QScrollBar::add-line:vertical,
            #BottomLogPanel QTextEdit#BottomLogText QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
        """)

    @staticmethod
    def _apply_tooltip_theme(is_dark: bool):
        """全局 QToolTip 跟随主题。

        QToolTip 是 QApplication 级别的顶层窗口，不是 MainWindow 的子控件，
        在 MainWindow 上 setStyleSheet 管不到它；main.py 里那句初始样式写死了白底，
        夜间模式下就变成白底黑字很刺眼。这里切换主题时统一覆盖 app 级样式表。

        app 级样式表原本只有这一条 QToolTip 规则，所以整体覆盖是安全的；
        各对话框 / 视图的样式都是各自 setStyleSheet，不受影响。
        """
        app = QApplication.instance()
        if app is None:
            return
        if is_dark:
            app.setStyleSheet("""
                QToolTip {
                    background-color: #3c3c3c;
                    color: #eeeeee;
                    border: 1px solid #666666;
                    padding: 4px;
                    font-size: 11px;
                }
            """)
        else:
            app.setStyleSheet("""
                QToolTip {
                    background-color: #ffffff;
                    color: #000000;
                    border: 1px solid #cccccc;
                    padding: 4px;
                    font-size: 11px;
                }
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
            ""
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
        self._adb_toolbox_controller = controller

    def _dispatch_quick_action(self, action_key):
        """顶栏 / 左工具栏的快捷功能按钮 -> ADB 工具箱的快捷动作

        控制器是启动后才注入的，这里延迟取，不能在 setup_toolbar 里直接绑定。
        """
        if self._adb_toolbox_controller is None:
            return
        self._adb_toolbox_controller._on_quick_action(action_key)

    def _focus_toolbox_panel(self, kind):
        """弱网 / Monkey 已内嵌到 ADB 工具箱页：快捷键改为切到该页并聚焦对应面板"""
        ctrl = self._adb_toolbox_controller
        if ctrl is None or getattr(ctrl, "view", None) is None:
            return
        self.switch_view(0)
        if kind == "weak_network":
            ctrl.view.focus_weak_network()
        else:
            ctrl.view.focus_monkey()

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

    # weditor 页面三栏是固定宽度（#left 500px、.middle 400px），而且 flex 子项
    # 默认 min-width:auto 不允许收缩，窗口一窄就会撑出页面级横向滚动条。
    # 这里注入一段样式让它按窗口宽度自适应（只改布局宽度，不动功能）。
    _WEDITOR_FIT_CSS = (
        "html,body{overflow-x:hidden;}"
        "#app,#upper{max-width:100%;}"
        "#upper>*{min-width:0;}"
        "#left{flex:0 1 auto;min-width:220px;}"
        "div.middle{flex:0 1 auto;min-width:180px;}"
        "#right{flex:1 1 0;min-width:0;}"
    )

    def _inject_weditor_fit_css(self, web_view):
        """往 weditor 页面注入自适应样式，避免窗口变窄时出现横向滚动条。"""
        import json as _json
        js = """
        (function () {
            var ID = 'qishi-fit-window';
            var old = document.getElementById(ID);
            if (old && old.parentNode) { old.parentNode.removeChild(old); }
            var style = document.createElement('style');
            style.id = ID;
            style.type = 'text/css';
            style.appendChild(document.createTextNode(%s));
            document.head.appendChild(style);
        })();
        """ % _json.dumps(self._WEDITOR_FIT_CSS)
        try:
            web_view.page().runJavaScript(js)
        except Exception as e:
            print(f"[weditor] 注入自适应样式失败: {e}")

    def _on_weditor_success(self, port):
        self._cleanup_thread()

        try:
            web_view = QWebEngineView()
            web_view.setParent(self._visualize_container)
            web_view.hide()

            def on_loaded(ok):
                if not ok:
                    return
                # 页面自带样式加载完之后再注入，保证优先级
                self._inject_weditor_fit_css(web_view)
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
                            id=new_node_id(node_type),
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
                                id=new_node_id('case'),
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
                                id=new_node_id('folder'),
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

            # 导入时"文件里的全部步骤"都会先建出来，但只有真正落地成新用例的那些才有人
            # 引用；被跳过的（同名的已存在用例）步骤会变成孤儿，顺手回收一次
            # （没有注入清理入口时跳过，导入本身不受影响）
            if callable(self.data_cleanup_handler):
                self.data_cleanup_handler()

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
            "QFrame#PerfContainer { border: none; }"
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

    def set_voice_view(self, voice_view):
        """挂载语音播报视图到 index 9（左工具栏「性能检测」下面那一项）"""
        voice_view.setObjectName("VoiceView")
        # 外层包裹，与性能检测页保持同一套布局约定
        container = QFrame()
        container.setObjectName("VoiceContainer")
        container.setStyleSheet("QFrame#VoiceContainer { border: none; }")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(voice_view)

        old = self.stacked_widget.widget(9)
        self.stacked_widget.removeWidget(old)
        old.deleteLater()
        self.stacked_widget.insertWidget(9, container)
        self.register_sub_view(voice_view)
        self._voice_view = voice_view
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

        sub = QLabel(f"V{APP_VERSION} · Build {BUILD_DATE}")
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
                f"版本：V{APP_VERSION} (Build: {BUILD_DATE})\n"
                "作者：李金钊\n"
                "技术栈：Python 3.13 · PyQt6 · uiautomator2 · weditor · qtawesome\n"
                "© 2026 李金钊 · 虫师团队"
            )
            QApplication.clipboard().setText(info)
            show_toast(dialog, "信息已复制到剪贴板", duration=1500)
            dialog.accept()

        copy_btn.clicked.connect(_copy_and_close)

        dialog.exec()