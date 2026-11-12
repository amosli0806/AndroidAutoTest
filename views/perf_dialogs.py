# views/perf_dialogs.py
"""性能检测相关的对话框"""
import os
from datetime import datetime

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QPushButton, QSpinBox, QDoubleSpinBox,
    QCheckBox, QComboBox, QFrame, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QMessageBox,
    QDialogButtonBox, QGraphicsDropShadowEffect, QListWidget,
    QListWidgetItem
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont
import qtawesome as qta

from utils.toast import show_toast
from utils.theme import Theme, ThemeMode
from utils.settings import Settings, THEME_MODE_DARK
from utils.dialogs import ConfirmDeleteDialog
from models.perf_model import MEM_CATEGORIES, scope_conflict_note
from utils.widget_helpers import BorderedCheckBox


# ============================================================
# 阈值配置对话框
# ============================================================
class PerfThresholdDialog(QDialog):
    """性能阈值设置对话框"""

    def __init__(self, threshold, parent=None, reference=None):
        """
        reference: 最近一次会话的统计（get_stats() 的返回），用于在对话框里显示
                   「本次会话峰值」给用户做参照 —— 阈值该设多少，看真实数据最直观。
        """
        super().__init__(parent)
        self.setObjectName("PerfThresholdDialog")
        self.setWindowTitle("阈值设置")
        self.setModal(True)
        # 高度 +40：自动转储开关从表单底部挪到顶部，并补一行说明
        self.setFixedSize(460, 446 if reference else 414)
        self.threshold = threshold
        self._reference = reference or {}

        self.setup_ui()
        self.load_data()
        self.apply_theme()

    def setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(14)

        title = QLabel("性能告警阈值")
        title.setStyleSheet("font-size: 16px; font-weight: bold;")
        root.addWidget(title)

        subtitle = QLabel("采集过程中，超过阈值会触发告警（曲线变红 + 日志提示）")
        subtitle.setStyleSheet("font-size: 12px; color: #999;")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        # 参考值：最近一次会话的峰值，帮用户判断阈值该定多少
        ref = self._reference or {}
        ref_bits = []
        if ref.get('cpu'):
            ref_bits.append(f"CPU 峰值 {ref['cpu'].get('max', 0):.0f}%")
        if ref.get('mem'):
            ref_bits.append(f"内存 峰值 {ref['mem'].get('max', 0):.0f}MB")
        if ref.get('fps'):
            ref_bits.append(f"帧率 最低 {ref['fps'].get('min', 0):.0f}")
        if ref_bits:
            hint = QLabel("最近一次会话：" + "  ·  ".join(ref_bits) + "（可直接作为上限参考）")
            hint.setWordWrap(True)
            hint.setStyleSheet("font-size: 12px; color: #1976d2;")
            root.addWidget(hint)

        # 两个开关放在一起：自动转储是阈值告警的子能力，原来放在表单底部，
        # 又是个普通复选框，用户根本感知不到它默认是开启的。
        self.enable_check = BorderedCheckBox("启用阈值告警")
        self.enable_check.setChecked(True)
        root.addWidget(self.enable_check)

        self.auto_hprof_check = BorderedCheckBox(
            "启用自动堆转储（内存超阈值时，每会话一次）")
        self.auto_hprof_check.setToolTip(
            "默认开启。内存首次越过阈值会立刻执行一次堆转储，抓住「涨上去那一刻」；\n"
            "每会话只触发一次，堆转储需 10~30 秒、产物几十 MB，不需要可取消勾选")
        root.addWidget(self.auto_hprof_check)

        self.auto_hprof_hint = QLabel(
            "默认开启；内存首次越过阈值会立即抓一次堆，约 10~30 秒、产物几十 MB")
        self.auto_hprof_hint.setWordWrap(True)
        self.auto_hprof_hint.setIndent(24)
        self.auto_hprof_hint.setStyleSheet("font-size: 12px; color: #999;")
        root.addWidget(self.auto_hprof_hint)

        # 阈值告警关闭时，自动转储不会触发；跟着置灰，避免用户误以为它还在工作
        self.enable_check.toggled.connect(self.auto_hprof_check.setEnabled)
        self.enable_check.toggled.connect(self.auto_hprof_hint.setEnabled)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("background-color: #e0e0e0; max-height: 1px;")
        root.addWidget(line)

        # 表单
        form = QFormLayout()
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.cpu_spin = QDoubleSpinBox()
        self.cpu_spin.setRange(0, 1600)  # 支持 16 核设备
        self.cpu_spin.setSuffix(" %")
        self.cpu_spin.setDecimals(1)
        form.addRow("CPU 超过:", self.cpu_spin)

        self.mem_spin = QDoubleSpinBox()
        self.mem_spin.setRange(10, 4096)
        self.mem_spin.setSuffix(" MB")
        self.mem_spin.setDecimals(0)
        form.addRow("内存超过:", self.mem_spin)

        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(1, 120)
        self.fps_spin.setSuffix(" 帧/秒")
        form.addRow("FPS 低于:", self.fps_spin)

        root.addLayout(form)

        root.addStretch()

        # 按钮
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        reset_btn = QPushButton("恢复默认")
        reset_btn.setObjectName("resetBtn")
        reset_btn.setFixedSize(100, 34)
        reset_btn.clicked.connect(self._on_reset)

        cancel_btn = QPushButton("取消")
        cancel_btn.setObjectName("cancelBtn")
        cancel_btn.setFixedSize(100, 34)
        cancel_btn.clicked.connect(self.reject)

        ok_btn = QPushButton("确定")
        ok_btn.setObjectName("okBtn")
        ok_btn.setFixedSize(100, 34)
        ok_btn.clicked.connect(self._on_ok)

        btn_row.addWidget(reset_btn)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)
        root.addLayout(btn_row)

    def load_data(self):
        th = self.threshold
        self.enable_check.setChecked(th.enabled)
        self.cpu_spin.setValue(th.cpu_max)
        self.mem_spin.setValue(th.mem_max)
        self.fps_spin.setValue(int(th.fps_min))
        self.auto_hprof_check.setChecked(bool(getattr(th, 'auto_hprof_on_alert', False)))
        # 阈值告警关闭时自动转储不会触发：跟着置灰（勾选状态保留，重新开启时生效）
        self.auto_hprof_check.setEnabled(th.enabled)
        self.auto_hprof_hint.setEnabled(th.enabled)

    def _on_reset(self):
        from models.perf_model import PerfThreshold
        default = PerfThreshold()
        self.enable_check.setChecked(default.enabled)
        self.cpu_spin.setValue(default.cpu_max)
        self.mem_spin.setValue(default.mem_max)
        self.fps_spin.setValue(int(default.fps_min))
        self.auto_hprof_check.setChecked(default.auto_hprof_on_alert)
        self.auto_hprof_check.setEnabled(default.enabled)
        self.auto_hprof_hint.setEnabled(default.enabled)

    def _on_ok(self):
        th = self.threshold
        th.enabled = self.enable_check.isChecked()
        th.cpu_max = self.cpu_spin.value()
        th.mem_max = self.mem_spin.value()
        th.fps_min = self.fps_spin.value()
        th.auto_hprof_on_alert = self.auto_hprof_check.isChecked()
        self.accept()

    def apply_theme(self, theme_mode=None):
        if theme_mode is None:
            theme_mode = ThemeMode.DARK if Settings.get_theme_mode() == THEME_MODE_DARK else ThemeMode.LIGHT

        if theme_mode == ThemeMode.DARK:
            bg = "#2d2d2d"
            text = "#eeeeee"
            border = "#555"
            input_bg = "#3c3c3c"
            input_text = "#eeeeee"
            input_border = "#555"
            cancel_bg = "#555"
            cancel_fg = "#eeeeee"
            reset_bg = "#555"
            reset_fg = "#eeeeee"
        else:
            bg = "#ffffff"
            text = "#333333"
            border = "#d0d0d0"
            input_bg = "#ffffff"
            input_text = "#333333"
            input_border = "#d0d0d0"
            cancel_bg = "#f0f0f0"
            cancel_fg = "#333333"
            reset_bg = "#f0f0f0"
            reset_fg = "#333333"

        self.setStyleSheet(f"""
            #PerfThresholdDialog {{
                background-color: {bg};
            }}
            #PerfThresholdDialog QLabel {{
                color: {text};
                background: transparent;
            }}
            #PerfThresholdDialog QCheckBox {{
                background: transparent;
            }}
            #PerfThresholdDialog QCheckBox:enabled {{
                color: {text};
            }}
            #PerfThresholdDialog QCheckBox:disabled {{
                color: {input_border};
            }}
            #PerfThresholdDialog QSpinBox,
            #PerfThresholdDialog QDoubleSpinBox {{
                background-color: {input_bg};
                color: {input_text};
                border: 1px solid {input_border};
                border-radius: 4px;
                padding: 4px 8px;
                min-height: 22px;
            }}
            #PerfThresholdDialog QPushButton#okBtn {{
                background-color: #1976d2;
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: 600;
            }}
            #PerfThresholdDialog QPushButton#okBtn:hover {{
                background-color: #1565c0;
            }}
            #PerfThresholdDialog QPushButton#cancelBtn {{
                background-color: {cancel_bg};
                color: {cancel_fg};
                border: none;
                border-radius: 6px;
                font-weight: 500;
            }}
            #PerfThresholdDialog QPushButton#resetBtn {{
                background-color: {reset_bg};
                color: {reset_fg};
                border: none;
                border-radius: 6px;
                font-weight: 500;
            }}
        """)


# ============================================================
# 抓包参数对话框
# ============================================================
class CaptureSettingsDialog(QDialog):
    """网络抓包参数：过滤表达式 / 网卡 / 单包长度 / 时长与大小上限。

    为什么要给上限也留入口：上限本来是「忘了停」的防呆（见 services/packet_capture
    顶部常量），但抓包现在会随监控自动开始，长跑场景 10 分钟就自动停会让人以为坏了。
    所以做成可配、0 = 不限。
    """

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.setObjectName("CaptureSettingsDialog")
        self.setWindowTitle("抓包参数")
        self.setModal(True)
        self.setFixedSize(520, 380)
        self._config = dict(config or {})

        self.setup_ui()
        self.load_data()
        self.apply_theme()

    def setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(14)

        title = QLabel("网络抓包参数")
        title.setStyleSheet("font-size: 16px; font-weight: bold;")
        root.addWidget(title)

        subtitle = QLabel(
            "抓包需设备已 root。过滤表达式留空表示抓全部流量；"
            "上限填 0 表示不限（长跑时建议按需放宽）。")
        subtitle.setStyleSheet("font-size: 12px; color: #999;")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("background-color: #e0e0e0; max-height: 1px;")
        root.addWidget(line)

        form = QFormLayout()
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("如：tcp port 443　/　host 10.0.0.5　（留空 = 全部）")
        form.addRow("过滤表达式:", self.filter_edit)

        self.interface_combo = QComboBox()
        self.interface_combo.setEditable(True)
        self.interface_combo.addItems(["any", "wlan0", "eth0", "rmnet0"])
        self.interface_combo.setToolTip("tcpdump 的 -i 参数；不确定就保持 any（抓所有网卡）")
        form.addRow("网卡:", self.interface_combo)

        self.snaplen_spin = QSpinBox()
        self.snaplen_spin.setRange(0, 65535)
        self.snaplen_spin.setSuffix(" 字节")
        self.snaplen_spin.setSpecialValueText("不限（完整包）")
        self.snaplen_spin.setToolTip("tcpdump 的 -s 参数：每个包最多抓多少字节。0 = 抓完整包")
        form.addRow("单包长度:", self.snaplen_spin)

        self.seconds_spin = QSpinBox()
        self.seconds_spin.setRange(0, 24 * 60)
        self.seconds_spin.setSuffix(" 分钟")
        self.seconds_spin.setSpecialValueText("不限")
        self.seconds_spin.setToolTip("单次抓包最长多久，到点自动停止并保存已抓内容")
        form.addRow("时长上限:", self.seconds_spin)

        self.bytes_spin = QSpinBox()
        self.bytes_spin.setRange(0, 10240)
        self.bytes_spin.setSuffix(" MB")
        self.bytes_spin.setSpecialValueText("不限")
        self.bytes_spin.setToolTip("单次抓包文件最大多大，到点自动停止并保存已抓内容")
        form.addRow("大小上限:", self.bytes_spin)

        root.addLayout(form)
        root.addStretch()

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        reset_btn = QPushButton("恢复默认")
        reset_btn.setObjectName("resetBtn")
        reset_btn.setFixedSize(100, 34)
        reset_btn.clicked.connect(self._on_reset)

        cancel_btn = QPushButton("取消")
        cancel_btn.setObjectName("cancelBtn")
        cancel_btn.setFixedSize(100, 34)
        cancel_btn.clicked.connect(self.reject)

        ok_btn = QPushButton("确定")
        ok_btn.setObjectName("okBtn")
        ok_btn.setFixedSize(100, 34)
        ok_btn.clicked.connect(self.accept)

        btn_row.addWidget(reset_btn)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)
        root.addLayout(btn_row)

    def load_data(self):
        cfg = self._config
        self.filter_edit.setText(str(cfg.get('filter', '')))
        self.interface_combo.setCurrentText(str(cfg.get('interface', 'any') or 'any'))
        self.snaplen_spin.setValue(int(cfg.get('snaplen', 0) or 0))
        self.seconds_spin.setValue(int(cfg.get('max_seconds', 0) or 0) // 60)
        self.bytes_spin.setValue(int(cfg.get('max_bytes', 0) or 0) // 1024 // 1024)

    def _on_reset(self):
        from services.packet_capture import DEFAULT_CAPTURE_CONFIG as _D
        self._config = dict(_D)
        self.load_data()

    def get_config(self) -> dict:
        return {
            'filter': self.filter_edit.text().strip(),
            'interface': self.interface_combo.currentText().strip() or 'any',
            'snaplen': self.snaplen_spin.value(),
            'max_seconds': self.seconds_spin.value() * 60,
            'max_bytes': self.bytes_spin.value() * 1024 * 1024,
        }

    def apply_theme(self, theme_mode=None):
        if theme_mode is None:
            theme_mode = ThemeMode.DARK if Settings.get_theme_mode() == THEME_MODE_DARK else ThemeMode.LIGHT

        if theme_mode == ThemeMode.DARK:
            bg = "#2d2d2d"
            text = "#eeeeee"
            input_bg = "#3c3c3c"
            input_text = "#eeeeee"
            input_border = "#555"
            cancel_bg = "#555"
            cancel_fg = "#eeeeee"
            reset_bg = "#555"
            reset_fg = "#eeeeee"
        else:
            bg = "#ffffff"
            text = "#333333"
            input_bg = "#ffffff"
            input_text = "#333333"
            input_border = "#d0d0d0"
            cancel_bg = "#f0f0f0"
            cancel_fg = "#333333"
            reset_bg = "#f0f0f0"
            reset_fg = "#333333"

        self.setStyleSheet(f"""
            #CaptureSettingsDialog {{
                background-color: {bg};
            }}
            #CaptureSettingsDialog QLabel {{
                color: {text};
                background: transparent;
            }}
            #CaptureSettingsDialog QLineEdit,
            #CaptureSettingsDialog QComboBox,
            #CaptureSettingsDialog QSpinBox {{
                background-color: {input_bg};
                color: {input_text};
                border: 1px solid {input_border};
                border-radius: 4px;
                padding: 4px 8px;
                min-height: 22px;
            }}
            #CaptureSettingsDialog QPushButton#okBtn {{
                background-color: #1976d2;
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: 600;
            }}
            #CaptureSettingsDialog QPushButton#okBtn:hover {{
                background-color: #1565c0;
            }}
            #CaptureSettingsDialog QPushButton#cancelBtn {{
                background-color: {cancel_bg};
                color: {cancel_fg};
                border: none;
                border-radius: 6px;
                font-weight: 500;
            }}
            #CaptureSettingsDialog QPushButton#resetBtn {{
                background-color: {reset_bg};
                color: {reset_fg};
                border: none;
                border-radius: 6px;
                font-weight: 500;
            }}
        """)


# ============================================================
# 基线管理对话框
# ============================================================
class PerfBaselineDialog(QDialog):
    """基线管理：列表 + 对比"""

    def __init__(self, perf_model, parent=None):
        super().__init__(parent)
        self.setObjectName("PerfBaselineDialog")
        self.setWindowTitle("性能基线管理")
        self.setModal(True)
        self.resize(820, 520)
        self.model = perf_model

        self.setup_ui()
        self.refresh_list()
        self.apply_theme()

    def setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        title = QLabel("性能基线")
        title.setStyleSheet("font-size: 16px; font-weight: bold;")
        root.addWidget(title)

        subtitle = QLabel("保存某个版本的性能数据作为基线，回归测试时自动对比发现性能退化。"
                          "如需新建基线，请回到性能检测页点击「保存基线」。")
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("font-size: 12px; color: #999;")
        root.addWidget(subtitle)

        # 左右布局：左基线列表，右详情
        body = QHBoxLayout()
        body.setSpacing(12)

        # 左侧列表
        left = QVBoxLayout()
        left.setSpacing(6)

        self.baseline_list = QListWidget()
        self.baseline_list.setMinimumWidth(220)
        self.baseline_list.currentItemChanged.connect(self._on_select)
        left.addWidget(self.baseline_list, 1)

        # 删除按钮：与上方基线列表同宽（撑满左列）
        del_btn = QPushButton("删除")
        del_btn.setObjectName("dangerBtn")
        del_btn.setIcon(qta.icon('fa6s.trash-can', color='white'))
        del_btn.setFixedHeight(34)
        del_btn.clicked.connect(self._on_delete)
        left.addWidget(del_btn)

        body.addLayout(left, 0)

        # 右侧详情
        right = QVBoxLayout()
        right.setSpacing(6)

        self.detail_title = QLabel("选择左侧基线查看详情")
        self.detail_title.setStyleSheet("font-weight: 600; font-size: 13px;")
        right.addWidget(self.detail_title)

        self.detail_table = QTableWidget()
        self.detail_table.setColumnCount(5)
        self.detail_table.setHorizontalHeaderLabels(
            ["指标", "最小值", "最大值", "平均值", "标准差"]
        )
        header = self.detail_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.detail_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        right.addWidget(self.detail_table, 1)

        self.compare_btn = QPushButton("与当前会话对比")
        self.compare_btn.setIcon(qta.icon('fa6s.code-compare', color='white'))
        self.compare_btn.setFixedHeight(34)  # 与左列「删除」按钮等高
        self.compare_btn.setEnabled(False)
        self.compare_btn.clicked.connect(self._on_compare)
        right.addWidget(self.compare_btn)

        body.addLayout(right, 1)
        root.addLayout(body, 1)

    def refresh_list(self):
        self.baseline_list.clear()
        for b in self.model.baselines:
            item = QListWidgetItem(f"{b.name}\n{b.app_package}")
            item.setData(Qt.ItemDataRole.UserRole, b.name)
            self.baseline_list.addItem(item)

    def _on_select(self, current, previous):
        if not current:
            self.detail_title.setText("选择左侧基线查看详情")
            self.detail_table.setRowCount(0)
            self.compare_btn.setEnabled(False)
            return
        name = current.data(Qt.ItemDataRole.UserRole)
        baseline = self.model.get_baseline(name)
        if not baseline:
            return
        self.detail_title.setText(f"{name} · {baseline.app_package}")
        self._render_metrics(baseline.metrics)
        self.compare_btn.setEnabled(True)

    def _render_metrics(self, metrics: dict):
        self.detail_table.setRowCount(0)
        rows = []
        if 'cpu' in metrics:
            s = metrics['cpu']
            rows.append(('CPU (%)', s['min'], s['max'], s['avg'], s['std']))
        if 'mem' in metrics:
            s = metrics['mem']
            rows.append(('Total (MB)', s['min'], s['max'], s['avg'], s['std']))
            # 内存分类拆解（Java/Native/Graphics/Stack/Code/Others，旧基线无拆解时不展示）
            bb = metrics.get('mem_breakdown', {})
            if isinstance(bb, dict):
                for cat, lbl, _color in MEM_CATEGORIES:
                    cs = bb.get(cat)
                    if isinstance(cs, dict) and cs:
                        rows.append(
                            (f'{lbl} (MB)', cs['min'], cs['max'], cs['avg'], cs['std'])
                        )
        if 'fps' in metrics:
            s = metrics['fps']
            rows.append(('FPS', s['min'], s['max'], s['avg'], s['std']))
        if 'traffic' in metrics:
            s = metrics['traffic']
            rows.append(('应用流量 RX/TX (MB)', '-', '-', f"{s['rx_mb']:.2f}/{s['tx_mb']:.2f}", '-'))

        self.detail_table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for j, val in enumerate(row):
                text = f"{val:.2f}" if isinstance(val, float) else str(val)
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.detail_table.setItem(i, j, item)

    def _on_new(self):
        from utils.dialogs import InputDialog
        name, ok = InputDialog.get_text(
            self,
            title="新建基线",
            label="输入基线名称:",
            placeholder="例如：主图态_8核_华为"
        )
        if not ok or not name:
            return
        name = name.strip()
        if self.model.get_baseline(name):
            show_toast(self, f"基线 '{name}' 已存在")
            return

        # 用最后一个会话作为数据来源
        if not self.model.sessions:
            show_toast(self, "暂无性能会话数据")
            return
        session = self.model.sessions[-1]

        from models.perf_model import PerfBaseline
        baseline = PerfBaseline(
            name=name,
            session_id=session.id,
            app_package=session.app_package,
            device_serial=session.device_serial,
            metrics=session.get_stats(),
            created_at=datetime.now().isoformat(),
            scopes=dict(getattr(session, "scopes", {}) or {}),
        )
        self.model.add_baseline(baseline)
        self.refresh_list()
        show_toast(self, f"基线 '{name}' 已保存")

    def _on_delete(self):
        current = self.baseline_list.currentItem()
        if not current:
            return
        name = current.data(Qt.ItemDataRole.UserRole)
        if not ConfirmDeleteDialog.ask(
                self,
                title="确认删除",
                message=f"确定要删除基线 '{name}' 吗？",
                detail="删除后该基线的性能数据将不可恢复。"
        ):
            return
        self.model.remove_baseline(name)
        self.refresh_list()
        show_toast(self, "已删除")

    def _on_compare(self):
        current = self.baseline_list.currentItem()
        if not current:
            return
        name = current.data(Qt.ItemDataRole.UserRole)
        baseline = self.model.get_baseline(name)
        if not baseline:
            return
        if not self.model.sessions:
            show_toast(self, "暂无新数据可对比")
            return

        # 对比最近一次会话
        session = self.model.sessions[-1]
        current_stats = session.get_stats()

        dlg = PerfCompareDialog(name, baseline, session, current_stats, self)
        dlg.exec()

    def apply_theme(self, theme_mode=None):
        if theme_mode is None:
            theme_mode = ThemeMode.DARK if Settings.get_theme_mode() == THEME_MODE_DARK else ThemeMode.LIGHT

        if theme_mode == ThemeMode.DARK:
            bg, text, border = "#2d2d2d", "#eeeeee", "#555"
            panel_bg, panel_border = "#3c3c3c", "#555"
            item_hover = "#4a4a4a"
            item_sel = "#1e3a5f"
        else:
            bg, text, border = "#ffffff", "#333333", "#d0d0d0"
            panel_bg, panel_border = "#ffffff", "#d0d0d0"
            item_hover = "#f0f4f8"
            item_sel = "#e3f2fd"

        self.setStyleSheet(f"""
            #PerfBaselineDialog {{
                background-color: {bg};
            }}
            #PerfBaselineDialog QLabel {{
                color: {text};
                background: transparent;
            }}
            #PerfBaselineDialog QListWidget {{
                background-color: {panel_bg};
                border: 1px solid {panel_border};
                border-radius: 6px;
                outline: none;
                padding: 4px;
                color: {text};
            }}
            #PerfBaselineDialog QListWidget::item {{
                padding: 6px 8px;
                border-radius: 4px;
                margin: 1px 2px;
            }}
            #PerfBaselineDialog QListWidget::item:hover {{
                background: {item_hover};
            }}
            #PerfBaselineDialog QListWidget::item:selected {{
                background: {item_sel};
                color: {text};
            }}
            #PerfBaselineDialog QTableWidget {{
                background-color: {panel_bg};
                border: 1px solid {panel_border};
                color: {text};
                gridline-color: {border};
            }}
            #PerfBaselineDialog QTableWidget::item {{
                border: none;
            }}
            #PerfBaselineDialog QTableCornerButton::section {{
                background-color: {panel_bg};
                border: none;
                border-bottom: 1px solid {border};
            }}
            #PerfBaselineDialog QHeaderView::section {{
                background-color: {panel_bg};
                color: {text};
                border: none;
                border-bottom: 1px solid {border};
                padding: 6px;
                font-weight: bold;
            }}
            #PerfBaselineDialog QPushButton {{
                background-color: #1976d2;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 6px 14px;
                font-weight: 500;
            }}
            #PerfBaselineDialog QPushButton:hover {{
                background-color: #1565c0;
            }}
            #PerfBaselineDialog QPushButton#dangerBtn {{
                background-color: #e74c3c;
            }}
            #PerfBaselineDialog QPushButton#dangerBtn:hover {{
                background-color: #c0392b;
            }}
            #PerfBaselineDialog QPushButton:disabled {{
                background-color: #b0b0b0;
                color: #e0e0e0;
            }}
        """)


# ============================================================
# 对比对话框
# ============================================================
class PerfCompareDialog(QDialog):
    """性能对比对话框"""

    def __init__(self, baseline_name, baseline, session, current_stats, parent=None):
        super().__init__(parent)
        self.setObjectName("PerfCompareDialog")
        self.setWindowTitle(f"性能对比 - {baseline_name}")
        self.setModal(True)
        self.resize(640, 620)

        self.baseline = baseline
        self.session = session
        self.current_stats = current_stats
        # 口径不一致 / 基线没记录口径时，只展示数据、不判改善劣化
        self._scope_note = scope_conflict_note(getattr(baseline, "scopes", None),
                                               getattr(session, "scopes", None))

        self.setup_ui()
        self.apply_theme()

    def setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)

        title = QLabel(f"基线: {self.baseline.name}")
        title.setStyleSheet("font-size: 16px; font-weight: bold;")
        root.addWidget(title)

        sub = QLabel(
            f"应用: {self.baseline.app_package}  ·  "
            f"会话: {self.session.name}"
        )
        sub.setStyleSheet("font-size: 12px; color: #999;")
        root.addWidget(sub)

        if self._scope_note:
            warn = QLabel("⚠ " + self._scope_note)
            warn.setWordWrap(True)
            warn.setStyleSheet(
                "font-size: 12px; color: #b9770e; background: rgba(230,126,34,0.12);"
                " border: 1px solid rgba(230,126,34,0.35); border-radius: 4px; padding: 6px 8px;"
            )
            root.addWidget(warn)

        # 对比表
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["指标", "基线", "当前", "变化"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setRowHeight(0, 30)
        root.addWidget(self.table, 1)

        self._render_compare()

        # 按钮
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.setFixedSize(100, 34)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    def _render_compare(self):
        rows = []

        def fmt(v, unit=''):
            if isinstance(v, float):
                return f"{v:.2f}{unit}"
            return f"{v}{unit}"

        def calc_change(base, cur, is_reverse=False):
            """计算变化。is_reverse=True 时（如 FPS 越低越差），负值为恶化"""
            if base == 0:
                return "-", False
            delta = cur - base
            pct = delta / base * 100
            worse = (delta > 0) if not is_reverse else (delta < 0)
            arrow = "↑" if delta > 0 else ("↓" if delta < 0 else "—")
            return f"{arrow} {pct:+.1f}%", worse

        # CPU（均值/峰值/最低，越高越差）
        b = self.baseline.metrics.get('cpu', {})
        c = self.current_stats.get('cpu', {})
        if b and c:
            for lbl, key in (('CPU 均值(%)', 'avg'), ('CPU 峰值(%)', 'max'), ('CPU 最低(%)', 'min')):
                change, worse = calc_change(b.get(key, 0), c.get(key, 0))
                rows.append((lbl, fmt(b.get(key, 0)), fmt(c.get(key, 0)), change, worse))

        # 内存
        b = self.baseline.metrics.get('mem', {})
        c = self.current_stats.get('mem', {})
        if b and c:
            change, worse = calc_change(b.get('avg', 0), c.get('avg', 0))
            rows.append(('Total均值(MB)', fmt(b.get('avg', 0)), fmt(c.get('avg', 0)), change, worse))
            change, worse = calc_change(b.get('max', 0), c.get('max', 0))
            rows.append(('Total峰值(MB)', fmt(b.get('max', 0)), fmt(c.get('max', 0)), change, worse))
            if b.get('min') is not None and c.get('min') is not None:
                change, worse = calc_change(b.get('min', 0), c.get('min', 0))
                rows.append(('Total最低(MB)', fmt(b.get('min', 0)), fmt(c.get('min', 0)), change, worse))

        # 内存分类拆解（各分类均值/峰值/最低，旧数据无拆解时跳过）
        bb = self.baseline.metrics.get('mem_breakdown', {})
        cc = self.current_stats.get('mem_breakdown', {})
        if isinstance(bb, dict) and isinstance(cc, dict):
            for cat, lbl, _color in MEM_CATEGORIES:
                bcat = bb.get(cat, {})
                ccat = cc.get(cat, {})
                if not bcat or not ccat:
                    continue
                change, worse = calc_change(bcat.get('avg', 0), ccat.get('avg', 0))
                rows.append(
                    (f'{lbl}均值(MB)', fmt(bcat.get('avg', 0)),
                     fmt(ccat.get('avg', 0)), change, worse)
                )
                change, worse = calc_change(bcat.get('max', 0), ccat.get('max', 0))
                rows.append(
                    (f'{lbl}峰值(MB)', fmt(bcat.get('max', 0)),
                     fmt(ccat.get('max', 0)), change, worse)
                )
                change, worse = calc_change(bcat.get('min', 0), ccat.get('min', 0))
                rows.append(
                    (f'{lbl}最低(MB)', fmt(bcat.get('min', 0)),
                     fmt(ccat.get('min', 0)), change, worse)
                )

        # FPS（均值/峰值/最低，越低越差）
        b = self.baseline.metrics.get('fps', {})
        c = self.current_stats.get('fps', {})
        if b and c:
            for lbl, key in (('FPS 均值', 'avg'), ('FPS 峰值', 'max'), ('FPS 最低', 'min')):
                change, worse = calc_change(b.get(key, 0), c.get(key, 0), is_reverse=True)
                rows.append((lbl, fmt(b.get(key, 0)), fmt(c.get(key, 0)), change, worse))

        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for j in range(4):
                item = QTableWidgetItem(str(row[j]))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if j == 3 and not self._scope_note:      # 口径不一致时不着色
                    if row[4]:
                        item.setForeground(QColor("#e74c3c"))
                    elif row[3] != "—":
                        item.setForeground(QColor("#27ae60"))
                self.table.setItem(i, j, item)

    def apply_theme(self, theme_mode=None):
        if theme_mode is None:
            theme_mode = ThemeMode.DARK if Settings.get_theme_mode() == THEME_MODE_DARK else ThemeMode.LIGHT

        if theme_mode == ThemeMode.DARK:
            bg, text, border = "#2d2d2d", "#eeeeee", "#555"
            table_bg = "#2d2d2d"
        else:
            bg, text, border = "#ffffff", "#333333", "#d0d0d0"
            table_bg = "#ffffff"

        self.setStyleSheet(f"""
            #PerfCompareDialog {{
                background-color: {bg};
            }}
            #PerfCompareDialog QLabel {{
                color: {text};
                background: transparent;
            }}
            #PerfCompareDialog QTableWidget {{
                background-color: {table_bg};
                border: 1px solid {border};
                color: {text};
                gridline-color: {border};
            }}
            #PerfCompareDialog QHeaderView::section {{
                background-color: {table_bg};
                color: {text};
                border: none;
                border-bottom: 1px solid {border};
                padding: 6px;
                font-weight: bold;
            }}
            #PerfCompareDialog QPushButton {{
                background-color: #1976d2;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 6px 14px;
            }}
        """)

