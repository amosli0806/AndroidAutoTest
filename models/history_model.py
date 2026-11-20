# models/history_model.py
"""数据历史：把 data/ 下的用户资产按快照存下来，支持回滚。

为什么不用 Git（详见扩展方向文档）：
  * 打包版没有 git.exe，内置或引 dulwich 都要 +20~40MB 包体
  * 数据只有约 50KB，全量复制 100 次也才 5MB —— 增量与分支没有价值
  * 单人工具，分支 / 合并 / 冲突解决是负担不是能力

触发方式：每 5 秒比对一次被跟踪文件的 mtime + 大小，有变化就重启 10 秒防抖计时，
到点写一份快照。不用 QFileSystemWatcher —— 一次保存可能触发好几条事件，
会产出几十份内容相同的无意义快照（用户翻都翻不动）。

目录结构：
    data/.history/20261121_071456_123/
        steps_data.json  project_data.json  ...（只放被跟踪的文件）
        meta.json        {"created_at":..., "reason":"auto", "label":""}
"""
import gzip
import json
import os
import shutil
import time
from typing import Dict, List, Optional

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from utils.app_paths import data_path, get_data_dir

# 纳入快照的是「用户资产」。刻意排除：
#   perf_data.json（7MB 高频采样，进来会把历史撑爆）
#   perf_baselines / edge_voices_cache / gui.info（缓存类，每次重启内容都变）
TRACKED_FILES = (
    "project_data.json",
    "steps_data.json",
    "suites_data.json",
    "tasks_data.json",
    "vars_data.json",
    "voice_data.json",
    "elements_data.json",
    "api_data.json",
    "knowledge_data.json",
    "step_templates.json",
    "config.json",
)


def history_dir() -> str:
    return os.path.join(get_data_dir(), ".history")


def read_json(directory: str, filename: str) -> Optional[dict]:
    """从指定目录（快照目录或 data/ 本身）读一个 json，读不到返回 None。

    快照里存的是 <name>.json.gz（voice_data.json 单个就有 356KB，不压缩的话
    500 份快照能吃掉 200MB），data/ 里是原始文件 —— 两种都认。
    """
    gz_path = os.path.join(directory, filename + ".gz")
    raw_path = os.path.join(directory, filename)
    try:
        if os.path.isfile(gz_path):
            with gzip.open(gz_path, "rt", encoding="utf-8") as f:
                return json.load(f)
        if os.path.isfile(raw_path):
            with open(raw_path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        return None
    return None


class HistoryModel(QObject):
    """快照的写入、列举、恢复与清理。所有异常都吞掉 —— 历史功能坏了不能拖垮主程序。"""

    snapshot_ready = pyqtSignal(str)    # 新快照已写入（快照 id）
    restored = pyqtSignal(str)          # 已恢复到某个快照

    POLL_MS = 5000          # 多久比对一次文件状态
    DEBOUNCE_MS = 10000     # 最后一次改动后静置多久才写快照
    KEEP_DAYS = 30          # 自动快照保留天数
    MAX_COUNT = 500         # 快照数量上限（超出删最旧的非里程碑）
    MIN_KEEP = 5            # 无论多旧，至少留最近这么多份

    def __init__(self, parent=None):
        super().__init__(parent)
        self._dir = history_dir()
        self._last_sig = ""
        self._poll = QTimer(self)
        self._poll.setInterval(self.POLL_MS)
        self._poll.timeout.connect(self._on_poll)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(self.DEBOUNCE_MS)
        self._debounce.timeout.connect(lambda: self.take_snapshot("auto"))

    # ---------- 生命周期 ----------
    def start(self):
        try:
            os.makedirs(self._dir, exist_ok=True)
            self._last_sig = self._signature()
            # 首次启动就把现状存一份：否则用户第一次改坏了没有「改之前」可比
            if not self.list_snapshots():
                self.take_snapshot("baseline", label="首次记录")
            self._poll.start()
        except Exception:
            pass

    def stop(self):
        self._poll.stop()
        self._debounce.stop()

    # ---------- 触发 ----------
    def _signature(self) -> str:
        """所有被跟踪文件的 mtime+大小拼成一个签名，变了就说明有写入。"""
        parts = []
        for name in TRACKED_FILES:
            path = data_path(name)
            try:
                st = os.stat(path)
                parts.append(f"{name}:{st.st_mtime_ns}:{st.st_size}")
            except OSError:
                parts.append(f"{name}:-")
        return "|".join(parts)

    def _on_poll(self):
        try:
            sig = self._signature()
            if sig == self._last_sig:
                return
            self._last_sig = sig
            self._debounce.start()      # 每次变化都重新计时，连续编辑合并成一份
        except Exception:
            pass

    # ---------- 写快照 ----------
    def take_snapshot(self, reason: str = "auto", label: str = "") -> Optional[str]:
        try:
            base = get_data_dir()
            files = [n for n in TRACKED_FILES
                     if os.path.isfile(os.path.join(base, n))]
            if not files:
                return None
            snap_id = time.strftime("%Y%m%d_%H%M%S") + f"_{int(time.time() * 1000) % 1000:03d}"
            snap_dir = os.path.join(self._dir, snap_id)
            os.makedirs(snap_dir, exist_ok=True)

            copied = []
            for name in files:
                try:
                    # 压缩存：voice_data.json 356KB、steps_data 23KB，
                    # 不压缩 500 份能到 200MB；gzip 后约 1/7
                    with open(os.path.join(base, name), "rb") as src:
                        raw = src.read()
                    with gzip.open(os.path.join(snap_dir, name + ".gz"), "wb",
                                   compresslevel=6) as dst:
                        dst.write(raw)
                    copied.append(name)
                except Exception:
                    continue

            meta = {
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "reason": reason,
                "label": label,
                "files": copied,
            }
            with open(os.path.join(snap_dir, "meta.json"), "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)

            self._last_sig = self._signature()
            self.snapshot_ready.emit(snap_id)
            self._cleanup()
            return snap_id
        except Exception:
            return None

    # ---------- 读 ----------
    def list_snapshots(self) -> List[Dict]:
        """全部快照，按时间**倒序**（最新的在前）。"""
        out: List[Dict] = []
        try:
            if not os.path.isdir(self._dir):
                return out
            for name in os.listdir(self._dir):
                snap_dir = os.path.join(self._dir, name)
                if not os.path.isdir(snap_dir):
                    continue
                meta = read_json(snap_dir, "meta.json") or {}
                files = meta.get("files") or []
                size = 0
                for fn in files:
                    try:
                        size += os.path.getsize(os.path.join(snap_dir, fn + ".gz"))
                    except OSError:
                        try:
                            size += os.path.getsize(os.path.join(snap_dir, fn))
                        except OSError:
                            pass
                out.append({
                    "id": name,
                    "path": snap_dir,
                    "created_at": meta.get("created_at") or self._fmt_from_id(name),
                    "reason": meta.get("reason") or "auto",
                    "label": meta.get("label") or "",
                    "files": files,
                    "size": size,
                    "mtime": self._mtime(snap_dir),
                })
        except Exception:
            pass
        out.sort(key=lambda s: s["id"], reverse=True)
        return out

    def get(self, snap_id: str) -> Optional[Dict]:
        for s in self.list_snapshots():
            if s["id"] == snap_id:
                return s
        return None

    def total_bytes(self) -> int:
        return sum(s["size"] for s in self.list_snapshots())

    # ---------- 改 / 删 ----------
    def restore(self, snap_id: str) -> bool:
        """恢复到某个快照。

        先把**当前状态**存一份再覆盖 —— 于是「恢复」本身也能被撤销，
        用户才敢点。恢复完由调用方负责让各 model 重新读盘。
        """
        snap = self.get(snap_id)
        if snap is None:
            return False
        try:
            self.take_snapshot("before_restore", label="恢复前自动保存")
            base = get_data_dir()
            for name in snap["files"]:
                src = os.path.join(snap["path"], name + ".gz")
                if not os.path.isfile(src):
                    continue
                with gzip.open(src, "rb") as f:
                    raw = f.read()
                with open(os.path.join(base, name), "wb") as f:
                    f.write(raw)
            self._last_sig = self._signature()
            self.restored.emit(snap_id)
            return True
        except Exception:
            return False

    def set_label(self, snap_id: str, label: str) -> bool:
        snap = self.get(snap_id)
        if snap is None:
            return False
        try:
            meta_path = os.path.join(snap["path"], "meta.json")
            meta = read_json(snap["path"], "meta.json") or {}
            meta["label"] = (label or "").strip()
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
            return True
        except Exception:
            return False

    def _cleanup(self):
        """滚动清理：先按天数，再按数量；带 label 的里程碑永不删。"""
        try:
            snaps = self.list_snapshots()
            now = time.time()
            keep_ids = set()
            for i, s in enumerate(snaps):
                is_milestone = bool(s.get("label")) and s.get("reason") != "auto"
                too_old = (now - s["mtime"]) > self.KEEP_DAYS * 86400
                too_many = i >= self.MAX_COUNT
                if is_milestone or i < self.MIN_KEEP:
                    continue
                if too_old or too_many:
                    shutil.rmtree(s["path"], ignore_errors=True)
                else:
                    keep_ids.add(s["id"])
        except Exception:
            pass

    # ---------- 小工具 ----------
    @staticmethod
    def _mtime(path: str) -> float:
        try:
            return os.path.getmtime(path)
        except OSError:
            return 0.0

    @staticmethod
    def _fmt_from_id(snap_id: str) -> str:
        try:
            t = time.strptime(snap_id.split("_")[0], "%Y%m%d")
            return time.strftime("%Y-%m-%d 00:00:00", t)
        except Exception:
            return snap_id
