# views/task_view.py
import logging

import qtawesome as qta
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                             QListWidget, QListWidgetItem, QLabel, QFormLayout,
                             QLineEdit, QComboBox, QDateTimeEdit, QCheckBox,
                             QSpinBox, QDialog, QFrame,
                             QMenu, QAbstractItemView,
                             QButtonGroup, QRadioButton, QGraphicsDropShadowEffect,
                             QSizePolicy, QListView, QStyleOptionButton, QStyle)
from PyQt6.QtCore import pyqtSignal, Qt, QDateTime, QTime, QSize
from PyQt6.QtGui import QColor, QAction, QIcon, QPainter, QPen
from models.task_model import ScheduledTask
from models.suite_model import SuiteModel
from utils.dialogs import ConfirmDeleteDialog, WarningDialog
from utils.toast import show_toast
from utils.theme import Theme, ThemeMode
from datetime import datetime

class BorderedCheckBox(QCheckBox):
    """复选框：保留 Fusion 默认绘制（勾选时有 √），额外叠加明显的边框。"""

    def paintEvent(self, event):
        super().paintEvent(event)
        try:
            opt = QStyleOptionButton()
            self.initStyleOption(opt)
            rect = self.style().subElementRect(
                QStyle.SubElement.SE_CheckBoxIndicator, opt, self
            )
            if rect.isValid() and rect.width() > 0:
                painter = QPainter(self)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setPen(QPen(QColor(120, 120, 120), 1))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(rect.adjusted(0, 0, -1, -1))
                painter.end()
        except Exception:
            pass

class TaskView(QWidget):
    task_selected = pyqtSignal(object)
    task_added = pyqtSignal()
    task_deleted = pyqtSignal(str)
    task_updated = pyqtSignal(str)
    task_toggle_enabled = pyqtSignal(str, bool)
    task_run_now = pyqtSignal(str)

    def __init__(self, task_model, suite_model, parent=None):
        super().__init__(parent)
        self.setObjectName("TaskView")
        self.task_model = task_model
        self.suite_model = suite_model
        self.project_model = None
        self.device_service = None
        self.execution_controller = None
        self.logs_view = None
        self._current_task_id = None
        self._refreshing = False
        self._current_theme = ThemeMode.LIGHT
        self.setup_ui()
        self.refresh_list()

    # ---------- Setter 方法 ----------
    def set_project_model(self, model):
        self.project_model = model

    def set_device_service(self, svc):
        self.device_service = svc

    def set_execution_controller(self, ctrl):
        self.execution_controller = ctrl

    def set_logs_view(self, view):
        self.logs_view = view

    def start_scheduler(self):
        pass

    def apply_theme(self, theme_mode: ThemeMode):
        """应用主题到视图（由主窗口调用）"""
        self._current_theme = theme_mode
        Theme.apply_theme_to_widget(self, theme_mode)

        # 检测是否设置了壁纸（用于列表背景半透明处理）
        import os as _os
        from utils.settings import Settings as _Settings
        has_wp = False
        try:
            wp_path = _Settings.get_wallpaper_path()
            has_wp = bool(wp_path and _os.path.exists(wp_path))
        except Exception:
            pass

        # 定时计划功能按钮：日夜模式统一使用白天模式的深蓝样式
        btn_style = """
            QPushButton {
                background-color: #1976d2;
                color: white;
                border: none;
                padding: 5px 12px;
                border-radius: 4px;
                font-weight: 500;
                min-width: 90px;
            }
            QPushButton:hover {
                background-color: #1565c0;
            }
            QPushButton:pressed {
                background-color: #0d47a1;
            }
            QPushButton:disabled {
                background-color: #b0b0b0;
                color: #e0e0e0;
            }
        """

        if has_wp:
            # 有壁纸：视图透明（由外层 centralWidget 罩层透出），列表区域半透明
            if theme_mode == ThemeMode.DARK:
                list_bg = "rgba(55, 55, 55, 0.85)"
                list_border = "#555"
                text_color = "#eee"
                item_border = "#3a3a3a"
                sb_bg = "rgba(58, 58, 58, 0.5)"
                sb_handle = "#666"
            else:
                list_bg = "rgba(232, 234, 237, 0.85)"
                list_border = "#d0d0d0"
                text_color = "#333"
                item_border = "#dfe1e5"
                sb_bg = "#e0e0e0"
                sb_handle = "#c0c0c0"

            self.setStyleSheet(f"""
                #TaskView {{
                    background-color: transparent;
                    border: none;
                    padding: 8px;
                }}
                #TaskView QListWidget {{
                    border: 1px solid {list_border};
                    border-radius: 6px;
                    background: {list_bg};
                    padding: 4px;
                }}
                #TaskView QListWidget::item {{
                    padding: 0px;
                    border-bottom: 1px solid {item_border};
                }}
                #TaskView QListWidget::item:selected {{
                    background-color: transparent;
                }}
                #TaskView QLabel {{ color: {text_color}; background: transparent; }}
                #TaskView #taskAddBtn, #TaskView #taskDeleteBtn, #TaskView #taskRunBtn {{
                    background-color: #1976d2;
                    color: white;
                    border: none;
                    padding: 5px 12px;
                    border-radius: 4px;
                    font-weight: 500;
                    min-width: 90px;
                }}
                #TaskView #taskAddBtn:hover, #TaskView #taskDeleteBtn:hover, #TaskView #taskRunBtn:hover {{
                    background-color: #1565c0;
                }}
                #TaskView #taskAddBtn:pressed, #TaskView #taskDeleteBtn:pressed, #TaskView #taskRunBtn:pressed {{
                    background-color: #0d47a1;
                }}
                                #TaskView #taskAddBtn:disabled, #TaskView #taskDeleteBtn:disabled, #TaskView #taskRunBtn:disabled {{
                    background-color: #b0b0b0;
                    color: #e0e0e0;
                }}
                #TaskView QCheckBox {{ color: {text_color}; }}
                #TaskView QListWidget QScrollBar:vertical {{
                    width: 6px;
                    background: {sb_bg};
                    border-radius: 3px;
                    margin: 0px;
                }}
                #TaskView QListWidget QScrollBar::handle:vertical {{
                    background: {sb_handle};
                    border-radius: 3px;
                    min-height: 20px;
                }}
                #TaskView QListWidget QScrollBar::add-line:vertical,
                #TaskView QListWidget QScrollBar::sub-line:vertical {{
                    height: 0px;
                    width: 0px;
                }}
                #TaskView QListWidget QScrollBar::add-page:vertical,
                #TaskView QListWidget QScrollBar::sub-page:vertical {{
                    background: transparent;
                }}
            """)

        # 为三个按钮应用样式（无论是否有壁纸都要设）
        for btn in (self.add_btn, self.delete_btn, self.run_btn):
            btn.setStyleSheet(btn_style)
            if btn is self.add_btn:
                btn.setIcon(qta.icon('fa6s.plus', color='white'))
            elif btn is self.delete_btn:
                btn.setIcon(qta.icon('fa6s.trash-can', color='white'))
            elif btn is self.run_btn:
                btn.setIcon(qta.icon('fa6s.play', color='white'))

        # 刷新列表以更新 TaskListItem
        self.refresh_list()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # ---------- 标题 + 按钮 ----------
        top_layout = QHBoxLayout()
        top_layout.setSpacing(8)
        title_label = QLabel("⏰ 定时计划")
        title_label.setStyleSheet("font-weight: bold; font-size: 16px;")
        top_layout.addWidget(title_label)
        top_layout.addStretch()

        self.add_btn = QPushButton("新增")
        self.add_btn.setObjectName("taskAddBtn")
        self.add_btn.setIcon(qta.icon('fa6s.plus', color='white'))
        self.add_btn.clicked.connect(self._on_add_task)

        self.delete_btn = QPushButton("删除")
        self.delete_btn.setObjectName("taskDeleteBtn")
        self.delete_btn.setIcon(qta.icon('fa6s.trash-can', color='white'))
        self.delete_btn.clicked.connect(self._on_delete_task)
        self.delete_btn.setEnabled(False)

        self.run_btn = QPushButton("执行")
        self.run_btn.setObjectName("taskRunBtn")
        self.run_btn.setIcon(qta.icon('fa6s.play', color='white'))
        self.run_btn.clicked.connect(self._on_run_now)
        self.run_btn.setEnabled(False)

        top_layout.addWidget(self.add_btn)
        top_layout.addWidget(self.delete_btn)
        top_layout.addWidget(self.run_btn)
        layout.addLayout(top_layout)

        # ---------- 任务列表 ----------
        self.task_list = QListWidget()
        self.task_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # 禁用焦点矩形
        self.task_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.task_list.currentItemChanged.connect(self._on_item_selected)
        self.task_list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        # 彻底清除 QListWidget 自身的 item 样式，让 widget 完全控制
        self.task_list.setStyleSheet("""
            QListWidget::item {
                padding: 0px;
                margin: 0px;
                background: transparent;
                background-color: transparent;
                border: none;
                outline: none;
            }
            QListWidget::item:selected {
                background: transparent;
                background-color: transparent;
                selection-background-color: transparent;
                border: none;
                outline: none;
            }
            QListWidget::item:focus {
                outline: none;
                border: none;
            }
        """)
        layout.addWidget(self.task_list)

        # ---------- 占位提示 ----------
        self.placeholder = QLabel("请创建定时任务")
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setStyleSheet("color: #999; font-size: 14px;")
        self.placeholder.hide()
        layout.addWidget(self.placeholder)

        # 右键菜单
        self.task_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.task_list.customContextMenuRequested.connect(self._show_context_menu)

    def refresh_list(self):
        logger = logging.getLogger(__name__)
        logger.info("refresh_list START")
        if self._refreshing:
            logger.warning("refresh_list already in progress, exit")
            return
        self._refreshing = True
        try:
            # 保存当前选中的任务ID
            selected_id = None
            current_item = self.task_list.currentItem()
            if current_item:
                widget = self.task_list.itemWidget(current_item)
                if widget and hasattr(widget, 'task'):
                    selected_id = widget.task.id

            # 阻塞所有现有列表项的 enable_check 信号，避免清空时触发
            for i in range(self.task_list.count()):
                item = self.task_list.item(i)
                widget = self.task_list.itemWidget(item)
                if widget and hasattr(widget, 'enable_check'):
                    widget.enable_check.blockSignals(True)

            self.task_list.blockSignals(True)
            self.task_list.clear()
            self.task_list.blockSignals(False)

            tasks = self.task_model.get_all_tasks()
            if not tasks:
                self.placeholder.show()
                self.task_list.hide()
            else:
                self.placeholder.hide()
                self.task_list.show()
                for task in tasks:
                    item = QListWidgetItem()
                    widget = TaskListItem(task, self._current_theme)
                    widget.task_toggle.connect(self._on_toggle)
                    # 强制应用主题样式（确保边框等生效）
                    widget.apply_theme()
                    item.setSizeHint(QSize(0, 44))
                    self.task_list.addItem(item)
                    self.task_list.setItemWidget(item, widget)

            # 恢复选中
            if selected_id:
                for i in range(self.task_list.count()):
                    item = self.task_list.item(i)
                    widget = self.task_list.itemWidget(item)
                    if widget and hasattr(widget, 'task') and widget.task.id == selected_id:
                        self.task_list.setCurrentItem(item)
                        break
                if self.task_list.currentItem():
                    self._on_item_selected(self.task_list.currentItem(), None)

            self._update_buttons()
            logger.info("refresh_list finished")
        except Exception as e:
            logger.exception("refresh_list crashed")
            raise
        finally:
            self._refreshing = False

    def _update_buttons(self):
        current = self.task_list.currentItem()
        has_selection = current is not None
        self.delete_btn.setEnabled(has_selection)
        if has_selection:
            self.delete_btn.setIcon(qta.icon('fa6s.trash-can', color='white'))
        else:
            self.delete_btn.setIcon(QIcon())

        run_enabled = False
        if has_selection:
            widget = self.task_list.itemWidget(current)
            if widget and hasattr(widget, 'task'):
                task = widget.task
                run_enabled = task.enabled and task.last_result != "device_offline"
        self.run_btn.setEnabled(run_enabled)
        if run_enabled:
            self.run_btn.setIcon(qta.icon('fa6s.play', color='white'))
        else:
            self.run_btn.setIcon(QIcon())

    def _on_item_selected(self, current, previous):
        if self._refreshing:
            return
        # 取消所有项的选中状态
        for i in range(self.task_list.count()):
            item = self.task_list.item(i)
            widget = self.task_list.itemWidget(item)
            if widget and hasattr(widget, 'set_selected'):
                widget.set_selected(False)
        # 设置当前项为选中
        if current:
            widget = self.task_list.itemWidget(current)
            if widget and hasattr(widget, 'set_selected'):
                widget.set_selected(True)
                if hasattr(widget, 'task'):
                    self._current_task_id = widget.task.id
                    self.task_selected.emit(widget.task)
                else:
                    self._current_task_id = None
                    self.task_selected.emit(None)
        else:
            self._current_task_id = None
            self.task_selected.emit(None)
        self._update_buttons()

    # ---------- 信号发射方法 ----------
    def _on_toggle(self, task_id, enabled):
        if self._refreshing:
            return
        self.task_toggle_enabled.emit(task_id, enabled)

    def _on_add_task(self):
        self.task_added.emit()

    def _on_delete_task(self):
        if self._refreshing:
            return
        item = self.task_list.currentItem()
        if not item:
            return
        widget = self.task_list.itemWidget(item)
        if not widget or not hasattr(widget, 'task'):
            return
        task_id = widget.task.id
        self.task_deleted.emit(task_id)

    def _on_run_now(self):
        if self._refreshing:
            return
        item = self.task_list.currentItem()
        if not item:
            return
        widget = self.task_list.itemWidget(item)
        if not widget or not hasattr(widget, 'task'):
            return
        task_id = widget.task.id
        self.task_run_now.emit(task_id)

    def _show_context_menu(self, pos):
        if self._refreshing:
            return
        item = self.task_list.itemAt(pos)
        if not item:
            return
        widget = self.task_list.itemWidget(item)
        if not widget or not hasattr(widget, 'task'):
            return
        task = widget.task
        menu = QMenu()
        toggle_action = QAction("启用" if not task.enabled else "禁用", self)
        toggle_action.triggered.connect(lambda: self.task_toggle_enabled.emit(task.id, not task.enabled))
        menu.addAction(toggle_action)
        menu.addSeparator()
        run_action = QAction("立即执行", self)
        run_action.triggered.connect(lambda: self.task_run_now.emit(task.id))
        menu.addAction(run_action)
        menu.addSeparator()
        edit_action = QAction("编辑", self)
        edit_action.triggered.connect(lambda: self.task_updated.emit(task.id))
        menu.addAction(edit_action)
        menu.addAction("删除", lambda: self._on_delete_task())
        menu.exec(self.task_list.viewport().mapToGlobal(pos))


class TaskListItem(QWidget):
    task_toggle = pyqtSignal(str, bool)

    def __init__(self, task: ScheduledTask, theme_mode: ThemeMode = ThemeMode.LIGHT, parent=None):
        super().__init__(parent)
        self.setObjectName("TaskListItem")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.task = task
        self._selected = False
        self._theme_mode = theme_mode
        self.setup_ui()
        self.apply_theme()

    def setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 2, 10, 2)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self.status_dot = QLabel()
        self.status_dot.setFixedSize(12, 12)
        self.status_dot.setStyleSheet("border-radius: 6px; background-color: %s;" % self._get_color())
        layout.addWidget(self.status_dot, alignment=Qt.AlignmentFlag.AlignVCenter)

        self.name_label = QLabel(self.task.name)
        self.name_label.setStyleSheet("font-weight: 500; font-size: 13px;")
        layout.addWidget(self.name_label, 1)

        if self.task.next_run:
            try:
                dt = datetime.fromisoformat(self.task.next_run)
                next_run_display = dt.strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                next_run_display = self.task.next_run
        else:
            next_run_display = "任务未启用或执行时间已过期"
        time_label = QLabel(f"执行: {next_run_display}")
        time_label.setStyleSheet("font-size: 11px; color: #888;")
        layout.addWidget(time_label)

        self.enable_check = BorderedCheckBox()
        self.enable_check.blockSignals(True)
        self.enable_check.setChecked(self.task.enabled)
        self.enable_check.blockSignals(False)
        self.enable_check.stateChanged.connect(self._on_toggle)
        layout.addWidget(self.enable_check, alignment=Qt.AlignmentFlag.AlignVCenter)

        self.setFixedHeight(44)

    def apply_theme(self):
        """根据当前主题和选中状态更新样式（清除所有外部边框干扰）"""
        if self._theme_mode == ThemeMode.DARK:
            border_color = "#888"
            bg_color = "#1e3a5f" if self._selected else "transparent"
            text_color = "#eee"
        else:
            border_color = "#999"
            bg_color = "#e3f2fd" if self._selected else "transparent"
            text_color = "#333"

        self.setStyleSheet(f"""
            #TaskListItem {{
                background: {bg_color} !important;
                background-color: {bg_color} !important;
                selection-background-color: {bg_color} !important;
                border: 1px solid {border_color} !important;
                border-radius: 6px !important;
                margin: 2px 0px !important;  /* 上下2px间距 */
                padding: 0px !important;
            }}
            #TaskListItem QLabel {{
                color: {text_color} !important;
            }}
            #TaskListItem QCheckBox {{
                color: {text_color} !important;
                spacing: 4px;
            }}
        """)

        # 只设文字色 + palette（让 Fusion 绘制带边框的复选框）
        self.enable_check.setStyleSheet(f"""
            QCheckBox {{
                color: {text_color};
                spacing: 4px;
            }}
        """)

    def set_selected(self, selected: bool):
        if self._selected != selected:
            self._selected = selected
            self.apply_theme()
            self.update()

    def sizeHint(self):
        return QSize(0, 44)

    def _get_color(self):
        if not self.task.enabled:
            return "#b0b0b0"
        if self.task.last_result == "success":
            return "#27ae60"
        elif self.task.last_result == "failed":
            return "#e74c3c"
        elif self.task.last_result == "device_offline":
            return "#f39c12"
        return "#1976d2"

    def _on_toggle(self, state):
        self.task_toggle.emit(self.task.id, state == Qt.CheckState.Checked.value)


class TaskEditDialog(QDialog):
    def __init__(self, parent, task: ScheduledTask = None, suite_names: list = None):
        super().__init__(parent)
        self.setObjectName("TaskEditDialog")
        self.task = task
        self.suite_names = suite_names or []
        self.setWindowTitle("编辑定时任务" if task else "新增定时任务")
        self.setFixedSize(450, 520)
        self.setModal(True)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._valid_data = None
        self.setup_ui()
        if task:
            self._load_data(task)
        self.apply_theme()

    def apply_theme(self, theme_mode: ThemeMode = None):
        """应用主题到对话框"""
        if theme_mode is None:
            from utils.settings import Settings, THEME_MODE_DARK
            theme_mode = ThemeMode.DARK if Settings.get_theme_mode() == THEME_MODE_DARK else ThemeMode.LIGHT
        Theme.apply_theme_to_widget(self, theme_mode)

        # 让 QDateTimeEdit 的日历弹窗也跟随主题
        try:
            cal = self.time_edit.calendarWidget()
            if cal is not None:
                from PyQt6.QtGui import QPalette, QColor
                if theme_mode == ThemeMode.DARK:
                    # QSS：处理背景、选中、工具条
                    cal.setStyleSheet("""
                        QCalendarWidget QWidget {
                            background-color: #3c3c3c;
                            color: #eeeeee;
                        }
                        QCalendarWidget QToolButton {
                            background-color: transparent;
                            color: #eeeeee;
                            border: none;
                            padding: 4px 8px;
                        }
                        QCalendarWidget QToolButton:hover {
                            background-color: #555555;
                            border-radius: 4px;
                        }
                        QCalendarWidget QMenu {
                            background-color: #3c3c3c;
                            color: #eeeeee;
                        }
                        QCalendarWidget QSpinBox {
                            background-color: #2d2d2d;
                            color: #eeeeee;
                            border: 1px solid #555555;
                            selection-background-color: #90caf9;
                            selection-color: #1e1e1e;
                        }
                        QCalendarWidget QAbstractItemView:enabled {
                            background-color: #2d2d2d;
                            color: #eeeeee;
                            selection-background-color: #90caf9;
                            selection-color: #1e1e1e;
                            outline: none;
                        }
                        QCalendarWidget QAbstractItemView:disabled {
                            color: #777777;
                        }
                        /* 星期标题栏（周一到周五、周六周日） */
                        QCalendarWidget QWidget#qt_calendar_navigationbar {
                            background-color: #3c3c3c;
                        }
                        QCalendarWidget QTableView {
                            background-color: #2d2d2d;
                            alternate-background-color: #2d2d2d;
                            color: #eeeeee;
                            selection-background-color: #90caf9;
                            selection-color: #1e1e1e;
                            gridline-color: transparent;
                        }
                        QCalendarWidget QTableView QHeaderView::section {
                            background-color: #3c3c3c;
                            color: #eeeeee;
                            border: none;
                            padding: 4px;
                        }
                    """)
                    # palette 覆盖：让 QCalendarWidget 内部绘制的星期标题文字变亮
                    pal = cal.palette()
                    pal.setColor(QPalette.ColorRole.WindowText, QColor("#eeeeee"))
                    pal.setColor(QPalette.ColorRole.Text, QColor("#eeeeee"))
                    pal.setColor(QPalette.ColorRole.ButtonText, QColor("#eeeeee"))
                    pal.setColor(QPalette.ColorRole.Base, QColor("#2d2d2d"))
                    pal.setColor(QPalette.ColorRole.Window, QColor("#3c3c3c"))
                    cal.setPalette(pal)
                else:
                    cal.setStyleSheet("""
                        QCalendarWidget QWidget {
                            background-color: #ffffff;
                            color: #333333;
                        }
                        QCalendarWidget QToolButton {
                            background-color: transparent;
                            color: #333333;
                            border: none;
                            padding: 4px 8px;
                        }
                        QCalendarWidget QToolButton:hover {
                            background-color: #f0f0f0;
                            border-radius: 4px;
                        }
                        QCalendarWidget QMenu {
                            background-color: #ffffff;
                            color: #333333;
                        }
                        QCalendarWidget QSpinBox {
                            background-color: #ffffff;
                            color: #333333;
                            border: 1px solid #d0d0d0;
                            selection-background-color: #1976d2;
                            selection-color: #ffffff;
                        }
                        QCalendarWidget QAbstractItemView:enabled {
                            background-color: #ffffff;
                            color: #333333;
                            selection-background-color: #1976d2;
                            selection-color: #ffffff;
                            outline: none;
                        }
                        QCalendarWidget QAbstractItemView:disabled {
                            color: #999999;
                        }
                        QCalendarWidget QWidget#qt_calendar_navigationbar {
                            background-color: #ffffff;
                        }
                        QCalendarWidget QTableView {
                            background-color: #ffffff;
                            alternate-background-color: #ffffff;
                            color: #333333;
                            selection-background-color: #1976d2;
                            selection-color: #ffffff;
                            gridline-color: transparent;
                        }
                        QCalendarWidget QTableView QHeaderView::section {
                            background-color: #f5f5f5;
                            color: #333333;
                            border: none;
                            padding: 4px;
                        }
                    """)
                    pal = cal.palette()
                    pal.setColor(QPalette.ColorRole.WindowText, QColor("#333333"))
                    pal.setColor(QPalette.ColorRole.Text, QColor("#333333"))
                    pal.setColor(QPalette.ColorRole.ButtonText, QColor("#333333"))
                    pal.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
                    pal.setColor(QPalette.ColorRole.Window, QColor("#ffffff"))
                    cal.setPalette(pal)
        except Exception as e:
            print(f"[TaskEditDialog] 日历主题设置失败: {e}")

    def _setup_combo_style(self, combo):
        from utils.widget_helpers import prepare_combo_view
        prepare_combo_view(combo)

    def setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(0)

        container = QFrame()
        container.setObjectName("container")
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(20)
        shadow.setOffset(0, 0)
        shadow.setColor(QColor(0, 0, 0, 80))
        container.setGraphicsEffect(shadow)

        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(24, 20, 24, 20)
        container_layout.setSpacing(16)

        title_label = QLabel(self.windowTitle())
        title_label.setStyleSheet("font-size: 16px; font-weight: bold;")
        container_layout.addWidget(title_label)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("background-color: #e0e0e0; max-height: 1px;")
        container_layout.addWidget(line)

        form = QFormLayout()
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("请输入任务名称（必填）")
        form.addRow("任务名称:", self.name_edit)

        self.suite_combo = QComboBox()
        self.suite_combo.addItems(self.suite_names)
        self._setup_combo_style(self.suite_combo)
        form.addRow("关联套件:", self.suite_combo)

        self.schedule_group = QButtonGroup(self)
        self.once_radio = QRadioButton("不重复")
        self.daily_radio = QRadioButton("每天")
        self.weekly_radio = QRadioButton("每周")
        self.monthly_radio = QRadioButton("每月")
        self.schedule_group.addButton(self.once_radio)
        self.schedule_group.addButton(self.daily_radio)
        self.schedule_group.addButton(self.weekly_radio)
        self.schedule_group.addButton(self.monthly_radio)
        self.once_radio.toggled.connect(self._on_schedule_changed)
        self.daily_radio.toggled.connect(self._on_schedule_changed)
        self.weekly_radio.toggled.connect(self._on_schedule_changed)
        self.monthly_radio.toggled.connect(self._on_schedule_changed)

        schedule_layout = QHBoxLayout()
        schedule_layout.addWidget(self.once_radio)
        schedule_layout.addWidget(self.daily_radio)
        schedule_layout.addWidget(self.weekly_radio)
        schedule_layout.addWidget(self.monthly_radio)
        schedule_layout.addStretch()
        form.addRow("执行频率:", schedule_layout)

        self.time_edit = QDateTimeEdit()
        self.time_edit.setDateTime(QDateTime.currentDateTime().addSecs(300))
        self.time_edit.setCalendarPopup(True)
        form.addRow("执行时间:", self.time_edit)

        self.week_widget = QWidget()
        week_layout = QHBoxLayout(self.week_widget)
        week_layout.setContentsMargins(0, 0, 0, 0)
        week_layout.setSpacing(6)
        self.week_checks = []
        days = ["日", "一", "二", "三", "四", "五", "六"]
        for i, d in enumerate(days):
            cb = BorderedCheckBox(d)
            cb.setProperty("day_index", i)
            cb.setStyleSheet("QCheckBox { font-size: 12px; }")
            week_layout.addWidget(cb)
            self.week_checks.append(cb)
        week_layout.addStretch()
        self.week_widget.setEnabled(False)
        form.addRow("每周:", self.week_widget)

        self.day_combo = QComboBox()
        self._setup_combo_style(self.day_combo)
        self.day_combo.addItems([str(i) for i in range(1, 32)])
        self.day_combo.setEnabled(False)
        form.addRow("每月:", self.day_combo)

        self.loop_spin = QSpinBox()
        self.loop_spin.setRange(1, 999)
        self.loop_spin.setValue(1)
        form.addRow("循环次数:", self.loop_spin)

        self.stop_on_fail_check = BorderedCheckBox("失败停止")
        form.addRow("", self.stop_on_fail_check)

        self.enable_check = BorderedCheckBox("启用")
        self.enable_check.setChecked(True)
        form.addRow("", self.enable_check)

        container_layout.addLayout(form)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)

        cancel_btn = QPushButton("取消")
        cancel_btn.setObjectName("cancelBtn")
        cancel_btn.setFixedSize(100, 34)
        cancel_btn.clicked.connect(self.reject)

        ok_btn = QPushButton("保存")
        ok_btn.setObjectName("saveBtn")
        ok_btn.setFixedSize(100, 34)
        ok_btn.clicked.connect(self._on_ok_clicked)

        btn_layout.addStretch()
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(ok_btn)
        container_layout.addLayout(btn_layout)

        main_layout.addWidget(container)

        self.once_radio.setChecked(True)
        self._on_schedule_changed()

    def _on_ok_clicked(self):
        task_data = self._validate_and_get_data()
        if task_data is not None:
            self._valid_data = task_data
            self.accept()

    def _validate_and_get_data(self):
        name = self.name_edit.text().strip()
        if not name:
            show_toast(message="任务名称不能为空")
            return None

        task = self.task
        if task is None:
            import time
            task = ScheduledTask(
                id=f"task_{int(time.time()*1000)}",
                name="",
                suite_name="",
                schedule_type="once",
                scheduled_time=""
            )
        task.name = name
        task.suite_name = self.suite_combo.currentText()
        task.loop_count = self.loop_spin.value()
        task.stop_on_fail = self.stop_on_fail_check.isChecked()
        task.enabled = self.enable_check.isChecked()
        if self.once_radio.isChecked():
            task.schedule_type = "once"
            task.scheduled_time = self.time_edit.dateTime().toString(Qt.DateFormat.ISODate)
            task.day_of_week = -1
            task.day_of_month = -1
        elif self.daily_radio.isChecked():
            task.schedule_type = "daily"
            task.scheduled_time = self.time_edit.time().toString("HH:mm")
            task.day_of_week = -1
            task.day_of_month = -1
        elif self.weekly_radio.isChecked():
            task.schedule_type = "weekly"
            task.scheduled_time = self.time_edit.time().toString("HH:mm")
            selected = -1
            for cb in self.week_checks:
                if cb.isChecked():
                    selected = cb.property("day_index")
                    break
            task.day_of_week = selected if selected >= 0 else 0
            task.day_of_month = -1
        elif self.monthly_radio.isChecked():
            task.schedule_type = "monthly"
            task.scheduled_time = self.time_edit.time().toString("HH:mm")
            task.day_of_month = int(self.day_combo.currentText() or 1)
            task.day_of_week = -1
        return task

    def get_task_data(self):
        return getattr(self, '_valid_data', None)

    def _on_schedule_changed(self):
        if self.once_radio.isChecked():
            self.week_widget.setEnabled(False)
            self.day_combo.setEnabled(False)
            self.time_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        elif self.daily_radio.isChecked():
            self.week_widget.setEnabled(False)
            self.day_combo.setEnabled(False)
            self.time_edit.setDisplayFormat("HH:mm")
        elif self.weekly_radio.isChecked():
            self.week_widget.setEnabled(True)
            self.day_combo.setEnabled(False)
            self.time_edit.setDisplayFormat("HH:mm")
        elif self.monthly_radio.isChecked():
            self.week_widget.setEnabled(False)
            self.day_combo.setEnabled(True)
            self.time_edit.setDisplayFormat("HH:mm")

    def _load_data(self, task):
        self.name_edit.setText(task.name)
        index = self.suite_combo.findText(task.suite_name)
        if index >= 0:
            self.suite_combo.setCurrentIndex(index)
        if task.schedule_type == "once":
            self.once_radio.setChecked(True)
        elif task.schedule_type == "daily":
            self.daily_radio.setChecked(True)
        elif task.schedule_type == "weekly":
            self.weekly_radio.setChecked(True)
        elif task.schedule_type == "monthly":
            self.monthly_radio.setChecked(True)
        self.loop_spin.setValue(task.loop_count)
        self.stop_on_fail_check.setChecked(task.stop_on_fail)
        self.enable_check.setChecked(task.enabled)
        if task.schedule_type == "once":
            dt = QDateTime.fromString(task.scheduled_time, Qt.DateFormat.ISODate)
            self.time_edit.setDateTime(dt)
        else:
            parts = task.scheduled_time.split(":")
            if len(parts) == 2:
                dt = QDateTime.currentDateTime()
                dt.setTime(QTime(int(parts[0]), int(parts[1])))
                self.time_edit.setDateTime(dt)
        if task.schedule_type == "weekly" and task.day_of_week >= 0:
            for cb in self.week_checks:
                if cb.property("day_index") == task.day_of_week:
                    cb.setChecked(True)
        if task.schedule_type == "monthly" and task.day_of_month >= 1:
            self.day_combo.setCurrentText(str(task.day_of_month))