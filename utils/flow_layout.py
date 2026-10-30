# utils/flow_layout.py
from PyQt6.QtCore import QPoint, QRect, QSize, Qt
from PyQt6.QtWidgets import QLayout, QStyle, QWidget

class FlowLayout(QLayout):
    def __init__(self, parent=None, margin=0, spacing=-1):
        super().__init__(parent)
        self._item_list = []
        self._margin = margin
        self._spacing = spacing

    def __del__(self):
        item = self.takeAt(0)
        while item:
            item = self.takeAt(0)

    def addItem(self, item):
        self._item_list.append(item)

    def count(self):
        return len(self._item_list)

    def itemAt(self, index):
        if 0 <= index < len(self._item_list):
            return self._item_list[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self._item_list):
            return self._item_list.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        height = self._do_layout(QRect(0, 0, width, 0), True)
        return height

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._item_list:
            size = size.expandedTo(item.minimumSize())
        margin = self._margin
        size += QSize(2 * margin, 2 * margin)
        return size

    def _do_layout(self, rect, test_only):
        """先按行分组，再统一摆放 —— 行内高度要等整行排完才知道，才能垂直居中。

        以前是边排边放，x/y 一到就 setGeometry，行高只能"事后"知道，
        于是同一行里矮的控件（QLabel 17px）贴着行顶、高的控件（下拉框 30px）
        上下撑开，看起来就是「文案没垂直居中」（2026-10-30 反馈）。
        """
        margin = self._margin
        spacing = self._spacing if self._spacing >= 0 else self._get_spacing()
        right = rect.right() - margin
        x = rect.x() + margin
        y = rect.y() + margin
        line = []            # [(item, x, w, h), ...] 当前行
        line_height = 0

        def _flush(top):
            """摆完当前行：行内每项按 (行高 - 自身高) / 2 垂直居中。"""
            for it, ix, iw, ih in line:
                iy = top + max(0, (line_height - ih) // 2)
                it.setGeometry(QRect(QPoint(ix, iy), QSize(iw, ih)))

        for item in self._item_list:
            widget = item.widget()
            # 用 isHidden()（自身显式隐藏）而非 isVisible()（连带祖先隐藏）：
            # 页面整体切走时祖先不可见会让所有项被跳过、布局被清空，
            # 切回后若几何未变化就不会重排，出现整行控件"消失"。
            if widget and widget.isHidden():
                continue
            hint = item.sizeHint()
            # 支持能"自己再折行"的子控件（如组内用 FlowLayout 的分组容器）：
            # 它的高度随可用宽度变化，这里按实际给的宽度问它要高度，
            # 否则宽屏一行、窄屏折行时会被按单行高度裁掉半截。
            item_w = min(hint.width(), max(1, rect.width() - 2 * margin))
            item_h = hint.height()
            if widget is not None and widget.hasHeightForWidth():
                item_h = widget.heightForWidth(item_w)
            if line and x + item_w > right:
                if not test_only:
                    _flush(y)
                y += line_height + spacing
                x = rect.x() + margin
                line = []
                line_height = 0
            line.append((item, x, item_w, item_h))
            x += item_w + spacing
            line_height = max(line_height, item_h)

        if not line:
            return 2 * margin
        total = (y + line_height + margin) - rect.y()
        if not test_only:
            _flush(y)
        return total if test_only else None

    def _get_spacing(self):
        if self._spacing >= 0:
            return self._spacing
        else:
            return self.parentWidget().style().pixelMetric(QStyle.PixelMetric.PM_DefaultLayoutSpacing, None, self.parentWidget())