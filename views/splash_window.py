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
from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QPainter, QColor, QFont, QPixmap, QLinearGradient
from PyQt6.QtWidgets import QWidget

# ---------- 视觉参数（都相对底图尺寸，换底图不用改） ----------
_BAR_MARGIN_X = 0.044      # 进度条左端距左边，28/640
_BAR_WIDTH = 0.34          # 进度条长度占底图宽的比例（左下留白区，到波浪左边界之前收住）
_BAR_Y = 0.905             # 进度条纵向位置（中心）378/400 附近
_BAR_HEIGHT = 3            # 进度条粗细（像素）
_BAR_TRACK = QColor(226, 232, 240)          # 底槽：浅灰 #E2E8F0
_BAR_GRADIENT = (QColor('#54A8FF'), QColor('#2B7FFF'))  # 填充：品牌蓝渐变
_LABEL_COLOR = QColor('#98A2B3')            # 阶段文案：与版本号同一个灰
_LABEL_SIZE_RATIO = 0.030                   # 文案字号占底图高的比例（400*0.03=12px）
_LABEL_GAP = 16                             # 文案在进度条上方多少像素

# 每个阶段文案至少显示多久（毫秒）。启动时几个阶段会在极短时间内连续触发，
# 不设下限的话用户只看得到最后一句（其余都被覆盖）。这个值让每句都有存在感，
# 又不至于把启动拖慢 —— 5 个阶段满打满算也就多等 1 秒出头。
_MIN_DWELL_MS = 260
# 收尾模式下每句的停留时间（略短于正常，让剩余阶段快速但看得清地闪完）
_FINISH_DWELL_MS = 180

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
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
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
        # 初值留空：第一句阶段文案（'正在加载数据…'）要靠 set_stage 排进队列正常显示。
        # 若这里就设成 _STAGES[0][1]，set_stage 会因「和当前一样」判定为重复而跳过，
        # 用户就永远看不到第一句（曾踩过这个坑）。
        self._label = ''
        self._closing = False
        # finish() 用：完成回调 + 已等待毫秒（见 _poll_finish）
        self._finish_cb = None
        self._finish_waited_ms = 0
        self._finish_timer = None
        # 阶段排队：主程序给的阶段常常挤在一瞬间发出来（真实工作分不出那么多停顿），
        # 直接设上去后面的会盖掉前面的、用户只看得到最后一句。这里改成队列，
        # 每句至少显示 _MIN_DWELL_MS 再换下一句。
        self._pending = []          # [(text, progress), ...]
        self._shown_at = 0          # 当前这句是什么时候显示上去的（单调时钟 ms）
        self._finishing = False     # finish() 已调用（收尾模式：加快阶段切换）
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
        self._pump_stage()   # 若当前空闲，立刻顶上，别等下一个 tick

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

    def finish(self, on_done=None) -> None:
        """收尾：把队列里剩的阶段快速走完，补到 100%，再淡出。

        要点：
        * 队列里可能还有没轮到的阶段（比如「正在整理界面…」刚设上就收尾了）。
          这里不直接丢弃 —— 而是进入「收尾模式」，让 _pump_stage 把停留时间
          从 _MIN_DWELL_MS 缩到 _FINISH_DWELL_MS，快速把每句都闪一遍。
          全丢弃的话用户会漏看阶段；按正常停留又太拖，折中。
        * 等到队列走空，再补满进度、显示「即将就绪…」、停 200ms 后淡出。
        """
        self._finish_cb = on_done
        self._finishing = True
        self._target = 1.0
        # 确保末句在队列里（若还没排过）
        if not self._pending and self._label != _STAGES[-1][1]:
            self._pending.append((_STAGES[-1][1], 1.0))
        self._pump_stage()

        self._finish_timer = QTimer(self)
        self._finish_timer.setInterval(30)
        self._finish_timer.timeout.connect(self._poll_finish)
        self._finish_timer.start()

    def _poll_finish(self) -> None:
        """等队列走空 + 留一小段停顿后淡出。

        注：这个定时器要等 main() 跑到 app.exec() 才真正开始走（之前是同步代码），
        所以实际停留时间会比这里设的略长一点 —— 观感上正好，不必刻意补偿。
        """
        # 队列还没走完，先让它继续（_pump_stage 在收尾模式下会加速）
        if self._pending:
            self._finish_waited_ms = 0
            self._pump_stage()
            return
        # 队列空了：补满进度 + 显示末句
        self._progress = 1.0
        self._label = _STAGES[-1][1]
        self.update()
        self._finish_waited_ms += 30
        if self._finish_waited_ms < 200:
            return
        self._finish_timer.stop()
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
        p.drawPixmap(0, 0, self._pixmap)

        # ---- 版本号（右上角，与旧 Tcl/Tk 封面同位置） ----
        ver = _app_version()
        if ver:
            font = QFont('Microsoft YaHei')
            font.setPixelSize(max(10, round(self._h * _VERSION_SIZE_RATIO)))
            p.setFont(font)
            p.setPen(_VERSION_COLOR)
            fm = p.fontMetrics()
            text = f'V{ver}'
            ax, ay = _VERSION_ANCHOR
            # 右边缘对齐 w*ax，上边缘对齐 h*ay（drawText 的 y 是基线，需 +ascent）
            p.drawText(int(self._w * ax - fm.horizontalAdvance(text)),
                       int(self._h * ay + fm.ascent()), text)

        # ---- 进度条 ----
        bar_x = self._w * _BAR_MARGIN_X
        bar_y = self._h * _BAR_Y
        bar_w = self._w * _BAR_WIDTH
        r = _BAR_HEIGHT / 2

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_BAR_TRACK)
        p.drawRoundedRect(int(bar_x), int(bar_y - r), int(bar_w), _BAR_HEIGHT, r, r)

        fill_w = max(0.0, min(1.0, self._progress)) * bar_w
        if fill_w >= _BAR_HEIGHT:
            grad = QLinearGradient(bar_x, 0, bar_x + bar_w, 0)
            grad.setColorAt(0.0, _BAR_GRADIENT[0])
            grad.setColorAt(1.0, _BAR_GRADIENT[1])
            p.setBrush(grad)
            p.drawRoundedRect(int(bar_x), int(bar_y - r), int(fill_w), _BAR_HEIGHT, r, r)

        # ---- 阶段文案（进度条上方） ----
        if self._label:
            font = QFont('Microsoft YaHei')
            font.setPixelSize(max(10, round(self._h * _LABEL_SIZE_RATIO)))
            p.setFont(font)
            p.setPen(_LABEL_COLOR)
            fm = p.fontMetrics()
            # 与进度条左端对齐（比底图原有 text_pos 的 28px 略靠左，和进度条同一起点）
            p.drawText(int(bar_x), int(bar_y - _LABEL_GAP),
                       self._label)
        p.end()

    def closeEvent(self, event):
        self._tick.stop()
        self._pump.stop()
        super().closeEvent(event)
