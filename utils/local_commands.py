# utils/local_commands.py
"""可选的本地命令库 —— 放在 data/local_commands.json，不进版本库。

用途：有些命令属于特定客户/项目、不适合随仓库分发（车厂私有的 settings 键、客户设备的内部
日志路径、特定机型的入口 Activity 等等），把它们当"数据"放本地即可：

    data/local_commands.json     ← data/ 已被 .gitignore 挡住，不会进仓库
    {
      "presets": [ ... 同 services/preset_commands.py 的字段 ... ],
      "adb":     [ ... 同 services/adb_commands.py 的字段 ... ]
    }

* presets 会作为"预设命令"附加到预设列表里（和内置预设一样点一下就执行）
* adb 会并入 ADB 指令库的搜索结果里

文件不存在 / 格式不对时一律当空处理，**不影响程序启动**。
换机器时把这个文件拷过去即可；也可以直接手写编辑。
"""

import json
import logging
import os

from utils.app_paths import data_path

logger = logging.getLogger(__name__)

LOCAL_COMMANDS_FILE = "local_commands.json"


def local_commands_path() -> str:
    return data_path(LOCAL_COMMANDS_FILE)


def load_local_commands() -> dict:
    """读本地命令库；返回 {"presets": [...], "adb": [...]}（缺项给空列表）。"""
    path = local_commands_path()
    empty = {"presets": [], "adb": []}
    if not os.path.isfile(path):
        return empty
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning("本地命令库读取失败（忽略）%s: %s", path, e)
        return empty
    if not isinstance(data, dict):
        logger.warning("本地命令库格式不对（应为对象）：%s", path)
        return empty
    return {
        "presets": [c for c in (data.get("presets") or []) if isinstance(c, dict)],
        "adb": [c for c in (data.get("adb") or []) if isinstance(c, dict)],
    }


def save_local_commands(data: dict) -> str:
    """写本地命令库（迁移脚本 / 设置界面用），返回写入路径。"""
    path = local_commands_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path
