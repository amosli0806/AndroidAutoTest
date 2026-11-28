# services/asset_migrator.py
"""把存量「绝对路径」资源收拢进 data/assets/ 并改写为相对路径。

背景：数据驱动文件（`TreeNode.data_file`）、图像定位参考图（元素 `loc_value`、
步骤 `params.locationValue`）以前都存绝对路径，换机器 / 拷工程后全部失效。

**为什么不只看一次性标记**：历史回滚会把数据退回迁移前的快照（里面是旧绝对
路径），如果只凭 `assets_migrated_v1` 标记判断，回滚后就再也不会迁移了。
所以判据是「还有没有可迁移的绝对路径」——扫描只遍历四类字段，成本很低。

**为什么只复制不移动**：用户的原始表格可能还要用，删掉原文件是不可逆操作；
复制进 assets/ 后按内容哈希去重，不会产生重复副本。

**两阶段执行**（`plan_assets` → `apply_plan`）：
第一阶段只读模型 + 复制文件（IO 密集，可放后台线程）；第二阶段才改内存数据并
落盘（必须回主线程，否则 GUI 可能读到写了一半的模型）。中间用「改写计划」传递，
不跨线程持有模型引用。
"""
import os

from utils.app_paths import (
    ASSET_PREFIX_DATA,
    ASSET_PREFIX_IMAGE,
    import_asset,
    is_asset_rel,
    to_rel,
    get_data_dir,
)


def _legacy_basename_path(text: str) -> str:
    """「仅文件名」的旧引用 → element_images/ 下的真实路径；不是这种格式返回空串。

    旧版应用可视化存进元素库时只写了 basename（`elem_2026....png`），文件实际
    躺在 data/element_images/。这类引用也得收拢，否则旧目录永远删不掉。
    """
    name = str(text or "").strip()
    if not name or os.path.basename(name) != name:
        return ""
    if any(ch in name for ch in ("/", "\\", ":")):
        return ""
    legacy = os.path.join(get_data_dir(), "element_images", name)
    return legacy if os.path.isfile(legacy) else ""


def _plan_value(value, prefix: str, report: dict, owner: str, kind: str,
                allow_basename: bool = False):
    """只读判断 + 必要的文件复制。返回 (新值, 是否改动)，不改任何模型字段。"""
    text = str(value or "").strip()
    if not text or is_asset_rel(text):
        return value, False
    if not os.path.isabs(text):
        if not allow_basename:
            return value, False      # 数据驱动等字段：非路径文本不动
        legacy = _legacy_basename_path(text)
        if not legacy:
            return value, False
        rel = import_asset(legacy, prefix)
        if not rel or not is_asset_rel(rel):
            report["missing"].append(f"{owner}（{kind}）：旧引用收拢失败 {text}")
            return value, False
        report["copied"] += 1
        return rel, True
    if not os.path.isfile(text):
        report["missing"].append(f"{owner}（{kind}）：{text}")
        return value, False
    rel = to_rel(text)
    if not is_asset_rel(rel):
        rel = import_asset(text, prefix)
    if not rel or not is_asset_rel(rel):
        report["missing"].append(f"{owner}（{kind}）：复制失败 {text}")
        return value, False
    report["copied"] += 1
    return rel, True


def plan_assets(project_model=None, step_model=None,
                element_model=None, template_model=None):
    """第一阶段：扫描 + 复制文件，返回 (改写计划, 统计)。

    可在后台线程执行：只读模型字段、只往 assets/ 写文件，不修改任何模型。
    """
    plan = {"project": [], "step": [], "element": [], "template": []}
    report = {"copied": 0, "missing": []}

    # ① 项目树：数据驱动文件
    if project_model is not None:
        try:
            for node in project_model.get_all_cases():
                raw = getattr(node, "data_file", "")
                if not raw:
                    continue
                new, ok = _plan_value(raw, ASSET_PREFIX_DATA, report,
                                      f"用例「{getattr(node, 'name', '')}」", "数据驱动")
                if ok:
                    plan["project"].append((node.id, new))
        except Exception as e:
            report["missing"].append(f"项目树扫描失败：{type(e).__name__}: {e}")

    # ② 步骤：图像定位参数
    if step_model is not None:
        try:
            for step_id, step in list(getattr(step_model, "_steps", {}).items()):
                params = getattr(step, "params", None) or {}
                if params.get("locationType") != "图像":
                    continue
                raw = params.get("locationValue")
                if not raw:
                    continue
                new, ok = _plan_value(raw, ASSET_PREFIX_IMAGE, report,
                                      f"步骤「{getattr(step, 'name', '')}」", "参考图",
                                      allow_basename=True)
                if ok:
                    plan["step"].append((step_id, new))
        except Exception as e:
            report["missing"].append(f"步骤扫描失败：{type(e).__name__}: {e}")

    # ③ 元素库：图像定位值
    if element_model is not None:
        try:
            for elem in getattr(element_model, "elements", []):
                if getattr(elem, "loc_type", "") != "图像":
                    continue
                new, ok = _plan_value(getattr(elem, "loc_value", ""), ASSET_PREFIX_IMAGE,
                                      report, f"元素「{getattr(elem, 'name', '')}」", "参考图",
                                      allow_basename=True)
                if ok:
                    plan["element"].append((elem.id, new))
        except Exception as e:
            report["missing"].append(f"元素库扫描失败：{type(e).__name__}: {e}")

    # ④ 步骤模板：独立快照，最容易漏
    if template_model is not None:
        try:
            for tpl in getattr(template_model, "_templates", []):
                tpl_name = tpl.get("name", "")
                for idx, snap in enumerate(tpl.get("steps", []) or []):
                    params = snap.get("params") or {}
                    if params.get("locationType") != "图像":
                        continue
                    raw = params.get("locationValue")
                    if not raw:
                        continue
                    new, ok = _plan_value(raw, ASSET_PREFIX_IMAGE, report,
                                          f"模板「{tpl_name}」第 {idx + 1} 步", "参考图",
                                          allow_basename=True)
                    if ok:
                        plan["template"].append((tpl_name, idx, new))
        except Exception as e:
            report["missing"].append(f"模板扫描失败：{type(e).__name__}: {e}")

    return plan, report


def apply_plan(plan, project_model=None, step_model=None,
               element_model=None, template_model=None) -> int:
    """第二阶段：把计划写回模型并落盘。**必须在主线程执行**。

    按 id 重新定位对象而不是持有引用 —— 计划在后台线程生成，期间模型若被
    重新加载过（例如用户回滚了历史），旧引用就失效了。

    返回实际落盘（save 过）的模型数。
    """
    if not plan:
        return 0
    changed = 0

    if project_model is not None and plan.get("project"):
        try:
            index = {n.id: n for n in project_model.get_all_cases()}
            hit = False
            for node_id, new in plan["project"]:
                node = index.get(node_id)
                if node is not None and getattr(node, "data_file", "") != new:
                    node.data_file = new
                    hit = True
            if hit:
                project_model.save()
                changed += 1
        except Exception as e:
            print(f"[asset_migrator] 项目树改写失败: {type(e).__name__}: {e}")

    if step_model is not None and plan.get("step"):
        try:
            steps = getattr(step_model, "_steps", {})
            hit = False
            for step_id, new in plan["step"]:
                step = steps.get(step_id)
                if step is None:
                    continue
                params = dict(getattr(step, "params", None) or {})
                if params.get("locationValue") != new:
                    params["locationValue"] = new
                    step.params = params
                    hit = True
            if hit:
                step_model.save()
                changed += 1
        except Exception as e:
            print(f"[asset_migrator] 步骤改写失败: {type(e).__name__}: {e}")

    if element_model is not None and plan.get("element"):
        try:
            index = {e.id: e for e in getattr(element_model, "elements", [])}
            hit = False
            for elem_id, new in plan["element"]:
                elem = index.get(elem_id)
                if elem is not None and getattr(elem, "loc_value", "") != new:
                    elem.loc_value = new
                    hit = True
            if hit:
                element_model.save()
                changed += 1
        except Exception as e:
            print(f"[asset_migrator] 元素库改写失败: {type(e).__name__}: {e}")

    if template_model is not None and plan.get("template"):
        try:
            by_name = {t.get("name"): t for t in getattr(template_model, "_templates", [])}
            hit = False
            for tpl_name, idx, new in plan["template"]:
                tpl = by_name.get(tpl_name)
                if not tpl:
                    continue
                snaps = tpl.get("steps", []) or []
                if idx >= len(snaps):
                    continue
                params = snaps[idx].get("params") or {}
                if params.get("locationValue") != new:
                    params["locationValue"] = new
                    snaps[idx]["params"] = params
                    hit = True
            if hit:
                template_model.save()
                changed += 1
        except Exception as e:
            print(f"[asset_migrator] 模板改写失败: {type(e).__name__}: {e}")

    return changed


def migrate_assets(project_model=None, step_model=None,
                   element_model=None, template_model=None) -> dict:
    """同步版：plan + apply 一次做完（供自测与命令行使用）。"""
    plan, report = plan_assets(project_model, step_model, element_model, template_model)
    changed = apply_plan(plan, project_model, step_model, element_model, template_model)
    return {"migrated": report.get("copied", 0),
            "missing": report.get("missing", []),
            "models_changed": changed}


def has_absolute_assets(project_model=None, step_model=None,
                        element_model=None, template_model=None) -> bool:
    """是否还有需要迁移的绝对路径（只读检查，供启动时决定要不要跑迁移）。"""
    def _is_abs(val) -> bool:
        text = str(val or "").strip()
        if not text or is_asset_rel(text):
            return False
        if os.path.isabs(text):
            return True
        # 仅文件名的旧引用：落在 element_images/ 里的也算待收拢
        return bool(_legacy_basename_path(text))

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
