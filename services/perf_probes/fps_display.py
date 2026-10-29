# services/perf_probes/fps_display.py
"""FPS 帧率探针（按显示屏 / 图层，兼容副屏）。

两条数据源，互相补位：
  1. **SurfaceFlinger**（通用）：`--list` 找到目标应用的图层，`--latency`
     取帧时间戳算 FPS；再用 `dumpsys display` + SurfaceFlinger 的图层尺寸表
     把图层归到显示屏（车机的仪表副屏通常是应用自建的 VirtualDisplay，
     分辨率与主屏不同，按尺寸能对上）。
  2. **应用日志**（精确，百度同款）：常驻 `logcat` 流里带 `SDK:FPS_C` 的行，
     解析 `fps: 15.9 / 16  mapType:0 current:...@cbc8fa2`，其中 `cbc8fa2`
     就是百度报表里的 ControllerID。日志**逐条入库**（带日志时间戳），
     统计与曲线都按事件算，和百度报表口径一致；流起不来时退回轮询日志尾部。
     没有该日志的应用自动只用 SF 数据。

采样结果：
    {"ts", "layers":[{name, display, fps, refresh, frames}],
     "controllers":[{id, fps, s_fps, map_type}],
     "displays":[{name, width, height, refresh}]}
事件序列（逐条 FPS 日志）：[{"ts", "id", "fps", "s_fps", "map_type"}, ...]
"""
import os
import re
import statistics
import subprocess
import threading
import time
from datetime import datetime, timedelta

from services.perf_compat import WORKER_TIMEOUT, shell_text_checked
from .base import PerfProbe, ProbeError

FPS_LOG_TAG = "SDK:FPS_C"
MAX_LAYERS = 8
LAYER_TTL = 60.0        # 图层列表缓存
META_TTL = 60.0         # 显示屏 / 图层栈 / 尺寸表缓存（虚拟屏可能中途才创建）
CONTROLLER_TTL = 300.0  # Controller 超过这么久没再上报才丢弃（5 分钟内沿用最新值）
MAX_FPS = 240.0

# 这些图层是窗口/动画的辅助层，测出来不是用户看到的画面
_SKIP_MARKS = ("Background for ", "Bounds for ", "ActivityRecord",
               "InputSink", "animation-leash", "Bounds for - ")
_HEX_PREFIX = re.compile(r"^[0-9a-f]{6,}\s+")
_LOG_RE = re.compile(
    r"SDK:FPS_C:.*?:fps:\s+([\d.]+)\s+/\s+([\d.]+)\s+mapType:(\S+)\s+"
    r"current:.*?@(\w+)")
_LOG_TIME = re.compile(r"^(\d{2})-(\d{2})\s+(\d{2}):(\d{2}):(\d{2})\.(\d+)")


def _parse_log_dt(line: str):
    """解析 logcat 行首的 `MM-DD HH:MM:SS.mmm`（补当前年份）。"""
    m = _LOG_TIME.match(line.strip())
    if not m:
        return None
    mon, day, hh, mm, ss, frac = m.groups()
    now = datetime.now()
    try:
        dt = datetime(now.year, int(mon), int(day), int(hh), int(mm), int(ss),
                      int(float("0." + frac) * 1_000_000))
    except (ValueError, TypeError):
        return None
    if dt - now > timedelta(days=1):        # 跨年：设备时间是去年
        try:
            dt = dt.replace(year=now.year - 1)
        except ValueError:
            return None
    return dt


def parse_latency(out: str):
    """解析 `SurfaceFlinger --latency`：返回 (刷新率fps, 采样区间平均fps, 帧数)。

    第一行是刷新周期（ns），后面每行 `<desired> <actual> <ready>`；
    actual=0 表示该槽位没有新帧。FPS 用相邻帧时间差的**中位数**算，
    避免偶尔一次卡顿或长时间空闲把均值拉偏。
    """
    lines = [ln for ln in (out or "").splitlines() if ln.strip()]
    if not lines:
        return None, None, 0
    try:
        period = float(lines[0].strip())
    except ValueError:
        return None, None, 0
    refresh = round(1e9 / period, 1) if period > 0 else None

    actuals = []
    for line in lines[1:]:
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            actual = int(parts[1])
        except ValueError:
            continue
        if actual > 0:
            actuals.append(actual)
    actuals.sort()
    if len(actuals) < 2:
        return refresh, (0.0 if actuals else None), len(actuals)

    deltas = [b - a for a, b in zip(actuals, actuals[1:]) if b > a]
    if not deltas:
        return refresh, None, len(actuals)
    fps = 1e9 / statistics.median(deltas)
    return refresh, round(min(fps, MAX_FPS), 1), len(actuals)


def parse_displays(out: str) -> list:
    """从 `dumpsys display` 解析显示屏列表（名称 / 分辨率 / 刷新率 / owner）。

    车机上副屏通常是 `DisplayDeviceInfo{"honda_meter": ..., 1130 x 306, ...,
    type VIRTUAL, owner com.baidu.naviauto (uid 2911)}`。
    """
    rows = []
    text = out or ""
    for m in re.finditer(r'DisplayDeviceInfo\{"([^"]+)":', text):
        name = m.group(1)
        body = text[m.end():m.end() + 2000]
        nxt = body.find('DisplayDeviceInfo{')
        if nxt > 0:
            body = body[:nxt]
        size = re.search(r"(\d+)\s*x\s*(\d+)", body)
        rate = re.search(r"fps=([\d.]+)", body)
        owner = re.search(r"owner (\S+) \(uid", body)
        rows.append({
            "name": name,
            "width": int(size.group(1)) if size else 0,
            "height": int(size.group(2)) if size else 0,
            "refresh": round(float(rate.group(1)), 1) if rate else None,
            "virtual": "type VIRTUAL" in body,
            "owner": owner.group(1) if owner else "",
        })
    return rows


def parse_display_stacks(dump: str) -> dict:
    """从 `dumpsys SurfaceFlinger` 解析 {显示屏名: {stack, width, height, virtual}}。

    虚拟屏（如仪表的 `honda_meter`）在 dump 里是
    `+ DisplayDevice{..., virtual, "honda_meter"}`，块内有 `layerStack=6`
    和 `layerStackSpace=...Rect(0, 0, 1130, 306)`。
    """
    out = {}
    for part in re.split(r"\n(?=\+ DisplayDevice\{)", dump or ""):
        m = re.match(r"\+ DisplayDevice\{([^}]*)\}", part)
        if not m:
            continue
        header = m.group(1)
        name = re.search(r'"([^"]+)"', header)
        stack = re.search(r"layerStack=(\d+)", part)
        bounds = re.search(
            r"layerStackSpace=ProjectionSpace\(bounds = Rect\(0, 0, (\d+), (\d+)\)", part)
        if not name or not stack:
            continue
        out[name.group(1)] = {
            "stack": int(stack.group(1)),
            "width": int(bounds.group(1)) if bounds else 0,
            "height": int(bounds.group(2)) if bounds else 0,
            "virtual": "virtual" in header,
        }
    return out


def parse_layer_stacks(dump: str) -> dict:
    """从 `dumpsys SurfaceFlinger` 解析 {图层名: {"stack", "kind"}}。

    每个图层块以 `+ BufferStateLayer (图层名) uid=...` 开头，块内有
    `layerStack=N`。虚拟屏的图层 stack 与显示屏的 layerStack 一致，
    据此把 `SurfaceView[](BLAST)#0` 这种不含包名的图层归到副屏。
    图层名本身可能带括号（`SurfaceView[...](BLAST)#0`），所以取
    ` uid=` 之前的**最后一个** `)` 才是名字的结尾。
    """
    out = {}
    for part in re.split(r"\n(?=\+ )", dump or ""):
        m = re.match(r"\+ (\w*Layer) \((.*)\)\s+uid=\d+", part)
        if not m:
            continue
        kind = m.group(1)
        name = _HEX_PREFIX.sub("", m.group(2).strip())
        stack = re.search(r"layerStack=\s*(\d+)", part)
        if not stack:
            continue
        out[name] = {"stack": int(stack.group(1)), "kind": kind}
    return out


def parse_layer_sizes(out: str) -> dict:
    """从 `dumpsys SurfaceFlinger` 的图层表解析 {图层名: (宽, 高)}。

    表结构：图层名单独一行，下一行 ` rel 0 | ... | L T R B | ...`。
    只取 Disp Frame 的宽高，用来把图层对到分辨率不同的副屏上。
    """
    sizes = {}
    lines = (out or "").splitlines()
    for i, line in enumerate(lines):
        name = line.strip()
        if not name or "#" not in name or "|" in name or len(name) > 160:
            continue
        if i + 1 >= len(lines):
            continue
        nxt = lines[i + 1]
        if "|" not in nxt:
            continue
        m = re.search(r"\|\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*\|", nxt)
        if not m:
            continue
        left, top, right, bottom = (int(x) for x in m.groups())
        if right > left and bottom > top:
            sizes[name] = (right - left, bottom - top)
    return sizes


class FpsDisplayProbe(PerfProbe):
    key = "fps_display"
    title = "FPS 帧率"
    scope = "process"
    default_interval = 5.0

    def __init__(self):
        super().__init__()
        self.package = ""
        self._layers = []
        self._layers_ts = 0.0
        self._displays = []
        self._layer_sizes = {}
        self._display_stacks = {}      # 显示屏名 -> {stack,width,height,virtual}
        self._layer_stacks = {}        # 图层名 -> layerStack
        self._virtual_layers = set()   # 应用自建虚拟屏上的图层（可能不含包名）
        self._meta_ts = 0.0
        self._pid_cache = None
        self._pid_ts = 0.0
        self._controllers = {}     # ControllerID -> 最新值（缺日志时沿用）
        # 常驻 logcat 流：逐条记录 FPS 事件（与百度口径一致）
        self._events = []
        self._log_proc = None
        self._log_thread = None
        self._log_offset = None    # 设备日志时间 → 本机时间 的偏移
        self._events_dropped = 0

    # ---------- 生命周期：日志流随探针启停 ----------
    def start(self, interval=None):
        super().start(interval)
        self._start_log_stream()

    def stop(self):
        super().stop()
        self._stop_log_stream()

    def _start_log_stream(self):
        if self._log_proc is not None:
            return
        try:
            import adbutils
            adb = adbutils.adb_path()
        except Exception:
            return
        cmd = [adb, "-s", self.serial, "shell", "logcat", "-v", "threadtime", "*:E"]
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            self._log_proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace",
                creationflags=flags)
        except Exception:
            self._log_proc = None
            return
        self._log_thread = threading.Thread(
            target=self._read_log_stream, name="fps-logcat", daemon=True)
        self._log_thread.start()

    def _stop_log_stream(self):
        proc, self._log_proc = self._log_proc, None
        if proc is not None:
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        self._log_thread = None

    def _read_log_stream(self):
        proc = self._log_proc
        if proc is None or proc.stdout is None:
            return
        try:
            for line in proc.stdout:
                if self._stop_evt.is_set():
                    break
                self._ingest_log_line(line)
        except Exception:
            pass

    def _ingest_log_line(self, line: str):
        """把一条日志解析成事件：逐条入库（去重交给流本身），并更新 Controller 最新值。"""
        if FPS_LOG_TAG not in line:
            return
        m = _LOG_RE.search(line)
        if not m:
            return
        parts = line.split(None, 5)
        pids = self._app_pids()
        if pids and len(parts) >= 3 and parts[2] not in pids:
            return

        read_ts = time.time()
        dt = _parse_log_dt(line)
        if dt is not None:
            dev_ts = dt.timestamp()
            if self._log_offset is None or abs(read_ts - dev_ts - self._log_offset) > 10:
                self._log_offset = read_ts - dev_ts      # 设备时钟与本机对齐
            ts = dev_ts + self._log_offset
        else:
            ts = read_ts

        fps, s_fps, map_type, controller = m.groups()
        event = {
            "ts": round(ts, 3),
            "id": controller,
            "fps": round(float(fps), 1),
            "s_fps": round(float(s_fps), 1),
            "map_type": map_type,
        }
        with self._lock:
            if len(self._events) < 100000:
                self._events.append(event)
            else:
                self._events_dropped += 1
            self._controllers[controller] = {
                "id": controller, "fps": event["fps"], "s_fps": event["s_fps"],
                "map_type": map_type, "seen": time.monotonic(),
            }

    def _sh(self, cmd: str, require_output: bool = True) -> str:
        ok, out = shell_text_checked(self.device, cmd, timeout=WORKER_TIMEOUT)
        if not ok and require_output:
            raise ProbeError(f"命令执行失败：{cmd}")
        return out or ""

    def _app_pids(self) -> set:
        """目标应用**所有进程**的 PID（主进程 + `:子进程`），20 秒缓存。

        只按主进程 PID 过滤会漏掉子进程打的 FPS 日志（仪表副屏经常跑在
        `包名:xxx` 里），折线就会一段一段断掉。
        """
        now = time.monotonic()
        if self._pid_cache is not None and now - self._pid_ts < 20:
            return self._pid_cache
        pids = set()
        if self.package:
            try:
                out = self._sh("ps -A")
            except Exception:
                out = ""
            for line in out.splitlines():
                parts = line.split()
                if len(parts) < 3 or not parts[1].isdigit():
                    continue
                name = parts[-1]
                if name == self.package or name.startswith(self.package + ":"):
                    pids.add(parts[1])
        self._pid_cache = pids
        self._pid_ts = now
        return pids

    # ---------- 元数据（图层列表 / 显示屏 / 图层尺寸） ----------
    def _refresh_layers(self, force=False):
        now = time.monotonic()
        if not force and self._layers and now - self._layers_ts < LAYER_TTL:
            return
        out = self._sh("dumpsys SurfaceFlinger --list")
        layers = []
        for raw in out.splitlines():
            line = _HEX_PREFIX.sub("", raw.strip())
            if not line or any(mark in line for mark in _SKIP_MARKS):
                continue
            if self.package and self.package not in line:
                continue
            if line not in layers:
                layers.append(line)
        # 副屏图层经常是 `SurfaceView[](BLAST)#0`（名字里没有包名）：
        # 把应用自建虚拟屏上的图层也加进来，否则副屏在报告里永远「未识别」
        for name in sorted(self._virtual_layers):
            if name in layers or any(mark in name for mark in _SKIP_MARKS):
                continue
            layers.append(name)
        self._layers = layers[:MAX_LAYERS]
        self._layers_ts = now

    def _refresh_meta(self, force=False):
        now = time.monotonic()
        if not force and self._meta_ts and now - self._meta_ts < META_TTL:
            return
        try:
            self._displays = parse_displays(self._sh("dumpsys display"))
        except Exception:
            self._displays = []
        try:
            sf_dump = self._sh("dumpsys SurfaceFlinger")
            self._layer_sizes = parse_layer_sizes(sf_dump)
            self._display_stacks = parse_display_stacks(sf_dump)
            self._layer_stacks = parse_layer_stacks(sf_dump)
        except Exception:
            self._layer_sizes = {}
            self._display_stacks = {}
            self._layer_stacks = {}
        new_virtual = self._collect_virtual_layers()
        if new_virtual != self._virtual_layers:
            self._virtual_layers = new_virtual
            self._layers_ts = 0        # 虚拟屏图层变了，下一轮重新拼候选列表
        self._meta_ts = now

    def _collect_virtual_layers(self) -> set:
        """应用自己创建的虚拟屏（仪表副屏）上的图层。

        `dumpsys display` 给出显示屏 owner；SurfaceFlinger 给出显示屏的 layerStack，
        图层块里又带自己的 layerStack——三者一串，就能把 `SurfaceView[]...#0`
        这种无名图层归到副屏，不再依赖分辨率猜测。
        """
        owner_of = {d["name"]: d.get("owner", "") for d in self._displays}
        stacks = {info["stack"] for name, info in self._display_stacks.items()
                  if info.get("virtual") and owner_of.get(name) == self.package}
        if not stacks:
            # dumpsys display 没给 owner 时退一步：所有虚拟屏的图层都纳入
            stacks = {info["stack"] for info in self._display_stacks.values()
                      if info.get("virtual")}
        return {name for name, info in self._layer_stacks.items()
                if info["stack"] in stacks
                and info["kind"] in ("BufferStateLayer", "BufferQueueLayer")}

    def _display_of(self, layer: str) -> str:
        """图层 → 显示屏：优先用 layerStack（最准），对不上再按分辨率猜。"""
        info = self._layer_stacks.get(layer)
        stack = info["stack"] if info else None
        if stack is not None:
            hit = next((name for name, info in self._display_stacks.items()
                        if info["stack"] == stack), None)
            if hit:
                # SurfaceFlinger 的名字（Internal display）换成 dumpsys display 的
                # 友好名字（内置屏幕 / honda_meter）：按分辨率对上
                info = self._display_stacks[hit]
                same = [d for d in self._displays
                        if (d["width"], d["height"]) == (info["width"], info["height"])]
                if len(same) == 1:
                    return same[0]["name"]
                return hit
        size = self._layer_sizes.get(layer)
        if not size:
            return ""
        hits = [d for d in self._displays
                if (d["width"], d["height"]) == size]
        if len(hits) == 1:
            return hits[0]["name"]
        # 同名分辨率多屏时用刷新率区分不了，宁可不猜
        return ""

    # ---------- 应用日志里的 Controller（百度同款） ----------
    def _collect_controllers(self) -> list:
        """返回当前存活的 Controller；日志流没起来时退回轮询尾部。

        常驻流模式下，事件由 `_read_log_stream` 实时入库，这里只取最新值；
        轮询模式下每轮读一次日志尾部，交给同一个 `_ingest_log_line` 处理。
        """
        # 日志流可能因为 adb server 重启而退出：发现进程死了就重开，开不起来则退回轮询
        if self._log_proc is not None and self._log_proc.poll() is not None:
            self._stop_log_stream()
            self._start_log_stream()
        if self._log_proc is None:
            try:
                out = self._sh("logcat -d -v threadtime -t 800 '*:E'",
                               require_output=False)
            except Exception:
                out = ""
            for line in (out or "").splitlines():
                self._ingest_log_line(line)

        now = time.monotonic()
        with self._lock:
            alive = []
            for cid, row in list(self._controllers.items()):
                if now - row.get("seen", now) > CONTROLLER_TTL:
                    self._controllers.pop(cid, None)
                    continue
                alive.append({k: v for k, v in row.items() if k != "seen"})
        return alive

    def events(self) -> list:
        """逐条 FPS 日志事件（报告按事件统计，和百度口径一致）。"""
        with self._lock:
            return [dict(e) for e in self._events]

    # ---------- 采样 ----------
    def sample_once(self):
        if not self.package:
            raise ProbeError("未指定目标应用")
        controllers = self._collect_controllers()
        # 应用日志可用时**只报 Controller**（与性能报告一致），
        # 不再跑图层 latency：少 8 次 adb 调用，报告也不会多出图层行
        self._refresh_meta()
        if controllers:
            return {
                "ts": time.time(),
                "layers": [],
                "controllers": controllers,
                "displays": list(self._displays),
            }

        # 没有日志（应用没打 SDK:FPS_C）→ 退回 SurfaceFlinger 图层口径
        self._refresh_layers()
        if not self._layers:
            raise ProbeError("SurfaceFlinger 里没有该应用的图层")

        layers = []
        for name in self._layers:
            try:
                out = self._sh(f'dumpsys SurfaceFlinger --latency "{name}"')
            except Exception:
                continue
            refresh, fps, frames = parse_latency(out)
            if frames < 2:
                continue          # 0/1 帧算不出帧率，不拿假 0 污染统计
            layers.append({
                "name": name,
                "display": self._display_of(name),
                "fps": fps,
                "refresh": refresh,
                "frames": frames,
            })
        return {
            "ts": time.time(),
            "layers": layers,
            "controllers": self._collect_controllers(),
            "displays": list(self._displays),
        }
