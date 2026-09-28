# models/api_model.py
"""接口自动化数据层：分组 → 接口 两层，外加一份环境表。

为什么只有两层（不像「自动化编辑」那样 项目 → 模块 → 用例 三层）：接口测试的天然
组织单位就是「按业务域分组的一堆接口」，这轮不需要中间层 —— 场景编排、链路串联是
二期的事，先不引入会白占结构的层级。

存储：data/api_data.json，与其它模块一致（顶层一个 dict，每段一个数组）。
环境变量的 {{var}} 替换只发生在 URL / 请求头 / 请求体 三处，规则见 services/api_service。
"""
import json
import os
import time
from dataclasses import dataclass, field
from typing import List, Optional

from utils.app_paths import data_path

# 请求方法（顺序即界面下拉框顺序）
METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD"]

# 请求体类型：非 GET/HEAD 才用得上
BODY_NONE = "无"
BODY_JSON = "JSON"
BODY_FORM = "表单"
BODY_RAW = "原始文本"
BODY_TYPES = [BODY_NONE, BODY_JSON, BODY_FORM, BODY_RAW]

# 断言类型
ASSERT_STATUS = "status"       # 状态码
ASSERT_JSONPATH = "jsonpath"   # 响应体按路径取值
ASSERT_CONTAINS = "contains"   # 响应体包含文本
ASSERT_REGEX = "regex"         # 响应体正则匹配
ASSERT_TIME = "time"           # 响应耗时上限（毫秒）
ASSERT_TYPES = [ASSERT_STATUS, ASSERT_JSONPATH, ASSERT_CONTAINS, ASSERT_REGEX, ASSERT_TIME]

# 断言类型 -> 界面显示名
ASSERT_LABELS = {
    ASSERT_STATUS: "状态码",
    ASSERT_JSONPATH: "响应取值",
    ASSERT_CONTAINS: "包含文本",
    ASSERT_REGEX: "正则匹配",
    ASSERT_TIME: "耗时(毫秒)",
}

# 断言操作符
OP_EQ = "等于"
OP_NE = "不等于"
OP_CONTAINS = "包含"
OP_NOT_CONTAINS = "不包含"
OP_REGEX = "正则匹配"
OP_GT = "大于"
OP_LT = "小于"
OPS = [OP_EQ, OP_NE, OP_CONTAINS, OP_NOT_CONTAINS, OP_REGEX, OP_GT, OP_LT]

DEFAULT_ENV_NAME = "默认环境"
DEFAULT_BASE_URL = "https://"


def _new_id(prefix: str, existing) -> str:
    """生成不与 existing 冲突的 id：时间戳做基，撞了就自增。"""
    base = int(time.time() * 1000) % 1000000000
    index = 0
    while True:
        candidate = f"{prefix}_{base}_{index}"
        if candidate not in existing:
            return candidate
        index += 1


@dataclass
class ApiAssert:
    """一条断言。expr 的含义随 type 变：响应取值时是 JSON 路径，耗时/状态码时留空。"""
    type: str = ASSERT_STATUS
    expr: str = ""
    op: str = OP_EQ
    expect: str = ""
    enabled: bool = True

    def to_dict(self):
        return {
            "type": self.type, "expr": self.expr, "op": self.op,
            "expect": self.expect, "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            type=str(data.get("type") or ASSERT_STATUS),
            expr=str(data.get("expr") or ""),
            op=str(data.get("op") or OP_EQ),
            expect=str(data.get("expect") or ""),
            enabled=bool(data.get("enabled", True)),
        )

    def describe(self) -> str:
        """给日志/报告用的一句话描述。"""
        label = ASSERT_LABELS.get(self.type, self.type)
        if self.type == ASSERT_STATUS:
            return f"状态码 {self.op} {self.expect}"
        if self.type == ASSERT_TIME:
            return f"耗时 {self.op} {self.expect} 毫秒"
        return f"{label}「{self.expr}」{self.op} {self.expect}"


@dataclass
class ApiCase:
    """一个接口。headers 用「[名称, 值] 列表」而不是 dict：界面上要能留空行、
    也要保持用户录入顺序，dict 会丢顺序。"""
    id: str
    name: str
    group_id: str = ""
    method: str = "GET"
    url: str = ""
    headers: List[List[str]] = field(default_factory=list)
    body_type: str = BODY_NONE
    body: str = ""
    description: str = ""
    asserts: List[ApiAssert] = field(default_factory=list)

    def to_dict(self):
        return {
            "id": self.id, "name": self.name, "group_id": self.group_id,
            "method": self.method, "url": self.url,
            "headers": [list(h) for h in self.headers],
            "body_type": self.body_type, "body": self.body,
            "description": self.description,
            "asserts": [a.to_dict() for a in self.asserts],
        }

    @classmethod
    def from_dict(cls, data):
        raw_headers = data.get("headers") or []
        headers = []
        for item in raw_headers:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                headers.append([str(item[0]), str(item[1])])
            elif isinstance(item, dict):   # 容错：早期/手写的 {"k": "v"} 形式
                for k, v in item.items():
                    headers.append([str(k), str(v)])
        return cls(
            id=str(data.get("id", "")),
            name=str(data.get("name", "")),
            group_id=str(data.get("group_id", "")),
            method=str(data.get("method") or "GET").upper(),
            url=str(data.get("url") or ""),
            headers=headers,
            body_type=str(data.get("body_type") or BODY_NONE),
            body=str(data.get("body") or ""),
            description=str(data.get("description") or ""),
            asserts=[ApiAssert.from_dict(a) for a in (data.get("asserts") or [])],
        )


@dataclass
class ApiGroup:
    id: str
    name: str

    def to_dict(self):
        return {"id": self.id, "name": self.name}

    @classmethod
    def from_dict(cls, data):
        return cls(id=str(data.get("id", "")), name=str(data.get("name", "")))


@dataclass
class ApiEnv:
    """一套环境：一个 base_url + 若干自定义变量，供 {{var}} 引用。"""
    name: str
    base_url: str = DEFAULT_BASE_URL
    variables: List[List[str]] = field(default_factory=list)

    def to_dict(self):
        return {
            "name": self.name, "base_url": self.base_url,
            "variables": [list(v) for v in self.variables],
        }

    @classmethod
    def from_dict(cls, data):
        raw = data.get("variables") or []
        variables = []
        for item in raw:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                variables.append([str(item[0]), str(item[1])])
            elif isinstance(item, dict):
                for k, v in item.items():
                    variables.append([str(k), str(v)])
        return cls(
            name=str(data.get("name") or DEFAULT_ENV_NAME),
            base_url=str(data.get("base_url") or DEFAULT_BASE_URL),
            variables=variables,
        )

    def as_map(self) -> dict:
        """变量表 -> dict，供渲染器查表。base_url 也进表，用 {{base_url}} 引用。"""
        table = {k: v for k, v in self.variables if k}
        table.setdefault("base_url", self.base_url)
        return table


class ApiModel:
    DATA_FILE = data_path("api_data.json")

    def __init__(self):
        self.groups: List[ApiGroup] = []
        self.cases: List[ApiCase] = []
        self.envs: List[ApiEnv] = []
        self.current_env: str = ""
        self.load()

    # ---------------- 读写 ----------------
    def load(self):
        if os.path.exists(self.DATA_FILE):
            try:
                with open(self.DATA_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.groups = [ApiGroup.from_dict(g) for g in (data.get("groups") or [])]
                self.cases = [ApiCase.from_dict(c) for c in (data.get("cases") or [])]
                self.envs = [ApiEnv.from_dict(e) for e in (data.get("envs") or [])]
                self.current_env = str(data.get("current_env") or "")
            except Exception:
                # 文件读坏了：退回空状态（不覆盖原文件，用户还能手工抢救）
                self.groups, self.cases, self.envs = [], [], []
                self.current_env = ""
        else:
            self.groups, self.cases, self.envs = [], [], []
            self.current_env = ""
            self.save()
        self._ensure_env()
        self._ensure_group()

    def _ensure_env(self):
        """至少留一套环境：没有就补一个「默认环境」，免得界面上下拉框是空的。"""
        if not self.envs:
            self.envs = [ApiEnv(name=DEFAULT_ENV_NAME)]
            self.save()
        names = [e.name for e in self.envs]
        if self.current_env not in names:
            self.current_env = self.envs[0].name
            self.save()

    def _ensure_group(self):
        """至少留一个分组：新用户点进来就能直接建接口，不用先学"要建分组"。"""
        if not self.groups:
            self.groups = [ApiGroup(id=_new_id("agroup", set()), name="默认分组")]
            self.save()

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.DATA_FILE), exist_ok=True)
            with open(self.DATA_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "groups": [g.to_dict() for g in self.groups],
                    "cases": [c.to_dict() for c in self.cases],
                    "envs": [e.to_dict() for e in self.envs],
                    "current_env": self.current_env,
                }, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[api_model] 保存失败 {self.DATA_FILE}: {e}")

    # ---------------- 分组 ----------------
    def get_group(self, group_id: str) -> Optional[ApiGroup]:
        return next((g for g in self.groups if g.id == group_id), None)

    def add_group(self, name: str = "") -> ApiGroup:
        if not name:
            existing = {g.name for g in self.groups}
            index = len(self.groups) + 1
            while f"分组 {index}" in existing:
                index += 1
            name = f"分组 {index}"
        group = ApiGroup(id=_new_id("agroup", {g.id for g in self.groups}), name=name)
        self.groups.append(group)
        self.save()
        return group

    def rename_group(self, group_id: str, name: str) -> bool:
        group = self.get_group(group_id)
        if group is None or not name:
            return False
        group.name = name
        self.save()
        return True

    def remove_group(self, group_id: str, with_cases: bool = True) -> int:
        """删分组。with_cases=True 连组内接口一起删，返回被删掉的接口数。"""
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
            for case in self.cases:
                if case.group_id == group_id:
                    case.group_id = ""
        self.groups.remove(group)
        self.save()
        return removed

    def cases_of_group(self, group_id: str) -> List[ApiCase]:
        return [c for c in self.cases if c.group_id == group_id]

    # ---------------- 接口 ----------------
    def get_case(self, case_id: str) -> Optional[ApiCase]:
        return next((c for c in self.cases if c.id == case_id), None)

    def add_case(self, name: str = "", group_id: str = "") -> ApiCase:
        if not group_id:
            group_id = self.groups[0].id if self.groups else ""
        if not name:
            existing = {c.name for c in self.cases if c.group_id == group_id}
            index = len(self.cases_of_group(group_id)) + 1
            while f"接口 {index}" in existing:
                index += 1
            name = f"接口 {index}"
        case = ApiCase(id=_new_id("acase", {c.id for c in self.cases}),
                       name=name, group_id=group_id)
        self.cases.append(case)
        self.save()
        return case

    def copy_case(self, case_id: str) -> Optional[ApiCase]:
        src = self.get_case(case_id)
        if src is None:
            return None
        existing = {c.name for c in self.cases}
        name = f"{src.name} - 副本"
        index = 2
        while name in existing:
            name = f"{src.name} - 副本{index}"
            index += 1
        case = ApiCase.from_dict(src.to_dict())
        case.id = _new_id("acase", {c.id for c in self.cases})
        case.name = name
        # 深拷贝断言，避免副本和原件共享同一批对象
        case.asserts = [ApiAssert.from_dict(a.to_dict()) for a in src.asserts]
        self.cases.append(case)
        self.save()
        return case

    def rename_case(self, case_id: str, name: str) -> bool:
        case = self.get_case(case_id)
        if case is None or not name:
            return False
        case.name = name
        self.save()
        return True

    def remove_case(self, case_id: str) -> bool:
        case = self.get_case(case_id)
        if case is None:
            return False
        self.cases.remove(case)
        self.save()
        return True

    def update_case(self, case_id: str, **kwargs) -> bool:
        case = self.get_case(case_id)
        if case is None:
            return False
        for key, value in kwargs.items():
            if hasattr(case, key):
                setattr(case, key, value)
        self.save()
        return True

    def set_asserts(self, case_id: str, asserts: List[ApiAssert]) -> bool:
        case = self.get_case(case_id)
        if case is None:
            return False
        case.asserts = list(asserts)
        self.save()
        return True

    # ---------------- 环境 ----------------
    def get_env(self, name: str = None) -> Optional[ApiEnv]:
        target = name if name is not None else self.current_env
        return next((e for e in self.envs if e.name == target), None)

    def add_env(self, name: str = "") -> ApiEnv:
        if not name:
            existing = {e.name for e in self.envs}
            index = len(self.envs) + 1
            while f"环境 {index}" in existing:
                index += 1
            name = f"环境 {index}"
        env = ApiEnv(name=name)
        self.envs.append(env)
        self.save()
        return env

    def update_env(self, name: str, **kwargs) -> bool:
        env = self.get_env(name)
        if env is None:
            return False
        old_name = env.name
        for key, value in kwargs.items():
            if hasattr(env, key):
                setattr(env, key, value)
        if env.name != old_name and self.current_env == old_name:
            self.current_env = env.name
        self.save()
        return True

    def remove_env(self, name: str) -> bool:
        """删环境。至少留一套，否则 {{base_url}} 无处可查。"""
        if len(self.envs) <= 1:
            return False
        env = self.get_env(name)
        if env is None:
            return False
        self.envs.remove(env)
        if self.current_env == name:
            self.current_env = self.envs[0].name
        self.save()
        return True

    def set_current_env(self, name: str) -> bool:
        if self.get_env(name) is None:
            return False
        self.current_env = name
        self.save()
        return True

    # ---------------- 导入导出 ----------------
    def export_all(self) -> dict:
        """整份导出（分组 + 接口 + 环境），换机器时能完整还原。"""
        return {
            "groups": [g.to_dict() for g in self.groups],
            "cases": [c.to_dict() for c in self.cases],
            "envs": [e.to_dict() for e in self.envs],
        }

    def import_all(self, data: dict) -> dict:
        """导入：分组按名字合并，接口按名字去重后追加，环境按名字覆盖。

        返回统计 {'groups': n, 'cases': n, 'skipped': n, 'envs': n}。
        与「语音播报」「自动化编辑」的导入语义一致：增量合并、同名跳过，不覆盖现有数据。
        """
        if not isinstance(data, dict):
            raise ValueError("无效的接口配置文件")
        if not any(k in data for k in ("groups", "cases", "envs")):
            raise ValueError("无效的接口配置文件：缺少 groups / cases / envs")

        stats = {"groups": 0, "cases": 0, "skipped": 0, "envs": 0}

        # 分组：按名字对齐，导入的分组 id 需要重映射到本地 id
        id_map = {}
        for gdata in (data.get("groups") or []):
            g = ApiGroup.from_dict(gdata)
            local = next((x for x in self.groups if x.name == g.name), None)
            if local is None:
                local = self.add_group(g.name)
                stats["groups"] += 1
            id_map[g.id] = local.id

        for cdata in (data.get("cases") or []):
            case = ApiCase.from_dict(cdata)
            group_id = id_map.get(case.group_id, case.group_id)
            if self.get_group(group_id) is None:
                group_id = self.groups[0].id if self.groups else ""
            existing = {c.name for c in self.cases if c.group_id == group_id}
            if case.name in existing:
                stats["skipped"] += 1
                continue
            case.id = _new_id("acase", {c.id for c in self.cases})
            case.group_id = group_id
            self.cases.append(case)
            stats["cases"] += 1

        for edata in (data.get("envs") or []):
            env = ApiEnv.from_dict(edata)
            local = self.get_env(env.name)
            if local is None:
                self.envs.append(env)
                stats["envs"] += 1
            else:
                local.base_url = env.base_url
                local.variables = [list(v) for v in env.variables]

        self.save()
        return stats
