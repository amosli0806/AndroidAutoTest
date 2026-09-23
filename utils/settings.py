# utils/settings.py
"""
设置管理模块
负责加载和保存应用程序的设置（如输出目录、主题模式等）
设置存储在 JSON 配置文件中
"""
import json
import os

from utils.app_paths import data_path

# 配置文件路径（统一放在程序目录的 data/ 目录下）
def get_config_path():
    """获取配置文件路径"""
    return data_path('config.json')


CONFIG_FILE = get_config_path()

# 默认输出目录（用户桌面上的 ADB_Output 文件夹）
DEFAULT_OUTPUT_DIR = os.path.expanduser("~/Desktop")

# 主题模式常量
THEME_MODE_SYSTEM = "system"
THEME_MODE_LIGHT = "light"
THEME_MODE_DARK = "dark"
DEFAULT_THEME_MODE = THEME_MODE_SYSTEM

# 默认快捷键映射（按功能模块分组）
DEFAULT_SHORTCUTS = {
    # ---------- 全局 ----------
    "help_center": "F1",
    "open_settings": "F2",
    "refresh_devices": "F5",
    "toggle_log_panel": "Ctrl+L",
    "restore_ime": "Ctrl+Shift+K",

    # ---------- 自动化编辑页 ----------
    "toggle_record": "Ctrl+Shift+R",
    "generate_steps": "Ctrl+Shift+G",
    "focus_step_search": "Ctrl+F",
    "import_cases": "Ctrl+Shift+I",
    "export_cases": "Ctrl+Shift+E",

    # ---------- 自动化执行页 ----------
    "execute_cases": "F9",
    "toggle_select_all": "Ctrl+Shift+A",
    "save_suite": "Ctrl+S",
    "delete_suite": "Ctrl+Shift+D",
    "generate_report": "Ctrl+P",

    # ---------- ADB 工具箱页 ----------
    "adb_search": "Ctrl+F",
    "adb_add_command": "Ctrl+N",
    "adb_edit_command": "Ctrl+E",
    "adb_delete_command": "Ctrl+D",
    "adb_import_commands": "Ctrl+Shift+I",
    "adb_export_commands": "Ctrl+Shift+E",
    "adb_execute_selected": "F9",

    # ---------- ADB 快捷功能 ----------
    "adb_wireless": "Alt+W",
    "adb_scrcpy": "Alt+P",
    "adb_install": "Alt+I",
    "adb_push": "Alt+U",
    "adb_device_info": "Alt+H",
    "adb_hprof": "Alt+J",
    "adb_monkey": "Alt+M",
    "adb_crash": "Alt+L",
    "adb_anr": "Alt+R",
    "adb_md5": "Alt+Q",
    "adb_weak_network": "Alt+Y",
    "adb_packet": "Alt+B",

    # ---------- 性能检测页 ----------
    "perf_toggle_monitor": "F9",
    "perf_toggle_pause": "F6",
    "perf_export_csv": "Ctrl+Shift+E",
    "perf_save_baseline": "Ctrl+B",

    # ---------- 应用元素库页 ----------
    "elem_add": "Ctrl+N",
    "elem_edit": "Ctrl+E",
    "elem_delete": "Delete",
    "elem_verify": "Ctrl+T",
}

class Settings:
    """设置管理类，使用类方法操作配置文件"""

    @classmethod
    def load(cls):
        """加载配置文件，返回字典"""
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                return {}
        return {}

    @classmethod
    def save(cls, settings_dict):
        """保存设置字典到配置文件"""
        try:
            os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(settings_dict, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"保存配置失败: {e}")

    # ---------- 输出目录 ----------
    @classmethod
    def get_output_dir(cls):
        """获取输出目录，若不存在则创建默认目录"""
        settings = cls.load()
        path = settings.get('output_dir', DEFAULT_OUTPUT_DIR)
        # 确保默认目录存在
        try:
            os.makedirs(DEFAULT_OUTPUT_DIR, exist_ok=True)
        except:
            pass
        # 如果保存的路径不存在，回退到默认目录
        if not os.path.exists(path):
            if path != DEFAULT_OUTPUT_DIR:
                cls.set_output_dir(DEFAULT_OUTPUT_DIR)
            return DEFAULT_OUTPUT_DIR
        return path

    @classmethod
    def set_output_dir(cls, path):
        """设置输出目录并保存"""
        settings = cls.load()
        settings['output_dir'] = path
        cls.save(settings)

    # ---------- 主题模式 ----------
    @classmethod
    def get_theme_mode(cls) -> str:
        """获取当前生效的主题模式：'light' / 'dark'
        若用户配置为 'system'，会根据系统当前颜色方案动态返回 'light' 或 'dark'。"""
        raw = cls.get_raw_theme_mode()
        if raw == THEME_MODE_SYSTEM:
            return cls._detect_system_theme()
        return raw

    @classmethod
    def get_raw_theme_mode(cls) -> str:
        """获取用户配置的原始主题模式：'system' / 'light' / 'dark'
        供设置对话框展示用（'跟随系统' 时下拉框应显示该项）。"""
        settings = cls.load()
        mode = settings.get('theme_mode', DEFAULT_THEME_MODE)
        if mode not in (THEME_MODE_SYSTEM, THEME_MODE_LIGHT, THEME_MODE_DARK):
            mode = DEFAULT_THEME_MODE
        return mode

    @classmethod
    def _detect_system_theme(cls) -> str:
        """检测操作系统当前的颜色方案，返回 'light' 或 'dark'"""
        try:
            from PyQt6.QtWidgets import QApplication
            from PyQt6.QtCore import Qt
            app = QApplication.instance()
            if app is None:
                return THEME_MODE_LIGHT
            # Qt 6.5+ 提供的接口
            try:
                scheme = app.styleHints().colorScheme()
                if scheme == Qt.ColorScheme.Dark:
                    return THEME_MODE_DARK
                elif scheme == Qt.ColorScheme.Light:
                    return THEME_MODE_LIGHT
            except AttributeError:
                pass
            # 兜底：根据 palette 亮度判断
            try:
                from PyQt6.QtGui import QPalette
                window_color = app.palette().color(QPalette.ColorRole.Window)
                if window_color.lightness() < 128:
                    return THEME_MODE_DARK
            except Exception:
                pass
        except Exception:
            pass
        return THEME_MODE_LIGHT

    @classmethod
    def set_theme_mode(cls, mode: str):
        """设置主题模式：'system' / 'light' / 'dark'"""
        if mode not in (THEME_MODE_SYSTEM, THEME_MODE_LIGHT, THEME_MODE_DARK):
            raise ValueError(f"无效的主题模式: {mode}")
        settings = cls.load()
        settings['theme_mode'] = mode
        cls.save(settings)

    # ---------- 壁纸相关 ----------
    @classmethod
    def get_wallpaper_path(cls):
        settings = cls.load()
        return settings.get('wallpaper_path', '')

    @classmethod
    def set_wallpaper_path(cls, path):
        settings = cls.load()
        settings['wallpaper_path'] = path
        cls.save(settings)

    @classmethod
    def get_wallpaper_opacity(cls):
        settings = cls.load()
        return settings.get('wallpaper_opacity', 100)  # 0-100

    @classmethod
    def set_wallpaper_opacity(cls, opacity):
        settings = cls.load()
        settings['wallpaper_opacity'] = opacity
        cls.save(settings)

    # ---------- 快捷键 ----------
    @classmethod
    def get_shortcuts(cls):
        """获取快捷键映射，若未设置则用默认值兜底"""
        settings = cls.load()
        saved = settings.get('shortcuts', {})
        result = DEFAULT_SHORTCUTS.copy()
        result.update(saved)
        return result

    @classmethod
    def set_shortcut(cls, action_name, key_sequence):
        """保存单个快捷键"""
        settings = cls.load()
        if 'shortcuts' not in settings:
            settings['shortcuts'] = {}
        settings['shortcuts'][action_name] = key_sequence
        cls.save(settings)

    @classmethod
    def reset_shortcuts(cls):
        """重置所有快捷键为默认值"""
        settings = cls.load()
        settings['shortcuts'] = DEFAULT_SHORTCUTS.copy()
        cls.save(settings)

    # ---------- 捕虫师：自定义指令 ----------
    @classmethod
    def get_custom_commands(cls):
        settings = cls.load()
        return settings.get('custom_commands', [])

    @classmethod
    def set_custom_commands(cls, commands):
        settings = cls.load()
        settings['custom_commands'] = commands
        cls.save(settings)

    # ---------- AI 辅助 ----------
    @classmethod
    def get_ai_config(cls) -> dict:
        settings = cls.load()
        return {
            "enabled": settings.get("ai_enabled", False),
            "api_key": settings.get("ai_api_key", ""),
            "model": settings.get("ai_model", "gpt-4o-mini"),
            "base_url": settings.get("ai_base_url", "https://api.openai.com/v1"),
            "timeout": settings.get("ai_timeout", 30),
        }

    @classmethod
    def set_ai_config(cls, **kwargs):
        settings = cls.load()
        key_map = {
            "enabled": "ai_enabled", "api_key": "ai_api_key",
            "model": "ai_model", "base_url": "ai_base_url",
            "timeout": "ai_timeout",
        }
        for k, v in kwargs.items():
            if k in key_map:
                settings[key_map[k]] = v
        cls.save(settings)

    @classmethod
    def is_ai_ready(cls) -> bool:
        """AI 功能是否可用：开关打开 + 有 key"""
        cfg = cls.get_ai_config()
        return bool(cfg["enabled"] and cfg["api_key"])

    # ---------- 捕虫师：UI 状态 ----------
    @classmethod
    def save_display_command_ids(cls, ids):
        settings = cls.load()
        settings['display_command_ids'] = ids
        cls.save(settings)

    @classmethod
    def load_display_command_ids(cls):
        settings = cls.load()
        return settings.get('display_command_ids')

    @classmethod
    def save_display_filter_mode(cls, mode):
        settings = cls.load()
        settings['display_filter_mode'] = mode
        cls.save(settings)

    @classmethod
    def load_display_filter_mode(cls):
        settings = cls.load()
        return settings.get('display_filter_mode', 'all')

    @classmethod
    def save_checked_commands(cls, ids):
        settings = cls.load()
        settings['checked_commands'] = ids
        cls.save(settings)

    @classmethod
    def load_checked_commands(cls):
        settings = cls.load()
        return settings.get('checked_commands', [])

    # ---------- 自动化编辑页：动作卡片显示哪些 ----------
    @classmethod
    def save_visible_action_cards(cls, types):
        """保存「动作卡片 ▾」里勾选的卡片类型（列表）；None = 全部显示"""
        settings = cls.load()
        settings['visible_action_cards'] = types
        cls.save(settings)

    @classmethod
    def load_visible_action_cards(cls):
        """读取勾选的卡片类型；**None = 从来没设置过**（= 全部显示）。

        注意空列表与 None 的区别：[] 是"用户把卡片全隐藏了"，要原样恢复；
        所以这里不能写成 `or None` 那种兜底。
        """
        settings = cls.load()
        value = settings.get('visible_action_cards')
        if isinstance(value, list):
            return [str(v) for v in value]
        return None

    # ---------- 站点私有目标（存在本机 data/config.json，不进公开仓库） ----------
    # 这类"具体测哪个应用"的值不该写死在代码里：公开仓库里只留通用默认值，
    # 各自的机器在本地配置里指定。
    @classmethod
    def get_target_package(cls) -> str:
        """被测应用包名（内存监控脚本、内存解析用它定位目标进程）。空 = 自动识别。"""
        settings = cls.load()
        return str(settings.get('target_package', '') or '').strip()

    @classmethod
    def set_target_package(cls, package: str):
        settings = cls.load()
        settings['target_package'] = str(package or '').strip()
        cls.save(settings)

    @classmethod
    def get_monkey_package(cls) -> str:
        """Monkey 面板里默认填入的包名（记住上次用的，省得每次重敲）。"""
        settings = cls.load()
        return str(settings.get('monkey_package', '') or '').strip()

    @classmethod
    def set_monkey_package(cls, package: str):
        settings = cls.load()
        settings['monkey_package'] = str(package or '').strip()
        cls.save(settings)
