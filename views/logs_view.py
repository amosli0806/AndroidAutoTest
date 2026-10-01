# views/logs_view.py
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit, QPushButton,
    QComboBox, QLineEdit
)
from PyQt6.QtCore import Qt, QRectF, QThread, pyqtSignal
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QFont
from models.execution_model import ExecutionModel
from utils.theme import ThemeMode
from utils.settings import Settings
from utils import log_colors
from utils.toast import show_toast


class AIAnalyzeWorker(QThread):
    """后台跑 AI 失败分析，避免阻塞 UI 线程"""
    one_ready = pyqtSignal(int, object)   # (failure_index, FailureSuggestion)
    all_done = pyqtSignal(int, int)       # (成功数, 总数)

    def __init__(self, contexts, parent=None):
        super().__init__(parent)
        self.contexts = contexts

    def run(self):
        from services.ai_service import AIService
        service = AIService()
        ok = 0
        for i, ctx in enumerate(self.contexts):
            if ctx.ai_suggestion is not None:
                # 已经分析过，不重复请求
                ok += 1
                continue
            try:
                suggestion = service.analyze_failure(
                    step={
                        "type": ctx.step_type,
                        "name": ctx.step_name,
                        "params": ctx.step_params,
                    },
                    prev_steps=ctx.prev_steps,
                    error_msg=ctx.error_msg,
                    screenshot_path=ctx.screenshot_path,
                )
            except Exception:
                suggestion = None
            if suggestion is not None:
                ctx.ai_suggestion = suggestion
                ok += 1
                self.one_ready.emit(i, suggestion)
        self.all_done.emit(ok, len(self.contexts))


class LogsView(QWidget):
    # AI 失败分析收尾：(成功归因数, 失败总数)。
    # 视图只负责报事件，不直接碰消息中心 —— 由 main.py 接到 NotificationController
    ai_analysis_done = pyqtSignal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("LogsView")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.model = None
        self._ai_worker = None
        self._dark = False
        self.setup_ui()

    def apply_theme(self, theme_mode: ThemeMode):
        # 日志行内颜色优先于控件配色，这里同步刷新日志配色表，
        # 否则切到夜间模式后日志文字仍是深色
        self._dark = theme_mode == ThemeMode.DARK
        log_colors.refresh(self._dark)
        # 已输出的日志颜色写死在 HTML 里，需要按新主题整体重渲染一遍
        if self.model is not None and getattr(self.model, "logs", None):
            self._on_logs_changed()
        # 甜甜圈中心的 0% / 100% 文案颜色跟随主题
        if theme_mode == ThemeMode.DARK:
            self.donut.set_text_color("#eeeeee")
        else:
            self.donut.set_text_color("#323232")
        # AI 按钮配色（是否高亮取决于 AI 是否启用）
        self._apply_ai_style(theme_mode == ThemeMode.DARK)

    def _apply_ai_style(self, dark: bool):
        """AI 按钮常驻样式：已启用 AI → 紫色高亮；未启用 → 置灰。"""
        if Settings.is_ai_ready():
            disabled = "#555" if dark else "#b0b0b0"
            disabled_text = "#999" if dark else "#e0e0e0"
            self.ai_btn.setStyleSheet(f"""
                QPushButton#aiAnalyzeBtn {{
                    background-color: #7e57c2;
                    color: #ffffff;
                    border: none;
                    border-radius: 4px;
                    font-weight: 500;
                    padding: 0 14px;
                }}
                QPushButton#aiAnalyzeBtn:hover {{ background-color: #9575cd; }}
                QPushButton#aiAnalyzeBtn:disabled {{ background-color: {disabled}; color: {disabled_text}; }}
            """)
            self.ai_btn.setToolTip("让 AI 结合步骤、日志、截图给出失败归因")
        else:
            bg, hover, text = ("#3a3a3a", "#454545", "#8a8a8a") if dark \
                else ("#e4e4e4", "#dadada", "#909090")
            self.ai_btn.setStyleSheet(f"""
                QPushButton#aiAnalyzeBtn {{
                    background-color: {bg};
                    color: {text};
                    border: none;
                    border-radius: 4px;
                    font-weight: 500;
                    padding: 0 14px;
                }}
                QPushButton#aiAnalyzeBtn:hover {{ background-color: {hover}; }}
            """)
            self.ai_btn.setToolTip("在 设置 → AI 辅助 中启用后可用")

    def setup_ui(self):
        layout = QVBoxLayout(self)
        # 留出边距让圆角边框可见
        layout.setContentsMargins(8, 8, 8, 8)

        stats_layout = QHBoxLayout()
        stats_layout.addStretch()
        self.donut = DonutWidget()
        stats_layout.addWidget(self.donut)

        legend_layout = QVBoxLayout()
        self.pass_label = QLabel("通过: 0")
        self.fail_label = QLabel("失败: 0")
        self.rate_label = QLabel("通过率: 0%")

        legend_layout.addWidget(self.pass_label)
        legend_layout.addWidget(self.fail_label)
        legend_layout.addWidget(self.rate_label)
        stats_layout.addLayout(legend_layout)
        stats_layout.addStretch()
        layout.addLayout(stats_layout)

        # AI 分析按钮 + 日志过滤/搜索：同一行，AI 按钮常驻
        ai_row = QHBoxLayout()
        self.level_combo = QComboBox()
        self.level_combo.addItems(["全部类型", "错误", "成功", "警告", "信息"])
        self.level_combo.setFixedHeight(28)
        self.level_combo.setToolTip("按日志类型快速筛选")
        self.level_combo.currentIndexChanged.connect(self._on_filter_changed)
        ai_row.addWidget(self.level_combo)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("🔍 搜索日志内容…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setFixedHeight(28)
        self.search_edit.setToolTip("按关键字过滤日志，命中内容高亮")
        self.search_edit.textChanged.connect(self._on_filter_changed)
        ai_row.addWidget(self.search_edit, 1)

        self.ai_btn = QPushButton("✨ AI 分析失败")
        self.ai_btn.setObjectName("aiAnalyzeBtn")
        self.ai_btn.setFixedHeight(28)
        self.ai_btn.setMinimumWidth(120)
        self.ai_btn.clicked.connect(self._on_ai_analyze)
        ai_row.addWidget(self.ai_btn)
        layout.addLayout(ai_row)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Consolas", 10))
        layout.addWidget(self.log_text)

    def set_model(self, model: ExecutionModel):
        self.model = model
        self.model.logs_changed = self._on_logs_changed
        self.update_stats()

    def _on_logs_changed(self):
        self._render_logs()
        self.update_stats()

    # ---------- 日志过滤与搜索 ----------
    # 类型下拉与枚举值的对应关系（index -> 日志 typ）
    _LEVEL_KEYS = ['all', 'error', 'success', 'warning', 'info']

    def _on_filter_changed(self, *_):
        self._render_logs()

    def _render_logs(self):
        """按「类型筛选 + 关键字搜索」重渲染日志区（数据始终来自 model.logs）。"""
        if self.model is None:
            return
        # 新一轮执行（日志被清空）后，旧的 AI 结论缓存一并作废
        if not self.model.logs:
            self._ai_blocks = []
        self.log_text.clear()
        level_of = {
            'error': log_colors.ERROR,
            'success': log_colors.SUCCESS,
            'warning': log_colors.WARNING,
        }
        sel = self._LEVEL_KEYS[self.level_combo.currentIndex()] \
            if hasattr(self, 'level_combo') else 'all'
        kw = self.search_edit.text().strip().lower() \
            if hasattr(self, 'search_edit') else ''

        for msg, typ in self.model.logs:
            if sel != 'all' and typ != sel:
                continue
            if kw and kw not in msg.lower():
                continue
            color = log_colors.log_color(level_of.get(typ, log_colors.INFO))
            if kw:
                # 命中关键字高亮：保留原大小写、转义 HTML，避免日志里的 <>& 破坏标记
                highlighted = self._highlight(msg, kw)
                self.log_text.append(
                    f'<font color="{color}">{highlighted}</font>')
            else:
                self.log_text.append(f'<font color="{color}">{msg}</font>')

        # 重渲染后补回已缓存的 AI 分析结论（原 __init__ 里没有初始化列表，此处惰性建）
        for i, s in getattr(self, '_ai_blocks', []):
            self._append_suggestion_block(i, s)

    @staticmethod
    def _highlight(msg: str, kw: str) -> str:
        """大小写不敏感地把 kw 包上高亮标记，保留原文本大小写，转义 HTML。"""
        import html
        out, low, klen, i = [], msg.lower(), len(kw), 0
        while i < len(msg):
            if low.startswith(kw, i):
                out.append(f'<span style="background-color:rgba(255,193,7,0.45);">'
                           f'{html.escape(msg[i:i + klen])}</span>')
                i += klen
            else:
                out.append(html.escape(msg[i]))
                i += 1
        return ''.join(out)

    def update_stats(self):
        if self.model:
            pass_count, fail_count, rate = self.model.get_stats()
            # 通过 - 绿色
            self.pass_label.setText(f"通过: {pass_count}")
            self.pass_label.setStyleSheet("font-weight: bold; font-size: 16px; color: #27ae60;")
            # 失败 - 红色
            self.fail_label.setText(f"失败: {fail_count}")
            self.fail_label.setStyleSheet("font-weight: bold; font-size: 16px; color: #e74c3c;")
            # 通过率 - 蓝色
            self.rate_label.setText(f"通过率: {rate}%")
            self.rate_label.setStyleSheet("font-weight: bold; font-size: 16px; color: #1976d2;")
            self.donut.set_stats(pass_count, fail_count)

    def add_log(self, message, log_type='info'):
        if self.model:
            self.model.add_log(message, log_type)
            self._on_logs_changed()

    # ---------- AI 分析 ----------
    def set_ai_available(self, available: bool):
        """执行结束后由控制器调用。按钮常驻，这里只刷新文案与状态：
        AI 已启用 → 高亮可用；未启用 → 置灰（样式在 _apply_ai_style）"""
        self.ai_btn.setVisible(True)
        self._apply_ai_style(self._dark)
        if available:
            self.ai_btn.setEnabled(True)
            self.ai_btn.setText("✨ AI 分析失败")

    def _on_ai_analyze(self):
        if not Settings.is_ai_ready():
            show_toast(self.window(), "请先在 设置 → AI 辅助 中启用 AI", duration=2500)
            return
        if self.model is None or not self.model.failure_contexts:
            show_toast(self.window(), "暂无失败用例可分析", duration=2500)
            return
        if self._ai_worker is not None and self._ai_worker.isRunning():
            return

        # 已经全部分析过就直接重渲染
        if all(ctx.ai_suggestion for ctx in self.model.failure_contexts):
            self._render_ai_suggestions()
            return

        self.ai_btn.setEnabled(False)
        self.ai_btn.setText("分析中…")

        self._ai_worker = AIAnalyzeWorker(self.model.failure_contexts, self)
        self._ai_worker.one_ready.connect(self._on_one_suggestion)
        self._ai_worker.all_done.connect(self._on_analysis_done)
        self._ai_worker.start()

    def _on_one_suggestion(self, index, suggestion):
        # 缓存起来，切换筛选/搜索导致重渲染后仍能补回
        if not hasattr(self, '_ai_blocks'):
            self._ai_blocks = []
        if all(i != index for i, _ in self._ai_blocks):
            self._ai_blocks.append((index, suggestion))
        # 单条到达就可以先追加展示，让用户尽早看到
        self.log_text.append("")
        self._append_suggestion_block(index, suggestion)

    def _on_analysis_done(self, ok, total):
        self.ai_btn.setEnabled(True)
        if ok == 0:
            self.ai_btn.setText("✨ AI 分析失败")
            self.log_text.append(
                f'<span style="color:{log_colors.log_color(log_colors.ERROR)};">'
                f'AI 分析未返回结果，请检查 API Key / 网络 / 模型名</span>'
            )
        else:
            self.ai_btn.setText("✨ 重新分析")
        # 消息中心据此留痕（重复点击走 _render_ai_suggestions，不会发这个信号）
        self.ai_analysis_done.emit(ok, total)

    def _render_ai_suggestions(self):
        """对已缓存的 suggestion 统一渲染（用于重复点击、切主题等场景）"""
        self._ai_blocks = []
        for i, ctx in enumerate(self.model.failure_contexts):
            if ctx.ai_suggestion:
                self._ai_blocks.append((i, ctx.ai_suggestion))
                self._append_suggestion_block(i, ctx.ai_suggestion)

    def _append_suggestion_block(self, index, suggestion):
        """把一条 FailureSuggestion 渲染到日志区"""
        ctx = self.model.failure_contexts[index]
        title = log_colors.log_color(log_colors.RESULT)
        info = log_colors.log_color(log_colors.INFO)
        warn = log_colors.log_color(log_colors.WARNING)

        self.log_text.append(
            f'<span style="color:{title};font-weight:bold;">'
            f'✨ [AI 分析] 用例「{ctx.case_name}」步骤 {ctx.step_index} · {ctx.step_name}'
            f'</span>'
        )
        self.log_text.append(f'<span style="color:{info};">  结论：{suggestion.summary}</span>')

        if suggestion.cause:
            self.log_text.append(f'<span style="color:{info};">  可能原因：</span>')
            for line in suggestion.cause.splitlines():
                line = line.strip()
                if line:
                    self.log_text.append(f'<span style="color:{info};">    {line}</span>')

        if suggestion.actions:
            self.log_text.append(f'<span style="color:{warn};">  建议动作：</span>')
            for act in suggestion.actions:
                if act.type == "none":
                    continue
                self.log_text.append(
                    f'<span style="color:{warn};">    · {act.label}</span>'
                )

        self.log_text.append("")


class DonutWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.pass_count = 0
        self.fail_count = 0
        self._text_color = QColor(50, 50, 50)  # 默认亮色
        self.setFixedSize(160, 160)

    def set_text_color(self, color):
        self._text_color = QColor(color)
        self.update()

    def set_stats(self, pass_count, fail_count):
        self.pass_count = pass_count
        self.fail_count = fail_count
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(20, 20, 120, 120)
        total = self.pass_count + self.fail_count

        # 灰色背景圆环
        painter.setPen(QPen(QColor(220, 220, 220), 16))
        painter.drawEllipse(rect)

        if total > 0:
            # 通过 (绿色)
            start_angle = -90 * 16
            pass_angle = int(self.pass_count / total * 360 * 16)
            painter.setPen(QPen(QColor(39, 174, 96), 16))
            painter.drawArc(rect, start_angle, pass_angle)

            # 失败 (红色)
            fail_angle = int(self.fail_count / total * 360 * 16)
            painter.setPen(QPen(QColor(231, 76, 60), 16))
            painter.drawArc(rect, start_angle + pass_angle, fail_angle)

            rate = round(self.pass_count / total * 100)
            text = f"{rate}%"
        else:
            text = "0%"

        # 中心文字颜色跟随主题
        painter.setPen(QPen(self._text_color))
        painter.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)