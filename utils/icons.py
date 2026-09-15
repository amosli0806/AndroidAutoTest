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