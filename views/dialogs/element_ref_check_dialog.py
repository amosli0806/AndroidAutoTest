# views/dialogs/element_ref_check_dialog.py
"""失效元素引用检查：列出「步骤引用了已不存在的元素」，逐条修复。

场景：元素库里删掉/替换过元素后，步骤里留下的 element_id 就成了死引用。
执行时 ElementController.resolve_element() 找不到元素会**直接抛错**
（`元素ID 'xxx' 已失效，请更新元素库`），也就是这些步骤一跑就失败。

修复只有两条路，都在这里：
    重新拾取 —— 连设备，打开拾取面板点一下控件，把新定位写回步骤，并解除死引用
    解除引用 —— 不连设备，直接抹掉 element_id，保留步骤里原有的定位方式与值

只读两个 JSON（steps_data.json / elements_data.json）就能把问题列全，不需要设备。
"""
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                             QTableWidget, QTableWidgetItem, QHeaderView,
                             QAbstractItemView, QWidget)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor

from utils.settings import Settings, THEME_MODE_DARK
from utils.toast import show_toast


class ElementRefCheckDialog(QDialog):
    """检查元素引用的对话框（模态）。"""

    def __init__(self, rows, element_controller, device_service=None, parent=None):
        super().__init__(parent)
        self.setObjectName("ElementRefCheckDialog")
        self.setWindowTitle("检查元素引用")
        self.setModal(True)
        self.resize(860, 520)
        self.setMinimumSize(720, 420)

        self.rows = list(rows)
        self.element_controller = element_controller
        self.device_service = device_service
        self._fixed = set()          # 已修复的 step_id

        self._setup_ui()
        self._apply_theme()
        self._refresh_summary()

    # ---------- 界面 ----------
    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(10)

        title = QLabel("步骤引用的元素已不存在")
        title.setObjectName("RefTitle")
        root.addWidget(title)

        self.summary_label = QLabel("")
        self.summary_label.setObjectName("RefSummary")
        self.summary_label.setWordWrap(True)
        root.addWidget(self.summary_label)

        self.table = QTableWidget(0, 5)
        self.table.setObjectName("RefTable")
        self.table.setHorizontalHeaderLabels(["用例", "步骤", "缺失的元素", "现有定位", "操作"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 150)
        self.table.setColumnWidth(1, 150)
        self.table.setColumnWidth(2, 175)
        self.table.setColumnWidth(4, 168)
        root.addWidget(self.table, 1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.detach_all_btn = QPushButton("全部解除引用")
        self.detach_all_btn.setObjectName("detachAllBtn")
        self.detach_all_btn.clicked.connect(self._on_detach_all)
        btn_row.addWidget(self.detach_all_btn)
        btn_row.addStretch()
        self.close_btn = QPushButton("关闭")
        self.close_btn.setObjectName("okBtn")
        self.close_btn.setDefault(True)
        self.close_btn.clicked.connect(self.accept)
        btn_row.addWidget(self.close_btn)
        root.addLayout(btn_row)

        self._fill_table()

    def _fill_table(self):
        self.table.setRowCount(0)
        for row in self.rows:
            r = self.table.rowCount()
            row["_row"] = r          # 修完要回写这一行，别靠内容去猜
            self.table.insertRow(r)
            self.table.setRowHeight(r, 34)

            name = row.get("step_name") or f"第 {row.get('seq', 0)} 步"
            loc_type = row.get("loc_type") or "—"
            loc_value = row.get("loc_value") or ""
            if len(loc_value) > 60:
                loc_value = loc_value[:57] + "…"
            loc_text = f"{loc_type}：{loc_value}" if loc_value else f"{loc_type}：—"

            items = [row.get("case_name", ""), name, row.get("element_id", ""), loc_text]
            for col, text in enumerate(items):
                item = QTableWidgetItem(str(text))
                item.setToolTip(str(text))
                self.table.setItem(r, col, item)

            self.table.setCellWidget(r, 4, self._make_ops(row))

    def _make_ops(self, row):
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        pick = QPushButton("重新拾取")
        pick.setObjectName("rowPickBtn")
        pick.setFixedHeight(24)
        pick.clicked.connect(lambda _=False, r=row: self._on_repick(r))

        detach = QPushButton("解除引用")
        detach.setObjectName("rowDetachBtn")
        detach.setFixedHeight(24)
        detach.clicked.connect(lambda _=False, r=row: self._on_detach(r))

        lay.addWidget(pick)
        lay.addWidget(detach)
        lay.addStretch()
        return box

    # ---------- 修复动作 ----------
    def _on_repick(self, row):
        """打开拾取面板，把新定位写回步骤（并解除死引用）。"""
        if not self.device_service or not getattr(self.device_service, "device", None):
            show_toast(self, "请先在顶部工具栏连接设备", duration=2000)
            return

        from views.dialogs.element_pick_dialog import ElementPickDialog
        try:
            dlg = ElementPickDialog(self.device_service, self)
        except Exception as e:      # 打不开面板（设备掉线等）不能让对话框崩
            show_toast(self, f"打不开拾取面板：{e}", duration=2500)
            return
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.result:
            return

        res = dlg.result
        if self.element_controller.set_step_locator(
                row["step_id"], res["loc_type"], res["loc_value"]):
            self._mark_fixed(row)
            show_toast(self, "已写回新定位", duration=1600)

    def _on_detach(self, row):
        """只抹掉 element_id，步骤里原有的定位方式与值保留。"""
        if self.element_controller.detach_step_element(row["step_id"]):
            self._mark_fixed(row)
            show_toast(self, "已解除引用", duration=1600)

    def _on_detach_all(self):
        todo = [r for r in self.rows if r["step_id"] not in self._fixed]
        if not todo:
            return
        n = 0
        for row in todo:
            if self.element_controller.detach_step_element(row["step_id"]):
                self._mark_fixed(row, silent=True)
                n += 1
        show_toast(self, f"已解除 {n} 处引用", duration=2000)

    def _mark_fixed(self, row, silent=False):
        self._fixed.add(row["step_id"])
        r = self._row_index_of(row)
        if r < 0:
            return
        # 整行置灰 + 操作列换成「已修复」
        for col in range(4):
            item = self.table.item(r, col)
            if item is not None:
                item.setForeground(QColor("#9a9a9a"))
        done = QLabel("已修复")
        done.setObjectName("RefDone")
        done.setContentsMargins(6, 0, 0, 0)
        self.table.setCellWidget(r, 4, done)
        self._refresh_summary()

    def _row_index_of(self, row):
        return int(row.get("_row", -1))

    # ---------- 汇总 ----------
    def _refresh_summary(self):
        total = len(self.rows)
        left = total - len(self._fixed)
        cases = len({r.get("case_id") for r in self.rows})
        elems = len({r.get("element_id") for r in self.rows})
        if left:
            self.summary_label.setText(
                f"共 {total} 处失效引用，涉及 {elems} 个元素、{cases} 个用例。"
                f"这些步骤执行时会直接报「元素ID 已失效」，可以先「重新拾取」，"
                f"或点「解除引用」保留步骤里现有的定位。")
        else:
            self.summary_label.setText(f"全部 {total} 处已处理完毕，关闭窗口即可。")
        self.detach_all_btn.setEnabled(bool(left))

    # ---------- 主题 ----------
    def _apply_theme(self):
        is_dark = Settings.get_theme_mode() == THEME_MODE_DARK
        if is_dark:
            bg, fg, sub = "#2d2d2d", "#eeeeee", "#a0a0a0"
            card_bg, card_border = "#3c3c3c", "#555555"
            btn_bg, btn_fg, btn_border = "#4a4a4a", "#eeeeee", "#5a5a5a"
            row_alt = "#343434"
        else:
            bg, fg, sub = "#ffffff", "#1a1a1a", "#888888"
            card_bg, card_border = "#f8f9fa", "#e0e0e0"
            btn_bg, btn_fg, btn_border = "#f0f0f0", "#333333", "#d0d0d0"
            row_alt = "#fafafa"
        primary, primary_hover = "#1976d2", "#1565c0"

        self.setStyleSheet(f"""
            QDialog#ElementRefCheckDialog {{ background-color: {bg}; }}
            QDialog#ElementRefCheckDialog QLabel {{ color: {fg}; background: transparent; }}
            QDialog#ElementRefCheckDialog QLabel#RefTitle {{ font-size: 15px; font-weight: 600; }}
            QDialog#ElementRefCheckDialog QLabel#RefSummary {{ color: {sub}; font-size: 12px; }}
            QDialog#ElementRefCheckDialog QLabel#RefDone {{ color: #27ae60; font-size: 12px; }}
            QDialog#ElementRefCheckDialog QTableWidget {{
                background-color: {bg};
                alternate-background-color: {row_alt};
                color: {fg};
                border: 1px solid {card_border};
                border-radius: 8px;
                gridline-color: transparent;
                font-size: 12px;
            }}
            QDialog#ElementRefCheckDialog QHeaderView::section {{
                background-color: {card_bg};
                color: {sub};
                border: none;
                border-bottom: 1px solid {card_border};
                padding: 6px 4px;
                font-size: 12px;
            }}
            QDialog#ElementRefCheckDialog QTableWidget::item {{ padding: 4px; }}
            QDialog#ElementRefCheckDialog QPushButton {{
                background-color: {btn_bg};
                color: {btn_fg};
                border: 1px solid {btn_border};
                border-radius: 6px;
                padding: 5px 12px;
                font-size: 12px;
            }}
            QDialog#ElementRefCheckDialog QPushButton:hover {{ border-color: {primary}; }}
            QDialog#ElementRefCheckDialog QPushButton#rowPickBtn {{
                background-color: {primary};
                color: #ffffff;
                border-color: {primary};
            }}
            QDialog#ElementRefCheckDialog QPushButton#rowPickBtn:hover {{
                background-color: {primary_hover};
            }}
            /* 批量解除引用会改数据，用红色，和单行「重新拾取」区分开 */
            QDialog#ElementRefCheckDialog QPushButton#detachAllBtn {{
                background-color: #e74c3c;
                color: #ffffff;
                border-color: #e74c3c;
            }}
            QDialog#ElementRefCheckDialog QPushButton#detachAllBtn:hover {{
                background-color: #c0392b;
            }}
            QDialog#ElementRefCheckDialog QPushButton:disabled {{
                background-color: {card_bg};
                color: {sub};
                border-color: {card_border};
            }}
        """)
