# services/weditor_service.py
import subprocess
import time
import os
import sys
import atexit
import socket
import threading
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import QLabel
from PyQt6.QtCore import Qt
from utils.theme import ThemeMode

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def _bind_kill_on_close(process):
    """把子进程绑定到 Windows Job Object（KILL_ON_JOB_CLOSE）。

    为什么需要：应用可视化用了 QWebEngineView，WebEngine 应用退出时会走
    Chromium 自己的清理路径（可能直接结束进程），**Python 的 atexit 不可靠**
    ——实测 1.1.8 关闭虫师后 weditor.exe 仍残留（atexit 没执行）。
    Job Object 是 Windows 的「父死子亡」系统机制：虫师进程退出时 Job 句柄
    随之关闭，系统自动杀掉绑定的 weditor.exe——无论主程序是正常退出、
    崩溃还是被强杀，物理上杜绝残留。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        class _IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                (n, ctypes.c_ulonglong)
                for n in ("ReadOperationCount", "WriteOperationCount",
                          "OtherOperationCount", "ReadTransferCount",
                          "WriteTransferCount", "OtherTransferCount")
            ]

        class _BASIC_LIMITS(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", ctypes.c_uint32),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", ctypes.c_uint32),
                ("SchedulingClass", ctypes.c_uint32),
            ]

        class _EXTENDED_LIMITS(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _BASIC_LIMITS),
                ("IoInfo", _IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        k32 = ctypes.windll.kernel32
        job = k32.CreateJobObjectW(None, None)
        if not job:
            return
        info = _EXTENDED_LIMITS()
        info.BasicLimitInformation.LimitFlags = 0x2000   # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(
                job, 9, ctypes.byref(info), ctypes.sizeof(info)):   # 9 = ExtendedLimitInformation
            k32.CloseHandle(job)
            return
        if not k32.AssignProcessToJobObject(job, int(process._handle)):
            k32.CloseHandle(job)
            return
        # 句柄故意不关：持有在当前进程，虫师退出时句柄关闭 -> 触发 KILL_ON_JOB_CLOSE
        process._kill_on_close_job = job
    except Exception as e:
        # Job 绑定失败不影响正常功能，退出清理还有 atexit / aboutToQuit 兜底
        print(f"[weditor] Job Object 绑定失败（退出清理降级为 atexit）: {e}")


class WeditorService:
    def __init__(self):
        self.process = None
        self.web_view = None
        # 启动锁：防止并发调用 _start_process_and_wait 时重复 spawn weditor
        # （实测竞态：第一次还在等端口监听时第二次进来检测不到端口，又起一份
        #  —— 会出现两个 weditor 实例、两份 ipyshell，弹多个 python 窗口）
        self._start_lock = threading.Lock()
        self._fix_weditor_version()
        self._patch_weditor_shell()
        # 退出清理：主进程关闭时终止 weditor 子进程，避免孤儿化残留
        # （实测残留：weditor 双实例+ipyshell 在虫师退出后继续存活并占着 17310 端口，
        #  下次启动复用旧进程，旧进程里的旧代码还会继续弹窗）
        # 注意：atexit 只是兜底之一——QWebEngine 应用退出时 WebEngine 可能跳过
        # atexit，所以主防线是 spawn 时的 Job Object（_bind_kill_on_close）
        # 和 main.py 里的 app.aboutToQuit 连接，三者互相兜底。
        atexit.register(self.stop)

    def apply_theme(self, theme_mode: ThemeMode):
        """应用主题到服务（占位方法，保持接口一致性）"""
        # weditor 服务不涉及界面样式，无需实际操作
        # 但如果 web_view 已经创建，可以在这里刷新主题
        pass

    def _fix_weditor_version(self):
        """修复 weditor 0.7.3 在 Python 3.13 下的 version.py 问题"""
        try:
            import weditor
            base_path = os.path.dirname(weditor.__file__)
            version_file = os.path.join(base_path, 'web', 'version.py')
            if os.path.exists(version_file):
                with open(version_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                if 'pkg_resources' in content:
                    new_content = """try:
    import importlib.metadata
    __version__ = importlib.metadata.version("weditor")
except Exception:
    __version__ = "unknown"
"""
                    with open(version_file, 'w', encoding='utf-8') as f:
                        f.write(new_content)
                    print("已修复 weditor version.py")
        except Exception as e:
            print(f"修复 weditor 失败: {e}")

    def _patch_weditor_shell(self):
        """给 weditor 的 ipyshell 子进程补 CREATE_NO_WINDOW。

        weditor 无控制台运行（虫师 spawn 时已加 CREATE_NO_WINDOW）时，它内部
        spawn 的 ipython（shell.py，无任何窗口标志）会新建控制台 → 弹 "python"
        窗口。设备插拔触发 weditor 页面重连时就会出现，用户实测反馈。
        补丁幂等（带标记），仿 _fix_weditor_version 的启动时自动修复模式；
        weditor 升级后文件被覆盖，下次启动会自动重打。
        """
        try:
            import weditor
            shell_file = os.path.join(
                os.path.dirname(weditor.__file__), 'web', 'handlers', 'shell.py')
            if not os.path.exists(shell_file):
                return
            with open(shell_file, 'r', encoding='utf-8') as f:
                src = f.read()
            if 'CHONGSHI-NO-CONSOLE' in src:
                return
            old = "        self.proc = subprocess.Popen(*args, **kwargs)"
            new = ("        # CHONGSHI-NO-CONSOLE: 无控制台运行时 ipython 子进程会弹黑窗\n"
                   "        if IS_WINDOWS and 'creationflags' not in kwargs:\n"
                   "            kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW\n"
                   "        self.proc = subprocess.Popen(*args, **kwargs)")
            if old not in src:
                return
            src = src.replace(old, new, 1)
            with open(shell_file, 'w', encoding='utf-8') as f:
                f.write(src)
            # 清掉字节码缓存，确保补丁下次启动生效
            pycache = os.path.join(os.path.dirname(shell_file), '__pycache__')
            if os.path.isdir(pycache):
                for name in os.listdir(pycache):
                    if name.startswith('shell.') and name.endswith('.pyc'):
                        try:
                            os.remove(os.path.join(pycache, name))
                        except OSError:
                            pass
            print("已补丁 weditor ipyshell（无黑窗）")
        except Exception as e:
            print(f"补丁 weditor shell 失败: {e}")

    def _is_port_open(self, port, timeout=0.5):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect(('127.0.0.1', port))
            sock.close()
            return True
        except Exception:
            return False

    def _wait_for_port(self, port, timeout=10):
        start_time = time.time()
        while time.time() - start_time < timeout:
            if self._is_port_open(port):
                return True
            time.sleep(0.2)
        return False

    def _build_weditor_command(self, port=17310):
        """按运行形态构造 weditor 启动命令。

        - 源码模式：[venv python, -m, weditor, ...]
        - 打包模式：[tools/weditor.exe, ...] —— sys.executable 是虫师.exe，
          不支持 -m weditor（1.1.4~1.1.6 打包版应用可视化一直坏在这个点）；
          独立 exe 由 weditor.spec 打出、随主包 tools/ 分发。
        """
        if getattr(sys, "frozen", False):
            cand = os.path.join(sys._MEIPASS, "tools", "weditor.exe")
            if os.path.exists(cand):
                return [cand, "-p", str(port), "-q"]
            raise Exception(
                "未找到内置的 weditor.exe（安装目录 _internal/tools 下），"
                "请重新下载完整版虫师安装包"
            )
        return [sys.executable, "-m", "weditor", "-p", str(port), "-q"]

    def _start_process_and_wait(self, port=17310):
        """启动进程并等待端口，返回端口号，供线程调用，不创建任何 GUI 对象"""
        # 启动锁：并发调用（设备变化时可能多线程触发）只允许起一份 weditor。
        # 无锁实测会出现两个实例、两份 ipyshell，各弹一个 python 窗口。
        with self._start_lock:
            return self._start_process_and_wait_locked(port)

    def _start_process_and_wait_locked(self, port=17310):
        # 如果已有服务运行，直接返回端口
        if self._is_port_open(port):
            return port

        # 启动新进程
        try:
            self.process = subprocess.Popen(
                self._build_weditor_command(port),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=CREATE_NO_WINDOW,
                text=True
            )
            # Windows：绑定 Job Object（父死子亡），杜绝任何退出路径下的进程残留
            _bind_kill_on_close(self.process)
        except Exception as e:
            raise Exception(f"无法启动 weditor 进程: {e}")

        # 等待进程启动
        time.sleep(1)
        if self.process.poll() is not None:
            stdout, stderr = self.process.communicate()
            error_msg = stderr.strip() if stderr else stdout.strip()
            if error_msg and "already running" in error_msg.lower():
                # 可能已有实例，尝试连接
                if self._wait_for_port(port, timeout=3):
                    return port
                else:
                    raise Exception("已有 weditor 实例但无法连接")
            else:
                raise Exception(f"进程启动失败: {error_msg}" if error_msg else "进程启动后立即退出，未知错误")

        # 等待端口开放
        if not self._wait_for_port(port, timeout=15):
            self.process.terminate()
            self.process = None
            raise Exception("启动超时，端口未开放")

        return port

    def start(self, port=17310):
        """兼容旧接口，直接创建 WebView（主线程调用）"""
        # 如果已有 WebView 直接返回
        if self.process is not None and self.process.poll() is None:
            if self.web_view:
                return self.web_view

        if self._is_port_open(port):
            self.web_view = QWebEngineView()
            self.web_view.load(QUrl(f"http://localhost:{port}"))
            return self.web_view

        # 启动并等待
        try:
            self._start_process_and_wait(port)
        except Exception as e:
            error_widget = QLabel(f"启动失败: {e}")
            error_widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
            error_widget.setStyleSheet("color: red; font-size: 14px;")
            return error_widget

        self.web_view = QWebEngineView()
        self.web_view.load(QUrl(f"http://localhost:{port}"))
        return self.web_view

    def stop(self):
        """终止 weditor 服务进程（加固版：terminate -> 等待 -> kill 三段式）。

        之前的版本 terminate 后无限 wait——进程僵死时会挂死退出流程；
        且依赖 atexit 的退出路径在 QWebEngine 应用里不可靠（WebEngine 退出时
        走 Chromium 清理可能跳过 atexit）。现在主防线是 Job Object
        （见 _bind_kill_on_close），这里的三段式只是主动清理的兜底。
        """
        if not self.process:
            return
        try:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=3)
                except Exception:
                    self.process.kill()          # terminate 没杀掉，升级为 kill
                    try:
                        self.process.wait(timeout=3)
                    except Exception:
                        pass                      # 最后交由 Job Object 兜底
        except Exception as e:
            print(f"[weditor] 停止服务进程异常（忽略）: {e}")
        finally:
            self.process = None
            self.web_view = None