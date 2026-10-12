# utils/icons.py
from PyQt6.QtWidgets import QApplication, QStyle
from PyQt6.QtGui import QIcon, QPainter, QColor, QPen, QBrush, QPixmap
from PyQt6.QtCore import Qt

class IconManager:
    # 标准图标映射（移除了 'add' 的映射）
    STANDARD_MAP = {
        'folder': QStyle.StandardPixmap.SP_DirClosedIcon,
        'folder_open': QStyle.StandardPixmap.SP_DirOpenIcon,
        'doc': QStyle.StandardPixmap.SP_FileIcon,
        'refresh': QStyle.StandardPixmap.SP_BrowserReload,
        'delete': QStyle.StandardPixmap.SP_TrashIcon,
        'close': QStyle.StandardPixmap.SP_DialogCloseButton,
        'check': QStyle.StandardPixmap.SP_DialogApplyButton,
        'menu': QStyle.StandardPixmap.SP_TitleBarMenuButton,
        'play': QStyle.StandardPixmap.SP_MediaPlay,
        'stop': QStyle.StandardPixmap.SP_MediaStop,
        'log': QStyle.StandardPixmap.SP_FileIcon,
        'edit': QStyle.StandardPixmap.SP_FileDialogContentsView,  # 编辑图标
        'list': QStyle.StandardPixmap.SP_FileDialogListView,
        'device': QStyle.StandardPixmap.SP_FileDialogListView,
        'copy': QStyle.StandardPixmap.SP_FileDialogContentsView,
        'rename': QStyle.StandardPixmap.SP_FileDialogContentsView,
        'update': QStyle.StandardPixmap.SP_BrowserReload,
        'trash': QStyle.StandardPixmap.SP_TrashIcon,
        'chevron': QStyle.StandardPixmap.SP_ArrowRight,
        'eye': QStyle.StandardPixmap.SP_FileDialogContentsView,
        'bolt': QStyle.StandardPixmap.SP_MessageBoxWarning,
        'robot': QStyle.StandardPixmap.SP_MessageBoxInformation,
        'titlebar': QStyle.StandardPixmap.SP_TitleBarMenuButton,
    }

    @classmethod
    def get_icon(cls, key: str, color: str = None) -> QIcon:
        """根据键获取图标，优先使用标准图标，否则返回自定义绘制图标。
        color 参数只对自定义绘制图标（add / drag / menu_icon）有效。"""
        style = QApplication.style()
        if key == 'add':
            return cls._create_plus_icon(color or '#ffffff')
        if key in cls.STANDARD_MAP:
            return style.standardIcon(cls.STANDARD_MAP[key])
        if key == 'drag':
            return cls._create_drag_icon(color or '#646464')
        if key == 'menu_icon':
            return cls._create_menu_icon(color or '#505050')
        return cls._create_fallback_icon(key)

    @classmethod
    def _create_plus_icon(cls, color: str = '#ffffff') -> QIcon:
        """绘制加号图标（颜色可定制）"""
        pixmap = QPixmap(24, 24)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setPen(QPen(QColor(color), 2))
        painter.drawLine(4, 12, 20, 12)
        painter.drawLine(12, 4, 12, 20)
        painter.end()
        return QIcon(pixmap)

    @classmethod
    def _create_drag_icon(cls, color: str = '#646464') -> QIcon:
        """绘制拖拽图标（三条横线，颜色可定制）"""
        pixmap = QPixmap(24, 24)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setPen(QPen(QColor(color), 2))
        for i in range(3):
            y = 6 + i * 6
            painter.drawLine(6, y, 18, y)
        painter.end()
        return QIcon(pixmap)

    @classmethod
    def _create_menu_icon(cls) -> QIcon:
        """绘制菜单图标（三个点）"""
        pixmap = QPixmap(24, 24)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setBrush(QBrush(QColor(80, 80, 80)))
        painter.setPen(Qt.PenStyle.NoPen)
        for i in range(3):
            x = 6 + i * 6
            painter.drawEllipse(x, 10, 4, 4)
        painter.end()
        return QIcon(pixmap)

    @classmethod
    def _create_fallback_icon(cls, key: str) -> QIcon:
        pixmap = QPixmap(24, 24)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setBrush(QBrush(QColor(150, 150, 200)))
        painter.setPen(QPen(QColor(80, 80, 120), 1))
        painter.drawRect(4, 4, 16, 16)
        painter.end()
        return QIcon(pixmap)

def menu_icon(name: str, color: str):
    """菜单项图标：**鼠标划过时自动转白**。

    为什么需要它：应用的右键菜单（`QMenu`）大多没设自己的样式表，会走
    `main.py` 里那套全局 Fusion 调色板 —— `Highlight = #1976d2`、
    `HighlightedText = 白`，也就是悬浮项是「蓝底白字」。图标若保持本色
    （蓝、深红、深灰…）会糊在蓝底上，看起来像一小块色块。

    这里把 QIcon 的 Active / Selected 两个模式设成白色：平时按 color 显示，
    悬浮时自动变白（Qt 画菜单项时会按 `QIcon.Selected` 取图）。
    自带样式表、且把悬浮底色设成浅色的菜单（如 ADB 工具箱的指令菜单、
    主菜单）不受影响，也不需要这个函数。

    :param name: qtawesome 图标名，如 'fa6s.pen-to-square'
    :param color: 常态颜色（悬浮时固定为白色）
    """
    import qtawesome as qta
    return qta.icon(name, color=color,
                    color_active='#ffffff', color_selected='#ffffff')
