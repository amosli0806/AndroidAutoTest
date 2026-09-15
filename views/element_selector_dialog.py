# views/element_selector_dialog.py
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QTableWidget,
                             QTableWidgetItem, QPushButton, QLineEdit, QComboBox,
                             QHeaderView, QAbstractItemView)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from models.element_model import ElementModel, Element
from utils.theme import Theme, ThemeMode
from utils.settings import Settings, THEME_MODE_DARK


class ElementSelectorDialog(QDialog):
    """元素选择对话框，用于在动作卡片中选择元素"""
    element_selected = pyqtSignal(Element)  # 选中元素时发射

    def __init__(self, element_model: ElementModel, parent=None):
        super().__init__(parent)
        self.setObjectName("ElementSelectorDialog")
        self.element_model = element_model
        self.setWindowTitle("选择元素")
        self.setModal(True)
        self.resize(750, 450)
        self.setup_ui()
        self.load_data()
        # 应用主题
        self.apply_theme()

    def apply_theme(self, theme_mode: ThemeMode = None):
        """应用主题到对话框"""
        if theme_mode is None:
            theme_mode = ThemeMode.DARK if Settings.get_theme_mode() == THEME_MODE_DARK else ThemeMode.LIGHT
        Theme.apply_theme_to_widget(self, theme_mode)

    def setup_ui(self):
        layout = QVBoxLayout(self)

        # 过滤区域
        filter_layout = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索元素名称...")
        self.search_input.textChanged.connect(self.filter_table)

        self.app_combo = QComboBox()
        from utils.widget_helpers import prepare_combo_view
        prepare_combo_view(self.app_combo)
        self.app_combo.addItem("全部应用")
        self.app_combo.currentTextChanged.connect(self.filter_table)

        filter_layout.addWidget(self.search_input)
        filter_layout.addWidget(self.app_combo)
        layout.addLayout(filter_layout)

        # 表格
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["所属应用", "所属模块", "名称", "定位方式", "定位值"])

        # 表头字体加粗
        header_font = QFont()
        header_font.setBold(True)
        self.table.horizontalHeader().setFont(header_font)

        # 设置列宽模式
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)

        # 设置初始宽度
        self.table.setColumnWidth(0, 80)
        self.table.setColumnWidth(1, 80)
        self.table.setColumnWidth(2, 130)
        self.table.setColumnWidth(3, 60)

        # 行号列
        vheader = self.table.verticalHeader()
        vheader.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        vheader.setMinimumWidth(40)
        vheader.setDefaultSectionSize(28)

        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self.table)

        # 按钮
        btn_layout = QHBoxLayout()
        self.select_btn = QPushButton("确定")
        self.select_btn.setObjectName("selectBtn")
        self.select_btn.clicked.connect(self._on_select)
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setObjectName("cancelBtn")
        self.cancel_btn.clicked.connect(self.reject)
        btn_layout.addStretch()
        btn_layout.addWidget(self.select_btn)
        btn_layout.addWidget(self.cancel_btn)
        layout.addLayout(btn_layout)

        # 更新应用下拉框
        self._update_app_combo()

    def _update_app_combo(self):
        apps = self.element_model.get_all_apps()
        current_text = self.app_combo.currentText()
        self.app_combo.clear()
        self.app_combo.addItem("全部应用")
        self.app_combo.addItems(apps)
        if current_text in apps:
            self.app_combo.setCurrentText(current_text)

    def load_data(self, filter_text="", filter_app=""):
        elements = self.element_model.get_elements()
        if filter_text:
            elements = [e for e in elements if filter_text.lower() in e.name.lower()]
        if filter_app and filter_app != "全部应用":
            elements = [e for e in elements if e.app == filter_app]

        self.table.setRowCount(len(elements))
        for row, elem in enumerate(elements):
            # 所属应用 - 居中
            item0 = QTableWidgetItem(elem.app)
            item0.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 0, item0)

            # 所属模块 - 居中
            item1 = QTableWidgetItem(elem.module)
            item1.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 1, item1)

            # 名称 - 左对齐
            item2 = QTableWidgetItem(elem.name)
            item2.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row, 2, item2)
            item2.setData(Qt.ItemDataRole.UserRole, elem.id)

            # 定位方式 - 居中
            item3 = QTableWidgetItem(elem.loc_type)
            item3.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 3, item3)

            # 定位值 - 左对齐
            item4 = QTableWidgetItem(elem.loc_value)
            item4.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row, 4, item4)

    def filter_table(self):
        filter_text = self.search_input.text().strip()
        filter_app = self.app_combo.currentText()
        self.load_data(filter_text, filter_app)

    def _on_item_double_clicked(self, item):
        self._select_current()

    def _on_select(self):
        self._select_current()

    def _select_current(self):
        row = self.table.currentRow()
        if row < 0:
            return
        item = self.table.item(row, 2)
        if not item:
            return
        elem_id = item.data(Qt.ItemDataRole.UserRole)
        elem = self.element_model.get_element_by_id(elem_id)
        if elem:
            self.element_selected.emit(elem)
            self.accept()