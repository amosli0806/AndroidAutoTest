# views/easter_egg.py
"""「关于 虫师」对话框里的一枚小彩蛋。

触发方式：在关于对话框里**连续点击版本号 5 次**（相邻两次间隔不超过 1.5 秒）。
兑现方式：对话框上浮出一层自绘画面 —— 幽微的「虫」（bug）化作光点飘散，
一只鲸鱼从右往左缓缓游过；同时浮现一句「虫师」的自我介绍。
约 3.7 秒后自动淡出，不留任何痕迹。

关于「虫」：这里的虫是**程序里的 bug**（debug 的 bug），不是那部同名动画。
「虫师」即「捉虫的人」—— 本工具做的正是把 bug 一只只捉出来这件事。
因此彩蛋的立意也落在「捉虫」上，而不是借动漫的壳。

设计原则（与项目其它 UI 一致）：
  * **完全独立**：不引用任何业务对象，只依赖 QDialog + QPainter + 动画。
    主窗口侧只需一行 `attach_about_easter_egg(dialog, sub_label, is_dark)`。
  * **零外部素材**：鲸鱼与粒子全部 QPainter 自绘（形取自常见鲸鱼插画画法：
    圆头 + 圆肚 + 上扬尾鳍 + 胸鳍 + 喷水），不新增图片资源，
    不受打包 spec 影响。
  * **不干扰**：覆盖层鼠标穿透（WA_TransparentForMouseEvents），
    彩蛋进行中点「关闭 / 复制并关闭」照常可用；随对话框一起销毁。
  * **不剧透**：触发标签**不设 tooltip、不改光标** —— 彩蛋要留给用户自己发现。
"""
import math

from PyQt6.QtCore import (
    QEasingCurve, QObject, QPointF, QPropertyAnimation, QRectF, Qt, QTimer,
    pyqtProperty,
)
from PyQt6.QtGui import (
    QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen,
)
from PyQt6.QtWidgets import QWidget

# 触发判定
_CLICKS_NEEDED = 5          # 需要连点的次数
_CLICK_WINDOW_MS = 1500     # 相邻两次点击的最大间隔（超过则重新计数）

# 彩蛋节奏（ms）
_INTRO_MS = 420            # 覆盖层整体淡入
_HOLD_MS = 2600            # 鲸鱼游过 + 文案停留
_OUTRO_MS = 700            # 覆盖层淡出
_TOTAL_MS = _INTRO_MS + _HOLD_MS + _OUTRO_MS

# 文案：「虫」= bug；「虫师」= 捉虫的人。正经、有力量。
_QUOTE = "见虫即捉，见 BUG 即斩。"
_QUOTE_SUB = "让每一只藏起来的虫，都无处可藏。"
_QUOTE_SOURCE = "—— 虫师"


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
            wx = w * 1.18 - ease * (w * 1.36)            # 右侧外 → 左侧外
            wy = h * 0.30 + math.sin(span * math.pi) * 8
            self._draw_whale(p, wx, wy, fade)

        # 文案：t 0.30 后淡入，退场前淡出
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
        """画一只自然可爱的鲸鱼（朝左游）。

        参照常见鲸鱼插画的画法：**大而圆的头**（占身体大部分）+ 圆肚子 +
        上扬的小尾鳍（两叶、圆润）+ 小胸鳍 + 大眼高光 + 头顶喷水。
        坐标以 (cx, cy) 为身体中心，整体约占 150×110 的方框。
        """
        a = max(0.0, min(1.0, fade))

        top = QColor(110, 210, 255); top.setAlphaF(0.98 * a)
        bot = QColor(24, 146, 246);  bot.setAlphaF(0.98 * a)
        belly = QColor(214, 240, 255); belly.setAlphaF(0.92 * a)

        # ---- 身体：大圆弧头 + 圆肚子，尾根收细 ----
        body = QPainterPath()
        body.moveTo(cx - 62, cy + 6)                                    # 吻端
        body.cubicTo(cx - 62, cy - 40, cx - 10, cy - 52, cx + 26, cy - 34)  # 背部大弧
        body.cubicTo(cx + 44, cy - 25, cx + 52, cy - 12, cx + 54, cy - 4)   # 收向尾根
        body.cubicTo(cx + 52, cy + 6, cx + 42, cy + 16, cx + 22, cy + 24)   # 腹部
        body.cubicTo(cx - 12, cy + 34, cx - 54, cy + 30, cx - 62, cy + 6)   # 回到吻端
        body.closeSubpath()

        grad = QLinearGradient(cx, cy - 52, cx, cy + 34)
        grad.setColorAt(0.0, top)
        grad.setColorAt(1.0, bot)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawPath(body)

        # ---- 腹部浅色弧（鲸鱼特征）----
        p.save()
        p.setClipPath(body)
        belly_path = QPainterPath()
        belly_path.moveTo(cx - 62, cy + 10)
        belly_path.cubicTo(cx - 30, cy + 52, cx + 20, cy + 50, cx + 54, cy + 10)
        belly_path.cubicTo(cx + 34, cy + 40, cx - 26, cy + 44, cx - 62, cy + 10)
        belly_path.closeSubpath()
        p.setBrush(belly)
        p.drawPath(belly_path)
        p.restore()

        # ---- 尾鳍：尾根处向右上扬的两叶（圆润、翘起）----
        tail = QPainterPath()
        tail.moveTo(cx + 48, cy - 4)
        # 上叶（向右上翘）
        tail.cubicTo(cx + 62, cy - 22, cx + 82, cy - 34, cx + 90, cy - 28)
        tail.cubicTo(cx + 84, cy - 10, cx + 68, cy - 2, cx + 56, cy + 2)
        # 下叶（略向右下）
        tail.cubicTo(cx + 70, cy + 4, cx + 84, cy + 2, cx + 90, cy + 10)
        tail.cubicTo(cx + 76, cy + 22, cx + 58, cy + 16, cx + 48, cy + 4)
        tail.closeSubpath()
        tgrad = QLinearGradient(cx + 48, cy - 30, cx + 90, cy + 16)
        t1 = QColor(128, 218, 255); t1.setAlphaF(0.96 * a)
        t2 = QColor(24, 146, 246);  t2.setAlphaF(0.96 * a)
        tgrad.setColorAt(0.0, t1)
        tgrad.setColorAt(1.0, t2)
        p.setBrush(tgrad)
        p.drawPath(tail)

        # ---- 胸鳍：身体下方一片小小的船桨 ----
        fin = QPainterPath()
        fin.moveTo(cx - 30, cy + 18)
        fin.cubicTo(cx - 42, cy + 32, cx - 58, cy + 40, cx - 66, cy + 36)
        fin.cubicTo(cx - 60, cy + 22, cx - 46, cy + 12, cx - 34, cy + 10)
        fin.closeSubpath()
        fcol = QColor(24, 146, 246); fcol.setAlphaF(0.9 * a)
        p.setBrush(fcol)
        p.drawPath(fin)

        # ---- 眼睛：大眼 + 白色高光（靠吻端一些，符合鲸鱼侧脸比例）----
        eye_c = QPainterPath()
        eye_c.addEllipse(QPointF(cx - 48, cy - 8), 5.6, 5.6)
        p.setBrush(QColor(255, 255, 255, int(245 * a)))
        p.drawPath(eye_c)
        p.setBrush(QColor(20, 55, 100, int(245 * a)))
        p.drawEllipse(QPointF(cx - 47, cy - 8), 3.1, 3.1)
        p.setBrush(QColor(255, 255, 255, int(235 * a)))
        p.drawEllipse(QPointF(cx - 48.6, cy - 9.4), 1.3, 1.3)

        # ---- 喷水：头顶一道细细的水柱 + 顶端水花 ----
        # 亮色背景下白色太淡，改用浅蓝白；暗色下用纯白。
        sp_c = (QColor(255, 255, 255, int(190 * a)) if self._dark
                else QColor(150, 205, 245, int(210 * a)))
        pen = QPen(sp_c)
        pen.setWidthF(2.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        spout = QPainterPath()
        spout.moveTo(cx - 16, cy - 44)
        spout.cubicTo(cx - 20, cy - 58, cx - 14, cy - 68, cx - 8, cy - 74)
        p.drawPath(spout)
        p.setPen(Qt.PenStyle.NoPen)
        droplet = (QColor(255, 255, 255, int(200 * a)) if self._dark
                   else QColor(150, 205, 245, int(220 * a)))
        p.setBrush(droplet)
        for dx, dy, r in ((-14, -80, 2.6), (-7, -84, 2.0), (-1, -79, 1.6)):
            p.drawEllipse(QPointF(cx + dx, cy + dy), r, r)

    def _draw_quote(self, p: QPainter, w: int, h: int, alpha: float):
        alpha = max(0.0, min(1.0, alpha))
        if alpha <= 0.01:
            return
        from PyQt6.QtGui import QFont as _QFont

        # 主文案（大字、加粗、有力量）
        col = QColor(232, 243, 255) if self._dark else QColor(26, 48, 82)
        col.setAlphaF(alpha)
        p.setPen(QPen(col))
        f = _QFont("Microsoft YaHei")
        f.setPointSize(15)
        f.setBold(True)
        f.setLetterSpacing(_QFont.SpacingType.AbsoluteSpacing, 1.5)
        p.setFont(f)
        p.drawText(
            QRectF(0, h * 0.62, w, 32),
            int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop),
            _QUOTE,
        )

        # 副文案（小字、柔和）
        col_sub = QColor(150, 185, 225) if self._dark else QColor(120, 145, 180)
        col_sub.setAlphaF(alpha * 0.92)
        p.setPen(QPen(col_sub))
        f2 = _QFont("Microsoft YaHei")
        f2.setPointSize(9)
        p.setFont(f2)
        p.drawText(
            QRectF(0, h * 0.72, w, 24),
            int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop),
            _QUOTE_SUB,
        )

        # 落款
        col3 = QColor(120, 160, 210) if self._dark else QColor(130, 150, 178)
        col3.setAlphaF(alpha * 0.85)
        p.setPen(QPen(col3))
        f3 = _QFont("Microsoft YaHei")
        f3.setPointSize(10)
        f3.setBold(True)
        p.setFont(f3)
        p.drawText(
            QRectF(0, h * 0.84, w, 24),
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
    # 注意：**不设 tooltip、不改光标** —— 彩蛋要留给用户自己发现，
    # 任何提示都会变成剧透。（2026-10-10 用户明确要求）

    dialog._egg_tracker = tracker        # 防 GC
    dialog._egg_filter = flt
    dialog._egg_overlay = overlay
    return overlay
