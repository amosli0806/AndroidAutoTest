# views/logs_view.py
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit, QPushButton
)
from PyQt6.QtCore import Qt, QRectF, QThread, pyqtSignal
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QFont
from models.execution_model import ExecutionModel
from utils.theme import ThemeMode
from utils.settings import Settings
from utils import log_colors


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
        self.setup_ui()

    def apply_theme(self, theme_mode: ThemeMode):
        # 日志行内颜色优先于控件配色，这里同步刷新日志配色表，
        # 否则切到夜间模式后日志文字仍是深色
        log_colors.refresh(theme_mode == ThemeMode.DARK)
        # 已输出的日志颜色写死在 HTML 里，需要按新主题整体重渲染一遍
        if self.model is not None and getattr(self.model, "logs", None):
            self._on_logs_changed()
        # 甜甜圈中心的 0% / 100% 文案颜色跟随主题
        if theme_mode == ThemeMode.DARK:
            self.donut.set_text_color("#eeeeee")
        else:
            self.donut.set_text_color("#323232")
        # AI 按钮配色
        if theme_mode == ThemeMode.DARK:
            self.ai_btn.setStyleSheet("""
                QPushButton#aiAnalyzeBtn {
                    background-color: #7e57c2;
                    color: #ffffff;
                    border: none;
                    border-radius: 4px;
                    font-weight: 500;
                    padding: 0 14px;
                }
                QPushButton#aiAnalyzeBtn:hover { background-color: #9575cd; }
                QPushButton#aiAnalyzeBtn:disabled { background-color: #555; color: #999; }
            """)
        else:
            self.ai_btn.setStyleSheet("""
                QPushButton#aiAnalyzeBtn {
                    background-color: #7e57c2;
                    color: #ffffff;
                    border: none;
                    border-radius: 4px;
                    font-weight: 500;
                    padding: 0 14px;
                }
                QPushButton#aiAnalyzeBtn:hover { background-color: #9575cd; }
                QPushButton#aiAnalyzeBtn:disabled { background-color: #b0b0b0; color: #e0e0e0; }
            """)

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

        # AI 分析按钮行：只有配置了 AI 且本次执行有失败时才显示
        ai_row = QHBoxLayout()
        ai_row.addStretch()
        self.ai_btn = QPushButton("✨ AI 分析失败")
        self.ai_btn.setObjectName("aiAnalyzeBtn")
        self.ai_btn.setFixedHeight(28)
        self.ai_btn.setMinimumWidth(120)
        self.ai_btn.setToolTip("让 AI 结合步骤、日志、截图给出失败归因")
        self.ai_btn.clicked.connect(self._on_ai_analyze)
        self.ai_btn.setVisible(False)
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
        self.log_text.clear()
        # 颜色跟随主题：原先写死的 black/green 在深色日志区里几乎看不见
        level_of = {
            'error': log_colors.ERROR,
            'success': log_colors.SUCCESS,
            'warning': log_colors.WARNING,
        }
        for msg, typ in self.model.logs:
            color = log_colors.log_color(level_of.get(typ, log_colors.INFO))
            self.log_text.append(f'<font color="{color}">{msg}</font>')
        self.update_stats()

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
        """执行结束后由控制器调用：有失败上下文且 AI 已启用时，显示按钮"""
        show = bool(
            available
            and self.model is not None
            and self.model.failure_contexts
            and Settings.is_ai_ready()
        )
        self.ai_btn.setVisible(show)
        if show:
            self.ai_btn.setEnabled(True)
            self.ai_btn.setText("✨ AI 分析失败")

    def _on_ai_analyze(self):
        if self.model is None or not self.model.failure_contexts:
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
        for i, ctx in enumerate(self.model.failure_contexts):
            if ctx.ai_suggestion:
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