# views/splash_window.py
"""启动封面窗口。

为什么要自己画（而不是用 PyInstaller 的 Splash / Tcl-Tk）：
  PyInstaller 的启动封面由 bootloader 用 Tcl/Tk 渲染，只能在固定的一个位置
  画**一行文字**（text_pos），做不了细进度条、做不了分阶段文案的淡入淡出、
  也做不了淡出过渡。所以这里改成：底图仍在打包时由 main.spec 画好版本号，
  界面则交给这个 Qt 窗口自己绘制 —— 进度条、阶段文案、淡出都能自由控制。

代价：封面不再是「解释器启动前」就出现，而是 QApplication 建好之后才显示，
  真正被盖住的是模型初始化 + 主界面构建这一段（大约几秒）。模块导入那几秒
  由 main.spec 里的 Splash 兜底（见 main.spec 的说明），两者可以并存。

设计要点：
  * 无边框 + 置顶 + 不进任务栏，避免抢焦点、避免在任务栏多出一个按钮；
  * 用 windowOpacity 做淡出 —— QGraphicsOpacityEffect 对顶层窗口不生效
    （见 utils/fx.py 里 scale_in 的同类问题）；
  * 进度条在底图左下留白处自绘，不依赖任何外部素材。
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

# 阶段文案（进度值, 文字）—— 主程序按实际进度调用 set_stage()
_STAGES = [
    (0.10, '正在加载数据…'),
    (0.35, '正在初始化界面…'),
    (0.62, '正在准备主界面…'),
    (0.88, '正在连接设备…'),
    (1.00, '即将就绪…'),
]


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
        self._label = _STAGES[0][1]
        self._closing = False
        # finish() 用：完成回调 + 已等待毫秒（见 _poll_finish）
        self._finish_cb = None
        self._finish_waited_ms = 0
        self._finish_timer = None

        # 进度平滑推进：主程序给的是离散的阶段值，这里补间成连续动画
        self._tick = QTimer(self)
        self._tick.setInterval(16)
        self._tick.timeout.connect(self._on_tick)
        self._tick.start()

        self._fade = QPropertyAnimation(self, b'windowOpacity', self)

        self._center_on_screen()

    # ---------- 对外接口 ----------
    def set_stage(self, text: str, progress: float | None = None) -> None:
        """更新阶段文案与进度；progress 为 None 时按内置阶段表反查。"""
        self._label = text
        if progress is None:
            progress = self._progress
            for p, t in _STAGES:
                if t == text:
                    progress = p
                    break
        self._target = max(self._progress, min(1.0, float(progress)))
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
        """把进度补到 100%，**等它真正走完**再淡出。

        不能只等固定时长：进度是用 _on_tick 平滑追赶的（每次靠近 12%），
        从 60% 爬到 100% 需要十几帧。固定等 220ms 的话进度条才走到七八成就切走了，
        用户看到的是「进度条没到头，首页已经打开」。这里改成轮询，
        每一帧检查是否到顶，到顶后再留一小段停顿才淡出。
        """
        self._target = 1.0
        self._label = _STAGES[-1][1]
        self._finish_cb = on_done
        self._finish_waited_ms = 0
        # 立即强制推到 100%（跳过平滑补间）：否则用户会看到进度条慢慢爬，
        # 而首页其实早就建好了，反而显得拖沓。这里给一个「瞬间补满 + 短暂停留」的观感。
        self._progress = 1.0
        self.update()

        self._finish_timer = QTimer(self)
        self._finish_timer.setInterval(30)
        self._finish_timer.timeout.connect(self._poll_finish)
        self._finish_timer.start()

    def _poll_finish(self) -> None:
        """留一小段停顿让用户看清「100% + 即将就绪」，然后淡出。

        注：这个定时器要等 main() 跑到 app.exec() 才真正开始走（之前是同步代码），
        所以实际停留时间会比这里设的略长一点 —— 观感上正好，不必刻意补偿。
        """
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
        # 缓动追赶目标值，视觉上不会一格一格跳
        if abs(self._target - self._progress) < 0.001:
            return
        self._progress += (self._target - self._progress) * 0.12
        if abs(self._target - self._progress) < 0.002:
            self._progress = self._target
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.drawPixmap(0, 0, self._pixmap)

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
        super().closeEvent(event)
