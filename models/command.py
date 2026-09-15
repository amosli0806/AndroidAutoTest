# src/models/command.py
"""
命令数据模型模块
定义 Command 类，用于表示一个可执行的 ADB 命令，包含命令的名称、文本、描述、
定时/循环设置、是否显示停止按钮、是否为预设命令、是否保存输出等属性。
该类用于在程序内部统一表示命令，并提供与 JSON 相互转换的方法。
"""

class Command:
    """
    命令类，封装一条 ADB 命令的所有属性。
    可以是预设命令（内置的，不可修改部分属性）或自定义命令。
    """

    def __init__(self, id=None, name="", command_text="", description="",
                 interval=None, loop_count=None, show_stop_button=True,
                 is_preset=False, save_output=False, no_timeout=False):
        """
        初始化一个命令对象。
        :param id: 命令的唯一标识符（整数）。预设命令的 ID 固定为 1-4，自定义命令 ID 动态生成。
        :param name: 命令的显示名称（例如“截屏”）。
        :param command_text: 实际的 ADB 命令文本（例如 "shell screencap -p /sdcard/screen.png"）。
                             支持多条命令用分号分隔。
        :param description: 命令的详细描述，用于提示用户。
        :param interval: 定时执行的间隔时间（秒）。如果为 None 则表示不启用定时执行。
        :param loop_count: 循环执行的次数。如果为 None 则表示不限制次数（无限循环），
                           需要配合 interval 使用；如果 interval 也为 None，则表示只执行一次。
        :param show_stop_button: 布尔值，指示是否在命令列表项中显示“停止”按钮。
                                 通常对于会持续运行的命令（如 logcat、录屏）设为 True。
        :param is_preset: 布尔值，是否为预置命令。预置命令从 PRESET_COMMANDS 加载，
                          不可修改定时/循环设置，且不允许删除。
        :param save_output: 布尔值，是否将命令的输出保存到文件。默认为 False。
        :param no_timeout: 布尔值，是否取消30秒超时限制。默认为 False。
        """
        self.id = id
        self.name = name
        self.command_text = command_text
        self.description = description
        self.interval = interval
        self.loop_count = loop_count
        self.show_stop_button = show_stop_button
        self.is_preset = is_preset
        self.save_output = save_output
        self.no_timeout = no_timeout

    def to_dict(self):
        """
        将 Command 对象转换为字典格式，便于序列化为 JSON。
        :return: 包含所有属性键值对的字典。
        """
        return {
            "id": self.id,
            "name": self.name,
            "command_text": self.command_text,
            "description": self.description,
            "interval": self.interval,
            "loop_count": self.loop_count,
            "show_stop_button": self.show_stop_button,
            "is_preset": self.is_preset,
            "save_output": self.save_output,
            "no_timeout": self.no_timeout
        }