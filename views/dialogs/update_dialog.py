# views/dialogs/update_dialog.py
"""「发现新版本」对话框。

刻意不碰网络：只把 main.py 传进来的 UpdateInfo 展示出来，用户点了什么就返回什么
（动作常量见下面 ACTION_*）。下载与替换由 main.py 编排 —— 这样这个对话框能在
离屏环境里单独跑、单独验，不必真的联网。
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTextEdit, QFrame
)

from utils.settings import Settings, THEME_MODE_DARK
from utils.version import APP_VERSION

ACTION_DOWNLOAD = "download"      # 下载并更新
ACTION_APPLY = "apply"            # 已下载好，立即重启并更新
ACTION_LATER = "later"            # 稍后再说


class UpdateAvailableDialog(QDialog):
    """can_install=False（开发模式/安装目录不可写）时不提供自动更新入口。

    already_staged=True 表示上次已经下好包、用户当时点了「稍后」—— 主按钮变成
    「立即重启更新」，不必重新下载。
    """

    def __init__(self, info, parent=None, can_install=True, already_staged=False):
        super().__init__(parent)
        self.info = info
        self.can_install = bool(can_install)
        self.already_staged = bool(already_staged)
        self.action = ACTION_LATER
        self.setObjectName("UpdateDialog")
        self.setWindowTitle("发现新版本")
        self.setMinimumWidth(480)
        self._build_ui()
        self._apply_style()

    # ---------- 构建 ----------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(10)

        title = QLabel(f"发现新版本 V{self.info.version}")
        title.setObjectName("UpdateTitle")
        root.addWidget(title)

        meta = [f"当前版本 V{APP_VERSION}"]
        if self.info.published_at:
            meta.append(f"发布于 {self.info.published_at[:10]}")
        if self.info.asset_size:
            meta.append(f"更新包 {self.info.asset_size_text}")
        subtitle = QLabel(" · ".join(meta))
        subtitle.setObjectName("UpdateSubtitle")
        root.addWidget(subtitle)

        divider = QFrame()
        divider.setObjectName("UpdateDivider")
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setFixedHeight(1)
        root.addWidget(divider)

        notes = QTextEdit()
        notes.setObjectName("UpdateNotes")
        notes.setReadOnly(True)
        notes.setPlainText(self.info.notes.strip() or "（该版本没有填写更新说明）")
        notes.setMinimumHeight(160)
        notes.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        root.addWidget(notes, 1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch()

        self.later_btn = QPushButton("稍后")
        self.later_btn.setObjectName("UpdateGhostBtn")
        self.later_btn.clicked.connect(lambda: self._choose(ACTION_LATER))
        btn_row.addWidget(self.later_btn)

        # 主按钮：能自动更新就引导自动更新；不能（开发模式）时只剩「稍后」
        if self.can_install:
            if self.already_staged:
                primary_text, primary_action = "立即重启更新", ACTION_APPLY
            else:
                primary_text, primary_action = "下载并更新", ACTION_DOWNLOAD
            self.primary_btn = QPushButton(primary_text)
            self.primary_btn.setObjectName("UpdatePrimaryBtn")
            self.primary_btn.clicked.connect(lambda: self._choose(primary_action))
            self.primary_btn.setEnabled(bool(self.info.asset_url))
            if not self.info.asset_url:
                self.primary_btn.setToolTip("这个 Release 里没有更新包")
            btn_row.addWidget(self.primary_btn)
        else:
            self.primary_btn = None

        root.addLayout(btn_row)

    def _choose(self, action):
        self.action = action
        self.accept()

    def _apply_style(self):
        """主题配色与「关于」对话框保持一致（同一组取值）。"""
        is_dark = (Settings.get_theme_mode() == THEME_MODE_DARK)
        if is_dark:
            bg, border, title_color, body_color = "#3c3c3c", "#555", "#ffffff", "#dddddd"
            notes_bg, notes_fg = "#333333", "#cccccc"
            ghost_bg, ghost_fg, ghost_hover = "#555", "#eeeeee", "#666666"
        else:
            bg, border, title_color, body_color = "#ffffff", "#d0d0d0", "#1a1a1a", "#555555"
            notes_bg, notes_fg = "#fafafa", "#444444"
            ghost_bg, ghost_fg, ghost_hover = "#f0f0f0", "#333333", "#e0e0e0"

        self.setStyleSheet(f"""
            #UpdateDialog {{
                background-color: {bg};
            }}
            #UpdateDialog QLabel {{
                background: transparent;
                color: {body_color};
                font-size: 13px;
            }}
            #UpdateDialog QLabel#UpdateTitle {{
                color: {title_color};
                font-size: 18px;
                font-weight: bold;
            }}
            #UpdateDialog QLabel#UpdateSubtitle {{
                color: #999999;
                font-size: 12px;
            }}
            #UpdateDialog QFrame#UpdateDivider {{
                background-color: {border};
                max-height: 1px;
            }}
            #UpdateDialog QTextEdit#UpdateNotes {{
                background-color: {notes_bg};
                color: {notes_fg};
                border: 1px solid {border};
                border-radius: 6px;
                padding: 8px;
                font-size: 12px;
            }}
            #UpdateDialog QPushButton#UpdatePrimaryBtn {{
                background-color: #1976d2;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 6px 18px;
                font-size: 13px;
                font-weight: 500;
            }}
            #UpdateDialog QPushButton#UpdatePrimaryBtn:hover {{
                background-color: #1565c0;
            }}
            #UpdateDialog QPushButton#UpdatePrimaryBtn:disabled {{
                background-color: #b0b0b0;
            }}
            #UpdateDialog QPushButton#UpdateGhostBtn {{
                background-color: {ghost_bg};
                color: {ghost_fg};
                border: none;
                border-radius: 6px;
                padding: 6px 16px;
                font-size: 13px;
            }}
            #UpdateDialog QPushButton#UpdateGhostBtn:hover {{
                background-color: {ghost_hover};
            }}
        """)

    @staticmethod
    def ask(info, parent=None, can_install=True, already_staged=False) -> str:
        """弹对话框并返回用户选的动作（ACTION_*）。"""
        dlg = UpdateAvailableDialog(info, parent,
                                    can_install=can_install,
                                    already_staged=already_staged)
        dlg.exec()
        return dlg.action
