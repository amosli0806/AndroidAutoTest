# views/visual_view.py
"""应用可视化（整页）：看设备画面，点一下取定位值。

与「元素拾取面板」（views/dialogs/element_pick_dialog.py）的分工：
    拾取面板 —— 从动作卡片/元素库弹出来，一次只为填一个定位，选完就关
    这个页面 —— 常驻，反复看、来回对照，定位值点一下就复制

命中规则、XPath 生成都在 utils/ui_tree.py，和录制反查、拾取面板共用一份，
不在这里另写一套。
"""
import os
import time
from datetime import datetime

from PyQt6.QtCore import QPoint, QRect, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (QApplication, QComboBox, QFrame, QHBoxLayout,
                             QLabel, QLineEdit, QProgressBar, QPushButton,
                             QSizePolicy, QSplitter, QVBoxLayout, QWidget)

from utils.app_paths import get_data_dir
from utils.settings import Settings, THEME_MODE_DARK
from utils.theme import ThemeMode
from utils.toast import show_toast
from utils.ui_tree import describe_node, suggest_element_name

# 画面四周留白：让截图不贴边，便于判断边界
CANVAS_PAD = 12
MIN_SCALE, MAX_SCALE = 0.08, 4.0


class _ScreenCanvas(QWidget):
    """设备画面：等比缩放、拖动平移、点击回调原图坐标、命中高亮。

    为什么不用 QLabel 显示缩放图：整页浏览需要滚轮缩放与拖动平移，
    QLabel 只能整张替换，也没法把「显示坐标」换算回「设备原图坐标」。
    """

    clicked = pyqtSignal(int, int)     # 设备原图坐标
    zoom_changed = pyqtSignal(float)   # 当前缩放比
    chain_step = pyqtSignal(int)       # ↑↓ 切换嵌套节点：-1 / +1
    region_selected = pyqtSignal(object)   # 框选完成：QRect（设备原图坐标）
    crop_cancelled = pyqtSignal()          # 框选被取消（Esc / 切页）

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("VisualCanvas")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        self.setMinimumSize(240, 160)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self._pixmap = None
        self._scale = 1.0
        self._offset = QPoint(0, 0)
        self._fit = True                 # True = 跟随窗口自动适配
        self._hit = None                 # 原图 bounds
        self._hit_label = ""
        self._hint = ""
        self._hint_sub = ""
        self._drag_from = QPoint()
        self._drag_offset = QPoint()
        self._dragging = False
        self._moved = False
        self._is_dark = False
        self._has_wallpaper = False
        self._crop_mode = False          # 图像定位的框选模式
        self._crop_origin = QPoint()
        self._crop_rect = None           # 显示坐标

    # ------------------------------------------------------------------
    def set_pixmap(self, pixmap):
        self._pixmap = pixmap
        self._hit = None
        self._hit_label = ""
        self._fit = True
        self._apply_fit()
        self.update()

    def clear(self, hint="", sub=""):
        self._pixmap = None
        self._hit = None
        self._hit_label = ""
        self._hint, self._hint_sub = hint, sub
        self.update()

    def set_hint(self, hint, sub=""):
        self._hint, self._hint_sub = hint, sub
        self.update()

    def set_hit(self, bounds, label=""):
        self._hit = tuple(bounds) if bounds else None
        self._hit_label = label
        self.update()

    def set_theme(self, is_dark: bool, has_wallpaper: bool = False):
        self._is_dark = bool(is_dark)
        self._has_wallpaper = bool(has_wallpaper)
        self.update()

    # ------------------------------------------------------------------ 框选
    def set_crop_mode(self, on: bool):
        """进入/退出框选模式（图像定位用）。"""
        self._crop_mode = bool(on)
        self._crop_rect = None
        self.setCursor(Qt.CursorShape.CrossCursor if on
                       else Qt.CursorShape.ArrowCursor)
        self.update()

    @property
    def crop_mode(self) -> bool:
        return self._crop_mode

    def _to_image_rect(self, rect: QRect) -> QRect:
        """显示坐标的矩形 → 设备原图坐标（并夹到图片范围内）。"""
        if not self.has_image or rect is None:
            return QRect()
        s = self._scale
        x = int((rect.left() - self._offset.x()) / s)
        y = int((rect.top() - self._offset.y()) / s)
        w = int(rect.width() / s)
        h = int(rect.height() / s)
        pw, ph = self._pixmap.width(), self._pixmap.height()
        x = max(0, min(pw - 1, x))
        y = max(0, min(ph - 1, y))
        w = max(1, min(pw - x, w))
        h = max(1, min(ph - y, h))
        return QRect(x, y, w, h)

    @property
    def scale(self) -> float:
        return self._scale

    @property
    def has_image(self) -> bool:
        return self._pixmap is not None and not self._pixmap.isNull()

    def fit_to_window(self):
        self._fit = True
        self._apply_fit()
        self.update()
        self.zoom_changed.emit(self._scale)

    def zoom_by(self, factor: float):
        """按比例缩放，以画面中心为锚点（滚轮缩放则以鼠标位置为锚点）。"""
        if not self.has_image:
            return
        self._zoom_to(self._scale * factor)
        self._fit = False

    # ------------------------------------------------------------------
    def _apply_fit(self):
        if not self.has_image:
            return
        pw, ph = self._pixmap.width(), self._pixmap.height()
        if pw <= 0 or ph <= 0:
            return
        avail_w = max(1, self.width() - CANVAS_PAD * 2)
        avail_h = max(1, self.height() - CANVAS_PAD * 2)
        # 适配时不放大超过原尺寸：小屏截图放大只会变糊
        self._scale = max(MIN_SCALE, min(avail_w / pw, avail_h / ph, 1.0))
        self._offset = QPoint(
            int((self.width() - pw * self._scale) / 2),
            int((self.height() - ph * self._scale) / 2),
        )

    def _zoom_to(self, scale, anchor=None):
        if not self.has_image:
            return
        new_scale = max(MIN_SCALE, min(MAX_SCALE, float(scale)))
        if abs(new_scale - self._scale) < 1e-6:
            return
        if anchor is None:
            anchor = self.rect().center()
        # 锚点下的原图坐标在缩放前后保持不动
        img_x = (anchor.x() - self._offset.x()) / self._scale
        img_y = (anchor.y() - self._offset.y()) / self._scale
        self._scale = new_scale
        self._offset = QPoint(int(anchor.x() - img_x * new_scale),
                              int(anchor.y() - img_y * new_scale))
        self.update()
        self.zoom_changed.emit(self._scale)

    def _image_rect(self) -> QRect:
        if not self.has_image:
            return QRect()
        return QRect(self._offset,
                     QSize(int(self._pixmap.width() * self._scale),
                           int(self._pixmap.height() * self._scale)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._fit:
            self._apply_fit()

    # ------------------------------------------------------------------
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        if self._has_wallpaper:
            # 壁纸模式：半透明底，既压住花哨背景又不完全挡掉
            bg = (QColor(22, 23, 26, 150) if self._is_dark
                  else QColor(244, 246, 248, 150))
        else:
            bg = QColor("#1e1f22") if self._is_dark else QColor("#f2f3f5")
        painter.fillRect(self.rect(), bg)

        if not self.has_image:
            self._paint_hint(painter)
            painter.end()
            return

        target = self._image_rect()
        painter.drawPixmap(target, self._pixmap)
        border = QColor("#4a4a4a") if self._is_dark else QColor("#d0d3d8")
        painter.setPen(QPen(border, 1))
        painter.drawRect(target.adjusted(-1, -1, 0, 0))
        if self._crop_rect is not None:
            self._paint_crop(painter)
        else:
            self._paint_hit(painter)
        painter.end()

    def _paint_crop(self, painter):
        """框选中：框外压暗 + 白色虚线框 + 原图像素尺寸。"""
        rect = self._crop_rect
        if rect is None or rect.isNull():
            return
        mask = QColor(0, 0, 0, 110)
        painter.fillRect(QRect(0, 0, self.width(), rect.top()), mask)
        painter.fillRect(QRect(0, rect.bottom() + 1, self.width(),
                               self.height() - rect.bottom() - 1), mask)
        painter.fillRect(QRect(0, rect.top(), rect.left(), rect.height()), mask)
        painter.fillRect(QRect(rect.right() + 1, rect.top(),
                               self.width() - rect.right() - 1,
                               rect.height()), mask)
        painter.setPen(QPen(QColor("#ffffff"), 1, Qt.PenStyle.DashLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)

        img_rect = self._to_image_rect(rect)
        text = f"{img_rect.width()} × {img_rect.height()}"
        font = QFont(self.font())
        font.setPointSize(9)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        tw = metrics.horizontalAdvance(text) + 12
        th = metrics.height() + 4
        ty = rect.bottom() + 6
        if ty + th > self.height() - 2:
            ty = rect.top() - th - 6
        painter.fillRect(QRect(rect.left(), ty, tw, th), QColor(0, 0, 0, 170))
        painter.setPen(QColor("#ffffff"))
        painter.drawText(QRect(rect.left(), ty, tw, th),
                         Qt.AlignmentFlag.AlignCenter, text)

    def _paint_hint(self, painter):
        fg = QColor("#8a8a8a") if self._is_dark else QColor("#999999")
        border = QColor("#3a3d42") if self._is_dark else QColor("#d8dce2")
        # 空态给一个虚线占位框，让"这里将来是设备画面"一目了然
        painter.setPen(QPen(border, 1, Qt.PenStyle.DashLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(self.rect().adjusted(24, 24, -24, -24), 8, 8)
        painter.setPen(fg)
        font = QFont(self.font())
        font.setPointSize(11)
        painter.setFont(font)
        main_rect = self.rect().adjusted(24, -14 if self._hint_sub else 0,
                                         -24, -14 if self._hint_sub else 0)
        painter.drawText(main_rect, Qt.AlignmentFlag.AlignCenter,
                         self._hint or "")
        if self._hint_sub:
            font.setPointSize(10)
            painter.setFont(font)
            painter.setPen(QColor("#7a7a7a") if self._is_dark else QColor("#aaaaaa"))
            sub_rect = self.rect().adjusted(24, 26, -24, 26)
            painter.drawText(sub_rect, Qt.AlignmentFlag.AlignCenter, self._hint_sub)

    def _paint_hit(self, painter):
        b = self._hit
        if not b:
            return
        s = self._scale
        rect = QRect(int(b[0] * s) + self._offset.x(),
                     int(b[1] * s) + self._offset.y(),
                     max(2, int((b[2] - b[0]) * s)),
                     max(2, int((b[3] - b[1]) * s)))
        painter.setPen(QPen(QColor("#1976d2"), 2))
        painter.setBrush(QColor(25, 118, 210, 38))
        painter.drawRect(rect)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        if not self._hit_label:
            return
        font = QFont(self.font())
        font.setPointSize(9)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        pad = 5
        w = min(metrics.horizontalAdvance(self._hit_label) + pad * 2,
                max(80, self.width() - 8))
        h = metrics.height() + 4
        # 框上方放不下就放到框内顶部，避免标签跑到画面外
        label_y = rect.top() - h - 2
        if label_y < 2:
            label_y = rect.top() + 2
        label_x = max(4, min(rect.left(), self.width() - w - 4))
        painter.fillRect(QRect(label_x, label_y, w, h), QColor("#1976d2"))
        painter.setPen(QColor("#ffffff"))
        painter.drawText(QRect(label_x + pad, label_y, w - pad * 2, h),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         metrics.elidedText(self._hit_label,
                                            Qt.TextElideMode.ElideRight,
                                            max(20, w - pad * 2)))

    # ------------------------------------------------------------------
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.has_image:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            pos = event.position().toPoint()
            if self._crop_mode:
                self._crop_origin = pos
                self._crop_rect = QRect(pos, QSize(1, 1))
                self.update()
                return
            self._drag_from = pos
            self._drag_offset = QPoint(self._offset)
            self._dragging = True
            self._moved = False
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._crop_mode and self._crop_rect is not None:
            self._crop_rect = QRect(self._crop_origin,
                                    event.position().toPoint()).normalized()
            self.update()
            return
        if self._dragging:
            pos = event.position().toPoint()
            delta = pos - self._drag_from
            if not self._moved and (abs(delta.x()) > 4 or abs(delta.y()) > 4):
                self._moved = True
            if self._moved:
                self._offset = self._drag_offset + delta
                self._fit = False
                self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._crop_mode:
            rect = self._crop_rect
            self._crop_rect = None
            if rect is not None and rect.width() >= 5 and rect.height() >= 5:
                self.set_crop_mode(False)
                self.region_selected.emit(self._to_image_rect(rect))
            else:
                # 太小：留在框选模式让用户重新拖（提示由宿主窗口给）
                self.update()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self.setCursor(Qt.CursorShape.CrossCursor)
            if not self._moved and self.has_image:
                pos = event.position().toPoint()
                img_x = int((pos.x() - self._offset.x()) / self._scale)
                img_y = int((pos.y() - self._offset.y()) / self._scale)
                if (0 <= img_x < self._pixmap.width()
                        and 0 <= img_y < self._pixmap.height()):
                    self.clicked.emit(img_x, img_y)
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.fit_to_window()
            return
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event):
        if not self.has_image:
            return
        step = event.angleDelta().y()
        if step == 0:
            return
        self._zoom_to(self._scale * (1.1 if step > 0 else 1 / 1.1),
                      event.position().toPoint())
        self._fit = False

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_Escape and self._crop_mode:
            self.set_crop_mode(False)
            self.crop_cancelled.emit()
            return
        if key == Qt.Key.Key_Up:
            self.chain_step.emit(-1)
            return
        if key == Qt.Key.Key_Down:
            self.chain_step.emit(1)
            return
        super().keyPressEvent(event)


class _LocatorRow(QFrame):
    """侧栏里的一行定位方式：名称 + 值 + 右侧状态，整行可点即复制。"""

    activated = pyqtSignal(str, str)      # (定位方式, 定位值)

    def __init__(self, type_name: str, parent=None):
        super().__init__(parent)
        self.setObjectName("VisualLocRow")
        self.type_name = type_name
        self.value = ""
        self.available = False
        self.clickable = False
        self.setProperty("available", "0")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 5, 8, 5)
        layout.setSpacing(8)

        self.name_label = QLabel(type_name)
        self.name_label.setObjectName("VisualLocName")
        self.name_label.setFixedWidth(46)

        self.value_label = QLabel("—")
        self.value_label.setObjectName("VisualLocValue")
        # 允许换行：资源 ID / XPath 一行放不下，换行比裁掉更可读
        policy = QSizePolicy(QSizePolicy.Policy.Preferred,
                             QSizePolicy.Policy.Minimum)
        policy.setHeightForWidth(True)
        self.value_label.setSizePolicy(policy)
        self.value_label.setWordWrap(True)

        self.tail_label = QLabel("")
        self.tail_label.setObjectName("VisualLocTail")
        self.tail_label.setAlignment(Qt.AlignmentFlag.AlignRight
                                     | Qt.AlignmentFlag.AlignVCenter)
        self.tail_label.setFixedWidth(64)

        layout.addWidget(self.name_label)
        layout.addWidget(self.value_label, 1)
        layout.addWidget(self.tail_label)

    def set_row(self, value: str, ok: bool, note: str = "",
                recommended: bool = False, clickable: bool = None,
                display: str = None):
        self.value = value or ""          # 真正拿去用/复制的值（可能是很长的路径）
        self.available = bool(ok)
        # 图像行比较特殊：没有值也能点（点了进入框选），所以「可点」与「有值」分开
        self.clickable = bool(ok if clickable is None else clickable)
        self.setProperty("available", "1" if self.available else "0")
        self.setProperty("clickable", "1" if self.clickable else "0")
        self.setProperty("recommended", "1" if recommended else "0")
        if display is not None:
            # 显示用短名（图像模板只显示文件名），但 self.value 仍是完整路径
            shown = display
        elif ok and len(self.value) > 48:
            # 两行也放不下时中间省略，首尾都留着便于辨认（点一下复制的是全量）
            shown = f"{self.value[:20]}…{self.value[-24:]}"
        elif ok:
            shown = self.value
        elif self.clickable:
            # 可点但还没有值（图像行的「框选」）：值位留空，动作写在右侧 tail 上
            shown = ""
        else:
            shown = note or "无"
        self.value_label.setText(shown)
        self.value_label.setToolTip(self.value if ok else "")
        if ok and note:
            tail = note
        elif self.clickable and note:
            tail = note
        elif ok:
            tail = "复制"
        else:
            tail = ""
        self.tail_label.setText(tail)
        self.setCursor(Qt.CursorShape.PointingHandCursor if self.clickable
                       else Qt.CursorShape.ArrowCursor)
        for w in (self, self.name_label, self.value_label, self.tail_label):
            w.style().unpolish(w)
            w.style().polish(w)
        self.update()

    def set_selected(self, selected: bool):
        self.setProperty("selected", "1" if selected else "0")
        for w in (self, self.name_label, self.value_label, self.tail_label):
            w.style().unpolish(w)
            w.style().polish(w)
        self.update()

    def flash_copied(self):
        self.tail_label.setText("已复制")
        QTimer.singleShot(1200, lambda: self.tail_label.setText(
            "复制" if self.available else ("框选" if self.clickable else "")))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.clickable:
            self.activated.emit(self.type_name, self.value)
            if self.available and self.value:
                self.flash_copied()
            return
        super().mousePressEvent(event)


class VisualView(QWidget):
    """应用可视化整页。数据全在 VisualController，这里只负责展示与交互。"""

    element_saved = pyqtSignal()      # 存入元素库成功，元素库页据此刷新

    def __init__(self, controller=None, parent=None):
        super().__init__(parent)
        self.setObjectName("VisualView")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.controller = controller
        self._pixmap = None
        self._info = None
        self._chain = []
        self._chain_index = 0
        self._load_started = 0.0
        self._loaded_once = False
        self._last_device_key = None
        self._rows = {}
        self._image_path = ""       # 已框选的图像模板路径（图像定位用）
        self._active_type = ""       # 当前选中的定位方式（用于「填入卡片」）
        self.element_model = None   # 由 main.py 注入，「存入元素库」用
        self._return_label = ""
        self._return_callback = None
        self._is_dark = False

        self._setup_ui()
        self._connect_controller()
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(500)
        self._tick_timer.timeout.connect(self._on_tick)
        self.apply_theme()

    # ------------------------------------------------------------------
    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        # ---------- 顶部工具条 ----------
        bar = QHBoxLayout()
        bar.setSpacing(6)

        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.setObjectName("VisualRefreshBtn")
        self.refresh_btn.clicked.connect(self.refresh)
        bar.addWidget(self.refresh_btn)

        self.zoom_out_btn = QPushButton("−")
        self.zoom_out_btn.setObjectName("VisualIconBtn")
        self.zoom_out_btn.setFixedWidth(30)
        self.zoom_out_btn.clicked.connect(lambda: self.canvas.zoom_by(1 / 1.15))
        bar.addWidget(self.zoom_out_btn)

        self.fit_btn = QPushButton("适配")
        self.fit_btn.setObjectName("VisualFitBtn")
        self.fit_btn.clicked.connect(lambda: self.canvas.fit_to_window())
        bar.addWidget(self.fit_btn)

        self.zoom_in_btn = QPushButton("+")
        self.zoom_in_btn.setObjectName("VisualIconBtn")
        self.zoom_in_btn.setFixedWidth(30)
        self.zoom_in_btn.clicked.connect(lambda: self.canvas.zoom_by(1.15))
        bar.addWidget(self.zoom_in_btn)

        self.zoom_label = QLabel("100%")
        self.zoom_label.setObjectName("VisualZoomLabel")
        self.zoom_label.setFixedWidth(46)
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bar.addWidget(self.zoom_label)

        self.progress = QProgressBar()
        self.progress.setObjectName("VisualProgress")
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedSize(110, 4)
        self.progress.setVisible(False)
        bar.addWidget(self.progress)

        bar.addStretch(1)

        self.device_label = QLabel("未连接设备")
        self.device_label.setObjectName("VisualDeviceLabel")
        bar.addWidget(self.device_label)

        self.time_label = QLabel("尚未刷新")
        self.time_label.setObjectName("VisualTimeLabel")
        bar.addWidget(self.time_label)
        root.addLayout(bar)

        # ---------- 错误提示条 ----------
        self.error_bar = QFrame()
        self.error_bar.setObjectName("VisualErrorBar")
        err_layout = QHBoxLayout(self.error_bar)
        err_layout.setContentsMargins(10, 6, 10, 6)
        self.error_label = QLabel("")
        self.error_label.setObjectName("VisualErrorLabel")
        self.error_label.setWordWrap(True)
        err_layout.addWidget(self.error_label, 1)
        self.error_bar.setVisible(False)
        root.addWidget(self.error_bar)

        # ---------- 主体：画面 + 侧栏 ----------
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(4)

        self.canvas = _ScreenCanvas()
        self.canvas.clicked.connect(self._on_canvas_clicked)
        self.canvas.zoom_changed.connect(self._on_zoom_changed)
        self.canvas.chain_step.connect(self._step_chain)
        self.canvas.region_selected.connect(self._on_region_selected)
        self.canvas.crop_cancelled.connect(self._on_crop_cancelled)
        self.splitter.addWidget(self.canvas)

        side = QFrame()
        side.setObjectName("VisualSide")
        side.setMinimumWidth(220)
        side.setMaximumWidth(340)
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(12, 10, 12, 10)
        side_layout.setSpacing(6)

        caption = QLabel("当前命中")
        caption.setObjectName("VisualCaption")
        side_layout.addWidget(caption)

        self.node_class = QLabel("—")
        self.node_class.setObjectName("VisualNodeValue")
        self.node_text = QLabel("—")
        self.node_text.setObjectName("VisualNodeValue")
        self.node_text.setWordWrap(True)
        self.node_bounds = QLabel("—")
        self.node_bounds.setObjectName("VisualNodeValue")
        for label, widget in (("类型", self.node_class),
                              ("文本", self.node_text),
                              ("区域", self.node_bounds)):
            row = QHBoxLayout()
            row.setSpacing(8)
            key = QLabel(label)
            key.setObjectName("VisualNodeKey")
            key.setFixedWidth(34)
            row.addWidget(key)
            row.addWidget(widget, 1)
            side_layout.addLayout(row)

        divider = QFrame()
        divider.setObjectName("VisualDivider")
        divider.setFixedHeight(1)
        side_layout.addSpacing(4)
        side_layout.addWidget(divider)
        side_layout.addSpacing(2)

        loc_caption = QLabel("定位方式")
        loc_caption.setObjectName("VisualCaption")
        side_layout.addWidget(loc_caption)

        for type_name in ("资源ID", "文本", "描述", "XPath", "坐标", "图像"):
            row = _LocatorRow(type_name)
            row.activated.connect(self._on_row_action)
            self._rows[type_name] = row
            side_layout.addWidget(row)

        side_layout.addStretch(1)
        self.splitter.addWidget(side)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        self.splitter.setSizes([780, 320])
        self._side_panel = side
        root.addWidget(self.splitter, 1)

        # ---------- 底部状态 ----------
        status = QHBoxLayout()
        status.setSpacing(8)
        self.hint_label = QLabel("")
        self.hint_label.setObjectName("VisualHint")
        self.count_label = QLabel("")
        self.count_label.setObjectName("VisualCount")
        status.addWidget(self.hint_label, 1)
        status.addWidget(self.count_label)

        # 「填入动作卡片」：只有从卡片跳过来时才出现（见 set_return_target）
        self.apply_btn = QPushButton("填入")
        self.apply_btn.setObjectName("VisualApplyBtn")
        self.apply_btn.setVisible(False)
        self.apply_btn.clicked.connect(self._on_apply_to_card)
        status.addWidget(self.apply_btn)

        self.save_elem_btn = QPushButton("存入元素库")
        self.save_elem_btn.setObjectName("VisualSaveElemBtn")
        self.save_elem_btn.clicked.connect(self._toggle_save_bar)
        status.addWidget(self.save_elem_btn)
        root.addLayout(status)

        # ---------- 存入元素库的内联表单（默认收起） ----------
        self.save_bar = QFrame()
        self.save_bar.setObjectName("VisualSaveBar")
        save_layout = QHBoxLayout(self.save_bar)
        save_layout.setContentsMargins(10, 6, 10, 6)
        save_layout.setSpacing(6)

        self.save_app = QLineEdit()
        self.save_app.setPlaceholderText("所属应用")
        self.save_module = QLineEdit()
        self.save_module.setPlaceholderText("所属模块")
        self.save_name = QLineEdit()
        self.save_name.setPlaceholderText("元素名称")
        for w in (self.save_app, self.save_module, self.save_name):
            w.setMinimumWidth(0)
            save_layout.addWidget(w, 1)

        self.save_loc = QComboBox()
        self.save_loc.addItems(["资源ID", "文本", "描述", "XPath", "坐标", "图像"])
        self.save_loc.setFixedWidth(96)
        save_layout.addWidget(self.save_loc)

        self.save_confirm = QPushButton("保存")
        self.save_confirm.setObjectName("VisualSaveConfirmBtn")
        self.save_confirm.clicked.connect(self._on_save_element)
        save_layout.addWidget(self.save_confirm)

        self.save_cancel = QPushButton("取消")
        self.save_cancel.clicked.connect(lambda: self.save_bar.setVisible(False))
        save_layout.addWidget(self.save_cancel)

        self.save_bar.setVisible(False)
        root.addWidget(self.save_bar)

        self.canvas.clear("点击「刷新」读取当前设备画面",
                          "首次进入不会自动读取，避免车机长时间等待")
        self._set_zoom_enabled(False)

    def _set_zoom_enabled(self, enabled: bool):
        """没有画面时缩放/适配没有意义，置灰避免点了没反应。"""
        for btn in (self.zoom_out_btn, self.zoom_in_btn, self.fit_btn):
            btn.setEnabled(bool(enabled))

    def _connect_controller(self):
        if self.controller is None:
            return
        try:
            self.controller.load_finished.connect(self._on_load_finished)
            self.controller.load_failed.connect(self._on_load_failed)
        except Exception:
            pass

    # ------------------------------------------------------------------
    def set_controller(self, controller):
        self.controller = controller
        self._connect_controller()

    def set_device_service(self, device_service):
        if self.controller is not None:
            self.controller.set_device_service(device_service)
        self.on_device_changed()

    def on_device_changed(self):
        """设备切换/断开：清掉旧画面与旧定位，绝不把旧设备的数据留在界面上。"""
        self._pixmap = None
        self._info = None
        self._chain = []
        self._loaded_once = False
        self._image_path = ""          # 旧设备的图像模板不能留给新设备
        self.canvas.set_crop_mode(False)
        self._clear_node_panel()
        ready = bool(self.controller is not None and self.controller.device_ready())
        name = self._current_device_name() if ready else ""
        self.device_label.setText(name or ("已连接设备" if ready else "未连接设备"))
        self.time_label.setText("尚未刷新")
        self._hide_error()
        self.canvas.clear("点击「刷新」读取当前设备画面",
                          "首次进入不会自动读取，避免车机长时间等待")
        self.hint_label.setText("")
        self.count_label.setText("")
        self.zoom_label.setText("100%")
        self._set_zoom_enabled(False)
        self.refresh_btn.setEnabled(True)
        self.refresh_btn.setText("刷新")
        self.progress.setVisible(False)
        self._tick_timer.stop()

    def _current_device_name(self) -> str:
        """优先取设备服务给的名字，没有就用序列号 —— 不显示含糊的「已连接设备」。"""
        try:
            service = self.controller.device_service
            name = getattr(service, "device_name", None)
            text = str(name() if callable(name) else (name or "")).strip()
        except Exception:
            text = ""
        return text or self._device_key()

    def _device_key(self) -> str:
        """当前设备标识（序列号）。用来发现「换了设备但页面还开着」的情况。"""
        try:
            dev = getattr(self.controller.device_service, "device", None)
            if dev is None:
                return ""
            return str(getattr(dev, "serial", "") or "")
        except Exception:
            return ""

    # ------------------------------------------------------------------
    def refresh(self):
        """手动刷新：后台读画面 + 层级，界面不阻塞。"""
        if self.controller is None:
            return
        # 换了设备就先清空：不能拿旧设备的画面配新设备的层级
        key = self._device_key()
        if key != self._last_device_key:
            self._last_device_key = key
            self.on_device_changed()
        if not self.controller.device_ready():
            # 没设备不值得占一条横幅（横幅留给"读取失败"这类需要停留在页面上的错误）
            show_toast(parent=self, message="未检测到设备")
            return
        if self.controller.loading:
            return
        self._hide_error()
        self.refresh_btn.setEnabled(False)
        self.refresh_btn.setText("刷新中…")
        self.progress.setVisible(True)
        self._load_started = time.time()
        self.hint_label.setText("正在读取设备画面…")
        self.count_label.setText("")
        self.canvas.set_hint("正在读取设备画面…", "车机首次 dump 可能需要 8~12 秒")
        self._tick_timer.start()
        if not self.controller.start_load():
            self._finish_loading_ui()

    def _on_tick(self):
        if self.controller is None or not self.controller.loading:
            self._tick_timer.stop()
            return
        used = max(0.0, time.time() - self._load_started)
        self.canvas.set_hint("正在读取设备画面…", f"已用 {used:.1f} 秒")
        self.hint_label.setText(f"正在读取设备画面…已用 {used:.1f} 秒")

    def _finish_loading_ui(self):
        self._tick_timer.stop()
        self.progress.setVisible(False)
        self.refresh_btn.setEnabled(True)
        self.refresh_btn.setText("刷新")

    def _on_load_finished(self, image, elapsed):
        self._finish_loading_ui()
        try:
            rgba = image.convert("RGBA")
            # QImage 不复制传入的内存：把 bytes 存在局部变量里，活到
            # QPixmap.fromImage（深拷贝）结束，避免野指针
            raw = rgba.tobytes("raw", "RGBA")
            qimg = QImage(raw, rgba.width, rgba.height,
                          QImage.Format.Format_RGBA8888)
            self._pixmap = QPixmap.fromImage(qimg)
        except Exception as e:
            self._show_error(f"画面转换失败：{e}")
            return

        self._loaded_once = True
        self._info = None
        self._chain = []
        self._clear_node_panel()
        self.canvas.set_pixmap(self._pixmap)
        self.canvas.fit_to_window()
        self._set_zoom_enabled(True)
        self.time_label.setText("上次刷新 " + datetime.now().strftime("%H:%M:%S"))

        if self.controller is not None and not self.controller.has_hierarchy():
            self._show_error("画面已读取，但界面层级不可用（界面可能一直在动，"
                             "或应用是自绘 UI）—— 可以再点「刷新」重试")
            self.hint_label.setText("层级不可用，只能查看画面")
            self.count_label.setText(f"{elapsed:.1f} 秒")
            return

        self.hint_label.setText("在画面上点击要定位的控件；↑↓ 切换嵌套层级")
        nodes = self.controller.node_count() if self.controller else 0
        self.count_label.setText(f"层级 {nodes} 个节点 · 读取用 {elapsed:.1f} 秒")

    def _on_load_failed(self, message):
        self._finish_loading_ui()
        if "未检测到设备" in (message or ""):
            # 与手动刷新一致：设备类提示走 toast，不占横幅
            show_toast(parent=self, message="未检测到设备")
        else:
            self._show_error(message)
        self.hint_label.setText("读取失败，可重试")
        if not self.canvas.has_image:
            self.canvas.clear("读取失败", "点「刷新」重试")

    # ------------------------------------------------------------------
    def _on_canvas_clicked(self, x, y):
        if self.controller is None or not self.controller.has_hierarchy():
            return
        chain = self.controller.chain(x, y)
        if not chain:
            self._chain = []
            self._info = None
            self.canvas.set_hit(None)
            self._clear_node_panel()
            self.hint_label.setText(f"({x}, {y}) 没有命中任何控件 —— "
                                    f"点图标/按钮中间再试")
            return
        self._chain = chain
        self._chain_index = 0
        self._show_chain_item()

    def _step_chain(self, delta):
        if len(self._chain) < 2:
            return
        self._chain_index = max(0, min(len(self._chain) - 1,
                                       self._chain_index + int(delta)))
        self._show_chain_item()

    def _show_chain_item(self):
        if not self._chain:
            return
        info = self._chain[self._chain_index]
        self._info = info

        cls = (info.get("className") or "").rsplit(".", 1)[-1]
        label = cls or "节点"
        text = (info.get("text") or "").strip()
        if text:
            label = f"{label} · {text[:16]}"
        self.canvas.set_hit(info.get("bounds"), label)

        self.node_class.setText((info.get("className") or "—"))
        self.node_text.setText(text or "—")
        b = info.get("bounds")
        self.node_bounds.setText(
            f"[{b[0]},{b[1]}][{b[2]},{b[3]}]" if b else "—")

        rows = self.controller.locator_rows(info) if self.controller else []
        self._active_type = ""
        for item in rows:
            row = self._rows.get(item["type"])
            if row is None:
                continue
            if item["type"] == "图像":
                # 图像是屏幕区域，与当前节点无关：保持已框选的结果不动
                self._sync_image_row()
                continue
            row.set_row(item["value"], item["ok"], item.get("note", ""),
                        item.get("recommended", False))
            row.set_selected(False)
        self._sync_apply_button()

        total = len(self._chain)
        if total > 1:
            self.hint_label.setText(
                f"命中 {total} 个嵌套节点（第 {self._chain_index + 1} 个）"
                f" · ↑↓ 切换 · 点右侧定位值复制")
        else:
            self.hint_label.setText(describe_node(info) + " · 点右侧定位值复制")

    def _clear_node_panel(self):
        self.node_class.setText("—")
        self.node_text.setText("—")
        self.node_bounds.setText("—")
        self._active_type = ""
        for name, row in self._rows.items():
            if name == "图像":
                continue
            row.set_row("", False, "")
            row.set_selected(False)
        self._sync_image_row()
        self._sync_apply_button()

    def _on_row_action(self, type_name, value):
        """点右侧定位行：图像 → 进入框选；其余 → 复制到剪贴板并设为当前项。"""
        if type_name == "图像":
            self._start_crop()
            return
        if not value:
            return
        try:
            QApplication.clipboard().setText(value)
        except Exception:
            pass
        self._set_active_row(type_name)
        self.hint_label.setText(f"已复制{type_name}：{value}")

    def _set_active_row(self, type_name):
        if self._active_type == type_name:
            self._sync_apply_button()
            return
        self._active_type = type_name
        for name, row in self._rows.items():
            row.set_selected(name == type_name)
        self._sync_apply_button()

    def _sync_apply_button(self):
        if not self._return_callback:
            return
        row = self._rows.get(self._active_type)
        self.apply_btn.setEnabled(bool(row is not None and row.value))

    # ---------------------------- 图像框选 ----------------------------
    def _start_crop(self):
        if not self.canvas.has_image:
            show_toast(parent=self, message="先点「刷新」读取画面")
            return
        self.canvas.set_crop_mode(True)
        self.hint_label.setText("在画面上拖出要识别的区域（特征越多越稳），Esc 取消")

    def _on_region_selected(self, rect):
        if self._pixmap is None or rect is None or rect.isNull():
            return
        crop = self._pixmap.copy(rect)
        if crop.isNull() or crop.width() < 5 or crop.height() < 5:
            show_toast(parent=self, message="框选区域太小")
            return
        try:
            out_dir = os.path.join(get_data_dir(), "element_images")
            os.makedirs(out_dir, exist_ok=True)
            name = f"elem_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]}.png"
            path = os.path.join(out_dir, name)
            if not crop.save(path, "PNG"):
                raise RuntimeError("保存失败")
        except Exception as e:
            show_toast(parent=self, message="图像保存失败")
            self.hint_label.setText(f"图像保存失败：{e}")
            return
        self._image_path = path
        self._sync_image_row()
        self._set_active_row("图像")
        self.hint_label.setText(
            f"已截取 {crop.width()}×{crop.height()} 的图像模板，可选「图像」定位")

    def _on_crop_cancelled(self):
        self.hint_label.setText("已取消框选")

    def _sync_image_row(self):
        row = self._rows.get("图像")
        if row is None:
            return
        if self._image_path:
            # value 用完整路径（复制/入库/回填都要能直接执行），显示只放文件名
            row.set_row(self._image_path, True, "重选", clickable=True,
                        display=os.path.basename(self._image_path))
        else:
            row.set_row("", False, "框选", clickable=True)
        row.set_selected(self._active_type == "图像")

    # ---------------------------- 存入元素库 ----------------------------
    def set_element_model(self, element_model):
        self.element_model = element_model

    def _toggle_save_bar(self):
        if self.save_bar.isVisible():
            self.save_bar.setVisible(False)
            return
        if not self._info and not self._image_path:
            show_toast(parent=self, message="先在画面上点一个控件")
            return
        self._fill_save_defaults()
        self.save_bar.setVisible(True)
        self.save_name.setFocus()

    def _fill_save_defaults(self):
        info = self._info or {}
        if not self.save_app.text().strip():
            self.save_app.setText(info.get("package") or "")
        if not self.save_module.text().strip():
            self.save_module.setText("默认")
        if not self.save_name.text().strip():
            self.save_name.setText(suggest_element_name(info) if info else "元素")
        loc_type, _value = self._current_locator()
        idx = self.save_loc.findText(loc_type)
        if idx >= 0:
            self.save_loc.setCurrentIndex(idx)

    def _current_locator(self):
        """当前要用的定位：优先用户点过的那一行，其次按推荐顺序找第一个有值的。"""
        if self._active_type:
            row = self._rows.get(self._active_type)
            if row is not None and row.value:
                return self._active_type, row.value
        for name in ("资源ID", "文本", "描述", "XPath", "坐标"):
            row = self._rows.get(name)
            if row is not None and row.value:
                return name, row.value
        return "坐标", ""

    def _on_save_element(self):
        if self.element_model is None:
            show_toast(parent=self, message="元素库未就绪")
            return
        loc_type = self.save_loc.currentText()
        row = self._rows.get(loc_type)
        value = row.value if row is not None else ""
        if not value:
            show_toast(parent=self, message="该定位方式还没有值")
            return
        info = self._info or {}
        app = self.save_app.text().strip() or (info.get("package") or "未分类")
        module = self.save_module.text().strip() or "默认"
        name = self.save_name.text().strip() or (
            suggest_element_name(info) if info else "元素")
        try:
            self.element_model.upsert_elements(
                [(app, module, name, loc_type, value, "")])
        except Exception:
            show_toast(parent=self, message="存入元素库失败")
            return
        self.save_bar.setVisible(False)
        self.save_name.clear()
        show_toast(parent=self, message="已存入元素库")
        self.element_saved.emit()

    # ---------------------------- 填入动作卡片 ----------------------------
    def set_return_target(self, label, callback):
        """从动作卡片跳过来时设置回填目标（label 用于按钮文案）。"""
        self._return_label = label or ""
        self._return_callback = callback
        self.apply_btn.setText(f"填入「{label}」" if label else "填入")
        self.apply_btn.setVisible(bool(callback))
        self._sync_apply_button()

    def clear_return_target(self):
        self._return_label = ""
        self._return_callback = None
        self.apply_btn.setVisible(False)

    def _on_apply_to_card(self):
        if not self._return_callback:
            return
        loc_type, value = self._current_locator()
        if not value:
            show_toast(parent=self, message="先在画面上点一个控件")
            return
        try:
            self._return_callback(loc_type, value)
        except Exception:
            show_toast(parent=self, message="填入失败")
            return
        show_toast(parent=self, message=f"已填入{loc_type}")

    def _on_zoom_changed(self, scale):
        self.zoom_label.setText(f"{int(round(scale * 100))}%")

    # ------------------------------------------------------------------
    def _show_error(self, text):
        self.error_label.setText(text)
        self.error_bar.setVisible(True)

    def _hide_error(self):
        self.error_bar.setVisible(False)

    def showEvent(self, event):
        super().showEvent(event)
        # 切到本页只同步设备状态，**不自动刷新**（手动刷新是明确要求）
        key = self._device_key()
        if key != self._last_device_key:
            self._last_device_key = key
            self.on_device_changed()
            return
        if self.controller is not None:
            ready = self.controller.device_ready()
            name = self._current_device_name() if ready else ""
            self.device_label.setText(
                name or ("已连接设备" if ready else "未连接设备"))
        if not self._loaded_once and not self.canvas.has_image:
            self.canvas.clear("点击「刷新」读取当前设备画面",
                              "首次进入不会自动读取，避免车机长时间等待")

    def apply_theme(self, theme_mode: ThemeMode = None, has_wallpaper: bool = None):
        """theme_mode / has_wallpaper 由主窗口统一传入（见 _apply_view_theme）。

        页面自身永远透明：无壁纸时底色由外层圆角容器提供，有壁纸时直接透出壁纸。
        画面区、侧栏这类"必须看清内容"的区域在壁纸下用半透明 —— 既不全挡住壁纸，
        也不至于让截图糊在花哨背景上看不清。
        """
        if theme_mode is None:
            theme_mode = (ThemeMode.DARK
                          if Settings.get_theme_mode() == THEME_MODE_DARK
                          else ThemeMode.LIGHT)
        has_wallpaper = bool(has_wallpaper)
        is_dark = theme_mode == ThemeMode.DARK
        self._is_dark = is_dark
        self._has_wallpaper = has_wallpaper

        if is_dark:
            # 壁纸模式：整页铺一层薄罩（0.42），壁纸明显透出，同时保证顶栏/底部
            # 那些直接落在页面上的文字仍然看得清；无壁纸时交给外层容器铺色
            page_bg = "rgba(26, 27, 30, 0.42)" if has_wallpaper else "transparent"
            card_bg = "rgba(43, 45, 48, 0.76)" if has_wallpaper else "#2b2d30"
            border = "rgba(96, 100, 106, 0.85)" if has_wallpaper else "#3a3d42"
            fg = "#e6e6e6"
            sub = "#c3c9cf" if has_wallpaper else "#9aa0a6"
            hover = "rgba(255, 255, 255, 0.08)"
            primary, primary_hover = "#1976d2", "#1565c0"
            err_bg = "rgba(74, 59, 29, 0.88)" if has_wallpaper else "#4a3b1d"
            err_fg = "#f0c36d"
            sel_bg = "rgba(25, 118, 210, 0.22)"
            field_bg = "rgba(30, 32, 36, 0.9)" if has_wallpaper else "#202226"
        else:
            page_bg = "rgba(255, 255, 255, 0.5)" if has_wallpaper else "transparent"
            card_bg = "rgba(247, 248, 250, 0.78)" if has_wallpaper else "#f7f8fa"
            border = "rgba(202, 206, 212, 0.9)" if has_wallpaper else "#e2e4e8"
            fg = "#1f2328"
            sub = "#3f4650" if has_wallpaper else "#6b7280"
            hover = "rgba(0, 0, 0, 0.05)"
            primary, primary_hover = "#1976d2", "#1565c0"
            err_bg = "rgba(253, 243, 216, 0.9)" if has_wallpaper else "#fdf3d8"
            err_fg = "#8a6414"
            sel_bg = "rgba(25, 118, 210, 0.10)"
            field_bg = "rgba(255, 255, 255, 0.92)" if has_wallpaper else "#ffffff"

        self.setStyleSheet(f"""
            QWidget#VisualView {{
                background-color: {page_bg};
                /* 与外层圆角容器贴合：比容器的 8px 少 1，配合 1px 内边距 */
                border-radius: 7px;
            }}
            QLabel {{ color: {fg}; background: transparent; }}
            QLabel#VisualZoomLabel, QLabel#VisualDeviceLabel,
            QLabel#VisualTimeLabel, QLabel#VisualCaption,
            QLabel#VisualNodeKey, QLabel#VisualHint,
            QLabel#VisualCount {{ color: {sub}; font-size: 12px; }}
            QLabel#VisualNodeValue {{ color: {fg}; font-size: 12px; }}
            QLabel#VisualErrorLabel {{ color: {err_fg}; font-size: 12px; }}
            QFrame#VisualErrorBar {{
                background-color: {err_bg};
                border: 1px solid {err_fg};
                border-radius: 6px;
            }}
            QFrame#VisualSide {{
                background-color: {card_bg};
                border: 1px solid {border};
                border-radius: 8px;
            }}
            QFrame#VisualDivider {{ background-color: {border}; border: none; }}
            QFrame#VisualLocRow {{ background: transparent; border-radius: 5px; }}
            QFrame#VisualLocRow[available="1"]:hover {{ background-color: {hover}; }}
            QFrame#VisualLocRow[selected="1"] {{
                background-color: {sel_bg};
                border: 1px solid {primary};
            }}
            QLabel#VisualLocName {{ color: {sub}; font-size: 12px; }}
            QFrame#VisualLocRow[available="1"] QLabel#VisualLocName {{ color: {fg}; }}
            QFrame#VisualLocRow[recommended="1"] QLabel#VisualLocName {{
                color: {primary}; font-weight: 600;
            }}
            QLabel#VisualLocValue {{ color: {sub}; font-size: 12px; }}
            QFrame#VisualLocRow[available="1"] QLabel#VisualLocValue {{ color: {fg}; }}
            QLabel#VisualLocTail {{ color: {sub}; font-size: 11px; }}
            QFrame#VisualLocRow[available="1"] QLabel#VisualLocTail {{ color: {primary}; }}
            QPushButton {{
                background-color: {card_bg};
                color: {fg};
                border: 1px solid {border};
                border-radius: 6px;
                padding: 5px 12px;
                font-size: 12px;
            }}
            QPushButton:hover {{ border-color: {primary}; }}
            QPushButton:disabled {{ color: {sub}; }}
            QPushButton#VisualRefreshBtn {{
                background-color: {primary};
                color: #ffffff;
                border: 1px solid {primary};
                font-weight: 500;
            }}
            QPushButton#VisualRefreshBtn:hover {{ background-color: {primary_hover}; }}
            QPushButton#VisualRefreshBtn:disabled {{
                background-color: {border};
                border-color: {border};
                color: {sub};
            }}
            QProgressBar#VisualProgress {{
                background-color: {border};
                border: none;
                border-radius: 2px;
            }}
            QProgressBar#VisualProgress::chunk {{
                background-color: {primary};
                border-radius: 2px;
            }}
            QFrame#VisualSaveBar {{
                background-color: {card_bg};
                border: 1px solid {border};
                border-radius: 6px;
            }}
            QLineEdit, QComboBox {{
                background-color: {field_bg};
                color: {fg};
                border: 1px solid {border};
                border-radius: 5px;
                padding: 4px 8px;
                font-size: 12px;
            }}
            QLineEdit:focus, QComboBox:focus {{ border-color: {primary}; }}
            QComboBox QAbstractItemView {{
                background-color: {card_bg};
                color: {fg};
                border: 1px solid {border};
                selection-background-color: {primary};
                selection-color: #ffffff;
                outline: none;
            }}
            QPushButton#VisualApplyBtn, QPushButton#VisualSaveConfirmBtn {{
                background-color: {primary};
                color: #ffffff;
                border: 1px solid {primary};
            }}
            QPushButton#VisualApplyBtn:hover,
            QPushButton#VisualSaveConfirmBtn:hover {{
                background-color: {primary_hover};
            }}
            QPushButton#VisualApplyBtn:disabled {{
                background-color: {border};
                border-color: {border};
                color: {sub};
            }}
            QSplitter::handle {{ background-color: transparent; }}
        """)
        self.canvas.set_theme(is_dark, self._has_wallpaper)
