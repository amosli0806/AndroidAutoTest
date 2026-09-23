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
    """程序所在目录：打包后为 exe 同级目录，开发环境为项目根目录。"""
    if getattr(sys, "frozen", False):
        # 打包环境：onedir 下 exe 与 _internal 同级，数据放在 exe 旁边
        return os.path.dirname(os.path.abspath(sys.executable))
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
