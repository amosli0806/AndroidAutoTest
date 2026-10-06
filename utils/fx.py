# utils/fx.py
"""界面动效工具：淡入 / 缩放淡入 / 呼吸闪烁。

原则（克制）：
  * 只动 opacity（QGraphicsOpacityEffect）与几何缩放，不动布局尺寸 ——
    避免触发重排（与主窗口切页的 FlowLayout 空排布坑撞车）。
  * 时长统一 150~250ms，缓动 OutCubic。
  * 所有动画对象挂在目标 widget 上，widget 销毁时自动回收，不泄漏。

用法：
  fade_in(widget, ...)          —— 页面/控件出现时淡入
  scale_in(dialog, ...)         —— 对话框出现时缩放 + 淡入（0.96 → 1.0）
  pulse(opacity_effect, ...)    —— 进行态呼吸闪烁（无限循环），返回动画对象供 stop
"""
from PyQt6.QtCore import (
    QEasingCurve, QPropertyAnimation, QPoint, QRect, pyqtSignal,
)
from PyQt6.QtWidgets import QGraphicsOpacityEffect, QWidget

_DURATION = 230  # 默认过渡时长（ms），页面切换淡入


def _ensure_effect(widget: QWidget) -> QGraphicsOpacityEffect:
    """给 widget 挂一个透明度效果（若已有则复用；已有则不动它）。"""
    eff = widget.graphicsEffect()
    if isinstance(eff, QGraphicsOpacityEffect):
        return eff
    eff = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(eff)
    return eff


def fade_in(widget: QWidget, duration: int = _DURATION, offset_y: int = 6,
            on_done=None) -> QPropertyAnimation:
    """淡入 + 轻微上移（页面/分组切换用）。

    offset_y：从下往上浮入的像素距离（0 = 纯淡入）。结束后恢复原位并释放效果，
    避免残留 opacity 效果干扰后续绘制（尤其带阴影/透明背景的控件）。
    """
    eff = _ensure_effect(widget)
    eff.setOpacity(0.0)
    anim = QPropertyAnimation(eff, b"opacity", widget)
    anim.setDuration(duration)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    if offset_y > 0:
        orig_pos = widget.pos()
        start_pos = QPoint(orig_pos.x(), orig_pos.y() + offset_y)
        widget.move(start_pos)
        pos_anim = QPropertyAnimation(widget, b"pos", widget)
        pos_anim.setDuration(duration)
        pos_anim.setStartValue(start_pos)
        pos_anim.setEndValue(orig_pos)
        pos_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        # 两个动画并行：pos_anim 由 Qt 管理生命周期，结束时随 widget 回收
        anim.finished.connect(lambda: _cleanup(widget))
        pos_anim.start()
        anim.start()
        return anim

    anim.finished.connect(lambda: _cleanup(widget))
    anim.start()
    return anim


def _cleanup(widget: QWidget):
    """动画结束后摘掉透明度效果，恢复纯净（避免 opacity 残留 + 与阴影冲突）。"""
    eff = widget.graphicsEffect()
    if isinstance(eff, QGraphicsOpacityEffect):
        widget.setGraphicsEffect(None)
        # 不主动 deleteLater：setGraphicsEffect(None) 已让 effect 脱离 widget，
        # 由 Python 引用计数回收即可；主动 deleteLater 反而可能因 C++ 侧未
        # 同步清空导致 graphicsEffect() 短暂仍返回旧指针。


def scale_in(dialog: QWidget, duration: int = 250, from_scale: float = 0.96,
             on_done=None) -> QPropertyAnimation:
    """对话框出现：缩放 + 淡入（from_scale → 1.0）。

    缩放通过修改窗口 geometry（等比向中心收缩），只动 geometry 不改布局。
    结束恢复原始 geometry 并摘掉透明度效果。
    """
    eff = _ensure_effect(dialog)
    eff.setOpacity(0.0)

    # 记录目标几何，算出收缩后的起始几何（向中心收缩）
    target = dialog.geometry()
    dw = int(target.width() * (1.0 - from_scale))
    dh = int(target.height() * (1.0 - from_scale))
    start = QRect(
        target.x() + dw // 2, target.y() + dh // 2,
        target.width() - dw, target.height() - dh,
    )

    anim = QPropertyAnimation(eff, b"opacity", dialog)
    anim.setDuration(duration)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    dialog.setGeometry(start)
    geo_anim = QPropertyAnimation(dialog, b"geometry", dialog)
    geo_anim.setDuration(duration)
    geo_anim.setStartValue(start)
    geo_anim.setEndValue(target)
    geo_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    anim.finished.connect(lambda: _cleanup(dialog))
    geo_anim.start()
    anim.start()
    return anim


def pulse(effect: QGraphicsOpacityEffect, duration: int = 1100,
          low: float = 0.35, high: float = 1.0) -> QPropertyAnimation:
    """呼吸闪烁（进行态红点/执行中指示用）：opacity 在 low~high 间往复。

    返回的动画要由调用方持有引用（否则被 GC 回收会停），停止用 anim.stop()。
    """
    anim = QPropertyAnimation(effect, b"opacity", effect)
    anim.setDuration(duration)
    anim.setStartValue(high)
    anim.setKeyValueAt(0.5, low)
    anim.setEndValue(high)
    anim.setLoopCount(-1)  # 无限循环
    anim.setEasingCurve(QEasingCurve.Type.InOutSine)
    anim.start()
    return anim


def pulse_widget(widget: QWidget, on: bool, duration: int = 1100,
                 low: float = 0.4) -> None:
    """开关式呼吸闪烁：给整个 widget 挂/摘呼吸效果。

    on=True 时启动无限呼吸（并把动画对象缓存到 widget 属性，防止被 GC 回收）；
    on=False 时停止并摘掉 opacity 效果，恢复纯净。重复调用 on=True 幂等。
    """
    if on:
        # 已在呼吸中就不重复起动画
        existing = getattr(widget, '_pulse_anim', None)
        if existing is not None:
            return
        eff = _ensure_effect(widget)
        eff.setOpacity(1.0)
        anim = pulse(eff, duration=duration, low=low)
        widget._pulse_anim = anim
    else:
        anim = getattr(widget, '_pulse_anim', None)
        if anim is not None:
            anim.stop()
        widget._pulse_anim = None
        _cleanup(widget)
