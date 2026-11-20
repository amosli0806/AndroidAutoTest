# views/template_dialog.py
"""步骤模板的两个对话框：保存（从选中步骤建模板）和插入（挑选模板）。

为什么不做独立的「模板管理页」：那等于再造一个用例列表，维护成本和注意力成本
都不划算。管理动作（重命名 / 删除）直接挂在插入对话框的列表右键上，
用到的时候顺手改，不用专门去一个页面。
"""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QComboBox, QFrame, QGraphicsDropShadowEffect,
                             QHBoxLayout, QLabel, QLineEdit, QListWidget,
                             QListWidgetItem, QPushButton, QTextBrowser,
                             QVBoxLayout, QWidget)

from utils.dialogs import AutoFreeDialog, QuestionDialog
from utils.icons import menu_icon
from utils.toast import show_toast

# 步骤类型 → 中文名（和步骤卡片的类型名保持一致，用户看到的是同一套词）
_TYPE_NAMES = {
    'click': '点击', 'double_click': '双击', 'long_press': '长按', 'swipe': '滑动',
    'drag_drop': '拖拽', 'multi_swipe': '多点滑动', 'gesture_zoom': '手势缩放',
    'flick': '飞掠', 'gesture_seq': '复杂手势', 'physical_key': '物理按键',
    'screen_ctrl': '屏幕控制', 'app_mgr': '应用管理', 'screenshot': '截图',
    'input': '输入', 'wait': '等待', 'wait_for': '显式等待', 'assert': '断言',
    'voice': '语音播报',
}


def _type_label(step_type: str) -> str:
    return _TYPE_NAMES.get(step_type, step_type or '未知')


def _step_line(index: int, snap: dict) -> str:
    """把一条步骤快照压成一行预览：序号. 类型 · 名称  关键参数。"""
    label = _type_label(snap.get('type', ''))
    name = (snap.get('name') or '').strip()
    params = snap.get('params') or {}
    bits = [f"{index}. {label}"]
    # 步骤名常常就是类型名（「等待」「点击」），重复显示读起来很傻
    if name and name != label:
        bits.append(f"· {name}")
    # 只挑最有信息量的一个参数，避免一行太长
    for key in ('locationValue', 'text', 'packageName', 'package', 'ms', 'key'):
        val = params.get(key)
        if val not in (None, ''):
            bits.append(f"  {val}")
            break
    return "".join(bits)


class _TemplateRow(QWidget):
    """模板列表的一行：名称 + 副标题（步数 / 分组 / 使用次数）。"""

    def __init__(self, template: dict, parent=None):
        super().__init__(parent)
        self.setObjectName("TemplateRow")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 7, 10, 7)
        lay.setSpacing(2)

        name = QLabel(template.get("name") or "未命名")
        name.setStyleSheet("font-size: 13px; font-weight: 600; color: #1f2733;")
        lay.addWidget(name)

        steps = template.get("steps") or []
        group = template.get("group") or "未分组"
        used = int(template.get("used_count", 0) or 0)
        meta = f"{len(steps)} 个步骤 · {group}"
        if used:
            meta += f" · 用过 {used} 次"
        sub = QLabel(meta)
        sub.setStyleSheet("font-size: 11px; color: #98a2b3;")
        lay.addWidget(sub)

        first = steps[0] if steps else None
        if first:
            tail = "  →  ".join(
                (s.get("name") or _type_label(s.get("type", ""))) for s in steps[:3])
            if len(steps) > 3:
                tail += f"  …共 {len(steps)} 步"
            hint = QLabel(tail)
            hint.setStyleSheet("font-size: 10.5px; color: #8fa3bc;")
            lay.addWidget(hint)


class TemplatePickerDialog(AutoFreeDialog):
    """插入步骤模板。选中后 `self.result_template` 是模板 dict，`self.result_steps` 是快照列表。"""

    def __init__(self, parent, model, position_text: str = ""):
        super().__init__(parent)
        self._model = model
        self.result_template = None
        self.result_steps = []

        self.setObjectName("TemplatePickerDialog")
        self.setWindowTitle("插入步骤模板")
        self.setModal(True)
        self.setFixedSize(640, 500)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(0)

        container = QFrame()
        container.setObjectName("container")
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 0)
        shadow.setColor(QColor(0, 0, 0, 80))
        container.setGraphicsEffect(shadow)
        outer.addWidget(container)

        box = QVBoxLayout(container)
        box.setContentsMargins(22, 18, 22, 16)
        box.setSpacing(12)

        # ---- 标题行 ----
        head = QHBoxLayout()
        title = QLabel("插入步骤模板")
        title.setStyleSheet("font-size: 16px; font-weight: bold;")
        head.addWidget(title)
        head.addStretch(1)
        if position_text:
            pos = QLabel(position_text)
            pos.setStyleSheet("font-size: 11px; color: #98a2b3;")
            head.addWidget(pos)
        box.addLayout(head)

        # ---- 搜索 ----
        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索模板名称…")
        self._search.setStyleSheet(
            "QLineEdit{background:#f5f7fa;border:1px solid #e4e9f0;border-radius:6px;"
            "padding:6px 10px;font-size:12px;}")
        self._search.textChanged.connect(self._reload)
        box.addWidget(self._search)

        # ---- 左分组 + 右列表 ----
        mid = QHBoxLayout()
        mid.setSpacing(10)

        self._group_list = QListWidget()
        self._group_list.setFixedWidth(118)
        self._group_list.setStyleSheet(
            "QListWidget{background:#fafbfd;border:1px solid #edf1f7;border-radius:6px;"
            "font-size:12px;outline:none;}"
            "QListWidget::item{padding:6px 8px;border-radius:4px;color:#5b6472;}"
            "QListWidget::item:selected{background:#e8f1fd;color:#12406e;}"
            "QListWidget::item:hover{background:#f1f5fa;}")
        self._group_list.currentRowChanged.connect(lambda _r: self._reload())
        mid.addWidget(self._group_list)

        self._list = QListWidget()
        self._list.setStyleSheet(
            "QListWidget{background:#ffffff;border:1px solid #edf1f7;border-radius:6px;"
            "outline:none;}"
            "QListWidget::item{margin:2px;border-radius:6px;}"
            "QListWidget::item:selected{background:#e8f1fd;}"
            "QListWidget::item:hover{background:#f6f9fd;}")
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_row_menu)
        self._list.itemDoubleClicked.connect(lambda _i: self._accept_insert())
        self._list.currentRowChanged.connect(lambda _r: self._update_preview())
        mid.addWidget(self._list, 1)
        box.addLayout(mid, 1)

        # ---- 预览 ----
        self._preview = QTextBrowser()
        self._preview.setFixedHeight(86)
        self._preview.setStyleSheet(
            "QTextBrowser{background:#fafbfd;border:1px solid #edf1f7;border-radius:6px;"
            "font-size:11.5px;color:#3d4654;padding:6px 8px;}")
        box.addWidget(self._preview)

        # ---- 底部按钮 ----
        foot = QHBoxLayout()
        foot.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setFixedWidth(84)
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setStyleSheet(
            "QPushButton{background:#f2f4f7;border:none;border-radius:5px;padding:6px 0;"
            "font-size:12px;color:#5b6472;}"
            "QPushButton:hover{background:#e8ebf0;}")
        cancel.clicked.connect(self.reject)
        foot.addWidget(cancel)

        self._ok = QPushButton("插入")
        self._ok.setFixedWidth(120)
        self._ok.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ok.setStyleSheet(
            "QPushButton{background:#1976d2;border:none;border-radius:5px;padding:6px 0;"
            "font-size:12px;color:#fff;}"
            "QPushButton:hover{background:#1565c0;}"
            "QPushButton:disabled{background:#c9d4e2;}")
        self._ok.clicked.connect(self._accept_insert)
        foot.addWidget(self._ok)
        box.addLayout(foot)

        self._reload_groups()
        self._reload()

    # ---------- 数据加载 ----------
    def _reload_groups(self):
        self._group_list.blockSignals(True)
        self._group_list.clear()
        item = QListWidgetItem("全部分组")
        self._group_list.addItem(item)
        groups = self._model.groups()
        for g, n in sorted(groups.items(), key=lambda kv: -kv[1]):
            self._group_list.addItem(QListWidgetItem(f"{g} · {n}"))
        self._group_list.setCurrentRow(0)
        self._group_list.blockSignals(False)

    def _current_group(self) -> str:
        row = self._group_list.currentRow()
        if row <= 0:
            return ""
        text = self._group_list.currentItem().text()
        return text.rsplit(" · ", 1)[0]

    def _reload(self):
        keyword = self._search.text().strip().lower()
        group = self._current_group()
        self._list.clear()

        rows = []
        for t in self._model.all():
            if group and (t.get("group") or "未分组") != group:
                continue
            if keyword and keyword not in (t.get("name") or "").lower():
                continue
            rows.append(t)

        for t in rows:
            item = QListWidgetItem()
            widget = _TemplateRow(t)
            item.setSizeHint(widget.sizeHint())
            item.setData(Qt.ItemDataRole.UserRole, t.get("id"))
            self._list.addItem(item)
            self._list.setItemWidget(item, widget)

        if not rows:
            hint = ("还没有模板。在步骤列表里选中几个步骤，右键 →「存为步骤模板…」"
                    if not self._model.all() else "没有匹配的模板")
            item = QListWidgetItem(hint)
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self._list.addItem(item)

        if rows:
            self._list.setCurrentRow(0)
        self._update_preview()

    def _selected_template(self):
        item = self._list.currentItem()
        if item is None:
            return None
        tid = item.data(Qt.ItemDataRole.UserRole)
        return self._model.get(tid) if tid is not None else None

    def _update_preview(self):
        tpl = self._selected_template()
        if tpl is None:
            self._preview.setPlainText("")
            self._ok.setText("插入")
            self._ok.setEnabled(False)
            return
        steps = tpl.get("steps") or []
        lines = [_step_line(i, s) for i, s in enumerate(steps, 1)]
        self._preview.setPlainText("\n".join(lines))
        self._ok.setText(f"插入 {len(steps)} 个步骤")
        self._ok.setEnabled(bool(steps))

    # ---------- 交互 ----------
    def _accept_insert(self):
        tpl = self._selected_template()
        if tpl is None:
            return
        steps = self._model.steps_to_snapshot_list(tpl)
        if not steps:
            show_toast(message="这个模板里没有步骤")
            return
        self._model.touch(tpl.get("id"))
        self.result_template = tpl
        self.result_steps = steps
        self.accept()

    def _on_row_menu(self, pos):
        item = self._list.itemAt(pos)
        if item is None:
            return
        tid = item.data(Qt.ItemDataRole.UserRole)
        tpl = self._model.get(tid) if tid is not None else None
        if tpl is None:
            return

        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        act_rename = menu.addAction(menu_icon('fa6s.pen', '#1976d2'), "重命名…")
        act_delete = menu.addAction(menu_icon('fa6s.trash-can', '#e74c3c'), "删除")
        chosen = menu.exec(self._list.viewport().mapToGlobal(pos))
        if chosen is act_rename:
            from utils.dialogs import InputDialog
            # get_text 返回 (文本, 是否确认)，不是裸字符串 —— 直接当字符串用会拿到元组
            new_name, confirmed = InputDialog.get_text(
                self, "重命名模板", "模板名称：",
                default_text=tpl.get("name") or "")
            if confirmed and new_name:
                ok, msg = self._model.rename(tid, new_name)
                show_toast(message=msg)
                if ok:
                    self._reload_groups()
                    self._reload()
        elif chosen is act_delete:
            dlg = QuestionDialog(self, "删除模板",
                                 f"确定删除模板「{tpl.get('name')}」？")
            if dlg.exec() == QuestionDialog.DialogCode.Accepted:
                ok, msg = self._model.delete(tid)
                show_toast(message=msg)
                self._reload_groups()
                self._reload()


class SaveTemplateDialog(AutoFreeDialog):
    """把选中步骤存成模板：名称 + 分组 + 预览。"""

    def __init__(self, parent, steps, existing_groups=None):
        super().__init__(parent)
        self._steps = steps or []
        self.result_name = ""
        self.result_group = ""

        self.setObjectName("SaveTemplateDialog")
        self.setWindowTitle("存为步骤模板")
        self.setModal(True)
        self.setFixedSize(520, 400)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(0)

        container = QFrame()
        container.setObjectName("container")
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 0)
        shadow.setColor(QColor(0, 0, 0, 80))
        container.setGraphicsEffect(shadow)
        outer.addWidget(container)

        box = QVBoxLayout(container)
        box.setContentsMargins(22, 18, 22, 16)
        box.setSpacing(12)

        title = QLabel(f"存为步骤模板（{len(self._steps)} 个步骤）")
        title.setStyleSheet("font-size: 16px; font-weight: bold;")
        box.addWidget(title)

        sub = QLabel("模板存的是这些步骤的快照，之后在原用例里改动不会影响模板。")
        sub.setStyleSheet("font-size: 11px; color: #98a2b3;")
        sub.setWordWrap(True)
        box.addWidget(sub)

        name_label = QLabel("模板名称")
        name_label.setStyleSheet("font-size: 12px; color: #5b6472;")
        box.addWidget(name_label)

        self._name = QLineEdit()
        self._name.setPlaceholderText("例如：启动应用并进入首页")
        self._name.setStyleSheet(
            "QLineEdit{background:#f5f7fa;border:1px solid #e4e9f0;border-radius:6px;"
            "padding:6px 10px;font-size:12px;}")
        box.addWidget(self._name)

        group_label = QLabel("分组（可留空）")
        group_label.setStyleSheet("font-size: 12px; color: #5b6472;")
        box.addWidget(group_label)

        self._group = QComboBox()
        self._group.setEditable(True)
        self._group.addItem("未分组")
        for g in (existing_groups or []):
            if g and g != "未分组":
                self._group.addItem(g)
        self._group.setStyleSheet(
            "QComboBox{background:#f5f7fa;border:1px solid #e4e9f0;border-radius:6px;"
            "padding:5px 8px;font-size:12px;}")
        box.addWidget(self._group)

        preview = QTextBrowser()
        preview.setStyleSheet(
            "QTextBrowser{background:#fafbfd;border:1px solid #edf1f7;border-radius:6px;"
            "font-size:11.5px;color:#3d4654;padding:6px 8px;}")
        lines = []
        for i, s in enumerate(self._steps, 1):
            label = _type_label(s.type)
            name = (s.name or "").strip()
            lines.append(f"{i}. {label}" + (f" · {name}" if name and name != label else ""))
        preview.setPlainText("\n".join(lines))
        box.addWidget(preview, 1)

        foot = QHBoxLayout()
        foot.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setFixedWidth(84)
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setStyleSheet(
            "QPushButton{background:#f2f4f7;border:none;border-radius:5px;padding:6px 0;"
            "font-size:12px;color:#5b6472;}"
            "QPushButton:hover{background:#e8ebf0;}")
        cancel.clicked.connect(self.reject)
        foot.addWidget(cancel)

        ok = QPushButton("保存")
        ok.setFixedWidth(96)
        ok.setCursor(Qt.CursorShape.PointingHandCursor)
        ok.setStyleSheet(
            "QPushButton{background:#1976d2;border:none;border-radius:5px;padding:6px 0;"
            "font-size:12px;color:#fff;}"
            "QPushButton:hover{background:#1565c0;}")
        ok.clicked.connect(self._on_save)
        foot.addWidget(ok)
        box.addLayout(foot)

    def _on_save(self):
        name = self._name.text().strip()
        if not name:
            show_toast(message="请填写模板名称")
            self._name.setFocus()
            return
        self.result_name = name
        self.result_group = self._group.currentText().strip()
        self.accept()
