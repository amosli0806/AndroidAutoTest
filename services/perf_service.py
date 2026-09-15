# services/perf_service.py
"""性能指标采集服务（基于 ADB）"""
import re
import time
import logging
from typing import Optional

from models.perf_model import PerfSample

logger = logging.getLogger(__name__)
from services.perf_compat import shell_text

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
        # 卡顿累计
        self._last_jank_count = 0
        # CPU 差分计算用
        self._last_cpu_ticks = None
        self._last_cpu_time = 0.0
        self._cpu_ticks_per_sec = 100
        self._cpu_core_count = None  # 首次采集时探测

    # ============================================================
    # 对外接口：一次性采集所有启用的指标
    # ============================================================
    def collect_sample(self, package: str, metrics: list) -> PerfSample:
        """执行一次完整的采样"""
        sample = PerfSample(timestamp=time.time())

        if 'cpu' in metrics:
            try:
                sample.cpu_percent = self._collect_cpu(package)
            except Exception as e:
                sample.errors.append(f"cpu:{e}")

        if 'mem' in metrics:
            try:
                sample.mem_pss_mb = self._collect_mem(package)
            except Exception as e:
                sample.errors.append(f"mem:{e}")

        # FPS 与卡顿共用一份 dumpsys gfxinfo 输出，避免重复采集
        if 'fps' in metrics or 'jank' in metrics:
            try:
                gfx_out = shell_text(self.device, f"dumpsys gfxinfo {package}")
            except Exception as e:
                gfx_out = ""
                if 'fps' in metrics:
                    sample.errors.append(f"fps:{e}")
                if 'jank' in metrics:
                    sample.errors.append(f"jank:{e}")

            if 'fps' in metrics:
                try:
                    sample.fps = self._parse_fps(gfx_out)
                except Exception as e:
                    sample.errors.append(f"fps:{e}")
            if 'jank' in metrics:
                try:
                    sample.jank_count = self._parse_jank(gfx_out)
                    # 同时记录累计总帧数，用于精确计算卡顿率
                    sample.total_frames = self._parse_total_frames(gfx_out)
                except Exception as e:
                    sample.errors.append(f"jank:{e}")

        if 'traffic' in metrics:
            try:
                rx, tx = self._collect_traffic(package)
                sample.rx_bytes = rx
                sample.tx_bytes = tx
            except Exception as e:
                sample.errors.append(f"traffic:{e}")

        return sample

    # ============================================================
    # CPU
    # ============================================================
    def _collect_cpu(self, package: str) -> float:
        """
        通过 /proc/<pid>/stat 差分计算进程 CPU 占用率（Linux 通用，不依赖 dumpsys）。
        首采样返回 0（无基准）；后续按 (Δutime+Δstime)/Δt 计算。
        """
        import time as _time
        try:
            pid_out = shell_text(self.device, f"pidof {package}").strip()
            if not pid_out:
                return 0.0
            pid = pid_out.split()[0]

            stat = shell_text(self.device, f"cat /proc/{pid}/stat")
            if not stat:
                return 0.0

            # 第 14/15 项是 utime/stime，需从最后一个 ")" 之后切（comm 里可能有空格/括号）
            rp = stat.rfind(")")
            if rp < 0:
                return 0.0
            fields = stat[rp + 2:].split()
            if len(fields) < 13:
                return 0.0
            utime = int(fields[11])
            stime = int(fields[12])
            total_ticks = utime + stime
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
                    cores_out = shell_text(self.device, "nproc").strip()
                    self._cpu_core_count = int(cores_out) if cores_out else 8
                except Exception:
                    self._cpu_core_count = 8  # 保守默认

            max_percent = self._cpu_core_count * 100.0
            return round(min(percent, max_percent), 2)
        except Exception as e:
            logger.warning(f"[PerfService] CPU 差分采集失败: {e}")
            return 0.0

    # ============================================================
    # 内存
    # ============================================================
    def _collect_mem(self, package: str) -> float:
        """
        通过 dumpsys meminfo <pkg> 采集 TOTAL PSS（单位 KB）。
        输出中找：
          TOTAL    45000    42000    1000    ... (KB)
        """
        out = shell_text(self.device, f"dumpsys meminfo {package}")
        if not out:
            return 0.0
        # 优先找带 pid 的块
        m = re.search(r"TOTAL\s+(\d+)", out)
        if m:
            return int(m.group(1)) / 1024.0  # KB -> MB
        return 0.0

    # ============================================================
    # FPS
    # ============================================================
    def _collect_fps(self, package: str) -> int:
        out = shell_text(self.device, f"dumpsys gfxinfo {package}")
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

    def _parse_total_frames(self, out: str) -> int:
        """从 gfxinfo 输出里解析累计总帧数"""
        if not out:
            return 0
        m = re.search(r"Total frames rendered:\s*(\d+)", out)
        if m:
            return int(m.group(1))
        return 0

    # ============================================================
    # 卡顿
    # ============================================================
    def _collect_jank(self, package: str) -> int:
        out = shell_text(self.device, f"dumpsys gfxinfo {package}")
        return self._parse_jank(out)

    def _parse_jank(self, out: str) -> int:
        if not out:
            return self._last_jank_count
        m = re.search(r"Janky frames:\s*(\d+)", out)
        if m:
            count = int(m.group(1))
            self._last_jank_count = count
            return count
        return self._last_jank_count

    # ============================================================
    # 流量
    # ============================================================
    def _collect_traffic(self, package: str) -> tuple:
        uid = self.compat.get_package_uid(package)
        if uid < 0:
            return self._last_rx, self._last_tx

        method = self.compat.get_traffic_collect_method()

        if method == "uid_stat":
            # Android 9 及以下：直接读 /proc/uid_stat
            try:
                rx = int((shell_text(self.device, f"cat /proc/uid_stat/{uid}/tcp_rcv") or "0").strip())
                tx = int((shell_text(self.device, f"cat /proc/uid_stat/{uid}/tcp_snd") or "0").strip())
                self._last_rx, self._last_tx = rx, tx
                return rx, tx
            except (ValueError, TypeError):
                return self._last_rx, self._last_tx

        elif method == "qtaguid":
            # Android 10~12：优先 xt_qtaguid
            try:
                out = shell_text(self.device, "cat /proc/net/xt_qtaguid/stats")
                rx, tx = self._parse_qtaguid(out, uid)
                if rx >= 0:
                    self._last_rx, self._last_tx = rx, tx
                    return rx, tx
            except Exception:
                pass
            # 降级到 netstats
            return self._collect_netstats(uid)

        else:
            # Android 13+：只能走 netstats
            return self._collect_netstats(uid)

    def _collect_netstats(self, uid: int) -> tuple:
        try:
            out = shell_text(self.device, "dumpsys netstats detail")
            rx, tx = self._parse_netstats(out, uid)
            if rx >= 0:
                self._last_rx, self._last_tx = rx, tx
                return rx, tx
        except Exception:
            pass
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
            shell_text(self.device, f"am force-stop {package}")
            time.sleep(0.5)
            return self._do_launch(package, activity)
        except Exception as e:
            logger.warning(f"[PerfService] 冷启动失败: {e}")
            return -1

    def measure_warm_launch(self, package: str, activity: str = None) -> int:
        """测量热启动耗时"""
        try:
            # 按 Home 键回桌面，但应用仍在内存中
            shell_text(self.device, "input keyevent KEYCODE_HOME")
            time.sleep(0.5)
            return self._do_launch(package, activity)
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
            f"am start -W -n {package}/{activity}"
        )
        if not out:
            return -1
        # 解析 TotalTime: 1234
        m = re.search(r"TotalTime:\s*(\d+)", out)
        if m:
            return int(m.group(1))
        return -1

    def _get_launcher_activity(self, package: str) -> Optional[str]:
        """获取应用的主 Activity"""
        try:
            out = shell_text(
                self.device,
                f"cmd package resolve-activity --brief {package}"
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