# views/perf_view.py
"""性能检测主视图"""
import os
import time
import logging

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QComboBox, QPushButton, QCheckBox,
    QSpinBox, QFrame, QScrollArea, QSizePolicy,
    QFileDialog, QDialog, QStackedWidget
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QSize
from PyQt6.QtGui import QIcon

import qtawesome as qta

from utils.toast import show_toast
from utils.theme import Theme, ThemeMode
from utils.settings import Settings, THEME_MODE_DARK
from views.perf_widgets import MetricCard, StatCard
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

class PerfView(QWidget):
    """性能检测视图"""

    # 对外信号
    start_requested = pyqtSignal(dict)      # {'device': str, 'package': str,
                                            #  'interval': float, 'metrics': list,
                                            #  'mode': 'monitor'|'scenario',
                                            #  'suite': str, 'loop': int,
                                            #  'stop_on_fail': bool}
    pause_requested = pyqtSignal()
    resume_requested = pyqtSignal()
    stop_requested = pyqtSignal()
    app_list_refresh_requested = pyqtSignal()
    launch_test_requested = pyqtSignal(dict)   # {'package', 'type': 'cold'|'warm'|'both'}
    export_csv_requested = pyqtSignal()
    export_json_requested = pyqtSignal()
    report_requested = pyqtSignal()
    baseline_requested = pyqtSignal()
    baseline_manager_requested = pyqtSignal()
    clear_requested = pyqtSignal()
    threshold_config_requested = pyqtSignal()

    # 状态枚举
    STATE_IDLE = 'idle'
    STATE_RUNNING = 'running'
    STATE_PAUSED = 'paused'
    STATE_SCENARIO = 'scenario'

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PerfView")
        self._theme_mode = ThemeMode.LIGHT
        self._has_wallpaper = False
        self._state = self.STATE_IDLE
        self._suite_model = None
        self._project_model = None
        self._cards = {}
        self._metric_available = {}  # 各指标在当前设备上的可用性
        self._active_metrics = []  # 本次会话实际勾选的指标
        # 流量速率差分基准
        self._last_traffic_ts = None
        self._last_traffic_rx = 0
        self._last_traffic_tx = 0

        self.setup_ui()
        self._apply_state()

    # ------------------------------------------------------------------
    # 对外 Setter
    # ------------------------------------------------------------------
    def set_suite_model(self, model):
        self._suite_model = model
        self._refresh_suite_combo()

    def set_project_model(self, model):
        self._project_model = model

    def update_app_list(self, packages):
        self.app_combo.blockSignals(True)
        current = self.app_combo.currentText()
        self.app_combo.clear()
        if not packages:
            self.app_combo.addItem("未检测到应用")
        else:
            self.app_combo.addItems(packages)
            idx = self.app_combo.findText(current)
            if idx >= 0:
                self.app_combo.setCurrentIndex(idx)
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
            self._cards['cpu'].append_data(ts, {'value': sample.cpu_percent})
        if 'mem' in self._active_metrics and 'mem' in self._cards:
            self._cards['mem'].append_data(ts, {'value': sample.mem_pss_mb})
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
            self._cards['traffic'].set_stats(
                current_text=f"接收 {rx_mb:.2f}MB",
                peak_text=f"发送 {tx_mb:.2f}MB",
                avg_text=f"总共 {total_mb:.2f}MB",
                extra_text="",
            )
        if 'jank' in stats and 'jank' in self._cards:
            s = stats['jank']
            duration = s.get('duration', 0)
            if duration >= 60:
                dur_text = f"{int(duration // 60)}m{int(duration % 60)}s"
            else:
                dur_text = f"{duration:.0f}s"
            self._cards['jank'].set_value('total', s.get('total', 0))
            self._cards['jank'].set_value('rate', f"{s.get('rate', 0):.2f}%")
            self._cards['jank'].set_value('duration', dur_text)
            self._cards['jank'].set_value('samples', s.get('samples', 0))

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
        app_ok = self.app_combo.currentText() not in ("", "未检测到应用")
        if self._state == self.STATE_IDLE:
            self.start_btn.setEnabled(app_ok)
        # 同步刷新开始按钮图标（置灰时只留文案）
        if self.start_btn.isEnabled():
            self.start_btn.setIcon(qta.icon('fa6s.play', color='white'))
        else:
            self.start_btn.setIcon(QIcon())

    def _apply_state(self):
        state = self._state
        running = state in (self.STATE_RUNNING, self.STATE_SCENARIO)
        paused = state == self.STATE_PAUSED
        idle = state == self.STATE_IDLE

        self.start_btn.setEnabled(idle)
        self.pause_btn.setEnabled(running)
        self.resume_btn.setEnabled(paused)
        self.stop_btn.setEnabled(running or paused)

        self.app_combo.setEnabled(idle)
        self.interval_combo.setEnabled(idle)
        self.mode_combo.setEnabled(idle)
        self.launch_test_btn.setEnabled(idle)

        # 场景化相关控件
        scenario_on = (self.mode_combo.currentIndex() == 1) and idle
        # 只切换子控件可见性，scenario_row 本身保持固定高度占位
        for w in (self.suite_label, self.suite_combo,
                  self.loop_label, self.loop_spin,
                  self.stop_on_fail_check):
            w.setVisible(scenario_on)

        # 指标复选框：运行中禁用；空闲/暂停时按设备兼容性恢复
        for key, cb in self.metric_checks.items():
            if idle or paused:
                cb.setEnabled(self._metric_available.get(key, True))
            else:
                cb.setEnabled(False)
        # 置灰时只显示文案，不显示图标
        for btn, icon_name in self._btn_icon_map.items():
            if btn.isEnabled():
                btn.setIcon(qta.icon(icon_name, color='white'))
            else:
                btn.setIcon(QIcon())
        # 底部"启动测试"按钮跟随同一规则
        if self.launch_test_btn.isEnabled():
            self.launch_test_btn.setIcon(qta.icon('fa6s.rocket', color='white'))
        else:
            self.launch_test_btn.setIcon(QIcon())
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

        # ---------- 顶部控制区 ----------
        control = QFrame()
        control.setObjectName("PerfControlPanel")
        control.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        c_layout = QVBoxLayout(control)
        c_layout.setContentsMargins(16, 12, 16, 12)
        c_layout.setSpacing(10)

        # ---------- 第 1 行：应用 + 采样参数 + 操作按钮 ----------
        row1 = QHBoxLayout()
        row1.setSpacing(8)

        # 应用
        row1.addWidget(QLabel("应用:"))
        self.app_combo = QComboBox()
        self.app_combo.setEditable(True)
        self.app_combo.setMinimumWidth(260)
        self.app_combo.addItem("未检测到应用")
        self.app_combo.currentTextChanged.connect(lambda _: self._check_state())
        row1.addWidget(self.app_combo)

        self.app_refresh_btn = QPushButton("刷新应用")
        self.app_refresh_btn.setIcon(qta.icon('fa6s.rotate', color='white'))
        self.app_refresh_btn.clicked.connect(self.app_list_refresh_requested.emit)
        row1.addWidget(self.app_refresh_btn)

        row1.addSpacing(20)

        # 采样间隔
        row1.addWidget(QLabel("采样间隔:"))
        self.interval_combo = QComboBox()
        self.interval_combo.addItems(["100 毫秒", "500 毫秒", "1 秒", "2 秒", "5 秒", "10 秒"])
        self.interval_combo.setCurrentText("5 秒")
        self.interval_combo.setFixedWidth(110)
        row1.addWidget(self.interval_combo)

        row1.addSpacing(16)

        # 模式
        row1.addWidget(QLabel("模式:"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["独立监控", "场景化测试"])
        self.mode_combo.setFixedWidth(130)
        self.mode_combo.currentIndexChanged.connect(lambda _: self._apply_state())
        row1.addWidget(self.mode_combo)

        # 中间弹性空间
        row1.addStretch()

        # 操作按钮组
        self.start_btn = QPushButton("开始监控")
        self.start_btn.clicked.connect(self._on_start_clicked)

        self.pause_btn = QPushButton("暂停")
        self.pause_btn.clicked.connect(self.pause_requested.emit)

        self.resume_btn = QPushButton("继续")
        self.resume_btn.clicked.connect(self.resume_requested.emit)

        self.stop_btn = QPushButton("停止")
        self.stop_btn.clicked.connect(self.stop_requested.emit)

        # 记录图标名，置灰时清空图标
        self._btn_icon_map = {
            self.start_btn: 'fa6s.play',
            self.pause_btn: 'fa6s.pause',
            self.resume_btn: 'fa6s.play',
            self.stop_btn: 'fa6s.stop',
        }

        # 统一固定宽度：保证置灰隐藏图标后按钮尺寸不变
        for b in (self.start_btn, self.pause_btn, self.resume_btn, self.stop_btn):
            b.setFixedWidth(100)
            row1.addWidget(b)

        c_layout.addLayout(row1)

        # ---------- 第 2 行：监控指标 + 场景化参数 ----------
        row2 = QHBoxLayout()
        row2.setSpacing(12)

        row2.addWidget(QLabel("监控指标:"))
        self.metric_checks = {}
        for key, label in [('cpu', 'CPU'), ('mem', '内存'), ('fps', 'FPS'),
                           ('traffic', '流量'), ('jank', '卡顿')]:
            cb = BorderedCheckBox(label)
            cb.setChecked(False)
            self.metric_checks[key] = cb
            row2.addWidget(cb)

        row2.addSpacing(24)

        # 场景化专用控件（用固定高度容器包裹，避免模式切换时行高变化）
        self.scenario_row = QWidget()
        self.scenario_row.setFixedHeight(30)
        sc_layout = QHBoxLayout(self.scenario_row)
        sc_layout.setContentsMargins(0, 0, 0, 0)
        sc_layout.setSpacing(12)

        self.suite_label = QLabel("套件:")
        sc_layout.addWidget(self.suite_label)
        self.suite_combo = QComboBox()
        self.suite_combo.setMinimumWidth(200)
        sc_layout.addWidget(self.suite_combo)

        self.loop_label = QLabel("循环:")
        sc_layout.addWidget(self.loop_label)
        self.loop_spin = QSpinBox()
        self.loop_spin.setRange(1, 999)
        self.loop_spin.setValue(1)
        self.loop_spin.setFixedWidth(70)
        sc_layout.addWidget(self.loop_spin)

        self.stop_on_fail_check = BorderedCheckBox("失败停止")
        sc_layout.addWidget(self.stop_on_fail_check)

        row2.addWidget(self.scenario_row)
        row2.addStretch()
        c_layout.addLayout(row2)

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

        # 右侧：操作按钮组（按语义分三组）
        # 组 1：单次测量
        self.launch_test_btn = QPushButton("启动测试")
        self.launch_test_btn.setIcon(qta.icon('fa6s.rocket', color='white'))
        self.launch_test_btn.setFixedWidth(100)
        self.launch_test_btn.clicked.connect(self._on_launch_test_clicked)
        b_layout.addWidget(self.launch_test_btn)

        b_layout.addSpacing(12)  # 分组间隔

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

        b_layout.addSpacing(12)  # 分组间隔

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
            'mem', '内存占用 (PSS)', 'MB', '#27ae60',
            y_min=0, y_max=500
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
        self._cards['jank'] = StatCard('卡顿统计', items=[
            {'key': 'total', 'label': '累计卡顿', 'value': '0'},
            {'key': 'rate', 'label': '卡顿率', 'value': '0.00%'},
            {'key': 'duration', 'label': '采样时长', 'value': '0s'},
            {'key': 'samples', 'label': '采样点数', 'value': '0'},
        ])

        # 初始布局
        self._relayout_cards()

    def _relayout_cards(self):
        """根据当前宽度决定是两列还是一列"""
        # 清空布局
        while self.cards_grid.count():
            item = self.cards_grid.takeAt(0)
            if item.widget():
                item.widget().setParent(None)

        width = self.scroll.viewport().width() if self.scroll else 900
        two_cols = width >= 900

        order = ['cpu', 'mem', 'fps', 'traffic', 'jank']
        if two_cols:
            # 前 4 项 2×2 排布
            top_order = ['cpu', 'mem', 'fps', 'traffic']
            for i, key in enumerate(top_order):
                r = i // 2
                c = i % 2
                card = self._cards[key]
                card.setParent(self.cards_container)
                card.setVisible(True)
                self.cards_grid.addWidget(card, r, c)
            # 卡顿统计跨 2 列
            jank = self._cards['jank']
            jank.setParent(self.cards_container)
            jank.setVisible(True)
            self.cards_grid.addWidget(jank, 2, 0, 1, 2)
        else:
            for i, key in enumerate(order):
                card = self._cards[key]
                card.setParent(self.cards_container)
                card.setVisible(True)
                self.cards_grid.addWidget(card, i, 0)

        # 让多余空间落到最末行下方，而不是分散到卡片之间
        for r in range(0, 20):
            self.cards_grid.setRowStretch(r, 0)
        used_rows = 3 if two_cols else 5
        self.cards_grid.setRowStretch(used_rows, 1)

        self.cards_container.updateGeometry()

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

        metrics = [k for k, cb in self.metric_checks.items() if cb.isChecked()]
        if not metrics:
            show_toast(message="请至少选择一项指标", parent=self)
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

    def _on_launch_test_clicked(self):
        package = self.app_combo.currentText().strip()
        if not package or package == "未检测到应用":
            show_toast(message="请先选择应用", parent=self)
            return
        self.launch_test_requested.emit({
            'package': package,
            'type': 'both',
        })

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
        self.suite_combo.clear()
        self.suite_combo.addItem("(无套件)")
        if self._suite_model:
            for s in self._suite_model.get_all_suites():
                self.suite_combo.addItem(s.name)

    def _update_status_bar(self):
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
        if self._state == self.STATE_IDLE:
            return
        state_text = {
            self.STATE_RUNNING: "采集中",
            self.STATE_PAUSED: "已暂停",
            self.STATE_SCENARIO: "场景化执行中",
        }.get(self._state, "")
        self.status_info.setText(f"{state_text} · 采样点 {total} · 间隔 {self.interval_combo.currentText()}")

    # ------------------------------------------------------------------
    def apply_theme(self, theme_mode: ThemeMode, has_wallpaper: bool = False):
        self._theme_mode = theme_mode
        self._has_wallpaper = has_wallpaper
        is_dark = (theme_mode == ThemeMode.DARK)

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
                #PerfControlPanel QCheckBox {{
                    background: transparent;
                }}
                #PerfControlPanel QCheckBox:enabled {{
                    color: {text};
                }}
                #PerfControlPanel QCheckBox:disabled {{
                    color: {disabled_text};
                }}
            """)

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

        btn_qss = f"""
            QPushButton {{
                background-color: {primary_bg};
                color: {primary_fg};
                border: none;
                border-radius: 4px;
                padding: 6px 14px;
                font-weight: 500;
                min-height: 22px;
            }}
            QPushButton:hover {{
                background-color: {'#1565c0' if not is_dark else '#64b5f6'};
            }}
            QPushButton:disabled {{
                background-color: {'#b0b0b0' if not is_dark else '#555'};
                color: {'#e0e0e0' if not is_dark else '#888'};
            }}
        """
        for btn in self.findChildren(QPushButton):
            if btn.objectName() == "dangerBtn":
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background-color: {danger_bg};
                        color: {danger_fg};
                        border: none;
                        border-radius: 4px;
                        padding: 6px 14px;
                        font-weight: 500;
                        min-height: 22px;
                    }}
                    QPushButton:hover {{
                        background-color: {'#ef5350' if is_dark else '#c0392b'};
                    }}
                """)
            else:
                btn.setStyleSheet(btn_qss)

        self.scroll.setStyleSheet(
            f"QScrollArea {{"
            f" background-color: {cards_bg};"
            f" border: none;"
            f" }}"
            f"QScrollArea > QWidget > QWidget {{"
            f" background-color: {cards_bg};"
            f" }}"
        )
        self.cards_container.setStyleSheet(f"background-color: {cards_bg};")

        for card in self._cards.values():
            if hasattr(card, 'apply_theme'):
                card.apply_theme(theme_mode, has_wallpaper)

        # 复选框颜色由父级 QSS 的 :enabled / :disabled 分支控制，无需额外刷新