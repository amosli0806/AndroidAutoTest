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
        self._last_cpu_time = 0.0
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
    def collect_sample(self, package: str, metrics: list) -> Optional[PerfSample]:
        """执行一次完整的采样。

        返回 None 表示**本轮所有启用指标都失败**（典型：设备通道失效）——
        调用方应跳过入库，不要拿一条全 0 的样本去污染曲线与统计。
        """
        sample = PerfSample(timestamp=time.time())

        if 'cpu' in metrics:
            try:
                sample.cpu_percent = self._collect_cpu(package)
            except Exception as e:
                sample.errors.append(f"cpu:{e}")

        if 'mem' in metrics:
            try:
                pss_mb, breakdown = self._collect_mem(package)
                sample.mem_pss_mb = pss_mb
                sample.mem_breakdown = breakdown
            except Exception as e:
                sample.errors.append(f"mem:{e}")

        if 'fps' in metrics:
            try:
                gfx_out = self._sh(f"dumpsys gfxinfo {package}", what="dumpsys gfxinfo")
                sample.fps = self._parse_fps(gfx_out)
            except Exception as e:
                sample.errors.append(f"fps:{e}")

        if 'traffic' in metrics:
            try:
                rx, tx = self._collect_traffic(package)
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
    def _collect_cpu(self, package: str) -> float:
        """
        通过 /proc/<pid>/stat 差分计算进程 CPU 占用率（Linux 通用，不依赖 dumpsys）。
        首采样返回 0（无基准）；后续按 (Δutime+Δstime)/Δt 计算。

        拿不到数据一律抛 PerfShellError（**不再静默返回 0**）—— 见 _sh 的说明。
        """
        import time as _time
        # pidof：命令执行失败抛异常（通道问题）；命令成功但没输出 = 应用确实没在跑
        pid_out = self._sh(f"pidof {package}", require_output=False).strip()
        if not pid_out:
            raise PerfShellError(f"应用未在运行（pidof 无输出）：{package}")
        pid = pid_out.split()[0]

        stat = self._sh(f"cat /proc/{pid}/stat", what=f"/proc/{pid}/stat")

        # 第 14/15 项是 utime/stime，需从最后一个 ")" 之后切（comm 里可能有空格/括号）
        rp = stat.rfind(")")
        if rp < 0:
            raise PerfShellError(f"/proc/{pid}/stat 格式异常")
        fields = stat[rp + 2:].split()
        if len(fields) < 13:
            raise PerfShellError(f"/proc/{pid}/stat 字段不足（{len(fields)} 项）")
        total_ticks = int(fields[11]) + int(fields[12])
        now = _time.time()

        if self._last_cpu_ticks is None:
            self._last_cpu_ticks = total_ticks
            self._last_cpu_time = now
            return 0.0

        delta_ticks = total_ticks - self._last_cpu_ticks
        delta_sec = now - self._last_cpu_time
        self._last_cpu_ticks = total_ticks
        self._last_cpu_time = now

        if delta_sec <= 0 or delta_ticks < 0:
            return 0.0
        cpu_sec = delta_ticks / self._cpu_ticks_per_sec
        percent = cpu_sec / delta_sec * 100.0

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
    def _collect_mem(self, package: str) -> tuple:
        """
        采集应用内存，返回 (pss_mb, breakdown)：
          - pss_mb：应用 TOTAL PSS（MB）
          - breakdown：Android Studio 风格分类拆解 dict，如
            {'Java': 61.8, 'Native': 12.3, 'Graphics': 5.0, ...}（单位 MB）
        """
        out = self._sh(f"dumpsys meminfo {package}", what="dumpsys meminfo")
        # 应用没在跑时 dumpsys 会这么回 —— 给出明确结论，而不是记一个 0 内存
        if "No process found" in out or "Unknown package" in out:
            raise PerfShellError(f"应用未在运行（dumpsys meminfo 无进程）：{package}")
        # TOTAL PSS 优先取 App Summary，回退到进程明细表 TOTAL 行
        m = re.search(r"TOTAL PSS:\s*(\d+)", out)
        if not m:
            m = re.search(r"TOTAL\s+(\d+)", out)
        if not m:
            raise PerfShellError(f"dumpsys meminfo 输出里没有 TOTAL PSS：{package}")
        pss_mb = int(m.group(1)) / 1024.0  # KB -> MB
        breakdown = self._parse_mem_breakdown(out)
        return pss_mb, breakdown

    def _parse_mem_breakdown(self, out: str) -> dict:
        """把 dumpsys meminfo 输出拆成 Android Studio 风格分类（单位 MB）。

        优先解析 App Summary（Android 8.0+，分类与 Android Studio 一一对应）：
          Java Heap / Native Heap / Code / Stack / Graphics / Private Other / System
        无 App Summary 时回退到进程明细表，按行归类。
        """
        cats: dict = {}
        # Private Other 与 System 都归入 Others，累加合并
        for label, key in [
            ("Java Heap", "Java"),
            ("Native Heap", "Native"),
            ("Code", "Code"),
            ("Stack", "Stack"),
            ("Graphics", "Graphics"),
            ("Private Other", "Others"),
            ("System", "Others"),
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