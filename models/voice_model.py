# models/voice_model.py
"""语音播报的数据模型：独立的「语音用例」库 + 引擎设置。

持久化到 data/voice_data.json，结构：
    {
      "settings": {...},
      "groups": [{"id", "name"}],                       # 分组（对应项目树里的"功能模块"）
      "cases":  [{"id", "name", "group_id", "phrases": [{"text", "delay"}]}]
    }

两个关键约定：
  1. **语音用例和「自动化编辑」里的用例是两套东西，不共用。**
     那边是 App 操作序列（点击/输入/断言…），这里是纯播报脚本
     （一条条要念给车机听的文案），互不影响。
     （自动化编辑页里那个 `voice` 步骤类型仍然保留，用于把一句播报
     插进 App 操作用例，那是另一条路径。）
  2. 引擎设置（音色/语速/输出设备/唤醒词）由「设置 → 语音播报」维护，
     语音页与自动化用例里的语音步骤共用同一套。
"""
import itertools
import json
import os
import time
from dataclasses import dataclass, field
from typing import List, Optional

from utils.app_paths import data_path

# 唤醒词：语音页那个「唤醒词」按钮追加到用例末尾的文案。
# 没配置（清空过）时回落到这个值，保证按钮任何时候点下去都有内容可插。
DEFAULT_WAKE_WORD = "你好虫师"

DEFAULT_SETTINGS = {
    "engine": "windows_sapi",
    "voice_id": "",
    "rate": 0,          # SAPI -10..10
    "device_id": "",    # 空 = 系统默认输出设备
    "wake_word": DEFAULT_WAKE_WORD,
    # 没有 volume：响度直接用电脑的系统音量，界面上不提供音量调节
}

# 首次使用（还没有数据文件时）给一个示例分组 + 用例，直接演示「唤醒 + 指令」
SEED_GROUP_NAME = "示例分组"
SEED_CASE_NAME = "唤醒后打开地图"
SEED_PHRASES = [
    {"text": "你好，小爱同学", "delay": 2.0},
    {"text": "打开地图", "delay": 2.0},
]

DEFAULT_DELAY = 2.0
# 没有归到任何分组的用例，在树上挂在这个虚拟节点下
UNGROUPED_ID = ""
UNGROUPED_NAME = "未分组"


_id_seq = itertools.count()


def _new_id(prefix: str) -> str:
    """毫秒时间戳 + 自增序号。

    只靠毫秒的话，同一毫秒内连续建多个（复制分组会一次建一批用例）会撞 id，
    序号部分保证同毫秒也不重复。
    """
    return f"{prefix}_{int(time.time() * 1000)}_{next(_id_seq) % 1000}"


def _unique_name(base: str, taken) -> str:
    """在 taken 里挑一个没被占用的名字：base 被占了就退化成 base1 / base2 …

    与「项目管理」树复制节点时的命名一致（那边是 "xxx 副本" / "xxx 副本1"）。
    """
    if base not in taken:
        return base
    index = 1
    while f"{base}{index}" in taken:
        index += 1
    return f"{base}{index}"


@dataclass
class VoicePhrase:
    """一条待播报的文案。"""
    text: str = ""
    delay: float = DEFAULT_DELAY   # 播完这句后再等几秒

    def to_dict(self):
        return {"text": self.text, "delay": self.delay}

    @classmethod
    def from_dict(cls, data):
        return cls(text=str(data.get("text", "")),
                   delay=float(data.get("delay", DEFAULT_DELAY) or 0))


@dataclass
class VoiceCase:
    """一个语音用例 = 一组按顺序播报的文案。"""
    id: str = ""
    name: str = ""
    group_id: str = ""
    phrases: List[VoicePhrase] = field(default_factory=list)

    def to_dict(self):
        return {"id": self.id, "name": self.name, "group_id": self.group_id,
                "phrases": [p.to_dict() for p in self.phrases]}

    @classmethod
    def from_dict(cls, data):
        return cls(
            id=str(data.get("id", "")),
            name=str(data.get("name", "")),
            group_id=str(data.get("group_id", "") or ""),
            phrases=[VoicePhrase.from_dict(p)
                     for p in (data.get("phrases") or [])],
        )

    @property
    def usable_phrases(self) -> List[VoicePhrase]:
        return [p for p in self.phrases if (p.text or "").strip()]


@dataclass
class VoiceGroup:
    """语音用例的分组（对应项目树里的功能模块）。"""
    id: str = ""
    name: str = ""

    def to_dict(self):
        return {"id": self.id, "name": self.name}

    @classmethod
    def from_dict(cls, data):
        return cls(id=str(data.get("id", "")), name=str(data.get("name", "")))


class VoiceModel:
    DATA_FILE = data_path("voice_data.json")

    def __init__(self):
        self.settings = dict(DEFAULT_SETTINGS)
        self.groups: List[VoiceGroup] = []
        self.cases: List[VoiceCase] = []
        self.load()

    # ---------------- 读写 ----------------
    def load(self):
        if os.path.exists(self.DATA_FILE):
            try:
                with open(self.DATA_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                merged = dict(DEFAULT_SETTINGS)
                # 只认已知键：早先版本写进去的 volume 之类会被丢掉
                for key, value in (data.get("settings") or {}).items():
                    if key in DEFAULT_SETTINGS:
                        merged[key] = value
                self.settings = merged
                self.groups = [VoiceGroup.from_dict(g)
                               for g in (data.get("groups") or [])]
                self.cases = [VoiceCase.from_dict(c)
                              for c in (data.get("cases") or [])]
                return
            except Exception:
                pass
        # 文件不存在 / 读坏了：给一份空状态。
        # 空状态下用户需要先右键建分组，再在分组里建用例 ——
        # 与「自动化编辑」页"先建项目/模块，再建用例"的路径一致。
        self.settings = dict(DEFAULT_SETTINGS)
        self.groups = []
        self.cases = []
        self.save()

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.DATA_FILE), exist_ok=True)
            with open(self.DATA_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "settings": self.settings,
                    "groups": [g.to_dict() for g in self.groups],
                    "cases": [c.to_dict() for c in self.cases],
                }, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[voice_model] 保存失败 {self.DATA_FILE}: {e}")

    # ---------------- 设置 ----------------
    def set_settings(self, **kwargs):
        for key, value in kwargs.items():
            if key in DEFAULT_SETTINGS:
                self.settings[key] = value
        self.save()

    # ---------------- 分组 ----------------
    def get_group(self, group_id: str) -> Optional[VoiceGroup]:
        for group in self.groups:
            if group.id == group_id:
                return group
        return None

    def add_group(self, name: str = "") -> VoiceGroup:
        if not name:
            existing = {g.name for g in self.groups}
            index = len(self.groups) + 1
            while f"分组 {index}" in existing:
                index += 1
            name = f"分组 {index}"
        group = VoiceGroup(id=_new_id("vgroup"), name=name)
        self.groups.append(group)
        self.save()
        return group

    def copy_group(self, group_id: str) -> Optional[VoiceGroup]:
        """复制一个分组：连组内所有语音用例（含文案）一起复制。

        与「项目管理」树复制功能模块的行为对齐 —— 那边也是递归复制子节点，
        而不是只复制一个空壳。
        """
        src = self.get_group(group_id)
        if src is None:
            return None
        new_group = VoiceGroup(
            id=_new_id("vgroup"),
            name=_unique_name(f"{src.name} 副本",
                              {g.name for g in self.groups}),
        )
        self.groups.append(new_group)
        for case in self.cases_of_group(group_id):
            self.cases.append(VoiceCase(
                id=_new_id("vcase"),
                name=case.name,
                group_id=new_group.id,
                phrases=[VoicePhrase(text=p.text, delay=p.delay)
                         for p in case.phrases],
            ))
        self.save()
        return new_group

    def rename_group(self, group_id: str, name: str) -> bool:
        group = self.get_group(group_id)
        if group is None or not (name or "").strip():
            return False
        group.name = name.strip()
        self.save()
        return True

    def remove_group(self, group_id: str, with_cases: bool = True) -> int:
        """删除分组。with_cases=True 时连组内用例一起删，返回删掉的用例数。"""
        group = self.get_group(group_id)
        if group is None:
            return 0
        removed = 0
        if with_cases:
            keep = []
            for case in self.cases:
                if case.group_id == group_id:
                    removed += 1
                else:
                    keep.append(case)
            self.cases = keep
        else:
            # 只删分组，组内用例落到「未分组」
            for case in self.cases:
                if case.group_id == group_id:
                    case.group_id = UNGROUPED_ID
        self.groups.remove(group)
        self.save()
        return removed

    def cases_of_group(self, group_id: str) -> List[VoiceCase]:
        return [c for c in self.cases if c.group_id == group_id]

    # ---------------- 语音用例 ----------------
    def get_case(self, case_id: str) -> Optional[VoiceCase]:
        for case in self.cases:
            if case.id == case_id:
                return case
        return None

    def add_case(self, name: str = "", group_id: str = "") -> VoiceCase:
        # 用例必须归属某个分组：没有分组时由 UI 层拦住，这里兜底抛异常，
        # 避免再产生"无主用例"（历史上它们挂在"未分组"虚拟节点下）
        if not group_id or self.get_group(group_id) is None:
            raise ValueError("必须先选择或创建分组，才能新建语音用例")
        if not name:
            existing = {c.name for c in self.cases}
            index = len(self.cases) + 1
            while f"语音用例 {index}" in existing:
                index += 1
            name = f"语音用例 {index}"
        case = VoiceCase(id=_new_id("vcase"), name=name, group_id=group_id)
        self.cases.append(case)
        self.save()
        return case

    def copy_case(self, case_id: str) -> Optional[VoiceCase]:
        """复制一个语音用例（连文案一起），落在同一个分组里。

        名字按「xxx 副本 / xxx 副本1 / 副本2…」去重，与「项目管理」树复制节点的命名一致。
        """
        src = self.get_case(case_id)
        if src is None:
            return None
        taken = {c.name for c in self.cases if c.group_id == src.group_id}
        new_case = VoiceCase(
            id=_new_id("vcase"),
            name=_unique_name(f"{src.name} 副本", taken),
            group_id=src.group_id,
            phrases=[VoicePhrase(text=p.text, delay=p.delay)
                     for p in src.phrases],
        )
        self.cases.append(new_case)
        self.save()
        return new_case

    def rename_case(self, case_id: str, name: str) -> bool:
        case = self.get_case(case_id)
        if case is None or not (name or "").strip():
            return False
        case.name = name.strip()
        self.save()
        return True

    def remove_case(self, case_id: str) -> bool:
        case = self.get_case(case_id)
        if case is None:
            return False
        self.cases.remove(case)
        self.save()
        return True

    def move_case_to_group(self, case_id: str, group_id: str) -> bool:
        case = self.get_case(case_id)
        if case is None:
            return False
        case.group_id = group_id or UNGROUPED_ID
        self.save()
        return True

    # ---------------- 文案 ----------------
    def add_phrase(self, case_id: str, text: str = "",
                   delay: float = DEFAULT_DELAY) -> Optional[VoicePhrase]:
        case = self.get_case(case_id)
        if case is None:
            return None
        phrase = VoicePhrase(text=text, delay=delay)
        case.phrases.append(phrase)
        self.save()
        return phrase

    def update_phrase(self, case_id: str, index: int, **kwargs) -> bool:
        case = self.get_case(case_id)
        if case is None or not (0 <= index < len(case.phrases)):
            return False
        phrase = case.phrases[index]
        for key, value in kwargs.items():
            if hasattr(phrase, key):
                setattr(phrase, key, value)
        self.save()
        return True

    def remove_phrase(self, case_id: str, index: int) -> bool:
        case = self.get_case(case_id)
        if case is None or not (0 <= index < len(case.phrases)):
            return False
        del case.phrases[index]
        self.save()
        return True

    # ---------------- 导入 / 导出 ----------------
    def export_case(self, case_id: str) -> Optional[dict]:
        case = self.get_case(case_id)
        if case is None:
            return None
        return {"name": case.name,
                "phrases": [p.to_dict() for p in case.phrases]}

    def export_all(self) -> dict:
        """全量导出：分组 -> 用例 -> 文案，整棵树打包成一个 dict。

        与 export_case（单用例文案包）不同，这个格式包含分组与用例结构，
        配合 import_all 可以在换机器/换项目时完整还原整棵语音用例树。
        """
        cases_by_group = {}
        for c in self.cases:
            cases_by_group.setdefault(c.group_id, []).append(c)
        groups = []
        for g in self.groups:
            groups.append({
                "name": g.name,
                "cases": [
                    {"name": c.name,
                     "phrases": [p.to_dict() for p in c.phrases]}
                    for c in cases_by_group.get(g.id, [])
                ],
            })
        return {"format": "voice-cases-all", "version": 1, "groups": groups}

    def import_all(self, data) -> dict:
        """全量导入：按 export_all 的格式重建分组/用例/文案（追加式）。

        合并规则（都是追加，不覆盖已有数据）：
        - 同名分组已存在 -> 复用现有分组（不新建、不改名）
        - 该分组内同名用例已存在 -> 跳过该用例（避免文案重复）
        - 其余分组/用例新建，文案（含 delay）原样带入
        返回统计 {"groups": 新建分组数, "cases": 新建用例数,
                  "skipped": 同名跳过的用例数, "phrases": 导入文案数}
        """
        if not isinstance(data, dict) or data.get("format") != "voice-cases-all":
            raise ValueError("不是全量语音用例文件（缺少 format 标记），"
                             "请选择「导出全部语音用例」生成的 JSON")
        stats = {"groups": 0, "cases": 0, "skipped": 0, "phrases": 0}
        for gdata in (data.get("groups") or []):
            gname = str(gdata.get("name", "")).strip() or "导入分组"
            group = next((g for g in self.groups if g.name == gname), None)
            if group is None:
                group = self.add_group(gname)
                stats["groups"] += 1
            existing_names = {c.name for c in self.cases
                              if c.group_id == group.id}
            for cdata in (gdata.get("cases") or []):
                cname = str(cdata.get("name", "")).strip() or "导入用例"
                if cname in existing_names:
                    stats["skipped"] += 1
                    continue
                case = self.add_case(cname, group.id)
                stats["cases"] += 1
                for pdata in (cdata.get("phrases") or []):
                    try:
                        self.add_phrase(
                            case.id,
                            text=str(pdata.get("text", "")),
                            delay=float(pdata.get("delay", 0) or 0))
                        stats["phrases"] += 1
                    except Exception:
                        pass
        self.save()
        return stats

    def import_phrases(self, case_id: str, phrases) -> int:
        """把外部文案追加到指定用例；phrases 支持 dict / 字符串两种元素"""
        case = self.get_case(case_id)
        if case is None or not isinstance(phrases, list):
            return 0
        added = 0
        for entry in phrases:
            if isinstance(entry, dict):
                text = str(entry.get("text", ""))
                delay = float(entry.get("delay", DEFAULT_DELAY) or 0)
            else:
                text, delay = str(entry), DEFAULT_DELAY
            case.phrases.append(VoicePhrase(text=text, delay=delay))
            added += 1
        if added:
            self.save()
        return added


def get_wake_word() -> str:
    """当前配置的唤醒词；没配置（清空过）或读不出来都回落到 DEFAULT_WAKE_WORD。

    取值规则与 VoiceView._wake_word 一致（都从 voice_data.json 的 settings 段取），
    但这里**只读 settings 段**，不构造 VoiceModel —— 「动作卡片」里语音播报卡片的
    默认文案要在界面构建时取一次，没必要为了一句话把上千条语音用例读进内存。
    """
    try:
        with open(VoiceModel.DATA_FILE, encoding="utf-8") as f:
            data = json.load(f)
        word = ((data.get("settings") or {}).get("wake_word") or "").strip()
    except Exception:
        word = ""
    return word or DEFAULT_WAKE_WORD

