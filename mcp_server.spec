# -*- mode: python ; coding: utf-8 -*-
"""虫师 MCP 独立打包：产出无控制台的 stdio 可执行文件。

构建：
    pyinstaller mcp_server.spec --noconfirm --distpath dist_mcp --workpath build_mcp

说明：
    * 只包含 MCP server 依赖，不包含 PyQt6 / views；
    * console=False：双击/被客户端拉起时不弹黑框；
    * stdio 句柄由 MCP 客户端通过管道提供，启动后 stdout 只走协议。
"""
import os

import uiautomator2

ROOT = os.path.abspath(".")
u2_dir = os.path.dirname(uiautomator2.__file__)
hiddenimports = [
    "mcp",
    "mcp.server",
    "mcp.server.mcpserver",
    "mcp.server.stdio",
    "mcp.server.lowlevel",
    "mcp.server.session",
    "mcp.shared",
    "mcp.types",
    "mcp_server.server",
    "mcp_server.runtime",
    "mcp_server.device_jobs",
    "services.monkey_runner",
    "services.screenrecord_runner",
    "services.logcat_stream",
    "services.perf_runner",
    "services.tcpdump_runner",
    "mcp_server.schema",
    "mcp_server.audit",
    "mcp_server.logging_setup",
]

a = Analysis(
    ["mcp_server/__main__.py"],
    pathex=[ROOT],
    binaries=[],
    datas=[
        (os.path.join(u2_dir, "assets"), os.path.join("uiautomator2", "assets")),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "PyQt6", "PySide6", "PySide2",
        "matplotlib", "pyecharts", "pyqtgraph",
        "weditor",
        "tkinter",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="虫师-mcp",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
