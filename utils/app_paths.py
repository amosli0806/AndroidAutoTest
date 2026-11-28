# utils/app_paths.py
"""统一管理运行期数据文件（config.json 与各类 *_data.json）的存放路径。

统一放在程序目录下的 data/ 子目录：
  - 开发环境：项目根目录/data/
  - 打包环境：可执行文件同级目录/data/

这样程序目录保持整洁，数据集中存放、便于备份迁移，也不容易被误删。
注意：这里只负责「路径」，不含任何业务逻辑，可以被 models / utils 自由引用。
"""
import os
import shutil
import subprocess
import sys

# 数据目录名
DATA_DIR_NAME = "data"

# 早期版本散落在程序目录根下的数据文件，首次启动会被搬进 data/
LEGACY_DATA_FILES = (
    "config.json",
    "project_data.json",
    "steps_data.json",
    "elements_data.json",
    "suites_data.json",
    "tasks_data.json",
    "perf_data.json",
    "perf_baselines.json",
)


def get_app_dir() -> str:
    """程序所在目录：打包后为 exe 同级目录，开发环境为项目根目录。

    `CHONGSHI_APP_DIR` 可显式覆盖（MCP 独立 exe 从 dist_mcp 启动时，
    需要和主程序共用同一份 data/）。
    """
    override = (os.environ.get("CHONGSHI_APP_DIR") or "").strip()
    if override and os.path.isdir(override):
        return os.path.abspath(override)
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        # onedir 里可能有辅助 exe 放在 _internal/（如 虫师-mcp.exe）：
        # 它们要和主程序共用根目录的 data/，不能各自在 _internal 下建一份
        if os.path.basename(exe_dir).lower() == "_internal":
            return os.path.dirname(exe_dir)
        # 打包环境：onedir 下 exe 与 _internal 同级，数据放在 exe 旁边
        return exe_dir
    # 开发环境：本文件位于 <项目根>/utils/ 下，往上两级即项目根
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_data_dir() -> str:
    """数据目录的绝对路径（不存在时会自动创建）。"""
    path = os.path.join(get_app_dir(), DATA_DIR_NAME)
    try:
        os.makedirs(path, exist_ok=True)
    except Exception as e:
        print(f"[app_paths] 创建数据目录失败 {path}: {e}")
    return path


def data_path(filename: str) -> str:
    """返回数据目录下指定文件的绝对路径。"""
    return os.path.join(get_data_dir(), filename)


def migrate_legacy_data_files():
    """把早期散落在程序目录根下的数据文件搬进 data/。

    已存在同名文件时跳过（绝不覆盖），返回实际迁移的文件名列表。
    """
    app_dir = os.path.abspath(get_app_dir())
    data_dir = os.path.abspath(get_data_dir())
    if app_dir == data_dir:
        return []

    moved = []
    for name in LEGACY_DATA_FILES:
        legacy = os.path.join(app_dir, name)
        target = os.path.join(data_dir, name)
        if not os.path.isfile(legacy) or os.path.exists(target):
            continue
        try:
            shutil.move(legacy, target)
            moved.append(name)
        except Exception as e:
            print(f"[app_paths] 迁移 {name} 失败: {e}")

    if moved:
        print(f"[app_paths] 已迁移 {len(moved)} 个数据文件到 {data_dir}: {', '.join(moved)}")
    return moved


# ---------------------------------------------------------------------------
# 资产路径：assets/ 下的用户资源（数据驱动表格、图像定位参考图）
#
# 为什么要相对路径：以前库内存的是绝对路径（C://xxx//data//element_images//a.png），
# 换机器 / 拷工程后全部失效。现在统一存 `assets/<前缀>-<哈希>-<原名>`，
# 只靠 data/ 目录定位，跟着程序一起搬。
# ---------------------------------------------------------------------------
ASSETS_DIR_NAME = "assets"
ASSET_PREFIX_DATA = "data"    # 数据驱动：csv / xlsx
ASSET_PREFIX_IMAGE = "img"    # 图像定位参考图：png / jpg

_HASH_LEN = 8


def get_assets_dir() -> str:
    """资产目录 data/assets/ 的绝对路径（不存在会自动创建）。"""
    path = os.path.join(get_data_dir(), ASSETS_DIR_NAME)
    try:
        os.makedirs(path, exist_ok=True)
    except Exception as e:
        print(f"[app_paths] 创建资产目录失败 {path}: {e}")
    return path


def _is_unsafe_rel(rel: str) -> bool:
    """相对路径里出现盘符 / UNC / 上跳，一律视为不安全。

    导入别人给的数据包时，这条是必须挡住的输入（zip 路径穿越）。
    """
    text = rel.replace("\\", "/")
    if ":" in text or text.startswith("//"):
        return True
    return ".." in text.split("/")


def is_asset_rel(path: str) -> bool:
    """是否是本程序认可的资产相对路径（assets/ 开头且安全）。"""
    text = str(path or "").strip().replace("\\", "/")
    if not text.startswith(ASSETS_DIR_NAME + "/"):
        return False
    return not _is_unsafe_rel(text)


def to_abs(path: str) -> str:
    """资产路径 → 绝对路径。

    - 绝对路径原样返回（兼容改造前的存量数据）
    - 合法相对路径（assets/xxx）拼到 data/ 下，且结果必须仍在 data/ 内
    - 其它（含上跳、盘符、UNC）原样返回，由调用方报「资源缺失」
    """
    text = str(path or "").strip()
    if not text:
        return ""
    if os.path.isabs(text):
        return text
    if not is_asset_rel(text):
        return text
    data_dir = os.path.abspath(get_data_dir())
    full = os.path.abspath(os.path.join(data_dir, text.replace("/", os.sep)))
    try:
        if os.path.commonpath([data_dir, full]) != data_dir:
            return text          # 越界，按缺失处理
    except ValueError:
        return text              # 不同盘符
    return full


def to_rel(path: str) -> str:
    """绝对路径 → 资产相对路径；不在 data/ 内的原样返回。

    只做「规范化表示」，不复制文件 —— 复制由 import_asset() 负责。
    """
    text = str(path or "").strip()
    if not text or not os.path.isabs(text):
        return text
    data_dir = os.path.abspath(get_data_dir())
    full = os.path.abspath(text)
    try:
        rel = os.path.relpath(full, data_dir)
    except ValueError:
        return text
    if rel.startswith(".."):
        return text
    return rel.replace(os.sep, "/")


def _file_hash(path: str) -> str:
    import hashlib
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()[:_HASH_LEN]
    except Exception:
        return ""


def asset_name(src_path: str, prefix: str) -> str:
    """按「前缀-内容哈希-原名」生成资产文件名。

    内容哈希参与命名：同一份表格被两次绑定不会产生两份副本；
    同名不同内容也不会互相覆盖。
    """
    base = os.path.basename(str(src_path or "").strip())
    digest = _file_hash(src_path) if os.path.isfile(src_path) else ""
    safe = base.replace("/", "_").replace("\\", "_")
    return f"{prefix}-{digest}-{safe}" if digest else f"{prefix}-{safe}"


def import_asset(src_path: str, prefix: str) -> str:
    """把外部文件复制进 assets/ 并返回相对路径；失败返回空串。

    已经位于 data/ 内的文件不复制，直接返回相对路径（避免自我复制）。
    同名且同内容的文件复用现有副本。
    """
    src = str(src_path or "").strip()
    if not src or not os.path.isfile(src):
        return ""
    existing_rel = to_rel(src)
    if is_asset_rel(existing_rel):
        return existing_rel
    name = asset_name(src, prefix)
    if not name:
        return ""
    target = os.path.join(get_assets_dir(), name)
    rel = f"{ASSETS_DIR_NAME}/{name}"
    if os.path.isfile(target):
        return rel
    try:
        shutil.copy2(src, target)
        return rel
    except Exception as e:
        print(f"[app_paths] 复制资产失败 {src} -> {target}: {e}")
        return ""


def normalize_image_value(value: str) -> str:
    """把「图像定位值」统一成 assets/ 相对路径；外部文件自动复制进来。

    元素库、步骤参数都允许手填/粘贴路径，这条收口挡住绝对路径重新渗回库里。
    非文件路径（资源ID、坐标文本）原样返回。
    """
    text = str(value or "").strip()
    if not text or is_asset_rel(text):
        return text
    if not os.path.isabs(text) or not os.path.isfile(text):
        return text
    rel = to_rel(text)
    if is_asset_rel(rel):
        return rel
    return import_asset(text, ASSET_PREFIX_IMAGE) or text


def display_name(path: str) -> str:
    """资产路径的友好显示名：去掉 assets/ 前缀与哈希段。

    `assets/img-5e7d9c01-登录按钮.png` → `登录按钮.png`
    非资产路径按 basename 处理。
    """
    import re as _re
    base = os.path.basename(str(path or "").strip())
    m = _re.match(r"^(?:data|img)-[0-9a-f]{8}-(.+)$", base)
    return m.group(1) if m else base


def open_containing_folder(file_path: str) -> bool:
    """在系统文件管理器里打开文件所在目录；失败时返回 False，不抛异常。"""
    folder = os.path.dirname(os.path.abspath(file_path))
    try:
        if os.name == 'nt':
            os.startfile(folder)
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', folder])
        else:
            subprocess.Popen(['xdg-open', folder])
        return True
    except Exception as e:
        print(f"[app_paths] 打开目录失败 {folder}: {e}")
        return False
