# services/perf_compat.py
"""Android 版本兼容性检测"""
import re
import logging

logger = logging.getLogger(__name__)


def shell_text(device, cmd: str) -> str:
    """统一从 uiautomator2 的 device.shell() 返回值中提取字符串。
    兼容旧版（返回 str）与新版（返回 ShellResponse）。"""
    try:
        resp = device.shell(cmd)
    except Exception:
        return ""
    if resp is None:
        return ""
    # 新版 u2：ShellResponse 有 .output 属性
    if hasattr(resp, "output"):
        return resp.output or ""
    return str(resp)

class AndroidCompat:
    """检测设备 Android 版本及各项指标的可用性"""

    # 各项指标的最低 API Level
    MIN_API = {
        'cpu': 1,        # 全版本
        'mem': 1,        # 全版本
        'fps': 16,       # Android 4.1
        'jank': 23,      # Android 6.0
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
            out = shell_text(self.device, f"getprop {key}")
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
        """获取应用 UID（用于流量采集）"""
        if package in self._pkg_uid:
            return self._pkg_uid[package]
        try:
            out = shell_text(self.device, f"dumpsys package {package}")
            match = re.search(
                rf"Package\s+\[{re.escape(package)}\].*?userId=(\d+)",
                out, re.DOTALL,
            )
            if not match:
                match = re.search(r"userId=(\d+)", out)
            if match:
                uid = int(match.group(1))
                self._pkg_uid[package] = uid
                return uid
        except Exception as e:
            logger.warning(f"[AndroidCompat] 获取 UID 失败: {e}")
        return -1

    def is_package_running(self, package: str) -> bool:
        """检查应用是否在运行"""
        try:
            out = shell_text(self.device, f"pidof {package}")
            return bool(out.strip())
        except Exception:
            return False

    def list_third_party_packages(self) -> list:
        """获取第三方应用包名列表（用于应用下拉框）"""
        try:
            out = shell_text(self.device, "pm list packages -3")
            packages = []
            for line in out.splitlines():
                line = line.strip()
                if line.startswith("package:"):
                    packages.append(line[8:])
            return sorted(packages)
        except Exception as e:
            logger.warning(f"[AndroidCompat] 获取应用列表失败: {e}")
            return []

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