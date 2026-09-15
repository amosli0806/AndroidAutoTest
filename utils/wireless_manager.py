# utils/wireless_manager.py
import subprocess
import re
import sys
import socket
import time


def _get_local_ip():
    """获取本机局域网 IP"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


def _same_subnet(ip1, ip2):
    if not ip1 or not ip2:
        return False
    parts1 = ip1.split('.')
    parts2 = ip2.split('.')
    if len(parts1) != 4 or len(parts2) != 4:
        return False
    return parts1[0] == parts2[0] and parts1[1] == parts2[1]


def _ping_device(ip, timeout=3):
    param = '-n' if sys.platform == 'win32' else '-c'
    if sys.platform == 'win32':
        cmd = ['ping', param, '1', '-w', str(timeout * 1000), ip]
        creationflags = subprocess.CREATE_NO_WINDOW
    else:
        cmd = ['ping', param, '1', '-W', str(timeout), ip]
        creationflags = 0
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout + 1,
                                creationflags=creationflags)
        return result.returncode == 0
    except Exception:
        return False


def _run_adb_command(cmd, timeout=10):
    """执行 ADB 命令并返回结果，隐藏窗口"""
    common_kwargs = {
        'shell': True, 'capture_output': True, 'text': True,
        'timeout': timeout, 'encoding': 'utf-8', 'errors': 'replace'
    }
    if sys.platform == 'win32':
        common_kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
    return subprocess.run(cmd, **common_kwargs)


class WirelessManager:
    """无线 ADB 连接工具（无状态，不需要实例化时传设备管理器）"""

    def get_device_ip(self, serial):
        """获取设备IP，优先从 WiFi 接口获取，兼容 Android 8-14"""
        try:
            # 常见 WiFi 接口名（含车机特殊名称）
            wifi_ifaces = ['wlan0', 'wlan1', 'wifi0', 'wlan2', 'eth0', 'eth1']

            # 方法1：尝试从指定接口直接获取 IP（使用 ip addr）
            for iface in wifi_ifaces:
                cmd = f"adb -s {serial} shell ip addr show {iface}"
                result = _run_adb_command(cmd, timeout=5)
                if result.returncode == 0:
                    match = re.search(r'inet (\d+\.\d+\.\d+\.\d+)/', result.stdout)
                    if match:
                        ip = match.group(1)
                        if not ip.startswith('127.'):
                            return ip

            # 方法2：扫描所有接口
            commands = ["ip addr", "ifconfig", "netcfg"]
            all_ips = []
            wifi_ips = []
            for cmd_base in commands:
                cmd = f"adb -s {serial} shell {cmd_base}"
                result = _run_adb_command(cmd, timeout=5)
                if result.returncode != 0:
                    continue
                output = result.stdout

                if cmd_base == "ip addr":
                    lines = output.splitlines()
                    current_iface = None
                    for line in lines:
                        if line.strip() and not line.startswith(' '):
                            iface_match = re.match(r'\d+:\s+(\S+):', line)
                            if iface_match:
                                current_iface = iface_match.group(1)
                        elif 'inet ' in line and current_iface:
                            ip_match = re.search(r'inet (\d+\.\d+\.\d+\.\d+)/', line)
                            if ip_match:
                                ip = ip_match.group(1)
                                if ip.startswith('127.'):
                                    continue
                                all_ips.append(ip)
                                if current_iface in wifi_ifaces:
                                    wifi_ips.append(ip)
                elif cmd_base == "ifconfig":
                    blocks = output.split('\n\n')
                    for block in blocks:
                        iface_match = re.match(r'(\S+):', block)
                        if not iface_match:
                            continue
                        iface = iface_match.group(1)
                        ip_match = re.search(r'inet addr:(\d+\.\d+\.\d+\.\d+)', block) or \
                                   re.search(r'inet (\d+\.\d+\.\d+\.\d+)', block)
                        if ip_match:
                            ip = ip_match.group(1)
                            if ip.startswith('127.'):
                                continue
                            all_ips.append(ip)
                            if iface in wifi_ifaces:
                                wifi_ips.append(ip)
                elif cmd_base == "netcfg":
                    for line in output.splitlines():
                        parts = line.split()
                        if len(parts) >= 3:
                            iface = parts[0]
                            ip = parts[2]
                            if ip.startswith('127.') or ip == '0.0.0.0':
                                continue
                            if re.match(r'\d+\.\d+\.\d+\.\d+', ip):
                                all_ips.append(ip)
                                if iface in wifi_ifaces:
                                    wifi_ips.append(ip)

            if wifi_ips:
                return wifi_ips[0]
            if not all_ips:
                return None

            local_ip = _get_local_ip()
            for ip in all_ips:
                if local_ip and _same_subnet(local_ip, ip):
                    return ip
            for ip in all_ips:
                if ip.startswith('192.168.'):
                    return ip
            for ip in all_ips:
                if ip.startswith('10.'):
                    return ip
                if ip.startswith('172.'):
                    parts = ip.split('.')
                    if len(parts) >= 2 and 16 <= int(parts[1]) <= 31:
                        return ip
            return all_ips[0]

        except Exception as e:
            print(f"获取IP异常: {e}")
            return None

    def connect(self, ip, port=5555, serial=None):
        # 1. 检查同网段
        local_ip = _get_local_ip()
        if local_ip and not _same_subnet(local_ip, ip):
            return False, f"电脑 IP ({local_ip}) 与设备 IP ({ip}) 不在同一局域网段，请检查网络连接。"

        # 2. ping 检测
        if not _ping_device(ip):
            return False, f"无法 ping 通设备 IP ({ip})，请确认设备已连接到同一 Wi-Fi 且 IP 正确。"

        # 3. 如果有线设备序列号存在，执行 tcpip 切换
        if serial:
            check_cmd = f"adb -s {serial} shell getprop service.adb.tcp.port"
            check_result = _run_adb_command(check_cmd, timeout=5)
            if check_result.returncode == 0 and check_result.stdout.strip() == str(port):
                pass
            else:
                tcpip_cmd = f"adb -s {serial} tcpip {port}"
                tcpip_result = _run_adb_command(tcpip_cmd, timeout=10)
                if tcpip_result.returncode != 0:
                    return False, f"设置 TCP/IP 模式失败: {tcpip_result.stderr}"
                time.sleep(5)
                for _ in range(3):
                    check_result = _run_adb_command(check_cmd, timeout=5)
                    if check_result.returncode == 0 and check_result.stdout.strip() == str(port):
                        break
                    time.sleep(2)
                else:
                    return False, "设备未能成功切换到 TCP/IP 模式，请确保设备已通过 USB 连接并授权。"

        # 4. 执行 connect
        connect_cmd = f"adb connect {ip}:{port}"
        connect_result = _run_adb_command(connect_cmd, timeout=30)
        output = connect_result.stdout + connect_result.stderr
        if "connected to" in output or "already connected" in output:
            return True, output.strip()
        else:
            if "unable to connect" in output:
                return False, "无法连接，请确认设备 IP 正确且设备已开启无线调试（Android 11+ 需在开发者选项中开启无线调试并配对）。"
            elif "Connection refused" in output:
                return False, "连接被拒绝，请确认设备已执行 tcpip 命令或已开启无线调试端口。"
            else:
                return False, output.strip()

    def disconnect(self, ip=None):
        try:
            if ip:
                cmd = f"adb disconnect {ip}:5555"
            else:
                cmd = "adb disconnect"
            result = _run_adb_command(cmd, timeout=10)
            return True, result.stdout.strip()
        except Exception as e:
            return False, str(e)