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
                "sel_bg": "#1d3a57", "sel_fg": "#cfe2f7", "hover": "#363636"}
    return {"bg": "#ffffff", "border": "#d0d0d0", "text": "#1f2733",
            "muted": "#98a2b3", "input_bg": "#f5f7fa", "input_border": "#e4e9f0",
            "btn_bg": "#f2f4f7", "btn_fg": "#5b6472",
            "sel_bg": "#e8f1fd", "sel_fg": "#12406e", "hover": "#f6f9fd"}


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

        box.addWidget(label("放到哪里"))
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setStyleSheet(
            f"QTreeWidget{{background:{pal['input_bg']};"
            f"border:1px solid {pal['input_border']};border-radius:6px;"
            f"font-size:12px;color:{pal['text']};outline:none;padding:4px;}}"
            f"QTreeWidget::item{{min-height:24px;padding:2px 4px;border-radius:4px;}}"
            f"QTreeWidget::item:selected{{background:{pal['sel_bg']};color:{pal['sel_fg']};}}"
            f"QTreeWidget::item:hover{{background:{pal['hover']};}}"
            # 6px 细则滚动条，与项目其它面板一致
            f"QScrollBar:vertical{{background:transparent;width:6px;margin:0;}}"
            f"QScrollBar::handle:vertical{{background:{'#565656' if pal['bg'] != '#ffffff' else '#c8ced8'};"
            f"border-radius:3px;min-height:24px;}}"
            f"QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0;}}"
            f"QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{{background:transparent;}}")
        box.addWidget(self.tree, 1)

        self._fill_tree(self.tree, root_nodes or [])
        # 默认选中第一个可挂载的节点，用户直接回车就能用
        if self.tree.topLevelItemCount() > 0:
            first = self.tree.topLevelItem(0)
            self.tree.setCurrentItem(first)

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
        ok.setFixedWidth(96)
        ok.setCursor(Qt.CursorShape.PointingHandCursor)
        ok.setStyleSheet(
            "QPushButton{background:#1976d2;border:none;border-radius:5px;"
            "padding:6px 0;font-size:12px;color:#fff;}")
        ok.clicked.connect(self._on_ok)
        foot.addWidget(ok)
        box.addLayout(foot)

        self.name_edit.setFocus()
        self.name_edit.selectAll()

    def _fill_tree(self, tree, nodes, parent_item=None):
        """只建「项目 / 文件夹」，用例不出现（挂到用例下面没有意义）。"""
        for n in nodes:
            if getattr(n, "type", "") == "case":
                continue
            item = QTreeWidgetItem([getattr(n, "name", "") or str(n.id)])
            item.setData(0, Qt.ItemDataRole.UserRole, n.id)
            if parent_item is None:
                tree.addTopLevelItem(item)
            else:
                parent_item.addChild(item)
            item.setExpanded(True)
            self._fill_tree(tree, getattr(n, "children", None) or [], item)

    def _on_ok(self):
        name = self.name_edit.text().strip()
        if not name:
            show_toast(self, "用例名称不能为空", duration=2000)
            return
        item = self.tree.currentItem()
        if item is None:
            show_toast(self, "请选择一个项目或文件夹", duration=2000)
            return
        self.result = {
            "parent_id": item.data(0, Qt.ItemDataRole.UserRole),
            "name": name,
        }
        self.accept()
