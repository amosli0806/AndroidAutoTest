# views/step_list_view.py
import os

import qtawesome as qta
from PyQt6.QtWidgets import QListWidget, QListWidgetItem, QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QSizePolicy
from PyQt6.QtCore import pyqtSignal, Qt, QMimeData, QTimer, QSize
from PyQt6.QtGui import QDrag, QResizeEvent
from models.step_model import Step
from utils.icons import IconManager
from utils.flow_layout import FlowLayout
from utils.theme import Theme, ThemeMode
from utils.settings import Settings


class ParamChipLabel(QLabel):
    """参数 chip 专用 QLabel。
    覆写 sizeHint / minimumSizeHint，保证高度不低于 20px，
    否则 FlowLayout 会用 QLabel 的默认 sizeHint（不含 QSS padding/border），
    导致 chip 被压扁、圆角被裁切。"""

    MIN_HEIGHT = 20

    def sizeHint(self):
        s = super().sizeHint()
        s.setHeight(max(s.height(), self.MIN_HEIGHT))
        return s

    def minimumSizeHint(self):
        s = super().minimumSizeHint()
        s.setHeight(max(s.height(), self.MIN_HEIGHT))
        return s

class StepListView(QListWidget):
    step_dropped = pyqtSignal(int, int)
    update_step = pyqtSignal(int)
    delete_step = pyqtSignal(int)
    duplicate_step = pyqtSignal(int)
    recording_state_changed = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("StepListView")
        self.element_controller = None
        self._current_steps = []
        self._all_steps = []
        self._filter_text = ""
        self._original_steps = []
        self._current_theme = ThemeMode.LIGHT
        self._has_wallpaper = False
        self._refresh_timer = QTimer()
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self._refresh_visible_items)

        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.setSpacing(2)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # 注意：选中高亮清除 + 滚动条样式统一由 apply_theme 通过主题机制设置
        # 见 theme.py 的 STEP_LIST_LIGHT / STEP_LIST_DARK

        # 占位标签
        self.placeholder_label = QLabel("请创建用例，添加步骤", self)
        self.placeholder_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder_label.setStyleSheet("color: #999; font-size: 16px;")
        self.placeholder_label.hide()

        self.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self.currentItemChanged.connect(self._on_current_item_changed)
        # 初始化时直接应用一次滚动条样式（亮色），保证即使 apply_theme 未触发也有效
        self._apply_scrollbar_style(ThemeMode.LIGHT)
        # 初始化时也屏蔽选中高亮，防止第一次选中时露出 #1976d2
        self._disable_selection_highlight()

    def apply_theme(self, theme_mode: ThemeMode):
        Theme.apply_theme_to_widget(self, theme_mode)
        self._current_theme = theme_mode
        self._apply_scrollbar_style(theme_mode)
        self._disable_selection_highlight()
        path = Settings.get_wallpaper_path()
        self._has_wallpaper = bool(path and os.path.exists(path))

        # 直接清空步骤列表（不重建）
        self._clear_all_cards()

    def _clear_all_cards(self):
        """清空列表，彻底销毁所有 itemWidget"""
        self.blockSignals(True)
        try:
            # 从后往前遍历，每处理一个就 takeItem 移除，避免索引错乱
            for i in range(self.count() - 1, -1, -1):
                item = self.takeItem(i)
                if item is None:
                    continue
                w = self.itemWidget(item)
                if w is not None:
                    # 关键：立即 hide，避免异步删除期间视觉残留
                    w.hide()
                    self.removeItemWidget(item)
                    w.setParent(None)
                    w.deleteLater()
                del item
            # 兜底再 clear 一次
            self.clear()
            self.setCurrentItem(None)
        finally:
            self.blockSignals(False)

        # 清空所有缓存数据，避免后续 refresh_steps 把旧卡片重建回来
        self._all_steps = []
        self._original_steps = []
        self._current_steps = []
        self._filter_text = ""

        # 显示占位提示
        self.placeholder_label.show()
        self.placeholder_label.setGeometry(self.viewport().rect())
        self.placeholder_label.raise_()

        self.viewport().update()
        self.update()
        self.repaint()

    def _disable_selection_highlight(self):
        """从 palette 层面把选中高亮色和文字色改成透明，
        彻底避免 QListWidget 绘制 #1976d2 背景高亮"""
        from PyQt6.QtGui import QPalette, QColor
        pal = self.palette()
        # 用透明的颜色取代默认的 Highlight（#1976d2）
        pal.setColor(QPalette.ColorRole.Highlight, QColor(0, 0, 0, 0))
        pal.setColor(QPalette.ColorRole.HighlightedText, QColor(0, 0, 0, 0))
        # 应用到自身和 viewport
        self.setPalette(pal)
        vp = self.viewport()
        if vp is not None:
            vp.setPalette(pal)
            vp.setAutoFillBackground(False)

    def _apply_scrollbar_style(self, theme_mode: ThemeMode):
        """单独给垂直滚动条设置样式，并强制 Qt 重新应用"""
        if theme_mode == ThemeMode.DARK:
            sb_bg = "#3a3a3a"
            sb_handle = "#666"
            sb_handle_hover = "#888"
        else:
            sb_bg = "#e0e0e0"
            sb_handle = "#c0c0c0"
            sb_handle_hover = "#a0a0a0"

        sb = self.verticalScrollBar()
        if sb is None:
            return
        sb.setStyleSheet(f"""
            QScrollBar:vertical {{
                width: 6px;
                background: {sb_bg};
                border-radius: 3px;
                margin: 0px;
            }}
            QScrollBar::handle:vertical {{
                background: {sb_handle};
                border-radius: 3px;
                min-height: 20px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {sb_handle_hover};
            }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{
                height: 0px;
                width: 0px;
                background: transparent;
                border: none;
            }}
            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {{
                background: transparent;
            }}
            QScrollBar::up-arrow:vertical,
            QScrollBar::down-arrow:vertical {{
                background: transparent;
                border: none;
                width: 0px;
                height: 0px;
            }}
        """)
        # 关键：强制 Qt 重新应用样式表
        sb.style().unpolish(sb)
        sb.style().polish(sb)
        sb.update()
        sb.repaint()

    def _on_scroll(self, value):
        self._refresh_timer.start(50)

    def _refresh_visible_items(self):
        viewport = self.viewport()
        if not viewport:
            return
        rect = viewport.rect()
        for i in range(self.count()):
            item = self.item(i)
            if not item:
                continue
            pos = self.visualItemRect(item)
            if rect.intersects(pos):
                widget = self.itemWidget(item)
                if widget:
                    widget.update_display(i)
                    widget._refresh_layout()
        self.update()

    def set_element_controller(self, controller):
        self.element_controller = controller

    def on_element_changed(self):
        if self._all_steps:
            self.set_steps(self._all_steps)

    def set_recording_state(self, is_recording):
        self.recording_state_changed.emit(is_recording)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(50, self._refresh_visible_items)
        if self.placeholder_label.isVisible():
            self.placeholder_label.setGeometry(self.viewport().rect())
            self.placeholder_label.raise_()
        # 兜底：每次显示时重新应用滚动条样式（防止被系统重新初始化覆盖）
        QTimer.singleShot(0, lambda: self._apply_scrollbar_style(self._current_theme))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.placeholder_label.isVisible():
            self.placeholder_label.setGeometry(self.viewport().rect())
            self.placeholder_label.raise_()
        self._refresh_visible_items()

    def _on_current_item_changed(self, current, previous):
        for i in range(self.count()):
            item = self.item(i)
            widget = self.itemWidget(item)
            if widget:
                widget.set_selected(False)
        if current:
            widget = self.itemWidget(current)
            if widget:
                widget.set_selected(True)

    def set_steps(self, steps):
        self._original_steps = steps[:] if steps else []
        self._all_steps = steps
        self._filter_text = ""
        self._current_steps = steps[:]
        self._rebuild()

    def filter_steps(self, text):
        self._filter_text = text.strip().lower()
        if not self._filter_text:
            self.set_steps(self._original_steps if self._original_steps else self._all_steps)
            return
        type_names = {
            'click': '点击', 'double_click': '双击', 'long_press': '长按',
            'swipe': '滑动', 'drag_drop': '拖拽', 'multi_swipe': '多点滑动',
            'gesture_zoom': '手势缩放', 'flick': '飞掠', 'gesture_seq': '复杂手势',
            'physical_key': '物理按键', 'screen_ctrl': '屏幕控制', 'app_mgr': '应用管理',
            'screenshot': '截图', 'input': '输入', 'wait': '等待',
            'assert': '断言'
        }
        filtered = []
        for step in self._all_steps:
            if step.name and self._filter_text in step.name.lower():
                filtered.append(step)
                continue
            if step.type in type_names and self._filter_text in type_names[step.type].lower():
                filtered.append(step)
                continue
            match = False
            for key, value in step.params.items():
                if key == 'element_id':
                    continue
                if key == 'locationType' and isinstance(value, str):
                    if self._filter_text in value.lower():
                        match = True
                        break
                if isinstance(value, str) and self._filter_text in value.lower():
                    match = True
                    break
                elif isinstance(value, (int, float)) and self._filter_text in str(value):
                    match = True
                    break
            if match:
                filtered.append(step)
        self._current_steps = filtered
        self._rebuild()

    def _rebuild(self):
        scroll_bar = self.verticalScrollBar()
        scroll_value = scroll_bar.value() if scroll_bar else 0

        self.blockSignals(True)
        self.clear()
        self.blockSignals(False)

        if not self._current_steps:
            self.placeholder_label.show()
            self.placeholder_label.setGeometry(self.viewport().rect())
            self.placeholder_label.raise_()
        else:
            self.placeholder_label.hide()
            for step in self._current_steps:
                item = QListWidgetItem()
                widget = StepCardWidget(step, self.element_controller)
                widget._has_wallpaper = self._has_wallpaper
                widget.apply_theme(self._current_theme)
                widget.update_signal.connect(self.update_step)
                widget.delete_signal.connect(self.delete_step)
                widget.duplicate_signal.connect(self.duplicate_step)
                item.setSizeHint(widget.sizeHint())
                self.addItem(item)
                self.setItemWidget(item, widget)
            self.doItemsLayout()
        self.setCurrentItem(None)

        if scroll_bar:
            scroll_bar.setValue(scroll_value)

        QTimer.singleShot(10, self._refresh_items)
        QTimer.singleShot(100, self._refresh_items)

    def _refresh_items(self):
        self.doItemsLayout()
        self.updateGeometry()
        self.update()
        self.repaint()
        for i in range(self.count()):
            item = self.item(i)
            widget = self.itemWidget(item)
            if widget:
                widget.update_display(i)
                widget._refresh_layout()
                widget.updateGeometry()
                widget.update()
        self.update()

    def dropEvent(self, event):
        from_index = self.currentRow()
        super().dropEvent(event)
        to_index = self.currentRow()
        if from_index != to_index and from_index >= 0 and to_index >= 0:
            self.step_dropped.emit(from_index, to_index)
            self._refresh_visible_items()

    def scroll_to_top(self):
        scroll_bar = self.verticalScrollBar()
        if scroll_bar:
            scroll_bar.setValue(0)


class StepCardWidget(QWidget):
    update_signal = pyqtSignal(int)
    delete_signal = pyqtSignal(int)
    duplicate_signal = pyqtSignal(int)

    COLOR_MAP = {
        'click': '#3498db',
        'double_click': '#9b59b6',
        'long_press': '#e67e22',
        'swipe': '#1abc9c',
        'drag_drop': '#8e44ad',
        'multi_swipe': '#16a085',
        'gesture_zoom': '#27ae60',
        'flick': '#2980b9',
        'gesture_seq': '#c0392b',
        'physical_key': '#7f8c8d',
        'screen_ctrl': '#2c3e50',
        'app_mgr': '#d35400',
        'screenshot': '#e91e63',
        'input': '#f39c12',
        'wait': '#95a5a6',
        'assert': '#ff9800',
    }

    LABEL_MAP = {
        'locationType': '定位方式', 'locationValue': '定位值', 'duration': '时长(秒)',
        'text': '输入文字', 'longPressMs': '长按毫秒', 'startX': '起始X', 'startY': '起始Y',
        'endX': '结束X', 'endY': '结束Y', 'keyword': '搜索关键字',
        'fromLocationType': '源定位方式', 'fromValue': '源定位值',
        'toLocationType': '目标定位方式', 'toValue': '目标定位值',
        'direction': '方向', 'points': '路径点', 'durationMs': '总时长(毫秒)',
        'gestureType': '手势类型', 'centerX': '中心点X', 'centerY': '中心点Y',
        'scale': '缩放比例', 'distance': '飞掠距离(像素)', 'velocity': '速度(像素/秒)',
        'sequenceId': '手势序列编号', 'description': '手势描述',
        'keyName': '按键名称', 'action': '操作', 'packageName': '包名/应用名',
        'apkPath': '安装包路径', 'savePath': '保存路径', 'fileName': '文件名前缀',
        'assert_type': '断言类型', 'expected_value': '预期值', 'timeout': '超时(秒)',
        'element_id': '元素ID'
    }

    # 参数 chip 的短标签（用于步骤卡片上的紧凑展示）
    COMPACT_LABEL_MAP = {
        'duration': '时长',
        'text': '文本',
        'longPressMs': '长按',
        'startX': 'X1', 'startY': 'Y1',
        'endX': 'X2', 'endY': 'Y2',
        'direction': '方向',
        'points': '路径',
        'durationMs': '时长',
        'gestureType': '手势',
        'centerX': '中心X', 'centerY': '中心Y',
        'scale': '比例',
        'distance': '距离', 'velocity': '速度',
        'sequenceId': '序号', 'description': '描述',
        'keyName': '按键', 'action': '操作',
        'packageName': '包名', 'apkPath': 'APK',
        'savePath': '路径', 'fileName': '前缀',
        'assert_type': '断言', 'expected_value': '预期', 'timeout': '超时',
    }

    def __init__(self, step, element_controller=None, parent=None):
        super().__init__(parent)
        self.setObjectName("StepCard")
        self.step = step
        self._selected = False
        self._theme_mode = ThemeMode.LIGHT
        self._has_wallpaper = False
        self.element_controller = element_controller
        # 内容指纹，用于避免重复重建 chip
        self._content_signature = None
        # WA_StyledBackground 让 QSS 的 background/border 生效
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # 关键：禁用 palette 自动填充，否则会按矩形填充导致圆角失效
        self.setAutoFillBackground(False)
        self.setMinimumWidth(280)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.setup_ui()
        self.update_style()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(10, 10, 10, 10)

        row1 = QHBoxLayout()
        row1.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self.drag_btn = QPushButton()
        self.drag_btn.setIcon(IconManager.get_icon('drag'))
        self.drag_btn.setFixedSize(20, 20)
        self.drag_btn.setStyleSheet("border: none; background: transparent;")
        self.drag_btn.setCursor(Qt.CursorShape.OpenHandCursor)
        self.drag_btn.mousePressEvent = self._start_drag
        row1.addWidget(self.drag_btn)

        self.index_label = QLabel()
        self.index_label.setFixedSize(20, 20)
        self.index_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.index_label.setStyleSheet("background-color: #1976d2; color: white; border-radius: 10px; font-weight: bold; font-size: 10px;")
        row1.addWidget(self.index_label)

        self.type_label = QLabel()
        self.type_label.setFixedHeight(20)
        row1.addWidget(self.type_label)

        self.name_label = QLabel()
        self.name_label.setStyleSheet("font-weight: 600; font-size: 12px;")
        self.name_label.setWordWrap(False)
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self.name_label.setMinimumHeight(20)
        self.name_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        row1.addWidget(self.name_label, 1)
        layout.addLayout(row1)

        layout.addSpacing(4)

        params_container = QWidget()
        params_container.setObjectName("ParamsContainer")
        params_container.setStyleSheet(
            "#ParamsContainer { background: transparent; }"
        )
        self.params_layout = FlowLayout(params_container, margin=0, spacing=2)
        # 关键：给最小高度兜底，避免 FlowLayout 被压扁导致 chip 底部被裁
        params_container.setMinimumHeight(24)
        layout.addWidget(params_container)

        row3 = QHBoxLayout()
        row3.addStretch()

        self.update_btn = QPushButton()
        self.update_btn.setIcon(qta.icon('fa6s.pen-to-square', color='#555555'))
        self.update_btn.setFixedSize(20, 20)
        self.update_btn.setStyleSheet("border: none; background: transparent;")
        self.update_btn.clicked.connect(lambda: self.update_signal.emit(self.step.id))
        row3.addWidget(self.update_btn)

        self.copy_btn = QPushButton()
        self.copy_btn.setIcon(qta.icon('fa6s.copy', color='#555555'))
        self.copy_btn.setFixedSize(20, 20)
        self.copy_btn.setStyleSheet("border: none; background: transparent;")
        self.copy_btn.clicked.connect(lambda: self.duplicate_signal.emit(self.step.id))
        row3.addWidget(self.copy_btn)

        self.delete_btn = QPushButton()
        self.delete_btn.setIcon(qta.icon('fa6s.trash-can', color='#e74c3c'))
        self.delete_btn.setFixedSize(20, 20)
        self.delete_btn.setStyleSheet("border: none; background: transparent;")
        self.delete_btn.clicked.connect(lambda: self.delete_signal.emit(self.step.id))
        row3.addWidget(self.delete_btn)

        layout.addLayout(row3)
        self.update_display()

    def set_selected(self, selected):
        if self._selected != selected:
            self._selected = selected
            self.update_style()

    def apply_theme(self, theme_mode):
        """设置卡片主题"""
        self._theme_mode = theme_mode
        self.update_style()

    def update_style(self):
        """根据主题和选中状态更新样式（选中只加粗边框，背景不变，圆角保持一致）"""
        from utils.settings import Settings, THEME_MODE_DARK
        import os as _os

        border_color = self.COLOR_MAP.get(self.step.type, '#888888')
        border_width = "3px" if self._selected else "1px"
        # 选中态边框更粗，圆角稍大，视觉上更协调
        radius = "10px" if self._selected else "8px"

        # 判断主题
        try:
            theme_mode = Settings.get_theme_mode()
        except Exception:
            theme_mode = "light"
        is_dark = (theme_mode == THEME_MODE_DARK)

        # 判断壁纸
        try:
            wp_path = Settings.get_wallpaper_path()
            has_wp = bool(wp_path and _os.path.exists(wp_path))
        except Exception:
            has_wp = False

        if is_dark:
            if has_wp:
                # 有壁纸时半透明，透出壁纸
                bg = "rgba(55, 55, 55, 0.85)"
            else:
                bg = "#373737"
            text_color = "#eee"
            hover_bg = "rgba(255, 255, 255, 0.1)"
        else:
            if has_wp:
                # 亮色 + 有壁纸：半透明
                bg = "rgba(232, 234, 237, 0.85)"
            else:
                bg = "#e8eaed"
            text_color = "#333"
            hover_bg = "rgba(0, 0, 0, 0.05)"

        # 用双重选择器（QWidget#StepCard + #StepCard），兼容性最好
        self.setStyleSheet(f"""
            QWidget#StepCard, #StepCard {{
                background-color: {bg};
                border: {border_width} solid {border_color};
                border-radius: {radius};
                margin: 1px 0px;
                padding: 2px;
            }}
            QWidget#StepCard QPushButton, #StepCard QPushButton {{
                border: none;
                background: transparent;
            }}
            QWidget#StepCard QPushButton:hover, #StepCard QPushButton:hover {{
                background-color: {hover_bg};
                border-radius: 4px;
            }}
        """)

        # ---- 根据主题刷新图标颜色 & name_label 颜色 ----
        # 统一用 is_dark（来自 Settings），保证与背景色判断一致
        if is_dark:
            icon_color = "#bbbbbb"
            name_color = "#eee"
            delete_color = "#ff6b6b"
        else:
            icon_color = "#555555"
            name_color = "#333"
            delete_color = "#e74c3c"

        # 拖拽手柄（绘制图标，颜色可定制）
        if hasattr(self, 'drag_btn') and self.drag_btn is not None:
            self.drag_btn.setIcon(IconManager.get_icon('drag', color=icon_color))
        # 编辑 / 复制按钮
        if hasattr(self, 'update_btn') and self.update_btn is not None:
            self.update_btn.setIcon(qta.icon('fa6s.pen-to-square', color=icon_color))
        if hasattr(self, 'copy_btn') and self.copy_btn is not None:
            self.copy_btn.setIcon(qta.icon('fa6s.copy', color=icon_color))
        # 删除按钮
        if hasattr(self, 'delete_btn') and self.delete_btn is not None:
            self.delete_btn.setIcon(qta.icon('fa6s.trash-can', color=delete_color))

        # 步骤名称文案颜色
        if hasattr(self, 'name_label') and self.name_label is not None:
            self.name_label.setStyleSheet(
                f"font-weight: 600; font-size: 12px; color: {name_color}; background: transparent;"
            )

        # 强制 Qt 重新应用样式表并立即重绘，
        # 否则切换主题后卡片可能等到下次交互（比如被选中）才变色
        try:
            self.style().unpolish(self)
            self.style().polish(self)
        except Exception:
            pass
        self.update()

    def _start_drag(self, event):
        list_widget = self.parent()
        if isinstance(list_widget, QListWidget):
            item = list_widget.itemAt(self.mapToParent(event.pos()))
            if item:
                list_widget.setCurrentItem(item)
                drag = QDrag(list_widget)
                mime = QMimeData()
                mime.setData("application/x-qabstractitemmodeldatalist", b"")
                drag.setMimeData(mime)
                drag.exec(Qt.DropAction.MoveAction)

    def _compute_content_signature(self):
        """计算内容指纹，用于判断是否需要重建 chip"""
        if not self.step:
            return None
        params = self.step.params or {}
        elem_id = params.get('element_id')
        elem_sig = None
        if elem_id and self.element_controller:
            elem = self.element_controller.get_element_by_id(elem_id)
            if elem:
                elem_sig = (elem.loc_type, elem.loc_value, elem.name)
        # 排序后的参数列表作为指纹的一部分
        try:
            param_items = tuple(
                sorted((k, str(v)) for k, v in params.items() if k != 'element_id')
            )
        except Exception:
            param_items = tuple(params.items())
        return (
            self.step.type,
            self.step.name,
            elem_id,
            elem_sig,
            param_items,
        )

    def update_display(self, index=None):
        if not self.step:
            return

        # 索引更新（便宜，总是执行）
        if index is not None:
            self.index_label.setText(str(index + 1))

        # 内容指纹比对，未变化则跳过重建（优化刷新性能）
        sig = self._compute_content_signature()
        if sig == self._content_signature:
            return
        self._content_signature = sig

        # 内容变化，重建显示
        self._refresh_content()

    def _refresh_content(self):
        """重建 type_label、name_label、参数 chips"""
        # 1. type_label
        type_names = {
            'click': '点击', 'double_click': '双击', 'long_press': '长按',
            'swipe': '滑动', 'drag_drop': '拖拽', 'multi_swipe': '多点滑动',
            'gesture_zoom': '手势缩放', 'flick': '飞掠', 'gesture_seq': '复杂手势',
            'physical_key': '物理按键', 'screen_ctrl': '屏幕控制', 'app_mgr': '应用管理',
            'screenshot': '截图', 'input': '输入', 'wait': '等待',
            'assert': '断言'
        }
        self.type_label.setText(type_names.get(self.step.type, self.step.type))
        self.type_label.setStyleSheet(
            f"padding: 1px 6px; border-radius: 8px; font-weight: 600; color: white; "
            f"background-color: {self.COLOR_MAP.get(self.step.type, '#333')}; font-size: 11px;"
        )

        # 2. name_label
        elem_id = self.step.params.get('element_id')
        display_name = ""
        if elem_id and self.element_controller:
            elem = self.element_controller.get_element_by_id(elem_id)
            if elem:
                display_name = f"元素：{elem.name}"
            else:
                display_name = f"元素：{elem_id}（已失效）"
        else:
            if self.step.name:
                raw_name = self.step.name
                max_len = 17
                display_name = raw_name[:max_len] + "..." if len(raw_name) > max_len else raw_name

        # name_label 颜色从 Settings 直接读取，保证与背景色判断一致
        from utils.settings import Settings, THEME_MODE_DARK
        try:
            _is_dark = (Settings.get_theme_mode() == THEME_MODE_DARK)
        except Exception:
            _is_dark = (self._theme_mode == ThemeMode.DARK)
        name_color = "#eee" if _is_dark else "#333"
        self.name_label.setStyleSheet(
            f"font-weight: 600; font-size: 12px; color: {name_color}; background: transparent;"
        )
        self.name_label.setText(display_name)
        self.name_label.setVisible(bool(display_name))

        # 3. 参数 chips
        self._rebuild_param_chips()

    def _rebuild_param_chips(self):
        """重建参数 chip。使用紧凑格式：
        - 合并 locationType/locationValue 为一个 chip
        - 合并 fromLocationType/fromValue、toLocationType/toValue
        - 用短标签
        - 最多 4 个 chip
        """
        # 清空旧 chip
        while self.params_layout.count():
            item = self.params_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # 根据当前主题决定 chip 颜色（从 Settings 直接读，与背景色判断一致）
        from utils.settings import Settings, THEME_MODE_DARK
        try:
            _is_dark_chip = (Settings.get_theme_mode() == THEME_MODE_DARK)
        except Exception:
            _is_dark_chip = (self._theme_mode == ThemeMode.DARK)
        is_dark = _is_dark_chip
        if is_dark:
            chip_bg = "#4a4a4a"
            chip_fg = "#dcdcdc"
            chip_border = "#5a5a5a"
        else:
            chip_bg = "#eef1f5"
            chip_fg = "#4a5568"
            chip_border = "#d8dde3"

        # 用 ID 选择器 #ParamChip，确保优先级最高，不被父控件 QSS 污染
        chip_style = (
            f"#ParamChip {{"
            f" background-color: {chip_bg};"
            f" color: {chip_fg};"
            f" border: 1px solid {chip_border};"
            f" border-radius: 6px;"
            f" padding: 2px 8px;"
            f" font-size: 10px;"
            f"}}"
        )

        def make_chip(text, max_len=28):
            if len(text) > max_len:
                text = text[:max_len - 1] + "…"
            chip = ParamChipLabel(text)
            chip.setObjectName("ParamChip")  # ← 关键
            chip.setStyleSheet(chip_style)
            chip.setFixedHeight(20)
            chip.setMinimumWidth(chip.sizeHint().width())
            chip.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
            return chip

        chips = []  # (展示文本) 列表
        elem_id = self.step.params.get('element_id')
        params = self.step.params or {}

        # ---------- 合并定位参数 ----------
        # 1) locationType / locationValue
        loc_type = params.get('locationType')
        loc_value = params.get('locationValue')
        if not loc_type and elem_id and self.element_controller:
            elem = self.element_controller.get_element_by_id(elem_id)
            if elem:
                loc_type = elem.loc_type
                loc_value = elem.loc_value
        if loc_type or loc_value:
            if loc_type and loc_value:
                chips.append(f"{loc_type}: {loc_value}")
            elif loc_value:
                chips.append(f"{loc_value}")

        # 2) fromLocationType / fromValue（拖拽起点）
        from_type = params.get('fromLocationType')
        from_value = params.get('fromValue')
        if from_type or from_value:
            if from_type and from_value:
                chips.append(f"起点 {from_type}: {from_value}")
            elif from_value:
                chips.append(f"起点 {from_value}")

        # 3) toLocationType / toValue（拖拽终点）
        to_type = params.get('toLocationType')
        to_value = params.get('toValue')
        if to_type or to_value:
            if to_type and to_value:
                chips.append(f"终点 {to_type}: {to_value}")
            elif to_value:
                chips.append(f"终点 {to_value}")

        # ---------- 其余参数 ----------
        skip_keys = {
            'locationType', 'locationValue',
            'fromLocationType', 'fromValue',
            'toLocationType', 'toValue',
            'element_id',
            # 归一化相关的内部字段，不在卡片上展示
            'screenWidth', 'screenHeight',
            'normalizedX', 'normalizedY',
            'normalizedStartX', 'normalizedStartY',
            'normalizedEndX', 'normalizedEndY',
        }
        for key, value in params.items():
            if key in skip_keys:
                continue
            if value is None or value == '':
                continue
            label = self.COMPACT_LABEL_MAP.get(key, key)
            chips.append(f"{label}: {value}")

        # ---------- 添加到布局，最多 4 个 ----------
        max_chips = 4
        for text in chips[:max_chips]:
            self.params_layout.addWidget(make_chip(text))

        QTimer.singleShot(10, self._refresh_layout)

    def _refresh_layout(self):
        self.params_layout.update()
        self.updateGeometry()
        self._update_item_size()

    def _update_item_size(self):
        parent_list = self.parent()
        if isinstance(parent_list, QListWidget):
            for i in range(parent_list.count()):
                item = parent_list.item(i)
                if parent_list.itemWidget(item) == self:
                    new_hint = self.sizeHint()
                    max_width = max(parent_list.viewport().width() - 4, self.minimumWidth())
                    if new_hint.width() > max_width:
                        new_hint.setWidth(max_width)
                    if item.sizeHint() != new_hint:
                        item.setSizeHint(new_hint)
                        parent_list.doItemsLayout()
                    break

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.params_layout.update()
        self.updateGeometry()
        self._update_item_size()

    def showEvent(self, event):
        super().showEvent(event)
        self.params_layout.update()
        self.updateGeometry()
        self._update_item_size()