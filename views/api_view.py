# views/api_view.py
"""接口自动化页：左分组树 / 中接口列表 / 右请求-响应详情。

三栏分工（观感与「语音管理」「自动化编辑」一致）：
    左  接口管理  —— 分组 → 接口 的导航树，右键新建 / 复制 / 重命名 / 删除
    中  接口列表  —— 当前分组下的接口（名称 / 方法 / URL / 结果），多选后可批量执行
    右  详情      —— 上半是请求（方法 / URL / 头 / 体 / 断言），下半是响应与断言明细

编辑与执行的关系：右栏改完点「保存」落库；点「发送」会**先自动保存再发**，
切换接口时若有未保存的改动也会静默落库 —— 宁可多存一次，也不要让用户
"改了没保存、跑的还是旧参数"，或者切走之后改动凭空消失。

主题：与语音管理页同一套手法（自绘 QSS + 壁纸半透明），不依赖 Theme 全局表。
"""
import os

import qtawesome as qta
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMenu, QPlainTextEdit, QPushButton,
    QScrollArea, QSplitter, QTableWidget, QTableWidgetItem,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from models.api_model import (
    ASSERT_LABELS, ASSERT_TYPES, BODY_NONE, BODY_TYPES, DEFAULT_BASE_URL,
    METHODS, OPS, OP_EQ, ApiAssert)
from services.api_service import (
    format_headers_text, format_kv_text, parse_headers_text, parse_kv_text)
from utils.dialogs import ConfirmDeleteDialog, ErrorDialog, InputDialog
from utils.settings import Settings, THEME_MODE_DARK
from utils.theme import ThemeMode

# 断言表列
_A_ENABLED, _A_TYPE, _A_EXPR, _A_OP, _A_EXPECT = range(5)
# 中栏接口列表列
_C_NAME, _C_METHOD, _C_URL, _C_RESULT = range(4)


class ApiEnvDialog(QDialog):
    """环境设置：选/建环境，改 base_url 与变量表。

    变量表用「一行一个 name=value」的文本框而不是表格 —— 变量通常就那么几个，
    表格控件的代码量却要翻几倍，不值当。
    """

    def __init__(self, api_model, parent=None):
        super().__init__(parent)
        self.api_model = api_model
        self.setWindowTitle("环境设置")
        self.setModal(True)
        self.resize(560, 440)
        self.setup_ui()
        self.apply_theme()
        self._reload_envs()

    def setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)

        title = QLabel("环境设置")
        title.setObjectName("ApiDialogTitle")
        root.addWidget(title)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(QLabel("环境:"))
        self.env_combo = QComboBox()
        self.env_combo.setMinimumWidth(200)
        self.env_combo.currentIndexChanged.connect(self._on_env_changed)
        row.addWidget(self.env_combo)
        self.add_btn = QPushButton("新建")
        self.add_btn.clicked.connect(self._on_add_env)
        row.addWidget(self.add_btn)
        self.del_btn = QPushButton("删除")
        self.del_btn.clicked.connect(self._on_delete_env)
        row.addWidget(self.del_btn)
        row.addStretch()
        root.addLayout(row)

        form = QVBoxLayout()
        form.setSpacing(6)
        form.addWidget(QLabel("环境名称:"))
        self.name_edit = QLineEdit()
        form.addWidget(self.name_edit)

        form.addWidget(QLabel("base_url（可用 {{base_url}} 在接口里引用）:"))
        self.base_edit = QLineEdit()
        self.base_edit.setPlaceholderText(DEFAULT_BASE_URL)
        form.addWidget(self.base_edit)

        form.addWidget(QLabel("变量（一行一个，格式 名称=值，如 token=abc123）:"))
        self.vars_edit = QPlainTextEdit()
        self.vars_edit.setPlaceholderText("token=abc123\nuserId=10001")
        form.addWidget(self.vars_edit, 1)
        root.addLayout(form, 1)

        self.hint = QLabel("变量在 URL / 请求头 / 请求体 里用 {{名称}} 引用；"
                           "未定义的变量会让请求直接失败并说明是哪个。")
        self.hint.setObjectName("ApiDialogHint")
        self.hint.setWordWrap(True)
        root.addWidget(self.hint)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.ok_btn = QPushButton("确定")
        self.ok_btn.clicked.connect(self._on_ok)
        buttons.addWidget(self.ok_btn)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(cancel_btn)
        root.addLayout(buttons)

    def apply_theme(self, theme_mode: ThemeMode = None):
        if theme_mode is None:
            theme_mode = (ThemeMode.DARK if Settings.get_theme_mode() == THEME_MODE_DARK
                          else ThemeMode.LIGHT)
        is_dark = theme_mode == ThemeMode.DARK
        bg = "#2d2d2d" if is_dark else "#ffffff"
        fg = "#eee" if is_dark else "#333"
        border = "#555" if is_dark else "#d0d0d0"
        edit_bg = "#3a3a3a" if is_dark else "#ffffff"
        self.setStyleSheet(f"""
            QDialog {{ background: {bg}; }}
            QLabel {{ color: {fg}; background: transparent; }}
            #ApiDialogTitle {{ font-size: 16px; font-weight: 600; }}
            #ApiDialogHint {{ color: #888; font-size: 12px; }}
            QLineEdit, QPlainTextEdit, QComboBox {{
                background: {edit_bg}; color: {fg};
                border: 1px solid {border}; border-radius: 6px; padding: 5px 8px;
            }}
            QPushButton {{
                background: #1976d2; color: #fff; border: none;
                border-radius: 6px; padding: 6px 16px;
            }}
            QPushButton:hover {{ background: #1565c0; }}
        """)

    # ---------- 数据 ----------
    def _reload_envs(self):
        self.env_combo.blockSignals(True)
        self.env_combo.clear()
        for env in self.api_model.envs:
            self.env_combo.addItem(env.name)
        index = self.env_combo.findText(self.api_model.current_env)
        self.env_combo.setCurrentIndex(max(0, index))
        self.env_combo.blockSignals(False)
        self._on_env_changed()

    def _on_env_changed(self):
        env = self.api_model.get_env(self.env_combo.currentText())
        if env is None:
            return
        self.name_edit.setText(env.name)
        self.base_edit.setText(env.base_url)
        self.vars_edit.setPlainText(format_kv_text(env.variables))
        self.del_btn.setEnabled(len(self.api_model.envs) > 1)

    def _on_add_env(self):
        env = self.api_model.add_env()
        self._reload_envs()
        self.env_combo.setCurrentText(env.name)
        self._on_env_changed()
        self.name_edit.setFocus()

    def _on_delete_env(self):
        name = self.env_combo.currentText()
        if len(self.api_model.envs) <= 1:
            ErrorDialog.show_error(self, "无法删除", "至少要保留一套环境。")
            return
        if ConfirmDeleteDialog.ask(self, "删除环境", f"确定删除环境「{name}」？"):
            self.api_model.remove_env(name)
            self._reload_envs()

    def _on_ok(self):
        old_name = self.env_combo.currentText()
        name = (self.name_edit.text() or "").strip()
        if not name:
            ErrorDialog.show_error(self, "保存失败", "环境名称不能为空。")
            return
        if name != old_name and self.api_model.get_env(name) is not None:
            ErrorDialog.show_error(self, "保存失败", f"已存在同名环境「{name}」。")
            return
        try:
            variables = parse_kv_text(self.vars_edit.toPlainText())
        except ValueError as e:
            ErrorDialog.show_error(self, "变量格式有误", str(e))
            return
        self.api_model.update_env(old_name, name=name,
                                 base_url=(self.base_edit.text() or "").strip(),
                                 variables=variables)
        self.accept()


class ApiView(QWidget):
    """接口自动化主视图。执行类动作只发信号，由 ApiController 接手。"""

    send_requested = pyqtSignal(str)          # 发送单个接口（case_id）
    run_selected_requested = pyqtSignal(list)  # 批量执行（case_id 列表）
    report_requested = pyqtSignal()
    stop_requested = pyqtSignal()

    def __init__(self, api_model, parent=None):
        super().__init__(parent)
        self.setObjectName("ApiView")
        self.model = api_model
        self._current_case_id = ""
        self._current_group_id = ""
        self._results = {}          # case_id -> ApiResult
        self._dirty = False
        self._running = False
        self.setup_ui()
        self.apply_theme()
        self.refresh_all()

    # ==================================================================
    # 主题
    # ==================================================================
    def apply_theme(self, theme_mode: ThemeMode = None):
        if theme_mode is None:
            theme_mode = (ThemeMode.DARK if Settings.get_theme_mode() == THEME_MODE_DARK
                          else ThemeMode.LIGHT)
        is_dark = theme_mode == ThemeMode.DARK
        has_wp = False
        try:
            wp = Settings.get_wallpaper_path()
            has_wp = bool(wp and os.path.exists(wp))
        except Exception:
            has_wp = False

        if is_dark:
            container_bg = "transparent" if has_wp else "#2d2d2d"
            border = "rgba(85, 85, 85, 0.9)" if has_wp else "#555"
            fg = "#eee"
            edit_bg = "rgba(58, 58, 58, 0.85)" if has_wp else "#3a3a3a"
            sel_bg = "#1e3a5f"
            hover = "rgba(74, 74, 74, 0.6)"
            head_bg = "rgba(58, 58, 58, 0.9)" if has_wp else "#3a3a3a"
        else:
            container_bg = "transparent" if has_wp else "white"
            border = "rgba(208, 208, 208, 0.9)" if has_wp else "#d0d0d0"
            fg = "#333"
            edit_bg = "rgba(255, 255, 255, 0.9)" if has_wp else "#ffffff"
            sel_bg = "#d0e4f7"
            hover = "#dfe2e6"
            head_bg = "rgba(232, 234, 237, 0.9)" if has_wp else "#e8eaed"

        self.setStyleSheet(f"""
            #ApiView {{ background: transparent; }}
            #ApiLeftContainer, #ApiMidContainer, #ApiRightContainer {{
                background-color: {container_bg};
                border: 1px solid {border};
                border-radius: 8px;
            }}
            #ApiPaneTitle {{ color: {fg}; font-size: 14px; font-weight: 600; }}
            #ApiHint {{ color: #888; font-size: 12px; }}
            QLabel {{ color: {fg}; background: transparent; }}
            QTreeWidget, QTableWidget, QPlainTextEdit, QLineEdit, QComboBox {{
                background: {edit_bg}; color: {fg};
                border: 1px solid {border}; border-radius: 6px;
                selection-background-color: {sel_bg};
            }}
            QTreeWidget, QTableWidget {{ outline: none; }}
            QTreeWidget::item, QTableWidget::item {{ padding: 4px 2px; }}
            QTreeWidget::item:hover:!selected, QTableWidget::item:hover:!selected {{
                background: {hover};
            }}
            QHeaderView::section {{
                background: {head_bg}; color: {fg};
                border: none; border-bottom: 1px solid {border};
                padding: 5px 6px; font-weight: 600;
            }}
            QPushButton {{
                background: #1976d2; color: #fff; border: none;
                border-radius: 6px; padding: 5px 12px;
            }}
            QPushButton:hover {{ background: #1565c0; }}
            QPushButton:disabled {{ background: #9e9e9e; }}
            QPushButton#ApiGhostBtn {{
                background: transparent; color: {fg}; border: 1px solid {border};
            }}
            QPushButton#ApiGhostBtn:hover {{ background: {hover}; }}
            QPlainTextEdit {{ padding: 6px; }}
        """)
        # 响应体只读但要能选中复制
        self.response_edit.setReadOnly(True)

    # ==================================================================
    # 构建
    # ==================================================================
    def setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter)

        splitter.addWidget(self._build_left())
        splitter.addWidget(self._build_middle())
        splitter.addWidget(self._build_right())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 3)
        splitter.setSizes([260, 340, 640])

    # ---------- 左：分组树 ----------
    def _build_left(self):
        box = QFrame()
        box.setObjectName("ApiLeftContainer")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        title_row = QHBoxLayout()
        title = QLabel("接口管理")
        title.setObjectName("ApiPaneTitle")
        title_row.addWidget(title)
        title_row.addStretch()
        self.add_group_btn = QPushButton("新建分组")
        self.add_group_btn.setObjectName("ApiGhostBtn")
        self.add_group_btn.clicked.connect(self._on_add_group)
        title_row.addWidget(self.add_group_btn)
        layout.addLayout(title_row)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_tree_menu)
        self.tree.itemClicked.connect(self._on_tree_clicked)
        layout.addWidget(self.tree, 1)

        bottom = QHBoxLayout()
        self.import_btn = QPushButton("导入")
        self.import_btn.setObjectName("ApiGhostBtn")
        self.import_btn.clicked.connect(self._on_import)
        self.export_btn = QPushButton("导出")
        self.export_btn.setObjectName("ApiGhostBtn")
        self.export_btn.clicked.connect(self._on_export)
        bottom.addWidget(self.import_btn)
        bottom.addWidget(self.export_btn)
        layout.addLayout(bottom)
        return box

    # ---------- 中：接口列表 ----------
    def _build_middle(self):
        box = QFrame()
        box.setObjectName("ApiMidContainer")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        title = QLabel("接口列表")
        title.setObjectName("ApiPaneTitle")
        layout.addWidget(title)

        env_row = QHBoxLayout()
        env_row.setSpacing(6)
        env_row.addWidget(QLabel("环境:"))
        self.env_combo = QComboBox()
        self.env_combo.currentIndexChanged.connect(self._on_env_changed)
        env_row.addWidget(self.env_combo, 1)
        self.env_btn = QPushButton("设置")
        self.env_btn.setObjectName("ApiGhostBtn")
        self.env_btn.clicked.connect(self._on_edit_envs)
        env_row.addWidget(self.env_btn)
        layout.addLayout(env_row)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self.send_btn = QPushButton("发送")
        self.send_btn.clicked.connect(self._on_send)
        self.run_btn = QPushButton("执行选中")
        self.run_btn.clicked.connect(self._on_run_selected)
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setObjectName("ApiGhostBtn")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_requested.emit)
        self.report_btn = QPushButton("生成报告")
        self.report_btn.setObjectName("ApiGhostBtn")
        self.report_btn.clicked.connect(self.report_requested.emit)
        for b in (self.send_btn, self.run_btn, self.stop_btn, self.report_btn):
            btn_row.addWidget(b)
        layout.addLayout(btn_row)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["名称", "方法", "URL", "结果"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_table_menu)
        self.table.itemSelectionChanged.connect(self._on_table_selection_changed)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(_C_NAME, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(_C_METHOD, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(_C_URL, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(_C_RESULT, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setColumnWidth(_C_NAME, 130)
        layout.addWidget(self.table, 1)
        return box

    # ---------- 右：详情 ----------
    def _build_right(self):
        box = QFrame()
        box.setObjectName("ApiRightContainer")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)

        # 上半：请求
        req_wrap = QWidget()
        req_outer = QVBoxLayout(req_wrap)
        req_outer.setContentsMargins(0, 0, 0, 0)
        req_outer.setSpacing(8)

        head_row = QHBoxLayout()
        title = QLabel("请求")
        title.setObjectName("ApiPaneTitle")
        head_row.addWidget(title)
        head_row.addStretch()
        self.save_btn = QPushButton("保存")
        self.save_btn.clicked.connect(self._on_save)
        head_row.addWidget(self.save_btn)
        req_outer.addLayout(head_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        form_host = QWidget()
        form = QVBoxLayout(form_host)
        form.setContentsMargins(2, 2, 2, 2)
        form.setSpacing(6)

        form.addWidget(QLabel("名称:"))
        self.name_edit = QLineEdit()
        self.name_edit.textChanged.connect(self._mark_dirty)
        form.addWidget(self.name_edit)

        url_row = QHBoxLayout()
        url_row.setSpacing(6)
        self.method_combo = QComboBox()
        self.method_combo.addItems(METHODS)
        self.method_combo.setFixedWidth(96)
        self.method_combo.currentIndexChanged.connect(self._mark_dirty)
        url_row.addWidget(self.method_combo)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("{{base_url}}/api/user 或 http://完整地址")
        self.url_edit.textChanged.connect(self._mark_dirty)
        url_row.addWidget(self.url_edit, 1)
        form.addLayout(url_row)

        form.addWidget(QLabel("请求头（一行一个，格式 名称: 值）:"))
        self.headers_edit = QPlainTextEdit()
        self.headers_edit.setPlaceholderText("Content-Type: application/json\nAuthorization: Bearer {{token}}")
        self.headers_edit.setFixedHeight(78)
        self.headers_edit.textChanged.connect(self._mark_dirty)
        form.addWidget(self.headers_edit)

        body_row = QHBoxLayout()
        body_row.setSpacing(6)
        body_row.addWidget(QLabel("请求体:"))
        self.body_combo = QComboBox()
        self.body_combo.addItems(BODY_TYPES)
        self.body_combo.setFixedWidth(110)
        self.body_combo.currentIndexChanged.connect(self._on_body_type_changed)
        body_row.addWidget(self.body_combo)
        body_row.addStretch()
        form.addLayout(body_row)
        self.body_edit = QPlainTextEdit()
        self.body_edit.setPlaceholderText('{"name": "{{token}}"}')
        self.body_edit.setFixedHeight(96)
        self.body_edit.textChanged.connect(self._mark_dirty)
        form.addWidget(self.body_edit)

        assert_row = QHBoxLayout()
        assert_row.setSpacing(6)
        assert_row.addWidget(QLabel("断言:"))
        assert_row.addStretch()
        add_assert_btn = QPushButton("+ 添加")
        add_assert_btn.setObjectName("ApiGhostBtn")
        add_assert_btn.clicked.connect(self._on_add_assert)
        assert_row.addWidget(add_assert_btn)
        del_assert_btn = QPushButton("− 删除选中")
        del_assert_btn.setObjectName("ApiGhostBtn")
        del_assert_btn.clicked.connect(self._on_remove_assert)
        assert_row.addWidget(del_assert_btn)
        form.addLayout(assert_row)

        self.assert_table = QTableWidget(0, 5)
        self.assert_table.setHorizontalHeaderLabels(["启用", "类型", "表达式", "操作符", "期望值"])
        self.assert_table.verticalHeader().setVisible(False)
        self.assert_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        ah = self.assert_table.horizontalHeader()
        ah.setSectionResizeMode(_A_ENABLED, QHeaderView.ResizeMode.ResizeToContents)
        ah.setSectionResizeMode(_A_TYPE, QHeaderView.ResizeMode.ResizeToContents)
        ah.setSectionResizeMode(_A_EXPR, QHeaderView.ResizeMode.Stretch)
        ah.setSectionResizeMode(_A_OP, QHeaderView.ResizeMode.ResizeToContents)
        ah.setSectionResizeMode(_A_EXPECT, QHeaderView.ResizeMode.Stretch)
        self.assert_table.setMinimumHeight(120)
        self.assert_table.itemChanged.connect(self._mark_dirty)
        form.addWidget(self.assert_table)

        self.assert_hint = QLabel(
            "「响应取值」的表达式填 JSON 路径（如 data.name、data.list[0].id）；"
            "「包含文本 / 正则匹配」填要匹配的内容；「状态码 / 耗时」留空即可。")
        self.assert_hint.setObjectName("ApiHint")
        self.assert_hint.setWordWrap(True)
        form.addWidget(self.assert_hint)

        scroll.setWidget(form_host)
        req_outer.addWidget(scroll, 1)

        # 下半：响应
        resp_wrap = QWidget()
        resp = QVBoxLayout(resp_wrap)
        resp.setContentsMargins(0, 0, 0, 0)
        resp.setSpacing(6)
        resp_title = QLabel("响应")
        resp_title.setObjectName("ApiPaneTitle")
        resp.addWidget(resp_title)
        self.summary_label = QLabel("尚未执行")
        self.summary_label.setObjectName("ApiHint")
        self.summary_label.setWordWrap(True)
        resp.addWidget(self.summary_label)
        self.response_edit = QPlainTextEdit()
        self.response_edit.setReadOnly(True)
        self.response_edit.setPlaceholderText("发送请求后在这里看响应内容")
        resp.addWidget(self.response_edit, 1)

        self.result_table = QTableWidget(0, 4)
        self.result_table.setHorizontalHeaderLabels(["断言", "结果", "实际值", "失败原因"])
        self.result_table.verticalHeader().setVisible(False)
        self.result_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        rh = self.result_table.horizontalHeader()
        rh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        rh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        rh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        rh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.result_table.setFixedHeight(120)
        resp.addWidget(self.result_table)

        splitter.addWidget(req_wrap)
        splitter.addWidget(resp_wrap)
        splitter.setSizes([520, 320])
        layout.addWidget(splitter, 1)
        return box

    # ==================================================================
    # 刷新
    # ==================================================================
    def refresh_all(self):
        """重读模型，重建树与列表（导入/导出、环境切换后调用）。"""
        self._reload_env_combo()
        self._reload_tree()
        self._reload_table()
        if self._current_case_id:
            case = self.model.get_case(self._current_case_id)
            if case is None:
                self._current_case_id = ""
            else:
                self._load_case(case)
        if not self._current_case_id:
            self._clear_form()

    def _reload_env_combo(self):
        self.env_combo.blockSignals(True)
        self.env_combo.clear()
        for env in self.model.envs:
            self.env_combo.addItem(env.name)
        index = self.env_combo.findText(self.model.current_env)
        self.env_combo.setCurrentIndex(max(0, index))
        self.env_combo.blockSignals(False)

    def _reload_tree(self):
        self.tree.clear()
        for group in self.model.groups:
            cases = self.model.cases_of_group(group.id)
            node = QTreeWidgetItem([f"{group.name}（{len(cases)}）"])
            node.setData(0, Qt.ItemDataRole.UserRole, ("group", group.id))
            node.setIcon(0, qta.icon('fa6s.folder', color='#f0b429'))
            for case in cases:
                child = QTreeWidgetItem([f"{case.method}  {case.name}"])
                child.setData(0, Qt.ItemDataRole.UserRole, ("case", case.id))
                child.setIcon(0, qta.icon('fa6s.plug', color='#7f8c8d'))
                node.addChild(child)
            self.tree.addTopLevelItem(node)
            node.setExpanded(True)
        # 选中态跟着 _current_case_id / _current_group_id 走
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            kind, ident = top.data(0, Qt.ItemDataRole.UserRole)
            if ident == self._current_group_id and not self._current_case_id:
                self.tree.setCurrentItem(top)
            for j in range(top.childCount()):
                child = top.child(j)
                _, cid = child.data(0, Qt.ItemDataRole.UserRole)
                if cid == self._current_case_id:
                    self.tree.setCurrentItem(child)

    def _reload_table(self):
        cases = self.model.cases_of_group(self._current_group_id) \
            if self._current_group_id else []
        self.table.blockSignals(True)
        self.table.setRowCount(len(cases))
        for row, case in enumerate(cases):
            name_item = QTableWidgetItem(case.name)
            name_item.setData(Qt.ItemDataRole.UserRole, case.id)
            self.table.setItem(row, _C_NAME, name_item)
            self.table.setItem(row, _C_METHOD, QTableWidgetItem(case.method))
            self.table.setItem(row, _C_URL, QTableWidgetItem(case.url))
            self.table.setItem(row, _C_RESULT,
                               QTableWidgetItem(self._result_text(case.id)))
        self.table.blockSignals(False)
        # 保持当前选中行
        if self._current_case_id:
            for row in range(self.table.rowCount()):
                if self.table.item(row, _C_NAME).data(Qt.ItemDataRole.UserRole) \
                        == self._current_case_id:
                    self.table.selectRow(row)
                    break

    def _result_text(self, case_id: str) -> str:
        result = self._results.get(case_id)
        if result is None:
            return "未执行"
        if result.error:
            return "请求失败"
        if result.ok:
            return f"通过 · {result.elapsed_ms}ms"
        return f"失败 · {result.status_code}"

    # ==================================================================
    # 表单 <-> 数据
    # ==================================================================
    def _clear_form(self):
        for widget in (self.name_edit, self.url_edit):
            widget.blockSignals(True)
            widget.clear()
            widget.blockSignals(False)
        self.headers_edit.blockSignals(True)
        self.headers_edit.clear()
        self.headers_edit.blockSignals(False)
        self.body_edit.blockSignals(True)
        self.body_edit.clear()
        self.body_edit.blockSignals(False)
        self.body_combo.setCurrentText(BODY_NONE)
        self.method_combo.setCurrentText("GET")
        self.assert_table.setRowCount(0)
        self._dirty = False
        self._show_result(None)

    def _load_case(self, case):
        self._loading = True
        self.name_edit.setText(case.name)
        self.method_combo.setCurrentText(case.method if case.method in METHODS else "GET")
        self.url_edit.setText(case.url)
        self.headers_edit.setPlainText(format_headers_text(case.headers))
        self.body_combo.setCurrentText(case.body_type or BODY_NONE)
        self.body_edit.setPlainText(case.body)
        self.body_edit.setEnabled(case.body_type != BODY_NONE)
        self._fill_assert_table(case.asserts)
        self._loading = False
        self._dirty = False
        self._show_result(self._results.get(case.id))

    def _fill_assert_table(self, asserts):
        self.assert_table.blockSignals(True)
        self.assert_table.setRowCount(len(asserts))
        for row, item in enumerate(asserts):
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            check.setCheckState(Qt.CheckState.Checked if item.enabled
                                else Qt.CheckState.Unchecked)
            self.assert_table.setItem(row, _A_ENABLED, check)

            type_combo = QComboBox()
            for t in ASSERT_TYPES:
                type_combo.addItem(ASSERT_LABELS[t], t)
            index = type_combo.findData(item.type)
            type_combo.setCurrentIndex(max(0, index))
            type_combo.currentIndexChanged.connect(self._mark_dirty)
            self.assert_table.setCellWidget(row, _A_TYPE, type_combo)

            self.assert_table.setItem(row, _A_EXPR, QTableWidgetItem(item.expr))

            op_combo = QComboBox()
            op_combo.addItems(OPS)
            op_combo.setCurrentText(item.op if item.op in OPS else OP_EQ)
            op_combo.currentIndexChanged.connect(self._mark_dirty)
            self.assert_table.setCellWidget(row, _A_OP, op_combo)

            self.assert_table.setItem(row, _A_EXPECT, QTableWidgetItem(item.expect))
        self.assert_table.blockSignals(False)

    def _collect_asserts(self):
        asserts = []
        for row in range(self.assert_table.rowCount()):
            check = self.assert_table.item(row, _A_ENABLED)
            type_combo = self.assert_table.cellWidget(row, _A_TYPE)
            op_combo = self.assert_table.cellWidget(row, _A_OP)
            expr_item = self.assert_table.item(row, _A_EXPR)
            expect_item = self.assert_table.item(row, _A_EXPECT)
            asserts.append(ApiAssert(
                type=type_combo.currentData() if type_combo else "status",
                expr=(expr_item.text() if expr_item else "").strip(),
                op=op_combo.currentText() if op_combo else OP_EQ,
                expect=(expect_item.text() if expect_item else "").strip(),
                enabled=(check is None or check.checkState() == Qt.CheckState.Checked),
            ))
        return asserts

    def _mark_dirty(self, *args):
        if not getattr(self, "_loading", False) and self._current_case_id:
            self._dirty = True

    def save_current(self, quiet: bool = True) -> bool:
        """把右栏内容写回模型。返回是否成功；quiet=True 时不弹错误框。"""
        case = self.model.get_case(self._current_case_id)
        if case is None:
            return False
        name = (self.name_edit.text() or "").strip()
        if not name:
            if not quiet:
                ErrorDialog.show_error(self, "保存失败", "接口名称不能为空。")
            return False
        try:
            headers = parse_headers_text(self.headers_edit.toPlainText())
        except ValueError as e:
            if not quiet:
                ErrorDialog.show_error(self, "请求头格式有误", str(e))
            return False
        body_type = self.body_combo.currentText()
        if body_type == BODY_NONE:
            body = ""
        else:
            body = self.body_edit.toPlainText()
        self.model.update_case(
            case.id, name=name,
            method=self.method_combo.currentText(),
            url=(self.url_edit.text() or "").strip(),
            headers=headers, body_type=body_type, body=body,
            asserts=self._collect_asserts())
        self._dirty = False
        self._reload_tree()
        self._reload_table()
        return True

    # ==================================================================
    # 左栏交互
    # ==================================================================
    def _selected_tree_node(self):
        item = self.tree.currentItem()
        if item is None:
            return None, ""
        kind, ident = item.data(0, Qt.ItemDataRole.UserRole)
        return kind, ident

    def _on_tree_clicked(self, item, _column=0):
        self._flush_pending_edit()
        kind, ident = item.data(0, Qt.ItemDataRole.UserRole)
        if kind == "group":
            self._current_group_id = ident
            self._current_case_id = ""
            self._reload_table()
            self._clear_form()
        else:
            case = self.model.get_case(ident)
            if case is None:
                return
            self._current_case_id = ident
            self._current_group_id = case.group_id
            self._reload_table()
            self._load_case(case)

    def _flush_pending_edit(self):
        """切走之前把未保存的改动落库 —— 见文件头注释。"""
        if self._dirty and self._current_case_id:
            self.save_current(quiet=True)

    def _on_tree_menu(self, pos):
        item = self.tree.itemAt(pos)
        menu = QMenu(self)
        if item is None:
            menu.addAction("新建分组", self._on_add_group)
        else:
            kind, ident = item.data(0, Qt.ItemDataRole.UserRole)
            if kind == "group":
                menu.addAction("在此分组新建接口",
                               lambda: self._on_add_case(ident))
                menu.addAction("重命名分组", lambda: self._on_rename_group(ident))
                menu.addAction("删除分组", lambda: self._on_delete_group(ident))
            else:
                menu.addAction("复制接口", lambda: self._on_copy_case(ident))
                menu.addAction("重命名接口", lambda: self._on_rename_case(ident))
                menu.addAction("删除接口", lambda: self._on_delete_case(ident))
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _on_add_group(self):
        name, ok = InputDialog.get_text(self, "新建分组", "分组名称：", "新分组")
        if not ok:
            return
        group = self.model.add_group(name.strip())
        self._current_group_id = group.id
        self._current_case_id = ""
        self._reload_tree()
        self._reload_table()
        self._clear_form()

    def _on_rename_group(self, group_id):
        group = self.model.get_group(group_id)
        if group is None:
            return
        name, ok = InputDialog.get_text(self, "重命名分组", "分组名称：", group.name)
        if not ok:
            return
        self.model.rename_group(group_id, name.strip())
        self._reload_tree()

    def _on_delete_group(self, group_id):
        group = self.model.get_group(group_id)
        if group is None:
            return
        count = len(self.model.cases_of_group(group_id))
        tip = f"确定删除分组「{group.name}」？"
        if count:
            tip += f"\n组内 {count} 个接口会一并删除。"
        if not ConfirmDeleteDialog.ask(self, "删除分组", tip):
            return
        self.model.remove_group(group_id, with_cases=True)
        if self._current_group_id == group_id:
            self._current_group_id = ""
            self._current_case_id = ""
        self.refresh_all()

    def _on_add_case(self, group_id=""):
        if not group_id:
            kind, ident = self._selected_tree_node()
            group_id = ident if kind == "group" else self._current_group_id
        if not group_id:
            group_id = self.model.groups[0].id if self.model.groups else ""
        case = self.model.add_case("", group_id)
        self._current_case_id = case.id
        self._current_group_id = case.group_id
        self._reload_tree()
        self._reload_table()
        self._load_case(case)
        self.name_edit.setFocus()
        self.name_edit.selectAll()

    def _on_copy_case(self, case_id):
        new_case = self.model.copy_case(case_id)
        if new_case is None:
            return
        self._current_case_id = new_case.id
        self._current_group_id = new_case.group_id
        self._reload_tree()
        self._reload_table()
        self._load_case(new_case)

    def _on_rename_case(self, case_id):
        case = self.model.get_case(case_id)
        if case is None:
            return
        name, ok = InputDialog.get_text(self, "重命名接口", "接口名称：", case.name)
        if not ok:
            return
        self.model.rename_case(case_id, name.strip())
        self._reload_tree()
        self._reload_table()

    def _on_delete_case(self, case_id):
        case = self.model.get_case(case_id)
        if case is None:
            return
        if not ConfirmDeleteDialog.ask(self, "删除接口",
                                       f"确定删除接口「{case.name}」？"):
            return
        self.model.remove_case(case_id)
        self._results.pop(case_id, None)
        if self._current_case_id == case_id:
            self._current_case_id = ""
        self.refresh_all()

    # ==================================================================
    # 中栏交互
    # ==================================================================
    def _on_table_menu(self, pos):
        menu = QMenu(self)
        menu.addAction("新建接口", lambda: self._on_add_case(self._current_group_id))
        menu.addAction("复制接口", lambda: self._on_copy_selected("copy"))
        menu.addAction("删除接口", lambda: self._on_copy_selected("delete"))
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _on_copy_selected(self, action: str):
        ids = self.selected_case_ids()
        if not ids:
            return
        if action == "copy":
            for cid in ids:
                self.model.copy_case(cid)
        else:
            if not ConfirmDeleteDialog.ask(self, "删除接口",
                                           f"确定删除选中的 {len(ids)} 个接口？"):
                return
            for cid in ids:
                self.model.remove_case(cid)
                self._results.pop(cid, None)
        self.refresh_all()

    def selected_case_ids(self):
        ids = []
        for row in sorted({i.row() for i in self.table.selectedIndexes()}):
            item = self.table.item(row, _C_NAME)
            if item is not None:
                ids.append(item.data(Qt.ItemDataRole.UserRole))
        return ids

    def _on_table_selection_changed(self):
        ids = self.selected_case_ids()
        if not ids:
            return
        if ids[0] == self._current_case_id:
            return
        self._flush_pending_edit()
        case = self.model.get_case(ids[0])
        if case is None:
            return
        self._current_case_id = case.id
        self._load_case(case)
        self._reload_tree()

    def _on_env_changed(self):
        name = self.env_combo.currentText()
        if name and name != self.model.current_env:
            self.model.set_current_env(name)

    def _on_edit_envs(self):
        dlg = ApiEnvDialog(self.model, self)
        dlg.exec()
        self._reload_env_combo()

    def _on_body_type_changed(self):
        self.body_edit.setEnabled(self.body_combo.currentText() != BODY_NONE)
        self._mark_dirty()

    # ---------- 断言表 ----------
    def _on_add_assert(self):
        row = self.assert_table.rowCount()
        self.assert_table.blockSignals(True)
        self.assert_table.insertRow(row)
        check = QTableWidgetItem()
        check.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
        check.setCheckState(Qt.CheckState.Checked)
        self.assert_table.setItem(row, _A_ENABLED, check)
        type_combo = QComboBox()
        for t in ASSERT_TYPES:
            type_combo.addItem(ASSERT_LABELS[t], t)
        type_combo.currentIndexChanged.connect(self._mark_dirty)
        self.assert_table.setCellWidget(row, _A_TYPE, type_combo)
        self.assert_table.setItem(row, _A_EXPR, QTableWidgetItem(""))
        op_combo = QComboBox()
        op_combo.addItems(OPS)
        op_combo.currentIndexChanged.connect(self._mark_dirty)
        self.assert_table.setCellWidget(row, _A_OP, op_combo)
        self.assert_table.setItem(row, _A_EXPECT, QTableWidgetItem(""))
        self.assert_table.blockSignals(False)
        self._mark_dirty()

    def _on_remove_assert(self):
        rows = sorted({i.row() for i in self.assert_table.selectedIndexes()},
                      reverse=True)
        for row in rows:
            self.assert_table.removeRow(row)
        if rows:
            self._mark_dirty()

    # ==================================================================
    # 执行入口
    # ==================================================================
    def _on_save(self):
        if self.save_current(quiet=False):
            self.summary_label.setText("已保存")

    def _on_send(self):
        if not self._current_case_id:
            ErrorDialog.show_error(self, "无法发送", "先在左侧选中一个接口。")
            return
        if not self.save_current(quiet=False):
            return
        self.send_requested.emit(self._current_case_id)

    def _on_run_selected(self):
        ids = self.selected_case_ids()
        if not ids:
            ids = [self._current_case_id] if self._current_case_id else []
        if not ids:
            ErrorDialog.show_error(self, "无法执行", "先选中要执行的接口（可多选）。")
            return
        if self._dirty:
            self.save_current(quiet=True)
        self.run_selected_requested.emit(ids)

    # ==================================================================
    # 由 controller 调用
    # ==================================================================
    def set_running(self, running: bool):
        self._running = running
        for btn in (self.send_btn, self.run_btn, self.report_btn):
            btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        self.send_btn.setText("发送中…" if running else "发送")

    def show_result(self, result, refresh_table: bool = True):
        """落一条执行结果：更新中栏结果列 + 若正是当前接口则刷新右下详情。"""
        self._results[result.case_id] = result
        if refresh_table:
            self._reload_table()
        if result.case_id == self._current_case_id:
            self._show_result(result)

    def _show_result(self, result):
        if result is None:
            self.summary_label.setText("尚未执行")
            self.response_edit.clear()
            self.result_table.setRowCount(0)
            return
        if result.error:
            self.summary_label.setText(f"✗ {result.summary()}")
        elif result.ok:
            self.summary_label.setText(
                f"✓ {result.method} {result.url}\n{result.summary()}")
        else:
            self.summary_label.setText(
                f"✗ {result.method} {result.url}\n{result.summary()}")
        self.response_edit.setPlainText(result.response_text or result.error)

        self.result_table.setRowCount(len(result.assert_results))
        for row, item in enumerate(result.assert_results):
            self.result_table.setItem(
                row, 0, QTableWidgetItem(item.assert_.describe()))
            self.result_table.setItem(
                row, 1, QTableWidgetItem("通过" if item.ok else "未通过"))
            self.result_table.setItem(row, 2, QTableWidgetItem(str(item.actual)))
            self.result_table.setItem(row, 3, QTableWidgetItem(item.message))

    # ==================================================================
    # 导入 / 导出
    # ==================================================================
    def _on_import(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "导入接口配置", "", "JSON 文件 (*.json);;所有文件 (*)")
        if not path:
            return
        try:
            import json
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            stats = self.model.import_all(data)
        except Exception as e:
            ErrorDialog.show_error(self, "导入失败", str(e))
            return
        self.refresh_all()
        self.summary_label.setText(
            f"导入完成：新增 {stats['cases']} 个接口、{stats['groups']} 个分组、"
            f"{stats['envs']} 套环境；跳过同名 {stats['skipped']} 个")

    def _on_export(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "导出接口配置", "api_export.json", "JSON 文件 (*.json)")
        if not path:
            return
        try:
            import json
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.model.export_all(), f, ensure_ascii=False, indent=2)
        except Exception as e:
            ErrorDialog.show_error(self, "导出失败", str(e))
            return
        self.summary_label.setText(f"已导出到 {path}")
