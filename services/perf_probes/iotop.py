# services/perf_probes/iotop.py
"""磁盘 IO 探针（整机，基于 toybox 自带的 iotop）。

数据源：`toybox iotop -n 1 -b`，实测输出（车机，非 root）：

    Totals: DREAD:      0, DWRITE:      0, SWAP:      0, DIO:      0
      PID PR USER          DREAD DWRITE   SWAP   [DIO]COMM
    12184 20 shell             0      0      0      0 toybox
    12104 20 root              0      0      0      0 [kworker/3:0-events]

**必须先 root**：DREAD / DWRITE 来自 `/proc/<pid>/io`，而 Android 上 shell 用户
读别的应用会被 SELinux 拒（实测 `cat /proc/<pid>/io` = Permission denied，
`/proc/diskstats`、`/sys/block/*/stat` 同样被拒）。无 root 时 iotop 仍会"正常输出"，
只是所有进程都显示 0 —— 那是假数据，比报错更糟，所以这里主动探测 root：
没有 root 就抛 ProbeError("需要 root 权限")，界面上明确提示，不产出 0。

**数值口径**：DREAD / DWRITE 是进程启动以来的累计字节（与 /proc/<pid>/io 一致），
所以本探针按「累计值差分」算速率，与网卡流量探针同一套做法。
"""
import re
import time

from services.perf_compat import shell_text_checked, GUI_TIMEOUT_HEAVY
from .base import PerfProbe, ProbeError

_TOTALS_RE = re.compile(
    r"Totals:\s*DREAD:\s*([0-9]+),\s*DWRITE:\s*([0-9]+)", re.ASCII)


def parse_iotop(out: str) -> dict:
    """解析 toybox iotop -b 的输出：Totals 汇总 + 每进程明细。"""
    totals = {"dread": 0, "dwrite": 0}
    m = _TOTALS_RE.search(out or "")
    if m:
        totals = {"dread": int(m.group(1)), "dwrite": int(m.group(2))}

    procs = []
    for line in (out or "").splitlines():
        s = line.strip()
        if not s or s.startswith("Totals:") or s.startswith("PID"):
            continue
        # 列：PID PR USER DREAD DWRITE SWAP [DIO] COMM（COMM 可能带空格）
        # 有 DIO 列时共 8 段，没有时 7 段 —— 按段数判断，别把 DIO 的数字混进进程名
        parts = s.split(None, 7)
        if len(parts) < 6 or not parts[0].isdigit():
            continue
        try:
            dread = int(parts[3])
            dwrite = int(parts[4])
        except (ValueError, IndexError):
            continue
        comm = (parts[7] if len(parts) >= 8 else (parts[6] if len(parts) == 7 else "")).strip()
        procs.append({"pid": parts[0], "user": parts[2],
                      "dread": dread, "dwrite": dwrite, "comm": comm})
    return {"totals": totals, "procs": procs}


class IoTopProbe(PerfProbe):
    key = "iotop"
    title = "磁盘 IO"
    scope = "system"
    default_interval = 15.0

    def __init__(self):
        super().__init__()
        self._last = None          # (dread, dwrite, 单调时钟)
        self._prefix = None        # None=未探测; ''=直连; 'su -c '=提权
        self.root = False

    # ---------- root 探测 ----------
    def _resolve_prefix(self) -> str:
        """返回执行前缀（'' 或 'su -c '），没有 root 就抛错。结果缓存。"""
        if self._prefix is not None:
            return self._prefix
        ok, out = shell_text_checked(self.device, "id -u", timeout=GUI_TIMEOUT_HEAVY)
        if ok and out.strip() == "0":
            self._prefix, self.root = "", True
            return ""
        ok, out = shell_text_checked(self.device, "su -c id -u", timeout=GUI_TIMEOUT_HEAVY)
        if ok and out.strip().splitlines() and out.strip().splitlines()[-1].strip() == "0":
            self._prefix, self.root = "su -c ", True
            return "su -c "
        raise ProbeError("需要 root 权限")

    def _run_iotop(self) -> str:
        prefix = self._resolve_prefix()
        if prefix:
            cmd = "su -c 'toybox iotop -n 1 -b'"
        else:
            cmd = "toybox iotop -n 1 -b"
        ok, out = shell_text_checked(self.device, cmd, timeout=GUI_TIMEOUT_HEAVY)
        if not ok or "DREAD" not in (out or ""):
            raise ProbeError("设备不支持 iotop（toybox 里没有该命令）")
        return out

    # ---------- 采样 ----------
    def sample_once(self):
        out = self._run_iotop()
        parsed = parse_iotop(out)
        totals = parsed["totals"]
        now = time.monotonic()
        prev = self._last
        self._last = (totals["dread"], totals["dwrite"], now)
        if prev is None:
            # 首轮只能建基准（累计值没有可相减的上一份）
            return None
        dt = now - prev[2]
        if dt <= 0:
            raise ProbeError("两次采样的时间差为 0，跳过本轮")

        d_read = max(0, totals["dread"] - prev[0])
        d_write = max(0, totals["dwrite"] - prev[1])
        if d_read < 0 or d_write < 0:       # 计数被重置（重启/清空），跳过
            return None

        # Top 进程：按累计读写字节排序，取前 3（放 tooltip / 报告）
        procs = sorted(parsed["procs"],
                       key=lambda p: p["dread"] + p["dwrite"], reverse=True)[:3]
        top = [{"comm": p["comm"] or f"pid {p['pid']}",
                "read_mb": round(p["dread"] / 1048576.0, 1),
                "write_mb": round(p["dwrite"] / 1048576.0, 1)} for p in procs]

        return {
            "ts": time.time(),
            "read_kbps": round(d_read / dt / 1024.0, 1),
            "write_kbps": round(d_write / dt / 1024.0, 1),
            "read_total_mb": round(totals["dread"] / 1048576.0, 1),
            "write_total_mb": round(totals["dwrite"] / 1048576.0, 1),
            "top": top,
            "proc_count": len(parsed["procs"]),
        }
