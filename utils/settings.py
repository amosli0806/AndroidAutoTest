# utils/settings.py
"""
设置管理模块
负责加载和保存应用程序的设置（如输出目录、主题模式等）
设置存储在 JSON 配置文件中
"""
import json
import os
import sys

# 配置文件路径（放在项目根目录）
def get_config_path():
    """获取配置文件路径"""
    if getattr(sys, 'frozen', False):
        # 打包环境：放在可执行文件所在目录
        base = os.path.dirname(sys.executable)
    else:
        # 开发环境：放在项目根目录（main.py 所在目录）
        # 获取 main.py 的绝对路径
        main_file = sys.argv[0]
        if not os.path.isabs(main_file):
            main_file = os.path.abspath(main_file)
        base = os.path.dirname(main_file)
    return os.path.join(base, 'config.json')

CONFIG_FILE = get_config_path()

# 默认输出目录（用户桌面上的 ADB_Output 文件夹）
DEFAULT_OUTPUT_DIR = os.path.expanduser("~/Desktop")

# 主题模式常量
THEME_MODE_SYSTEM = "system"
THEME_MODE_LIGHT = "light"
THEME_MODE_DARK = "dark"
DEFAULT_THEME_MODE = THEME_MODE_SYSTEM

# 默认快捷键映射（合并了捕虫师的默认值）
DEFAULT_SHORTCUTS = {
    "refresh_devices": "F5",
    "execute_selected": "M",
    "clear_log": "Delete",
    "search_commands": "Ctrl+F",
    "add_command": "Ctrl+N",
    "edit_command": "Ctrl+E",
    "delete_command": "Ctrl+D",
    "wireless": "Ctrl+W",
    "scrcpy": "Ctrl+P",
    "install_app": "Ctrl+I",
    "push_file": "Ctrl+U",
    "app_manager": "Ctrl+A",
    "device_info": "Ctrl+H",
    "hprof_dump": "Ctrl+J",
    "monkey": "Ctrl+M",
    "crash_log": "Ctrl+L",
    "anr_analyzer": "Ctrl+R",
    "md5_query": "Ctrl+Q",
    "weak_network": "Ctrl+Y",
    "export_commands": "Ctrl+Shift+E",
    "import_commands": "Ctrl+Shift+I",
    "help_center": "F1",
    "packet_capture": "Ctrl+G",
    "about": "",
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