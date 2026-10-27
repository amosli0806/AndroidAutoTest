# services/perf_probes/threads.py
"""线程探针（所选进程）：线程总数 / 活跃线程数 / 每线程 CPU。

数据源：`cat /proc/<pid>/task/*/stat` —— **一次 adb 往返**读回该进程的所有线程。
线程名就在 stat 的 `(comm)` 字段里，不必再逐个读 comm（266 个线程时省 266 次读）。

口径：多核累计（与「CPU」卡一致），单线程最高 100%；
sum(所有线程的 CPU) 应约等于进程 CPU —— 报告里拿它做自洽校验。

差分基准按 TID 保存：
  * 进程重启（PID 变了）→ 整组基准重建，本轮不产出；
  * 新线程没有基准 → 本轮记 0（计入线程总数、不计入活跃数）；
  * 线程消失 → 直接忽略。

全量线程明细**边采边写 CSV**（不攒在内存里，长稳测试不会吃满内存）；
报告只保留每个采样点最忙的 TOP_N 个线程。
"""
import csv
import os
import time

from services.perf_compat import WORKER_TIMEOUT, shell_text_checked
from .base import PerfProbe, ProbeError

TOP_N = 15          # 报告 / 卡片只留最忙的 N 个线程
ACTIVE_MIN_CPU = 1.0  # 单线程 ≥1% 算"活跃"


def parse_task_stats(out: str) -> dict:
    """解析 `cat /proc/<pid>/task/*/stat` 的输出，返回 {tid: (线程名, utime+stime)}。

    每行格式与进程 stat 相同：`tid (comm) state ppid ... utime stime ...`。
    comm 里可能有空格/括号，所以从**第一个** `(` 到**最后一个** `)` 之间取名字。
    """
    rows = {}
    for line in (out or "").splitlines():
        line = line.strip()
        if not line:
            continue
        lp = line.find("(")
        rp = line.rfind(")")
        if lp <= 0 or rp <= lp:
            continue
        try:
            tid = int(line[:lp].strip())
        except ValueError:
            continue
        name = line[lp + 1:rp]
        fields = line[rp + 2:].split()
        if len(fields) < 13:          # utime/stime 在第 14/15 项
            continue
        try:
            ticks = int(fields[11]) + int(fields[12])
        except ValueError:
            continue
        rows[tid] = (name, ticks)
    return rows


class ThreadsProbe(PerfProbe):
    key = "threads"
    title = "线程"
    scope = "process"
    default_interval = 15.0

    def __init__(self):
        super().__init__()
        self._prev = None          # {tid: ticks} 上一次的基准
        self._prev_time = 0.0
        self._prev_pid = ""
        self._csv_file = None
        self._csv_writer = None

    # ---------- 采集 ----------
    def sample_once(self):
        pid = ""
        if self._pid_of is not None:
            try:
                pid = str(self._pid_of() or "").strip()
            except Exception:
                pid = ""
        if not pid:
            raise ProbeError("未获取到所选进程 PID（应用可能没在运行）")

        ok, out = shell_text_checked(
            self.device, f"cat /proc/{pid}/task/*/stat", timeout=WORKER_TIMEOUT)
        if not ok or not out.strip():
            raise ProbeError(f"读取 /proc/{pid}/task/*/stat 失败（无权限或进程已退出）")

        stats = parse_task_stats(out)
        if not stats:
            raise ProbeError("线程 stat 解析为空（该设备可能不支持 /proc/<pid>/task）")

        now = time.monotonic()
        if self._prev is None or pid != self._prev_pid:
            self._prev = {tid: ticks for tid, (_n, ticks) in stats.items()}
            self._prev_time = now
            self._prev_pid = pid
            return None                       # 首轮 / 进程重启：只建基准

        delta_sec = now - self._prev_time
        if delta_sec <= 0:
            return None

        per = []
        for tid, (name, ticks) in stats.items():
            prev = self._prev.get(tid)
            if prev is None:
                continue                      # 新线程：本轮没有基准
            delta = ticks - prev
            if delta < 0:
                continue                      # TID 复用：跳过
            cpu = delta / 100.0 / delta_sec * 100.0
            per.append((tid, name, cpu))

        self._prev = {tid: ticks for tid, (_n, ticks) in stats.items()}
        self._prev_time = now

        total = sum(x[2] for x in per)
        active = sum(1 for x in per if x[2] >= ACTIVE_MIN_CPU)
        top = sorted(per, key=lambda x: x[2], reverse=True)[:TOP_N]
        ts = time.time()

        self._write_rows(ts, per)

        return {
            "ts": ts,
            "pid": pid,
            "thread_total": len(stats),
            "thread_active": active,
            "cpu_total": round(total, 2),
            "top": [
                {
                    "tid": tid,
                    "name": name,
                    "cpu": round(cpu, 2),
                    "share": round(cpu / total * 100.0, 1) if total > 0 else 0.0,
                }
                for tid, name, cpu in top
            ],
        }

    # ---------- 全量明细：边采边写，避免长稳测试吃内存 ----------
    def _write_rows(self, ts, per):
        if self._csv_writer is None:
            self._open_csv()
        if self._csv_writer is None:
            return
        try:
            for tid, name, cpu in per:
                self._csv_writer.writerow([f"{ts:.3f}", tid, name, f"{cpu:.2f}"])
            self._csv_file.flush()
        except Exception:
            pass

    def _open_csv(self):
        if not self.out_dir:
            return
        try:
            os.makedirs(self.out_dir, exist_ok=True)
            path = os.path.join(
                self.out_dir, f"threads_{time.strftime('%Y%m%d_%H%M%S')}.csv")
            self._csv_file = open(path, "w", encoding="utf-8-sig", newline="")
            self._csv_writer = csv.writer(self._csv_file)
            self._csv_writer.writerow(["ts", "tid", "name", "cpu_pct"])
            self.artifact = path
        except Exception:
            self._csv_file = None
            self._csv_writer = None

    def _dump_csv(self):
        """收尾：关掉全量明细文件（base.wait 会在停止后调用）。"""
        f = self._csv_file
        self._csv_file = None
        self._csv_writer = None
        if f is not None:
            try:
                f.close()
            except Exception:
                pass
        return self.artifact
