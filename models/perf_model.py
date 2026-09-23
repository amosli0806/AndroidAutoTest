# models/perf_model.py
import json
import os
import time
from dataclasses import dataclass, field, asdict, fields
from typing import List, Optional

from utils.app_paths import data_path

@dataclass
class PerfSample:
    """单个采样点"""
    timestamp: float = 0.0            # 采集时间（unix 秒）
    cpu_percent: float = 0.0          # CPU 占用率（%）
    mem_pss_mb: float = 0.0           # 内存 PSS（MB）
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

    def __init__(self):
        self.sessions: List[PerfSession] = []
        self.baselines: List[PerfBaseline] = []
        self.threshold: PerfThreshold = PerfThreshold()
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

    def save_baselines(self):
        try:
            with open(self.BASELINE_FILE, 'w', encoding='utf-8') as f:
                json.dump([b.to_dict() for b in self.baselines], f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[PerfModel] 基线保存失败: {e}")

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

    def remove_baseline(self, name: str):
        self.baselines = [b for b in self.baselines if b.name != name]
        self.save_baselines()