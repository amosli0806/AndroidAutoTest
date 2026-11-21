# views/template_dialog.py
"""步骤模板的两个对话框：保存（从选中步骤建模板）和插入（挑选模板）。

为什么不做独立的「模板管理页」：那等于再造一个用例列表，维护成本和注意力成本
都不划算。管理动作（重命名 / 删除）直接挂在插入对话框的列表右键上，
用到的时候顺手改，不用专门去一个页面。

样式说明：项目其它对话框的背景写在 `utils/theme.py` 里，按
`#InputDialog QFrame#container` 这样的 objectName 精确匹配。新对话框不在那份
注册表里，拿不到样式 —— 所以这里自带一套 `_palette()`，同时照顾明暗两种主题
（2026-11-20：第一版只给了浅色硬编码，容器没有背景色，整个对话框是透明的）。
"""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QComboBox, QFrame, QGraphicsDropShadowEffect,
                             QHBoxLayout, QLabel, QLineEdit, QListWidget,
                             QListWidgetItem, QPushButton, QTextBrowser,
                             QVBoxLayout, QWidget)

from utils.dialogs import AutoFreeDialog, QuestionDialog
from utils.icons import menu_icon
from utils.step_params import TYPE_NAMES
from utils.toast import show_toast

def _palette() -> dict:
    """按当前主题返回一套色值（明 / 暗）。"""
    try:
        from utils.settings import THEME_MODE_DARK, Settings
        dark = Settings.get_theme_mode() == THEME_MODE_DARK
    except Exception:
        dark = False
    if dark:
        return {
            "bg": "#2b2b2b", "border": "#3d3d3d", "text": "#e8e8e8",
            "muted": "#9aa4b2", "faint": "#7d8794",
            "input_bg": "#333333", "input_border": "#4a4a4a",
            "list_bg": "#2b2b2b", "side_bg": "#262626",
            "sel_bg": "#1d3a57", "sel_fg": "#cfe2f7", "hover_bg": "#363636",
            "preview_bg": "#242424", "btn_bg": "#3a3a3a", "btn_fg": "#d0d0d0",
            "divider": "#3a3a3a",
            "sb_handle": "#565656", "sb_handle_hover": "#6e6e6e",
        }
    return {
        "bg": "#ffffff", "border": "#d0d0d0", "text": "#1f2733",
        "muted": "#98a2b3", "faint": "#8fa3bc",
        "input_bg": "#f5f7fa", "input_border": "#e4e9f0",
        "list_bg": "#ffffff", "side_bg": "#fafbfd",
        "sel_bg": "#e8f1fd", "sel_fg": "#12406e", "hover_bg": "#f6f9fd",
        "preview_bg": "#fafbfd", "btn_bg": "#f2f4f7", "btn_fg": "#5b6472",
        "divider": "#edf1f7",
        "sb_handle": "#c8ced8", "sb_handle_hover": "#a8b2c1",
    }


def _scrollbar_qss(pal: dict) -> str:
    """6px 细滚动条。

    和 `StepListView._apply_scrollbar_style` / 项目树用同一套尺寸：Qt 默认的
    Fusion 滚动条有 14px 宽、带上下箭头，塞进 86px 高的预览框里又粗又抢眼。
    """
    h = pal["sb_handle"]
    hh = pal["sb_handle_hover"]
    return (
        "QScrollBar:vertical{width:6px;background:transparent;margin:0px;}"
        f"QScrollBar::handle:vertical{{background:{h};border-radius:3px;min-height:20px;}}"
        f"QScrollBar::handle:vertical:hover{{background:{hh};}}"
        "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0px;}"
        "QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{background:transparent;}"
        "QScrollBar:horizontal{height:6px;background:transparent;margin:0px;}"
        f"QScrollBar::handle:horizontal{{background:{h};border-radius:3px;min-width:20px;}}"
        f"QScrollBar::handle:horizontal:hover{{background:{hh};}}"
        "QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal{width:0px;}"
    )


def _type_label(step_type: str) -> str:
    return TYPE_NAMES.get(step_type, step_type or '未知')


def _head_line(index: int, step_type: str, name: str) -> str:
    """一行步骤的开头：序号 + 类型 + 名称。

    步骤名常常自带类型前缀（真实数据里是「点击 - 开始导航(步行导航)」），
    再补一遍类型就成了「点击 · 点击 - 开始导航」，所以名字以类型开头时不再加。
    """
    label = _type_label(step_type)
    name = (name or "").strip()
    if not name:
        return f"{index}. {label}"
    if name.startswith(label):
        return f"{index}. {name}"
    return f"{index}. {label} · {name}"


def _step_line(index: int, snap: dict) -> str:
    """把一条步骤快照压成一行预览：序号. 类型 · 名称  关键参数。"""
    bits = [_head_line(index, snap.get('type', ''), snap.get('name') or '')]
    params = snap.get('params') or {}
    # 只挑最有信息量的一个参数，避免一行太长
    for key in ('locationValue', 'text', 'packageName', 'package', 'ms', 'key'):
        val = params.get(key)
        if val not in (None, ''):
            bits.append(f"  {val}")
            break
    return "".join(bits)


class _TemplateRow(QWidget):
    """模板列表的一行：名称 + 副标题（步数 / 分组 / 使用次数）。"""

    def __init__(self, template: dict, pal: dict, parent=None):
        super().__init__(parent)
        self.setObjectName("TemplateRow")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 7, 10, 7)
        lay.setSpacing(2)

        name = QLabel(template.get("name") or "未命名")
        name.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {pal['text']};")
        lay.addWidget(name)

        steps = template.get("steps") or []
        group = template.get("group") or "未分组"
        used = int(template.get("used_count", 0) or 0)
        meta = f"{len(steps)} 个步骤 · {group}"
        if used:
            meta += f" · 用过 {used} 次"
        sub = QLabel(meta)
        sub.setStyleSheet(f"font-size: 11px; color: {pal['muted']};")
        lay.addWidget(sub)

        if steps:
            tail = "  →  ".join(
                (s.get("name") or _type_label(s.get("type", ""))) for s in steps[:3])
            if len(steps) > 3:
                tail += f"  …共 {len(steps)} 步"
            hint = QLabel(tail)
            hint.setStyleSheet(f"font-size: 10.5px; color: {pal['faint']};")
            lay.addWidget(hint)


class TemplatePickerDialog(AutoFreeDialog):
    """插入步骤模板。选中后 `self.result_template` 是模板 dict，`self.result_steps` 是快照列表。"""

    def __init__(self, parent, model, position_text: str = ""):
        super().__init__(parent)
        self._model = model
        self.result_template = None
        self.result_steps = []
        self._pal = _palette()
        pal = self._pal

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
        # 背景和圆角必须显式给：theme.py 只认它自己那几个 objectName，
        # 没有这条规则时整个对话框是全透明的（能看见下面的步骤卡片）
        container.setStyleSheet(
            f"QFrame#container{{background-color:{pal['bg']};"
            f"border:1px solid {pal['border']};border-radius:12px;}}")
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 0)
        shadow.setColor(QColor(0, 0, 0, 90))
        container.setGraphicsEffect(shadow)
        outer.addWidget(container)

        box = QVBoxLayout(container)
        box.setContentsMargins(22, 18, 22, 16)
        box.setSpacing(12)

        # ---- 标题行 ----
        head = QHBoxLayout()
        title = QLabel("插入步骤模板")
        title.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {pal['text']};")
        head.addWidget(title)
        head.addStretch(1)
        if position_text:
            pos = QLabel(position_text)
            pos.setStyleSheet(f"font-size: 11px; color: {pal['muted']};")
            head.addWidget(pos)
        box.addLayout(head)

        # ---- 搜索 ----
        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索模板名称…")
        self._search.setStyleSheet(
            f"QLineEdit{{background:{pal['input_bg']};border:1px solid {pal['input_border']};"
            f"border-radius:6px;padding:6px 10px;font-size:12px;color:{pal['text']};}}")
        self._search.textChanged.connect(self._reload)
        box.addWidget(self._search)

        # ---- 左分组 + 右列表 ----
        mid = QHBoxLayout()
        mid.setSpacing(10)

        self._group_list = QListWidget()
        self._group_list.setFixedWidth(118)
        self._group_list.setStyleSheet(
            f"QListWidget{{background:{pal['side_bg']};border:1px solid {pal['divider']};"
            f"border-radius:6px;font-size:12px;outline:none;color:{pal['muted']};}}"
            f"QListWidget::item{{padding:6px 8px;border-radius:4px;}}"
            f"QListWidget::item:selected{{background:{pal['sel_bg']};color:{pal['sel_fg']};}}"
            f"QListWidget::item:hover{{background:{pal['hover_bg']};}}"
            + _scrollbar_qss(pal))
        self._group_list.currentRowChanged.connect(lambda _r: self._reload())
        mid.addWidget(self._group_list)

        self._list = QListWidget()
        self._list.setStyleSheet(
            f"QListWidget{{background:{pal['list_bg']};border:1px solid {pal['divider']};"
            f"border-radius:6px;outline:none;}}"
            f"QListWidget::item{{margin:2px;border-radius:6px;color:{pal['text']};}}"
            f"QListWidget::item:selected{{background:{pal['sel_bg']};}}"
            f"QListWidget::item:hover{{background:{pal['hover_bg']};}}"
            + _scrollbar_qss(pal))
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
            f"QTextBrowser{{background:{pal['preview_bg']};border:1px solid {pal['divider']};"
            f"border-radius:6px;font-size:11.5px;color:{pal['text']};padding:6px 8px;}}"
            + _scrollbar_qss(pal))
        box.addWidget(self._preview)

        # ---- 底部按钮 ----
        foot = QHBoxLayout()
        foot.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setFixedWidth(84)
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setStyleSheet(
            f"QPushButton{{background:{pal['btn_bg']};border:none;border-radius:5px;"
            f"padding:6px 0;font-size:12px;color:{pal['btn_fg']};}}"
            f"QPushButton:hover{{background:{pal['hover_bg']};}}")
        cancel.clicked.connect(self.reject)
        foot.addWidget(cancel)

        self._ok = QPushButton("插入")
        self._ok.setFixedWidth(96)
        self._ok.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ok.setStyleSheet(
            "QPushButton{background:#1976d2;border:none;border-radius:5px;padding:6px 0;"
            "font-size:12px;color:#fff;}"
            "QPushButton:hover{background:#1565c0;}"
            "QPushButton:disabled{background:#c9d4e2;color:#f2f5f9;}")
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
            widget = _TemplateRow(t, self._pal)
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
        self._ok.setText("插入")
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
        self._pal = _palette()
        pal = self._pal

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
        container.setStyleSheet(
            f"QFrame#container{{background-color:{pal['bg']};"
            f"border:1px solid {pal['border']};border-radius:12px;}}")
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 0)
        shadow.setColor(QColor(0, 0, 0, 90))
        container.setGraphicsEffect(shadow)
        outer.addWidget(container)

        box = QVBoxLayout(container)
        box.setContentsMargins(22, 18, 22, 16)
        box.setSpacing(12)

        title = QLabel(f"存为步骤模板（{len(self._steps)} 个步骤）")
        title.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {pal['text']};")
        box.addWidget(title)

        sub = QLabel("模板存的是这些步骤的快照，之后在原用例里改动不会影响模板。")
        sub.setStyleSheet(f"font-size: 11px; color: {pal['muted']};")
        sub.setWordWrap(True)
        box.addWidget(sub)

        name_label = QLabel("模板名称")
        name_label.setStyleSheet(f"font-size: 12px; color: {pal['muted']};")
        box.addWidget(name_label)

        self._name = QLineEdit()
        self._name.setPlaceholderText("例如：启动应用并进入首页")
        self._name.setStyleSheet(
            f"QLineEdit{{background:{pal['input_bg']};border:1px solid {pal['input_border']};"
            f"border-radius:6px;padding:6px 10px;font-size:12px;color:{pal['text']};}}"
            f"QLineEdit:focus{{border-color:#1976d2;}}")
        box.addWidget(self._name)

        group_label = QLabel("分组（可留空）")
        group_label.setStyleSheet(f"font-size: 12px; color: {pal['muted']};")
        box.addWidget(group_label)

        self._group = QComboBox()
        self._group.setEditable(True)
        self._group.addItem("未分组")
        for g in (existing_groups or []):
            if g and g != "未分组":
                self._group.addItem(g)
        self._group.setStyleSheet(
            f"QComboBox{{background:{pal['input_bg']};border:1px solid {pal['input_border']};"
            f"border-radius:6px;padding:5px 10px;font-size:12px;color:{pal['text']};"
            f"min-height:20px;}}"
            f"QComboBox:hover{{border-color:{pal['sb_handle_hover']};}}"
            f"QComboBox:focus{{border-color:#1976d2;}}"
            # 不要自定义 ::drop-down —— 一旦写了 border/width，Fusion 就不再画那个
            # 小三角，而下拉箭头必须靠 image 资源补，项目里没有现成的，于是变成
            # 「右边空一块、看不见能点」（2026-11-20 实际踩到）。
            # 弹出列表：默认每个 item 只有 ~18px 高、零内边距，三项挤在一起像老控件；
            # 这里给足行高和内边距，圆角和选中色跟列表保持一致
            f"QComboBox QAbstractItemView{{background:{pal['bg']};"
            f"border:1px solid {pal['border']};border-radius:6px;padding:4px;"
            f"outline:none;font-size:12px;"
            f"selection-background-color:{pal['sel_bg']};"
            f"selection-color:{pal['sel_fg']};}}"
            f"QComboBox QAbstractItemView::item{{min-height:28px;padding:3px 10px;"
            f"border-radius:4px;}}"
            # ::item 一旦自定义，selection-background-color 就不再生效，
            # 必须显式写 :selected（否则只有文字变蓝、背景还是白的）
            f"QComboBox QAbstractItemView::item:selected{{background:{pal['sel_bg']};"
            f"color:{pal['sel_fg']};}}"
            f"QComboBox QAbstractItemView::item:hover{{background:{pal['hover_bg']};}}"
            + _scrollbar_qss(pal))
        box.addWidget(self._group)

        # 弹出层去掉右下那圈灰边：QComboBox 的 popup 是个 Raised 风格的 QFrame，
        # 外加系统 drop shadow，在浅色底上会拖出一道脏边（用户截图右侧/底部那两条）。
        # 注意要在 showPopup 之前设，show 之后再改 windowFlags 会让 popup 重新创建。
        popup = self._group.view().window()
        # 只**追加**标志，不要整体覆盖：popup 原本带 Popup / FramelessWindowHint 等，
        # 覆盖掉会冒出一个标题栏或抢焦点
        popup.setWindowFlags(popup.windowFlags() | Qt.WindowType.NoDropShadowWindowHint)
        popup.setFrameShape(QFrame.Shape.NoFrame)

        preview = QTextBrowser()
        preview.setStyleSheet(
            f"QTextBrowser{{background:{pal['preview_bg']};border:1px solid {pal['divider']};"
            f"border-radius:6px;font-size:11.5px;color:{pal['text']};padding:6px 8px;}}"
            + _scrollbar_qss(pal))
        lines = [_head_line(i, s.type, s.name or '')
                 for i, s in enumerate(self._steps, 1)]
        preview.setPlainText("\n".join(lines))
        box.addWidget(preview, 1)

        foot = QHBoxLayout()
        foot.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setFixedWidth(84)
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setStyleSheet(
            f"QPushButton{{background:{pal['btn_bg']};border:none;border-radius:5px;"
            f"padding:6px 0;font-size:12px;color:{pal['btn_fg']};}}"
            f"QPushButton:hover{{background:{pal['hover_bg']};}}")
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

        self._name.setFocus()

    def _on_save(self):
        name = self._name.text().strip()
        if not name:
            show_toast(message="请填写模板名称")
            self._name.setFocus()
            return
        self.result_name = name
        self.result_group = self._group.currentText().strip()
        self.accept()
