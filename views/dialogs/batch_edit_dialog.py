# views/dialogs/batch_edit_dialog.py
"""批量修改步骤参数对话框。

入口：步骤列表右键 →「批量修改步骤参数…」（views/step_list_view.py），
由 controllers/step_controller.py 打开、校验并落盘。

设计要点：
* 字段下拉**只列出选中步骤真正带有的参数**（取并集，按出现次数排），没带该字段的
  步骤自动跳过并在底部说明；
* 「定位方式 / 定位值」在绑定了元素库（`element_id`）的步骤上会被元素库覆盖
  （见 utils/step_params.ELEMENT_OVERRIDDEN_KEYS），改了对运行时不生效 ——
  所以默认**只改未绑定元素的步骤**，另给一个「全部改（解除绑定）」的作用范围
  让用户显式选择，避免"改了没反应"；
* 底部只有一个主操作「应用」，放弃修改走标题栏 × 或 Esc（与套件内容对话框一致）。
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QComboBox,
    QLineEdit, QSpinBox, QDoubleSpinBox, QPushButton, QFrame, QWidget,
)
from PyQt6.QtCore import Qt

from utils.step_params import (
    INTERNAL_KEYS, NON_NEGATIVE_KEYS, label_of, is_location_key,
    is_element_overridden, options_for,
)
from utils.theme import ThemeMode
from utils.settings import Settings, THEME_MODE_DARK


class BatchEditStepsDialog(QDialog):
    """把同一个参数值批量写到多个步骤上。"""

    def __init__(self, steps, parent=None):
        super().__init__(parent)
        self._steps = [s for s in (steps or []) if s is not None]
        self._keys = self._collect_keys()
        self._value_widget = None
        self._unbind = False
        self._theme_mode = ThemeMode.LIGHT

        self.setWindowTitle("批量修改步骤参数")
        self.resize(540, 360)
        self.setMinimumWidth(500)
        self.setup_ui()
        self.apply_theme()
        self._on_field_changed()

    # ------------------------------------------------------------------
    # 数据
    # ------------------------------------------------------------------
    def _collect_keys(self):
        """选中步骤的参数键并集（剔除内部字段），按出现次数从多到少排"""
        counter = {}
        for step in self._steps:
            for key in (step.params or {}):
                if key in INTERNAL_KEYS:
                    continue
                counter[key] = counter.get(key, 0) + 1
        return [k for k, _ in sorted(counter.items(),
                                     key=lambda kv: (-kv[1], label_of(kv[0])))]

    def _steps_with(self, key):
        return [s for s in self._steps if key in (s.params or {})]

    def _bound_steps(self, key):
        """会被元素库覆盖的步骤（带 element_id）"""
        return [s for s in self._steps_with(key) if s.params.get('element_id')]

    def _step_types_with(self, key):
        return {s.type for s in self._steps_with(key)}

    def _sample_value(self, key):
        for step in self._steps_with(key):
            return (step.params or {}).get(key)
        return ""

    def current_key(self):
        return self.field_combo.currentData()

    def target_steps(self):
        """本次会真正被改动的步骤"""
        key = self.current_key()
        if not key:
            return []
        steps = self._steps_with(key)
        if is_element_overridden(key) and not self._unbind:
            return [s for s in steps if not s.params.get('element_id')]
        return steps

    def skipped_count(self):
        return max(0, len(self._steps) - len(self.target_steps()))

    def result_value(self):
        """保存结果（无可改字段时为 None）::

            {'key': 参数键, 'value': 新值, 'unbind': 是否解除元素库绑定,
             'step_ids': 实际要改的步骤 id 列表}

        带上 step_ids 是必须的：控制器不能自己按「有该字段」去挑步骤 ——
        那样会把「作用范围」里明确排除掉的、绑定元素库的步骤也一起改掉，
        与对话框上承诺的「将修改 N 个」对不上。
        """
        key = self.current_key()
        if not key:
            return None
        return {
            'key': key,
            'value': self._current_value(),
            'unbind': self._unbind,
            'step_ids': [s.id for s in self.target_steps()],
        }

    def _current_value(self):
        w = self._value_widget
        if isinstance(w, QComboBox):
            return w.currentText()
        if isinstance(w, (QSpinBox, QDoubleSpinBox)):
            return w.value()
        if isinstance(w, QLineEdit):
            return w.text()
        return None

    # ------------------------------------------------------------------
    # 界面
    # ------------------------------------------------------------------
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        header = QHBoxLayout()
        self.title_label = QLabel("批量修改步骤参数")
        self.title_label.setObjectName("titleLabel")
        header.addWidget(self.title_label)
        header.addStretch()
        self.count_label = QLabel(f"已选中 {len(self._steps)} 个步骤")
        self.count_label.setObjectName("hintLabel")
        header.addWidget(self.count_label, 0,
                         Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(header)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(1, 1)

        self.field_label = QLabel("修改字段:")
        self.field_label.setObjectName("formLabel")
        self.field_label.setAlignment(Qt.AlignmentFlag.AlignRight |
                                      Qt.AlignmentFlag.AlignVCenter)
        grid.addWidget(self.field_label, 0, 0)

        self.field_combo = QComboBox()
        for key in self._keys:
            self.field_combo.addItem(label_of(key), key)
        self.field_combo.currentIndexChanged.connect(self._on_field_changed)
        grid.addWidget(self.field_combo, 0, 1)

        self.value_label = QLabel("设为:")
        self.value_label.setObjectName("formLabel")
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignRight |
                                      Qt.AlignmentFlag.AlignVCenter)
        grid.addWidget(self.value_label, 1, 0)

        self.value_host = QWidget()
        self.value_layout = QHBoxLayout(self.value_host)
        self.value_layout.setContentsMargins(0, 0, 0, 0)
        self.value_layout.setSpacing(6)
        grid.addWidget(self.value_host, 1, 1)

        # 作用范围：只在「会被元素库覆盖的字段 + 确实存在绑定步骤」时出现。
        # 隐藏时把行内两个控件一起藏掉 —— QGridLayout 会把全是隐藏控件的行高算成 0，
        # 行会自然收起，不会留一条空档。
        self.scope_label = QLabel("作用范围:")
        self.scope_label.setObjectName("formLabel")
        self.scope_label.setAlignment(Qt.AlignmentFlag.AlignRight |
                                      Qt.AlignmentFlag.AlignVCenter)
        grid.addWidget(self.scope_label, 2, 0)
        self.scope_combo = QComboBox()
        self.scope_combo.currentIndexChanged.connect(self._on_scope_changed)
        grid.addWidget(self.scope_combo, 2, 1)

        layout.addLayout(grid)

        self.summary_label = QLabel("")
        self.summary_label.setObjectName("summaryLabel")
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        self.tip_label = QLabel("")
        self.tip_label.setObjectName("hintLabel")
        self.tip_label.setWordWrap(True)
        layout.addWidget(self.tip_label)

        layout.addStretch(1)

        self.sep_line = QFrame()
        self.sep_line.setObjectName("sepLine")
        self.sep_line.setFixedHeight(1)
        layout.addWidget(self.sep_line)

        action_row = QHBoxLayout()
        action_row.addStretch(1)
        self.apply_btn = QPushButton("应用")
        self.apply_btn.setObjectName("okBtn")
        self.apply_btn.setMinimumWidth(150)
        self.apply_btn.clicked.connect(self.accept)
        action_row.addWidget(self.apply_btn)
        layout.addLayout(action_row)

    def _on_field_changed(self):
        key = self.current_key()

        # 1) 重建取值控件
        if self._value_widget is not None:
            self.value_layout.removeWidget(self._value_widget)
            self._value_widget.setParent(None)
            self._value_widget.deleteLater()
            self._value_widget = None
        if key is not None:
            self._value_widget = self._build_value_widget(key)
            self.value_layout.addWidget(self._value_widget)

        # 2) 作用范围
        self._unbind = False
        bound = self._bound_steps(key) if key else []
        show_scope = bool(key) and is_element_overridden(key) and bool(bound)
        self.scope_label.setVisible(show_scope)
        self.scope_combo.setVisible(show_scope)
        if show_scope:
            total = len(self._steps_with(key))
            free = total - len(bound)
            self.scope_combo.blockSignals(True)
            self.scope_combo.clear()
            self.scope_combo.addItem(f"只改未绑定元素的步骤（{free} 个）", False)
            self.scope_combo.addItem(f"全部改，并解除元素库绑定（{total} 个）", True)
            self.scope_combo.setCurrentIndex(0)
            self.scope_combo.blockSignals(False)

        # 3) 提示行
        if key and is_location_key(key):
            self.tip_label.setText(
                "提示：绑定了元素库的步骤，运行时的定位方式以元素库为准；"
                "选「全部改」会解除这些步骤的元素库绑定，改为直接使用上面的定位值。")
            self.tip_label.setVisible(show_scope)
        else:
            self.tip_label.setVisible(False)

        self._refresh_summary()

    def _on_scope_changed(self, _index):
        self._unbind = bool(self.scope_combo.currentData())
        self._refresh_summary()

    def _refresh_summary(self):
        key = self.current_key()
        if key is None:
            self.summary_label.setText("选中的步骤没有可批量修改的参数。")
            self.apply_btn.setText("应用")
            self.apply_btn.setEnabled(False)
            return

        n = len(self.target_steps())
        skipped = self.skipped_count()
        if n:
            text = f"将把 {n} 个步骤的「{label_of(key)}」改为：{self._format_value(self._current_value())}"
        else:
            text = f"没有可修改的步骤（选中的步骤都没有「{label_of(key)}」或都已绑定元素库）。"
        if skipped and n:
            text += f"　跳过 {skipped} 个不含该字段的步骤。"
        self.summary_label.setText(text)

        self.apply_btn.setText(f"应用到 {n} 个步骤" if n else "应用")
        self.apply_btn.setEnabled(bool(n))

    @staticmethod
    def _format_value(value):
        text = "" if value is None else str(value)
        return f"「{text}」"

    # ------------------------------------------------------------------
    def _build_value_widget(self, key):
        """按字段类型给合适的取值控件：固定选项用下拉，数字用数字框，其余用输入框"""
        options = options_for(key, self._step_types_with(key))
        if options:
            w = QComboBox()
            w.addItems(options)
            idx = w.findText(str(self._sample_value(key)))
            if idx >= 0:
                w.setCurrentIndex(idx)
            w.currentIndexChanged.connect(lambda *_: self._refresh_summary())
            return w

        sample = self._sample_value(key)
        if isinstance(sample, bool):
            return QLineEdit("true" if sample else "false")
        if isinstance(sample, int):
            w = QSpinBox()
            w.setRange(0 if key in NON_NEGATIVE_KEYS else -999999, 999999)
            w.setValue(max(w.minimum(), min(int(sample), w.maximum())))
            w.valueChanged.connect(lambda *_: self._refresh_summary())
            return w
        if isinstance(sample, float):
            w = QDoubleSpinBox()
            w.setRange(0.0 if key in NON_NEGATIVE_KEYS else -999999.0, 999999.0)
            w.setSingleStep(0.1)
            w.setDecimals(2)
            w.setValue(max(w.minimum(), min(float(sample), w.maximum())))
            w.valueChanged.connect(lambda *_: self._refresh_summary())
            return w

        w = QLineEdit("" if sample is None else str(sample))
        w.textChanged.connect(lambda *_: self._refresh_summary())
        return w

    # ------------------------------------------------------------------
    def apply_theme(self, theme_mode: ThemeMode = None):
        if theme_mode is None:
            theme_mode = (ThemeMode.DARK
                          if Settings.get_theme_mode() == THEME_MODE_DARK
                          else ThemeMode.LIGHT)
        self._theme_mode = theme_mode

        if theme_mode == ThemeMode.DARK:
            bg, text, border = "#2d2d2d", "#eeeeee", "#555"
            title, hint = "#ffffff", "#aaaaaa"
            input_bg, input_fg = "#1e1e1e", "#eeeeee"
            summary = "#ffd479"
        else:
            bg, text, border = "#ffffff", "#333333", "#d0d0d0"
            title, hint = "#1a1a1a", "#888888"
            input_bg, input_fg = "#ffffff", "#333333"
            summary = "#b26a00"

        self.setStyleSheet(f"""
            QDialog {{ background-color: {bg}; }}
            QFrame#sepLine {{ background-color: {border}; border: none; }}
            QDialog QLabel {{ color: {text}; background: transparent; font-size: 13px; }}
            QDialog QLabel#titleLabel {{ font-size: 16px; font-weight: bold; color: {title}; }}
            QDialog QLabel#hintLabel {{ color: {hint}; font-size: 12px; }}
            QDialog QLabel#formLabel {{ color: {text}; font-size: 13px; }}
            QDialog QLabel#summaryLabel {{ color: {summary}; font-size: 12px; }}
            QDialog QComboBox, QDialog QLineEdit,
            QDialog QSpinBox, QDialog QDoubleSpinBox {{
                background-color: {input_bg};
                color: {input_fg};
                border: 1px solid {border};
                border-radius: 4px;
                padding: 4px 8px;
                min-height: 22px;
                max-height: 22px;
                font-size: 13px;
            }}
            QPushButton#okBtn {{
                background-color: #1976d2;
                color: #ffffff;
                border: 1px solid #1976d2;
                border-radius: 4px;
                padding: 4px 12px;
                min-height: 22px;
                max-height: 22px;
                font-weight: 500;
            }}
            QPushButton#okBtn:hover {{ background-color: #1565c0; }}
            QPushButton#okBtn:disabled {{ background-color: #9e9e9e; border-color: #9e9e9e; }}
        """)
