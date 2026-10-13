# views/knowledge_view.py
"""个人知识库页面：左侧「项目 → 分类 → 条目」树，右侧 Markdown 编辑 / 预览。

界面结构（为什么这么分）：

    顶部   [全库搜索] [新建项目] [新建条目] [导出]
    左栏   项目 / 分类 / 条目 三级树（分类固定四类，见 knowledge_model.CATEGORIES）
    右栏   ① 速查卡片带（当前项目置顶，点卡片直接跳）
           ② 标题 / 分类 / 标签
           ③ Markdown 工具条 + 「编辑 / 预览」两个页签
    搜索时 右栏整体切成「搜索结果」页，点结果回到编辑页

几个刻意的取舍：

  * **点项目 / 分类 = 打开它的第一条条目**，而不是再做一个「概览」页。
    右栏因此永远只有「编辑器」和「搜索结果」两种形态，用户不用记"我现在在哪种视图里"。
  * **自动保存**（停顿 500ms 落盘，切换条目前强制落盘）。
    记笔记最怕"写了半天没保存"，所以不做保存按钮 —— 没有需要点的东西，就不会忘。
  * **图片粘贴 / 拖入自动落盘**，正文里只留 `assets/xxx.png` 相对路径，
    应用内预览与导出后的 Markdown 用同一条路径，导出无需改写链接。
"""
import os
import re

import qtawesome as qta
from PyQt6.QtCore import QBuffer, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import (QFont, QImage, QKeySequence, QShortcut, QTextBlockFormat,
                         QTextCharFormat, QTextCursor, QTextFormat, QTextImageFormat,
                         QTextListFormat)
from PyQt6.QtWidgets import (
    QComboBox, QCompleter, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMenu, QPlainTextEdit, QPushButton, QSplitter,
    QStackedWidget, QTabWidget, QTextEdit, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from models.knowledge_model import CATEGORIES
from utils import tree_state
from utils.dialogs import ConfirmDeleteDialog, InputDialog, QuestionDialog, WarningDialog
from utils.flow_layout import FlowLayout
from utils.settings import Settings, THEME_MODE_DARK
from utils.theme import ThemeMode
from utils.toast import show_toast

# 树节点的类型标记（存在 UserRole+1 里）
KIND_PROJECT = "project"
KIND_CATEGORY = "category"
KIND_NOTE = "note"

# 允许当作图片粘贴 / 拖入的扩展名
IMAGE_EXTS = ('.png', '.jpg', '.jpeg', '.bmp', '.gif', '.webp')

# 自动保存的停顿时间（毫秒）：太短会在连续输入时反复写盘，太长丢字风险变大
AUTOSAVE_DELAY = 500


# ----------------------------------------------------------------------
# 富文本 → Markdown
# ----------------------------------------------------------------------
# 为什么自己写而不用 QTextDocument.toMarkdown()：**实测 Qt 自带的那个会丢掉粗体与
# 斜体**（`**粗体**` 用 setMarkdown 读进去，再 toMarkdown 出来就是普通文字；
# Qt 6 的已知缺陷），而这恰好是工具栏最常用的两个按钮。
# 读入端（setMarkdown）是完整的 —— 粗体 / 斜体 / 等宽 / 标题 / 列表 / 引用 /
# 链接 / 图片都能正确识别，所以这里只补写出端，行为可预期、可测。
_MONO_FAMILY = "monospace"

# 需要转义的字符：不转义的话用户正文里的字面 `*`、`[` 会被当成标记
_ESCAPE_CHARS = "\\`*["


def _escape_markdown(text: str) -> str:
    return "".join(("\\" + ch) if ch in _ESCAPE_CHARS else ch for ch in text)


def _iter_fragments(block):
    """遍历一个文本块里的片段（片段是"格式相同的一段连续文字"）"""
    it = block.begin()
    while not it.atEnd():
        frag = it.fragment()
        if frag.isValid():
            yield frag
        it += 1


def _fragment_to_markdown(frag, suppress_bold: bool = False) -> str:
    """单个片段 → Markdown。图片 → `![图片](路径)`，链接 / 等宽 / 粗斜体依次处理。

    suppress_bold=True 用在标题块里：setMarkdown 会把整条标题的字体设成粗体，
    再补一层 `**` 就变成 `## **标题**`（冗余且难看）。
    """
    char_format = frag.charFormat()
    if char_format.isImageFormat():
        name = char_format.toImageFormat().name()
        return f"![图片]({name})" if name else ""
    text = frag.text()
    if not text:
        return ""
    anchor = char_format.anchorHref()
    if anchor:
        return f"[{text}]({anchor})"
    if _MONO_FAMILY in (char_format.fontFamilies() or []):
        return f"`{text}`"
    escaped = _escape_markdown(text)
    bold = char_format.fontWeight() >= QFont.Weight.Bold and not suppress_bold
    italic = char_format.fontItalic()
    if bold and italic:
        return f"***{escaped}***"
    if bold:
        return f"**{escaped}**"
    if italic:
        return f"*{escaped}*"
    return escaped


def _block_is_code(block) -> bool:
    """整块都是等宽字体、且不在列表/标题/引用里 → 按围栏代码块输出。

    判据只能是"整块等宽"：setMarkdown 把 ``` 代码块解析成普通块 + 等宽字体，
    跟行内代码在文档结构上没区别，只有"整块还是局部"能区分。

    含图片的块直接排除：图片片段的字体属性是空的，不排除的话
    「只有一张图」的块会被当成"整块等宽"而输出成代码块。
    """
    if not block.text().strip():
        return False
    block_format = block.blockFormat()
    if block_format.headingLevel() or block.textList() is not None:
        return False
    if block_format.property(int(QTextFormat.Property.BlockQuoteLevel)):
        return False
    for frag in _iter_fragments(block):
        if frag.charFormat().isImageFormat():
            return False
        if _MONO_FAMILY not in (frag.charFormat().fontFamilies() or []):
            return False
    return True


def document_to_markdown(document) -> str:
    """把富文本文档整份转成 Markdown 文本。"""
    parts = []          # [(是否列表项, 文本)]
    code_lines = []

    def flush_code():
        if code_lines:
            parts.append((False, "```\n" + "\n".join(code_lines) + "\n```"))
            code_lines.clear()

    block = document.begin()
    while block.isValid():
        if _block_is_code(block):
            code_lines.append(block.text())
            block = block.next()
            continue
        flush_code()

        block_format = block.blockFormat()
        heading = block_format.headingLevel()
        quote = block_format.property(int(QTextFormat.Property.BlockQuoteLevel)) or 0
        text_list = block.textList()

        content = "".join(
            _fragment_to_markdown(f, suppress_bold=bool(heading))
            for f in _iter_fragments(block))

        prefix = ""
        is_list = False
        if heading:
            prefix = "#" * min(int(heading), 6) + " "
        elif text_list is not None:
            is_list = True
            style = text_list.format().style()
            prefix = "1. " if style == QTextListFormat.Style.ListDecimal else "- "
            depth = max(1, int(text_list.format().indent() or 1))
            prefix = "  " * (depth - 1) + prefix
        if quote:
            prefix = "> " * int(quote) + prefix

        # 空块直接跳过：块之间本来就用空行分隔，多输出只会积出连续空行
        if content.strip() or prefix.strip():
            parts.append((is_list, prefix + content))
        block = block.next()
    flush_code()

    out = []
    prev_is_list = False
    for is_list, text in parts:
        if out:
            # 相邻的列表项之间只换行（紧凑列表），其余用空行分段
            out.append("\n" if (is_list and prev_is_list) else "\n\n")
        out.append(text)
        prev_is_list = is_list
    return "".join(out)


class MarkdownEditor(QTextEdit):
    """**所见即所得**的 Markdown 编辑器。

    内部是富文本（QTextEdit 富文本模式），只有落库那一刻才用 document_to_markdown
    转成 Markdown 文本。这样点「加粗」看到的就是真的粗体，而不是往正文里插一对 `**`
    —— 纯文本 Markdown 编辑器做不到（用户 2026-10-12 反馈「只是加了 **」）。

    图片仍然拦下来交给视图落盘：编辑器不认识模型，只负责把图片原样抛出去。
    """

    # 参数是 QImage（粘贴位图）或 str（拖入的图片文件路径）
    image_source_ready = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptRichText(True)

    # ---------------- Markdown 互转 ----------------
    def set_markdown(self, text: str):
        self.setMarkdown(text or "")

    def to_markdown(self) -> str:
        return document_to_markdown(self.document())

    def set_base_dir(self, base_dir: str):
        """图片相对路径（assets/x.png）的解析基准 —— 不设的话编辑器里显示不出图"""
        self.document().setBaseUrl(QUrl.fromLocalFile(base_dir.rstrip("/\\") + os.sep))

    def insert_image(self, rel_path: str, width: int = 320):
        image_format = QTextImageFormat()
        image_format.setName(rel_path)
        image_format.setWidth(width)
        cursor = self.textCursor()
        cursor.insertImage(image_format)
        self.setTextCursor(cursor)

    # ---------------- 行内格式 ----------------
    def _current_char_format(self) -> QTextCharFormat:
        return self.textCursor().charFormat()

    def toggle_bold(self):
        fmt = QTextCharFormat()
        is_bold = self._current_char_format().fontWeight() >= QFont.Weight.Bold
        fmt.setFontWeight(QFont.Weight.Normal if is_bold else QFont.Weight.Bold)
        self.mergeCurrentCharFormat(fmt)

    def toggle_italic(self):
        fmt = QTextCharFormat()
        fmt.setFontItalic(not self._current_char_format().fontItalic())
        self.mergeCurrentCharFormat(fmt)

    def toggle_code(self):
        """行内代码 = 切成等宽字体（导出时再变回 `反引号`）"""
        fmt = QTextCharFormat()
        is_mono = _MONO_FAMILY in (self._current_char_format().fontFamilies() or [])
        if is_mono:
            # 取消时把字体设回编辑器默认，而不是清空属性 —— 清空在 merge 里不生效
            fmt.setFontFamilies([self.font().family()])
        else:
            fmt.setFontFamilies([_MONO_FAMILY])
        self.mergeCurrentCharFormat(fmt)

    # ---------------- 块级格式 ----------------
    def _selected_blocks(self):
        document = self.document()
        cursor = self.textCursor()
        first = document.findBlock(cursor.selectionStart())
        last = document.findBlock(cursor.selectionEnd())
        blocks = []
        block = first
        while block.isValid():
            blocks.append(block)
            if block == last:
                break
            block = block.next()
        return blocks or [document.findBlock(cursor.position())]

    def _apply_block_format(self, mutate):
        cursor = self.textCursor()
        cursor.beginEditBlock()
        for block in self._selected_blocks():
            block_format = QTextBlockFormat(block.blockFormat())
            mutate(block_format)
            QTextCursor(block).setBlockFormat(block_format)
        cursor.endEditBlock()

    def toggle_heading(self, level: int = 2):
        def mutate(block_format):
            block_format.setHeadingLevel(0 if block_format.headingLevel() else level)
        self._apply_block_format(mutate)

    def toggle_quote(self):
        def mutate(block_format):
            current = block_format.property(int(QTextFormat.Property.BlockQuoteLevel)) or 0
            block_format.setProperty(int(QTextFormat.Property.BlockQuoteLevel),
                                     0 if current else 1)
        self._apply_block_format(mutate)

    def toggle_list(self, style):
        cursor = self.textCursor()
        cursor.beginEditBlock()
        for block in self._selected_blocks():
            text_list = block.textList()
            block_cursor = QTextCursor(block)
            if text_list is not None and text_list.format().style() == style:
                # 再点一次取消：脱离列表（Qt 里用 objectIndex(-1) 表示"不在任何列表"）
                block_format = QTextBlockFormat(block.blockFormat())
                block_format.setObjectIndex(-1)
                block_format.setIndent(0)
                block_cursor.setBlockFormat(block_format)
            else:
                block_cursor.createList(style)
        cursor.endEditBlock()

    def insert_link(self, text: str = "链接文字", href: str = "https://"):
        cursor = self.textCursor()
        selected = cursor.selectedText().replace("\u2029", "\n")
        char_format = QTextCharFormat()
        char_format.setAnchor(True)
        char_format.setAnchorHref(href)
        if selected:
            cursor.insertText(selected, char_format)
        else:
            cursor.insertText(text, char_format)
        self.setTextCursor(cursor)

    # ---------------- 图片粘贴 / 拖入 ----------------
    def insertFromMimeData(self, source):
        if self._try_emit_image(source):
            return
        super().insertFromMimeData(source)

    def canInsertFromMimeData(self, source):
        if source.hasImage() or source.hasUrls():
            return True
        return super().canInsertFromMimeData(source)

    def dropEvent(self, event):
        if self._try_emit_image(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    def _try_emit_image(self, mime) -> bool:
        """是图片就发信号并返回 True（已消费）；否则返回 False 交回默认处理。"""
        if mime.hasImage():
            image = mime.imageData()
            if isinstance(image, QImage) and not image.isNull():
                self.image_source_ready.emit(image)
                return True
        if mime.hasUrls():
            for url in mime.urls():
                if not url.isLocalFile():
                    continue
                path = url.toLocalFile()
                if path.lower().endswith(IMAGE_EXTS) and os.path.isfile(path):
                    self.image_source_ready.emit(path)
                    return True
        return False


class QuickCard(QFrame):
    """速查卡片：项目置顶展示的速查条目（点一下跳到该条目）"""

    clicked = pyqtSignal(str)      # note_id

    def __init__(self, note, parent=None):
        super().__init__(parent)
        self.setObjectName("KbQuickCard")
        self._note_id = note.id
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(note.title)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(1)

        title = QLabel(note.title)
        title.setObjectName("KbQuickCardTitle")
        layout.addWidget(title)

        first_line = next(
            (ln.strip() for ln in (note.body or "").splitlines() if ln.strip()), "")
        detail = QLabel(first_line or "（空）")
        detail.setObjectName("KbQuickCardBody")
        layout.addWidget(detail)
        self.setFixedWidth(180)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._note_id)
        super().mousePressEvent(event)


class KnowledgeView(QFrame):
    """个人知识库页面。数据全部来自 KnowledgeModel，本视图不直接读写文件。"""

    def __init__(self, model, parent=None):
        super().__init__(parent)
        self.model = model
        self.setObjectName("KnowledgeView")
        self.setFrameStyle(QFrame.Shape.NoFrame)
        self.setAutoFillBackground(True)

        self._current_note_id = None
        self._item_by_note = {}      # note_id -> QTreeWidgetItem（改标题时就地更新）
        self._loading = False        # 载入内容期间不回写
        self._dirty = False
        self._is_dark = False
        # 需要跟随主题换色的图标按钮：[(QPushButton, 图标名, 用途)]
        self._icon_buttons = []

        # 自动保存：改动后停顿一下再落盘，连续输入只写一次
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(AUTOSAVE_DELAY)
        self._save_timer.timeout.connect(self._flush_save)

        # 搜索防抖：边打边搜会让大库卡顿
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(200)
        self._search_timer.timeout.connect(self._run_search)

        self._tree_keeper = None
        self.setup_ui()
        self.refresh_tree()
        self.apply_theme()

    # ------------------------------------------------------------------
    # 主题
    # ------------------------------------------------------------------
    def _icon_color(self, role: str = 'button') -> str:
        """按当前主题给图标取色。

        为什么不能写死白色：页面工具栏这三个按钮没有页面级 QSS，白天模式下走的是
        平台默认的**浅色底**，白图标等于隐形（用户 2026-10-12 反馈「看不清图标」）。
        """
        dark = self._is_dark
        if role == 'button':
            return '#e8eaed' if dark else '#3c4043'
        if role == 'menu':
            return '#dddddd' if dark else '#555555'
        if role == 'menu_danger':
            return '#ff8a80' if dark else '#e05555'
        # 编辑器工具条：中性灰，浅色/深色底上都看得清，不需要分主题
        return '#8a8f98'

    def _apply_icon_colors(self):
        """按当前主题刷新所有登记过的图标按钮"""
        for button, icon_name, role in self._icon_buttons:
            button.setIcon(qta.icon(icon_name, color=self._icon_color(role)))

    def apply_theme(self, theme_mode: ThemeMode = None):
        if theme_mode is None:
            theme_mode = (ThemeMode.DARK if Settings.get_theme_mode() == THEME_MODE_DARK
                          else ThemeMode.LIGHT)
        self._is_dark = (theme_mode == ThemeMode.DARK)

        has_wp = False
        try:
            wp_path = Settings.get_wallpaper_path()
            has_wp = bool(wp_path and os.path.exists(wp_path))
        except Exception:
            pass

        if theme_mode == ThemeMode.DARK:
            # 页面整张卡片：与「应用元素库 / 项目管理」同一套取值（那两页也是整页一张带框卡片）。
            # 有壁纸时底色透明让壁纸透出；注意写 rgba(...,0) 而不是关键字 transparent ——
            # 关键字会让 Qt 把该控件的调色板整份算成全黑，挂在它下面的原生弹窗会跟着变黑
            # （帮助中心踩过这个坑，见 help_view.py 的同名注释）。
            if has_wp:
                page_bg, page_border = "rgba(45, 45, 45, 0)", "rgba(85, 85, 85, 0.9)"
            else:
                page_bg, page_border = "#2d2d2d", "#555"
            container_bg = "transparent" if has_wp else "#2d2d2d"
            container_border = "rgba(85, 85, 85, 0.9)" if has_wp else "#555"
            text = "#eee"
            sub_text = "#9aa0a6"
            selected_bg = "#1e3a5f"
            hover_bg = "rgba(74, 74, 74, 0.6)"
            edit_bg = "#252525"
            card_bg = "#333333"
            card_hover = "#3d3d3d"
            sb_handle = "#666"
        else:
            if has_wp:
                page_bg, page_border = "rgba(255, 255, 255, 0)", "rgba(208, 208, 208, 0.9)"
            else:
                page_bg, page_border = "#ffffff", "#d0d0d0"
            container_bg = "transparent" if has_wp else "white"
            container_border = "rgba(208, 208, 208, 0.9)" if has_wp else "#d0d0d0"
            text = "#333"
            sub_text = "#888"
            selected_bg = "#d0e4f7"
            hover_bg = "#dfe2e6"
            edit_bg = "#ffffff"
            card_bg = "#f4f6f8"
            card_hover = "#e8edf2"
            sb_handle = "#c0c0c0"

        self.setStyleSheet(f"""
            #KnowledgeView {{
                background-color: {page_bg};
                border: 1px solid {page_border};
                border-radius: 8px;
            }}
            #KbTreeContainer, #KbEditorContainer {{
                background-color: {container_bg};
                border: 1px solid {container_border};
                border-radius: 8px;
            }}
            #KbTreeContainer QTreeWidget {{
                background-color: transparent;
                border: none;
                outline: none;
                color: {text};
                selection-background-color: transparent;
            }}
            #KbTreeContainer QTreeWidget::item {{
                height: 26px;
            }}
            /* 刻意不写 border-radius：选中/悬浮底色要铺满整行（带圆角会露出四个角，
               看着像一颗药丸，与帮助中心、执行页的树不一致） */
            #KbTreeContainer QTreeWidget::item:hover {{
                background-color: {hover_bg};
            }}
            #KbTreeContainer QTreeWidget::item:selected {{
                background-color: {selected_bg};
                color: {text};
            }}
            #KbQuickStrip {{
                background: transparent;
            }}
            #KbQuickCard {{
                background-color: {card_bg};
                border: 1px solid {container_border};
                border-radius: 6px;
            }}
            #KbQuickCard:hover {{
                background-color: {card_hover};
            }}
            #KbQuickCardTitle {{
                font-weight: 600;
                font-size: 12px;
                color: {text};
                background: transparent;
            }}
            #KbQuickCardBody {{
                font-size: 11px;
                color: {sub_text};
                background: transparent;
            }}
            #KbSectionLabel {{
                color: {sub_text};
                font-size: 12px;
                background: transparent;
            }}
            #KbTitleEdit {{
                font-size: 15px;
                font-weight: 600;
                color: {text};
                background-color: {edit_bg};
                border: 1px solid {container_border};
                border-radius: 6px;
                padding: 6px 8px;
            }}
            #KbBodyEdit {{
                background-color: {edit_bg};
                color: {text};
                border: 1px solid {container_border};
                border-radius: 6px;
                padding: 6px;
                font-family: Consolas, "Microsoft YaHei", monospace;
                font-size: 13px;
            }}
            #KbSourceView {{
                background-color: {edit_bg};
                color: {text};
                border: 1px solid {container_border};
                border-radius: 6px;
                padding: 8px;
                font-family: Consolas, "Microsoft YaHei", monospace;
                font-size: 13px;
            }}
            #KbSearchInput, #KbTagsEdit {{
                background-color: {edit_bg};
                color: {text};
                border: 1px solid {container_border};
                border-radius: 6px;
                padding: 5px 8px;
            }}
            #KbSearchList {{
                background-color: transparent;
                border: none;
                outline: none;
                color: {text};
            }}
            #KbSearchList::item {{
                padding: 6px 8px;
                border-radius: 4px;
            }}
            #KbSearchList::item:hover {{
                background-color: {hover_bg};
            }}
            #KbSearchList::item:selected {{
                background-color: {selected_bg};
                color: {text};
            }}
            #KbTreeContainer QScrollBar:vertical, #KbSearchList QScrollBar:vertical {{
                background: transparent;
                width: 10px;
                margin: 0;
            }}
            #KbTreeContainer QScrollBar::handle:vertical, #KbSearchList QScrollBar::handle:vertical {{
                background: {sb_handle};
                border-radius: 5px;
                min-height: 30px;
            }}
            #KbTreeContainer QScrollBar::add-line:vertical, #KbTreeContainer QScrollBar::sub-line:vertical,
            #KbSearchList QScrollBar::add-line:vertical, #KbSearchList QScrollBar::sub-line:vertical {{
                height: 0;
            }}
        """)
        # 图标颜色跟着主题走（浅色底上白图标看不见）
        self._apply_icon_colors()

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        root.addLayout(self._build_toolbar())

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(self._build_tree_panel())
        self.splitter.addWidget(self._build_right_panel())
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([260, 760])
        self.splitter.setChildrenCollapsible(False)
        root.addWidget(self.splitter, 1)

    def _build_toolbar(self):
        row = QHBoxLayout()
        row.setSpacing(8)

        self.search_input = QLineEdit()
        self.search_input.setObjectName("KbSearchInput")
        self.search_input.setPlaceholderText("搜索全部项目（标题 / 正文 / 标签）…")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(lambda _: self._search_timer.start())
        row.addWidget(self.search_input, 1)

        self.new_project_btn = QPushButton("新建项目")
        self.new_project_btn.clicked.connect(self._on_new_project)
        row.addWidget(self.new_project_btn)
        self._icon_buttons.append((self.new_project_btn, 'fa6s.folder-plus', 'button'))

        self.new_note_btn = QPushButton("新建条目")
        self.new_note_btn.clicked.connect(self._on_new_note)
        row.addWidget(self.new_note_btn)
        self._icon_buttons.append((self.new_note_btn, 'fa6s.file-circle-plus', 'button'))

        self.export_btn = QPushButton("导出")
        self.export_btn.setToolTip("把整个知识库导出为 Markdown 目录（含图片）")
        self.export_btn.clicked.connect(self._on_export)
        row.addWidget(self.export_btn)
        self._icon_buttons.append((self.export_btn, 'fa6s.file-export', 'button'))
        return row

    def _build_tree_panel(self):
        container = QFrame()
        container.setObjectName("KbTreeContainer")
        # 与帮助中心的容器保持一致：显式 NoFrame，边框完全交给样式表画，
        # 免得平台样式再叠一层自己的面板框
        container.setFrameStyle(QFrame.Shape.NoFrame)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setColumnCount(1)
        self.tree.setIndentation(14)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        self.tree.currentItemChanged.connect(self._on_tree_selection)
        layout.addWidget(self.tree, 1)

        # 展开状态按节点 id 记住（默认全折叠，见 utils/tree_state.py）
        self._tree_keeper = tree_state.bind_tree(self.tree, "knowledge_tree")
        return container

    def _build_right_panel(self):
        container = QFrame()
        container.setObjectName("KbEditorContainer")
        container.setFrameStyle(QFrame.Shape.NoFrame)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        self.right_stack = QStackedWidget()
        self.right_stack.addWidget(self._build_editor_page())
        self.right_stack.addWidget(self._build_search_page())
        layout.addWidget(self.right_stack, 1)
        return container

    def _build_editor_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # ---- 速查卡片带（当前项目的「速查」条目，点一下跳过去）----
        # 直接放进布局、不套滚动区：FlowLayout 自己会换行，速查本就该短；
        # 套 QScrollArea 反而要处理 heightForWidth，得不偿失。
        self.quick_strip = QWidget()
        self.quick_strip.setObjectName("KbQuickStrip")
        self.quick_layout = FlowLayout(self.quick_strip, margin=0, spacing=8)
        layout.addWidget(self.quick_strip)

        # ---- 标题行：标题 + 分类 ----
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        self.title_edit = QLineEdit()
        self.title_edit.setObjectName("KbTitleEdit")
        self.title_edit.setPlaceholderText("条目标题")
        self.title_edit.textChanged.connect(self._on_content_changed)
        title_row.addWidget(self.title_edit, 1)

        self.category_combo = QComboBox()
        self.category_combo.addItems(CATEGORIES)
        self.category_combo.setFixedWidth(110)
        self.category_combo.currentIndexChanged.connect(self._on_category_changed)
        title_row.addWidget(self.category_combo)
        layout.addLayout(title_row)

        # ---- 标签行 ----
        tag_row = QHBoxLayout()
        tag_row.setSpacing(8)
        tag_label = QLabel("标签")
        tag_label.setObjectName("KbSectionLabel")
        tag_row.addWidget(tag_label)
        self.tags_edit = QLineEdit()
        self.tags_edit.setObjectName("KbTagsEdit")
        self.tags_edit.setPlaceholderText("多个标签用空格或逗号分隔，如：输入法 权限弹窗")
        self.tags_edit.textChanged.connect(self._on_content_changed)
        tag_row.addWidget(self.tags_edit, 1)
        layout.addLayout(tag_row)

        # ---- 编辑 / 源码 ----
        # 编辑器本身已是所见即所得，所以第二个页签不再是「预览」（那会和编辑长得一模一样），
        # 改成「源码」看实际存下来的 Markdown —— 导出前想核对格式时用得着。
        self.tabs = QTabWidget()
        self.body_edit = MarkdownEditor()
        self.body_edit.setObjectName("KbBodyEdit")
        self.body_edit.setPlaceholderText(
            "可直接粘贴或拖入截图，图片会自动保存。工具栏按钮即改即见效果。")
        self.body_edit.textChanged.connect(self._on_content_changed)
        self.body_edit.image_source_ready.connect(self._on_image_source)

        editor_side = QWidget()
        editor_layout = QVBoxLayout(editor_side)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(6)
        editor_layout.addLayout(self._build_format_toolbar())
        editor_layout.addWidget(self.body_edit, 1)

        self.source_view = QPlainTextEdit()
        self.source_view.setObjectName("KbSourceView")
        self.source_view.setReadOnly(True)
        self.source_view.setPlaceholderText("（这条笔记的 Markdown 源码）")

        self.tabs.addTab(editor_side, "编辑")
        self.tabs.addTab(self.source_view, "源码")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self.tabs, 1)

        # ---- 状态行 ----
        status_row = QHBoxLayout()
        self.status_label = QLabel("")
        self.status_label.setObjectName("KbSectionLabel")
        status_row.addWidget(self.status_label)
        status_row.addStretch()
        self.hint_label = QLabel("")
        self.hint_label.setObjectName("KbSectionLabel")
        status_row.addWidget(self.hint_label)
        layout.addLayout(status_row)

        # 快捷键与工具条按钮同一套动作
        for seq, slot in (("Ctrl+B", self.body_edit.toggle_bold),
                          ("Ctrl+I", self.body_edit.toggle_italic),
                          ("Ctrl+K", self._on_insert_link)):
            QShortcut(QKeySequence(seq), self.body_edit).activated.connect(slot)

        self._editor_widgets = [self.title_edit, self.category_combo,
                                self.tags_edit, self.tabs]
        self._set_editor_enabled(False)
        return page

    def _build_format_toolbar(self):
        row = QHBoxLayout()
        row.setSpacing(4)

        def add(icon, tip, slot):
            btn = QPushButton()
            btn.setToolTip(tip)
            btn.setFixedSize(28, 26)
            btn.setFlat(True)
            btn.clicked.connect(slot)
            row.addWidget(btn)
            self._icon_buttons.append((btn, icon, 'flat'))

        add('fa6s.bold', "加粗 (Ctrl+B)", self.body_edit.toggle_bold)
        add('fa6s.italic', "斜体 (Ctrl+I)", self.body_edit.toggle_italic)
        add('fa6s.heading', "标题", self.body_edit.toggle_heading)
        add('fa6s.list-ul', "无序列表",
            lambda: self.body_edit.toggle_list(QTextListFormat.Style.ListDisc))
        add('fa6s.list-ol', "有序列表",
            lambda: self.body_edit.toggle_list(QTextListFormat.Style.ListDecimal))
        add('fa6s.quote-left', "引用", self.body_edit.toggle_quote)
        add('fa6s.code', "行内代码", self.body_edit.toggle_code)
        add('fa6s.link', "插入链接 (Ctrl+K)", self._on_insert_link)
        add('fa6s.image', "插入图片文件", self._on_insert_image_file)
        row.addStretch()
        return row

    def _build_search_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.search_summary = QLabel("")
        self.search_summary.setObjectName("KbSectionLabel")
        layout.addWidget(self.search_summary)

        self.search_list = QListWidget()
        self.search_list.setObjectName("KbSearchList")
        self.search_list.setWordWrap(True)
        self.search_list.itemClicked.connect(self._on_search_result)
        self.search_list.itemActivated.connect(self._on_search_result)
        layout.addWidget(self.search_list, 1)
        return page

    # ------------------------------------------------------------------
    # 树的构建与选择
    # ------------------------------------------------------------------
    def refresh_tree(self, select_note_id: str = None, select_project_id: str = None):
        """重建左树。

        选中还原的优先级：**有打开中的条目就优先还原它**，其次才看
        select_project_id —— 改标题/改分类这类"就地重建"不该把用户的上下文踢走。
        想强制选中某个项目（如新建项目后），调用方先自己把 _current_note_id 清掉。
        """
        if select_note_id is None:
            select_note_id = self._current_note_id
        self._tree_keeper.snapshot()
        with self._tree_keeper.pause():
            self.tree.blockSignals(True)
            self.tree.clear()
            self._item_by_note = {}
            for project in self.model.get_projects():
                pitem = QTreeWidgetItem(
                    [f"{project.name}（{self.model.count_notes(project.id)}）"])
                pitem.setData(0, Qt.ItemDataRole.UserRole, f"p:{project.id}")
                pitem.setData(0, Qt.ItemDataRole.UserRole + 1, (KIND_PROJECT, project.id))
                pitem.setIcon(0, qta.icon('fa6s.folder', color='#e0a24a'))
                self.tree.addTopLevelItem(pitem)

                for category in CATEGORIES:
                    notes = self.model.get_notes(project_id=project.id, category=category)
                    citem = QTreeWidgetItem([f"{category}（{len(notes)}）"])
                    citem.setData(0, Qt.ItemDataRole.UserRole,
                                  f"c:{project.id}:{category}")
                    citem.setData(0, Qt.ItemDataRole.UserRole + 1,
                                  (KIND_CATEGORY, (project.id, category)))
                    citem.setIcon(0, qta.icon('fa6s.tag', color='#7f8fa6'))
                    pitem.addChild(citem)
                    for note in notes:
                        nitem = QTreeWidgetItem([note.title])
                        nitem.setData(0, Qt.ItemDataRole.UserRole, f"n:{note.id}")
                        nitem.setData(0, Qt.ItemDataRole.UserRole + 1,
                                      (KIND_NOTE, note.id))
                        nitem.setToolTip(0, note.title)
                        citem.addChild(nitem)
                        self._item_by_note[note.id] = nitem
            self.tree.blockSignals(False)
        self._tree_keeper.restore()
        self._update_tag_completer()

        if select_note_id and select_note_id in self._item_by_note:
            self.tree.setCurrentItem(self._item_by_note[select_note_id])
        elif select_project_id:
            for i in range(self.tree.topLevelItemCount()):
                item = self.tree.topLevelItem(i)
                if item.data(0, Qt.ItemDataRole.UserRole) == f"p:{select_project_id}":
                    self.tree.setCurrentItem(item)
                    break

    def _update_tag_completer(self):
        completer = QCompleter(self.model.all_tags(), self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.tags_edit.setCompleter(completer)

    def _on_tree_selection(self, current, _previous):
        if current is None:
            return
        payload = current.data(0, Qt.ItemDataRole.UserRole + 1)
        if not payload:
            return
        kind, value = payload
        if kind == KIND_NOTE:
            self._open_note(value)
        elif kind == KIND_CATEGORY:
            self._open_first_note(value[0], value[1])
        else:
            self._open_first_note(value, None)

    def _open_first_note(self, project_id: str, category: str = None):
        """点项目 / 分类时打开它的第一条条目 —— 右栏因此不必再做一个「概览」形态"""
        notes = self.model.get_notes(project_id=project_id, category=category)
        if not notes:
            self._show_empty_state()
            return
        self._open_note(notes[0].id)

    def _show_empty_state(self):
        self._flush_save()
        self._current_note_id = None
        self._loading = True
        self.title_edit.clear()
        self.tags_edit.clear()
        self.body_edit.clear()
        self.source_view.clear()
        self._loading = False
        self._dirty = False
        self._set_editor_enabled(False)
        self._clear_quick_cards()
        self.status_label.setText("")
        self.hint_label.setText("该项目还没有条目 · 点「新建条目」开始记录")

    # ------------------------------------------------------------------
    # 打开 / 编辑 / 保存
    # ------------------------------------------------------------------
    def _open_note(self, note_id: str):
        note = self.model.get_note(note_id)
        if note is None or note_id == self._current_note_id:
            return
        self._flush_save()          # 切走之前先把上一条的改动落盘
        self._current_note_id = note_id
        self._loading = True
        self.title_edit.setText(note.title)
        idx = self.category_combo.findText(note.category)
        self.category_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.tags_edit.setText(" ".join(note.tags))
        # 图片的相对路径（assets/x.png）要有个基准目录才解析得出来，否则编辑器里只显示占位
        self.body_edit.set_base_dir(self.model.note_base_dir(note.id))
        self.body_edit.set_markdown(note.body)
        self._loading = False
        self._dirty = False
        self._set_editor_enabled(True)
        self._refresh_quick_cards(note.project_id)
        self.right_stack.setCurrentIndex(0)
        if self.tabs.currentIndex() == 1:
            self._refresh_source_view()
        self.status_label.setText(f"已保存 · {note.updated}")
        self.hint_label.setText("")

    def _set_editor_enabled(self, enabled: bool):
        for widget in self._editor_widgets:
            widget.setEnabled(enabled)

    def _on_tab_changed(self, index: int):
        if index == 1:
            self._refresh_source_view()

    def _refresh_source_view(self):
        """「源码」页签：显示当前内容转出来的 Markdown（只读）"""
        self.source_view.setPlainText(self.body_edit.to_markdown())

    def _on_content_changed(self):
        if self._loading or self._current_note_id is None:
            return
        self._dirty = True
        self.status_label.setText("编辑中…")
        self._save_timer.start()

    def _on_category_changed(self, _index):
        if self._loading or self._current_note_id is None:
            return
        # 分类决定条目挂在树上哪个节点，必须立刻落盘 + 重建树，不能等防抖
        self._save_timer.stop()
        self._dirty = True
        self._flush_save()

    @staticmethod
    def _parse_tags(text: str):
        return [t for t in re.split(r"[,，;；\s]+", text or "") if t]

    def _flush_save(self):
        if not self._dirty or self._current_note_id is None:
            return
        note_id = self._current_note_id
        note = self.model.get_note(note_id)
        if note is None:
            return
        self._dirty = False
        category = self.category_combo.currentText()
        category_moved = (category != note.category)
        self.model.update_note(
            note_id,
            title=self.title_edit.text().strip() or "未命名条目",
            body=self.body_edit.to_markdown(),
            tags=self._parse_tags(self.tags_edit.text()),
            category=category,
        )
        note = self.model.get_note(note_id)
        self.status_label.setText(f"已保存 · {note.updated}")
        if category_moved:
            # 换了分类 = 换了树节点，只能重建；重建后把选中还原回来
            self.refresh_tree(select_note_id=note_id)
            self._refresh_quick_cards(note.project_id)
        else:
            item = self._item_by_note.get(note_id)
            if item is not None:
                item.setText(0, note.title)
                item.setToolTip(0, note.title)

    # ------------------------------------------------------------------
    # 速查卡片
    # ------------------------------------------------------------------
    def _clear_quick_cards(self):
        while self.quick_layout.count():
            item = self.quick_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        # 卡片清空后整条隐藏：空 FlowLayout 高度为 0 看不见，但仍占着布局位置，
        # 留着会让标题行上方多出一段说不清的空隙
        self.quick_strip.setVisible(False)

    def _refresh_quick_cards(self, project_id: str):
        self._clear_quick_cards()
        cards = self.model.quick_cards(project_id)
        self.quick_strip.setVisible(bool(cards))
        for note in cards:
            card = QuickCard(note)
            card.clicked.connect(self._jump_to_note)
            self.quick_layout.addWidget(card)

    def _jump_to_note(self, note_id: str):
        item = self._item_by_note.get(note_id)
        if item is not None:
            self.tree.setCurrentItem(item)
        # 必须显式打开：setCurrentItem 在"目标已是当前项"时**不发** currentItemChanged，
        # 光靠信号会出现"点了卡片但右栏没换"（_open_note 自己会对同一 id 去重，重复调无害）
        self._open_note(note_id)

    # ------------------------------------------------------------------
    # 新建 / 删除 / 排序
    # ------------------------------------------------------------------
    def _current_project_id(self):
        """当前上下文所属的项目：有打开条目就取它的，否则取树上选中的项目"""
        note = self.model.get_note(self._current_note_id) if self._current_note_id else None
        if note is not None:
            return note.project_id
        item = self.tree.currentItem()
        if item is not None:
            payload = item.data(0, Qt.ItemDataRole.UserRole + 1)
            if payload:
                kind, value = payload
                if kind == KIND_PROJECT:
                    return value
                if kind == KIND_CATEGORY:
                    return value[0]
        return None

    def _on_new_project(self):
        text, ok = InputDialog.get_text(
            self, "新建项目", "项目名称（如「A 车型」）:", "", "给这个项目起个名字")
        if not ok or not text:
            return
        use_template = QuestionDialog.ask(
            self, "新建项目", f"要为「{text}」生成标准模板吗？",
            "模板会预建四条骨架：速查 / 差异点 / 踩坑 / 环境。",
            yes_text="用模板", no_text="空白项目")
        project = self.model.add_project(text, use_template=use_template)
        # 先清掉当前条目：refresh_tree 是「有打开条目就优先还原它」的语义，
        # 不清的话新项目根本选不上，选中还停在旧条目上
        self._current_note_id = None
        self.refresh_tree(select_project_id=project.id)
        if not use_template:
            self._show_empty_state()
        show_toast(parent=self, message=f"已创建项目「{project.name}」")

    def _on_new_note(self):
        project_id = self._current_project_id()
        if project_id is None:
            WarningDialog.show_warning(self, "提示", "请先新建一个项目。")
            return
        # 分类默认取树上选中的分类；否则用「差异点」——比「速查」更常用来随手记
        category = CATEGORIES[1]
        item = self.tree.currentItem()
        if item is not None:
            payload = item.data(0, Qt.ItemDataRole.UserRole + 1)
            if payload and payload[0] == KIND_CATEGORY and payload[1][0] == project_id:
                category = payload[1][1]
        note = self.model.add_note(project_id, category, title="未命名条目")
        self._current_note_id = None          # 置空，保证 _open_note 真正载入
        self.refresh_tree(select_note_id=note.id)
        self.tabs.setCurrentIndex(0)          # 新建 → 直接进编辑
        self._open_note(note.id)
        self.title_edit.setFocus()
        self.title_edit.selectAll()

    def _on_rename_project(self, project_id: str):
        project = self.model.get_project(project_id)
        if project is None:
            return
        text, ok = InputDialog.get_text(self, "重命名项目", "项目名称:", project.name)
        if ok and text and self.model.rename_project(project_id, text):
            self.refresh_tree(select_project_id=project_id)

    def _on_delete_project(self, project_id: str):
        project = self.model.get_project(project_id)
        if project is None:
            return
        count = self.model.count_notes(project_id)
        if not ConfirmDeleteDialog.ask(
                self, "删除项目", f"确定删除项目「{project.name}」吗？",
                f"该项目下的 {count} 条笔记与图片会一并删除，且不可恢复。"):
            return
        self._dirty = False
        self._save_timer.stop()
        self._current_note_id = None
        self.model.delete_project(project_id)
        self.refresh_tree()
        self._show_empty_state()
        show_toast(parent=self, message=f"已删除项目「{project.name}」")

    def _on_delete_note(self, note_id: str):
        note = self.model.get_note(note_id)
        if note is None:
            return
        if not ConfirmDeleteDialog.ask(
                self, "删除条目", f"确定删除「{note.title}」吗？",
                "该条目引用的图片会一并删除，且不可恢复。"):
            return
        project_id = note.project_id
        self._dirty = False                   # 别在删除之后又把改动写回去
        self._save_timer.stop()
        self._current_note_id = None
        self.model.delete_note(note_id)
        self.refresh_tree(select_project_id=project_id)

    def _on_move_project(self, project_id: str, delta: int):
        if self.model.move_project(project_id, delta):
            self.refresh_tree(select_project_id=project_id)

    def _on_tree_context_menu(self, pos):
        item = self.tree.itemAt(pos)
        menu = QMenu(self)
        if item is None:
            menu.addAction(qta.icon('fa6s.folder-plus', color=self._icon_color('menu')), "新建项目",
                           self._on_new_project)
            menu.exec(self.tree.viewport().mapToGlobal(pos))
            return

        payload = item.data(0, Qt.ItemDataRole.UserRole + 1)
        if not payload:
            return
        kind, value = payload
        if kind == KIND_PROJECT:
            menu.addAction(qta.icon('fa6s.file-circle-plus', color=self._icon_color('menu')), "新建条目",
                           self._on_new_note)
            menu.addSeparator()
            menu.addAction(qta.icon('fa6s.pen', color=self._icon_color('menu')), "重命名项目",
                           lambda: self._on_rename_project(value))
            menu.addAction(qta.icon('fa6s.arrow-up', color=self._icon_color('menu')), "上移",
                           lambda: self._on_move_project(value, -1))
            menu.addAction(qta.icon('fa6s.arrow-down', color=self._icon_color('menu')), "下移",
                           lambda: self._on_move_project(value, 1))
            menu.addSeparator()
            menu.addAction(qta.icon('fa6s.trash', color=self._icon_color('menu_danger')), "删除项目",
                           lambda: self._on_delete_project(value))
        elif kind == KIND_CATEGORY:
            menu.addAction(qta.icon('fa6s.file-circle-plus', color=self._icon_color('menu')),
                           "在此分类下新建条目", self._on_new_note)
        else:
            menu.addAction(qta.icon('fa6s.pen', color=self._icon_color('menu')), "打开",
                           lambda: self._open_note(value))
            menu.addSeparator()
            menu.addAction(qta.icon('fa6s.trash', color=self._icon_color('menu_danger')), "删除条目",
                           lambda: self._on_delete_note(value))
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------------
    # 搜索
    # ------------------------------------------------------------------
    def _run_search(self):
        keyword = self.search_input.text().strip()
        if not keyword:
            self.right_stack.setCurrentIndex(0)
            return
        hits = self.model.search(keyword)
        self.search_list.clear()
        for hit in hits:
            note = hit.note
            if hit.matched == 'title':
                second = f"{hit.project_name} · {note.category}"
            else:
                second = f"{hit.project_name} · {note.category} · {hit.snippet}"
            item = QListWidgetItem(f"{note.title}\n{second}")
            item.setData(Qt.ItemDataRole.UserRole, note.id)
            item.setIcon(qta.icon('fa6s.file-lines', color=self._icon_color('flat')))
            self.search_list.addItem(item)
        self.search_summary.setText(
            f"「{keyword}」命中 {len(hits)} 条" if hits
            else f"没有找到含「{keyword}」的内容")
        self.right_stack.setCurrentIndex(1)

    def _on_search_result(self, item):
        note_id = item.data(Qt.ItemDataRole.UserRole)
        if not note_id:
            return
        self.search_input.clear()             # 清空即回到编辑页，行为可预期
        self.right_stack.setCurrentIndex(0)
        self._jump_to_note(note_id)

    # ------------------------------------------------------------------
    # 插入图片 / 链接
    # ------------------------------------------------------------------
    def _on_image_source(self, source):
        """编辑器抛来的图片：QImage（粘贴的位图）或 str（图片文件路径）"""
        if self._current_note_id is None:
            show_toast(parent=self, message="请先选择或新建一个条目")
            return
        suffix = ".png"
        try:
            if isinstance(source, QImage):
                buffer = QBuffer()
                buffer.open(QBuffer.OpenModeFlag.WriteOnly)
                source.save(buffer, "PNG")
                data = bytes(buffer.data())
            else:
                suffix = os.path.splitext(source)[1] or ".png"
                with open(source, "rb") as f:
                    data = f.read()
        except Exception as e:
            WarningDialog.show_warning(self, "插入图片失败", str(e))
            return
        if not data:
            return
        rel = self.model.save_asset(self._current_note_id, data, suffix)
        self.body_edit.insert_image(rel)
        self._on_content_changed()

    def _on_insert_image_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择图片", "", "图片 (*.png *.jpg *.jpeg *.bmp *.gif *.webp)")
        if path:
            self._on_image_source(path)

    def _on_insert_link(self):
        self.body_edit.insert_link()

    # ------------------------------------------------------------------
    # 导出
    # ------------------------------------------------------------------
    def _on_export(self):
        if not self.model.get_projects():
            WarningDialog.show_warning(self, "提示", "知识库还是空的，先记两条再导出。")
            return
        self.flush()
        dest = QFileDialog.getExistingDirectory(self, "选择导出目录")
        if not dest:
            return
        try:
            stats = self.model.export_markdown(dest)
        except Exception as e:
            WarningDialog.show_warning(self, "导出失败", str(e))
            return
        WarningDialog.show_warning(
            self, "导出完成",
            f"已导出 {stats['projects']} 个项目 / {stats['notes']} 条笔记。",
            f"图片 {stats['images']} 张已一并复制。\n目录：{dest}")

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def flush(self):
        """外部（切页 / 退出）想确保内容落盘时调一下"""
        self._save_timer.stop()
        self._flush_save()

    def hideEvent(self, event):
        self.flush()
        super().hideEvent(event)
