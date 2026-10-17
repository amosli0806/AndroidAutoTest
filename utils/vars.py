# utils/vars.py
"""变量替换：把 `${name}` 换成变量值。

语法（与「扩展方向」里的口径一致）：

    ${name}          → 变量值；**未定义时原样保留**（不静默变空 —— 静默变空会让用例
                        莫名失败，还查不出原因，所以保留占位符 + 由调用方告警）
    ${name:默认值}    → 未定义时用默认值
    $${name}         → 输出字面量 `${name}`（转义）

不支持嵌套（`${a${b}}`）—— 一眼看不出结果的东西不该写进用例。
与 ADB 工具箱那套 `{timestamp}`（无 $ 前缀，见 services/adb_command_pool.py:677）
**不冲突**：这里只认 `$` 开头的占位符。

本文件只管"算"，不碰界面；用户变量从 models/var_model.py 读（带 mtime 缓存）。

名字规则：`${...}` 里的名字取「到下一个 `}` 为止的非空串」，**允许中文** —— 因为数据驱动的
表头 = 变量名，而 CSV 表头用中文很常见（如 `${账号}`）。全局变量表那边仍是 ASCII 命名
（设置页里有意约束，避免日志里中文变量名和正文糊在一起），两者互不影响。
"""
import os
import re
from datetime import datetime
from typing import Dict, List, Tuple

# `$$`（转义） 或 `${name}` / `${name:默认值}`；name 允许中文（数据驱动表头），
# 非贪婪，取到第一个 `}`；`:默认值` 可选。不支持嵌套（`${a${b}}` 会因内层 `{` 而整体不匹配）。
_VAR_RE = re.compile(r"\$(?:\$|\{([^{}]+?)(?::([^}]*))?\})")


def builtin_values(serial: str = "") -> Dict[str, str]:
    """内置变量（只读）。时间戳格式与项目里既有写法对齐：%Y%m%d_%H%M%S。"""
    now = datetime.now()
    return {
        'serial': str(serial or ""),
        'date': now.strftime("%Y-%m-%d"),
        'time': now.strftime("%H:%M:%S"),
        'timestamp': now.strftime("%Y%m%d_%H%M%S"),
    }


def resolve_text(text: str, values: Dict[str, str]) -> Tuple[str, List[str]]:
    """替换一段文本，返回 (结果, 未定义变量名列表)。未定义的 `${x}` 原样保留。"""
    if not isinstance(text, str) or "$" not in text:
        return text, []
    missing: List[str] = []

    def repl(m):
        if m.group(0) == "$$":
            return "$"
        name, default = m.group(1), m.group(2)
        if name in values and values[name] is not None:
            return str(values[name])
        if default is not None:          # ${x:默认值} —— 没定义就用默认
            return default
        missing.append(name)
        return m.group(0)                # 未定义：原样保留，等调用方告警

    return _VAR_RE.sub(repl, text), missing


def resolve_params(params, values: Dict[str, str]):
    """递归替换 params 里的所有字符串（list / dict 也走一遍）。

    返回 (新对象, 未定义变量名去重列表)。**不改入参**：调用方拿返回值去用，
    步骤模型里存的原始 `${...}` 保持不变（否则改一次参数就把占位符换成了当次的值）。
    """
    missing: List[str] = []

    def walk(obj):
        if isinstance(obj, str):
            new, miss = resolve_text(obj, values)
            missing.extend(miss)
            return new
        if isinstance(obj, list):
            return [walk(x) for x in obj]
        if isinstance(obj, dict):
            return {k: walk(v) for k, v in obj.items()}
        return obj

    out = walk(params)
    return out, list(dict.fromkeys(missing))


# 用户变量按 data/vars_data.json 的 (mtime, 大小) 缓存：一条用例几十步，
# 每步都读一次盘没必要；文件一变（用户刚改完）下次就自动重读。
_CACHE = {"sig": None, "values": {}}


def user_values() -> Dict[str, str]:
    """读用户变量表（带缓存）。文件不存在 = 没有用户变量。"""
    from models.var_model import VarModel       # 延迟导入：避免 utils ← models 的循环
    path = VarModel.DATA_FILE
    try:
        st = os.stat(path)
        sig = (st.st_mtime_ns, st.st_size)
    except OSError:
        return {}
    if _CACHE["sig"] == sig:
        return _CACHE["values"]
    values = VarModel().values()
    _CACHE["sig"] = sig
    _CACHE["values"] = values
    return values


def current_values(serial: str = "") -> Dict[str, str]:
    """当前生效的变量：用户变量 + 内置变量 + 数据驱动这一行的值。

    内置变量**最后覆盖**：即使用户表里存了同名项（老版本文件 / 手工改过 JSON），
    也不会让 `${serial}` 变成别的东西（模型层已经拦了，这里是最后一道）。
    数据驱动的行级值在**最最外层**：它只在某一组数据执行期间存在，优先级最高。
    """
    values = dict(user_values())
    values.update(builtin_values(serial))
    values.update(_row_override)
    return values


# 数据驱动：当前这一组数据的变量值。执行 worker 在每组数据前 set_row_values、
# 跑完 clear_row_values；只由执行线程写、perform() 在同一线程里读，无需加锁。
_row_override: Dict[str, str] = {}


def set_row_values(values) -> None:
    """设当前数据组的变量值（数据驱动执行用，覆盖用户变量/内置变量）。"""
    global _row_override
    _row_override = dict(values or {})


def clear_row_values() -> None:
    global _row_override
    _row_override = {}


def invalidate_cache():
    """清掉缓存（变量页保存后调用，保证下一步就用上新值）。"""
    _CACHE["sig"] = None
    _CACHE["values"] = {}
