# utils/win_dark_title.py
"""Windows 平台让应用标题栏跟随深色/浅色主题。"""
import sys


def set_dark_title_bar(window, dark: bool):
    """设置 Windows 原生标题栏为深色或浅色。
    仅在 Windows 平台生效，其他平台静默忽略。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        hwnd = int(window.winId())

        # DWMWA_USE_IMMERSIVE_DARK_MODE:
        #   20 是 Windows 10 20H1+ 的属性号
        #   19 是旧版本（Win10 1809~1909）
        DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19

        value = ctypes.c_int(1 if dark else 0)

        dwmapi = ctypes.windll.dwmapi
        res = dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd),
            ctypes.c_uint(DWMWA_USE_IMMERSIVE_DARK_MODE),
            ctypes.byref(value),
            ctypes.sizeof(value),
        )
        if res != 0:
            # 回退到旧属性号
            dwmapi.DwmSetWindowAttribute(
                wintypes.HWND(hwnd),
                ctypes.c_uint(DWMWA_USE_IMMERSIVE_DARK_MODE_OLD),
                ctypes.byref(value),
                ctypes.sizeof(value),
            )

        # 强制重绘标题栏
        try:
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            SWP_NOZORDER = 0x0004
            SWP_FRAMECHANGED = 0x0020
            ctypes.windll.user32.SetWindowPos(
                hwnd, 0, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED,
            )
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