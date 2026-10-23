# services/perf_compat.py
"""Android 版本兼容性检测"""
import re
import logging

logger = logging.getLogger(__name__)


# shell 调用的超时。
# 背景（2026-10-11 修）：shell_text 原先既不传 timeout、又把异常吞成空串，
# 两个缺陷叠在一起 —— 设备通道失效（如 Android Studio 启动重启 adb server）时，
# socket 读会一直等到 u2 默认的 60 秒，GUI 线程同步调用就把窗口冻成「未响应」；
# 超时后又被吞成空串，调用方分不清「命令失败」与「命令没输出」，于是把
# 「查询失败」误判成「应用没运行」「列表为空」。
#   现在：默认超时给一个明确的数值，调用方还能拿到 ok 标志自行判断。
GUI_TIMEOUT = 5.0          # GUI 线程上的轻量探询（pidof / getprop）
GUI_TIMEOUT_HEAVY = 6.0    # GUI 线程上的重命令（pm list packages / dumpsys），
                            # 车机较慢，给宽一点，否则会误判成失败
WORKER_TIMEOUT = 10.0      # 采集线程里的调用（可以稍宽松）


def shell_text(device, cmd: str, timeout: float = None) -> str:
    """统一从 uiautomator2 的 device.shell() 返回值中提取字符串。

    兼容旧版（返回 str）与新版（返回 ShellResponse）。
    `timeout` 为 None 时用 u2 默认值（旧行为不变）；**失败时仍返回空串**，
    需要区分「失败」与「无输出」的调用方请用 shell_text_checked()。
    """
    return shell_text_checked(device, cmd, timeout)[1]


def shell_text_checked(device, cmd: str, timeout: float = None):
    """执行 shell 并返回 (ok, text)。

    ok=False 表示命令**执行失败**（通道异常 / 超时）—— 这是旧的 shell_text
    无法表达的信息；ok=True 且 text 为空才是「命令跑了但确实没输出」。
    采集侧据此把失败写进 sample.errors、把「静默 0」变成可见的错误。
    """
    try:
        if timeout is None:
            resp = device.shell(cmd)
        else:
            try:
                resp = device.shell(cmd, timeout=timeout)
            except TypeError:
                # 旧版 u2 的 shell() 没有 timeout 形参，退回不传（保持可用）
                resp = device.shell(cmd)
    except Exception as e:
        logger.debug("[perf_compat] shell 失败 cmd=%r timeout=%s: %s", cmd, timeout, e)
        return False, ""
    if resp is None:
        return True, ""
    # 新版 u2：ShellResponse 有 .output 属性
    if hasattr(resp, "output"):
        return True, (resp.output or "")
    return True, str(resp)

class AndroidCompat:
    """检测设备 Android 版本及各项指标的可用性"""

    # 各项指标的最低 API Level
    MIN_API = {
        'cpu': 1,        # 全版本
        'mem': 1,        # 全版本
        'fps': 16,       # Android 4.1
        'traffic': 1,    # 全版本（但 Android 10+ 可能需要降级）
    }

    def __init__(self, device):
        self.device = device
        self._version = None
        self._api_level = None
        self._pkg_uid = {}

    # ---------- 基础信息 ----------
    @property
    def version(self) -> str:
        if self._version is None:
            self._version = self._detect_prop("ro.build.version.release")
        return self._version

    @property
    def api_level(self) -> int:
        if self._api_level is None:
            raw = self._detect_prop("ro.build.version.sdk")
            try:
                self._api_level = int(raw)
            except (ValueError, TypeError):
                self._api_level = 0
        return self._api_level

    def _detect_prop(self, key: str) -> str:
        try:
            out = shell_text(self.device, f"getprop {key}", timeout=GUI_TIMEOUT)
            return out.strip()
        except Exception as e:
            logger.warning(f"[AndroidCompat] getprop {key} 失败: {e}")
            return ""

    # ---------- 指标可用性 ----------
    def is_metric_available(self, metric: str) -> bool:
        min_api = self.MIN_API.get(metric, 0)
        return self.api_level >= min_api

    def get_available_metrics(self) -> list:
        return [m for m in self.MIN_API if self.is_metric_available(m)]

    def get_unavailable_reason(self, metric: str) -> str:
        """返回某项指标不可用的原因（用于 tooltip 显示）"""
        min_api = self.MIN_API.get(metric, 0)
        if self.api_level >= min_api:
            return ""
        # API Level → Android 版本映射
        version_map = {
            16: "Android 4.1",
            23: "Android 6.0",
        }
        min_version = version_map.get(min_api, f"API {min_api}")
        return f"需要 {min_version}+，当前设备为 Android {self.version or '未知'}（API {self.api_level}）"

    # ---------- 包名 & UID ----------
    def get_package_uid(self, package: str) -> int:
        """获取应用 UID（用于流量采集）。

        入参可能是 `包名:子进程`（性能页下拉现在按进程列），而 `dumpsys package`
        只认包名，所以先切掉 ":" 后面那截。流量本来就按 UID 统计，子进程与主进程
        同 UID，切掉不影响结果。
        """
        base = package.split(":", 1)[0].strip()
        if base in self._pkg_uid:
            return self._pkg_uid[base]
        try:
            out = shell_text(self.device, f"dumpsys package {base}",
                             timeout=GUI_TIMEOUT_HEAVY)
            match = re.search(
                rf"Package\s+\[{re.escape(base)}\].*?userId=(\d+)",
                out, re.DOTALL,
            )
            if not match:
                match = re.search(r"userId=(\d+)", out)
            if match:
                uid = int(match.group(1))
                self._pkg_uid[base] = uid
                return uid
        except Exception as e:
            logger.warning(f"[AndroidCompat] 获取 UID 失败: {e}")
        return -1

    def is_package_running(self, package: str) -> bool:
        """检查应用是否在运行（失败时返回 False —— 需要区分请看 checked 版）"""
        return self.is_package_running_checked(package)[1]

    def is_package_running_checked(self, package: str) -> tuple:
        """返回 (ok, running)。

        ok=False：**查询本身失败**（通道异常 / 超时）—— 不能据此断定应用没运行，
                  否则就会弹出「应用未在运行」这种误导性提示（2026-10-11 修）。
        ok=True 且 running=False：确实是「查到了，但进程不在」。
        """
        ok, out = shell_text_checked(self.device, f"pidof {package}",
                                     timeout=GUI_TIMEOUT)
        return ok, bool(out.strip())

    def list_all_packages(self) -> list:
        """获取设备上所有应用包名（第三方 + 系统预装，用于应用下拉框）。

        获取方式与 ADB 工具箱「Monkey 测试」的包名能力对齐：
        同时拉取 -3（第三方）与 -s（系统预装），这样车机上原有的预装应用
        也能被选到，而不是只有用户后来手动安装的那些。
        两个命令分别容错，其中一个失败时仍返回另一部分结果。
        需要区分「命令失败」与「设备上真没应用」的调用方用 checked 版。
        """
        return self.list_all_packages_checked()[1]

    def list_all_packages_checked(self) -> tuple:
        """返回 (ok, packages)。

        ok=False 表示 -3 与 -s **两条命令都失败**（通道整体不可用）——
        此时 packages 为空，但不代表设备上没有应用，调用方应据此重试或提示，
        而不是把空列表当成结论（这正是「设备已连接却拿不到应用列表」的成因）。
        """
        packages = set()
        ok = False
        for flag in ("-3", "-s"):
            good, out = shell_text_checked(self.device, f"pm list packages {flag}",
                                           timeout=GUI_TIMEOUT_HEAVY)
            if not good:
                logger.warning("[AndroidCompat] pm list packages %s 执行失败（通道异常/超时）", flag)
                continue
            ok = True
            for line in (out or "").splitlines():
                line = line.strip()
                if line.startswith("package:"):
                    name = line[8:].strip()
                    if name:
                        packages.add(name)
        return ok, sorted(packages)

    def list_app_processes_checked(self) -> tuple:
        """返回 (ok, 进程名列表)，口径对齐 Android Studio Profiler 的进程列表。

        为什么要换数据源：`pm list packages` 给的是**全部已安装应用**（这台车机
        105 个），而 AS 的进程列表来自 AMS，只含**正在运行**的应用进程，并且把
        `pkg:子进程` 单列一行（同一台设备 70 个）。两者数量对不上，用户会以为
        应用少了（2026-10-22 反馈）。

        三级取数（都失败才算 ok=False）：
          1) `dumpsys activity processes` 的 ProcessRecord —— 与 AS 同源
          2) 退回 `ps -A` ∩ 已安装包名
          3) 再退回 `pm list packages`（只有包名，但功能不至于不可用）
        """
        # 1) AMS。只 grep 出 ProcessRecord 行：整段 dump 有 370KB，
        #    过滤后只有 15KB，adbd 传输量差一个数量级
        _ok, out = shell_text_checked(
            self.device, "dumpsys activity processes | grep 'ProcessRecord{'",
            timeout=GUI_TIMEOUT_HEAVY)
        if out:
            names = []
            seen = set()
            for m in re.finditer(r"ProcessRecord\{[0-9a-f]+\s+\d+:([^/]+)/", out):
                name = m.group(1).strip()
                # "system" 是纯系统进程，AS 的列表里也没有它
                if not name or name == "system" or name in seen:
                    continue
                seen.add(name)
                names.append(name)
            if names:
                return True, sorted(names)

        # 2) ps -A ∩ 已安装包名（进程名与包名对得上的那些）
        ok_pkg, packages = self.list_all_packages_checked()
        if not ok_pkg:
            return False, []
        pkg_set = set(packages)
        _ok_ps, ps_out = shell_text_checked(self.device, "ps -A",
                                            timeout=GUI_TIMEOUT_HEAVY)
        names = set()
        for line in (ps_out or "").splitlines():
            parts = line.split()
            if len(parts) < 2:
                continue
            name = parts[-1]
            if name in pkg_set or name.split(":", 1)[0] in pkg_set:
                names.add(name)
        # 3) 连 ps 都不行时，至少把包名给出去
        return True, (sorted(names) if names else packages)

    def get_traffic_collect_method(self) -> str:
        """返回当前设备可用的流量采集方式（含文件存在性探测，通用兼容）"""
        api = self.api_level
        if api <= 0:
            return "qtaguid"
        if api <= 28:  # Android 9 及以下
            return "uid_stat"
        if api <= 32:
            # Android 10~12 理论走 qtaguid，但部分内核/ROM 已移除该文件
            # 先探测文件是否存在，不存在直接走 netstats
            try:
                out = self.device.shell("ls /proc/net/xt_qtaguid/stats 2>&1")
                out_str = out.output if hasattr(out, "output") else str(out or "")
                if "No such file" not in out_str and "/proc/net/xt_qtaguid/stats" in out_str:
                    return "qtaguid"
            except Exception:
                pass
            return "netstats"
        return "netstats"