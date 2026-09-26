# views/execute_view.py
import qtawesome as qta
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                             QTreeView, QLabel, QSpinBox, QCheckBox,
                             QComboBox, QMessageBox, QSizePolicy)
from PyQt6.QtCore import pyqtSignal, Qt, QSize
from PyQt6.QtGui import QStandardItemModel, QStandardItem, QIcon
from models.project_model import ProjectModel, TreeNode
from models.suite_model import SuiteModel
from utils import tree_state
from utils.toast import show_toast
from utils.dialogs import InputDialog, WarningDialog, ConfirmDeleteDialog, ErrorDialog
from utils.theme import ThemeMode, Theme
from utils.settings import Settings, THEME_MODE_DARK
from PyQt6.QtWidgets import QCheckBox, QStyleOptionButton, QStyleOptionViewItem, QStyle
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

class _CaseTreeView(QTreeView):
    """整行可勾选的用例树：点行内任意位置都能勾选/取消，不必对准那个小方框。

    与「语音播报」页的 _ClickAnywhereCheckTree 同一套做法（views/voice_view.py）：
    为什么不直接连 clicked 信号去改状态 —— 点在复选框本身时 Qt 自己已经切过一次
    （QStyledItemDelegate::editorEvent 负责 indicator 的点击），再切一次就抵掉了，
    表现就是"点复选框勾不上"。所以这里在 mousePressEvent 里按落点分流，
    保证任何位置都恰好切换一次：
      - 分支列（缩进 + 展开箭头）-> 交给基类，否则点箭头会把展开/收起吃掉
      - 复选框本身              -> 也交给基类，走 Qt 那一次切换
      - 其余位置（图标/文案/右侧空白）-> 自己切换并消费事件
    部分选中（父节点）按 Qt 的老规矩回到「全选」。
    """

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        pos = event.position().toPoint()
        index = self.indexAt(pos)
        model = self.model()
        item = model.itemFromIndex(index) if index.isValid() else None
        if item is None or not item.isCheckable():
            return super().mousePressEvent(event)

        # 分支列不碰：缩进列的宽度 = indentation * (深度 + 1)
        depth, parent = 0, item.parent()
        while parent is not None:
            depth += 1
            parent = parent.parent()
        if pos.x() < self.indentation() * (depth + 1):
            return super().mousePressEvent(event)

        if self._hit_check_indicator(index, pos):
            return super().mousePressEvent(event)

        state = item.checkState()
        item.setCheckState(
            Qt.CheckState.Unchecked if state == Qt.CheckState.Checked
            else Qt.CheckState.Checked)
        event.accept()

    def _hit_check_indicator(self, index, pos) -> bool:
        """落点是否在复选框矩形内（delegate 拿到的 rect 就是 Qt 画 indicator 的位置）"""
        opt = QStyleOptionViewItem()
        self.itemDelegate().initStyleOption(opt, index)
        opt.rect = self.visualRect(index)
        rect = self.style().subElementRect(
            QStyle.SubElement.SE_ItemViewItemCheckIndicator, opt, self)
        return rect.isValid() and rect.contains(pos)

class ExecuteView(QWidget):
    execute_selected = pyqtSignal(list)
    generate_report_signal = pyqtSignal()
    # 执行中点「停止」：请求中止正在跑的这一轮
    stop_requested = pyqtSignal()

    # 执行中「执行」按钮变成红色的「停止」（配色与 ADB 工具箱行内按钮同一套）
    STOP_BTN_QSS = """
        QPushButton {
            background-color: #e74c3c;
            border: none;
            padding: 2px;
            border-radius: 4px;
            min-width: 34px;
            max-width: 34px;
            min-height: 28px;
            max-height: 28px;
        }
        QPushButton:hover { background-color: #f05a4a; }
        QPushButton:pressed { background-color: #c0392b; }
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ExecuteView")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.project_model = None
        self._updating = False
        self.suite_model = SuiteModel()
        self._suppress_suite_signal = False
        self._task_view = None
        # 主题里的主按钮样式，「执行/停止」来回切时要用它把蓝色恢复回来
        self._primary_btn_style = None
        # 工具栏图标颜色：跟随主题（亮色深图标 / 暗色浅图标），apply_theme 时刷新
        self._icon_color = ("#e0e0e0"
                            if Settings.get_theme_mode() == THEME_MODE_DARK
                            else "#444444")
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

        # 工具栏图标按钮：独立主题样式（不套文字按钮的深蓝大按钮样式）
        self._icon_color = "#e0e0e0" if theme_mode == ThemeMode.DARK else "#444444"
        if theme_mode == ThemeMode.DARK:
            icon_btn_style = """
                QPushButton#ToolIconBtn {
                    background-color: #3c3c3c;
                    border: 1px solid #555;
                    border-radius: 4px;
                    padding: 2px;
                    min-width: 34px; max-width: 34px;
                    min-height: 28px; max-height: 28px;
                }
                QPushButton#ToolIconBtn:hover { background-color: #4a4a4a; }
                QPushButton#ToolIconBtn:disabled { background-color: #333; }
            """
            # 主操作（执行）：蓝底白图标，保持醒目
            self._primary_btn_style = """
                QPushButton#ExecIconBtn {
                    background-color: #1976d2;
                    border: none;
                    border-radius: 4px;
                    padding: 2px;
                    min-width: 34px; max-width: 34px;
                    min-height: 28px; max-height: 28px;
                }
                QPushButton#ExecIconBtn:hover { background-color: #1565c0; }
                QPushButton#ExecIconBtn:disabled { background-color: #555; }
            """
        else:
            icon_btn_style = """
                QPushButton#ToolIconBtn {
                    background-color: #f5f6f8;
                    border: 1px solid #d0d0d0;
                    border-radius: 4px;
                    padding: 2px;
                    min-width: 34px; max-width: 34px;
                    min-height: 28px; max-height: 28px;
                }
                QPushButton#ToolIconBtn:hover { background-color: #e8eaee; }
                QPushButton#ToolIconBtn:disabled { background-color: #eee; }
            """
            self._primary_btn_style = """
                QPushButton#ExecIconBtn {
                    background-color: #1976d2;
                    border: none;
                    border-radius: 4px;
                    padding: 2px;
                    min-width: 34px; max-width: 34px;
                    min-height: 28px; max-height: 28px;
                }
                QPushButton#ExecIconBtn:hover { background-color: #1565c0; }
                QPushButton#ExecIconBtn:disabled { background-color: #b0b0b0; }
            """

        # 应用样式到各控件（使用 findChildren 或直接设置）
        for btn in self.findChildren(QPushButton):
            if btn.objectName() == "ToolIconBtn":
                btn.setStyleSheet(icon_btn_style)
            elif btn.objectName() == "ExecIconBtn":
                btn.setStyleSheet(self._primary_btn_style)
            elif btn.objectName() not in ("reportBtn", "save_suite_btn", "del_suite_btn"):
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

        # 记下主按钮样式：执行中「停止」要切回「执行」时靠它恢复蓝色
        self._primary_btn_style = btn_style
        self._rebuild_icons()
        self._update_buttons()

    def _rebuild_icons(self):
        """主题切换后按当前主题色重建工具栏图标（图标-only 按钮的可见性命脉）。"""
        c = self._icon_color
        self.select_all_btn_icon = qta.icon('fa6s.check-double', color=c)
        self.deselect_all_btn_icon = qta.icon('fa6s.square-minus', color=c)
        self.execute_icon_enabled = qta.icon('fa6s.play', color=c)
        self.save_suite_btn.setIcon(qta.icon('fa6s.floppy-disk', color=c))
        self.del_suite_btn.setIcon(qta.icon('fa6s.trash-can', color=c))
        self.report_btn.setIcon(qta.icon('fa6s.file-lines', color=c))

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # ---------- 工具栏（单行：纯按钮只显示图标，悬停见 tooltip） ----------
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(8, 4, 8, 4)
        toolbar.setSpacing(6)

        self.select_all_btn = QPushButton()
        self.select_all_btn_icon = qta.icon('fa6s.check-double', color=self._icon_color)
        self.select_all_btn.setIcon(self.select_all_btn_icon)
        self.select_all_btn.setIconSize(QSize(15, 15))
        self.select_all_btn.setFixedSize(34, 28)
        self.select_all_btn.setObjectName("ToolIconBtn")
        self.select_all_btn.setToolTip("全选")
        self.select_all_btn.clicked.connect(self.select_all)

        self.deselect_all_btn = QPushButton()
        self.deselect_all_btn_icon = qta.icon('fa6s.square-minus', color=self._icon_color)
        self.deselect_all_btn.setIcon(self.deselect_all_btn_icon)
        self.deselect_all_btn.setIconSize(QSize(15, 15))
        self.deselect_all_btn.setFixedSize(34, 28)
        self.deselect_all_btn.setObjectName("ToolIconBtn")
        self.deselect_all_btn.setToolTip("取消全选")
        self.deselect_all_btn.setEnabled(False)
        self.deselect_all_btn.clicked.connect(self.deselect_all)

        self.execute_btn = QPushButton()
        self.execute_icon_enabled = qta.icon('fa6s.play', color=self._icon_color)
        self.execute_btn.setIcon(QIcon())   # 空闲且无勾选时是灰态占位
        self.execute_btn.setIconSize(QSize(15, 15))
        self.execute_btn.setFixedSize(34, 28)
        self.execute_btn.setObjectName("ExecIconBtn")
        self.execute_btn.setToolTip("执行")
        self.execute_btn.setEnabled(False)
        self.execute_btn.clicked.connect(self._execute)

        self.loop_label = QLabel("循环")
        self.loop_spin = QSpinBox()
        self.loop_spin.setRange(1, 999)
        self.loop_spin.setValue(1)
        self.loop_spin.setFixedWidth(62)
        self.loop_spin.setToolTip("循环次数")

        self.stop_on_fail_check = BorderedCheckBox("失败停止")

        # 套件与报告相关控件（图标化，悬停见 tooltip）
        self.suite_combo = QComboBox()
        from utils.widget_helpers import prepare_combo_view
        prepare_combo_view(self.suite_combo)
        self.suite_combo.setMinimumWidth(130)
        self.suite_combo.currentTextChanged.connect(self._on_suite_selected)

        self.save_suite_btn = QPushButton()
        self.save_suite_btn.setIcon(qta.icon('fa6s.floppy-disk', color=self._icon_color))
        self.save_suite_btn.setIconSize(QSize(15, 15))
        self.save_suite_btn.setFixedSize(34, 28)
        self.save_suite_btn.setObjectName("ToolIconBtn")
        self.save_suite_btn.setToolTip("保存套件")
        self.save_suite_btn.clicked.connect(self._save_current_as_suite)

        self.del_suite_btn = QPushButton()
        self.del_suite_btn.setIcon(qta.icon('fa6s.trash-can', color=self._icon_color))
        self.del_suite_btn.setIconSize(QSize(15, 15))
        self.del_suite_btn.setFixedSize(34, 28)
        self.del_suite_btn.setObjectName("ToolIconBtn")
        self.del_suite_btn.setToolTip("删除套件")
        self.del_suite_btn.clicked.connect(self._delete_selected_suite)

        self.report_btn = QPushButton()
        self.report_btn.setIcon(qta.icon('fa6s.file-lines', color=self._icon_color))
        self.report_btn.setIconSize(QSize(15, 15))
        self.report_btn.setFixedSize(34, 28)
        self.report_btn.setObjectName("ToolIconBtn")
        self.report_btn.setToolTip("测试报告")
        self.report_btn.setEnabled(False)
        self.report_btn.clicked.connect(self._on_report_clicked)

        toolbar.addWidget(self.select_all_btn)
        toolbar.addWidget(self.deselect_all_btn)
        toolbar.addWidget(self.execute_btn)
        toolbar.addSpacing(4)
        toolbar.addWidget(self.suite_combo)
        toolbar.addWidget(self.save_suite_btn)
        toolbar.addWidget(self.del_suite_btn)
        toolbar.addWidget(self.report_btn)
        toolbar.addSpacing(4)
        toolbar.addWidget(self.loop_label)
        toolbar.addWidget(self.loop_spin)
        toolbar.addWidget(self.stop_on_fail_check)
        toolbar.addStretch()

        layout.addLayout(toolbar)

        # ---------- 用例树 ----------
        self.tree_view = _CaseTreeView()
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
        # 勾选不再走 clicked 信号：点击分流在 _CaseTreeView.mousePressEvent 里做
        # （点复选框时 Qt 自己已经切过一次，这里再切一次就会互相抵掉）
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
            self.report_btn.setIcon(qta.icon('fa6s.file-lines', color=self._icon_color))
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
        # 执行中按钮本身就是「停止」，必须可点；空闲时要有勾选才可点
        self.execute_btn.setEnabled(can_execute or self._executing)
        self._apply_execute_btn_state(can_execute)

        all_checked = (total_checkable > 0 and checked_count == total_checkable)
        can_select_all = not all_checked and not self._executing
        self.select_all_btn.setEnabled(can_select_all)
        # 图标-only 按钮：禁用态保留图标由 Qt 自动灰化（清空会变成空白按钮难辨认）

        none_checked = (checked_count == 0)
        can_deselect_all = not none_checked and not self._executing
        self.deselect_all_btn.setEnabled(can_deselect_all)

        self.loop_spin.setEnabled(not self._executing)
        self.stop_on_fail_check.setEnabled(not self._executing)
        self.suite_combo.setEnabled(not self._executing)
        self.save_suite_btn.setEnabled(not self._executing)
        is_suite_selected = self.suite_combo.currentText() != "(无套件)" and self.suite_combo.count() > 1
        self.del_suite_btn.setEnabled(not self._executing and is_suite_selected)

    def _apply_execute_btn_state(self, can_execute: bool):
        """「执行」按钮的两种样子：空闲 = 蓝色播放图标，执行中 = 红色停止图标（tooltip 同步）"""
        if self._executing:
            self.execute_btn.setIcon(qta.icon('fa6s.stop', color='white'))
            self.execute_btn.setToolTip("停止")
            self.execute_btn.setStyleSheet(self.STOP_BTN_QSS)
            return
        self.execute_btn.setToolTip("执行")
        if self._primary_btn_style:
            self.execute_btn.setStyleSheet(self._primary_btn_style)
        # 没勾选用例时按钮是灰的，这时不显示图标（与改造前一致）
        self.execute_btn.setIcon(self.execute_icon_enabled if can_execute else QIcon())

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
        # 执行中：这个按钮已经是「停止」了，点它就是请求中止正在跑的这一轮
        if self._executing:
            self.stop_requested.emit()
            return
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