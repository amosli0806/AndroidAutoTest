# utils/gui_presence.py
"""GUI 存在性锁：跨平台判断「虫师界面是否正在运行」。

为什么不用 Windows 命名互斥量：
    MCP / 未来 macOS 都需要判断 GUI 是否在跑；命名互斥量只在 Windows 有效，
    文件锁在 Windows / macOS / Linux 都能用。

约定：
    * `gui.lock` 只负责锁，不写业务数据；文件存在 ≠ GUI 在跑，**锁被占用**才算；
    * `gui.info.json` 只用于给 MCP 的只读提示提供 PID / 版本等元信息；
    * GUI 退出时 OS 自动释放锁，崩溃 / 强杀也不会留下「永远在运行」的脏状态；
    * 不删除 lock 文件（删除会和 MCP 探测产生竞态），只释放锁。
"""
from __future__ import annotations

import atexit
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Optional

from utils.app_paths import data_path
from utils.version import APP_VERSION

LOCK_FILE = "gui.lock"
INFO_FILE = "gui.info.json"


@dataclass
class GuiInfo:
    pid: int = 0
    started_at: str = ""
    version: str = ""
    exe: str = ""
    platform: str = ""
    # 锁被占用但元数据读不到（旧版本 / 文件损坏 / 权限异常）
    unknown: bool = False


def _lock_path() -> str:
    return data_path(LOCK_FILE)


def _info_path() -> str:
    return data_path(INFO_FILE)


def _ensure_byte(f):
    """Windows 的 msvcrt.locking 需要一个真实存在的字节区域。"""
    f.seek(0, os.SEEK_END)
    if f.tell() == 0:
        f.write(b"\n")
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    f.seek(0)


def _try_lock(f):
    if sys.platform == "win32":
        import msvcrt
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(f):
    try:
        if sys.platform == "win32":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


def _write_info():
    info = GuiInfo(
        pid=os.getpid(),
        started_at=datetime.now().isoformat(timespec="seconds"),
        version=APP_VERSION,
        exe=sys.executable or "",
        platform=sys.platform,
    )
    path = _info_path()
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(asdict(info), f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        # 元数据写失败不影响锁本身；MCP 会退化成 unknown 提示
        try:
            os.remove(tmp)
        except OSError:
            pass


def _read_info() -> Optional[GuiInfo]:
    try:
        with open(_info_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None
        return GuiInfo(
            pid=int(data.get("pid") or 0),
            started_at=str(data.get("started_at") or ""),
            version=str(data.get("version") or ""),
            exe=str(data.get("exe") or ""),
            platform=str(data.get("platform") or ""),
        )
    except Exception:
        return None


class GuiPresence:
    """GUI 侧 `acquire()` 持锁；MCP 侧每个写操作前 `probe()`。"""

    _handle = None

    @classmethod
    def acquire(cls) -> bool:
        """GUI 启动时调用；已有 GUI 持锁时返回 False。"""
        if cls._handle is not None:
            return True
        try:
            f = open(_lock_path(), "a+b")
        except Exception:
            return False
        try:
            _ensure_byte(f)
            _try_lock(f)
        except OSError:
            try:
                f.close()
            except OSError:
                pass
            return False
        cls._handle = f
        _write_info()
        return True

    @classmethod
    def release(cls):
        f = cls._handle
        cls._handle = None
        if f is None:
            return
        _unlock(f)
        try:
            f.close()
        except OSError:
            pass

    @classmethod
    def probe(cls) -> Optional[GuiInfo]:
        """返回 None = GUI 没在跑；否则返回 GuiInfo（unknown=True 表示保守只读）。"""
        path = _lock_path()
        if not os.path.exists(path):
            return None
        try:
            f = open(path, "r+b")
        except Exception:
            return GuiInfo(unknown=True)
        try:
            _ensure_byte(f)
            try:
                _try_lock(f)
            except OSError:
                # 锁被占用 = GUI 在跑；尽量读元数据，读不到就 unknown
                return _read_info() or GuiInfo(unknown=True)
            else:
                _unlock(f)
                return None
        except Exception:
            return GuiInfo(unknown=True)
        finally:
            try:
                f.close()
            except OSError:
                pass

    @classmethod
    def is_running(cls) -> bool:
        return cls.probe() is not None


def probe_gui() -> Optional[GuiInfo]:
    """MCP 侧便捷入口：先看跨平台文件锁，Windows 再看命名互斥量兜底。"""
    info = GuiPresence.probe()
    if info is not None:
        return info
    if _windows_mutex_running():
        # 旧版本 GUI 还没写 gui.lock，或文件锁获取失败：命名互斥量仍在
        return GuiInfo(platform="win32", unknown=True)
    return None


def _windows_mutex_running(name: str = r"Local\ChongshiSingleInstance") -> bool:
    """Windows 命名互斥量兜底；非 Windows / 权限异常按「在跑」保守处理。"""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        ERROR_FILE_NOT_FOUND = 2
        ERROR_PATH_NOT_FOUND = 3
        SYNCHRONIZE = 0x00100000
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenMutexW.restype = wintypes.HANDLE
        kernel32.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        handle = kernel32.OpenMutexW(SYNCHRONIZE, False, name)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        err = ctypes.get_last_error()
        return err not in (ERROR_FILE_NOT_FOUND, ERROR_PATH_NOT_FOUND)
    except Exception:
        return True


atexit.register(GuiPresence.release)
