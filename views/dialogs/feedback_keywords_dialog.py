# views/dialogs/feedback_keywords_dialog.py
"""语音回执验证「成功关键词」编辑对话框（PyQt6 + 主题适配）

按「用例」维度维护成功关键词：一个用例对应一种语音场景（导航/音乐/空调…），
每个场景车机识别成功时的反馈措辞不同，都塞设置页会越来越乱，所以放这里。
每行一个词，命中任意一个即判识别成功（模糊包含匹配）。
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QPlainTextEdit
)
from PyQt6.QtCore import Qt

from utils.theme import ThemeMode
from utils.settings import Settings, THEME_MODE_DARK


class FeedbackKeywordsDialog(QDialog):
    """编辑当前用例的「成功关键词」，返回去空去重的关键词列表。"""

    def __init__(self, parent=None, case_name="", keywords=None):
        super().__init__(parent)
        self._theme_mode = ThemeMode.LIGHT
        self._case_name = case_name or ""

        self.setWindowTitle("反馈检测 · 成功关键词")
        self.setModal(True)
        self.resize(460, 360)

        self._setup_ui(keywords or [])
        self.apply_theme()

    # ------------------------------------------------------------------
    def _setup_ui(self, keywords):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        title = QLabel("识别成功关键词")
        title.setObjectName("titleLabel")
        layout.addWidget(title)

        subtitle = QLabel(
            f"给「{self._case_name}」这个用例配置：播报后，车机日志里出现下面"
            "任意一个词，就判定这句被正确识别。"
        )
        subtitle.setObjectName("subtitleLabel")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        self.edit = QPlainTextEdit()
        self.edit.setPlaceholderText(
            "每行一个关键词，例如：\n已为您导航\n导航到\n正在播放")
        self.edit.setPlainText("\n".join(keywords))
        layout.addWidget(self.edit, 1)

        hint = QLabel(
            "模糊匹配：日志里只要「包含」这个词就算命中。"
            "失败关键词不用在这里填，去「设置 → 语音播报 → 回执验证」里配。"
        )
        hint.setObjectName("subtitleLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("取消")
        cancel_btn.setObjectName("secondaryBtn")
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setFixedSize(96, 32)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        save_btn = QPushButton("保存")
        save_btn.setObjectName("primaryBtn")
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setFixedSize(96, 32)
        save_btn.setDefault(True)
        save_btn.clicked.connect(self.accept)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

    def keywords(self):
        """把文本框内容按行拆成去空去重的关键词列表。"""
        seen = set()
        result = []
        for line in self.edit.toPlainText().splitlines():
            line = line.strip()
            if line and line not in seen:
                seen.add(line)
                result.append(line)
        return result

    # ------------------------------------------------------------------
    def apply_theme(self, theme_mode: ThemeMode = None):
        if theme_mode is None:
            theme_mode = (ThemeMode.DARK
                          if Settings.get_theme_mode() == THEME_MODE_DARK
                          else ThemeMode.LIGHT)
        self._theme_mode = theme_mode

        if theme_mode == ThemeMode.DARK:
            bg, text, muted = "#2d2d2d", "#eeeeee", "#aaaaaa"
            border, field_bg = "#555", "#1e1e1e"
            primary_bg, primary_hover = "#3498db", "#2f81b8"
            secondary_bg, secondary_hover = "#3a3a3a", "#454545"
        else:
            bg, text, muted = "#ffffff", "#333333", "#7f8c8d"
            border, field_bg = "#d0d0d0", "#f7f7f7"
            primary_bg, primary_hover = "#1976d2", "#1565c0"
            secondary_bg, secondary_hover = "#f0f0f0", "#e0e0e0"

        self.setStyleSheet(f"""
            QDialog {{ background-color: {bg}; }}
            QDialog QLabel {{ color: {text}; background: transparent; font-size: 13px; }}
            QDialog QLabel#titleLabel {{
                font-size: 16px; font-weight: bold;
            }}
            QDialog QLabel#subtitleLabel {{ color: {muted}; font-size: 12px; }}
            QDialog QPlainTextEdit {{
                background-color: {field_bg};
                color: {text};
                border: 1px solid {border};
                border-radius: 6px;
                padding: 8px;
                font-size: 13px;
            }}
            QDialog QPlainTextEdit:focus {{ border-color: {primary_bg}; }}
            QDialog QPushButton#secondaryBtn {{
                background-color: {secondary_bg};
                color: {text};
                border: 1px solid {border};
                border-radius: 6px;
                font-size: 13px;
            }}
            QDialog QPushButton#secondaryBtn:hover {{
                background-color: {secondary_hover};
            }}
            QDialog QPushButton#primaryBtn {{
                background-color: {primary_bg};
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: 500;
                font-size: 13px;
            }}
            QDialog QPushButton#primaryBtn:hover {{
                background-color: {primary_hover};
            }}
        """)
