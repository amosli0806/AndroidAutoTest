# views/dialogs/convert_target_dialog.py
"""「转为自动化用例」的目标位置选择框。

语音用例转自动化时要做两个决定：新用例叫什么、挂在项目树的哪个位置。
原先固定落到第一个项目下、名字自动加「（自动化）」，用户没法选 —— 项目一多
就得到处挪。这里把两件事一起收进一个框里。

只列「项目 / 文件夹」，用例节点不出现（挂到用例下面没有意义）。
样式做法与 voice_assert_dialog 一致：自带 _palette + container 自绘背景，
不往 theme.py 里加规则。
"""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel,
                             QLineEdit, QPushButton, QTreeWidget, QTreeWidgetItem,
                             QVBoxLayout)

from utils.dialogs import AutoFreeDialog
from utils.toast import show_toast


def _palette() -> dict:
    try:
        from utils.settings import THEME_MODE_DARK, Settings
        dark = Settings.get_theme_mode() == THEME_MODE_DARK
    except Exception:
        dark = False
    if dark:
        return {"bg": "#2b2b2b", "border": "#3d3d3d", "text": "#e8e8e8",
                "muted": "#9aa4b2", "input_bg": "#333333", "input_border": "#4a4a4a",
                "btn_bg": "#3a3a3a", "btn_fg": "#d0d0d0",
                "sel_bg": "#2f6fae", "sel_fg": "#ffffff", "hover": "#363636",
                "disabled": "#6f7783"}
    return {"bg": "#ffffff", "border": "#d0d0d0", "text": "#1f2733",
            "muted": "#98a2b3", "input_bg": "#f5f7fa", "input_border": "#e4e9f0",
            "btn_bg": "#f2f4f7", "btn_fg": "#5b6472",
            "sel_bg": "#bcdcff", "sel_fg": "#0d3f74", "hover": "#f2f7fd",
            "disabled": "#a8b0bb"}


class ConvertTargetDialog(AutoFreeDialog):
    """确定后 self.result = {'parent_id': ..., 'name': ...}；取消为 None。"""

    def __init__(self, parent, root_nodes, default_name=""):
        super().__init__(parent)
        self.result = None
        self._pal = _palette()
        pal = self._pal

        self.setObjectName("ConvertTargetDialog")
        self.setWindowTitle("转为自动化用例")
        self.setModal(True)
        self.setFixedSize(520, 440)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(0)

        container = QFrame()
        container.setObjectName("container")
        container.setStyleSheet(
            f"QFrame#container{{background-color:{pal['bg']};"
            f"border:1px solid {pal['border']};border-radius:12px;}}")
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 0)
        shadow.setColor(QColor(0, 0, 0, 90))
        container.setGraphicsEffect(shadow)
        outer.addWidget(container)

        box = QVBoxLayout(container)
        box.setContentsMargins(22, 18, 22, 16)
        box.setSpacing(10)

        title = QLabel("转为自动化用例")
        title.setStyleSheet(f"font-size:16px;font-weight:bold;color:{pal['text']};")
        box.addWidget(title)

        tip = QLabel("生成一条新的自动化用例，原语音用例保留。"
                     "播报转「语音播报」、回执转「语音回执」、断言原样带过去。")
        tip.setWordWrap(True)
        tip.setStyleSheet(f"font-size:11px;color:{pal['muted']};")
        box.addWidget(tip)

        def label(text):
            lb = QLabel(text)
            lb.setStyleSheet(f"font-size:12px;color:{pal['muted']};")
            return lb

        box.addWidget(label("用例名称"))
        self.name_edit = QLineEdit(default_name)
        self.name_edit.setPlaceholderText("新用例的名称")
        self.name_edit.setStyleSheet(
            f"QLineEdit{{background:{pal['input_bg']};border:1px solid {pal['input_border']};"
            f"border-radius:6px;padding:6px 10px;font-size:12px;color:{pal['text']};}}"
            f"QLineEdit:focus{{border-color:#1976d2;}}")
        box.addWidget(self.name_edit)

        box.addWidget(label("放到哪里（只能选功能模块）"))
        self.hint_label = QLabel("项目只是分组，用例要挂在功能模块下")
        self.hint_label.setStyleSheet(f"font-size:11px;color:{pal['muted']};")
        box.addWidget(self.hint_label)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(True)
        # 选中项四周的虚线焦点框是 QStyle 画在 item 上的（QSS 的 outline:none 管不到），
        # 只能让树不接焦点来去掉；点击照样能选中，选中态由 ::item:selected 绘制
        self.tree.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.tree.setStyleSheet(
            f"QTreeWidget{{background:{pal['input_bg']};"
            f"border:1px solid {pal['input_border']};border-radius:6px;"
            f"font-size:12px;color:{pal['text']};outline:0;padding:4px;"
            # 这两条管的是「::item 以外」的选中底色：展开箭头那一条会被视图
            # 用 palette 的深蓝 highlight 涂满，QSS 的 ::branch 盖不住
            f"selection-background-color:{pal['sel_bg']};"
            f"selection-color:{pal['sel_fg']};"
            f"show-decoration-selected:1;}}"
            # 树是整行选中，圆角会让选中块的左右两端各缺一块，看着琐碎 —— 用直角
            f"QTreeWidget::item{{min-height:24px;padding:2px 4px;border:none;}}"
            f"QTreeWidget::item:selected{{background:{pal['sel_bg']};"
            f"color:{pal['sel_fg']};border:none;}}"
            f"QTreeWidget::item:hover:!selected{{background:{pal['hover']};}}"
            # branch（展开箭头那一条）选中时沿用同一底色，整行连成一片
            f"QTreeWidget::branch:selected{{background:{pal['sel_bg']};}}"
            f"QTreeWidget::branch:hover:!selected{{background:transparent;}}"
            # 6px 细则滚动条，与项目其它面板一致
            f"QScrollBar:vertical{{background:transparent;width:6px;margin:0;}}"
            f"QScrollBar::handle:vertical{{background:{'#565656' if pal['bg'] != '#ffffff' else '#c8ced8'};"
            f"border-radius:3px;min-height:24px;}}"
            f"QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0;}}"
            f"QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{{background:transparent;}}")
        box.addWidget(self.tree, 1)

        self._fill_tree(self.tree, root_nodes or [])
        # 点项目节点时自动落到它的第一个模块 —— 项目不可选，点了没反应
        # 会让人以为界面卡住
        self.tree.itemClicked.connect(self._on_item_clicked)
        self._has_folder = True
        first = self._first_folder()
        if first is not None:
            # 默认选中第一个功能模块，用户直接回车就能用
            self.tree.setCurrentItem(first)
        else:
            self.hint_label.setText("还没有功能模块 —— 请先在自动化编辑页的项目下建一个模块")
            self.hint_label.setStyleSheet("font-size:11px;color:#c0392b;")
            self._has_folder = False

        foot = QHBoxLayout()
        foot.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setFixedWidth(84)
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setStyleSheet(
            f"QPushButton{{background:{pal['btn_bg']};border:none;border-radius:5px;"
            f"padding:6px 0;font-size:12px;color:{pal['btn_fg']};}}")
        cancel.clicked.connect(self.reject)
        foot.addWidget(cancel)
        ok = QPushButton("转换")
        self._ok_btn = ok
        ok.setFixedWidth(96)
        ok.setCursor(Qt.CursorShape.PointingHandCursor)
        ok.setStyleSheet(
            "QPushButton{background:#1976d2;border:none;border-radius:5px;"
            "padding:6px 0;font-size:12px;color:#fff;}")
        ok.clicked.connect(self._on_ok)
        foot.addWidget(ok)
        box.addLayout(foot)

        # 一个模块都没有时，点「转换」只能报错，直接置灰
        if not self._has_folder:
            self._ok_btn.setEnabled(False)

        self.name_edit.setFocus()
        self.name_edit.selectAll()

    def _fill_tree(self, tree, nodes, parent_item=None):
        """建出项目与功能模块；用例不出现（挂到用例下面没有意义）。

        **只有功能模块（folder）可选**：用例直接挂在项目下会让项目树变平，
        项目本身只是一个分组壳，往里塞用例后没法再和模块区分。所以项目节点
        保留显示（用户要看到层级），但去掉可选中标志并灰显。
        """
        for n in nodes:
            ntype = getattr(n, "type", "")
            if ntype == "case":
                continue
            item = QTreeWidgetItem([getattr(n, "name", "") or str(n.id)])
            item.setData(0, Qt.ItemDataRole.UserRole, n.id)
            item.setData(0, Qt.ItemDataRole.UserRole + 1, ntype)
            item.setExpanded(True)
            if ntype != "folder":
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                item.setForeground(0, QColor(self._pal["disabled"]))
                item.setToolTip(0, "用例要放到功能模块下，请选中一个模块")
            if parent_item is None:
                tree.addTopLevelItem(item)
            else:
                parent_item.addChild(item)
            self._fill_tree(tree, getattr(n, "children", None) or [], item)
            # 折叠项目节点没有意义（模块都藏在里面），默认展开
            if parent_item is not None:
                parent_item.setExpanded(True)

    def _on_item_clicked(self, item, _column):
        if item.data(0, Qt.ItemDataRole.UserRole + 1) == "folder":
            return
        for i in range(item.childCount()):
            child = item.child(i)
            if child.data(0, Qt.ItemDataRole.UserRole + 1) == "folder":
                self.tree.setCurrentItem(child)
                return

    def _first_folder(self):
        """深度优先找第一个可选的功能模块，作为默认选中项。"""
        def walk(item):
            if item is None:
                return None
            if item.flags() & Qt.ItemFlag.ItemIsSelectable:
                return item
            for i in range(item.childCount()):
                found = walk(item.child(i))
                if found is not None:
                    return found
            return None

        for i in range(self.tree.topLevelItemCount()):
            found = walk(self.tree.topLevelItem(i))
            if found is not None:
                return found
        return None

    def _on_ok(self):
        name = self.name_edit.text().strip()
        if not name:
            show_toast(self, "用例名称不能为空", duration=2000)
            return
        item = self.tree.currentItem()
        if item is None:
            show_toast(self, "请选择一个功能模块", duration=2000)
            return
        # 项目节点的 ItemIsSelectable 已去掉，但程序化 setCurrentItem 或方向键
        # 仍可能把它设成 current —— 这里按类型再拦一道（2026-11-21 实测）
        if item.data(0, Qt.ItemDataRole.UserRole + 1) != "folder":
            show_toast(self, "用例要放到功能模块下，请选中一个模块", duration=2500)
            return
        self.result = {
            "parent_id": item.data(0, Qt.ItemDataRole.UserRole),
            "name": name,
        }
        self.accept()
