# utils/adb_path.py
"""统一获取 adb 可执行文件路径"""
import os
import sys


def get_adb_path() -> str:
    """返回 adb 可执行文件的路径，优先级：
    1. 打包后的 tools/adb.exe
    2. 当前可执行文件目录下的 adb.exe
    3. 系统 PATH 中的 adb
    """
    adb_name = 'adb' if sys.platform == 'darwin' else 'adb.exe'

    # 1. 打包环境：_MEIPASS/tools/adb.exe
    if getattr(sys, 'frozen', False):
        packed = os.path.join(sys._MEIPASS, 'tools', adb_name)
        if os.path.exists(packed):
            return packed

    # 2. 开发环境：项目根目录/tools/adb.exe
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    local = os.path.join(base, 'tools', adb_name)
    if os.path.exists(local):
        return local

    # 3. 系统 PATH
    return 'adb'

def get_scrcpy_path() -> str:
    """返回 scrcpy 可执行文件路径，优先级：
    1. 打包后的 tools/scrcpy.exe
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