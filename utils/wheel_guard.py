# utils/wheel_guard.py
"""让滚轮「穿过」下拉框 / 数字框，专心滚动外层列表。

Qt 的默认行为：鼠标停在 QComboBox / QSpinBox / QDoubleSpinBox 上滚动滚轮，
这些控件会**自己改值**、并把事件吃掉，于是事件不再冒泡给外层 QScrollArea。
表现就是「一边滚列表、光标划过某个参数框时，列表突然不动了，
参数的值反而被改掉」——很打断操作。

装上 WheelScrollGuard 之后：

* 控件**没有焦点** → 滚轮交给外层滚动区，列表继续滚，参数值不动；
* 控件**已经获得焦点**（用户点过它、正在编辑它）→ 保持原生行为，
  滚轮照旧微调数值，不打断当前操作。

滚动区是**事件发生时动态解析**的：从控件沿父链往上找「最近一个还能朝该方向
滚动的 QScrollArea」。这样嵌套滚动区也能正确处理 —— 内层滚到头时，
滚轮顺势交给外层继续滚（例如 ADB 工具箱里的 Monkey 面板：
高级选项有自己的滚动区，整个面板又在外层工具箱滚动区里）。

用法（两步）::

    guard = WheelScrollGuard(panel)   # 父对象挂被守护的区域，随它一起销毁
    guard.watch(panel)                # 给该区域下的下拉/数字控件装上

注意：`watch()` 只覆盖**调用时已存在**的控件；之后动态新增的子树要再调一次。
"""
from PyQt6.QtCore import QEvent, QObject
from PyQt6.QtWidgets import (
    QAbstractSpinBox, QApplication, QComboBox, QScrollArea, QSlider,
)

# 会「吃掉」滚轮的控件类型（下拉、数字框、滑块）
_WHEEL_HOGS = (QComboBox, QAbstractSpinBox, QSlider)


def _enclosing_scroll_areas(widget):
    """从内到外列出控件的所有祖先滚动区。"""
    areas = []
    parent = widget.parent()
    while parent is not None:
        if isinstance(parent, QScrollArea):
            areas.append(parent)
        parent = parent.parent()
    return areas


def _can_scroll(area, delta_y: int) -> bool:
    """该滚动区还能朝 delta_y 方向滚吗。

    delta_y > 0 是滚轮前推（内容上移，滚动条数值减小），< 0 反之。
    """
    bar = area.verticalScrollBar()
    return bar.value() > bar.minimum() if delta_y > 0 else bar.value() < bar.maximum()


class WheelScrollGuard(QObject):
    """把「未获得焦点」的下拉 / 数字控件的滚轮事件转交给外层滚动区。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._watched = set()      # 已安装的控件 id，避免重复安装

    # ------------------------------------------------------------------
    def watch(self, root) -> int:
        """给 root 及其子树里的下拉 / 数字控件装上过滤器。

        :param root: 任意 QWidget（传卡片、面板、页面都可以）
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
        if not (event.type() == QEvent.Type.Wheel
                and isinstance(obj, _WHEEL_HOGS)
                and not obj.hasFocus()):
            return super().eventFilter(obj, event)

        areas = _enclosing_scroll_areas(obj)
        if not areas:
            # 不在任何滚动区里（普通对话框等）：没有列表可滚，保留原生调值行为
            return super().eventFilter(obj, event)

        delta = event.angleDelta().y()
        for area in areas:
            if _can_scroll(area, delta):
                # 转交给滚动区：效果与「光标停在空白处滚动」完全一致
                QApplication.sendEvent(area.viewport(), event)
                return True
        # 在滚动区里、但已经滚到头：吃掉事件，
        # 避免"列表翻不动、参数反而被改了"
        return True
