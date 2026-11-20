# utils/history_diff.py
"""把两个快照（或快照与当前数据）的差异翻译成人话。

为什么不直接给 JSON 文本 diff：用户看不懂 "+12 −3" 这种行号差异，
也不知道哪个 id 对应哪个用例。这里按 id 对齐实体，输出的是
「用例「首页导航」新增 2 个步骤」「元素「首页按钮」定位从文本改成 ID」。

只处理到「实体级 + 关键字段级」，不做深层递归 —— 再细下去收益递减，
而且输出会长到没法读。
"""
import gzip
import json
import os
from typing import Dict, List, Optional

# 文件 → 分组名（面板上按这个分组展示）
GROUP_NAMES = {
    "project_data.json": "用例与项目",
    "steps_data.json": "步骤",
    "elements_data.json": "元素库",
    "suites_data.json": "测试套件",
    "tasks_data.json": "任务",
    "vars_data.json": "变量",
    "voice_data.json": "语音",
    "api_data.json": "接口",
    "knowledge_data.json": "知识库",
    "step_templates.json": "步骤模板",
    "config.json": "设置",
}

# 每个分组最多列这么多条，其余折叠成「还有 N 处」
MAX_ITEMS = 8
# 参数逐项比较时最多列这么多
MAX_PARAM_ITEMS = 3


def _load(directory: str, filename: str) -> Optional[object]:
    """读 json：快照目录里是 .gz，data/ 里是原始文件，两种都认。"""
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


def _short(value, limit: int = 24) -> str:
    """值转成一行短文本。"""
    if value is None:
        return "（空）"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (list, dict)):
        s = json.dumps(value, ensure_ascii=False)
    else:
        s = str(value)
    s = s.replace("\n", " ").strip()
    if not s:
        return "（空）"
    return s if len(s) <= limit else s[:limit] + "…"


def _walk_tree(nodes, out: Dict[str, dict], parent_id: str = "", parent_name: str = ""):
    """把 project_data 的树拍平成 {id: {name, type, parent_id, parent_name}}。

    同时记 parent 的 **id** 和名字：判断「是否移动」只能比 id ——
    父节点改名时名字会变，但节点其实没动；只比名字会误报一堆「移动」。
    """
    if not isinstance(nodes, list):
        return
    for n in nodes:
        if not isinstance(n, dict):
            continue
        nid = str(n.get("id") or "")
        name = (n.get("name") or "").strip()
        if nid:
            out[nid] = {
                "name": name or nid,
                "type": n.get("type") or "",
                "parent_id": parent_id,
                "parent_name": parent_name,
            }
        _walk_tree(n.get("children"), out, nid, name)


def _entities(obj) -> Dict[str, dict]:
    """从 json 里抽实体：顶层 list，或 dict 里值为 list 的字段。"""
    items: List[dict] = []
    if isinstance(obj, list):
        items = [x for x in obj if isinstance(x, dict)]
    elif isinstance(obj, dict):
        for v in obj.values():
            if isinstance(v, list):
                items.extend(x for x in v if isinstance(x, dict))
    return {str(x["id"]): x for x in items if "id" in x}


def _diff_entity_fields(old: dict, new: dict) -> List[str]:
    """比较同一个实体的字段，返回描述改动的人话（可能多条）。"""
    out: List[str] = []
    if (old.get("name") or "") != (new.get("name") or ""):
        out.append(f"名称：{_short(old.get('name'))} → {_short(new.get('name'))}")

    old_p = old.get("params") or {}
    new_p = new.get("params") or {}
    if isinstance(old_p, dict) and isinstance(new_p, dict):
        shown = 0
        for key in sorted(set(old_p) | set(new_p)):
            a, b = old_p.get(key), new_p.get(key)
            if a == b:
                continue
            if shown >= MAX_PARAM_ITEMS:
                out.append("…还有更多参数变化")
                break
            out.append(f"{key}：{_short(a)} → {_short(b)}")
            shown += 1

    # 元素库这类扁平的实体，关键字段单独比（没有 params 包装）
    for key in ("loc_type", "loc_value", "app", "module", "remark", "type"):
        a, b = old.get(key), new.get(key)
        if a != b and key not in ("params",):
            out.append(f"{key}：{_short(a)} → {_short(b)}")
    return out


def _add(group: str, kind: str, text: str, buckets: Dict[str, List[dict]]):
    buckets.setdefault(group, []).append({"kind": kind, "text": text})


def _diff_flat(old_map: Dict[str, dict], new_map: Dict[str, dict],
               group: str, buckets: Dict[str, List[dict]]):
    """通用实体比较：新增 / 删除 / 修改。"""
    for eid in new_map:
        if eid not in old_map:
            _add(group, "add", f"新增「{_short(new_map[eid].get('name'), 18)}」", buckets)
    for eid in old_map:
        if eid not in new_map:
            _add(group, "del", f"删除「{_short(old_map[eid].get('name'), 18)}」", buckets)
    for eid in old_map:
        if eid not in new_map:
            continue
        changes = _diff_entity_fields(old_map[eid], new_map[eid])
        if changes:
            name = _short(new_map[eid].get("name"), 18)
            for c in changes[:3]:
                _add(group, "mod", f"「{name}」{c}", buckets)


def _diff_projects(old, new, buckets: Dict[str, List[dict]]) -> Dict[str, str]:
    """项目树：新增 / 删除 / 改名 / 移动。返回 {id: 名称} 给步骤比较用。"""
    old_tree: Dict[str, dict] = {}
    new_tree: Dict[str, dict] = {}
    _walk_tree(old, old_tree)
    _walk_tree(new, new_tree)

    for nid in new_tree:
        if nid not in old_tree:
            _add("用例与项目", "add",
                 f"新增「{_short(new_tree[nid]['name'], 18)}」", buckets)
    for nid in old_tree:
        if nid not in new_tree:
            _add("用例与项目", "del",
                 f"删除「{_short(old_tree[nid]['name'], 18)}」", buckets)
    for nid in old_tree:
        if nid not in new_tree:
            continue
        a, b = old_tree[nid], new_tree[nid]
        if a["name"] != b["name"]:
            _add("用例与项目", "mod",
                 f"「{_short(a['name'], 16)}」改名为「{_short(b['name'], 16)}」", buckets)
        elif a["parent_id"] != b["parent_id"]:
            _add("用例与项目", "mod",
                 f"「{_short(b['name'], 16)}」从「{_short(a['parent_name'], 12)}」"
                 f"移到「{_short(b['parent_name'], 12)}」", buckets)
    return {nid: v["name"] for nid, v in new_tree.items()}


def _diff_case_steps(old, new, case_names: Dict[str, str],
                     buckets: Dict[str, List[dict]]):
    """步骤列表：按用例比较「增了几步 / 删了几步」，再逐条列出参数变化。"""
    old_cases = (old or {}).get("case_steps") or {}
    new_cases = (new or {}).get("case_steps") or {}
    old_steps = {str(s["id"]): s for s in (old or {}).get("steps") or []
                 if isinstance(s, dict) and "id" in s}
    new_steps = {str(s["id"]): s for s in (new or {}).get("steps") or []
                 if isinstance(s, dict) and "id" in s}

    for cid, ids in new_cases.items():
        old_ids = old_cases.get(cid)
        name = _short(case_names.get(cid) or cid, 16)
        if old_ids is None:
            _add("步骤", "add", f"「{name}」新建了 {len(ids)} 个步骤", buckets)
            continue
        added = [i for i in ids if str(i) not in {str(x) for x in old_ids}]
        removed = [i for i in old_ids if str(i) not in {str(x) for x in ids}]
        if added:
            _add("步骤", "add", f"「{name}」新增 {len(added)} 个步骤", buckets)
        if removed:
            _add("步骤", "del", f"「{name}」删除 {len(removed)} 个步骤", buckets)

    for cid in old_cases:
        if cid not in new_cases:
            name = _short(case_names.get(cid) or cid, 16)
            _add("步骤", "del", f"「{name}」的步骤全部清空", buckets)

    # 参数变化：只比两边都存在的步骤
    for sid, s in new_steps.items():
        if sid not in old_steps:
            continue
        changes = _diff_entity_fields(old_steps[sid], s)
        if not changes:
            continue
        label = _short(s.get("name") or s.get("type"), 16)
        _add("步骤", "mod", f"步骤「{label}」{changes[0]}", buckets)


def diff_dirs(old_dir: str, new_dir: str) -> List[dict]:
    """比较两个目录（快照目录，或 data/ 本身），返回分组后的改动列表。

    返回： [{"group": "步骤", "items": [{"kind":"add","text":"…"}, …]}, …]
    kind: add（新增）/ del（删除）/ mod（修改）
    """
    buckets: Dict[str, List[dict]] = {}

    # ---- 项目树（顺便拿到 case_id → 用例名，给步骤比较用）----
    old_proj = _load(old_dir, "project_data.json")
    new_proj = _load(new_dir, "project_data.json")
    if old_proj is not None or new_proj is not None:
        case_names = _diff_projects(old_proj or [], new_proj or [], buckets)
    else:
        case_names = {}

    # ---- 步骤（含 case_steps 的增删）----
    old_steps = _load(old_dir, "steps_data.json")
    new_steps = _load(new_dir, "steps_data.json")
    if old_steps is not None or new_steps is not None:
        _diff_case_steps(old_steps, new_steps, case_names, buckets)

    # ---- 其余文件走通用实体比较 ----
    for filename, group in GROUP_NAMES.items():
        if filename in ("project_data.json", "steps_data.json"):
            continue
        old = _load(old_dir, filename)
        new = _load(new_dir, filename)
        if old is None and new is None:
            continue
        if filename == "config.json":
            # 设置项太多且经常无关紧要，只报「有几项变了」
            o = old or {}
            n = new or {}
            changed = [k for k in set(o) | set(n) if o.get(k) != n.get(k)]
            if changed:
                _add("设置", "mod", f"{len(changed)} 项设置发生变化", buckets)
            continue
        _diff_flat(_entities(old or {}), _entities(new or {}), group, buckets)

    # ---- 组装：空分组丢掉，超长折叠 ----
    out: List[dict] = []
    for group in ["用例与项目", "步骤", "元素库", "测试套件", "步骤模板",
                  "任务", "变量", "语音", "接口", "知识库", "设置"]:
        items = buckets.get(group)
        if not items:
            continue
        if len(items) > MAX_ITEMS:
            rest = len(items) - MAX_ITEMS
            items = items[:MAX_ITEMS] + [{"kind": "mod", "text": f"…还有 {rest} 处改动"}]
        out.append({"group": group, "items": items})
    return out


def summarize(diff: List[dict]) -> str:
    """一句话摘要，给时间线条目用：「改 3 处」。"""
    n = sum(len(g["items"]) for g in diff)
    return f"改 {n} 处" if n else "无改动"
