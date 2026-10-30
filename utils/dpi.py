# utils/dpi.py
"""分辨率 / 高 DPI 适配的公共尺寸工具。

**要解决什么问题**（「扩展方向」第 5 项）：界面里写死了不少逻辑像素尺寸，
在 1366×768@150%（逻辑 910×512）这类环境下，弹窗会顶出屏幕、底部「确定」
点不到；页面也会因为最小宽度过大而被挤压。

三条约定，新增代码请照此写：
1. 弹窗尺寸不要直接写死 `resize(820, 520)`，改用
   `fit_to_screen(self, 820, 520)` —— 大屏上仍是 820×520，小屏自动收进屏幕；
2. **不要用 `setFixedSize`** 给窗口定死尺寸：固定尺寸会顶掉后续的 `resize()`
   修正（Qt 会强制回到固定值），跨分辨率下就没救了。用 `resize` + `setMinimumSize`；
3. 只要 `install_auto_fit(app)` 装过一次，任何 QDialog 显示时都会自动收进屏幕 —
   旧弹窗不用逐个改也能兜住，新弹窗天然受保护。

为什么用事件过滤器而不是逐个改：全库有十几个写死尺寸的弹窗，逐个改容易漏；
而「显示时检查一下」这件事对所有 QDialog 都成立，一处生效、将来也生效。
"""
import logging

from PyQt6.QtCore import QEvent, QObject, QTimer
from PyQt6.QtWidgets import QApplication, QDialog

logger = logging.getLogger(__name__)

# 收进屏幕时留的余量：宽度留 8%、高度留 12%（底下任务栏 / Dock 常占一截）
W_RATIO = 0.92
H_RATIO = 0.88


def avail_rect(widget=None):
    """当前屏幕的可用区域（逻辑像素，已扣掉任务栏）。

    传了 widget 就优先用它所在的屏幕（多屏时别把窗口推到另一块屏上）。
    拿不到屏幕时返回 None，调用方按「不做限制」处理。
    """
    screen = None
    if widget is not None:
        try:
            screen = widget.screen()
        except Exception:            # noqa: BLE001 - 拿不到就退回主屏
            screen = None
    if screen is None:
        screen = QApplication.primaryScreen()
    return screen.availableGeometry() if screen is not None else None


def fit_to_screen(widget, design_w=None, design_h=None, center=True):
    """把窗口收进屏幕可用区（只缩不放），返回最终 (宽, 高)。

    :param design_w/h: 设计尺寸；不传则用窗口当前尺寸。
    :param center: 收完后是否居中到可用区（默认居中，避免收缩后卡在屏幕边缘）。
    """
    avail = avail_rect(widget)
    cur_w = int(design_w if design_w is not None else widget.width())
    cur_h = int(design_h if design_h is not None else widget.height())
    if avail is None:
        widget.resize(cur_w, cur_h)
        return cur_w, cur_h

    max_w = int(avail.width() * W_RATIO)
    max_h = int(avail.height() * H_RATIO)
    widget.resize(min(cur_w, max_w), min(cur_h, max_h))

    # 布局的最小尺寸可能把窗口顶回来，所以重新读一次实际尺寸再居中
    w, h = widget.width(), widget.height()
    if center:
        x = avail.left() + max(0, (avail.width() - w) // 2)
        y = avail.top() + max(0, (avail.height() - h) // 2)
        widget.move(x, y)
    if (w, h) != (cur_w, cur_h):
        logger.info("窗口收进屏幕：%dx%d → %dx%d（可用 %dx%d）",
                    cur_w, cur_h, w, h, avail.width(), avail.height())
    return w, h


def screen_info():
    """一行文字描述当前屏幕（写进启动日志，排查尺寸问题时有据可查）。"""
    try:
        screen = QApplication.primaryScreen()
        if screen is None:
            return "无屏幕信息"
        geo = screen.geometry()
        avail = screen.availableGeometry()
        dpr = screen.devicePixelRatio()
        return (f"{geo.width()}x{geo.height()} 物理像素 · 缩放 {dpr:.2f} · "
                f"逻辑 {int(geo.width() / dpr)}x{int(geo.height() / dpr)} · "
                f"可用 {avail.width()}x{avail.height()}")
    except Exception as e:           # noqa: BLE001 - 只是日志，不能影响启动
        return f"读取屏幕信息失败：{e}"


class _AutoFitFilter(QObject):
    """应用级兜底：任何 QDialog 显示时，超屏就收进屏幕并居中。

    用 Show 事件而不是构造函数，是为了覆盖「已经写死尺寸」的老弹窗 ——
    它们改不动自己的尺寸，但显示时可以被子类外的这一层修正。
    `setFixedSize` 的窗口修正不了（Qt 会顶回固定值），所以那个别再用。
    """

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Show and isinstance(obj, QDialog):
            # Show 时布局还没算完，延后一拍再量，避免按"还没撑开"的尺寸误判
            QTimer.singleShot(0, lambda d=obj: self._fit(d))
        return False

    @staticmethod
    def _fit(dialog):
        try:
            if not dialog.isVisible():
                return
            avail = avail_rect(dialog)
            if avail is None:
                return
            if (dialog.width() <= int(avail.width() * W_RATIO)
                    and dialog.height() <= int(avail.height() * H_RATIO)):
                return
            fit_to_screen(dialog)
        except Exception:            # noqa: BLE001 - 兜底失败也不能影响弹窗显示
            logger.exception("自动收进屏幕失败")


def install_auto_fit(app):
    """装上应用级兜底过滤器（幂等）。返回过滤器对象，便于测试时移除。"""
    if getattr(app, "_dpi_auto_fit", None) is not None:
        return app._dpi_auto_fit
    flt = _AutoFitFilter(app)
    app.installEventFilter(flt)
    app._dpi_auto_fit = flt
    logger.info("已启用弹窗自动收进屏幕：%s", screen_info())
    return flt
