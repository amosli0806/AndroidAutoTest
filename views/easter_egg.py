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
  * **零外部素材**：鲸鱼与粒子全部 QPainter 自绘。鲸鱼画成**座头鲸**
    （大钝头 + 浅色下颌与喉腹褶 + 大眼 + 长胸鳍 + 上扬分叉尾鳍），
    形取自用户提供的参考图；不新增图片资源，不受打包 spec 影响。
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

        # 水下斜射光（呼应参考图的氛围）
        self._draw_rays(p, w, h, t, fade)
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

    def _draw_rays(self, p: QPainter, w: int, h: int, t: float, fade: float):
        """水下斜射光：几道自左上斜向右下的柔光柱（呼应参考图的氛围）。

        只画在暗色主题 —— 浅色底上光柱几乎不可见，画了反而脏。
        """
        if not self._dark:
            return
        p.save()
        p.setPen(Qt.PenStyle.NoPen)
        # (起点 x 比例, 柱宽, 倾角系数)：自上而下渐隐，避免变成生硬的条纹
        for xf, wid, lean in ((0.12, 26, 0.32), (0.34, 40, 0.42),
                              (0.60, 20, 0.36), (0.80, 32, 0.48)):
            x0 = w * xf
            grad = QLinearGradient(0, 0, 0, h)
            grad.setColorAt(0.0, QColor(150, 205, 245, int(34 * fade)))
            grad.setColorAt(0.55, QColor(150, 205, 245, int(11 * fade)))
            grad.setColorAt(1.0, QColor(150, 205, 245, 0))
            p.setBrush(grad)
            path = QPainterPath()
            path.moveTo(x0, 0)
            path.lineTo(x0 + wid, 0)
            path.lineTo(x0 + wid + h * lean, h)
            path.lineTo(x0 + h * lean, h)
            path.closeSubpath()
            p.drawPath(path)
        p.restore()

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
        """画一只座头鲸（humpback），形取自用户提供的参考图。

        抓住座头鲸的几个标志性特征：
          * **又大又钝的头** + 向下的下颌线；
          * 下颌与喉腹是**浅色**，并带多道 **喉腹褶（ventral pleats）**；
          * **大而传神的眼睛**（虹膜 + 瞳孔 + 高光）；
          * 座头鲸招牌的**长胸鳍**（约占体长 1/3），向下后方舒展；
          * **高高扬起的分叉尾鳍**。

        坐标以 (cx, cy) 为鲸身中心；内部用 translate+scale 在局部坐标
        （原点=鲸身中心）作画，整体约占 180×150 的方框。
        """
        a = max(0.0, min(1.0, fade))
        if a <= 0.01:
            return

        p.save()
        p.translate(cx, cy)
        p.scale(0.80, 0.80)

        # 配色：保持青蓝调（契合彩蛋的深蓝/浅蓝底），但用座头鲸的明暗关系
        top_c = QColor(96, 168, 216); top_c.setAlphaF(0.99 * a)
        bot_c = QColor(38, 100, 156);  bot_c.setAlphaF(0.99 * a)
        belly_c = QColor(228, 241, 251); belly_c.setAlphaF(0.97 * a)

        # ---- 主体轮廓：大钝头 + 平缓背部 + 圆腹，尾根收细 ----
        body = QPainterPath()
        body.moveTo(-96, -2)                                      # 吻端（钝圆）
        body.cubicTo(-101, -34, -68, -55, -22, -55)               # 头顶大弧
        body.cubicTo(20, -55, 48, -47, 58, -33)                   # 背部
        body.cubicTo(64, -23, 62, -13, 54, -3)                    # 收向尾根
        body.cubicTo(50, 17, 26, 37, -16, 45)                     # 腹部
        body.cubicTo(-54, 51, -92, 37, -96, -2)                   # 回到吻端
        body.closeSubpath()

        grad = QLinearGradient(0, -55, 0, 46)
        grad.setColorAt(0.0, top_c)
        grad.setColorAt(1.0, bot_c)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawPath(body)

        # ---- 浅色的下颌 / 喉腹（裁剪在体内）----
        p.save()
        p.setClipPath(body)
        lower = QPainterPath()
        lower.moveTo(-98, -4)
        lower.cubicTo(-72, 32, -20, 50, 22, 46)
        lower.cubicTo(48, 42, 60, 20, 62, 2)
        lower.lineTo(84, 92)
        lower.lineTo(-124, 92)
        lower.closeSubpath()
        p.setBrush(belly_c)
        p.drawPath(lower)

        # ---- 喉腹褶（ventral pleats）：几道平行的浅色沟 ----
        pen = QPen(QColor(146, 178, 206, int(155 * a)))
        pen.setWidthF(1.7)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for i in range(6):
            k = i / 5.0
            pl = QPainterPath()
            pl.moveTo(-82 + k * 9, 3 + k * 10)
            pl.cubicTo(-50, 24 + k * 9, -8, 39 + k * 5, 28, 29 + k * 2)
            p.drawPath(pl)
        p.restore()

        # ---- 嘴线：自吻端向后下方轻扫（下颌与上颌的分界）----
        pen = QPen(QColor(26, 64, 102, int(205 * a)))
        pen.setWidthF(3.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        mouth = QPainterPath()
        mouth.moveTo(-97, 3)
        mouth.cubicTo(-74, -1, -48, -5, -26, -7)
        p.drawPath(mouth)

        # ---- 远侧胸鳍（在身体后下方，深色，先画）----
        far_fin = QPainterPath()
        far_fin.moveTo(-20, 30)
        far_fin.cubicTo(-8, 52, 6, 74, 16, 88)
        far_fin.cubicTo(24, 96, 30, 94, 27, 84)
        far_fin.cubicTo(20, 66, 10, 48, 2, 32)
        far_fin.closeSubpath()
        far_c = QColor(30, 84, 134); far_c.setAlphaF(0.9 * a)
        p.setBrush(far_c)
        p.drawPath(far_fin)

        # ---- 尾鳍：自尾根向右上高高扬起的分叉大尾 ----
        fluke = QPainterPath()
        fluke.moveTo(52, -33)                                     # 尾根上端
        fluke.cubicTo(70, -50, 88, -80, 98, -102)                 # 上叶尖（略偏左）
        fluke.cubicTo(104, -94, 102, -78, 93, -66)                # 叶间缺刻
        fluke.cubicTo(102, -68, 116, -66, 128, -62)               # 下叶尖（偏右）
        fluke.cubicTo(118, -46, 96, -32, 78, -24)
        fluke.cubicTo(66, -20, 56, -24, 52, -33)
        fluke.closeSubpath()
        fgrad = QLinearGradient(52, -100, 128, -22)
        f1 = QColor(112, 182, 226); f1.setAlphaF(0.98 * a)
        f2 = QColor(34, 94, 148);   f2.setAlphaF(0.98 * a)
        fgrad.setColorAt(0.0, f1)
        fgrad.setColorAt(1.0, f2)
        p.setBrush(fgrad)
        p.drawPath(fluke)

        # ---- 近侧胸鳍：座头鲸招牌的长胸鳍，向下后方舒展，带浅色斑 ----
        fin = QPainterPath()
        fin.moveTo(-32, 12)
        fin.cubicTo(-14, 34, 12, 60, 32, 78)
        fin.cubicTo(46, 90, 60, 90, 58, 76)
        fin.cubicTo(52, 58, 28, 34, 8, 14)
        fin.cubicTo(-2, 4, -24, 2, -32, 12)
        fin.closeSubpath()
        ngrad = QLinearGradient(0, 10, 56, 86)
        n1 = QColor(168, 214, 240); n1.setAlphaF(0.96 * a)
        n2 = QColor(74, 144, 198);  n2.setAlphaF(0.96 * a)
        ngrad.setColorAt(0.0, n1)
        ngrad.setColorAt(1.0, n2)
        p.setBrush(ngrad)
        p.drawPath(fin)

        # ---- 眼睛：大眼（暗色眼周 + 暖金虹膜 + 瞳孔 + 高光）----
        ex, ey = -42, -21
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(120, 164, 200, int(95 * a)))        # 眼周淡影
        p.drawEllipse(QPointF(ex, ey), 11.6, 11.6)
        p.setBrush(QColor(36, 70, 106, int(246 * a)))         # 眼球深色
        p.drawEllipse(QPointF(ex, ey), 8.7, 8.7)
        p.setBrush(QColor(198, 160, 94, int(242 * a)))        # 暖金虹膜（含蓄）
        p.drawEllipse(QPointF(ex, ey), 6.0, 6.0)
        p.setBrush(QColor(17, 25, 37, 253))                   # 瞳孔
        p.drawEllipse(QPointF(ex + 0.3, ey + 0.3), 3.2, 3.2)
        p.setBrush(QColor(255, 255, 255, int(235 * a)))       # 高光
        p.drawEllipse(QPointF(ex - 2.2, ey - 2.4), 1.7, 1.7)

        # ---- 几颗细小的气泡（水下感，呼应参考图的氛围）----
        bub = QColor(255, 255, 255, int(90 * a))
        pen = QPen(bub)
        pen.setWidthF(1.4)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for bx, by, br in ((70, 4, 3.4), (84, -10, 2.5), (94, 2, 1.9)):
            p.drawEllipse(QPointF(bx, by), br, br)

        p.restore()

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
