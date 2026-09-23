# services/scrcpy_service.py
"""scrcpy 投屏的启动封装（无界面）。

原先这套逻辑在 views/adb_dialogs/screen_mirror_dialog.py 里，和参数调节、
同步录屏混在一个对话框中。对话框去掉后，投屏收敛成「一键启动 + toast 反馈」，
这里只负责：检查可执行文件 → 组命令 → 启动 → 确认没立刻退出。

失败一律抛 ScrcpyError，message 就是给用户看的那句话，调用方直接 toast 出来。
"""
import subprocess
import sys
import time

from utils.adb_path import get_scrcpy_path

# 启动后等这么久再确认进程是否还活着。
# scrcpy 参数错 / 设备没响应时会很快退出，用这个窗口把失败捞出来告诉用户，
# 而不是"看起来投屏了其实窗口闪一下就没了"。
STARTUP_GRACE_SECONDS = 0.8


class ScrcpyError(Exception):
    """投屏启动失败，str(e) 是给用户看的简短原因。"""


class ScrcpyService:
    """一键投屏。参数沿用原对话框的默认值，不再提供界面调节。"""

    # 原对话框的默认值
    BITRATE_MBPS = 8
    MAX_FPS = 60
    RESOLUTION = "原始"        # 原始 = 不传 --max-size
    STAY_AWAKE = True          # 原对话框默认勾选
    SHOW_TOUCHES = True        # 原对话框默认勾选
    TURN_SCREEN_OFF = False    # 原对话框默认未勾选

    def __init__(self):
        self._process = None

    # ------------------------------------------------------------------
    def is_mirroring(self) -> bool:
        """当前是否已有投屏在跑（启动确认的那 0.8 秒内也算，避免重复点击开两个窗口）"""
        return self._process is not None and self._process.poll() is None

    def build_command(self, serial: str):
        cmd = [get_scrcpy_path(), "-s", serial]
        cmd.extend(["--video-bit-rate", f"{self.BITRATE_MBPS}M"])
        cmd.extend(["--max-fps", str(self.MAX_FPS)])
        if self.STAY_AWAKE:
            cmd.append("--stay-awake")
        if self.SHOW_TOUCHES:
            cmd.append("--show-touches")
        if self.TURN_SCREEN_OFF:
            cmd.append("--turn-screen-off")
        return cmd

    def start(self, serial: str):
        """启动投屏；失败抛 ScrcpyError。

        内部有 ~0.8 秒的启动确认等待，调用方应放到后台线程里执行。
        """
        if not serial:
            raise ScrcpyError("设备未连接，请先连接设备")
        if self.is_mirroring():
            raise ScrcpyError("投屏已在进行中")

        scrcpy_path = get_scrcpy_path()
        # Windows 下 scrcpy 是控制台程序：不加这个标志会额外弹出一个 CMD 窗口。
        # scrcpy 的画面窗口由 SDL 单独创建，不受该标志影响。
        # 非 Windows 下 creationflags=0，与默认行为一致。
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0

        # scrcpy 不在 tools 目录时会回退到 PATH，先探一下，
        # 免得把"找不到可执行文件"当成启动失败报给用户
        if scrcpy_path == "scrcpy":
            try:
                subprocess.run([scrcpy_path, "--version"],
                               capture_output=True, timeout=5, check=True,
                               creationflags=creationflags)
            except Exception:
                raise ScrcpyError("未找到 scrcpy，请放入 tools 目录或加入 PATH")

        try:
            self._process = subprocess.Popen(
                self.build_command(serial),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
        except Exception as e:
            self._process = None
            raise ScrcpyError(f"无法启动 scrcpy：{str(e)[:60]}")

        time.sleep(STARTUP_GRACE_SECONDS)
        code = self._process.poll()
        if code is not None:
            self._process = None
            raise ScrcpyError(f"scrcpy 启动后立即退出（返回码 {code}）")
