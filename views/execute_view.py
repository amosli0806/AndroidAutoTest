# views/execute_view.py
import qtawesome as qta
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                             QTreeView, QLabel, QSpinBox, QCheckBox,
                             QComboBox, QMessageBox, QSizePolicy)
from PyQt6.QtCore import pyqtSignal, Qt, QModelIndex
from PyQt6.QtGui import QStandardItemModel, QStandardItem, QIcon
from models.project_model import ProjectModel, TreeNode
from models.suite_model import SuiteModel
from utils import tree_state
from utils.toast import show_toast
from utils.dialogs import InputDialog, WarningDialog, ConfirmDeleteDialog, ErrorDialog
from utils.theme import ThemeMode, Theme
from PyQt6.QtWidgets import QCheckBox, QStyleOptionButton, QStyle
from PyQt6.QtGui import QPainter, QPen, QColor

# 树节点图标：与「项目管理」树的 _create_item（views/project_tree_view.py）
# 和「语音播报」页的分组/用例图标保持同一套，改一处记得同步另一处。
ICON_COLOR_GROUP = "#f0b429"    # 琥珀：项目 / 功能模块
ICON_COLOR_CASE = "#8a9099"     # 中性灰：用例


class BorderedCheckBox(QCheckBox):
    """复选框：保留 Fusion 默认绘制（勾选时有 √），额外叠加明显的边框。
    避免 Fusion 默认边框太浅导致"看不见边框"。"""

    def paintEvent(self, event):
        # 先让基类绘制默认的 checkbox（含勾选 √）
        super().paintEvent(event)
        # 再叠加一层明显的边框
        try:
            opt = QStyleOptionButton()
            self.initStyleOption(opt)
            rect = self.style().subElementRect(
                QStyle.SubElement.SE_CheckBoxIndicator, opt, self
            )
            if rect.isValid() and rect.width() > 0:
                painter = QPainter(self)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                # 用比较明显的灰蓝色边框
                painter.setPen(QPen(QColor(120, 120, 120), 1))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(rect.adjusted(0, 0, -1, -1))
                painter.end()
        except Exception:
            pass

class ExecuteView(QWidget):
    execute_selected = pyqtSignal(list)
    generate_report_signal = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ExecuteView")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.project_model = None
        self._updating = False
        self.suite_model = SuiteModel()
        self._suppress_suite_signal = False
        self._task_view = None
        self.setup_ui()
        # 移除原有的硬编码样式，由主题系统控制
        self._refresh_suite_combo()

    def set_suite_model(self, suite_model: SuiteModel):
        self.suite_model = suite_model
        self._refresh_suite_combo()

    def apply_theme(self, theme_mode: ThemeMode):
        """应用主题到视图（由主窗口调用）"""
        # 先应用整体样式（通过 objectName）
        Theme.apply_theme_to_widget(self, theme_mode)

        # 功能按钮：日夜模式统一使用白天模式的深蓝样式
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
            QPushButton:hover { background-color: #1565c0; }
            QPushButton:pressed { background-color: #0d47a1; }
            QPushButton:disabled { background-color: #b0b0b0; color: #e0e0e0; }
        """

        # 控件（下拉框 / 数字框 / 复选框）跟随主题
        if theme_mode == ThemeMode.DARK:
            combo_style = """
                QComboBox {
                    border: 1px solid #555;
                    border-radius: 4px;
                    padding: 4px 6px;
                    background-color: #3c3c3c;
                    color: #eee;
                }
                QComboBox:focus { border-color: #90caf9; }
                QComboBox QAbstractItemView {
                    background-color: #3c3c3c;
                    color: #eee;
                    border: 1px solid #555;
                    border-radius: 4px;
                    selection-background-color: #90caf9;
                    selection-color: #1e1e1e;
                    outline: none;
                    padding: 2px;
                }
                QComboBox QAbstractItemView::item {
                    background-color: #3c3c3c;
                    color: #eee;
                    min-height: 22px;
                    padding: 2px 8px;
                }
                QComboBox QAbstractItemView::item:hover {
                    background-color: #64b5f6;
                    color: #1e1e1e;
                }
                QComboBox QAbstractItemView::item:selected {
                    background-color: #90caf9;
                    color: #1e1e1e;
                }
            """
            spin_style = """
                QSpinBox {
                    border: 1px solid #555;
                    border-radius: 4px;
                    padding: 4px 6px;
                    background-color: #3c3c3c;
                    color: #eee;
                }
                QSpinBox:focus { border-color: #90caf9; }
            """
            # 只给 QCheckBox 设文字色，不设 ::indicator，让 Fusion 绘制默认带 √ 的复选框
            checkbox_style = "QCheckBox { color: #eee; }"
        else:
            combo_style = """
                QComboBox {
                    border: 1px solid #d0d0d0;
                    border-radius: 4px;
                    padding: 4px 6px;
                    background-color: white;
                    color: #333;
                }
                QComboBox:focus { border-color: #1976d2; }
                QComboBox QAbstractItemView {
                    background-color: white;
                    color: #333;
                    border: 1px solid #d0d0d0;
                    border-radius: 4px;
                    selection-background-color: #1976d2;
                    selection-color: white;
                    outline: none;
                    padding: 2px;
                }
                QComboBox QAbstractItemView::item {
                    background-color: white;
                    color: #333;
                    min-height: 22px;
                    padding: 2px 8px;
                }
                QComboBox QAbstractItemView::item:hover {
                    background-color: #1565c0;
                    color: white;
                }
                QComboBox QAbstractItemView::item:selected {
                    background-color: #1976d2;
                    color: white;
                }
            """
            spin_style = """
                QSpinBox {
                    border: 1px solid #d0d0d0;
                    border-radius: 4px;
                    padding: 4px 6px;
                    background-color: white;
                    color: #333;
                }
                QSpinBox:focus { border-color: #1976d2; }
            """
            checkbox_style = "QCheckBox { color: #333; }"

        # 应用样式到各控件（使用 findChildren 或直接设置）
        for btn in self.findChildren(QPushButton):
            # 排除 report_btn 等特殊按钮（保留其原有样式）
            if btn.objectName() not in ("reportBtn", "save_suite_btn", "del_suite_btn"):
                btn.setStyleSheet(btn_style)
        for combo in self.findChildren(QComboBox):
            combo.setStyleSheet(combo_style)
        for spin in self.findChildren(QSpinBox):
            spin.setStyleSheet(spin_style)
        # QCheckBox 文字颜色跟随主题（边框由 BorderedCheckBox 自绘）
        for cb in self.findChildren(QCheckBox):
            cb.setStyleSheet(checkbox_style)

        # QLabel（循环次数: / 套件: 等）文字颜色跟随主题
        label_color = "#eee" if theme_mode == ThemeMode.DARK else "#333"
        for lbl in self.findChildren(QLabel):
            if lbl.objectName() in ("", "loopLabel", "suiteLabel") or \
                    lbl.text() in ("循环次数:", "套件:"):
                existing = lbl.styleSheet() or ""
                lbl.setStyleSheet(existing + f" color: {label_color}; background: transparent;")

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # ---------- 第一行工具栏 ----------
        toolbar_row1 = QHBoxLayout()
        toolbar_row1.setContentsMargins(8, 4, 8, 0)
        toolbar_row1.setSpacing(6)

        self.select_all_btn = QPushButton("全选")
        self.select_all_btn_icon = qta.icon('fa6s.check-double', color='white')
        self.select_all_btn.setIcon(self.select_all_btn_icon)
        self.select_all_btn.clicked.connect(self.select_all)
        # 样式由主题控制，不设置硬编码样式

        self.deselect_all_btn = QPushButton("取消全选")
        self.deselect_all_btn_icon = qta.icon('fa6s.square', color='white')
        self.deselect_all_btn.setIcon(QIcon())
        self.deselect_all_btn.setEnabled(False)
        self.deselect_all_btn.clicked.connect(self.deselect_all)

        self.execute_btn = QPushButton("执行")
        self.execute_icon_enabled = qta.icon('fa6s.play', color='white')
        self.execute_btn.setIcon(QIcon())
        self.execute_btn.setEnabled(False)
        self.execute_btn.clicked.connect(self._execute)

        self.loop_label = QLabel("循环次数:")
        self.loop_spin = QSpinBox()
        self.loop_spin.setRange(1, 999)
        self.loop_spin.setValue(1)
        self.loop_spin.setFixedWidth(80)

        self.stop_on_fail_check = BorderedCheckBox("失败停止")


        toolbar_row1.addWidget(self.select_all_btn)
        toolbar_row1.addWidget(self.deselect_all_btn)
        toolbar_row1.addWidget(self.execute_btn)
        toolbar_row1.addWidget(self.loop_label)
        toolbar_row1.addWidget(self.loop_spin)
        toolbar_row1.addWidget(self.stop_on_fail_check)
        toolbar_row1.addStretch()

        layout.addLayout(toolbar_row1)

        # ---------- 第二行工具栏 ----------
        toolbar_row2 = QHBoxLayout()
        toolbar_row2.setContentsMargins(8, 0, 8, 4)
        toolbar_row2.setSpacing(6)

        self.suite_label = QLabel("套件:")
        self.suite_combo = QComboBox()
        from utils.widget_helpers import prepare_combo_view
        prepare_combo_view(self.suite_combo)
        self.suite_combo.setMinimumWidth(150)
        self.suite_combo.currentTextChanged.connect(self._on_suite_selected)

        self.save_suite_btn = QPushButton("保存套件")
        self.save_suite_btn.setIcon(qta.icon('fa6s.floppy-disk', color='white'))
        self.save_suite_btn.clicked.connect(self._save_current_as_suite)

        self.del_suite_btn = QPushButton("删除套件")
        self.del_suite_btn.setIcon(qta.icon('fa6s.trash-can', color='white'))
        self.del_suite_btn.clicked.connect(self._delete_selected_suite)

        self.report_btn = QPushButton("测试报告")
        self.report_btn.setIcon(QIcon())
        self.report_btn.setEnabled(False)
        self.report_btn.clicked.connect(self._on_report_clicked)

        toolbar_row2.addWidget(self.suite_label)
        toolbar_row2.addWidget(self.suite_combo)
        toolbar_row2.addWidget(self.save_suite_btn)
        toolbar_row2.addWidget(self.del_suite_btn)
        toolbar_row2.addWidget(self.report_btn)
        toolbar_row2.addStretch()

        layout.addLayout(toolbar_row2)

        # ---------- 用例树 ----------
        self.tree_view = QTreeView()
        self.tree_view.setHeaderHidden(True)
        self.tree_view.setIndentation(20)
        self.tree_view.setStyleSheet("""
            QTreeView {
                padding: 4px;
            }
            QTreeView::item {
                height: 30px !important;
                min-height: 30px !important;
                max-height: 30px !important;
            }
        """)
        self.tree_view.setItemsExpandable(True)
        self.tree_view.setEditTriggers(QTreeView.EditTrigger.NoEditTriggers)
        self.tree_view.clicked.connect(self._on_tree_item_clicked)
        self.tree_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.tree_view.setMinimumWidth(0)
        layout.addWidget(self.tree_view)

        # ---------- 其他初始化 ----------
        self.model = QStandardItemModel()
        self.tree_view.setModel(self.model)
        self.model.dataChanged.connect(self._on_data_changed)
        # 展开状态持久化：默认全折叠，记住用户上次展开的项目/模块（存 data/config.json）
        self._tree_state = tree_state.bind_view(self.tree_view, "execute_tree")

        self.placeholder = QLabel("无自动化执行", self)
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setStyleSheet("color: #999; font-size: 16px;")
        self.placeholder.hide()

        self._executing = False

    # ---------- 定时任务视图管理 ----------
    def set_task_view(self, task_view):
        """保存定时任务视图引用"""
        self._task_view = task_view

    def get_task_view(self):
        """返回定时任务视图"""
        return getattr(self, '_task_view', None)

    def remove_task_view(self):
        """从布局中移除 task_view（如果已添加）"""
        if hasattr(self, '_task_view') and self._task_view:
            self._task_view.setParent(None)
            self._task_view = None

    # ---------- 以下为原有方法（未做任何改动，但移除了对 _button_style 的依赖） ----------
    def _on_report_clicked(self):
        self.generate_report_signal.emit()

    def set_report_enabled(self, enabled: bool):
        self.report_btn.setEnabled(enabled)
        if enabled:
            self.report_btn.setIcon(qta.icon('fa6s.file-lines', color='white'))
        else:
            self.report_btn.setIcon(QIcon())

    def set_model(self, model: ProjectModel):
        self.project_model = model
        self.refresh()

    def refresh(self):
        # 重建前先把当前展开态收下来（model.clear() 会把展开态全部丢掉）
        self._tree_state.snapshot()
        with self._tree_state.pause():
            self.model.clear()
        if not self.project_model or not self.project_model.root_nodes:
            self.placeholder.show()
            self.placeholder.setGeometry(0, 0, self.width(), self.height())
            self.execute_btn.setEnabled(False)
            self._update_buttons()
            return
        self.placeholder.hide()
        for node in self.project_model.root_nodes:
            item = self._create_item(node)
            self.model.appendRow(item)
        # 按上次记录还原展开态；没有记录（首次运行）就保持全折叠（原来是无条件 expandAll）
        self._tree_state.restore()
        self.tree_view.doItemsLayout()
        self._update_buttons()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.placeholder.isVisible():
            self.placeholder.setGeometry(0, 0, self.width(), self.height())

    def _create_item(self, node: TreeNode):
        item = QStandardItem(node.name)
        item.setData(node.id, Qt.ItemDataRole.UserRole)
        item.setEditable(False)
        item.setCheckable(True)
        item.setCheckState(Qt.CheckState.Unchecked)
        # 图标放在复选框和文案之间（Qt 自动就是插在这里），
        # 用的图标与「项目管理」树、以及「语音播报」页的分组/用例是同一套：
        # 项目/功能模块 = 琥珀色文件夹，用例 = 灰色对话气泡
        if node.type == 'project':
            item.setIcon(qta.icon("fa6s.folder-open", color=ICON_COLOR_GROUP))
        elif node.type == 'folder':
            item.setIcon(qta.icon("fa6s.folder", color=ICON_COLOR_GROUP))
        else:
            item.setIcon(qta.icon("fa6s.comment-dots", color=ICON_COLOR_CASE))
        if node.children:
            for child in node.children:
                child_item = self._create_item(child)
                item.appendRow(child_item)
        return item

    def _on_tree_item_clicked(self, index: QModelIndex):
        if not index.isValid():
            return
        item = self.model.itemFromIndex(index)
        if item and item.isCheckable():
            current_state = item.checkState()
            new_state = Qt.CheckState.Unchecked if current_state == Qt.CheckState.Checked else Qt.CheckState.Checked
            item.setCheckState(new_state)

    def _on_data_changed(self, top_left, bottom_right):
        if self._updating:
            return
        item = self.model.itemFromIndex(top_left)
        if item and item.isCheckable():
            state = item.checkState()
            self._updating = True
            self._set_children_check_state(item, state)
            self._update_parent_check_state(item)
            self._updating = False
        self._update_buttons()
        self._check_current_state_against_suites()

    def _set_children_check_state(self, parent_item, state):
        for i in range(parent_item.rowCount()):
            child = parent_item.child(i)
            if child and child.isCheckable():
                child.setCheckState(state)
                self._set_children_check_state(child, state)

    def _update_parent_check_state(self, item):
        parent = item.parent()
        if parent is None:
            return
        checked_count = 0
        total = 0
        for i in range(parent.rowCount()):
            child = parent.child(i)
            if child and child.isCheckable():
                total += 1
                if child.checkState() == Qt.CheckState.Checked:
                    checked_count += 1
        if total == 0:
            return
        new_state = Qt.CheckState.Unchecked
        if checked_count == total:
            new_state = Qt.CheckState.Checked
        elif checked_count > 0:
            new_state = Qt.CheckState.PartiallyChecked
        parent.setCheckState(new_state)
        self._update_parent_check_state(parent)

    def _update_buttons(self):
        total_checkable = 0
        checked_count = 0

        def traverse(item):
            nonlocal total_checkable, checked_count
            if item.isCheckable():
                total_checkable += 1
                if item.checkState() == Qt.CheckState.Checked:
                    checked_count += 1
            for i in range(item.rowCount()):
                traverse(item.child(i))

        for i in range(self.model.rowCount()):
            traverse(self.model.item(i))

        can_execute = checked_count > 0 and not self._executing
        self.execute_btn.setEnabled(can_execute)
        self.execute_btn.setIcon(self.execute_icon_enabled if can_execute else QIcon())

        all_checked = (total_checkable > 0 and checked_count == total_checkable)
        can_select_all = not all_checked and not self._executing
        self.select_all_btn.setEnabled(can_select_all)
        self.select_all_btn.setIcon(self.select_all_btn_icon if can_select_all else QIcon())

        none_checked = (checked_count == 0)
        can_deselect_all = not none_checked and not self._executing
        self.deselect_all_btn.setEnabled(can_deselect_all)
        self.deselect_all_btn.setIcon(self.deselect_all_btn_icon if can_deselect_all else QIcon())

        self.loop_spin.setEnabled(not self._executing)
        self.stop_on_fail_check.setEnabled(not self._executing)
        self.suite_combo.setEnabled(not self._executing)
        self.save_suite_btn.setEnabled(not self._executing)
        is_suite_selected = self.suite_combo.currentText() != "(无套件)" and self.suite_combo.count() > 1
        self.del_suite_btn.setEnabled(not self._executing and is_suite_selected)
        if not self._executing and is_suite_selected:
            self.del_suite_btn.setIcon(qta.icon('fa6s.trash-can', color='white'))
        else:
            self.del_suite_btn.setIcon(QIcon())
        if not self._executing:
            self.save_suite_btn.setIcon(qta.icon('fa6s.floppy-disk', color='white'))
        else:
            self.save_suite_btn.setIcon(QIcon())

    def _get_checked_ids(self):
        ids = []
        for i in range(self.model.rowCount()):
            ids.extend(self._collect_checked_ids(self.model.item(i)))
        return ids

    def _collect_checked_ids(self, item):
        ids = []
        if item.isCheckable() and item.checkState() == Qt.CheckState.Checked:
            if item.data(Qt.ItemDataRole.UserRole):
                ids.append(item.data(Qt.ItemDataRole.UserRole))
        for i in range(item.rowCount()):
            ids.extend(self._collect_checked_ids(item.child(i)))
        return ids

    def _execute(self):
        ids = self._get_checked_ids()
        if ids:
            self._executing = True
            self._update_buttons()
            self.execute_selected.emit(ids)

    def select_all(self):
        if self._executing:
            return
        for i in range(self.model.rowCount()):
            root = self.model.item(i)
            self._updating = True
            root.setCheckState(Qt.CheckState.Checked)
            self._set_children_check_state(root, Qt.CheckState.Checked)
            self._updating = False
        self._update_buttons()
        self._check_current_state_against_suites()

    def deselect_all(self):
        if self._executing:
            return
        for i in range(self.model.rowCount()):
            root = self.model.item(i)
            self._updating = True
            root.setCheckState(Qt.CheckState.Unchecked)
            self._set_children_check_state(root, Qt.CheckState.Unchecked)
            self._updating = False
        self._update_buttons()
        self._check_current_state_against_suites()

    def set_executing(self, executing: bool):
        self._executing = executing
        self._update_buttons()

    def _refresh_suite_combo(self):
        current = self.suite_combo.currentText()
        self.suite_combo.clear()
        self.suite_combo.addItem("(无套件)")
        suites = self.suite_model.get_all_suites()
        if suites:
            suite_names = [s.name for s in suites]
            self.suite_combo.addItems(suite_names)
            if current in suite_names:
                self.suite_combo.setCurrentText(current)
            else:
                self.suite_combo.setCurrentText("(无套件)")
        else:
            self.suite_combo.setCurrentText("(无套件)")
        self._update_buttons()

    def _check_current_state_against_suites(self):
        if self._executing or self.project_model is None:
            return
        if self._suppress_suite_signal:
            return
        try:
            current_ids = set(self._get_checked_ids())
            if not current_ids:
                if self.suite_combo.currentText() != "(无套件)":
                    self._suppress_suite_signal = True
                    self.suite_combo.setCurrentText("(无套件)")
                    self._suppress_suite_signal = False
                    self._update_buttons()
                return
            matched = None
            for suite in self.suite_model.get_all_suites():
                if set(suite.case_ids) == current_ids:
                    matched = suite.name
                    break
            if matched and self.suite_combo.currentText() != matched:
                self._suppress_suite_signal = True
                self.suite_combo.setCurrentText(matched)
                self._suppress_suite_signal = False
                self._update_buttons()
            elif not matched and self.suite_combo.currentText() != "(无套件)":
                self._suppress_suite_signal = True
                self.suite_combo.setCurrentText("(无套件)")
                self._suppress_suite_signal = False
                self._update_buttons()
        except Exception as e:
            print(f"_check_current_state_against_suites error: {e}")

    def _on_suite_selected(self, name):
        if self._suppress_suite_signal:
            return
        if name == "(无套件)" or not name:
            self.deselect_all()
            return
        if self.project_model is None:
            WarningDialog.show_warning(self, "提示", "请先加载项目")
            self._suppress_suite_signal = True
            self.suite_combo.setCurrentText("(无套件)")
            self._suppress_suite_signal = False
            return
        suite = self.suite_model.get_suite_by_name(name)
        if not suite:
            return
        try:
            valid_ids = []
            for cid in suite.case_ids:
                node = self.project_model.get_node_by_id(cid)
                if node:
                    valid_ids.append(cid)

            if len(valid_ids) != len(suite.case_ids):
                self.suite_model.update_suite(name, valid_ids)
                if not valid_ids:
                    self._suppress_suite_signal = True
                    self.suite_combo.setCurrentText("(无套件)")
                    self._suppress_suite_signal = False
                    show_toast(message="套件已失效")
                    return
                show_toast(message="套件已更新")
                self._refresh_suite_combo()
                suite = self.suite_model.get_suite_by_name(name)
                if not suite:
                    return
                valid_ids = suite.case_ids

            self.model.blockSignals(True)

            def clear_all(item):
                if item.isCheckable():
                    item.setCheckState(Qt.CheckState.Unchecked)
                for i in range(item.rowCount()):
                    clear_all(item.child(i))

            for i in range(self.model.rowCount()):
                clear_all(self.model.item(i))

            def set_checked(item):
                node_id = item.data(Qt.ItemDataRole.UserRole)
                if node_id and node_id in valid_ids:
                    item.setCheckState(Qt.CheckState.Checked)
                for i in range(item.rowCount()):
                    set_checked(item.child(i))

            for i in range(self.model.rowCount()):
                set_checked(self.model.item(i))

            self._refresh_all_parent_states()

            self.model.blockSignals(False)

            self._update_buttons()

            self._suppress_suite_signal = True
            self.suite_combo.setCurrentText(name)
            self._suppress_suite_signal = False

            self._suppress_suite_signal = True
            self._check_current_state_against_suites()
            self._suppress_suite_signal = False

        except Exception as e:
            import traceback
            traceback.print_exc()
            ErrorDialog.show_error(self, "加载套件出错", str(e))

    def _refresh_all_parent_states(self):
        nodes = []

        def collect(item):
            if item.hasChildren():
                nodes.append(item)
            for i in range(item.rowCount()):
                collect(item.child(i))

        for i in range(self.model.rowCount()):
            collect(self.model.item(i))
        for node in nodes:
            checked = 0
            total = 0

            def count(item):
                nonlocal checked, total
                if item.isCheckable():
                    total += 1
                    if item.checkState() == Qt.CheckState.Checked:
                        checked += 1
                for i in range(item.rowCount()):
                    count(item.child(i))

            count(node)
            if total > 0:
                if checked == total:
                    new_state = Qt.CheckState.Checked
                elif checked == 0:
                    new_state = Qt.CheckState.Unchecked
                else:
                    new_state = Qt.CheckState.PartiallyChecked
                node.setCheckState(new_state)

    def _save_current_as_suite(self):
        checked_ids = self._get_checked_ids()
        if not checked_ids:
            WarningDialog.show_warning(self, "保存套件", "当前没有勾选用例，无法保存套件")
            return
        case_ids = []
        for cid in checked_ids:
            node = self.project_model.get_node_by_id(cid)
            if node and node.type == 'case':
                case_ids.append(cid)
        if not case_ids:
            WarningDialog.show_warning(self, "保存套件", "当前勾选的项目中没有用例，无法保存套件")
            return
        name, ok = InputDialog.get_text(self, "保存套件", "输入套件名称:", placeholder="例如：回归测试套件")
        if not ok or not name:
            return
        name = name.strip()
        if self.suite_model.get_suite_by_name(name):
            WarningDialog.show_warning(self, "保存套件", f"套件 '{name}' 已存在，请换一个名称")
            return
        try:
            self.suite_model.create_suite(name, case_ids)
            self._suppress_suite_signal = True
            self._refresh_suite_combo()
            self.suite_combo.setCurrentText(name)
            self._suppress_suite_signal = False
            show_toast(message="保存套件成功")
        except Exception as e:
            ErrorDialog.show_error(self, "保存失败", f"保存套件时出错：{str(e)}")

    def _delete_selected_suite(self):
        name = self.suite_combo.currentText()
        if name == "(无套件)" or not name:
            return

        if not ConfirmDeleteDialog.ask(
                self,
                title="确认删除",
                message=f"确定要删除套件 '{name}' 吗？",
                detail="删除后该套件将不可恢复。"
        ):
            return

        self.suite_model.delete_suite(name)
        self._refresh_suite_combo()
        self._suppress_suite_signal = True
        self.suite_combo.setCurrentText("(无套件)")
        self._suppress_suite_signal = False
        show_toast(message="删除套件成功")