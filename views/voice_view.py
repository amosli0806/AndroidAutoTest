# views/voice_view.py
"""语音播报页：把写好的文案用电脑扬声器读出来，供车机语音助手拾取。

能力定位：声学耦合 —— 电脑播放，车机麦克风拾取后交给它自己的语音助手。
所以这个页面要解决的核心问题是「声音从哪个扬声器出去」以及「每句之间等多久」，
而不是跟车机做协议对接。

三栏结构（观感与「自动化编辑」「自动化执行」两页一致）：
    左  语音管理  —— 独立的「语音用例」列表，可新建/删除/导入/导出
    中  播报文案  —— 选中用例的文案列表（增删改 + 单条播报）
    右  执行      —— 勾选要播的语音用例、全选/取消全选、语速、循环、执行选中、停止

重要：**语音用例与「自动化编辑」里的用例是两套独立的东西**。
那边是 App 操作序列（点击/输入/断言…），这里是纯播报脚本，互不影响。
（自动化编辑页的 `voice` 步骤类型仍然保留，用于把一句播报插进 App 操作用例，
那是另一条路径。）

两个实现要点：
  1. 「执行选中」跑在后台线程，用异步播报 + 分片轮询，这样「停止」能立刻
     打断当前这句。SAPI 的 voice 对象按线程持有，打断必须由播放线程自己调
     stop()，主线程调是打不断的。
  2. SAPI 是 COM 组件，播放线程首次调用时会自行 CoInitialize。
"""
import json
import os
import time

from PyQt6.QtCore import QRectF, QThread, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QDoubleSpinBox, QFileDialog, QFrame,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMenu, QPushButton, QScrollArea, QSpinBox, QSplitter, QStyle,
    QStyledItemDelegate, QStyleOptionViewItem, QToolButton,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)
import qtawesome as qta

from models.voice_model import DEFAULT_DELAY, DEFAULT_WAKE_WORD, VoiceModel
from services.voice_service import VoiceError
from utils import tree_state
# InputDialog 是项目自定义的圆角卡片式对话框，与编辑页"创建项目"用同一个
from utils.dialogs import ConfirmDeleteDialog, ErrorDialog, InputDialog
from utils.theme import ThemeMode

# 导入/导出语音文案的默认文件名
PHRASES_FILE_NAME = "voice_phrases.json"


class _PlaybackWorker(QThread):
    """按顺序播报若干条文案，可循环、可被 stop() 打断。

    items 里每条是 (case_id, phrase_index, text, delay)，
    带上来源是为了让界面能高亮"正在播的是哪一条"。
    """

    progress = pyqtSignal(str, int)       # (正在播的用例, 第几条)
    round_changed = pyqtSignal(int, int)  # (当前第几轮, 总轮数)
    done = pyqtSignal(bool, str)          # (是否正常播完, 错误信息)

    def __init__(self, service, items, loop_count=1, parent=None):
        super().__init__(parent)
        self.service = service
        self.items = list(items)          # 先做快照
        self.loop_count = max(1, int(loop_count or 1))
        self._stop = False
        self._interrupted = False

    def stop(self):
        self._stop = True

    def run(self):
        error = ""
        try:
            for round_index in range(self.loop_count):
                if self._stop:
                    self._interrupted = True
                    break
                self.round_changed.emit(round_index + 1, self.loop_count)
                for case_id, phrase_index, text, delay in self.items:
                    if self._stop:
                        self._interrupted = True
                        break
                    self.progress.emit(case_id, phrase_index)
                    self.service.speak_async(text)

                    # 等这句播完；分片轮询是为了能及时响应「停止」
                    deadline = time.time() + 30 + len(text) * 0.6
                    while True:
                        if self._stop:
                            self.service.stop()
                            self._interrupted = True
                            break
                        if self.service.wait_done(100):
                            break
                        if time.time() > deadline:
                            error = f"「{text[:12]}」播报超时"
                            self._interrupted = True
                            break
                    if self._interrupted:
                        break

                    # 播后等待：留给车机语音助手处理时间
                    waited = 0.0
                    while waited < delay:
                        if self._stop:
                            self._interrupted = True
                            break
                        time.sleep(0.05)
                        waited += 0.05
                    if self._interrupted:
                        break
                if self._interrupted:
                    break
        except VoiceError as e:
            error = str(e)
        except Exception as e:
            error = f"{type(e).__name__}: {str(e)[:60]}"
        self.progress.emit("", -1)
        self.done.emit(not self._interrupted and not error, error)


class _BorderedTreeItemDelegate(QStyledItemDelegate):
    """给 QTreeWidget 节点上的复选框叠一圈明显的边框。

    为什么不能复用 BorderedCheckBox：那是给真正的 QCheckBox 控件用的，
    通过 subElementRect(SE_CheckBoxIndicator) 拿位置。而树节点上的复选框
    不是独立控件 —— 它由 style 在绘制整行 item 时顺手画上去，拿不到控件引用。

    这里用 delegate 先让基类正常绘制（保留勾号 / 减号 / 部分选中的方块），
    再用 SE_ItemViewItemCheckIndicator 拿到复选框矩形，叠一圈边框。
    """

    def __init__(self, border_color, selected_border_color, parent=None):
        super().__init__(parent)
        self._border_color = QColor(border_color)
        self._selected_border_color = QColor(selected_border_color)

    def set_border_colors(self, border_color, selected_border_color):
        self._border_color = QColor(border_color)
        self._selected_border_color = QColor(selected_border_color)

    def paint(self, painter, option, index):
        super().paint(painter, option, index)

        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)

        # 只处理带 checkbox 的 item（分组 / 用例才有，纯文字行没有）。
        # 必须用这里 initStyleOption 出来的 opt 判断：传进来的 option 里没有
        # HasCheckIndicator —— 这个标志是 QStyledItemDelegate 在自己的 paint
        # 内部对 option 副本 initStyleOption 之后才置上的。靠传进来的 option
        # 判断会恒为 False，边框永远画不出来（这就是改之前那圈边框失效的原因）。
        if not (opt.features
                & QStyleOptionViewItem.ViewItemFeature.HasCheckIndicator):
            return

        style = (option.widget.style() if option.widget is not None
                 else QApplication.style())
        rect = style.subElementRect(
            QStyle.SubElement.SE_ItemViewItemCheckIndicator,
            opt, option.widget)
        if not (rect.isValid() and rect.width() > 0):
            return

        if opt.state & QStyle.StateFlag.State_Selected:
            pen_color = self._selected_border_color
        else:
            pen_color = self._border_color

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(pen_color, 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        # 半像素对齐：rect 的边界落在像素中心上，1px 的边框才会正好压在
        # 原生方框那一圈像素上（14x14），和里面的方块同心。
        # 早先写的是 rect.adjusted(0, 0, -1, -1)：边框比原生方框小 1px 且偏左上，
        # 加上抗锯齿在左上溢出 1px，视觉上外框就比方块偏左上，方块看着像「偏右下角」。
        painter.drawRect(QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5))
        painter.restore()


class _ClickAnywhereCheckTree(QTreeWidget):
    """整行可勾选的勾选树：点行内任意位置都能勾选/取消，不必对准那个小方框。

    为什么不直接连 clicked 信号去改状态：点在复选框本身时 Qt 自己已经切过一次
    （QStyledItemDelegate::editorEvent 负责 indicator 的点击），再切一次就抵掉了。
    所以这里在 mousePressEvent 里按落点分流，保证任何位置都恰好切换一次：
      - 分支列（缩进 + 展开箭头）-> 交给基类，否则点箭头会把展开/收起吃掉
      - 复选框本身              -> 也交给基类，走 Qt 那一次切换
      - 其余位置（图标/文案/右侧空白）-> 自己切换并消费事件
    部分选中（分组）按 Qt 的老规矩回到「全选」。
    """

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        pos = event.position().toPoint()
        index = self.indexAt(pos)
        item = self.itemFromIndex(index) if index.isValid() else None
        if item is None:
            return super().mousePressEvent(event)

        # 分支列不碰：缩进列的宽度 = indentation * (深度 + 1)
        depth, parent = 0, item.parent()
        while parent is not None:
            depth += 1
            parent = parent.parent()
        if pos.x() < self.indentation() * (depth + 1):
            return super().mousePressEvent(event)

        if self._hit_check_indicator(index, pos):
            return super().mousePressEvent(event)

        state = item.checkState(0)
        item.setCheckState(
            0,
            Qt.CheckState.Unchecked if state == Qt.CheckState.Checked
            else Qt.CheckState.Checked)
        event.accept()

    def _hit_check_indicator(self, index, pos) -> bool:
        """落点是否在复选框矩形内（delegate 拿到的 rect 就是 Qt 画 indicator 的位置）"""
        opt = QStyleOptionViewItem()
        self.itemDelegate().initStyleOption(opt, index)
        opt.rect = self.visualRect(index)
        rect = self.style().subElementRect(
            QStyle.SubElement.SE_ItemViewItemCheckIndicator, opt, self)
        return rect.isValid() and rect.contains(pos)


class _PhraseRow(QFrame):
    """一条文案的编辑行（按下标回调，因为文案是按下标存的）。"""

    play_requested = pyqtSignal(int)
    remove_requested = pyqtSignal(int)
    text_changed = pyqtSignal(int, str)
    delay_changed = pyqtSignal(int, float)

    def __init__(self, index, phrase, parent=None):
        super().__init__(parent)
        self.index = index
        self.setObjectName("PhraseRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 5, 8, 5)
        layout.setSpacing(8)

        self.no_label = QLabel(str(index + 1))
        self.no_label.setObjectName("PhraseNo")
        self.no_label.setFixedWidth(26)
        self.no_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.no_label)

        self.text_edit = QLineEdit(phrase.text)
        self.text_edit.setObjectName("PhraseText")
        self.text_edit.setPlaceholderText("要播报的文案，例如：你好，虫师")
        self.text_edit.editingFinished.connect(self._on_text_edited)
        layout.addWidget(self.text_edit, 1)

        layout.addWidget(QLabel("播后等待"))
        self.delay_spin = QDoubleSpinBox()
        self.delay_spin.setObjectName("PhraseDelay")
        self.delay_spin.setRange(0.0, 60.0)
        self.delay_spin.setSingleStep(0.5)
        self.delay_spin.setDecimals(1)
        self.delay_spin.setSuffix(" 秒")
        self.delay_spin.setFixedWidth(90)
        self.delay_spin.setValue(float(phrase.delay or 0))
        self.delay_spin.valueChanged.connect(
            lambda v: self.delay_changed.emit(self.index, float(v)))
        layout.addWidget(self.delay_spin)

        # 行内按钮改成无边框图标按钮：不占地方、不抢视觉，
        # 图标颜色由 apply_theme 按当前主题刷新（qta 图标是位图，QSS 管不到颜色）
        self.play_btn = QToolButton()
        self.play_btn.setObjectName("PhrasePlayBtn")
        self.play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.play_btn.setFixedSize(26, 26)
        self.play_btn.setAutoRaise(True)
        self.play_btn.clicked.connect(lambda: self.play_requested.emit(self.index))
        layout.addWidget(self.play_btn)

        self.del_btn = QToolButton()
        self.del_btn.setObjectName("PhraseDelBtn")
        self.del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.del_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.del_btn.setFixedSize(26, 26)
        self.del_btn.setAutoRaise(True)
        self.del_btn.clicked.connect(lambda: self.remove_requested.emit(self.index))
        layout.addWidget(self.del_btn)

    def _on_text_edited(self):
        self.text_changed.emit(self.index, self.text_edit.text())

    def set_playing(self, playing: bool):
        self.setProperty("playing", "true" if playing else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def apply_theme(self, theme_mode):
        """按主题刷新两个图标按钮的图标颜色。"""
        if theme_mode == ThemeMode.DARK:
            play_color = "#90caf9"  # 亮蓝：深色底上有足够对比度
            del_color = "#ff6b6b"  # 亮红
        else:
            play_color = "#1976d2"  # 主题蓝
            del_color = "#e74c3c"  # 主题红
        self.play_btn.setIcon(qta.icon('fa6s.play', color=play_color))
        self.del_btn.setIcon(qta.icon('fa6s.trash-can', color=del_color))

class VoiceView(QWidget):
    """语音播报页（左：语音用例 / 中：文案编辑 / 右：执行）。"""

    # 分组标题与自动化编辑页对齐（16px 粗体 + 同样的左右上下留白）
    GROUP_TITLE_PADDING_LEFT = 7
    GROUP_TITLE_PADDING_TOP = 3

    # 树节点上存的两个自定义角色：节点 id / 节点类型（group / case / ungrouped）
    ROLE_ID = int(Qt.ItemDataRole.UserRole)
    ROLE_KIND = int(Qt.ItemDataRole.UserRole) + 1

    @classmethod
    def _group_title_qss(cls, color: str) -> str:
        return (
            f"font-weight: bold; font-size: 16px; background: transparent; "
            f"color: {color}; "
            f"padding-left: {cls.GROUP_TITLE_PADDING_LEFT}px; "
            f"padding-top: {cls.GROUP_TITLE_PADDING_TOP}px;"
        )

    def __init__(self, model=None, service=None, parent=None):
        super().__init__(parent)
        self.setObjectName("VoiceView")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self.model = model or VoiceModel()
        if service is None:
            from services.voice_service import get_voice_service
            service = get_voice_service()
        self.service = service

        self._worker = None
        self._rows = []
        self._current_case_id = None
        # 记录当前主题，_reload_steps 里新建的 _PhraseRow 需要用它上图标颜色
        self._current_theme = ThemeMode.LIGHT

        self._build_ui()
        self._load_settings_into_ui()
        self._reload_tree()
        self._reload_steps()
        self._sync_play_buttons()

    # ------------------------------------------------------------------
    # 构建
    # ------------------------------------------------------------------
    def _has_wallpaper(self) -> bool:
        """与 MainWindow._has_wallpaper 同源：以壁纸实际加载状态为准"""
        return bool(getattr(self.window(), "_wallpaper_loaded", False))

    def _build_ui(self):
        layout = QVBoxLayout(self)
        # 与「自动化编辑」页一致：三栏直接铺满，外边不留间距
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_left_pane())
        splitter.addWidget(self._build_middle_pane())
        splitter.addWidget(self._build_right_pane())
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 4)
        splitter.setStretchFactor(2, 3)
        splitter.setSizes([300, 620, 460])
        layout.addWidget(splitter, 1)

    def _build_left_pane(self):
        group = QGroupBox()
        group.setObjectName("VoiceLeftGroup")
        self.left_group = group
        v = QVBoxLayout(group)
        # 标题到卡片边框的间距对齐「项目管理 ▾」（实测编辑页是 左13 / 上9）
        # 与编辑页的分组结构保持一致（它也是 0）；间距交给 QGroupBox 的 QSS padding
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)  # ← 与编辑页 title_layout 对齐
        self.voice_menu_btn = QToolButton()
        self.voice_menu_btn.setObjectName("VoiceMenuBtn")
        self.voice_menu_btn.setText("语音管理 ▾")
        self.voice_menu_btn.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup)
        self.voice_menu_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.voice_menu = QMenu(self.voice_menu_btn)
        self.voice_menu.setObjectName("VoiceMenu")  # 供主题 QSS 精确定位
        self.voice_menu.addAction(
            qta.icon('fa6s.file-import', color='#555555'), "导入文案到当前用例",
            self._on_import_phrases)
        self.voice_menu.addAction(
            qta.icon('fa6s.file-export', color='#555555'), "导出当前用例文案",
            self._on_export_phrases)
        self.voice_menu.addSeparator()
        self.voice_menu.addAction(
            qta.icon('fa6s.folder-open', color='#555555'), "导入全部语音用例",
            self._on_import_all)
        self.voice_menu.addAction(
            qta.icon('fa6s.folder-tree', color='#555555'), "导出全部语音用例",
            self._on_export_all)
        self.voice_menu_btn.setMenu(self.voice_menu)
        title_row.addWidget(self.voice_menu_btn)
        # 说明：本页是"电脑扬声器放音、车机麦克风拾取"的纯播报，跟 App 操作无关，
        # 所以不强求连设备。位置和取值照抄编辑页「项目管理 ▾」旁边那句
        # 「点击节点可查看用例」（views/main_window.py 的同名 hint_label）：
        # color #999 / 12px / 透明底，两页放一起看才是同一套。
        no_device_hint = QLabel("无设备连接也可执行")
        no_device_hint.setStyleSheet(
            "color: #999; font-size: 12px; background: transparent;")
        title_row.addWidget(no_device_hint)
        title_row.addStretch()
        v.addLayout(title_row)

        self.tree = QTreeWidget()
        self.tree.setObjectName("VoiceTree")
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(14)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        self.tree.itemSelectionChanged.connect(self._on_tree_selection_changed)
        v.addWidget(self.tree, 1)
        # 展开状态持久化：默认全折叠，记住用户上次展开的分组（存 data/config.json）
        self._tree_state = tree_state.bind_tree(self.tree, "voice_tree")
        return group

    def _build_middle_pane(self):
        group = QGroupBox()
        group.setObjectName("VoiceMiddleGroup")
        self.middle_group = group
        v = QVBoxLayout(group)
        # 与编辑页的分组结构保持一致（它也是 0）；间距交给 QGroupBox 的 QSS padding
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)  # ← 与编辑页 title_layout 对齐
        self.middle_title = QLabel("用例步骤")
        title_row.addWidget(self.middle_title)
        self.case_label = QLabel("未选用例")
        self.middle_hint = self.case_label
        title_row.addWidget(self.case_label)
        title_row.addStretch()
        # 唤醒词：一键把配置好的唤醒词追加成一条文案。
        # 和「+ 添加步骤」一样，没选用例时置灰（启用态才是实心蓝）
        self.wake_btn = QPushButton("唤醒词")
        self.wake_btn.setObjectName("VoiceWakeBtn")
        self.wake_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.wake_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.wake_btn.setToolTip("在当前用例末尾追加一条唤醒词文案（文案在「设置 → 语音播报」里改）")
        self.wake_btn.clicked.connect(self._on_add_wake_word)
        title_row.addWidget(self.wake_btn)
        self.add_btn = QPushButton("+ 添加步骤")
        self.add_btn.setObjectName("VoiceAddBtn")
        self.add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.add_btn.clicked.connect(self._on_add_phrase)
        title_row.addWidget(self.add_btn)
        v.addLayout(title_row)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("VoiceScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.viewport().setAutoFillBackground(False)
        # 视口自己会铺一层底色，必须显式透明，否则卡片里会有一块灰
        self.scroll.viewport().setStyleSheet("background: transparent;")

        self._container = QWidget()
        self._container.setObjectName("VoiceList")
        self._container.setAutoFillBackground(False)
        self._list_layout = QVBoxLayout(self._container)
        self._list_layout.setContentsMargins(0, 6, 0, 0)
        self._list_layout.setSpacing(0)
        self.scroll.setWidget(self._container)
        v.addWidget(self.scroll, 1)
        return group

    def _build_right_pane(self):
        group = QGroupBox()
        group.setObjectName("VoiceRightGroup")
        self.right_group = group
        v = QVBoxLayout(group)
        # 与编辑页的分组结构保持一致（它也是 0）；间距交给 QGroupBox 的 QSS padding
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        # 单行工具栏：语速 / 循环 / 执行选中 / 停止  +  右侧全选 / 取消全选
        # 不再有"执行"标题，控件全部集中在这一行
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(6)

        title_row.addWidget(QLabel("语速"))
        self.rate_spin = QSpinBox()
        self.rate_spin.setObjectName("VoiceRateSpin")
        self.rate_spin.setRange(-10, 10)
        self.rate_spin.setFixedWidth(56)
        self.rate_spin.setFixedHeight(26)
        self.rate_spin.valueChanged.connect(self._on_rate_changed)
        title_row.addWidget(self.rate_spin)

        title_row.addSpacing(4)
        title_row.addWidget(QLabel("循环"))
        self.loop_spin = QSpinBox()
        self.loop_spin.setObjectName("VoiceLoopSpin")
        self.loop_spin.setRange(1, 99)
        self.loop_spin.setValue(1)
        self.loop_spin.setFixedWidth(64)
        self.loop_spin.setFixedHeight(26)
        title_row.addWidget(self.loop_spin)

        title_row.addSpacing(8)
        self.play_all_btn = QPushButton("执行选中")
        self.play_all_btn.setObjectName("VoicePlayAllBtn")
        self.play_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_all_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.play_all_btn.setFixedHeight(26)
        self.play_all_btn.clicked.connect(self._on_play_all)
        title_row.addWidget(self.play_all_btn)

        self.stop_btn = QPushButton("停止")
        self.stop_btn.setObjectName("VoiceStopBtn")
        self.stop_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.stop_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.stop_btn.setFixedHeight(26)
        self.stop_btn.clicked.connect(self._on_stop)
        title_row.addWidget(self.stop_btn)

        title_row.addStretch()

        self.all_btn = QPushButton("全选")
        self.none_btn = QPushButton("取消全选")
        for btn in (self.all_btn, self.none_btn):
            btn.setObjectName("VoiceToolBtn")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setFixedHeight(26)
        self.all_btn.clicked.connect(lambda: self._set_all_checked(True))
        self.none_btn.clicked.connect(lambda: self._set_all_checked(False))
        title_row.addWidget(self.all_btn)
        title_row.addWidget(self.none_btn)

        v.addLayout(title_row)

        # 勾选树（分组 → 用例，用例节点带复选框）。
        # itemChanged 信号签名是 (item, column)，比 QListWidget 多一列参数。
        # 用 _ClickAnywhereCheckTree：整行点哪儿都能勾，不用对准小方框
        self.check_tree = _ClickAnywhereCheckTree()
        self.check_tree.setObjectName("VoiceCheckTree")
        self.check_tree.setHeaderHidden(True)
        self.check_tree.setIndentation(14)
        self.check_tree.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection)
        self.check_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.check_tree.itemChanged.connect(self._on_check_tree_item_changed)
        # 同上：viewport 透明，半透明底 + 圆角才生效
        self.check_tree.viewport().setAutoFillBackground(False)
        # 给树节点的复选框叠一圈明显边框：Fusion 默认画的框太淡，
        # 在深色底上几乎只剩一个勾号，用户感知不到"这里可以勾选"
        self._check_delegate = _BorderedTreeItemDelegate(
            border_color="#5a5a5a",  # 浅色主题默认值
            selected_border_color="#ffffff",
            parent=self.check_tree,
        )
        self.check_tree.setItemDelegate(self._check_delegate)
        # 展开状态持久化：默认全折叠，记住用户上次展开的分组（存 data/config.json）
        self._check_tree_state = tree_state.bind_tree(
            self.check_tree, "voice_check_tree")
        v.addWidget(self.check_tree, 1)

        # 状态标签保留对象（_on_playback_done 等仍会 setText），
        # 但不再加入布局：本页顶部按钮的启用/禁用状态已经能反映运行态，
        # 单独再摆一行"播报完成"属于冗余
        self.status_label = QLabel("")
        self.status_label.setObjectName("VoiceStatus")
        self.status_label.hide()
        return group

    # ------------------------------------------------------------------
    # 左：语音用例树（分组 → 语音用例，与项目树同一套层级观感）
    # ------------------------------------------------------------------
    def _reload_tree(self):
        # 重建前先把当前展开态收下来（clear() 会把展开态全部丢掉）
        self._tree_state.snapshot()
        self.tree.blockSignals(True)
        with self._tree_state.pause():
            self.tree.clear()

        for group in self.model.groups:
            node = QTreeWidgetItem([group.name or "(未命名分组)"])
            node.setData(0, self.ROLE_ID, group.id)
            node.setData(0, self.ROLE_KIND, "group")
            node.setIcon(0, qta.icon("fa6s.folder", color="#f0b429"))
            # 分组本身只用于展开/右键，不可选中（选中态留给用例）
            node.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            for case in self.model.cases_of_group(group.id):
                node.addChild(self._make_case_item(case))
            self.tree.addTopLevelItem(node)

        # 按上次记录还原展开态；没有记录（首次运行）就保持全折叠（原来是无条件全展开）
        self._tree_state.restore()

        # 尽量选回原来那个用例；没有就保持"未选中"状态，
        # 让用户手动点选 —— 不默认选第一个，避免用户没注意就误播第一个用例
        if self._current_case_id:
            self._select_case_in_tree(self._current_case_id)
        self.tree.blockSignals(False)

    @staticmethod
    def _make_case_item(case):
        item = QTreeWidgetItem([case.name or "(未命名)"])
        item.setData(0, VoiceView.ROLE_ID, case.id)
        item.setData(0, VoiceView.ROLE_KIND, "case")
        item.setIcon(0, qta.icon("fa6s.comment-dots", color="#8a9099"))
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        return item

    def _all_case_items(self):
        items = []
        for i in range(self.tree.topLevelItemCount()):
            node = self.tree.topLevelItem(i)
            for j in range(node.childCount()):
                items.append(node.child(j))
        return items

    def _first_case_item(self):
        items = self._all_case_items()
        return items[0] if items else None

    def _select_case_in_tree(self, case_id) -> bool:
        for item in self._all_case_items():
            if item.data(0, self.ROLE_ID) == case_id:
                self.tree.setCurrentItem(item)
                return True
        return False

    def _on_tree_selection_changed(self):
        items = self.tree.selectedItems()
        kind = items[0].data(0, self.ROLE_KIND) if items else None
        self._current_case_id = (
            items[0].data(0, self.ROLE_ID) if items and kind == "case" else None)
        self._reload_steps()

    def _current_case(self):
        if not self._current_case_id:
            return None
        return self.model.get_case(self._current_case_id)

    def _selected_group_id(self):
        """新建用例时归到哪个分组：优先当前用例所在分组，其次第一个分组。

        不再有"未分组"这一归宿：没有分组时返回 ""，由调用方拦住并提示。
        """
        case = self._current_case()
        if case is not None:
            return case.group_id
        if self.model.groups:
            return self.model.groups[0].id
        return ""

    # ------------------------------------------------------------------
    # 树的右键菜单：增 / 删 / 改
    # ------------------------------------------------------------------
    def _on_tree_context_menu(self, pos):
        item = self.tree.itemAt(pos)
        kind = item.data(0, self.ROLE_KIND) if item is not None else None
        node_id = item.data(0, self.ROLE_ID) if item is not None else None

        menu = QMenu(self)

        # 图标颜色跟主题走：qta 生成的是位图，QSS 改不了颜色，只能在这里按主题选。
        # 取色与「语音管理 ▾」下拉菜单（_apply_voice_menu_theme）保持一致。
        is_dark = self._current_theme == ThemeMode.DARK
        icon_color = "#bbbbbb" if is_dark else "#555555"

        # 「新建分组」任何时候都有：空白 / 分组 / 用例上右键都能建顶层分组
        menu.addAction(qta.icon('fa6s.plus', color=icon_color), "新建分组",
                       self._on_new_group)

        # 「新建语音用例」只在已存在至少一个分组时才出现：
        # 没有分组时用例无处安放，菜单里干脆不给入口，
        # 强制用户先走"建分组"这一步（与编辑页"先建模块再建用例"一致）。
        if self.model.groups:
            if kind == "group":
                target_group = node_id
            elif kind == "case":
                case = self.model.get_case(node_id)
                target_group = case.group_id if case else self._selected_group_id()
            else:
                target_group = self._selected_group_id()
            menu.addAction(
                qta.icon('fa6s.comment-dots', color=icon_color), "新建语音用例",
                lambda: self._on_new_case(target_group))

        if kind in ("case", "group"):
            menu.addSeparator()
            # 复制：用例复制一条，分组连组内用例一起复制
            # （与「项目管理」树的 复制用例 / 复制功能模块 对齐）
            if kind == "case":
                menu.addAction(qta.icon('fa6s.copy', color=icon_color),
                               "复制语音用例",
                               lambda: self._on_copy_case(node_id))
            else:
                menu.addAction(qta.icon('fa6s.copy', color=icon_color),
                               "复制分组",
                               lambda: self._on_copy_group(node_id))
            menu.addSeparator()
            menu.addAction(qta.icon('fa6s.pen', color=icon_color), "重命名",
                           lambda: self._on_rename_node(kind, node_id))
            menu.addAction(qta.icon('fa6s.trash', color=icon_color), "删除",
                           lambda: self._on_delete_node(kind, node_id))

        menu.exec(self.tree.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------------
    # 中：文案编辑 / 右：执行清单
    # ------------------------------------------------------------------
    def _reload_steps(self):
        case = self._current_case()
        phrases = list(case.phrases) if case else []

        # ---- 中栏：编辑行 ----
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._rows = []

        if case is None:
            if not self.model.groups:
                hint = "还没有分组，请在左侧空白处右键 → 新建分组"
            elif not self.model.cases:
                hint = "还没有语音用例，右键分组 → 新建语音用例"
            else:
                hint = "先在左侧选一个语音用例"
            self._add_empty_hint(hint)
        elif not phrases:
            self._add_empty_hint("这个用例还没有文案，点右上角「+ 添加步骤」添加")
        else:
            for i, phrase in enumerate(phrases):
                row = _PhraseRow(i, phrase, self._container)
                row.play_requested.connect(self._on_play_one)
                row.remove_requested.connect(self._on_remove_phrase)
                row.text_changed.connect(self._on_text_changed)
                row.delay_changed.connect(self._on_delay_changed)
                # 新建的行不会自动拿到当前主题，这里手动上一次
                row.apply_theme(self._current_theme)
                self._list_layout.addWidget(row)
                self._rows.append(row)
            self._list_layout.addStretch()

        # ---- 右栏：勾选树（分组 → 语音用例，用例可勾选）----
        checked_before = self._collect_checked_case_ids()
        # 默认全部不勾选：只恢复"之前勾过的"。首次进入右栏时一个都不勾，
        # 免得用户还没看清有哪些用例，就已经是一副"全选好了"的样子。

        self.check_tree.blockSignals(True)
        # 重建前先把当前展开态收下来（clear() 会把展开态全部丢掉）
        self._check_tree_state.snapshot()
        with self._check_tree_state.pause():
            self.check_tree.clear()
        for group in self.model.groups:
            group_item = QTreeWidgetItem([group.name or "(未命名分组)"])
            group_item.setData(0, Qt.ItemDataRole.UserRole, group.id)
            group_item.setData(0, Qt.ItemDataRole.UserRole + 1, "group")
            # 分组也带复选框：勾选 = 全选 / 全不选该组下的所有用例，
            # 部分勾选时显示为 PartiallyChecked（方块带减号）
            group_item.setFlags(
                group_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            group_item.setIcon(0, qta.icon("fa6s.folder", color="#f0b429"))

            cases = self.model.cases_of_group(group.id)
            for voice_case in cases:
                case_item = QTreeWidgetItem([voice_case.name or "(未命名)"])
                case_item.setData(0, Qt.ItemDataRole.UserRole, voice_case.id)
                case_item.setData(0, Qt.ItemDataRole.UserRole + 1, "case")
                case_item.setFlags(
                    case_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                # 不再按"当前选中用例"打蓝点，右侧只体现"要不要播"，
                # 与左侧的选中态解耦（左侧点击不再联动右侧视觉）
                case_item.setIcon(
                    0, qta.icon("fa6s.comment-dots", color="#8a9099"))
                checked = voice_case.id in checked_before
                case_item.setCheckState(
                    0,
                    Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
                group_item.addChild(case_item)

            # 子节点都建好后，再按它们的勾选情况算分组的初始状态
            total = group_item.childCount()
            if total == 0:
                group_state = Qt.CheckState.Unchecked
            else:
                checked_count = sum(
                    1 for j in range(total)
                    if group_item.child(j).checkState(0) == Qt.CheckState.Checked)
                if checked_count == total:
                    group_state = Qt.CheckState.Checked
                elif checked_count == 0:
                    group_state = Qt.CheckState.Unchecked
                else:
                    group_state = Qt.CheckState.PartiallyChecked
            group_item.setCheckState(0, group_state)

            self.check_tree.addTopLevelItem(group_item)
        # 按上次记录还原展开态；没有记录（首次运行）就保持全折叠（原来是无条件全展开）
        self._check_tree_state.restore()
        self.check_tree.blockSignals(False)

        # 中栏标题旁不再显示"用例名 · N 条"（数量信息由右栏树自行体现）
        self.case_label.setText("")
        self._sync_play_buttons()

    def _add_empty_hint(self, text):
        label = QLabel(text)
        label.setObjectName("VoiceEmpty")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._list_layout.addStretch()
        self._list_layout.addWidget(label)
        self._list_layout.addStretch()

    # ------------------------------------------------------------------
    # 左栏菜单
    # ------------------------------------------------------------------
    def _current_node(self):
        """树上当前选中的节点 -> (kind, id)；没选中返回 (None, None)"""
        items = self.tree.selectedItems()
        if not items:
            return None, None
        return items[0].data(0, self.ROLE_KIND), items[0].data(0, self.ROLE_ID)

    def _on_new_group(self):
        name, ok = InputDialog.get_text(
            self,
            title="新建分组",
            label="请输入分组名称:",
            placeholder="例如：常用指令",
        )
        if not ok:
            return
        group = self.model.add_group((name or "").strip())
        self._reload_tree()
        self._reload_steps()
        self.status_label.setText(f"已新建分组「{group.name}」")

    def _on_new_case(self, group_id=None):
        # 没有分组时先提示建分组（右键菜单里已不给这个入口，这里兜底）
        if not self.model.groups:
            self.status_label.setText("请先创建分组，再在分组里创建语音用例")
            return
        name, ok = InputDialog.get_text(
            self,
            title="新建语音用例",
            label="请输入用例名称:",
            placeholder="例如：唤醒后打开地图",
        )
        if not ok:
            return
        if not group_id:
            group_id = self._selected_group_id()
        if not group_id:
            self.status_label.setText("请先在左侧选中一个分组")
            return
        case = self.model.add_case((name or "").strip(), group_id)
        self._current_case_id = case.id
        self._reload_tree()
        self._reload_steps()
        self.status_label.setText(f"已新建「{case.name}」")

    def _on_copy_case(self, case_id):
        new_case = self.model.copy_case(case_id)
        if new_case is None:
            return
        # 选中副本：与新建用例一样，落点就在用户眼前
        self._current_case_id = new_case.id
        self._reload_tree()
        self._reload_steps()
        self.status_label.setText(f"已复制为「{new_case.name}」")

    def _on_copy_group(self, group_id):
        new_group = self.model.copy_group(group_id)
        if new_group is None:
            return
        self._reload_tree()
        # 右栏勾选树列的是所有用例，复制出来的用例也要进去
        self._reload_steps()
        self.status_label.setText(f"已复制为「{new_group.name}」")

    def _on_rename_node(self, kind, node_id):
        if kind == "case":
            case = self.model.get_case(node_id)
            if case is None:
                return
            name, ok = InputDialog.get_text(
                self,
                title="重命名语音用例",
                label="请输入新名称:",
                default_text=case.name,
            )
            if not ok or not (name or "").strip():
                return
            self.model.rename_case(case.id, name)
        elif kind == "group":
            group = self.model.get_group(node_id)
            if group is None:
                return
            name, ok = InputDialog.get_text(
                self,
                title="重命名分组",
                label="请输入新名称:",
                default_text=group.name,
            )
            if not ok or not (name or "").strip():
                return
            self.model.rename_group(group.id, name)
        else:
            self.status_label.setText("请先在左侧选中一个分组或语音用例")
            return
        self._reload_tree()
        self._reload_steps()

    def _on_delete_node(self, kind, node_id):
        if kind == "case":
            case = self.model.get_case(node_id)
            if case is None:
                return
            if not ConfirmDeleteDialog.ask(
                    self, title="删除语音用例",
                    message=f"确定删除「{case.name}」？该用例下的文案会一起删掉。"):
                return
            self.model.remove_case(case.id)
            if self._current_case_id == case.id:
                self._current_case_id = None
            self.status_label.setText(f"已删除「{case.name}」")
        elif kind == "group":
            group = self.model.get_group(node_id)
            if group is None:
                return
            count = len(self.model.cases_of_group(group.id))
            if not ConfirmDeleteDialog.ask(
                    self, title="删除分组",
                    message=f"确定删除分组「{group.name}」？"
                            f"组内 {count} 个语音用例会一起删掉。"):
                return
            current = (self.model.get_case(self._current_case_id)
                       if self._current_case_id else None)
            removed = self.model.remove_group(group.id, with_cases=True)
            if current is not None and current.group_id == group.id:
                self._current_case_id = None
            self.status_label.setText(
                f"已删除分组「{group.name}」（含 {removed} 个用例）")
        else:
            self.status_label.setText("请先在左侧选中一个分组或语音用例")
            return
        self._reload_tree()
        self._reload_steps()

    def _on_export_all(self):
        """全量导出：分组 -> 用例 -> 文案 整棵树，换机器/换项目时用。

        与「导出当前用例文案」（单用例的文案包）不同，这个文件包含分组与
        用例结构，配合「导入全部语音用例」可以完整还原。
        """
        total_cases = len(self.model.cases)
        if not total_cases:
            self.status_label.setText("还没有语音用例可导出")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出全部语音用例", "voice_cases_all.json",
            "JSON Files (*.json)")
        if not path:
            return
        try:
            data = self.model.export_all()
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            n_groups = len(data.get("groups", []))
            self.status_label.setText(
                f"已导出 {n_groups} 个分组 / {total_cases} 个用例到 "
                f"{os.path.basename(path)}")
        except Exception as e:
            ErrorDialog.show_error(self, "导出失败",
                                   f"导出全部语音用例时出错：\n{e}")

    def _on_import_all(self):
        """全量导入：按「导出全部语音用例」的格式还原分组/用例/文案（追加式）。

        合并规则：同名分组复用现有分组；分组内同名用例跳过；其余新建。
        """
        path, _ = QFileDialog.getOpenFileName(
            self, "导入全部语音用例", "", "JSON Files (*.json)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            stats = self.model.import_all(data)
        except ValueError as e:
            ErrorDialog.show_error(self, "导入失败", str(e))
            return
        except Exception as e:
            ErrorDialog.show_error(self, "导入失败",
                                   f"导入全部语音用例时出错：\n{e}")
            return
        self._reload_tree()
        self.status_label.setText(
            f"已导入：新建 {stats['groups']} 个分组 / {stats['cases']} 个用例 / "
            f"{stats['phrases']} 条文案"
            + (f"，跳过同名用例 {stats['skipped']} 个" if stats["skipped"] else ""))

    def _on_export_phrases(self):
        """把当前语音用例导成 JSON，方便换机器/换项目复用"""
        case = self._current_case()
        if case is None:
            self.status_label.setText("先在左侧选一个语音用例")
            return
        if not case.phrases:
            self.status_label.setText("当前用例还没有文案可导出")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出语音文案", PHRASES_FILE_NAME, "JSON Files (*.json)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.model.export_case(case.id), f,
                          ensure_ascii=False, indent=2)
            self.status_label.setText(
                f"已导出 {len(case.phrases)} 条到 {os.path.basename(path)}")
        except Exception as e:
            ErrorDialog.show_error(self, "导出失败",
                                   f"导出语音文案时出错：\n{e}")

    def _on_import_phrases(self):
        """把 JSON 里的文案追加成当前用例的文案。

        兼容两种格式：{"phrases": [...]}（本页导出的）和裸数组 [...]。
        """
        case = self._current_case()
        if case is None:
            self.status_label.setText("先在左侧选一个语音用例")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "导入语音文案", "", "JSON Files (*.json)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            phrases = data.get("phrases") if isinstance(data, dict) else data
            if not isinstance(phrases, list):
                raise ValueError("文件里找不到 phrases 列表")
            added = self.model.import_phrases(case.id, phrases)
            self._reload_steps()
            self.status_label.setText(f"已导入 {added} 条语音文案")
        except Exception as e:
            ErrorDialog.show_error(self, "导入失败",
                                   f"导入语音文案时出错：\n{e}")

    # ------------------------------------------------------------------
    # 中栏编辑
    # ------------------------------------------------------------------
    def _on_add_phrase(self):
        case = self._current_case()
        if case is None:
            self.status_label.setText("先在左侧选一个语音用例")
            return
        self.model.add_phrase(case.id, "", DEFAULT_DELAY)
        self._reload_steps()
        self.status_label.setText("已新增一条文案")

    def _wake_word(self) -> str:
        """当前配置的唤醒词。

        唤醒词由「设置 → 语音播报」写进 voice_data.json，而设置对话框用的是另一个
        VoiceModel 实例（只往文件里写）。本页这个实例的 settings 是构造时的快照，
        所以这里先 load() 一次再取，否则在设置页改完文案，本页还是旧值。
        """
        self.model.load()
        return ((self.model.settings.get("wake_word") or "").strip()
                or DEFAULT_WAKE_WORD)

    def _on_add_wake_word(self):
        # 先取唤醒词（顺带把 model 重新读盘），再找用例，避免用到 load() 之前的旧对象
        wake_word = self._wake_word()
        case = self._current_case()
        if case is None:
            self.status_label.setText("先在左侧选一个语音用例")
            return
        self.model.add_phrase(case.id, wake_word, DEFAULT_DELAY)
        self._reload_steps()
        self.status_label.setText(f"已追加唤醒词「{wake_word}」")

    def _on_remove_phrase(self, index):
        case = self._current_case()
        if case is None:
            return
        self.model.remove_phrase(case.id, index)
        self._reload_steps()

    def _on_text_changed(self, index, text):
        case = self._current_case()
        if case is None:
            return
        self.model.update_phrase(case.id, index, text=text)
        # 右栏不再显示"N 条"，无需再刷新计数

    def _on_delay_changed(self, index, delay):
        case = self._current_case()
        if case is None:
            return
        self.model.update_phrase(case.id, index, delay=delay)

    # ------------------------------------------------------------------
    # 配置
    # ------------------------------------------------------------------
    def _load_settings_into_ui(self):
        """回填语速，并把整份配置（含音色/输出设备）套到引擎上"""
        settings = self.model.settings
        self.rate_spin.blockSignals(True)
        self.rate_spin.setValue(int(settings.get("rate", 0)))
        self.rate_spin.blockSignals(False)
        self._apply(lambda: self.service.apply_cfg(settings))

    def _on_rate_changed(self, value):
        self.model.set_settings(rate=int(value))
        self._apply(lambda: self.service.set_rate(int(value)))

    def _apply(self, action):
        if not self.service.is_available():
            return
        try:
            action()
            self.status_label.setText("")
        except VoiceError as e:
            self.status_label.setText(str(e))

    # ------------------------------------------------------------------
    # 右栏执行
    # ------------------------------------------------------------------
    def _on_check_tree_item_changed(self, item, column):
        """勾选状态变化：
          - 分组变化 → 级联到所有子用例
          - 用例变化 → 反向更新父分组的勾选状态（全/部分/全不）
        注意：级联期间必须 blockSignals，否则会产生"改父→改子→再改父"的回环。
        """
        kind = item.data(0, Qt.ItemDataRole.UserRole + 1)

        if kind == "group":
            state = item.checkState(0)
            # 用户点分组时只会得到 Checked 或 Unchecked（点不出 Partially），
            # 直接按这个状态级联到子节点
            self.check_tree.blockSignals(True)
            for j in range(item.childCount()):
                item.child(j).setCheckState(0, state)
            self.check_tree.blockSignals(False)

        elif kind == "case":
            parent = item.parent()
            if parent is not None:
                self.check_tree.blockSignals(True)
                self._update_group_check_state(parent)
                self.check_tree.blockSignals(False)

        self._sync_play_buttons()

    def _update_group_check_state(self, group_item):
        """按子用例的勾选情况刷新分组的勾选状态：
          全选 → Checked；全不选 → Unchecked；部分 → PartiallyChecked
        只读子节点、只写父节点，不会递归触发其它变化。
        """
        total = group_item.childCount()
        if total == 0:
            group_item.setCheckState(0, Qt.CheckState.Unchecked)
            return
        checked = sum(
            1 for j in range(total)
            if group_item.child(j).checkState(0) == Qt.CheckState.Checked)
        if checked == total:
            state = Qt.CheckState.Checked
        elif checked == 0:
            state = Qt.CheckState.Unchecked
        else:
            state = Qt.CheckState.PartiallyChecked
        group_item.setCheckState(0, state)

    def _collect_checked_case_ids(self):
        """把右栏树里所有勾选的用例 id 收集成集合"""
        ids = set()
        for i in range(self.check_tree.topLevelItemCount()):
            group_item = self.check_tree.topLevelItem(i)
            for j in range(group_item.childCount()):
                case_item = group_item.child(j)
                if case_item.checkState(0) == Qt.CheckState.Checked:
                    ids.add(case_item.data(0, Qt.ItemDataRole.UserRole))
        return ids

    def _set_all_checked(self, checked):
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        self.check_tree.blockSignals(True)
        for i in range(self.check_tree.topLevelItemCount()):
            group_item = self.check_tree.topLevelItem(i)
            for j in range(group_item.childCount()):
                group_item.child(j).setCheckState(0, state)
            # 子节点已经全改成同一个状态，分组按新状态直接设即可，
            # 不需要再走 _update_group_check_state 逐个子节点去数
            group_item.setCheckState(0, state)
        self.check_tree.blockSignals(False)
        self._sync_play_buttons()

    def _checked_items(self):
        """勾选的语音用例 -> [(case_id, 第几条, 文案, 播后等待), ...]"""
        items = []
        for i in range(self.check_tree.topLevelItemCount()):
            group_item = self.check_tree.topLevelItem(i)
            for j in range(group_item.childCount()):
                case_item = group_item.child(j)
                if case_item.checkState(0) != Qt.CheckState.Checked:
                    continue
                case_id = case_item.data(0, Qt.ItemDataRole.UserRole)
                voice_case = self.model.get_case(case_id)
                if voice_case is None:
                    continue
                for idx, phrase in enumerate(voice_case.phrases):
                    text = (phrase.text or "").strip()
                    if not text:
                        continue
                    items.append((case_id, idx, text, float(phrase.delay or 0)))
        return items

    def _sync_action_button_widths(self):
        """把两排动作按钮各自对齐到组内最宽的宽度。

        「唤醒词 / + 添加步骤」和「执行选中 / 停止 / 全选 / 取消全选」的文案长短不一
        （2 字 ~ 4 字），不定宽时一排里宽窄参差，看着散。按组取 sizeHint 的最大值，
        不写死像素 —— 字号或系统字体变了也不会把文案截掉。
        必须在 QSS 下发之后调用，sizeHint 才是按按钮那套 12px 字号算出来的。
        """
        for buttons in (
            (self.wake_btn, self.add_btn),
            (self.play_all_btn, self.stop_btn, self.all_btn, self.none_btn),
        ):
            width = max(b.sizeHint().width() for b in buttons)
            for btn in buttons:
                btn.setFixedWidth(width)

    def _sync_play_buttons(self, running=False):
        available = self.service.is_available()
        has_checked = bool(self._collect_checked_case_ids())
        # 一条都没勾选时直接禁用，比点了再弹提示更直观
        self.play_all_btn.setEnabled(available and not running and has_checked)
        self.stop_btn.setEnabled(running)
        self.add_btn.setEnabled(not running and self._current_case() is not None)
        self.wake_btn.setEnabled(not running and self._current_case() is not None)
        self.all_btn.setEnabled(not running)
        self.none_btn.setEnabled(not running)
        self.loop_spin.setEnabled(not running)
        self.rate_spin.setEnabled(available and not running)
        self.check_tree.setEnabled(not running)
        self.tree.setEnabled(not running)
        for row in self._rows:
            row.play_btn.setEnabled(available and not running)
            row.del_btn.setEnabled(not running)
            row.text_edit.setEnabled(not running)
            row.delay_spin.setEnabled(not running)

    def _start_playback(self, items, loop_count, label):
        if self._worker is not None and self._worker.isRunning():
            return
        if not self.service.is_available():
            self.status_label.setText("本机没有可用的语音引擎，无法播报")
            return
        self._worker = _PlaybackWorker(self.service, items, loop_count, self)
        self._worker.progress.connect(self._on_progress)
        self._worker.round_changed.connect(self._on_round_changed)
        self._worker.done.connect(self._on_playback_done)
        self._sync_play_buttons(running=True)
        self.status_label.setText(label)
        self._worker.start()

    def _on_play_one(self, index):
        case = self._current_case()
        if case is None or not (0 <= index < len(case.phrases)):
            return
        text = (case.phrases[index].text or "").strip()
        if not text:
            self.status_label.setText("这条是空的，先填文案")
            return
        # 单条播报不等待后面的间隔
        self._start_playback([(case.id, index, text, 0.0)], 1, "正在播报…")

    def _on_play_all(self):
        items = self._checked_items()
        if not items:
            # 状态标签已隐藏，改为不打扰的 toast 提示
            from utils.toast import show_toast
            show_toast(self, "没有勾选可播报的文案", duration=1500)
            return
        loop_count = self.loop_spin.value()
        case_count = len(self._collect_checked_case_ids())
        label = f"正在播报 {case_count} 个用例 / {len(items)} 条"
        if loop_count > 1:
            label += f" × {loop_count} 轮"
        self._start_playback(items, loop_count, label + "…")

    def _on_stop(self):
        if self._worker is not None:
            self._worker.stop()
            self.status_label.setText("正在停止…")

    def _on_progress(self, case_id, phrase_index):
        """高亮正在播的那条（只在它属于当前展示的用例时）"""
        for row in self._rows:
            playing = (case_id == self._current_case_id
                       and phrase_index >= 0 and row.index == phrase_index)
            row.set_playing(playing)

    def _on_round_changed(self, current, total):
        if total > 1:
            self.status_label.setText(f"正在播报 · 第 {current}/{total} 轮…")

    def _on_playback_done(self, ok, error):
        self._sync_play_buttons(running=False)
        for row in self._rows:
            row.set_playing(False)
        if error:
            self.status_label.setText(f"播报失败：{error}")
        elif ok:
            self.status_label.setText("播报完成")
        else:
            self.status_label.setText("已停止")
        self._worker = None

    def _iter_tree_items(self):
        return [self.tree.topLevelItem(i)
                for i in range(self.tree.topLevelItemCount())]

    def showEvent(self, event):
        """切回本页时重新读一遍（设置页可能改过音色/输出设备）"""
        super().showEvent(event)
        self._reload_tree()
        # 无条件刷新中栏：_current_case_id 可能因为树被重建、
        # 或对应用例被删掉而变化，中栏必须跟它保持同步
        self._reload_steps()

    # ------------------------------------------------------------------
    # 主题
    # ------------------------------------------------------------------
    def _apply_voice_menu_theme(self, is_dark):
        """语音管理下拉菜单：与编辑页「项目管理 ▾」菜单使用同一套配色与排版。

        手法照抄 main_window._apply_project_menu_theme：
          - 去掉系统窗口装饰 + 允许透明背景，QSS 的圆角四角才真正生效
          - item 左 padding 32px 给图标留位，`::icon { left: 10px; }` 让图标居中
        """
        if self.voice_menu is None:
            return

        self.voice_menu.setWindowFlags(
            Qt.WindowType.Popup
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.voice_menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        if is_dark:
            menu_bg = "#2b2d30"
            menu_border = "#4a4a4a"
            menu_text = "#dddddd"
            menu_hover_bg = "#1e3a5f"
            menu_hover_text = "#ffffff"
            sep_color = "#4a4a4a"
            icon_color = "#bbbbbb"
        else:
            menu_bg = "#ffffff"
            menu_border = "#d0d0d0"
            menu_text = "#333333"
            menu_hover_bg = "#e8f0fe"
            menu_hover_text = "#1976d2"
            sep_color = "#e0e0e0"
            icon_color = "#555555"

        self.voice_menu.setStyleSheet(f"""
            QMenu#VoiceMenu {{
                background-color: {menu_bg};
                border: 1px solid {menu_border};
                border-radius: 8px;
                padding: 6px;
            }}
            QMenu#VoiceMenu::item {{
                background: transparent;
                padding: 7px 28px 7px 32px;
                margin: 1px 2px;
                border-radius: 5px;
                color: {menu_text};
                font-size: 13px;
            }}
            QMenu#VoiceMenu::item:selected {{
                background-color: {menu_hover_bg};
                color: {menu_hover_text};
            }}
            QMenu#VoiceMenu::separator {{
                height: 1px;
                background: {sep_color};
                margin: 4px 8px;
            }}
            QMenu#VoiceMenu::icon {{
                left: 10px;
            }}
        """)

        # 图标是 qta 生成的位图，QSS 改不了颜色，只能按主题重建
        icon_map = {
            "导入语音文案": 'fa6s.file-import',
            "导出语音文案": 'fa6s.file-export',
        }
        for act in self.voice_menu.actions():
            name = act.text()
            if name in icon_map:
                act.setIcon(qta.icon(icon_map[name], color=icon_color))

    def apply_theme(self, theme_mode: ThemeMode):
        is_dark = (theme_mode == ThemeMode.DARK)
        has_wallpaper = self._has_wallpaper()
        # 记下当前主题：右键菜单（每次弹出新建）、以及之后重建的文案行
        # 都要按它挑图标颜色。以前这里没赋值，_current_theme 一直是初始的 LIGHT，
        # 夜间模式下这两处的图标就都还是浅色那套。
        self._current_theme = theme_mode


        # ---------- 三个分组卡片：与「自动化编辑」页同一套规则 ----------
        # 有壁纸时透明（由 MainWindow 的壁纸罩层透出来），无壁纸时纯色
        if is_dark:
            card_bg = "transparent" if has_wallpaper else "#191a1c"
            card_border = "rgba(85, 85, 85, 0.9)" if has_wallpaper else "#555"
            title_color, hint_color = "#ffffff", "#9aa0a6"
            text, muted = "#e8e8e8", "#9aa0a6"
            row_hover, row_border = "#33353a", "#3a3c42"
            row_playing = "#2f3a4a"
            btn_bg, btn_hover, btn_border = "#3a3c42", "#46484f", "#4a4c53"
            input_bg = "#25262a"
            scroll_handle = "#5a5c63"
            # 树的底：与项目树（PROJECT_TREE_DARK）完全一致 —— 半透明，
            # 让中央区域那层 0.7 的深色罩 + 壁纸透出来。无壁纸时是叠加后的深灰，
            # 有壁纸时能隐约看到壁纸纹理，与「项目管理」观感一致
            tree_bg = "rgba(60, 60, 60, 0.7)"
            tree_border = "#4a4a4a"
            # 树节点选中态：项目树是 rgba(30, 58, 95, 0.85)，这里取它的等效实色
            # #1e3a5f。**必须实色**：半透明色会被「分支列」和「item 单元格」
            # 各叠一次，同一行从左到右会呈现两种蓝，选中行左端出现一道色差
            tree_sel_bg, tree_sel_text = "#1e3a5f", "#ffffff"
            # 树节点复选框的边框色：深色底上用亮灰才看得见
            check_border_color = "#b8b8b8"
        else:
            card_bg = "transparent" if has_wallpaper else "#ffffff"
            card_border = ("rgba(208, 208, 208, 0.9)" if has_wallpaper
                           else "#d0d0d0")
            # 与编辑页的浅色规则保持完全一致（它用的是 #333 / #999）
            title_color, hint_color = "#333", "#999"
            text, muted = "#333333", "#8a9099"
            row_hover, row_border = "#f2f5fa", "#e6e8ec"
            row_playing = "#e3f0ff"
            btn_bg, btn_hover, btn_border = "#eef0f4", "#e2e6ec", "#d8dce3"
            input_bg = "#ffffff"
            scroll_handle = "#c4c8cf"
            # 树的底：与项目树同款 —— 有壁纸时半透明（rgba 0.7），
            # 无壁纸时实色 #e8eaed
            if has_wallpaper:
                tree_bg = "rgba(232, 234, 237, 0.7)"
                tree_border = "rgba(208, 208, 208, 0.9)"
            else:
                tree_bg = "#e8eaed"
                tree_border = "#d0d0d0"
            # 树节点选中态：项目树（PROJECT_TREE_LIGHT）的 #d0e4f7 + 深色字。
            # 同样用实色 —— 壁纸分支那边项目树写的是 rgba(...,0.9)，会有一样的色差
            tree_sel_bg, tree_sel_text = "#d0e4f7", "#1a1a1a"
            # 树节点复选框的边框色：浅色底上用深灰保证对比度
            check_border_color = "#5a5a5a"

        card_qss = f"""
            QGroupBox {{
                background-color: {card_bg};
                border: 1px solid {card_border};
                border-radius: 8px;
                padding: 6px;
            }}
            QGroupBox::title {{ color: {title_color}; }}
        """
        for group in (self.left_group, self.middle_group, self.right_group):
            group.setStyleSheet(card_qss)

        # 右栏已改成纯工具栏（没有标题了），只剩中栏的标题需要上样式
        self.middle_title.setStyleSheet(self._group_title_qss(title_color))
        # 「语音管理 ▾」是 QToolButton，样式对齐编辑页的「项目管理 ▾」：
        # - padding 归零（靠 QToolButton 自带的 ~13px 内部水平边距对齐）
        # - ::menu-indicator 置空，去掉 Qt 默认画的那个暗色小三角
        #   （文本里已经有 ▾，两个箭头会并存）
        self.voice_menu_btn.setStyleSheet(
            f"QToolButton#VoiceMenuBtn {{"
            f" background: transparent;"
            f" border: none;"
            f" color: {title_color};"
            f" font-weight: bold;"
            f" font-size: 16px;"
            f" padding: 0px 0px;"
            f"}}"
            f"QToolButton#VoiceMenuBtn::menu-indicator {{"
            f" image: none;"
            f" width: 0px;"
            f" height: 0px;"
            f"}}"
        )
        # 下拉菜单样式：与编辑页「项目管理 ▾」完全一致
        self._apply_voice_menu_theme(is_dark)
        self.case_label.setStyleSheet(
            f"color: {hint_color}; font-size: 12px; background: transparent;")

        # 注意：页面本体不铺底色，让 MainWindow 的灰底/壁纸透上来，
        # 三张卡片才会像编辑页那样"浮"在页面上。
        # 三张卡片（分组框）与分组标题的样式在上面按「主题 × 壁纸」单独下发，
        # 这里不再统一指定 —— 也不要往下面这段 QSS 里写 # 开头的注释，
        # QSS 的注释是 /* */，# 会被当成选择器导致后面的规则全部失效。
        self.setStyleSheet(f"""
            #VoiceView {{ background: transparent; }}
            #VoiceView QLabel {{
                color: {text}; background: transparent; font-size: 13px;
            }}
            #VoiceView QLabel#VoiceStatus {{ color: {muted}; font-size: 12px; }}
            #VoiceView QLineEdit, #VoiceView QSpinBox, #VoiceView QDoubleSpinBox {{
                background-color: {input_bg};
                color: {text};
                border: 1px solid {btn_border};
                border-radius: 4px;
                padding: 3px 8px;
                min-height: 20px;
                font-size: 13px;
            }}
            /* 树自身：带底色 + 圆角；与编辑页的项目树 / 步骤列表同款 */
            #VoiceView QTreeWidget#VoiceTree, #VoiceView QTreeWidget#VoiceCheckTree {{
                background-color: {tree_bg};
                border: 1px solid {tree_border};
                border-radius: 6px;
                color: {text};
                font-size: 13px;
                outline: none;
                padding: 4px;
            }}
            /* viewport 会被 QTreeView 单独绘制一层底，必须显式透明，
               否则圆角处会露出直角方块的底色 */
            #VoiceView QTreeWidget#VoiceTree::viewport,
            #VoiceView QTreeWidget#VoiceCheckTree::viewport {{
                background: transparent;
            }}
            #VoiceView QTreeWidget#VoiceTree::item,
            #VoiceView QTreeWidget#VoiceCheckTree::item {{
                padding: 4px 2px;
                background: transparent;
            }}
            #VoiceView QTreeWidget#VoiceTree::item:hover,
            #VoiceView QTreeWidget#VoiceCheckTree::item:hover {{
                background: {row_hover};
            }}
            /* 分组 / 用例的选中态：跟「项目管理」的树保持一致（浅蓝底 + 深色字，
               深色主题是深蓝底 + 白字）。四个伪状态都要写 —— 树失焦时 Qt 会换成
               另一套高亮色，只写 :selected 会出现"点了别处选中行就变样" */
            #VoiceView QTreeWidget#VoiceTree::item:selected,
            #VoiceView QTreeWidget#VoiceTree::item:selected:active,
            #VoiceView QTreeWidget#VoiceTree::item:selected:!active,
            #VoiceView QTreeWidget#VoiceTree::item:selected:focus {{
                background: {tree_sel_bg}; color: {tree_sel_text};
            }}
            #VoiceView QTreeWidget#VoiceCheckTree::item:selected {{
                background: transparent; color: {text};
            }}
            #VoiceView QScrollArea#VoiceScroll, #VoiceView QWidget#VoiceList {{
                background: transparent; border: none;
            }}
            #VoiceView QScrollBar:vertical {{
                width: 6px; background: transparent; border-radius: 3px;
            }}
            #VoiceView QScrollBar::handle:vertical {{
                background: {scroll_handle}; border-radius: 3px; min-height: 20px;
            }}
            #VoiceView QScrollBar::add-line:vertical,
            #VoiceView QScrollBar::sub-line:vertical {{ height: 0px; }}
            #VoiceView QScrollBar::add-page:vertical,
            #VoiceView QScrollBar::sub-page:vertical {{ background: transparent; }}
            #VoiceView QFrame#PhraseRow {{
                background: transparent;
                border: none;
                border-bottom: 1px solid {row_border};
            }}
            #VoiceView QFrame#PhraseRow:hover {{ background: {row_hover}; }}
            #VoiceView QFrame#PhraseRow[playing="true"] {{ background: {row_playing}; }}
            #VoiceView QLabel#PhraseNo {{ color: {muted}; }}
            #VoiceView QLabel#VoiceEmpty {{ color: {muted}; padding: 16px 0; }}
            /* 本页所有按钮统一为「刷新」按钮样式：
               蓝底白字、圆角 4px、浅色/深色两套主题下观感一致。
               "唤醒词"、"+ 添加步骤"、"全选"、"取消全选"、"执行选中"
               以及行内的"播报 / 删除"按钮都走这条通用规则。 */
            #VoiceView QPushButton {{
                background: rgba(25, 118, 210, 0.9);
                color: white;
                border: 1px solid rgba(25, 118, 210, 0.9);
                border-radius: 4px;
                padding: 0px 12px;
                font-weight: 500;
                font-size: 12px;
                min-height: 24px;
            }}
            #VoiceView QPushButton:hover {{
                background: rgba(21, 101, 192, 0.95);
                border-color: rgba(21, 101, 192, 0.95);
            }}
            #VoiceView QPushButton:pressed {{
                background: rgba(13, 71, 161, 0.95);
                border-color: rgba(13, 71, 161, 0.95);
            }}
            #VoiceView QPushButton:disabled {{
                background: rgba(25, 118, 210, 0.35);
                color: rgba(255, 255, 255, 0.65);
                border-color: rgba(25, 118, 210, 0.2);
            }}
            /* "停止"单独走红色：它是打断动作，而且只在播报中可用，
               红底能让它在蓝按钮里一眼被找到（下面四条按上面的结构等比换色） */
            #VoiceView QPushButton#VoiceStopBtn {{
                background: rgba(231, 76, 60, 0.9);
                border-color: rgba(231, 76, 60, 0.9);
            }}
            #VoiceView QPushButton#VoiceStopBtn:hover {{
                background: rgba(211, 47, 47, 0.95);
                border-color: rgba(211, 47, 47, 0.95);
            }}
            #VoiceView QPushButton#VoiceStopBtn:pressed {{
                background: rgba(183, 28, 28, 0.95);
                border-color: rgba(183, 28, 28, 0.95);
            }}
            #VoiceView QPushButton#VoiceStopBtn:disabled {{
                background: rgba(231, 76, 60, 0.35);
                color: rgba(255, 255, 255, 0.65);
                border-color: rgba(231, 76, 60, 0.2);
            }}
            /* 行内图标按钮：无边框、透明底，hover 时浅色底 */
            #VoiceView QToolButton#PhrasePlayBtn,
            #VoiceView QToolButton#PhraseDelBtn {{
                background: transparent;
                border: none;
                padding: 0px;
            }}
            #VoiceView QToolButton#PhrasePlayBtn:hover,
            #VoiceView QToolButton#PhraseDelBtn:hover {{
                background: {btn_hover};
                border-radius: 4px;
            }}
            #VoiceView QToolButton#PhrasePlayBtn:disabled,
            #VoiceView QToolButton#PhraseDelBtn:disabled {{
                background: transparent;
            }}
        """)

        # 已存在的文案行的图标按钮也刷新一下图标颜色
        for row in self._rows:
            row.apply_theme(theme_mode)

        # 树节点复选框的边框色跟着主题走（delegate 是自绘的，QSS 管不到）
        if getattr(self, "_check_delegate", None) is not None:
            self._check_delegate.set_border_colors(
                check_border_color,
                "#ffffff" if is_dark else "#ffffff",
            )
        # 强制树重绘，边框色立即生效
        if getattr(self, "check_tree", None) is not None:
            self.check_tree.viewport().update()

        # 两排动作按钮等宽（要放在 QSS 下发之后，sizeHint 才按新字号算）
        if getattr(self, "wake_btn", None) is not None:
            self._sync_action_button_widths()

