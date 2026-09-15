# utils/widget_helpers.py
from PyQt6.QtWidgets import QComboBox, QListView


def prepare_combo_view(combo: QComboBox):
    """给 QComboBox 设置透明的 QListView view，让 QSS 的下拉面板样式生效。
    同时避免 view 默认画白底盖住 QSS 面板背景。"""
    view = QListView()
    view.setAutoFillBackground(False)
    view.setStyleSheet("QListView { background: transparent; border: none; }")
    combo.setView(view)