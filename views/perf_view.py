# views/perf_view.py
"""性能检测主视图"""
import os
import time
import logging

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QComboBox, QPushButton, QCheckBox, QRadioButton, QButtonGroup,
    QSpinBox, QFrame, QScrollArea, QSizePolicy,
    QFileDialog, QDialog, QStackedWidget, QListWidget, QListWidgetItem,
    QDialogButtonBox
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
from PyQt6.QtWidgets import (QStyleOptionButton, QStyle, QStyledItemDelegate,
                             QAbstractItemView)
from PyQt6.QtGui import QPainter, QPen, QColor
from PyQt6.QtCore import QRect, QEvent, QPointF
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

# CheckStateRole 读出来是 int：0 未选 / 1 半选 / 2 选中
_CHECKED = int(Qt.CheckState.Checked.value)


class _ProcCheckDelegate(QStyledItemDelegate):
    """列表行自绘：左边方框（选中画蓝勾），右边进程名。

    为什么不用 QSS 的 ::indicator：QSS 选中态只能填一块颜色，要画勾必须引用图片，
    而图片在 PyInstaller 打包后路径容易失效。这里用 QPainter 直接画，
    换主题就是换几个颜色值，也没有额外资源文件。
    """

    BOX = 15
    ROW_H = 29
    PAD_L = 10

    def __init__(self, colors, parent=None):
        super().__init__(parent)
        self.c = colors

    def _box_rect(self, rect):
        return QRect(rect.left() + self.PAD_L,
                     rect.center().y() - self.BOX // 2 + 1,
                     self.BOX, self.BOX)

    def sizeHint(self, option, index):
        h = super().sizeHint(option, index).height()
        return QSize(0, max(self.ROW_H, h))

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect

        # 不画「选中」底色：这个列表里勾选才是状态，点一下留一整条蓝底反而干扰
        if option.state & QStyle.StateFlag.State_MouseOver:
            painter.fillRect(rect, QColor(self.c["hover"]))

        # index.data(CheckStateRole) 在 PyQt6 返回 int，直接和 Qt.CheckState 枚举比
        # 永远是 False（枚举不是 IntEnum）—— 初始勾选的行会画不出勾。统一按 int 比。
        checked = int(index.data(Qt.ItemDataRole.CheckStateRole) or 0) == _CHECKED
        box = self._box_rect(rect)
        accent = QColor(self.c["accent"])
        painter.setPen(QPen(accent if checked else QColor(self.c["box_bd"]), 1.2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(box, 3.5, 3.5)

        if checked:
            pen = QPen(accent, 1.9)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            x, y = box.left(), box.top()
            # 两段直线画勾（比 QPolygonF 少一层依赖，PyQt6 里它在 QtGui）
            painter.drawLine(QPointF(x + 3.0, y + 7.7), QPointF(x + 6.2, y + 10.9))
            painter.drawLine(QPointF(x + 6.2, y + 10.9), QPointF(x + 12.0, y + 4.2))

        painter.setPen(QColor(self.c["text"]))
        tr = rect.adjusted(box.right() - rect.left() + 10, 0, -8, 0)
        fm = painter.fontMetrics()
        text = fm.elidedText(index.data() or "", Qt.TextElideMode.ElideRight, tr.width())
        painter.drawText(tr, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
        painter.restore()

    def editorEvent(self, event, model, option, index):
        # 点整行任意位置都能切换（用户要求：不用瞄准方框）
        if (event.type() == QEvent.Type.MouseButtonRelease
                and event.button() == Qt.MouseButton.LeftButton
                and option.rect.contains(event.pos())):
            cur = int(index.data(Qt.ItemDataRole.CheckStateRole) or 0)
            new = 0 if cur == _CHECKED else _CHECKED
            model.setData(index, new, Qt.ItemDataRole.CheckStateRole)
            return True
        return super().editorEvent(event, model, option, index)


class MultiProcessDialog(QDialog):
    """选择要一起监控的多个目标进程（主进程 + 子进程）。

    与性能报告「目标进程」的口径一致：CPU / 内存按勾选的进程求和，
    FPS / 流量 / 线程仍按主进程（列表里第一个勾选的）。
    """

    def __init__(self, processes, selected=None, parent=None):
        super().__init__(parent)
        self.setObjectName("MultiProcessDialog")
        self.setWindowTitle("选择目标进程")
        self.setMinimumWidth(480)
        self.resize(500, 440)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.setSpacing(10)

        self.tip = QLabel("勾选要一起监控的进程，CPU / 内存按所选进程求和。\n"
                          "第一个勾选的作为主进程，FPS / 流量 / 线程仍按它采集。")
        self.tip.setWordWrap(True)
        lay.addWidget(self.tip)

        self.list = QListWidget()
        self.list.setObjectName("ProcList")
        # 只在纵向上滚动：包名本来就短，横向滚动条纯属碍事
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.list.setUniformItemSizes(True)
        # 没有「选中行」这个概念：勾选才是状态，避免点一下留下蓝底 + 焦点框
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        chosen = set(selected or [])
        for name in processes:
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if name in chosen else Qt.CheckState.Unchecked)
            self.list.addItem(item)
        lay.addWidget(self.list, 1)

        box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        ok_btn = box.button(QDialogButtonBox.StandardButton.Ok)
        cancel_btn = box.button(QDialogButtonBox.StandardButton.Cancel)
        ok_btn.setText("确定")
        cancel_btn.setText("取消")
        ok_btn.setObjectName("primaryBtn")
        for btn in (ok_btn, cancel_btn):
            btn.setMinimumSize(84, 32)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)

        self._apply_style()

    # ---------- 外观 ----------
    def _is_dark(self) -> bool:
        """当前是否深色主题。

        注意 THEME_MODE_DARK 在 utils.settings（不是 utils.theme）——
        2026-10-29 曾从 utils.theme 导入，ImportError 被 except 吞掉，
        结果无论什么主题都走浅色。
        """
        try:
            return Settings.get_theme_mode() == THEME_MODE_DARK
        except Exception:
            return False

    def _apply_style(self):
        dark = self._is_dark()
        if dark:
            bg, tip_c = "#1e2023", "#a9b0b8"
            border, list_bg, item_c = "#34373c", "#26282b", "#e5e7ea"
            hover, sel = "#2c2f33", "#2f4a72"
            box_bd, bar_c, bar_h = "#5a6068", "#4b5057", "#5f656d"
            btn_bd, cancel_fg = "#43474d", "#d6d9dd"
            accent = "#4c8dff"
        else:
            bg, tip_c = "#f6f7f9", "#6b7280"
            border, list_bg, item_c = "#dfe3e8", "#ffffff", "#2b2f33"
            hover, sel = "#f0f3f7", "#dbe7f5"
            box_bd, bar_c, bar_h = "#c3c9d0", "#c9ced6", "#b3b9c2"
            btn_bd, cancel_fg = "#d5dae0", "#3a3f45"
            accent = "#2d6cdf"

        self.setStyleSheet(f"""
            QDialog#MultiProcessDialog {{ background: {bg}; }}
            QLabel {{ font-size: 13px; color: {tip_c}; background: transparent; }}
            QPushButton#primaryBtn {{
                background: #2d6cdf; color: #ffffff; border: none;
                border-radius: 5px; font-size: 13px; padding: 0 16px;
            }}
            QPushButton#primaryBtn:hover {{ background: #3a7aeb; }}
            QPushButton#primaryBtn:pressed {{ background: #2560c8; }}
            QPushButton {{
                background: transparent; color: {cancel_fg};
                border: 1px solid {btn_bd}; border-radius: 5px;
                font-size: 13px; padding: 0 16px;
            }}
            QPushButton:hover {{ border-color: #2d6cdf; color: #2d6cdf; }}
        """)

        # 行内容（方框 + 勾 + 文字）由 delegate 自绘，QSS 只管容器外观；
        # 滚动条也单独设（同一份 QSS 里混写 ::item 与 QScrollBar，
        # 部分 Qt 版本会丢滚动条规则 —— theme.py 里已有同样注释）
        self.list.setStyleSheet(f"""
            QListWidget#ProcList {{
                background: {list_bg}; border: 1px solid {border};
                border-radius: 6px; outline: none; padding: 4px;
                font-size: 13px; color: {item_c};
            }}
        """)
        self.list.setItemDelegate(_ProcCheckDelegate({
            "text": item_c, "box_bd": box_bd, "accent": accent,
            "hover": hover, "sel": sel,
        }, self.list))
        self.list.verticalScrollBar().setStyleSheet(f"""
            QScrollBar:vertical {{
                background: transparent; width: 8px; margin: 2px 0; border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {bar_c}; min-height: 28px; border-radius: 4px;
            }}
            QScrollBar::handle:vertical:hover {{ background: {bar_h}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0; border: none; background: none;
            }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
                background: none;
            }}
        """)

    def selected_processes(self) -> list:
        out = []
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                out.append(item.text())
        return out


class _WrappingGroup(QFrame):
    """能自己折行的分组框（性能页顶部「应用 / 采集 / 指标」等分组）。

    内层是 FlowLayout：宽屏按 sizeHint（全部平铺）给宽度就是一行；
    容器变窄时按可用宽度重新排，超出就折到第二行，高度随之增加。
    """

    SPACING = 8
    H_MARGIN = 10
    V_MARGIN = 6

    def __init__(self, title, widgets, parent=None):
        super().__init__(parent)
        self.setObjectName("PerfGroup")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setContentsMargins(self.H_MARGIN, self.V_MARGIN,
                                self.H_MARGIN, self.V_MARGIN)
        self._flow = FlowLayout(self, margin=0, spacing=self.SPACING)
        self._title = None
        if title:
            self._title = QLabel(title)
            self._title.setObjectName("PerfGroupTitle")
            self._flow.addWidget(self._title)
            self._flow.addSpacing(2) if hasattr(self._flow, "addSpacing") else None
        for w in widgets:
            self._flow.addWidget(w)

    def _flat_width(self):
        """全部控件平铺所需宽度（宽屏时按这个值一行排开）。"""
        total = 0
        for i in range(self._flow.count()):
            it = self._flow.itemAt(i)
            wdg = it.widget() if it is not None else None
            if wdg is not None and not wdg.isHidden():
                total += wdg.sizeHint().width() + self.SPACING
        # +4 是防止差 1px 就折行：内层 FlowLayout 用 rect.right() 判边界，
        # 刚好等于累加宽时会判成"放不下"，宽屏下就莫名折成两行
        return max(0, total - self.SPACING) + 2 * self.H_MARGIN + 4

    def sizeHint(self):
        return QSize(self._flat_width(), 44)

    def minimumSizeHint(self):
        inner = self._flow.minimumSize()
        return QSize(inner.width() + 2 * self.H_MARGIN, 44)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._flow.heightForWidth(max(1, width - 2 * self.H_MARGIN)) + 2 * self.V_MARGIN


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
    smaps_interval_changed = pyqtSignal()
    record_once_requested = pyqtSignal()
    record_toggle_requested = pyqtSignal(bool)
    record_interval_changed = pyqtSignal()
    record_duration_changed = pyqtSignal()          # 堆转储间隔变更
    packet_toggle_requested = pyqtSignal(bool)     # 抓包开始/停止
    packet_config_requested = pyqtSignal()         # 打开「抓包参数」对话框
    screenshot_toggle_requested = pyqtSignal(bool)  # 截图自动循环开关（性能工具卡片）
    screenshot_once_requested = pyqtSignal()        # 手动触发一次截图
    screenshot_interval_changed = pyqtSignal()      # 截图间隔变更

    # 状态枚举（曾经的「暂停」状态已随暂停/继续按钮一并移除）
    STATE_IDLE = 'idle'
    STATE_RUNNING = 'running'
    STATE_SCENARIO = 'scenario'
    # 停止中：点「停止」到回到「开始」之间的过渡态。
    # 控制器的停止是同步阻塞的（worker.wait / 探针收尾 / 写文件），直接跳回
    # 「开始」会让人以为按钮没生效，所以插入这个状态并强制停留一段时间。
    STATE_STOPPING = 'stopping'

    # 「停止中」最少停留时长（毫秒）：用户要求至少 2 秒
    _STOPPING_MIN_MS = 2000
    # 兜底上限：万一控制器异常没把状态切回空闲，也不能永远卡在「停止中」
    _STOPPING_MAX_MS = 15000

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PerfView")
        self._theme_mode = ThemeMode.LIGHT
        self._has_wallpaper = False
        self._state = self.STATE_IDLE
        # 「停止中」计时：deadline 是"最早可以回空闲"的时刻；pending 表示
        # 控制器那边的收尾工作已经做完了，只等最短停留时间到点
        self._stop_deadline = 0.0
        self._stop_pending = False
        self._stop_timer = QTimer(self)
        self._stop_timer.setSingleShot(True)
        self._stop_timer.timeout.connect(self._maybe_leave_stopping)
        self._stop_guard = QTimer(self)
        self._stop_guard.setSingleShot(True)
        self._stop_guard.timeout.connect(self._force_leave_stopping)
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
        # 开始/停止二合一按钮的三套外观（在 apply_theme 里按主题生成）
        self._run_btn_qss_normal = ""
        self._run_btn_qss_danger = ""
        self._run_btn_qss_stopping = ""
        # 应用列表是否已成功加载（决定「开始」按钮可用性；见 update_app_list）
        self._apps_loaded = False
        # 配置恢复期间的守卫：恢复过程会触发控件的变更信号，此时不回写配置，
        # 否则会把「恢复出来的值」当成用户刚改的值又存一遍（无害但纯属多余写入）
        self._perf_cfg_ready = False
        # 记忆里的上次应用包名：应用列表还没加载（或首次加载失败）时用它回选
        self._pref_package = ""
        # 记忆里的上次套件名：套件列表由 set_suite_model 稍后填充，届时回选
        self._pref_suite = ""
        # 多进程目标：当前列表里可选的运行中进程，以及已勾选的目标进程（主进程在第一个）
        self._running_processes = []
        self._multi_processes = []

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
        self._running_processes = list(packages or [])
        self._check_state()

    # ------------------------------------------------------------------
    # 多进程目标
    # ------------------------------------------------------------------
    def _on_app_text_changed(self, text: str):
        """主进程变了：之前勾的其它目标进程不再属于同一组，清掉。"""
        if self._multi_processes and text not in self._multi_processes:
            self._multi_processes = []
            self._update_multi_btn()
        self._check_state()

    def _update_multi_btn(self):
        n = len(self._multi_processes)
        self.app_multi_btn.setText(f"多进程（{n}）" if n > 1 else "多进程")
        if n > 1:
            self.app_multi_btn.setToolTip("本次目标进程：\n" + "\n".join(self._multi_processes))
        else:
            self.app_multi_btn.setToolTip(
                "选择多个目标进程一起监控（主进程 + 子进程，CPU / 内存求和）")

    def _choose_processes(self):
        processes = list(self._running_processes)
        current = self.app_combo.currentText().strip()
        if current and not current.startswith("未检测到应用") and current not in processes:
            processes.insert(0, current)      # 允许把手动输入、当前没在跑的进程也纳入
        if not processes:
            show_toast(message="请先刷新应用列表", parent=self)
            return
        selected = self._multi_processes or ([current] if current else [])
        dlg = MultiProcessDialog(processes, selected, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        picked = dlg.selected_processes()
        self._multi_processes = picked
        if picked:
            # 第一个勾选的作为主进程：同步到应用下拉框
            self.app_combo.blockSignals(True)
            self.app_combo.setEditText(picked[0])
            self.app_combo.blockSignals(False)
        self._update_multi_btn()
        self._check_state()
        self._save_perf_config()

    def _target_processes(self) -> list:
        """本次要监控的进程列表（主进程在第一个）。"""
        package = self.app_combo.currentText().strip()
        out = []
        if package and not package.startswith("未检测到应用"):
            out.append(package)
        for p in self._multi_processes:
            if p and p not in out:
                out.append(p)
        return out


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
        """旁路探针的最新快照（由 PerfController 每秒推一次）。"""
        # 整机 CPU / 线程已并入 CPU 指标：没有独立卡片，摘要挂到 CPU 卡片的 tooltip
        if key in ("sys_cpu", "threads"):
            self._update_cpu_side_summary(key, data)
            return
        card = self._cards.get(key)
        if card is None or not hasattr(card, "set_values"):
            return
        data = data or {}
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

    def _update_cpu_side_summary(self, key: str, data: dict):
        """整机 CPU / 线程的实时摘要 → CPU 卡片 tooltip（不额外占版面）。

        这两项是 CPU 分析的附属数据：只看数值时鼠标悬停 CPU 卡就能看到，
        需要完整数据时看报告里的「CPU 分析 / 整机 CPU / 线程分析」章节。
        """
        if not hasattr(self, "_cpu_side"):
            self._cpu_side = {}
        self._cpu_side[key] = data or {}

        parts = []
        sc = self._cpu_side.get("sys_cpu") or {}
        if "cpu_pct" in sc:
            cores = sc.get("cores") or 0
            txt = f"整机 CPU {sc['cpu_pct']:.0f}%"
            if cores:
                busy = sc.get("busy_raw")
                busy = busy if busy is not None else sc['cpu_pct'] * cores
                txt += f"（{cores} 核累计 {busy:.0f}%）"
            parts.append(txt)
        th = self._cpu_side.get("threads") or {}
        if "thread_total" in th:
            parts.append(f"线程 {th['thread_total']} 个（活跃 {th['thread_active']}）")
            tops = (th.get("top") or [])[:3]
            if tops:
                parts.append("最忙线程：" + "、".join(
                    f"{t.get('name', '')} {float(t.get('cpu') or 0):.1f}%" for t in tops))
        errs = [str(d.get("error")) for d in (sc, th) if d.get("error")]
        if errs:
            parts.append("⚠ " + "；".join(errs))
        card = self._cards.get("cpu")
        if card is not None:
            card.setToolTip("\n".join(parts))


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
        # 「运行 → 空闲」不直接生效：中间插一层「停止中」，至少停 _STOPPING_MIN_MS
        # 才回到「开始」。两条路径都会走到这里：
        #   ① 用户点停止：按钮先切成「停止中」（见 _on_run_btn_clicked），
        #      控制器收尾完再调 set_state(IDLE) → 这里只负责"收尾已完成"的标记；
        #   ② 自动停止（到时长 / 场景化跑完）：这里补上「停止中」这一跳。
        if state == self.STATE_IDLE:
            if self._state in (self.STATE_RUNNING, self.STATE_SCENARIO):
                self._enter_stopping()
                self._stop_pending = True
                self._maybe_leave_stopping()
                return
            if self._state == self.STATE_STOPPING:
                self._stop_pending = True
                self._maybe_leave_stopping()
                return
        self._state = state
        self._apply_state()

    def _enter_stopping(self):
        """切到「停止中」：立刻改按钮观感，并起最短停留 + 兜底两个计时器。"""
        if self._state == self.STATE_STOPPING:
            return
        self._stop_pending = False
        self._stop_deadline = time.monotonic() + self._STOPPING_MIN_MS / 1000.0
        self._state = self.STATE_STOPPING
        self._apply_state()
        # 必须强制重绘一次：紧接着的停止是同步阻塞的（等 worker、收探针、落盘），
        # 不在这里刷一下，事件循环被占住的整段时间里按钮还是旧样子 —— 点了像没反应
        self.start_btn.repaint()
        self.status_info.repaint()
        self._stop_timer.start(self._STOPPING_MIN_MS)
        self._stop_guard.start(self._STOPPING_MAX_MS)

    def _maybe_leave_stopping(self):
        """收尾已完成且已过最短停留时间 → 真正回到空闲。"""
        if self._state != self.STATE_STOPPING or not self._stop_pending:
            return
        remain = self._stop_deadline - time.monotonic()
        if remain > 0:
            # 还没停够最短时长，把剩余时间再排一次
            self._stop_timer.start(int(remain * 1000) + 20)
            return
        self._stop_guard.stop()
        self._stop_pending = False
        self._state = self.STATE_IDLE
        self._apply_state()

    def _force_leave_stopping(self):
        """兜底：控制器异常没回空闲时也要还原界面，不能永久卡在「停止中」。"""
        if self._state != self.STATE_STOPPING:
            return
        logger.warning("停止中超过 %d ms 仍未收到空闲状态，强制还原界面",
                       self._STOPPING_MAX_MS)
        self._stop_pending = True
        self._stop_deadline = 0.0
        self._maybe_leave_stopping()

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
        图标）；停止中 = 橙色沙漏（表示正在收尾，不接受点击）。几套 QSS 由
        apply_theme 按当前主题预算好（见 _run_btn_qss_*），这里只做切换；
        按钮被置灰（未选应用）时只留文案、去掉图标。
        """
        if self._state == self.STATE_STOPPING:
            self.start_btn.setText("停止中")
            self.start_btn.setToolTip("正在停止采集，请稍候…")
            self.start_btn.setStyleSheet(
                self._run_btn_qss_stopping or self._run_btn_qss_danger)
            # 不带图标：文案本身已经说清是在收尾，多一个沙漏反而显得忙乱
            self.start_btn.setIcon(QIcon())
            return
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
            (self.packet_config_btn, 'fa6s.sliders', flat),
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

    # ---------- 录屏 ----------
    @staticmethod
    def _combo_seconds(text: str, default: int) -> int:
        """把「10 分钟 / 30 秒」这类文案换成秒（注意分钟要 ×60，不能只取数字）。"""
        t = (text or "").strip()
        digits = "".join(ch for ch in t if ch.isdigit())
        if not digits:
            return default
        try:
            val = int(digits)
        except ValueError:
            return default
        return val * 60 if "分" in t else val

    def get_record_interval(self) -> float:
        """自动录屏间隔（秒）"""
        return float(self._combo_seconds(self.record_interval_combo.currentText(), 600))

    def get_record_duration(self) -> int:
        """单段时长（秒）"""
        return self._combo_seconds(self.record_duration_combo.currentText(), 30)

    def record_config_ok(self) -> bool:
        """间隔必须大于单段时长，否则两段会重叠（screenrecord 同时在跑会失败）"""
        return self.get_record_interval() > self.get_record_duration()

    def _on_record_cfg_changed(self, *_):
        """间隔/时长变化时给出提示（不阻止，真正启动时会校验）。

        提示放在下拉的 tooltip + 卡片圆点上，不再往卡片里塞一行字 ——
        「循环间隔 / 录制时长」两个标签把这一行占满了（2026-10-29）。
        """
        ok = self.record_config_ok()
        tip = "" if ok else "间隔必须大于单段时长，否则两段录制会重叠"
        self.record_interval_combo.setToolTip(tip)
        self.record_duration_combo.setToolTip(tip)
        self._set_dot_alert(getattr(self, '_record_dot', None), not ok)

    def record_loop_enabled(self) -> bool:
        return bool(self.record_enable_check.isChecked())

    def _on_record_toggle(self, checked: bool):
        """录制循环勾选变化：放开/锁住间隔与时长下拉，再通知控制器启停。

        之前只发了信号、没刷新下拉的可用性，导致勾上「循环间隔」后点间隔
        下拉没反应（2026-10-29 反馈）。监控中勾选本身是禁用的，这里再兜一层
        idle 判断，避免运行中被别处调用时把下拉放开。
        """
        idle = self._state == self.STATE_IDLE
        self.record_interval_combo.setEnabled(idle and checked)
        self.record_duration_combo.setEnabled(idle and checked)
        self.record_toggle_requested.emit(checked)

    def smaps_loop_enabled(self) -> bool:
        return bool(self.smaps_enable_check.isChecked())

    def get_smaps_interval(self) -> float:
        """自动抓取间隔（秒）。统一走 _combo_seconds，别再各写一套解析。"""
        return float(self._combo_seconds(self.smaps_interval_combo.currentText(), 300))

    def probe_interval(self, key: str) -> float:
        """取某探针卡片上选中的间隔（秒）；拿不到就返回 0，让控制器用探针默认值。"""
        card = self._cards.get(key)
        if card is None or not hasattr(card, "interval_seconds"):
            return 0.0
        try:
            return float(card.interval_seconds())
        except Exception:
            return 0.0

    # 运行中不允许改动的勾选：卡片显隐 + 整机探针。
    # **新增勾选时只改这里**，不要在 _apply_state 里逐个手写 ——
    # 2026-10-26、2026-10-28 两次都是因为漏写某个勾选，导致监控中还能点。
    _IDLE_ONLY_CHECKS = (
        'sys_cpu_check', 'sys_mem_check', 'netdev_check', 'iotop_check',
        'record_visible_check', 'smaps_visible_check',
        'hprof_visible_check', 'packet_visible_check', 'screenshot_visible_check',
    )

    def _idle_only_checks(self):
        return [w for w in (getattr(self, n, None) for n in self._IDLE_ONLY_CHECKS)
                if w is not None]

    def _apply_state(self):
        state = self._state
        idle = state == self.STATE_IDLE

        # 开始/停止二合一：空闲时按「应用列表是否加载成功」决定可用性，
        # 采集中恒可用（点即停止）；停止中一律不可点（收尾还没完，重复点没意义）
        app_ok = bool(getattr(self, '_apps_loaded', False))
        if state == self.STATE_STOPPING:
            self.start_btn.setEnabled(False)
        else:
            self.start_btn.setEnabled(app_ok if idle else True)
        self._refresh_run_btn()

        self.app_combo.setEnabled(idle)
        self.app_multi_btn.setEnabled(idle)
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

        # 卡片显隐 / 整机探针勾选：非空闲一律禁用（列表统一处理）
        for chk in self._idle_only_checks():
            chk.setEnabled(idle)
        # 整机探针的间隔下拉：探针只在开始时启动，运行中同样锁定
        for key in ('sys_cpu', 'sys_mem', 'netdev', 'iotop'):
            combo = self._probe_interval_combo(key)
            if combo is not None:
                combo.setEnabled(idle)

        # 堆转储设置：监控中锁定（自动循环勾选与间隔不可改）；空闲时按勾选状态开放间隔
        self.record_enable_check.setEnabled(idle)
        self.record_interval_combo.setEnabled(idle and self.record_enable_check.isChecked())
        self.record_duration_combo.setEnabled(idle and self.record_enable_check.isChecked())
        self.record_once_btn.setEnabled(True)
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
            """语义分组容器：组名 + 控件，宽屏一行、窄屏在组内自己折行。

            以前用 QHBoxLayout：组内永不换行，组的"最小宽度"就是所有控件宽度之和
            —— 「指标」组 12 个勾选约 960px，直接把页面最小宽度顶到 980，
            1366×768@150%（页面可用宽 814）时横向溢出。
            现在内层用 FlowLayout：最小宽度 = 最宽的那个控件，窄屏组内折行；
            宽屏时 sizeHint 仍给平铺宽度，观感与原来一致（同样 44px 高）。
            """
            return _WrappingGroup(title, widgets)

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
        self.app_combo.currentTextChanged.connect(self._on_app_text_changed)
        self.app_multi_btn = QPushButton("多进程")
        self.app_multi_btn.setMinimumWidth(96)      # 容得下「多进程（3）」，数字变两位也不截断
        self.app_multi_btn.setToolTip(
            "选择多个目标进程一起监控（主进程 + 子进程，CPU / 内存求和）")
        self.app_multi_btn.clicked.connect(self._choose_processes)
        self.app_refresh_btn = QPushButton("刷新")
        self.app_refresh_btn.setIcon(qta.icon('fa6s.rotate', color='white'))
        self.app_refresh_btn.clicked.connect(self.app_list_refresh_requested.emit)
        flow1.addWidget(_make_group(
            None, [self.app_combo, self.app_multi_btn, self.app_refresh_btn]))

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
        for key, label in [('fps', 'FPS'), ('traffic', '应用流量')]:
            cb = BorderedCheckBox(label)
            self.metric_radios[key] = cb
            # 勾选状态联动下方卡片显隐
            cb.toggled.connect(self._on_card_visibility_changed)
            metric_widgets.append(cb)

        # 整机口径的旁路探针（独立于主采样，勾选后随监控启动）
        self.iotop_check = BorderedCheckBox("磁盘 IO")
        self.iotop_check.setToolTip(
            "设备整体磁盘读写速率（整机口径）\n"
            "⚠ 需要设备 root：读取进程 IO 统计被系统限制，未 root 时该指标不可用")
        self.iotop_check.toggled.connect(self._on_card_visibility_changed)
        metric_widgets.append(self.iotop_check)

        self.netdev_check = BorderedCheckBox("网卡流量")
        self.netdev_check.setToolTip(
            "设备各网卡的收发速率与累计流量（整机口径，所有应用合计），"
            "与上方「应用流量」卡（所选应用）不同")
        self.netdev_check.toggled.connect(self._on_card_visibility_changed)
        metric_widgets.append(self.netdev_check)

        self.sys_mem_check = BorderedCheckBox("系统内存")
        self.sys_mem_check.setToolTip(
            "整机内存（Total / Free / Used / Lost），按设备统计，与上方「内存」卡的所选进程口径不同")
        self.sys_mem_check.toggled.connect(self._on_card_visibility_changed)
        metric_widgets.append(self.sys_mem_check)

        self.record_visible_check = BorderedCheckBox("录屏")
        self.record_visible_check.setToolTip(
            "显示「录屏」卡片：按间隔自动录一段屏幕视频（间隔必须大于单段时长，否则会重叠）")
        self.record_visible_check.toggled.connect(self._on_card_visibility_changed)
        metric_widgets.append(self.record_visible_check)

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

        # 展示顺序与创建顺序解耦：创建顺序散在整段初始化里（还被 _apply_state
        # 等逻辑依赖），调个展示顺序不该牵动它们，所以在这里按文案重排。
        # 新加的勾选没登记进 _metric_order 时自动排到末尾，不会凭空消失。
        _metric_order = [
            'CPU', '内存',
            '堆转储', '截图', '录屏',
            'FPS', '应用流量',
            '网卡流量', '抓包',
            '系统内存', '内存明细', '磁盘 IO',
        ]
        _by_text = {w.text(): w for w in metric_widgets}
        _ordered = [_by_text.pop(t) for t in _metric_order if t in _by_text]
        metric_widgets = _ordered + list(_by_text.values())
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
        # 两列等宽：以前不加 stretch，两列各按内容撑，实测 551 / 507 永远不等宽
        self.cards_grid.setColumnStretch(0, 1)
        self.cards_grid.setColumnStretch(1, 1)

        self._build_cards()

        self.scroll.setWidget(self.cards_container)
        root.addWidget(self.scroll, 1)

        # ---------- 底部状态栏（单行：状态左 + 按钮右）----------
        bottom = QFrame()
        bottom.setObjectName("PerfBottomBar")
        bottom.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        b_layout = QHBoxLayout(bottom)
        # 边距/间距比原来紧一档：底部栏 6 个按钮 + 状态文字是页面最小宽度的
        # 最后一道瓶颈（879），1366×768@150% 下页面只有 814 可用
        b_layout.setContentsMargins(10, 6, 10, 6)
        b_layout.setSpacing(5)

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

        self.export_csv_btn = QPushButton("导出")
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
            'traffic', '应用流量 (KB/s)', 'KB/s', '#9b59b6',
            y_min=0, y_max=100,
            series=[
                {'name': '接收', 'color': '#9b59b6'},
                {'name': '发送', 'color': '#e67e22'},
            ]
        )

        # 整机探针：数值卡（单行 44px），不画曲线；间隔可调，默认 15 秒
        # （dumpsys meminfo 一次 3~5 秒，5 秒节奏会把整机 CPU 抬高几个百分点）
        # 注意：整机 CPU 与线程已并入 CPU 指标（随 CPU 自动采集、进报告），不再有卡片
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
        self._cards['netdev'].setVisible(False)
        self._cards['iotop'].setVisible(False)
        self._hprof_card.setVisible(False)
        self._smaps_card.setVisible(False)
        self._packet_card.setVisible(False)
        self._screenshot_card.setVisible(False)
        self._record_card.setVisible(False)

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
        for key in ('sys_mem', 'netdev', 'iotop'):
            combo = self._probe_interval_combo(key)
            if combo is not None:
                combo.currentTextChanged.connect(self._save_perf_config)
        self.record_enable_check.toggled.connect(self._save_perf_config)
        self.record_interval_combo.currentTextChanged.connect(self._save_perf_config)
        self.record_duration_combo.currentTextChanged.connect(self._save_perf_config)
        self.smaps_enable_check.toggled.connect(self._save_perf_config)
        self.smaps_interval_combo.currentTextChanged.connect(self._save_perf_config)
        for chk in (self.sys_mem_check, self.netdev_check,
                    self.iotop_check, self.record_visible_check,
                    self.smaps_visible_check, self.hprof_visible_check,
                    self.packet_visible_check, self.screenshot_visible_check):
            chk.toggled.connect(self._save_perf_config)
        self.hprof_enable_check.toggled.connect(self._save_perf_config)
        self.hprof_interval_combo.currentTextChanged.connect(self._save_perf_config)
        self.screenshot_enable_check.toggled.connect(self._save_perf_config)
        self.screenshot_interval_combo.currentTextChanged.connect(self._save_perf_config)
        self.suite_combo.currentTextChanged.connect(self._save_perf_config)
        self.loop_spin.valueChanged.connect(self._save_perf_config)
        self.stop_on_fail_check.toggled.connect(self._save_perf_config)

    # 卡片三段的尺寸约定（「扩展方向」第 9 项：卡片排版重构）
    _CARD_TITLE_W = 80          # 标题段固定宽 → 所有卡标题左边缘对齐
    _CARD_BTN_W = 84            # 动作按钮统一宽（四字文案刚好放得下）
    _CARD_BTN_H = 26

    def _build_tool_cards(self):
        """构建工具卡片：堆转储 / 内存明细 / 网络抓包 / 截图 / 录屏。

        统一三段栅格（2026-10-29 按「扩展方向」第 9 项重排）：
            标题段 80px ｜ 参数段（自适应）｜ 按钮段（右对齐，84×26）
        以前是控件一路往左排、末尾 addStretch()，按钮位置随内容长短漂移
        （实测 x 在 97~889 之间跳），扫一眼找不到该点哪儿。
        """

        def _card(dot_color, title):
            card = QFrame()
            card.setObjectName("PerfToolCard")
            grid = QGridLayout(card)
            grid.setContentsMargins(12, 6, 12, 6)
            grid.setHorizontalSpacing(10)
            grid.setVerticalSpacing(0)
            # 三段宽度：标题 80 固定 ｜ 参数自适应 ｜ 按钮段按内容
            grid.setColumnMinimumWidth(0, self._CARD_TITLE_W)
            grid.setColumnStretch(1, 1)

            # 三段都用 addLayout 直接放进栅格，**不要**再套一层 QWidget：
            # 裸 QWidget 会继承 cards_container 的 background-color 并把卡片
            # 底色盖成页面底色（2026-10-29 实测卡片变成纯黑的元凶）。
            head_row = QHBoxLayout()
            head_row.setContentsMargins(0, 0, 0, 0)
            head_row.setSpacing(6)
            dot = QLabel("●")
            # 圆点颜色存进动态属性，apply_theme 刷新时从属性读回，避免逐卡硬编码
            dot.setProperty("dotColor", dot_color)
            dot.setStyleSheet(
                f"color: {dot_color}; font-size: 14px; background: transparent;")
            head_row.addWidget(dot)
            title_label = QLabel(title)
            title_label.setStyleSheet(
                "font-weight: 600; font-size: 13px; background: transparent;")
            head_row.addWidget(title_label)
            head_row.addStretch()
            grid.addLayout(head_row, 0, 0)

            p_row = QHBoxLayout()
            p_row.setContentsMargins(0, 0, 0, 0)
            p_row.setSpacing(8)
            grid.addLayout(p_row, 0, 1)

            a_row = QHBoxLayout()
            a_row.setContentsMargins(0, 0, 0, 0)
            a_row.setSpacing(6)
            a_row.addStretch()
            grid.addLayout(a_row, 0, 2)

            card.setFixedHeight(44)
            return card, p_row, a_row, dot, title_label

        def _action_btn(text, tooltip=""):
            """动作按钮：统一 84x26、四字文案、不带图标（带图标就放不下了）"""
            btn = QPushButton(text)
            btn.setObjectName("PerfCardAction")
            btn.setFixedSize(self._CARD_BTN_W, self._CARD_BTN_H)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            if tooltip:
                btn.setToolTip(tooltip)
            return btn

        def _interval_combo(items, current, width=88):
            combo = QComboBox()
            combo.addItems(items)
            combo.setCurrentText(current)
            combo.setFixedWidth(width)
            combo.setEnabled(False)
            return combo

        # ---- 堆转储 ----
        (self._hprof_card, hprof_params, hprof_actions,
         self._hprof_dot, self._hprof_title) = _card("#e67e22", "堆转储")
        self.hprof_enable_check = BorderedCheckBox("循环间隔")
        self.hprof_enable_check.setToolTip(
            "开启后，点击「开始监控」即按右侧间隔自动对所选应用执行堆转储\n"
            "（第一次转储在一个完整间隔后执行）")
        self.hprof_enable_check.toggled.connect(self._on_hprof_toggle)
        hprof_params.addWidget(self.hprof_enable_check)
        self.hprof_interval_combo = _interval_combo(
            ["1 分钟", "5 分钟", "10 分钟", "30 分钟", "60 分钟", "120 分钟"], "5 分钟")
        self.hprof_interval_combo.currentTextChanged.connect(
            lambda _: self.hprof_interval_changed.emit())
        hprof_params.addWidget(self.hprof_interval_combo)
        hprof_params.addStretch()

        self.hprof_once_btn = _action_btn("立即转储")
        self.hprof_once_btn.clicked.connect(self.hprof_once_requested.emit)
        hprof_actions.addWidget(self.hprof_once_btn)

        # ---- 内存明细（smaps）----
        (self._smaps_card, smaps_params, smaps_actions,
         self._smaps_dot, self._smaps_title) = _card("#16a085", "内存明细")
        self._smaps_card.setToolTip(
            "抓取所选进程的 /proc/<pid>/smaps 到本地\n"
            "⚠ 需要设备 root，或应用是 debug（可调试）包")
        self.smaps_enable_check = BorderedCheckBox("循环间隔")
        self.smaps_enable_check.setToolTip(
            "开启后，点击「开始监控」即按右侧间隔自动抓取所选进程的 smaps\n"
            "（第一次抓取在一个完整间隔后执行）")
        self.smaps_enable_check.setEnabled(False)
        self.smaps_enable_check.toggled.connect(
            lambda _: self.smaps_interval_combo.setEnabled(
                self._state == self.STATE_IDLE and self.smaps_enable_check.isChecked()))
        smaps_params.addWidget(self.smaps_enable_check)
        self.smaps_interval_combo = _interval_combo(
            ["1 分钟", "2 分钟", "5 分钟", "10 分钟", "30 分钟"], "5 分钟")
        self.smaps_interval_combo.currentTextChanged.connect(
            lambda _: self.smaps_interval_changed.emit())
        smaps_params.addWidget(self.smaps_interval_combo)
        smaps_params.addStretch()

        self.smaps_once_btn = _action_btn("立即抓取")
        self.smaps_once_btn.clicked.connect(self.smaps_once_requested.emit)
        smaps_actions.addWidget(self.smaps_once_btn)

        # ---- 网络抓包 ----
        (self._packet_card, packet_params, packet_actions,
         self._packet_dot, self._packet_title) = _card("#9b59b6", "网络抓包")
        self._packet_available = True      # 抓包能力探测结果（未探测时按可用处理）
        self.packet_status_label = QLabel("未开始")
        self.packet_status_label.setStyleSheet(
            "font-size: 12px; color: #999999; background: transparent;")
        packet_params.addWidget(self.packet_status_label)
        packet_params.addStretch()

        # 参数入口：过滤表达式 / 网卡 / 单包长度 / 上限（此前这些全是写死的）
        # 纯图标按钮：单列 objectName，避免被「通用按钮 QSS」套上 padding/min-height
        # （那会把 28x26 撑成 30 高，比同排按钮低 2px 且底边被卡片裁掉）
        self.packet_config_btn = QPushButton()
        self.packet_config_btn.setObjectName("PerfIconBtn")
        self.packet_config_btn.setIcon(qta.icon('fa6s.sliders', color='#8a8f98'))
        self.packet_config_btn.setToolTip("抓包参数：过滤表达式 / 网卡 / 单包长度 / 上限")
        self.packet_config_btn.setFlat(True)
        self.packet_config_btn.clicked.connect(self.packet_config_requested.emit)
        packet_actions.addWidget(self.packet_config_btn)

        self.packet_toggle_btn = _action_btn("开始抓包")
        self.packet_toggle_btn.setCheckable(True)
        self.packet_toggle_btn.toggled.connect(self.packet_toggle_requested.emit)
        packet_actions.addWidget(self.packet_toggle_btn)
        self.refresh_packet_tooltip()

        # ---- 截图 ----
        (self._screenshot_card, screenshot_params, screenshot_actions,
         self._screenshot_dot, self._screenshot_title) = _card("#16a085", "截图")
        self.screenshot_enable_check = BorderedCheckBox("循环间隔")
        self.screenshot_enable_check.setToolTip(
            "开启后，点击「开始监控」即按右侧间隔自动截取设备屏幕\n"
            "（第一次截图在一个完整间隔后执行）")
        self.screenshot_enable_check.toggled.connect(self._on_screenshot_toggle)
        screenshot_params.addWidget(self.screenshot_enable_check)
        self.screenshot_interval_combo = _interval_combo(
            ["30 秒", "1 分钟", "5 分钟", "10 分钟", "30 分钟", "60 分钟"], "5 分钟")
        self.screenshot_interval_combo.currentTextChanged.connect(
            lambda _: self.screenshot_interval_changed.emit())
        screenshot_params.addWidget(self.screenshot_interval_combo)
        self.screenshot_status_label = QLabel("")
        self.screenshot_status_label.setStyleSheet(
            "font-size: 12px; color: #999999; background: transparent;")
        screenshot_params.addWidget(self.screenshot_status_label)
        screenshot_params.addStretch()

        self.screenshot_once_btn = _action_btn("立即截图")
        self.screenshot_once_btn.clicked.connect(self.screenshot_once_requested.emit)
        screenshot_actions.addWidget(self.screenshot_once_btn)

        # ---- 录屏 ----
        (self._record_card, record_params, record_actions,
         self._record_dot, self._record_title) = _card("#e84393", "录屏")
        self.record_enable_check = BorderedCheckBox("循环间隔")
        self.record_enable_check.setToolTip(
            "开启后，点击「开始监控」即按间隔自动录一段（单段时长见右侧）\n"
            "间隔必须大于单段时长，否则会重叠")
        self.record_enable_check.setEnabled(False)
        self.record_enable_check.toggled.connect(self._on_record_toggle)
        record_params.addWidget(self.record_enable_check)
        # 录屏这张卡参数最多（循环 + 间隔 + 录 + 时长 + 提示），两个下拉各窄一点
        self.record_interval_combo = _interval_combo(
            ["1 分钟", "2 分钟", "5 分钟", "10 分钟", "30 分钟"], "10 分钟", width=84)
        self.record_interval_combo.currentTextChanged.connect(self._on_record_cfg_changed)
        self.record_interval_combo.currentTextChanged.connect(
            lambda _: self.record_interval_changed.emit())
        record_params.addWidget(self.record_interval_combo)
        # 时长前面加个「录」字：和间隔一眼区分开，又不动下拉里的数据文案
        record_params.addWidget(QLabel("录制时长"))
        self.record_duration_combo = _interval_combo(
            ["15 秒", "30 秒", "60 秒", "120 秒"], "30 秒", width=76)
        self.record_duration_combo.currentTextChanged.connect(self._on_record_cfg_changed)
        self.record_duration_combo.currentTextChanged.connect(
            lambda _: self.record_duration_changed.emit())
        record_params.addWidget(self.record_duration_combo)
        record_params.addStretch()

        self.record_once_btn = _action_btn("立即录制")
        self.record_once_btn.clicked.connect(self.record_once_requested.emit)
        record_actions.addWidget(self.record_once_btn)

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
        """更新截图卡片状态文字（只有失败才写，传空串表示不显示）。

        成功的截图文件名不再写进卡片（2026-10-29 用户要求）：卡片上那串
        `screenshot_2026...png` 对用户没意义，还白占一截位置。留痕统一看
        「事件 / 虫师日志」。失败仍在这里提示，同时还有 toast。
        """
        self.screenshot_status_label.setText(text)
        self.screenshot_status_label.setStyleSheet(
            f"font-size: 12px; color: {'#e74c3c' if error else '#999999'}; "
            f"background: transparent;")

    @staticmethod
    def _set_dot_alert(dot, alert: bool):
        """把卡片圆点临时切成橙色表示「这项当前不可用」，恢复正常时回到原色。

        颜色统一写在动态属性 dotColor 上 —— apply_theme 换肤时读的也是这份属性，
        所以换主题不会把「不可用」的橙色冲掉。
        """
        if dot is None:
            return
        if not dot.property("dotBaseColor"):
            dot.setProperty("dotBaseColor", dot.property("dotColor"))
        base = dot.property("dotBaseColor") or dot.property("dotColor")
        color = "#e67e22" if alert else base
        dot.setProperty("dotColor", color)
        dot.setStyleSheet(
            f"color: {color}; font-size: 14px; background: transparent;")

    def set_packet_available(self, ok: bool, reason: str = ""):
        """抓包能力前置探测结果：不能抓就把「开始抓包」置灰。

        卡片上只留最短的状态（未 root / 不可用），完整原因进 tooltip ——
        长句直接把 44px 高的单行卡片撑乱（2026-10-29 按「扩展方向」第 9 项收敛）。
        这样未 root 的设备一进页面就知道抓不了，而不是点下去才弹错误。
        """
        self._packet_available = bool(ok)
        if ok:
            self.packet_toggle_btn.setEnabled(True)
            self.packet_toggle_btn.setToolTip("")
            self._set_dot_alert(self._packet_dot, False)
            if not self.packet_toggle_btn.isChecked():
                self.packet_status_label.setText("未开始")
                self.packet_status_label.setToolTip("")
                self.packet_status_label.setStyleSheet(
                    "font-size: 12px; color: #999999; background: transparent;")
        else:
            self.packet_toggle_btn.setEnabled(False)
            self.packet_toggle_btn.setToolTip(f"无法抓包：{reason}")
            self._set_dot_alert(self._packet_dot, True)
            short = "未 root" if "root" in (reason or "").lower() else "不可用"
            self.packet_status_label.setText(short)
            self.packet_status_label.setToolTip(f"无法抓包：{reason}")
            self.packet_status_label.setStyleSheet(
                "font-size: 12px; color: #e67e22; background: transparent;")

    def packet_available(self) -> bool:
        return bool(getattr(self, "_packet_available", True))

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
        """按当前宽度决定两列还是一列，并按勾选状态决定哪些卡片显示。

        2026-10-29 按「扩展方向」第 9 项重排：
        * 两列等宽（setColumnStretch 1:1）—— 以前两列各自按内容撑，永远不等宽
        * 分「整机指标 / 现场留证」两组，各带一行小标题，扫一眼知道哪块是什么
        * 视口 < 1060 降为单列（再窄两列就挤不下 84px 的按钮了）
        """
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
        two_cols = width >= 1060

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
            'screenshot': self.screenshot_visible_check,
            'record': self.record_visible_check}
        tool_cards = {'hprof': self._hprof_card, 'smaps': self._smaps_card,
                      'packet': self._packet_card,
                      'screenshot': self._screenshot_card,
                      'record': self._record_card}
        shown_tools = [tool_cards[k]
                       for k in ('hprof', 'smaps', 'packet', 'screenshot', 'record')
                       if tool_checks[k].isChecked()]

        # 整机探针卡（系统内存 / 网卡流量 / 磁盘 IO）自成一「整机指标」组
        probe_checks = (
            ('sys_mem', getattr(self, 'sys_mem_check', None)),
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
        for tc in tool_cards.values():
            tc.setParent(self.cards_container)
            tc.setVisible(tc in shown_tools)

        cols = 2 if two_cols else 1
        # 单列时第二列必须归零，否则它仍按 1:1 占掉一半宽度，卡片只铺半屏
        self.cards_grid.setColumnStretch(0, 1)
        self.cards_grid.setColumnStretch(1, 1 if two_cols else 0)
        # 指标卡片（图表）排最前
        row = 0
        for i, key in enumerate(visible_metrics):
            self.cards_grid.addWidget(self._cards[key], i // cols, i % cols)
        row = (len(visible_metrics) + cols - 1) // cols

        # 整机探针卡、工具卡依次跟在指标卡片后面（不再插入分组小标题：
        # 2026-10-29 用户反馈那两行字多余，占高度又割裂视觉）
        for cards in (probe_cards, shown_tools):
            if not cards:
                continue
            for i, card in enumerate(cards):
                self.cards_grid.addWidget(card, row + i // cols, i % cols)
            row += (len(cards) + cols - 1) // cols

        used_rows = row

        # 让多余空间落到最末行下方，而不是分散到卡片之间
        for r in range(0, 30):
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
            # 目标进程列表：主进程在第一个；只选了一个进程时就是它自己
            'processes': self._target_processes(),
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
        elif self._state == self.STATE_STOPPING:
            return                      # 收尾中，忽略连点
        else:
            # 先切「停止中」再发信号：控制器是同步收尾的（等 worker、收探针、
            # 落盘），等它返回再切就等于没切
            self._enter_stopping()
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
            'sys_mem_interval': self._probe_interval_text('sys_mem'),
            'show_hprof': self.hprof_visible_check.isChecked(),
            'show_record': self.record_visible_check.isChecked(),
            'record_loop': self.record_enable_check.isChecked(),
            'record_interval': self.record_interval_combo.currentText(),
            'record_duration': self.record_duration_combo.currentText(),
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
            # 多进程目标（主进程不含在内，启动时由 package + 这个列表拼成）
            'processes': [p for p in self._multi_processes if p != pkg],
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
        for key in ('sys_mem', 'netdev', 'iotop'):
            combo = self._probe_interval_combo(key)
            text = cfg.get(f'{key}_interval')
            if combo is not None and text and combo.findText(text) >= 0:
                combo.setCurrentText(text)

        # 卡片显隐
        if cfg.get('show_record'):
            self.record_visible_check.setChecked(True)
        if cfg.get('record_loop'):
            self.record_enable_check.setChecked(True)
        _pick(self.record_interval_combo, cfg.get('record_interval'))
        _pick(self.record_duration_combo, cfg.get('record_duration'))
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
        # 多进程目标：主进程由上面的 package 决定，这里只恢复附带的子进程
        extra = [p for p in (cfg.get('processes') or []) if isinstance(p, str) and p]
        if extra:
            self._multi_processes = ([pkg] if pkg else []) + extra
            self._update_multi_btn()

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
            self.STATE_STOPPING: "停止中",
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
        # 「停止中」：仍在进行中的状态，用橙色 + 白字表达「还在收尾」；
        # 按钮此时是禁用的，所以 :disabled 也必须是橙色 —— 否则会被涂成灰色，
        # 看着像"停好了但点不动"，与「进行中」的语义正好相反。
        stopping_qss = f"""
            QPushButton {{
                background-color: {'#b9770e' if is_dark else '#e08e0b'};
                color: #ffffff;
                border: none;
                border-radius: 4px;
                padding: 4px 14px;
                font-weight: 500;
                min-height: 22px;
            }}
            QPushButton:disabled {{
                background-color: {'#8a5a0a' if is_dark else '#efa33a'};
                color: #ffffff;
            }}
        """
        # 记住三套外观，供运行按钮按状态切换
        self._run_btn_qss_normal = btn_qss
        self._run_btn_qss_danger = danger_qss
        self._run_btn_qss_stopping = stopping_qss

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

        # 卡片动作按钮（立即转储 / 开始抓包…）：固定 84x26，字号小一号、
        # 左右 padding 收到 6 —— 通用 QSS 的 4px/14px 会把四字文案挤出去
        card_action_qss = f"""
            QPushButton#PerfCardAction {{
                background-color: {primary_bg};
                color: {primary_fg};
                border: none;
                border-radius: 4px;
                padding: 0px;
                font-size: 12px;
                font-weight: 500;
            }}
            QPushButton#PerfCardAction:hover {{
                background-color: {'#1565c0' if not is_dark else '#64b5f6'};
            }}
            QPushButton#PerfCardAction:pressed {{
                background-color: {'#0d47a1' if not is_dark else '#42a5f5'};
            }}
            QPushButton#PerfCardAction:disabled {{
                background-color: {disabled_bg};
                color: {disabled_fg};
            }}
        """

        for btn in self.findChildren(QPushButton):
            name = btn.objectName()
            if name == "PerfIconBtn":
                btn.setStyleSheet(icon_btn_qss)
            elif name == "PerfCardAction":
                btn.setStyleSheet(card_action_qss)
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
        # 上限 92：不加限制时「导出 CSV」的自然宽（99）成了整排宽度，
        # 底部栏最小宽跟着涨到 879；92 下四字按钮仍完整，小屏才放得下
        uniform_w = min(max(b.sizeHint().width() for b in bottom_btns), 88)
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
            (self._record_card, self._record_title, self._record_dot),
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