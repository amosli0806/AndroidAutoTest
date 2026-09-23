# utils/log_colors.py
"""日志文本颜色（用于拼接 HTML span）。

日志是以 HTML 形式塞进 QTextEdit 的，行内 color 会覆盖控件自身的主题色，
所以这里的配色必须自己跟随主题：
  - 浅色主题：沿用原本为白底设计的深色系（保持不变）
  - 深色主题：换成对应的亮色系，否则深色文字画在深色面板上几乎看不见

配色在模块内缓存，避免每行日志都去读一次配置；主题切换后由界面调用 refresh()。
"""
import logging
import re

logger = logging.getLogger(__name__)

# ---------- 语义级别 ----------
INFO = "info"
SUCCESS = "success"
WARNING = "warning"
ERROR = "error"
RESULT = "result"

# ---------- 两套配色 ----------
# dark 一列的取值刻意选用项目里深色主题已在使用的亮色，保持一致
_PALETTE = {
    "light": {
        INFO: "#000000",
        SUCCESS: "#2e7d32",
        WARNING: "#f57c00",
        ERROR: "#c62828",
        RESULT: "#1565c0",
    },
    "dark": {
        INFO: "#e0e0e0",
        SUCCESS: "#2ecc71",
        WARNING: "#f39c12",
        ERROR: "#e74c3c",
        RESULT: "#64b5f6",
    },
}

# 当前生效的一列（模块导入时按配置解析一次，之后靠 refresh() 更新）
_current = "light"


def refresh(is_dark=None) -> str:
    """刷新配色，返回 'light' 或 'dark'。

    is_dark 为 None 时按当前主题配置解析；界面侧已知主题时建议直接传入，
    避免依赖「配置文件先写入、再应用主题」的时序。
    """
    global _current
    if is_dark is None:
        try:
            from utils.settings import Settings, THEME_MODE_DARK
            is_dark = Settings.get_theme_mode() == THEME_MODE_DARK
        except Exception as e:
            logger.warning(f"[log_colors] 解析主题失败，沿用 {_current}: {e}")
            return _current
    _current = "dark" if is_dark else "light"
    return _current


def current_mode() -> str:
    """当前生效的配色名（'light' / 'dark'），便于调试。"""
    return _current


def log_color(level: str) -> str:
    """返回指定级别在当前主题下的颜色。"""
    palette = _PALETTE.get(_current, _PALETTE["light"])
    return palette.get(level, palette[INFO])


def span(level: str, text: str, icon: str = "") -> str:
    """直接生成带主题色的日志 span。"""
    prefix = f"{icon} " if icon else ""
    return f"<span style='color:{log_color(level)};'>{prefix}{text}</span>"


def recolor(html: str) -> str:
    """把已生成的日志 HTML 里的颜色换成当前主题对应的颜色。

    日志颜色是写死在 HTML 行内样式里的，控件样式表改不动它，
    所以切换主题后必须把已经输出过的日志重新过一遍这个函数，否则
    先输出的那些日志会一直保留旧主题的颜色。

    对同一段 HTML 可以重复调用（两个主题的色值互不相同，不会互相误伤）。
    """
    target = _PALETTE.get(_current, _PALETTE["light"])
    source = _PALETTE["dark" if _current == "light" else "light"]
    out = html
    for level, new_color in target.items():
        old_color = source.get(level)
        if old_color and old_color.lower() != new_color.lower():
            out = re.sub(re.escape(old_color), new_color, out, flags=re.IGNORECASE)
    return out


refresh()
