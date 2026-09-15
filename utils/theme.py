# utils/theme.py
"""
主题管理模块
定义日间/夜间样式常量，并提供应用主题的统一接口
每个控件通过 objectName 精确定位，避免全局污染
"""
from enum import Enum
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget, QApplication


class ThemeMode(Enum):
    LIGHT = "light"
    DARK = "dark"
    SYSTEM = "system"


class Theme:
    """存放所有控件样式的字符串常量，每个控件独立"""

    # ---------- 基础通用样式（仅用于极少数全局控制，不建议使用）----------
    BASE_LIGHT = """
        QWidget { font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif; }
    """
    BASE_DARK = """
        QWidget { font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif; }
    """

    # ---------- 主窗口 (objectName: MainWindow) ----------
    MAIN_WINDOW_LIGHT = """
        #MainWindow {
            background-color: #f5f6fa;
        }
        #MainWindow QToolBar {
            background-color: #f5f6fa;
            border: none;
            spacing: 1px;
        }
        #MainWindow QToolButton {
            border: none;
            border-radius: 3px;
            padding: 3px 1px;
            margin: 1px 2px;
        }
        #MainWindow QToolButton:hover {
            background-color: #e8edf2;
        }
        #MainWindow QToolButton:checked {
            background-color: #1976d2;
        }
        #MainWindow QStatusBar {
            background-color: #f5f6fa;
            color: #333;
        }

    """
    MAIN_WINDOW_DARK = """
        #MainWindow {
            background-color: #2c2c2c;
        }
        #MainWindow QToolBar {
            background-color: #2c2c2c;
            border: none;
            spacing: 1px;
        }
        #MainWindow QToolButton {
            border: none;
            border-radius: 3px;
            padding: 3px 1px;
            margin: 1px 2px;
        }
        #MainWindow QToolButton:hover {
            background-color: #3a3a3a;
        }
        #MainWindow QToolButton:checked {
            background-color: #90caf9;
        }
        #MainWindow QStatusBar {
            background-color: #2c2c2c;
            color: #eee;
        }

    """

    # ---------- 自动化编辑视图（容器，不直接设样式，其子控件各自独立）----------

    # 项目树 (objectName: ProjectTreeView)
    PROJECT_TREE_LIGHT = """
        #ProjectTreeView {
            background-color: #e8eaed;
            border: none;
            outline: none;
        }
        #ProjectTreeView::item {
            height: 30px;
            color: #333;
            border: none;
            outline: none;
        }
        #ProjectTreeView::item:selected,
        #ProjectTreeView::item:selected:active,
        #ProjectTreeView::item:selected:!active,
        #ProjectTreeView::item:selected:focus {
            background-color: #d0e4f7;
            color: #1a1a1a;
            border: none;
            outline: none;
        }
        #ProjectTreeView::item:hover:!selected {
            background-color: #dfe2e6;
        }
        #ProjectTreeView::branch {
            background: transparent;
        }
        #ProjectTreeView::branch:selected,
        #ProjectTreeView::branch:selected:active,
        #ProjectTreeView::branch:selected:!active {
            background: transparent;
        }
        #ProjectTreeView QScrollBar:vertical {
            width: 6px;
            background: #e0e0e0;
            border-radius: 3px;
        }
        #ProjectTreeView QScrollBar::handle:vertical {
            background: #c0c0c0;
            border-radius: 3px;
            min-height: 20px;
        }
        #ProjectTreeView QScrollBar::add-line:vertical,
        #ProjectTreeView QScrollBar::sub-line:vertical {
            height: 0px;
        }
    """
    PROJECT_TREE_DARK = """
        #ProjectTreeView {
            background-color: rgba(60, 60, 60, 0.7);
            border: none;
            outline: none;
        }
        #ProjectTreeView::item {
            height: 30px;
            color: #eee;
            border: none;
            outline: none;
        }
        #ProjectTreeView::item:selected,
        #ProjectTreeView::item:selected:active,
        #ProjectTreeView::item:selected:!active,
        #ProjectTreeView::item:selected:focus {
            background-color: rgba(30, 58, 95, 0.85);
            color: #ffffff;
            border: none;
            outline: none;
        }
        #ProjectTreeView::item:hover:!selected {
            background-color: rgba(74, 74, 74, 0.6);
        }
        #ProjectTreeView::branch {
            background: transparent;
        }
        #ProjectTreeView::branch:selected,
        #ProjectTreeView::branch:selected:active,
        #ProjectTreeView::branch:selected:!active {
            background: transparent;
        }
        #ProjectTreeView QScrollBar:vertical {
            width: 6px;
            background: rgba(58, 58, 58, 0.5);
            border-radius: 3px;
        }
        #ProjectTreeView QScrollBar::handle:vertical {
            background: #666;
            border-radius: 3px;
            min-height: 20px;
        }
        #ProjectTreeView QScrollBar::add-line:vertical,
        #ProjectTreeView QScrollBar::sub-line:vertical {
            height: 0px;
        }
    """

    # 步骤列表 (objectName: StepListView)
    # 注意：滚动条样式由 StepListView._apply_scrollbar_style 单独设置，
    # 因为 QListWidget 的样式表里同时包含 ::item 和 QScrollBar 时，
    # 部分 Qt 版本会忽略 QScrollBar 规则。
    STEP_LIST_LIGHT = """
        #StepListView {
            background-color: transparent;
            border: none;
            outline: none;
            selection-background-color: transparent;
            selection-color: transparent;
        }
        #StepListView::item {
            background: transparent;
            background-color: transparent;
            border: none;
            padding: 0px;
            margin: 0px;
            outline: none;
        }
        #StepListView::item:selected,
        #StepListView::item:selected:active,
        #StepListView::item:selected:!active,
        #StepListView::item:selected:focus {
            background: transparent;
            background-color: transparent;
            border: none;
            outline: none;
            color: transparent;
        }
        #StepListView::item:hover,
        #StepListView::item:focus,
        #StepListView::item:pressed {
            background: transparent;
            background-color: transparent;
            border: none;
            outline: none;
        }
    """
    STEP_LIST_DARK = """
        #StepListView {
            background-color: transparent;
            border: none;
            outline: none;
            selection-background-color: transparent;
            selection-color: transparent;
        }
        #StepListView::item {
            background: transparent;
            background-color: transparent;
            border: none;
            padding: 0px;
            margin: 0px;
            outline: none;
        }
        #StepListView::item:selected,
        #StepListView::item:selected:active,
        #StepListView::item:selected:!active,
        #StepListView::item:selected:focus {
            background: transparent;
            background-color: transparent;
            border: none;
            outline: none;
            color: transparent;
        }
        #StepListView::item:hover,
        #StepListView::item:focus,
        #StepListView::item:pressed {
            background: transparent;
            background-color: transparent;
            border: none;
            outline: none;
        }
    """

    # 步骤卡片 (objectName: StepCard) - 卡片本身的样式在 StepCardWidget 中动态设置
    # 但是其内部子控件样式可以统一
    STEP_CARD_LIGHT = """
        QWidget#StepCard {
            background-color: white;
            border: 1px solid #d0d0d0;
            border-radius: 8px;
            margin: 1px 0px;
            padding: 2px;
        }
        QWidget#StepCard QLabel {
            color: #333;
        }
        QWidget#StepCard QPushButton {
            border: none;
            background: transparent;
        }
        QWidget#StepCard QPushButton:hover {
            background-color: #f0f0f0;
        }
    """
    STEP_CARD_DARK = """
        QWidget#StepCard {
            background-color: #3c3c3c;
            border: 1px solid #555;
            border-radius: 8px;
            margin: 1px 0px;
            padding: 2px;
        }
        QWidget#StepCard QLabel {
            color: #eee;
        }
        QWidget#StepCard QPushButton {
            border: none;
            background: transparent;
        }
        QWidget#StepCard QPushButton:hover {
            background-color: #4a4a4a;
        }
    """

    # 动作卡片 (objectName: ActionCard)
    ACTION_CARD_LIGHT = """
        #ActionCard {
            background-color: rgba(240, 242, 245, 0.85);
            border: 1px solid #d0d0d0;
            border-radius: 6px;
            margin-top: 6px;
            padding: 6px;
        }
        #ActionCard::title {
            color: #333;
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 5px;
        }
        #ActionCard QComboBox, #ActionCard QLineEdit, #ActionCard QSpinBox, #ActionCard QDoubleSpinBox, #ActionCard QLabel {
            font-size: 9pt;
            color: #333;
        }
        #ActionCard QComboBox {
            border: 1px solid #555;
            border-radius: 4px;
            padding: 4px 6px;
            background-color: rgba(255, 255, 255, 0.9);
            outline: none;
        }
        #ActionCard QLineEdit, #ActionCard QSpinBox, #ActionCard QDoubleSpinBox {
            border: 1px solid #555;
            border-radius: 4px;
            padding: 4px 6px;
            background-color: rgba(255, 255, 255, 0.9);
            outline: none;
        }
        #ActionCard QComboBox:focus, #ActionCard QLineEdit:focus, #ActionCard QSpinBox:focus, #ActionCard QDoubleSpinBox:focus {
            border-color: #1976d2;
        }
        #ActionCard QPushButton {
            background-color: #1976d2;
            border: none;
            border-radius: 4px;
            padding: 2px;
            color: white;
        }
        #ActionCard QPushButton:hover {
            background-color: #1565c0;
        }
        #ActionCard QPushButton:pressed {
            background-color: #0d47a1;
        }
    """

    ACTION_CARD_DARK = """
        #ActionCard {
            background-color: rgba(60, 60, 60, 0.85);
            border: 1px solid #555;
            border-radius: 6px;
            margin-top: 6px;
            padding: 6px;
        }
        #ActionCard::title {
            color: #eee;
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 5px;
        }
        #ActionCard QComboBox, #ActionCard QLineEdit, #ActionCard QSpinBox, #ActionCard QDoubleSpinBox, #ActionCard QLabel {
            font-size: 9pt;
            color: #eee;
        }
        #ActionCard QComboBox {
            border: 1px solid #bbb;
            border-radius: 4px;
            padding: 4px 6px;
            background-color: rgba(45, 45, 45, 0.9);
            color: #eee;
        }
        #ActionCard QLineEdit, #ActionCard QSpinBox, #ActionCard QDoubleSpinBox {
            border: 1px solid #bbb;
            border-radius: 4px;
            padding: 4px 6px;
            background-color: rgba(45, 45, 45, 0.9);
            color: #eee;
        }
        #ActionCard QComboBox:focus, #ActionCard QLineEdit:focus, #ActionCard QSpinBox:focus, #ActionCard QDoubleSpinBox:focus {
            border-color: #90caf9;
        }
        #ActionCard QPushButton {
            background-color: #90caf9;
            border: none;
            border-radius: 4px;
            padding: 2px;
            color: #1e1e1e;
        }
        #ActionCard QPushButton:hover {
            background-color: #64b5f6;
        }
        #ActionCard QPushButton:pressed {
            background-color: #42a5f5;
        }
    """

    # ---------- 动作卡片滚动区 (objectName: ActionCardView) ----------
    ACTION_CARD_VIEW_LIGHT = """
        #ActionCardView {
            background: transparent;
            border: none;
        }
        #ActionCardView QScrollBar:vertical {
            width: 6px;
            background: #e0e0e0;
            border-radius: 3px;
        }
        #ActionCardView QScrollBar::handle:vertical {
            background: #c0c0c0;
            border-radius: 3px;
            min-height: 20px;
        }
        #ActionCardView QScrollBar::add-line:vertical,
        #ActionCardView QScrollBar::sub-line:vertical {
            height: 0px;
        }
    """
    ACTION_CARD_VIEW_DARK = """
        #ActionCardView {
            background: transparent;
            border: none;
        }
        #ActionCardView QScrollBar:vertical {
            width: 6px;
            background: #3a3a3a;
            border-radius: 3px;
        }
        #ActionCardView QScrollBar::handle:vertical {
            background: #666;
            border-radius: 3px;
            min-height: 20px;
        }
        #ActionCardView QScrollBar::add-line:vertical,
        #ActionCardView QScrollBar::sub-line:vertical {
            height: 0px;
        }
    """

    # ---------- 自动化编辑主区域组 (EditProjectGroup, EditStepGroup, EditActionGroup) ----------
    EDIT_GROUP_LIGHT = """
        QGroupBox {
            background-color: #ffffff;
            border: 1px solid #d0d0d0;
            border-radius: 8px;
            padding: 6px;
        }
        QGroupBox::title {
            color: #333;
        }
    """

    EDIT_GROUP_DARK = """
        QGroupBox {
            background-color: #191a1c;
            border: 1px solid #555;
            border-radius: 8px;
            padding: 6px;
        }
        QGroupBox::title {
            color: #ffffff;
        }
    """

    # ---------- 自动化执行视图 (ExecuteView) ----------
    EXECUTE_VIEW_LIGHT = """
        #ExecuteView {
            background-color: transparent;
            border: 1px solid #d0d0d0;
            border-radius: 12px;
            padding: 8px;
        }
        #ExecuteView QTreeView {
            padding: 4px;
            margin: 10px;
            border: 1px solid #d0d0d0;
            border-radius: 4px;
            background-color: #e8eaed;
        }
        #ExecuteView QTreeView::item {
            height: 30px;
            min-height: 30px;
            max-height: 30px;
            color: #333;
        }
        #ExecuteView QTreeView::item:selected {
            background-color: #cfe3f7;
        }
    """
    EXECUTE_VIEW_DARK = """
        #ExecuteView {
            background-color: #191a1c;
            border: 1px solid #555;
            border-radius: 12px;
            padding: 8px;
        }
        #ExecuteView QTreeView {
            padding: 4px;
            margin: 10px;
            border: 1px solid #555;
            border-radius: 4px;
            background-color: #373737;
        }
        #ExecuteView QTreeView::item {
            height: 30px;
            min-height: 30px;
            max-height: 30px;
            color: #eee;
        }
        #ExecuteView QTreeView::item:selected {
            background-color: #1e3a5f;
        }
        #ExecuteView QLabel {
            color: #eee;
            background: transparent;
        }
    """
    # ---------- 日志视图 (LogsView) ----------
    LOGS_VIEW_LIGHT = """
        #LogsView {
            background-color: transparent;
            border: 1px solid #d0d0d0;
            border-radius: 12px;
            padding: 8px;
        }
        #LogsView QTextEdit {
            padding: 4px;
            border: none;
            background-color: transparent;
            color: #333;
            font-family: Consolas, monospace;
            font-size: 10pt;
        }
        #LogsView QLabel {
            color: #333;
            background: transparent;
        }
    """
    LOGS_VIEW_DARK = """
        #LogsView {
            background-color: #191a1c;
            border: 1px solid #555;
            border-radius: 12px;
        }
        #LogsView QTextEdit {
            padding: 4px;
            border: none;
            background-color: transparent;
            color: #eee;
            font-family: Consolas, monospace;
            font-size: 10pt;
        }
        #LogsView QLabel {
            color: #eee;
            background: transparent;
        }
    """

    # ---------- 定时任务视图 (TaskView) ----------

    TASK_VIEW_LIGHT = """
        #TaskView {
            background-color: transparent;
            border: none;
            padding: 8px;
        }
        #TaskView QListWidget {
            border: 1px solid #d0d0d0;
            border-radius: 6px;
            background: #e8eaed;
            padding: 4px;
        }
        #TaskView QListWidget::item {
            padding: 0px;
            border-bottom: 1px solid #dfe1e5;
        }
        #TaskView QListWidget::item:selected {
            background-color: transparent;
        }
        #TaskView QListWidget QScrollBar:vertical {
            width: 6px;
            background: #e0e0e0;
            border-radius: 3px;
            margin: 0px;
        }
        #TaskView QListWidget QScrollBar::handle:vertical {
            background: #c0c0c0;
            border-radius: 3px;
            min-height: 20px;
        }
        #TaskView QListWidget QScrollBar::add-line:vertical,
        #TaskView QListWidget QScrollBar::sub-line:vertical {
            height: 0px;
            width: 0px;
        }
        #TaskView QListWidget QScrollBar::add-page:vertical,
        #TaskView QListWidget QScrollBar::sub-page:vertical {
            background: transparent;
        }
        #TaskView QLabel { color: #333; }
        #TaskView #taskAddBtn, #TaskView #taskDeleteBtn, #TaskView #taskRunBtn {
            background-color: #1976d2;
            color: white;
            border: none;
            padding: 5px 12px;
            border-radius: 4px;
            font-weight: 500;
            min-width: 90px;
        }
        #TaskView #taskAddBtn:hover, #TaskView #taskDeleteBtn:hover, #TaskView #taskRunBtn:hover {
            background-color: #1565c0;
        }
        #TaskView #taskAddBtn:pressed, #TaskView #taskDeleteBtn:pressed, #TaskView #taskRunBtn:pressed {
            background-color: #0d47a1;
        }
        #TaskView #taskAddBtn:disabled, #TaskView #taskDeleteBtn:disabled, #TaskView #taskRunBtn:disabled {
            background-color: #b0b0b0;
            color: #e0e0e0;
        }
        #TaskView QCheckBox { color: #333; }
    """

    TASK_VIEW_DARK = """
        #TaskView {
            background-color: #191a1c;
            border: none;
            padding: 8px;
        }
        #TaskView QListWidget {
            border: 1px solid #555;
            border-radius: 6px;
            background: #373737;
            padding: 4px;
        }
        #TaskView QListWidget::item {
            padding: 0px;
            border-bottom: 1px solid #3a3a3a;
        }
        #TaskView QListWidget::item:selected {
            background-color: transparent;
        }
        #TaskView QListWidget QScrollBar:vertical {
            width: 6px;
            background: rgba(58, 58, 58, 0.5);
            border-radius: 3px;
            margin: 0px;
        }
        #TaskView QListWidget QScrollBar::handle:vertical {
            background: #666;
            border-radius: 3px;
            min-height: 20px;
        }
        #TaskView QListWidget QScrollBar::add-line:vertical,
        #TaskView QListWidget QScrollBar::sub-line:vertical {
            height: 0px;
            width: 0px;
        }
        #TaskView QListWidget QScrollBar::add-page:vertical,
        #TaskView QListWidget QScrollBar::sub-page:vertical {
            background: transparent;
        }
        #TaskView QLabel { color: #eee; }
        #TaskView #taskAddBtn, #TaskView #taskDeleteBtn, #TaskView #taskRunBtn {
            background-color: #1976d2;
            color: white;
            border: none;
            padding: 5px 12px;
            border-radius: 4px;
            font-weight: 500;
            min-width: 90px;
        }
        #TaskView #taskAddBtn:hover, #TaskView #taskDeleteBtn:hover, #TaskView #taskRunBtn:hover {
            background-color: #1565c0;
        }
        #TaskView #taskAddBtn:pressed, #TaskView #taskDeleteBtn:pressed, #TaskView #taskRunBtn:pressed {
            background-color: #0d47a1;
        }
        #TaskView #taskAddBtn:disabled, #TaskView #taskDeleteBtn:disabled, #TaskView #taskRunBtn:disabled {
            background-color: #555;
            color: #888;
        }
        #TaskView QCheckBox { color: #eee; }
    """

    # ---------- 元素管理器 (ElementManagerView) ----------
    ELEMENT_MANAGER_LIGHT = """
        #ElementManagerView {
            background-color: transparent;
        }
        #ElementManagerView QTableWidget {
            gridline-color: #d0d0d0;
            border: none;
            background-color: #e8eaed;
        }
        #ElementManagerView QTableWidget::item {
            border: none;
            background-color: #e8eaed;
            color: black;
        }
        #ElementManagerView QTableWidget::item:selected {
            background-color: #e3f2fd;
        }
        #ElementManagerView QTableCornerButton::section {
            background-color: #e8eaed;
            border: 1px solid #d0d0d0;
        }
        #ElementManagerView QHeaderView::section {
            background-color: #e8eaed;
            color: #333;
            border: 1px solid #d0d0d0;
            padding: 4px;
            font-weight: bold;
        }
        #ElementManagerView QPushButton {
            background-color: #1976d2;
            color: white;
            border: none;
            padding: 5px 12px;
            border-radius: 4px;
            font-weight: 500;
            min-width: 100px;
        }
        #ElementManagerView QPushButton:hover {
            background-color: #1565c0;
        }
        #ElementManagerView QPushButton:pressed {
            background-color: #0d47a1;
        }
        #ElementManagerView QPushButton:disabled {
            background-color: #b0b0b0;
            color: #e0e0e0;
        }
        #ElementManagerView QLineEdit, #ElementManagerView QComboBox {
            border: 1px solid #d0d0d0;
            border-radius: 4px;
            padding: 4px 6px;
            background-color: white;
            color: #333;
        }
        #ElementManagerView QLineEdit:focus, #ElementManagerView QComboBox:focus {
            border-color: #1976d2;
        }
        #ElementManagerView QComboBox QAbstractItemView {
            background-color: white;
            color: #333;
            border: 1px solid #d0d0d0;
            border-radius: 4px;
            selection-background-color: #1976d2;
            selection-color: white;
            outline: none;
            padding: 2px;
        }
        #ElementManagerView QComboBox QAbstractItemView::item {
            background-color: white;
            color: #333;
            min-height: 22px;
            padding: 2px 8px;
        }
        #ElementManagerView QComboBox QAbstractItemView::item:hover {
            background-color: #1565c0;
            color: white;
        }
        #ElementManagerView QComboBox QAbstractItemView::item:selected {
            background-color: #1976d2;
            color: white;
        }
        #ElementManagerView QTableWidget QScrollBar:vertical {
            width: 6px;
            background: #e0e0e0;
            border-radius: 3px;
            margin: 0px;
        }
        #ElementManagerView QTableWidget QScrollBar::handle:vertical {
            background: #c0c0c0;
            border-radius: 3px;
            min-height: 20px;
        }
        #ElementManagerView QTableWidget QScrollBar::add-line:vertical,
        #ElementManagerView QTableWidget QScrollBar::sub-line:vertical {
            height: 0px;
            width: 0px;
        }
        #ElementManagerView QTableWidget QScrollBar::add-page:vertical,
        #ElementManagerView QTableWidget QScrollBar::sub-page:vertical {
            background: transparent;
        }
        #ElementManagerView QTableWidget QScrollBar:horizontal {
            height: 6px;
            background: #e0e0e0;
            border-radius: 3px;
            margin: 0px;
        }
        #ElementManagerView QTableWidget QScrollBar::handle:horizontal {
            background: #c0c0c0;
            border-radius: 3px;
            min-width: 20px;
        }
        #ElementManagerView QTableWidget QScrollBar::add-line:horizontal,
        #ElementManagerView QTableWidget QScrollBar::sub-line:horizontal {
            height: 0px;
            width: 0px;
        }
        #ElementManagerView QTableWidget QScrollBar::add-page:horizontal,
        #ElementManagerView QTableWidget QScrollBar::sub-page:horizontal {
            background: transparent;
        }
    """
    ELEMENT_MANAGER_DARK = """
        #ElementManagerView {
            background-color: transparent;
        }
        #ElementManagerView QTableWidget {
            gridline-color: #555;
            border: none;
            background-color: #323232;
        }
        #ElementManagerView QTableWidget::item {
            border: none;
            background-color: #323232;
            color: #eee;
        }
        #ElementManagerView QTableWidget::item:selected {
            background-color: #1e3a5f;
        }
        #ElementManagerView QTableCornerButton::section {
            background-color: #3a3a3a;
            border: 1px solid #555;
        }
        #ElementManagerView QHeaderView::section {
            background-color: #3a3a3a;
            color: #eee;
            border: 1px solid #555;
            padding: 4px;
            font-weight: bold;
        }
        #ElementManagerView QPushButton {
            background-color: #1976d2;
            color: white;
            border: none;
            padding: 5px 12px;
            border-radius: 4px;
            font-weight: 500;
            min-width: 100px;
        }
        #ElementManagerView QPushButton:hover {
            background-color: #1565c0;
        }
        #ElementManagerView QPushButton:pressed {
            background-color: #0d47a1;
        }
        #ElementManagerView QPushButton:disabled {
            background-color: #555;
            color: #888;
        }
        #ElementManagerView QLineEdit, #ElementManagerView QComboBox {
            border: 1px solid #555;
            border-radius: 4px;
            padding: 4px 6px;
            background-color: #3c3c3c;
            color: #eee;
        }
        #ElementManagerView QLineEdit:focus, #ElementManagerView QComboBox:focus {
            border-color: #90caf9;
        }
        #ElementManagerView QComboBox QAbstractItemView {
            background-color: #3c3c3c;
            color: #eee;
            border: 1px solid #555;
            border-radius: 4px;
            selection-background-color: #90caf9;
            selection-color: #1e1e1e;
            outline: none;
            padding: 2px;
        }
        #ElementManagerView QComboBox QAbstractItemView::item {
            background-color: #3c3c3c;
            color: #eee;
            min-height: 22px;
            padding: 2px 8px;
        }
        #ElementManagerView QComboBox QAbstractItemView::item:hover {
            background-color: #64b5f6;
            color: #1e1e1e;
        }
        #ElementManagerView QComboBox QAbstractItemView::item:selected {
            background-color: #90caf9;
            color: #1e1e1e;
        }
        #ElementManagerView QTableWidget QScrollBar:vertical {
            width: 6px;
            background: rgba(58, 58, 58, 0.5);
            border-radius: 3px;
            margin: 0px;
        }
        #ElementManagerView QTableWidget QScrollBar::handle:vertical {
            background: #666;
            border-radius: 3px;
            min-height: 20px;
        }
        #ElementManagerView QTableWidget QScrollBar::add-line:vertical,
        #ElementManagerView QTableWidget QScrollBar::sub-line:vertical {
            height: 0px;
            width: 0px;
        }
        #ElementManagerView QTableWidget QScrollBar::add-page:vertical,
        #ElementManagerView QTableWidget QScrollBar::sub-page:vertical {
            background: transparent;
        }
        #ElementManagerView QTableWidget QScrollBar:horizontal {
            height: 6px;
            background: rgba(58, 58, 58, 0.5);
            border-radius: 3px;
            margin: 0px;
        }
        #ElementManagerView QTableWidget QScrollBar::handle:horizontal {
            background: #666;
            border-radius: 3px;
            min-width: 20px;
        }
        #ElementManagerView QTableWidget QScrollBar::add-line:horizontal,
        #ElementManagerView QTableWidget QScrollBar::sub-line:horizontal {
            height: 0px;
            width: 0px;
        }
        #ElementManagerView QTableWidget QScrollBar::add-page:horizontal,
        #ElementManagerView QTableWidget QScrollBar::sub-page:horizontal {
            background: transparent;
        }
    """

    # ---------- 帮助视图 (HelpView) ----------
    HELP_VIEW_LIGHT = """
        #HelpView {
            background-color: transparent;
            border: 1px solid #d0d0d0;
            border-radius: 8px;
            padding: 4px;
        }
        #HelpView QTreeView {
            background-color: transparent;
            border: none;
            outline: none;
        }
        #HelpView QTreeView::item {
            height: 30px;
            color: #333;
            border: none;
            outline: none;
        }
        #HelpView QTreeView::item:selected,
        #HelpView QTreeView::item:selected:active,
        #HelpView QTreeView::item:selected:!active,
        #HelpView QTreeView::item:selected:focus {
            background-color: #d0e4f7;
            color: #1a1a1a;
            border: none;
            outline: none;
        }
        #HelpView QTreeView::item:hover:!selected {
            background-color: #dfe2e6;
        }
        #HelpView QTreeView::branch {
            background: transparent;
        }
        #HelpView QTreeView::branch:selected,
        #HelpView QTreeView::branch:selected:active,
        #HelpView QTreeView::branch:selected:!active {
            background: transparent;
        }
        #HelpView QTreeView QScrollBar:vertical {
            width: 6px;
            background: #e0e0e0;
            border-radius: 3px;
            margin: 0px;
        }
        #HelpView QTreeView QScrollBar::handle:vertical {
            background: #c0c0c0;
            border-radius: 3px;
            min-height: 20px;
        }
        #HelpView QTreeView QScrollBar::add-line:vertical,
        #HelpView QTreeView QScrollBar::sub-line:vertical {
            height: 0px;
            width: 0px;
        }
        #HelpView QTreeView QScrollBar::add-page:vertical,
        #HelpView QTreeView QScrollBar::sub-page:vertical {
            background: transparent;
        }
        #HelpView QTextEdit {
            border: none;
            background: transparent;
            color: #333;
            padding: 20px 24px;
            font-size: 14px;
            line-height: 1.8;
        }
        #HelpView QTextEdit QScrollBar:vertical {
            width: 6px;
            background: #e0e0e0;
            border-radius: 3px;
            margin: 0px;
        }
        #HelpView QTextEdit QScrollBar::handle:vertical {
            background: #c0c0c0;
            border-radius: 3px;
            min-height: 20px;
        }
        #HelpView QTextEdit QScrollBar::add-line:vertical,
        #HelpView QTextEdit QScrollBar::sub-line:vertical {
            height: 0px;
            width: 0px;
        }
        #HelpView QTextEdit QScrollBar::add-page:vertical,
        #HelpView QTextEdit QScrollBar::sub-page:vertical {
            background: transparent;
        }
    """

    HELP_VIEW_DARK = """
        #HelpView {
            background-color: transparent;
            border: 1px solid #555;
            border-radius: 8px;
            padding: 4px;
        }
        #HelpView QTreeView {
            background-color: transparent;
            border: none;
            outline: none;
        }
        #HelpView QTreeView::item {
            height: 30px;
            color: #eee;
            border: none;
            outline: none;
        }
        #HelpView QTreeView::item:selected,
        #HelpView QTreeView::item:selected:active,
        #HelpView QTreeView::item:selected:!active,
        #HelpView QTreeView::item:selected:focus {
            background-color: #1e3a5f;
            color: #ffffff;
            border: none;
            outline: none;
        }
        #HelpView QTreeView::item:hover:!selected {
            background-color: rgba(74, 74, 74, 0.6);
        }
        #HelpView QTreeView::branch {
            background: transparent;
        }
        #HelpView QTreeView::branch:selected,
        #HelpView QTreeView::branch:selected:active,
        #HelpView QTreeView::branch:selected:!active {
            background: transparent;
        }
        #HelpView QTreeView QScrollBar:vertical {
            width: 6px;
            background: rgba(58, 58, 58, 0.5);
            border-radius: 3px;
            margin: 0px;
        }
        #HelpView QTreeView QScrollBar::handle:vertical {
            background: #666;
            border-radius: 3px;
            min-height: 20px;
        }
        #HelpView QTreeView QScrollBar::add-line:vertical,
        #HelpView QTreeView QScrollBar::sub-line:vertical {
            height: 0px;
            width: 0px;
        }
        #HelpView QTreeView QScrollBar::add-page:vertical,
        #HelpView QTreeView QScrollBar::sub-page:vertical {
            background: transparent;
        }
        #HelpView QTextEdit {
            border: none;
            background: transparent;
            color: #eee;
            padding: 20px 24px;
            font-size: 14px;
            line-height: 1.8;
        }
        #HelpView QTextEdit QScrollBar:vertical {
            width: 6px;
            background: rgba(58, 58, 58, 0.5);
            border-radius: 3px;
            margin: 0px;
        }
        #HelpView QTextEdit QScrollBar::handle:vertical {
            background: #666;
            border-radius: 3px;
            min-height: 20px;
        }
        #HelpView QTextEdit QScrollBar::add-line:vertical,
        #HelpView QTextEdit QScrollBar::sub-line:vertical {
            height: 0px;
            width: 0px;
        }
        #HelpView QTextEdit QScrollBar::add-page:vertical,
        #HelpView QTextEdit QScrollBar::sub-page:vertical {
            background: transparent;
        }
    """

    # ---------- 各种对话框 ----------
    # 确认删除对话框 (ConfirmDeleteDialog) - objectName: ConfirmDeleteDialog
    CONFIRM_DELETE_LIGHT = """
        #ConfirmDeleteDialog QFrame#container {
            background-color: white;
            border-radius: 12px;
            border: 1px solid #d0d0d0;
        }
        #ConfirmDeleteDialog QLabel {
            color: #333;
        }
        #ConfirmDeleteDialog QPushButton {
            border: none;
            border-radius: 6px;
            font-size: 14px;
            font-weight: 600;
        }
        #ConfirmDeleteDialog QPushButton#cancelBtn {
            background-color: #f0f0f0;
            color: #333;
        }
        #ConfirmDeleteDialog QPushButton#cancelBtn:hover {
            background-color: #e0e0e0;
        }
        #ConfirmDeleteDialog QPushButton#confirmBtn {
            background-color: #e74c3c;
            color: white;
        }
        #ConfirmDeleteDialog QPushButton#confirmBtn:hover {
            background-color: #c0392b;
        }
    """
    CONFIRM_DELETE_DARK = """
        #ConfirmDeleteDialog QFrame#container {
            background-color: #3c3c3c;
            border-radius: 12px;
            border: 1px solid #555;
        }
        #ConfirmDeleteDialog QLabel {
            color: #eee;
        }
        #ConfirmDeleteDialog QPushButton {
            border: none;
            border-radius: 6px;
            font-size: 14px;
            font-weight: 600;
        }
        #ConfirmDeleteDialog QPushButton#cancelBtn {
            background-color: #555;
            color: #eee;
        }
        #ConfirmDeleteDialog QPushButton#cancelBtn:hover {
            background-color: #666;
        }
        #ConfirmDeleteDialog QPushButton#confirmBtn {
            background-color: #e74c3c;
            color: white;
        }
        #ConfirmDeleteDialog QPushButton#confirmBtn:hover {
            background-color: #c0392b;
        }
    """

    # 输入对话框 (InputDialog) - objectName: InputDialog
    INPUT_DIALOG_LIGHT = """
        #InputDialog QFrame#container {
            background-color: white;
            border-radius: 12px;
            border: 1px solid #d0d0d0;
        }
        #InputDialog QLabel {
            color: #333;
        }
        #InputDialog QLineEdit {
            border: 1px solid #d0d0d0;
            border-radius: 6px;
            padding: 8px 12px;
            font-size: 14px;
            background-color: #f8f9fa;
            color: #333;
        }
        #InputDialog QLineEdit:focus {
            border-color: #1976d2;
            background-color: white;
        }
        #InputDialog QPushButton {
            border: none;
            border-radius: 6px;
            font-size: 14px;
            font-weight: 600;
        }
        #InputDialog QPushButton#cancelBtn {
            background-color: #f0f0f0;
            color: #333;
        }
        #InputDialog QPushButton#cancelBtn:hover {
            background-color: #e0e0e0;
        }
        #InputDialog QPushButton#confirmBtn {
            background-color: #1976d2;
            color: white;
        }
        #InputDialog QPushButton#confirmBtn:hover {
            background-color: #1565c0;
        }
    """
    INPUT_DIALOG_DARK = """
        #InputDialog QFrame#container {
            background-color: #3c3c3c;
            border-radius: 12px;
            border: 1px solid #555;
        }
        #InputDialog QLabel {
            color: #eee;
        }
        #InputDialog QLineEdit {
            border: 1px solid #555;
            border-radius: 6px;
            padding: 8px 12px;
            font-size: 14px;
            background-color: #2d2d2d;
            color: #eee;
        }
        #InputDialog QLineEdit:focus {
            border-color: #90caf9;
            background-color: #3c3c3c;
        }
        #InputDialog QPushButton {
            border: none;
            border-radius: 6px;
            font-size: 14px;
            font-weight: 600;
        }
        #InputDialog QPushButton#cancelBtn {
            background-color: #555;
            color: #eee;
        }
        #InputDialog QPushButton#cancelBtn:hover {
            background-color: #666;
        }
        #InputDialog QPushButton#confirmBtn {
            background-color: #1976d2;
            color: white;
        }
        #InputDialog QPushButton#confirmBtn:hover {
            background-color: #64b5f6;
        }
    """

    # 警告对话框 (WarningDialog) - objectName: WarningDialog
    WARNING_DIALOG_LIGHT = """
        #WarningDialog QFrame#container {
            background-color: white;
            border-radius: 12px;
            border: 1px solid #d0d0d0;
        }
        #WarningDialog QLabel {
            color: #333;
        }
        #WarningDialog QPushButton {
            border: none;
            border-radius: 6px;
            font-size: 14px;
            font-weight: 600;
        }
        #WarningDialog QPushButton#okBtn {
            background-color: #1976d2;
            color: white;
        }
        #WarningDialog QPushButton#okBtn:hover {
            background-color: #1565c0;
        }
    """
    WARNING_DIALOG_DARK = """
        #WarningDialog QFrame#container {
            background-color: #3c3c3c;
            border-radius: 12px;
            border: 1px solid #555;
        }
        #WarningDialog QLabel {
            color: #eee;
        }
        #WarningDialog QPushButton {
            border: none;
            border-radius: 6px;
            font-size: 14px;
            font-weight: 600;
        }
        #WarningDialog QPushButton#okBtn {
            background-color: #1976d2;
            color: white;
        }
        #WarningDialog QPushButton#okBtn:hover {
            background-color: #64b5f6;
        }
    """

    # 错误对话框 (ErrorDialog) - objectName: ErrorDialog
    ERROR_DIALOG_LIGHT = """
        #ErrorDialog QFrame#container {
            background-color: white;
            border-radius: 12px;
            border: 1px solid #d0d0d0;
        }
        #ErrorDialog QLabel {
            color: #333;
        }
        #ErrorDialog QPushButton {
            border: none;
            border-radius: 6px;
            font-size: 14px;
            font-weight: 600;
        }
        #ErrorDialog QPushButton#okBtn {
            background-color: #1976d2;
            color: white;
        }
        #ErrorDialog QPushButton#okBtn:hover {
            background-color: #1565c0;
        }
    """
    ERROR_DIALOG_DARK = """
        #ErrorDialog QFrame#container {
            background-color: #3c3c3c;
            border-radius: 12px;
            border: 1px solid #555;
        }
        #ErrorDialog QLabel {
            color: #eee;
        }
        #ErrorDialog QPushButton {
            border: none;
            border-radius: 6px;
            font-size: 14px;
            font-weight: 600;
        }
        #ErrorDialog QPushButton#okBtn {
            background-color: #1976d2;
            color: white;
        }
        #ErrorDialog QPushButton#okBtn:hover {
            background-color: #64b5f6;
        }
    """


    # 通用问题询问对话框 (QuestionDialog) - objectName: QuestionDialog
    QUESTION_DIALOG_LIGHT = """
        #QuestionDialog QFrame#container {
            background-color: white;
            border-radius: 12px;
            border: 1px solid #d0d0d0;
        }
        #QuestionDialog QLabel {
            color: #333;
        }
        #QuestionDialog QPushButton {
            border: none;
            border-radius: 6px;
            font-size: 14px;
            font-weight: 600;
        }
        #QuestionDialog QPushButton#cancelBtn {
            background-color: #f0f0f0;
            color: #333;
        }
        #QuestionDialog QPushButton#cancelBtn:hover {
            background-color: #e0e0e0;
        }
        #QuestionDialog QPushButton#confirmBtn {
            background-color: #1976d2;
            color: white;
        }
        #QuestionDialog QPushButton#confirmBtn:hover {
            background-color: #1565c0;
        }
    """
    QUESTION_DIALOG_DARK = """
        #QuestionDialog QFrame#container {
            background-color: #3c3c3c;
            border-radius: 12px;
            border: 1px solid #555;
        }
        #QuestionDialog QLabel {
            color: #eee;
        }
        #QuestionDialog QPushButton {
            border: none;
            border-radius: 6px;
            font-size: 14px;
            font-weight: 600;
        }
        #QuestionDialog QPushButton#cancelBtn {
            background-color: #555;
            color: #eee;
        }
        #QuestionDialog QPushButton#cancelBtn:hover {
            background-color: #666;
        }
        #QuestionDialog QPushButton#confirmBtn {
            background-color: #1976d2;
            color: white;
        }
        #QuestionDialog QPushButton#confirmBtn:hover {
            background-color: #64b5f6;
        }
    """
    # ---------- 设置对话框 (SettingsDialog) ----------
    SETTINGS_DIALOG_LIGHT = """
        #SettingsDialog {
            background-color: white;
        }
        #SettingsDialog QLabel {
            color: #333;
        }
        #SettingsDialog QLineEdit, #SettingsDialog QComboBox, #SettingsDialog QSlider {
            border: 1px solid #d0d0d0;
            border-radius: 4px;
            padding: 4px 6px;
            background-color: white;
            color: #333;
        }
        #SettingsDialog QPushButton {
            border: none;
            border-radius: 6px;
            font-size: 14px;
            font-weight: 600;
        }
        #SettingsDialog QPushButton#browseBtn {
            background-color: #3498db;
            color: white;
        }
        #SettingsDialog QPushButton#browseBtn:hover {
            background-color: #5dade2;
        }
        #SettingsDialog QPushButton#okBtn {
            background-color: #27ae60;
            color: white;
        }
        #SettingsDialog QPushButton#okBtn:hover {
            background-color: #2ecc71;
        }
        #SettingsDialog QPushButton#cancelBtn {
            background-color: #f0f0f0;
            color: #333;
        }
        #SettingsDialog QPushButton#cancelBtn:hover {
            background-color: #e0e0e0;
        }
    """
    SETTINGS_DIALOG_DARK = """
        #SettingsDialog {
            background-color: #2d2d2d;
        }
        #SettingsDialog QLabel {
            color: #eee;
        }
        #SettingsDialog QLineEdit, #SettingsDialog QComboBox, #SettingsDialog QSlider {
            border: 1px solid #555;
            border-radius: 4px;
            padding: 4px 6px;
            background-color: #3c3c3c;
            color: #eee;
        }
        #SettingsDialog QPushButton {
            border: none;
            border-radius: 6px;
            font-size: 14px;
            font-weight: 600;
        }
        #SettingsDialog QPushButton#browseBtn {
            background-color: #3498db;
            color: white;
        }
        #SettingsDialog QPushButton#browseBtn:hover {
            background-color: #5dade2;
        }
        #SettingsDialog QPushButton#okBtn {
            background-color: #27ae60;
            color: white;
        }
        #SettingsDialog QPushButton#okBtn:hover {
            background-color: #81c784;
        }
        #SettingsDialog QPushButton#cancelBtn {
            background-color: #555;
            color: #eee;
        }
        #SettingsDialog QPushButton#cancelBtn:hover {
            background-color: #666;
        }
    """

    # ---------- 元素选择对话框 (ElementSelectorDialog) ----------
    ELEMENT_SELECTOR_LIGHT = """
        #ElementSelectorDialog {
            background-color: white;
        }
        #ElementSelectorDialog QTableWidget {
            background-color: white;
            gridline-color: #d0d0d0;
            border: 1px solid #d0d0d0;
        }
        #ElementSelectorDialog QTableWidget::item {
            color: #333;
        }
        #ElementSelectorDialog QHeaderView::section {
            background-color: #f5f6fa;
            color: #333;
            border: 1px solid #d0d0d0;
        }
        #ElementSelectorDialog QLineEdit, #ElementSelectorDialog QComboBox {
            border: 1px solid #d0d0d0;
            border-radius: 4px;
            padding: 4px 6px;
            background-color: white;
            color: #333;
        }
        #ElementSelectorDialog QPushButton {
            border: none;
            border-radius: 6px;
            font-size: 14px;
            font-weight: 600;
        }
        #ElementSelectorDialog QPushButton#selectBtn {
            background-color: #1976d2;
            color: white;
        }
        #ElementSelectorDialog QPushButton#selectBtn:hover {
            background-color: #1565c0;
        }
        #ElementSelectorDialog QPushButton#cancelBtn {
            background-color: #f0f0f0;
            color: #333;
        }
        #ElementSelectorDialog QPushButton#cancelBtn:hover {
            background-color: #e0e0e0;
        }
    """
    ELEMENT_SELECTOR_DARK = """
        #ElementSelectorDialog {
            background-color: #2d2d2d;
        }
        #ElementSelectorDialog QTableWidget {
            background-color: #2d2d2d;
            gridline-color: #555;
            border: 1px solid #555;
        }
        #ElementSelectorDialog QTableWidget::item {
            color: #eee;
        }
        #ElementSelectorDialog QHeaderView::section {
            background-color: #3a3a3a;
            color: #eee;
            border: 1px solid #555;
        }
        #ElementSelectorDialog QLineEdit, #ElementSelectorDialog QComboBox {
            border: 1px solid #555;
            border-radius: 4px;
            padding: 4px 6px;
            background-color: #3c3c3c;
            color: #eee;
        }
        #ElementSelectorDialog QPushButton {
            border: none;
            border-radius: 6px;
            font-size: 14px;
            font-weight: 600;
        }
        #ElementSelectorDialog QPushButton#selectBtn {
            background-color: #1976d2;
            color: white;
        }
        #ElementSelectorDialog QPushButton#selectBtn:hover {
            background-color: #64b5f6;
        }
        #ElementSelectorDialog QPushButton#cancelBtn {
            background-color: #555;
            color: #eee;
        }
        #ElementSelectorDialog QPushButton#cancelBtn:hover {
            background-color: #666;
        }
    """

    # ---------- 编辑任务对话框 (TaskEditDialog) ----------
    TASK_EDIT_DIALOG_LIGHT = """
        #TaskEditDialog QFrame#container {
            background-color: white;
            border-radius: 12px;
            border: 1px solid #d0d0d0;
        }
        #TaskEditDialog QLabel {
            color: #333;
        }
        #TaskEditDialog QLineEdit, #TaskEditDialog QComboBox, #TaskEditDialog QDateTimeEdit, #TaskEditDialog QSpinBox {
            border: 1px solid #d0d0d0;
            border-radius: 6px;
            padding: 6px 10px;
            font-size: 14px;
            background-color: white;
            color: #333;
        }
        #TaskEditDialog QLineEdit:focus, #TaskEditDialog QComboBox:focus, #TaskEditDialog QDateTimeEdit:focus, #TaskEditDialog QSpinBox:focus {
            border-color: #1976d2;
        }
        #TaskEditDialog QRadioButton {
            color: #333;
        }
        #TaskEditDialog QCheckBox {
            color: #333;
        }
        #TaskEditDialog QComboBox QAbstractItemView {
            background-color: white;
            color: #333;
            border: 1px solid #d0d0d0;
            border-radius: 6px;
            selection-background-color: #1976d2;
            selection-color: white;
            outline: none;
            padding: 4px;
        }
        #TaskEditDialog QComboBox QAbstractItemView::item {
            background-color: white;
            color: #333;
            min-height: 24px;
            padding: 4px 10px;
            margin: 1px 2px;
            border: none;
            border-radius: 4px;
        }
        #TaskEditDialog QComboBox QAbstractItemView::item:hover {
            background-color: #e8f0fe;
            color: #1976d2;
        }
        #TaskEditDialog QComboBox QAbstractItemView::item:selected {
            background-color: #1976d2;
            color: white;
        }
        #TaskEditDialog QPushButton {
            border: none;
            border-radius: 6px;
            font-size: 14px;
            font-weight: 600;
        }
        #TaskEditDialog QPushButton#cancelBtn {
            background-color: #f0f0f0;
            color: #333;
        }
        #TaskEditDialog QPushButton#cancelBtn:hover {
            background-color: #e0e0e0;
        }
        #TaskEditDialog QPushButton#saveBtn {
            background-color: #1976d2;
            color: white;
        }
        #TaskEditDialog QPushButton#saveBtn:hover {
            background-color: #1565c0;
        }
    """
    TASK_EDIT_DIALOG_DARK = """
        #TaskEditDialog QFrame#container {
            background-color: #3c3c3c;
            border-radius: 12px;
            border: 1px solid #555;
        }
        #TaskEditDialog QLabel {
            color: #eee;
        }
        #TaskEditDialog QLineEdit, #TaskEditDialog QComboBox, #TaskEditDialog QDateTimeEdit, #TaskEditDialog QSpinBox {
            border: 1px solid #555;
            border-radius: 6px;
            padding: 6px 10px;
            font-size: 14px;
            background-color: #2d2d2d;
            color: #eee;
        }
        #TaskEditDialog QLineEdit:focus, #TaskEditDialog QComboBox:focus, #TaskEditDialog QDateTimeEdit:focus, #TaskEditDialog QSpinBox:focus {
            border-color: #90caf9;
        }
        #TaskEditDialog QRadioButton {
            color: #eee;
        }
        #TaskEditDialog QCheckBox {
            color: #eee;
        }
        #TaskEditDialog QComboBox QAbstractItemView {
            background-color: #3c3c3c;
            color: #eee;
            border: 1px solid #555;
            border-radius: 6px;
            selection-background-color: #90caf9;
            selection-color: #1e1e1e;
            outline: none;
            padding: 4px;
        }
        #TaskEditDialog QComboBox QAbstractItemView::item {
            background-color: #3c3c3c;
            color: #eee;
            min-height: 24px;
            padding: 4px 10px;
            margin: 1px 2px;
            border: none;
            border-radius: 4px;
        }
        #TaskEditDialog QComboBox QAbstractItemView::item:hover {
            background-color: #64b5f6;
            color: #1e1e1e;
        }
        #TaskEditDialog QComboBox QAbstractItemView::item:selected {
            background-color: #90caf9;
            color: #1e1e1e;
        }
        #TaskEditDialog QPushButton {
            border: none;
            border-radius: 6px;
            font-size: 14px;
            font-weight: 600;
        }
        #TaskEditDialog QPushButton#cancelBtn {
            background-color: #555;
            color: #eee;
        }
        #TaskEditDialog QPushButton#cancelBtn:hover {
            background-color: #666;
        }
        #TaskEditDialog QPushButton#saveBtn {
            background-color: #1976d2;
            color: white;
        }
        #TaskEditDialog QPushButton#saveBtn:hover {
            background-color: #64b5f6;
        }
    """

    # ---------- 元素编辑对话框 (ElementEditDialog) ----------
    ELEMENT_EDIT_LIGHT = """
        #ElementEditDialog QFrame#container {
            background-color: white;
            border-radius: 12px;
            border: 1px solid #d0d0d0;
        }
        #ElementEditDialog QLabel {
            color: #333;
        }
        #ElementEditDialog QLineEdit, #ElementEditDialog QComboBox {
            border: 1px solid #d0d0d0;
            border-radius: 6px;
            padding: 6px 10px;
            font-size: 14px;
            background-color: #f8f9fa;
            color: #333;
        }
        #ElementEditDialog QLineEdit:focus, #ElementEditDialog QComboBox:focus {
            border-color: #1976d2;
            background-color: white;
        }

        #ElementEditDialog QComboBox QAbstractItemView {
            border: 1px solid #d0d0d0;
            border-radius: 6px;
            background-color: white;
            selection-background-color: #1976d2;
            selection-color: white;
            color: black;
            outline: none;
        }
        #ElementEditDialog QComboBox QAbstractItemView::item {
            padding: 5px 10px;
            color: black;
        }
        #ElementEditDialog QComboBox QAbstractItemView::item:hover {
            background-color: #e3f2fd;
            color: black;
        }
        #ElementEditDialog QPushButton {
            border: none;
            border-radius: 6px;
            font-size: 14px;
            font-weight: 600;
        }
        #ElementEditDialog QPushButton#cancelBtn {
            background-color: #f0f0f0;
            color: #333;
        }
        #ElementEditDialog QPushButton#cancelBtn:hover {
            background-color: #e0e0e0;
        }
        #ElementEditDialog QPushButton#okBtn {
            background-color: #1976d2;
            color: white;
        }
        #ElementEditDialog QPushButton#okBtn:hover {
            background-color: #1565c0;
        }
    """
    ELEMENT_EDIT_DARK = """
        #ElementEditDialog QFrame#container {
            background-color: #3c3c3c;
            border-radius: 12px;
            border: 1px solid #555;
        }
        #ElementEditDialog QLabel {
            color: #eee;
        }
        #ElementEditDialog QLineEdit, #ElementEditDialog QComboBox {
            border: 1px solid #555;
            border-radius: 6px;
            padding: 6px 10px;
            font-size: 14px;
            background-color: #2d2d2d;
            color: #eee;
        }
        #ElementEditDialog QLineEdit:focus, #ElementEditDialog QComboBox:focus {
            border-color: #90caf9;
            background-color: #3c3c3c;
        }
        #ElementEditDialog QComboBox QAbstractItemView {
            border: 1px solid #555;
            border-radius: 6px;
            background-color: #3c3c3c;
            selection-background-color: #90caf9;
            selection-color: #1e1e1e;
            color: #eee;
            outline: none;
        }
        #ElementEditDialog QComboBox QAbstractItemView::item {
            padding: 5px 10px;
            color: #eee;
        }
        #ElementEditDialog QComboBox QAbstractItemView::item:hover {
            background-color: #1e3a5f;
            color: #eee;
        }
        #ElementEditDialog QPushButton {
            border: none;
            border-radius: 6px;
            font-size: 14px;
            font-weight: 600;
        }
        #ElementEditDialog QPushButton#cancelBtn {
            background-color: #555;
            color: #eee;
        }
        #ElementEditDialog QPushButton#cancelBtn:hover {
            background-color: #666;
        }
        #ElementEditDialog QPushButton#okBtn {
            background-color: #1976d2;
            color: white;
        }
        #ElementEditDialog QPushButton#okBtn:hover {
            background-color: #64b5f6;
        }
    """

    # ---------- 更新步骤对话框 (UpdateStepDialog) ----------
    UPDATE_STEP_DIALOG_LIGHT = """
        #UpdateStepDialog QFrame#updateStepContainer {
            background-color: white;
            border-radius: 12px;
            border: 1px solid #d0d0d0;
        }
        #UpdateStepDialog QLabel {
            color: #333;
        }
        #UpdateStepDialog QLineEdit,
        #UpdateStepDialog QComboBox,
        #UpdateStepDialog QSpinBox,
        #UpdateStepDialog QDoubleSpinBox {
            border: 1px solid #d0d0d0;
            border-radius: 6px;
            padding: 6px 10px;
            font-size: 14px;
            background-color: white;
            color: #333;
        }
        #UpdateStepDialog QLineEdit:focus,
        #UpdateStepDialog QComboBox:focus,
        #UpdateStepDialog QSpinBox:focus,
        #UpdateStepDialog QDoubleSpinBox:focus {
            border-color: #1976d2;
        }
        #UpdateStepDialog QPushButton {
            border: none;
            border-radius: 6px;
            font-size: 14px;
            font-weight: 600;
        }
    """
    UPDATE_STEP_DIALOG_DARK = """
        #UpdateStepDialog QFrame#updateStepContainer {
            background-color: #3c3c3c;
            border-radius: 12px;
            border: 1px solid #555;
        }
        #UpdateStepDialog QLabel {
            color: #eee;
        }
        #UpdateStepDialog QLineEdit,
        #UpdateStepDialog QComboBox,
        #UpdateStepDialog QSpinBox,
        #UpdateStepDialog QDoubleSpinBox {
            border: 1px solid #555;
            border-radius: 6px;
            padding: 6px 10px;
            font-size: 14px;
            background-color: #2d2d2d;
            color: #eee;
        }
        #UpdateStepDialog QLineEdit:focus,
        #UpdateStepDialog QComboBox:focus,
        #UpdateStepDialog QSpinBox:focus,
        #UpdateStepDialog QDoubleSpinBox:focus {
            border-color: #90caf9;
        }
        #UpdateStepDialog QPushButton {
            border: none;
            border-radius: 6px;
            font-size: 14px;
            font-weight: 600;
        }
    """

    # ---------- 应用主题的方法 ----------
    @classmethod
    def apply_theme_to_widget(cls, widget: QWidget, theme_mode: ThemeMode):
        """为单个控件应用主题样式（通过 objectName 匹配）"""
        if theme_mode == ThemeMode.DARK:
            # 根据控件类型或 objectName 选择暗色样式
            style = cls._get_dark_style(widget)
        else:
            style = cls._get_light_style(widget)
        if style is not None:
            widget.setStyleSheet(style)

    @classmethod
    def _get_light_style(cls, widget):
        obj_name = widget.objectName()
        if obj_name == "MainWindow":
            return cls.MAIN_WINDOW_LIGHT
        elif obj_name == "ProjectTreeView":
            return cls.PROJECT_TREE_LIGHT
        elif obj_name == "StepListView":
            return cls.STEP_LIST_LIGHT
        elif obj_name == "ExecuteView":
            return cls.EXECUTE_VIEW_LIGHT
        elif obj_name == "LogsView":
            return cls.LOGS_VIEW_LIGHT
        elif obj_name == "TaskView":
            return cls.TASK_VIEW_LIGHT
        elif obj_name == "ElementManagerView":
            return cls.ELEMENT_MANAGER_LIGHT
        elif obj_name == "HelpView":
            return cls.HELP_VIEW_LIGHT
        elif obj_name == "ConfirmDeleteDialog":
            return cls.CONFIRM_DELETE_LIGHT
        elif obj_name == "InputDialog":
            return cls.INPUT_DIALOG_LIGHT
        elif obj_name == "WarningDialog":
            return cls.WARNING_DIALOG_LIGHT
        elif obj_name == "ErrorDialog":
            return cls.ERROR_DIALOG_LIGHT
        elif obj_name == "QuestionDialog":
            return cls.QUESTION_DIALOG_LIGHT
        elif obj_name == "SettingsDialog":
            return cls.SETTINGS_DIALOG_LIGHT
        elif obj_name == "ElementSelectorDialog":
            return cls.ELEMENT_SELECTOR_LIGHT
        elif obj_name == "TaskEditDialog":
            return cls.TASK_EDIT_DIALOG_LIGHT
        elif obj_name == "ElementEditDialog":
            return cls.ELEMENT_EDIT_LIGHT
        elif obj_name == "UpdateStepDialog":
            return cls.UPDATE_STEP_DIALOG_LIGHT
        elif obj_name == "ActionCard":
            return cls.ACTION_CARD_LIGHT
        elif obj_name == "ActionCardView":
            return cls.ACTION_CARD_VIEW_LIGHT
        elif obj_name in ("EditProjectGroup", "EditStepGroup", "EditActionGroup"):
            return cls.EDIT_GROUP_LIGHT
        # 对于未单独定义的控件，可返回 None（保持原样）
        return None

    @classmethod
    def _get_dark_style(cls, widget):
        obj_name = widget.objectName()
        if obj_name == "MainWindow":
            return cls.MAIN_WINDOW_DARK
        elif obj_name == "ProjectTreeView":
            return cls.PROJECT_TREE_DARK
        elif obj_name == "StepListView":
            return cls.STEP_LIST_DARK
        elif obj_name == "ExecuteView":
            return cls.EXECUTE_VIEW_DARK
        elif obj_name == "LogsView":
            return cls.LOGS_VIEW_DARK
        elif obj_name == "TaskView":
            return cls.TASK_VIEW_DARK
        elif obj_name == "ElementManagerView":
            return cls.ELEMENT_MANAGER_DARK
        elif obj_name == "HelpView":
            return cls.HELP_VIEW_DARK
        elif obj_name == "ConfirmDeleteDialog":
            return cls.CONFIRM_DELETE_DARK
        elif obj_name == "InputDialog":
            return cls.INPUT_DIALOG_DARK
        elif obj_name == "WarningDialog":
            return cls.WARNING_DIALOG_DARK
        elif obj_name == "ErrorDialog":
            return cls.ERROR_DIALOG_DARK
        elif obj_name == "QuestionDialog":
            return cls.QUESTION_DIALOG_DARK
        elif obj_name == "SettingsDialog":
            return cls.SETTINGS_DIALOG_DARK
        elif obj_name == "ElementSelectorDialog":
            return cls.ELEMENT_SELECTOR_DARK
        elif obj_name == "TaskEditDialog":
            return cls.TASK_EDIT_DIALOG_DARK
        elif obj_name == "ElementEditDialog":
            return cls.ELEMENT_EDIT_DARK
        elif obj_name == "UpdateStepDialog":
            return cls.UPDATE_STEP_DIALOG_DARK
        elif obj_name == "ActionCard":  # 修正：返回暗色样式
            return cls.ACTION_CARD_DARK
        elif obj_name == "ActionCardView":
            return cls.ACTION_CARD_VIEW_DARK
        elif obj_name in ("EditProjectGroup", "EditStepGroup", "EditActionGroup"):
            return cls.EDIT_GROUP_DARK
        return None

    @classmethod
    def apply_theme_to_all(cls, theme_mode: ThemeMode):
        """遍历所有顶层窗口，应用主题"""
        for widget in QApplication.topLevelWidgets():
            cls.apply_theme_to_widget(widget, theme_mode)
        # 对于非顶层但已设置 objectName 的对话框，可单独处理，但这里简化