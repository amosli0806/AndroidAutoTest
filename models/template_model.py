# models/template_model.py
"""步骤模板：把一段步骤存下来，在任意用例里插入复用。

和「用例复制」（`StepModel.copy_steps_for_case`）的区别：那个是整条用例搬家，
这个是**片段**复用。和「元素库」的区别：那个复用单个控件的定位值，
这个复用一整段流程（启动 → 等待 → 点击）。

模板里存的是**步骤快照**而不是原步骤 id 的引用 —— 原用例被删掉后模板必须还能用，
所以插入时统一重新分配 id（见 `StepModel.insert_steps_at`）。

数据文件独立于 steps_data.json：模板是跨用例的资产，不该跟用例一起被清理
（`gc_orphan_steps` 只处理用例下的孤儿步骤，碰不到这里）。
"""
import copy
import json
import os
import time
from typing import Any, Dict, List, Optional

from utils.app_paths import data_path


class TemplateModel:
    DATA_FILE = data_path("step_templates.json")
    MAX_NAME = 40          # 名字太长在选择器里会撑破一行
    MAX_TEMPLATES = 200    # 只是防御，正常用不到

    def __init__(self):
        self._id_counter = 1
        self._templates: List[Dict[str, Any]] = []
        self.load()

    # ---------- 持久化 ----------
    def load(self):
        if os.path.exists(self.DATA_FILE):
            try:
                with open(self.DATA_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._id_counter = int(data.get("id_counter", 1))
                self._templates = list(data.get("templates", []))
                return
            except Exception:
                pass
        self._templates = []
        self._id_counter = 1

    def save(self):
        """先写临时文件再 replace：中途出错不会把原文件写坏（模板丢了比步骤丢了更烦）。"""
        data = {"id_counter": self._id_counter, "templates": self._templates}
        try:
            os.makedirs(os.path.dirname(self.DATA_FILE), exist_ok=True)
            tmp = self.DATA_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.DATA_FILE)
        except Exception:
            pass

    # ---------- 查询 ----------
    def all(self) -> List[Dict[str, Any]]:
        """按最近使用排序：常用的自然浮到最上面，不用翻。"""
        return sorted(self._templates, key=lambda t: t.get("last_used_at", ""),
                      reverse=True)

    def get(self, template_id: int) -> Optional[Dict[str, Any]]:
        for t in self._templates:
            if t.get("id") == template_id:
                return t
        return None

    def groups(self) -> Dict[str, int]:
        """分组 → 数量，给选择器左侧的筛选列表用。"""
        out: Dict[str, int] = {}
        for t in self._templates:
            g = (t.get("group") or "未分组").strip() or "未分组"
            out[g] = out.get(g, 0) + 1
        return out

    # ---------- 写入 ----------
    def save_from_steps(self, name: str, group: str, steps: List[Any]) -> tuple:
        """把一批步骤存成模板。同名则覆盖（返回的第二个值会说明）。

        steps 是 `Step` 对象列表，只取 type / name / params 做快照。
        """
        name = (name or "").strip()
        if not name:
            return False, "模板名称不能为空"
        if len(name) > self.MAX_NAME:
            return False, f"名称最长 {self.MAX_NAME} 个字符"
        if not steps:
            return False, "没有可保存的步骤"
        if len(self._templates) >= self.MAX_TEMPLATES:
            return False, f"模板数量已达上限（{self.MAX_TEMPLATES}）"

        snapshot = [{
            "type": s.type,
            "name": s.name,
            "params": copy.deepcopy(s.params or {}),
        } for s in steps]

        now = time.strftime("%Y-%m-%d %H:%M:%S")
        group = (group or "").strip() or "未分组"

        existing = next((t for t in self._templates if t.get("name") == name), None)
        if existing:
            existing["group"] = group
            existing["steps"] = snapshot
            existing["updated_at"] = now
            self.save()
            return True, f"已覆盖同名模板「{name}」（{len(snapshot)} 个步骤）"

        self._templates.append({
            "id": self._id_counter,
            "name": name,
            "group": group,
            "steps": snapshot,
            "created_at": now,
            "updated_at": now,
            "last_used_at": "",
            "used_count": 0,
        })
        self._id_counter += 1
        self.save()
        return True, f"已保存模板「{name}」（{len(snapshot)} 个步骤）"

    def rename(self, template_id: int, new_name: str) -> tuple:
        t = self.get(template_id)
        if t is None:
            return False, "模板不存在"
        new_name = (new_name or "").strip()
        if not new_name:
            return False, "名称不能为空"
        if len(new_name) > self.MAX_NAME:
            return False, f"名称最长 {self.MAX_NAME} 个字符"
        if any(o.get("name") == new_name and o.get("id") != template_id
               for o in self._templates):
            return False, "已有同名模板"
        t["name"] = new_name
        t["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self.save()
        return True, "已重命名"

    def delete(self, template_id: int) -> tuple:
        before = len(self._templates)
        self._templates = [t for t in self._templates if t.get("id") != template_id]
        if len(self._templates) == before:
            return False, "模板不存在"
        self.save()
        return True, "已删除"

    def touch(self, template_id: int):
        """记录一次使用：选择器按 last_used_at 排序，常用的会浮上来。"""
        t = self.get(template_id)
        if t is None:
            return
        t["used_count"] = int(t.get("used_count", 0)) + 1
        t["last_used_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self.save()

    # ---------- 给插入用 ----------
    @staticmethod
    def steps_to_snapshot_list(template: Dict[str, Any]) -> List[Dict[str, Any]]:
        """取出模板里的步骤快照（深拷贝，避免调用方改到模板本体）。"""
        return copy.deepcopy(template.get("steps") or [])
