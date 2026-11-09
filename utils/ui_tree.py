# utils/ui_tree.py
"""uiautomator dump 出来的 UI 层级树的解析与「按坐标命中控件」判定。

同一份规则被两处共用：
    录制反查 —— controllers/step_controller.py，点完设备后把点击点还原成控件
    元素拾取 —— views/dialogs/element_pick_dialog.py，在截图面板上点控件取定位

规则（与录制端历史行为保持一致，改这里两边一起变）：
    1. 收集所有 bounds 覆盖该坐标的节点，祖先与后代都算
    2. 排序档位：可点击 > 有文案(text/desc) > 仅资源 ID，同档取面积最小
       —— 用户点的多半是可点击控件；只按「面积最小」会把分隔线、图标选出来
    3. 命中带 resource-id 时，统计**同一窗口内**同 ID 的节点数与序号，
       解决「列表项共用一个 ID」时回放点错行的问题
    4. 全都没有属性时（自绘界面：游戏 / Flutter），仍按档位兜底返回一个节点，
       由调用方决定是否退化成坐标
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import List, Optional, Tuple

BOUNDS_RE = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")

# 文本定位值的长度上限：超长文案当定位值既难维护又容易撞，
# 录制端一直用 60 字，拾取器保持一致。
TEXT_LOCATOR_MAX = 60

Candidate = Tuple[int, dict, bool, object]   # (area, info, has_attr, window_root)


def parse_bounds(text) -> Optional[Tuple[int, int, int, int]]:
    """把 `[l,t][r,b]` 解析成 (left, top, right, bottom)，失败返回 None。"""
    if not text:
        return None
    m = BOUNDS_RE.search(text)
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)))


def parse_xml(xml_str) -> Optional[ET.Element]:
    """解析 dump 出来的 XML；空串或坏 XML 返回 None（调用方按「dump 失败」处理）。"""
    if not xml_str:
        return None
    try:
        return ET.fromstring(xml_str)
    except Exception:
        return None


def collect_candidates(node, x: int, y: int,
                       out: Optional[list] = None, window=None) -> List[Candidate]:
    """递归收集所有 bounds 覆盖 (x, y) 的节点。

    `<hierarchy>` 的直接子节点是各个窗口，逐个带上「所属窗口」身份再往下走 ——
    计算同类元素序号时要限定在同一窗口内（不同包可能复用同一个 resource-id）。
    """
    if out is None:
        out = []

    if window is None and node.tag == "hierarchy":
        for child in node:
            collect_candidates(child, x, y, out, window=child)
        return out
    if window is None:
        window = node

    bounds = parse_bounds(node.get("bounds"))
    if bounds:
        left, top, right, bottom = bounds
        if left <= x <= right and top <= y <= bottom:
            area = max(0, right - left) * max(0, bottom - top)
            rid = (node.get("resource-id") or "").strip()
            text = (node.get("text") or "").strip()
            desc = (node.get("content-desc") or "").strip()
            info = {
                "resourceId": rid,
                "text": text,
                "description": desc,
                "className": node.get("class"),
                "package": node.get("package") or "",
                "clickable": node.get("clickable") == "true",
                "enabled": node.get("enabled") != "false",   # 缺省视为可用
                "bounds": bounds,
            }
            out.append((area, info, bool(rid or text or desc), window))

    for child in node:
        collect_candidates(child, x, y, out, window=window)
    return out


def _sort_key(c: Candidate):
    """(档位, 面积)：0 = 可点击，1 = 有文案，2 = 仅资源 ID / 无文案。"""
    area, info, _, _ = c
    if info.get("clickable"):
        tier = 0
    elif info.get("text") or info.get("description"):
        tier = 1
    else:
        tier = 2
    return (tier, area)


def compute_instance(window_root, target_rid: str,
                     target_bounds) -> Tuple[int, int]:
    """统计同一窗口内 resource-id == target_rid 的节点数，并算出目标节点的序号。

    序号按 (top, left) 排序，从 0 起；target_bounds 为空时序号给 0。
    """
    if not target_rid:
        return 1, 0

    siblings = []
    for node in window_root.iter():
        rid = (node.get("resource-id") or "").strip()
        if rid != target_rid:
            continue
        b = parse_bounds(node.get("bounds"))
        if b:
            siblings.append((b[1], b[0], b))       # (top, left, bounds)

    count = len(siblings)
    if count == 0 or target_bounds is None:
        return count, 0

    siblings.sort(key=lambda item: (item[0], item[1]))
    for idx, (_, _, b) in enumerate(siblings):
        if b == tuple(target_bounds):
            return count, idx
    return count, 0


def choose_best(candidates: List[Candidate]) -> Optional[dict]:
    """从候选里挑一个，并补上 resourceIdCount / instance。"""
    if not candidates:
        return None

    # 优先：有属性（资源 ID / 文案 / 描述）的节点
    with_attr = [c for c in candidates if c[2]]
    pool = with_attr if with_attr else candidates
    pool.sort(key=_sort_key)

    _, info, _, window_root = pool[0]
    info = dict(info)                              # 别污染候选里的原字典
    if info.get("resourceId"):
        count, instance = compute_instance(
            window_root, info["resourceId"], info.get("bounds"))
        info["resourceIdCount"] = count
        info["instance"] = instance
    else:
        info["resourceIdCount"] = 1
        info["instance"] = 0
    return info


def find_node_at(root, x: int, y: int) -> Optional[dict]:
    """按坐标命中控件的对外入口。root 是 `<hierarchy>` 元素。"""
    if root is None:
        return None
    try:
        return choose_best(collect_candidates(root, int(x), int(y)))
    except Exception:
        return None


def locator_for(info: dict, x: Optional[int] = None,
                y: Optional[int] = None) -> Tuple[str, str]:
    """由命中节点生成「定位方式 + 定位值」，优先级：资源 ID > 文本 > 描述 > 坐标。

    返回的定位方式与卡片里的下拉选项完全一致（资源ID / 文本 / 描述 / 坐标）。
    """
    rid = (info.get("resourceId") or "").strip()
    if rid:
        return "资源ID", rid

    text = (info.get("text") or "").strip()
    if text and len(text) <= TEXT_LOCATOR_MAX:
        return "文本", text

    desc = (info.get("description") or "").strip()
    if desc:
        return "描述", desc

    if x is None or y is None:
        b = info.get("bounds")
        if b:
            x = (b[0] + b[2]) // 2
            y = (b[1] + b[3]) // 2
    if x is None or y is None:
        return "坐标", ""
    return "坐标", f"{int(x)},{int(y)}"


def describe_node(info: Optional[dict]) -> str:
    """一句话说明命中的是哪个控件，给拾取面板的预览条 / 日志用。"""
    if not info:
        return "未命中控件"

    b = info.get("bounds")
    size = f"{b[2] - b[0]}×{b[3] - b[1]}" if b else "?"
    parts = []
    rid = (info.get("resourceId") or "").strip()
    if rid:
        # 完整 ID 太长，预览条只留最后一段，完整的在定位值里
        parts.append(f"id={rid.rsplit('/', 1)[-1]}")
    text = (info.get("text") or "").strip()
    if text:
        parts.append(f"文本「{text[:16]}{'…' if len(text) > 16 else ''}」")
    desc = (info.get("description") or "").strip()
    if desc:
        parts.append(f"描述「{desc[:12]}{'…' if len(desc) > 12 else ''}」")
    cls = (info.get("className") or "").rsplit(".", 1)[-1]
    if cls:
        parts.append(cls)
    parts.append(f"{size}px")
    if not info.get("clickable"):
        parts.append("不可点击")
    if not info.get("enabled", True):
        parts.append("已禁用")

    dup = int(info.get("resourceIdCount") or 1)
    if dup > 1:
        parts.append(f"同 ID 共 {dup} 个（第 {int(info.get('instance') or 0) + 1} 个）")

    return " · ".join(parts) if parts else "命中控件"
