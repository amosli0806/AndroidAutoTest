# utils/voice_log.py
"""语音播报的统一日志桥：把播报/回执验证的结果发到底部「虫师日志」面板。

为什么单独建一个桥：
    语音播报发生在两条路径上 —— 自动化执行 worker（QThread）里的 voice 步骤，
    以及语音页的 _PlaybackWorker（QThread）。这两处都在后台线程，不能直接碰
    界面控件；而底部「虫师日志」的 append 必须回到主线程做。

    做法与 adb_toolbox_controller.log_emitted 完全一致：一个挂在主线程的
    QObject 单例，后台线程调用 emit()，Qt 用 queued connection 把消息投递回
    主线程，再由 main.py 里连好的 append_bottom_log 落进日志面板。

用法（任何线程）：
    from utils import voice_log
    voice_log.emit("success", "语音验证通过")
    voice_log.emit("error", "语音验证失败：车机反馈「没听清」")

level 用 log_colors 的语义级别：info / success / warning / error / result。
"""
from PyQt6.QtCore import QObject, pyqtSignal

from utils import log_colors


class _VoiceLogBus(QObject):
    """语音日志信号总线。message 携带已按当前主题上色的 HTML 片段。"""
    message = pyqtSignal(str)


_bus = _VoiceLogBus()


def bus() -> _VoiceLogBus:
    """取全局信号总线（main.py 在主线程里 connect 它）。"""
    return _bus


def emit(level: str, text: str):
    """发一条语音日志。level 用 log_colors 语义级别，text 是纯文本（不含 HTML）。"""
    _bus.message.emit(log_colors.span(level, text))
