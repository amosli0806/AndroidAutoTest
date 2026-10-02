# views/perf_dialogs.py
"""性能检测相关的对话框"""
import os
from datetime import datetime

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QPushButton, QSpinBox, QDoubleSpinBox,
    QCheckBox, QComboBox, QFrame, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QMessageBox, QTextEdit,
    QDialogButtonBox, QGraphicsDropShadowEffect, QListWidget,
    QListWidgetItem
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont
import qtawesome as qta
import pyqtgraph as pg

from utils.toast import show_toast
from utils.theme import Theme, ThemeMode
from utils.settings import Settings, THEME_MODE_DARK
from utils.dialogs import ConfirmDeleteDialog
from models.perf_model import MEM_CATEGORIES
from PyQt6.QtWidgets import QStyleOptionButton, QStyle
from PyQt6.QtGui import QPainter, QPen, QColor, QBrush


class BorderedCheckBox(QCheckBox):
    """复选框：保留 Fusion 默认绘制（勾选时有 √），额外叠加明显的边框。"""

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

# ============================================================
# 阈值配置对话框
# ============================================================
class PerfThresholdDialog(QDialog):
    """性能阈值设置对话框"""

    def __init__(self, threshold, parent=None):
        super().__init__(parent)
        self.setObjectName("PerfThresholdDialog")
        self.setWindowTitle("阈值设置")
        self.setModal(True)
        self.setFixedSize(460, 340)
        self.threshold = threshold

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

        # 启用开关
        self.enable_check = BorderedCheckBox("启用阈值告警")
        self.enable_check.setChecked(True)
        root.addWidget(self.enable_check)

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

    def _on_reset(self):
        from models.perf_model import PerfThreshold
        default = PerfThreshold()
        self.enable_check.setChecked(default.enabled)
        self.cpu_spin.setValue(default.cpu_max)
        self.mem_spin.setValue(default.mem_max)
        self.fps_spin.setValue(int(default.fps_min))

    def _on_ok(self):
        th = self.threshold
        th.enabled = self.enable_check.isChecked()
        th.cpu_max = self.cpu_spin.value()
        th.mem_max = self.mem_spin.value()
        th.fps_min = self.fps_spin.value()
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
            rows.append(('内存 (MB)', s['min'], s['max'], s['avg'], s['std']))
        if 'fps' in metrics:
            s = metrics['fps']
            rows.append(('FPS', s['min'], s['max'], s['avg'], s['std']))
        if 'traffic' in metrics:
            s = metrics['traffic']
            rows.append(('流量 RX/TX (MB)', '-', '-', f"{s['rx_mb']:.2f}/{s['tx_mb']:.2f}", '-'))

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

        # CPU
        b = self.baseline.metrics.get('cpu', {})
        c = self.current_stats.get('cpu', {})
        if b and c:
            change, worse = calc_change(b.get('max', 0), c.get('max', 0))
            rows.append(('CPU 峰值(%)', fmt(b.get('max', 0)), fmt(c.get('max', 0)), change, worse))

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

        # FPS
        b = self.baseline.metrics.get('fps', {})
        c = self.current_stats.get('fps', {})
        if b and c:
            change, worse = calc_change(b.get('avg', 0), c.get('avg', 0), is_reverse=True)
            rows.append(('FPS 均值', fmt(b.get('avg', 0)), fmt(c.get('avg', 0)), change, worse))

        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for j in range(4):
                item = QTableWidgetItem(str(row[j]))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if j == 3 and row[4]:
                    item.setForeground(QColor("#e74c3c"))
                elif j == 3 and not row[4] and row[3] != "—":
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


# ============================================================
# 启动测试对话框
# ============================================================
class LaunchTestDialog(QDialog):
    """启动耗时测试对话框：本次结果 + 历史趋势 + 基线对比"""

    def __init__(self, package, cold_ms, warm_ms, history=None, baseline=None,
                 baseline_setter=None, parent=None):
        super().__init__(parent)
        self.setObjectName("LaunchTestDialog")
        self.setWindowTitle("启动耗时测试")
        self.setModal(True)
        self.resize(620, 560)
        self.package = package
        self.cold_ms = cold_ms
        self.warm_ms = warm_ms
        self.history = history or []      # List[LaunchResult]
        self.baseline = baseline or None  # {'cold_ms','warm_ms','set_at'}
        self._baseline_setter = baseline_setter
        self.retest_requested = False

        self.setup_ui()
        self.apply_theme()

    # ------------------------------------------------------------------
    def _history_points(self):
        """返回 (cold_list, warm_list)，每个元素是 (序号, ms)，跳过失败项(-1/0)"""
        cold, warm = [], []
        for i, rec in enumerate(self.history):
            c = rec.cold_start_ms
            w = rec.warm_start_ms
            if c and c > 0:
                cold.append((i + 1, c))
            if w and w > 0:
                warm.append((i + 1, w))
        return cold, warm

    def _draw_trend(self):
        """绘制冷/热启动历史趋势 + 基线参考线"""
        plot = self.trend_plot
        plot.clear()
        pg.setConfigOptions(antialias=True)
        cold_pts, warm_pts = self._history_points()

        # 冷启动曲线（蓝）
        if cold_pts:
            xs = [p[0] for p in cold_pts]
            ys = [p[1] for p in cold_pts]
            plot.plot(xs, ys, pen=pg.mkPen('#3498db', width=2),
                      name='冷启动', symbol='o', symbolSize=5,
                      symbolBrush='#3498db')
        # 热启动曲线（绿）
        if warm_pts:
            xs = [p[0] for p in warm_pts]
            ys = [p[1] for p in warm_pts]
            plot.plot(xs, ys, pen=pg.mkPen('#27ae60', width=2),
                      name='热启动', symbol='o', symbolSize=5,
                      symbolBrush='#27ae60')

        # 基线参考线（虚线）
        if self.baseline:
            n = max([p[0] for p in (cold_pts + warm_pts)] or [1])
            if self.baseline.get('cold_ms', 0) > 0:
                c = self.baseline['cold_ms']
                plot.plot([0, n + 0.5], [c, c],
                          pen=pg.mkPen('#3498db', width=1, style=Qt.PenStyle.DashLine))
            if self.baseline.get('warm_ms', 0) > 0:
                w = self.baseline['warm_ms']
                plot.plot([0, n + 0.5], [w, w],
                          pen=pg.mkPen('#27ae60', width=1, style=Qt.PenStyle.DashLine))

    def setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)

        title = QLabel("启动耗时测试")
        title.setStyleSheet("font-size: 16px; font-weight: bold;")
        root.addWidget(title)

        sub = QLabel(f"应用: {self.package}")
        sub.setStyleSheet("font-size: 12px; color: #999;")
        root.addWidget(sub)

        # 结果卡片
        result_card = QFrame()
        result_card.setObjectName("LaunchResultCard")
        result_layout = QHBoxLayout(result_card)
        result_layout.setContentsMargins(20, 16, 20, 16)
        result_layout.setSpacing(24)

        cold_col = QVBoxLayout()
        cold_col.setSpacing(2)
        cold_val = QLabel(f"{self.cold_ms}" if self.cold_ms > 0 else "失败")
        cold_val.setObjectName("LaunchNumber")
        cold_val.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cold_lbl = QLabel("冷启动 (ms)")
        cold_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cold_lbl.setObjectName("LaunchLabel")
        cold_col.addWidget(cold_val)
        cold_col.addWidget(cold_lbl)
        self.cold_vs = QLabel("")
        self.cold_vs.setObjectName("LaunchVs")
        self.cold_vs.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cold_col.addWidget(self.cold_vs)
        result_layout.addLayout(cold_col)

        warm_col = QVBoxLayout()
        warm_col.setSpacing(2)
        warm_val = QLabel(f"{self.warm_ms}" if self.warm_ms > 0 else "失败")
        warm_val.setObjectName("LaunchNumber")
        warm_val.setAlignment(Qt.AlignmentFlag.AlignCenter)
        warm_lbl = QLabel("热启动 (ms)")
        warm_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        warm_lbl.setObjectName("LaunchLabel")
        warm_col.addWidget(warm_val)
        warm_col.addWidget(warm_lbl)
        self.warm_vs = QLabel("")
        self.warm_vs.setObjectName("LaunchVs")
        self.warm_vs.setAlignment(Qt.AlignmentFlag.AlignCenter)
        warm_col.addWidget(self.warm_vs)
        result_layout.addLayout(warm_col)

        root.addWidget(result_card)
        self._update_vs_labels()

        # 历史趋势图
        hist_title = QLabel("历史趋势（冷启动/热启动，虚线为基线）")
        hist_title.setStyleSheet("font-weight: 600; font-size: 13px;")
        root.addWidget(hist_title)

        self.trend_plot = pg.PlotWidget()
        self.trend_plot.setMinimumHeight(180)
        self.trend_plot.setMenuEnabled(False)
        self.trend_plot.setMouseEnabled(x=False, y=False)
        self.trend_plot.hideButtons()
        self.trend_plot.showGrid(x=True, y=True, alpha=0.25)
        self.trend_plot.setLabel('bottom', '测试序号')
        self.trend_plot.setLabel('left', '耗时 (ms)')
        self.trend_plot.addLegend(offset=(10, 10))
        self._draw_trend()
        root.addWidget(self.trend_plot, 1)

        # 历史记录文字
        hist_text = QLabel("历史记录（最近 10 次）")
        hist_text.setStyleSheet("font-weight: 600; font-size: 13px;")
        root.addWidget(hist_text)

        self.history_list = QTextEdit()
        self.history_list.setReadOnly(True)
        self.history_list.setMaximumHeight(110)
        if self.history:
            for rec in self.history[-10:]:
                ts = (rec.timestamp or "")[:19].replace("T", " ")
                self.history_list.append(
                    f"{ts}  冷启动: {rec.cold_start_ms if rec.cold_start_ms > 0 else '-'} ms  "
                    f"热启动: {rec.warm_start_ms if rec.warm_start_ms > 0 else '-'} ms"
                )
        else:
            self.history_list.setPlainText("暂无历史记录")
        root.addWidget(self.history_list)

        # 按钮
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self.set_baseline_btn = QPushButton("设为基线")
        self.set_baseline_btn.setObjectName("baselineBtn")
        self.set_baseline_btn.setFixedSize(110, 34)
        self.set_baseline_btn.clicked.connect(self._on_set_baseline)
        btn_row.addWidget(self.set_baseline_btn)

        retest_btn = QPushButton("重新测试")
        retest_btn.setObjectName("retestBtn")
        retest_btn.setFixedSize(110, 34)
        retest_btn.clicked.connect(self._on_retest)
        btn_row.addWidget(retest_btn)

        close_btn = QPushButton("关闭")
        close_btn.setObjectName("closeBtn")
        close_btn.setFixedSize(100, 34)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)

        root.addLayout(btn_row)

    # ------------------------------------------------------------------
    def _update_vs_labels(self):
        """结果卡片里的基线对比小字"""
        def _fmt(name, cur, base):
            if cur <= 0 or base is None or base <= 0:
                return ""
            pct = (cur - base) / base * 100.0
            arrow = "↑" if pct > 0 else ("↓" if pct < 0 else "—")
            color = "#e74c3c" if pct > 10 else ("#27ae60" if pct < -10 else "#999999")
            return f"vs 基线 {pct:+.1f}% {arrow}"
        if self.baseline:
            self.cold_vs.setText(_fmt("冷", self.cold_ms, self.baseline.get('cold_ms')))
            self.warm_vs.setText(_fmt("热", self.warm_ms, self.baseline.get('warm_ms')))
        else:
            self.cold_vs.setText("未设基线")
            self.warm_vs.setText("未设基线")

    def _on_set_baseline(self):
        if self._baseline_setter is not None:
            self._baseline_setter(self.cold_ms, self.warm_ms)
        self.baseline = {
            'cold_ms': self.cold_ms if self.cold_ms > 0 else 0,
            'warm_ms': self.warm_ms if self.warm_ms > 0 else 0,
        }
        self._update_vs_labels()
        self._draw_trend()
        show_toast(self, "已设为该应用的启动耗时基线")

    def _on_retest(self):
        self.retest_requested = True
        self.accept()

    def apply_theme(self, theme_mode=None):
        if theme_mode is None:
            theme_mode = ThemeMode.DARK if Settings.get_theme_mode() == THEME_MODE_DARK else ThemeMode.LIGHT

        if theme_mode == ThemeMode.DARK:
            bg, text = "#2d2d2d", "#eeeeee"
            card_bg = "#3c3c3c"
            card_border = "#555"
            edit_bg = "#2d2d2d"
            edit_border = "#555"
            plot_bg = "#1c1d20"
            grid_color = "#3a3a3a"
            axis_color = "#aaaaaa"
            baseline_btn_bg = "#1976d2"
        else:
            bg, text = "#ffffff", "#333333"
            card_bg = "#f8f9fa"
            card_border = "#e0e0e0"
            edit_bg = "#ffffff"
            edit_border = "#d0d0d0"
            plot_bg = "#fafbfc"
            grid_color = "#e8e8e8"
            axis_color = "#888888"
            baseline_btn_bg = "#1976d2"

        self.trend_plot.setBackground(plot_bg)
        for ax in ('left', 'bottom'):
            axis = self.trend_plot.getAxis(ax)
            axis.setPen(pg.mkPen(color=grid_color))
            axis.setTextPen(pg.mkPen(color=axis_color))

        self.setStyleSheet(f"""
            #LaunchTestDialog {{
                background-color: {bg};
            }}
            #LaunchTestDialog QLabel {{
                color: {text};
                background: transparent;
            }}
            #LaunchResultCard {{
                background-color: {card_bg};
                border: 1px solid {card_border};
                border-radius: 8px;
            }}
            #LaunchResultCard QLabel#LaunchNumber {{
                font-size: 28px;
                font-weight: bold;
                color: #1976d2;
            }}
            #LaunchResultCard QLabel#LaunchLabel {{
                font-size: 12px;
                color: #999999;
            }}
            #LaunchResultCard QLabel#LaunchVs {{
                font-size: 11px;
                color: #999999;
            }}
            #LaunchTestDialog QTextEdit {{
                background-color: {edit_bg};
                color: {text};
                border: 1px solid {edit_border};
                border-radius: 6px;
                font-family: Consolas, monospace;
                font-size: 12px;
                padding: 6px;
            }}
            #LaunchTestDialog QPushButton {{
                background-color: #1976d2;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 6px 14px;
                font-weight: 500;
            }}
            #LaunchTestDialog QPushButton#retestBtn {{
                background-color: {card_bg};
                color: {text};
                border: 1px solid {card_border};
            }}
            #LaunchTestDialog QPushButton#baselineBtn {{
                background-color: {baseline_btn_bg};
                color: white;
                border: none;
            }}
        """)