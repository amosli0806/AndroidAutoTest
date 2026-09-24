# utils/adb_path.py
"""统一获取 adb 可执行文件路径。

2026-09-25 起不再内置 adb（tools/adb.exe 已移除）：完全依赖系统 PATH 里的 adb。
为什么不内置（历史教训）：内置 adb 与用户电脑上其他 adb（Android SDK / 其他工具）
版本可能不一致，虽然协议号相同不会互踢 server，但双份二进制让"用哪个 adb"变得
不可预期，排查问题时还要先弄清版本来源。统一用系统 PATH 的一份，行为可预期。

用户没装 adb 怎么办：帮助中心「环境准备」有安装指引；启动预扫检测不到 adb 时
也会在消息中心提示。
"""
import os
import sys
import shutil


def get_adb_path() -> str:
    """返回 adb 可执行文件路径：系统 PATH 里的 adb。

    返回裸命令名 'adb'，由子进程按 PATH 搜索解析。
    没装 adb 时调用会抛 FileNotFoundError，由调用方处理
    （启动预扫会捕获并提示用户安装，见 main.py 的启动预扫）。
    """
    adb_name = 'adb' if sys.platform == 'darwin' else 'adb.exe'
    return adb_name


def adb_installed() -> bool:
    """系统 PATH 里是否找得到 adb。用于启动时给出友好提示。"""
    return shutil.which('adb') is not None


def get_scrcpy_path() -> str:
    """返回 scrcpy 可执行文件路径，优先级：
    1. 打包后的 tools/scrcpy.exe（scrcpy 仍在内置——它不依赖 adb 的版本一致性，
       调用系统 adb 即可工作）
    2. 项目根 tools/scrcpy.exe
    3. 系统 PATH 中的 scrcpy
    """
    scrcpy_name = 'scrcpy.exe' if sys.platform == 'win32' else 'scrcpy'

    # 1. 打包环境：_MEIPASS/tools/scrcpy.exe
    if getattr(sys, 'frozen', False):
        packed = os.path.join(sys._MEIPASS, 'tools', scrcpy_name)
        if os.path.exists(packed):
            return packed

    # 2. 开发环境：utils/adb_path.py 往上两级才是项目根
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    local = os.path.join(base, 'tools', scrcpy_name)
    if os.path.exists(local):
        return local

    # 3. 系统 PATH
    return 'scrcpy'
