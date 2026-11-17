# services/monkey_runner.py
"""Monkey 执行器（无 Qt 依赖）。

与 GUI 面板的差别：这里把 Monkey 发成**设备端常驻进程**——
`nohup [timeout N] monkey ... > /data/local/tmp/monkey_<tag>.log 2>&1 & echo $!`。
命令下发后立即返回，MCP / Comate 重启不会带走设备上的 Monkey；停止时按
记录的 PID 终止，探不到再 `pgrep monkey` 兜底。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import time
from typing import Optional

from utils.adb_path import get_adb_path

_PACKAGE_RE = re.compile(r"^[A-Za-z0-9._]+$")

# 与 GUI 面板一致的默认事件比例。syskeys 必须是 0：
# 这台车机没有物理按键，monkey 一旦注入 SYS_KEYS 会直接以 rc 251 退出
# （日志只有 "VM exiting with result code -5"，没有其他线索）。
DEFAULT_PCTS = {
    "touch": 95,
    "motion": 5,
    "trackball": 0,
    "nav": 0,
    "majornav": 0,
    "appswitch": 0,
    "flip": 0,
    "anyevent": 0,
    "syskeys": 0,
}

# 与 GUI 面板保持一致的常用开关
IGNORE_FLAGS = {
    "ignore_crashes": "--ignore-crashes",
    "ignore_timeouts": "--ignore-timeouts",
    "ignore_security": "--ignore-security-exceptions",
    "ignore_native": "--ignore-native-crashes",
    "monitor_native": "--monitor-native-crashes",
}


class MonkeyRunner:
    def __init__(self, serial: str, adb_path: Optional[str] = None):
        self.serial = serial
        self.adb = adb_path or get_adb_path()

    # ---------- 基础 ----------
    def _adb(self, args: list, timeout: float = 10) -> subprocess.CompletedProcess:
        return subprocess.run(
            [self.adb, "-s", self.serial, *args],
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW
            if sys.platform == "win32" else 0)

    @staticmethod
    def validate_packages(packages: list) -> list[str]:
        cleaned = []
        for name in packages or []:
            name = (name or "").strip()
            if not _PACKAGE_RE.match(name):
                raise ValueError(f"包名不合法：{name!r}")
            if name not in cleaned:
                cleaned.append(name)
        if not cleaned:
            raise ValueError("至少需要一个包名")
        return cleaned

    def packages_exist(self, packages: list) -> list[str]:
        missing = []
        for name in packages:
            out = (self._adb(["shell", "pm", "list", "packages", name],
                             timeout=10).stdout or "")
            if f"package:{name}" not in out:
                missing.append(name)
        return missing

    def running_pid(self) -> str:
        """设备上已有的 monkey 进程（不区分是不是虫师启动的）。"""
        r = self._adb(["shell", "pgrep", "monkey"], timeout=5)
        text = (r.stdout or "").strip()
        return text.splitlines()[0].strip() if text else ""

    # ---------- 启动 ----------
    def build_command(self, packages: list, events: int, seed: int,
                      throttle_ms: int = 800, verbose: int = 0,
                      flags: Optional[dict] = None,
                      pcts: Optional[dict] = None,
                      whitelist_remote: str = "") -> str:
        parts = ["monkey"]
        if whitelist_remote:
            parts.extend(["--pkg-whitelist-file", whitelist_remote])
        else:
            for name in packages:
                parts.extend(["-p", name])
        parts.extend(["-s", str(int(seed))])
        if throttle_ms and int(throttle_ms) > 0:
            parts.extend(["--throttle", str(int(throttle_ms))])
        merged = dict(DEFAULT_PCTS)
        merged.update({k: v for k, v in (pcts or {}).items() if k in merged})
        for key, value in merged.items():
            parts.extend([f"--pct-{key}", str(int(value))])
        for key, flag in IGNORE_FLAGS.items():
            if (flags or {}).get(key):
                parts.append(flag)
        for _ in range(max(0, int(verbose or 0))):
            parts.append("-v")
        parts.append(str(int(events)))
        return " ".join(parts)

    def start(self, packages: list, events: int, seed: int,
              throttle_ms: int = 800, verbose: int = 0,
              duration_s: int = 0, tag: str = "", flags: Optional[dict] = None,
              pcts: Optional[dict] = None, use_whitelist: bool = False,
              start_timeout_s: float = 8.0) -> dict:
        packages = self.validate_packages(packages)
        missing = self.packages_exist(packages)
        if missing:
            raise ValueError(f"包名不存在于设备上：{', '.join(missing)}")
        existing = self.running_pid()
        if existing:
            raise RuntimeError(f"设备上已有 Monkey 在运行（PID {existing}），先停止再启动")

        tag = tag or str(int(time.time()))
        log_path = f"/data/local/tmp/monkey_{tag}.log"
        whitelist_remote = ""
        if use_whitelist:
            whitelist_remote = f"/data/local/tmp/monkey_whitelist_{tag}.txt"
            local = os.path.join(tempfile.gettempdir(),
                                 f"chongshi_wl_{tag}.txt")
            with open(local, "w", encoding="utf-8") as f:
                f.write("\n".join(packages) + "\n")
            try:
                push = self._adb(["push", local, whitelist_remote], timeout=15)
                if push.returncode != 0:
                    raise RuntimeError(
                        "白名单文件推送失败："
                        f"{((push.stdout or '') + (push.stderr or '')).strip()[:300]}")
            finally:
                try:
                    os.remove(local)
                except OSError:
                    pass

        inner = self.build_command(packages, events, seed, throttle_ms,
                                   verbose, flags, pcts, whitelist_remote)

        def launch(use_timeout: bool) -> tuple[str, str]:
            wrapped = (f"timeout {int(duration_s)} {inner}"
                       if use_timeout and duration_s else inner)
            shell_cmd = f"nohup {wrapped} > {log_path} 2>&1 & echo $!"
            r = self._adb(["shell", shell_cmd], timeout=start_timeout_s)
            text = ((r.stdout or "") + (r.stderr or "")).strip()
            pid = next((ln.strip() for ln in text.splitlines()
                        if ln.strip().isdigit()), "")
            return pid, text

        pid, output = launch(bool(duration_s))
        used_timeout = bool(duration_s)
        if not pid or not self.alive(pid):
            # 部分 ROM 没有 toybox timeout：去掉外层 timeout 再试一次
            head = self.tail(log_path, 10) if pid else []
            if used_timeout and any(
                    "not found" in line or "inaccessible" in line
                    for line in head):
                pid, output = launch(False)
                used_timeout = False
        if not pid:
            self.remove_whitelist(whitelist_remote)
            raise RuntimeError(f"Monkey 启动失败：{output[:300] or '未返回 PID'}")
        if not self.alive(pid):
            head = "\n".join(self.tail(log_path, 10))
            self.remove_whitelist(whitelist_remote)
            raise RuntimeError(f"Monkey 启动后立即退出：{head[:500] or output[:300]}")

        return {"pid": pid, "log_path": log_path, "command": inner,
                "events": int(events), "duration_s": int(duration_s or 0),
                "device_timeout": used_timeout, "packages": packages,
                "whitelist_remote": whitelist_remote}

    # ---------- 状态 ----------
    def alive(self, pid: str) -> bool:
        if not pid:
            return False
        r = self._adb(["shell", "kill", "-0", str(pid)], timeout=5)
        return r.returncode == 0

    def tail(self, log_path: str, lines: int = 20) -> list[str]:
        if not log_path:
            return []
        r = self._adb(["shell", "tail", "-n", str(max(1, int(lines))),
                       log_path], timeout=10)
        text = (r.stdout or "").strip()
        return text.splitlines() if text else []

    # ---------- 停止 ----------
    def remove_whitelist(self, whitelist_remote: str) -> None:
        if not whitelist_remote:
            return
        try:
            self._adb(["shell", "rm", "-f", whitelist_remote], timeout=10)
        except Exception:
            pass

    def stop(self, pid: str = "", whitelist_remote: str = "") -> dict:
        pid = str(pid or "").strip()
        if not pid:
            pid = self.running_pid()
        killed = []
        if pid:
            self._adb(["shell", "kill", "-9", pid], timeout=5)
            killed.append(pid)
        # 兜底：pgrep 再确认一次（防止 PID 记录过期或起停错位）
        deadline = time.time() + 5
        while time.time() < deadline:
            if not self.running_pid():
                break
            time.sleep(0.3)
        leftover = self.running_pid()
        if leftover:
            self._adb(["shell", "kill", "-9", leftover], timeout=5)
            killed.append(leftover)
        self.remove_whitelist(whitelist_remote)
        return {"killed": killed, "alive": bool(self.running_pid())}
