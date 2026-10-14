# services/packet_capture.py
"""网络抓包（tcpdump）的执行能力。

原本挂在 ADB 工具箱的「网络抓包」对话框里；抓包入口迁移到「性能检测」页之后，
这里只保留实际干活的线程与保护常量，供性能检测页调用（不再有对话框）。

两个线程：
  * TcpdumpDeployThread —— 检测 root、按需把 tcpdump 推到设备并 chmod +x
  * CaptureWorker       —— 执行 tcpdump，把抓到的数据流式写入本地 pcap

抓包保护：单次抓包有「时长 / 文件大小」上限，到限自动停止并保存已抓内容，
避免「忘了停」把设备或本地磁盘撑爆（尤其长时间无人值守时）。
"""
import os
import subprocess
import sys
import threading
from datetime import datetime

from PyQt6.QtCore import QThread, pyqtSignal

from utils.adb_path import get_adb_path

# 抓包保护默认值：单次抓包的时长 / 文件大小上限，到限自动停止并保存已抓内容。
CAPTURE_MAX_SECONDS = 10 * 60              # 10 分钟
CAPTURE_MAX_BYTES = 100 * 1024 * 1024      # 100 MB

# 抓包参数（界面「参数」对话框可改，存 data/config.json 的 capture_config）。
# 0 表示「不限」——上限本来是防呆用的，长跑场景需要能放开。
DEFAULT_CAPTURE_CONFIG = {
    'filter': '',                # BPF 过滤表达式，如 "tcp port 443"；空 = 抓全部
    'interface': 'any',          # -i 网卡
    'snaplen': 0,                # -s 单包最大长度；0 = 完整包
    'max_seconds': CAPTURE_MAX_SECONDS,
    'max_bytes': CAPTURE_MAX_BYTES,
}


def load_capture_config() -> dict:
    """读抓包参数；缺项/脏值一律用默认值兜底，保证 worker 拿到的一定是完整可用的。"""
    from utils.settings import Settings
    merged = dict(DEFAULT_CAPTURE_CONFIG)
    raw = Settings.get_capture_config()
    for key in DEFAULT_CAPTURE_CONFIG:
        if key not in raw:
            continue
        value = raw[key]
        if key in ('max_seconds', 'max_bytes', 'snaplen'):
            try:
                value = max(0, int(value))
            except (TypeError, ValueError):
                continue
        else:
            value = str(value)
            if key == 'interface' and not value.strip():
                continue
        merged[key] = value
    return merged


def save_capture_config(config: dict):
    """只落盘认识的键，避免把界面上的临时字段写进配置"""
    from utils.settings import Settings
    Settings.set_capture_config(
        {k: v for k, v in (config or {}).items() if k in DEFAULT_CAPTURE_CONFIG})


def capture_limit_text(reason: str) -> str:
    """把 CaptureWorker.stop_reason 转成给用户看的限制说明。

    仅在「到限自动停止」（time / size）时返回文案；手动停止或自然结束返回空串。
    """
    return {
        'time': f'已达时长上限 {CAPTURE_MAX_SECONDS // 60} 分钟',
        'size': f'已达大小上限 {CAPTURE_MAX_BYTES // 1024 // 1024} MB',
    }.get(reason or '', '')


class TcpdumpDeployThread(QThread):
    """后台线程：检查/部署 tcpdump。

    查找顺序（能少一步就少一步）：
      1. 设备上已经有能跑的 tcpdump —— 直接用，不 push
         （多数 ROM 的 /system/bin 里就带，root 机器尤其常见）
      2. 本地 tools/tcpdump —— push 到 /data/local/tmp 再 chmod +x
      3. 都没有 —— 报错并把该放的路径说清楚

    解析出的设备侧路径放在 ``device_path``，由控制器传给 CaptureWorker，
    否则抓包会写死去找 /data/local/tmp/tcpdump 而用不上设备自带的那份。
    """
    finished_with = pyqtSignal(bool, str)

    # 设备侧 tcpdump 的候选位置，按优先级试
    DEVICE_CANDIDATES = (
        "/system/bin/tcpdump", "/system/xbin/tcpdump",
        "/vendor/bin/tcpdump", "/data/local/tmp/tcpdump",
    )

    def __init__(self, serial, tcpdump_local_path):
        super().__init__()
        self.serial = serial
        self.tcpdump_local_path = tcpdump_local_path
        self.device_path = "/data/local/tmp/tcpdump"

    def run(self):
        adb = get_adb_path()
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0

        # 1. 检查 root
        try:
            r = subprocess.run(
                [adb, "-s", self.serial, "shell", "id"],
                capture_output=True, text=True, timeout=5,
                creationflags=creationflags, encoding='utf-8', errors='replace',
            )
            if "uid=0" not in (r.stdout or ""):
                self.finished_with.emit(False, "设备未 root，无法抓包。请 root 后再试。")
                return
        except Exception as e:
            self.finished_with.emit(False, f"检测 root 失败: {e}")
            return

        # 2. 设备上已有可执行 tcpdump？（一次 shell 走完，省得每个路径一问一答）
        probe = ("for p in " + " ".join(self.DEVICE_CANDIDATES) + "; do "
                 'if [ -x "$p" ]; then echo "$p"; break; fi; done')
        try:
            r = subprocess.run(
                [adb, "-s", self.serial, "shell", probe],
                capture_output=True, text=True, timeout=8,
                creationflags=creationflags, encoding='utf-8', errors='replace',
            )
            found = [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]
            if found:
                self.device_path = found[0]
                self.finished_with.emit(
                    True, f"使用设备自带 tcpdump（{self.device_path}）")
                return
        except Exception:
            pass

        # 3. 本地文件存在？
        if not os.path.exists(self.tcpdump_local_path):
            self.finished_with.emit(
                False,
                "设备上没有可用的 tcpdump，本地也没找到。\n"
                f"请把一个与设备 CPU 架构匹配的 tcpdump 放到:\n"
                f"{self.tcpdump_local_path}"
            )
            return

        # 4. push + chmod
        try:
            subprocess.run(
                [adb, "-s", self.serial, "push", self.tcpdump_local_path,
                 "/data/local/tmp/tcpdump"],
                check=True, timeout=30, creationflags=creationflags,
            )
            subprocess.run(
                [adb, "-s", self.serial, "shell", "chmod", "+x",
                 "/data/local/tmp/tcpdump"],
                check=True, timeout=5, creationflags=creationflags,
            )
            self.device_path = "/data/local/tmp/tcpdump"
            self.finished_with.emit(True, "tcpdump 部署成功")
        except Exception as e:
            self.finished_with.emit(False, f"部署失败: {e}")


def _sh_quote(text: str) -> str:
    """给设备 shell 用的单引号转义（BPF 表达式里可能有空格 / 括号 / 重定向符）"""
    return "'" + str(text).replace("'", "'\\''") + "'"


class CaptureWorker(QThread):
    """后台线程：执行 tcpdump 抓包，实时输出到 pcap 文件。

    带保护：超过时长 / 文件大小上限后自动停止并保存已抓内容。跑完可读
    ``stop_reason`` 判断停止原因（'' 自然结束 / 'time' 超时 / 'size' 超量 / 'user' 手动）。
    """
    started_signal = pyqtSignal(str)          # 文件路径
    finished_signal = pyqtSignal(bool, str)   # (成功, 结果)
    error_signal = pyqtSignal(str)
    size_updated = pyqtSignal(int)            # 文件大小

    def __init__(self, serial, output_dir, filter_expr="",
                 device_path="/data/local/tmp/tcpdump",
                 interface="any", snaplen=0,
                 max_seconds=CAPTURE_MAX_SECONDS, max_bytes=CAPTURE_MAX_BYTES):
        super().__init__()
        self.serial = serial
        self.output_dir = output_dir
        self.filter_expr = filter_expr
        # 设备侧 tcpdump 路径：可能来自 push，也可能是设备自带的
        # （见 TcpdumpDeployThread.DEVICE_CANDIDATES），不能写死
        self.device_path = device_path
        self.interface = interface or "any"
        self.snaplen = max(0, int(snaplen or 0))
        self.max_seconds = max_seconds
        self.max_bytes = max_bytes
        self._is_running = True
        self.process = None
        self.capture_file = None
        self.file_handle = None
        self.stop_reason = ""
        self._limit_timer = None

    def run(self):
        adb = get_adb_path()
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0

        # 准备输出文件
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_serial = self.serial.replace(':', '_')
        device_dir = os.path.join(self.output_dir, safe_serial)
        try:
            os.makedirs(device_dir, exist_ok=True)
        except Exception as e:
            self.error_signal.emit(f"无法创建目录: {e}")
            self.finished_signal.emit(False, "创建目录失败")
            return

        self.capture_file = os.path.join(device_dir, f"capture_{timestamp}.pcap")
        try:
            self.file_handle = open(self.capture_file, 'wb')
        except Exception as e:
            self.error_signal.emit(f"无法创建文件: {e}")
            self.finished_signal.emit(False, "创建文件失败")
            return

        # 为什么必须用 exec-out、而不是 shell（2026-10-14 实测踩出来的）：
        #   * `adb shell` 会把输出里的 0x0A 翻成 0x0D 0x0A（终端换行翻译），
        #     pcap 是二进制流，一旦某个包里含 0x0A，后面的记录整体错位 —— 抓出来的
        #     文件前几个包看着正常、再往后全是垃圾，Wireshark 打不开。
        #   * `adb exec-out` 不做换行翻译，但它把 tcpdump 的 stderr 也并进了 stdout，
        #     "tcpdump: listening on any..." 那段文本会被塞进 24 字节文件头之后。
        # 所以两个都要：exec-out + 把 stderr 重定向到 /dev/null。
        # 整条命令作为**一个参数**交给设备 shell，BPF 表达式用单引号包住。
        parts = [self.device_path, "-i", _sh_quote(self.interface),
                 "-s", str(self.snaplen), "-w", "-", "-U"]
        if self.filter_expr.strip():
            parts.append(_sh_quote(self.filter_expr.strip()))
        cmd = [adb, "-s", self.serial, "exec-out", " ".join(parts) + " 2>/dev/null"]

        try:
            self.process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
        except Exception as e:
            self.error_signal.emit(f"启动 tcpdump 失败: {e}")
            if self.file_handle:
                self.file_handle.close()
            self.finished_signal.emit(False, "启动失败")
            return

        self.started_signal.emit(self.capture_file)

        # 时长上限用独立计时器触发：无流量时 stdout.read 会一直阻塞，
        # 光靠循环里的时间判断触发不了（这是保护最容易失效的地方）。
        if self.max_seconds and self.max_seconds > 0:
            self._limit_timer = threading.Timer(self.max_seconds, self._stop_by_time)
            self._limit_timer.daemon = True
            self._limit_timer.start()

        try:
            while self._is_running:
                data = self.process.stdout.read(8192)
                if not data:
                    break
                self.file_handle.write(data)
                self.file_handle.flush()
                size = self.file_handle.tell()
                if size % (1024 * 1024) < 8192:
                    self.size_updated.emit(size)
                # 大小上限：有数据写入时才可能触发（够用，且不引入额外查询）
                if self.max_bytes and size >= self.max_bytes:
                    self.stop_reason = "size"
                    break
        except Exception as e:
            self.error_signal.emit(f"读取数据异常: {e}")
        finally:
            self._cleanup()

    def _stop_by_time(self):
        """时长计时器回调：标记原因并终止 tcpdump，让阻塞的 read 返回。"""
        if not self._is_running:
            return
        self.stop_reason = "time"
        self._is_running = False
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
            except Exception:
                pass

    def _cleanup(self):
        if self._limit_timer:
            try:
                self._limit_timer.cancel()
            except Exception:
                pass
            self._limit_timer = None
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=3)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
            self.process = None
        if self.file_handle:
            try:
                self.file_handle.close()
            except Exception:
                pass
            self.file_handle = None
        if self.capture_file and os.path.exists(self.capture_file):
            self.size_updated.emit(os.path.getsize(self.capture_file))
        self.finished_signal.emit(True, self.capture_file)

    def stop(self):
        """外部请求停止（用户点击 / 随监控结束自动收尾）。"""
        if self._is_running:
            self.stop_reason = "user"
        self._is_running = False
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
            except Exception:
                pass
