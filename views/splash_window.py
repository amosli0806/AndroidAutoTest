# views/splash_window.py
"""启动封面窗口。

为什么自绘（而不是用 PyInstaller 的 Splash / Tcl-Tk）：
  PyInstaller 的启动封面由 bootloader 用 Tcl/Tk 渲染，只能在一个固定位置画
  **一行文字**，做不了细进度条、做不了分阶段文案、也做不了淡出过渡。
  所以封面、进度条、阶段文案、版本号、淡出全部在这里自绘。

  打包版唯一入口：main.py 的 splash_show()（QApplication 建好后接管），
  不再有 Tcl/Tk 那层（2026-10-07 起已从 main.spec 彻底移除，避免新旧两个封面）。

设计要点：
  * 无边框 + 置顶 + 不进任务栏，避免抢焦点、避免在任务栏多出一个按钮；
  * 用 windowOpacity 做淡出 —— QGraphicsOpacityEffect 对顶层窗口不生效
    （见 utils/fx.py 里 scale_in 的同类问题）；
  * 进度条在底图左下留白处自绘，版本号在右上角自绘，都不依赖外部素材。
"""
import time

from PyQt6.QtCore import (Qt, QTimer, QPropertyAnimation, QEasingCurve, QPointF,
                          QRectF)
from PyQt6.QtGui import (QPainter, QColor, QFont, QPixmap, QLinearGradient,
                         QPainterPath, QPen)
from PyQt6.QtWidgets import QWidget

# ---------- 视觉参数（都相对底图尺寸，换底图不用改） ----------
# 轨迹：起点 + 5 个启动阶段共 6 个节点，坐标是相对底图宽/高的比例。
# 底图上只画了空心灰节点，运行到这里按进度把走过的节点和线段点亮。
_NODES = [
    (0.1875, 0.3800),   # (120, 152) 起点
    (0.2969, 0.4200),   # (190, 168) 阶段 1
    (0.4094, 0.5450),   # (262, 218) 阶段 2
    (0.5313, 0.6650),   # (340, 266) 阶段 3
    (0.7031, 0.7500),   # (450, 300) 阶段 4
    (0.8438, 0.8250),   # (540, 330) 终点
]
# 5 段三次贝塞尔（控制点也用相对坐标），必须与底图上的灰线严格重合，
# 否则点亮的部分会偏离背景轨迹，看着像两条线。
_SEGS = [
    ((0.1875, 0.3800), (0.2344, 0.3800), (0.2656, 0.3950), (0.2969, 0.4200)),
    ((0.2969, 0.4200), (0.3359, 0.4500), (0.3750, 0.4950), (0.4094, 0.5450)),
    ((0.4094, 0.5450), (0.4453, 0.5900), (0.4922, 0.6350), (0.5313, 0.6650)),
    ((0.5313, 0.6650), (0.5859, 0.6950), (0.6484, 0.7300), (0.7031, 0.7500)),
    ((0.7031, 0.7500), (0.7500, 0.7700), (0.8047, 0.8050), (0.8438, 0.8250)),
]
_NODE_R_RATIO = 0.0063      # 节点半径占底图宽的比例（640*0.0063≈4px）
_SEG_WIDTH_RATIO = 0.0031   # 轨迹线宽（640*0.0031≈2px）
_TRACE_COLOR = QColor('#3B82F6')      # 已走过的线段
_NODE_DONE = QColor('#1D4ED8')        # 已点亮的节点
_NODE_CURRENT = QColor('#3B82F6')     # 当前节点
_HALO_COLOR = QColor(59, 130, 246, 38)  # 当前节点的光圈

# 阶段文案（左下）与右侧计数
_LABEL_MARGIN_X = 0.0437    # 28/640，与底图留白对齐
_LABEL_Y = 0.890            # 文案基线（400*0.89=356）
_LABEL_COLOR = QColor('#7C8898')
_LABEL_SIZE_RATIO = 0.0290   # 约 12px
_COUNT_COLOR = QColor('#3B82F6')

# 圆角半径（像素，按底图宽等比缩放）与描边色
_CORNER_RADIUS_RATIO = 0.019     # 640*0.019 ≈ 12px
_EDGE_COLOR = QColor(15, 23, 42, 20)   # 极淡的边线，浅色桌面上也能看出轮廓

# 每个阶段文案至少显示多久（毫秒）。启动时几个阶段会在极短时间内连续触发，
# 不设下限的话用户只看得到最后一句（其余都被覆盖）。这个值让每句都有存在感，
# 又不至于把启动拖慢 —— 5 个阶段满打满算也就多等 1 秒出头。
_MIN_DWELL_MS = 260
# 收尾模式下每句的停留时间（略短于正常，但要看清节点一个个亮起来）
_FINISH_DWELL_MS = 170
# 收尾时节点全部走完后，再停多久才淡出（让「6/6」有存在感）
_FINISH_HOLD_MS = 200

# 阶段文案（进度值, 文字）。
# 这 5 句对应真实启动阶段，**不要出现重复文案**——重复的会被后一句覆盖，
# 用户永远看不到（历史上「正在加载数据…」写过三次就是这个问题）。
# 另外每句都得是「封面已经显示之后」才设的，否则同样看不到（见 main.py 的调用点）。
# 设备连接发生在主界面显示之后，不属于启动封面阶段，别往这里加。
_STAGES = [
    (0.15, '正在加载数据…'),
    (0.40, '正在初始化界面…'),
    (0.65, '正在准备主界面…'),
    (0.85, '正在整理界面…'),
    (1.00, '即将就绪…'),
]

# 版本号（右上角，与旧 Tcl/Tk 封面同一位置/颜色/字号比例）
_VERSION_ANCHOR = (0.94, 0.11)   # 右边缘 / 上边缘（相对底图宽/高）
_VERSION_COLOR = QColor('#98A2B3')
_VERSION_SIZE_RATIO = 0.035      # 字号占底图高的比例（400*0.035=14px）


def _app_version() -> str:
    """读版本号（单点来源 utils/version.py）。失败返回空串（封面就不画版本号）。"""
    try:
        from utils.version import APP_VERSION
        return str(APP_VERSION).strip()
    except Exception:
        return ''


def _to_px(win, rx: float, ry: float) -> QPointF:
    """轨迹用的相对坐标 → 窗口像素坐标（底图尺寸变了也不用改常量）。"""
    return QPointF(win._w * rx, win._h * ry)


class SplashWindow(QWidget):
    """自绘启动封面：底图 + 进度条 + 阶段文案，带淡出。"""

    def __init__(self, image_path: str):
        super().__init__(None)
        # 无边框 + 置顶 + 不抢焦点 + 不进任务栏
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.Tool
        )
        # 圆角要靠透明背景：否则圆角外面会露出一块不透明的直角底
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        self._pixmap = QPixmap(image_path)
        if self._pixmap.isNull():
            self._pixmap = QPixmap(640, 400)
            self._pixmap.fill(QColor('#FDFDFD'))
        self._w = self._pixmap.width()
        self._h = self._pixmap.height()
        self.setFixedSize(self._w, self._h)

        self._progress = 0.0        # 0.0 ~ 1.0
        self._target = 0.0          # 目标值，主程序只调 set_stage，这里平滑追上去
        # 起点先给一句占位文案，并让它停留一个正常阶段的时长：否则封面刚出现
        # 就被第一句阶段顶成 2/6，用户永远看不到轨迹的第一格（2026-11-18 反馈）。
        # 注意别写成 _STAGES[0][1]：那样 set_stage 会判定「和当前一样」而跳过，
        # 真正的第一句就永远显示不出来（老坑）。
        self._label = '正在启动…'
        self._closing = False
        # finish() 用：完成回调 + 已等待毫秒（见 _poll_finish）
        self._finish_cb = None
        self._reveal_cb = None
        self._finish_waited_ms = 0
        self._finish_timer = None
        # 阶段排队：主程序给的阶段常常挤在一瞬间发出来（真实工作分不出那么多停顿），
        # 直接设上去后面的会盖掉前面的、用户只看得到最后一句。这里改成队列，
        # 每句至少显示 _MIN_DWELL_MS 再换下一句。
        self._pending = []          # [(text, progress), ...]
        self._shown_at = time.monotonic() * 1000.0   # 占位文案「正在启动…」的显示时刻
        self._finishing = False     # finish() 已调用（收尾模式：加快阶段切换）
        self._drained_at = 0.0      # 收尾时节点全部亮完的时刻（再停一段才淡出）
        self._pump = QTimer(self)
        self._pump.setInterval(30)
        self._pump.timeout.connect(self._pump_stage)
        self._pump.start()

        # 进度平滑推进：主程序给的是离散的阶段值，这里补间成连续动画
        self._tick = QTimer(self)
        self._tick.setInterval(16)
        self._tick.timeout.connect(self._on_tick)
        self._tick.start()

        self._fade = QPropertyAnimation(self, b'windowOpacity', self)

        self._center_on_screen()

    # ---------- 对外接口 ----------
    def set_stage(self, text: str, progress: float | None = None) -> None:
        """更新阶段文案与进度。

        文案与进度**一起排队**（见 _pump_stage）：
        * 主程序是同步连续调用 set_stage 的 —— 5 句几乎在同一毫秒发出来。直接设上去
          后一句会盖掉前一句，用户只看得到最后一句。
        * 进度也不能立刻生效：那样会被最后一句顶到 100%，而文案还在队列里慢慢放，
          观感就变成「文案写正在初始化界面，进度条已经快满了」。
        排进队列、轮到某句时才一起把文案和进度顶上去，两者始终对得上。
        """
        if progress is None:
            progress = self._progress
            for p, t in _STAGES:
                if t == text:
                    progress = p
                    break
        target = max(self._target, min(1.0, float(progress)))
        self._target = target

        # 同一句已经在显示或已在队尾，就不重复排队
        if text == self._label or (self._pending and self._pending[-1][0] == text):
            self.update()
            return
        self._pending.append((text, target))
        # 不在调用线程里直接推进队列：主程序是同步连续调 set_stage 的，中间那些
        # 「初始化界面」的重活会把事件循环堵死，等它放行时时间已经过去好几秒 ——
        # 直接推进就会连着顶掉好几格，表现为「1 到 4 瞬间走完」（2026-11-18 反馈）。
        # 统一交给 _pump 定时器按固定节奏放，每格才有相同的可见时长。
        self.update()

    def _pump_stage(self) -> None:
        """队列驱动：每句至少显示 _MIN_DWELL_MS，之后再换下一句。

        收尾模式（finish 已调用）下停留时间缩到 _FINISH_DWELL_MS，把剩下几句
        快速闪一遍，不至于让收尾拖太久。
        """
        import time as _time
        if self._closing:
            return
        dwell = _FINISH_DWELL_MS if self._finishing else _MIN_DWELL_MS
        now = _time.monotonic() * 1000.0
        if self._shown_at and (now - self._shown_at) < dwell:
            return
        if not self._pending:
            return
        text, progress = self._pending.pop(0)
        self._label = text
        # 进度跟着这一句一起到位（不再做平滑补间，见 set_stage 的说明）
        self._progress = max(self._progress, min(1.0, progress))
        self._shown_at = now
        self.update()

    def _drain_stages(self) -> None:
        """把队列里剩下的阶段立刻全部走完（收尾时用，别让 finish 被队列拖住）。"""
        if self._pending:
            self._label = self._pending[-1][0]
            self._pending.clear()
        self._shown_at = 0
        self.update()

    def set_progress(self, value: float) -> None:
        """只推进度，不改文案。"""
        self._target = max(self._progress, min(1.0, float(value)))
        self.update()

    def fade_out(self, duration: int = 260, on_done=None) -> None:
        """淡出后自行关闭；on_done 在关闭后回调（用于接主窗口显示）。"""
        if self._closing:
            return
        self._closing = True
        self._tick.stop()
        self._pump.stop()
        self._fade.stop()
        self._fade.setDuration(duration)
        self._fade.setStartValue(float(self.windowOpacity() or 1.0))
        self._fade.setEndValue(0.0)
        self._fade.setEasingCurve(QEasingCurve.Type.InQuad)
        if on_done:
            self._fade.finished.connect(lambda: (self.close(), on_done()))
        else:
            self._fade.finished.connect(self.close)
        self._fade.start()

    def finish(self, on_done=None, on_reveal=None) -> None:
        """收尾：直接跳到末句、补满进度，停一小段后淡出。

        为什么不再把队列里没展示的阶段逐句回放：收尾时主窗口可能已经/即将
        显示，若此时封面还停在「正在加载数据…」，看起来就是「主界面已经出来了、
        封面还在加载」并且叠在一起。工作已经完成，直接显示「即将就绪…」更合理。
        on_reveal 在设置末句后同步回调；on_done 在封面完全关闭后回调。
        启动流程用 on_done 显示主窗口 —— 避免启动阶段主窗口提前露在封面四周时，
        Windows 把底部状态栏挂到任务栏下面。
        """
        self._finish_cb = on_done
        self._reveal_cb = on_reveal
        self._finishing = True
        self._target = 1.0
        # 不再直接跳到满进度：没走到的节点按收尾节奏一句句点亮，走完才淡出。
        # 之前这里 clear 队列 + _progress=1.0，表现为「2/6 瞬间变 6/6」（2026-11-18 反馈）。
        if not self._pending:
            self._pending = [(t, p) for p, t in _STAGES
                             if p > self._progress + 1e-6]
        # 注意**不要**把 _shown_at 清零：清了会让当前这句立刻被下一句顶掉，
        # 表现为「2/6 只闪 30ms」（实测过）。保留原值，当前句走完它的停留时间
        # 再换下一句，节奏才均匀。
        self._drained_at = 0.0
        self.update()

        # 主窗口立即显示。封面是置顶且不透明的窗口，会一直盖在上面，用户看不到
        # 底下的切换；而收尾定时器可能被启动后期的大量初始化任务饿住（实测几秒
        # 才轮到下一次 tick），等定时器再 reveal 会让主窗口迟迟不出现。
        self._reveal_now()

        self._finish_waited_ms = 0
        self._finish_timer = QTimer(self)
        self._finish_timer.setInterval(30)
        self._finish_timer.timeout.connect(self._poll_finish)
        self._finish_timer.start()

    def _reveal_now(self) -> None:
        """回调主窗口显示（只回调一次；finish 正常路径已调用，这里是兜底）。"""
        cb = getattr(self, "_reveal_cb", None)
        self._reveal_cb = None
        if cb:
            try:
                cb()
            except Exception:
                pass

    def _poll_finish(self) -> None:
        """等剩余节点走完，再停 _FINISH_HOLD_MS 让用户看清「即将就绪」，然后淡出。

        主窗口在 finish() 里已同步显示，封面置顶盖着，用户看不到底下的切换。
        """
        if self._pending:
            return                      # 队列还没走完，继续一个个点亮
        if self._progress < 1.0:
            self._progress = 1.0
            self._label = _STAGES[-1][1]
            self.update()
        if not self._drained_at:
            self._drained_at = time.monotonic()
            return
        if (time.monotonic() - self._drained_at) * 1000.0 < _FINISH_HOLD_MS:
            return
        self._finish_timer.stop()
        # 兜底：若 finish 里的同步显示没有执行（例如对象状态异常），这里补一次
        self._reveal_now()
        self.fade_out(260, getattr(self, '_finish_cb', None))

    # ---------- 内部 ----------
    def _center_on_screen(self) -> None:
        # 用屏幕工作区居中（多屏取主屏）；没 screen 时退化为左上角
        screen = None
        try:
            from PyQt6.QtGui import QGuiApplication
            screen = QGuiApplication.primaryScreen()
        except Exception:
            pass
        if screen is None:
            self.move(100, 100)
            return
        geo = screen.availableGeometry()
        self.move(geo.x() + (geo.width() - self._w) // 2,
                  geo.y() + (geo.height() - self._h) // 2)

    def _on_tick(self) -> None:
        """进度补间已废弃 —— 现在进度随阶段文案一步到位（见 set_stage / _pump_stage）。
        保留一个空实现只为兼容可能存在的旧引用（定时器仍在跑，开销可忽略）。"""
        return

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        # 整窗裁成圆角：四角之外保持透明（配合 WA_TranslucentBackground），
        # 后面所有绘制（版本号 / 轨迹 / 文案）都自动被裁在里面
        radius = max(6.0, self._w * _CORNER_RADIUS_RATIO)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, self._w, self._h), radius, radius)
        p.setClipPath(clip)
        p.drawPixmap(0, 0, self._pixmap)

        # ---- 版本号（右上角） ----
        ver = _app_version()
        if ver:
            font = QFont('Microsoft YaHei')
            font.setPixelSize(max(10, round(self._h * _VERSION_SIZE_RATIO)))
            p.setFont(font)
            p.setPen(_VERSION_COLOR)
            fm = p.fontMetrics()
            text = f'v{ver}'
            ax, ay = _VERSION_ANCHOR
            # 右边缘对齐 w*ax，上边缘对齐 h*ay（drawText 的 y 是基线，需 +ascent）
            p.drawText(int(self._w * ax - fm.horizontalAdvance(text)),
                       int(self._h * ay + fm.ascent()), text)

        # ---- 轨迹：走过的线段 + 点亮的节点 ----
        lit = self._lit_count()
        pen = QPen(_TRACE_COLOR)
        pen.setWidthF(max(1.5, self._w * _SEG_WIDTH_RATIO))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for i in range(min(lit - 1, len(_SEGS))):
            p0, p1, p2, p3 = (_to_px(self, *pt) for pt in _SEGS[i])
            path = QPainterPath(p0)
            path.cubicTo(p1, p2, p3)
            p.drawPath(path)

        p.setPen(Qt.PenStyle.NoPen)
        r = max(2.5, self._w * _NODE_R_RATIO)
        for i, (nx, ny) in enumerate(_NODES):
            if i > lit - 1:
                break
            cx, cy = self._w * nx, self._h * ny
            if i == lit - 1 and lit < len(_NODES):
                # 当前节点：先画一圈淡蓝光晕，再压一个实心点
                p.setBrush(_HALO_COLOR)
                p.drawEllipse(QPointF(cx, cy), r * 2.3, r * 2.3)
                p.setBrush(_NODE_CURRENT)
            else:
                p.setBrush(_NODE_DONE)
            p.drawEllipse(QPointF(cx, cy), r, r)

        # 全部走完时把终点方块填成实心：底图上它是空心的白框，
        # 只在收尾那一瞬间被填满，作为"到达"的收束
        if lit >= len(_NODES):
            cx, cy = self._w * _NODES[-1][0], self._h * _NODES[-1][1]
            half = max(6.0, self._w * 0.0125)
            p.setBrush(_NODE_DONE)
            p.drawRoundedRect(QRectF(cx - half, cy - half, half * 2, half * 2),
                              r * 0.7, r * 0.7)

        # ---- 阶段文案（左下） + 节点计数（右下） ----
        font = QFont('Microsoft YaHei')
        font.setPixelSize(max(10, round(self._h * _LABEL_SIZE_RATIO)))
        p.setFont(font)
        fm = p.fontMetrics()
        x = self._w * _LABEL_MARGIN_X
        y = int(self._h * _LABEL_Y)
        if self._label:
            p.setPen(_LABEL_COLOR)
            p.drawText(int(x), y, self._label)

        p.setPen(_COUNT_COLOR)
        count_text = f'{lit} / {len(_NODES)}'
        p.drawText(int(self._w - x - fm.horizontalAdvance(count_text)),
                   y, count_text)

        # 最后描一圈极淡的边：白底封面放在浅色桌面上时，圆角轮廓才看得出来。
        # 关掉裁剪再画，线才不会在圆角处被削掉一半。
        p.setClipping(False)
        p.setPen(QPen(_EDGE_COLOR, 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(0.5, 0.5, self._w - 1, self._h - 1),
                          radius, radius)
        p.end()

    def _lit_count(self) -> int:
        """已点亮的节点数（1 = 只有起点）。阶段阈值与底图节点一一对应。"""
        lit = 1
        for threshold, _text in _STAGES:
            if self._progress >= threshold - 1e-6:
                lit += 1
        return max(1, min(lit, len(_NODES)))

    def closeEvent(self, event):
        self._tick.stop()
        self._pump.stop()
        super().closeEvent(event)
