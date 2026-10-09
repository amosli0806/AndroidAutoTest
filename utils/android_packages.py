# utils/android_packages.py
"""Android 包名相关的公共判定。

为什么单独放一个文件：车机/手机的系统 UI（状态栏、空调面板、系统组件）会混进
uiautomator 的窗口树里 —— 抓取界面要**排除**它们，录制反查要**提示**它们。
两处必须是同一份名单，散在两个模块里早晚会走岔。
"""

# 系统 UI 包名：不属于任何被测应用
SYSTEM_UI_PACKAGES = frozenset({
    'com.android.systemui',
    'android',
})


def is_system_ui_package(package: str) -> bool:
    """包名是否属于系统 UI（空包名返回 False，避免把「没识别出来」误判成系统 UI）。"""
    return bool(package) and package in SYSTEM_UI_PACKAGES
