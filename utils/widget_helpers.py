# utils/widget_helpers.py
"""下拉框弹出面板的统一处理。

应用里各处下拉框原先各写一套弹出面板样式（有的干脆没写，用的是 Qt 默认样式），
风格不一致。这里统一用 Theme.COMBO_POPUP_LIGHT/DARK 一份样式，
直接设在弹出面板的 view 上 —— view 是面板里最"靠近"的控件，QSS 优先级最高，
可以盖过各对话框自己写的 QComboBox QAbstractItemView 规则，做到全应用一致。
"""
from PyQt6.QtWidgets import QApplication, QComboBox, QListView, QStyledItemDelegate

# 当前生效的下拉面板样式
_popup_qss = ""

# 当前是否为暗色（供弹出容器同步底色用）
_is_dark = False

# 下拉每一项的最小高度：全应用统一兜底，避免某些环境下 QSS 的 min-height 没落到面板上
_MIN_ROW_HEIGHT = 32

# Qt 弹出面板的容器类名（不是导出类型，只能用字符串比对）
_CONTAINER_CLASS = "QComboBoxPrivateContainer"



class _MinHeightDelegate(QStyledItemDelegate):
    """给下拉列表的每一项兜一个最小高度。

    QSS 里的 `min-height` 在部分环境下没有落到弹出面板上，行高会退化成
    「字体高度」，导致选项被压扁、文字显示不全。这里用 sizeHint 兜一个下限，
    不依赖 QSS 是否生效。
    """

    def __init__(self, min_height, parent=None):
        super().__init__(parent)
        self._min_height = int(min_height)

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        if size.height() < self._min_height:
            size.setHeight(self._min_height)
        return size


def _canonical_popup_qss(is_dark: bool) -> str:
    from utils.theme import Theme
    return Theme.COMBO_POPUP_DARK if is_dark else Theme.COMBO_POPUP_LIGHT


def _resolve_theme_is_dark() -> bool:
    try:
        from utils.settings import Settings
        return Settings.get_theme_mode() == "dark"
    except Exception:
        return False


def _style_popup_container(view):
    """同步弹出容器的底色。

    弹出面板本体是 view，但外面还套着 Qt 的 QComboBoxPrivateContainer
    （以及它在需要滚动时插入的上下小箭头），这些都不是 view 的子控件，
    不受 view 上的样式影响，底色会取自全局调色板（浅色），
    于是夜间模式下会在面板顶部/底部露出一条白边。这里显式把容器底色设成面板底色。
    """
    container = view.parentWidget()
    if container is None:
        return
    try:
        from PyQt6.QtGui import QPalette, QColor
        from utils.theme import Theme
        color = Theme.COMBO_POPUP_BG["dark" if _is_dark else "light"]
        pal = container.palette()
        for role in (QPalette.ColorRole.Window, QPalette.ColorRole.Base,
                     QPalette.ColorRole.Button, QPalette.ColorRole.AlternateBase):
            pal.setColor(role, QColor(color))
        container.setPalette(pal)
        container.setAutoFillBackground(True)
        # 再显式给容器自己写一条背景规则：有的环境里容器是走 QSS 绘制的，
        # 光设调色板压不住（表现就是面板上下两端露出白条）
        qss = ("QComboBoxPrivateContainer { background-color: %s; border: none; }"
               % color)
        if container.styleSheet() != qss:
            container.setStyleSheet(qss)
    except Exception:
        pass


def _apply_view_style(view):
    """把一个弹出面板的 view 设成统一样式（含容器底色与行高下限）。"""
    if view.styleSheet() != _popup_qss:
        view.setStyleSheet(_popup_qss)
    if not isinstance(view.itemDelegate(), _MinHeightDelegate):
        view.setItemDelegate(_MinHeightDelegate(_MIN_ROW_HEIGHT, view))
    _style_popup_container(view)


def _apply_popup_style(combo: QComboBox):
    """把统一样式和行高下限设到某个下拉框的弹出面板上。"""
    try:
        view = combo.view()
        if view is not None:
            _apply_view_style(view)
    except RuntimeError:
        # 控件已被销毁
        pass


def set_combo_popup_theme(is_dark: bool) -> bool:
    """设置下拉面板样式，并刷新所有已存在的下拉框。

    每次都会扫一遍控件树：应用里有些下拉框没有走 prepare_combo_view
    （例如性能检测页的），只靠构造时设置会漏掉它们，必须在这里兜住。
    真正重设样式表的只有那些还不一致的控件，所以开销很小。
    返回样式是否发生了变化。

    注意：这里只能覆盖「已经创建出来」的下拉框。之后才弹出的对话框里的下拉，
    需要在它自己的 apply_theme 里再调一次本函数（例如设置页、性能检测页）。
    （曾试过用全局事件过滤器在控件显示时补样式，但那样会让进程退出时崩溃，
    所以改回这种显式调用的方式。）
    """
    global _popup_qss, _is_dark
    qss = _canonical_popup_qss(is_dark)
    changed = qss != _popup_qss
    _popup_qss = qss
    _is_dark = bool(is_dark)

    app = QApplication.instance()
    if app is not None:
        for widget in app.allWidgets():
            if isinstance(widget, QComboBox):
                _apply_popup_style(widget)
    return changed


def prepare_combo_view(combo: QComboBox, min_row_height: int = _MIN_ROW_HEIGHT,
                       min_popup_width: int = 0):
    """给 QComboBox 换上统一样式的弹出面板。

    min_row_height   下拉每一项的最小高度，默认 32（传 0 表示不干预）
    min_popup_width  下拉面板最小宽度（0 表示不干预）
    """
    global _popup_qss, _is_dark
    if not _popup_qss:
        # 还没有人刷过主题：先按当前配置兜一个，保证样式不为空
        _is_dark = _resolve_theme_is_dark()
        _popup_qss = _canonical_popup_qss(_is_dark)

    view = QListView()
    view.setAutoFillBackground(False)
    view.setStyleSheet(_popup_qss)
    if min_row_height:
        view.setItemDelegate(_MinHeightDelegate(min_row_height, view))
    if min_popup_width:
        view.setMinimumWidth(min_popup_width)
    combo.setView(view)
    # setView() 之后弹出容器才存在，这时才能同步它的底色
    _style_popup_container(view)
    return view
