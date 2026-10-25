# services/perf_probes/__init__.py
"""性能检测的旁路探针（系统内存 / 整机 CPU / 网卡流量 …）。

与主采样（PerfService，5 秒一轮采 CPU/内存/帧率/流量）分开：
探针各有自己的间隔、有的要长期跑，塞进主循环会把节奏拖垮。
契约见 base.py；每个探针自己起后台线程，GUI 只读 latest()。
"""
from .base import PerfProbe, ProbeError          # noqa: F401
from .meminfo_sys import MemInfoSysProbe         # noqa: F401
from .top_sys import TopSysProbe                 # noqa: F401
