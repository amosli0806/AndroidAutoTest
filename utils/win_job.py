# utils/win_job.py
"""Windows Job Object：把子进程绑成「父死子亡」。

为什么要有这个模块：应用可视化（weditor.exe）和 ADB 命令池（adb.exe）需要的能力完全一样
—— 虫师退出（正常 / 崩溃 / 被任务管理器强杀）时子进程必须跟着走，否则会留下孤儿进程。

对 ADB 命令池尤其要紧：指令是用 `shell=True` 拉起的，真正的 adb.exe 是中间 cmd.exe 的子
进程，`terminate()` 只杀得掉 cmd.exe。实测（2026-10-16）攒了 8 个 `adb logcat` 孤儿，
父进程 PID 全部已退出 —— 只有 Job Object 这种系统级「父死子亡」才能兜住崩溃/强杀这两条路径。
"""
import sys


def bind_kill_on_close(process) -> bool:
    """把 process 绑到 KILL_ON_JOB_CLOSE 的 Job Object 上。

    返回 True 表示「已绑定」或「当前平台不需要绑定」（非 Windows）；False 表示绑定失败
    （调用方可以据此降级，但不要当成致命错误 —— 退出清理还有 stop_all / atexit 兜底）。

    句柄故意不关：持有在当前进程，虫师退出时句柄关闭 → 系统自动杀掉 Job 里的所有进程。
    """
    if sys.platform != "win32":
        return True
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
            return False
        info = _EXTENDED_LIMITS()
        info.BasicLimitInformation.LimitFlags = 0x2000   # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(
                job, 9, ctypes.byref(info), ctypes.sizeof(info)):   # 9 = ExtendedLimitInformation
            k32.CloseHandle(job)
            return False
        if not k32.AssignProcessToJobObject(job, int(process._handle)):
            k32.CloseHandle(job)
            return False
        # 句柄故意不关：持有在当前进程，虫师退出时句柄关闭 -> 触发 KILL_ON_JOB_CLOSE
        process._kill_on_close_job = job
        return True
    except Exception:
        return False
