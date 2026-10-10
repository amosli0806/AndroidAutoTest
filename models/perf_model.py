# models/perf_model.py
import json
import os
import time
from dataclasses import dataclass, field, asdict, fields
from datetime import datetime
from typing import List, Optional

from utils.app_paths import data_path


# Android Studio 风格的内存分类拆解：(分类键, 展示名, 曲线颜色)
# 由 dumpsys meminfo 的 App Summary 归类而来：
#   Java / Native / Graphics / Stack / Code / Others
MEM_CATEGORIES = [
    ("Java", "Java", "#f39c12"),
    ("Native", "Native", "#9b59b6"),
    ("Graphics", "Graphics", "#e74c3c"),
    ("Stack", "Stack", "#1abc9c"),
    ("Code", "Code", "#3498db"),
    ("Others", "Others", "#95a5a6"),
]


@dataclass
class PerfSample:
    """单个采样点"""
    timestamp: float = 0.0            # 采集时间（unix 秒）
    cpu_percent: float = 0.0          # CPU 占用率（%）
    mem_pss_mb: float = 0.0           # 内存 PSS 总量（MB）
    mem_breakdown: dict = field(default_factory=dict)  # 分类拆解 {'Java': MB, ...}
    fps: int = 0                      # 帧率
    rx_bytes: int = 0                 # 接收字节（累计）
    tx_bytes: int = 0                 # 发送字节（累计）
    errors: List[str] = field(default_factory=list)  # 本次采样中失败的项

    def to_dict(self):
        return asdict(self)


_PERF_SAMPLE_FIELDS = {f.name for f in fields(PerfSample)}


@dataclass
class PerfSession:
    """一次采集会话"""
    id: str
    name: str
    device_serial: str
    app_package: str
    start_time: str = ""
    end_time: str = ""
    sample_interval: float = 5.0
    metrics: List[str] = field(default_factory=list)     # ['cpu','mem','fps','traffic']
    samples: List[PerfSample] = field(default_factory=list)
    # 场景化（可选）
    suite_name: str = ""
    case_ids: List[str] = field(default_factory=list)
    case_names: List[str] = field(default_factory=list)
    # 设备信息（会话创建时采集一次，用于报告「基本信息」；旧数据为空）
    screen_resolution: str = ""       # 如 1080x2340（含物理分辨率时的 Override 值）
    screen_density: str = ""          # 如 480（dpi），取自 wm density
    android_version: str = ""         # 如 13，取自 ro.build.version.release
    # 阈值告警记录：[(timestamp, metric, message), ...]
    alerts: List[tuple] = field(default_factory=list)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "device_serial": self.device_serial,
            "app_package": self.app_package,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "sample_interval": self.sample_interval,
            "metrics": self.metrics,
            "samples": [s.to_dict() for s in self.samples],
            "suite_name": self.suite_name,
            "case_ids": self.case_ids,
            "case_names": self.case_names,
            "screen_resolution": self.screen_resolution,
            "screen_density": self.screen_density,
            "android_version": self.android_version,
            "alerts": [list(a) for a in self.alerts],
        }

    @classmethod
    def from_dict(cls, data):
        session = cls(
            id=data["id"],
            name=data["name"],
            device_serial=data["device_serial"],
            app_package=data["app_package"],
            start_time=data.get("start_time", ""),
            end_time=data.get("end_time", ""),
            sample_interval=data.get("sample_interval", 5.0),
            metrics=data.get("metrics", []),
            suite_name=data.get("suite_name", ""),
            case_ids=data.get("case_ids", []),
            case_names=data.get("case_names", []),
            screen_resolution=data.get("screen_resolution", ""),
            screen_density=data.get("screen_density", ""),
            android_version=data.get("android_version", ""),
        )
        for s in data.get("samples", []):
            # 兼容旧数据：早期 perf_data.json 的采样点里还带卡顿相关的字段，
            # 这些字段已经下线，构造时直接忽略，避免整份历史数据加载失败
            session.samples.append(PerfSample(**{k: v for k, v in s.items()
                                                if k in _PERF_SAMPLE_FIELDS}))
        session.alerts = [tuple(a) for a in data.get("alerts", [])]
        return session

    # ---------- 统计 ----------
    def get_stats(self) -> dict:
        """返回各指标的统计：峰值 / 均值 / 最小值 / 标准差"""
        result = {}
        if not self.samples:
            return result

        def calc(values):
            if not values:
                return {"min": 0, "max": 0, "avg": 0, "std": 0}
            n = len(values)
            avg = sum(values) / n
            var = sum((v - avg) ** 2 for v in values) / n
            return {
                "min": min(values),
                "max": max(values),
                "avg": avg,
                "std": var ** 0.5,
            }

        # CPU
        if 'cpu' in self.metrics:
            stat = calc([s.cpu_percent for s in self.samples if s.cpu_percent > 0])
            stat['current'] = self.samples[-1].cpu_percent
            result['cpu'] = stat
        # 内存
        if 'mem' in self.metrics:
            stat = calc([s.mem_pss_mb for s in self.samples if s.mem_pss_mb > 0])
            stat['current'] = self.samples[-1].mem_pss_mb
            result['mem'] = stat
            # 内存分类拆解（Java/Native/Graphics/Stack/Code/Others，旧数据无拆解时跳过）
            breakdown = {}
            for cat, _lbl, _color in MEM_CATEGORIES:
                vals = [s.mem_breakdown.get(cat, 0.0) for s in self.samples
                        if s.mem_breakdown.get(cat, 0.0) > 0]
                if vals:
                    cs = calc(vals)
                    cs['current'] = self.samples[-1].mem_breakdown.get(cat, 0.0)
                    breakdown[cat] = cs
            if breakdown:
                result['mem_breakdown'] = breakdown
        # FPS
        if 'fps' in self.metrics:
            stat = calc([s.fps for s in self.samples if s.fps > 0])
            stat['current'] = self.samples[-1].fps
            result['fps'] = stat
        # 流量
        if 'traffic' in self.metrics:
            if len(self.samples) >= 2:
                s0, s1 = self.samples[0], self.samples[-1]
                result['traffic'] = {
                    "rx_mb": (s1.rx_bytes - s0.rx_bytes) / 1024 / 1024,
                    "tx_mb": (s1.tx_bytes - s0.tx_bytes) / 1024 / 1024,
                }
        return result


def compare_stats(base_metrics: dict, cur_stats: dict, threshold_pct: float = 0.10):
    """对比当前统计与基线，返回逐项对比结果列表。

    base_metrics / cur_stats 均为 PerfSession.get_stats() 产出的 dict。
    判定规则（相对变化超过 threshold_pct 才算明显）：
      - CPU 峰值、内存峰值、流量总量：越高越差
      - FPS 均值：越低越差
    返回：[
        {'key': 'cpu', 'label': 'CPU 峰值 (%)', 'base': 120.0, 'cur': 132.0,
         'change_pct': 10.0, 'status': 'worse'|'better'|'stable'},
        ...
    ]
    """
    result = []

    def _row(key, label, base, cur, lower_is_worse=False):
        if base is None or cur is None:
            return
        if base == 0:
            status = 'stable'
            change_pct = 0.0
        else:
            change_pct = (cur - base) / base * 100.0
            if lower_is_worse:
                if cur < base * (1 - threshold_pct):
                    status = 'worse'
                elif cur > base * (1 + threshold_pct):
                    status = 'better'
                else:
                    status = 'stable'
            else:
                if cur > base * (1 + threshold_pct):
                    status = 'worse'
                elif cur < base * (1 - threshold_pct):
                    status = 'better'
                else:
                    status = 'stable'
        result.append({
            'key': key, 'label': label,
            'base': base, 'cur': cur,
            'change_pct': change_pct, 'status': status,
        })

    def _is_stats(d):
        # 防御历史遗留的非 dict 格式基线（如纯数值），跳过而不是崩
        return isinstance(d, dict) and bool(d)

    b, c = base_metrics.get('cpu'), cur_stats.get('cpu')
    if _is_stats(b) and _is_stats(c):
        _row('cpu_avg', 'CPU 均值 (%)', b.get('avg'), c.get('avg'))
        _row('cpu', 'CPU 峰值 (%)', b.get('max'), c.get('max'))
        _row('cpu_min', 'CPU 最低 (%)', b.get('min'), c.get('min'))
    b, c = base_metrics.get('mem'), cur_stats.get('mem')
    if _is_stats(b) and _is_stats(c):
        _row('mem_avg', 'Total均值 (MB)', b.get('avg'), c.get('avg'))
        _row('mem', 'Total峰值 (MB)', b.get('max'), c.get('max'))
        _row('mem_min', 'Total最低 (MB)', b.get('min'), c.get('min'))
    # 内存分类拆解对比（各分类均值/峰值/最低，旧数据无拆解时自动跳过）
    bb = base_metrics.get('mem_breakdown', {})
    cc = cur_stats.get('mem_breakdown', {})
    if isinstance(bb, dict) and isinstance(cc, dict):
        for cat, lbl, _color in MEM_CATEGORIES:
            bcat, ccat = bb.get(cat), cc.get(cat)
            if _is_stats(bcat) and _is_stats(ccat):
                _row(f'mem_{cat}_avg', f'{lbl}均值 (MB)',
                     bcat.get('avg'), ccat.get('avg'))
                _row(f'mem_{cat}', f'{lbl}峰值 (MB)',
                     bcat.get('max'), ccat.get('max'))
                _row(f'mem_{cat}_min', f'{lbl}最低 (MB)',
                     bcat.get('min'), ccat.get('min'))
    b, c = base_metrics.get('fps'), cur_stats.get('fps')
    if _is_stats(b) and _is_stats(c):
        # FPS 越低越差（帧率掉了才是劣化），与 CPU 方向相反
        _row('fps_avg', 'FPS 均值', b.get('avg'), c.get('avg'), lower_is_worse=True)
        _row('fps', 'FPS 峰值', b.get('max'), c.get('max'), lower_is_worse=True)
        _row('fps_min', 'FPS 最低', b.get('min'), c.get('min'), lower_is_worse=True)
    b, c = base_metrics.get('traffic'), cur_stats.get('traffic')
    if _is_stats(b) and _is_stats(c):
        base_total = b.get('rx_mb', 0) + b.get('tx_mb', 0)
        cur_total = c.get('rx_mb', 0) + c.get('tx_mb', 0)
        _row('traffic', '流量总量 (MB)', base_total, cur_total)

    return result


@dataclass
class PerfThreshold:
    """阈值配置（默认值针对 8 核设备上的地图类应用调优）"""
    cpu_max: float = 400.0           # 8 核多核累计，400% ≈ 4 核满载
    mem_max: float = 800.0           # 地图类 PSS 典型 500~800MB
    fps_min: float = 50.0            # 地图渲染低于 50 帧已可感知卡顿
    enabled: bool = True             # 是否启用阈值告警

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class LaunchResult:
    """应用启动耗时结果"""
    package: str
    cold_start_ms: int = 0            # 冷启动耗时
    warm_start_ms: int = 0            # 热启动耗时
    timestamp: str = ""

    def to_dict(self):
        return asdict(self)


@dataclass
class PerfBaseline:
    """性能基线"""
    name: str
    session_id: str
    app_package: str
    device_serial: str
    metrics: dict = field(default_factory=dict)   # 各指标的统计值
    created_at: str = ""

    def to_dict(self):
        return asdict(self)


class PerfModel:
    """性能数据的持久化管理"""
    DATA_FILE = data_path("perf_data.json")
    BASELINE_FILE = data_path("perf_baselines.json")
    LAUNCH_FILE = data_path("launch_data.json")

    def __init__(self):
        self.sessions: List[PerfSession] = []
        self.baselines: List[PerfBaseline] = []
        self.threshold: PerfThreshold = PerfThreshold()
        # 启动耗时：records 按时间累积，baselines 以包名为键存冷/热启动基线
        self.launch_records: List[LaunchResult] = []
        self.launch_baselines: dict = {}
        self.load()

    # ---------- 加载保存 ----------
    def load(self):
        if os.path.exists(self.DATA_FILE):
            try:
                with open(self.DATA_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.sessions = [PerfSession.from_dict(s) for s in data.get('sessions', [])]
                    th = data.get('threshold', {})
                    self.threshold = PerfThreshold.from_dict(th)
            except Exception as e:
                print(f"[PerfModel] 加载失败: {e}")
                self.sessions = []
        if os.path.exists(self.BASELINE_FILE):
            try:
                with open(self.BASELINE_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.baselines = [PerfBaseline(**b) for b in data]
            except Exception as e:
                print(f"[PerfModel] 基线加载失败: {e}")
                self.baselines = []

        self._migrate_baseline_breakdown()

        if os.path.exists(self.LAUNCH_FILE):
            try:
                with open(self.LAUNCH_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.launch_records = [
                        LaunchResult(**r) for r in data.get('records', [])
                    ]
                    self.launch_baselines = data.get('baselines', {}) or {}
            except Exception as e:
                print(f"[PerfModel] 启动耗时数据加载失败: {e}")
                self.launch_records = []
                self.launch_baselines = {}

    def save(self):
        try:
            data = {
                'sessions': [s.to_dict() for s in self.sessions],
                'threshold': self.threshold.to_dict(),
            }
            with open(self.DATA_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[PerfModel] 保存失败: {e}")

    def _migrate_baseline_breakdown(self):
        """旧基线的统计快照里没有内存分类拆解（该功能上线前保存的基线），
        导致基线对比表缺 Java/Native 等分类行。这里在加载时从其关联会话
        补算一次并写回，会话已删除或会话本身无拆解数据时保持原样。"""
        changed = False
        for b in self.baselines:
            if not isinstance(b.metrics, dict) or 'mem_breakdown' in b.metrics:
                continue
            session = self.get_session(b.session_id)
            if session is None:
                continue
            breakdown = session.get_stats().get('mem_breakdown')
            if breakdown:
                b.metrics['mem_breakdown'] = breakdown
                changed = True
        if changed:
            self.save_baselines()

    def save_baselines(self):
        try:
            with open(self.BASELINE_FILE, 'w', encoding='utf-8') as f:
                json.dump([b.to_dict() for b in self.baselines], f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[PerfModel] 基线保存失败: {e}")

    # ---------- 启动耗时 ----------
    def save_launch(self):
        try:
            with open(self.LAUNCH_FILE, 'w', encoding='utf-8') as f:
                json.dump({
                    'records': [r.to_dict() for r in self.launch_records],
                    'baselines': self.launch_baselines,
                }, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[PerfModel] 启动耗时数据保存失败: {e}")

    def add_launch_record(self, package: str, cold_ms: int, warm_ms: int) -> LaunchResult:
        rec = LaunchResult(
            package=package,
            cold_start_ms=cold_ms,
            warm_start_ms=warm_ms,
            timestamp=datetime.now().isoformat(),
        )
        self.launch_records.append(rec)
        # 只保留最近 200 条，避免文件无限增长
        if len(self.launch_records) > 200:
            self.launch_records = self.launch_records[-200:]
        self.save_launch()
        return rec

    def get_launch_history(self, package: str) -> List[LaunchResult]:
        return [r for r in self.launch_records if r.package == package]

    def set_launch_baseline(self, package: str, cold_ms: int, warm_ms: int):
        self.launch_baselines[package] = {
            'cold_ms': cold_ms,
            'warm_ms': warm_ms,
            'set_at': datetime.now().isoformat(),
        }
        self.save_launch()

    def get_launch_baseline(self, package: str) -> Optional[dict]:
        return self.launch_baselines.get(package)

    # ---------- 会话 ----------
    def add_session(self, session: PerfSession):
        self.sessions.append(session)
        self.save()

    def remove_session(self, session_id: str):
        self.sessions = [s for s in self.sessions if s.id != session_id]
        self.save()

    def get_session(self, session_id: str) -> Optional[PerfSession]:
        for s in self.sessions:
            if s.id == session_id:
                return s
        return None

    def gen_session_id(self) -> str:
        return f"perf_{int(time.time() * 1000)}"

    # ---------- 阈值 ----------
    def set_threshold(self, threshold: PerfThreshold):
        self.threshold = threshold
        self.save()

    # ---------- 基线 ----------
    def add_baseline(self, baseline: PerfBaseline):
        self.baselines.append(baseline)
        self.save_baselines()

    def get_baseline(self, name: str) -> Optional[PerfBaseline]:
        for b in self.baselines:
            if b.name == name:
                return b
        return None

    def find_baseline_for_package(self, app_package: str,
                                  metrics: Optional[List[str]] = None) -> Optional[PerfBaseline]:
        """返回指定应用最匹配的基线（按 created_at 最新优先）。

        传入 metrics（本次采集的指标列表）时，优先返回与本次指标有交集的最新基线，
        避免被同应用其他指标的新基线「挡住」（例如只采内存时，应匹配内存基线，
        而不是更新保存的 CPU 基线）；无交集时回退到该应用最新的基线。
        """
        candidates = [b for b in self.baselines if b.app_package == app_package]
        if not candidates:
            return None
        if metrics:
            common = [b for b in candidates if set(b.metrics) & set(metrics)]
            if common:
                return sorted(common, key=lambda b: b.created_at)[-1]
        return sorted(candidates, key=lambda b: b.created_at)[-1]

    def remove_baseline(self, name: str):
        self.baselines = [b for b in self.baselines if b.name != name]
        self.save_baselines()