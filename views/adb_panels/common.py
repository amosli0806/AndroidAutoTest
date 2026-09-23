# views/adb_panels/common.py
"""内嵌面板（弱网 / Monkey）共用的排版小件

两块面板都放在 ADB 工具箱右栏，视觉上要能一眼区分，所以统一：
标题 + 一行灰字说明 + 分隔线 + 两列参数 + 右下角动作按钮。
"""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QCheckBox


def panel_header(title: str, hint: str) -> QHBoxLayout:
    """面板标题行：加粗标题 + 一行灰色说明"""
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(8)

    title_label = QLabel(title)
    title_label.setObjectName("panelTitle")
    row.addWidget(title_label)

    hint_label = QLabel(hint)
    hint_label.setObjectName("panelHint")
    hint_label.setWordWrap(True)
    row.addWidget(hint_label, 1)
    return row


def panel_separator(parent=None) -> QFrame:
    """标题与参数之间的细分隔线"""
    line = QFrame(parent)
    line.setObjectName("panelSep")
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFixedHeight(1)
    return line


def form_label(text: str, parent=None) -> QLabel:
    """参数名标签：右对齐，观感与原来的 QFormLayout 一致"""
    label = QLabel(text, parent)
    label.setObjectName("fieldLabel")
    label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return label


def thin_scrollbar_qss(scope_selector: str, track: str, handle: str) -> str:
    """细圆角滚动条（与指令列表 CommandList 的观感一致）

    scope_selector 形如 "#MonkeyPanel QScrollArea#MonkeyAdvScroll"，
    限定只作用于本面板内的滚动区，避免脏到页面其它控件。
    """
    return f"""
        {scope_selector} QScrollBar:vertical {{
            width: 6px;
            background: {track};
            border-radius: 3px;
            margin: 0px;
        }}
        {scope_selector} QScrollBar::handle:vertical {{
            background: {handle};
            border-radius: 3px;
            min-height: 20px;
        }}
        {scope_selector} QScrollBar:horizontal {{
            height: 6px;
            background: {track};
            border-radius: 3px;
            margin: 0px;
        }}
        {scope_selector} QScrollBar::handle:horizontal {{
            background: {handle};
            border-radius: 3px;
            min-width: 20px;
        }}
        {scope_selector} QScrollBar::add-line:vertical,
        {scope_selector} QScrollBar::sub-line:vertical,
        {scope_selector} QScrollBar::add-line:horizontal,
        {scope_selector} QScrollBar::sub-line:horizontal {{
            height: 0px;
            width: 0px;
            background: transparent;
            border: none;
        }}
        {scope_selector} QScrollBar::add-page:vertical,
        {scope_selector} QScrollBar::sub-page:vertical,
        {scope_selector} QScrollBar::add-page:horizontal,
        {scope_selector} QScrollBar::sub-page:horizontal {{
            background: transparent;
        }}
        {scope_selector} QScrollBar::up-arrow:vertical,
        {scope_selector} QScrollBar::down-arrow:vertical,
        {scope_selector} QScrollBar::left-arrow:horizontal,
        {scope_selector} QScrollBar::right-arrow:horizontal {{
            background: transparent;
            border: none;
            width: 0px;
            height: 0px;
        }}
    """


def panel_chrome_qss(root_selector: str, text: str, sub_text: str, border: str) -> str:
    """标题 / 说明 / 参数名 / 分隔线的统一样式

    root_selector 形如 "#WeakNetworkPanel"，只作用于本面板，避免脏到页面其它控件。
    """
    return f"""
        {root_selector} QLabel#panelTitle {{
            font-size: 13px;
            font-weight: 600;
            color: {text};
            background: transparent;
        }}
        {root_selector} QLabel#panelHint {{
            font-size: 11px;
            color: {sub_text};
            background: transparent;
        }}
        {root_selector} QLabel#fieldLabel {{
            font-size: 13px;
            color: {text};
            background: transparent;
        }}
        {root_selector} QFrame#panelSep {{
            background-color: {border};
            border: none;
            max-height: 1px;
        }}
    """


def disable_context_menus(root_widget):
    """递归禁用 root_widget 里所有输入控件的右键上下文菜单。

    可编辑的 QComboBox 和 QLineEdit 默认带 Undo/Redo/Cut/Copy/Paste/Delete 菜单，
    嵌在偏配置型的内嵌面板里既没用（数值都是用鼠标+键盘改）又容易误触，
    所以整面板统一关掉。

    注意：QComboBox 如果是可编辑的，弹菜单的其实是它内部的 lineEdit，
    光对 combo 本身 setContextMenuPolicy 不生效，必须连 lineEdit 一起关。
    """
    from PyQt6.QtCore import Qt as _Qt
    from PyQt6.QtWidgets import QLineEdit, QComboBox, QAbstractSpinBox

    def _apply(w):
        if isinstance(w, (QLineEdit, QAbstractSpinBox, QComboBox)):
            w.setContextMenuPolicy(_Qt.ContextMenuPolicy.NoContextMenu)
            if isinstance(w, QComboBox):
                le = w.lineEdit()
                if le is not None:
                    le.setContextMenuPolicy(_Qt.ContextMenuPolicy.NoContextMenu)

    _apply(root_widget)
    for child in root_widget.findChildren((QLineEdit, QAbstractSpinBox, QComboBox)):
        _apply(child)

class BorderedCheckBox(QCheckBox):
    """带明显边框的复选框。

    Fusion 默认的 indicator 边框非常淡（深色底上几乎只剩一个勾号），
    放在 Monkey 面板这种密集配置区里用户容易看不见"这里是个勾选框"。
    这里保留原生绘制（保证勾选时有 √），额外叠一圈可见的方框。
    与 execute_view / task_view / perf_view 里的同名控件保持一致。
    """

    def paintEvent(self, event):
        super().paintEvent(event)
        try:
            from PyQt6.QtWidgets import QStyleOptionButton, QStyle
            from PyQt6.QtGui import QPainter, QPen, QColor
            from PyQt6.QtCore import Qt as _Qt

            opt = QStyleOptionButton()
            self.initStyleOption(opt)
            rect = self.style().subElementRect(
                QStyle.SubElement.SE_CheckBoxIndicator, opt, self
            )
            if rect.isValid() and rect.width() > 0:
                painter = QPainter(self)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setPen(QPen(QColor(140, 140, 140), 1))
                painter.setBrush(_Qt.BrushStyle.NoBrush)
                painter.drawRect(rect.adjusted(0, 0, -1, -1))
                painter.end()
        except Exception:
            pass