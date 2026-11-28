# services/asset_migrator.py
"""把存量「绝对路径」资源收拢进 data/assets/ 并改写为相对路径。

背景：数据驱动文件（`TreeNode.data_file`）、图像定位参考图（元素 `loc_value`、
步骤 `params.locationValue`）以前都存绝对路径，换机器 / 拷工程后全部失效。

**为什么不只看一次性标记**：历史回滚会把数据退回迁移前的快照（里面是旧绝对
路径），如果只凭 `assets_migrated_v1` 标记判断，回滚后就再也不会迁移了。
所以判据是「还有没有可迁移的绝对路径」——扫描只遍历四类字段，成本很低。

**为什么只复制不移动**：用户的原始表格可能还要用，删掉原文件是不可逆操作；
复制进 assets/ 后按内容哈希去重，不会产生重复副本。
"""
import os

from utils.app_paths import (
    ASSET_PREFIX_DATA,
    ASSET_PREFIX_IMAGE,
    import_asset,
    is_asset_rel,
    to_rel,
)


def _fix_value(value, prefix: str, report: dict, owner: str, kind: str):
    """把一个路径值规范化。返回 (新值, 是否改动)。"""
    text = str(value or "").strip()
    if not text or is_asset_rel(text):
        return value, False
    if not os.path.isabs(text):
        return value, False          # 仅文件名 / 普通文本，不动
    if not os.path.isfile(text):
        report["missing"].append(f"{owner}（{kind}）：{text}")
        return value, False
    rel = to_rel(text)
    if not is_asset_rel(rel):
        rel = import_asset(text, prefix)
    if not rel or not is_asset_rel(rel):
        report["missing"].append(f"{owner}（{kind}）：复制失败 {text}")
        return value, False
    report["migrated"] += 1
    return rel, True


def migrate_assets(project_model=None, step_model=None,
                   element_model=None, template_model=None) -> dict:
    """执行一次迁移，返回 {"migrated": n, "missing": [...]}。

    单个模型出错不影响其它模型（各自 try 包住），迁移本身失败也不阻塞启动。
    """
    report = {"migrated": 0, "missing": []}

    # ① 项目树：数据驱动文件
    if project_model is not None:
        try:
            changed = False
            for node in project_model.get_all_cases():
                raw = getattr(node, "data_file", "")
                if not raw:
                    continue
                new, ok = _fix_value(raw, ASSET_PREFIX_DATA, report,
                                     f"用例「{getattr(node, 'name', '')}」", "数据驱动")
                if ok:
                    node.data_file = new
                    changed = True
            if changed:
                project_model.save()
        except Exception as e:
            report["missing"].append(f"项目树迁移失败：{type(e).__name__}: {e}")

    # ② 步骤：图像定位参数
    if step_model is not None:
        try:
            changed = False
            for step in getattr(step_model, "_steps", {}).values():
                params = getattr(step, "params", None) or {}
                if params.get("locationType") != "图像":
                    continue
                raw = params.get("locationValue")
                if not raw:
                    continue
                new, ok = _fix_value(raw, ASSET_PREFIX_IMAGE, report,
                                     f"步骤「{getattr(step, 'name', '')}」", "参考图")
                if ok:
                    fixed = dict(params)
                    fixed["locationValue"] = new
                    step.params = fixed
                    changed = True
            if changed:
                step_model.save()
        except Exception as e:
            report["missing"].append(f"步骤迁移失败：{type(e).__name__}: {e}")

    # ③ 元素库：图像定位值
    if element_model is not None:
        try:
            changed = False
            for elem in getattr(element_model, "elements", []):
                if getattr(elem, "loc_type", "") != "图像":
                    continue
                raw = getattr(elem, "loc_value", "")
                new, ok = _fix_value(raw, ASSET_PREFIX_IMAGE, report,
                                     f"元素「{getattr(elem, 'name', '')}」", "参考图")
                if ok:
                    elem.loc_value = new
                    changed = True
            if changed:
                element_model.save()
        except Exception as e:
            report["missing"].append(f"元素库迁移失败：{type(e).__name__}: {e}")

    # ④ 步骤模板：独立快照，最容易漏
    if template_model is not None:
        try:
            changed = False
            for tpl in getattr(template_model, "_templates", []):
                for snap in tpl.get("steps", []) or []:
                    params = snap.get("params") or {}
                    if params.get("locationType") != "图像":
                        continue
                    raw = params.get("locationValue")
                    if not raw:
                        continue
                    new, ok = _fix_value(raw, ASSET_PREFIX_IMAGE, report,
                                         f"模板「{tpl.get('name', '')}」", "参考图")
                    if ok:
                        params["locationValue"] = new
                        snap["params"] = params
                        changed = True
            if changed:
                template_model.save()
        except Exception as e:
            report["missing"].append(f"模板迁移失败：{type(e).__name__}: {e}")

    return report

def has_absolute_assets(project_model=None, step_model=None,
                        element_model=None, template_model=None) -> bool:
    """是否还有需要迁移的绝对路径（只读检查，供启动时决定要不要跑迁移）。"""
    def _is_abs(val) -> bool:
        text = str(val or "").strip()
        return bool(text) and os.path.isabs(text) and not is_asset_rel(text)

    try:
        if project_model is not None:
            for node in project_model.get_all_cases():
                if _is_abs(getattr(node, "data_file", "")):
                    return True
        if step_model is not None:
            for step in getattr(step_model, "_steps", {}).values():
                params = getattr(step, "params", None) or {}
                if params.get("locationType") == "图像" and _is_abs(params.get("locationValue")):
                    return True
        if element_model is not None:
            for elem in getattr(element_model, "elements", []):
                if getattr(elem, "loc_type", "") == "图像" and _is_abs(getattr(elem, "loc_value", "")):
                    return True
        if template_model is not None:
            for tpl in getattr(template_model, "_templates", []):
                for snap in tpl.get("steps", []) or []:
                    params = snap.get("params") or {}
                    if params.get("locationType") == "图像" and _is_abs(params.get("locationValue")):
                        return True
    except Exception:
        return False
    return False
