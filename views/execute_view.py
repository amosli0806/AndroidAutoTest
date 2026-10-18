# views/execute_view.py
import qtawesome as qta
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                             QTreeView, QLabel, QSpinBox, QCheckBox,
                             QComboBox, QMessageBox, QSizePolicy,
                             QDialog, QMenu)
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
    # 套件增删改名后发出：性能检测页的「场景化测试」套件下拉要跟着同步，
    # 否则新套件要重启虫师才出现（2026-10-10 用户反馈）
    suites_changed = pyqtSignal()

    # 执行中「执行」按钮变成红色的「停止」（配色与 ADB 工具箱行内按钮同一套）
    STOP_BTN_QSS = """
        QPushButton {
            background-color: #e74c3c;
            border: none;
            padding: 2px 10px;
            border-radius: 4px;
            /* 内容盒 24 + padding 2x2 = 总高 28，与套件下拉框一致（无边框） */
            min-height: 24px;
            max-height: 24px;
            font-size: 12px;
            color: #ffffff;
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
        self._current_theme_mode = theme_mode
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
                    min-height: 22px; max-height: 22px;
                }
                QPushButton#ToolIconBtn:hover { background-color: #4a4a4a; }
                QPushButton#ToolIconBtn:disabled { background-color: #333; }
            """
            # 文字版工具按钮（保存套件 / 删除套件 / 报告）：宽度自适应文字
            text_btn_style = """
                QPushButton#ToolTextBtn {
                    background-color: #3c3c3c;
                    border: 1px solid #555;
                    border-radius: 4px;
                    padding: 2px 9px;
                    min-height: 22px; max-height: 22px;
                    font-size: 12px;
                    color: #e0e0e0;
                }
                QPushButton#ToolTextBtn:hover { background-color: #4a4a4a; }
                QPushButton#ToolTextBtn:disabled { background-color: #333; color: #777; }
            """
            # 主操作（执行）：禁用时与其他工具按钮一致，启用后蓝底白图标突出
            self._primary_btn_style = """
                QPushButton#ExecTextBtn {
                    background-color: #3c3c3c;
                    border: 1px solid #555;
                    border-radius: 4px;
                    padding: 2px 10px;
                    min-height: 22px; max-height: 22px;
                    font-size: 12px;
                    color: #999999;
                }
                QPushButton#ExecTextBtn:enabled { background-color: #1976d2; border: 1px solid transparent; color: #ffffff; }
                QPushButton#ExecTextBtn:enabled:hover { background-color: #1565c0; }
                QPushButton#ExecTextBtn:disabled { background-color: #333; color: #777777; }
            """
        else:
            icon_btn_style = """
                QPushButton#ToolIconBtn {
                    background-color: #f5f6f8;
                    border: 1px solid #d0d0d0;
                    border-radius: 4px;
                    padding: 2px;
                    min-width: 34px; max-width: 34px;
                    min-height: 22px; max-height: 22px;
                }
                QPushButton#ToolIconBtn:hover { background-color: #e8eaee; }
                QPushButton#ToolIconBtn:disabled { background-color: #eee; }
            """
            text_btn_style = """
                QPushButton#ToolTextBtn {
                    background-color: #f5f6f8;
                    border: 1px solid #d0d0d0;
                    border-radius: 4px;
                    padding: 2px 9px;
                    min-height: 22px; max-height: 22px;
                    font-size: 12px;
                    color: #333;
                }
                QPushButton#ToolTextBtn:hover { background-color: #e8eaee; }
                QPushButton#ToolTextBtn:disabled { background-color: #eee; color: #aaa; }
            """
            self._primary_btn_style = """
                QPushButton#ExecTextBtn {
                    background-color: #f5f6f8;
                    border: 1px solid #d0d0d0;
                    border-radius: 4px;
                    padding: 2px 10px;
                    min-height: 22px; max-height: 22px;
                    font-size: 12px;
                    color: #aaaaaa;
                }
                QPushButton#ExecTextBtn:enabled { background-color: #1976d2; border: 1px solid transparent; color: #ffffff; }
                QPushButton#ExecTextBtn:enabled:hover { background-color: #1565c0; }
                QPushButton#ExecTextBtn:disabled { background-color: #eeeeee; color: #aaaaaa; }
            """

        # 应用样式到各控件（使用 findChildren 或直接设置）
        self._equalize_tool_btn_widths()
        for btn in self.findChildren(QPushButton):
            if btn.objectName() == "ToolIconBtn":
                btn.setStyleSheet(icon_btn_style)
            elif btn.objectName() == "ExecTextBtn":
                btn.setStyleSheet(self._primary_btn_style)
            elif btn.objectName() == "ToolTextBtn":
                btn.setStyleSheet(text_btn_style)
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

        # 注意：不能再把文字按钮样式（btn_style）赋给 _primary_btn_style——
        # 执行按钮已改为图标按钮，_primary_btn_style 在上方按主题生成的是
        # 34px 图标版；这里一旦覆盖回 90px 文字版，_update_buttons 就会把
        # 执行按钮重新撑宽套蓝底（历史上「执行按钮还显示之前的宽度和背景」的根因）
        self.tree_view.setStyleSheet(self._tree_qss())
        # 树复选框边框色跟随主题（delegate 是自绘的，QSS 管不到）；
        # 选中行无高亮底色，选中态边框与普通态一致
        check_border = "#b8b8b8" if theme_mode == ThemeMode.DARK else "#5a5a5a"
        if getattr(self, "_check_delegate", None) is not None:
            self._check_delegate.set_border_colors(check_border, check_border)
        self._rebuild_icons()
        self._update_buttons()

    def _equalize_tool_btn_widths(self):
        """把「全选 / 执行 / 保存套件 / 删除套件 / 报告」五个按钮设成同一宽度。

        宽度按**最长文案 + 当前字体**现算，而不是写死像素：高 DPI 下字体变大会跟着
        变宽，写死会裁字（本项目在高 DPI 固定宽度上吃过亏）。
        """
        btns = (self.toggle_select_btn, self.execute_btn,
                self.save_suite_btn, self.del_suite_btn, self.report_btn)
        fm = self.save_suite_btn.fontMetrics()
        longest = max(fm.horizontalAdvance(t) for t in
                      ("取消全选", "保存套件", "删除套件", "报告", "执行", "停止"))
        # 按「最长文案 + 内边距(9*2) + 边框」算，不再取 sizeHint ——
        # sizeHint 里含全局 QSS 的 min-width(60)，会把 5 个按钮都撑到 81px，
        # 进而把主窗口最小宽度顶到 1265（小屏笔记本会放不下）
        width = longest + 24
        for b in btns:
            b.setFixedWidth(width)

    def _rebuild_icons(self):
        """主题切换后按当前主题色重建工具栏图标（图标-only 按钮的可见性命脉）。"""
        # 全选 / 执行 已改成文字按钮，这里不需要重建图标（配色由 apply_theme 的 QSS 负责）
        # 保存套件 / 删除套件 / 报告 已改成文字按钮，不再重建图标

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        # 布局间距 8px = 容器 QSS padding 8px：工具栏与树的间距和树到
        # 容器左/右/下的间距完全一致（树的 QSS margin 已归零）
        layout.setSpacing(8)

        # ---------- 工具栏（单行：纯按钮只显示图标，悬停见 tooltip） ----------
        # 顶部 8px（面板 QSS 的 padding 不参与布局，需在这里给出），
        # 与下方到树的布局间距 8px 对称，工具栏行垂直居中不贴边
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(8, 8, 8, 0)
        toolbar.setSpacing(6)

        self.toggle_select_btn = QPushButton("全选")
        self.toggle_select_btn.setObjectName("ToolTextBtn")
        self.toggle_select_btn.setFixedHeight(28)
        self.toggle_select_btn.setToolTip("全选")
        self.toggle_select_btn.clicked.connect(self._on_toggle_select)

        self.execute_btn = QPushButton("执行")
        self.execute_btn.setFixedHeight(28)
        self.execute_btn.setObjectName("ExecTextBtn")
        self.execute_btn.setToolTip("执行")
        self.execute_btn.setEnabled(False)
        self.execute_btn.clicked.connect(self._execute)

        self.loop_label = QLabel("循环")
        self.loop_spin = QSpinBox()
        self.loop_spin.setRange(1, 999)
        self.loop_spin.setValue(1)
        self.loop_spin.setFixedWidth(56)
        self.loop_spin.setFixedHeight(28)   # 与图标按钮同高，避免撑高工具栏行
        self.loop_spin.setToolTip("循环次数")

        self.stop_on_fail_check = BorderedCheckBox("失败停止")

        # 失败重试：次数 / 间隔 / 触发条件（全局，对所有步骤生效）
        self.retry_label = QLabel("重试")
        self.retry_spin = QSpinBox()
        self.retry_spin.setRange(0, 5)
        self.retry_spin.setValue(1)
        self.retry_spin.setFixedWidth(54)
        self.retry_spin.setFixedHeight(28)
        self.retry_spin.setToolTip("失败重试次数（0 = 不重试）")
        self.retry_delay_spin = QSpinBox()
        self.retry_delay_spin.setRange(0, 30)
        self.retry_delay_spin.setValue(2)
        self.retry_delay_spin.setFixedWidth(54)
        self.retry_delay_spin.setFixedHeight(28)
        self.retry_delay_spin.setToolTip("重试间隔（秒）")
        # 单位标签（原来只有 tooltip，鼠标不悬停就看不出这两个数字是什么）
        self.retry_unit_label = QLabel("次")
        self.retry_interval_label = QLabel("间隔")
        self.retry_delay_unit_label = QLabel("秒")
        self.retry_cond_combo = QComboBox()
        self.retry_cond_combo.addItems(["仅超时类", "所有失败"])
        self.retry_cond_combo.setFixedHeight(28)
        # 下拉面板宽度跟着控件走：太窄会把「仅超时类 / 所有失败」截成「仅… / 所…」
        self.retry_cond_combo.setMinimumWidth(96)
        self.retry_cond_combo.setToolTip("重试触发条件：仅超时类（等待/连接超时）或所有失败")

        # 套件与报告相关控件（图标化，悬停见 tooltip）
        self.suite_combo = QComboBox()
        from utils.widget_helpers import prepare_combo_view
        prepare_combo_view(self.suite_combo)
        self.suite_combo.setMinimumWidth(130)
        self.suite_combo.setFixedHeight(28)  # 与图标按钮同高，避免撑高工具栏行
        self.suite_combo.currentTextChanged.connect(self._on_suite_selected)
        # 套件内容 / 执行顺序编辑走右键菜单：不往工具栏加按钮，避免挤动已有布局
        self.suite_combo.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.suite_combo.customContextMenuRequested.connect(self._on_suite_menu)
        self.suite_combo.setToolTip("选择套件；右键可编辑套件内容与执行顺序")

        self.save_suite_btn = QPushButton("保存套件")
        self.save_suite_btn.setFixedHeight(28)
        self.save_suite_btn.setObjectName("ToolTextBtn")
        self.save_suite_btn.setToolTip("把当前选中的用例保存为套件")
        self.save_suite_btn.clicked.connect(self._save_current_as_suite)

        self.del_suite_btn = QPushButton("删除套件")
        self.del_suite_btn.setFixedHeight(28)
        self.del_suite_btn.setObjectName("ToolTextBtn")
        self.del_suite_btn.setToolTip("删除选中的套件")
        self.del_suite_btn.clicked.connect(self._delete_selected_suite)

        self.report_btn = QPushButton("报告")
        self.report_btn.setFixedHeight(28)
        self.report_btn.setObjectName("ToolTextBtn")
        self.report_btn.setToolTip("生成测试报告")
        self.report_btn.setEnabled(False)
        self.report_btn.clicked.connect(self._on_report_clicked)

        toolbar.addWidget(self.toggle_select_btn)
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
        toolbar.addSpacing(6)
        toolbar.addWidget(self.retry_label)
        toolbar.addWidget(self.retry_spin)
        toolbar.addWidget(self.retry_unit_label)
        toolbar.addSpacing(4)
        toolbar.addWidget(self.retry_interval_label)
        toolbar.addWidget(self.retry_delay_spin)
        toolbar.addWidget(self.retry_delay_unit_label)
        toolbar.addWidget(self.retry_cond_combo)
        toolbar.addStretch()

        layout.addLayout(toolbar)

        # ---------- 用例树 ----------
        self.tree_view = _CaseTreeView()
        self.tree_view.setHeaderHidden(True)
        self.tree_view.setIndentation(20)
        # indicator 不写 QSS 规则：走 Fusion 原生绘制（蓝底白勾），
        # 与语音播报页用例树的复选框样式同源一致
        self.tree_view.setStyleSheet(self._tree_qss())
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
        # 树节点复选框：与语音播报页的用例树同款 —— Fusion 原生绘制 +
        # delegate 叠一圈清晰边框（QSS indicator 的 1px 边框高 DPI 下看不清）。
        # 选中行不再用白色边框：选中态高亮已移除（见主窗口 _apply_execute_page_theme），
        # 选中与否的边框颜色一致，避免「选中行复选框边框消失」
        from views.voice_view import _BorderedTreeItemDelegate
        self._check_delegate = _BorderedTreeItemDelegate(
            border_color="#5a5a5a",          # 亮色主题边框（暗色在 apply_theme 刷新）
            selected_border_color="#5a5a5a",
            parent=self.tree_view,
        )
        self.tree_view.setItemDelegate(self._check_delegate)
        # 展开状态持久化：默认全折叠，记住用户上次展开的项目/模块（存 data/config.json）
        self._tree_state = tree_state.bind_view(self.tree_view, "execute_tree")

        self.placeholder = QLabel("无自动化执行", self)
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setStyleSheet("color: #999; font-size: 16px;")
        self.placeholder.hide()

        self._executing = False

    def _tree_qss(self) -> str:
        """用例树的样式：行高 + 底色，复选框 indicator 不写规则。

        indicator 不写 QSS 规则时走 Fusion 原生绘制，与语音播报页的用例树
        完全同源（含勾号粗细、三态灰块等细节）。树的其余样式（item 高度、
        底色）仍由这里控制；复选框外圈边框由 _BorderedTreeItemDelegate 叠加
        （见 apply_theme）。

        壁纸模式注意：树调色板不得给 Base 设透明度（历史方案，导致原生
        勾选框渲染成黑块），必须与日夜模式一致用实色 —— 见主窗口
        _apply_execute_page_theme 的 palette 处理。
        """
        return """
            QTreeView {
                padding: 4px;
                /* 上边距归零：工具栏与树之间只留布局 spacing(8px)；
                   左/下/右保留 10px，树边框与执行区边框的间距不变（与
                   主窗口面板 QSS 的取值保持一致） */
                margin: 0px 10px 10px 10px;
            }
            QTreeView::item {
                height: 30px !important;
                min-height: 30px !important;
                max-height: 30px !important;
                /* 必须显式透明：::item 规则存在但没写 background 时，
                   壁纸的半透明树底上原生勾选框会被填充成纯黑方块 */
                background: transparent;
            }
        """

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
        # 全选/取消全选是一个切换按钮：按当前勾选状态切图标与 tooltip
        if all_checked:
            self.toggle_select_btn.setText("取消全选")
            self.toggle_select_btn.setToolTip("取消全选")
        else:
            self.toggle_select_btn.setText("全选")
            self.toggle_select_btn.setToolTip("全选")
        self.toggle_select_btn.setEnabled(total_checkable > 0 and not self._executing)

        self.loop_spin.setEnabled(not self._executing)
        self.stop_on_fail_check.setEnabled(not self._executing)
        self.retry_spin.setEnabled(not self._executing)
        self.retry_delay_spin.setEnabled(not self._executing)
        self.retry_cond_combo.setEnabled(not self._executing)
        self.suite_combo.setEnabled(not self._executing)
        self.save_suite_btn.setEnabled(not self._executing)
        is_suite_selected = self.suite_combo.currentText() != "(无套件)" and self.suite_combo.count() > 1
        self.del_suite_btn.setEnabled(not self._executing and is_suite_selected)

    def _apply_execute_btn_state(self, can_execute: bool):
        """「执行」按钮的两种样子：有勾选 = 蓝底白播放图标，执行中 = 红底白停止图标；
        无勾选禁用时与其他工具按钮一致（浅灰底深图标），不再突兀。"""
        if self._executing:
            self.execute_btn.setText("停止")
            self.execute_btn.setToolTip("停止")
            self.execute_btn.setStyleSheet(self.STOP_BTN_QSS)
            return
        self.execute_btn.setText("执行")
        self.execute_btn.setToolTip("执行")
        if self._primary_btn_style:
            self.execute_btn.setStyleSheet(self._primary_btn_style)

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
        ids = self._ordered_run_ids(self._get_checked_ids())
        if ids:
            self._executing = True
            self._update_buttons()
            self.execute_selected.emit(ids)

    def _on_toggle_select(self):
        """切换按钮：按点击那一刻的勾选状态决定全选还是取消全选。"""
        checked = len(self._get_checked_ids())
        total = 0

        def traverse(item):
            nonlocal total
            if item.isCheckable():
                total += 1
            for i in range(item.rowCount()):
                traverse(item.child(i))

        for i in range(self.model.rowCount()):
            traverse(self.model.item(i))
        if total > 0 and checked >= total:
            self.deselect_all()
        else:
            self.select_all()

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

    def _on_suite_menu(self, pos):
        """套件下拉的右键菜单：编辑套件内容与执行顺序。"""
        if self._executing:
            return
        name = self.suite_combo.currentText()
        menu = QMenu()
        edit_action = menu.addAction("编辑套件内容 / 执行顺序…")
        edit_action.setEnabled(bool(name) and name != "(无套件)")
        chosen = menu.exec(self.suite_combo.mapToGlobal(pos))
        if chosen is edit_action:
            self._edit_current_suite()

    def _edit_current_suite(self):
        """打开套件内容对话框：查看用例、拖拽排序、增删；保存后写回套件。"""
        name = self.suite_combo.currentText()
        if not name or name == "(无套件)":
            return
        suite = self.suite_model.get_suite_by_name(name)
        if not suite:
            return

        from views.dialogs.suite_editor_dialog import SuiteEditorDialog

        # 用例名从项目树取；取不到的回退显示 id（用例可能已被删，保存时会被清掉）
        id_to_name = {}
        if self.project_model is not None:
            for cid in suite.case_ids:
                node = self.project_model.get_node_by_id(cid)
                if node:
                    id_to_name[cid] = node.name

        dlg = SuiteEditorDialog(name, list(suite.case_ids), id_to_name,
                                available_ids=self._get_checked_ids(), parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        new_ids = dlg.result_ids()
        if not new_ids:
            show_toast(message="套件不能为空，未保存")
            return
        if new_ids == list(suite.case_ids):
            return

        self.suite_model.update_suite(name, new_ids)
        self.suites_changed.emit()
        self._refresh_suite_combo()
        self._suppress_suite_signal = True
        self.suite_combo.setCurrentText(name)
        self._suppress_suite_signal = False
        self._on_suite_selected(name)      # 按新集合重新勾选树
        show_toast(message=f"套件「{name}」已更新（{len(new_ids)} 条）")

    def _ordered_run_ids(self, ids):
        """执行顺序：选中了套件、且勾选集合与它一致时，按套件里存的顺序跑。

        没选套件（或勾选与套件不一致，说明是手动勾的）时保持树的顺序，
        行为与改动前完全一致 —— 只有用户真的排过序才会体现差异。
        """
        name = self.suite_combo.currentText()
        if name and name != "(无套件)":
            suite = self.suite_model.get_suite_by_name(name)
            if suite and set(suite.case_ids) == set(ids):
                order = {cid: i for i, cid in enumerate(suite.case_ids)}
                return sorted(ids, key=lambda c: order.get(c, len(order)))
        return list(ids)

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
                self.suites_changed.emit()
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
            self.suites_changed.emit()
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
        self.suites_changed.emit()
        show_toast(message="删除套件成功")