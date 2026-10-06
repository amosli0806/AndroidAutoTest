# views/dialogs/settings_dialog.py
import os
import sys

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGridLayout,
    QLineEdit, QPushButton, QFileDialog, QSlider, QLabel,
    QComboBox, QWidget, QStackedWidget, QTreeWidget, QTreeWidgetItem,
    QFrame, QScrollArea, QKeySequenceEdit, QCheckBox, QToolButton
)
from PyQt6.QtCore import Qt, QObject, QThread, pyqtSignal, QSize
from PyQt6.QtGui import QKeySequence, QIcon, QPixmap, QImageReader

from utils import tree_state


# 快捷键 key -> 中文名映射
_SHORTCUT_LABELS = {
    "help_center": "帮助中心",
    "open_settings": "打开设置",
    "refresh_devices": "刷新设备列表",
    "toggle_log_panel": "显示/隐藏日志面板",
    "restore_ime": "恢复设备输入法",
    "toggle_record": "开始/停止录制",
    "generate_steps": "生成步骤",
    "focus_step_search": "聚焦步骤搜索",
    "import_cases": "导入用例",
    "export_cases": "导出用例",
    "execute_cases": "执行选中用例",
    "toggle_select_all": "全选/取消全选",
    "save_suite": "保存套件",
    "delete_suite": "删除套件",
    "generate_report": "生成测试报告",
    "adb_search": "指令搜索",
    "adb_add_command": "新增指令",
    "adb_edit_command": "编辑指令",
    "adb_delete_command": "删除指令",
    "adb_import_commands": "导入指令",
    "adb_export_commands": "导出指令",
    "adb_execute_selected": "执行选中指令",
    "adb_wireless": "无线联调",
    "adb_scrcpy": "投屏",
    "adb_install": "安装 APK",
    "adb_push": "推送文件",
    "adb_device_info": "硬件信息",
    "adb_hprof": "堆转储",
    "adb_monkey": "Monkey 测试",
    "adb_crash": "Crash 日志",
    "adb_anr": "ANR 日志",
    "adb_md5": "MD5 查询",
    "adb_weak_network": "弱网模拟",
    "adb_packet": "网络抓包",
    "perf_toggle_monitor": "开始/停止监控",
    "perf_toggle_pause": "暂停/继续",
    "perf_export_csv": "导出 CSV",
    "perf_save_baseline": "保存基线",
    "elem_add": "新增元素",
    "elem_edit": "编辑元素",
    "elem_delete": "删除元素",
    "elem_verify": "验证元素",
}
import qtawesome as qta
from utils.settings import Settings, THEME_MODE_SYSTEM, THEME_MODE_LIGHT, THEME_MODE_DARK
from utils.toast import show_toast


# 试听念什么见 _on_test_voice：**当前唤醒词本身**。
# 原来这里写死一句跟唤醒词无关的样例文案，改了唤醒词再点试听，听到的还是老句子。


class WallpaperThumbLoader(QThread):
    """后台加载预设壁纸缩略图，避免解码高清大图卡住设置对话框"""
    thumbs_ready = pyqtSignal(list)  # [(path, QImage), ...]

    def __init__(self, paths, tw, th, parent=None):
        super().__init__(parent)
        self._paths = paths
        self._tw = tw
        self._th = th

    def run(self):
        thumbs = []
        try:
            for path in self._paths:
                img = self._make_thumb(path)
                if img is not None:
                    thumbs.append((path, img))
        except Exception as e:
            print(f"[WallpaperThumbLoader] 缩略图加载失败: {e}")
        self.thumbs_ready.emit(thumbs)

    def _make_thumb(self, path):
        tw, th = self._tw, self._th
        reader = QImageReader(path)
        reader.setAutoTransform(True)
        orig = reader.size()
        # 解码时直接缩到覆盖尺寸（JPEG 硬解缩放，避免解全图）
        if orig.isValid() and orig.width() > 0 and orig.height() > 0:
            s = max(tw / orig.width(), th / orig.height())
            if s < 1.0:
                reader.setScaledSize(QSize(
                    max(tw, int(round(orig.width() * s))),
                    max(th, int(round(orig.height() * s))),
                ))
        img = reader.read()
        if img.isNull():
            return None
        # 覆盖式缩放到恰好覆盖 tw×th（小图会放大），再居中裁剪
        filled = img.scaled(
            tw, th,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        x = (filled.width() - tw) // 2
        y = (filled.height() - th) // 2
        return filled.copy(x, y, tw, th)


class VoiceTestWorker(QThread):
    """后台试听一句，避免 SAPI 阻塞设置对话框"""
    done = pyqtSignal(bool, str)

    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.text = text

    def run(self):
        try:
            from services.voice_service import get_voice_service
            get_voice_service().speak(self.text)
            self.done.emit(True, "")
        except Exception as e:
            self.done.emit(False, str(e))


class AIConnectTestWorker(QThread):
    """后台跑「测试连接」，避免网络请求卡住设置对话框"""
    done = pyqtSignal(bool, str)

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg

    def run(self):
        try:
            from services.ai_service import AIService
            ok, msg = AIService().test_connection(self.cfg)
        except Exception as e:
            ok, msg = False, f"测试失败：{str(e)[:80]}"
        self.done.emit(ok, msg)


class SettingsDialog(QDialog):
    """参照 PyCharm 风格的设置对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SettingsDialog")
        self.setWindowTitle("设置 - 虫师")
        self.setModal(True)
        self.resize(820, 560)
        self.setMinimumSize(720, 500)
        # AI「测试连接」的后台线程引用（不跑时为 None）
        self._ai_test_worker = None
        # 语音试听的后台线程引用
        self._voice_test_worker = None

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
        # 16 而不是 14：分支列是箭头的位置，宽一点箭头才不至于贴着左边框。
        # 配合 QSS 里 item 的 padding 一起调，具体数值见那两条规则的注释。
        self.nav_tree.setIndentation(16)
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
        self._wallpaper_page_idx = idx
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

        # ========== 分类 3：快捷键 ==========
        shortcut_root = QTreeWidgetItem(["快捷键"])
        shortcut_root.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self.nav_tree.addTopLevelItem(shortcut_root)

        shortcut_item = QTreeWidgetItem(["快捷键设置"])
        shortcut_item.setData(0, Qt.ItemDataRole.UserRole, idx)
        shortcut_root.addChild(shortcut_item)
        self.content_stack.addWidget(self._build_shortcuts_page())
        idx += 1

        # ========== 分类：AI 辅助 ==========
        ai_root = QTreeWidgetItem(["AI 辅助"])
        ai_root.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self.nav_tree.addTopLevelItem(ai_root)

        ai_item = QTreeWidgetItem(["AI 配置"])
        ai_item.setData(0, Qt.ItemDataRole.UserRole, idx)
        ai_root.addChild(ai_item)
        self.content_stack.addWidget(self._build_ai_page())
        idx += 1

        # ========== 分类：语音播报 ==========
        voice_root = QTreeWidgetItem(["语音播报"])
        voice_root.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self.nav_tree.addTopLevelItem(voice_root)

        voice_item = QTreeWidgetItem(["语音设置"])
        voice_item.setData(0, Qt.ItemDataRole.UserRole, idx)
        voice_root.addChild(voice_item)
        self.content_stack.addWidget(self._build_voice_page())
        idx += 1

        voice_verify_item = QTreeWidgetItem(["回执验证"])
        voice_verify_item.setData(0, Qt.ItemDataRole.UserRole, idx)
        voice_root.addChild(voice_verify_item)
        self.content_stack.addWidget(self._build_voice_verify_page())
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

        # ========== 分类：数据维护 ==========
        data_root = QTreeWidgetItem(["数据维护"])
        data_root.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self.nav_tree.addTopLevelItem(data_root)

        data_item = QTreeWidgetItem(["无用数据清理"])
        data_item.setData(0, Qt.ItemDataRole.UserRole, idx)
        data_root.addChild(data_item)
        self.content_stack.addWidget(self._build_data_page())
        idx += 1

        # ========== 分类：通知 ==========
        notify_root = QTreeWidgetItem(["通知"])
        notify_root.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self.nav_tree.addTopLevelItem(notify_root)

        notify_item = QTreeWidgetItem(["系统托盘"])
        notify_item.setData(0, Qt.ItemDataRole.UserRole, idx)
        notify_root.addChild(notify_item)
        self.content_stack.addWidget(self._build_notify_page())
        idx += 1


        # 展开状态持久化：默认全折叠，记住用户上次展开的分类（存 data/config.json）。
        # 导航项没有业务 id（只有子项带内容下标），用"从根到自己的文字路径"当 id
        self._nav_state = tree_state.bind_tree(
            self.nav_tree, "settings_nav", id_of=tree_state.item_text_path_id)
        self._nav_state.restore()

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

        browse_wallpaper_btn = QPushButton("浏览")
        browse_wallpaper_btn.setObjectName("browseBtn")
        browse_wallpaper_btn.setFixedSize(80, 30)
        browse_wallpaper_btn.clicked.connect(self.browse_wallpaper)

        wallpaper_layout = QHBoxLayout()
        wallpaper_layout.setSpacing(8)
        wallpaper_layout.addWidget(self.wallpaper_edit)
        wallpaper_layout.addWidget(browse_wallpaper_btn)
        form.addRow("壁纸图片:", wallpaper_layout)

        # 透明度 + 移除按钮（移除放在滑动条右侧）
        self.wallpaper_opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.wallpaper_opacity_slider.setRange(0, 100)
        self.wallpaper_opacity_slider.setValue(100)
        self.wallpaper_opacity_slider.setTickInterval(10)
        self.wallpaper_opacity_slider.setTickPosition(QSlider.TickPosition.TicksBelow)

        self.opacity_label = QLabel("100%")
        self.opacity_label.setFixedWidth(50)

        remove_btn = QPushButton("移除")
        remove_btn.setObjectName("dangerBtn")
        remove_btn.setFixedSize(80, 30)  # 与「浏览」按钮同宽同高
        remove_btn.clicked.connect(self.remove_wallpaper)

        opacity_layout = QHBoxLayout()
        opacity_layout.setSpacing(8)
        opacity_layout.addWidget(self.wallpaper_opacity_slider, 1)
        opacity_layout.addWidget(self.opacity_label)
        opacity_layout.addWidget(remove_btn)
        form.addRow("透明度:", opacity_layout)

        layout.addLayout(form)

        # 预设壁纸缩略图（三列网格，单选）
        preset_label = QLabel("预设壁纸")
        preset_label.setObjectName("SettingsPageSubtitle")
        layout.addWidget(preset_label)

        layout.addWidget(self._build_preset_wallpaper_grid(), 1)

        self.wallpaper_opacity_slider.valueChanged.connect(
            lambda v: self.opacity_label.setText(f"{v}%")
        )
        return page

    # 预设壁纸目录：图片放这里就会被自动扫出来（按文件名排序）
    # 开发环境在项目根 resources/ 下；打包后在 _internal/resources/ 下（整包随 spec datas 带上）
    PRESET_WALLPAPER_DIR_NAME = os.path.join("resources", "images", "wallpapers")
    # 缩略图统一裁成 16:9（144×81），保证每张宽高一致
    # 尺寸取小一点：3 列 + 间距要装进面板宽度（还要给纵向滚动条留位置），避免底部出现横向滚动条
    PRESET_THUMB_W = 144
    PRESET_THUMB_H = 81

    def _preset_wallpaper_dir(self):
        if getattr(sys, "frozen", False):
            # 打包后资源在 _internal/resources/ 下
            base = sys._MEIPASS
        else:
            # 开发环境：本文件在 views/dialogs/ 下，回溯两级到项目根
            base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        return os.path.join(base, self.PRESET_WALLPAPER_DIR_NAME)

    def _list_preset_wallpapers(self):
        """扫描预设壁纸目录，返回按文件名排序的绝对路径列表"""
        d = self._preset_wallpaper_dir()
        if not os.path.isdir(d):
            return []
        exts = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp")
        files = [f for f in os.listdir(d) if f.lower().endswith(exts)]
        return [os.path.join(d, f) for f in sorted(files, key=str.lower)]

    def _build_preset_wallpaper_grid(self):
        """三列缩略图网格容器：缩略图改为后台懒加载（_ensure_wallpaper_thumbs），
        打开设置对话框时不再同步解码 25 张高清大图，避免卡顿"""
        container = QWidget()
        container.setObjectName("WallpaperGridContainer")
        self._wallpaper_grid = QGridLayout(container)
        self._wallpaper_grid.setContentsMargins(0, 4, 4, 4)
        self._wallpaper_grid.setHorizontalSpacing(10)
        self._wallpaper_grid.setVerticalSpacing(14)

        self._preset_buttons = {}  # path -> QToolButton（后台加载完成后填充）
        self._thumb_loader = None
        self._thumbs_loaded = False

        col_count = 3
        for c in range(col_count):
            self._wallpaper_grid.setColumnStretch(c, 1)
        self._wallpaper_grid.setRowStretch(0, 1)

        scroll = QScrollArea()
        scroll.setObjectName("WallpaperScroll")
        scroll.setWidget(container)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        # 只保留上下滚动；列宽已按面板宽度算好，横向滚动条一律不出现
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        return scroll

    def _ensure_wallpaper_thumbs(self):
        """启动后台线程加载缩略图（只做一次，失败不重试避免反复开销）"""
        if self._thumbs_loaded or self._thumb_loader is not None:
            return
        paths = self._list_preset_wallpapers()
        if not paths:
            self._thumbs_loaded = True
            return
        loader = WallpaperThumbLoader(
            paths, self.PRESET_THUMB_W, self.PRESET_THUMB_H, self)
        loader.thumbs_ready.connect(self._on_thumbs_ready)
        self._thumb_loader = loader
        loader.start()

    def _on_thumbs_ready(self, thumbs):
        """后台缩略图就绪：填充网格按钮并恢复当前壁纸的选中态"""
        self._thumb_loader = None
        self._thumbs_loaded = True
        tw, th = self.PRESET_THUMB_W, self.PRESET_THUMB_H
        col_count = 3
        for i, (path, img) in enumerate(thumbs):
            btn = QToolButton()
            btn.setObjectName("wallpaperThumbBtn")
            btn.setCheckable(True)
            btn.setAutoExclusive(True)
            btn.setFixedSize(tw + 16, th + 44)
            btn.setIconSize(QSize(tw, th))
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            name = os.path.splitext(os.path.basename(path))[0]
            btn.setText(name)
            btn.setIcon(QIcon(QPixmap.fromImage(img)))
            btn.setToolTip("选择后点击「应用」或「确定」生效")
            btn.clicked.connect(lambda checked, p=path: self._on_preset_clicked(checked, p))
            self._preset_buttons[path] = btn
            row, col = i // col_count, i % col_count
            # 三列均分剩余宽度，按钮在各自单元格里居中 → 最右列贴住滚动条，不留大空隙
            self._wallpaper_grid.addWidget(
                btn, row, col,
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        # 最后一行撑开，按钮整体靠上
        rows = (len(thumbs) + col_count - 1) // col_count
        self._wallpaper_grid.setRowStretch(rows, 1)
        # 若已保存的壁纸是预设图，恢复选中态
        wp = Settings.get_wallpaper_path()
        if wp:
            self._select_preset_by_path(wp)

    def _on_preset_clicked(self, checked, path):
        if checked:
            # 选中缩略图 = 预填路径，走既有的「应用/确定」保存链路
            self.wallpaper_edit.setText(path)
        else:
            # 再点一次取消单选（autoExclusive 只在换选时取消，这里处理点自己）
            if self.wallpaper_edit.text().strip() == path:
                self.wallpaper_edit.clear()

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
        self.dir_edit.setMinimumHeight(32)

        browse_btn = QPushButton("浏览")
        browse_btn.setObjectName("browseBtn")
        browse_btn.setFixedSize(90, 32)
        browse_btn.clicked.connect(self.browse_output_dir)

        dir_layout = QHBoxLayout()
        dir_layout.setSpacing(10)
        dir_layout.addWidget(self.dir_edit, 1)
        dir_layout.addWidget(browse_btn, 0)
        form.addRow("输出目录:", dir_layout)

        layout.addLayout(form)
        layout.addStretch()
        return page

    def _build_ai_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)

        title = QLabel("AI 辅助")
        title.setObjectName("SettingsPageTitle")
        layout.addWidget(title)

        subtitle = QLabel(
            "开启后，失败日志面板的「AI 分析失败」按钮会亮起，"
            "由 AI 结合步骤、错误信息、截图给出失败归因"
        )
        subtitle.setObjectName("SettingsPageSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        form = QFormLayout()
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.ai_enable_check = QCheckBox("启用 AI 辅助")
        form.addRow("", self.ai_enable_check)

        self.ai_base_edit = QLineEdit()
        self.ai_base_edit.setPlaceholderText("https://api.openai.com/v1")
        form.addRow("API 端点:", self.ai_base_edit)

        self.ai_key_edit = QLineEdit()
        self.ai_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.ai_key_edit.setPlaceholderText("sk-...")
        form.addRow("API Key:", self.ai_key_edit)

        self.ai_model_edit = QLineEdit()
        self.ai_model_edit.setPlaceholderText("gpt-4o-mini")
        form.addRow("模型:", self.ai_model_edit)

        # 测试连接：用当前填写（不必先保存）的配置发一次最小请求
        test_row = QHBoxLayout()
        test_row.setSpacing(10)
        self.ai_test_btn = QPushButton("测试连接")
        self.ai_test_btn.setObjectName("aiTestBtn")
        self.ai_test_btn.setFixedHeight(30)
        self.ai_test_btn.clicked.connect(self._on_test_ai_connection)
        test_row.addWidget(self.ai_test_btn)

        self.ai_test_result = QLabel("")
        self.ai_test_result.setObjectName("SettingsPageSubtitle")
        self.ai_test_result.setWordWrap(True)
        test_row.addWidget(self.ai_test_result, 1)
        form.addRow("", test_row)

        layout.addLayout(form)

        hint = QLabel(
            "兼容 OpenAI 协议的服务均可使用（如 DeepSeek、通义、本地 Ollama）。"
            "API Key 只保存在本机 config.json，不会上传。"
        )
        hint.setObjectName("SettingsPageSubtitle")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        layout.addStretch()
        return page

    # ------------------------------------------------------------------
    # 语音播报设置
    # ------------------------------------------------------------------
    def _build_voice_page(self):
        from models.voice_model import DEFAULT_WAKE_WORD

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)

        title = QLabel("语音播报")
        title.setObjectName("SettingsPageTitle")
        layout.addWidget(title)

        subtitle = QLabel(
            "「语音播报」页把文案用电脑扬声器读出来，车机麦克风拾取后交给它自己的语音助手。"
            "这里选音色和声音从哪个扬声器出去 —— 选错设备车机就完全听不见。"
        )
        subtitle.setObjectName("SettingsPageSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        form = QFormLayout()
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.voice_engine_combo = QComboBox()
        self.voice_engine_combo.setMinimumWidth(340)
        self.voice_engine_combo.currentIndexChanged.connect(
            self._on_voice_engine_changed)
        form.addRow("引擎:", self.voice_engine_combo)

        self.voice_tone_combo = QComboBox()
        self.voice_tone_combo.setMinimumWidth(340)
        form.addRow("音色:", self.voice_tone_combo)

        self.voice_device_combo = QComboBox()
        self.voice_device_combo.setMinimumWidth(340)
        form.addRow("输出设备:", self.voice_device_combo)

        # 唤醒词：语音页「唤醒词」按钮追加到用例末尾的那句。
        # 车机助手得先被叫醒才听得进后面的指令，所以这句往往是每条用例的第一句。
        self.wake_word_edit = QLineEdit()
        self.wake_word_edit.setMinimumWidth(340)
        self.wake_word_edit.setPlaceholderText("例如：你好虫师")
        form.addRow("唤醒词:", self.wake_word_edit)

        layout.addLayout(form)

        # 试听：用当前选中的音色与输出设备念一句，确认车机那边真能听见
        test_row = QHBoxLayout()
        test_row.setSpacing(10)
        self.voice_test_btn = QPushButton("试听")
        self.voice_test_btn.setObjectName("aiTestBtn")
        self.voice_test_btn.setFixedHeight(30)
        self.voice_test_btn.clicked.connect(self._on_test_voice)
        test_row.addWidget(self.voice_test_btn)

        self.voice_test_result = QLabel("")
        self.voice_test_result.setObjectName("SettingsPageSubtitle")
        self.voice_test_result.setWordWrap(True)
        test_row.addWidget(self.voice_test_result, 1)
        layout.addLayout(test_row)

        hint = QLabel(
            "音色和输出设备由「语音播报」页与用例里的语音步骤共用，保存后立即生效。"
            "响度直接用电脑的系统音量，程序内不再单独调音量。"
            "唤醒词是「语音播报」页「唤醒词」按钮追加到用例末尾的那句，留空则用默认的"
            f"「{DEFAULT_WAKE_WORD}」。\n"
            "引擎可选「Windows 内置语音」（离线，音色来自系统）或「Edge 在线语音」"
            "（需联网，微软在线音色，普通话更自然）。系统音色偏少时，可到 "
            "Windows 设置 → 时间和语言 → 语音 → 添加语音，装更多语言包。"
        )
        hint.setObjectName("SettingsPageSubtitle")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        layout.addStretch()
        return page

    def _build_voice_verify_page(self):
        """回执验证独立一页：播报后抓车机日志反馈文案，判定这句是否被正确识别。"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)

        title = QLabel("语音回执验证")
        title.setObjectName("SettingsPageTitle")
        layout.addWidget(title)

        subtitle = QLabel(
            "播报后从车机日志里抓反馈文案，判定这句语音是否被正确识别。"
            "判定失败时该步骤会标红，并附上车机反馈的原文，方便回看是哪句没被听懂。"
        )
        subtitle.setObjectName("SettingsPageSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        form = QFormLayout()
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.verify_enabled_check = QCheckBox("启用回执验证")
        form.addRow("开关:", self.verify_enabled_check)

        self.verify_tag_edit = QLineEdit()
        self.verify_tag_edit.setMinimumWidth(340)
        self.verify_tag_edit.setPlaceholderText(
            "可填多个，空格或逗号分隔；留空抓全量日志")
        form.addRow("日志标签:", self.verify_tag_edit)

        self.verify_fail_edit = QLineEdit()
        self.verify_fail_edit.setMinimumWidth(340)
        self.verify_fail_edit.setPlaceholderText("逗号分隔，如：没听清,无法识别,抱歉")
        form.addRow("失败关键词:", self.verify_fail_edit)

        layout.addLayout(form)

        hint = QLabel(
            "日志标签填车机语音助手打日志用的 tag，可填多个、用空格或逗号分隔"
            "（如：TtsBusinessManager TestManager）。留空抓全量日志：量大，且别的应用"
            "打出的日志可能碰巧命中关键词，建议至少填一个。\n"
            "抓取规则因车型而异，建议先不开启；等拿到车机、按它的日志格式填好标签和关键词后再打开。"
            "都填好但一句反馈都没抓到时，也会当作判定失败，避免没验证到却按通过算。\n"
            "这个开关只是总闸，只对「配了检测步骤的播报」生效 —— 判定依据是预期结果关键词，"
            "在「语音播报页」选中用例后点「+ 添加检测」按用例配置；"
            "没配检测步骤的播报一律不验证。这里只保留很少变的失败关键词。"
        )
        hint.setObjectName("SettingsPageSubtitle")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        layout.addStretch()
        return page

    def _load_voice_config(self):
        """把引擎/音色/输出设备选项灌进下拉框，并回填已保存的选择"""
        from models.voice_model import DEFAULT_WAKE_WORD, VoiceModel
        from services.voice_service import VoiceError, get_voice_service

        service = get_voice_service()
        settings = VoiceModel().settings

        self.wake_word_edit.setText(
            (settings.get("wake_word") or "").strip() or DEFAULT_WAKE_WORD)

        # 语音回执验证规则
        verify = settings.get("verify") or {}
        self.verify_enabled_check.setChecked(bool(verify.get("enabled", False)))
        self.verify_tag_edit.setText(str(verify.get("log_tag") or ""))
        self.verify_fail_edit.setText("，".join(verify.get("fail_keywords") or []))

        # 引擎下拉（blockSignals 避免 setCurrentIndex 触发 _on_voice_engine_changed 重复刷新）
        self.voice_engine_combo.blockSignals(True)
        self.voice_engine_combo.clear()
        for e in service.available_engines():
            label = e["label"] + ("" if e["available"] else "（不可用）")
            self.voice_engine_combo.addItem(label, e["key"])
        idx = self.voice_engine_combo.findData(settings.get("engine") or "")
        if idx >= 0:
            self.voice_engine_combo.setCurrentIndex(idx)
        self.voice_engine_combo.blockSignals(False)

        # 按当前引擎填音色
        self._load_voice_tones()

        self.voice_device_combo.clear()
        self.voice_device_combo.addItem("系统默认输出设备", "")
        if service.is_available():
            try:
                for item in service.list_output_devices():
                    self.voice_device_combo.addItem(item["label"], item["id"])
            except VoiceError:
                pass
        idx = self.voice_device_combo.findData(settings.get("device_id") or "")
        if idx >= 0:
            self.voice_device_combo.setCurrentIndex(idx)

    def _load_voice_tones(self):
        """按当前引擎 key 切引擎，并灌该引擎的音色列表。"""
        from models.voice_model import VoiceModel
        from services.voice_service import VoiceError, get_voice_service

        service = get_voice_service()
        engine_key = self.voice_engine_combo.currentData() or ""
        if engine_key:
            service.set_engine(engine_key)

        self.voice_tone_combo.clear()
        if service.is_available():
            try:
                for item in service.list_voices():
                    self.voice_tone_combo.addItem(item["label"], item["id"])
            except VoiceError:
                pass
        if self.voice_tone_combo.count() == 0:
            self.voice_tone_combo.addItem("（本机没有可用音色）", "")
        # 音色 id 是分引擎的：切引擎后旧 id 找不到，自然停在第一个（需重选）
        saved_voice = VoiceModel().settings.get("voice_id") or ""
        idx = self.voice_tone_combo.findData(saved_voice)
        if idx >= 0:
            self.voice_tone_combo.setCurrentIndex(idx)

    def _on_voice_engine_changed(self, _index):
        """用户切换引擎时刷新音色下拉。"""
        self._load_voice_tones()

    def _apply_voice_selection(self):
        """把下拉里**当前**的选择套到引擎上，但不落盘。

        试听要听到的是刚选的音色/设备，所以得先套上去；而试听不该顺手
        把配置写进文件 —— 用户可能试完点「取消」。
        """
        from services.voice_service import VoiceError, get_voice_service

        service = get_voice_service()
        if not service.is_available():
            return
        try:
            engine_key = self.voice_engine_combo.currentData() or ""
            if engine_key:
                service.set_engine(engine_key)
            service.set_voice(self.voice_tone_combo.currentData() or "")
            service.set_output_device(self.voice_device_combo.currentData() or "")
        except VoiceError as e:
            self.voice_test_result.setText(str(e))

    def _save_voice_config(self):
        """写回语音配置并立即套到引擎上：试听和用例里的语音步骤都用这套"""
        from models.voice_model import VoiceModel

        if not hasattr(self, "voice_tone_combo"):
            return
        vm = VoiceModel()
        verify = vm.settings.get("verify") or {}
        vm.set_settings(
            engine=self.voice_engine_combo.currentData() or "",
            voice_id=self.voice_tone_combo.currentData() or "",
            device_id=self.voice_device_combo.currentData() or "",
            # 留空也存空串：语音页取空值时回落到 DEFAULT_WAKE_WORD
            wake_word=(self.wake_word_edit.text() or "").strip(),
            verify={
                "enabled": self.verify_enabled_check.isChecked(),
                "log_tag": (self.verify_tag_edit.text() or "").strip(),
                # 成功关键词已改到「语音播报页 → 反馈检测」按用例配置，
                # 这里原样保留旧值（兜底），不再在设置页编辑
                "success_keywords": verify.get("success_keywords") or [],
                "fail_keywords": self._split_keywords(self.verify_fail_edit.text()),
            },
        )
        self._apply_voice_selection()

    @staticmethod
    def _split_keywords(text):
        """把逗号（中英文都认）分隔的关键词串拆成去空列表。"""
        import re
        return [w.strip() for w in re.split(r"[,，]", text or "") if w.strip()]

    def _on_test_voice(self):
        if self._voice_test_worker is not None and self._voice_test_worker.isRunning():
            return
        self._apply_voice_selection()      # 先套上当前选择，否则听到的是旧的
        self.voice_test_btn.setEnabled(False)
        self.voice_test_btn.setText("试听中…")

        # 试听念的就是**唤醒词本身**：用户要确认的是"车机听不听得见这句话"，
        # 而用例里第一句正是它。留空时按同一套规则回落到默认唤醒词。
        from models.voice_model import DEFAULT_WAKE_WORD
        wake_word = (self.wake_word_edit.text() or "").strip() or DEFAULT_WAKE_WORD
        self.voice_test_result.setText(
            f"正在用当前音色与输出设备试听「{wake_word}」…")

        self._voice_test_worker = VoiceTestWorker(wake_word, self)
        self._voice_test_worker.done.connect(self._on_test_voice_done)
        self._voice_test_worker.start()

    def _on_test_voice_done(self, ok: bool, msg: str):
        self.voice_test_btn.setEnabled(True)
        self.voice_test_btn.setText("试听")
        self.voice_test_result.setText("" if ok else f"试听失败：{msg}")

    # ------------------------------------------------------------------
    # AI 连接自测
    # ------------------------------------------------------------------
    def _collect_ai_config(self) -> dict:
        """取界面上当前填的值（不要求先保存），供「测试连接」使用"""
        return {
            "enabled": self.ai_enable_check.isChecked(),
            "base_url": self.ai_base_edit.text().strip() or "https://api.openai.com/v1",
            "api_key": self.ai_key_edit.text().strip(),
            "model": self.ai_model_edit.text().strip() or "gpt-4o-mini",
            "timeout": Settings.get_ai_config().get("timeout", 30),
        }

    def _on_test_ai_connection(self):
        if self._ai_test_worker is not None and self._ai_test_worker.isRunning():
            return
        self.ai_test_btn.setEnabled(False)
        self.ai_test_btn.setText("测试中…")
        self.ai_test_result.setStyleSheet("color: #888;")
        self.ai_test_result.setText("正在连接…")

        self._ai_test_worker = AIConnectTestWorker(self._collect_ai_config(), self)
        self._ai_test_worker.done.connect(self._on_test_ai_done)
        self._ai_test_worker.start()

    def _on_test_ai_done(self, ok: bool, msg: str):
        self.ai_test_btn.setEnabled(True)
        self.ai_test_btn.setText("测试连接")
        self.ai_test_result.setStyleSheet(
            "color: #27ae60;" if ok else "color: #e74c3c;")
        self.ai_test_result.setText(("✅ " if ok else "❌ ") + msg)

    def done(self, result):
        """accept / reject / Esc / 点 X 都会走到这里：
        后台线程还在跑的话先等它收尾，别让 QThread 带着运行中的线程被销毁"""
        for worker in (self._ai_test_worker, self._voice_test_worker):
            if worker is not None and worker.isRunning():
                worker.wait(5000)
        self._ai_test_worker = None
        self._voice_test_worker = None
        super().done(result)

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
            "检查设备当前输入法：正常时不做任何改动；"
            "异常（未设置或已被卸载）时自动切回可用的输入法（优先系统 AOSP 键盘），"
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
            # 直接展示服务返回的结论：正常时是「当前输入法正常，无需恢复」，
            # 真恢复过则是「已恢复输入法：xxx」，比笼统的"成功"更有信息量
            show_toast(self, ds.restore_ime(), duration=2500)
        except Exception as e:
            msg = str(e)
            if "设备未连接" in msg:
                show_toast(self, "设备未连接", duration=2000)
            else:
                show_toast(self, "恢复输入法失败", duration=2000)

    # ------------------------------------------------------------------
    # 数据维护：回收无用步骤数据
    # ------------------------------------------------------------------
    def _build_data_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)

        title = QLabel("无用数据清理")
        title.setObjectName("SettingsPageTitle")
        layout.addWidget(title)

        subtitle = QLabel("回收用例数据和执行数据里已经没人引用的残留")
        subtitle.setObjectName("SettingsPageSubtitle")
        layout.addWidget(subtitle)

        row = QHBoxLayout()
        row.setSpacing(10)

        self.cleanup_btn = QPushButton("清理无用数据")
        self.cleanup_btn.setObjectName("cleanupDataBtn")
        self.cleanup_btn.setIcon(qta.icon('fa6s.broom', color='white'))
        self.cleanup_btn.setFixedSize(140, 32)
        self.cleanup_btn.clicked.connect(self._on_cleanup_data)
        row.addWidget(self.cleanup_btn)
        row.addStretch()
        layout.addLayout(row)

        hint = QLabel(
            "删除用例、删除项目/功能模块、导入用例都会在 steps_data.json 里留下没人引用的"
            "步骤（那份文件每改一个步骤就要整个重写一遍，残留越多越拖慢）。"
            "清理只删「不被任何现存用例引用」的步骤和套件里指向已删除用例的引用，"
            "现存用例的步骤一条都不会动。程序每次启动也会自动清理一次。"
        )
        hint.setObjectName("SettingsPageSubtitle")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # 上一次清理的结果（按钮点完把统计写在这里，不弹窗打断）
        self.cleanup_result = QLabel("")
        self.cleanup_result.setObjectName("SettingsPageSubtitle")
        self.cleanup_result.setWordWrap(True)
        layout.addWidget(self.cleanup_result)

        layout.addStretch()
        return page

    def _build_notify_page(self):
        """系统托盘页：托盘通知 + 最小化到托盘。"""
        outer = QWidget()
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setObjectName("NotifyScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer_layout.addWidget(scroll, 1)

        page = QWidget()
        scroll.setWidget(page)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)

        title = QLabel("系统托盘")
        title.setObjectName("SettingsPageTitle")
        layout.addWidget(title)

        subtitle = QLabel(
            "设备断连、任务完成时弹系统托盘通知；关闭窗口时最小化到托盘，后台继续运行。"
        )
        subtitle.setObjectName("SettingsPageSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        tray_form = QFormLayout()
        tray_form.setSpacing(12)
        tray_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.notify_tray_check = QCheckBox("设备断连 / 任务完成时弹系统托盘通知")
        self.notify_tray_check.setChecked(True)
        tray_form.addRow("托盘通知:", self.notify_tray_check)

        self.notify_tray_minimize_check = QCheckBox("关闭窗口时最小化到托盘（不退出程序）")
        self.notify_tray_minimize_check.setChecked(True)
        tray_form.addRow("最小化到托盘:", self.notify_tray_minimize_check)

        layout.addLayout(tray_form)

        layout.addStretch()
        return outer
    def _on_cleanup_data(self):
        main_win = self.parent()
        handler = getattr(main_win, 'data_cleanup_handler', None) if main_win else None
        if not callable(handler):
            self.cleanup_result.setText("当前无法清理：用例数据未就绪")
            return
        try:
            stat = handler()
        except Exception as e:
            self.cleanup_result.setText(f"清理失败：{e}")
            return
        if not stat:
            self.cleanup_result.setText("用例数据未加载完成，已跳过（不会动任何数据）")
            return
        if not stat.get('steps') and not stat.get('keys') and not stat.get('suite_refs'):
            self.cleanup_result.setText("很干净，没有需要清理的残留")
            return
        self.cleanup_result.setText(
            f"清理完成：删除无用步骤 {stat.get('steps', 0)} 个、"
            f"失效用例映射 {stat.get('keys', 0)} 条、"
            f"套件里的失效引用 {stat.get('suite_refs', 0)} 条，"
            f"steps_data.json 省下 {self._fmt_size(stat.get('freed', 0))}"
        )

    @staticmethod
    def _fmt_size(num_bytes) -> str:
        size = float(num_bytes or 0)
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024 or unit == "GB":
                return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
            size /= 1024

    # ------------------------------------------------------------------
    # 导航切换
    # ------------------------------------------------------------------
    def _on_nav_changed(self, current, previous):
        if current is None:
            return
        idx = current.data(0, Qt.ItemDataRole.UserRole)
        if idx is not None and 0 <= idx < self.content_stack.count():
            self.content_stack.setCurrentIndex(idx)
        # 切到「壁纸」页时才后台加载缩略图，设置对话框打开本身不卡
        if idx == getattr(self, "_wallpaper_page_idx", -1):
            self._ensure_wallpaper_thumbs()

    # ------------------------------------------------------------------
    # 数据加载 / 保存
    # ------------------------------------------------------------------
    def load_settings(self):
        self.dir_edit.setText(Settings.get_output_dir())
        wallpaper_path = Settings.get_wallpaper_path()
        self.wallpaper_edit.setText(wallpaper_path)
        # 已保存的是预设壁纸时，点亮对应缩略图
        if hasattr(self, "_preset_buttons") and wallpaper_path:
            self._select_preset_by_path(wallpaper_path)
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

        # AI 配置
        if hasattr(self, "ai_enable_check"):
            ai_cfg = Settings.get_ai_config()
            self.ai_enable_check.setChecked(ai_cfg["enabled"])
            self.ai_base_edit.setText(ai_cfg["base_url"])
            self.ai_key_edit.setText(ai_cfg["api_key"])
            self.ai_model_edit.setText(ai_cfg["model"])

        # 语音播报配置
        if hasattr(self, "voice_tone_combo"):
            self._load_voice_config()

        # 通知配置（系统托盘）
        if hasattr(self, "notify_tray_check"):
            cfg = Settings.get_notify_config()
            self.notify_tray_check.setChecked(cfg["tray_enabled"])
            self.notify_tray_minimize_check.setChecked(cfg["tray_minimize"])

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

        # 保存快捷键（如果已构建）
        if hasattr(self, '_shortcut_edits'):
            self._save_shortcuts()

        # 保存 AI 配置
        if hasattr(self, "ai_enable_check"):
            Settings.set_ai_config(
                enabled=self.ai_enable_check.isChecked(),
                base_url=self.ai_base_edit.text().strip() or "https://api.openai.com/v1",
                api_key=self.ai_key_edit.text().strip(),
                model=self.ai_model_edit.text().strip() or "gpt-4o-mini",
            )

        # 保存语音播报配置
        self._save_voice_config()

        # 保存通知配置（系统托盘）
        if hasattr(self, "notify_tray_check"):
            Settings.set_notify_config(
                tray_enabled=self.notify_tray_check.isChecked(),
                tray_minimize=self.notify_tray_minimize_check.isChecked(),
            )

    # ------------------------------------------------------------------
    # 按钮行为
    # ------------------------------------------------------------------
    def _on_apply(self):
        self._save_settings()
        # 设置页自己也要跟着换主题：只通知父窗口的话，弹窗会停留在旧主题
        try:
            self.apply_theme()
        except Exception as e:
            print(f"[SettingsDialog] 自身主题刷新失败: {e}")
        # 通知父窗口刷新壁纸和主题（顺序：先壁纸后主题）
        p = self.parent()
        if p is not None:
            if hasattr(p, 'load_wallpaper'):
                try:
                    p.load_wallpaper()
                except Exception as e:
                    print(f"[SettingsDialog] load_wallpaper 失败: {e}")
            if hasattr(p, 'apply_shortcuts'):
                try:
                    p.apply_shortcuts()
                except Exception as e:
                    print(f"[SettingsDialog] apply_shortcuts 失败: {e}")
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
            # 走自定义路径时取消预设缩略图的单选
            self._clear_preset_selection()

    def remove_wallpaper(self):
        self.wallpaper_edit.clear()
        self.wallpaper_opacity_slider.setValue(100)
        # 移除同时取消预设缩略图的单选
        self._clear_preset_selection()

    def _clear_preset_selection(self):
        # autoExclusive 的按钮直接 setChecked(False) 可能不生效（Qt 互斥组会保留一个选中），
        # 必须先临时关掉互斥、取消勾选、再恢复
        for b in getattr(self, "_preset_buttons", {}).values():
            b.setAutoExclusive(False)
            b.setChecked(False)
            b.setAutoExclusive(True)

    def _select_preset_by_path(self, path):
        """按路径点亮对应缩略图；找不到就不点亮（如自定义路径）"""
        for p, b in self._preset_buttons.items():
            b.setChecked(os.path.normpath(p) == os.path.normpath(path))

    # ------------------------------------------------------------------
    # 主题适配
    # ------------------------------------------------------------------
    def apply_theme(self):
        theme_mode = Settings.get_theme_mode()
        is_dark = (theme_mode == THEME_MODE_DARK)

        # 原生标题栏也要跟着换：main.py 里给 QDialog.showEvent 打的补丁只在
        # 对话框「显示时」生效，而这里是对话框开着的时候切主题（点「应用」）
        try:
            from utils.win_dark_title import set_dark_title_bar
            set_dark_title_bar(self, is_dark)
        except Exception as e:
            print(f"[SettingsDialog] 标题栏主题适配失败: {e}")

        # 下拉面板的统一样式：本对话框是自己建样式、不走 Theme.apply_theme_to_widget，
        # 而它又是打开设置时才创建的（错过了启动时那次批量刷新），所以这里显式刷一次
        try:
            from utils import widget_helpers
            widget_helpers.set_combo_popup_theme(is_dark)
        except Exception as e:
            print(f"[SettingsDialog] 下拉面板样式刷新失败: {e}")



        if is_dark:
            content_bg = "#2b2d30"
            nav_bg = "#1e1f22"
            nav_border = "#393b40"
            nav_text = "#dddddd"
            # 必须是不透明色：半透明色会被 QTreeView 的「缩进列 / 内容列」两个单元格
            # 以不同次数叠加，导致同一行出现明显色差
            nav_hover_bg = "#2b2c2f"
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
            btn_primary_pressed = "#0d47a1"
            btn_secondary_bg = "#3c3c3c"
            btn_secondary_fg = "#eeeeee"
            btn_secondary_hover = "#4a4a4a"
            btn_secondary_pressed = "#3a3a3a"
            btn_secondary_border = "#555555"
            danger_bg = "#c0392b"
            danger_fg = "#ffffff"
            danger_hover = "#e74c3c"
            danger_pressed = "#96281b"
            bottom_border = "#393b40"
            scroll_track = "rgba(58, 58, 58, 0.5)"
            scroll_handle = "#666666"
            scroll_handle_hover = "#888888"
        else:
            content_bg = "#ffffff"
            nav_bg = "#f7f8fa"
            nav_border = "#e0e0e0"
            nav_text = "#333333"
            # 同上，必须不透明（等价于 4% 黑叠在 nav_bg #f7f8fa 上）
            nav_hover_bg = "#edeef0"
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
            btn_primary_pressed = "#0d47a1"
            btn_secondary_bg = "#f0f0f0"
            btn_secondary_fg = "#333333"
            btn_secondary_hover = "#e0e0e0"
            btn_secondary_pressed = "#d0d0d0"
            btn_secondary_border = "#d0d0d0"
            danger_bg = "#e74c3c"
            danger_fg = "#ffffff"
            danger_hover = "#c0392b"
            danger_pressed = "#96281b"
            bottom_border = "#e0e0e0"
            scroll_track = "#e0e0e0"
            scroll_handle = "#c0c0c0"
            scroll_handle_hover = "#a0a0a0"

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
                /* 左边留 4px：箭头靠近对话框左边缘会显得挤，整列右移一点。
                   文字位置由下面 ::item 的 padding 拉回来，所以文字仍从 22px 起 */
                padding: 8px 0 8px 4px;
                font-size: 13px;
                /* 关键：让选中背景铺满整行（含 branch 缩进列） */
                show-decoration-selected: 1;
                /* 禁用 Qt 用 palette.Highlight 绘制，改用 QSS 控制 */
                selection-background-color: transparent;
                selection-color: transparent;
            }}
            #SettingsNav::item {{
                height: 28px;
                /* 6 而不是 10：箭头右缘到文字的间距从 14px 收到 7px。
                   文字起点 = 树左内边距(4) + indentation(16) + 这里(6) ≈ 22px，
                   与调整前(24px)基本一致，层级缩进也不会变 */
                padding: 0 6px;
                /* 注意：QTreeView 会把每行拆成 "缩进列 + 内容列" 两个单元格分别绘制背景，
                   若这里带水平 margin 或圆角，两段填充之间会露出底色形成缺口。
                   因此横向不留边距、不加圆角，让整行（含缩进列）连成一条完整高亮。 */
                border-radius: 0px;
                margin: 1px 0px;
                color: {nav_text};
            }}
            #SettingsNav::item:hover {{
                background: {nav_hover_bg};
            }}
            #SettingsNav::item:selected {{
                background-color: {nav_sel_bg};
                color: {nav_sel_text};
            }}
            /* 这里刻意不写任何 #SettingsNav::branch 规则：只要给 ::branch 指定属性
               （哪怕只是 background: transparent），Qt 就接管分支列的绘制，
               不再画展开/折叠箭头 —— 左导航的箭头就是这么丢的。
               选中行整行铺色由上面的 ::item:selected + selection-background-color:
               transparent 负责，缩进列会自动跟着 item 一起铺满，不需要 ::branch。 */

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

            /* ---------- 快捷键页滚动区（跟随主题，避免夜间模式白底） ---------- */
            #SettingsShortcutScroll {{
                background-color: {content_bg};
                border: none;
            }}
            #SettingsShortcutScroll > QWidget#qt_scrollarea_viewport {{
                background-color: {content_bg};
            }}
            #SettingsShortcutContainer {{
                background-color: {content_bg};
            }}
            #SettingsDialog QLabel#ShortcutGroupLabel {{
                color: {sub_color};
                font-size: 12px;
                font-weight: 600;
                background: transparent;
                padding: 6px 0 2px 0;
            }}

            /* ---------- 快捷键页滚动条（细条，对齐项目树 ProjectTreeView 样式） ---------- */
            #SettingsShortcutScroll QScrollBar:vertical {{
                width: 6px;
                background: {scroll_track};
                border-radius: 3px;
                margin: 0px;
            }}
            #SettingsShortcutScroll QScrollBar::handle:vertical {{
                background: {scroll_handle};
                border-radius: 3px;
                min-height: 20px;
            }}
            #SettingsShortcutScroll QScrollBar::handle:vertical:hover {{
                background: {scroll_handle_hover};
            }}
            #SettingsShortcutScroll QScrollBar::add-line:vertical,
            #SettingsShortcutScroll QScrollBar::sub-line:vertical {{
                height: 0px;
                width: 0px;
                background: transparent;
                border: none;
            }}
            #SettingsShortcutScroll QScrollBar::add-page:vertical,
            #SettingsShortcutScroll QScrollBar::sub-page:vertical {{
                background: transparent;
            }}
            #SettingsShortcutScroll QScrollBar::up-arrow:vertical,
            #SettingsShortcutScroll QScrollBar::down-arrow:vertical {{
                background: transparent;
                border: none;
                width: 0px;
                height: 0px;
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
            #SettingsDialog QPushButton:pressed {{
                background-color: {btn_secondary_pressed};
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
            #SettingsDialog QPushButton#restoreImeBtn:pressed {{
                background-color: #216692;
                border-color: #216692;
            }}

            /* ---------- 数据维护：清理无用数据 ----------
               取值与上面「设备维护：恢复输入法」#restoreImeBtn 完全一致，
               改一处记得同步另一处，保证两个页面的动作按钮观感相同 */
            #SettingsDialog QPushButton#cleanupDataBtn {{
                background-color: #3498db;
                color: white;
                border: 1px solid #3498db;
                font-weight: 500;
            }}
            #SettingsDialog QPushButton#cleanupDataBtn:hover {{
                background-color: #5dade2;
                border-color: #5dade2;
            }}
            #SettingsDialog QPushButton#cleanupDataBtn:pressed {{
                background-color: #216692;
                border-color: #216692;
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
            #SettingsDialog QPushButton#dangerBtn:pressed {{
                background-color: {danger_pressed};
                border-color: {danger_pressed};
            }}

            /* ---------- 壁纸预设缩略图 ---------- */
            #SettingsDialog QToolButton#wallpaperThumbBtn {{
                background-color: {input_bg};
                color: {input_text};
                border: 2px solid {input_border};
                border-radius: 8px;
                padding: 6px 4px;
                font-size: 12px;
            }}
            #SettingsDialog QToolButton#wallpaperThumbBtn:hover {{
                border-color: {btn_primary_hover};
            }}
            #SettingsDialog QToolButton#wallpaperThumbBtn:checked {{
                border-color: {btn_primary_bg};
                background-color: {nav_sel_bg};
            }}

            /* ---------- 壁纸预设区：背景与页面一致 ---------- */
            #SettingsDialog QScrollArea#WallpaperScroll,
            #SettingsDialog QScrollArea#WallpaperScroll > QWidget#qt_scrollarea_viewport,
            #SettingsDialog QWidget#WallpaperGridContainer {{
                background-color: {content_bg};
                border: none;
            }}

            /* ---------- 壁纸预设滚动条（对齐项目树 ProjectTreeView / 快捷键页样式） ---------- */
            #SettingsDialog QScrollArea#WallpaperScroll QScrollBar:vertical {{
                width: 6px;
                background: {scroll_track};
                border-radius: 3px;
                margin: 0px;
            }}
            #SettingsDialog QScrollArea#WallpaperScroll QScrollBar::handle:vertical {{
                background: {scroll_handle};
                border-radius: 3px;
                min-height: 20px;
            }}
            #SettingsDialog QScrollArea#WallpaperScroll QScrollBar::handle:vertical:hover {{
                background: {scroll_handle_hover};
            }}
            #SettingsDialog QScrollArea#WallpaperScroll QScrollBar::add-line:vertical,
            #SettingsDialog QScrollArea#WallpaperScroll QScrollBar::sub-line:vertical {{
                height: 0px;
                width: 0px;
                background: transparent;
                border: none;
            }}
            #SettingsDialog QScrollArea#WallpaperScroll QScrollBar::add-page:vertical,
            #SettingsDialog QScrollArea#WallpaperScroll QScrollBar::sub-page:vertical {{
                background: transparent;
            }}

            /* ---------- 设置框内全局细滚动条（覆盖所有未单独定制的滚动条） ---------- */
            #SettingsDialog QScrollBar:vertical {{
                width: 6px;
                background: {scroll_track};
                border-radius: 3px;
                margin: 0px;
            }}
            #SettingsDialog QScrollBar::handle:vertical {{
                background: {scroll_handle};
                border-radius: 3px;
                min-height: 20px;
            }}
            #SettingsDialog QScrollBar::handle:vertical:hover {{
                background: {scroll_handle_hover};
            }}
            #SettingsDialog QScrollBar::add-line:vertical,
            #SettingsDialog QScrollBar::sub-line:vertical,
            #SettingsDialog QScrollBar::up-arrow:vertical,
            #SettingsDialog QScrollBar::down-arrow:vertical {{
                width: 0px;
                height: 0px;
                background: transparent;
                border: none;
            }}
            #SettingsDialog QScrollBar::add-page:vertical,
            #SettingsDialog QScrollBar::sub-page:vertical {{
                background: transparent;
            }}
            #SettingsDialog QScrollBar:horizontal {{
                height: 6px;
                background: {scroll_track};
                border-radius: 3px;
                margin: 0px;
            }}
            #SettingsDialog QScrollBar::handle:horizontal {{
                background: {scroll_handle};
                border-radius: 3px;
                min-width: 20px;
            }}
            #SettingsDialog QScrollBar::handle:horizontal:hover {{
                background: {scroll_handle_hover};
            }}

            /* ---------- 通知页滚动区（跟随主题背景） ---------- */
            #NotifyScroll,
            #NotifyScroll > QWidget#qt_scrollarea_viewport {{
                background-color: {content_bg};
                border: none;
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
            #SettingsDialog QPushButton#okBtn:pressed,
            #SettingsDialog QPushButton#applyBtn:pressed {{
                background-color: {btn_primary_pressed};
                border-color: {btn_primary_pressed};
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

    def _build_shortcuts_page(self):
        """快捷键设置页面"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(12)

        title = QLabel("快捷键")
        title.setObjectName("SettingsPageTitle")
        layout.addWidget(title)

        subtitle = QLabel("自定义全局与各功能页的快捷键；留空表示禁用该快捷键")
        subtitle.setObjectName("SettingsPageSubtitle")
        layout.addWidget(subtitle)

        # 提示框
        hint = QLabel(
            "💡 页内快捷键只在对应页面生效（如 Ctrl+F 在自动化编辑页聚焦步骤搜索，"
            "在 ADB 工具箱页聚焦指令搜索）"
        )
        hint.setObjectName("SettingsPageSubtitle")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # 滚动区
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setObjectName("SettingsShortcutScroll")

        container = QWidget()
        container.setObjectName("SettingsShortcutContainer")
        form = QFormLayout(container)
        form.setSpacing(8)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._shortcut_edits = {}
        # 分类展示
        groups = [
            ("—— 全局 ——", ["help_center", "open_settings", "refresh_devices",
                          "toggle_log_panel", "restore_ime"]),
            ("—— 自动化编辑 ——", ["toggle_record", "generate_steps",
                                 "focus_step_search", "import_cases", "export_cases"]),
            ("—— 自动化执行 ——", ["execute_cases", "toggle_select_all",
                                 "save_suite", "delete_suite", "generate_report"]),
            ("—— ADB 工具箱 ——", ["adb_search", "adb_add_command", "adb_edit_command",
                                 "adb_delete_command", "adb_import_commands",
                                 "adb_export_commands", "adb_execute_selected"]),
            ("—— ADB 快捷功能 ——", ["adb_wireless", "adb_scrcpy", "adb_install",
                                   "adb_push", "adb_device_info",
                                   "adb_hprof", "adb_monkey", "adb_crash",
                                   "adb_anr", "adb_md5", "adb_weak_network", "adb_packet"]),
            ("—— 性能检测 ——", ["perf_toggle_monitor", "perf_toggle_pause",
                               "perf_export_csv", "perf_save_baseline"]),
            ("—— 应用元素库 ——", ["elem_add", "elem_edit", "elem_delete", "elem_verify"]),
        ]
        labels = _SHORTCUT_LABELS

        for group_title, keys in groups:
            sep = QLabel(group_title)
            sep.setObjectName("ShortcutGroupLabel")
            form.addRow(sep)
            for key in keys:
                edit = QKeySequenceEdit()
                edit.setMaximumWidth(240)
                self._shortcut_edits[key] = edit
                if key not in labels:
                    # groups 与 _SHORTCUT_LABELS 是两份清单，加新快捷键时容易只改一处；
                    # 漏了界面上就会直接显示英文 key（如 adb_device_info），留个提示便于发现
                    print(f"[settings] 快捷键 {key} 缺少中文名，请补进 _SHORTCUT_LABELS")
                form.addRow(f"{labels.get(key, key)}:", edit)

        scroll.setWidget(container)
        layout.addWidget(scroll, 1)

        # 按钮：恢复默认
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        reset_btn = QPushButton("恢复默认快捷键")
        reset_btn.setObjectName("browseBtn")
        reset_btn.setFixedSize(160, 32)
        reset_btn.clicked.connect(self._on_reset_shortcuts)
        btn_row.addWidget(reset_btn)
        layout.addLayout(btn_row)

        # 加载当前配置
        self._load_shortcuts()
        return page

    def _load_shortcuts(self):
        shortcuts = Settings.get_shortcuts()
        for key, edit in self._shortcut_edits.items():
            ks = shortcuts.get(key, "")
            if ks:
                edit.setKeySequence(QKeySequence(ks))
            else:
                edit.clear()

    def _on_reset_shortcuts(self):
        from utils.dialogs import ConfirmDeleteDialog
        if not ConfirmDeleteDialog.ask(
            self, "恢复默认", "确定恢复所有快捷键为默认值吗？"
        ):
            return
        Settings.reset_shortcuts()
        self._load_shortcuts()

    def _save_shortcuts(self):
        new = {}
        conflicts = {}
        for key, edit in self._shortcut_edits.items():
            ks = edit.keySequence().toString()
            if ks:
                new[key] = ks
                conflicts.setdefault(ks, []).append(key)
        # 只警告，不阻止（因为页内同名是允许的）
        # 但全局快捷键之间以及全局与页内之间应警告
        # 这里简单跳过，用户自己负责
        settings = Settings.load()
        settings["shortcuts"] = new
        Settings.save(settings)