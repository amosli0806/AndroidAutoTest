# models/var_model.py
"""全局变量表：`name → value`（一律按字符串存），供步骤参数里的 `${变量}` 引用。

三条约定（别绕开）：

  1. **只有一层（全局）**。不做套件 / 环境作用域 —— 先有一层能用的，再谈分层；
     真需要区分环境时用命名前缀（`test_url` / `prod_url`）就够，比多一层作用域好懂。
  2. **值就是字符串**。替换是纯文本替换，不做类型推断（`01` 不会变成 `1`）。
  3. **内置变量只读**。`serial / date / time / timestamp` 由运行时算，不落盘，
     也不允许用户定义同名变量 —— 「设备号」这种关键信息被误改会很难查。

本文件不依赖 Qt：数据层只管存取；替换逻辑见 utils/vars.py。
"""
import json
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional

from utils.app_paths import data_path

# 变量名规则：字母 / 下划线开头，后接字母数字下划线（与常见模板引擎一致）。
# 刻意不允许中文与空格 —— `${测试账号}` 这种在日志里容易和中文正文糊在一起。
VAR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# 内置变量（只读，见文件头第 3 条）
BUILTIN_NAMES = ("serial", "date", "time", "timestamp")


@dataclass
class Var:
    """一个用户变量。note 只是给人看的说明（会显示在变量页第三列）。"""
    name: str
    value: str = ""
    note: str = ""

    def to_dict(self):
        return {'name': self.name, 'value': self.value, 'note': self.note}

    @classmethod
    def from_dict(cls, data):
        return cls(name=str(data.get('name', '')),
                   value=str(data.get('value', '')),
                   note=str(data.get('note', '')))


def is_valid_name(name: str) -> bool:
    """变量名是否合法（同时用于界面校验与导入校验）。"""
    name = str(name or "").strip()
    if not name or not VAR_NAME_RE.match(name):
        return False
    return name not in BUILTIN_NAMES      # 内置变量名保留，不允许占用


def name_error(name: str) -> str:
    """返回给人看的错误说明（合法时为空串），界面直接显示。"""
    name = str(name or "").strip()
    if not name:
        return "变量名不能为空"
    if name in BUILTIN_NAMES:
        return f"「{name}」是内置变量，不能占用"
    if not VAR_NAME_RE.match(name):
        return "变量名只能用字母、数字、下划线，且不能以数字开头"
    return ""


class VarModel:
    """变量表的读写。落盘为 data/vars_data.json（与其它 data/*.json 同目录）。"""

    DATA_FILE = data_path("vars_data.json")

    def __init__(self):
        self.vars: List[Var] = []
        self.load()

    # ------------------------------------------------------------------
    def load(self):
        self.vars = []
        if not os.path.exists(self.DATA_FILE):
            return
        try:
            with open(self.DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            for item in data.get('vars', []) or []:
                var = Var.from_dict(item)
                if is_valid_name(var.name):
                    self.vars.append(var)
        except Exception as e:
            print(f"[vars] 读取失败，按空表处理: {e}")
            self.vars = []

    def save(self):
        payload = {'vars': [v.to_dict() for v in self.vars]}
        os.makedirs(os.path.dirname(self.DATA_FILE), exist_ok=True)
        tmp = self.DATA_FILE + ".tmp"
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.DATA_FILE)      # 原子替换，避免写一半断电把表写坏

    # ------------------------------------------------------------------
    def get_all(self) -> List[Var]:
        """按名称排序返回（界面表格直接用，顺序稳定）。"""
        return sorted(self.vars, key=lambda v: v.name.lower())

    def get(self, name: str) -> Optional[Var]:
        name = str(name or "").strip()
        for v in self.vars:
            if v.name == name:
                return v
        return None

    def upsert(self, name: str, value: str, note: str = "") -> bool:
        """新增或更新；名字不合法直接拒绝（返回 False）。"""
        name = str(name or "").strip()
        if not is_valid_name(name):
            return False
        var = self.get(name)
        if var is None:
            self.vars.append(Var(name=name, value=str(value), note=str(note)))
        else:
            var.value = str(value)
            var.note = str(note)
        self.save()
        return True

    def remove(self, name: str) -> bool:
        var = self.get(name)
        if var is None:
            return False
        self.vars.remove(var)
        self.save()
        return True

    def rename(self, old: str, new: str) -> bool:
        """改名（值 / 说明保留）。步骤里引用旧名的地方不会自动改写 —— 见变量页提示。"""
        old, new = str(old or "").strip(), str(new or "").strip()
        var = self.get(old)
        if var is None or not is_valid_name(new) or old == new:
            return False
        if self.get(new) is not None:
            return False
        var.name = new
        self.save()
        return True

    def values(self) -> Dict[str, str]:
        """用户变量字典（不含内置变量；内置由 utils/vars.current_values 合并）。"""
        return {v.name: v.value for v in self.vars}
