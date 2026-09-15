# views/perf_widgets.py
"""性能检测的自定义组件"""
import pyqtgraph as pg
from PyQt6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel,
    QSizePolicy, QGraphicsDropShadowEffect, QWidget
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor

from utils.theme import ThemeMode

class TimeAxisItem(pg.AxisItem):
    """横轴：把 Unix 时间戳格式化为相对起始时间的秒数（或 m s）"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._t0 = None

    def update_t0(self, x_list):
        if x_list and self._t0 is None:
            self._t0 = x_list[0]

    def reset_t0(self):
        self._t0 = None

    def tickStrings(self, values, scale, spacing):
        if self._t0 is None:
            return ['' for _ in values]
        out = []
        for v in values:
            dt = max(0.0, v - self._t0)
            if dt < 60:
                out.append(f"{dt:.0f}s")
            else:
                m = int(dt // 60)
                s = int(dt % 60)
                out.append(f"{m}m{s}s")
        return out

class MetricCard(QFrame):
    """单个指标卡片：标题 + 实时曲线 + 统计栏"""

    def __init__(self, key, title, unit, color,
                 y_min=0, y_max=100,
                 series=None, parent=None):
        """
        key: 指标键（'cpu' / 'mem' / 'fps' / 'traffic'）
        title: 卡片标题，如"CPU 使用率"
        unit: 单位（'%' / 'MB' / ''）
        color: 曲线主色
        series: [{'name': 'RX', 'color': '#xxx'}, ...]，支持多条曲线；None 时单条
        """
        super().__init__(parent)
        self.setObjectName(f"MetricCard_{key}")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._key = key
        self._title = title
        self._unit = unit
        self._color = color
        self._y_min = y_min
        self._y_max = y_max
        self._theme_mode = ThemeMode.LIGHT
        self._alert = False

        # 序列定义
        if series is None:
            self._series = [{'name': 'value', 'color': color}]
        else:
            self._series = series
        self._curves = {}

        self.setMinimumSize(400, 240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(240)

        self._setup_ui()
        # 初始占位：显示 0，避免空着
        self._reset_stats_text()

    # ------------------------------------------------------------------
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        # 标题行
        title_row = QHBoxLayout()
        title_row.setSpacing(8)

        self.dot = QLabel("●")
        self.dot.setStyleSheet(f"color: {self._color}; font-size: 14px; background: transparent;")
        title_row.addWidget(self.dot)

        self.title_label = QLabel(self._title)
        self.title_label.setStyleSheet(
            "font-weight: 600; font-size: 13px; background: transparent;"
        )
        title_row.addWidget(self.title_label)
        title_row.addStretch()

        self.alert_icon = QLabel("⚠")
        self.alert_icon.setStyleSheet("color: #e74c3c; font-size: 14px; background: transparent;")
        self.alert_icon.setVisible(False)
        title_row.addWidget(self.alert_icon)

        layout.addLayout(title_row)

        # 图表
        pg.setConfigOptions(antialias=True)
        self._time_axis = TimeAxisItem(orientation='bottom')
        self.plot = pg.PlotWidget(axisItems={'bottom': self._time_axis})
        self.plot.setMinimumHeight(150)
        self.plot.setMenuEnabled(False)
        self.plot.setMouseEnabled(x=False, y=False)
        self.plot.hideButtons()
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.setYRange(self._y_min, self._y_max)

        for s in self._series:
            curve = self.plot.plot(
                pen=pg.mkPen(color=s['color'], width=2),
                name=s['name']
            )
            self._curves[s['name']] = {
                'curve': curve,
                'x': [],
                'y': [],
                'color': s['color'],
            }

        layout.addWidget(self.plot, 1)

        # 统计栏
        self.stats_row = QHBoxLayout()
        self.stats_row.setSpacing(16)
        self.stats_labels = {}
        for key in ('current', 'peak', 'avg', 'extra'):
            lbl = QLabel("")
            lbl.setStyleSheet(
                "font-size: 12px; color: #999999; background: transparent;"
            )
            self.stats_labels[key] = lbl
            self.stats_row.addWidget(lbl)
        self.stats_row.addStretch()
        layout.addLayout(self.stats_row)

    # ------------------------------------------------------------------
    def append_data(self, timestamp, values: dict):
        """
        values: {'value': 12.5} 或 {'RX': 123, 'TX': 456}
        只保留最近 300 个采样点
        """
        for name, val in values.items():
            if name not in self._curves:
                continue
            data = self._curves[name]
            data['x'].append(timestamp)
            data['y'].append(val)
            # 保留最近 300 点
            if len(data['x']) > 300:
                data['x'] = data['x'][-300:]
                data['y'] = data['y'][-300:]
            data['curve'].setData(data['x'], data['y'])

        # 记录起始时间戳，让横轴以相对秒数显示
        if self._curves:
            first_data = next(iter(self._curves.values()))
            self._time_axis.update_t0(first_data['x'])

        # 更新 Y 轴范围（自适应）
        self._auto_scale()

    def _auto_scale(self):
        all_y = []
        for data in self._curves.values():
            all_y.extend(data['y'])
        if not all_y:
            return
        cur_max = max(all_y)
        cur_min = min(all_y)
        # 上限扩大 20%
        top = max(self._y_max, cur_max * 1.2)
        # 留一点头部空间
        self.plot.setYRange(self._y_min, top, padding=0.05)

    # ------------------------------------------------------------------
    def set_stats(self, current_text=None, peak_text=None,
                  avg_text=None, extra_text=None):
        """设置统计栏文字"""
        if current_text is not None:
            self.stats_labels['current'].setText(current_text)
        if peak_text is not None:
            self.stats_labels['peak'].setText(peak_text)
        if avg_text is not None:
            self.stats_labels['avg'].setText(avg_text)
        if extra_text is not None:
            self.stats_labels['extra'].setText(extra_text)

    def _reset_stats_text(self):
        """重置底部统计标签为 0 占位。按卡片类型展示对应单位。"""
        if self._key == 'cpu':
            self.set_stats(
                current_text="当前 0.0%", peak_text="峰值 0.0%",
                avg_text="均值 0.0%", extra_text="抖动 0.0%",
            )
        elif self._key == 'mem':
            self.set_stats(
                current_text="当前 0MB", peak_text="峰值 0MB",
                avg_text="均值 0MB", extra_text="",
            )
        elif self._key == 'fps':
            self.set_stats(
                current_text="当前 0", peak_text="峰值 0",
                avg_text="均值 0", extra_text="",
            )
        elif self._key == 'traffic':
            self.set_stats(
                current_text="接收 0.0MB", peak_text="发送 0.0MB",
                avg_text="总共 0.0MB", extra_text="",
            )
        else:
            self.set_stats(
                current_text="当前 0", peak_text="峰值 0",
                avg_text="均值 0", extra_text="",
            )

    def set_alert(self, alert: bool):
        """设置告警状态：曲线变红 + 显示 ⚠ + peak 标签染红"""
        if self._alert == alert:
            return
        self._alert = alert
        self.alert_icon.setVisible(alert)
        for data in self._curves.values():
            data['curve'].setPen(pg.mkPen(
                color='#e74c3c' if alert else data['color'], width=2
            ))
        peak = self.stats_labels.get('peak')
        if peak is not None:
            peak.setStyleSheet(
                f"font-size: 12px; color: {'#e74c3c' if alert else '#999999'};"
                " background: transparent;"
            )

    def clear_data(self):
        for data in self._curves.values():
            data['x'].clear()
            data['y'].clear()
            data['curve'].setData([], [])
        self._alert = False
        self.alert_icon.setVisible(False)
        # 重置横轴起点
        if self._time_axis is not None:
            self._time_axis.reset_t0()
        # 底部统计标签归零
        self._reset_stats_text()

    # ------------------------------------------------------------------
    def apply_theme(self, theme_mode: ThemeMode, has_wallpaper: bool = False):
        self._theme_mode = theme_mode
        if theme_mode == ThemeMode.DARK:
            card_bg = "rgba(35, 36, 39, 0.85)" if has_wallpaper else "#232427"
            border = "#3a3a3a"
            title_color = "#ffffff"
            stat_color = "#999999"
            plot_bg = "#1c1d20"
            grid_color = "#3a3a3a"
        else:
            card_bg = "rgba(232, 234, 237, 0.85)" if has_wallpaper else "#e8eaed"
            border = "#d0d0d0"
            title_color = "#333333"
            stat_color = "#999999"
            plot_bg = "#fafbfc"
            grid_color = "#e8e8e8"

        self.setStyleSheet(f"""
            #{self.objectName()} {{
                background-color: {card_bg};
                border: 1px solid {border};
                border-radius: 8px;
            }}
        """)
        self.title_label.setStyleSheet(
            f"font-weight: 600; font-size: 13px; color: {title_color}; background: transparent;"
        )
        stat_colors = {
            'current': stat_color,
            'peak': '#e74c3c' if self._alert else stat_color,
            'avg': '#27ae60',
            'extra': stat_color,
        }
        for key, lbl in self.stats_labels.items():
            lbl.setStyleSheet(
                f"font-size: 12px; color: {stat_colors.get(key, stat_color)};"
                " background: transparent;"
            )

        self.plot.setBackground(plot_bg)
        for ax in ('left', 'bottom'):
            axis = self.plot.getAxis(ax)
            axis.setPen(pg.mkPen(color=grid_color))
            axis.setTextPen(pg.mkPen(color='#888888' if theme_mode == ThemeMode.LIGHT else '#aaaaaa'))

class StatCard(QFrame):
    """纯数字展示卡片（用于卡顿统计、启动耗时等）"""

    def __init__(self, title, items=None, parent=None):
        super().__init__(parent)
        self.setObjectName("StatCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._theme_mode = ThemeMode.LIGHT
        self._items = {}
        self.setMinimumWidth(400)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(150)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(12)

        # 标题行（带彩色圆点，与 MetricCard 视觉统一）
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        self.dot = QLabel("●")
        self.dot.setStyleSheet(
            "color: #e67e22; font-size: 14px; background: transparent;"
        )
        title_row.addWidget(self.dot)
        title_label = QLabel(title)
        title_label.setStyleSheet(
            "font-weight: 600; font-size: 13px; background: transparent;"
        )
        self._title_label = title_label
        title_row.addWidget(title_label)
        title_row.addStretch()
        layout.addLayout(title_row)

        # 指标横向行（每项等宽、居中）
        row = QHBoxLayout()
        row.setSpacing(8)
        for item in (items or []):
            col = QVBoxLayout()
            col.setSpacing(2)
            col.setAlignment(Qt.AlignmentFlag.AlignCenter)

            val_label = QLabel(str(item.get('value', '-')))
            val_label.setStyleSheet(
                "font-size: 26px; font-weight: bold; background: transparent;"
            )
            val_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

            key_label = QLabel(item.get('label', ''))
            key_label.setStyleSheet(
                "font-size: 11px; color: #999999; background: transparent;"
            )
            key_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

            col.addWidget(val_label)
            col.addWidget(key_label)

            cell = QWidget()
            cell.setLayout(col)
            self._items[item['key']] = val_label
            row.addWidget(cell, 1)   # stretch=1 让每项等宽

        layout.addLayout(row)

    def set_value(self, key: str, value):
        if key in self._items:
            self._items[key].setText(str(value))

    def reset(self):
        for lbl in self._items.values():
            lbl.setText("0")

    def apply_theme(self, theme_mode: ThemeMode, has_wallpaper: bool = False):
        self._theme_mode = theme_mode
        if theme_mode == ThemeMode.DARK:
            bg = "rgba(35, 36, 39, 0.85)" if has_wallpaper else "#232427"
            border = "#3a3a3a"
            text = "#ffffff"
        else:
            bg = "rgba(232, 234, 237, 0.85)" if has_wallpaper else "#e8eaed"
            border = "#d0d0d0"
            text = "#333333"
        self.setStyleSheet(f"""
            #StatCard {{
                background-color: {bg};
                border: 1px solid {border};
                border-radius: 8px;
            }}
            #StatCard QLabel {{
                color: {text};
                background: transparent;
            }}
        """)