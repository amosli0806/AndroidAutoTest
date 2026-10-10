# views/easter_egg.py
"""「关于 虫师」对话框里的一枚小彩蛋。

触发方式：在关于对话框里**连续点击版本号 5 次**（相邻两次间隔不超过 1.5 秒）。
兑现方式：对话框上浮出一层自绘画面 —— 「虫」化作微光粒子飘散、一只
鲸鱼从右往左缓缓游过，同时浮现一句《虫师》的台词；约 3.7 秒后自动
淡出，不留任何痕迹。

设计原则（与项目其它 UI 一致）：
  * **完全独立**：不引用任何业务对象，只依赖 QDialog + QPainter + 动画。
    主窗口侧只需一行 `attach_about_easter_egg(dialog, sub_label, is_dark)`。
  * **零外部素材**：鲸鱼与粒子全部 QPainter 自绘，不新增图片资源，
    不受打包 spec 影响。
  * **不干扰**：覆盖层鼠标穿透（WA_TransparentForMouseEvents），
    彩蛋进行中点「关闭 / 复制并关闭」照常可用；随对话框一起销毁。
"""
import math

from PyQt6.QtCore import (
    QEasingCurve, QObject, QPointF, QPropertyAnimation, QRectF, Qt, QTimer,
    pyqtProperty,
)
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QWidget

# 触发判定
_CLICKS_NEEDED = 5          # 需要连点的次数
_CLICK_WINDOW_MS = 1500     # 相邻两次点击的最大间隔（超过则重新计数）

# 彩蛋节奏（ms）
_INTRO_MS = 420            # 覆盖层整体淡入
_HOLD_MS = 2600            # 鲸鱼游过 + 台词停留
_OUTRO_MS = 700            # 覆盖层淡出
_TOTAL_MS = _INTRO_MS + _HOLD_MS + _OUTRO_MS

# 台词（《虫师》）
_QUOTE = "虫并没有消失。\n它们只是回到了，原本该在的地方。"
_QUOTE_SOURCE = "—— 银古"


class _EggOverlay(QWidget):
    """覆盖在关于对话框上的自绘层：暮色底 + 微光粒子 + 鲸鱼 + 台词。

    不做布局，几何始终跟随父对话框的 rect（由 _OverlayResizer 维护）。
    `progress` 属性 0.0~1.0 由动画驱动，paintEvent 按它逐帧作画。
    """

    def __init__(self, parent: QWidget, dark: bool = False):
        super().__init__(parent)
        self._dark = dark
        self._t = 0.0
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.hide()

    # ---- 动画驱动的进度属性 ----
    def _get_progress(self) -> float:
        return self._t

    def _set_progress(self, v: float):
        self._t = max(0.0, min(1.0, float(v)))
        self.update()

    progress = pyqtProperty(float, fget=_get_progress, fset=_set_progress)

    # ---- 自绘 ----
    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return

        t = self._t
        # 整体明灭：0→1（进入）→保持→0（退出）
        if t < 0.12:
            fade = t / 0.12
        elif t > 0.90:
            fade = max(0.0, (1.0 - t) / 0.10)
        else:
            fade = 1.0
        fade = max(0.0, min(1.0, fade))

        # 暮色底
        base = QColor(10, 18, 32) if self._dark else QColor(244, 249, 255)
        base.setAlphaF(0.94 * fade)
        p.fillRect(0, 0, w, h, base)

        self._draw_particles(p, w, h, t, fade)

        # 鲸鱼：t 0.10~0.90 内从右往左游过。
        # 用 InOutSine 而非 OutCubic —— 前者两头慢、中段快，鲸鱼会在画面
        # 中央附近放慢，看得最清楚；OutCubic 起步就窜到左边，来不及看清。
        if t > 0.10:
            span = max(0.0, min(1.0, (t - 0.10) / 0.80))
            ease = -(math.cos(math.pi * span) - 1) / 2   # InOutSine
            wx = w * 1.12 - ease * (w * 1.24)            # 右侧外 → 左侧外
            wy = h * 0.30 + math.sin(span * math.pi) * 14
            self._draw_whale(p, wx, wy, fade)

        # 台词：t 0.30 后淡入，退场前淡出
        if t > 0.30:
            qa = min(1.0, (t - 0.30) / 0.20)
            if t > 0.86:
                qa *= max(0.0, (1.0 - t) / 0.14)
            self._draw_quote(p, w, h, fade * qa)
        p.end()

    def _draw_particles(self, p: QPainter, w: int, h: int, t: float, fade: float):
        """微光粒子（「虫」）：缓慢上浮 + 明灭。"""
        p.setPen(Qt.PenStyle.NoPen)
        base = QColor(96, 178, 255) if self._dark else QColor(43, 127, 255)
        for i in range(26):
            phase = (i * 0.6180339887) % 1.0
            px = ((i * 53) % 97) / 97.0 * w
            py = h - ((phase + t * 0.55) % 1.0) * h
            twinkle = 0.4 + 0.6 * abs(math.sin((t * 3.0 + phase) * math.pi))
            col = QColor(base)
            col.setAlphaF(max(0.0, min(1.0, 0.5 * fade * twinkle)))
            p.setBrush(col)
            r = 1.4 + 1.6 * ((i % 3) / 2.0)
            p.drawEllipse(QPointF(px, py), r, r)

    def _draw_whale(self, p: QPainter, cx: float, cy: float, fade: float):
        """画一只朝左游的简约鲸鱼（品牌鲸鱼 logo 的简化版）。

        坐标以 (cx, cy) 为身体中心；整体宽约 96、高约 48。
        """
        body_col = QColor(43, 127, 255)
        body_col.setAlphaF(0.95 * fade)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(body_col)

        # 身体：背部高拱、腹部平缓收拢，头部在左
        path = QPainterPath()
        path.moveTo(cx - 48, cy + 2)                                   # 吻端
        path.cubicTo(cx - 46, cy - 20, cx - 20, cy - 30, cx + 8, cy - 26)  # 背
        path.cubicTo(cx + 30, cy - 23, cx + 40, cy - 12, cx + 44, cy)      # 尾根
        path.cubicTo(cx + 40, cy + 12, cx + 26, cy + 20, cx + 6, cy + 20)  # 腹
        path.cubicTo(cx - 20, cy + 20, cx - 46, cy + 16, cx - 48, cy + 2)  # 回到吻端
        path.closeSubpath()
        p.drawPath(path)

        # 尾鳍：尾根处向后上、后下各挑出一叶（鲸尾的经典分叉）
        tail = QPainterPath()
        tail.moveTo(cx + 42, cy - 2)
        tail.cubicTo(cx + 56, cy - 22, cx + 70, cy - 24, cx + 68, cy - 8)
        tail.cubicTo(cx + 66, cy - 2, cx + 58, cy + 2, cx + 42, cy + 4)
        tail.closeSubpath()
        p.drawPath(tail)
        tail2 = QPainterPath()
        tail2.moveTo(cx + 42, cy + 2)
        tail2.cubicTo(cx + 56, cy + 20, cx + 68, cy + 22, cx + 66, cy + 8)
        tail2.cubicTo(cx + 64, cy + 2, cx + 56, cy - 2, cx + 42, cy - 4)
        tail2.closeSubpath()
        p.drawPath(tail2)

        # 眼睛
        eye = QColor(255, 255, 255)
        eye.setAlphaF(0.95 * fade)
        p.setBrush(eye)
        p.drawEllipse(QPointF(cx - 34, cy - 4), 2.6, 2.6)

        # 喷水：吻端上方三颗水珠，从小渐大
        spout = QColor(255, 255, 255)
        spout.setAlphaF(0.6 * fade)
        p.setBrush(spout)
        for dx, dy, r in ((-30, -26, 2.6), (-24, -34, 2.0), (-17, -41, 1.4)):
            p.drawEllipse(QPointF(cx + dx, cy + dy), r, r)

    def _draw_quote(self, p: QPainter, w: int, h: int, alpha: float):
        alpha = max(0.0, min(1.0, alpha))
        if alpha <= 0.01:
            return

        col = QColor(235, 244, 255) if self._dark else QColor(38, 62, 96)
        col.setAlphaF(alpha)
        p.setPen(QPen(col))
        f = QFont()
        f.setFamily("Microsoft YaHei")
        f.setPointSize(12)
        p.setFont(f)
        p.drawText(
            QRectF(0, h * 0.58, w, h * 0.34),
            int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop),
            _QUOTE,
        )

        col2 = QColor(120, 160, 210) if self._dark else QColor(120, 140, 170)
        col2.setAlphaF(alpha * 0.9)
        p.setPen(QPen(col2))
        f2 = QFont()
        f2.setFamily("Microsoft YaHei")
        f2.setPointSize(9)
        p.setFont(f2)
        p.drawText(
            QRectF(0, h * 0.79, w, 24),
            int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop),
            _QUOTE_SOURCE,
        )


class _ClickTracker(QObject):
    """统计版本号的连续点击；达标后回调 on_trigger。"""

    def __init__(self, on_trigger, parent=None):
        super().__init__(parent)
        self._on_trigger = on_trigger
        self._count = 0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(_CLICK_WINDOW_MS)
        self._timer.timeout.connect(self._reset)

    def _reset(self):
        self._count = 0

    def feed(self):
        self._count += 1
        self._timer.start()
        if self._count >= _CLICKS_NEEDED:
            self._count = 0
            self._timer.stop()
            self._on_trigger()


class _LabelClickFilter(QObject):
    """把触发标签上的左键单击转成 tracker.feed()。"""

    def __init__(self, tracker, parent=None):
        super().__init__(parent)
        self._tracker = tracker

    def eventFilter(self, obj, event):
        if event.type() == event.Type.MouseButtonRelease:
            try:
                if event.button() == Qt.MouseButton.LeftButton:
                    self._tracker.feed()
            except Exception:
                pass
        return False


class _OverlayResizer(QObject):
    """对话框尺寸变化时把覆盖层拉满。"""

    def __init__(self, overlay, dialog):
        super().__init__(dialog)
        self._overlay = overlay
        self._dialog = dialog

    def eventFilter(self, obj, event):
        if obj is self._dialog and event.type() == event.Type.Resize:
            self._overlay.setGeometry(0, 0, self._dialog.width(), self._dialog.height())
        return False


def attach_about_easter_egg(dialog, trigger_label, dark: bool = False):
    """给关于对话框挂上彩蛋。

    dialog        —— QDialog
    trigger_label —— 显示「V1.4.5 · Build …」的 QLabel（连点它触发）
    dark          —— 当前是否暗色主题

    返回覆盖层 widget（不必持有，随 dialog 一起销毁）。
    """
    overlay = _EggOverlay(dialog, dark=dark)
    overlay.setGeometry(0, 0, dialog.width(), dialog.height())
    dialog.installEventFilter(_OverlayResizer(overlay, dialog))

    def _play():
        overlay.setGeometry(0, 0, dialog.width(), dialog.height())
        overlay.raise_()
        overlay.show()
        anim = QPropertyAnimation(overlay, b"progress", overlay)
        anim.setDuration(_TOTAL_MS)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.Linear)
        anim.finished.connect(overlay.hide)
        dialog._egg_anim = anim          # 持有引用，防 GC
        anim.start()

    tracker = _ClickTracker(_play, parent=dialog)
    flt = _LabelClickFilter(tracker, parent=dialog)
    trigger_label.installEventFilter(flt)
    trigger_label.setCursor(Qt.CursorShape.PointingHandCursor)
    trigger_label.setToolTip("虫师 · 连点 5 次有惊喜")

    dialog._egg_tracker = tracker        # 防 GC
    dialog._egg_filter = flt
    dialog._egg_overlay = overlay
    return overlay
