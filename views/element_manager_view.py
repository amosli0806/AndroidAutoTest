# views/element_manager_view.py
import qtawesome as qta
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QTableWidget,
                             QTableWidgetItem, QPushButton, QLineEdit, QComboBox,
                             QHeaderView, QAbstractItemView, QMessageBox,
                             QFileDialog, QDialog, QFormLayout, QDialogButtonBox,
                             QGridLayout, QLabel, QMenu, QFrame, QGraphicsDropShadowEffect)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QIcon, QFont, QAction, QColor
from models.element_model import ElementModel, Element
from utils.toast import show_toast
from utils.dialogs import ConfirmDeleteDialog, ErrorDialog
from utils.theme import Theme, ThemeMode
from utils.settings import Settings, THEME_MODE_DARK


class ElementManagerView(QWidget):
    element_changed = pyqtSignal()
    verify_element_signal = pyqtSignal(str)

    def __init__(self, element_model: ElementModel, parent=None):
        super().__init__(parent)
        self.setObjectName("ElementManagerView")
        self.element_model = element_model
        self.setup_ui()
        self.load_data()
        # 应用主题（由主窗口调用，但这里确保初始化时也应用）
        self.apply_theme()

    def apply_theme(self, theme_mode: ThemeMode = None):
        """应用主题到视图"""
        import os as _os
        if theme_mode is None:
            theme_mode = ThemeMode.DARK if Settings.get_theme_mode() == THEME_MODE_DARK else ThemeMode.LIGHT

        # 应用整体主题样式
        Theme.apply_theme_to_widget(self, theme_mode)

        # 检查是否有壁纸（亮色和暗色都需要处理半透明）
        has_wp = False
        try:
            wp_path = Settings.get_wallpaper_path()
            has_wp = bool(wp_path and _os.path.exists(wp_path))
        except Exception:
            has_wp = False

        is_dark = (theme_mode == ThemeMode.DARK)

        # 有壁纸：给表格单独套一层半透明 QSS（参考项目树的处理）
        if has_wp:
            if is_dark:
                table_bg = "rgba(50, 50, 50, 0.85)"  # #323232 的半透明
                grid_color = "#555"
                item_color = "#eee"
                sel_bg = "rgba(30, 58, 95, 0.9)"  # #1e3a5f 半透明
                sel_color = "#eee"
                corner_border = "#555"
            else:
                table_bg = "rgba(232, 234, 237, 0.85)"  # #e8eaed 的半透明
                grid_color = "#d0d0d0"
                item_color = "black"
                sel_bg = "rgba(227, 242, 253, 0.9)"  # #e3f2fd 半透明
                sel_color = "black"
                corner_border = "#d0d0d0"

            self.table.setStyleSheet(f"""
                QTableWidget {{
                    background-color: {table_bg};
                    gridline-color: {grid_color};
                    border: none;
                }}
                QTableWidget::item {{
                    background-color: {table_bg};
                    color: {item_color};
                    border: none;
                }}
                QTableWidget::item:selected {{
                    background-color: {sel_bg};
                    color: {sel_color};
                }}
                QTableCornerButton::section {{
                    background-color: {table_bg};
                    border: 1px solid {corner_border};
                }}
            """)
        else:
            # 清掉 table 的独立样式，让 Theme 里的 QSS 生效
            self.table.setStyleSheet("")

        # 根据主题设置表头样式（同时应用于水平和垂直表头）
        if is_dark:
            if has_wp:
                header_style = """
                    QHeaderView::section {
                        background-color: rgba(58, 58, 58, 0.85);
                        color: #eee;
                        border: 1px solid #555;
                        padding: 4px;
                        font-weight: bold;
                    }
                """
            else:
                header_style = """
                    QHeaderView::section {
                        background-color: #3a3a3a;
                        color: #eee;
                        border: 1px solid #555;
                        padding: 4px;
                        font-weight: bold;
                    }
                """
        else:
            if has_wp:
                header_style = """
                    QHeaderView::section {
                        background-color: rgba(232, 234, 237, 0.85);
                        color: #333333;
                        border: none;
                        border-bottom: 1px solid #d0d0d0;
                        border-right: 1px solid #d0d0d0;
                        padding: 4px;
                        font-weight: bold;
                    }
                """
            else:
                header_style = """
                    QHeaderView::section {
                        background-color: #e8eaed;
                        color: #333333;
                        border: none;
                        border-bottom: 1px solid #d0d0d0;
                        border-right: 1px solid #d0d0d0;
                        padding: 4px;
                        font-weight: bold;
                    }
                """
        self.table.horizontalHeader().setStyleSheet(header_style)
        self.table.verticalHeader().setStyleSheet(header_style)

    def setup_ui(self):
        main_layout = QVBoxLayout(self)

        toolbar_layout = QHBoxLayout()

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索元素名称...")
        self.search_input.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.search_input.textChanged.connect(self.filter_table)

        self.app_combo = QComboBox()
        from utils.widget_helpers import prepare_combo_view
        prepare_combo_view(self.app_combo)
        self.app_combo.addItem("全部应用")
        self.app_combo.currentTextChanged.connect(self.filter_table)
        # 禁用焦点，避免打开页面时自动聚焦到它
        self.app_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        toolbar_layout.addWidget(self.search_input)
        toolbar_layout.addWidget(self.app_combo)

        self.add_btn = QPushButton("新增")
        self.add_btn.setIcon(qta.icon('fa6s.plus', color='white'))
        self.add_btn.clicked.connect(self._on_add)
        # 按钮样式由主题控制

        self.edit_btn = QPushButton("编辑")
        self.edit_btn.setIcon(QIcon())
        self.edit_btn.setEnabled(False)
        self.edit_btn.clicked.connect(self._on_edit)

        self.delete_btn = QPushButton("删除")
        self.delete_btn.setIcon(QIcon())
        self.delete_btn.setEnabled(False)
        self.delete_btn.clicked.connect(self._on_delete)

        self.import_btn = QPushButton("导入")
        self.import_btn.setIcon(qta.icon('fa6s.file-import', color='white'))
        self.import_btn.clicked.connect(self._on_import)

        self.export_btn = QPushButton("导出")
        self.export_btn.setIcon(QIcon())
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self._on_export)

        toolbar_layout.addStretch()
        toolbar_layout.addWidget(self.add_btn)
        toolbar_layout.addWidget(self.edit_btn)
        toolbar_layout.addWidget(self.delete_btn)
        toolbar_layout.addWidget(self.import_btn)
        toolbar_layout.addWidget(self.export_btn)

        main_layout.addLayout(toolbar_layout)

        # table_container：无圆角，避免影响表格
        table_container = QWidget()
        table_container.setStyleSheet("background: transparent; border-radius: 0px;")
        table_layout = QGridLayout(table_container)
        table_layout.setContentsMargins(0, 0, 0, 0)

        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["所属应用", "所属模块", "名称", "定位方式", "定位值"])

        header_font = QFont()
        header_font.setBold(True)
        self.table.horizontalHeader().setFont(header_font)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)

        self.table.setColumnWidth(0, 100)
        self.table.setColumnWidth(1, 100)
        self.table.setColumnWidth(2, 250)
        self.table.setColumnWidth(3, 80)

        vheader = self.table.verticalHeader()
        vheader.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        vheader.setMinimumWidth(40)
        vheader.setDefaultSectionSize(28)

        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)

        table_layout.addWidget(self.table, 0, 0)

        self.placeholder_empty = QLabel("暂未添加元素，点击「新增」创建")
        self.placeholder_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder_empty.setStyleSheet("color: #999; font-size: 20px;")
        self.placeholder_empty.hide()
        table_layout.addWidget(self.placeholder_empty, 0, 0)

        self.placeholder_search_empty = QLabel("未找到匹配的元素")
        self.placeholder_search_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder_search_empty.setStyleSheet("color: #999; font-size: 20px;")
        self.placeholder_search_empty.hide()
        table_layout.addWidget(self.placeholder_search_empty, 0, 0)

        main_layout.addWidget(table_container)

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
        all_elements = self.element_model.get_elements()
        filtered_elements = all_elements
        if filter_text:
            filtered_elements = [e for e in filtered_elements if filter_text.lower() in e.name.lower()]
        if filter_app and filter_app != "全部应用":
            filtered_elements = [e for e in filtered_elements if e.app == filter_app]

        if not all_elements:
            self.placeholder_empty.show()
            self.placeholder_search_empty.hide()
            self.table.hide()
            self.export_btn.setEnabled(False)
            self.export_btn.setIcon(QIcon())
        elif not filtered_elements:
            self.placeholder_empty.hide()
            self.placeholder_search_empty.show()
            self.table.hide()
            self.export_btn.setEnabled(True)
            self.export_btn.setIcon(qta.icon('fa6s.file-export', color='white'))
        else:
            self.placeholder_empty.hide()
            self.placeholder_search_empty.hide()
            self.table.show()
            self.export_btn.setEnabled(True)
            self.export_btn.setIcon(qta.icon('fa6s.file-export', color='white'))

        self.table.setRowCount(len(filtered_elements))
        for row, elem in enumerate(filtered_elements):
            item0 = QTableWidgetItem(elem.app)
            item0.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 0, item0)

            item1 = QTableWidgetItem(elem.module)
            item1.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 1, item1)

            item2 = QTableWidgetItem(elem.name)
            item2.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row, 2, item2)
            item2.setData(Qt.ItemDataRole.UserRole, elem.id)

            item3 = QTableWidgetItem(elem.loc_type)
            item3.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 3, item3)

            item4 = QTableWidgetItem(elem.loc_value)
            item4.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row, 4, item4)

        self.table.clearSelection()
        self._on_selection_changed()

    def filter_table(self):
        filter_text = self.search_input.text().strip()
        filter_app = self.app_combo.currentText()
        self.load_data(filter_text, filter_app)

    def _on_selection_changed(self):
        selected = len(self.table.selectedItems()) > 0
        self.edit_btn.setEnabled(selected)
        self.delete_btn.setEnabled(selected)
        if selected:
            self.edit_btn.setIcon(qta.icon('fa6s.pen', color='white'))
            self.delete_btn.setIcon(qta.icon('fa6s.trash-can', color='white'))
        else:
            self.edit_btn.setIcon(QIcon())
            self.delete_btn.setIcon(QIcon())

    def _get_selected_element_ids(self):
        rows = set()
        for item in self.table.selectedItems():
            rows.add(item.row())
        ids = []
        for row in rows:
            item = self.table.item(row, 2)
            if item:
                ids.append(item.data(Qt.ItemDataRole.UserRole))
        return ids

    def _show_context_menu(self, pos):
        selected_ids = self._get_selected_element_ids()
        if not selected_ids:
            return
        menu = QMenu()
        if len(selected_ids) == 1:
            verify_action = QAction("验证元素", self)
            verify_action.triggered.connect(lambda: self.verify_element_signal.emit(selected_ids[0]))
            menu.addAction(verify_action)
            menu.addSeparator()
        delete_action = QAction(f"删除选中的 {len(selected_ids)} 个元素", self)
        delete_action.triggered.connect(lambda: self._batch_delete_elements(selected_ids))
        menu.addAction(delete_action)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _batch_delete_elements(self, elem_ids):
        if not elem_ids:
            return
        names = []
        for eid in elem_ids:
            elem = self.element_model.get_element_by_id(eid)
            if elem:
                names.append(elem.name)
        if not names:
            return

        if not ConfirmDeleteDialog.ask(
                self,
                title="确认批量删除",
                message=f"确定删除以下 {len(names)} 个元素吗？",
                detail="如果这些元素被步骤引用，引用将被清除。"
        ):
            return

        deleted = 0
        for eid in elem_ids:
            if self.element_model.delete_element(eid):
                deleted += 1
        self.refresh()
        self.element_changed.emit()
        show_toast(message=f"成功删除 {deleted} 个元素")

    def _on_add(self):
        dialog = ElementEditDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            data = dialog.get_data()
            elem = Element(
                id=self.element_model._generate_id(),
                name=data['name'],
                app=data['app'],
                module=data['module'],
                loc_type=data['loc_type'],
                loc_value=data['loc_value'],
                remark=data['remark']
            )
            self.element_model.add_element(elem)
            self.refresh()
            self.element_changed.emit()

    def _on_edit(self):
        elem_ids = self._get_selected_element_ids()
        if not elem_ids:
            return
        elem = self.element_model.get_element_by_id(elem_ids[0])
        if not elem:
            return
        dialog = ElementEditDialog(self, elem)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            data = dialog.get_data()
            self.element_model.update_element(
                elem.id,
                name=data['name'],
                app=data['app'],
                module=data['module'],
                loc_type=data['loc_type'],
                loc_value=data['loc_value'],
                remark=data['remark']
            )
            self.refresh()
            self.element_changed.emit()

    def _on_delete(self):
        elem_ids = self._get_selected_element_ids()
        if not elem_ids:
            return
        if len(elem_ids) > 1:
            self._batch_delete_elements(elem_ids)
            return
        elem = self.element_model.get_element_by_id(elem_ids[0])
        if not elem:
            return

        if not ConfirmDeleteDialog.ask(
                self,
                title="确认删除",
                message=f"确定要删除元素 '{elem.name}' 吗？",
                detail="如果该元素被步骤引用，引用将被清除。"
        ):
            return

        self.element_model.delete_element(elem.id)
        self.refresh()
        self.element_changed.emit()

    def _on_import(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "导入元素", "", "JSON Files (*.json)"
        )
        if file_path:
            try:
                count = self.element_model.import_from_file(file_path)
                show_toast(message=f"导入成功")
                self.refresh()
                self.element_changed.emit()
            except ValueError as e:
                ErrorDialog.show_error(self, "导入失败", str(e))
            except Exception as e:
                ErrorDialog.show_error(self, "导入失败", f"导入时发生错误：\n{str(e)}")

    def _on_export(self):
        file_path, _ = QFileDialog.getSaveFileName(
            self, "导出元素", "elements_export.json", "JSON Files (*.json)"
        )
        if file_path:
            try:
                self.element_model.export_to_file(file_path)
                show_toast(message="导出成功")
            except Exception as e:
                ErrorDialog.show_error(self, "导出失败", f"导出时发生错误：\n{str(e)}")

    def refresh(self):
        self._update_app_combo()
        self.filter_table()


class ElementEditDialog(QDialog):
    """美观的新增/编辑元素对话框"""

    def __init__(self, parent=None, element: Element = None):
        super().__init__(parent)
        self.setObjectName("ElementEditDialog")
        self.element = element
        self.setWindowTitle("编辑元素" if element else "新增元素")
        self.setModal(True)
        self.setFixedSize(480, 420)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setup_ui()
        if element:
            self._load_data(element)
        # 应用主题
        self.apply_theme()

    def apply_theme(self, theme_mode: ThemeMode = None):
        if theme_mode is None:
            theme_mode = ThemeMode.DARK if Settings.get_theme_mode() == THEME_MODE_DARK else ThemeMode.LIGHT

        # 应用整体主题样式
        Theme.apply_theme_to_widget(self, theme_mode)

        # 表头样式由 theme.py 统一控制，不再单独设置
        # 如果希望保留主题切换时的额外控制，可以保留，但建议删除

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

        # 标题
        title_label = QLabel(self.windowTitle())
        title_label.setStyleSheet("font-size: 16px; font-weight: bold;")
        container_layout.addWidget(title_label)

        # 分隔线
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("background-color: #e0e0e0; max-height: 1px;")
        container_layout.addWidget(line)

        # 表单
        form_layout = QFormLayout()
        form_layout.setSpacing(14)
        form_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.app_edit = QLineEdit()
        self.app_edit.setPlaceholderText("例如：地图")

        self.module_edit = QLineEdit()
        self.module_edit.setPlaceholderText("例如：首页")

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("例如：GPS图标")

        self.loc_type_combo = QComboBox()
        from utils.widget_helpers import prepare_combo_view
        prepare_combo_view(self.loc_type_combo)
        self.loc_type_combo.addItems(['资源ID', '坐标', '文本', '描述', 'XPath'])
        self.loc_type_combo.setObjectName("locTypeCombo")

        self.loc_value_edit = QLineEdit()
        self.loc_value_edit.setPlaceholderText("例如：com.example.app:id/btn")

        self.remark_edit = QLineEdit()
        self.remark_edit.setPlaceholderText("可选备注信息")

        form_layout.addRow("所属应用:", self.app_edit)
        form_layout.addRow("所属模块:", self.module_edit)
        form_layout.addRow("元素名称:", self.name_edit)
        form_layout.addRow("定位方式:", self.loc_type_combo)
        form_layout.addRow("定位值:", self.loc_value_edit)
        form_layout.addRow("备注:", self.remark_edit)

        container_layout.addLayout(form_layout)

        # 按钮
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)

        cancel_btn = QPushButton("取消")
        cancel_btn.setObjectName("cancelBtn")
        cancel_btn.setFixedSize(100, 34)
        cancel_btn.clicked.connect(self.reject)

        ok_btn = QPushButton("确定")
        ok_btn.setObjectName("okBtn")
        ok_btn.setFixedSize(100, 34)
        ok_btn.clicked.connect(self.accept)

        btn_layout.addStretch()
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(ok_btn)
        container_layout.addLayout(btn_layout)

        main_layout.addWidget(container)

        # 回车快捷键
        for widget in [self.app_edit, self.module_edit, self.name_edit,
                       self.loc_value_edit, self.remark_edit]:
            widget.returnPressed.connect(self.accept)

    def _load_data(self, element: Element):
        self.app_edit.setText(element.app)
        self.module_edit.setText(element.module)
        self.name_edit.setText(element.name)
        index = self.loc_type_combo.findText(element.loc_type)
        if index >= 0:
            self.loc_type_combo.setCurrentIndex(index)
        self.loc_value_edit.setText(element.loc_value)
        self.remark_edit.setText(element.remark)

    def get_data(self):
        return {
            'app': self.app_edit.text().strip(),
            'module': self.module_edit.text().strip(),
            'name': self.name_edit.text().strip(),
            'loc_type': self.loc_type_combo.currentText(),
            'loc_value': self.loc_value_edit.text().strip(),
            'remark': self.remark_edit.text().strip()
        }