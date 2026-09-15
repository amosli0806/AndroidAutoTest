# utils/serial_helper.py
"""序列号清洗工具：把设备序列号转换为安全的文件夹名"""
import re


def safe_serial(serial: str) -> str:
    """把设备序列号清洗成可作文件夹名的字符串。
    兼容 USB 序列号（如 DKS9K23xxx）和无线序列号（如 192.168.1.100:5555）。
    """
    if not serial:
        return "unknown"
    return re.sub(r'[\\/*?:"<>|]', '_', serial)