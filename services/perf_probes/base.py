# services/perf_probes/base.py
"""探针的公共契约。

探针 = 一条**独立于主采样循环**的旁路采集，自带间隔与后台线程：
  * 主循环（PerfService.collect_sample）每 5 秒要跑 CPU/内存/帧率/流量四条命令，
    再塞进去会把节奏拖垮、还会互相抢 adb 通道；
  * 探针的失败不应影响主曲线，所以单次失败只记在 latest()['error'] 里，不抛到界面。

生命周期：bind() → start(interval) → （GUI 定时读 latest()）→ stop() / wait()。
停止顺序由 PerfController._finalize_probes() 统一保证：先全部 stop，再统一 wait。
"""
import threading
import time


class ProbeError(Exception):
    """探针采集失败（通道异常 / 输出为空 / 解析不到字段）。"""


class PerfProbe:
    key = ""                # 'sys_mem'：配置记忆与产物子目录都用它
    title = ""              # '系统内存'
    scope = "system"        # 'process' | 'app' | 'system'
    default_interval = 5.0  # 秒

    def __init__(self):
        self.device = None
        self.serial = ""
        self.out_dir = ""
        self._pid_of = None           # 需要"当前所选进程 PID"的探针用
        self._stop_evt = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self._latest = {}
        self._samples = []
        self._interval = self.default_interval

    # ---------- 运行环境 ----------
    def bind(self, device, serial, out_dir, pid_provider=None):
        self.device = device
        self.serial = serial or ""
        self.out_dir = out_dir or ""
        self._pid_of = pid_provider

    # ---------- 生命周期 ----------
    def start(self, interval=None):
        self._interval = float(interval or self.default_interval)
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._run, name=f"perf-probe-{self.key or 'probe'}", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_evt.set()

    def wait(self, timeout=15):
        t = self._thread
        if t is not None:
            t.join(timeout)
            if t.is_alive():
                # 线程没退出（命令卡住）：不死等，交给 daemon 属性随进程退出
                pass
            self._thread = None

    def _run(self):
        while not self._stop_evt.is_set():
            try:
                data = self.sample_once()
                with self._lock:
                    self._latest = data
                    self._samples.append(data)
            except Exception as e:      # 单次失败不退出，界面能看到原因
                with self._lock:
                    self._latest = {**self._latest, "error": str(e), "ts": time.time()}
            self._stop_evt.wait(self._interval)

    # ---------- 子类实现 ----------
    def sample_once(self) -> dict:
        """采一次，返回结构化结果（必须带 ts）。失败请抛 ProbeError。"""
        raise NotImplementedError

    # ---------- 结果读取（线程安全快照） ----------
    def latest(self) -> dict:
        with self._lock:
            return dict(self._latest)

    def samples(self) -> list:
        with self._lock:
            return [dict(s) for s in self._samples]
