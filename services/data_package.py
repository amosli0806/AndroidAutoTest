# services/data_package.py
"""数据包导出 / 导入（整包备份与迁移）。

一个 zip 装走全部用户资产：11 个资产 json（config 走白名单）、`assets/`、
`knowledge_assets/`，可选最近若干份历史快照。

**刻意排除**：`perf_data.json`（性能采样，动辄几 MB 且是"跑出来的数据"）、
`perf_baselines.json`、`edge_voices_cache.json`（TTS 缓存）、`gui.info.json` /
`gui.lock`（本机单实例状态）、`mcp_*.log`（日志）。排除名单与历史快照共用一套。

**为什么要 manifest**：导入前要给用户看「包里有什么、多大的包、什么版本」，还要能
逐文件校验完整性。没有 manifest 就只能解压后猜。

**为什么先写 .part 再改名**：打包中途失败不能留下半个 zip 让用户以为有备份。
"""
import hashlib
import json
import os
import shutil
import time
import zipfile

from utils.app_paths import data_path, get_data_dir

# manifest 格式版本。将来结构变化时自增，老版本虫师遇到更高的 format 直接拒绝，
# 避免"半懂不懂地导"。
FORMAT_VERSION = 1

# 必带资产（config.json 单独按白名单处理）
ASSET_JSONS = (
    "project_data.json",
    "steps_data.json",
    "elements_data.json",
    "suites_data.json",
    "tasks_data.json",
    "vars_data.json",
    "voice_data.json",
    "api_data.json",
    "knowledge_data.json",
    "step_templates.json",
)

# 必带目录（相对 data/）
ASSET_DIRS = ("assets", "knowledge_assets")

# config 白名单：只带跨机器有意义的偏好。
# 不带：output_dir / wallpaper_path（本机路径）、shortcuts（键位是肌肉记忆）、
# tree_expanded（本机视图状态）、ai_api_key（敏感）。
CONFIG_WHITELIST = (
    "theme_mode",
    "sidebar_show_labels",
    "step_interval",
    "wallpaper_opacity",
    "visible_action_cards",
    "notify",
    "perf_config",
    "capture_config",
    "monkey_config",
    "monkey_package",
    "monkey_start_mode",
    "target_package",
    "checked_commands",
    "custom_commands",
    "display_command_ids",
    "ai_enabled",
    "ai_model",
    "ai_base_url",
)

HISTORY_DIR = ".history"
HISTORY_KEEP = 10          # 勾选「带历史」时只带最近 N 份

MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024    # 解压总体积上限（zip 炸弹防护）
MAX_SINGLE_BYTES = 200 * 1024 * 1024        # 单文件上限


# ---------------------------------------------------------------------------
# 统计与清单
# ---------------------------------------------------------------------------
def _read_json(name: str, default):
    path = data_path(name)
    if not os.path.isfile(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _count_cases(nodes) -> int:
    """递归数用例节点（project_data.json 是树）。"""
    total = 0
    for node in nodes or []:
        if isinstance(node, dict):
            if node.get("type") == "case":
                total += 1
            total += _count_cases(node.get("children") or [])
    return total


def collect_counts() -> dict:
    """按当前 data/ 统计各类资产数量（导出时写进 manifest，导入前展示）。"""
    # project_data.json 顶层是 list（节点树）；steps 的 steps 字段可能是 list 或 dict
    project = _read_json("project_data.json", [])
    steps = _read_json("steps_data.json", {})
    elements = _read_json("elements_data.json", {})
    voice = _read_json("voice_data.json", {})
    api = _read_json("api_data.json", {})
    suites = _read_json("suites_data.json", [])
    knowledge = _read_json("knowledge_data.json", {})
    templates = _read_json("step_templates.json", {})

    asset_files = 0
    asset_bytes = 0
    for rel_dir in ASSET_DIRS:
        root = os.path.join(get_data_dir(), rel_dir)
        for base, _dirs, files in os.walk(root):
            for name in files:
                asset_files += 1
                try:
                    asset_bytes += os.path.getsize(os.path.join(base, name))
                except OSError:
                    pass

    if isinstance(project, list):
        project_nodes = project
    elif isinstance(project, dict):
        project_nodes = project.get("nodes") or project.get("projects") or []
    else:
        project_nodes = []

    raw_steps = steps.get("steps") if isinstance(steps, dict) else None
    steps_count = len(raw_steps) if isinstance(raw_steps, (list, dict)) else 0

    return {
        "cases": _count_cases(project_nodes),
        "steps": steps_count,
        "elements": len(elements.get("elements") or []),
        "voice_cases": len(voice.get("cases") or []),
        "api_cases": len(api.get("cases") or []),
        "suites": len(suites if isinstance(suites, list) else suites.get("suites") or []),
        "knowledge_notes": len(knowledge.get("notes") or []),
        "templates": len(templates.get("templates") or []),
        "asset_files": asset_files,
        "asset_bytes": asset_bytes,
    }


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_zip_entry(zf: zipfile.ZipFile, arcname: str, src_path: str, files_meta: list,
                     progress=None, done=[0], total=[1]):
    """写一个文件进 zip 并记录 sha256。"""
    zf.write(src_path, arcname)
    files_meta.append({
        "path": arcname,
        "size": os.path.getsize(src_path),
        "sha256": _sha256(src_path),
    })
    done[0] += 1
    if progress:
        progress(done[0], total[0], arcname)


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------
def export_package(dest_path: str, include_history: bool = False, progress=None) -> dict:
    """打包到 dest_path。返回 {"path", "size", "counts", "includes_history"}。

    progress(done, total, current_name) 可选，用于界面进度。
    失败会清掉 .part，不留半个包。
    """
    dest_path = os.path.abspath(dest_path)
    part_path = dest_path + ".part"
    data_dir = get_data_dir()

    # 先算清单：要打包哪些文件
    plan = []           # (arcname, abs_path)
    for name in ASSET_JSONS:
        src = os.path.join(data_dir, name)
        if os.path.isfile(src):
            plan.append((name, src))
    for rel_dir in ASSET_DIRS:
        root = os.path.join(data_dir, rel_dir)
        if not os.path.isdir(root):
            continue
        for base, _dirs, files in os.walk(root):
            for fn in files:
                src = os.path.join(base, fn)
                arc = os.path.relpath(src, data_dir).replace(os.sep, "/")
                plan.append((arc, src))

    hist_entries = []
    if include_history:
        hist_root = os.path.join(data_dir, HISTORY_DIR)
        if os.path.isdir(hist_root):
            snaps = sorted((d for d in os.listdir(hist_root)
                            if os.path.isdir(os.path.join(hist_root, d))), reverse=True)
            for snap in snaps[:HISTORY_KEEP]:
                base_dir = os.path.join(hist_root, snap)
                for fn in os.listdir(base_dir):
                    src = os.path.join(base_dir, fn)
                    if os.path.isfile(src):
                        hist_entries.append((f"{HISTORY_DIR}/{snap}/{fn}", src))

    counts = collect_counts()
    total = len(plan) + len(hist_entries) + 1     # +1 = config.preferences.json
    done = [0]
    files_meta = []

    try:
        with zipfile.ZipFile(part_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for arc, src in plan:
                _write_zip_entry(zf, arc, src, files_meta, progress, done, [total])

            # config 白名单
            cfg = _read_json("config.json", {})
            prefs = {k: cfg[k] for k in CONFIG_WHITELIST if k in cfg}
            prefs_tmp = os.path.join(get_data_dir(), "_config.preferences.tmp")
            with open(prefs_tmp, "w", encoding="utf-8") as f:
                json.dump(prefs, f, ensure_ascii=False, indent=2)
            try:
                _write_zip_entry(zf, "config.preferences.json", prefs_tmp,
                                 files_meta, progress, done, [total])
            finally:
                try:
                    os.remove(prefs_tmp)
                except OSError:
                    pass

            for arc, src in hist_entries:
                _write_zip_entry(zf, arc, src, files_meta, progress, done, [total])

            manifest = {
                "format": FORMAT_VERSION,
                "app_version": _app_version(),
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "includes_history": bool(include_history),
                "history_count": len({e[0].split("/")[1] for e in hist_entries}),
                "counts": counts,
                "files": files_meta,
            }
            zf.writestr("manifest.json",
                        json.dumps(manifest, ensure_ascii=False, indent=2))

        os.replace(part_path, dest_path)
    except Exception:
        try:
            os.remove(part_path)
        except OSError:
            pass
        raise

    return {
        "path": dest_path,
        "size": os.path.getsize(dest_path),
        "counts": counts,
        "includes_history": bool(include_history),
    }


def _app_version() -> str:
    try:
        from utils.version import APP_VERSION
        return str(APP_VERSION)
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# 读包（导入预览）
# ---------------------------------------------------------------------------
def _safe_member(name: str) -> bool:
    """zip 成员名是否安全：拒绝绝对路径、上跳、盘符、反斜杠穿越。"""
    text = name.replace("\\", "/")
    if text.startswith("/") or ":" in text.split("/")[0]:
        return False
    parts = text.split("/")
    return ".." not in parts


def inspect_package(path: str) -> dict:
    """只读 manifest 返回预览信息；包不合法抛 ValueError（中文原因）。

    不解压任何业务文件，所以很快。
    """
    if not os.path.isfile(path):
        raise ValueError("文件不存在")
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            if "manifest.json" not in names:
                raise ValueError("这不是虫师数据包（缺少 manifest.json）")
            # 路径安全检查（含符号链接）
            for info in zf.infolist():
                if not _safe_member(info.filename):
                    raise ValueError(f"数据包结构异常：包含非法路径 {info.filename}")
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError(f"数据包结构异常：包含符号链接 {info.filename}")
            total = sum(i.file_size for i in zf.infolist())
            if total > MAX_TOTAL_BYTES:
                raise ValueError(
                    f"数据包解压后有 {total / 1024 / 1024:.0f} MB，超过上限 "
                    f"{MAX_TOTAL_BYTES // 1024 // 1024} MB")
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
    except zipfile.BadZipFile:
        raise ValueError("文件不是有效的 zip")
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"无法读取数据包：{type(e).__name__}: {e}")

    fmt = int(manifest.get("format") or 0)
    if fmt > FORMAT_VERSION:
        raise ValueError(
            f"该数据包格式版本 {fmt} 高于当前程序支持的 {FORMAT_VERSION}，请先升级虫师")

    return {
        "format": fmt,
        "app_version": manifest.get("app_version", ""),
        "created_at": manifest.get("created_at", ""),
        "includes_history": bool(manifest.get("includes_history")),
        "history_count": int(manifest.get("history_count") or 0),
        "counts": manifest.get("counts") or {},
        "files": manifest.get("files") or [],
        "zip_size": os.path.getsize(path),
        "unpacked_size": total,
        "path": path,
    }


def check_version_compatible(info: dict) -> None:
    """包版本高于当前程序时拒绝（避免用新格式数据把旧程序打乱）。"""
    pkg_ver = str(info.get("app_version") or "")
    cur_ver = _app_version()
    if not pkg_ver or pkg_ver == cur_ver or cur_ver == "unknown":
        return
    try:
        p = tuple(int(x) for x in pkg_ver.split("."))
        c = tuple(int(x) for x in cur_ver.split("."))
    except ValueError:
        return
    if p > c:
        raise ValueError(
            f"该数据包由虫师 {pkg_ver} 创建，当前版本 {cur_ver} 无法导入，请先升级")


# ---------------------------------------------------------------------------
# 导入
# ---------------------------------------------------------------------------
def _free_bytes(path: str) -> int:
    try:
        return shutil.disk_usage(os.path.dirname(os.path.abspath(path)) or ".").free
    except Exception:
        return 0


def import_package(path: str, progress=None,
                   snapshot_cb=None, backup_cb=None) -> dict:
    """导入数据包（整包覆盖）。

    - `snapshot_cb(label)`：导入前存历史快照（由调用方注入 HistoryModel，避免反向依赖）
    - `backup_cb()`：可选的「先导出当前数据包」动作，返回备份路径或空串
    - `progress(step, done, total, name)`：步骤名 + 进度

    返回 {"counts", "restored", "backup_path", "assets_added"}。
    任何一步失败都会清理临时目录并通过 snapshot_cb 提示回滚。
    """
    info = inspect_package(path)
    check_version_compatible(info)

    # 空间检查：解压体积 × 1.5（还要留出替换时的临时占用）
    need = int(info["unpacked_size"] * 1.5)
    free = _free_bytes(get_data_dir())
    if free and free < need:
        raise ValueError(
            f"磁盘空间不足：需要约 {need / 1024 / 1024:.1f} GB，"
            f"当前可用 {free / 1024 / 1024:.1f} GB")

    backup_path = ""
    if backup_cb:
        try:
            backup_path = backup_cb() or ""
        except Exception as e:
            print(f"[data_package] 备份当前数据失败（继续导入）: {e}")

    if snapshot_cb:
        try:
            snapshot_cb("导入数据包前")
        except Exception as e:
            print(f"[data_package] 导入前快照失败（继续导入）: {e}")

    data_dir = get_data_dir()
    tmp_dir = os.path.join(data_dir, "_import_tmp")
    if os.path.isdir(tmp_dir):
        shutil.rmtree(tmp_dir, ignore_errors=True)
    os.makedirs(tmp_dir, exist_ok=True)

    try:
        # ① 解压到临时目录
        with zipfile.ZipFile(path) as zf:
            members = [i for i in zf.infolist() if not i.is_dir()]
            for idx, member in enumerate(members, 1):
                if not _safe_member(member.filename):
                    raise ValueError(f"数据包结构异常：{member.filename}")
                if member.file_size > MAX_SINGLE_BYTES:
                    raise ValueError(
                        f"数据包内单文件过大：{member.filename} "
                        f"({member.file_size / 1024 / 1024:.0f} MB)")
                target = os.path.join(tmp_dir, member.filename.replace("/", os.sep))
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with zf.open(member) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                if progress:
                    progress("解压", idx, len(members), member.filename)

        # ② 逐文件校验 sha256
        manifest_path = os.path.join(tmp_dir, "manifest.json")
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        for idx, entry in enumerate(manifest.get("files") or [], 1):
            rel = str(entry.get("path") or "")
            target = os.path.join(tmp_dir, rel.replace("/", os.sep))
            if not os.path.isfile(target):
                raise ValueError(f"数据包缺少文件：{rel}")
            if entry.get("sha256") and _sha256(target) != entry["sha256"]:
                raise ValueError(f"数据包已损坏（校验失败）：{rel}")
            if progress:
                progress("校验", idx, len(manifest["files"]), rel)

        # ③ 替换资产 json（config 单独合并）
        restored = 0
        for name in ASSET_JSONS:
            src = os.path.join(tmp_dir, name)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(data_dir, name))
                restored += 1

        prefs_src = os.path.join(tmp_dir, "config.preferences.json")
        if os.path.isfile(prefs_src):
            with open(prefs_src, "r", encoding="utf-8") as f:
                prefs = json.load(f)
            cfg_path = os.path.join(data_dir, "config.json")
            cfg = {}
            if os.path.isfile(cfg_path):
                try:
                    with open(cfg_path, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                except Exception:
                    cfg = {}
            # 只覆盖白名单键：本机路径、快捷键、api_key 保持不动
            for k, v in prefs.items():
                if k in CONFIG_WHITELIST:
                    cfg[k] = v
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)

        # ④ 合并资源目录（只增不删：旧文件可能仍被本机其它用例引用）
        assets_added = 0
        for rel_dir in ASSET_DIRS:
            src_root = os.path.join(tmp_dir, rel_dir)
            if not os.path.isdir(src_root):
                continue
            for base, _dirs, files in os.walk(src_root):
                for fn in files:
                    src = os.path.join(base, fn)
                    rel = os.path.relpath(src, tmp_dir)
                    dst = os.path.join(data_dir, rel)
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(src, dst)
                    assets_added += 1

        # ⑤ 历史快照：同名不覆盖（本机已有的优先）
        hist_src = os.path.join(tmp_dir, HISTORY_DIR)
        if os.path.isdir(hist_src):
            hist_dst = os.path.join(data_dir, HISTORY_DIR)
            for snap in os.listdir(hist_src):
                s_dir = os.path.join(hist_src, snap)
                if not os.path.isdir(s_dir):
                    continue
                d_dir = os.path.join(hist_dst, snap)
                if os.path.isdir(d_dir):
                    continue
                shutil.copytree(s_dir, d_dir)

        if progress:
            progress("完成", 1, 1, "")

        return {
            "counts": manifest.get("counts") or {},
            "restored": restored,
            "assets_added": assets_added,
            "backup_path": backup_path,
            "app_version": manifest.get("app_version", ""),
        }
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
