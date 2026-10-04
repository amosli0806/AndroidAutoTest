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
        x = rect.x() + self._margin
        y = rect.y() + self._margin
        line_height = 0
        spacing = self._spacing if self._spacing >= 0 else self._get_spacing()
        for item in self._item_list:
            widget = item.widget()
            # 用 isHidden()（自身显式隐藏）而非 isVisible()（连带祖先隐藏）：
            # 页面整体切走时祖先不可见会让所有项被跳过、布局被清空，
            # 切回后若几何未变化就不会重排，出现整行控件"消失"。
            if widget and widget.isHidden():
                continue
            hint = item.sizeHint()
            if x + hint.width() > rect.right() - self._margin:
                x = rect.x() + self._margin
                y += line_height + spacing
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + spacing
            line_height = max(line_height, hint.height())
        if test_only:
            return y + line_height + self._margin - rect.y()
        return None

    def _get_spacing(self):
        if self._spacing >= 0:
            return self._spacing
        else:
            return self.parentWidget().style().pixelMetric(QStyle.PixelMetric.PM_DefaultLayoutSpacing, None, self.parentWidget())