# utils/no_context_menu.py
"""全局禁用「下拉框 / 输入框 / 数字框」的原生右键菜单。

为什么需要：
    QLineEdit / QComboBox / QAbstractSpinBox 默认带 Qt 原生右键菜单
    （撤销 / 重做 / 剪切 / 复制 / 粘贴 / 删除 / 全选）。在动作卡这种卡片式界面里
    很突兀，而且和项目里自定义的右键菜单（项目树、套件下拉）风格不统一。

做法：装一个**应用级事件过滤器**，统一吃掉这三类控件的 ContextMenu 事件。
    键盘快捷键（Ctrl+C / Ctrl+V 等）不受影响，只是不弹右键菜单。

两条注意：
  1. 连同它们的**内部子控件**一起管：QSpinBox 的右键落在它内嵌的 QLineEdit 上；
  2. **不动**设了 CustomContextMenu 的控件 —— 那是有意做的自定义右键菜单
     （如执行页的「套件」下拉），拦掉会让它失效。
"""
from PyQt6.QtCore import QObject, QEvent, Qt
from PyQt6.QtWidgets import (QApplication, QAbstractSpinBox, QComboBox, QLineEdit)


class _NoContextMenuFilter(QObject):
    def eventFilter(self, obj, event):
        if event.type() != QEvent.Type.ContextMenu:
            return False
        if isinstance(obj, (QLineEdit, QComboBox, QAbstractSpinBox)):
            # 自定义右键菜单的控件不动它（如套件下拉的编辑菜单）
            if obj.contextMenuPolicy() == Qt.ContextMenuPolicy.CustomContextMenu:
                return False
            return True          # 吃掉 → 不弹原生菜单
        return False


_filter = None


def install(app=None):
    """在 QApplication 上装一次即可（重复调用无副作用）。"""
    global _filter
    app = app or QApplication.instance()
    if app is None or _filter is not None:
        return
    _filter = _NoContextMenuFilter(app)
    app.installEventFilter(_filter)
