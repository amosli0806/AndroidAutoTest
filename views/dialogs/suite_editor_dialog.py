# views/dialogs/suite_editor_dialog.py
"""套件内容编辑对话框：查看套件内用例、拖拽调整执行顺序、增删用例。

列表顺序就是执行顺序 —— 保存后写回套件的 case_ids，执行页按这个顺序跑。
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QPushButton, QLabel
)
from PyQt6.QtCore import Qt

from utils.theme import ThemeMode
from utils.settings import Settings, THEME_MODE_DARK
from utils.toast import show_toast

_ID_ROLE = Qt.ItemDataRole.UserRole            # 用例 id
_NAME_ROLE = Qt.ItemDataRole.UserRole + 1      # 用例名（重排后按它重编序号）


class SuiteEditorDialog(QDialog):
    """编辑一个套件的用例集合与执行顺序。"""

    def __init__(self, suite_name: str, ordered_ids, id_to_name=None,
                 available_ids=None, parent=None):
        """
        :param suite_name: 套件名（仅用于显示）
        :param ordered_ids: 套件当前用例 id，**按执行顺序**
        :param id_to_name: {case_id: 用例名}，取不到时回退显示 id
        :param available_ids: 页面上当前勾选的用例 id（可一键加入；已在套件里的不重复）
        """
        super().__init__(parent)
        self._theme_mode = ThemeMode.LIGHT
        self._suite_name = suite_name
        self._id_to_name = dict(id_to_name or {})
        self._available_ids = list(available_ids or [])

        self.setWindowTitle(f"套件内容 · {suite_name}")
        self.resize(540, 560)
        self.setup_ui()
        self.apply_theme()
        self._populate(ordered_ids)

    # ------------------------------------------------------------------
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        self.title_label = QLabel(f"套件内容 · {self._suite_name}")
        self.title_label.setObjectName("titleLabel")
        layout.addWidget(self.title_label)

        self.hint_label = QLabel("拖拽条目可调整执行顺序（列表顺序 = 执行顺序）")
        self.hint_label.setObjectName("hintLabel")
        self.hint_label.setWordWrap(True)
        layout.addWidget(self.hint_label)

        self.count_label = QLabel("")
        self.count_label.setObjectName("countLabel")
        layout.addWidget(self.count_label)

        self.list_widget = QListWidget()
        self.list_widget.setObjectName("suiteList")
        # 拖拽排序：内部移动；顺序即执行顺序
        self.list_widget.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.list_widget.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.list_widget.setSelectionMode(
            QListWidget.SelectionMode.ExtendedSelection)
        self.list_widget.model().rowsMoved.connect(lambda *_: self._renumber())
        layout.addWidget(self.list_widget, 1)

        op_row = QHBoxLayout()
        op_row.setSpacing(8)
        self.add_checked_btn = QPushButton("加入当前勾选的用例")
        self.add_checked_btn.setObjectName("opBtn")
        self.add_checked_btn.clicked.connect(self._on_add_checked)
        op_row.addWidget(self.add_checked_btn)

        self.remove_btn = QPushButton("移除选中")
        self.remove_btn.setObjectName("opBtn")
        self.remove_btn.clicked.connect(self._on_remove_selected)
        op_row.addWidget(self.remove_btn)

        self.move_up_btn = QPushButton("上移")
        self.move_up_btn.setObjectName("opBtn")
        self.move_up_btn.clicked.connect(lambda: self._move_selected(-1))
        op_row.addWidget(self.move_up_btn)

        self.move_down_btn = QPushButton("下移")
        self.move_down_btn.setObjectName("opBtn")
        self.move_down_btn.clicked.connect(lambda: self._move_selected(1))
        op_row.addWidget(self.move_down_btn)
        op_row.addStretch()
        layout.addLayout(op_row)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setObjectName("cancelBtn")
        self.cancel_btn.setFixedHeight(34)
        self.cancel_btn.setMinimumWidth(120)
        self.cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self.cancel_btn)

        self.ok_btn = QPushButton("保存")
        self.ok_btn.setObjectName("okBtn")
        self.ok_btn.setFixedHeight(34)
        self.ok_btn.setMinimumWidth(120)
        self.ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(self.ok_btn)
        layout.addLayout(btn_row)

    # ------------------------------------------------------------------
    def _display_name(self, case_id: str) -> str:
        return self._id_to_name.get(case_id) or case_id

    def _populate(self, ordered_ids):
        self.list_widget.clear()
        for cid in (ordered_ids or []):
            item = QListWidgetItem()
            item.setData(_ID_ROLE, cid)
            item.setData(_NAME_ROLE, self._display_name(cid))
            self.list_widget.addItem(item)
        self._renumber()

    def _renumber(self):
        """给每条加「序号. 名称」前缀，让执行顺序一眼可见。"""
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            item.setText(f"{i + 1}. {item.data(_NAME_ROLE)}")
        self._update_count()
        self._update_op_buttons()

    def _update_count(self):
        n = self.list_widget.count()
        self.count_label.setText(
            f"共 {n} 条用例" if n else "套件里还没有用例 —— 可点下方「加入当前勾选的用例」")

    def _update_op_buttons(self):
        has_sel = bool(self.list_widget.selectedItems())
        for b in (self.remove_btn, self.move_up_btn, self.move_down_btn):
            b.setEnabled(has_sel)

    def _current_ids(self):
        return [self.list_widget.item(i).data(_ID_ROLE)
                for i in range(self.list_widget.count())]

    # ------------------------------------------------------------------
    def _on_add_checked(self):
        """把执行页当前勾选的用例追加进来（已在套件里的跳过）。"""
        if not self._available_ids:
            show_toast(self, "执行页当前没有勾选用例", duration=2000)
            return
        existing = set(self._current_ids())
        added = 0
        for cid in self._available_ids:
            if cid in existing:
                continue
            item = QListWidgetItem()
            item.setData(_ID_ROLE, cid)
            item.setData(_NAME_ROLE, self._display_name(cid))
            self.list_widget.addItem(item)
            existing.add(cid)
            added += 1
        self._renumber()
        show_toast(self, f"已加入 {added} 条" if added else "勾选的用例都已在套件里",
                   duration=2000)

    def _on_remove_selected(self):
        for item in list(self.list_widget.selectedItems()):
            self.list_widget.takeItem(self.list_widget.row(item))
        self._renumber()

    def _move_selected(self, delta: int):
        """上移/下移选中项（多选时整体移动，保持相对顺序）。"""
        rows = sorted(self.list_widget.row(i)
                      for i in self.list_widget.selectedItems())
        if not rows:
            return
        if delta < 0 and rows[0] == 0:
            return
        if delta > 0 and rows[-1] == self.list_widget.count() - 1:
            return
        ordered = rows if delta < 0 else list(reversed(rows))
        for row in ordered:
            item = self.list_widget.takeItem(row)
            self.list_widget.insertItem(row + delta, item)
            item.setSelected(True)
        self._renumber()

    def result_ids(self):
        """保存时取回排序后的用例 id。"""
        return self._current_ids()

    # ------------------------------------------------------------------
    def apply_theme(self, theme_mode: ThemeMode = None):
        if theme_mode is None:
            theme_mode = (ThemeMode.DARK
                          if Settings.get_theme_mode() == THEME_MODE_DARK
                          else ThemeMode.LIGHT)
        self._theme_mode = theme_mode

        if theme_mode == ThemeMode.DARK:
            bg, text, border = "#2d2d2d", "#eeeeee", "#555"
            list_bg, list_sel, title, hint = "#1e1e1e", "#1e3a5f", "#ffffff", "#aaaaaa"
            btn_bg, btn_fg, btn_hover = "#555", "#eeeeee", "#666"
        else:
            bg, text, border = "#ffffff", "#333333", "#d0d0d0"
            list_bg, list_sel, title, hint = "#fafbfc", "#e3f2fd", "#1a1a1a", "#888888"
            btn_bg, btn_fg, btn_hover = "#f0f0f0", "#333333", "#e0e0e0"

        self.setStyleSheet(f"""
            QDialog {{ background-color: {bg}; }}
            QDialog QLabel {{ color: {text}; background: transparent; font-size: 13px; }}
            QDialog QLabel#titleLabel {{ font-size: 16px; font-weight: bold; color: {title}; }}
            QDialog QLabel#hintLabel, QDialog QLabel#countLabel {{ color: {hint}; font-size: 12px; }}
            QListWidget#suiteList {{
                background-color: {list_bg};
                color: {text};
                border: 1px solid {border};
                border-radius: 4px;
                padding: 4px;
                font-size: 13px;
            }}
            QListWidget#suiteList::item {{ padding: 5px 6px; border-radius: 3px; }}
            QListWidget#suiteList::item:selected {{ background-color: {list_sel}; color: {text}; }}
            QPushButton#opBtn, QPushButton#cancelBtn {{
                background-color: {btn_bg};
                color: {btn_fg};
                border: 1px solid {border};
                border-radius: 4px;
                padding: 4px 12px;
                min-height: 26px;
            }}
            QPushButton#opBtn:hover, QPushButton#cancelBtn:hover {{ background-color: {btn_hover}; }}
            QPushButton#opBtn:disabled {{ color: {hint}; }}
            QPushButton#okBtn {{
                background-color: #1976d2;
                color: #ffffff;
                border: none;
                border-radius: 4px;
                font-weight: 500;
            }}
            QPushButton#okBtn:hover {{ background-color: #1565c0; }}
        """)
