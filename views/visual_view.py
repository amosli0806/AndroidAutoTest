# views/visual_view.py
"""应用可视化（整页）：看设备画面，点一下取定位值。

与「元素拾取面板」（views/dialogs/element_pick_dialog.py）的分工：
    拾取面板 —— 从动作卡片/元素库弹出来，一次只为填一个定位，选完就关
    这个页面 —— 常驻，反复看、来回对照，定位值点一下就复制

命中规则、XPath 生成都在 utils/ui_tree.py，和录制反查、拾取面板共用一份，
不在这里另写一套。
"""
import time
from datetime import datetime

from PyQt6.QtCore import QPoint, QRect, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel,
                             QProgressBar, QPushButton, QSizePolicy, QSplitter,
                             QVBoxLayout, QWidget)

from utils.settings import Settings, THEME_MODE_DARK
from utils.theme import ThemeMode
from utils.ui_tree import describe_node

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

    def set_theme(self, is_dark: bool):
        self._is_dark = bool(is_dark)
        self.update()

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
        self._paint_hit(painter)
        painter.end()

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
            self._drag_from = event.position().toPoint()
            self._drag_offset = QPoint(self._offset)
            self._dragging = True
            self._moved = False
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
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
                recommended: bool = False):
        self.value = value or ""
        self.available = bool(ok)
        self.setProperty("available", "1" if ok else "0")
        self.setProperty("recommended", "1" if recommended else "0")
        if ok and len(self.value) > 48:
            # 两行也放不下时中间省略，首尾都留着便于辨认（点一下复制的是全量）
            shown = f"{self.value[:20]}…{self.value[-24:]}"
        else:
            shown = self.value if ok else ""
        shown = shown or note or "无"
        self.value_label.setText(shown)
        self.value_label.setToolTip(self.value if ok else "")
        self.tail_label.setText(note if (ok and note) else ("复制" if ok else ""))
        self.setCursor(Qt.CursorShape.PointingHandCursor if ok
                       else Qt.CursorShape.ArrowCursor)
        for w in (self, self.name_label, self.value_label, self.tail_label):
            w.style().unpolish(w)
            w.style().polish(w)
        self.update()

    def flash_copied(self):
        self.tail_label.setText("已复制")
        QTimer.singleShot(1200, lambda: self.tail_label.setText(
            "复制" if self.available else ""))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.available and self.value:
            self.activated.emit(self.type_name, self.value)
            self.flash_copied()
            return
        super().mousePressEvent(event)


class VisualView(QWidget):
    """应用可视化整页。数据全在 VisualController，这里只负责展示与交互。"""

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
            row.activated.connect(self._on_copy_value)
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
        status.setSpacing(12)
        self.hint_label = QLabel("")
        self.hint_label.setObjectName("VisualHint")
        self.count_label = QLabel("")
        self.count_label.setObjectName("VisualCount")
        status.addWidget(self.hint_label, 1)
        status.addWidget(self.count_label)
        root.addLayout(status)

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
            self._show_error("未检测到设备，请先在顶部工具栏连接设备")
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
        for item in rows:
            row = self._rows.get(item["type"])
            if row is not None:
                row.set_row(item["value"], item["ok"], item.get("note", ""),
                            item.get("recommended", False))

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
        for row in self._rows.values():
            row.set_row("", False, "")

    def _on_copy_value(self, type_name, value):
        try:
            QApplication.clipboard().setText(value)
        except Exception:
            return
        self.hint_label.setText(f"已复制{type_name}：{value}")

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

    def apply_theme(self, theme_mode: ThemeMode = None):
        if theme_mode is None:
            theme_mode = (ThemeMode.DARK
                          if Settings.get_theme_mode() == THEME_MODE_DARK
                          else ThemeMode.LIGHT)
        is_dark = theme_mode == ThemeMode.DARK
        self._is_dark = is_dark

        if is_dark:
            page_bg, card_bg, border = "#1e1f22", "#2b2d30", "#3a3d42"
            fg, sub, hover = "#e6e6e6", "#9aa0a6", "#34373d"
            primary, primary_hover = "#1976d2", "#1565c0"
            err_bg, err_fg = "#4a3b1d", "#f0c36d"
        else:
            page_bg, card_bg, border = "#ffffff", "#f7f8fa", "#e2e4e8"
            fg, sub, hover = "#1f2328", "#6b7280", "#eef1f5"
            primary, primary_hover = "#1976d2", "#1565c0"
            err_bg, err_fg = "#fdf3d8", "#8a6414"

        self.setStyleSheet(f"""
            QWidget#VisualView {{ background-color: {page_bg}; }}
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
            QSplitter::handle {{ background-color: transparent; }}
        """)
        self.canvas.set_theme(is_dark)
