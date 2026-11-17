# services/tcpdump_runner.py
"""tcpdump 抓包执行器（无 Qt 依赖）。

从 `services/packet_capture.py` 抽出实际干活的流程（原实现是 QThread + 信号）：
root 检测 → 找到/部署 tcpdump → `adb exec-out tcpdump -w -` 流式写 pcap。
保留原实现的保护：单段大小上限、总时长上限、最多分段数。

未 root 的设备无法开原始套接字，`probe_ready()` 会直接给出原因，不做任何尝试。
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from typing import Optional

from utils.adb_path import get_adb_path

CAPTURE_MAX_SECONDS = 10 * 60
CAPTURE_MAX_BYTES = 100 * 1024 * 1024
CAPTURE_MAX_SEGMENTS = 6


def _creationflags():
    return subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def _sh_quote(text: str) -> str:
    return "'" + str(text).replace("'", "'\\''") + "'"


class TcpdumpRunner:
    DEVICE_CANDIDATES = (
        "/system/bin/tcpdump", "/system/xbin/tcpdump",
        "/vendor/bin/tcpdump", "/data/local/tmp/tcpdump",
    )

    def __init__(self, serial: str, output_dir: str,
                 adb_path: Optional[str] = None):
        self.serial = serial
        self.output_dir = output_dir
        self.adb = adb_path or get_adb_path()
        self.device_path = "/data/local/tmp/tcpdump"
        self.process: Optional[subprocess.Popen] = None
        self.file_handle = None
        self.segments: list = []
        self.stop_reason = ""
        self.bytes_written = 0
        self._stop_evt = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._limit_timer: Optional[threading.Timer] = None
        self._lock = threading.RLock()
        self._max_bytes = CAPTURE_MAX_BYTES
        self._max_segments = CAPTURE_MAX_SEGMENTS

    # ---------- 基础 ----------
    def _adb(self, args: list, timeout: float = 10,
             **kwargs) -> subprocess.CompletedProcess:
        return subprocess.run(
            [self.adb, "-s", self.serial, *args],
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
            creationflags=_creationflags(), **kwargs)

    def _is_root(self) -> bool:
        try:
            r = self._adb(["shell", "id"], timeout=6)
            return "uid=0" in (r.stdout or "")
        except Exception:
            return False

    def probe_ready(self, local_tcpdump_path: str) -> tuple:
        if not self._is_root():
            return False, "设备未 root，无法抓包（tcpdump 需要原始套接字权限）"
        probe = ("for p in " + " ".join(self.DEVICE_CANDIDATES) + "; do "
                 'if [ -x "$p" ]; then echo "$p"; break; fi; done')
        try:
            r = self._adb(["shell", probe], timeout=8)
            found = [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]
            if found:
                return True, ""
        except Exception:
            pass
        if not os.path.exists(local_tcpdump_path):
            return False, ("设备上没有 tcpdump，本地也没有可推送的副本"
                           f"（期望：{local_tcpdump_path}）")
        return True, ""

    def deploy(self, local_tcpdump_path: str) -> str:
        probe = ("for p in " + " ".join(self.DEVICE_CANDIDATES) + "; do "
                 'if [ -x "$p" ]; then echo "$p"; break; fi; done')
        try:
            r = self._adb(["shell", probe], timeout=8)
            found = [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]
            if found:
                self.device_path = found[0]
                return self.device_path
        except Exception:
            pass
        if not os.path.isfile(local_tcpdump_path):
            raise RuntimeError(f"本地缺少 tcpdump：{local_tcpdump_path}")
        push = self._adb(["push", local_tcpdump_path,
                          "/data/local/tmp/tcpdump"], timeout=30)
        if push.returncode != 0:
            raise RuntimeError("tcpdump 推送失败："
                               f"{((push.stdout or '') + (push.stderr or '')).strip()[:300]}")
        self._adb(["shell", "chmod", "+x", "/data/local/tmp/tcpdump"], timeout=10)
        self.device_path = "/data/local/tmp/tcpdump"
        return self.device_path

    # ---------- 启动 ----------
    def start(self, local_tcpdump_path: str, filter_expr: str = "",
              interface: str = "any", snaplen: int = 0,
              max_seconds: int = CAPTURE_MAX_SECONDS,
              max_bytes: int = CAPTURE_MAX_BYTES,
              subdir: str = "capture",
              max_segments: int = CAPTURE_MAX_SEGMENTS) -> dict:
        ready, reason = self.probe_ready(local_tcpdump_path)
        if not ready:
            raise RuntimeError(reason)
        self.deploy(local_tcpdump_path)

        max_seconds = max(0, min(3600, int(max_seconds or 0)))
        self._max_bytes = max(0, min(1024 * 1024 * 1024, int(max_bytes or 0)))
        self._max_segments = max(1, min(20, int(max_segments or 1)))
        self._stop_evt.clear()
        self.stop_reason = ""
        self.bytes_written = 0
        self.segments = []

        folder = os.path.join(self.output_dir, self.serial, subdir)
        os.makedirs(folder, exist_ok=True)
        self._base_path = os.path.join(
            folder, f"capture_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
        self._filter_expr = (filter_expr or "").strip()
        self._interface = interface or "any"
        self._snaplen = max(0, int(snaplen or 0))

        if max_seconds:
            self._limit_timer = threading.Timer(max_seconds, self._stop_by_time)
            self._limit_timer.daemon = True
            self._limit_timer.start()

        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="mcp-tcpdump")
        self._thread.start()
        return {"device_path": self.device_path,
                "dir": folder, "interface": self._interface,
                "filter": self._filter_expr,
                "max_seconds": max_seconds,
                "max_bytes": self._max_bytes,
                "max_segments": self._max_segments}

    def _stop_by_time(self) -> None:
        if not self._thread or not self._thread.is_alive():
            return
        self.stop_reason = "time"
        self._stop_evt.set()
        proc = self.process
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass

    def _loop(self) -> None:
        try:
            while not self._stop_evt.is_set() and \
                    len(self.segments) < self._max_segments:
                if not self._run_segment():
                    break
        finally:
            self._cleanup()

    def _run_segment(self) -> bool:
        idx = len(self.segments) + 1
        path = self._base_path + ("" if idx == 1 else f"_p{idx}") + ".pcap"
        self.file_handle = open(path, "wb")
        self.segments.append(path)

        parts = [self.device_path, "-i", _sh_quote(self._interface),
                 "-s", str(self._snaplen), "-w", "-", "-U"]
        if self._filter_expr:
            parts.append(_sh_quote(self._filter_expr))
        cmd = [self.adb, "-s", self.serial, "exec-out",
               " ".join(parts) + " 2>/dev/null"]
        try:
            self.process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                creationflags=_creationflags())
        except Exception:
            self._close_segment()
            raise

        seg_full = False
        try:
            while not self._stop_evt.is_set():
                data = self.process.stdout.read(8192)
                if not data:
                    break
                self.file_handle.write(data)
                self.file_handle.flush()
                self.bytes_written += len(data)
                if self._max_bytes and \
                        os.path.getsize(path) >= self._max_bytes:
                    seg_full = True
                    break
        finally:
            self._close_segment()

        if seg_full and not self._stop_evt.is_set() and \
                len(self.segments) < self._max_segments:
            return True
        if seg_full:
            self.stop_reason = "size"
        return False

    def _close_segment(self) -> None:
        proc = self.process
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            self.process = None
        if self.file_handle is not None:
            try:
                self.file_handle.close()
            except Exception:
                pass
            self.file_handle = None

    def _cleanup(self) -> None:
        if self._limit_timer:
            try:
                self._limit_timer.cancel()
            except Exception:
                pass
            self._limit_timer = None
        self._close_segment()
        if not self.stop_reason:
            self.stop_reason = "user" if self._stop_evt.is_set() else "eof"

    # ---------- 状态 / 停止 ----------
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def wait(self, timeout: Optional[float] = None) -> bool:
        thread = self._thread
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

    def status(self) -> dict:
        size = 0
        for path in self.segments:
            try:
                size += os.path.getsize(path)
            except OSError:
                pass
        return {"running": self.running(), "files": list(self.segments),
                "bytes": size, "stop_reason": self.stop_reason}

    def stop(self) -> dict:
        if self.stop_reason == "":
            self.stop_reason = "user"
        self._stop_evt.set()
        proc = self.process
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=15)
            self._thread = None
        if self._limit_timer:
            try:
                self._limit_timer.cancel()
            except Exception:
                pass
            self._limit_timer = None
        return self.status()
