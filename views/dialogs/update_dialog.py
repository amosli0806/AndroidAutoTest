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
        # 有增量补丁时报「实际下载量」（如 12.1 MB（增量下载，完整包 330.0 MB）），
        # 否则报全量包大小 —— 见 UpdateInfo.download_size_label
        size_label = self.info.download_size_label
        if size_label:
            meta.append(f"更新包 {size_label}")
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


class UpdateReadyDialog(QDialog):
    """「更新已就绪」确认框：更新包已下载校验完成，问是否立即重启生效。

    视觉与「发现新版本 / 正在更新」两个对话框同一套主题配色；
    标题直接用版本信息一句话，不再放大图标 + 大标题（窗口标题栏已有「更新已就绪」）。
    返回 True = 立即重启更新；False（稍后 / 关窗 / Esc）= 保留更新包下次生效。
    """

    def __init__(self, version: str, parent=None):
        super().__init__(parent)
        self._restart = False
        self.setObjectName("UpdateReadyDialog")
        self.setWindowTitle("更新已就绪")
        self.setMinimumWidth(440)
        self.setModal(True)
        self._build_ui(version)
        self._apply_style()

    # ---------- 构建 ----------
    def _build_ui(self, version: str):
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(12)

        # 标题直接用版本信息一句话：窗口标题栏已写「更新已就绪」，
        # 内容里再放大图标 + 大标题属于重复展示
        title = QLabel(f"新版本 V{version} 已下载并校验完成")
        title.setObjectName("ReadyTitle")
        root.addWidget(title)

        # 说明卡片：说清「要做什么 + 数据安全」两件事
        card = QFrame()
        card.setObjectName("ReadyCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 10, 12, 10)
        card_layout.setSpacing(4)
        main_line = QLabel("重启虫师即可完成更新")
        main_line.setObjectName("ReadyCardMain")
        card_layout.addWidget(main_line)
        sub_line = QLabel("过程只需几秒，你的项目与用例数据不会受影响")
        sub_line.setObjectName("ReadyCardSub")
        sub_line.setWordWrap(True)
        card_layout.addWidget(sub_line)
        root.addWidget(card)

        # 按钮行：稍后（灰）+ 立即重启更新（主按钮）
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch()
        later_btn = QPushButton("稍后")
        later_btn.setObjectName("ReadyGhostBtn")
        later_btn.clicked.connect(self.reject)
        btn_row.addWidget(later_btn)
        restart_btn = QPushButton("立即重启更新")
        restart_btn.setObjectName("ReadyPrimaryBtn")
        restart_btn.setDefault(True)
        restart_btn.clicked.connect(self._on_restart)
        btn_row.addWidget(restart_btn)
        root.addLayout(btn_row)
        restart_btn.setFocus()

    def _on_restart(self):
        self._restart = True
        self.accept()

    def _apply_style(self):
        """主题配色与「发现新版本」对话框保持一致（同一组取值）。"""
        is_dark = (Settings.get_theme_mode() == THEME_MODE_DARK)
        if is_dark:
            bg, border, title_color, body_color = "#3c3c3c", "#555", "#ffffff", "#dddddd"
            card_bg, card_sub = "#333333", "#bbbbbb"
            ghost_bg, ghost_fg, ghost_hover = "#555", "#eeeeee", "#666666"
        else:
            bg, border, title_color, body_color = "#ffffff", "#d0d0d0", "#1a1a1a", "#555555"
            card_bg, card_sub = "#f5f9ff", "#666666"
            ghost_bg, ghost_fg, ghost_hover = "#f0f0f0", "#333333", "#e0e0e0"

        self.setStyleSheet(f"""
            #UpdateReadyDialog {{
                background-color: {bg};
            }}
            #UpdateReadyDialog QLabel {{
                background: transparent;
                color: {body_color};
                font-size: 13px;
            }}
            #UpdateReadyDialog QLabel#ReadyTitle {{
                color: {title_color};
                font-size: 16px;
                font-weight: bold;
            }}
            #UpdateReadyDialog QFrame#ReadyCard {{
                background-color: {card_bg};
                border: 1px solid {border};
                border-radius: 8px;
            }}
            #UpdateReadyDialog QLabel#ReadyCardMain {{
                color: {title_color};
                font-size: 13px;
                font-weight: 500;
            }}
            #UpdateReadyDialog QLabel#ReadyCardSub {{
                color: {card_sub};
                font-size: 12px;
            }}
            #UpdateReadyDialog QPushButton#ReadyPrimaryBtn {{
                background-color: #1976d2;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 6px 18px;
                font-size: 13px;
                font-weight: 500;
            }}
            #UpdateReadyDialog QPushButton#ReadyPrimaryBtn:hover {{
                background-color: #1565c0;
            }}
            #UpdateReadyDialog QPushButton#ReadyGhostBtn {{
                background-color: {ghost_bg};
                color: {ghost_fg};
                border: none;
                border-radius: 6px;
                padding: 6px 16px;
                font-size: 13px;
            }}
            #UpdateReadyDialog QPushButton#ReadyGhostBtn:hover {{
                background-color: {ghost_hover};
            }}
        """)

    def reject(self):
        # 关窗 / Esc 都算「稍后」：更新包保留，下次检查更新可直接生效
        super().reject()

    @staticmethod
    def ask(version: str, parent=None) -> bool:
        """弹框并返回用户选择：True = 立即重启更新；False = 稍后。"""
        dlg = UpdateReadyDialog(version, parent)
        dlg.exec()
        return dlg._restart
