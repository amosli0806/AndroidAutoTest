# views/dialogs/kb_import_dialog.py
"""个人知识库「导入」对话框。

入口：知识库顶部工具栏 →「导入」（views/knowledge_view.py::_on_import）。

设计要点：
* **只做搬砖，不做转换**：源就是 Markdown / 纯文本（语雀、飞书、Obsidian 导出的
  Markdown 目录都能直接选），解析规则与 `KnowledgeModel.export_markdown` 对称 ——
  自己导出的库能原样导回来（见 models/knowledge_model.py::plan_import）。
* **先预览再落库**：扫描是只读的，界面上先给"将新增 N 条 / 跳过 M 条 / 新建项目 K 个"，
  确认后才写盘；重名判定按「目标项目 + 分类 + 标题」。
* 落位口径在界面上是显式的：默认分类 + 目标项目（自动 or 指定），不做隐式猜测。
"""
import os

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QComboBox,
    QLineEdit, QPushButton, QTreeWidget, QTreeWidgetItem, QFileDialog, QFrame,
)
from PyQt6.QtGui import QColor, QBrush
from PyQt6.QtCore import Qt

from models.knowledge_model import CATEGORIES, CATEGORY_DIFF
from utils.theme import ThemeMode
from utils.settings import Settings, THEME_MODE_DARK

# 预览列表最多展示这么多行（几千条全建进树里会把对话框拖慢，摘要数字仍是全量）
PREVIEW_LIMIT = 500


class KbImportDialog(QDialog):
    """选源 → 预览 → 落库。关闭后由调用方读 `dialog.stats` 拿统计结果。"""

    def __init__(self, model, parent=None):
        super().__init__(parent)
        self.model = model
        self._paths = []
        self._plan = None
        self._warnings = []
        self._theme_mode = ThemeMode.LIGHT
        self.stats = {"projects": 0, "notes": 0, "skipped": 0, "images": 0, "missing_images": 0}

        self.setWindowTitle("导入知识库")
        self.setObjectName("KbImportDialog")
        self.resize(720, 540)
        self.setMinimumSize(620, 460)
        self.setup_ui()
        self.apply_theme()
        self._rescan()

    # ------------------------------------------------------------------
    # 界面
    # ------------------------------------------------------------------
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        title = QLabel("导入知识库")
        title.setObjectName("titleLabel")
        layout.addWidget(title)

        hint = QLabel("选择文件夹或若干 .md / .txt 文件。按「一级子目录名 / 文件头声明的项目」"
                      "自动落位，分类可统一指定；同名条目默认跳过。")
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # ---- 源 ----
        src_row = QHBoxLayout()
        src_row.setSpacing(8)
        src_label = QLabel("源文档")
        src_label.setObjectName("formLabel")
        src_label.setFixedWidth(56)
        src_row.addWidget(src_label)
        self.path_edit = QLineEdit()
        self.path_edit.setReadOnly(True)
        self.path_edit.setPlaceholderText("还没有选择源（文件夹或文件）")
        src_row.addWidget(self.path_edit, 1)
        self.dir_btn = QPushButton("选择文件夹…")
        self.dir_btn.setObjectName("plainBtn")
        self.dir_btn.clicked.connect(self._on_pick_dir)
        src_row.addWidget(self.dir_btn)
        self.file_btn = QPushButton("选择文件…")
        self.file_btn.setObjectName("plainBtn")
        self.file_btn.clicked.connect(self._on_pick_files)
        src_row.addWidget(self.file_btn)
        layout.addLayout(src_row)

        # ---- 落位选项 ----
        opt = QGridLayout()
        opt.setHorizontalSpacing(8)
        opt.setVerticalSpacing(8)
        lab1 = QLabel("默认分类")
        lab1.setObjectName("formLabel")
        lab1.setFixedWidth(56)
        opt.addWidget(lab1, 0, 0)
        self.category_combo = QComboBox()
        self.category_combo.addItems(CATEGORIES)
        self.category_combo.setCurrentText(CATEGORY_DIFF)
        self.category_combo.currentIndexChanged.connect(self._rescan)
        opt.addWidget(self.category_combo, 0, 1)

        lab2 = QLabel("目标项目")
        lab2.setObjectName("formLabel")
        lab2.setFixedWidth(56)
        opt.addWidget(lab2, 0, 2)
        self.project_combo = QComboBox()
        self.project_combo.addItem("自动（按目录 / 文件头）")
        for p in self.model.get_projects():
            self.project_combo.addItem(p.name)
        self.project_combo.currentIndexChanged.connect(self._rescan)
        opt.addWidget(self.project_combo, 0, 3)

        lab3 = QLabel("重复条目")
        lab3.setObjectName("formLabel")
        lab3.setFixedWidth(56)
        opt.addWidget(lab3, 1, 0)
        self.dup_combo = QComboBox()
        self.dup_combo.addItems(["跳过同名", "仍然导入"])
        self.dup_combo.currentIndexChanged.connect(self._rescan)
        opt.addWidget(self.dup_combo, 1, 1)
        opt.setColumnStretch(1, 1)
        opt.setColumnStretch(3, 1)
        layout.addLayout(opt)

        sep = QFrame()
        sep.setObjectName("sepLine")
        sep.setFixedHeight(1)
        layout.addWidget(sep)

        # ---- 预览 ----
        self.summary_label = QLabel("请选择要导入的源。")
        self.summary_label.setObjectName("summaryLabel")
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        self.preview = QTreeWidget()
        self.preview.setHeaderHidden(False)
        self.preview.setColumnCount(4)
        self.preview.setHeaderLabels(["标题", "目标项目", "分类", "状态"])
        self.preview.setRootIsDecorated(False)
        self.preview.setUniformRowHeights(True)
        self.preview.setColumnWidth(0, 300)
        self.preview.setColumnWidth(1, 160)
        self.preview.setColumnWidth(2, 90)
        layout.addWidget(self.preview, 1)

        # ---- 底部 ----
        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        self.warn_label = QLabel("")
        self.warn_label.setObjectName("hintLabel")
        self.warn_label.setWordWrap(True)
        action_row.addWidget(self.warn_label, 1)
        # 不放「取消」：标题栏已有 ×（Esc 也能关），底部只留唯一主操作
        self.import_btn = QPushButton("开始导入")
        self.import_btn.setObjectName("okBtn")
        self.import_btn.setMinimumWidth(130)
        self.import_btn.clicked.connect(self._on_import_clicked)
        action_row.addWidget(self.import_btn)
        layout.addLayout(action_row)

    # ------------------------------------------------------------------
    # 选源
    # ------------------------------------------------------------------
    def _on_pick_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择要导入的文件夹")
        if d:
            self._paths = [d]
            self.path_edit.setText(d)
            self._rescan()

    def _on_pick_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择要导入的文档",
            "", "Markdown / 文本 (*.md *.markdown *.txt);;所有文件 (*.*)")
        if files:
            self._paths = list(files)
            self.path_edit.setText(f"已选 {len(files)} 个文件：" +
                                   "、".join(os.path.basename(f) for f in files[:3]) +
                                   ("…" if len(files) > 3 else ""))
            self._rescan()

    # ------------------------------------------------------------------
    # 扫描预览
    # ------------------------------------------------------------------
    def _mode(self):
        """(by_dir, default_project)：自动模式按目录/文件头，指定模式全部落到该项目。"""
        idx = self.project_combo.currentIndex()
        if idx <= 0:
            return True, ""
        return False, self.project_combo.currentText()

    def _fallback_project(self):
        """解析不出项目名时的兜底：目录名 → 文件名所在目录名 → 「导入」。"""
        if not self._paths:
            return "导入"
        first = self._paths[0]
        if os.path.isdir(first):
            return os.path.basename(os.path.normpath(first)) or "导入"
        return os.path.basename(os.path.dirname(first)) or "导入"

    def _rescan(self):
        self.preview.clear()
        if not self._paths:
            self._plan = None
            self.summary_label.setText("请选择要导入的源。")
            self.warn_label.setText("")
            self.import_btn.setEnabled(False)
            return
        by_dir, project = self._mode()
        self._plan, self._warnings = self.model.plan_import(
            self._paths, by_dir=by_dir, default_project=project,
            default_category=self.category_combo.currentText(),
            fallback_project=self._fallback_project())

        total = len(self._plan.items)
        dup = self._plan.count(duplicate=True)
        new = total - dup
        images = sum(len(it.asset_names) for it in self._plan.items)
        missing = sum(len(it.missing_assets) for it in self._plan.items)

        if total == 0:
            self.summary_label.setText("没有找到 .md / .txt 文件，换个目录试试。")
            self.import_btn.setEnabled(False)
        else:
            parts = [f"将新增 {new} 条"]
            if dup:
                parts.append(f"跳过 {dup} 条同名")
            if self._plan.projects_to_create:
                parts.append("新建项目 " + "、".join(self._plan.projects_to_create[:5]) +
                             ("…" if len(self._plan.projects_to_create) > 5 else ""))
            if images:
                parts.append(f"图片 {images} 张")
            if missing:
                parts.append(f"{missing} 张图片在源目录没找到")
            self.summary_label.setText(" · ".join(parts))
            # 只有「仍然导入」时重复项才会真正落库，故启用条件要跟着走
            self.import_btn.setEnabled(bool(new or self.dup_combo.currentIndex() == 1))

        for it in self._plan.items[:PREVIEW_LIMIT]:
            item = QTreeWidgetItem([it.title, it.project_name, it.category,
                                    "跳过（同名）" if it.duplicate else "新增"])
            if it.duplicate:
                for col in range(4):
                    item.setForeground(col, QBrush(QColor("#999999")))
            self.preview.addTopLevelItem(item)
        if total > PREVIEW_LIMIT:
            self.preview.addTopLevelItem(QTreeWidgetItem(
                [f"…… 其余 {total - PREVIEW_LIMIT} 条未显示（仍会导入）", "", "", ""]))

        self.warn_label.setText("；".join(self._warnings[:3]) +
                                (f"（共 {len(self._warnings)} 条提示）"
                                 if len(self._warnings) > 3 else ""))

    # ------------------------------------------------------------------
    # 落库
    # ------------------------------------------------------------------
    def _on_import_clicked(self):
        if not self._plan:
            return
        self.import_btn.setEnabled(False)
        self.import_btn.setText("正在导入…")
        # 让「正在导入…」先画出来（大目录可能有几百条）
        QDialog.repaint(self)
        try:
            self.stats = self.model.apply_import(
                self._plan, skip_duplicates=self.dup_combo.currentIndex() == 0)
        except Exception:
            self.import_btn.setEnabled(True)
            self.import_btn.setText("开始导入")
            raise
        self.accept()

    # ------------------------------------------------------------------
    # 主题
    # ------------------------------------------------------------------
    def apply_theme(self, theme_mode: ThemeMode = None):
        if theme_mode is None:
            theme_mode = (ThemeMode.DARK
                          if Settings.get_theme_mode() == THEME_MODE_DARK
                          else ThemeMode.LIGHT)
        self._theme_mode = theme_mode
        if theme_mode == ThemeMode.DARK:
            bg, text, border = "#2d2d2d", "#eeeeee", "#555"
            title, hint = "#ffffff", "#aaaaaa"
            input_bg, input_fg = "#1e1e1e", "#eeeeee"
            summary = "#ffd479"
            tree_bg, tree_alt = "#262626", "#2a2a2a"
        else:
            bg, text, border = "#ffffff", "#333333", "#d0d0d0"
            title, hint = "#1a1a1a", "#888888"
            input_bg, input_fg = "#ffffff", "#333333"
            summary = "#b26a00"
            tree_bg, tree_alt = "#ffffff", "#f7f8fa"

        self.setStyleSheet(f"""
            QDialog#KbImportDialog {{ background-color: {bg}; }}
            QDialog#KbImportDialog QLabel {{ color: {text}; background: transparent; font-size: 13px; }}
            QDialog#KbImportDialog QLabel#titleLabel {{ font-size: 16px; font-weight: bold; color: {title}; }}
            QDialog#KbImportDialog QLabel#hintLabel {{ color: {hint}; font-size: 12px; }}
            QDialog#KbImportDialog QLabel#formLabel {{ color: {text}; font-size: 13px; }}
            QDialog#KbImportDialog QLabel#summaryLabel {{ color: {summary}; font-size: 12px; }}
            QDialog#KbImportDialog QFrame#sepLine {{ background-color: {border}; border: none; }}
            QDialog#KbImportDialog QLineEdit,
            QDialog#KbImportDialog QComboBox {{
                background-color: {input_bg};
                color: {input_fg};
                border: 1px solid {border};
                border-radius: 4px;
                padding: 4px 8px;
                min-height: 22px;
                max-height: 22px;
                font-size: 13px;
            }}
            QDialog#KbImportDialog QComboBox QAbstractItemView {{
                background-color: {input_bg};
                color: {input_fg};
                border: 1px solid {border};
                selection-background-color: #1976d2;
                selection-color: #ffffff;
                outline: none;
            }}
            QDialog#KbImportDialog QPushButton#okBtn {{
                background-color: #1976d2;
                color: #ffffff;
                border: 1px solid #1976d2;
                border-radius: 4px;
                padding: 4px 12px;
                min-height: 22px;
                max-height: 22px;
                font-weight: 500;
            }}
            QDialog#KbImportDialog QPushButton#okBtn:hover {{ background-color: #1565c0; }}
            QDialog#KbImportDialog QPushButton#okBtn:disabled {{
                background-color: #9e9e9e; border-color: #9e9e9e;
            }}
            QDialog#KbImportDialog QPushButton#plainBtn {{
                background-color: {input_bg};
                color: {text};
                border: 1px solid {border};
                border-radius: 4px;
                padding: 4px 10px;
                min-height: 22px;
                max-height: 22px;
            }}
            QDialog#KbImportDialog QPushButton#plainBtn:hover {{ border-color: #1976d2; }}
            QDialog#KbImportDialog QTreeWidget {{
                background-color: {tree_bg};
                alternate-background-color: {tree_alt};
                color: {text};
                border: 1px solid {border};
                border-radius: 4px;
                font-size: 12px;
            }}
            QDialog#KbImportDialog QTreeWidget::item {{ padding: 2px 4px; }}
            QDialog#KbImportDialog QHeaderView::section {{
                background-color: {tree_alt};
                color: {hint};
                border: none;
                border-bottom: 1px solid {border};
                padding: 4px 6px;
                font-size: 12px;
            }}
        """)
        self.preview.setAlternatingRowColors(True)
