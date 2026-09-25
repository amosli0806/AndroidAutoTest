# -*- mode: python ; coding: utf-8 -*-
"""weditor 独立打包：产出 tools/weditor.exe，随虫师主包分发。

为什么：打包版的 sys.executable 是虫师.exe，不支持 `-m weditor`——
应用可视化在打包版里必须 spawn 独立的 weditor.exe（services/weditor_service.py）。

本地打包顺序（先 weditor 后主包，main.spec 会把它带进 tools/）：
    pyinstaller weditor.spec --noconfirm --distpath dist_weditor --workpath build_weditor
    pyinstaller main.spec --noconfirm
"""
import os

import weditor

_weditor_dir = os.path.dirname(weditor.__file__)

# ---------- 打包前修复 weditor 0.7.3 的 version.py（幂等） ----------
# 它引用 pkg_resources（setuptools，Python 3.13 / PyInstaller 环境没有），
# 且 except 分支写坏会直接 NameError 崩掉整个启动（weditor.exe 实测）。
# 修成 importlib.metadata 版。必须在 Analysis 之前执行——PYZ 封的是打包时快照，
# 运行时改文件无效（虫师进程内的 _fix_weditor_version 只救源码模式）。
_version_py = os.path.join(_weditor_dir, "web", "version.py")
if os.path.exists(_version_py):
    with open(_version_py, "r", encoding="utf-8") as _f:
        _src = _f.read()
    if "pkg_resources" in _src:
        _fixed = (
            "# coding: utf-8\n#\n\n"
            "try:\n"
            "    import importlib.metadata\n"
            '    __version__ = importlib.metadata.version("weditor")\n'
            "except Exception:\n"
            '    __version__ = "unknown"\n'
        )
        with open(_version_py, "w", encoding="utf-8") as _f:
            _f.write(_fixed)
        print("[weditor.spec] 已修复 weditor/web/version.py（pkg_resources -> importlib.metadata）")

a = Analysis(
    ["weditor_launcher.py"],
    pathex=[],
    binaries=[],
    datas=[
        # weditor 的静态资源（页面模板/前端静态文件），运行时按包路径读取。
        # 用 os.path 动态定位（相对路径 ../.venv 会因 spec 运行目录解析错误）
        (os.path.join(_weditor_dir, "templates"), "weditor/templates"),
        (os.path.join(_weditor_dir, "static"), "weditor/static"),
        (os.path.join(_weditor_dir, "page.xml"), "weditor/page.xml"),
    ],
    hiddenimports=[
        "weditor",
        "weditor.__main__",
        "weditor.web",
        "weditor.web.handlers",
        "tornado",
        "tornado.web",
        "tornado.ioloop",
        "tornado.websocket",
        "tornado.process",
        "requests",
        "six",
        "packaging",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "PySide6",
        "PySide2",
        "PyQt5",
        "tkinter",
        "matplotlib",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    name="weditor",
    debug=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,   # 无控制台：被虫师 spawn 时 stdout 走 PIPE，不弹黑窗
    icon="resources/icons/app_icon.ico",
)

