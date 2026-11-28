# utils/busy.py
"""全局「任务执行中」登记表。

为什么需要：数据包导出 / 导入要读、要覆盖整份资产 json。若此时自动化正在跑，
打出来的包可能少了最后几条用例；若正在跑的时候导入并重启，正在执行的任务会被
直接掐断。三个执行入口（自动化、性能采集、语音播报）各自把状态藏在视图里，
外部问不到，所以在这里做一张最小的登记表。

用法：

    from utils import busy
    busy.set_busy("execution", "自动化执行中")   # 开始时
    busy.clear_busy("execution")                 # 结束时（务必放在 finally）

登记的是「原因」而不是布尔 —— 提示里能直接写清是哪个任务在跑，不用调用方拼字符串。
同一个 key 重复 set 只更新原因，不会叠加；没登记就 clear 是安全的。
"""
from __future__ import annotations

import threading
from typing import Dict, List

_lock = threading.Lock()
_busy: Dict[str, str] = {}       # key -> 人类可读原因（dict 保序，先开始的排前面）

# 已知的 key，供调用方对齐（不强制，写错只是多一个 key）
KEY_EXECUTION = "execution"      # 自动化执行（含定时任务、场景化）
KEY_PERF = "perf"                # 性能采集
KEY_VOICE = "voice"              # 语音播报


def set_busy(key: str, reason: str = "") -> None:
    """登记一个正在执行的任务。重复登记只更新原因。"""
    if not key:
        return
    with _lock:
        _busy[str(key)] = str(reason or "任务执行中")


def clear_busy(key: str) -> None:
    """注销。未登记过也不报错（异常路径下重复 clear 很常见）。"""
    if not key:
        return
    with _lock:
        _busy.pop(str(key), None)


def busy_reason() -> str:
    """当前是否有任务在执行：有则返回第一条原因，否则空串。"""
    with _lock:
        for reason in _busy.values():
            if reason:
                return reason
    return ""


def is_busy() -> bool:
    with _lock:
        return bool(_busy)


def busy_keys() -> List[str]:
    """当前登记的全部 key（排查用）。"""
    with _lock:
        return list(_busy.keys())


def reset() -> None:
    """清空全部登记。只在测试与「进程即将重启」这类场景用。"""
    with _lock:
        _busy.clear()
