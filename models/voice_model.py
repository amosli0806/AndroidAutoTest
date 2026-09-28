# models/voice_model.py
"""语音播报的数据模型：独立的「语音用例」库 + 引擎设置。

持久化到 data/voice_data.json，结构：
    {
      "settings": {...},
      "groups": [{"id", "name"}],                       # 分组（对应项目树里的"功能模块"）
      "cases":  [{"id", "name", "group_id", "steps": [...]}]
    }

steps 是统一的步骤列表，每个元素带 kind 区分类型：
    {"kind": "phrase", "text", "delay"}               # 播报步骤：念给车机听的文案
    {"kind": "verify", "keywords": [...]}             # 检测步骤：预期结果（回执验证）

播报后面紧跟的检测步骤，就是对那句播报的预期结果 ——
执行时播完先等 delay，再抓车机日志按检测步骤的关键词判定。
这个结构与「自动化编辑」里"步骤 + 断言"的心智一致，方便后续导出/导入按步骤编排。

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
    # 语音回执验证：播报后从车机 logcat 抓反馈文案判定。enabled 默认关，
    # 因为抓取规则依赖具体车机的 log 格式，没适配好前开着会把正常播报误判成失败。
    # enabled 只是总闸：真正决定「验不验」的是该条播报后面有没有检测步骤。
    # success_keywords 按用例配在检测步骤里（语音播报页的「+ 添加检测」），
    # 这里的默认值已无消费方，仅为兼容旧数据文件保留，不在设置页里编辑。
    "verify": {
        "enabled": False,
        "log_tag": "",            # 车机语音助手的 log tag；留空抓全量日志
        "success_keywords": ["识别成功", "已为您", "导航到"],
        "fail_keywords": ["没听清", "无法识别", "未识别", "抱歉"],
    },
}

# 首次使用（还没有数据文件时）给一个示例分组 + 用例，直接演示「唤醒 + 指令」
SEED_GROUP_NAME = "示例分组"
SEED_CASE_NAME = "唤醒后打开地图"
SEED_PHRASES = [
    {"text": "你好，小爱同学", "delay": 2.0},
    {"text": "打开地图", "delay": 2.0},
]

KIND_PHRASE = "phrase"    # 播报步骤
KIND_VERIFY = "verify"    # 检测步骤（预期结果：回执验证关键词）

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
class VoiceStep:
    """用例里的一步。kind 区分两种类型：

    - KIND_PHRASE 播报步骤：text 是要念的文案，delay 是播后总等待（播报+缓冲）
    - KIND_VERIFY 检测步骤：keywords 是预期结果关键词（模糊包含，命中任一即通过）。
      紧跟在播报后面，执行时播完抓车机日志判定 —— 相当于"这句话的预期结果"。
    """
    kind: str = KIND_PHRASE
    text: str = ""
    delay: float = DEFAULT_DELAY
    keywords: List[str] = field(default_factory=list)

    def to_dict(self):
        if self.kind == KIND_VERIFY:
            return {"kind": KIND_VERIFY, "keywords": list(self.keywords)}
        return {"kind": KIND_PHRASE, "text": self.text, "delay": self.delay}

    @classmethod
    def from_dict(cls, data):
        data = data or {}
        kind = str(data.get("kind") or KIND_PHRASE)
        if kind == KIND_VERIFY:
            return cls(kind=KIND_VERIFY, keywords=_clean_keywords(
                data.get("keywords") or []))
        return cls(kind=KIND_PHRASE,
                   text=str(data.get("text", "")),
                   delay=float(data.get("delay", DEFAULT_DELAY) or 0))


def _clean_keywords(keywords) -> List[str]:
    """关键词去空、去重、保序 —— 各处入口（编辑/导入/迁移）共用这一套清洗。"""
    seen = set()
    clean = []
    for k in (keywords or []):
        k = str(k).strip()
        if k and k not in seen:
            seen.add(k)
            clean.append(k)
    return clean


@dataclass
class VoiceCase:
    """一个语音用例 = 一串按顺序执行的步骤（播报 + 检测预期结果）。

    steps 统一存两种步骤（VoiceStep），顺序即执行顺序；
    播报步骤后面紧跟的检测步骤，就是对那句播报的预期结果。
    """
    id: str = ""
    name: str = ""
    group_id: str = ""
    steps: List[VoiceStep] = field(default_factory=list)

    def to_dict(self):
        return {"id": self.id, "name": self.name, "group_id": self.group_id,
                "steps": [s.to_dict() for s in self.steps]}

    @classmethod
    def from_dict(cls, data):
        data = data or {}
        steps = [VoiceStep.from_dict(s) for s in (data.get("steps") or [])]
        if not steps:
            # 旧格式迁移：phrases 全部转成播报步骤；success_keywords（旧版
            # 按用例配的成功词）转成末尾一条检测步骤，语义不变（命中任一即过）
            for p in (data.get("phrases") or []):
                p = p or {}
                steps.append(VoiceStep(
                    kind=KIND_PHRASE,
                    text=str(p.get("text", "")),
                    delay=float(p.get("delay", DEFAULT_DELAY) or 0)))
            legacy_kw = _clean_keywords(data.get("success_keywords") or [])
            if legacy_kw:
                steps.append(VoiceStep(kind=KIND_VERIFY, keywords=legacy_kw))
        return cls(
            id=str(data.get("id", "")),
            name=str(data.get("name", "")),
            group_id=str(data.get("group_id", "") or ""),
            steps=steps,
        )

    @property
    def phrases(self) -> List[VoiceStep]:
        """播报步骤（kind=phrase），按原顺序。执行/勾选树只认播报步骤。"""
        return [s for s in self.steps if s.kind == KIND_PHRASE]


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
        # 本实例显式改过的 settings 键。save() 做字段级合并时只让这些键以内存为准，
        # 其余键重新从磁盘取 —— 语音页持有的是启动时的长生命周期实例，见 _merge_settings_for_save。
        self._dirty_settings = set()
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
                # 只认已知键：早先版本写进去的 volume 之类会被丢掉。
                # dict 类型的值（verify）做浅合并，用户只覆盖改动的键，默认关键词保留。
                for key, value in (data.get("settings") or {}).items():
                    if key not in DEFAULT_SETTINGS:
                        continue
                    if isinstance(DEFAULT_SETTINGS[key], dict) and isinstance(value, dict):
                        merged[key] = {**DEFAULT_SETTINGS[key], **value}
                    else:
                        merged[key] = value
                self.settings = merged
                self._dirty_settings = set()
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
        self._dirty_settings = set()
        self.groups = []
        self.cases = []
        self.save()

    def _merge_settings_for_save(self) -> dict:
        """算出本次要落盘的 settings：本实例没显式改过的键，以磁盘上的为准。

        为什么需要：语音管理页在 main.py 启动时 new 了一个 VoiceModel 并一直持有，
        它的 self.settings 是启动那一刻的快照。设置页改了配置（比如清空日志标签）
        之后，用户只要回语音页动一下语速、加一条步骤，这个旧实例的 save() 就会把
        整份旧 settings 写回去，设置页的改动被无声覆盖 —— 现象就是"清掉的日志标签
        又回来了"。改成字段级合并：只有本实例 set_settings 过的键以内存为准。
        """
        base = {}
        try:
            with open(self.DATA_FILE, encoding="utf-8") as f:
                base = json.load(f).get("settings") or {}
        except Exception:
            base = {}
        merged = {**DEFAULT_SETTINGS, **base}
        for key in self._dirty_settings:
            if key in self.settings:
                merged[key] = self.settings[key]
        return merged

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.DATA_FILE), exist_ok=True)
            self.settings = self._merge_settings_for_save()
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
                self._dirty_settings.add(key)
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
                steps=[VoiceStep(kind=s.kind, text=s.text, delay=s.delay,
                                 keywords=list(s.keywords))
                       for s in case.steps],
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
            steps=[VoiceStep(kind=s.kind, text=s.text, delay=s.delay,
                             keywords=list(s.keywords))
                   for s in src.steps],
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

    def set_case_success_keywords(self, case_id: str, keywords) -> bool:
        """（旧接口，兼容保留）把一组成功关键词写成用例末尾的一条检测步骤。

        新代码请直接用 add_verify / update_step —— 关键词已经是步骤列表里的
        独立步骤，不再挂在用例属性上。
        """
        case = self.get_case(case_id)
        if case is None:
            return False
        clean = _clean_keywords(keywords)
        # 末尾已经是检测步骤就就地覆盖；否则追加一条（清空=删掉末尾那条）
        if case.steps and case.steps[-1].kind == KIND_VERIFY:
            if clean:
                case.steps[-1].keywords = clean
            else:
                case.steps.pop()
        elif clean:
            case.steps.append(VoiceStep(kind=KIND_VERIFY, keywords=clean))
        self.save()
        return True

    def move_case_to_group(self, case_id: str, group_id: str) -> bool:
        case = self.get_case(case_id)
        if case is None:
            return False
        case.group_id = group_id or UNGROUPED_ID
        self.save()
        return True

    # ---------------- 步骤（播报 / 检测） ----------------
    def add_phrase(self, case_id: str, text: str = "",
                   delay: float = DEFAULT_DELAY) -> Optional[VoiceStep]:
        """追加一条播报步骤。"""
        case = self.get_case(case_id)
        if case is None:
            return None
        step = VoiceStep(kind=KIND_PHRASE, text=text, delay=delay)
        case.steps.append(step)
        self.save()
        return step

    def add_verify(self, case_id: str, keywords) -> Optional[VoiceStep]:
        """追加一条检测步骤（预期结果）。keywords 传入前先做去空去重。"""
        case = self.get_case(case_id)
        if case is None:
            return None
        step = VoiceStep(kind=KIND_VERIFY, keywords=_clean_keywords(keywords))
        case.steps.append(step)
        self.save()
        return step

    def update_step(self, case_id: str, index: int, **kwargs) -> bool:
        """按下标改一步的属性（text / delay / keywords）。"""
        case = self.get_case(case_id)
        if case is None or not (0 <= index < len(case.steps)):
            return False
        step = case.steps[index]
        for key, value in kwargs.items():
            if key == "keywords":
                step.keywords = _clean_keywords(value)
            elif hasattr(step, key):
                setattr(step, key, value)
        self.save()
        return True

    # 下面三个按下标操作的老接口保留原名（视图层沿用），但操作的是统一步骤列表
    def update_phrase(self, case_id: str, index: int, **kwargs) -> bool:
        return self.update_step(case_id, index, **kwargs)

    def remove_step(self, case_id: str, index: int) -> bool:
        case = self.get_case(case_id)
        if case is None or not (0 <= index < len(case.steps)):
            return False
        del case.steps[index]
        self.save()
        return True

    def remove_phrase(self, case_id: str, index: int) -> bool:
        return self.remove_step(case_id, index)

    def copy_step(self, case_id: str, index: int) -> bool:
        """复制第 index 步，副本追加到整个步骤列表的末尾。"""
        case = self.get_case(case_id)
        if case is None or not (0 <= index < len(case.steps)):
            return False
        src = case.steps[index]
        case.steps.append(VoiceStep(kind=src.kind, text=src.text,
                                    delay=src.delay, keywords=list(src.keywords)))
        self.save()
        return True

    def copy_phrase(self, case_id: str, index: int) -> bool:
        return self.copy_step(case_id, index)

    # ---------------- 导入 / 导出 ----------------
    def export_case(self, case_id: str) -> Optional[dict]:
        case = self.get_case(case_id)
        if case is None:
            return None
        return {"name": case.name,
                "steps": [s.to_dict() for s in case.steps]}

    def export_all(self) -> dict:
        """全量导出：分组 -> 用例 -> 步骤（播报+检测），整棵树打包成一个 dict。

        与 export_case（单用例步骤包）不同，这个格式包含分组与用例结构，
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
                     "steps": [s.to_dict() for s in c.steps]}
                    for c in cases_by_group.get(g.id, [])
                ],
            })
        return {"format": "voice-cases-all", "version": 2, "groups": groups}

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
                # 步骤按导出顺序原样带入；旧格式文件（phrases + success_keywords）
                # 由 VoiceCase.from_dict 的迁移逻辑统一转换，这里直接借它解析
                legacy_case = VoiceCase.from_dict(cdata)
                for step in legacy_case.steps:
                    case.steps.append(VoiceStep(
                        kind=step.kind, text=step.text,
                        delay=step.delay, keywords=list(step.keywords)))
                    stats["phrases"] += 1
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
            case.steps.append(VoiceStep(kind=KIND_PHRASE, text=text, delay=delay))
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


def get_rate() -> int:
    """当前配置的语速（SAPI -10..10）。只读 settings 段，不构造 VoiceModel。

    给「按语速估算播后等待」用：估算按钮在动作卡片等轻量场景里也要拿到语速，
    没必要为读一个 int 把上千条语音用例读进内存。
    """
    try:
        with open(VoiceModel.DATA_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return int((data.get("settings") or {}).get("rate", 0) or 0)
    except Exception:
        return 0


def get_verify_config() -> dict:
    """当前配置的语音回执验证规则。只读 settings 段，不构造 VoiceModel。

    返回 {enabled, log_tag, success_keywords, fail_keywords}，都带安全的默认值。
    供语音页 worker 在执行线程里读取，避免构造 VoiceModel。
    其中 success_keywords 已无消费方（成功关键词按用例配在检测步骤里），
    保留只是为了不破坏旧数据文件。
    """
    cfg = {
        "enabled": False,
        "log_tag": "",
        "success_keywords": DEFAULT_SETTINGS["verify"]["success_keywords"],
        "fail_keywords": DEFAULT_SETTINGS["verify"]["fail_keywords"],
    }
    try:
        with open(VoiceModel.DATA_FILE, encoding="utf-8") as f:
            data = json.load(f)
        v = ((data.get("settings") or {}).get("verify") or {})
        cfg["enabled"] = bool(v.get("enabled", False))
        cfg["log_tag"] = str(v.get("log_tag") or "").strip()
        if v.get("success_keywords"):
            cfg["success_keywords"] = [str(x) for x in v["success_keywords"] if str(x).strip()]
        if v.get("fail_keywords"):
            cfg["fail_keywords"] = [str(x) for x in v["fail_keywords"] if str(x).strip()]
    except Exception:
        pass
    return cfg

