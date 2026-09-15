# -*- mode: python ; coding: utf-8 -*-

import os
import sys
import uiautomator2

block_cipher = None

u2_path = os.path.dirname(uiautomator2.__file__)
assets_src = os.path.join(u2_path, 'assets')
assets_dst = os.path.join('uiautomator2', 'assets')

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('resources', 'resources'),
        (assets_src, assets_dst),
    ],
    hiddenimports=[
        'PyQt6.QtCore',
        'PyQt6.QtGui',
        'PyQt6.QtWidgets',
        'PyQt6.QtWebEngineWidgets',
        'PyQt6.QtWebEngineCore',
        'PyQt6.QtWebChannel',
        'qtawesome',
        'qtawesome.iconic_font',
        'uiautomator2',
        'uiautomator2.device',
        'uiautomator2.adbutils',
        'uiautomator2.xpath',
        'adbutils',
        'weditor',
        'lxml',
        'retry',
        'deprecation',
        'construct',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyd = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ---------- 改为 onedir 模式（快速启动） ----------
# 1. 生成可执行文件（不再包含所有数据，只包含启动器）
exe = EXE(
    pyd,
    a.scripts,
    [],
    exclude_binaries=True,           # 关键：将二进制文件排除，由 COLLECT 收集
    name='驭虫师',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                       # 关闭 UPX 压缩，减少解压开销（可选）
    runtime_tmpdir=None,
    console=False,                   # 是否显示控制台
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='resources/icons/app_icon.ico',
)

# 2. 收集所有依赖文件到目标目录
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='驭虫师',                    # 输出文件夹名称
)