# utils/win_dark_title.py
"""Windows 平台让应用标题栏跟随深色/浅色主题。"""
import sys

# Win11 显式标题栏配色（COLORREF，0x00BBGGRR）
_CAPTION_DARK = 0x00202020
_TEXT_DARK = 0x00E8E8E8
_DEFAULT = 0xFFFFFFFF


def set_dark_title_bar(window, dark: bool, delayed: bool = True, dark_getter=None):
    """设置 Windows 原生标题栏为深色或浅色。
    仅在 Windows 平台生效，其他平台静默忽略。

    `delayed=False` 时只设一次、不排补设 —— 给「窗口激活」这类高频事件用，
    避免每次切窗口都堆五个定时器。

    **为什么要补设**：
    切主题时 apply_theme() 会对整棵控件树重设 QSS + 重画壁纸，界面复杂时
    重绘会持续 1~2 秒；DWM 在重绘/非客户区重建后按系统主题重画标题栏，
    把刚设的属性冲掉。

    **补设为什么要读「当前主题」而不是捕获值**：
    补设是排在未来几秒执行的定时器。若期间用户又切了一次主题（或快速来回切），
    先排上的旧定时器会把**旧主题**再写一遍，最后生效的反而是旧值 ——
    界面已经变了、标题栏还停在另一个颜色上。传 dark_getter 后每次补设都按
    触发时刻的真实主题决定写 0 还是 1，旧定时器不会再把状态写反。
    """
    if sys.platform != "win32":
        return
    _apply_dark_title_bar(window, dark)
    if not delayed:
        return
    try:
        from PyQt6.QtCore import QTimer
        # 补设五次（2026-11-01 加三个 → 2026-11-13 加五个）。
        # 一次 220ms 在空壳窗口上够用，但真实使用场景下界面复杂、重绘更久，
        # 中途的补设照样会被冲掉。时间点覆盖 re-polish + 重画壁纸（实测能到
        # 2~3 秒）结束前后，多打几次 DWM 调用无副作用。
        for delay in (220, 700, 1600, 2600, 4000):
            QTimer.singleShot(delay,
                              lambda d=dark: _apply_dark_title_bar(
                                  window, _resolve_dark(dark_getter, d)))
    except Exception:
        pass


def _resolve_dark(dark_getter, fallback: bool) -> bool:
    """补设时优先读调用方给的「当前主题」，读不到再退回捕获值。"""
    if dark_getter is None:
        return fallback
    try:
        return bool(dark_getter())
    except Exception:
        return fallback


def _is_win11() -> bool:
    """Windows 11（build 22000+）才支持 DWMWA_CAPTION_COLOR / TEXT_COLOR。"""
    try:
        return sys.getwindowsversion().build >= 22000
    except Exception:
        return False


def _apply_dark_title_bar(window, dark: bool):
    """真正调用 DWM API 的那一层（立即调用 / 延迟补设共用）。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        try:
            hwnd = int(window.winId())
        except (RuntimeError, ValueError):
            return                      # 窗口已销毁（延迟补设时可能发生）
        if not hwnd or not ctypes.windll.user32.IsWindow(hwnd):
            return

        dwmapi = ctypes.windll.dwmapi
        user32 = ctypes.windll.user32

        # 显式声明签名：64 位下 HWND 是 8 字节指针，ctypes 不声明时按 c_int
        # 传参会把高 32 位截掉（本项目内存统计工具踩过同类坑）。
        try:
            dwmapi.DwmSetWindowAttribute.argtypes = [
                wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
            dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long
        except Exception:
            pass

        # DWMWA_USE_IMMERSIVE_DARK_MODE:
        #   20 是 Windows 10 20H1+ 的属性号
        #   19 是旧版本（Win10 1809~1909）
        DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19
        DWMWA_CAPTION_COLOR = 35
        DWMWA_TEXT_COLOR = 36

        value = ctypes.c_int(1 if dark else 0)
        res = dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd), DWMWA_USE_IMMERSIVE_DARK_MODE,
            ctypes.byref(value), ctypes.sizeof(value),
        )
        if res != 0:
            # 回退到旧属性号
            dwmapi.DwmSetWindowAttribute(
                wintypes.HWND(hwnd), DWMWA_USE_IMMERSIVE_DARK_MODE_OLD,
                ctypes.byref(value), ctypes.sizeof(value),
            )

        # **不要删这一段**（2026-11-13 实机验证）：
        # 上面的属性 20 在部分机器上「设了返回 0、读回也是 1，但标题栏像素
        # 仍是 (245,243,238) 浅色」—— Win11 25H2 上必然出现，光靠它不管设
        # 几次都无效。只有显式给 CAPTION_COLOR / TEXT_COLOR 才真正改色。
        if _is_win11():
            try:
                cap = ctypes.c_uint(_CAPTION_DARK if dark else _DEFAULT)
                dwmapi.DwmSetWindowAttribute(
                    wintypes.HWND(hwnd), DWMWA_CAPTION_COLOR,
                    ctypes.byref(cap), ctypes.sizeof(cap),
                )
                # 亮色传 DEFAULT 恢复系统默认（保留原生 Mica 观感）
                txt = ctypes.c_uint(_TEXT_DARK if dark else _DEFAULT)
                dwmapi.DwmSetWindowAttribute(
                    wintypes.HWND(hwnd), DWMWA_TEXT_COLOR,
                    ctypes.byref(txt), ctypes.sizeof(txt),
                )
            except Exception:
                pass

        # 只让非客户区失效、由系统异步重绘。
        # 不要用 SetWindowPos(..., SWP_FRAMECHANGED)：它会同步发送 WM_NCCALCSIZE，
        # 在「设置窗口正在 exec()/主窗口被禁用」的模态切换过程中会死锁
        # （用户实测：打开设置 → 确定 → 再打开设置，进程卡在 SetWindowPos）。
        try:
            RDW_INVALIDATE = 0x0001
            RDW_FRAME = 0x0400
            user32.RedrawWindow(hwnd, None, None, RDW_INVALIDATE | RDW_FRAME)
        except Exception:
            pass
    except Exception as e:
        print(f"[win_dark_title] 设置标题栏失败: {e}")


def disable_min_max_buttons(window):
    """通过 Win32 原生 API 去掉最小化和最大化按钮（Qt flag 在部分系统上不生效时的兜底）。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        hwnd = int(window.winId())
        GWL_STYLE = -16
        WS_MINIMIZEBOX = 0x00020000
        WS_MAXIMIZEBOX = 0x00010000

        user32 = ctypes.windll.user32
        style = user32.GetWindowLongW(wintypes.HWND(hwnd), GWL_STYLE)
        style &= ~WS_MINIMIZEBOX
        style &= ~WS_MAXIMIZEBOX
        user32.SetWindowLongW(wintypes.HWND(hwnd), GWL_STYLE, style)

        # 强制重绘标题栏
        SWP_NOMOVE = 0x0002
        SWP_NOSIZE = 0x0001
        SWP_NOZORDER = 0x0004
        SWP_FRAMECHANGED = 0x0020
        user32.SetWindowPos(
            hwnd, 0, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED,
        )
    except Exception as e:
        print(f"[win_dark_title] 禁用最小化/最大化失败: {e}")
