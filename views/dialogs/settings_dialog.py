# views/dialogs/settings_dialog.py
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLineEdit, QPushButton, QFileDialog, QSlider, QLabel,
    QComboBox, QWidget, QStackedWidget, QTreeWidget, QTreeWidgetItem,
    QFrame
)
from PyQt6.QtCore import Qt
import qtawesome as qta
from utils.settings import Settings, THEME_MODE_SYSTEM, THEME_MODE_LIGHT, THEME_MODE_DARK
from utils.toast import show_toast


class SettingsDialog(QDialog):
    """参照 PyCharm 风格的设置对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SettingsDialog")
        self.setWindowTitle("设置 - 虫师")
        self.setModal(True)
        self.resize(820, 560)
        self.setMinimumSize(720, 500)

        self.setup_ui()
        self.load_settings()
        self.apply_theme()

    # ------------------------------------------------------------------
    # UI 布局
    # ------------------------------------------------------------------
    def setup_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # ---------- 主体：左导航 + 右内容 ----------
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        # 左侧导航
        self.nav_tree = QTreeWidget()
        self.nav_tree.setObjectName("SettingsNav")
        self.nav_tree.setHeaderHidden(True)
        self.nav_tree.setFixedWidth(200)
        self.nav_tree.setIndentation(14)
        self.nav_tree.setRootIsDecorated(True)
        self.nav_tree.currentItemChanged.connect(self._on_nav_changed)

        # 右侧内容
        self.content_stack = QStackedWidget()
        self.content_stack.setObjectName("SettingsContent")

        idx = 0

        # ========== 分类 1：外观与行为 ==========
        appearance_root = QTreeWidgetItem(["外观与行为"])
        appearance_root.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self.nav_tree.addTopLevelItem(appearance_root)

        theme_item = QTreeWidgetItem(["主题"])
        theme_item.setData(0, Qt.ItemDataRole.UserRole, idx)
        appearance_root.addChild(theme_item)
        self.content_stack.addWidget(self._build_theme_page())
        idx += 1

        wallpaper_item = QTreeWidgetItem(["壁纸"])
        wallpaper_item.setData(0, Qt.ItemDataRole.UserRole, idx)
        appearance_root.addChild(wallpaper_item)
        self.content_stack.addWidget(self._build_wallpaper_page())
        idx += 1

        # ========== 分类 2：路径设置 ==========
        path_root = QTreeWidgetItem(["路径设置"])
        path_root.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self.nav_tree.addTopLevelItem(path_root)

        output_item = QTreeWidgetItem(["输出目录"])
        output_item.setData(0, Qt.ItemDataRole.UserRole, idx)
        path_root.addChild(output_item)
        self.content_stack.addWidget(self._build_output_page())
        idx += 1

        # ========== 分类 3：设备管理 ==========
        device_root = QTreeWidgetItem(["设备管理"])
        device_root.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self.nav_tree.addTopLevelItem(device_root)

        device_item = QTreeWidgetItem(["设备维护"])
        device_item.setData(0, Qt.ItemDataRole.UserRole, idx)
        device_root.addChild(device_item)
        self.content_stack.addWidget(self._build_device_page())
        idx += 1

        self.nav_tree.expandAll()

        body.addWidget(self.nav_tree)
        body.addWidget(self.content_stack, 1)
        root_layout.addLayout(body, 1)

        # ---------- 底部分隔线 ----------
        bottom_line = QFrame()
        bottom_line.setObjectName("SettingsBottomLine")
        bottom_line.setFrameShape(QFrame.Shape.HLine)
        bottom_line.setFixedHeight(1)
        root_layout.addWidget(bottom_line)

        # ---------- 底部按钮栏 ----------
        btn_bar = QHBoxLayout()
        btn_bar.setContentsMargins(24, 12, 24, 14)
        btn_bar.setSpacing(10)
        btn_bar.addStretch()

        self.apply_btn = QPushButton("应用")
        self.apply_btn.setObjectName("applyBtn")
        self.apply_btn.setFixedSize(90, 32)
        self.apply_btn.clicked.connect(self._on_apply)

        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setObjectName("cancelBtn")
        self.cancel_btn.setFixedSize(90, 32)
        self.cancel_btn.clicked.connect(self.reject)

        self.ok_btn = QPushButton("确定")
        self.ok_btn.setObjectName("okBtn")
        self.ok_btn.setFixedSize(90, 32)
        self.ok_btn.clicked.connect(self._on_ok)
        self.ok_btn.setDefault(True)

        btn_bar.addWidget(self.apply_btn)
        btn_bar.addWidget(self.cancel_btn)
        btn_bar.addWidget(self.ok_btn)
        root_layout.addLayout(btn_bar)

        # 默认选中"主题"
        if appearance_root.childCount() > 0:
            self.nav_tree.setCurrentItem(appearance_root.child(0))

    # ------------------------------------------------------------------
    # 主题页
    # ------------------------------------------------------------------
    def _build_theme_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)

        title = QLabel("主题")
        title.setObjectName("SettingsPageTitle")
        layout.addWidget(title)

        subtitle = QLabel("选择界面的主题样式，切换后立即生效")
        subtitle.setObjectName("SettingsPageSubtitle")
        layout.addWidget(subtitle)

        form = QFormLayout()
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["跟随系统", "亮色", "暗色"])
        self.theme_combo.setFixedWidth(220)
        form.addRow("主题:", self.theme_combo)

        layout.addLayout(form)
        layout.addStretch()
        return page

    # ------------------------------------------------------------------
    # 壁纸页
    # ------------------------------------------------------------------
    def _build_wallpaper_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)

        title = QLabel("壁纸")
        title.setObjectName("SettingsPageTitle")
        layout.addWidget(title)

        subtitle = QLabel("选择图片作为应用背景，支持自定义透明度")
        subtitle.setObjectName("SettingsPageSubtitle")
        layout.addWidget(subtitle)

        form = QFormLayout()
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        # 壁纸图片路径
        self.wallpaper_edit = QLineEdit()
        self.wallpaper_edit.setReadOnly(True)
        self.wallpaper_edit.setPlaceholderText("选择图片作为壁纸")

        browse_wallpaper_btn = QPushButton("浏览...")
        browse_wallpaper_btn.setObjectName("browseBtn")
        browse_wallpaper_btn.setFixedSize(80, 30)
        browse_wallpaper_btn.clicked.connect(self.browse_wallpaper)

        wallpaper_layout = QHBoxLayout()
        wallpaper_layout.setSpacing(8)
        wallpaper_layout.addWidget(self.wallpaper_edit)
        wallpaper_layout.addWidget(browse_wallpaper_btn)
        form.addRow("壁纸图片:", wallpaper_layout)

        # 透明度
        self.wallpaper_opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.wallpaper_opacity_slider.setRange(0, 100)
        self.wallpaper_opacity_slider.setValue(100)
        self.wallpaper_opacity_slider.setTickInterval(10)
        self.wallpaper_opacity_slider.setTickPosition(QSlider.TickPosition.TicksBelow)

        self.opacity_label = QLabel("100%")
        self.opacity_label.setFixedWidth(50)

        opacity_layout = QHBoxLayout()
        opacity_layout.setSpacing(8)
        opacity_layout.addWidget(self.wallpaper_opacity_slider, 1)
        opacity_layout.addWidget(self.opacity_label)
        form.addRow("透明度:", opacity_layout)

        # 移除壁纸
        remove_btn = QPushButton("移除壁纸")
        remove_btn.setObjectName("dangerBtn")
        remove_btn.setFixedSize(110, 30)
        remove_btn.clicked.connect(self.remove_wallpaper)
        remove_layout = QHBoxLayout()
        remove_layout.addWidget(remove_btn)
        remove_layout.addStretch()
        form.addRow("", remove_layout)

        layout.addLayout(form)
        layout.addStretch()

        self.wallpaper_opacity_slider.valueChanged.connect(
            lambda v: self.opacity_label.setText(f"{v}%")
        )
        return page

    # ------------------------------------------------------------------
    # 输出目录页
    # ------------------------------------------------------------------
    def _build_output_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)

        title = QLabel("输出目录")
        title.setObjectName("SettingsPageTitle")
        layout.addWidget(title)

        subtitle = QLabel("测试报告、截图等文件的默认保存位置")
        subtitle.setObjectName("SettingsPageSubtitle")
        layout.addWidget(subtitle)

        form = QFormLayout()
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.dir_edit = QLineEdit()
        self.dir_edit.setReadOnly(True)

        browse_btn = QPushButton("浏览...")
        browse_btn.setObjectName("browseBtn")
        browse_btn.setFixedSize(80, 30)
        browse_btn.clicked.connect(self.browse_output_dir)

        dir_layout = QHBoxLayout()
        dir_layout.setSpacing(8)
        dir_layout.addWidget(self.dir_edit)
        dir_layout.addWidget(browse_btn)
        form.addRow("输出目录:", dir_layout)

        layout.addLayout(form)
        layout.addStretch()
        return page


    def _build_device_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)

        title = QLabel("设备维护")
        title.setObjectName("SettingsPageTitle")
        layout.addWidget(title)

        subtitle = QLabel("修复设备连接相关的异常状态，操作前请确保设备已连接")
        subtitle.setObjectName("SettingsPageSubtitle")
        layout.addWidget(subtitle)

        # 恢复输入法
        row = QHBoxLayout()
        row.setSpacing(10)

        self.restore_ime_btn = QPushButton("恢复输入法")
        self.restore_ime_btn.setObjectName("restoreImeBtn")
        self.restore_ime_btn.setIcon(qta.icon('fa6s.keyboard', color='white'))
        self.restore_ime_btn.setFixedSize(140, 32)
        self.restore_ime_btn.clicked.connect(self._on_restore_ime)
        row.addWidget(self.restore_ime_btn)
        row.addStretch()
        layout.addLayout(row)

        hint = QLabel(
            "将设备输入法切换回系统默认 AOSP 键盘，"
            "解决输入法被篡改导致 uiautomator2 无法输入文字的问题。"
        )
        hint.setObjectName("SettingsPageSubtitle")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        layout.addStretch()
        return page

    def _on_restore_ime(self):
        main_win = self.parent()
        ds = getattr(main_win, 'device_service', None) if main_win else None
        if ds is None:
            show_toast(self, "设备服务未初始化", duration=2000)
            return
        try:
            ds.restore_ime()
            show_toast(self, "恢复输入法成功", duration=2000)
        except Exception as e:
            msg = str(e)
            if "设备未连接" in msg:
                show_toast(self, "设备未连接", duration=2000)
            else:
                show_toast(self, "恢复输入法失败", duration=2000)
    # ------------------------------------------------------------------
    # 导航切换
    # ------------------------------------------------------------------
    def _on_nav_changed(self, current, previous):
        if current is None:
            return
        idx = current.data(0, Qt.ItemDataRole.UserRole)
        if idx is not None and 0 <= idx < self.content_stack.count():
            self.content_stack.setCurrentIndex(idx)

    # ------------------------------------------------------------------
    # 数据加载 / 保存
    # ------------------------------------------------------------------
    def load_settings(self):
        self.dir_edit.setText(Settings.get_output_dir())
        self.wallpaper_edit.setText(Settings.get_wallpaper_path())
        opacity = Settings.get_wallpaper_opacity()
        self.wallpaper_opacity_slider.setValue(opacity)
        self.opacity_label.setText(f"{opacity}%")

        theme_mode = Settings.get_raw_theme_mode()
        if theme_mode == THEME_MODE_SYSTEM:
            self.theme_combo.setCurrentIndex(0)
        elif theme_mode == THEME_MODE_LIGHT:
            self.theme_combo.setCurrentIndex(1)
        elif theme_mode == THEME_MODE_DARK:
            self.theme_combo.setCurrentIndex(2)

    def _save_settings(self):
        Settings.set_output_dir(self.dir_edit.text())
        Settings.set_wallpaper_path(self.wallpaper_edit.text().strip())
        Settings.set_wallpaper_opacity(self.wallpaper_opacity_slider.value())

        theme_index = self.theme_combo.currentIndex()
        if theme_index == 0:
            Settings.set_theme_mode(THEME_MODE_SYSTEM)
        elif theme_index == 1:
            Settings.set_theme_mode(THEME_MODE_LIGHT)
        else:
            Settings.set_theme_mode(THEME_MODE_DARK)

    # ------------------------------------------------------------------
    # 按钮行为
    # ------------------------------------------------------------------
    def _on_apply(self):
        self._save_settings()
        # 通知父窗口刷新壁纸和主题（顺序：先壁纸后主题）
        p = self.parent()
        if p is not None:
            if hasattr(p, 'load_wallpaper'):
                try:
                    p.load_wallpaper()
                except Exception as e:
                    print(f"[SettingsDialog] load_wallpaper 失败: {e}")
            if hasattr(p, 'apply_theme'):
                try:
                    p.apply_theme()
                except Exception as e:
                    print(f"[SettingsDialog] apply_theme 失败: {e}")

    def _on_ok(self):
        self._save_settings()
        self.accept()

    # ------------------------------------------------------------------
    # 文件选择
    # ------------------------------------------------------------------
    def browse_output_dir(self):
        dir_path = QFileDialog.getExistingDirectory(
            self, "选择输出目录", self.dir_edit.text()
        )
        if dir_path:
            self.dir_edit.setText(dir_path)

    def browse_wallpaper(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择壁纸图片", "",
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.gif);;所有文件 (*.*)"
        )
        if file_path:
            self.wallpaper_edit.setText(file_path)

    def remove_wallpaper(self):
        self.wallpaper_edit.clear()
        self.wallpaper_opacity_slider.setValue(100)

    # ------------------------------------------------------------------
    # 主题适配
    # ------------------------------------------------------------------
    def apply_theme(self):
        theme_mode = Settings.get_theme_mode()
        is_dark = (theme_mode == THEME_MODE_DARK)

        if is_dark:
            content_bg = "#2b2d30"
            nav_bg = "#1e1f22"
            nav_border = "#393b40"
            nav_text = "#dddddd"
            nav_hover_bg = "rgba(255, 255, 255, 0.06)"
            nav_sel_bg = "#1e3a5f"
            nav_sel_text = "#ffffff"
            title_color = "#ffffff"
            sub_color = "#999999"
            input_bg = "#3c3c3c"
            input_border = "#555555"
            input_text = "#eeeeee"
            input_focus = "#90caf9"
            btn_primary_bg = "#1976d2"
            btn_primary_fg = "#ffffff"
            btn_primary_hover = "#1565c0"
            btn_secondary_bg = "#3c3c3c"
            btn_secondary_fg = "#eeeeee"
            btn_secondary_hover = "#4a4a4a"
            btn_secondary_border = "#555555"
            danger_bg = "#c0392b"
            danger_fg = "#ffffff"
            danger_hover = "#e74c3c"
            bottom_border = "#393b40"
        else:
            content_bg = "#ffffff"
            nav_bg = "#f7f8fa"
            nav_border = "#e0e0e0"
            nav_text = "#333333"
            nav_hover_bg = "rgba(0, 0, 0, 0.04)"
            nav_sel_bg = "#e8f0fe"
            nav_sel_text = "#1976d2"
            title_color = "#1a1a1a"
            sub_color = "#999999"
            input_bg = "#ffffff"
            input_border = "#d0d0d0"
            input_text = "#333333"
            input_focus = "#1976d2"
            btn_primary_bg = "#1976d2"
            btn_primary_fg = "#ffffff"
            btn_primary_hover = "#1565c0"
            btn_secondary_bg = "#f0f0f0"
            btn_secondary_fg = "#333333"
            btn_secondary_hover = "#e0e0e0"
            btn_secondary_border = "#d0d0d0"
            danger_bg = "#e74c3c"
            danger_fg = "#ffffff"
            danger_hover = "#c0392b"
            bottom_border = "#e0e0e0"

        self.setStyleSheet(f"""
            #SettingsDialog {{
                background-color: {content_bg};
            }}

            /* ---------- 左侧导航 ---------- */
            #SettingsNav {{
                background-color: {nav_bg};
                border: none;
                border-right: 1px solid {nav_border};
                outline: none;
                padding: 8px 0;
                font-size: 13px;
                /* 关键：让选中背景铺满整行（含 branch 缩进列） */
                show-decoration-selected: 1;
                /* 禁用 Qt 用 palette.Highlight 绘制，改用 QSS 控制 */
                selection-background-color: transparent;
                selection-color: transparent;
            }}
            #SettingsNav::item {{
                height: 28px;
                padding: 0 10px;
                border-radius: 4px;
                margin: 1px 6px;
                color: {nav_text};
            }}
            #SettingsNav::item:hover {{
                background: {nav_hover_bg};
            }}
            #SettingsNav::item:selected {{
                background-color: {nav_sel_bg};
                color: {nav_sel_text};
            }}
            #SettingsNav::branch {{
                background: transparent;
                border: none;
            }}
            #SettingsNav::branch:selected,
            #SettingsNav::branch:selected:active,
            #SettingsNav::branch:selected:!active {{
                background: transparent;
                border: none;
            }}
            #SettingsNav::branch:hover {{
                background: transparent;
            }}

            /* ---------- 右侧内容 ---------- */
            #SettingsContent {{
                background-color: {content_bg};
            }}
            #SettingsPageTitle {{
                color: {title_color};
                font-size: 18px;
                font-weight: bold;
                background: transparent;
            }}
            #SettingsPageSubtitle {{
                color: {sub_color};
                font-size: 12px;
                background: transparent;
            }}

            /* ---------- 表单控件 ---------- */
            #SettingsDialog QLabel {{
                color: {input_text};
                background: transparent;
                font-size: 13px;
            }}
            #SettingsDialog QLineEdit {{
                background-color: {input_bg};
                color: {input_text};
                border: 1px solid {input_border};
                border-radius: 4px;
                padding: 5px 10px;
                font-size: 13px;
                min-height: 20px;
            }}
            #SettingsDialog QLineEdit:focus {{
                border-color: {input_focus};
            }}
            #SettingsDialog QComboBox {{
                background-color: {input_bg};
                color: {input_text};
                border: 1px solid {input_border};
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 13px;
                min-height: 22px;
            }}
            #SettingsDialog QComboBox:focus {{
                border-color: {input_focus};
            }}
            #SettingsDialog QComboBox QAbstractItemView {{
                background-color: {input_bg};
                color: {input_text};
                border: 1px solid {input_border};
                border-radius: 6px;
                selection-background-color: {btn_primary_bg};
                selection-color: {btn_primary_fg};
                outline: none;
                padding: 4px;
            }}
            #SettingsDialog QComboBox QAbstractItemView::item {{
                background-color: {input_bg};
                color: {input_text};
                min-height: 24px;
                padding: 4px 10px;
                margin: 1px 2px;
                border-radius: 4px;
            }}
            #SettingsDialog QComboBox QAbstractItemView::item:hover {{
                background-color: {nav_sel_bg};
                color: {nav_sel_text};
            }}
            #SettingsDialog QComboBox QAbstractItemView::item:selected {{
                background-color: {btn_primary_bg};
                color: {btn_primary_fg};
            }}

            /* ---------- 滑条 ---------- */
            #SettingsDialog QSlider::groove:horizontal {{
                background: {input_border};
                height: 6px;
                border-radius: 3px;
            }}
            #SettingsDialog QSlider::handle:horizontal {{
                background: {btn_primary_bg};
                width: 14px;
                margin: -5px 0;
                border-radius: 7px;
            }}
            #SettingsDialog QSlider::sub-page:horizontal {{
                background: {btn_primary_bg};
                border-radius: 3px;
            }}

            /* ---------- 次要按钮（浏览、移除壁纸等） ---------- */
            #SettingsDialog QPushButton {{
                background-color: {btn_secondary_bg};
                color: {btn_secondary_fg};
                border: 1px solid {btn_secondary_border};
                border-radius: 4px;
                padding: 5px 12px;
                font-size: 13px;
            }}
            #SettingsDialog QPushButton:hover {{
                background-color: {btn_secondary_hover};
            }}

            /* ---------- 设备维护：恢复输入法 ---------- */
            #SettingsDialog QPushButton#restoreImeBtn {{
                background-color: #3498db;
                color: white;
                border: 1px solid #3498db;
                font-weight: 500;
            }}
            #SettingsDialog QPushButton#restoreImeBtn:hover {{
                background-color: #5dade2;
                border-color: #5dade2;
            }}

            /* ---------- 危险操作按钮（移除壁纸） ---------- */
            #SettingsDialog QPushButton#dangerBtn {{
                background-color: {danger_bg};
                color: {danger_fg};
                border: 1px solid {danger_bg};
            }}
            #SettingsDialog QPushButton#dangerBtn:hover {{
                background-color: {danger_hover};
                border-color: {danger_hover};
            }}

            /* ---------- 主按钮（确定、应用） ---------- */
            #SettingsDialog QPushButton#okBtn,
            #SettingsDialog QPushButton#applyBtn {{
                background-color: {btn_primary_bg};
                color: {btn_primary_fg};
                border: 1px solid {btn_primary_bg};
                font-weight: 500;
            }}
            #SettingsDialog QPushButton#okBtn:hover,
            #SettingsDialog QPushButton#applyBtn:hover {{
                background-color: {btn_primary_hover};
                border-color: {btn_primary_hover};
            }}

            /* ---------- 底部分隔线 ---------- */
            #SettingsBottomLine {{
                background-color: {bottom_border};
                border: none;
            }}
        """)
        # 从 palette 层把 Highlight 设为透明，彻底干掉左侧 branch 区域的蓝色方块
        try:
            from PyQt6.QtGui import QPalette, QColor
            pal = self.nav_tree.palette()
            pal.setColor(QPalette.ColorRole.Highlight, QColor(0, 0, 0, 0))
            pal.setColor(QPalette.ColorRole.HighlightedText, QColor(0, 0, 0, 0))
            self.nav_tree.setPalette(pal)
            vp = self.nav_tree.viewport()
            if vp is not None:
                vp.setPalette(pal)
        except Exception as e:
            print(f"[SettingsDialog] 导航 palette 设置失败: {e}")