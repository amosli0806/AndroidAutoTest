# services/preset_commands.py
"""捕虫师内置预设命令列表"""



PRESET_COMMANDS = [
    {
        "id": 1,  # 命令的唯一标识符，预设命令 ID 固定为 1-4
        "name": "权限获取",  # 命令名称
        "command_text": "root; remount",  # ADB 命令内容，多条命令用分号分隔
        "description": "获取root权限并remount",  # 命令描述
        "show_stop_button": True,  # 是否显示“停止”按钮（该命令可能持续运行）
        "is_preset": True  # 标记为预设命令
    },
    {
        "id": 2,
        "name": "logcat日志",
        # 先清除旧日志，然后开始抓取新日志并显示时间
        "command_text": "shell logcat -c; logcat -v time",
        "description": "清除旧日志并开始抓取新日志",
        "show_stop_button": True,
        "is_preset": True,
        "save_output": True,  # 新增：是否将命令输出保存到文件（例如日志保存到本地）
        "no_timeout": True  # 新增
    },
    {
        "id": 3,
        "name": "录屏",
        # 使用 screenrecord 命令录制 3 分钟视频，文件名包含时间戳
        "command_text": "shell screenrecord --time-limit 180 /sdcard/video_{timestamp}.mp4",
        "description": "录制3分钟视频",
        "show_stop_button": True,
        "is_preset": True
    },
    {
        "id": 4,
        "name": "截图",
        "command_text": "shell screencap /sdcard/screenshot_{timestamp}.png; pull /sdcard/screenshot_{timestamp}.png \"{output_dir}/{safe_device_serial}/screenshot_{timestamp}.png\"; shell rm /sdcard/screenshot_{timestamp}.png",
        "description": "截图并自动pull到本地（保存为 screenshot_时间戳.png）",
        "show_stop_button": True,
        "is_preset": True
    },
    {
        "id": 5,
        "name": "地图引擎日志",
        "command_text": "pull /sdcard/BaiduMapAuto/naviautoenginelog/ \"{output_dir}/{safe_device_serial}/\"",
        "description": "23M24M25M26M引擎日志",
        "show_stop_button": True,
        "is_preset": True,
        "no_timeout": True  # 新增
    },
    {
        "id": 6,
        "name": "车机日志27M（3DAA）",
        "command_text": "pull /mnt/vendor/log/Logcat \"{output_dir}/{safe_device_serial}/\"",
        "description": "27M车机日志",
        "show_stop_button": True,
        "is_preset": True,
        "no_timeout": True  # 新增
    },
    {
        "id": 7,
        "name": "车机日志26/27.5M（3A0W/30AW）",
        "command_text": "pull /data/persistlogs/hsaelog \"{output_dir}/{safe_device_serial}/\"",
        "description": "26M车机日志",
        "show_stop_button": True,
        "is_preset": True,
        "no_timeout": True  # 新增
    },
    {
        "id": 8,
        "name": "车机日志25M（33WA）",
        "command_text": "pull /data/hsaelog \"{output_dir}/{safe_device_serial}/\"",
        "description": "25M车机日志",
        "show_stop_button": True,
        "is_preset": True,
        "no_timeout": True  # 新增
    },
    {
        "id": 9,
        "name": "车机日志23/24M（30EA/31YA）",
        "command_text": "pull /data/misc/logd \"{output_dir}/{safe_device_serial}/\"",
        "description": "23M/24M车机日志",
        "show_stop_button": True,
        "is_preset": True,
        "no_timeout": True  # 新增
    },
    {
        "id": 10,
        "name": "TSU日志",
        "command_text": "pull /storage/emulated/0/cxlog \"{output_dir}/{safe_device_serial}/\"",
        "description": "TSU日志",
        "show_stop_button": True,
        "is_preset": True,
        "no_timeout": True  # 新增
    },
    {
        "id": 11,
        "name": "ANR/Crash/墓碑日志",
        "command_text": "pull /data/anr \"{output_dir}/{safe_device_serial}/\"; pull /storage/emulated/0/Android/data/com.baidu.naviauto/files/xcrash \"{output_dir}/{safe_device_serial}/\"; pull /data/tombstones \"{output_dir}/{safe_device_serial}/\";",
        "description": "ANR/Crash/墓碑日志",
        "show_stop_button": True,
        "is_preset": True,
        "no_timeout": True  # 新增
    },
    {
        "id": 12,
        "name": "原生设置页面",

        "command_text": "shell am start com.android.car.settings",
        "description": "原生设置页面",
        "show_stop_button": True,
        "is_preset": True
    },

]