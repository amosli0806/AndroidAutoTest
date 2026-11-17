# services/weak_net.py
"""弱网模拟（tc netem / htb）执行器，无 Qt 依赖。

从 `views/adb_panels/weak_network_panel.py` 抽出命令组装与下发逻辑：

* 限带宽：`htb` 根队列 + 1:1 / 1:11 class；
* 延迟 / 丢包：netem。能限带宽时挂在 htb 子类下，否则直接挂 root；
* 内核没有 netem 时：延迟/丢包直接忽略，只下发限带宽（与 GUI 行为一致）。

TTL 由**设备端** `sh -c 'sleep N; tc qdisc del ...'` 兜底：即使 MCP 或电脑
中途退出，规则也会按时自动清除，不会把设备一直留在弱网状态。
"""
from __future__ import annotations

import re
import subprocess
import sys
from typing import Optional

from utils.adb_path import get_adb_path

NETEM_MISSING_HINT = (
    "当前内核不支持 netem（延迟 / 丢包做不到）。"
    "可改用「带宽限制」，或换内核带 sch_netem 的设备。")

_IFACE_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")


def _creationflags():
    return subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def friendly_tc_error(raw: str) -> str:
    text = (raw or "").strip()
    low = text.lower()
    if "rtnetlink" in low and ("no such file or directory" in low
                               or "operation not supported" in low):
        return NETEM_MISSING_HINT
    if "cannot find device" in low:
        return f"网卡不存在，请换设备上真实存在的网卡（设备返回：{text}）"
    if "not permitted" in low:
        return "权限不足，弱网模拟需要 root"
    if "exclusivity flag on" in low or "file exists" in low:
        return "该网卡上已有队列规则，请先清空再应用"
    return text


def _netem_qdisc(iface: str, parent: str, delay: int, loss: float) -> str:
    cmd = f"qdisc add dev {iface} {parent} netem"
    if delay > 0:
        cmd += f" delay {delay}ms"
    if loss > 0:
        cmd += f" loss {loss}%"
    return cmd


def build_tc_commands(iface: str, delay: int, loss: float, bandwidth: int,
                      netem_ok: bool):
    needs_netem = delay > 0 or loss > 0
    if needs_netem and not netem_ok:
        needs_netem = False
    if not needs_netem and bandwidth <= 0:
        return [], ("当前内核不支持 netem，且没有填写带宽限制"
                    if (delay > 0 or loss > 0)
                    else "请至少填一项：延迟 / 丢包率 / 带宽限制")

    commands = []
    if bandwidth > 0:
        commands.append(f"qdisc add dev {iface} root handle 1: htb default 11")
        commands.append(
            f"class add dev {iface} parent 1: classid 1:1 htb rate {bandwidth}kbit")
        commands.append(
            f"class add dev {iface} parent 1:1 classid 1:11 htb rate {bandwidth}kbit")
        if needs_netem:
            commands.append(_netem_qdisc(iface, "parent 1:11 handle 10:",
                                         delay, loss))
    else:
        commands.append(_netem_qdisc(iface, "root", delay, loss))
    return commands, ""


class WeakNetRunner:
    def __init__(self, serial: str, adb_path: Optional[str] = None):
        self.serial = serial
        self.adb = adb_path or get_adb_path()
        self._tc_path = ""

    def _shell(self, cmd: str, timeout: float = 10):
        return subprocess.run(
            [self.adb, "-s", self.serial, "shell", cmd],
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
            creationflags=_creationflags())

    def is_root(self) -> bool:
        try:
            out = self._shell("id", timeout=5).stdout or ""
            return "uid=0" in out
        except Exception:
            return False

    def tc_path(self) -> str:
        if self._tc_path:
            return self._tc_path
        for cand in ("tc", "/system/bin/tc", "/system/xbin/tc"):
            try:
                r = self._shell(f"command -v {cand} 2>/dev/null", timeout=5)
                found = (r.stdout or "").strip().splitlines()
                if r.returncode == 0 and found:
                    self._tc_path = found[0].strip()
                    return self._tc_path
            except Exception:
                pass
        return ""

    def _tc(self, args: str, timeout: float = 15):
        tc = self.tc_path()
        if not tc:
            raise RuntimeError("设备上没有 tc 命令")
        return self._shell(f"{tc} {args}", timeout=timeout)

    def netem_ok(self) -> bool:
        if not self.tc_path():
            return False
        try:
            self._tc("qdisc del dev lo root 2>/dev/null")
            r = self._tc("qdisc add dev lo root netem 2>&1")
            ok = r.returncode == 0 and "RTNETLINK" not in \
                ((r.stdout or "") + (r.stderr or ""))
            self._tc("qdisc del dev lo root 2>/dev/null")
            return ok
        except Exception:
            return False

    def capability(self) -> dict:
        root = self.is_root()
        tc = self.tc_path()
        netem = self.netem_ok() if (root and tc) else False
        return {"root": root, "tc": tc != "", "tc_path": tc,
                "netem": netem}

    def apply(self, iface: str, delay: int = 0, loss: float = 0.0,
              bandwidth: int = 0, ttl_s: int = 600) -> dict:
        iface = (iface or "").strip()
        if not _IFACE_RE.match(iface):
            raise ValueError(f"网卡名不合法：{iface!r}")
        if not self.is_root():
            raise PermissionError("弱网模拟需要 root：adb shell id 不是 uid=0")
        if not self.tc_path():
            raise RuntimeError("设备上没有 tc 命令")
        netem_ok = self.netem_ok()
        commands, error = build_tc_commands(iface, int(delay or 0),
                                            float(loss or 0),
                                            int(bandwidth or 0), netem_ok)
        if error:
            raise ValueError(error)

        self._tc(f"qdisc del dev {iface} root 2>/dev/null")
        for cmd in commands:
            r = self._tc(cmd)
            if r.returncode != 0:
                raise RuntimeError(friendly_tc_error(
                    (r.stderr or "") + (r.stdout or "")))
        if ttl_s and ttl_s > 0:
            tc = self.tc_path()
            guard = (f"sleep {int(ttl_s)}; {tc} qdisc del dev {iface} root "
                     f"2>/dev/null")
            self._shell(f"nohup sh -c {_quote(guard)} >/dev/null 2>&1 &",
                        timeout=8)
        return {"interface": iface, "delay_ms": int(delay or 0),
                "loss_pct": float(loss or 0),
                "bandwidth_kbps": int(bandwidth or 0),
                "netem": netem_ok, "commands": commands,
                "ttl_s": int(ttl_s or 0)}

    def clear(self, iface: str) -> dict:
        iface = (iface or "").strip()
        if not _IFACE_RE.match(iface):
            raise ValueError(f"网卡名不合法：{iface!r}")
        if not self.is_root():
            raise PermissionError("清除弱网规则需要 root")
        r = self._tc(f"qdisc del dev {iface} root")
        detail = ((r.stderr or "") + (r.stdout or "")).strip()
        already_clear = ("no such file" in detail.lower()
                        or "cannot find device" in detail.lower())
        if r.returncode != 0 and not already_clear:
            raise RuntimeError(friendly_tc_error(detail))
        return {"interface": iface, "cleared": True,
                "already_clear": already_clear}

    def show(self, iface: str) -> str:
        r = self._tc(f"qdisc show dev {iface}", timeout=10)
        return ((r.stdout or "") + (r.stderr or "")).strip()


def _quote(text: str) -> str:
    return "'" + str(text).replace("'", "'\\''") + "'"
