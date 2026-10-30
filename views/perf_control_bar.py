# views/perf_control_bar.py
"""性能页顶部「单行命令栏」。

设计目标（2026-10-30 用户要求）：控制区**恒定一行**，不允许折行；
窗口变窄或系统缩放变大时，按优先级把低频参数收进「更多」弹层。

为什么不折行：2026-10-08 曾把工具栏改成 FlowLayout 折行，拖动窗口时
行高忽高忽低、按钮还会跑到左边，被整体回滚。这里行高恒定，
装不下就把控件挪走，不做换行。

实现要点：
- 每个控件放在一个「槽」(slot) 里，槽永远留在行内；控件被收起时只是
  从槽里搬进弹层，槽隐藏后自动让位，**不会打乱同行其它控件的顺序**。
- 收起的判断按 `priority` 从小到大（数字小的先收），必显项不参与。
- 只在「收起集合发生变化」时才搬控件，避免拖动窗口时反复重排闪烁。
"""

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QMenu, QWidgetAction,
    QSizePolicy,
)
from PyQt6.QtCore import Qt, QSize, QTimer


class ChipBar(QWidget):
    """单行命令栏：装不下就把低频控件收进「更多」。"""

    SPACING = 4

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PerfChipBar")
        # ⚠ 这里不能写 setStyleSheet("background: transparent;")：
        # 不带选择器的规则会作用到这个控件本身，并**连带影响它的 QToolTip**——
        # Qt 会把控件（及其祖先）样式表里的背景套到提示气泡上，transparent
        # 就是「气泡背景透明 → 合成成纯黑」，亮色主题下提示直接看不见
        # （2026-10-30 实测复现）。普通 QWidget 本来就不画背景，不需要这句。

        self._items = []          # [{key, chip, slot, lay, prio, eligible}]
        self._parked = set()      # 当前被收进弹层的 key
        self._applying = False
        self._dirty = True        # 参与资格变过：即便收起集合没变也要重刷显隐

        # 行高固定：收起不同组合时（下拉 30 / 数字框 34）不至于让面板高度来回跳
        self.setFixedHeight(34)

        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(self.SPACING)

        # 「更多」弹层：被收起的控件临时挂到这里
        self.panel = QWidget()
        self.panel.setObjectName("PerfPopupPanel")
        self.panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._panel_layout = QVBoxLayout(self.panel)
        self._panel_layout.setContentsMargins(12, 10, 12, 10)
        self._panel_layout.setSpacing(8)
        self._panel_action = QWidgetAction(self)
        self._panel_action.setDefaultWidget(self.panel)
        self._menu = QMenu(self)
        self._menu.addAction(self._panel_action)

        self.more_btn = QPushButton("更多", self)
        self.more_btn.setToolTip("当前窗口放不下的参数收在这里")
        self.more_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.more_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.more_btn.setFixedHeight(28)
        self.more_btn.clicked.connect(self._show_menu)
        self.more_btn.hide()

    # ------------------------------------------------------------------
    # 构建
    # ------------------------------------------------------------------
    def add_chip(self, key, chip, priority, eligible=True):
        """往行内加一个控件。priority 越小越先被收起。"""
        slot = QWidget(self)
        # 同上：不要在这里铺透明背景（会连带把提示气泡变透明/变黑）
        # 槽必须「宽度=内容宽」：否则行内还有剩余空间时，Qt 会把多余的宽度
        # 摊到这些槽上（槽内部的弹簧再把内容顶到左边），看起来就是一串 ~30px
        # 的空隙、整行左对齐不了（2026-10-30 用户反馈"间距有点大"）
        slot.setSizePolicy(QSizePolicy.Policy.Fixed,
                           QSizePolicy.Policy.Preferred)
        lay = QHBoxLayout(slot)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(chip)
        self._row.addWidget(slot)
        self._items.append({
            "key": key, "chip": chip, "slot": slot, "lay": lay,
            "prio": priority, "eligible": bool(eligible),
        })

    def finalize(self):
        """所有 chip 加完后调用：把「更多」按钮和弹簧放到行尾。"""
        self._row.addWidget(self.more_btn)
        self._row.addStretch()

    # ------------------------------------------------------------------
    # 显隐 / 收起
    # ------------------------------------------------------------------
    def set_eligibility(self, mapping):
        """批量设置「这一项当前是否参与排布」（如场景参数只在场景化模式出现）。

        一次性改完再重排，避免逐项设置时连触发多次重排。
        """
        changed = False
        for key, ok in mapping.items():
            for it in self._items:
                if it["key"] == key and it["eligible"] != bool(ok):
                    it["eligible"] = bool(ok)
                    changed = True
        if changed:
            # 收起集合可能不变（比如把"场景参数"整组关掉，本来就没被收起），
            # 但显隐必须跟着刷新，所以打脏标记强制走一次 _apply
            self._dirty = True
            self.relayout()

    def chip(self, key):
        for it in self._items:
            if it["key"] == key:
                return it["chip"]
        return None

    def minimumSizeHint(self):
        """最小宽度只按「最宽的一项 + 更多按钮」算。

        不能把全部项加起来：那样页面最小宽度会被顶到 1100 左右，窗口再也不能
        变窄，`relayout` 永远看不到"放不下"，收起逻辑就成了摆设。
        报一个宽松的下限，页面可以继续缩，缩进来之后再按优先级收。
        """
        widest = 0
        for it in self._items:
            chip = it["chip"]
            widest = max(widest, chip.minimumSizeHint().width(),
                         chip.minimumWidth())
        return QSize(widest + self.more_btn.sizeHint().width() + self.SPACING * 2,
                     30)

    def relayout(self):
        """按当前宽度决定哪些项要收进「更多」。"""
        if self._applying or not self._items:
            return
        avail = self.width()
        if avail <= 0:
            return
        parked = set()
        for it in sorted(self._items, key=lambda i: i["prio"]):
            if not it["eligible"]:
                continue
            if self._need(parked) <= avail:
                break
            parked.add(it["key"])
        self._apply(parked)

    @staticmethod
    def _chip_width(it):
        """单项占宽：取控件自身的 sizeHint 与 minimumWidth 的较大值。

        ⚠ 不能用 `it["slot"].sizeHint()`：控件被收进弹层时已从槽里搬走，
        槽是空的、sizeHint 近似 0，于是"假设把某项展开需要多少宽"会算小，
        收起集合在 空↔满 之间无限来回（实测每帧翻一次）。
        """
        chip = it["chip"]
        # 定宽控件（如只留图标的刷新按钮）以固定值为准：QPushButton 的 sizeHint
        # 会把 QSS 的左右 padding 算进去（48px），可它实际只有 32px
        if chip.minimumWidth() == chip.maximumWidth() > 0:
            return chip.minimumWidth()
        return max(chip.sizeHint().width(), chip.minimumWidth())

    def _need(self, parked):
        """当前可见项排成一行需要的宽度。"""
        total, n = 0, 0
        for it in self._items:
            if not it["eligible"] or it["key"] in parked:
                continue
            total += self._chip_width(it)
            n += 1
        if parked:
            total += max(self.more_btn.sizeHint().width(),
                         self.more_btn.minimumWidth())
            n += 1
        return total + self.SPACING * max(0, n - 1)

    def _apply(self, parked):
        if parked == self._parked and not self._dirty:
            return
        self._applying = True
        try:
            for it in self._items:
                if it["key"] in self._parked:
                    self._unpark(it)
            self._parked = set(parked)
            for it in self._items:
                if it["key"] in parked:
                    self._park(it)
                elif not it["eligible"]:
                    it["slot"].hide()
                else:
                    it["slot"].show()
            self.more_btn.setVisible(bool(parked))
            self._dirty = False
        finally:
            self._applying = False
        # 搬控件本身会改变行宽（父布局可能随即重新分配宽度），而那一刻
        # `_applying` 为真、resizeEvent 里的重排会被挡掉，容易停在"宽度已经变了
        # 但收起集合还是旧值"的状态（实测缩窄时全部控件又挤回来、横向溢出）。
        # 等这一轮布局跑完再校验一次；已收敛时 _apply 会直接返回，不会无限循环。
        QTimer.singleShot(0, self.relayout)

    def _park(self, it):
        it["lay"].removeWidget(it["chip"])
        it["chip"].setParent(self.panel)
        self._panel_layout.addWidget(it["chip"])
        it["chip"].show()
        it["slot"].hide()

    def _unpark(self, it):
        self._panel_layout.removeWidget(it["chip"])
        it["chip"].setParent(it["slot"])
        it["lay"].addWidget(it["chip"])
        it["chip"].show()
        it["slot"].show()

    def _show_menu(self):
        self._menu.popup(
            self.more_btn.mapToGlobal(self.more_btn.rect().bottomLeft()))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.relayout()
