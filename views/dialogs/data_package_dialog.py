# views/dialogs/data_package_dialog.py
"""数据包导入预览对话框 + 导出/导入后台 worker。

导出/导入都是「读几十 MB 文件 + 算 sha256」的活，必须放线程，否则设置页会卡死。
worker 只发信号，界面更新都在主线程做。
"""
import os

from PyQt6.QtCore import QThread, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from services.data_package import export_package, import_package
from utils.dialogs import AutoFreeDialog


class ExportWorker(QThread):
    progress = pyqtSignal(int, int, str)
    finished_ok = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, dest: str, include_history: bool, parent=None):
        super().__init__(parent)
        self._dest = dest
        self._include_history = include_history

    def run(self):
        try:
            result = export_package(
                self._dest, self._include_history,
                progress=lambda d, t, n: self.progress.emit(d, t, n))
            self.finished_ok.emit(result)
        except Exception as e:
            self.failed.emit(str(e))


class ImportWorker(QThread):
    progress = pyqtSignal(str, int, int, str)
    finished_ok = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, path: str, snapshot_cb=None, backup_cb=None, parent=None):
        super().__init__(parent)
        self._path = path
        self._snapshot_cb = snapshot_cb
        self._backup_cb = backup_cb

    def run(self):
        try:
            result = import_package(
                self._path,
                progress=lambda s, d, t, n: self.progress.emit(s, d, t, n),
                snapshot_cb=self._snapshot_cb,
                backup_cb=self._backup_cb)
            self.finished_ok.emit(result)
        except ValueError as e:
            self.failed.emit(str(e))
        except Exception as e:
            self.failed.emit(f"导入失败：{type(e).__name__}: {e}")


class ImportPreviewDialog(AutoFreeDialog):
    """导入前预览：包内清单 + 覆盖警告 + 「先备份当前数据」勾选。"""

    def __init__(self, info: dict, has_existing: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle("导入数据包")
        self.setModal(True)
        self.setFixedWidth(540)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)

        container = QFrame()
        container.setObjectName("pkgContainer")
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(20)
        shadow.setOffset(0, 0)
        shadow.setColor(Qt.GlobalColor.black)
        container.setGraphicsEffect(shadow)

        v = QVBoxLayout(container)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(12)

        title = QLabel("导入数据包")
        title.setStyleSheet("font-size: 16px; font-weight: 600; color: #1F2733;")
        v.addWidget(title)

        # 基本信息
        counts = info.get("counts") or {}
        meta = QGridLayout()
        meta.setHorizontalSpacing(12)
        meta.setVerticalSpacing(6)
        rows = [
            ("来源", os.path.basename(info.get("path", ""))),
            ("导出于", f"{info.get('created_at', '')} · 虫师 {info.get('app_version', '')}"
                       + ("· 含历史改动" if info.get("includes_history") else "")),
            ("体积", f"{info.get('zip_size', 0) / 1024:.0f} KB"
                     f"（解压后 {info.get('unpacked_size', 0) / 1024:.0f} KB）"),
        ]
        for i, (k, val) in enumerate(rows):
            key = QLabel(k)
            key.setStyleSheet("color: #888780; font-size: 12px;")
            value = QLabel(str(val))
            value.setStyleSheet("color: #2C2C2A; font-size: 12px;")
            value.setWordWrap(True)
            meta.addWidget(key, i, 0)
            meta.addWidget(value, i, 1)
        meta.setColumnStretch(1, 1)
        v.addLayout(meta)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: #EDEBE4;")
        v.addWidget(line)

        # 清单
        head = QLabel("包内包含")
        head.setStyleSheet("color: #5F5E5A; font-size: 12px; font-weight: 600;")
        v.addWidget(head)

        items = [
            ("用例", counts.get("cases", 0)),
            ("步骤", counts.get("steps", 0)),
            ("元素库", counts.get("elements", 0)),
            ("语音用例", counts.get("voice_cases", 0)),
            ("接口用例", counts.get("api_cases", 0)),
            ("套件 / 模板", f"{counts.get('suites', 0)} / {counts.get('templates', 0)}"),
            ("知识笔记", counts.get("knowledge_notes", 0)),
            ("资源文件", f"{counts.get('asset_files', 0)} 个"
                        f"（{counts.get('asset_bytes', 0) / 1024:.0f} KB）"),
        ]
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(4)
        for i, (name, val) in enumerate(items):
            n = QLabel(name)
            n.setStyleSheet("color: #2C2C2A; font-size: 12px;")
            c = QLabel(str(val))
            c.setStyleSheet("color: #888780; font-size: 12px;")
            grid.addWidget(n, i // 2, (i % 2) * 2)
            grid.addWidget(c, i // 2, (i % 2) * 2 + 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        v.addLayout(grid)

        # 警告
        warn = QLabel("将覆盖当前全部数据（用例 / 元素 / 语音 / 接口 / 知识库 / 设置偏好）。\n"
                      "导入前会自动存一份历史快照，可回滚。")
        warn.setWordWrap(True)
        warn.setStyleSheet(
            "background: #FDF3F2; border: 1px solid #F3C9C4; border-radius: 6px;"
            "padding: 8px 10px; color: #8C2B22; font-size: 12px;")
        v.addWidget(warn)

        # 备份勾选：已有数据时默认勾上，空目录时默认不勾
        self.backup_check = QCheckBox("导入前先导出当前数据包（多一层保险）")
        self.backup_check.setChecked(bool(has_existing))
        self.backup_check.setStyleSheet("font-size: 12px; color: #2C2C2A;")
        v.addWidget(self.backup_check)

        # 按钮
        btns = QHBoxLayout()
        btns.addStretch()
        cancel = QPushButton("取消")
        cancel.setFixedSize(80, 34)
        cancel.clicked.connect(self.reject)
        ok = QPushButton("开始导入")
        ok.setFixedSize(96, 34)
        ok.setObjectName("pkgOkBtn")
        ok.clicked.connect(self.accept)
        btns.addWidget(cancel)
        btns.addWidget(ok)
        v.addLayout(btns)

        outer.addWidget(container)

        container.setStyleSheet("""
            #pkgContainer {
                background: #FFFFFF;
                border-radius: 12px;
                border: 1px solid #D0D0D0;
            }
            #pkgContainer QPushButton {
                background: #F1EFE8;
                border: 1px solid #D3D1C7;
                border-radius: 6px;
                color: #444441;
                font-size: 13px;
            }
            #pkgContainer QPushButton:hover { background: #E8E6DF; }
            #pkgContainer QPushButton#pkgOkBtn {
                background: #1976D2;
                border: 1px solid #1976D2;
                color: #FFFFFF;
                font-weight: 600;
            }
            #pkgContainer QPushButton#pkgOkBtn:hover { background: #1565C0; }
        """)

    def backup_wanted(self) -> bool:
        return self.backup_check.isChecked()
