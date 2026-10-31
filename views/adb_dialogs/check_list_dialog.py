# views/adb_dialogs/check_list_dialog.py
"""通用勾选列表对话框：搜索 + 可勾选列表 + 确定/取消。

来源：把性能检测页「选择目标进程」对话框（views/perf_view.py 的
MultiProcessDialog + _ProcCheckDelegate）抽成通用组件，Monkey 测试的
「选择应用」、以及将来任何「从一长串里多选几项」的弹窗都能复用，
不用每处再复制一遍自绘勾选框和细滚动条（2026-10-31 用户要求）。

交互与观感（与性能检测多进程对话框一致）：
- 自绘勾选框：未选灰框、选中蓝勾描边（不用 QSS ::indicator，避免打包后图片路径失效）
- 点整行任意位置切换勾选，不需要瞄准方框
- 无「选中行」高亮（勾选才是状态），避免点一下留一整条蓝底
- 细滚动条：8px 无箭头、轨道透明；横向滚动条关闭
- 深浅两套主题自适应
"""
from PyQt6.QtCore import Qt, QEvent, QRect, QSize, QPointF
from PyQt6.QtGui import QPainter, QPen, QColor
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QDialogButtonBox, QAbstractItemView, QStyledItemDelegate, QStyle,
)

from utils.settings import Settings, THEME_MODE_DARK

# CheckStateRole 读出来是 int：0 未选 / 1 半选 / 2 选中。
# PyQt6 里与 Qt.CheckState 枚举比较恒为 False（枚举不是 IntEnum），统一按 int 比。
_CHECKED = int(Qt.CheckState.Checked.value)


class CheckRowDelegate(QStyledItemDelegate):
    """列表行自绘：左边方框（选中画蓝勾），右边文本。"""

    BOX = 15
    ROW_H = 29
    PAD_L = 10

    def __init__(self, colors, parent=None):
        super().__init__(parent)
        self.c = colors

    def _box_rect(self, rect):
        return QRect(rect.left() + self.PAD_L,
                     rect.center().y() - self.BOX // 2 + 1,
                     self.BOX, self.BOX)

    def sizeHint(self, option, index):
        h = super().sizeHint(option, index).height()
        return QSize(0, max(self.ROW_H, h))

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect

        # 不画「选中」底色：勾选才是状态
        if option.state & QStyle.StateFlag.State_MouseOver:
            painter.fillRect(rect, QColor(self.c["hover"]))

        checked = int(index.data(Qt.ItemDataRole.CheckStateRole) or 0) == _CHECKED
        box = self._box_rect(rect)
        accent = QColor(self.c["accent"])
        painter.setPen(QPen(accent if checked else QColor(self.c["box_bd"]), 1.2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(box, 3.5, 3.5)

        if checked:
            pen = QPen(accent, 1.9)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            x, y = box.left(), box.top()
            painter.drawLine(QPointF(x + 3.0, y + 7.7), QPointF(x + 6.2, y + 10.9))
            painter.drawLine(QPointF(x + 6.2, y + 10.9), QPointF(x + 12.0, y + 4.2))

        painter.setPen(QColor(self.c["text"]))
        tr = rect.adjusted(box.right() - rect.left() + 10, 0, -8, 0)
        fm = painter.fontMetrics()
        text = fm.elidedText(index.data() or "", Qt.TextElideMode.ElideRight, tr.width())
        painter.drawText(tr, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
        painter.restore()

    def editorEvent(self, event, model, option, index):
        if (event.type() == QEvent.Type.MouseButtonRelease
                and event.button() == Qt.MouseButton.LeftButton
                and option.rect.contains(event.pos())):
            cur = int(index.data(Qt.ItemDataRole.CheckStateRole) or 0)
            new = 0 if cur == _CHECKED else _CHECKED
            model.setData(index, new, Qt.ItemDataRole.CheckStateRole)
            return True
        return super().editorEvent(event, model, option, index)


class CheckListDialog(QDialog):
    """通用勾选列表对话框。

    参数：
    - title：窗口标题
    - items：可选项（字符串列表）
    - selected：默认勾选的子集
    - tip：列表上方的说明文字（可选，空则不显示）
    - search_placeholder：搜索框占位文字（可选，空则不显示搜索框）
    - ok_text：确定按钮文字（默认「确定」）
    """

    def __init__(self, title, items, selected=None, tip="", search_placeholder="",
                 ok_text="确定", parent=None):
        super().__init__(parent)
        self.setObjectName("CheckListDialog")
        self.setWindowTitle(title)
        self.setMinimumWidth(480)
        self.resize(500, 440)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.setSpacing(10)

        if tip:
            self.tip = QLabel(tip)
            self.tip.setWordWrap(True)
            lay.addWidget(self.tip)

        if search_placeholder:
            self.search = QLineEdit()
            self.search.setPlaceholderText(search_placeholder)
            self.search.setObjectName("searchEdit")
            self.search.setClearButtonEnabled(True)
            lay.addWidget(self.search)
            self.search.textChanged.connect(self._on_search)
        else:
            self.search = None

        self.list = QListWidget()
        self.list.setObjectName("CheckList")
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.list.setUniformItemSizes(True)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        chosen = set(selected or [])
        for name in items:
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setCheckState(
                Qt.CheckState.Checked if name in chosen else Qt.CheckState.Unchecked)
            self.list.addItem(item)
        lay.addWidget(self.list, 1)

        box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        ok_btn = box.button(QDialogButtonBox.StandardButton.Ok)
        cancel_btn = box.button(QDialogButtonBox.StandardButton.Cancel)
        ok_btn.setText(ok_text)
        cancel_btn.setText("取消")
        ok_btn.setObjectName("primaryBtn")
        for btn in (ok_btn, cancel_btn):
            btn.setMinimumSize(84, 32)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)

        self._apply_style()

    # ---------- 搜索 ----------
    def _on_search(self, text):
        kw = text.lower().strip()
        for i in range(self.list.count()):
            item = self.list.item(i)
            pkg = item.data(Qt.ItemDataRole.UserRole) or ""
            item.setHidden(bool(kw) and kw not in pkg.lower())

    # ---------- 外观 ----------
    def _is_dark(self) -> bool:
        try:
            return Settings.get_theme_mode() == THEME_MODE_DARK
        except Exception:
            return False

    def _apply_style(self):
        dark = self._is_dark()
        if dark:
            bg, tip_c = "#1e2023", "#a9b0b8"
            border, list_bg, item_c = "#34373c", "#26282b", "#e5e7ea"
            hover = "#2c2f33"
            box_bd, bar_c, bar_h = "#5a6068", "#4b5057", "#5f656d"
            btn_bd, cancel_fg = "#43474d", "#d6d9dd"
            accent = "#4c8dff"
            search_bg, search_bd = "#26282b", "#3a3e44"
        else:
            bg, tip_c = "#f6f7f9", "#6b7280"
            border, list_bg, item_c = "#dfe3e8", "#ffffff", "#2b2f33"
            hover = "#f0f3f7"
            box_bd, bar_c, bar_h = "#c3c9d0", "#c9ced6", "#b3b9c2"
            btn_bd, cancel_fg = "#d5dae0", "#3a3f45"
            accent = "#2d6cdf"
            search_bg, search_bd = "#ffffff", "#d5dae0"

        self.setStyleSheet(f"""
            QDialog#CheckListDialog {{ background: {bg}; }}
            QLabel {{ font-size: 13px; color: {tip_c}; background: transparent; }}
            QLineEdit#searchEdit {{
                background: {search_bg}; color: {item_c};
                border: 1px solid {search_bd}; border-radius: 6px;
                padding: 6px 10px; font-size: 13px;
            }}
            QLineEdit#searchEdit:focus {{ border-color: {accent}; }}
            QPushButton#primaryBtn {{
                background: #2d6cdf; color: #ffffff; border: none;
                border-radius: 5px; font-size: 13px; padding: 0 16px;
            }}
            QPushButton#primaryBtn:hover {{ background: #3a7aeb; }}
            QPushButton#primaryBtn:pressed {{ background: #2560c8; }}
            QPushButton {{
                background: transparent; color: {cancel_fg};
                border: 1px solid {btn_bd}; border-radius: 5px;
                font-size: 13px; padding: 0 16px;
            }}
            QPushButton:hover {{ border-color: #2d6cdf; color: #2d6cdf; }}
        """)

        # 行内容由 delegate 自绘，QSS 只管容器；滚动条单独设（同一份 QSS 混写
        # ::item 与 QScrollBar 部分 Qt 版本会丢滚动条规则）
        self.list.setStyleSheet(f"""
            QListWidget#CheckList {{
                background: {list_bg}; border: 1px solid {border};
                border-radius: 6px; outline: none; padding: 4px;
                font-size: 13px; color: {item_c};
            }}
        """)
        self.list.setItemDelegate(CheckRowDelegate({
            "text": item_c, "box_bd": box_bd, "accent": accent, "hover": hover,
        }, self.list))
        self.list.verticalScrollBar().setStyleSheet(f"""
            QScrollBar:vertical {{
                background: transparent; width: 8px; margin: 2px 0; border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {bar_c}; min-height: 28px; border-radius: 4px;
            }}
            QScrollBar::handle:vertical:hover {{ background: {bar_h}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0; border: none; background: none;
            }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
                background: none;
            }}
        """)

    # ---------- 结果 ----------
    def selected_items(self) -> list:
        out = []
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                out.append(item.text())
        return out
