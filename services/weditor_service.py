# services/weditor_service.py
import subprocess
import time
import os
import sys
import socket
import threading
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import QLabel
from PyQt6.QtCore import Qt
from utils.theme import ThemeMode


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
                [sys.executable, "-m", "weditor", "-p", str(port), "-q"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                text=True
            )
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
        if self.process:
            self.process.terminate()
            self.process.wait()
            self.process = None
            self.web_view = None