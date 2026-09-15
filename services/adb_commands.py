# src/utils/adb_commands.py
"""
ADB 命令库模块
提供预定义的 ADB 命令列表，按分类组织，用于在搜索功能中展示和复制。
每个命令包含命令文本、分类和描述。
同时提供一个按分类索引的字典，便于快速检索。
"""

ADB_COMMANDS = [
    # ---------- 设备管理 ----------
    {
        "command": "adb devices",
        "category": "设备管理",
        "description": "列出所有已连接的设备（包括真机和模拟器）"
    },
    {
        "command": "adb connect <ip>:<port>",
        "category": "设备管理",
        "description": "通过 TCP/IP 连接到无线设备（默认端口 5555）"
    },
    {
        "command": "adb disconnect <ip>:<port>",
        "category": "设备管理",
        "description": "断开无线设备连接"
    },
    {
        "command": "adb kill-server",
        "category": "设备管理",
        "description": "终止 ADB 服务进程"
    },
    {
        "command": "adb start-server",
        "category": "设备管理",
        "description": "启动 ADB 服务进程"
    },
    {
        "command": "adb reboot",
        "category": "设备管理",
        "description": "重启设备"
    },
    {
        "command": "adb reboot bootloader",
        "category": "设备管理",
        "description": "重启到 bootloader 模式（刷机模式）"
    },
    {
        "command": "adb reboot recovery",
        "category": "设备管理",
        "description": "重启到 recovery 模式（恢复模式）"
    },
    {
        "command": "adb root",
        "category": "设备管理",
        "description": "以 root 权限重启 adbd"
    },
    {
        "command": "adb remount",
        "category": "设备管理",
        "description": "重新挂载系统分区为可读写"
    },
    {
        "command": "adb shell settings put system hc_auth_personal_info_enable 1\n"
                   "adb shell settings put system hc_auth_sensitive_info_enable 1\n"
                   "adb shell settings put system hc_auth_gps_enable 1\n"
                   "adb shell settings put system hc_auth_mic_enable 1\n"
                   "adb shell settings put system hc_auth_camera_enable 1\n"
                   "adb shell settings put system hc_auth_info_sharing_enable 1",
        "category": "设备管理",
        "description": "开启个人隐私授权（本田车机）"
    },
    {
        "command": "adb shell settings put system hc_auth_personal_info_enable 0\n"
                   "adb shell settings put system hc_auth_sensitive_info_enable 0\n"
                   "adb shell settings put system hc_auth_gps_enable 0\n"
                   "adb shell settings put system hc_auth_mic_enable 0\n"
                   "adb shell settings put system hc_auth_camera_enable 0\n"
                   "adb shell settings put system hc_auth_info_sharing_enable 0",
        "category": "设备管理",
        "description": "关闭个人隐私授权（本田车机）"
    },
    {
        "command": "adb shell setprop hsae.vhal.debug 1\n"
                   "adb shell dumpsys activity service com.android.car/.CarService inject-vhal-event 0x40382402 1\n"
                   "adb shell dumpsys activity service com.android.car/.CarService inject-vhal-event 0x40382403 1",
        "category": "设备管理",
        "description": "25M 走行限制关闭，发送 P 档信号"
    },
    {
        "command": "adb shell am start -n com.alap.honda30ea.chs.developerdiag/com.alap.honda30ea.chs.developerdiag.MainActivity",
        "category": "设备管理",
        "description": "23M/24M 走行限制关闭"
    },

    # ---------- 应用管理 ----------
    {
        "command": "adb install <path_to_apk>",
        "category": "应用管理",
        "description": "安装应用（可附加 -r 覆盖安装，-d 允许降级）"
    },
    {
        "command": "adb uninstall <package_name>",
        "category": "应用管理",
        "description": "卸载应用（可附加 -k 保留数据和缓存）"
    },
    {
        "command": "adb shell pm list packages",
        "category": "应用管理",
        "description": "列出所有应用包名（-s 系统应用，-3 第三方应用）"
    },
    {
        "command": "adb shell pm clear <package_name>",
        "category": "应用管理",
        "description": "清除应用数据和缓存"
    },
    {
        "command": "adb shell am start -n <package_name>/<activity_name>",
        "category": "应用管理",
        "description": "启动指定的 Activity"
    },
    {
        "command": "adb shell am start -n com.hynex.vehicleservice/com.hynex.vehicleservice.MenuActivity",
        "category": "应用管理",
        "description": "本田 23M/24M/25M 写 VIN 后门"
    },
    {
        "command": "adb shell am start -n com.hynex.vehicleservice/com.hynex.vehicleservice.ui.MenuActivity",
        "category": "应用管理",
        "description": "本田 27M 写 VIN 后门"
    },
    {
        "command": "adb shell am start com.android.car.settings",
        "category": "应用管理",
        "description": "打开原生设置页面"
    },
    {
        "command": "adb shell dumpsys window | findstr mCurrentFocus",
        "category": "应用管理",
        "description": "查看当前运行的 Activity（Windows 适用）"
    },
    {
        "command": "adb shell am force-stop <package_name>",
        "category": "应用管理",
        "description": "强制停止应用"
    },
    {
        "command": "adb shell dumpsys package <pkg>",
        "category": "应用管理",
        "description": "查看应用的详细信息（权限、组件等）"
    },
    {
        "command": "adb shell pm path <pkg>",
        "category": "应用管理",
        "description": "查看应用 APK 的安装路径"
    },
    {
        "command": "adb shell cmd package list packages",
        "category": "应用管理",
        "description": "列出所有包名（新版命令）"
    },

    # ---------- 文件操作 ----------
    {
        "command": "adb push <local> <remote>",
        "category": "文件操作",
        "description": "将电脑上的文件或文件夹推送到设备"
    },
    {
        "command": "adb pull <remote> <local>",
        "category": "文件操作",
        "description": "将设备上的文件或文件夹拉取到电脑"
    },
    {
        "command": "adb shell ls <path>",
        "category": "文件操作",
        "description": "列出目录内容"
    },
    {
        "command": "adb shell ls -lh <path>",
        "category": "文件操作",
        "description": "以人类可读格式列出目录内容，显示文件大小"
    },
    {
        "command": "adb shell du -sh <path>",
        "category": "文件操作",
        "description": "显示指定目录的总大小（人类可读）"
    },
    {
        "command": "adb shell rm <file_path>",
        "category": "文件操作",
        "description": "删除文件或目录（加 -r 递归删除）"
    },
    {
        "command": "adb shell mkdir <directory>",
        "category": "文件操作",
        "description": "创建目录"
    },
    {
        "command": "adb pull /sdcard/BaiduMapAuto/video",
        "category": "文件操作",
        "description": "拉取仪表投流视频"
    },
    {
        "command": "adb pull /storage/emulated/0/Android/data/com.baidu.naviauto/BaiduMap/bnav/naviautoenginelog",
        "category": "文件操作",
        "description": "HC3.0 地图引擎日志"
    },
    {
        "command": "adb pull /sdcard/BaiduMapAuto/naviautoenginelog",
        "category": "日志文件",
        "description": "本田 23M/24M/25M/26M 地图引擎日志路径"
    },
    {
        "command": "adb pull /data/vendor/bdicc/log",
        "category": "日志文件",
        "description": "本田 25M GPS 日志路径"
    },

    # ---------- 日志 ----------
    {
        "command": "adb logcat",
        "category": "日志",
        "description": "查看设备日志（-c 清除，-v time 显示时间）"
    },
    {
        "command": "adb logcat -b radio",
        "category": "日志",
        "description": "查看无线通信相关日志"
    },
    {
        "command": "adb shell dmesg",
        "category": "日志",
        "description": "查看内核日志"
    },
    {
        "command": "adb bugreport",
        "category": "日志",
        "description": "生成完整的错误报告（压缩包）"
    },
    {
        "command": "adb logcat -b crash -v time",
        "category": "日志",
        "description": "捕获崩溃日志（带时间戳）"
    },
    {
        "command": "adb logcat | findstr \"关键词\"",
        "category": "日志",
        "description": "过滤并实时输出日志（Windows，将关键词替换为实际内容）"
    },

    # ---------- 系统信息 ----------
    {
        "command": "adb --version",
        "category": "系统信息",
        "description": "获取 adb 版本"
    },
    {
        "command": "adb shell getprop ro.build.version.release",
        "category": "系统信息",
        "description": "获取 Android 系统版本"
    },
    {
        "command": "adb shell getprop ro.product.model",
        "category": "系统信息",
        "description": "获取设备型号"
    },
    {
        "command": "adb shell dumpsys battery",
        "category": "系统信息",
        "description": "查看电池状态（电量、温度、电压等）"
    },
    {
        "command": "adb shell dumpsys meminfo",
        "category": "系统信息",
        "description": "查看内存使用情况"
    },
    {
        "command": "adb shell dumpsys cpuinfo",
        "category": "系统信息",
        "description": "查看 CPU 使用情况"
    },
    {
        "command": "adb shell dumpsys window displays",
        "category": "系统信息",
        "description": "查看屏幕显示参数（分辨率、刷新率等）"
    },
    {
        "command": "adb shell wm size",
        "category": "系统信息",
        "description": "查看当前屏幕分辨率"
    },
    {
        "command": "adb shell wm density",
        "category": "系统信息",
        "description": "查看当前屏幕密度（DPI）"
    },
    {
        "command": "adb shell wm size <width>x<height>",
        "category": "系统信息",
        "description": "修改屏幕分辨率为指定值（例如 1920x1080）"
    },
    {
        "command": "adb shell wm size reset",
        "category": "系统信息",
        "description": "恢复屏幕分辨率为系统默认值"
    },
    {
        "command": "adb shell uptime",
        "category": "系统信息",
        "description": "显示系统运行时间（开机时长）"
    },
    {
        "command": "adb shell free -m",
        "category": "系统信息",
        "description": "显示内存使用情况（单位 MB）"
    },
    {
        "command": "adb shell df -h",
        "category": "系统信息",
        "description": "显示分区使用情况（人类可读）"
    },
    {
        "command": "adb shell getprop",
        "category": "系统信息",
        "description": "列出所有系统属性"
    },
    {
        "command": "adb shell settings list global",
        "category": "系统信息",
        "description": "查看全局设置列表"
    },
    {
        "command": "adb shell service list",
        "category": "系统信息",
        "description": "列出所有系统服务"
    },

    # ---------- 截图录屏 ----------
    {
        "command": "adb shell screencap /sdcard/screenshot.png",
        "category": "截图录屏",
        "description": "截图并保存到设备"
    },
    {
        "command": "adb exec-out screencap -p > screenshot.png",
        "category": "截图录屏",
        "description": "截图直接保存到电脑（当前目录）"
    },
    {
        "command": "adb exec-out screencap -d 2 -p",
        "category": "截图录屏",
        "description": "截取副驾屏（索引2）并输出 PNG"
    },
    {
        "command": "adb shell screenrecord /sdcard/video.mp4",
        "category": "截图录屏",
        "description": "录屏（可加 --time-limit 设置时长，--bit-rate 码率）"
    },

    # ---------- 进程管理 ----------
    {
        "command": "adb shell ps",
        "category": "进程管理",
        "description": "查看正在运行的进程"
    },
    {
        "command": "adb shell top",
        "category": "进程管理",
        "description": "实时查看进程资源占用（CPU、内存）"
    },
    {
        "command": "adb shell kill <pid>",
        "category": "进程管理",
        "description": "终止指定 PID 的进程"
    },

    # ---------- 网络 ----------
    {
        "command": "adb shell netstat",
        "category": "网络",
        "description": "查看网络连接状态"
    },
    {
        "command": "adb shell ifconfig",
        "category": "网络",
        "description": "查看网络接口信息（IP、MAC 等）"
    },
    {
        "command": "adb shell ip addr show wlan0",
        "category": "网络",
        "description": "查看 WiFi IP 地址"
    },
    {
        "command": "adb shell dumpsys wifi",
        "category": "网络",
        "description": "查看 WiFi 详细信息（状态、信号、SSID 等）"
    },

    # ---------- 输入模拟 ----------
    {
        "command": "adb shell input tap <x> <y>",
        "category": "输入模拟",
        "description": "模拟点击屏幕指定坐标"
    },
    {
        "command": "adb shell input swipe <x1> <y1> <x2> <y2>",
        "category": "输入模拟",
        "description": "模拟滑动（可加持续时间）"
    },
    {
        "command": "adb shell input text <string>",
        "category": "输入模拟",
        "description": "模拟输入文本（支持英文及部分符号）"
    },
    {
        "command": "adb shell input keyevent <keycode>",
        "category": "输入模拟",
        "description": "模拟按键（如 KEYCODE_HOME、KEYCODE_BACK）"
    },

    # ---------- 发送广播 ----------
    {
        "command": "adb shell am broadcast -a action_notify_power_status --ei power_status {power}",
        "category": "发送广播",
        "description": "设置电量百分比（power=0-9: 极低；10-14: 低；≥15: 显示充电）"
    },
    {
        "command": "adb shell am broadcast -a action_set_distance --ei distance {distance}",
        "category": "发送广播",
        "description": "设置续航圈/新能源剩余里程（distance 为数值，如 5000）"
    },
    {
        "command": "adb shell am broadcast -a signal --ei hd_map_version {code}\n"
                   "adb shell am broadcast -a signal --ei ad_current_status 3\n"
                   "adb shell am broadcast -a signal --ei acc_engage 1\n"
                   "adb shell am broadcast -a signal --ei hd_status 1\n"
                   "adb shell am broadcast -a signal --es alc_status on",
        "category": "发送广播",
        "description": "触发车道级/人机共驾（code 为版本号，如 24Q4）"
    },
    {
        "command": "adb shell am broadcast -a signal --ei ad_current_status 0\n"
                   "adb shell am broadcast -a signal --ei acc_engage 0\n"
                   "adb shell am broadcast -a signal --es alc_status off",
        "category": "发送广播",
        "description": "解除车道级/人机共驾"
    },

    # ---------- 应用权限 ----------
    {
        "command": "adb shell setenforce 0",
        "category": "应用权限",
        "description": "关闭 SELinux（谨慎使用）"
    },
    {
        "command": "adb shell setenforce 1",
        "category": "应用权限",
        "description": "打开 SELinux"
    },
]

# 建立按分类的索引
ADB_COMMANDS_BY_CATEGORY = {}
for cmd in ADB_COMMANDS:
    category = cmd["category"]
    ADB_COMMANDS_BY_CATEGORY.setdefault(category, []).append(cmd)