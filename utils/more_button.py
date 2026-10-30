# utils/more_button.py
"""工具栏「更多」按钮：统一外观（文案 + 下拉箭头）与弹出行为。

为什么单独抽出来：同一件事在两处实现过，结果不一致 ——
  * 自动化执行页用的是 QToolButton + setMenu，Qt 会把 `::menu-indicator`
    画在按钮**右下角**（和文字挤在一起，看着像错位）；
  * 应用元素库用的是 QPushButton + 手写 popup，则完全没有箭头，
    用户看不出这里能展开。
两处都改用它：QPushButton 保证和旁边的蓝色工具按钮同一套样式，
箭头用文本字符「▾」而不是图标 —— QPushButton 的图标只能放左边，
文本字符则永远和文字同行、垂直居中。
"""
from PyQt6.QtCore import QPoint
from PyQt6.QtWidgets import QMenu, QPushButton

ARROW = "▾"


def make_more_button(parent, tooltip="更多操作"):
    """返回 (按钮, 菜单)；点按钮在按钮左下方弹出菜单。

    用法：
        btn, menu = make_more_button(self, "导入元素 / 导出元素")
        menu.addAction("导入元素…", self._on_import)
    菜单已挂在按钮上（QMenu(btn)），按钮销毁时一并回收。
    """
    btn = QPushButton(f"更多 {ARROW}", parent)
    btn.setToolTip(tooltip)
    menu = QMenu(btn)
    btn.clicked.connect(
        lambda: menu.popup(btn.mapToGlobal(QPoint(0, btn.height()))))
    return btn, menu
