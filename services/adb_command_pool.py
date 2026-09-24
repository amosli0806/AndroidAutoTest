# services/adb_command_pool.py
"""
ADB 命令并发执行池
职责：
1. 接收命令提交，分发给线程池执行（上限 10）
2. 转发信号（带 tag 区分来源：'main' 主项目 / 'user' 捕虫师用户）
3. 提供互斥检查（主项目执行中时禁用高风险命令）

不负责：
- 设备管理（由 DeviceService 统一处理）
"""

import subprocess
import threading
import os
import sys
import shlex
from datetime import datetime
import re
import time

from PyQt6.QtCore import QObject, pyqtSignal, QThreadPool, QRunnable

from utils.adb_path import get_adb_path
from utils import log_colors

# 并发上限
MAX_CONCURRENT_TASKS = 10


def _get_timestamp() -> str:
    """形如 14:23:05.123 的时间戳"""
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


# ============================================================
# 信号集合
# ============================================================
class CommandSignals(QObject):
    """命令执行过程中的信号（都带 tag）"""
    started = pyqtSignal(int, str, str)              # cmd_id, cmd_name, tag
    output = pyqtSignal(str, str)                    # text, tag
    error = pyqtSignal(str, str)                     # text, tag
    finished = pyqtSignal(int, str, bool, str)       # cmd_id, cmd_name, success, tag
    stopped = pyqtSignal(int, str, str)              # cmd_id, cmd_name, tag


# ============================================================
# 命令执行任务
# ============================================================
class AdbCommandRunnable(QRunnable):
    """在线程池中执行具体的 ADB 命令"""

    LEVEL_INFO = 0
    LEVEL_SUCCESS = 1
    LEVEL_WARNING = 2
    LEVEL_ERROR = 3
    LEVEL_RESULT = 4

    def __init__(self, command, device_serial, output_dir, signals, tag='user'):
        super().__init__()
        self.command = command
        self.tag = tag                                    # ← 新增
        self.device_serial = device_serial
        self.safe_device_serial = re.sub(r'[\\/*?:"<>|]', '_', device_serial or 'unknown')
        self.output_dir = output_dir
        self.signals = signals
        self.adb = get_adb_path()                         # ← 统一 adb 路径
        self.process = None
        self._is_running = True
        self._stopped = False
        self.output_file = None
        self.is_logcat = ("logcat" in self.command.name.lower()) or ("logcat" in self.command.command_text.lower())
        self.is_screenrecord = ("录屏" in self.command.name) or ("screenrecord" in self.command.command_text)
        self.is_monkey = ("monkey" in self.command.name.lower()) or ("monkey" in self.command.command_text.lower())
        self._device_video_path = None
        self._local_video_path = None
        self._video_pulled = False
        self._manually_stopped = False
        self._finished_emitted = False
        self._is_multi_command = len([c.strip() for c in command.command_text.split(';') if c.strip()]) > 1
        self._output_lines = []

        if command.save_output:
            self._prepare_output_file()

    # ---------- 工具 ----------
    def _format_message(self, level, text):
        icons = {
            self.LEVEL_INFO: "ℹ️", self.LEVEL_SUCCESS: "✅",
            self.LEVEL_WARNING: "⚠️", self.LEVEL_ERROR: "❌",
            self.LEVEL_RESULT: "🏁",
        }
        # 颜色跟随主题：原本写死的深色系只在白底上可读，深色面板上会看不清
        levels = {
            self.LEVEL_INFO: log_colors.INFO,
            self.LEVEL_SUCCESS: log_colors.SUCCESS,
            self.LEVEL_WARNING: log_colors.WARNING,
            self.LEVEL_ERROR: log_colors.ERROR,
            self.LEVEL_RESULT: log_colors.RESULT,
        }
        icon = icons.get(level, "")
        color = log_colors.log_color(levels.get(level, log_colors.INFO))
        return f"<span style='color:{color};'>{icon} {text}</span>"

    def _emit_finished(self, success):
        """安全发射 finished，只发一次"""
        if not self._finished_emitted:
            self._finished_emitted = True
            self.signals.finished.emit(self.command.id, self.command.name, success, self.tag)

    # ---------- 输出文件 ----------
    def _prepare_output_file(self):
        try:
            device_dir = os.path.join(self.output_dir, self.safe_device_serial)
            os.makedirs(device_dir, exist_ok=True)
            safe_name = re.sub(r'[\\/*?:"<>|]', "", self.command.name)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filepath = os.path.join(device_dir, f"{safe_name}_{timestamp}.log")
            self.output_file = open(filepath, 'a', encoding='utf-8', errors='replace')
            self.output_file.write(f"===== 命令启动于 {_get_timestamp()} =====\n")
            self.output_file.flush()
        except Exception as e:
            self.signals.error.emit(
                f"[{self.command.name}] 无法创建输出文件: {str(e)}", self.tag
            )

    # ---------- 录屏相关：获取 PID ----------
    def _get_screenrecord_pid(self):
        commands = [
            f'"{self.adb}" -s {self.device_serial} shell ps -e | grep screenrecord',
            f'"{self.adb}" -s {self.device_serial} shell ps -A | grep screenrecord',
            f'"{self.adb}" -s {self.device_serial} shell ps | grep screenrecord',
            f'"{self.adb}" -s {self.device_serial} shell pgrep screenrecord',
            f'"{self.adb}" -s {self.device_serial} shell pidof screenrecord',
        ]
        for cmd in commands:
            try:
                result = subprocess.run(
                    cmd, shell=True, capture_output=True, text=True, timeout=5,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
                )
                if result.returncode == 0 and result.stdout.strip():
                    output = result.stdout.strip()
                    if "pgrep" in cmd or "pidof" in cmd:
                        pid = output.split()[0] if output else None
                        if pid and pid.isdigit():
                            return pid
                    for line in output.splitlines():
                        parts = line.split()
                        if len(parts) >= 2:
                            if parts[0].isdigit():
                                return parts[0]
                            if parts[1].isdigit():
                                return parts[1]
            except Exception as e:
                self.signals.error.emit(
                    f"[{self.command.name}] 获取 PID 异常: {e}", self.tag
                )
                continue
        return None

    def _stop_screenrecord_gracefully(self):
        pid = self._get_screenrecord_pid()
        if not pid:
            self.signals.error.emit(
                f"[{self.command.name}] 无法获取录屏进程 PID，将强制终止", self.tag
            )
            return False
        try:
            kill_cmd = f'"{self.adb}" -s {self.device_serial} shell kill -2 {pid}'
            subprocess.run(kill_cmd, shell=True, timeout=3,
                           creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
            for _ in range(20):
                time.sleep(0.5)
                check_cmd = f'"{self.adb}" -s {self.device_serial} shell ps -e | grep {pid}'
                check_result = subprocess.run(
                    check_cmd, shell=True, capture_output=True, text=True, timeout=3,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
                )
                if check_result.returncode != 0 or not check_result.stdout:
                    return True
            self.signals.error.emit(
                f"[{self.command.name}] 等待录屏进程退出超时，将强制终止", self.tag
            )
            return False
        except Exception as e:
            self.signals.error.emit(
                f"[{self.command.name}] 优雅停止异常: {e}", self.tag
            )
            return False

    # ---------- 停止 ----------
    def stop(self):
        if self._stopped:
            return
        self._is_running = False
        self._stopped = True
        self._manually_stopped = True

        # 立即发射停止信号，让 UI 更新
        self.signals.stopped.emit(self.command.id, self.command.name, self.tag)

        if self.output_file:
            try:
                self.output_file.close()
            except Exception:
                pass
            self.output_file = None

        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
                if not self.process.wait(timeout=2):
                    self.process.kill()
                    self.process.wait(timeout=1)
            except Exception:
                if self.process.poll() is None:
                    self.process.kill()

        if "logcat" in self.command.name.lower():
            threading.Thread(target=self._clean_remote_process, daemon=True).start()

        if self.is_screenrecord and self._device_video_path and not self._video_pulled:
            self._stop_screenrecord_gracefully()
            if self.process and self.process.poll() is None:
                try:
                    self.process.terminate()
                    self.process.wait(timeout=2)
                except Exception:
                    pass
            threading.Thread(
                target=self._pull_video,
                args=(self._device_video_path, self._local_video_path),
                daemon=True,
            ).start()
        else:
            self._emit_finished(True)

    # ---------- 清理远端 logcat 进程 ----------
    def _clean_remote_process(self):
        try:
            result = subprocess.run(
                [self.adb, "-s", self.device_serial, "shell", "ps", "-e", "-o", "pid,cmd"],
                capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
                encoding='utf-8', errors='replace',
            )
            if result.returncode != 0:
                self.signals.error.emit(f"[清理] 获取进程列表失败: {result.stderr}", self.tag)
                return

            pids_to_kill = []
            for line in result.stdout.splitlines():
                if "PID" in line:
                    continue
                if "logcat" in line.lower() or "hilogcat" in line.lower():
                    parts = line.strip().split(maxsplit=1)
                    if parts and parts[0].isdigit():
                        pids_to_kill.append(parts[0])

            if not pids_to_kill:
                return

            for pid in pids_to_kill:
                kill_result = subprocess.run(
                    [self.adb, "-s", self.device_serial, "shell", "kill", "-9", pid],
                    timeout=2, capture_output=True, text=True,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
                    encoding='utf-8', errors='replace',
                )
                if kill_result.returncode != 0:
                    err_msg = kill_result.stderr.strip()
                    if "Operation not permitted" not in err_msg and "Permission denied" not in err_msg:
                        if err_msg:
                            self.signals.error.emit(f"[清理] 终止进程 {pid} 失败: {err_msg}", self.tag)
        except Exception as e:
            self.signals.error.emit(f"[清理] 异常: {str(e)}", self.tag)

    # ---------- 主 run ----------
    def run(self):
        success = True
        try:
            self.signals.started.emit(self.command.id, self.command.name, self.tag)
            loop_count = self.command.loop_count
            interval = self.command.interval
            count = 0
            while self._is_running:
                if loop_count is not None and count >= loop_count:
                    break
                timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
                if self.is_screenrecord:
                    success = self._run_screenrecord(timestamp)
                else:
                    success = self._run_normal_commands(timestamp)
                count += 1
                continue_loop = False
                if loop_count is not None:
                    if count < loop_count:
                        continue_loop = True
                else:
                    if interval is not None and interval > 0:
                        continue_loop = True
                if not continue_loop:
                    break
                if interval and interval > 0:
                    wait_end = time.time() + interval
                    while time.time() < wait_end and self._is_running:
                        time.sleep(0.1)
        except Exception as e:
            if not self._stopped:
                self.signals.error.emit(
                    self._format_message(self.LEVEL_ERROR, f"[{self.command.name}] 执行异常: {str(e)}"),
                    self.tag,
                )
                self._emit_finished(False)
        finally:
            if self.output_file:
                self.output_file.close()
                self.output_file = None

        if not self._stopped and not self._manually_stopped:
            self._emit_finished(success)

    # ---------- 录屏分支 ----------
    def _run_screenrecord(self, timestamp):
        # 检测是否已有录屏进程
        def check_screenrecord_running():
            try:
                cmd = f'"{self.adb}" -s {self.device_serial} shell pgrep screenrecord'
                result = subprocess.run(
                    cmd, shell=True, capture_output=True, text=True, timeout=3,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
                )
                if result.returncode == 0 and result.stdout.strip():
                    return result.stdout.strip().split()[0]
            except Exception:
                pass
            try:
                cmd = f'"{self.adb}" -s {self.device_serial} shell ps -e | grep screenrecord'
                result = subprocess.run(
                    cmd, shell=True, capture_output=True, text=True, timeout=3,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
                )
                if result.returncode == 0 and result.stdout.strip():
                    for line in result.stdout.strip().splitlines():
                        parts = line.split()
                        if len(parts) >= 2 and parts[1].isdigit():
                            return parts[1]
            except Exception:
                pass
            return None

        existing_pid = check_screenrecord_running()
        if existing_pid:
            self.signals.error.emit(
                self._format_message(
                    self.LEVEL_ERROR,
                    f"[{self.command.name}] 设备上已有录屏进程 (PID: {existing_pid}) 在运行，请先停止后再启动",
                ),
                self.tag,
            )
            self._emit_finished(False)
            return False

        try:
            self._device_video_path = f"/sdcard/video_{timestamp}.mp4"
            local_dir = os.path.join(self.output_dir, self.safe_device_serial)
            os.makedirs(local_dir, exist_ok=True)
            self._local_video_path = os.path.normpath(
                os.path.join(local_dir, f"video_{timestamp}.mp4")
            )
            full_cmd = f'"{self.adb}" -s {self.device_serial} shell screenrecord --time-limit 180 {self._device_video_path}'

            if sys.platform == 'win32':
                self.process = subprocess.Popen(
                    full_cmd, shell=True,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, bufsize=1, universal_newlines=True,
                    encoding='utf-8', errors='backslashreplace',
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            else:
                self.process = subprocess.Popen(
                    full_cmd, shell=True,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, bufsize=1, universal_newlines=True,
                    encoding='utf-8', errors='backslashreplace',
                    preexec_fn=os.setsid,
                )

            def read_stream(stream, is_stderr):
                for line in iter(stream.readline, ''):
                    if not self._is_running:
                        break
                    line = line.rstrip('\n')
                    if is_stderr:
                        self.signals.error.emit(
                            self._format_message(self.LEVEL_ERROR, f"[{self.command.name}] {line}"),
                            self.tag,
                        )
                    else:
                        self.signals.output.emit(
                            self._format_message(self.LEVEL_INFO, f"[{self.command.name}] {line}"),
                            self.tag,
                        )

            t_stdout = threading.Thread(target=read_stream, args=(self.process.stdout, False))
            t_stderr = threading.Thread(target=read_stream, args=(self.process.stderr, True))
            t_stdout.daemon = t_stderr.daemon = True
            t_stdout.start()
            t_stderr.start()

            self.process.wait()
            t_stdout.join()
            t_stderr.join()

            if self.process.returncode != 0:
                if self._stopped:
                    self.signals.output.emit(
                        self._format_message(self.LEVEL_INFO, f"[{self.command.name}] 录屏已手动停止"),
                        self.tag,
                    )
                else:
                    if self.process.returncode == 127:
                        self.signals.error.emit(
                            self._format_message(self.LEVEL_ERROR, f"[{self.command.name}] 设备不支持 screenrecord 命令"),
                            self.tag,
                        )
                    else:
                        self.signals.error.emit(
                            self._format_message(
                                self.LEVEL_ERROR,
                                f"[{self.command.name}] 录屏命令异常退出 (错误码 {self.process.returncode})",
                            ),
                            self.tag,
                        )
                    return False

            if not self._video_pulled:
                self._pull_video(self._device_video_path, self._local_video_path)

            if not self._stopped:
                return self.process.returncode == 0
            # 手动停止：检查拉取结果
            if self._video_pulled and self._local_video_path:
                normalized = os.path.normpath(self._local_video_path)
                if os.path.exists(normalized):
                    file_size = os.path.getsize(normalized)
                    success = file_size > 1024
                    if not success:
                        self.signals.error.emit(
                            self._format_message(self.LEVEL_ERROR, f"[{self.command.name}] 视频文件过小 ({file_size} 字节)"),
                            self.tag,
                        )
                    return success
                else:
                    self.signals.error.emit(
                        self._format_message(self.LEVEL_ERROR, f"[{self.command.name}] 视频文件不存在: {normalized}"),
                        self.tag,
                    )
                    return False
            else:
                self.signals.error.emit(
                    self._format_message(self.LEVEL_ERROR, f"[{self.command.name}] 视频未拉取或路径无效"),
                    self.tag,
                )
                return False

        except Exception as e:
            self.signals.error.emit(
                self._format_message(self.LEVEL_ERROR, f"[{self.command.name}] 录屏异常: {str(e)}"),
                self.tag,
            )
            return False

    def _pull_video(self, device_path, local_path):
        if self._video_pulled:
            return
        self._video_pulled = True
        success = False
        try:
            check_cmd = f'"{self.adb}" -s {self.device_serial} shell ls {device_path}'
            result = subprocess.run(
                check_cmd, shell=True, capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
                encoding='utf-8', errors='replace',
            )
            if result.returncode != 0:
                self.signals.error.emit(
                    self._format_message(self.LEVEL_ERROR, f"[{self.command.name}] 设备上未找到视频文件 {device_path}"),
                    self.tag,
                )
                return

            pull_cmd = f'"{self.adb}" -s {self.device_serial} pull {device_path} "{local_path}"'
            pull_result = subprocess.run(
                pull_cmd, shell=True, capture_output=True, text=True, timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
                encoding='utf-8', errors='replace',
            )
            if pull_result.returncode == 0:
                if os.path.exists(local_path):
                    file_size = os.path.getsize(local_path)
                    if file_size < 1024:
                        self.signals.error.emit(
                            self._format_message(self.LEVEL_ERROR, f"[{self.command.name}] 拉取的文件过小 ({file_size} 字节)，可能损坏，已删除"),
                            self.tag,
                        )
                        os.remove(local_path)
                    else:
                        self.signals.output.emit(
                            self._format_message(self.LEVEL_SUCCESS, f"[{self.command.name}] 视频已保存至: {local_path} ({file_size} 字节)"),
                            self.tag,
                        )
                        subprocess.run(
                            f'"{self.adb}" -s {self.device_serial} shell rm {device_path}',
                            shell=True, timeout=5,
                            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
                            encoding='utf-8', errors='replace',
                        )
                        success = True
                else:
                    self.signals.error.emit(
                        self._format_message(self.LEVEL_ERROR, f"[{self.command.name}] 拉取失败，本地文件未生成"),
                        self.tag,
                    )
            else:
                self.signals.error.emit(
                    self._format_message(self.LEVEL_ERROR, f"[{self.command.name}] 拉取视频失败: {pull_result.stderr}"),
                    self.tag,
                )
        except Exception as e:
            self.signals.error.emit(
                self._format_message(self.LEVEL_ERROR, f"[{self.command.name}] 拉取视频异常: {str(e)}"),
                self.tag,
            )
        finally:
            if self._stopped:
                self._emit_finished(success)

    # ---------- 通用错误检测 ----------
    def _check_output_for_errors(self, output_text):
        if self.is_logcat:
            return False
        if "intent has been delivered to currently running top-most instance" in output_text:
            return False
        if "its current task has been brought to the front" in output_text:
            return False
        error_keywords = [
            "Error", "error", "unable to", "not found", "inaccessible",
            "failed", "Failed", "No such file", "cannot", "Can't",
            "unable to resolve", "Activity not started",
        ]
        for kw in error_keywords:
            if kw in output_text:
                return True
        return False

    def _get_friendly_error(self, output):
        output_lower = output.lower()
        if "permission denied" in output_lower:
            return "权限不足，请尝试 adb root 或检查文件权限"
        elif "no such file" in output_lower or "not found" in output_lower:
            return "文件或目录不存在"
        elif "device offline" in output_lower:
            return "设备离线或未连接，请检查 USB 连接或重新插拔设备"
        elif "inaccessible or not found" in output_lower:
            return "设备不支持该命令"
        elif "unable to resolve intent" in output_lower or "activity not started" in output_lower:
            return "无法启动 Activity，请检查包名/Activity 名称是否正确"
        elif "operation not permitted" in output_lower:
            return "操作不允许，可能需要 root 权限"
        elif "unknown command" in output_lower:
            match = re.search(r"unknown command ['\"]?(\S+)['\"]?", output, re.IGNORECASE)
            bad_cmd = match.group(1) if match else "未知"
            return f"无效的 ADB 命令：'{bad_cmd}'，请检查命令内容是否正确"
        else:
            lines = [l.strip() for l in output.splitlines() if l.strip() and not l.startswith('[')]
            return lines[0][:150] if lines else "未知错误"

    # ---------- 普通命令分支 ----------
    def _run_normal_commands(self, timestamp):
        commands = [c.strip() for c in self.command.command_text.split(';') if c.strip()]
        overall_success = True
        first_error_reason = None
        sub_results = []

        for raw_cmd in commands:
            if not self._is_running:
                break

            is_push_pull = 'push' in raw_cmd.lower() or 'pull' in raw_cmd.lower()

            cmd = (raw_cmd
                   .replace("{output_dir}", self.output_dir)
                   .replace("{device_serial}", self.device_serial)
                   .replace("{safe_device_serial}", self.safe_device_serial)
                   .replace("{timestamp}", timestamp))

            if 'pull' in cmd:
                try:
                    args = shlex.split(cmd, posix=False)
                    if 'pull' in args:
                        pull_index = args.index('pull')
                        if len(args) > pull_index + 2:
                            local_path = args[-1]
                            raw_local = local_path.strip('"').strip("'")
                            norm_local = os.path.normpath(raw_local)
                            parent_dir = os.path.dirname(norm_local)
                            if parent_dir:
                                os.makedirs(parent_dir, exist_ok=True)
                            cmd = ' '.join(args)
                except Exception as e:
                    self.signals.error.emit(
                        self._format_message(self.LEVEL_ERROR, f"[{self.command.name}] 准备 pull 命令失败: {e}"),
                        self.tag,
                    )
                    overall_success = False
                    continue

            full_cmd = f'"{self.adb}" -s {self.device_serial} {cmd}'

            if sys.platform == 'win32':
                self.process = subprocess.Popen(
                    full_cmd, shell=True,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, bufsize=1, universal_newlines=True,
                    encoding='utf-8', errors='backslashreplace',
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            else:
                self.process = subprocess.Popen(
                    full_cmd, shell=True,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, bufsize=1, universal_newlines=True,
                    encoding='utf-8', errors='backslashreplace',
                    preexec_fn=os.setsid,
                )

            stdout_lines = []
            stderr_lines = []
            self._output_lines = []
            is_logcat = self.is_logcat

            def read_stream(stream, is_stderr):
                for line in iter(stream.readline, ''):
                    if not self._is_running:
                        break
                    line = line.rstrip('\n')
                    if not line:
                        continue
                    self._output_lines.append(line)
                    if is_stderr:
                        stderr_lines.append(line)
                        is_harmless_warning = (
                            "its current task has been brought to the front" in line or
                            "intent has been delivered to currently running top-most instance" in line
                        )
                        if not is_logcat and not self.is_monkey:
                            if is_harmless_warning:
                                self.signals.output.emit(
                                    self._format_message(self.LEVEL_INFO, f"[{self.command.name}] {line}"),
                                    self.tag,
                                )
                                if "intent has been delivered" in line and self.command.name == "原生设置页面":
                                    self.signals.output.emit(
                                        self._format_message(
                                            self.LEVEL_INFO,
                                            f"[{self.command.name}] 提示：页面已处于前台，无需重复启动",
                                        ),
                                        self.tag,
                                    )
                            else:
                                if is_push_pull:
                                    error_keywords = ["error", "failed", "permission denied",
                                                      "not found", "cannot", "unable", "denied"]
                                    if any(kw in line.lower() for kw in error_keywords):
                                        self.signals.error.emit(
                                            self._format_message(self.LEVEL_ERROR, f"[{self.command.name}] {line}"),
                                            self.tag,
                                        )
                                    else:
                                        self.signals.output.emit(
                                            self._format_message(self.LEVEL_INFO, f"[{self.command.name}] {line}"),
                                            self.tag,
                                        )
                                else:
                                    if not self._is_multi_command:
                                        self.signals.error.emit(
                                            self._format_message(self.LEVEL_ERROR, f"[{self.command.name}] {line}"),
                                            self.tag,
                                        )
                        if self.output_file:
                            self.output_file.write(f"[ERROR] {line}\n")
                            self.output_file.flush()
                    else:
                        stdout_lines.append(line)
                        if not is_logcat and not self.is_monkey:
                            if not self._is_multi_command:
                                self.signals.output.emit(
                                    self._format_message(self.LEVEL_INFO, f"[{self.command.name}] {line}"),
                                    self.tag,
                                )
                        if self.output_file:
                            self.output_file.write(f"{line}\n")
                            self.output_file.flush()

            t_stdout = threading.Thread(target=read_stream, args=(self.process.stdout, False))
            t_stderr = threading.Thread(target=read_stream, args=(self.process.stderr, True))
            t_stdout.daemon = t_stderr.daemon = True
            t_stdout.start()
            t_stderr.start()

            try:
                if self.is_logcat or is_push_pull or self.command.no_timeout:
                    timeout = None
                else:
                    timeout = 30

                returncode = self.process.wait(timeout=timeout)
                t_stdout.join(timeout=5)
                t_stderr.join(timeout=5)

                full_output = "\n".join(self._output_lines)
                is_really_success = (returncode == 0)
                if is_really_success and self._check_output_for_errors(full_output):
                    is_really_success = False

                sub_results.append((cmd.split()[0][:20], is_really_success))

                if not is_really_success:
                    if not self._stopped:
                        if self._is_multi_command:
                            if first_error_reason is None:
                                first_error_reason = self._get_friendly_error(full_output)
                        else:
                            friendly_error = self._get_friendly_error(full_output)
                            self.signals.error.emit(
                                self._format_message(self.LEVEL_ERROR, f"[{self.command.name}] 失败原因：{friendly_error}"),
                                self.tag,
                            )
                    overall_success = False
                else:
                    if not self._is_multi_command and not self.is_monkey:
                        if 'pull' in cmd.lower():
                            match = re.search(r'(\d+)\s+files? pulled', full_output, re.IGNORECASE)
                            if match:
                                pulled_count = int(match.group(1))
                                if pulled_count == 0:
                                    path_match = re.search(r'pull[:\s]+([^\s]+)', cmd, re.IGNORECASE)
                                    remote_path = path_match.group(1) if path_match else "指定路径"
                                    msg = f"拉取完成，但未获取到文件（目录可能为空或无匹配项）：{remote_path}"
                                    self.signals.output.emit(
                                        self._format_message(self.LEVEL_WARNING, f"[{self.command.name}] {msg}"),
                                        self.tag,
                                    )
                        elif not full_output.strip():
                            self.signals.output.emit(
                                self._format_message(self.LEVEL_SUCCESS, f"[{self.command.name}] 命令执行成功（无输出）"),
                                self.tag,
                            )

            except subprocess.TimeoutExpired:
                if not self._stopped:
                    self.signals.error.emit(
                        self._format_message(self.LEVEL_ERROR, f"[{self.command.name}] 命令执行超时（30秒），强制终止"),
                        self.tag,
                    )
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except Exception:
                    self.process.kill()
                overall_success = False
            except Exception as e:
                if not self._stopped:
                    self.signals.error.emit(
                        self._format_message(self.LEVEL_ERROR, f"[{self.command.name}] 命令执行异常: {str(e)}"),
                        self.tag,
                    )
                overall_success = False
            finally:
                if self.process and self.process.poll() is None:
                    self.process.kill()

        # 多命令汇总
        if self._is_multi_command and sub_results and not self._stopped and not self.is_monkey:
            if self.command.name not in ["截图", "权限获取"]:
                success_count = sum(1 for _, ok in sub_results if ok)
                total = len(sub_results)
                if success_count == total:
                    level = self.LEVEL_SUCCESS
                    summary = f"全部 {total} 项任务执行成功"
                else:
                    level = self.LEVEL_WARNING if success_count > 0 else self.LEVEL_ERROR
                    summary = f"{total} 项任务：{success_count} 项成功，{total - success_count} 项失败"
                self.signals.output.emit(
                    self._format_message(level, f"[{self.command.name}] {summary}"),
                    self.tag,
                )

        if self._is_multi_command and not overall_success and first_error_reason:
            self.signals.error.emit(
                self._format_message(self.LEVEL_ERROR, f"[{self.command.name}] 失败原因：{first_error_reason}"),
                self.tag,
            )

        return overall_success


# ============================================================
# 命令池
# ============================================================
class AdbCommandPool(QObject):
    """ADB 命令并发执行池"""

    command_output = pyqtSignal(str, str)                 # text, tag
    command_started = pyqtSignal(int, str, str)           # cmd_id, cmd_name, tag
    command_finished = pyqtSignal(int, str, bool, str)    # cmd_id, cmd_name, success, tag
    command_stopped = pyqtSignal(int, str, str)           # cmd_id, cmd_name, tag

    # 主项目正在执行用例时，这些命令会改动设备状态、或把正在跑的用例顶掉，必须挡住。
    # 「只读采集」类**不要**放进来：logcat / 录屏 / 截图 / pull 导出日志 恰恰是用例
    # 执行期间最需要的 —— 跑到一半发现问题，就要现场把日志和视频捞出来。
    # 注：remount 对应预设「权限获取」（root; remount），它会 remount /system，
    # 直接把设备状态改掉，正在跑的用例必挂，所以必须留在名单里。
    HIGH_IMPACT_KEYWORDS = (
        'monkey', 'tcpdump', 'meminfo', 'dumpsys', 'reboot',
        'pm clear', 'force-stop', 'netem', 'tc ', 'remount',
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self._main_running = False
        self.threadpool = QThreadPool()
        self.threadpool.setMaxThreadCount(MAX_CONCURRENT_TASKS)
        self.running_commands = {}

    # ---------- 主项目状态 ----------
    def set_main_running(self, running: bool):
        self._main_running = running

    # ---------- 互斥检查 ----------
    def can_execute(self, command) -> tuple:
        if not self._main_running:
            return True, ""
        if self._is_high_impact(command):
            return False, "主项目正在执行用例，该命令暂不可用"
        return True, ""

    def _is_high_impact(self, command) -> bool:
        text = (command.name + ' ' + command.command_text).lower()
        return any(k in text for k in self.HIGH_IMPACT_KEYWORDS)

    # ---------- 执行 / 停止 ----------
    def execute_command(self, command_obj, device_serial, output_dir, tag='user'):
        if tag == 'user':
            ok, reason = self.can_execute(command_obj)
            if not ok:
                self.command_output.emit(
                    f"<span style='color:{log_colors.log_color(log_colors.WARNING)};'>⚠️ {reason}</span>", tag
                )
                return

        signals = CommandSignals()
        signals.started.connect(self._on_signals_started)
        signals.output.connect(self._on_signals_output)
        signals.error.connect(self._on_signals_error)
        signals.finished.connect(self._on_signals_finished)
        signals.stopped.connect(self._on_signals_stopped)

        runnable = AdbCommandRunnable(command_obj, device_serial, output_dir, signals, tag)
        self.running_commands[command_obj.id] = runnable
        self.threadpool.start(runnable)

    def stop_command(self, command_id):
        runnable = self.running_commands.get(command_id)
        if runnable:
            runnable.stop()

    # ---------- 信号转发 ----------
    def _on_signals_started(self, cmd_id, cmd_name, tag):
        self.command_started.emit(cmd_id, cmd_name, tag)

    def _on_signals_output(self, text, tag):
        self.command_output.emit(text, tag)

    def _on_signals_error(self, text, tag):
        self.command_output.emit(
            f"<span style='color:{log_colors.log_color(log_colors.ERROR)};'>{text}</span>", tag)

    def _on_signals_finished(self, cmd_id, cmd_name, success, tag):
        self.running_commands.pop(cmd_id, None)
        self.command_finished.emit(cmd_id, cmd_name, success, tag)

    def _on_signals_stopped(self, cmd_id, cmd_name, tag):
        self.command_stopped.emit(cmd_id, cmd_name, tag)