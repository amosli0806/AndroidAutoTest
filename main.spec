# -*- mode: python ; coding: utf-8 -*-

import os
import uiautomator2

block_cipher = None

u2_path = os.path.dirname(uiautomator2.__file__)
assets_src = os.path.join(u2_path, 'assets')
assets_dst = os.path.join('uiautomator2', 'assets')

# ---------- weditor.exe（应用可视化的独立服务，由 weditor.spec 先行打包） ----------
# 打包顺序：pyinstaller weditor.spec --distpath dist_weditor → 再跑 main.spec。
# 缺它时打包版的应用可视化会报「未找到内置的 weditor.exe」（见 weditor_service）。
_WEDITOR_EXE = os.path.join(os.path.dirname(os.path.abspath('main.spec')), 'dist_weditor', 'weditor.exe')

_extra_datas = []
if os.path.exists(_WEDITOR_EXE):
    _extra_datas.append((_WEDITOR_EXE, 'tools'))
else:
    print(f"[spec] 未找到 {_WEDITOR_EXE} —— 打包版的应用可视化将不可用（先跑 weditor.spec）")

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('resources', 'resources'),
        (assets_src, assets_dst),
        ('tools', 'tools'),            # ← 新增：scrcpy + tcpdump
        *_extra_datas,
    ],
    hiddenimports=[
        # ---------- PyQt6 ----------
        'PyQt6.QtCore',
        'PyQt6.QtGui',
        'PyQt6.QtWidgets',
        'PyQt6.QtWebEngineWidgets',
        'PyQt6.QtWebEngineCore',
        'PyQt6.QtWebChannel',

        # ---------- 原有依赖 ----------
        'qtawesome',
        'qtawesome.iconic_font',
        'uiautomator2',
        # 注：不要再登记 uiautomator2.device / uiautomator2.adbutils ——
        # uiautomator2 3.x 里没有这两个子模块（实际是 core / xpath / base / selector…），
        # 登记了只会在每次打包时刷两条 "Hidden import ... not found" 的 ERROR 噪音，
        # 掩盖真正的问题。uiautomator2.xpath 是真的，保留。
        'uiautomator2.xpath',
        'adbutils',
        'weditor',
        'lxml',
        'retry',
        'deprecation',
        'construct',

        # ---------- 新增：捕虫师搬过来的模块 ----------
        'models.command',
        'services.adb_commands',
        'services.preset_commands',
        'services.command_manager',
        'services.adb_command_pool',
        'utils.memory_analyzer',
        'utils.wireless_manager',
        'utils.serial_helper',
        'utils.adb_path',

        # ---------- 新增：语音播报（Windows 内置 TTS，走 pywin32/COM） ----------
        # 这三个模块是在 main() 里函数级导入的，显式登记避免打包漏掉；
        # win32com.client / pythoncom 是 SAPI 的运行时依赖（晚绑定 COM）。
        'models.voice_model',
        'services.voice_service',
        'views.voice_view',
        # ---------- 新增：Edge 在线语音（edge-tts，函数级 import） ----------
        # edge_tts 是 EdgeTtsEngine 里函数级 import 的异步库；aiohttp 是它的
        # 运行时依赖（有官方 hook，这里显式登记更稳，避免动态导入漏掉子模块）。
        'edge_tts',
        'aiohttp',
        # miniaudio（mp3 解码转 wav）运行时 import cffi，其 C 扩展 _cffi_backend
        # 需要显式登记——漏了会在打包版报 "No module named '_cffi_backend'"
        'miniaudio',
        'cffi',
        '_cffi_backend',
        # ---------- 新增：检查更新 ----------
        # 更新相关的对话框/服务都是函数级导入（点菜单、点更新才用），显式登记避免打包漏掉
        'services.update_service',
        'views.dialogs.update_dialog',
        'views.dialogs.update_progress_dialog',
        'win32com.client',
        'pythoncom',

        # ---------- 新增：ADB 工具箱 dialog（已下线的对话框文件已删除） ----------
        'views.adb_toolbox_view',
        'views.adb_dialogs.custom_command_dialog',
        'views.adb_dialogs.select_commands_dialog',
        'views.adb_dialogs.process_selector_dialog',
        'views.adb_dialogs.search_results_dialog',
        'views.adb_dialogs.wireless_dialog',
        'views.adb_dialogs.push_progress_dialog',
        'views.adb_dialogs.packet_capture_dialog',
        'views.adb_dialogs.memory_monitor_dialog',

        # ---------- 新增：控制器 ----------
        'controllers.adb_toolbox_controller',

        # ---------- 新增：第三方依赖 ----------
        'pyecharts',
        'pyecharts.charts',
        'pyecharts.options',
        'openpyxl',
        'openpyxl.workbook',
        'pyqtgraph',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
        excludes=[
        # ---------- 严禁打进包里的 Qt 绑定 ----------
        'PySide6',
        'PySide2',
        'PyQt5',

        # ---------- 大而无用的库（减小体积） ----------
        # tkinter 可以放心排除：启动封面已改为 Qt 自绘（views/splash_window.py），
        # 不再依赖 Tcl/Tk；项目本身也没用 tkinter 模块。
        'tkinter',
        'matplotlib',
        'IPython',
        'jupyter',
        'test',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyd = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ---------- 启动封面：统一由 views/splash_window.py 自绘 ----------
# 不再用 PyInstaller 的 Tcl/Tk Splash。之前「两层封面并存」（Tcl/Tk 静态封面 + 自绘封面）
# 会导致打包后新旧两个封面先后/重叠出现 —— 2026-10-07 用户实测反馈后彻底去掉 Tcl/Tk 层。
# 现在：封面、进度条、分阶段文案、版本号、淡出全部在 views/splash_window.py 里自绘，
# 唯一入口是 main.py 里的 splash_show()（QApplication 建好后接管）。

# ---------- Windows：让进程从第一帧起就是 DPI 感知的 ----------
# 不声明会怎样（用户实际看到的「封面从大到小」）：
#   bootloader 起的进程是 DPI-unaware，启动封面窗口被 Windows 整体按系统缩放显示，
#   本机 150% -> 看着是 960x600（被位图拉伸、略糊）；
#   而 QApplication 一构造，Qt 就把进程切成 per-monitor DPI 感知，系统随即停止拉伸
#   这个「已经存在」的封面窗口 -> 它在那一瞬间掉回 640x400，居中位置也跟着跑偏。
#   时间点正好卡在「正在启动…」->「正在初始化界面…」之间。
# 在这里声明 per-monitor v2 之后：
#   * 封面从第一帧就是真实像素、居中正确、文字清晰；
#   * Qt 再想设置感知等级会失败（已被 manifest 声明），所以全程没有任何跳变。
# 注意：声明之后封面窗口不再被系统放大，**图多大就显示多大**（PNG 640x400 就是
# 640x400 物理像素），觉得小就换张更大的图。
_MANIFEST = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
  <application xmlns="urn:schemas-microsoft-com:asm.v3">
    <windowsSettings>
      <dpiAware xmlns="http://schemas.microsoft.com/SMI/2005/WindowsSettings">true/pm</dpiAware>
      <dpiAwareness xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">PerMonitorV2, PerMonitor</dpiAwareness>
    </windowsSettings>
  </application>
</assembly>
"""

# ---------- onedir 模式 ----------
exe = EXE(
    pyd,
    a.scripts,
    exclude_binaries=True,
    manifest=_MANIFEST,
    name='虫师',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,          # 首次调试可临时改 True
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='resources/icons/app_icon.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='虫师',
)