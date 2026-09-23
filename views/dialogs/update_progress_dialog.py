# views/dialogs/update_progress_dialog.py
"""下载并解压更新包的进度对话框。

后台 QThread 里跑 services.update_service.install_prepare()，本对话框只负责显示进度与
取消；结果（暂存目录 / 错误原因）通过属性回传，安装决策留给 main.py。刻意不在这里碰
安装目录的逻辑，方便离屏单独验证。
"""

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar, QPushButton
)

from utils.settings import Settings, THEME_MODE_DARK

_STAGE_TEXT = {
    "download": "正在下载更新包…",
    "extract": "正在解压更新包…",
}


class _PrepareWorker(QThread):
    """后台把更新包下好、校验、解压好（绝不碰界面）。"""

    stage_changed = pyqtSignal(str)
    progressed = pyqtSignal(int, int)     # (当前, 总数)
    done = pyqtSignal(str)                # 暂存目录
    failed = pyqtSignal(str)

    def __init__(self, info, parent=None):
        super().__init__(parent)
        self.info = info
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        from services import update_service as us
        try:
            staging = us.install_prepare(
                self.info,
                progress=lambda cur, total: self.progressed.emit(cur, total),
                cancel=lambda: self._cancel,
                on_stage=lambda s: self.stage_changed.emit(s),
            )
        except Exception as e:
            self.failed.emit(f"{type(e).__name__}: {e}")
            return
        self.done.emit(staging)


class UpdateProgressDialog(QDialog):
    """exec_and_prepare() 返回后：self.staging 有值 = 已就绪；否则看 self.error。"""

    def __init__(self, info, parent=None):
        super().__init__(parent)
        self.info = info
        self.staging = ""
        self.error = ""
        self.cancelled = False
        self._worker = None
        self._stage = "download"
        self._finished = False

        self.setObjectName("UpdateProgressDialog")
        self.setWindowTitle("正在更新")
        self.setMinimumWidth(420)
        self.setModal(True)
        self._build_ui()
        self._apply_style()

    # ---------- 构建 ----------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(12)

        title = QLabel(f"正在更新到 V{self.info.version}")
        title.setObjectName("ProgressTitle")
        root.addWidget(title)

        self.stage_label = QLabel(_STAGE_TEXT["download"])
        self.stage_label.setObjectName("ProgressStage")
        root.addWidget(self.stage_label)

        self.bar = QProgressBar()
        self.bar.setObjectName("ProgressBar")
        self.bar.setTextVisible(True)
        self.bar.setRange(0, 0)        # 起手先转起来（还不知道总大小）
        root.addWidget(self.bar)

        self.hint = QLabel("")
        self.hint.setObjectName("ProgressHint")
        self.hint.setWordWrap(True)
        root.addWidget(self.hint)

        row = QHBoxLayout()
        row.addStretch()
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setObjectName("ProgressCancelBtn")
        self.cancel_btn.clicked.connect(self._on_cancel_clicked)
        row.addWidget(self.cancel_btn)
        root.addLayout(row)

    def _apply_style(self):
        is_dark = (Settings.get_theme_mode() == THEME_MODE_DARK)
        if is_dark:
            bg, border, title_color, body = "#3c3c3c", "#555", "#ffffff", "#cccccc"
            bar_bg, ghost_bg, ghost_fg = "#2b2b2b", "#555", "#eeeeee"
        else:
            bg, border, title_color, body = "#ffffff", "#d0d0d0", "#1a1a1a", "#555555"
            bar_bg, ghost_bg, ghost_fg = "#f0f0f0", "#f0f0f0", "#333333"

        self.setStyleSheet(f"""
            #UpdateProgressDialog {{
                background-color: {bg};
            }}
            #UpdateProgressDialog QLabel {{
                background: transparent;
                color: {body};
                font-size: 13px;
            }}
            #UpdateProgressDialog QLabel#ProgressTitle {{
                color: {title_color};
                font-size: 16px;
                font-weight: bold;
            }}
            #UpdateProgressDialog QLabel#ProgressStage {{
                color: {body};
            }}
            #UpdateProgressDialog QLabel#ProgressHint {{
                color: #999999;
                font-size: 12px;
            }}
            #UpdateProgressDialog QProgressBar#ProgressBar {{
                background-color: {bar_bg};
                border: 1px solid {border};
                border-radius: 6px;
                height: 18px;
                text-align: center;
                font-size: 11px;
                color: {title_color};
            }}
            #UpdateProgressDialog QProgressBar#ProgressBar::chunk {{
                background-color: #1976d2;
                border-radius: 5px;
            }}
            #UpdateProgressDialog QPushButton#ProgressCancelBtn {{
                background-color: {ghost_bg};
                color: {ghost_fg};
                border: none;
                border-radius: 6px;
                padding: 6px 18px;
                font-size: 13px;
            }}
            #UpdateProgressDialog QPushButton#ProgressCancelBtn:hover {{
                background-color: #e0e0e0;
            }}
            #UpdateProgressDialog QPushButton#ProgressCancelBtn:disabled {{
                color: #aaaaaa;
            }}
        """)

    # ---------- 流程 ----------
    def exec_and_prepare(self) -> bool:
        """跑完整个「下载 + 解压」。返回 True = 更新包已就绪。"""
        self._worker = _PrepareWorker(self.info, self)
        self._worker.stage_changed.connect(self._on_stage)
        self._worker.progressed.connect(self._on_progress)
        self._worker.done.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()
        self.exec()
        return bool(self.staging)

    def _on_stage(self, stage: str):
        self._stage = stage
        self.stage_label.setText(_STAGE_TEXT.get(stage, "正在准备…"))
        self.bar.setRange(0, 0)

    def _on_progress(self, cur: int, total: int):
        if total <= 0:
            self.bar.setRange(0, 0)
            return
        if self._stage == "download":
            done_mb, total_mb = cur / 1024 / 1024, total / 1024 / 1024
            self.bar.setRange(0, 100)
            self.bar.setValue(int(cur * 100 / total))
            self.hint.setText(f"{done_mb:.1f} / {total_mb:.1f} MB")
        else:
            self.bar.setRange(0, total)
            self.bar.setValue(cur)
            self.hint.setText(f"已解压 {cur} / {total} 个文件")

    def _on_done(self, staging: str):
        self._finished = True
        self.staging = staging
        self.accept()

    def _on_failed(self, message: str):
        self._finished = True
        if self.cancelled:
            self.reject()
            return
        self.error = message
        self.stage_label.setText("更新准备失败")
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.hint.setText(message)
        self.cancel_btn.setText("关闭")
        self.cancel_btn.setEnabled(True)

    def _on_cancel_clicked(self):
        if self._finished:
            self.reject()
            return
        self.cancelled = True
        self.cancel_btn.setEnabled(False)
        self.stage_label.setText("正在取消…")
        if self._worker is not None:
            self._worker.cancel()
        self.reject()

    def reject(self):
        # 用户点取消 / 关窗 / Esc：让后台线程尽快停下再退出，别留下写到一半的文件
        if not self._finished and self._worker is not None and self._worker.isRunning():
            self.cancelled = True
            self._worker.cancel()
            self._worker.wait(5000)
        super().reject()
