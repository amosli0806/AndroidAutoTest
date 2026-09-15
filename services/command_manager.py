# services/command_manager.py
"""命令管理器：管理所有命令（预设 + 自定义）的加载、保存、增删改查"""
import json
import os

from models.command import Command
from services.preset_commands import PRESET_COMMANDS
from services.adb_commands import ADB_COMMANDS
from utils.settings import Settings


class CommandManager:

    def __init__(self):
        self.commands = []
        self.load()

    def load(self):
        """加载预设 + 自定义命令"""
        PRESET_IDS = {c['id'] for c in PRESET_COMMANDS if c.get('is_preset')}

        # 1. 预设命令
        self.commands = [Command(**cmd) for cmd in PRESET_COMMANDS]
        for cmd in self.commands:
            if cmd.is_preset:
                cmd.interval = None
                cmd.loop_count = None

        # 2. 自定义命令（从 Settings 读）
        custom_list = Settings.get_custom_commands()
        for cmd_data in custom_list:
            if cmd_data.get('id') in PRESET_IDS:
                continue
            if cmd_data.get('is_preset', False):
                continue
            self.commands.append(Command(**cmd_data))

    def save(self):
        """只保存自定义命令"""
        custom = [cmd.to_dict() for cmd in self.commands if not cmd.is_preset]
        Settings.set_custom_commands(custom)

    def get_all_commands(self):
        return self.commands

    def add_command(self, command):
        existing_ids = {c.id for c in self.commands}
        new_id = max(existing_ids) + 1 if existing_ids else 1
        if new_id <= 4:
            new_id = 5
        command.id = new_id
        command.is_preset = False
        self.commands.append(command)
        self.save()

    def update_command(self, cmd_id, updated_command):
        for i, cmd in enumerate(self.commands):
            if cmd.id == cmd_id:
                updated_command.id = cmd_id
                updated_command.is_preset = cmd.is_preset
                self.commands[i] = updated_command
                self.save()
                break

    def delete_command(self, cmd_id):
        self.commands = [c for c in self.commands if c.id != cmd_id or c.is_preset]
        self.save()

    def get_preset_commands(self):
        return [c for c in self.commands if c.is_preset]

    def get_custom_commands(self):
        return [c for c in self.commands if not c.is_preset]

    def search_adb_commands(self, keyword):
        if not keyword:
            return []
        kw = keyword.lower()
        results = []
        for cmd in ADB_COMMANDS:
            if (kw in cmd["command"].lower() or
                    kw in cmd["category"].lower() or
                    kw in cmd["description"].lower()):
                results.append(cmd.copy())
        return results

    def search_all(self, keyword):
        if not keyword:
            return {
                "preset": self.get_preset_commands(),
                "custom": self.get_custom_commands(),
                "adb_library": []
            }

        kw = keyword.lower()
        preset_results = [
            c for c in self.commands
            if c.is_preset and (kw in c.name.lower() or kw in c.description.lower())
        ]
        custom_results = [
            c for c in self.commands
            if not c.is_preset and (
                kw in c.name.lower() or
                kw in c.description.lower() or
                kw in c.command_text.lower()
            )
        ]
        return {
            "preset": preset_results,
            "custom": custom_results,
            "adb_library": self.search_adb_commands(keyword)
        }

    def export_custom_commands(self, file_path):
        custom = [cmd.to_dict() for cmd in self.commands if not cmd.is_preset]
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(custom, f, indent=2, ensure_ascii=False)

    def import_custom_commands(self, file_path, merge=True):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                imported = json.load(f)
            if not isinstance(imported, list):
                return 0, ["文件格式错误：应为命令列表"]

            existing_ids = {c.id for c in self.commands}
            if not merge:
                self.commands = [c for c in self.commands if c.is_preset]
                existing_ids = {c.id for c in self.commands}

            success = 0
            errors = []
            for cmd_dict in imported:
                if cmd_dict.get('is_preset', False):
                    continue
                if cmd_dict['id'] in existing_ids:
                    if merge:
                        errors.append(f"ID {cmd_dict['id']} ({cmd_dict['name']}) 已存在，跳过")
                        continue
                    else:
                        new_id = max(existing_ids) + 1 if existing_ids else 1
                        if new_id <= 4:
                            new_id = 5
                        cmd_dict['id'] = new_id
                cmd = Command(**cmd_dict)
                cmd.is_preset = False
                self.commands.append(cmd)
                existing_ids.add(cmd.id)
                success += 1
            self.save()
            return success, errors
        except Exception as e:
            return 0, [str(e)]