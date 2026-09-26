# -*- mode: python ; coding: utf-8 -*-

import os
import sys
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
        # tkinter 可以放心排除：启动封面走的是 Splash 自带的 tcl/tk 动态库 + 数据文件
        # （见上面的 splash 段），跟 Python 的 tkinter 模块没有关系。
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

# ---------- 启动封面（splash） ----------
# 由 bootloader 在 Python 解释器启动「之前」就弹出来，所以双击 exe 后几百毫秒
# 就能看到画面，而不是等 8~15 秒才蹦出主界面（实测冷启 14.8s / 温启 7.6~9.2s）。
# 盖住的正是：顶层 import、模型与主窗口构建、adb server 拉起、uiautomator2 连设备。
#
# 换图：把 resources/images/splash.png 换成你的**底图**即可（里面不要带版本号，
#   版本号由这个文件在打包时画上去，见下面「版本号」一段），**不用动这个文件**。
#   尺寸：会被 DPI 感知地按真实像素显示，**图多大就显示多大**（不再被系统放大）；
#   建议 640x400 起，想要更有存在感就等比放大（别超过 1280x800，上限见 max_img_size）。
#   Windows 上品红 #FF00FF 被当作透明色，图里要透明的部分才用它、别拿来当普通颜色。
# 进度文字：main.py 里的 pyi_splash.update_text() 分段刷新。
#   text_pos 是「相对图片左上角」的像素坐标，锚点是文字的左下角（(28, 372) = 左下角留白处）。
#   注意 text_font 里带空格的字体名必须自己加花括号（'{Microsoft YaHei}'）——
#   不加的话生成的 Tcl 脚本会报 bad option "YaHei" 并整段中断，
#   表现是封面变成屏幕左上角一个空白小窗（文字、居中、去边框全都不再生效）。
# ---------- 版本号：单点来源 ----------
# 版本号只写在 utils/version.py 里（「关于」对话框也从那里取）。这里读出来，
# 打包时自动画到封面底图上 —— 所以封面上的版本号不需要手动改图片，
# 换版本 = 改 utils/version.py 一处 + 重新打包。
_SPEC_DIR = globals().get('SPECPATH') or os.path.dirname(os.path.abspath('main.spec'))


def _load_app_version():
    """从 utils/version.py 读 APP_VERSION（用绝对路径加载，不依赖 cwd / sys.path）。"""
    import importlib.util
    path = os.path.join(_SPEC_DIR, 'utils', 'version.py')
    try:
        mod_spec = importlib.util.spec_from_file_location('_chongshi_version', path)
        mod = importlib.util.module_from_spec(mod_spec)
        mod_spec.loader.exec_module(mod)
        return str(mod.APP_VERSION).strip()
    except Exception as e:
        print(f"[spec] 读取 utils/version.py 失败（{e}），封面将不画版本号")
        return ''


APP_VERSION = _load_app_version()

# ---------- 封面图 = 底图 + 版本号（打包时现画） ----------
# resources/images/splash.png 是**底图**：换封面就换它，里面不要带版本号。
# 下面这几项控制版本号怎么画到图上；换了自己的底图后按需微调（都是相对底图尺寸的比例，
# 所以底图放大缩小都不用改）。想自己把版本号画进图里，就把 _SPLASH_VERSION_ANCHOR 设为 None。
_SPLASH_SRC = os.path.join(_SPEC_DIR, 'resources', 'images', 'splash.png')
_SPLASH_VERSION_ANCHOR = (0.94, 0.11)   # 版本号右上锚点（相对底图宽/高）；None = 不在图上画
_SPLASH_VERSION_COLOR = '#98A2B3'
_SPLASH_VERSION_FONT = r'C:\Windows\Fonts\msyh.ttc'


def _make_splash_image():
    """底图 + 版本号 -> 构建目录里的成品图；返回它的绝对路径。"""
    if not os.path.exists(_SPLASH_SRC):
        return None
    if not APP_VERSION or _SPLASH_VERSION_ANCHOR is None:
        return _SPLASH_SRC
    try:
        from PIL import Image, ImageDraw, ImageFont
        from PyInstaller.config import CONF

        img = Image.open(_SPLASH_SRC)
        # 带透明通道的底图先铺到白底上：启动封面是靠品红(#FF00FF)表示透明的，
        # 直接 convert('RGB') 会把透明区变成黑色（打包出来才发现就晚了）。
        if img.mode in ('RGBA', 'LA', 'P'):
            img = img.convert('RGBA')
            bg = Image.new('RGB', img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[-1])
            img = bg
        else:
            img = img.convert('RGB')

        w, h = img.size
        text = f'V{APP_VERSION}'
        font = ImageFont.truetype(_SPLASH_VERSION_FONT, max(10, round(h * 0.035)))
        draw = ImageDraw.Draw(img)
        ax, ay = _SPLASH_VERSION_ANCHOR
        draw.text((w * ax - draw.textlength(text, font=font), h * ay),
                  text, font=font, fill=_SPLASH_VERSION_COLOR)

        out = os.path.join(CONF['workpath'], 'splash_versioned.png')
        os.makedirs(os.path.dirname(out), exist_ok=True)
        img.save(out)
        print(f"[spec] 封面已画上版本号 V{APP_VERSION} -> {out}")
        return out
    except Exception as e:
        print(f"[spec] 封面画版本号失败（{e}），直接用底图")
        return _SPLASH_SRC


_splash_image = _make_splash_image()
splash = None
if _splash_image:
    splash = Splash(
        _splash_image,
        binaries=a.binaries,
        datas=a.datas,
        text_pos=(28, 372),
        text_size=11,
        text_font='{Microsoft YaHei}',
        text_color='#5A6472',
        text_default='正在启动…',
        always_on_top=True,
        max_img_size=(1280, 1280),   # 别让它悄悄缩我们的图（默认 760x480 会缩）
    )
else:
    print(f"[spec] 未找到启动封面底图 {_SPLASH_SRC}，本次构建不含启动画面")

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
    *([splash, splash.binaries] if splash is not None else []),
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
    # splash.binaries 必须**同时**交给 EXE 和 COLLECT，别只给 EXE ——
    # PyInstaller 6.22 有个增量构建的坑：PKG.dependencies 只在 PKG.assemble() 里填，
    # 而 PKG 走增量缓存（guts 校验通过、什么都没改的那种重新打包）时 assemble() 被跳过，
    # 于是 PKG.dependencies 保持空 -> EXE.dependencies 空 -> COLLECT 拿不到 splash 带来的
    # tcl/tk 二进制（EXE-00.toc / PKG-00.toc 里明明有，COLLECT-00.toc 里一条都没有）。
    # 后果很误导人：.res 是嵌在 exe 里的，bootloader 照样去拉 splash，但 _internal 下
    # 找不到 tcl86t.dll / _tk_data，双击就连弹三个
    # 「Failed to load Tcl DLL ... tcl86t.dll / SPLASH: failed to load Tcl/Tk shared libraries!」
    # 的错误框（应用本身还能用，只是没有启动封面）。
    # 显式传进来就不依赖 PKG 那条路径了，重复项由 normalize_toc 去重。
    *([splash.binaries] if splash is not None else []),
    strip=False,
    upx=False,
    upx_exclude=[],
    name='虫师',
)