# views/logs_view.py
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit
from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QFont
from models.execution_model import ExecutionModel
from utils.theme import ThemeMode


class LogsView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("LogsView")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.model = None
        self.setup_ui()

    def apply_theme(self, theme_mode: ThemeMode):
        # 甜甜圈中心的 0% / 100% 文案颜色跟随主题
        if theme_mode == ThemeMode.DARK:
            self.donut.set_text_color("#eeeeee")
        else:
            self.donut.set_text_color("#323232")

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
        for msg, typ in self.model.logs:
            color = "black"
            if typ == 'error':
                color = "red"
            elif typ == 'success':
                color = "green"
            elif typ == 'warning':
                color = "orange"
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