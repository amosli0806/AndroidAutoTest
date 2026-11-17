# services/screenrecord_runner.py
"""录屏执行器（无 Qt 依赖）。

主机侧持有 `adb shell screenrecord` 子进程；停止时先给设备端进程发 SIGINT
（让 mp4 正常收尾），再杀本地进程树，最后 pull 回本机并删设备端文件。
Android 的 screenrecord 有 180 秒硬限制，超出部分只能分段，这里不绕。
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from typing import Optional

from utils.adb_path import get_adb_path

MAX_SECONDS = 180


def _creationflags():
    return subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def _kill_tree(proc: subprocess.Popen) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, timeout=10,
                           creationflags=_creationflags())
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


class ScreenRecordRunner:
    def __init__(self, serial: str, output_dir: str,
                 adb_path: Optional[str] = None):
        self.serial = serial
        self.output_dir = output_dir
        self.adb = adb_path or get_adb_path()
        self.proc: Optional[subprocess.Popen] = None
        self.remote_path = ""
        self.local_path = ""
        self.started_at = ""
        self._lock = threading.RLock()
        self._finalized = False
        self._head: list[str] = []

    def _adb(self, args: list, timeout: float = 10,
             **kwargs) -> subprocess.CompletedProcess:
        return subprocess.run(
            [self.adb, "-s", self.serial, *args],
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
            creationflags=_creationflags(), **kwargs)

    def running_pids(self) -> list[str]:
        r = self._adb(["shell", "pgrep", "screenrecord"], timeout=5)
        text = (r.stdout or "").strip()
        return text.splitlines() if text else []

    def start(self, duration_s: int = 180, bitrate_mbps: float = 8.0) -> dict:
        duration_s = max(5, min(MAX_SECONDS, int(duration_s or MAX_SECONDS)))
        existing = self.running_pids()
        if existing:
            raise RuntimeError(
                f"设备上已有录屏在运行（PID {', '.join(existing)}），先停止再启动")

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.remote_path = f"/sdcard/video_{stamp}.mp4"
        folder = os.path.join(self.output_dir, self.serial)
        os.makedirs(folder, exist_ok=True)
        self.local_path = os.path.normpath(
            os.path.join(folder, f"video_{stamp}.mp4"))
        self.started_at = stamp

        cmd = [self.adb, "-s", self.serial, "shell", "screenrecord",
               "--time-limit", str(duration_s),
               "--bit-rate", str(int(float(bitrate_mbps or 8) * 1_000_000)),
               self.remote_path]
        self.proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
            creationflags=_creationflags(),
            **({} if sys.platform == "win32" else {"preexec_fn": os.setsid}))

        def _drain():
            try:
                for line in self.proc.stderr:
                    if len(self._head) < 20:
                        self._head.append(line.rstrip())
            except Exception:
                pass

        threading.Thread(target=_drain, daemon=True,
                         name="mcp-screenrecord-log").start()
        time.sleep(0.6)
        if self.proc.poll() is not None:
            reason = "\n".join(self._head)[:500]
            raise RuntimeError(f"录屏启动失败：{reason or '进程已退出'}")

        return {"remote_path": self.remote_path, "path": self.local_path,
                "duration_s": duration_s,
                "bitrate_mbps": float(bitrate_mbps or 8),
                "pid": self.proc.pid}

    def poll(self) -> Optional[int]:
        return self.proc.poll() if self.proc else None

    def wait(self, timeout: float = 200) -> Optional[int]:
        if not self.proc:
            return None
        try:
            return self.proc.wait(timeout=max(1.0, float(timeout)))
        except subprocess.TimeoutExpired:
            return None

    def _kill_remote(self, graceful: bool = True) -> None:
        pids = self.running_pids()
        for pid in pids:
            sig = "-2" if graceful else "-9"
            try:
                self._adb(["shell", "kill", sig, pid], timeout=5)
            except Exception:
                pass
        if graceful:
            deadline = time.time() + 10
            while time.time() < deadline and self.running_pids():
                time.sleep(0.3)

    def finalize(self) -> dict:
        """pull 视频并删除设备端文件；成功后才标记完成，失败可重试。"""
        with self._lock:
            if self._finalized:
                return {"path": self.local_path,
                        "size": os.path.getsize(self.local_path)
                        if os.path.isfile(self.local_path) else 0,
                        "pulled": os.path.isfile(self.local_path)}
        pulled = False
        try:
            r = self._adb(["pull", self.remote_path, self.local_path],
                          timeout=180)
            pulled = r.returncode == 0 and os.path.isfile(self.local_path)
        except Exception:
            pulled = False
        if not pulled:
            # 没拉回来就别删设备端文件，交给调用方提示手动 pull
            raise RuntimeError(
                f"录屏文件拉取失败，设备端保留：{self.remote_path}")
        try:
            self._adb(["shell", "rm", "-f", self.remote_path], timeout=15)
        except Exception:
            pass
        with self._lock:
            self._finalized = True
        return {"path": self.local_path,
                "size": os.path.getsize(self.local_path), "pulled": True}

    def stop(self) -> dict:
        """优雅停止：SIGINT → 等退出 → 杀本地树 → pull。"""
        self._kill_remote(graceful=True)
        with self._lock:
            proc = self.proc
        if proc is not None and proc.poll() is None:
            _kill_tree(proc)
            try:
                proc.wait(timeout=10)
            except Exception:
                pass
        return self.finalize()
