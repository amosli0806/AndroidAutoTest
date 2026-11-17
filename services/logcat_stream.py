# services/logcat_stream.py
"""实时 logcat（无 Qt 依赖）。

主机侧流式落盘：`adb shell logcat -v threadtime ...` 的 stdout 被持续写入
`<输出目录>/<序列号>/logcat_<时间>.log`。支持按行数 / 秒数自动停止，
停止时除了杀本地进程，还会清理设备端残留的 logcat 进程。
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime
from typing import Optional

from utils.adb_path import get_adb_path


def _creationflags():
    return subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def _kill_tree(proc: subprocess.Popen) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True, timeout=10,
                       creationflags=_creationflags())
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


class LogcatStream:
    def __init__(self, serial: str, output_dir: str,
                 adb_path: Optional[str] = None):
        self.serial = serial
        self.output_dir = output_dir
        self.adb = adb_path or get_adb_path()
        self.proc: Optional[subprocess.Popen] = None
        self.path = ""
        self.lines = 0
        self.stop_reason = ""
        self._max_lines = 20000
        self._lock = threading.RLock()
        self._done = threading.Event()
        self._reader: Optional[threading.Thread] = None
        self._timer: Optional[threading.Thread] = None

    def _adb(self, args: list, timeout: float = 10,
             **kwargs) -> subprocess.CompletedProcess:
        return subprocess.run(
            [self.adb, "-s", self.serial, *args],
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
            creationflags=_creationflags(), **kwargs)

    def running_pids(self) -> list[str]:
        r = self._adb(["shell", "pgrep", "logcat"], timeout=5)
        text = (r.stdout or "").strip()
        return text.splitlines() if text else []

    def _clean_remote(self) -> None:
        try:
            r = self._adb(["shell", "ps", "-e", "-o", "pid,cmd"], timeout=5)
        except Exception:
            return
        for line in (r.stdout or "").splitlines():
            low = line.lower()
            if "logcat" not in low and "hilogcat" not in low:
                continue
            if "ps -e" in low:
                continue
            pid = line.strip().split(maxsplit=1)[0]
            if pid.isdigit():
                try:
                    self._adb(["shell", "kill", "-9", pid], timeout=5)
                except Exception:
                    pass

    def start(self, tags: Optional[list] = None, max_lines: int = 20000,
              max_seconds: int = 300, clear_first: bool = False) -> dict:
        self._max_lines = max(100, min(200000, int(max_lines or 20000)))
        max_seconds = max(5, min(3600, int(max_seconds or 300)))
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        folder = os.path.join(self.output_dir, self.serial)
        os.makedirs(folder, exist_ok=True)
        self.path = os.path.normpath(
            os.path.join(folder, f"logcat_{stamp}.log"))

        if clear_first:
            self._adb(["shell", "logcat", "-c"], timeout=10)

        filters = ""
        cleaned = [str(t).strip() for t in (tags or []) if str(t).strip()]
        if cleaned:
            filters = " ".join(f"{t}:V" for t in cleaned) + " *:S"
        shell_cmd = "logcat -v threadtime" + (f" {filters}" if filters else "")

        self.proc = subprocess.Popen(
            [self.adb, "-s", self.serial, "shell", shell_cmd],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            creationflags=_creationflags())
        time.sleep(0.4)
        if self.proc.poll() is not None:
            raise RuntimeError("logcat 启动失败，进程已退出")

        def _reader():
            try:
                with open(self.path, "w", encoding="utf-8") as f:
                    for line in self.proc.stdout:
                        f.write(line)
                        self.lines += 1
                        if self.lines >= self._max_lines:
                            self.stop_reason = self.stop_reason or "lines"
                            break
            except Exception:
                pass
            finally:
                if not self.stop_reason:
                    self.stop_reason = "eof"
                try:
                    self.proc.stdout.close()
                except Exception:
                    pass
                self._done.set()

        def _timer():
            if self._done.wait(max_seconds):
                return
            self.stop_reason = self.stop_reason or "time"
            _kill_tree(self.proc)

        self._reader = threading.Thread(target=_reader, daemon=True,
                                        name="mcp-logcat-reader")
        self._timer = threading.Thread(target=_timer, daemon=True,
                                       name="mcp-logcat-timer")
        self._reader.start()
        self._timer.start()
        return {"path": self.path, "max_lines": self._max_lines,
                "max_seconds": max_seconds, "tags": cleaned,
                "pid": self.proc.pid}

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def wait(self, timeout: float = 300) -> bool:
        return self._done.wait(max(0.1, float(timeout)))

    def stop(self) -> dict:
        _kill_tree(self.proc)
        self._done.wait(5)
        try:
            if self.proc and self.proc.stdout:
                self.proc.stdout.close()
        except Exception:
            pass
        if self.stop_reason in ("", "eof"):
            self.stop_reason = "user"
        self._clean_remote()
        size = os.path.getsize(self.path) if os.path.isfile(self.path) else 0
        return {"path": self.path, "lines": self.lines,
                "bytes": size, "stop_reason": self.stop_reason}

    def tail(self, n: int = 200) -> list[str]:
        if not self.path or not os.path.isfile(self.path):
            return []
        n = max(1, min(5000, int(n or 200)))
        buf = deque(maxlen=n)
        try:
            with open(self.path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    buf.append(line.rstrip("\n"))
        except Exception:
            return []
        return list(buf)
