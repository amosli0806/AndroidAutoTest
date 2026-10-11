# utils/wheel_guard.py
"""让滚轮「穿过」下拉框 / 数字框，专心滚动外层列表。

Qt 的默认行为：鼠标停在 QComboBox / QSpinBox / QDoubleSpinBox 上滚动滚轮，
这些控件会**自己改值**、并把事件吃掉，于是事件不再冒泡给外层 QScrollArea。
表现就是「一边滚卡片列表、光标划过某个参数框时，列表突然不动了，
参数的值反而被改掉」——很打断操作。

装上 WheelScrollGuard 之后：

* 控件**没有焦点** → 滚轮被转交给外层滚动区，列表继续滚，参数值不动；
* 控件**已经获得焦点**（用户点过它、正在编辑它）→ 保持原生行为，
  滚轮照旧微调数值，不打断当前操作。

用法（两步）::

    guard = WheelScrollGuard(scroll_area)   # 父对象挂滚动区，随它一起销毁
    guard.watch(card)                       # 给 card 下的下拉/数字控件装上

注意：`watch()` 只覆盖**调用时已存在**的控件；之后动态新增的子树要再调一次。
"""
from PyQt6.QtCore import QEvent, QObject
from PyQt6.QtWidgets import (
    QAbstractSpinBox, QApplication, QComboBox, QSlider,
)

# 会「吃掉」滚轮的控件类型（下拉、数字框、滑块）
_WHEEL_HOGS = (QComboBox, QAbstractSpinBox, QSlider)


class WheelScrollGuard(QObject):
    """把「未获得焦点」的下拉 / 数字控件的滚轮事件转交给外层滚动区。"""

    def __init__(self, scroll_area):
        super().__init__(scroll_area)
        self._scroll = scroll_area
        self._watched = set()      # 已安装的控件 id，避免重复安装

    # ------------------------------------------------------------------
    def watch(self, root) -> int:
        """给 root 及其子树里的下拉 / 数字控件装上过滤器。

        :param root: 任意 QWidget（传卡片、页面都可以）
        :return: 本次新安装的数量（重复调用不会重复安装）
        """
        if root is None:
            return 0
        added = 0
        for widget in self._iter_targets(root):
            if id(widget) in self._watched:
                continue
            widget.installEventFilter(self)
            self._watched.add(id(widget))
            added += 1
        return added

    @staticmethod
    def _iter_targets(root):
        for cls in _WHEEL_HOGS:
            if isinstance(root, cls):
                yield root
            yield from root.findChildren(cls)

    # ------------------------------------------------------------------
    def eventFilter(self, obj, event):
        if (event.type() == QEvent.Type.Wheel
                and isinstance(obj, _WHEEL_HOGS)
                and not obj.hasFocus()):
            viewport = self._scroll.viewport()
            if viewport is not None:
                # 转交给滚动区：效果与「光标停在空白处滚动」完全一致
                QApplication.sendEvent(viewport, event)
                return True
        return super().eventFilter(obj, event)
