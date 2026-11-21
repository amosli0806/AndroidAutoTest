# views/history_panel.py
"""历史记录面板（底部面板的一种，与日志 / 消息等互斥显示）。

左侧是按批次聚合的时间线，右侧是「选中版 vs 当前」的语义差异。
双击不恢复 —— 破坏性操作不该给容易误触的入口，恢复只走按钮 + 二次确认。
"""
import html
import os
from typing import Dict, List

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                             QPushButton, QTextBrowser, QVBoxLayout, QWidget)

from models.history_model import HistoryModel
from utils.app_paths import get_data_dir
from utils.dialogs import ConfirmDeleteDialog, InputDialog, QuestionDialog
from utils.history_diff import diff_dirs
from utils.toast import show_toast

# 相邻快照间隔小于这个秒数就合并成一批（连续编辑不该出现几十条）
MERGE_GAP_S = 300

KIND_COLOR = {"add": "#0F8A4F", "del": "#C0392B", "mod": "#B26B00"}
KIND_MARK = {"add": "＋", "del": "－", "mod": "～"}


def _palette() -> dict:
    try:
        from utils.settings import THEME_MODE_DARK, Settings
        dark = Settings.get_theme_mode() == THEME_MODE_DARK
    except Exception:
        dark = False
    if dark:
        return {"bg": "#2b2b2b", "side": "#262626", "text": "#e8e8e8",
                "muted": "#9aa4b2", "line": "#3a3a3a", "sel": "#1d3a57",
                "sel_fg": "#cfe2f7", "btn": "#3a3a3a", "btn_fg": "#d0d0d0"}
    return {"bg": "#ffffff", "side": "#fafbfd", "text": "#1f2733",
            "muted": "#98a2b3", "line": "#edf1f7", "sel": "#e8f1fd",
            "sel_fg": "#12406e", "btn": "#f2f4f7", "btn_fg": "#5b6472"}


class HistoryPanel(QWidget):
    """底部历史面板。恢复后发 `restored_done`，由主窗口负责让各 model 重新读盘。"""

    restored_done = pyqtSignal(str)
    collapse_requested = pyqtSignal()

    def __init__(self, model: HistoryModel, parent=None):
        super().__init__(parent)
        self.setObjectName("HistoryPanel")
        self._model = model
        self._pal = _palette()
        self._groups: List[dict] = []

        pal = self._pal
        self.setStyleSheet(f"#HistoryPanel{{background:{pal['bg']};}}")

        root = QVBoxLayout(self)
        # 面板横跨整个窗口底部，原来 12px 让标题和按钮几乎贴住边框。
        # 左右取 22 —— 和上方内容区（元素库那页约 24）视觉对齐，
        # 上下收到 10/12，与分隔线之间留出呼吸但不浪费高度。
        root.setContentsMargins(22, 10, 22, 12)
        root.setSpacing(8)

        # ---------- 标题行 ----------
        head = QHBoxLayout()
        head.setSpacing(10)
        title = QLabel("历史记录")
        title.setStyleSheet(f"font-size:13px;font-weight:600;color:{pal['text']};")
        head.addWidget(title)
        self._summary = QLabel("")
        self._summary.setStyleSheet(f"font-size:10.5px;color:{pal['muted']};")
        head.addWidget(self._summary)
        head.addStretch(1)
        self._collapse_btn = QPushButton("收起 ▾")
        self._collapse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._collapse_btn.setStyleSheet(
            f"QPushButton{{background:transparent;border:none;color:{pal['muted']};"
            f"font-size:10.5px;padding:2px 4px;}}")
        self._collapse_btn.clicked.connect(self.collapse_requested.emit)
        head.addWidget(self._collapse_btn)
        root.addLayout(head)

        # ---------- 主体：左时间线 + 右差异 ----------
        body = QHBoxLayout()
        body.setSpacing(10)

        self._list = QListWidget()
        self._list.setFixedWidth(196)
        self._list.setStyleSheet(
            f"QListWidget{{background:{pal['side']};border:1px solid {pal['line']};"
            f"border-radius:5px;outline:none;font-size:11.5px;color:{pal['text']};}}"
            f"QListWidget::item{{padding:5px 8px;border-radius:4px;margin:1px 2px;}}"
            f"QListWidget::item:selected{{background:{pal['sel']};color:{pal['sel_fg']};}}"
            f"QListWidget::item:hover{{background:{pal['sel']};}}"
            "QScrollBar:vertical{width:6px;background:transparent;}"
            f"QScrollBar::handle:vertical{{background:{pal['muted']};border-radius:3px;"
            "min-height:20px;}"
            "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0px;}")
        self._list.currentRowChanged.connect(self._on_select)
        body.addWidget(self._list)

        # 新快照写入后自动刷新列表。不接这个信号的话，用户改完看不到新条目，
        # 会以为「历史没在记」—— 其实快照已经写进去了（2026-11-21 实测反馈）
        self._model.snapshot_ready.connect(self._on_snapshot_ready)
        self._diff = QTextBrowser()
        self._diff.setStyleSheet(
            f"QTextBrowser{{background:{pal['side']};border:1px solid {pal['line']};"
            f"border-radius:5px;padding:6px 10px;font-size:11.5px;color:{pal['text']};}}"
            "QScrollBar:vertical{width:6px;background:transparent;}"
            f"QScrollBar::handle:vertical{{background:{pal['muted']};border-radius:3px;"
            "min-height:20px;}"
            "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0px;}")
        body.addWidget(self._diff, 1)
        root.addLayout(body, 1)

        # ---------- 底部操作 ----------
        foot = QHBoxLayout()
        foot.setSpacing(8)
        self._restore_btn = QPushButton("恢复到此版本")
        self._restore_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._restore_btn.setStyleSheet(
            "QPushButton{background:#1976d2;border:none;border-radius:4px;"
            "padding:5px 12px;font-size:11.5px;color:#fff;}"
            "QPushButton:hover{background:#1565c0;}"
            "QPushButton:disabled{background:#c9d4e2;color:#f2f5f9;}")
        self._restore_btn.clicked.connect(self._on_restore)
        foot.addWidget(self._restore_btn)

        self._mark_btn = QPushButton("打标记")
        self._mark_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mark_btn.setStyleSheet(
            f"QPushButton{{background:{pal['btn']};border:none;border-radius:4px;"
            f"padding:5px 12px;font-size:11.5px;color:{pal['btn_fg']};}}"
            f"QPushButton:hover{{background:{pal['sel']};}}")
        self._mark_btn.clicked.connect(self._on_mark)
        foot.addWidget(self._mark_btn)
        foot.addStretch(1)

        self._hint = QLabel("恢复前会自动先把当前状态存一份")
        self._hint.setStyleSheet(f"font-size:10px;color:{pal['muted']};")
        foot.addWidget(self._hint)
        root.addLayout(foot)

        self.refresh()

    # ---------- 数据 ----------
    def refresh(self):
        """重新拉快照并重建时间线，尽量停在原来那条上。"""
        # 记住当前选中的快照 id 而不是行号：新快照会插到列表顶部，
        # 行号会整体下移，按行号恢复就会跳到别的条目上
        current_id = None
        group = self._current_group()
        if group:
            current_id = group["items"][0]["id"]

        snaps = self._model.list_snapshots()
        self._groups = self._group(snaps)

        self._list.blockSignals(True)
        self._list.clear()
        for g in self._groups:
            newest = g["items"][0]
            time_text = (newest.get("created_at") or "")[-8:-3] or newest["id"][-10:]
            label = newest.get("label") or ""
            if label:
                text = f"{time_text}　★ {label}"
            elif len(g["items"]) > 1:
                text = f"{time_text}　{len(g['items'])} 份"
            else:
                text = f"{time_text}　—"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, g)
            self._list.addItem(item)
        self._list.blockSignals(False)

        total = len(snaps)
        size_kb = self._model.total_bytes() / 1024
        size_text = f"{size_kb:.0f} KB" if size_kb < 1024 else f"{size_kb / 1024:.1f} MB"
        self._summary.setText(f"{total} 份快照 · 占用 {size_text}"
                              if total else "还没有历史记录")

        if self._groups:
            row = 0
            if current_id:
                for i, g in enumerate(self._groups):
                    if any(s["id"] == current_id for s in g["items"]):
                        row = i
                        break
            self._list.setCurrentRow(row)
        else:
            self._diff.setHtml(
                f"<p style='color:{self._pal['muted']}'>"
                "还没有历史记录。修改任意用例后会自动保存，你随时能回到之前的状态。</p>")
            self._restore_btn.setEnabled(False)
            self._mark_btn.setEnabled(False)

    def _on_snapshot_ready(self, _snap_id):
        """后台写了新快照：面板可见时立刻刷新，不可见就等下次打开（那时会 refresh）"""
        if self.isVisible():
            self.refresh()

    @staticmethod
    def _group(snaps: List[Dict]) -> List[Dict]:
        """把相邻（间隔 < MERGE_GAP_S）的快照合并成一批。snaps 已按时间倒序。"""
        groups: List[Dict] = []
        for s in snaps:
            if not groups:
                groups.append({"items": [s]})
                continue
            last = groups[-1]
            prev_oldest = last["items"][-1]
            if prev_oldest.get("mtime", 0) - s.get("mtime", 0) <= MERGE_GAP_S:
                last["items"].append(s)
            else:
                groups.append({"items": [s]})
        return groups

    def _current_group(self):
        item = self._list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _selected_snapshot(self):
        g = self._current_group()
        return g["items"][0] if g else None

    # ---------- 交互 ----------
    def _on_select(self, _row):
        snap = self._selected_snapshot()
        if snap is None:
            return
        self._restore_btn.setEnabled(True)
        self._mark_btn.setEnabled(True)
        self._render_diff(snap)

    def _render_diff(self, snap: Dict):
        pal = self._pal
        try:
            diff = diff_dirs(snap["path"], get_data_dir())
        except Exception as e:
            self._diff.setHtml(f"<p style='color:{pal['muted']}'>对比失败：{e}</p>")
            return

        if not diff:
            self._diff.setHtml(
                f"<p style='color:{pal['muted']}'>该版本与当前内容一致。</p>")
            return

        parts = [f"<div style='font-size:11px;color:{pal['muted']};margin-bottom:6px'>"
                 f"{snap.get('created_at', '')} → 当前</div>"]
        for g in diff:
            parts.append(f"<p style='margin:8px 0 2px 0;font-weight:600;"
                         f"color:{pal['text']}'>{html.escape(g['group'])}</p>")
            for it in g["items"]:
                color = KIND_COLOR.get(it["kind"], pal["muted"])
                mark = KIND_MARK.get(it["kind"], "·")
                parts.append(f"<p style='margin:1px 0 1px 10px;color:{color}'>"
                             f"{mark} {html.escape(it['text'])}</p>")
        self._diff.setHtml("".join(parts))

        # 把改动条数回填到时间线条目上（列表里只显示时间，选中后才知道改了几处）
        item = self._list.currentItem()
        g = self._current_group()
        if item is not None and g is not None:
            newest = g["items"][0]
            time_text = (newest.get("created_at") or "")[-8:-3]
            n = sum(len(x["items"]) for x in diff)
            label = newest.get("label") or ""
            if label:
                item.setText(f"{time_text}　★ {label}")
            else:
                item.setText(f"{time_text}　改 {n} 处")

    def _on_restore(self):
        snap = self._selected_snapshot()
        if snap is None:
            return
        # 先算出「会回退什么」，确认框里点名，而不是笼统问「确定吗」
        try:
            diff = diff_dirs(snap["path"], get_data_dir())
        except Exception:
            diff = []
        lines = []
        for g in diff[:3]:
            for it in g["items"][:3]:
                lines.append(f"· {it['text']}")
        detail = "\n".join(lines) if lines else "（与当前内容一致）"

        dlg = QuestionDialog(
            self, "恢复到此版本",
            f"将恢复到 {snap.get('created_at', '')} 的状态？\n\n{detail}",
            detail="当前状态会先自动保存一份，之后还能撤销这次恢复。",
            yes_text="恢复", no_text="取消", default_yes=False)
        if dlg.exec() != QuestionDialog.DialogCode.Accepted:
            return

        if self._model.restore(snap["id"]):
            show_toast(message="已恢复，正在重新加载数据…", duration=2500)
            self.restored_done.emit(snap["id"])
            self.refresh()
        else:
            show_toast(message="恢复失败，请检查 data 目录权限")

    def _on_mark(self):
        snap = self._selected_snapshot()
        if snap is None:
            return
        name, ok = InputDialog.get_text(
            self, "打标记", "给这个版本起个名字：",
            default_text=snap.get("label") or "",
            placeholder="例如：发布前基线")
        if not ok:
            return
        if self._model.set_label(snap["id"], name):
            show_toast(message="已标记" if name else "已取消标记")
            self.refresh()
