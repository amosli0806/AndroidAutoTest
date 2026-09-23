# -*- mode: python ; coding: utf-8 -*-
"""独立更新器的打包配置（onefile）。

只依赖标准库，所以刻意把 Qt / 科学计算这些大库全部排除，产物很小（个位数 MB）。
它会被 CI 一起打进更新包，用户在更新时由主程序解压出来直接运行。
"""

a = Analysis(
    ['updater.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'PyQt6', 'PySide6', 'PySide2',
        'pyecharts', 'pyqtgraph', 'numpy', 'matplotlib',
        'lxml', 'openpyxl', 'uiautomator2', 'adbutils', 'weditor',
        'tkinter', 'unittest', 'pydoc', 'email', 'http',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

# onefile：binaries/datas 一并塞进 exe（没有 COLLECT 步骤）
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='updater',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,          # 窗口程序：不闪黑框，日志写文件
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='resources/icons/app_icon.ico',
    manifest=(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">'
        '<application xmlns="urn:schemas-microsoft-com:asm.v3"><windowsSettings>'
        '<dpiAware xmlns="http://schemas.microsoft.com/SMI/2005/WindowsSettings">true/pm</dpiAware>'
        '<dpiAwareness xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">PerMonitorV2, PerMonitor</dpiAwareness>'
        '</windowsSettings></application></assembly>'
    ),
)
