# services/perf_probes/netdev.py
"""网卡流量探针（设备整机口径，非某个应用）。

数据源 `cat /proc/net/dev`，每行一个网卡、16 列：

    wlan0: 2938013457 2419190 0 0 0 0 0 0 180116650 1269938 0 0 0 0 0 0
           └ rx_bytes ┘                             └ tx_bytes ┘
    索引：0=rx_bytes … 8=tx_bytes（接收 8 列之后紧跟发送 8 列）

**为什么要算差分**：这个文件给的是「开机以来累计字节数」，不是速率。
所以每轮保存每张网卡的 (rx, tx, 单调时钟)，下一轮相减除以时间差得 KB/s。

**为什么不求和**：实测车机上同时存在 wlan0 和 rmnet_data0..5 / rmnet_ipa0，
后者是同一蜂窝通道的不同层，累加会重复计数（同样的字节被算两三遍）。
所以主值取「累计流量最大的那张网卡」（通常是 wlan0 或 rmnet_data0），
其余网卡放明细里，不提供"合计"。

**也不排除 rmnet**：车机可能只有蜂窝没 WiFi，那时 rmnet 就是唯一真实通道。

口径：整机（本机所有应用的流量合计），与「流量速率」卡的所选应用口径不同。
"""
import time

from services.perf_compat import shell_text_checked, GUI_TIMEOUT_HEAVY
from .base import PerfProbe, ProbeError

SKIP_IFACES = ("lo",)


def parse_netdev(out: str) -> list:
    """解析 /proc/net/dev，返回 [{name, rx_bytes, tx_bytes}, ...]（含全 0 的网卡）。"""
    rows = []
    for line in (out or "").splitlines():
        s = line.strip()
        if not s or s.startswith("Inter-") or s.startswith("face"):
            continue
        name, sep, rest = s.partition(":")
        if not sep:
            continue
        name = name.strip()
        if not name or name in SKIP_IFACES:
            continue
        fields = rest.split()
        if len(fields) < 16:
            continue
        try:
            rx = int(fields[0])
            tx = int(fields[8])
        except ValueError:
            continue
        rows.append({"name": name, "rx_bytes": rx, "tx_bytes": tx})
    # 只留有实际流量的网卡：车机上常年存在 sit0 / gretap0 / p2p0 / dummy0 以及
    # rmnet_data0..5 这类虚拟层，字节数只有几百，列出来会把真正在用的网卡淹掉。
    # 阈值取 64KB（累计），刚连上的网卡会稍晚一点出现，可以接受。
    real = [r for r in rows if r["rx_bytes"] >= 65536 or r["tx_bytes"] >= 65536]
    return real or rows      # 全都没到阈值（刚开机）时退回原始列表，避免"没有网卡"



class NetDevProbe(PerfProbe):
    key = "netdev"
    title = "网卡流量"
    scope = "system"
    default_interval = 15.0     # 与另外两个整机探针一致

    def __init__(self):
        super().__init__()
        self._last = {}         # name -> (rx, tx, 单调时钟)
        self._primary = ""

    def sample_once(self):
        ok, out = shell_text_checked(
            self.device, "cat /proc/net/dev", timeout=GUI_TIMEOUT_HEAVY)
        if not ok or ":" not in (out or ""):
            raise ProbeError("/proc/net/dev 读取失败（通道异常 / 超时）")
        rows = parse_netdev(out)
        if not rows:
            raise ProbeError("/proc/net/dev 里没有网卡记录")

        now = time.monotonic()
        prev, self._last = self._last, {r["name"]: (r["rx_bytes"], r["tx_bytes"], now)
                                        for r in rows}
        if not prev:
            # 首轮只能建基准（没有可相减的上一份），不产出数据点
            return None

        ifaces = []
        for r in rows:
            old = prev.get(r["name"])
            if old is None:                 # 本轮新出现的网卡：只能从下轮开始算速率
                continue
            dt = now - old[2]
            if dt <= 0:
                continue
            d_rx = max(0, r["rx_bytes"] - old[0])
            d_tx = max(0, r["tx_bytes"] - old[1])
            if d_rx < 0 or d_tx < 0:        # 网卡重置过，跳过这轮
                continue
            ifaces.append({
                "name": r["name"],
                "rx_kbps": round(d_rx / dt / 1024.0, 1),
                "tx_kbps": round(d_tx / dt / 1024.0, 1),
                "rx_mb": round(r["rx_bytes"] / 1048576.0, 1),
                "tx_mb": round(r["tx_bytes"] / 1048576.0, 1),
            })

        if not ifaces:
            raise ProbeError("本轮没有可计算的网卡差分（网卡刚起停？）")

        # 主网卡：累计流量最大的一张（不做多网卡求和，见文件头说明）
        primary = max(ifaces, key=lambda x: x["rx_mb"] + x["tx_mb"])
        self._primary = primary["name"]
        return {
            "ts": time.time(),
            "primary": primary["name"],
            "rx_kbps": primary["rx_kbps"],
            "tx_kbps": primary["tx_kbps"],
            "rx_total_mb": primary["rx_mb"],
            "tx_total_mb": primary["tx_mb"],
            "ifaces": ifaces,
            "active_count": len(ifaces),
        }
