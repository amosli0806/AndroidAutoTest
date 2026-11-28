# views/dialogs/kb_command_palette.py
"""知识库命令面板（Ctrl+Shift+P）。

一个浮层：上面输入、下面结果，上下键选、回车执行、Esc 关掉。
结果分两段 —— **命令**（新建 / 导入 / 导出 / 标签视图）与**笔记**（全库模糊搜）。

为什么单独一个文件：它是自包含的交互组件（只管"给我命令和笔记，我选一个"），
和主视图的布局、编辑状态都没有耦合，塞在 knowledge_view.py 里只会让它更长。
"""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (QDialog, QFrame, QGraphicsDropShadowEffect, QHBoxLayout,
                             QLabel, QLineEdit, QListWidget, QListWidgetItem,
                             QVBoxLayout)

# 命令的 data 值（笔记项用 "note:<id>"）
CMD_PREFIX = "cmd:"


class KbCommandPalette(QDialog):
    # 命令被选中：返回命令 key
    command_triggered = pyqtSignal(str)
    # 笔记被选中：返回 note_id
    note_opened = pyqtSignal(str)

    def __init__(self, model, current_project_id: str = "", parent=None):
        super().__init__(parent)
        self.model = model
        self.current_project_id = current_project_id

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        self.setFixedWidth(560)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)

        self._box = QFrame()
        self._box.setObjectName("KbPalette")
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(26)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(0, 0, 0, 60))
        self._box.setGraphicsEffect(shadow)
        outer.addWidget(self._box)

        v = QVBoxLayout(self._box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        self.input = QLineEdit()
        self.input.setObjectName("KbPaletteInput")
        self.input.setPlaceholderText("输入命令或笔记标题…（↑↓ 选择，回车执行，Esc 关闭）")
        self.input.textChanged.connect(self._refresh)
        self.input.returnPressed.connect(self._activate_current)
        v.addWidget(self.input)

        self.list = QListWidget()
        self.list.setObjectName("KbPaletteList")
        self.list.setFixedHeight(340)
        self.list.itemActivated.connect(lambda _i: self._activate_current())
        v.addWidget(self.list)

        self._commands = [
            ("new-note", "新建条目"),
            ("new-project", "新建项目"),
            ("import", "导入文档"),
            ("export", "导出为 Markdown"),
            ("tags", "打开标签视图"),
            ("quick", "跳到速查条目"),
        ]
        self._apply_style()
        self._refresh("")

    # ------------------------------------------------------------------
    def _refresh(self, keyword: str):
        kw = (keyword or "").strip().lower()
        self.list.clear()

        # ---- 命令段 ----
        matched_cmds = [c for c in self._commands if not kw or kw in c[1].lower()]
        if matched_cmds:
            self._add_separator("命令")
            for key, label in matched_cmds:
                item = QListWidgetItem(label)
                item.setData(Qt.ItemDataRole.UserRole, CMD_PREFIX + key)
                self.list.addItem(item)

        # ---- 笔记段 ----
        hits = []
        if kw:
            # 有输入才搜笔记：一进来就列全库没有意义，还慢
            try:
                hits = self.model.search(kw)[:12]
            except Exception:
                hits = []
        elif self.current_project_id:
            notes = self.model.get_notes(project_id=self.current_project_id)[:8]
            hits = [type("H", (), {"note": n, "project_name": "", "snippet": ""})
                    for n in notes]
        if hits:
            self._add_separator("笔记")
            for hit in hits:
                note = hit.note
                text = note.title or "（无标题）"
                item = QListWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, "note:" + note.id)
                tip = getattr(hit, "snippet", "") or note.category
                item.setToolTip(tip)
                self.list.addItem(item)

        if self.list.count() == 0:
            item = QListWidgetItem("没有匹配的内容")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(item)
        # 高度跟着内容走：固定 340 时只有三五条会在下面留一大块空白
        rows = self.list.count()
        self.list.setFixedHeight(min(340, max(74, rows * 31 + 14)))
        # 默认落在第一条可选内容上
        for row in range(self.list.count()):
            if self.list.item(row).flags() & Qt.ItemFlag.ItemIsSelectable:
                self.list.setCurrentRow(row)
                break

    def _add_separator(self, text: str):
        item = QListWidgetItem(text)
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        font = QFont()
        font.setPointSize(max(7, font.pointSize() - 2))
        item.setFont(font)
        item.setForeground(QColor("#98A2B3"))
        self.list.addItem(item)

    def _activate_current(self):
        item = self.list.currentItem()
        if item is None:
            return
        data = item.data(Qt.ItemDataRole.UserRole) or ""
        if data.startswith(CMD_PREFIX):
            key = data[len(CMD_PREFIX):]
            self.accept()
            self.command_triggered.emit(key)
        elif data.startswith("note:"):
            note_id = data[len("note:"):]
            self.accept()
            self.note_opened.emit(note_id)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up):
            self._move(1 if event.key() == Qt.Key.Key_Down else -1)
            return
        super().keyPressEvent(event)

    def _move(self, step: int):
        """上下移动选中项，跳过「命令 / 笔记」这类分隔行。"""
        row = self.list.currentRow()
        total = self.list.count()
        for _ in range(total):
            row += step
            if row < 0 or row >= total:
                return
            item = self.list.item(row)
            if item.flags() & Qt.ItemFlag.ItemIsSelectable:
                self.list.setCurrentRow(row)
                return

    def showEvent(self, event):
        super().showEvent(event)
        self.input.setFocus()

    # ------------------------------------------------------------------
    def _apply_style(self):
        """深/浅色两套配色。命令面板是独立顶层窗口，不继承主页面 QSS，
        所以必须自己画 —— 否则深色主题下会弹出一块白板。"""
        try:
            from utils.settings import Settings, THEME_MODE_DARK
            dark = Settings.get_theme_mode() == THEME_MODE_DARK
        except Exception:
            dark = False
        if dark:
            box_bg, border = "#2D2D2D", "#555555"
            text, sub = "#EEEEEE", "#9AA0A6"
            hover, selected = "#3D3D3D", "#1E3A5F"
        else:
            box_bg, border = "#FFFFFF", "#D8DCE2"
            text, sub = "#1F2733", "#98A2B3"
            hover, selected = "#F0F3F7", "#E3EEFB"
        self.setStyleSheet(f"""
            #KbPalette {{
                background: {box_bg};
                border: 1px solid {border};
                border-radius: 10px;
            }}
            #KbPaletteInput {{
                background: transparent;
                border: none;
                border-bottom: 1px solid {border};
                color: {text};
                font-size: 15px;
                padding: 12px 14px;
            }}
            #KbPaletteList {{
                background: transparent;
                border: none;
                outline: none;
                color: {text};
                padding: 6px;
            }}
            #KbPaletteList::item {{
                padding: 7px 10px;
                border-radius: 5px;
                font-size: 13px;
            }}
            #KbPaletteList::item:hover {{
                background: {hover};
            }}
            #KbPaletteList::item:selected {{
                background: {selected};
                color: {text};
            }}
        """)

