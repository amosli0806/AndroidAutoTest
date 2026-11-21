# views/dialogs/voice_assert_dialog.py
"""语音用例里的「界面断言」编辑框。

与自动化编辑页的断言参数完全一致（assert_type / locationType / locationValue /
expected_value / timeout），执行时原样交给 device_service._perform_assert，
不另写一套判定逻辑。

为什么不把五个字段摊在步骤行里：一行放下拉 + 下拉 + 输入 + 输入 + 数字
会挤成一团，而且这几个参数不是每次都要改 —— 行内只显示摘要，双击再进来改。
"""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
import qtawesome as qta
from PyQt6.QtWidgets import (QComboBox, QDoubleSpinBox, QFrame, QGraphicsDropShadowEffect,
                             QHBoxLayout, QLabel, QLineEdit, QPushButton, QToolButton,
                             QVBoxLayout)

from utils.dialogs import AutoFreeDialog
from utils.toast import show_toast

# 与自动化断言卡片保持一致（_perform_assert 支持这四种）
ASSERT_TYPES = ["元素存在", "元素不存在", "文本等于", "文本包含"]
LOC_TYPES = ["资源ID", "文本", "描述", "XPath", "图像"]


def _palette() -> dict:
    try:
        from utils.settings import THEME_MODE_DARK, Settings
        dark = Settings.get_theme_mode() == THEME_MODE_DARK
    except Exception:
        dark = False
    if dark:
        return {"bg": "#2b2b2b", "border": "#3d3d3d", "text": "#e8e8e8",
                "muted": "#9aa4b2", "input_bg": "#333333", "input_border": "#4a4a4a",
                "btn_bg": "#3a3a3a", "btn_fg": "#d0d0d0"}
    return {"bg": "#ffffff", "border": "#d0d0d0", "text": "#1f2733",
            "muted": "#98a2b3", "input_bg": "#f5f7fa", "input_border": "#e4e9f0",
            "btn_bg": "#f2f4f7", "btn_fg": "#5b6472"}


def assert_label(params: dict) -> str:
    """把断言参数压成一句人话（行内摘要与日志共用）。"""
    kind = (params.get("assert_type") or "元素存在").strip()
    loc = (params.get("locationValue") or "").strip()
    expected = (params.get("expected_value") or "").strip()
    if not loc:
        return f"{kind}（未填定位值）"
    short = loc if len(loc) <= 24 else loc[:24] + "…"
    text = f"{kind}「{short}」"
    if kind == "文本等于" and expected:
        text += f" = 「{expected}」"
    return text


class VoiceAssertDialog(AutoFreeDialog):
    """返回 self.result_params（dict）；取消时为 None。"""

    def __init__(self, parent, params=None):
        super().__init__(parent)
        self.result_params = None
        self._pal = _palette()
        pal = self._pal
        params = params or {}

        self.setObjectName("VoiceAssertDialog")
        self.setWindowTitle("界面断言")
        self.setModal(True)
        self.setFixedSize(520, 400)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(0)

        container = QFrame()
        container.setObjectName("container")
        container.setStyleSheet(
            f"QFrame#container{{background-color:{pal['bg']};"
            f"border:1px solid {pal['border']};border-radius:12px;}}")
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 0)
        shadow.setColor(QColor(0, 0, 0, 90))
        container.setGraphicsEffect(shadow)
        outer.addWidget(container)

        box = QVBoxLayout(container)
        box.setContentsMargins(22, 18, 22, 16)
        box.setSpacing(10)

        title = QLabel("界面断言")
        title.setStyleSheet(f"font-size:16px;font-weight:bold;color:{pal['text']};")
        box.addWidget(title)

        tip = QLabel("播完这句话后检查界面。「预期检测」验的是车机有没有听懂，"
                     "这里验的是界面有没有真的变化。")
        tip.setWordWrap(True)
        tip.setStyleSheet(f"font-size:11px;color:{pal['muted']};")
        box.addWidget(tip)

        def label(text):
            lb = QLabel(text)
            lb.setStyleSheet(f"font-size:12px;color:{pal['muted']};")
            return lb

        def combo(items, current=""):
            cb = QComboBox()
            cb.addItems(items)
            cb.setStyleSheet(
                f"QComboBox{{background:{pal['input_bg']};"
                f"border:1px solid {pal['input_border']};border-radius:6px;"
                f"padding:5px 10px;font-size:12px;color:{pal['text']};min-height:20px;}}"
                f"QComboBox QAbstractItemView{{background:{pal['bg']};"
                f"border:1px solid {pal['border']};font-size:12px;"
                f"selection-background-color:#e8f1fd;selection-color:#12406e;}}")
            if current:
                idx = cb.findText(current)
                if idx >= 0:
                    cb.setCurrentIndex(idx)
            return cb

        box.addWidget(label("断言类型"))
        self.type_combo = combo(ASSERT_TYPES, params.get("assert_type", ""))
        box.addWidget(self.type_combo)

        box.addWidget(label("定位方式"))
        self.loc_combo = combo(LOC_TYPES, params.get("locationType", ""))
        box.addWidget(self.loc_combo)

        box.addWidget(label("定位值"))
        value_row = QHBoxLayout()
        value_row.setSpacing(6)
        self.value_edit = QLineEdit(params.get("locationValue", ""))
        self.value_edit.setPlaceholderText(
            "资源ID 如 com.app:id/btn；文本/描述直接填写；图像填图片路径")
        self.value_edit.setStyleSheet(
            f"QLineEdit{{background:{pal['input_bg']};border:1px solid {pal['input_border']};"
            f"border-radius:6px;padding:6px 10px;font-size:12px;color:{pal['text']};}}")
        value_row.addWidget(self.value_edit, 1)

        # 准星 / 相机：与动作卡片同一套交互 —— 在设备画面上点控件直接回填，
        # 省得用户手抄一长串资源 ID。图像定位时变成相机（截屏取图）。
        self.pick_btn = QToolButton()
        self.pick_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pick_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.pick_btn.setFixedSize(30, 28)
        self.pick_btn.setAutoRaise(True)
        self.pick_btn.clicked.connect(self._on_pick)
        value_row.addWidget(self.pick_btn)
        box.addLayout(value_row)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(label("预期值"))
        self.expected_edit = QLineEdit(params.get("expected_value", ""))
        self.expected_edit.setPlaceholderText(
            "选「文本等于 / 文本包含」时才需要填写")
        self.expected_edit.setStyleSheet(self.value_edit.styleSheet())
        row.addWidget(self.expected_edit, 1)
        row.addWidget(label("超时"))
        self.timeout_spin = QDoubleSpinBox()
        self.timeout_spin.setRange(1, 60)
        self.timeout_spin.setDecimals(0)
        self.timeout_spin.setSuffix(" 秒")
        self.timeout_spin.setFixedWidth(84)
        self.timeout_spin.setValue(float(params.get("timeout") or 5))
        self.timeout_spin.setStyleSheet(self.value_edit.styleSheet())
        row.addWidget(self.timeout_spin)
        box.addLayout(row)

        box.addStretch(1)

        foot = QHBoxLayout()
        foot.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setFixedWidth(84)
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setStyleSheet(
            f"QPushButton{{background:{pal['btn_bg']};border:none;border-radius:5px;"
            f"padding:6px 0;font-size:12px;color:{pal['btn_fg']};}}")
        cancel.clicked.connect(self.reject)
        foot.addWidget(cancel)
        ok = QPushButton("确定")
        ok.setFixedWidth(96)
        ok.setCursor(Qt.CursorShape.PointingHandCursor)
        ok.setStyleSheet(
            "QPushButton{background:#1976d2;border:none;border-radius:5px;"
            "padding:6px 0;font-size:12px;color:#fff;}")
        ok.clicked.connect(self._on_ok)
        foot.addWidget(ok)
        box.addLayout(foot)

        self.type_combo.currentTextChanged.connect(self._sync_expected_enabled)
        self._sync_expected_enabled(self.type_combo.currentText())
        self.loc_combo.currentTextChanged.connect(lambda _t: self._sync_pick_icon())
        self._sync_pick_icon()
        self.value_edit.setFocus()

    # 只有这两类断言会读 expected_value（见 device_service._perform_assert）
    NEEDS_EXPECTED = ("文本等于", "文本包含")

    def _sync_expected_enabled(self, text):
        need = text in self.NEEDS_EXPECTED
        self.expected_edit.setEnabled(need)
        if need:
            self.expected_edit.setPlaceholderText(
                "元素实际文本应完全等于该值" if text == "文本等于"
                else "元素实际文本应包含该片段")

    def _sync_pick_icon(self):
        """图像定位 → 相机（截屏取图）；其余 → 准星（画面拾取）。与动作卡片一致。"""
        color = "#bbbbbb" if self._pal["bg"] != "#ffffff" else "#555555"
        if self.loc_combo.currentText() == "图像":
            self.pick_btn.setIcon(qta.icon('fa6s.camera', color=color))
        else:
            self.pick_btn.setIcon(qta.icon('fa6s.crosshairs', color=color))

    def _on_pick(self):
        """打开拾取面板：在设备画面上点控件 → 回填定位方式与定位值。"""
        parent = self.parent()
        ds = getattr(parent, "_device_service", None)
        if ds is None or not getattr(ds, "device", None):
            show_toast(self, "未检测到设备", duration=2000)
            return
        try:
            from views.dialogs.element_pick_dialog import ElementPickDialog
            dlg = ElementPickDialog(ds, self)
        except Exception as e:
            show_toast(self, f"打不开拾取面板：{e}", duration=2500)
            return
        if dlg.exec() != dlg.DialogCode.Accepted or not dlg.result:
            return
        res = dlg.result
        idx = self.loc_combo.findText(res.get("loc_type") or "")
        if idx >= 0:
            self.loc_combo.setCurrentIndex(idx)
        self.value_edit.setText(res.get("loc_value") or "")

    def _on_ok(self):
        value = self.value_edit.text().strip()
        if not value:
            show_toast(self, "请填写定位值", duration=2000)
            self.value_edit.setFocus()
            return
        assert_type = self.type_combo.currentText()
        expected = self.expected_edit.text().strip()
        if assert_type in ("文本等于", "文本包含") and not expected:
            show_toast(self, "「文本等于」需要填预期值", duration=2000)
            self.expected_edit.setFocus()
            return
        self.result_params = {
            "assert_type": assert_type,
            "locationType": self.loc_combo.currentText(),
            "locationValue": value,
            "expected_value": expected,
            "timeout": int(self.timeout_spin.value()),
        }
        self.accept()
