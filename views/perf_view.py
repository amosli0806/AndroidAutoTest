# views/perf_view.py
"""性能检测主视图"""
import os
import time
import logging

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QComboBox, QPushButton, QCheckBox, QRadioButton, QButtonGroup,
    QSpinBox, QFrame, QScrollArea, QSizePolicy,
    QFileDialog, QDialog, QStackedWidget
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QSize
from PyQt6.QtGui import QIcon

import qtawesome as qta

from utils.toast import show_toast
from utils.theme import Theme, ThemeMode
from utils.settings import Settings, THEME_MODE_DARK
from utils.flow_layout import FlowLayout
from views.perf_widgets import MetricCard, ProbeValueCard
from models.perf_model import MEM_CATEGORIES
from PyQt6.QtWidgets import QStyleOptionButton, QStyle
from PyQt6.QtGui import QPainter, QPen, QColor
logger = logging.getLogger(__name__)



class BorderedCheckBox(QCheckBox):
    """复选框：保留 Fusion 默认绘制（勾选时有 √），额外叠加明显的边框。
    与 execute_view / task_view 中同名控件一致。"""

    def paintEvent(self, event):
        super().paintEvent(event)
        try:
            opt = QStyleOptionButton()
            self.initStyleOption(opt)
            rect = self.style().subElementRect(
                QStyle.SubElement.SE_CheckBoxIndicator, opt, self
            )
            if rect.isValid() and rect.width() > 0:
                painter = QPainter(self)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setPen(QPen(QColor(120, 120, 120), 1))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(rect.adjusted(0, 0, -1, -1))
                painter.end()
        except Exception:
            pass

class BorderedRadioButton(QRadioButton):
    """单选框：Fusion 默认圆点又小又细，放在控制面板上几乎看不出是可选项
    （监控指标一排全未选中时，整行看起来就是纯文字）。这里在原生绘制之上叠加
    一圈更粗、对比更强的圆环：未选中灰色、选中主题蓝、置灰跟随禁用文字色。
    与 BorderedCheckBox 同一思路。"""

    # 圆环配色由 apply_theme 按当前主题覆写（深浅底都需要足够对比度）
    ring_unchecked = QColor(90, 90, 90)
    ring_checked = QColor(25, 118, 210)
    ring_disabled = QColor(180, 180, 180)

    @classmethod
    def set_ring_colors(cls, unchecked, checked, disabled):
        cls.ring_unchecked = QColor(unchecked)
        cls.ring_checked = QColor(checked)
        cls.ring_disabled = QColor(disabled)

    def paintEvent(self, event):
        super().paintEvent(event)
        try:
            opt = QStyleOptionButton()
            self.initStyleOption(opt)
            rect = self.style().subElementRect(
                QStyle.SubElement.SE_RadioButtonIndicator, opt, self
            )
            if not (rect.isValid() and rect.width() > 0):
                return
            if not self.isEnabled():
                ring_color = self.ring_disabled
            elif self.isChecked():
                ring_color = self.ring_checked
            else:
                ring_color = self.ring_unchecked
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(ring_color, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(rect.adjusted(1, 1, -1, -1))
            if self.isChecked() and self.isEnabled():
                # 原生选中点是深灰的，套在蓝色圆环里颜色不统一；用同色圆点盖掉
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(ring_color)
                painter.drawEllipse(rect.adjusted(3, 3, -3, -3))
            painter.end()
        except Exception:
            pass

class PerfView(QWidget):
    """性能检测视图"""

    # 对外信号
    start_requested = pyqtSignal(dict)      # {'device': str, 'package': str,
                                            #  'interval': float, 'metrics': list,
                                            #  'mode': 'monitor'|'scenario',
                                            #  'suite': str, 'loop': int,
                                            #  'stop_on_fail': bool}
    stop_requested = pyqtSignal()
    app_list_refresh_requested = pyqtSignal()
    export_csv_requested = pyqtSignal()
    export_json_requested = pyqtSignal()
    report_requested = pyqtSignal()
    baseline_requested = pyqtSignal()
    baseline_manager_requested = pyqtSignal()
    clear_requested = pyqtSignal()
    threshold_config_requested = pyqtSignal()
    hprof_toggle_requested = pyqtSignal(bool)      # 堆转储自动循环开关（性能工具卡片）
    hprof_once_requested = pyqtSignal()
    smaps_once_requested = pyqtSignal()            # 手动触发一次堆转储
    hprof_interval_changed = pyqtSignal()
    smaps_interval_changed = pyqtSignal()          # 堆转储间隔变更
    packet_toggle_requested = pyqtSignal(bool)     # 抓包开始/停止
    packet_config_requested = pyqtSignal()         # 打开「抓包参数」对话框
    screenshot_toggle_requested = pyqtSignal(bool)  # 截图自动循环开关（性能工具卡片）
    screenshot_once_requested = pyqtSignal()        # 手动触发一次截图
    screenshot_interval_changed = pyqtSignal()      # 截图间隔变更

    # 状态枚举（曾经的「暂停」状态已随暂停/继续按钮一并移除）
    STATE_IDLE = 'idle'
    STATE_RUNNING = 'running'
    STATE_SCENARIO = 'scenario'

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PerfView")
        self._theme_mode = ThemeMode.LIGHT
        self._has_wallpaper = False
        self._state = self.STATE_IDLE
        self._suite_model = None
        self._project_model = None
        self._shown_once = False  # 是否已显示过（视口宽度显示前不可信）
        self._cards = {}
        self._hprof_card = None
        self._packet_card = None
        self._metric_available = {}  # 各指标在当前设备上的可用性
        self._active_metrics = []  # 本次会话实际勾选的指标
        # 流量速率差分基准
        self._last_traffic_ts = None
        self._last_traffic_rx = 0
        self._last_traffic_tx = 0
        # 开始/停止二合一按钮的两套外观（在 apply_theme 里按主题生成）
        self._run_btn_qss_normal = ""
        self._run_btn_qss_danger = ""
        # 应用列表是否已成功加载（决定「开始」按钮可用性；见 update_app_list）
        self._apps_loaded = False
        # 配置恢复期间的守卫：恢复过程会触发控件的变更信号，此时不回写配置，
        # 否则会把「恢复出来的值」当成用户刚改的值又存一遍（无害但纯属多余写入）
        self._perf_cfg_ready = False
        # 记忆里的上次应用包名：应用列表还没加载（或首次加载失败）时用它回选
        self._pref_package = ""
        # 记忆里的上次套件名：套件列表由 set_suite_model 稍后填充，届时回选
        self._pref_suite = ""

        self.setup_ui()
        self._restore_perf_config()
        self._perf_cfg_ready = True
        self._apply_state()

    # ------------------------------------------------------------------
    # 对外 Setter
    # ------------------------------------------------------------------
    def set_suite_model(self, model):
        self._suite_model = model
        self.refresh_suite_combo()

    def refresh_suite_combo(self):
        """重新拉取套件列表。

        自动化执行页新建/删除套件后会发 suites_changed 调到这里（见 main.py 的接线）。
        原来只在 set_suite_model 时填一次，所以新套件要重启虫师才出现在下拉里
        —— 2026-10-10 用户反馈。
        """
        self._refresh_suite_combo()

    def set_project_model(self, model):
        self._project_model = model

    def update_app_list(self, packages, load_failed: bool = False):
        """刷新应用下拉框。

        load_failed=True 表示**这次是读取失败**（通道异常/超时），而不是
        「设备上真的没有应用」—— 占位文案要能区分，否则用户只会看到
        「未检测到应用」而不知道是失败（2026-10-11 修）。
        """
        self.app_combo.blockSignals(True)
        current = self.app_combo.currentText()
        # 当前是占位文案（还没成功加载过）时，用记忆里的上次包名去回选，
        # 否则「首次读取失败 → 重试成功」这条路径会把记住的应用弄丢
        if not current or current.startswith("未检测到应用"):
            current = getattr(self, '_pref_package', '') or current
        self.app_combo.clear()
        if not packages:
            self._apps_loaded = False
            self.app_combo.addItem(
                "未检测到应用 · 点「刷新」重试" if load_failed else "未检测到应用")
        else:
            self._apps_loaded = True
            self.app_combo.addItems(packages)
            idx = self.app_combo.findText(current)
            if idx >= 0:
                self.app_combo.setCurrentIndex(idx)
            elif current and not current.startswith("未检测到应用"):
                # 列表现在按「正在运行的进程」给：上次选的应用这一刻没在跑，
                # 自然不在列表里。保留用户的选择（组合框可编辑），
                # 不要静默重置成列表第一项
                self.app_combo.setEditText(current)
        self.app_combo.blockSignals(False)
        self._check_state()

    def update_metric_availability(self, available: dict, reasons: dict):
        """
        available: {'cpu': True, 'mem': True, 'fps': False, ...}
        reasons: {'fps': '需要 Android 6.0+', ...}
        设备支持的指标：高亮且可勾选/取消
        不支持的指标：置灰且禁止勾选/取消
        """
        self._metric_available = dict(available)
        # 立即重跑一次状态应用，确保 enabled 与可用性、当前运行状态一致
        self._apply_state()

    # ------------------------------------------------------------------
    # 数据更新（由控制器调用）
    # ------------------------------------------------------------------
    def append_sample(self, sample):
        ts = sample.timestamp
        # 只对本次会话勾选的指标追加数据
        if 'cpu' in self._active_metrics and 'cpu' in self._cards:
            # 本轮 CPU 没拿到有效值（首采 / 进程集合变化 / 采集失败）时不画点：
            # 否则曲线上会多出一个假的 0，看起来像"应用不占 CPU"
            if not any(e.startswith('cpu:') for e in getattr(sample, 'errors', ())):
                self._cards['cpu'].append_data(ts, {'value': sample.cpu_percent})
        if 'mem' in self._active_metrics and 'mem' in self._cards:
            # 多线：Java / Native / Graphics / Stack / Code / Others（Android Studio 风格）+ Total 总线
            mem_vals = {}
            breakdown = getattr(sample, 'mem_breakdown', {}) or {}
            for cat, _lbl, _color in MEM_CATEGORIES:
                val = breakdown.get(cat, 0.0)
                if val > 0:
                    mem_vals[cat] = val
            total = sample.mem_pss_mb
            if total > 0:
                mem_vals['Total'] = total
            if mem_vals:
                self._cards['mem'].append_data(ts, mem_vals)
        if 'fps' in self._active_metrics and 'fps' in self._cards:
            self._cards['fps'].append_data(ts, {'value': sample.fps})
        if 'traffic' in self._active_metrics and 'traffic' in self._cards:
            # 采集失败（返回 0,0）时不画，避免污染曲线
            if not (sample.rx_bytes == 0 and sample.tx_bytes == 0):
                if self._last_traffic_ts is None:
                    # 首次采样：只记基准，不画点
                    self._last_traffic_ts = ts
                    self._last_traffic_rx = sample.rx_bytes
                    self._last_traffic_tx = sample.tx_bytes
                else:
                    dt = ts - self._last_traffic_ts
                    if dt > 0:
                        rx_rate = max(0, sample.rx_bytes - self._last_traffic_rx) / dt / 1024
                        tx_rate = max(0, sample.tx_bytes - self._last_traffic_tx) / dt / 1024
                        self._cards['traffic'].append_data(ts, {'接收': rx_rate, '发送': tx_rate})
                    self._last_traffic_ts = ts
                    self._last_traffic_rx = sample.rx_bytes
                    self._last_traffic_tx = sample.tx_bytes

        self._update_status_bar()

    def update_stats(self, stats: dict):
        """更新卡片统计栏"""
        if 'cpu' in stats and 'cpu' in self._cards:
            s = stats['cpu']
            self._cards['cpu'].set_stats(
                current_text=f"当前 {s.get('current', s['avg']):.1f}%",
                peak_text=f"峰值 {s['max']:.1f}%",
                avg_text=f"均值 {s['avg']:.1f}%",
                extra_text=f"抖动 {s['std']:.1f}%",
            )
        if 'mem' in stats and 'mem' in self._cards:
            s = stats['mem']
            # 底部统计放当前/峰值/均值；第四格留空
            self._cards['mem'].set_stats(
                current_text=f"当前 {s.get('current', s['avg']):.0f}MB",
                peak_text=f"峰值 {s['max']:.0f}MB",
                avg_text=f"均值 {s['avg']:.0f}MB",
                extra_text="",
            )
        if 'fps' in stats and 'fps' in self._cards:
            s = stats['fps']
            self._cards['fps'].set_stats(
                current_text=f"当前 {s.get('current', s['avg']):.0f}",
                peak_text=f"峰值 {s['max']:.0f}",
                avg_text=f"均值 {s['avg']:.0f}",
                extra_text="",
            )
        if 'traffic' in stats and 'traffic' in self._cards:
            s = stats['traffic']
            rx_mb = s['rx_mb']
            tx_mb = s['tx_mb']
            total_mb = rx_mb + tx_mb
            # 流量的系统口径是「按应用 UID」：选了子进程，数字仍是整个应用的，
            # 不标注会被误读成该子进程的流量（2026-10-25）
            scope = "整应用统计" if ":" in self.app_combo.currentText() else ""
            self._cards['traffic'].set_stats(
                current_text=f"接收 {rx_mb:.2f}MB",
                peak_text=f"发送 {tx_mb:.2f}MB",
                avg_text=f"总共 {total_mb:.2f}MB",
                extra_text=scope,
            )


    def _probe_interval_combo(self, key: str):
        card = self._cards.get(key)
        return getattr(card, "interval_combo", None) if card is not None else None

    def _probe_interval_text(self, key: str) -> str:
        combo = self._probe_interval_combo(key)
        return combo.currentText() if combo is not None else ""

    def update_probe(self, key: str, data: dict):
        """旁路探针的最新快照 → 对应数值卡（由 PerfController 每秒推一次）。"""
        card = self._cards.get(key)
        if card is None or not hasattr(card, "set_values"):
            return
        data = data or {}
        if key == "sys_cpu":
            self._update_sys_cpu_card(card, data)
            return
        if key == "netdev":
            self._update_netdev_card(card, data)
            return
        if key == "iotop":
            self._update_iotop_card(card, data)
            return
        if not data or "total_mb" not in data:
            err = data.get("error", "")
            card.set_values("--", "" if err else "等待采样…", error=err)
            return
        total = data.get("total_mb", 0.0)
        used = data.get("used_mb", 0.0)
        free = data.get("free_mb", 0.0)
        lost = data.get("lost_mb", 0.0)
        pct = data.get("used_pct", 0.0)
        zram = data.get("zram_mb", 0.0)
        value = f"已用 {used / 1024:.1f} / {total / 1024:.1f} GB（{pct:.0f}%）"
        detail = f"空闲 {free / 1024:.2f} GB · 丢失 {lost:.0f} MB"
        if zram:
            detail += f" · ZRAM {zram:.0f} MB"
        card.set_values(value, detail, error=data.get("error", ""))

    @staticmethod
    def _update_sys_cpu_card(card, data: dict):
        """整机 CPU 卡片文案：27% / 8 核累计 219% / 用户 18% · 内核 7% · 空闲 73%"""
        if not data or "cpu_pct" not in data:
            err = data.get("error", "")
            card.set_values("--", "" if err else "等待采样…", error=err)
            return
        cpu = data.get("cpu_pct", 0.0)
        cores = data.get("cores") or 0
        value = f"{cpu:.0f}%"
        if cores:
            # 多核累计占用 = 整机占用率 × 核数（不是 total_pct，那是 8×100 的容量）
            value += f"（{cores} 核累计 {cpu * cores:.0f}%）"
        if data.get("degraded"):
            detail = f"用户 / 内核明细不可用（top 不支持 -b，已降级读 /proc/stat）· 空闲 {data.get('idle_pct', 0):.0f}%"
        else:
            detail = (f"用户 {data.get('user_pct', 0):.0f}% · 内核 {data.get('sys_pct', 0):.0f}%"
                      f" · 空闲 {data.get('idle_pct', 0):.0f}%")
        card.set_values(value, detail, error=data.get("error", ""))

    @staticmethod
    def _update_netdev_card(card, data: dict):
        """网卡卡片：主网卡的 ↓/↑ 速率；各网卡明细放 tooltip（不做多网卡求和）"""
        if not data or "rx_kbps" not in data:
            err = data.get("error", "")
            hint = "" if err else "等待采样…（差分需要两个采样点）"
            card.set_values("--", hint, error=err)
            return
        name = data.get("primary", "")
        rx = data.get("rx_kbps", 0.0)
        tx = data.get("tx_kbps", 0.0)
        ifaces = data.get("ifaces") or []
        value = f"{name}  ↓ {rx:.1f} KB/s   ↑ {tx:.1f} KB/s"
        lines = [f"{i['name']}：↓ {i['rx_kbps']:.1f} / ↑ {i['tx_kbps']:.1f} KB/s"
                 f"（累计 ↓{i['rx_mb']:.0f}MB ↑{i['tx_mb']:.0f}MB）" for i in ifaces]
        detail = "\n".join(lines) if lines else "无其他活跃网卡"
        if len(ifaces) > 1:
            detail += "\n（多张网卡可能是同一通道的不同层，故不做求和）"
        card.set_values(value, detail, error=data.get("error", ""))

    @staticmethod
    def _update_iotop_card(card, data: dict):
        """磁盘 IO 卡片：读/写速率；Top 进程放 tooltip（需 root 才有数据）"""
        if not data or "read_kbps" not in data:
            err = data.get("error", "")
            hint = "" if err else "等待采样…（差分需要两个采样点，且需 root）"
            card.set_values("--", hint, error=err)
            return
        value = f"读 {data.get('read_kbps', 0):.1f}  写 {data.get('write_kbps', 0):.1f} KB/s"
        top = data.get("top") or []
        if top:
            lines = [f"{t['comm']}：读 {t['read_mb']:.0f}MB / 写 {t['write_mb']:.0f}MB"
                     for t in top]
            detail = "累计 Top 进程：\n" + "\n".join(lines)
        else:
            detail = "无进程 IO 明细"
        card.set_values(value, detail, error=data.get("error", ""))

    def set_alert(self, metric: str, alert: bool):
        if metric in self._cards and isinstance(self._cards[metric], MetricCard):
            self._cards[metric].set_alert(alert)

    def add_alert_log(self, message: str):
        """把告警信息追加到状态栏"""
        current = self.status_info.text()
        # 只保留最近 3 条
        lines = current.split("  ·  ")
        if len(lines) >= 4:
            lines = lines[-3:]
        lines.append(message)
        self.status_info.setText("  ·  ".join(lines))

    def set_app_package(self, package: str):
        idx = self.app_combo.findText(package)
        if idx >= 0:
            self.app_combo.setCurrentIndex(idx)
        else:
            self.app_combo.setEditText(package)

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------
    def get_state(self):
        return self._state

    def set_state(self, state):
        self._state = state
        self._apply_state()

    def _check_state(self):
        # 用「应用列表是否真的加载成功」判断，而不是比对占位文案 —— 否则改一句
        # 提示语就会让开始按钮的可用性判断失效。
        app_ok = bool(getattr(self, '_apps_loaded', False))
        if self._state == self.STATE_IDLE:
            self.start_btn.setEnabled(app_ok)
        self._refresh_run_btn()

    def _refresh_run_btn(self):
        """刷新「开始 / 停止」二合一按钮的外观。

        空闲 = 开始（蓝底 play 图标）；采集中 / 场景化执行中 = 停止（红底 stop
        图标）。两套 QSS 由 apply_theme 按当前主题预算好（见 _run_btn_qss_*），
        这里只做切换；按钮被置灰（未选应用）时只留文案、去掉图标。
        """
        idle = self._state == self.STATE_IDLE
        if self.start_btn.isEnabled():
            self.start_btn.setIcon(
                qta.icon('fa6s.play' if idle else 'fa6s.stop', color='white'))
        else:
            self.start_btn.setIcon(QIcon())
        self.start_btn.setText("开始" if idle else "停止")
        self.start_btn.setToolTip("开始采集" if idle else "停止采集")
        qss = self._run_btn_qss_normal if idle else self._run_btn_qss_danger
        if qss:
            self.start_btn.setStyleSheet(qss)

    @staticmethod
    def _tinted_icon(name: str, normal: str, disabled: str) -> QIcon:
        """按主题给图标上色，并指定「禁用态」配色。

        qtawesome 造的实色图标默认只有一种颜色，按钮禁用时 Qt 会照画原色：
        白图标落到浅色禁用底上几乎看不见（2026-10-14 反馈）。这里用 color_disabled
        让图标自带禁用态配色，Qt 按按钮状态自动切换，无需挂钩 enabledChanged。
        （不用 QIcon.addPixmap：qtawesome 的图标引擎未实现 clone，会被 detach 打死。）
        """
        return qta.icon(name, color=normal, color_disabled=disabled)

    def _refresh_icons(self, is_dark: bool, disabled_fg: str):
        """按当前主题重刷本页图标：深色底用更亮的灰、浅色底用更深的灰"""
        flat = '#c8ccd4' if is_dark else '#5a5f66'   # 纯图标按钮的本色
        pairs = [
            (self.packet_toggle_btn, 'fa6s.network-wired', '#ffffff'),
            (self.packet_config_btn, 'fa6s.sliders', flat),
            (self.hprof_once_btn, 'fa6s.database', '#ffffff'),
            (self.screenshot_once_btn, 'fa6s.camera', '#ffffff'),
            (self.app_refresh_btn, 'fa6s.rotate', '#ffffff'),
            (self.threshold_btn, 'fa6s.sliders', '#ffffff'),
            (self.baseline_btn, 'fa6s.bookmark', '#ffffff'),
            (self.baseline_mgr_btn, 'fa6s.list-check', '#ffffff'),
            (self.report_btn, 'fa6s.file-lines', '#ffffff'),
            (self.export_csv_btn, 'fa6s.file-csv', '#ffffff'),
            (self.clear_btn, 'fa6s.trash-can', '#ffffff'),
        ]
        for btn, name, color in pairs:
            btn.setIcon(self._tinted_icon(name, color, disabled_fg))

    # 探针采样间隔选项：(显示文本, 秒)。整机类指标变化慢，默认 15 秒
    PROBE_INTERVALS = [("5 秒", 5), ("10 秒", 10), ("15 秒", 15),
                       ("30 秒", 30), ("1 分钟", 60), ("2 分钟", 120)]

    def smaps_loop_enabled(self) -> bool:
        return bool(self.smaps_enable_check.isChecked())

    def get_smaps_interval(self) -> float:
        """自动抓取间隔（秒）。下拉是分钟文案，这里换算成秒。"""
        text = self.smaps_interval_combo.currentText()
        digits = "".join(ch for ch in text if ch.isdigit())
        try:
            return float(digits or 5) * 60.0
        except ValueError:
            return 300.0

    def probe_interval(self, key: str) -> float:
        """取某探针卡片上选中的间隔（秒）；拿不到就返回 0，让控制器用探针默认值。"""
        card = self._cards.get(key)
        if card is None or not hasattr(card, "interval_seconds"):
            return 0.0
        try:
            return float(card.interval_seconds())
        except Exception:
            return 0.0

    def _apply_state(self):
        state = self._state
        idle = state == self.STATE_IDLE

        # 开始/停止二合一：空闲时按「应用列表是否加载成功」决定可用性，
        # 采集中恒可用（点即停止）
        app_ok = bool(getattr(self, '_apps_loaded', False))
        self.start_btn.setEnabled(app_ok if idle else True)
        self._refresh_run_btn()

        self.app_combo.setEnabled(idle)
        self.interval_combo.setEnabled(idle)
        self.mode_combo.setEnabled(idle)
        self.duration_label.setEnabled(idle)
        self.duration_spin.setEnabled(idle)

        # 场景化相关控件：只要模式是「场景化测试」就保持显示（运行中仅置灰不隐藏，
        # 隐藏会导致开始监控后参数区突然空一块、结束后又弹回）
        scenario_on = (self.mode_combo.currentIndex() == 1)
        # 整组显隐：FlowLayout 会跳过隐藏项，其余组自动补位不留空洞
        self.scenario_group.setVisible(scenario_on)
        for w in (self.suite_label, self.suite_combo,
                  self.loop_label, self.loop_spin,
                  self.stop_on_fail_check):
            w.setEnabled(scenario_on and idle)

        # 指标单选按钮：非空闲一律禁用（暂停也是监控中，指标已在开始时锁定）
        for key, cb in self.metric_radios.items():
            cb.setEnabled(idle and self._metric_available.get(key, True))

        # 堆转储/抓包/截图的「卡片显隐」复选框：同口径，非空闲禁用
        # 系统内存探针只在本轮开始时启动，运行中不允许改（避免半路加/减探针）
        self.sys_mem_check.setEnabled(idle)
        self.sys_cpu_check.setEnabled(idle)
        self.netdev_check.setEnabled(idle)
        self.iotop_check.setEnabled(idle)
        for key in ('sys_cpu', 'sys_mem', 'netdev', 'iotop'):
            combo = self._probe_interval_combo(key)
            if combo is not None:
                combo.setEnabled(idle)
        self.smaps_visible_check.setEnabled(idle)
        self.hprof_visible_check.setEnabled(idle)
        self.packet_visible_check.setEnabled(idle)
        self.screenshot_visible_check.setEnabled(idle)

        # 堆转储设置：监控中锁定（自动循环勾选与间隔不可改）；空闲时按勾选状态开放间隔
        self.smaps_enable_check.setEnabled(idle)
        self.smaps_interval_combo.setEnabled(idle and self.smaps_enable_check.isChecked())
        self.smaps_once_btn.setEnabled(True)     # 随时可以手动抓一次
        self.hprof_enable_check.setEnabled(idle)
        self.hprof_interval_combo.setEnabled(idle and self.hprof_enable_check.isChecked())
        # 截图设置：同堆转储口径
        self.screenshot_enable_check.setEnabled(idle)
        self.screenshot_interval_combo.setEnabled(
            idle and self.screenshot_enable_check.isChecked())
        self._update_status_bar()

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # 主区域外框（带圆角）
        self.main_container = QFrame()
        self.main_container.setObjectName("PerfMainContainer")
        self.main_container.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        outer.addWidget(self.main_container)

        root = QVBoxLayout(self.main_container)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(4)

        # ---------- 顶部控制区（语义分组 + 流式布局）----------
        # 结构：行1 = [应用组][采集组]（流式，窄窗口整组换行） | [执行组]（吸右）
        #       行2 = [指标组][场景组]（流式）
        # 以后新增能力：按语义建新组塞进对应 FlowLayout 即可，放不下自动换行。
        control = QFrame()
        control.setObjectName("PerfControlPanel")
        control.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        c_layout = QVBoxLayout(control)
        c_layout.setContentsMargins(12, 10, 12, 10)
        c_layout.setSpacing(8)

        def _make_group(title, widgets):
            """语义分组容器：组名 + 控件横排，作为一个整体参与流式换行"""
            g = QFrame()
            g.setObjectName("PerfGroup")
            g.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            gl = QHBoxLayout(g)
            gl.setContentsMargins(10, 6, 10, 6)
            gl.setSpacing(8)
            if title:
                t = QLabel(title)
                t.setObjectName("PerfGroupTitle")
                gl.addWidget(t)
                gl.addSpacing(2)
            for w in widgets:
                gl.addWidget(w)
            # 统一组高：组内控件高低不一（复选框 17、下拉框 30），
            # 固定高度保证同一行（以及两行之间）的组框严格等高，内容垂直居中
            g.setFixedHeight(44)
            return g

        # ---------- 行 1：应用 + 采集参数（流式） | 执行按钮（吸右）----------
        row1 = QHBoxLayout()
        row1.setSpacing(8)

        flow_host = QWidget()
        flow1 = FlowLayout(flow_host, margin=0, spacing=8)

        # 应用组
        self.app_combo = QComboBox()
        self.app_combo.setEditable(True)
        self.app_combo.setMinimumWidth(180)
        self.app_combo.addItem("未检测到应用")
        self.app_combo.currentTextChanged.connect(lambda _: self._check_state())
        self.app_refresh_btn = QPushButton("刷新")
        self.app_refresh_btn.setIcon(qta.icon('fa6s.rotate', color='white'))
        self.app_refresh_btn.clicked.connect(self.app_list_refresh_requested.emit)
        flow1.addWidget(_make_group(None, [self.app_combo, self.app_refresh_btn]))

        # 采集组
        self.interval_label = QLabel("间隔:")
        self.interval_combo = QComboBox()
        self.interval_combo.addItems(["100 毫秒", "500 毫秒", "1 秒", "2 秒", "5 秒", "10 秒"])
        self.interval_combo.setCurrentText("5 秒")
        self.interval_combo.setFixedWidth(110)
        self.mode_label = QLabel("模式:")
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["独立监控", "场景化测试"])
        self.mode_combo.setFixedWidth(130)
        self.mode_combo.currentIndexChanged.connect(lambda _: self._apply_state())
        # 监控时长：到点自动停止（典型场景：车机跑 12 小时 Monkey 检测内存泄露）
        self.duration_label = QLabel("时长:")
        self.duration_spin = QSpinBox()
        self.duration_spin.setRange(1, 10080)   # 1 分钟 ~ 7 天
        self.duration_spin.setValue(720)        # 默认 12 小时
        self.duration_spin.setSuffix(" 分钟")
        self.duration_spin.setFixedWidth(110)
        self.duration_spin.setToolTip(
            "达到设定时长后自动停止采集并收尾（保存会话、基线对比等）。\n"
            "典型用法：默认 720 分钟 = 12 小时，配合车机长时间 Monkey 检测内存泄露。")
        flow1.addWidget(_make_group(
            "采集", [self.interval_label, self.interval_combo,
                     self.mode_label, self.mode_combo,
                     self.duration_label, self.duration_spin]))
        row1.addWidget(flow_host, 1)

        # 行 1 只承载「应用 + 采集参数」（流式换行）；
        # 「开始 / 停止」二合一按钮已挪到底部按钮行的最左侧（见下方底部状态栏）。
        c_layout.addLayout(row1)

        # ---------- 行 2：指标 + 场景化参数（流式）----------
        flow_host2 = QWidget()
        flow2 = FlowLayout(flow_host2, margin=0, spacing=8)

        # 指标组：CPU 与内存互斥单选（二者都走 top 采样，同采会互相干扰、数据失真）；
        # FPS、流量是独立采样通道，可在 CPU/内存基础上自由多选；
        # 堆转储/抓包是卡片显隐开关（不参与采样指标，默认不勾选）
        self.metric_group = QButtonGroup(self)
        self.metric_group.setExclusive(True)
        self.metric_radios = {}
        metric_widgets = []
        for key, label in [('cpu', 'CPU'), ('mem', '内存')]:
            rb = BorderedRadioButton(label)
            self.metric_group.addButton(rb)
            self.metric_radios[key] = rb
            metric_widgets.append(rb)
        self.metric_radios['cpu'].setChecked(True)
        for key, label in [('fps', 'FPS'), ('traffic', '流量')]:
            cb = BorderedCheckBox(label)
            self.metric_radios[key] = cb
            # 勾选状态联动下方卡片显隐
            cb.toggled.connect(self._on_card_visibility_changed)
            metric_widgets.append(cb)

        # 整机口径的旁路探针（独立于主采样，勾选后随监控启动）
        self.sys_cpu_check = BorderedCheckBox("整机 CPU")
        self.sys_cpu_check.setToolTip(
            "设备整体 CPU 占用（所有进程合计，归一化到 0~100%），与上方「CPU」卡的所选进程口径不同")
        self.sys_cpu_check.toggled.connect(self._on_card_visibility_changed)
        metric_widgets.append(self.sys_cpu_check)

        self.iotop_check = BorderedCheckBox("磁盘 IO")
        self.iotop_check.setToolTip(
            "设备整体磁盘读写速率（整机口径）\n"
            "⚠ 需要设备 root：读取进程 IO 统计被系统限制，未 root 时该指标不可用")
        self.iotop_check.toggled.connect(self._on_card_visibility_changed)
        metric_widgets.append(self.iotop_check)

        self.netdev_check = BorderedCheckBox("网卡流量")
        self.netdev_check.setToolTip(
            "设备各网卡的收发速率与累计流量（整机口径，所有应用合计），"
            "与上方「流量速率」卡（所选应用）不同")
        self.netdev_check.toggled.connect(self._on_card_visibility_changed)
        metric_widgets.append(self.netdev_check)

        self.sys_mem_check = BorderedCheckBox("系统内存")
        self.sys_mem_check.setToolTip(
            "整机内存（Total / Free / Used / Lost），按设备统计，与上方「内存」卡的所选进程口径不同")
        self.sys_mem_check.toggled.connect(self._on_card_visibility_changed)
        metric_widgets.append(self.sys_mem_check)

        self.smaps_visible_check = BorderedCheckBox("内存明细")
        self.smaps_visible_check.setToolTip(
            "显示「内存明细（smaps）」卡片：把所选进程的 /proc/<pid>/smaps 完整抓回本地，"
            "可用来看分段内存映射（比 PSS 总量精细得多）\n"
            "⚠ 读取其他应用的 smaps 需要设备 root，或应用是 debug（可调试）包")
        self.smaps_visible_check.toggled.connect(self._on_card_visibility_changed)
        metric_widgets.append(self.smaps_visible_check)

        self.hprof_visible_check = BorderedCheckBox("堆转储")
        self.hprof_visible_check.toggled.connect(self._on_card_visibility_changed)
        metric_widgets.append(self.hprof_visible_check)

        self.packet_visible_check = BorderedCheckBox("抓包")
        self.packet_visible_check.setToolTip(
            "显示抓包卡片，并在「开始监控」时自动开始抓包（停止监控时一并收尾）")
        self.packet_visible_check.toggled.connect(self._on_card_visibility_changed)
        metric_widgets.append(self.packet_visible_check)

        self.screenshot_visible_check = BorderedCheckBox("截图")
        self.screenshot_visible_check.toggled.connect(self._on_card_visibility_changed)
        metric_widgets.append(self.screenshot_visible_check)
        flow2.addWidget(_make_group("指标", metric_widgets))

        # 场景化参数组：仅「场景化测试」模式下显示（显隐由 _apply_state 控制）
        self.suite_label = QLabel("套件:")
        self.suite_combo = QComboBox()
        self.suite_combo.setMinimumWidth(200)
        self.loop_label = QLabel("循环:")
        self.loop_spin = QSpinBox()
        self.loop_spin.setRange(1, 999)
        self.loop_spin.setValue(1)
        self.loop_spin.setFixedWidth(70)
        self.stop_on_fail_check = BorderedCheckBox("失败停止")
        # 组内控件已带「套件:/循环:/失败停止」标签，组名省去避免「场景 套件:」语义重复
        self.scenario_group = _make_group(
            None, [self.suite_label, self.suite_combo,
                   self.loop_label, self.loop_spin, self.stop_on_fail_check])
        flow2.addWidget(self.scenario_group)

        c_layout.addWidget(flow_host2)

        root.addWidget(control)
        root.addWidget(control)

        # ---------- 卡片区 ----------
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # 背景色由 apply_theme 统一设置
        pass

        self.cards_container = QWidget()
        self.cards_grid = QGridLayout(self.cards_container)
        self.cards_grid.setContentsMargins(0, 0, 0, 0)
        self.cards_grid.setSpacing(4)

        self._build_cards()

        self.scroll.setWidget(self.cards_container)
        root.addWidget(self.scroll, 1)

        # ---------- 底部状态栏（单行：状态左 + 按钮右）----------
        bottom = QFrame()
        bottom.setObjectName("PerfBottomBar")
        bottom.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        b_layout = QHBoxLayout(bottom)
        b_layout.setContentsMargins(16, 8, 16, 8)
        b_layout.setSpacing(8)

        # 左侧：状态信息（占用剩余空间）
        self.status_info = QLabel("就绪 · 未开始采集")
        self.status_info.setStyleSheet(
            "font-size: 12px; color: #999999; background: transparent;"
        )
        b_layout.addWidget(self.status_info, 1)

        # 右侧：操作按钮组
        # 组 1：开始 / 停止（二合一）—— 空闲蓝底「开始」，采集中红底「停止」，
        # 由 _refresh_run_btn 按状态切换文案 / 图标 / 配色。
        self.start_btn = QPushButton("开始")
        self.start_btn.clicked.connect(self._on_run_btn_clicked)
        self.start_btn.setFixedWidth(100)
        b_layout.addWidget(self.start_btn)

        # 组 2：配置 / 数据
        self.threshold_btn = QPushButton("阈值设置")
        self.threshold_btn.setIcon(qta.icon('fa6s.sliders', color='white'))
        self.threshold_btn.clicked.connect(self.threshold_config_requested.emit)
        b_layout.addWidget(self.threshold_btn)

        self.baseline_btn = QPushButton("保存基线")
        self.baseline_btn.setIcon(qta.icon('fa6s.bookmark', color='white'))
        self.baseline_btn.clicked.connect(self.baseline_requested.emit)
        b_layout.addWidget(self.baseline_btn)

        self.baseline_mgr_btn = QPushButton("基线管理")
        self.baseline_mgr_btn.setIcon(qta.icon('fa6s.list-check', color='white'))
        self.baseline_mgr_btn.clicked.connect(self.baseline_manager_requested.emit)
        b_layout.addWidget(self.baseline_mgr_btn)

        self.report_btn = QPushButton("生成报告")
        self.report_btn.setIcon(qta.icon('fa6s.file-lines', color='white'))
        self.report_btn.clicked.connect(self.report_requested.emit)
        b_layout.addWidget(self.report_btn)

        self.export_csv_btn = QPushButton("导出 CSV")
        self.export_csv_btn.setIcon(qta.icon('fa6s.file-csv', color='white'))
        self.export_csv_btn.clicked.connect(self.export_csv_requested.emit)
        b_layout.addWidget(self.export_csv_btn)

        # 组 3：危险操作
        self.clear_btn = QPushButton("清空")
        self.clear_btn.setObjectName("dangerBtn")
        self.clear_btn.setIcon(qta.icon('fa6s.trash-can', color='white'))
        self.clear_btn.clicked.connect(self._on_clear_clicked)
        b_layout.addWidget(self.clear_btn)

        root.addWidget(bottom)

    # ------------------------------------------------------------------
    def _build_cards(self):
        """构建卡片网格"""
        # 创建卡片
        self._cards['cpu'] = MetricCard(
            'cpu', 'CPU 使用率 (多核累计)', '%', '#3498db',
            y_min=0, y_max=800
        )
        self._cards['mem'] = MetricCard(
            'mem', '内存占用 (分类拆解)', 'MB', '#27ae60',
            y_min=0, y_max=500,
            series=[
                {'name': cat, 'color': color}
                for cat, _lbl, color in MEM_CATEGORIES
            ] + [{'name': 'Total', 'color': '#27ae60'}],
        )
        self._cards['fps'] = MetricCard(
            'fps', 'FPS', '帧/秒', '#f39c12',
            y_min=0, y_max=60
        )
        self._cards['traffic'] = MetricCard(
            'traffic', '流量速率 (KB/s)', 'KB/s', '#9b59b6',
            y_min=0, y_max=100,
            series=[
                {'name': '接收', 'color': '#9b59b6'},
                {'name': '发送', 'color': '#e67e22'},
            ]
        )

        # 整机探针：数值卡（单行 44px），不画曲线；间隔可调，默认 15 秒
        # （dumpsys meminfo 一次 3~5 秒，5 秒节奏会把整机 CPU 抬高几个百分点）
        self._cards['sys_cpu'] = ProbeValueCard(
            '整机 CPU', accent='#3498db', intervals=self.PROBE_INTERVALS)
        self._cards['sys_mem'] = ProbeValueCard(
            '系统内存', accent='#1976d2', intervals=self.PROBE_INTERVALS)
        self._cards['netdev'] = ProbeValueCard(
            '网卡流量', accent='#9b59b6', intervals=self.PROBE_INTERVALS)
        self._cards['iotop'] = ProbeValueCard(
            '磁盘 IO', accent='#e67e22', intervals=self.PROBE_INTERVALS)

        # 性能工具卡片：堆转储 / 抓包（原来挂在 ADB 工具箱，移到性能检测页）
        self._build_tool_cards()

        # 创建即按默认勾选状态隐藏（FPS/流量/堆转储/抓包默认未勾选），
        # 避免首次打开页面时这些卡片在 _relayout_cards 之前闪现一帧
        self._cards['fps'].setVisible(False)
        self._cards['traffic'].setVisible(False)
        self._cards['sys_mem'].setVisible(False)
        self._cards['sys_cpu'].setVisible(False)
        self._cards['netdev'].setVisible(False)
        self._cards['iotop'].setVisible(False)
        self._hprof_card.setVisible(False)
        self._smaps_card.setVisible(False)
        self._packet_card.setVisible(False)
        self._screenshot_card.setVisible(False)

        # 初始布局
        self._relayout_cards()

        # 配置记忆：任一控件改动即回写 data/config.json（见 _save_perf_config）
        self._connect_perf_cfg_signals()

    def _connect_perf_cfg_signals(self):
        """把「需要被记住」的控件的变更信号接到 _save_perf_config。

        单独成方法是为了让 setup_ui 保持清爽；新增需要记忆的控件时只改这里。
        """
        self.app_combo.currentTextChanged.connect(self._save_perf_config)
        self.interval_combo.currentTextChanged.connect(self._save_perf_config)
        self.mode_combo.currentIndexChanged.connect(self._save_perf_config)
        self.duration_spin.valueChanged.connect(self._save_perf_config)
        for cb in self.metric_radios.values():
            cb.toggled.connect(self._save_perf_config)
        for key in ('sys_cpu', 'sys_mem', 'netdev', 'iotop'):
            combo = self._probe_interval_combo(key)
            if combo is not None:
                combo.currentTextChanged.connect(self._save_perf_config)
        self.smaps_enable_check.toggled.connect(self._save_perf_config)
        self.smaps_interval_combo.currentTextChanged.connect(self._save_perf_config)
        for chk in (self.sys_cpu_check, self.sys_mem_check, self.netdev_check,
                    self.iotop_check, self.smaps_visible_check, self.hprof_visible_check,
                    self.packet_visible_check, self.screenshot_visible_check):
            chk.toggled.connect(self._save_perf_config)
        self.hprof_enable_check.toggled.connect(self._save_perf_config)
        self.hprof_interval_combo.currentTextChanged.connect(self._save_perf_config)
        self.screenshot_enable_check.toggled.connect(self._save_perf_config)
        self.screenshot_interval_combo.currentTextChanged.connect(self._save_perf_config)
        self.suite_combo.currentTextChanged.connect(self._save_perf_config)
        self.loop_spin.valueChanged.connect(self._save_perf_config)
        self.stop_on_fail_check.toggled.connect(self._save_perf_config)

    def _build_tool_cards(self):
        """构建三个工具卡片：堆转储（可自动循环）、抓包（手动开始/停止）、截图（可自动循环）。
        统一单行紧凑结构：圆点 + 标题 + 控件一行排开，高度 44 与控制面板分组一致。"""

        def _card(dot_color, title):
            card = QFrame()
            card.setObjectName("PerfToolCard")
            row = QHBoxLayout(card)
            row.setContentsMargins(12, 6, 12, 6)
            row.setSpacing(10)
            dot = QLabel("●")
            # 圆点颜色存进动态属性，apply_theme 刷新时从属性读回，避免 if/else 逐卡硬编码
            dot.setProperty("dotColor", dot_color)
            dot.setStyleSheet(
                f"color: {dot_color}; font-size: 14px; background: transparent;")
            row.addWidget(dot)
            title_label = QLabel(title)
            title_label.setStyleSheet(
                "font-weight: 600; font-size: 13px; background: transparent;")
            row.addWidget(title_label)
            return card, row, dot, title_label

        # ---- 堆转储卡片 ----
        (self._hprof_card, hprof_row,
         self._hprof_dot, self._hprof_title) = _card("#e67e22", "堆转储")
        self.hprof_enable_check = BorderedCheckBox("自动循环")
        self.hprof_enable_check.setToolTip(
            "开启后，点击「开始监控」即按右侧间隔自动对所选应用执行堆转储\n"
            "（第一次转储在一个完整间隔后执行）")
        self.hprof_enable_check.toggled.connect(self._on_hprof_toggle)
        hprof_row.addWidget(self.hprof_enable_check)

        hprof_row.addWidget(QLabel("间隔:"))
        self.hprof_interval_combo = QComboBox()
        self.hprof_interval_combo.addItems(
            ["1 分钟", "5 分钟", "10 分钟", "30 分钟", "60 分钟", "120 分钟"]
        )
        self.hprof_interval_combo.setCurrentText("5 分钟")
        self.hprof_interval_combo.setFixedWidth(100)
        self.hprof_interval_combo.setEnabled(False)
        self.hprof_interval_combo.currentTextChanged.connect(
            lambda _: self.hprof_interval_changed.emit()
        )
        hprof_row.addWidget(self.hprof_interval_combo)

        self.hprof_once_btn = QPushButton("立即转储一次")
        self.hprof_once_btn.setIcon(qta.icon('fa6s.database', color='white'))
        self.hprof_once_btn.clicked.connect(self.hprof_once_requested.emit)
        hprof_row.addWidget(self.hprof_once_btn)
        hprof_row.addStretch()
        self._hprof_card.setFixedHeight(44)

        # ---- 内存明细（smaps）卡片 ----
        (self._smaps_card, smaps_row,
         self._smaps_dot, self._smaps_title) = _card("#16a085", "内存明细")
        self._smaps_card.setToolTip(
            "抓取所选进程的 /proc/<pid>/smaps 到本地\n"
            "⚠ 需要设备 root，或应用是 debug（可调试）包")
        self.smaps_enable_check = BorderedCheckBox("自动循环")
        self.smaps_enable_check.setToolTip(
            "开启后，点击「开始监控」即按右侧间隔自动抓取所选进程的 smaps\n"
            "（第一次抓取在一个完整间隔后执行）")
        self.smaps_enable_check.setEnabled(False)
        self.smaps_enable_check.toggled.connect(
            lambda _: self.smaps_interval_combo.setEnabled(
                self._state == self.STATE_IDLE and self.smaps_enable_check.isChecked()))
        smaps_row.addWidget(self.smaps_enable_check)

        smaps_row.addWidget(QLabel("间隔:"))
        self.smaps_interval_combo = QComboBox()
        self.smaps_interval_combo.addItems(
            ["1 分钟", "2 分钟", "5 分钟", "10 分钟", "30 分钟"])
        self.smaps_interval_combo.setCurrentText("5 分钟")
        self.smaps_interval_combo.setFixedWidth(100)
        self.smaps_interval_combo.setEnabled(False)
        self.smaps_interval_combo.currentTextChanged.connect(
            lambda _: self.smaps_interval_changed.emit())
        smaps_row.addWidget(self.smaps_interval_combo)

        self.smaps_once_btn = QPushButton("立即抓取一次")
        self.smaps_once_btn.setIcon(qta.icon('fa6s.magnifying-glass', color='white'))
        self.smaps_once_btn.clicked.connect(self.smaps_once_requested.emit)
        smaps_row.addWidget(self.smaps_once_btn)
        smaps_row.addStretch()
        self._smaps_card.setFixedHeight(44)

        # ---- 抓包卡片 ----
        (self._packet_card, packet_row,
         self._packet_dot, self._packet_title) = _card("#9b59b6", "网络抓包")
        self.packet_toggle_btn = QPushButton("开始抓包")
        self.packet_toggle_btn.setIcon(qta.icon('fa6s.network-wired', color='white'))
        self.packet_toggle_btn.setCheckable(True)
        self.packet_toggle_btn.toggled.connect(self.packet_toggle_requested.emit)
        packet_row.addWidget(self.packet_toggle_btn)

        # 参数入口：过滤表达式 / 网卡 / 单包长度 / 上限（此前这些全是写死的）
        # 纯图标按钮：单列 objectName，避免被「通用按钮 QSS」套上 padding/min-height
        # （那会把 28x26 撑成 30 高，比同排按钮低 2px 且底边被卡片裁掉）
        self.packet_config_btn = QPushButton()
        self.packet_config_btn.setObjectName("PerfIconBtn")
        self.packet_config_btn.setIcon(qta.icon('fa6s.sliders', color='#8a8f98'))
        self.packet_config_btn.setToolTip("抓包参数：过滤表达式 / 网卡 / 单包长度 / 上限")
        self.packet_config_btn.setFlat(True)
        self.packet_config_btn.clicked.connect(self.packet_config_requested.emit)
        packet_row.addWidget(self.packet_config_btn)

        self.packet_status_label = QLabel("未开始")
        self.packet_status_label.setStyleSheet("font-size: 12px; color: #999999; background: transparent;")
        packet_row.addWidget(self.packet_status_label)
        packet_row.addStretch()
        self._packet_card.setFixedHeight(44)
        self.refresh_packet_tooltip()

        # ---- 截图卡片 ----
        (self._screenshot_card, screenshot_row,
         self._screenshot_dot, self._screenshot_title) = _card("#16a085", "截图")
        self.screenshot_enable_check = BorderedCheckBox("自动循环")
        self.screenshot_enable_check.setToolTip(
            "开启后，点击「开始监控」即按右侧间隔自动截取设备屏幕\n"
            "（第一次截图在一个完整间隔后执行）")
        self.screenshot_enable_check.toggled.connect(self._on_screenshot_toggle)
        screenshot_row.addWidget(self.screenshot_enable_check)

        screenshot_row.addWidget(QLabel("间隔:"))
        self.screenshot_interval_combo = QComboBox()
        self.screenshot_interval_combo.addItems(
            ["30 秒", "1 分钟", "5 分钟", "10 分钟", "30 分钟", "60 分钟"]
        )
        self.screenshot_interval_combo.setCurrentText("5 分钟")
        self.screenshot_interval_combo.setFixedWidth(100)
        self.screenshot_interval_combo.setEnabled(False)
        self.screenshot_interval_combo.currentTextChanged.connect(
            lambda _: self.screenshot_interval_changed.emit()
        )
        screenshot_row.addWidget(self.screenshot_interval_combo)

        self.screenshot_once_btn = QPushButton("立即截图一次")
        self.screenshot_once_btn.setIcon(qta.icon('fa6s.camera', color='white'))
        self.screenshot_once_btn.clicked.connect(self.screenshot_once_requested.emit)
        screenshot_row.addWidget(self.screenshot_once_btn)

        self.screenshot_status_label = QLabel("")
        self.screenshot_status_label.setStyleSheet("font-size: 12px; color: #999999; background: transparent;")
        screenshot_row.addWidget(self.screenshot_status_label)
        screenshot_row.addStretch()
        self._screenshot_card.setFixedHeight(44)

    def get_hprof_interval(self) -> float:
        """返回堆转储循环间隔（秒）"""
        text = self.hprof_interval_combo.currentText()
        mapping = {
            "1 分钟": 60.0, "5 分钟": 300.0, "10 分钟": 600.0,
            "30 分钟": 1800.0, "60 分钟": 3600.0, "120 分钟": 7200.0,
        }
        return mapping.get(text, 300.0)

    def get_screenshot_interval(self) -> float:
        """返回截图循环间隔（秒）"""
        text = self.screenshot_interval_combo.currentText()
        mapping = {
            "30 秒": 30.0, "1 分钟": 60.0, "5 分钟": 300.0,
            "10 分钟": 600.0, "30 分钟": 1800.0, "60 分钟": 3600.0,
        }
        return mapping.get(text, 300.0)

    def get_selected_package(self) -> str:
        """返回性能检测页左上角下拉框选中的应用包名"""
        pkg = self.app_combo.currentText().strip()
        return "" if pkg == "未检测到应用" else pkg

    def _on_hprof_toggle(self, checked: bool):
        """自动循环勾选状态变化：刷新间隔控件可用性并通知控制器"""
        self.hprof_interval_combo.setEnabled(checked)
        self.hprof_toggle_requested.emit(checked)

    def _on_screenshot_toggle(self, checked: bool):
        """截图自动循环勾选状态变化：刷新间隔控件可用性并通知控制器"""
        self.screenshot_interval_combo.setEnabled(checked)
        self.screenshot_toggle_requested.emit(checked)

    def set_screenshot_status(self, text: str, error: bool = False):
        """更新截图卡片状态文字（最近截图文件名 / 失败原因）"""
        self.screenshot_status_label.setText(text)
        self.screenshot_status_label.setStyleSheet(
            f"font-size: 12px; color: {'#e74c3c' if error else '#999999'}; "
            f"background: transparent;")

    def set_packet_capturing(self, capturing: bool):
        """更新抓包按钮外观与文案（由控制器回写，避免按钮/状态不同步）"""
        self.packet_toggle_btn.blockSignals(True)
        self.packet_toggle_btn.setChecked(capturing)
        self.packet_toggle_btn.blockSignals(False)
        if capturing:
            self.packet_toggle_btn.setText("停止抓包")
            self.packet_status_label.setText("抓包中…")
            self.packet_status_label.setStyleSheet(
                "font-size: 12px; color: #e74c3c; background: transparent;")
        else:
            self.packet_toggle_btn.setText("开始抓包")
            self.packet_status_label.setText("未开始")
            self.packet_status_label.setStyleSheet(
                "font-size: 12px; color: #999999; background: transparent;")

    def set_packet_status(self, text: str):
        """更新抓包状态文字（例如完成路径 / 失败原因）"""
        self.packet_status_label.setText(text)

    def refresh_packet_tooltip(self):
        """按当前抓包参数刷新按钮提示（参数对话框保存后由控制器调用）"""
        try:
            from services.packet_capture import load_capture_config
            cfg = load_capture_config()
        except Exception:
            cfg = {}
        lines = ["抓取设备网络流量存为 pcap（需 root）"]
        if cfg.get('filter'):
            lines.append(f"过滤：{cfg['filter']}")
        if cfg.get('interface') and cfg['interface'] != 'any':
            lines.append(f"网卡：{cfg['interface']}")
        secs = int(cfg.get('max_seconds') or 0)
        size = int(cfg.get('max_bytes') or 0)
        lines.append("上限：" + " / ".join([
            f"{secs // 60} 分钟" if secs else "不限时",
            f"{size // 1024 // 1024} MB" if size else "不限大小",
        ]))
        lines.append("勾选「抓包」后随「开始监控」自动开始，停止监控时一并收尾")
        self.packet_toggle_btn.setToolTip("\n".join(lines))

    def _on_card_visibility_changed(self, *_):
        """显隐开关变化：重新布局卡片"""
        self._relayout_cards()

    def _relayout_cards(self):
        """根据当前宽度决定是两列还是一列，并按勾选状态决定哪些卡片显示"""
        # 清空布局
        while self.cards_grid.count():
            item = self.cards_grid.takeAt(0)
            if item.widget():
                item.widget().setParent(None)

        width = self.scroll.viewport().width() if self.scroll else 900
        # 页面在堆里隐藏时不跟随主窗口布局，视口宽度还是构建期默认值（不可信）；
        # 叠加视图自身宽度兜底（首次 showEvent 时自身宽度已是真实值）
        if not self._shown_once:
            width = max(width, self.width())
        two_cols = width >= 900

        # 可见指标卡片：CPU/内存常驻；FPS/流量按勾选显示
        visible_metrics = ['cpu', 'mem']
        if self.metric_radios['fps'].isChecked():
            visible_metrics.append('fps')
        if self.metric_radios['traffic'].isChecked():
            visible_metrics.append('traffic')

        for key, card in self._cards.items():
            card.setParent(self.cards_container)
            card.setVisible(key in visible_metrics)

        # 可见工具卡片：按勾选显示
        tool_checks = {
            'hprof': self.hprof_visible_check, 'smaps': self.smaps_visible_check,
            'packet': self.packet_visible_check,
            'screenshot': self.screenshot_visible_check}
        tool_cards = {'hprof': self._hprof_card, 'smaps': self._smaps_card,
                      'packet': self._packet_card,
                      'screenshot': self._screenshot_card}
        shown_tools = [tool_cards[k] for k in ('hprof', 'smaps', 'packet', 'screenshot')
                       if tool_checks[k].isChecked()]
        # 整机探针卡也是 44px 单行，跟在工具卡一组里，视觉整齐（不混进指标网格）
        probe_checks = (
            ('sys_mem', getattr(self, 'sys_mem_check', None)),
            ('sys_cpu', getattr(self, 'sys_cpu_check', None)),
            ('netdev', getattr(self, 'netdev_check', None)),
            ('iotop', getattr(self, 'iotop_check', None)),
        )
        probe_cards = []
        for key, chk in probe_checks:
            if chk is None or not chk.isChecked():
                continue
            if not hasattr(self, '_cards') or key not in self._cards:
                continue
            card = self._cards[key]
            card.setParent(self.cards_container)
            card.setVisible(True)       # 上面的指标循环会把它隐藏，这里重新显示
            probe_cards.append(card)
        shown_tools = probe_cards + shown_tools
        for tc in tool_cards.values():
            tc.setParent(self.cards_container)
            tc.setVisible(tc in shown_tools)

        if two_cols:
            # 指标卡片 2 列排布
            for i, key in enumerate(visible_metrics):
                self.cards_grid.addWidget(self._cards[key], i // 2, i % 2)
            # 工具卡片紧跟其后，每行最多 2 张
            next_row = (len(visible_metrics) + 1) // 2
            row, col = next_row, 0
            for tc in shown_tools:
                self.cards_grid.addWidget(tc, row, col)
                col += 1
                if col >= 2:
                    col = 0
                    row += 1
            used_rows = row + (1 if col > 0 else 0)
        else:
            for i, key in enumerate(visible_metrics):
                self.cards_grid.addWidget(self._cards[key], i, 0)
            row = len(visible_metrics)
            for tc in shown_tools:
                self.cards_grid.addWidget(tc, row, 0)
                row += 1
            used_rows = row

        # 让多余空间落到最末行下方，而不是分散到卡片之间
        for r in range(0, 20):
            self.cards_grid.setRowStretch(r, 0)
        self.cards_grid.setRowStretch(used_rows, 1)

        self.cards_container.updateGeometry()

    def showEvent(self, event):
        super().showEvent(event)
        if not self._shown_once:
            # 页面隐藏时不跟随主窗口布局，构建期的卡片布局是按默认宽度
            # （单列）算的。首次显示时宽度已就位、还没画第一帧，这里同步
            # 重排一次，避免用户看到单列→两列的"加载"跳变。
            # 先重排再置标志：此刻视口布局可能还没激活，宽度判定仍需回退
            self._relayout_cards()
            self._shown_once = True

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 简单响应式：窗口变化时重新布局
        QTimer.singleShot(50, self._relayout_cards)

    # ------------------------------------------------------------------
    def _on_start_clicked(self):
        package = self.app_combo.currentText().strip()
        if not package or package == "未检测到应用":
            show_toast(message="请先选择应用", parent=self)
            return

        interval_text = self.interval_combo.currentText()
        interval_map = {
            "100 毫秒": 0.1, "500 毫秒": 0.5,
            "1 秒": 1.0, "2 秒": 2.0,
            "5 秒": 5.0, "10 秒": 10.0,
        }
        interval = interval_map.get(interval_text, 5.0)

        # 过滤掉当前设备不支持的指标：记忆恢复出来的 FPS/流量在换到老设备上会
        # 「置灰但仍勾选」，不过滤就会被带进采集（_metric_available 未就绪时按可用算）
        metrics = [k for k, cb in self.metric_radios.items()
                   if cb.isChecked() and self._metric_available.get(k, True)]
        if not metrics:
            show_toast(message="请选择一项监控指标", parent=self)
            return
        self._active_metrics = list(metrics)

        scenario = (self.mode_combo.currentIndex() == 1)
        if scenario:
            suite_name = self.suite_combo.currentText()
            if not suite_name or suite_name == "(无套件)":
                show_toast(message="请先选择测试套件", parent=self)
                return

        payload = {
            'package': package,
            'interval': interval,
            'metrics': metrics,
            'mode': 'scenario' if scenario else 'monitor',
            'duration_minutes': self.duration_spin.value(),
            'suite': self.suite_combo.currentText() if scenario else "",
            'loop': self.loop_spin.value() if scenario else 1,
            'stop_on_fail': self.stop_on_fail_check.isChecked() if scenario else False,
        }

        # 重置流量差分基准（每次新会话都从当前时刻重新算）
        self._last_traffic_ts = None
        self._last_traffic_rx = 0
        self._last_traffic_tx = 0

        for card in self._cards.values():
            card.clear_data() if hasattr(card, 'clear_data') else card.reset()

        self.start_requested.emit(payload)

    def _on_run_btn_clicked(self):
        """开始/停止二合一的派发：空闲时启动采集，采集中时停止。"""
        if self._state == self.STATE_IDLE:
            self._on_start_clicked()
        else:
            self.stop_requested.emit()


    def _on_clear_clicked(self):
        for card in self._cards.values():
            if hasattr(card, 'clear_data'):
                card.clear_data()
            elif hasattr(card, 'reset'):
                card.reset()
        # 重置流量差分基准
        self._last_traffic_ts = None
        self._last_traffic_rx = 0
        self._last_traffic_tx = 0
        # 通知控制器清空当前会话的采样数据
        self.clear_requested.emit()
        self.status_info.setText("就绪 · 未开始采集")

    # ------------------------------------------------------------------
    def _refresh_suite_combo(self):
        # 保留当前选择：这个方法现在会被频繁调用（自动化执行页增删套件后即时同步），
        # 不能每次都把用户选好的套件重置回「(无套件)」
        current = self.suite_combo.currentText()
        if not current or current == "(无套件)":
            # 首次填充（或当前还是占位）时，用记忆里的上次套件回选；
            # 只消费一次，之后完全以用户当前选择为准，避免用户改选「(无套件)」又被拉回去
            pref = getattr(self, '_pref_suite', '')
            if pref:
                current = pref
                self._pref_suite = ''
        self.suite_combo.clear()
        self.suite_combo.addItem("(无套件)")
        if self._suite_model:
            for s in self._suite_model.get_all_suites():
                self.suite_combo.addItem(s.name)
        if current and current != "(无套件)" and self.suite_combo.findText(current) >= 0:
            self.suite_combo.setCurrentText(current)
        else:
            self.suite_combo.setCurrentText("(无套件)")

    # ------------------------------------------------------------------
    # 配置记忆（data/config.json 的 perf_config）
    # ------------------------------------------------------------------
    def _collect_perf_config(self) -> dict:
        """把当前控件状态收成一个可 JSON 化的 dict（字段含义见 _restore_perf_config）。"""
        primary = 'mem' if self.metric_radios['mem'].isChecked() else 'cpu'
        # 占位文案（未检测到应用 / 未检测到应用 · 点「刷新」重试）不是真的包名，别存进去
        pkg = self.app_combo.currentText().strip()
        if pkg.startswith("未检测到应用"):
            pkg = ""
        return {
            'interval': self.interval_combo.currentText(),
            'mode': 'scenario' if self.mode_combo.currentIndex() == 1 else 'monitor',
            'duration_minutes': self.duration_spin.value(),
            'metric': primary,
            'fps': self.metric_radios['fps'].isChecked(),
            'traffic': self.metric_radios['traffic'].isChecked(),
            'sys_mem': self.sys_mem_check.isChecked(),
            'netdev': self.netdev_check.isChecked(),
            'netdev_interval': self._probe_interval_text('netdev'),
            'iotop': self.iotop_check.isChecked(),
            'iotop_interval': self._probe_interval_text('iotop'),
            'sys_cpu': self.sys_cpu_check.isChecked(),
            'sys_cpu_interval': self._probe_interval_text('sys_cpu'),
            'sys_mem_interval': self._probe_interval_text('sys_mem'),
            'show_hprof': self.hprof_visible_check.isChecked(),
            'show_smaps': self.smaps_visible_check.isChecked(),
            'smaps_loop': self.smaps_enable_check.isChecked(),
            'smaps_interval': self.smaps_interval_combo.currentText(),
            'show_packet': self.packet_visible_check.isChecked(),
            'show_screenshot': self.screenshot_visible_check.isChecked(),
            'hprof_loop': self.hprof_enable_check.isChecked(),
            'hprof_interval': self.hprof_interval_combo.currentText(),
            'screenshot_loop': self.screenshot_enable_check.isChecked(),
            'screenshot_interval': self.screenshot_interval_combo.currentText(),
            'package': pkg,
            'suite': self.suite_combo.currentText(),
            'loop': self.loop_spin.value(),
            'stop_on_fail': self.stop_on_fail_check.isChecked(),
        }

    def _save_perf_config(self, *_):
        """任一相关控件改动即回写配置。恢复期间（_perf_cfg_ready=False）跳过。"""
        if not getattr(self, '_perf_cfg_ready', False):
            return
        # 无头验证（脚本里构造 PerfView）会把测试值写进用户真实配置，加一道闸：
        # 设了 CHONGSHI_NO_CONFIG_WRITE 就不写（2026-10-25 踩过：package 被写成 com.demo.app）
        if os.environ.get("CHONGSHI_NO_CONFIG_WRITE"):
            return
        try:
            Settings.set_perf_config(self._collect_perf_config())
        except Exception as e:
            print(f"[perf_view] 保存性能页配置失败: {e}")

    def _restore_perf_config(self):
        """把上次的配置套回各控件（在 setup_ui 之后、控制器接线之前调用一次）。

        取值一律先校验存在性：下拉框 setCurrentText 一个不存在的文本会把
        currentIndex 打成 -1、currentText 变成空串，反而把默认值也弄丢。
        """
        cfg = Settings.get_perf_config()
        if not cfg:
            return

        def _pick(combo, text):
            """下拉框有该项才选中（避免非法文本把 currentIndex 打成 -1）"""
            if text and combo.findText(text) >= 0:
                combo.setCurrentText(text)

        _pick(self.interval_combo, cfg.get('interval'))
        if cfg.get('mode') == 'scenario':
            self.mode_combo.setCurrentIndex(1)
        duration = cfg.get('duration_minutes')
        if isinstance(duration, int):
            self.duration_spin.setValue(duration)   # setValue 自带范围钳制

        # 指标：CPU / 内存是互斥单选，先选主指标；FPS / 流量是独立复选
        primary = cfg.get('metric')
        if primary in self.metric_radios:
            self.metric_radios[primary].setChecked(True)
        if cfg.get('fps'):
            self.metric_radios['fps'].setChecked(True)
        if cfg.get('traffic'):
            self.metric_radios['traffic'].setChecked(True)
        if cfg.get('sys_mem'):
            self.sys_mem_check.setChecked(True)
        if cfg.get('sys_cpu'):
            self.sys_cpu_check.setChecked(True)
        if cfg.get('netdev'):
            self.netdev_check.setChecked(True)
        if cfg.get('iotop'):
            self.iotop_check.setChecked(True)
        for key in ('sys_cpu', 'sys_mem', 'netdev', 'iotop'):
            combo = self._probe_interval_combo(key)
            text = cfg.get(f'{key}_interval')
            if combo is not None and text and combo.findText(text) >= 0:
                combo.setCurrentText(text)

        # 卡片显隐
        if cfg.get('show_smaps'):
            self.smaps_visible_check.setChecked(True)
        if cfg.get('smaps_loop'):
            self.smaps_enable_check.setChecked(True)
        _pick(self.smaps_interval_combo, cfg.get('smaps_interval'))
        if cfg.get('show_hprof'):
            self.hprof_visible_check.setChecked(True)
        if cfg.get('show_packet'):
            self.packet_visible_check.setChecked(True)
        if cfg.get('show_screenshot'):
            self.screenshot_visible_check.setChecked(True)

        # 堆转储 / 截图的自动循环与间隔
        if cfg.get('hprof_loop'):
            self.hprof_enable_check.setChecked(True)
        _pick(self.hprof_interval_combo, cfg.get('hprof_interval'))
        if cfg.get('screenshot_loop'):
            self.screenshot_enable_check.setChecked(True)
        _pick(self.screenshot_interval_combo, cfg.get('screenshot_interval'))

        # 场景化参数：套件列表要等 set_suite_model 才填，此刻 combo 是空的，
        # 非可编辑下拉框 setCurrentText 不会生效 —— 先记下偏好，
        # 由 _refresh_suite_combo 在列表就绪时回选
        suite = cfg.get('suite')
        if suite and suite != "(无套件)":
            self._pref_suite = suite
        loop = cfg.get('loop')
        if isinstance(loop, int):
            self.loop_spin.setValue(loop)
        if cfg.get('stop_on_fail'):
            self.stop_on_fail_check.setChecked(True)

        # 应用包名：此刻应用列表多半还没加载，先写入文本并记下偏好；
        # update_app_list 会保留 currentText 并在列表就绪后回选同一项
        pkg = str(cfg.get('package') or '').strip()
        if pkg:
            self._pref_package = pkg
            self.app_combo.setCurrentText(pkg)

    def _update_status_bar(self):
        # 空闲态：状态栏归位。显式写回（而不是直接 return）是为了清掉
        # 掉线提示「⚠ 设备连接中断 · 采集已暂停」这类临时文案。
        if self._state == self.STATE_IDLE:
            self.status_info.setText("就绪 · 未开始采集")
            return
        # 取当前会话勾选的、所有卡片中采样点最多的那个数
        total = 0
        for key in self._active_metrics:
            card = self._cards.get(key)
            if card is None:
                continue
            curves = getattr(card, '_curves', None)
            if not curves:
                continue
            # 单曲线：取第一个 series 的 x 长度
            first = next(iter(curves.values()), None)
            if first is not None:
                total = max(total, len(first.get('x', [])))
        state_text = {
            self.STATE_RUNNING: "采集中",
            self.STATE_SCENARIO: "场景化执行中",
        }.get(self._state, "")
        self.status_info.setText(f"{state_text} · 采样点 {total} · 间隔 {self.interval_combo.currentText()}")

    # ------------------------------------------------------------------
    def apply_theme(self, theme_mode: ThemeMode, has_wallpaper: bool = False):
        self._theme_mode = theme_mode
        self._has_wallpaper = has_wallpaper
        is_dark = (theme_mode == ThemeMode.DARK)

        # 下拉面板的统一样式：本页不走 Theme.apply_theme_to_widget，
        # 这里显式刷新一次，否则本页下拉拿不到统一样式、弹出容器还会露白底
        try:
            from utils import widget_helpers
            widget_helpers.set_combo_popup_theme(is_dark)
        except Exception:
            pass

        # 主区域背景
        # 无壁纸：纯色
        # 有壁纸：transparent，让 centralWidget 罩层 + 壁纸透出
        if is_dark:
            main_bg = "transparent" if has_wallpaper else "#191a1c"
            main_border = "rgba(136, 136, 136, 0.9)" if has_wallpaper else "#4a4a4a"
        else:
            main_bg = "transparent" if has_wallpaper else "#ffffff"
            main_border = "rgba(176, 176, 176, 0.9)" if has_wallpaper else "#d0d0d0"

        # 子区域背景
        # 无壁纸：纯色 #e8eaed / #323232
        # 有壁纸：85% 透明度，透出壁纸
        if is_dark:
            panel_bg = "rgba(50, 50, 50, 0.85)" if has_wallpaper else "#323232"
            panel_border = "#4a4a4a"
            text = "#eeeeee"
            disabled_text = "#666666"
            input_bg = "#3c3c3c"
            primary_bg = "#1976d2"
            primary_fg = "#ffffff"
            danger_bg = "#c0392b"
            danger_fg = "#ffffff"
            radio_ring = "#b8b8b8"          # 深色底：圆环要更亮才看得见
            radio_ring_disabled = "#6a6a6a"
        else:
            panel_bg = "rgba(232, 234, 237, 0.85)" if has_wallpaper else "#e8eaed"
            panel_border = "#d0d0d0"
            text = "#333333"
            disabled_text = "#aaaaaa"
            input_bg = "#ffffff"
            primary_bg = "#1976d2"
            primary_fg = "#ffffff"
            danger_bg = "#e74c3c"
            danger_fg = "#ffffff"
            radio_ring = "#5a5a5a"          # 浅色底：深灰圆环保证对比度
            radio_ring_disabled = "#b0b0b0"

        # 卡片区背景：跟主区域一致（有壁纸 transparent，无壁纸纯色）
        if is_dark:
            cards_bg = "transparent" if has_wallpaper else "#191a1c"
            cards_border = "rgba(136, 136, 136, 0.9)" if has_wallpaper else "#4a4a4a"
        else:
            cards_bg = "transparent" if has_wallpaper else "#ffffff"
            cards_border = "rgba(176, 176, 176, 0.9)" if has_wallpaper else "#d0d0d0"

        # 主区域外框样式
        if getattr(self, 'main_container', None) is not None:
            self.main_container.setStyleSheet(f"""
                #PerfMainContainer {{
                    background-color: {main_bg};
                    border: 1px solid {main_border};
                    border-radius: 10px;
                }}
            """)

        ctrl = self.findChild(QFrame, "PerfControlPanel")
        if ctrl is not None:
            ctrl.setStyleSheet(f"""
                #PerfControlPanel {{
                    background-color: {panel_bg};
                    border: 1px solid {panel_border};
                    border-radius: 8px;
                }}
                #PerfControlPanel QLabel {{
                    color: {text};
                    background: transparent;
                }}
                #PerfControlPanel QComboBox,
                #PerfControlPanel QSpinBox {{
                    background-color: {input_bg};
                    color: {text};
                    border: 1px solid {panel_border};
                    border-radius: 4px;
                    padding: 3px 8px;
                    min-height: 22px;
                }}
                #PerfControlPanel QComboBox QAbstractItemView {{
                    background-color: {input_bg};
                    color: {text};
                    border: 1px solid {panel_border};
                    selection-background-color: {primary_bg};
                    selection-color: white;
                    outline: none;
                }}
                #PerfControlPanel QCheckBox, #PerfControlPanel QRadioButton {{
                    background: transparent;
                }}
                #PerfControlPanel QCheckBox:enabled, #PerfControlPanel QRadioButton:enabled {{
                    color: {text};
                }}
                #PerfControlPanel QCheckBox:disabled, #PerfControlPanel QRadioButton:disabled {{
                    color: {disabled_text};
                }}
                #PerfControlPanel QRadioButton:enabled:checked {{
                    color: {primary_bg};
                    font-weight: 600;
                }}
                /* 语义分组容器：组内紧凑，组间由 FlowLayout 换行留白 */
                #PerfGroup {{
                    background-color: {input_bg};
                    border: 1px solid {panel_border};
                    border-radius: 6px;
                }}
                #PerfControlPanel QLabel#PerfGroupTitle {{
                    color: {'#9aa0a6' if is_dark else '#8a8f98'};
                    font-size: 11px;
                    background: transparent;
                    border: none;
                }}
            """)

        # 指标单选框圆环自绘，配色不跟着 QSS 走，这里随主题一起刷新
        BorderedRadioButton.set_ring_colors(radio_ring, primary_bg, radio_ring_disabled)

        bottom = self.findChild(QFrame, "PerfBottomBar")
        if bottom is not None:
            bottom.setStyleSheet(f"""
                #PerfBottomBar {{
                    background-color: {panel_bg};
                    border: 1px solid {panel_border};
                    border-radius: 8px;
                }}
                #PerfBottomBar QLabel {{
                    color: {text};
                    background: transparent;
                }}
            """)

        # 禁用态配色：原先是「浅灰底 + 近白字 / 白图标」，浅色主题下几乎看不见
        # （白图标落在 #b0b0b0 上对比度极低）。统一改成「灰底 + 中灰字」，
        # 图标也用同一个中灰（见下方 _tinted_icon 的 Disabled 变体）。
        disabled_bg = '#4a4a4a' if is_dark else '#d9d9d9'
        disabled_fg = '#8f8f8f'

        btn_qss = f"""
            QPushButton {{
                background-color: {primary_bg};
                color: {primary_fg};
                border: none;
                border-radius: 4px;
                padding: 4px 14px;
                font-weight: 500;
                min-height: 22px;
            }}
            QPushButton:hover {{
                background-color: {'#1565c0' if not is_dark else '#64b5f6'};
            }}
            QPushButton:pressed {{
                background-color: {'#0d47a1' if not is_dark else '#42a5f5'};
            }}
            QPushButton:disabled {{
                background-color: {disabled_bg};
                color: {disabled_fg};
            }}
        """
        # 危险语义（停止 / 清空）：红底。单独抽成变量，供「开始/停止」二合一按钮
        # 在切到「停止」态时复用（见 _refresh_run_btn）。
        danger_qss = f"""
            QPushButton {{
                background-color: {danger_bg};
                color: {danger_fg};
                border: none;
                border-radius: 4px;
                padding: 4px 14px;
                font-weight: 500;
                min-height: 22px;
            }}
            QPushButton:hover {{
                background-color: {'#ef5350' if is_dark else '#c0392b'};
            }}
            QPushButton:pressed {{
                background-color: {'#c62828' if is_dark else '#96281b'};
            }}
            QPushButton:disabled {{
                background-color: {disabled_bg};
                color: {disabled_fg};
            }}
        """
        # 记住两套外观，供运行按钮按状态切换
        self._run_btn_qss_normal = btn_qss
        self._run_btn_qss_danger = danger_qss

        # 纯图标按钮（抓包参数等）：自带一套紧凑样式，不吃上面的通用按钮 QSS，
        # 否则 padding/min-height 会把固定尺寸撑大、与同行按钮错位
        icon_btn_qss = f"""
            QPushButton#PerfIconBtn {{
                background: transparent;
                border: none;
                border-radius: 4px;
                padding: 4px;
                min-width: 20px;  max-width: 20px;
                min-height: 22px; max-height: 22px;
            }}
            QPushButton#PerfIconBtn:hover {{
                background: {'rgba(255,255,255,0.12)' if is_dark else 'rgba(0,0,0,0.07)'};
            }}
            QPushButton#PerfIconBtn:pressed {{
                background: {'rgba(255,255,255,0.20)' if is_dark else 'rgba(0,0,0,0.12)'};
            }}
        """

        for btn in self.findChildren(QPushButton):
            name = btn.objectName()
            if name == "PerfIconBtn":
                btn.setStyleSheet(icon_btn_qss)
            elif name == "dangerBtn":
                btn.setStyleSheet(danger_qss)
            else:
                btn.setStyleSheet(btn_qss)

        # 图标随主题重算，并补上禁用态变体（白图标落在浅色禁用底上会看不见）
        self._refresh_icons(is_dark, disabled_fg)

        # 主题换色后，运行按钮要按它「当前是开始还是停止」重新套一次
        self._refresh_run_btn()

        # 底部操作按钮统一宽度：取自然宽度最大者（QSS 生效后测量），
        # 避免「清空」这类短文案按钮与四字按钮宽窄不一
        bottom_btns = [self.threshold_btn, self.baseline_btn,
                       self.baseline_mgr_btn, self.report_btn,
                       self.export_csv_btn, self.clear_btn]
        uniform_w = max(b.sizeHint().width() for b in bottom_btns)
        for b in bottom_btns:
            b.setFixedWidth(uniform_w)

        self.scroll.setStyleSheet(
            f"QScrollArea {{"
            f" background-color: {cards_bg};"
            f" border: none;"
            f" }}"
            f"QScrollArea > QWidget > QWidget {{"
            f" background-color: {cards_bg};"
            f" }}"
            f"QScrollBar:vertical {{"
            f" width: 6px;"
            f" background: {'#3a3a3a' if is_dark else '#e0e0e0'};"
            f" border-radius: 3px;"
            f" margin: 0px;"
            f" }}"
            f"QScrollBar::handle:vertical {{"
            f" background: {'#666' if is_dark else '#c0c0c0'};"
            f" border-radius: 3px;"
            f" min-height: 20px;"
            f" }}"
            f"QScrollBar::handle:vertical:hover {{"
            f" background: {'#888' if is_dark else '#a0a0a0'};"
            f" }}"
            f"QScrollBar::add-line:vertical,"
            f"QScrollBar::sub-line:vertical {{"
            f" height: 0px;"
            f" width: 0px;"
            f" background: transparent;"
            f" border: none;"
            f" }}"
            f"QScrollBar::add-page:vertical,"
            f"QScrollBar::sub-page:vertical {{"
            f" background: transparent;"
            f" }}"
            f"QScrollBar::up-arrow:vertical,"
            f"QScrollBar::down-arrow:vertical {{"
            f" background: transparent;"
            f" border: none;"
            f" width: 0px;"
            f" height: 0px;"
            f" }}"
        )
        self.cards_container.setStyleSheet(f"background-color: {cards_bg};")

        for card in self._cards.values():
            if hasattr(card, 'apply_theme'):
                card.apply_theme(theme_mode, has_wallpaper)

        # 性能工具卡片（堆转储 / 抓包 / 截图）：与指标卡片保持同一套配色
        for tool_card, title_widget, dot_widget in (
            (self._hprof_card, self._hprof_title, self._hprof_dot),
            (self._smaps_card, self._smaps_title, self._smaps_dot),
            (self._packet_card, self._packet_title, self._packet_dot),
            (self._screenshot_card, self._screenshot_title, self._screenshot_dot),
        ):
            if tool_card is None:
                continue
            if is_dark:
                tool_bg = "rgba(35, 36, 39, 0.85)" if has_wallpaper else "#232427"
                tool_border = "#3a3a3a"
                tool_title = "#ffffff"
            else:
                tool_bg = "rgba(232, 234, 237, 0.85)" if has_wallpaper else "#e8eaed"
                tool_border = "#d0d0d0"
                tool_title = "#333333"
            tool_card.setStyleSheet(f"""
                #PerfToolCard {{
                    background-color: {tool_bg};
                    border: 1px solid {tool_border};
                    border-radius: 8px;
                }}
                #PerfToolCard QLabel {{
                    color: {text};
                    background: transparent;
                }}
                #PerfToolCard QComboBox {{
                    background-color: {input_bg};
                    color: {text};
                    border: 1px solid {panel_border};
                    border-radius: 4px;
                    padding: 3px 8px;
                    min-height: 22px;
                }}
                #PerfToolCard QCheckBox {{
                    background: transparent;
                }}
                #PerfToolCard QCheckBox:enabled {{
                    color: {text};
                }}
                #PerfToolCard QCheckBox:disabled {{
                    color: {disabled_text};
                }}
            """)
            title_widget.setStyleSheet(
                f"font-weight: 600; font-size: 13px; color: {tool_title}; background: transparent;"
            )
            # 圆点为品牌区分色，不随主题变；按卡片逐一指定
            dot_widget.setStyleSheet(
                f"color: {dot_widget.property('dotColor')}; "
                f"font-size: 14px; background: transparent;")

        # 复选框颜色由父级 QSS 的 :enabled / :disabled 分支控制，无需额外刷新