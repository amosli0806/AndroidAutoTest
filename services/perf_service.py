# services/perf_service.py
"""性能指标采集服务（基于 ADB）"""
import re
import time
import logging
from typing import Optional

from models.perf_model import PerfSample

logger = logging.getLogger(__name__)
from services.perf_compat import (
    GUI_TIMEOUT, WORKER_TIMEOUT, shell_text, shell_text_checked,
)


class PerfShellError(RuntimeError):
    """shell 执行失败（通道异常 / 超时）或**该有输出却没有**。

    与「命令成功但确实没输出」是两回事 —— 采集侧以前无法区分，于是失败被
    静默记成 0（内存卡片「当前 0MB」而峰值正常就是这么来的）。现在一律抛
    异常，由 collect_sample 记进 sample.errors。
    """


class PerfService:
    """封装所有性能指标的采集方法"""

    def __init__(self, device, compat):
        self.device = device
        self.compat = compat
        # FPS 采集需要记录上次的总帧数
        self._last_total_frames: Optional[int] = None
        self._last_fps_time = 0.0
        # 流量采集需要记录上次字节数
        self._last_rx = 0
        self._last_tx = 0
        # CPU 差分计算用
        self._last_cpu_ticks = None
        self._last_cpu_pids = None      # 上次采样的 PID 集合（变了就得重建基准）
        self._last_cpu_time = 0.0
        # 多进程：pid → 进程名（_find_pids 解析 ps 时记录，供逐进程 CPU 拆分）
        self._pid_names = {}
        self._last_ticks_map = None     # 上一次每个 PID 的累计 jiffies
        self._last_cpu_by_process = {}  # 上一次拆分出的 {进程名: CPU%}
        # /proc/<pid>/stat 的 utime/stime 单位是 USER_HZ，ARM/x86 恒为 100，
        # 与内核 CONFIG_HZ（这台车机是 250）无关 —— 2026-10-22 实测：
        # 按 100 算得 31.37%，与 top 的 32.6% 吻合；按 250 会算成 12.55%
        self._cpu_ticks_per_sec = 100
        self._cpu_core_count = None  # 首次采集时探测

    # ============================================================
    def _sh(self, cmd: str, require_output: bool = True,
            timeout: float = None, what: str = "") -> str:
        """带超时执行 shell；**执行失败或该有输出却没有，一律抛 PerfShellError**。

        这是「静默 0」的根治点：调用方要么拿到真数据，要么走异常分支被记进
        sample.errors，不允许悄悄变成 0 混进曲线和统计。
        """
        ok, out = shell_text_checked(
            self.device, cmd, timeout=WORKER_TIMEOUT if timeout is None else timeout)
        if not ok:
            raise PerfShellError(f"shell 执行失败（通道异常/超时）：{cmd}")
        if require_output and not out.strip():
            raise PerfShellError(f"无输出：{what or cmd}")
        return out

    # ============================================================
    # 设备信息：会话开始时采集一次，用于报告的「基本信息」
    # ============================================================
    def get_device_info(self) -> dict:
        """采集设备的分辨率 / 屏幕密度 / 安卓版本。

        全部走 adb shell，任一项失败只留空、不影响采集主流程。
        返回 {'resolution': '1080x2340', 'density': '480 dpi', 'android': '13'}。
        """
        info = {'resolution': '', 'density': '', 'android': ''}
        try:
            out = shell_text(self.device, "wm size", timeout=GUI_TIMEOUT)
            # 「Physical size: 1080x2340」或「Override size: …」都取最后一处匹配
            matches = re.findall(r"(\d{3,5}x\d{3,5})", out or "")
            if matches:
                info['resolution'] = matches[-1]
        except Exception as e:
            logger.debug("采集分辨率失败: %s", e)

        try:
            out = shell_text(self.device, "wm density", timeout=GUI_TIMEOUT)
            # 「Physical density: 480」或「Override density: 420」
            matches = re.findall(r"(\d{2,4})", out or "")
            if matches:
                info['density'] = f"{matches[-1]} dpi"
        except Exception as e:
            logger.debug("采集屏幕密度失败: %s", e)

        try:
            out = shell_text(self.device, "getprop ro.build.version.release", timeout=GUI_TIMEOUT)
            info['android'] = (out or "").strip()
        except Exception as e:
            logger.debug("采集安卓版本失败: %s", e)

        return info

    # ============================================================
    # 对外接口：一次性采集所有启用的指标
    # ============================================================
    def collect_sample(self, package, metrics: list) -> Optional[PerfSample]:
        """执行一次完整的采样。

        `package` 可以是单个进程名（str），也可以是**目标进程列表**（list）：
        列表时按 Baidu 性能报告的「目标进程」口径，CPU / 内存对这些进程求和，
        FPS / 流量仍用第一个（主进程）—— 流量本身按 UID 统计，已覆盖整个应用。

        返回 None 表示**本轮所有启用指标都失败**（典型：设备通道失效）——
        调用方应跳过入库，不要拿一条全 0 的样本去污染曲线与统计。
        """
        packages = self._as_package_list(package)
        if not packages:
            raise PerfShellError("未指定目标进程")
        primary = packages[0]
        sample = PerfSample(timestamp=time.time())

        # 本轮只需要一次 ps -A：CPU 与内存都按「目标进程集合」取数
        pids = []
        if ('cpu' in metrics) or ('mem' in metrics):
            try:
                pids = self._find_pids(packages)
            except Exception:
                pids = []

        if 'cpu' in metrics:
            try:
                cpu = self._collect_cpu(primary, pids)
                if cpu is None:
                    # 首采 / 进程集合变化：本轮只重建基准，不产出数据点
                    sample.errors.append("cpu:本轮无有效值（首采或进程集合变化）")
                else:
                    sample.cpu_percent = cpu
                    # 多进程：逐进程 CPU 一起入库，报告据此画分进程曲线/统计
                    sample.cpu_by_process = dict(self._last_cpu_by_process or {})
            except Exception as e:
                sample.errors.append(f"cpu:{e}")

        if 'mem' in metrics:
            try:
                pss_mb, breakdown, res_mb, by_proc = self._collect_mem(primary, pids)
                sample.mem_pss_mb = pss_mb
                sample.mem_res_mb = res_mb
                sample.mem_breakdown = breakdown
                # 多进程：逐进程明细一起入库（报告据此按进程分行展示）
                sample.mem_by_process = by_proc
            except Exception as e:
                sample.errors.append(f"mem:{e}")

        if 'fps' in metrics:
            try:
                gfx_out = self._sh(f"dumpsys gfxinfo {primary}", what="dumpsys gfxinfo")
                sample.fps = self._parse_fps(gfx_out)
            except Exception as e:
                sample.errors.append(f"fps:{e}")

        if 'traffic' in metrics:
            try:
                rx, tx = self._collect_traffic(primary)
                sample.rx_bytes = rx
                sample.tx_bytes = tx
            except Exception as e:
                sample.errors.append(f"traffic:{e}")

        # 全部启用指标都失败 → 这次采样没有价值，丢弃（避免 0 值污染曲线/统计，
        # 也让「设备失联」这件事在采样层就显性化，交给调用方判定重连）
        if metrics and len(sample.errors) >= len(metrics):
            logger.warning("[PerfService] 本轮 %d 项指标全部采集失败，样本丢弃：%s",
                           len(metrics), sample.errors)
            return None

        return sample

    # ============================================================
    # CPU
    # ============================================================
    @staticmethod
    def _as_package_list(package) -> list:
        """str / list 统一成进程名列表（去空、保序）。"""
        if isinstance(package, str):
            return [package] if package else []
        return [p for p in (package or []) if p]

    def _find_pids(self, packages) -> list:
        """一次 `ps -A` 解析**多个目标进程**的 PID（精确匹配，不扩展子进程）。

        多进程监控时只跑一次 adb，避免 N 个进程 N 次往返；
        一个都没匹配上时才逐个 `pidof` 兜底（老 ROM 的 ps 不认 -A）。
        """
        names = set(packages)
        pids = []
        self._pid_names = {}               # 每次重解析，避免 PID 复用留下旧名字
        try:
            out = self._sh("ps -A", what="ps -A")
        except PerfShellError:
            out = ""
        for line in out.splitlines():
            parts = line.split()
            if len(parts) < 3 or not parts[1].isdigit():
                continue
            if parts[-1] in names:          # 精确匹配，不扩展子进程
                pids.append(parts[1])
                self._pid_names[parts[1]] = parts[-1]

        if not pids:
            for pkg in packages:
                pid_out = self._sh(f"pidof {pkg}", require_output=False).strip()
                matched = self._verify_by_cmdline(
                    pid_out.split() if pid_out else [], pkg)
                for pid in matched:
                    self._pid_names[pid] = pkg
                pids.extend(matched)
        return pids

    def _find_app_pids(self, package: str) -> list:
        """返回**名称与 package 完全一致**的进程 PID（只这一个进程，不做合并）。

        口径：进程下拉里每个进程是一行，**选谁就算谁** —— 与内存、帧率一致。
        以前这里会把 `包名:*` 的子进程一起算（CPU 合并整个应用），结果选子进程时
        数值里还混着主进程；需要整包数据时，分别选各进程累加即可（2026-10-23 用户要求）。

        两个匹配陷阱（都会把同应用的其它进程带进来，必须防）：
          * `ps -A` 的 NAME 列在部分 ROM 上被截断到 15 字符；
          * `pidof` 是**前缀匹配**，`pidof com.foo:remote` 可能连带 `com.foo`。

        所以 `ps` 走精确匹配；退回 `pidof` 时再用 `/proc/<pid>/cmdline` 复核进程名。

        `ps -A` 的列是 USER PID PPID VSZ RSS WCHAN ADDR S NAME，
        所以 PID 取第 2 列、进程名取最后一列。
        """
        return self._find_pids([package])

    def _verify_by_cmdline(self, pids, package) -> list:
        """用 `/proc/<pid>/cmdline` 复核进程名，只留下**完全等于 package** 的 PID。

        必要性：`ps` 的 NAME 可能被截断、`pidof` 是前缀匹配，两者都可能把
        同应用的其它进程带进来（选中子进程却算出整包的 CPU）。
        cmdline 读不到（权限/老系统）时原样返回，不让功能退化成采不到。
        """
        if not pids:
            return pids
        script = " ; ".join(
            f"echo -n '{pid}|'; tr '\0' ' ' < /proc/{pid}/cmdline 2>/dev/null; echo"
            for pid in pids
        )
        try:
            out = self._sh(script, require_output=False)
        except Exception:
            return pids
        keep = []
        for line in out.splitlines():
            pid, sep, cmd = line.partition("|")
            if not sep:
                continue
            # cmdline 是「进程名 + 参数」，用空格连成一串；第一个 token 才是进程名
            if cmd.strip().split(" ")[0] == package:
                keep.append(pid.strip())
        return keep or pids

    def _read_proc_ticks(self, pids: list):
        """一次 adb 往返读出多个进程的累计 jiffies，返回 {pid: utime+stime}。

        返回 None 表示"读到的 stat 行数与 PID 数对不上"（有进程刚好起停），
        调用方应重建基准，而不是拿一个残缺的和对差值。
        """
        paths = " ".join(f"/proc/{p}/stat" for p in pids)
        # 2>/dev/null + true：进程恰好退出时不要让 cat 的退出码把整轮判成失败
        out = self._sh(f"cat {paths} 2>/dev/null; true", what="多个 /proc/<pid>/stat")
        lines = [ln for ln in out.splitlines() if ln.strip()]
        if len(lines) != len(pids):
            return None

        ticks = {}
        for pid, line in zip(pids, lines):
            # 第 14/15 项是 utime/stime，需从最后一个 ")" 之后切
            # （comm 里可能有空格/括号）
            rp = line.rfind(")")
            if rp < 0:
                return None
            fields = line[rp + 2:].split()
            if len(fields) < 13:
                return None
            ticks[pid] = int(fields[11]) + int(fields[12])
        return ticks

    def _sum_proc_ticks(self, pids: list):
        """多个进程的累计 jiffies 之和（None 表示样本不完整）。"""
        ticks = self._read_proc_ticks(pids)
        return None if ticks is None else sum(ticks.values())

    def _collect_cpu(self, package: str, pids=None):
        """按 /proc/<pid>/stat 差分计算**所选进程**的 CPU 占用率（不合并同应用其它进程）。

        返回 None 表示"本轮没有有效值"：首采、进程集合变化、或时钟/PID 异常，
        此时只重建基准，**不产出数据点**（否则曲线会出现假的 0 或假尖峰）。

        拿不到进程一律抛 PerfShellError（不再静默返回 0）—— 见 _sh 的说明。
        数值口径是**多核累计**（6 核设备最高 600%），与界面上的「多核累计」一致。
        """
        import time as _time

        if pids is None:
            pids = self._find_app_pids(package)
        if not pids:
            raise PerfShellError(f"应用未在运行（找不到进程）：{package}")

        ticks_map = self._read_proc_ticks(pids)
        total_ticks = None if ticks_map is None else sum(ticks_map.values())
        key = tuple(sorted(pids))
        # 单调时钟：车机启动/联网时会校时，用 time.time() 会让差值忽大忽小
        now = _time.monotonic()

        # 首次采样，或进程集合变了（子进程起/停）：累计值没有可比性，重建基准
        if total_ticks is None or self._last_cpu_pids != key or self._last_cpu_ticks is None:
            self._last_cpu_pids = key
            self._last_cpu_ticks = total_ticks
            self._last_ticks_map = ticks_map
            self._last_cpu_time = now
            self._last_cpu_by_process = {}
            return None

        delta_ticks = total_ticks - self._last_cpu_ticks
        delta_sec = now - self._last_cpu_time
        prev_map = self._last_ticks_map or {}
        by_process = {}
        for pid, ticks in (ticks_map or {}).items():
            prev = prev_map.get(pid)
            if prev is None or ticks < prev:
                continue
            name = self._pid_names.get(pid) or pid
            by_process[name] = by_process.get(name, 0) + (ticks - prev)

        self._last_cpu_pids = key
        self._last_cpu_ticks = total_ticks
        self._last_ticks_map = ticks_map
        self._last_cpu_time = now

        if delta_sec <= 0 or delta_ticks < 0:
            # 时钟异常 / PID 被复用导致累计值回退：本轮作废，下一轮从新基准算
            self._last_cpu_by_process = {}
            return None

        cpu_sec = delta_ticks / self._cpu_ticks_per_sec
        percent = cpu_sec / delta_sec * 100.0

        # 逐进程拆分（同一进程名的多个 PID 合并）：给报告画多进程曲线 / 统计用
        self._last_cpu_by_process = {
            name: round(ticks / self._cpu_ticks_per_sec / delta_sec * 100.0, 2)
            for name, ticks in by_process.items()
        }

        # 首次采集时探测设备核数（决定合理上限：核数 × 100%）
        if self._cpu_core_count is None:
            try:
                cores_out = self._sh("nproc", require_output=False).strip()
                self._cpu_core_count = int(cores_out) if cores_out else 8
            except Exception:
                self._cpu_core_count = 8  # 保守默认

        max_percent = self._cpu_core_count * 100.0
        return round(min(percent, max_percent), 2)

    # ============================================================
    # 内存
    # ============================================================
    def _collect_mem(self, package: str, pids=None) -> tuple:
        """
        采集**目标进程**的内存，返回 (pss_mb, breakdown, res_mb, by_process)：
          - pss_mb：这些进程的 TOTAL PSS 之和（MB）
          - breakdown：分类拆解 dict（各进程同名分类相加），如
            {'Java': 61.8, 'Native': 12.3, 'System': 20.0, ...}（单位 MB）
          - res_mb：这些进程的 TOTAL RSS 之和（MB，报告里的 res 参数）
          - by_process：逐进程明细 {进程名: {'pss','res','breakdown'}}。
            本来就要逐个 PID dump，明细顺手留下即可（不再只剩一个合计值）。

        为什么按 PID 取：`dumpsys meminfo <包名>` 给的是整个应用的 App Summary
        （含 `包名:*` 子进程）—— 选子进程会显示整包内存，选主进程也混着子进程。
        改成 `dumpsys meminfo <pid>` 只出这一个进程，和 CPU 口径一致（2026-10-25 用户要求）。
        多进程监控时逐个 PID 取回再求和（PSS 按进程分摊，求和即应用总占用）。
        某个进程刚好退出就跳过它；全部取不到才算失败。
        拿不到 PID 时退回包名 dump（宁可给整包数，也不让采集直接失败）。
        """
        if pids is None:
            pids = self._find_app_pids(package)
        targets = list(pids) or [package]
        total_pss = 0.0
        total_res = 0.0
        merged = {}
        by_process = {}
        got = 0
        dumped = 0
        for target in targets:
            out = self._sh(f"dumpsys meminfo {target}", what="dumpsys meminfo")
            # 应用没在跑时 dumpsys 会这么回 —— 单个进程退出时跳过，不让整轮失败
            if "No process found" in out or "Unknown package" in out:
                continue
            dumped += 1
            # TOTAL PSS 优先取 App Summary，回退到进程明细表 TOTAL 行
            m = re.search(r"TOTAL PSS:\s*(\d+)", out)
            if not m:
                m = re.search(r"TOTAL\s+(\d+)", out)
            if not m:
                continue
            got += 1
            one_pss = int(m.group(1)) / 1024.0          # KB -> MB
            one_res = 0.0
            rss = re.search(r"TOTAL RSS:\s*(\d+)", out)
            if rss:
                one_res = int(rss.group(1)) / 1024.0
                total_res += one_res
            one_bd = self._parse_mem_breakdown(out)
            total_pss += one_pss
            for key, val in one_bd.items():
                merged[key] = merged.get(key, 0.0) + val
            # 进程名：_find_pids 已把 pid -> 名字 存好；拿不到就用 pid 本身
            name = (getattr(self, "_pid_names", {}) or {}).get(str(target), str(target))
            by_process[name] = {"pss": one_pss, "res": one_res,
                                "breakdown": dict(one_bd)}
        if not got:
            if dumped:
                raise PerfShellError(f"dumpsys meminfo 输出里没有 TOTAL PSS：{package}")
            raise PerfShellError(f"应用未在运行（dumpsys meminfo 无进程）：{package}")
        return total_pss, merged, total_res, by_process

    def _parse_mem_breakdown(self, out: str) -> dict:
        """把 dumpsys meminfo 输出拆成 Android Studio 风格分类（单位 MB）。

        优先解析 App Summary（Android 8.0+，分类与 Android Studio 一一对应）：
          Java Heap / Native Heap / Code / Stack / Graphics / Private Other / System
        无 App Summary 时回退到进程明细表，按行归类。
        """
        cats: dict = {}
        # Private Other 与 System 分开记：报告要按 Baidu 口径分别展示 other_mem / system_mem
        for label, key in [
            ("Java Heap", "Java"),
            ("Native Heap", "Native"),
            ("Code", "Code"),
            ("Stack", "Stack"),
            ("Graphics", "Graphics"),
            ("Private Other", "Others"),
            ("System", "System"),
        ]:
            m = re.search(rf"{label}:\s*(\d+)", out)
            if m:
                cats[key] = round(cats.get(key, 0.0) + int(m.group(1)) / 1024.0, 2)
        if cats:
            return cats
        return self._parse_mem_table(out)

    def _parse_mem_table(self, out: str) -> dict:
        """回退路径：无 App Summary 的旧版本，解析进程明细表并按行归类。"""
        row_map = {
            "Native Heap": "Native",
            "Dalvik Heap": "Java",
            "Gfx dev": "Graphics",
            "GL mtrack": "Graphics",
            "EGL mtrack": "Graphics",
            "Stack": "Stack",
            ".so mmap": "Code",
            ".jar mmap": "Code",
            ".apk mmap": "Code",
            ".ttf mmap": "Code",
            ".dex mmap": "Code",
            ".oat mmap": "Code",
            ".art mmap": "Java",
        }
        skip_prefix = (
            "Pss", "------", "TOTAL", "** MEMINFO", "Objects", "SQL",
            "DATABASES", "App Summary", "Java Heap:", "Native Heap:",
            "Code:", "Stack:", "Graphics:", "Private Other:", "System:",
            "TOTAL PSS", "TOTAL RSS",
        )
        cats = {"Java": 0.0, "Native": 0.0, "Graphics": 0.0,
                "Stack": 0.0, "Code": 0.0, "Others": 0.0}
        for line in out.splitlines():
            s = line.strip()
            if not s or s.startswith(skip_prefix):
                continue
            m = re.match(r"^([A-Za-z.\s]+?)\s+(\d+)", s)
            if not m:
                continue
            name = m.group(1).strip()
            val = int(m.group(2)) / 1024.0  # KB -> MB
            cats[row_map.get(name, "Others")] += val
        return {k: round(v, 2) for k, v in cats.items() if v > 0}

    # ============================================================
    # FPS
    # ============================================================
    def _collect_fps(self, package: str) -> int:
        out = self._sh(f"dumpsys gfxinfo {package}", what="dumpsys gfxinfo")
        return self._parse_fps(out)

    def _parse_fps(self, out: str) -> int:
        if not out:
            return 0
        m = re.search(r"Total frames rendered:\s*(\d+)", out)
        if not m:
            return 0
        total_frames = int(m.group(1))
        now = time.time()
        if self._last_total_frames is None:
            self._last_total_frames = total_frames
            self._last_fps_time = now
            return 0
        delta_frames = total_frames - self._last_total_frames
        delta_time = now - self._last_fps_time
        self._last_total_frames = total_frames
        self._last_fps_time = now
        if delta_time <= 0 or delta_frames < 0:
            return 0
        return min(int(delta_frames / delta_time), 240)

    # ============================================================
    # 流量
    # ============================================================
    def _collect_traffic(self, package: str) -> tuple:
        uid = self.compat.get_package_uid(package)
        if uid < 0:
            # 以前这里静默回退成上一次的值（通常是 0），于是「通道挂了」也能凑出
            # 一条 traffic=0 的样本，把「全部指标失败」的判定绕过去。改成抛异常，
            # 让流量这一项如实算作失败。
            raise PerfShellError(f"拿不到应用 UID，无法采集流量：{package}")

        method = self.compat.get_traffic_collect_method()

        if method == "uid_stat":
            # Android 9 及以下：直接读 /proc/uid_stat
            try:
                # require_output=False：命令成功但文件为空 = 该 uid 还没有流量记录，记 0 合理；
                # 命令**执行失败**（通道异常/超时）会抛异常 → 由 collect_sample 记进 sample.errors
                rx = int((self._sh(f"cat /proc/uid_stat/{uid}/tcp_rcv",
                                   require_output=False) or "0").strip())
                tx = int((self._sh(f"cat /proc/uid_stat/{uid}/tcp_snd",
                                   require_output=False) or "0").strip())
                self._last_rx, self._last_tx = rx, tx
                return rx, tx
            except (ValueError, TypeError):
                return self._last_rx, self._last_tx

        elif method == "qtaguid":
            # Android 10~12：优先 xt_qtaguid。这个文件在部分内核/ROM 上已被移除，
            # 因此**读不到就正常降级到 netstats**，不算错误。
            ok, out = shell_text_checked(self.device, "cat /proc/net/xt_qtaguid/stats",
                                         timeout=WORKER_TIMEOUT)
            if ok:
                rx, tx = self._parse_qtaguid(out, uid)
                if rx >= 0:
                    self._last_rx, self._last_tx = rx, tx
                    return rx, tx
            # 降级到 netstats
            return self._collect_netstats(uid)

        else:
            # Android 13+：只能走 netstats
            return self._collect_netstats(uid)

    def _collect_netstats(self, uid: int) -> tuple:
        # 走到这里说明 uid_stat / qtaguid 都不可用，netstats 是最后一手 ——
        # 它再失败就是真的拿不到数据，抛异常让上层记录，不再静默沿用旧值。
        out = self._sh("dumpsys netstats detail", what="dumpsys netstats detail")
        rx, tx = self._parse_netstats(out, uid)
        if rx >= 0:
            self._last_rx, self._last_tx = rx, tx
            return rx, tx
        # 输出正常但没有该 uid 的记录：说明它确实还没有流量
        return self._last_rx, self._last_tx

    def _parse_qtaguid(self, out: str, uid: int) -> tuple:
        if not out:
            return -1, -1
        rx_total = 0
        tx_total = 0
        for line in out.splitlines():
            parts = line.split()
            # 标准行 7~9 列，这里要求至少 8 列
            if len(parts) < 8:
                continue
            try:
                # 列 3 通常是 uid；部分 ROM 是 "uid_tag_int" 形式
                col3 = parts[3]
                if '_' in col3:
                    col3 = col3.split('_')[0]
                if int(col3) != uid:
                    continue
                rx_total += int(parts[5])
                tx_total += int(parts[7])
            except (ValueError, IndexError):
                continue
        return rx_total, tx_total

    def _parse_netstats(self, out: str, uid: int) -> tuple:
        """解析 dumpsys netstats detail 输出。
        AOSP（Android 4.4~15）字段为 rb=/tb=；
        部分 ROM 可能为 rxBytes=/txBytes=。
        逐行扫描：ident 行确定当前 uid，后续 st 行累加 rb/tb。
        """
        if not out:
            return -1, -1
        rx_total = 0
        tx_total = 0

        current_uid = None
        # AOSP 格式：rb=... rp=... tb=... tp=...
        pat_rb_tb = re.compile(r"\brb=(\d+)\b[^\n]*?\btb=(\d+)\b")
        # 兼容格式：rxBytes=... txBytes=...
        pat_rx_tx = re.compile(r"\brxBytes=(\d+)\b[^\n]*?\btxBytes=(\d+)\b")
        uid_pat = re.compile(r"\buid=(\d+)\b")

        for line in out.splitlines():
            m = uid_pat.search(line)
            if m:
                current_uid = int(m.group(1))
                # ident 行本身也可能带数据，继续往下走
                # 但大多数情况下 ident 行里没有 rb/tb，跳过
                continue

            if current_uid != uid:
                continue

            m = pat_rb_tb.search(line)
            if m:
                rx_total += int(m.group(1))
                tx_total += int(m.group(2))
                continue
            m = pat_rx_tx.search(line)
            if m:
                rx_total += int(m.group(1))
                tx_total += int(m.group(2))

        if rx_total > 0 or tx_total > 0:
            return rx_total, tx_total
        return -1, -1

    # ============================================================
    # 启动耗时（独立功能）
    # ============================================================
    def measure_cold_launch(self, package: str, activity: str = None) -> int:
        """
        测量冷启动耗时。返回毫秒，失败返回 -1。
        activity 为 None 时自动获取主 Activity。
        """
        try:
            # 先停掉应用
            shell_text(self.device, f"am force-stop {package}", timeout=WORKER_TIMEOUT)
            time.sleep(0.5)
            return self._do_launch(package, activity)
        except Exception as e:
            logger.warning(f"[PerfService] 冷启动失败: {e}")
            return -1

    def measure_warm_launch(self, package: str, activity: str = None) -> int:
        """测量热启动耗时。

        刚冷启动完的应用可能仍在加载中，此时 am start 只是把已有任务切到前台，
        输出里没有 TotalTime（LaunchState: UNKNOWN），解析不到会返回 -1；
        等应用加载稳定后才能测到 LaunchState: HOT 的真实热启动耗时，
        因此首测失败时自动等待重试。
        """
        try:
            for attempt in range(4):
                # 按 Home 键回桌面，但应用仍在内存中
                shell_text(self.device, "input keyevent KEYCODE_HOME", timeout=WORKER_TIMEOUT)
                time.sleep(0.5 if attempt == 0 else 2.0)
                ms = self._do_launch(package, activity)
                if ms > 0:
                    return ms
                logger.debug(
                    f"[PerfService] 热启动第 {attempt + 1} 次未测到 TotalTime，"
                    f"{'等待后重试' if attempt < 3 else '放弃'}"
                )
            return -1
        except Exception as e:
            logger.warning(f"[PerfService] 热启动失败: {e}")
            return -1

    def _do_launch(self, package: str, activity: str = None) -> int:
        """执行 am start -W 并解析 TotalTime"""
        if activity is None:
            activity = self._get_launcher_activity(package)
            if not activity:
                return -1
        out = shell_text(
            self.device,
            f"am start -W -n {package}/{activity}",
            timeout=WORKER_TIMEOUT,
        )
        if not out:
            return -1
        # 解析 TotalTime: 1234
        m = re.search(r"TotalTime:\s*(\d+)", out)
        if m:
            return int(m.group(1))
        # 任务仅被切到前台时（LaunchState: UNKNOWN）输出里没有 TotalTime
        logger.debug(f"[PerfService] am start -W 输出未解析到 TotalTime: {out!r}")
        return -1

    def _get_launcher_activity(self, package: str) -> Optional[str]:
        """获取应用的主 Activity"""
        try:
            out = shell_text(
                self.device,
                f"cmd package resolve-activity --brief {package}",
                timeout=WORKER_TIMEOUT,
            )
            if out:
                lines = out.strip().splitlines()
                # 最后一行形如：com.example.app/.MainActivity
                last = lines[-1].strip()
                if '/' in last:
                    return last.split('/', 1)[1]
        except Exception:
            pass
        return None