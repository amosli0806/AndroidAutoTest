# services/perf_probes/meminfo_sys.py
"""系统内存探针（整机口径）。

数据源：`dumpsys meminfo`（**不带包名**）末尾的汇总段，实测格式：

    Total RAM: 7,541,192K (status normal)
     Free RAM: 2,046,227K (  451,235K cached pss + 1,238,336K cached kernel +   356,656K free)
     Used RAM: 6,127,872K (5,366,880K used pss +   760,992K kernel)
     Lost RAM:   565,547K
         ZRAM:   647,560K physical used for 2,414,028K in swap (6,291,452K total swap)

坑：
  * 数字带**千分位逗号**，单位是 **K**（不是 B 也不是 MB）——正则要 `[\\d,]+` 且去逗号；
  * 这份 dump 很大（几百 KB），所以取数用 grep 过滤，不要整段拉回来；
  * 部分 ROM 没有 ZRAM 行，缺了就当 0。
"""
import os
import re
import time

from services.perf_compat import shell_text_checked, GUI_TIMEOUT_HEAVY
from .base import PerfProbe, ProbeError

_CPU_RE = {
    "total": re.compile(r"Total RAM:[ ]*([0-9,]+)K"),
    "free": re.compile(r"Free RAM:[ ]*([0-9,]+)K"),
    "used": re.compile(r"Used RAM:[ ]*([0-9,]+)K"),
    "lost": re.compile(r"Lost RAM:[ ]*([0-9,]+)K"),
    "zram": re.compile(r"ZRAM:[ ]*([0-9,]+)K"),
}


def parse_sys_mem(out: str) -> dict:
    """把 `dumpsys meminfo` 的汇总段解析成 MB / 百分比。解析不到 Total 视为失败。"""
    vals = {}
    for key, pattern in _CPU_RE.items():
        m = pattern.search(out or "")
        if m:
            vals[key] = int(m.group(1).replace(",", "")) / 1024.0
    if "total" not in vals or vals["total"] <= 0:
        raise ProbeError("dumpsys meminfo 输出里没有 Total RAM")
    total = vals["total"]
    used = vals.get("used") or 0.0
    return {
        "total_mb": round(total, 1),
        "free_mb": round(vals.get("free") or 0.0, 1),
        "used_mb": round(used, 1),
        "lost_mb": round(vals.get("lost") or 0.0, 1),
        "zram_mb": round(vals.get("zram") or 0.0, 1),
        "used_pct": round(used / total * 100.0, 1),
    }


class MemInfoSysProbe(PerfProbe):
    key = "sys_mem"
    title = "系统内存"
    scope = "system"
    # 默认 15 秒：dumpsys meminfo 一次要 3~5 秒，5 秒节奏几乎连轴转，
    # 而且会把「整机 CPU」抬高约 5 个百分点（探针自己的负载也会被统计进去）
    default_interval = 15.0
    RAW_EVERY = 4           # 每 4 次（约 1 分钟）落一份原始 dump，供事后比对

    def __init__(self):
        super().__init__()
        self._count = 0

    def sample_once(self) -> dict:
        ok, out = shell_text_checked(
            self.device,
            "dumpsys meminfo | grep -E 'Total RAM|Free RAM|Used RAM|Lost RAM|ZRAM'",
            timeout=GUI_TIMEOUT_HEAVY,
        )
        if not ok or not out.strip():
            raise ProbeError("dumpsys meminfo 执行失败（通道异常 / 超时）")
        data = parse_sys_mem(out)
        data["ts"] = time.time()
        self._count += 1
        if self._count % self.RAW_EVERY == 1:
            self._dump_raw(out, data["ts"])
        return data

    def _dump_raw(self, out: str, ts: float):
        """原始输出留一份（失败不影响采集）。"""
        try:
            d = os.path.join(self.out_dir, "meminfo_sys")
            os.makedirs(d, exist_ok=True)
            path = os.path.join(d, time.strftime("meminfo_%Y%m%d_%H%M%S.txt", time.localtime(ts)))
            with open(path, "w", encoding="utf-8") as f:
                f.write(out)
        except Exception:
            pass
