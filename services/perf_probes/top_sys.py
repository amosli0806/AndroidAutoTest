# services/perf_probes/top_sys.py
"""整机 CPU 探针（设备整体占用率，非所选进程）。

数据源优先 `top -b -n 1` 的汇总行，实测（车机，8 核）：

    800%cpu 144%user   0%nice  59%sys 581%idle   0%iow  13%irq   3%sirq   0%host

**口径关键**：`total` 是「核数 × 100」（8 核 = 800%），所以

    cpu_pct = (total - idle) / total * 100      ← 必须除以 total

若写成 `100 - idle`，8 核设备会得到 -481% 这种离谱值；写成 `total - idle` 又
会得到 219%（多核累计），和「整机占用率」的直觉不符。这里统一给 0~100 的整机占用率，
另外把核数与多核累计值一起返回，界面上可以显示成「27%（8 核累计 219%）」。

兜底：部分精简 ROM 的 top 不认 `-b`（会按屏幕高度截断），此时退回
`/proc/stat` 的 jiffies 差分——与 top 同源，数值一致。两者都拿不到就抛 ProbeError，
明确报「不支持」，绝不静默返回 0。

注意：本文件的正则用 `[0-9.]` / `[ \t]+` 书写，**不写 \\d / \\s** —— 本项目的编辑脚本
会吞反斜杠（2026-10-25 踩过），无转义写法更稳。
"""
import re
import time

from services.perf_compat import shell_text_checked, GUI_TIMEOUT_HEAVY
from .base import PerfProbe, ProbeError

_TOP_RE = re.compile(
    r"([0-9.]+)%cpu[ \t]+([0-9.]+)%user[ \t]+([0-9.]+)%nice[ \t]+"
    r"([0-9.]+)%sys[ \t]+([0-9.]+)%idle[ \t]+([0-9.]+)%iow"
    r"(?:[ \t]+([0-9.]+)%irq[ \t]+([0-9.]+)%sirq[ \t]+([0-9.]+)%host)?"
)


def parse_top_cpu(out: str) -> dict:
    """解析 top 的 CPU 汇总行。拿不到汇总行抛 ProbeError（交给调用方兜底）。

    返回两套数值：
      * `*_raw`：**多核累计**（与 top 原样一致，如 6 核上限 600）——报告用；
      * `*_pct`：归一化到 0~100——界面卡片用。
    百度报告的 D_total / D_busy / D_user / D_* 就是多核累计口径。
    """
    m = _TOP_RE.search(out or "")
    if not m:
        raise ProbeError("top 输出里没有 CPU 汇总行（该设备可能不支持 top -b）")
    vals = [float(x) if x is not None else 0.0 for x in m.groups()]
    total, user, nice, sys, idle, iow, irq, sirq, host = vals
    if total <= 0:
        raise ProbeError("top 汇总行的 %cpu 为 0，无法计算占用率")
    busy = max(0.0, total - idle)

    def _norm(v):
        return round(v / total * 100.0, 1)

    return {
        "total_pct": round(total, 1),                 # 核数 × 100（容量）
        "cores": int(round(total / 100.0)),
        # 多核累计（百度 D_* 口径）
        "busy_raw": round(busy, 1),
        "user_raw": round(user, 1),
        "nice_raw": round(nice, 1),
        "sys_raw": round(sys, 1),
        "idle_raw": round(idle, 1),
        "iow_raw": round(iow, 1),
        "irq_raw": round(irq, 1),
        "sirq_raw": round(sirq, 1),
        "host_raw": round(host, 1),
        # 归一化（界面卡片继续用）
        "cpu_pct": _norm(busy),
        "user_pct": _norm(user),
        "sys_pct": _norm(sys),
        "iow_pct": _norm(iow),
        "idle_pct": _norm(idle),
    }


class TopSysProbe(PerfProbe):
    key = "sys_cpu"
    title = "整机 CPU"
    scope = "system"
    default_interval = 15.0     # 与系统内存统一：整机类指标变化慢，15 秒足够

    def __init__(self):
        super().__init__()
        self._last_stat = None
        self._cores = 0
        self.source = ""        # 'top' / 'proc_stat'，界面可以据此提示降级

    def sample_once(self) -> dict:
        ok, out = shell_text_checked(
            self.device, "top -b -n 1 -m 1", timeout=GUI_TIMEOUT_HEAVY)
        if ok and out.strip():
            try:
                data = parse_top_cpu(out)
                self.source = "top"
                data["ts"] = time.time()
                return data
            except ProbeError:
                pass                     # 落到 /proc/stat 兜底
        data = self._sample_proc_stat()
        data["ts"] = time.time()
        return data

    # ---------- 兜底：/proc/stat 差分 ----------
    def _sample_proc_stat(self) -> dict:
        ok, out = shell_text_checked(
            self.device, "cat /proc/stat | grep '^cpu'", timeout=GUI_TIMEOUT_HEAVY)
        if not ok or "cpu" not in out:
            raise ProbeError("top 不支持批量模式，/proc/stat 也读不到")
        lines = [ln for ln in out.splitlines() if ln.startswith("cpu")]
        if not self._cores:
            # 第 1 行是汇总、其余每行一个核
            self._cores = max(0, len(lines) - 1)
        head = lines[0].split()
        vals = [float(x) for x in head[1:9]]
        while len(vals) < 8:            # user nice system idle iow irq sirq steal
            vals.append(0.0)
        total = sum(vals)
        now = time.monotonic()
        prev = self._last_stat
        self._last_stat = (total, vals)
        if prev is None:
            # 首次只能建基准，交给下一次（不产出假的 0）
            raise ProbeError("整机 CPU 首次采样，等待下一次差分")
        d_total = total - prev[0]
        if d_total <= 0:
            raise ProbeError("两次采样的 jiffies 差为 0，跳过本轮")
        prev_vals = prev[1]
        d = [max(0.0, vals[i] - prev_vals[i]) for i in range(8)]
        self.source = "proc_stat"
        cores = self._cores or 0
        cap = max(1, cores) * 100.0
        raw = [x / d_total * cap for x in d]
        busy_raw = cap - raw[3]           # total - idle（与 top 的 busy 口径一致）

        def _norm(v):
            return round(v / cap * 100.0, 1) if cap > 0 else 0.0

        return {
            "total_pct": round(cap, 1),
            "cores": cores,
            "cpu_pct": round(busy_raw / cap * 100.0, 1),
            "busy_raw": round(busy_raw, 1),
            "user_raw": round(raw[0], 1),
            "nice_raw": round(raw[1], 1),
            "sys_raw": round(raw[2], 1),
            "idle_raw": round(raw[3], 1),
            "iow_raw": round(raw[4], 1),
            "irq_raw": round(raw[5], 1),
            "sirq_raw": round(raw[6], 1),
            "host_raw": round(raw[7], 1),   # /proc/stat 的 steal，对应 top 的 %host
            "user_pct": _norm(raw[0]),
            "sys_pct": _norm(raw[2]),
            "iow_pct": _norm(raw[4]),
            "idle_pct": _norm(raw[3]),
            "degraded": True,           # 走兜底时界面提示「top 不可用，已降级」
        }
