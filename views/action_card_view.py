# views/action_card_view.py
import os
import tempfile

import qtawesome as qta
from PyQt6.QtWidgets import (QScrollArea, QWidget, QGridLayout, QGroupBox,
                             QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
                             QLineEdit, QSpinBox, QDoubleSpinBox, QPushButton,
                             QSizePolicy, QMessageBox, QToolButton,
                             QWIDGETSIZE_MAX)
from PyQt6.QtCore import pyqtSignal, Qt, QPoint
from PyQt6.QtGui import QPixmap, QPainter, QColor, QPolygon

from utils.dialogs import WarningDialog
from utils.icons import IconManager
from utils.settings import Settings
from models.voice_model import get_wake_word
from views.element_selector_dialog import ElementSelectorDialog
from utils.toast import show_toast
from utils.theme import Theme, ThemeMode


def _get_spinbox_arrow_url(color: str, direction: str) -> str:
    """用 QPainter 生成箭头 PNG 到临时目录，返回 QSS 可用的 file:// URL。
    比 data:image/svg+xml 兼容性更稳（Qt 各平台都支持 PNG 文件）。"""
    tmp_dir = tempfile.gettempdir()
    color_key = color.lstrip('#').replace('/', '_').replace('(', '').replace(')', '') \
        .replace(',', '_').replace(' ', '')
    filename = f"uc_spin_arrow_{direction}_{color_key}.png"
    filepath = os.path.join(tmp_dir, filename)

    if not os.path.exists(filepath):
        pixmap = QPixmap(12, 12)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor(color))
        painter.setPen(Qt.PenStyle.NoPen)
        if direction == 'up':
            pts = [QPoint(6, 2), QPoint(10, 9), QPoint(2, 9)]
        else:
            pts = [QPoint(6, 10), QPoint(2, 3), QPoint(10, 3)]
        painter.drawPolygon(QPolygon(pts))
        painter.end()
        pixmap.save(filepath, "PNG")

    url = filepath.replace('\\', '/')
    return f"file:///{url}"

class ActionCardView(QScrollArea):
    add_step_signal = pyqtSignal(str, dict, str)

    # 卡片区每行放几张（原来写死在 _build_cards 里的 max_cols）
    CARD_COLUMNS = 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ActionCardView")
        self.element_controller = None
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.container = QWidget()
        self.container.setObjectName("ActionCardScrollContainer")
        self.container.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Minimum)
        self.setWidget(self.container)
        # QScrollArea.setWidget() 会给内层 widget 打开 autoFillBackground，它会用调色板
        # 底色（浅色 #f5f6fa / 深色 #3c3c3c）刷一层实底：既跟「动作卡片」分组区域的底色
        # 有色差，有壁纸时还会把壁纸整块挡掉。这里关掉，底色统一由分组区域决定
        # （配合主题里的 #ActionCardScrollContainer { background: transparent; }）。
        self.container.setAutoFillBackground(False)
        self.layout = QGridLayout(self.container)
        self.layout.setSpacing(10)
        self.layout.setHorizontalSpacing(10)
        self.layout.setVerticalSpacing(10)
        self.layout.setColumnStretch(0, 0)
        self.layout.setColumnStretch(1, 0)
        # 按展示顺序保存卡片控件：隐藏时**只从布局里摘掉、不销毁**，
        # 所以已经填好的参数还在，重新勾上就原样回来
        self._cards = []
        self._visible_types = set()
        # 一张卡片都不显示时顶上这句（放在网格里居中）
        self._empty_hint = QLabel("已隐藏全部动作卡片，点上方「动作卡片 ▾」重新勾选")
        self._empty_hint.setObjectName("ActionCardEmptyHint")
        self._empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_hint.setStyleSheet(
            "color: #999; font-size: 14px; background: transparent;")
        self._empty_hint.hide()
        self._wake_word_applied = ""     # 上次自动填进「播报文案」的唤醒词
        self._build_cards()
        self._visible_types = self._load_visible_types()
        # 全部显示时 _build_cards 已经把位置排好了，不用再走一遍重排
        if len(self._visible_types) != len(self._cards):
            self._relayout_cards()
        # 移除滚动条样式，由主题控制

    def set_element_controller(self, controller):
        """设置元素控制器，用于选择元素"""
        self.element_controller = controller
        # 走 self._cards 而不是 self.layout：隐藏的卡片不在布局里，但重新勾上后
        # 一样要能选元素，所以控制器得给它装上
        for card in self._cards:
            card.set_element_controller(controller)

    def apply_theme(self, theme_mode: ThemeMode):
        """应用主题到所有动作卡片（保留彩色标题）"""
        # 应用自身滚动条样式
        Theme.apply_theme_to_widget(self, theme_mode)
        # 同上：隐藏的卡片也要跟着换主题，否则重新勾上时是旧配色
        for card in self._cards:
            card.apply_theme(theme_mode)

    def refresh_voice_default(self):
        """把「语音播报」卡片的播报文案刷成当前配置的唤醒词。

        设置里改完唤醒词后由主窗口调用（见 MainWindow.on_settings）。只在用户
        **没动过**这个输入框时才刷（空着，或还是上次自动填进去的那句唤醒词），
        免得把用户手打的指令覆盖掉。
        """
        card = next((c for c in self._cards if c.config['type'] == 'voice'), None)
        if card is None:
            return
        field = card.fields.get('voiceText')
        if not isinstance(field, QLineEdit):
            return
        current = field.text().strip()
        if current and current != self._wake_word_applied:
            return
        self._wake_word_applied = get_wake_word()
        field.setText(self._wake_word_applied)

    # ------------------------------------------------------------------
    # 「动作卡片 ▾」：显示哪些卡片（勾选结果记在 config.json）
    # ------------------------------------------------------------------
    def card_types(self) -> list:
        """按展示顺序返回所有卡片类型"""
        return [card.config['type'] for card in self._cards]

    def card_titles(self) -> dict:
        """{卡片类型: 标题}，给下拉菜单生成菜单项用"""
        return {card.config['type']: card.config['title'] for card in self._cards}

    def visible_card_types(self) -> set:
        """当前显示的卡片类型"""
        return set(self._visible_types)

    def set_visible_card_types(self, types, persist=True) -> bool:
        """设置要显示哪些卡片（None = 全部），并重排卡片区。

        persist=True 时把勾选结果写进 config.json（下次打开应用照旧）。
        返回是否真的发生了变化。
        """
        all_types = self.card_types()
        if types is None:
            new_types = set(all_types)
        else:
            # 过滤掉已经不存在的类型（卡片清单以后有增删时，老配置不会带坏界面）
            new_types = {t for t in types if t in all_types}
        if new_types == self._visible_types:
            return False
        self._visible_types = new_types
        if persist:
            Settings.save_visible_action_cards(sorted(new_types))
        self._relayout_cards()
        return True

    def _load_visible_types(self) -> set:
        """读上次勾选结果；没记录 = 全部显示"""
        all_types = self.card_types()
        saved = Settings.load_visible_action_cards()
        if saved is None:
            return set(all_types)
        return {t for t in saved if t in all_types}

    def _relayout_cards(self):
        """按当前显示范围重排卡片区：只把要显示的卡片放进网格，剩下的藏起来。

        两个实现要点：
        1. **只摘不销毁**（takeAt 不会删除控件），用户填到一半的参数不会因为勾掉
           别的卡片而丢；
        2. 复用同一个 QGridLayout，别重建。实测（Qt6/offscreen）takeAt 之后
           rowCount 不会回收，但空行不产生幽灵间距 —— "18 张摘到剩 3 张"与
           "一开始就摆 3 张"的 sizeHint().height() 完全相同（72 == 72）；
           而重建布局要用 QWidget.setLayout 摘旧布局，那会把卡片 reparent 到
           临时宿主上，宿主一销毁卡片就跟着没了。
        """
        while self.layout.count():
            self.layout.takeAt(0)

        # 复位高度：上一轮"同行等高"打的固定高度要清掉，否则单独一行时会留一条空档
        for card in self._cards:
            card.setMinimumHeight(0)
            card.setMaximumHeight(QWIDGETSIZE_MAX)

        row = col = 0
        row_cards = []
        for card in self._cards:
            if card.config['type'] not in self._visible_types:
                card.hide()
                continue
            card.show()
            self.layout.addWidget(card, row, col)
            if col == 0:
                row_cards.append([card])
            else:
                row_cards[-1].append(card)
            col += 1
            if col >= self.CARD_COLUMNS:
                col = 0
                row += 1

        # 同行两个卡片高度一致（与 _build_cards 里的处理保持一致）
        for cards in row_cards:
            if len(cards) == 2:
                max_h = max(c.sizeHint().height() for c in cards)
                cards[0].setFixedHeight(max_h)
                cards[1].setFixedHeight(max_h)

        if row_cards:
            self._empty_hint.hide()
        else:
            self.layout.addWidget(self._empty_hint, 0, 0, 1, self.CARD_COLUMNS)
            self._empty_hint.show()


    def _build_cards(self):
        # 语音播报卡片的默认文案 = 当前配置的唤醒词（设置 → 语音播报 → 唤醒词）。
        # 记下这个值：用户没动过输入框时，设置里改了唤醒词要能跟着刷新（见 refresh_voice_default）
        wake_word = get_wake_word()
        self._wake_word_applied = wake_word
        configs = [
            {'type': 'click', 'title': '点击', 'fields': [
                {'key': 'locationType', 'label': '定位方式：', 'type': 'select',
                 'options': ['资源ID', '坐标', '文本', '描述', 'XPath']},
                {'key': 'locationValue', 'label': '定 位 值 ：', 'type': 'line', 'required': True},
                {'key': 'timeout', 'label': '超时秒数：', 'type': 'spin', 'default': 10}
            ]},
            {'type': 'double_click', 'title': '双击', 'fields': [
                {'key': 'locationType', 'label': '定位方式：', 'type': 'select',
                 'options': ['资源ID', '坐标', '文本', '描述', 'XPath']},
                {'key': 'locationValue', 'label': '定 位 值 ：', 'type': 'line', 'required': True},
                {'key': 'timeout', 'label': '超时秒数：', 'type': 'spin', 'default': 10}
            ]},
            {'type': 'long_press', 'title': '长按', 'fields': [
                {'key': 'locationType', 'label': '定位方式：', 'type': 'select',
                 'options': ['资源ID', '坐标', '文本', '描述', 'XPath']},
                {'key': 'locationValue', 'label': '定 位 值 ：', 'type': 'line', 'required': True},
                {'key': 'longPressMs', 'label': '长按毫秒：', 'type': 'spin', 'default': 1000},
                {'key': 'timeout', 'label': '超时秒数：', 'type': 'spin', 'default': 10}
            ]},
            {'type': 'swipe', 'title': '滑动', 'fields': [
                {'key': 'direction', 'label': '方 向 ：', 'type': 'select',
                 'options': ['上滑', '下滑', '左滑', '右滑', '自定义坐标']},
                {'key': 'startX', 'label': '起始X：', 'type': 'spin', 'default': 500},
                {'key': 'startY', 'label': '起始Y：', 'type': 'spin', 'default': 800},
                {'key': 'endX', 'label': '结束X：', 'type': 'spin', 'default': 500},
                {'key': 'endY', 'label': '结束Y：', 'type': 'spin', 'default': 200}
            ]},
            {'type': 'drag_drop', 'title': '拖拽', 'fields': [
                {'key': 'fromLocationType', 'label': '起点方式：', 'type': 'select',
                 'options': ['资源ID', '坐标', '文本', '描述', 'XPath']},
                {'key': 'fromValue', 'label': '定 位 值 ：', 'type': 'line', 'required': True},
                {'key': 'toLocationType', 'label': '终点方式：', 'type': 'select',
                 'options': ['资源ID', '坐标', '文本', '描述', 'XPath']},
                {'key': 'toValue', 'label': '定 位 值 ：', 'type': 'line', 'required': True}
            ]},
            {'type': 'multi_swipe', 'title': '多点滑动', 'fields': [
                {'key': 'points', 'label': '路 径 点 ：', 'type': 'line', 'default': '100,1000;500,1000;900,1000',
                 'required': True},
                {'key': 'durationMs', 'label': '时长毫秒：', 'type': 'spin', 'default': 1000}
            ]},
            {'type': 'gesture_zoom', 'title': '手势（捏合/放大）', 'fields': [
                {'key': 'gestureType', 'label': '手势类型：', 'type': 'select', 'options': ['捏合（缩小）', '放大']},
                {'key': 'centerX', 'label': '中心点X ：', 'type': 'spin', 'default': 540},
                {'key': 'centerY', 'label': '中心点Y ：', 'type': 'spin', 'default': 960},
                {'key': 'scale', 'label': '缩放比例：', 'type': 'double', 'default': 1.5}
            ]},
            {'type': 'flick', 'title': '飞掠', 'fields': [
                {'key': 'direction', 'label': '  方  向  ：', 'type': 'select', 'options': ['上', '下', '左', '右']},
                {'key': 'distance', 'label': '距离像素：', 'type': 'spin', 'default': 300},
                {'key': 'velocity', 'label': '速度(px/s)', 'type': 'spin', 'default': 3000}
            ]},
            {'type': 'gesture_seq', 'title': '复杂手势序列', 'fields': [
                {'key': 'sequenceId', 'label': '序列编号：', 'type': 'line', 'default': 'pattern_01', 'required': True},
                {'key': 'description', 'label': '手势描述：', 'type': 'line', 'default': '自定义手势序列'}
            ]},
            {'type': 'physical_key', 'title': '物理按键', 'fields': [
                {'key': 'keyName', 'label': '按键名称：', 'type': 'select',
                 'options': ['返回', '主页', '菜单', '音量+', '音量-', '电源', '相机']}
            ]},
            {'type': 'screen_ctrl', 'title': '屏幕控制', 'fields': [
                {'key': 'action', 'label': '  操  作  ：', 'type': 'select',
                 'options': ['唤醒屏幕', '熄灭屏幕', '旋转屏幕', '固定方向(竖屏)', '固定方向(横屏)']}
            ]},
            {'type': 'app_mgr', 'title': '应用管理', 'fields': [
                {'key': 'action', 'label': '  操  作  ：', 'type': 'select',
                 'options': ['启动应用', '停止应用', '重启应用', '清空应用数据', '安装应用', '卸载应用']},
                {'key': 'packageName', 'label': '  包  名  ：', 'type': 'line', 'default': 'com.example.app', 'required': True},
                {'key': 'apkPath', 'label': '安装包路径', 'type': 'line'}
            ]},
            {'type': 'screenshot', 'title': '截图', 'fields': [
                {'key': 'savePath', 'label': ' 保存路径 ：', 'type': 'line', 'default': Settings.get_output_dir()},
                {'key': 'fileName', 'label': '文件名前缀', 'type': 'line', 'default': 'screenshot'}
            ]},
            {'type': 'input', 'title': '输入', 'fields': [
                {'key': 'locationType', 'label': '定位方式：', 'type': 'select',
                 'options': ['资源ID', '坐标', '文本', '描述', 'XPath']},
                {'key': 'locationValue', 'label': '定 位 值 ：', 'type': 'line', 'required': True},
                {'key': 'text', 'label': '输入文字：', 'type': 'line', 'required': True},
                {'key': 'timeout', 'label': '超时秒数：', 'type': 'spin', 'default': 10}
            ]},
            {'type': 'wait', 'title': '等待', 'fields': [
                {'key': 'duration', 'label': '  秒  数  ：', 'type': 'spin', 'default': 3, 'required': True}
            ]},
            {'type': 'assert', 'title': '断言', 'fields': [
                {'key': 'assert_type', 'label': '断言类型：', 'type': 'select',
                 'options': ['元素存在', '元素不存在', '文本等于', '文本包含']},
                {'key': 'locationType', 'label': '定位方式：', 'type': 'select',
                 'options': ['资源ID', '坐标', '文本', '描述', 'XPath']},
                {'key': 'locationValue', 'label': '定 位 值 ：', 'type': 'line', 'required': True},
                {'key': 'expected_value', 'label': '预 期 值 ：', 'type': 'line'},
                {'key': 'timeout', 'label': '超时秒数：', 'type': 'spin', 'default': 5}
            ]},
            {'type': 'voice', 'title': '语音播报', 'fields': [
                # 播报文案默认给当前配置的唤醒词（设置 → 语音播报 → 唤醒词）：
                # 用例里第一句通常就是唤醒词，省得每次手打一遍
                {'key': 'voiceText', 'label': '播报文案：', 'type': 'line', 'required': True,
                 'default': wake_word},
                {'key': 'afterDelay', 'label': '播后等待：', 'type': 'spin', 'default': 2}
            ]}
        ]

        # 按用户指定的固定顺序排列（每行两个卡片）
        ordered_types = [
            # 第一行
            'wait', 'click',
            # 第二行
            'app_mgr', 'input',
            # 第三行
            'swipe', 'assert',
            # 第四行
            'physical_key', 'screen_ctrl',
            # 第五行
            'long_press', 'flick',
            # 第六行
            'double_click', 'multi_swipe',
            # 第七行
            'drag_drop', 'gesture_zoom',
            # 第八行
            'gesture_seq', 'screenshot',
            # 第九行
            'voice',
        ]
        type_to_cfg = {c['type']: c for c in configs}
        configs_sorted = [type_to_cfg[t] for t in ordered_types if t in type_to_cfg]

        row, col = 0, 0
        max_cols = self.CARD_COLUMNS
        row_cards = []
        self._cards = []
        for cfg in configs_sorted:
            card = ActionCard(cfg)
            card.add_step.connect(self._on_add_step)
            card.setMaximumWidth(340)
            card.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
            self.layout.addWidget(card, row, col)
            self._cards.append(card)

            if col == 0:
                row_cards.append([card])
            else:
                row_cards[-1].append(card)

            col += 1
            if col >= max_cols:
                col = 0
                row += 1

        # 调整每行内两个卡片高度一致
        for cards in row_cards:
            if len(cards) == 2:
                h1 = cards[0].sizeHint().height()
                h2 = cards[1].sizeHint().height()
                max_h = max(h1, h2)
                cards[0].setFixedHeight(max_h)
                cards[1].setFixedHeight(max_h)

    def _on_add_step(self, action_type, params, name):
        self.add_step_signal.emit(action_type, params, name)


class ActionCard(QGroupBox):
    add_step = pyqtSignal(str, dict, str)

    # 颜色映射用于左侧色条，与主题无关
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
        'voice': '#00bcd4',
    }

    PLACEHOLDER_MAP = {
        '资源ID': '例如：com.example:id/button',
        '坐标': '例如：100,200',
        '文本': '输入要匹配的文本内容',
        '描述': '输入 content-description 内容',
        'XPath': '例如：//android.widget.Button[@text="确定"]'
    }

    def __init__(self, config, parent=None):
        super().__init__(config['title'], parent)
        self.setObjectName("ActionCard")  # 用于主题样式
        self.config = config
        self.fields = {}
        self.element_controller = None
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)

        # 标题样式（加粗、字号）
        title_font = self.font()
        title_font.setBold(True)
        title_font.setPointSize(10)
        self.setFont(title_font)

        # 初始化主题模式，并由 apply_theme 设置所有样式
        self._theme_mode = ThemeMode.LIGHT

        self.setup_ui()
        self._setup_placeholder_sync()
        self.apply_theme(self._theme_mode)

    def apply_theme(self, theme_mode):
        """应用主题，同时保留每种动作对应的彩色标题"""
        self._theme_mode = theme_mode
        color = self.COLOR_MAP.get(self.config['type'], '#888888')

        # 判断是否有壁纸
        from utils.settings import Settings
        import os as _os
        try:
            wp_path = Settings.get_wallpaper_path()
            has_wp = bool(wp_path and _os.path.exists(wp_path))
        except Exception:
            has_wp = False

        if theme_mode == ThemeMode.DARK:
            if has_wp:
                bg = "rgba(60, 60, 60, 0.85)"
            else:
                bg = "#373737"
            border = "#555"
            label_color = "#eee"
            input_bg = "rgba(45, 45, 45, 0.9)"
            input_border = "#bbb"
            input_color = "#eee"
            focus_color = "#90caf9"
            btn_bg = "#90caf9"
            btn_bg_hover = "#64b5f6"
            btn_bg_pressed = "#42a5f5"
            btn_text = "#1e1e1e"
            arrow_color = "#dcdcdc"
        else:
            if has_wp:
                bg = "rgba(232, 234, 237, 0.85)"
            else:
                bg = "#e8eaed"
            border = "#d0d0d0"
            label_color = "#333"
            input_bg = "#ffffff"
            input_border = "#b0b0b0"
            input_color = "#333"
            focus_color = "#1976d2"
            btn_bg = "#1976d2"
            btn_bg_hover = "#1565c0"
            btn_bg_pressed = "#0d47a1"
            btn_text = "white"
            arrow_color = "#555555"

        self.setStyleSheet(f"""
            QGroupBox#ActionCard {{
                background-color: {bg};
                border: 1px solid {border};
                border-left: 3px solid {color};
                border-radius: 6px;
                margin-top: 6px;
                padding: 6px;
            }}
            QGroupBox#ActionCard::title {{
                color: {color};
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }}
            QGroupBox#ActionCard QLabel {{
                color: {label_color};
                font-size: 9pt;
                background: transparent;
            }}
            QGroupBox#ActionCard QLineEdit,
            QGroupBox#ActionCard QComboBox {{
                border: 1px solid {input_border};
                border-radius: 4px;
                padding: 4px 6px;
                background-color: {input_bg};
                color: {input_color};
                font-size: 9pt;
            }}
                        QGroupBox#ActionCard QComboBox QAbstractItemView {{
                background-color: {input_bg};
                color: {input_color};
                border: 1px solid {input_border};
                border-radius: 6px;
                selection-background-color: {focus_color};
                selection-color: white;
                outline: none;
                padding: 4px;
                margin: 0px;
            }}
            QGroupBox#ActionCard QComboBox QAbstractItemView::item {{
                background-color: {input_bg};
                color: {input_color};
                min-height: 24px;
                padding: 4px 10px;
                margin: 1px 2px;
                border: none;
                border-radius: 4px;
            }}
            QGroupBox#ActionCard QComboBox QAbstractItemView::item:hover {{
                background-color: {btn_bg_hover};
                color: white;
            }}
            QGroupBox#ActionCard QComboBox QAbstractItemView::item:selected {{
                background-color: {focus_color};
                color: white;
            }}
            QGroupBox#ActionCard QSpinBox,
            QGroupBox#ActionCard QDoubleSpinBox {{
                border: 1px solid {input_border};
                border-radius: 4px;
                padding: 4px 20px 4px 6px;
                background-color: {input_bg};
                color: {input_color};
                font-size: 9pt;
            }}
            QGroupBox#ActionCard QSpinBox:focus,
            QGroupBox#ActionCard QDoubleSpinBox:focus {{
                border-color: {focus_color};
            }}
            QGroupBox#ActionCard QLineEdit:focus,
            QGroupBox#ActionCard QSpinBox:focus,
            QGroupBox#ActionCard QDoubleSpinBox:focus,
            QGroupBox#ActionCard QComboBox:focus {{
                border-color: {focus_color};
            }}
            /* 添加按钮：固定保持白天模式的深蓝样式，不受主题影响 */
            QGroupBox#ActionCard QPushButton#ActionCardAddBtn {{
                background-color: #1976d2;
                border: none;
                border-radius: 4px;
                padding: 2px;
            }}
            QGroupBox#ActionCard QPushButton#ActionCardAddBtn:hover {{
                background-color: #1565c0;
            }}
            QGroupBox#ActionCard QPushButton#ActionCardAddBtn:pressed {{
                background-color: #0d47a1;
            }}
        """)
        # 强制 SpinBox 使用上下箭头
        from PyQt6.QtWidgets import QAbstractSpinBox
        for sb in self.findChildren(QSpinBox):
            sb.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
        for sb in self.findChildren(QDoubleSpinBox):
            sb.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)

        # 更新"选择元素"按钮的图标颜色（夜间模式下需要更亮的颜色才看得清）
        select_icon_color = "#bbbbbb" if theme_mode == ThemeMode.DARK else "#555555"
        for btn in self.findChildren(QToolButton):
            if btn.objectName() == "ElementSelectBtn":
                btn.setIcon(qta.icon('fa6s.folder-open', color=select_icon_color))

    def set_element_controller(self, controller):
        self.element_controller = controller

    def setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(6, 6, 6, 6)

        top_layout = QHBoxLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("步骤说明（非必填）")
        self.name_edit.setFixedHeight(28)
        top_layout.addWidget(self.name_edit)

        self.add_btn = QPushButton()
        self.add_btn.setObjectName("ActionCardAddBtn")
        self.add_btn.setIcon(IconManager.get_icon('add', color='#ffffff'))
        self.add_btn.setFixedSize(28, 28)
        self.add_btn.clicked.connect(self._emit_add)
        top_layout.addWidget(self.add_btn)
        main_layout.addLayout(top_layout)

        form_layout = QVBoxLayout()
        form_layout.setSpacing(10)
        row_groups = {}
        for field in self.config['fields']:
            row_key = field.get('row', None)
            row_groups.setdefault(row_key, []).append(field)

        for row_key, fields in row_groups.items():
            if row_key is None:
                for f in fields:
                    form_layout.addLayout(self._create_field_row(f))
            else:
                row_layout = QHBoxLayout()
                row_layout.setSpacing(10)
                for f in fields:
                    row_layout.addLayout(self._create_field_row(f))
                form_layout.addLayout(row_layout)
        main_layout.addLayout(form_layout)

    def _create_field_row(self, field):
        layout = QHBoxLayout()
        label = QLabel(field['label'])
        label.setFixedWidth(60)
        layout.addWidget(label)

        if field['type'] == 'select':
            widget = QComboBox()
            # 关键：设置 QListView 作为 view，QSS 的下拉面板样式才会生效
            from PyQt6.QtWidgets import QListView
            view = QListView()
            # 让 QListView 及其 viewport 透明，避免白色底盖住 QSS 面板背景
            view.setAutoFillBackground(False)
            view.setStyleSheet("QListView { background: transparent; border: none; }")
            widget.setView(view)
            widget.addItems(field['options'])
            widget.setProperty('field_key', field['key'])
        elif field['type'] == 'line':
            widget = QLineEdit()
            if 'default' in field:
                widget.setText(str(field['default']))
            if field['key'] in ('locationValue', 'fromValue', 'toValue'):
                container = QWidget()
                container_layout = QHBoxLayout(container)
                container_layout.setContentsMargins(0, 0, 0, 0)
                container_layout.addWidget(widget)
                select_btn = QToolButton()
                select_btn.setObjectName("ElementSelectBtn")
                select_btn.setIcon(qta.icon('fa6s.folder-open', color='#555555'))
                select_btn.setFixedSize(24, 24)
                select_btn.setStyleSheet("border: none; background: transparent;")
                select_btn.setProperty('value_widget', widget)
                select_btn.clicked.connect(self._on_select_element)
                container_layout.addWidget(select_btn)
                widget = container
        elif field['type'] == 'spin':
            widget = QSpinBox()
            widget.setRange(-999999, 999999)
            if 'default' in field:
                widget.setValue(field['default'])
        elif field['type'] == 'double':
            widget = QDoubleSpinBox()
            widget.setRange(0.0, 100.0)
            widget.setSingleStep(0.1)
            if 'default' in field:
                widget.setValue(field['default'])
        else:
            widget = QLineEdit()

        self.fields[field['key']] = widget
        layout.addWidget(widget)
        return layout

    def _on_select_element(self):
        if not self.element_controller:
            WarningDialog.show_warning(self, "提示", "元素库未初始化")
            return

        btn = self.sender()
        if not btn:
            return
        value_widget = btn.property('value_widget')
        if not value_widget:
            return

        key_map = {
            'locationValue': 'locationType',
            'fromValue': 'fromLocationType',
            'toValue': 'toLocationType'
        }
        value_key = None
        for k, v in self.fields.items():
            if isinstance(v, QWidget):
                if v.layout():
                    for i in range(v.layout().count()):
                        item = v.layout().itemAt(i)
                        if item and item.widget() == value_widget:
                            value_key = k
                            break
            elif v == value_widget:
                value_key = k
        if not value_key:
            return
        loc_type_key = key_map.get(value_key)
        if not loc_type_key:
            return
        loc_type_widget = self.fields.get(loc_type_key)
        if not isinstance(loc_type_widget, QComboBox):
            return

        dialog = ElementSelectorDialog(self.element_controller.element_model, self)
        dialog.element_selected.connect(lambda elem: self._apply_selected_element(elem, value_widget, loc_type_widget))
        dialog.exec()

    def _apply_selected_element(self, elem, value_widget, loc_type_widget):
        index = loc_type_widget.findText(elem.loc_type)
        if index >= 0:
            loc_type_widget.setCurrentIndex(index)
        value_widget.setText(elem.loc_value)
        value_widget.setProperty('element_id', elem.id)

    def _setup_placeholder_sync(self):
        mapping = {
            'locationType': 'locationValue',
            'fromLocationType': 'fromValue',
            'toLocationType': 'toValue'
        }
        for key, value_key in mapping.items():
            if key in self.fields and value_key in self.fields:
                combo = self.fields[key]
                line_edit_widget = self.fields[value_key]
                if isinstance(line_edit_widget, QWidget):
                    if line_edit_widget.layout() and line_edit_widget.layout().count() > 0:
                        item = line_edit_widget.layout().itemAt(0)
                        if item:
                            line_edit_widget = item.widget()
                if isinstance(combo, QComboBox) and isinstance(line_edit_widget, QLineEdit):
                    combo.currentIndexChanged.connect(
                        lambda idx, c=combo, l=line_edit_widget: self._update_placeholder(c, l)
                    )
                    self._update_placeholder(combo, line_edit_widget)

    def _update_placeholder(self, combo, line_edit):
        current_text = combo.currentText()
        placeholder = self.PLACEHOLDER_MAP.get(current_text, '')
        line_edit.setPlaceholderText(placeholder)

    def _emit_add(self):
        params = {}
        missing_fields = []
        for field in self.config['fields']:
            key = field['key']
            widget = self.fields[key]
            # 提取内部的真实值控件（如果是容器布局，取第一个子控件）
            value_widget = widget
            if isinstance(widget, QWidget) and widget.layout():
                if widget.layout().count() > 0:
                    item = widget.layout().itemAt(0)
                    if item and item.widget():
                        value_widget = item.widget()

            # 获取值
            if isinstance(value_widget, QComboBox):
                value = value_widget.currentText()
            elif isinstance(value_widget, QLineEdit):
                value = value_widget.text().strip()
            elif isinstance(value_widget, (QSpinBox, QDoubleSpinBox)):
                value = value_widget.value()
            else:
                value = ''

            params[key] = value

            # 处理 element_id 的关联与解除
            if key in ('locationValue', 'fromValue', 'toValue'):
                if isinstance(value_widget, QLineEdit) and hasattr(value_widget, 'property'):
                    elem_id = value_widget.property('element_id')
                    if elem_id and self.element_controller:
                        loc_type_key = {
                            'locationValue': 'locationType',
                            'fromValue': 'fromLocationType',
                            'toValue': 'toLocationType'
                        }.get(key)
                        if loc_type_key:
                            loc_type_widget = self.fields.get(loc_type_key)
                            if isinstance(loc_type_widget, QComboBox):
                                current_loc_type = loc_type_widget.currentText()
                                elem = self.element_controller.get_element_by_id(elem_id)
                                if elem:
                                    if current_loc_type == elem.loc_type and value == elem.loc_value:
                                        params['element_id'] = elem_id
                                    else:
                                        value_widget.setProperty('element_id', None)
                                else:
                                    value_widget.setProperty('element_id', None)

            # 必填项检查
            if field.get('required', False):
                if not value or (isinstance(value, str) and value == ''):
                    missing_fields.append(field['label'])

        if missing_fields:
            show_toast(message="必填项不能为空")
            return

        name = self.name_edit.text().strip()
        self.add_step.emit(self.config['type'], params, name)