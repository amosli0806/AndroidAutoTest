# utils/win_dark_title.py
"""Windows 平台让应用标题栏跟随深色/浅色主题。

带一条**标题栏诊断日志**（data/titlebar_trace.log），用于排查
「切到暗夜后窗口顶部仍是浅色」这类偶现问题。每次设置都记录：

  - 期望值 dark / 触发来源 reason / hwnd
  - DwmSetWindowAttribute 的返回值（属性 20 失败会回退 19）
  - DwmGetWindowAttribute 的读回值（验证属性现在到底是多少）
  - 屏幕标题栏位置的实际像素颜色

三者一对就能分清是「属性没设上」「设上了但被 DWM 刷回去」还是
「属性为 1 但渲染与此无关」。

日志默认开启，设环境变量 CHONGSHI_TITLEBAR_TRACE=0 可关闭；
文件超过 256KB 自动轮转一次（旧的改名 .1，只留一份）。
"""
import os
import sys
import threading
import time

_TRACE_MAX_BYTES = 256 * 1024
_TRACE_PATH = None
_TRACE_LOCK = threading.Lock()

# Win11 显式标题栏配色（COLORREF，0x00BBGGRR）
_CAPTION_DARK = 0x00202020
_TEXT_DARK = 0x00E8E8E8
_COLOR_DEFAULT = 0xFFFFFFFF


# ---------------------------------------------------------------- 诊断日志
def _trace_path():
    """日志文件路径（data/titlebar_trace.log），取不到就退回程序目录。"""
    global _TRACE_PATH
    if _TRACE_PATH is not None:
        return _TRACE_PATH
    try:
        try:
            from utils.app_paths import data_path
        except Exception:
            from .app_paths import data_path
        _TRACE_PATH = data_path("titlebar_trace.log")
    except Exception:
        _TRACE_PATH = ""
    return _TRACE_PATH


def trace_titlebar(msg: str):
    """写一行标题栏诊断日志。默认开启，CHONGSHI_TITLEBAR_TRACE=0 关闭。

    任何异常都静默：诊断代码不能影响主题切换。
    """
    if os.environ.get("CHONGSHI_TITLEBAR_TRACE", "1") == "0":
        return
    try:
        path = _trace_path()
        if not path:
            return
        t = time.time()
        stamp = time.strftime("%m-%d %H:%M:%S", time.localtime(t))
        stamp += ".%03d" % int((t - int(t)) * 1000)
        line = f"{stamp} {msg}\n"
        with _TRACE_LOCK:
            try:
                if os.path.isfile(path) and os.path.getsize(path) > _TRACE_MAX_BYTES:
                    os.replace(path, path + ".1")
            except Exception:
                pass
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        pass


# ---------------------------------------------------------------- 平台判断
def _is_win11() -> bool:
    """Windows 11（build 22000+）才支持 DWMWA_CAPTION_COLOR / TEXT_COLOR。"""
    try:
        return sys.getwindowsversion().build >= 22000
    except Exception:
        return False


def _system_app_dark():
    """系统「应用」配色是否为深色（读注册表，不缓存：切主题要实时准确）。

    这条对排查「偶现」很关键：标题栏的兜底渲染跟**系统**主题走，
    系统配色与应用主题不一致时最容易出现「界面黑了标题还白」。
    """
    try:
        import winreg
        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            value, _ = winreg.QueryValueEx(k, "AppsUseLightTheme")
        return int(value) == 0
    except Exception:
        return None


def _screen_pixel(user32, gdi32, x, y):
    """读屏幕上一个点的 RGB。失败返回 None。"""
    try:
        hdc = user32.GetDC(None)
        if not hdc:
            return None
        try:
            color = gdi32.GetPixel(hdc, int(x), int(y))
        finally:
            user32.ReleaseDC(None, hdc)
        if color in (0xFFFFFFFF, -1):
            return None
        return (color & 0xFF, (color >> 8) & 0xFF, (color >> 16) & 0xFF)
    except Exception:
        return None


def _caption_pixels(user32, gdi32, hwnd, wintypes, ctypes):
    """采两个标题栏像素点（左侧空白 / 右侧按钮间隙）。

    为什么要采样：DWM 属性读回 1 不等于屏幕上真的是深色。窗口最大化时
    左上角没有可采样的空白，所以取「左侧 80px」和「右边缘往左 150px」
    两处（避开标题文字与三个系统按钮）。窗口被遮挡时采到的是别的窗口，
    此时只能作参考 —— 日志里同时记了激活状态。
    """
    try:
        rect = wintypes.RECT()
        if not user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
            return None, None
        height = rect.bottom - rect.top
        y = rect.top + max(6, min(12, max(0, height) // 4))
        left = _screen_pixel(user32, gdi32, rect.left + 80, y)
        right = _screen_pixel(user32, gdi32, rect.right - 150, y)
        return left, right
    except Exception:
        return None, None


# ---------------------------------------------------------------- 对外入口
def set_dark_title_bar(window, dark: bool, delayed: bool = True, dark_getter=None,
                       reason: str = ""):
    """设置 Windows 原生标题栏为深色或浅色。
    仅在 Windows 平台生效，其他平台静默忽略。

    `delayed=False` 时只设一次、不排补设 —— 给「窗口激活」这类高频事件用，
    避免每次切窗口都堆五个定时器。

    **为什么要补设**：
    切主题时 apply_theme() 会对整棵控件树重设 QSS + 重画壁纸，界面复杂时
    重绘会持续 1~2 秒；DWM 在重绘/非客户区重建后按系统主题重画标题栏，
    把刚设的属性冲掉。实测主窗口「亮 → 暗」只设一次：属性先变 1，0.25 秒后
    又回到 0，标题栏停在浅色 —— 这正是用户看到的现象。

    **补设为什么要读「当前主题」而不是捕获值**（2026-11-13 修「偶现」）：
    补设是排在未来几秒执行的定时器。若期间用户又切了一次主题（或快速来回切），
    先排上的旧定时器会把**旧主题**再写一遍，最后生效的反而是旧值 ——
    界面已经变了、标题栏还停在另一个颜色上。传 dark_getter 后每次补设都按
    触发时刻的真实主题决定写 0 还是 1，旧定时器不会再把状态写反。

    `reason`：只用于诊断日志，说明这次设置是谁触发的。
    """
    if sys.platform != "win32":
        return
    trace_titlebar(f"==== set dark={int(bool(dark))} delayed={int(bool(delayed))} "
                   f"reason={reason or '-'}")
    _apply_dark_title_bar(window, dark, reason=reason or "direct")
    if not delayed:
        return
    try:
        from PyQt6.QtCore import QTimer
        # 补设五次（2026-11-01 加三个 → 2026-11-13 加五个）。
        # 一次 220ms 在空壳窗口上够用，但真实使用场景下界面复杂、重绘更久，
        # 中途的补设照样会被冲掉（用户报「切暗夜后顶部还是白的」，本地空窗口
        # 复现不出）。时间点覆盖 re-polish + 重画壁纸（实测能到 2~3 秒）结束前后，
        # 多打几次 DWM 调用无副作用。
        for idx, delay in enumerate((220, 700, 1600, 2600, 4000), 1):
            QTimer.singleShot(
                delay,
                lambda d=dark, i=idx, ms=delay: _apply_dark_title_bar(
                    window, _resolve_dark(dark_getter, d),
                    reason=f"retry{i}@{ms}ms"),
            )
    except Exception as e:
        trace_titlebar(f"!! 排补设定时器失败: {e}")


def _resolve_dark(dark_getter, fallback: bool) -> bool:
    """补设时优先读调用方给的「当前主题」，读不到再退回捕获值。"""
    if dark_getter is None:
        return fallback
    try:
        return bool(dark_getter())
    except Exception:
        return fallback


def _apply_dark_title_bar(window, dark: bool, reason: str = ""):
    """真正调用 DWM API 的那一层（立即调用 / 延迟补设共用）。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        try:
            hwnd = int(window.winId())
        except (RuntimeError, ValueError):
            trace_titlebar(f"apply reason={reason} -> winId 不可用（窗口已销毁）")
            return
        if not hwnd or not ctypes.windll.user32.IsWindow(hwnd):
            trace_titlebar(f"apply reason={reason} -> hwnd=0x{hwnd:X} 无效")
            return

        dwmapi = ctypes.windll.dwmapi
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32

        # 显式声明签名：64 位下 HWND/HDC 是 8 字节指针，ctypes 不声明时按
        # c_int 传参会把高 32 位截掉（本项目内存统计工具踩过同类坑）。
        try:
            dwmapi.DwmSetWindowAttribute.argtypes = [
                wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
            dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long
            dwmapi.DwmGetWindowAttribute.argtypes = [
                wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
            dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long
            user32.GetDC.argtypes = [wintypes.HWND]
            user32.GetDC.restype = wintypes.HDC
            user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
            gdi32.GetPixel.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
            gdi32.GetPixel.restype = wintypes.DWORD
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
        used_attr = DWMWA_USE_IMMERSIVE_DARK_MODE
        if res != 0:
            # 回退到旧属性号
            used_attr = DWMWA_USE_IMMERSIVE_DARK_MODE_OLD
            res = dwmapi.DwmSetWindowAttribute(
                wintypes.HWND(hwnd), DWMWA_USE_IMMERSIVE_DARK_MODE_OLD,
                ctypes.byref(value), ctypes.sizeof(value),
            )

        # 读回：确认属性此刻的真值（诊断用）
        got = ctypes.c_int(-1)
        got_res = dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd), DWMWA_USE_IMMERSIVE_DARK_MODE,
            ctypes.byref(got), ctypes.sizeof(got),
        )
        readback = got.value if got_res == 0 else None

        # Win11 再显式指定标题栏/文字颜色。
        # 只看 immersive dark 属性时，实测过「读回 1、屏幕还是浅灰」：属性由
        # DWM 保存，但重绘时机/系统强调色会决定最终画什么。显式给 COLORREF
        # 后标题栏颜色不再依赖系统推断，从根源上消掉「偶现还是白的」。
        cap_hex = txt_hex = "-"
        cap_res = txt_res = "-"
        if _is_win11():
            try:
                cap = ctypes.c_uint(_CAPTION_DARK if dark else _COLOR_DEFAULT)
                cap_res = dwmapi.DwmSetWindowAttribute(
                    wintypes.HWND(hwnd), DWMWA_CAPTION_COLOR,
                    ctypes.byref(cap), ctypes.sizeof(cap),
                )
                cap_hex = f"0x{cap.value:08X}"
                txt = ctypes.c_uint(_TEXT_DARK if dark else _COLOR_DEFAULT)
                txt_res = dwmapi.DwmSetWindowAttribute(
                    wintypes.HWND(hwnd), DWMWA_TEXT_COLOR,
                    ctypes.byref(txt), ctypes.sizeof(txt),
                )
                txt_hex = f"0x{txt.value:08X}"
            except Exception as e:
                cap_hex = f"err:{e}"

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

        # 诊断：属性说深色，但屏幕上标题栏还是亮的 —— 记下来（grep !! 即可）
        px_left, px_right = _caption_pixels(user32, gdi32, hwnd, wintypes, ctypes)
        bright = ""
        try:
            if dark:
                for p in (px_left, px_right):
                    if p and sum(p) / 3.0 > 150:
                        bright = " !!PIXEL_BRIGHT"
                        break
        except Exception:
            pass
        active = int(user32.GetForegroundWindow() == hwnd)
        sys_dark = _system_app_dark()
        trace_titlebar(
            f"apply reason={reason} dark={int(bool(dark))} hwnd=0x{hwnd:X} "
            f"set={res}(attr={used_attr}) read={readback} "
            f"cap={cap_hex}(res={cap_res}) txt={txt_hex}(res={txt_res}) "
            f"px={px_left}/{px_right} active={active} "
            f"sys_dark={'-' if sys_dark is None else int(sys_dark)}{bright}"
        )
    except Exception as e:
        trace_titlebar(f"!! apply reason={reason} 失败: {e}")
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
