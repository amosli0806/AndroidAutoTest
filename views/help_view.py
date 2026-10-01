# views/help_view.py
import os
import sys
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QSplitter, QTreeView, QApplication, QStyle, QTextEdit, QVBoxLayout
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QStandardItemModel, QStandardItem, QImage, QTextDocument
from utils.theme import Theme, ThemeMode
from utils import tree_state
from utils.settings import Settings, THEME_MODE_DARK


class HelpView(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("HelpView")
        # 禁用 QFrame 默认边框，由样式表控制
        self.setFrameStyle(QFrame.Shape.NoFrame)
        # 启用背景填充，确保样式表的背景色生效（但整体透明，容器各自设置）
        self.setAutoFillBackground(True)
        self.setup_ui()
        self.load_help_data()
        # 应用主题
        self.apply_theme()

    def apply_theme(self, theme_mode: ThemeMode = None):
        if theme_mode is None:
            theme_mode = ThemeMode.DARK if Settings.get_theme_mode() == THEME_MODE_DARK else ThemeMode.LIGHT

        # 检测是否设置了壁纸（用于容器背景半透明处理）
        import os as _os
        has_wp = False
        try:
            wp_path = Settings.get_wallpaper_path()
            has_wp = bool(wp_path and _os.path.exists(wp_path))
        except Exception:
            pass

        if theme_mode == ThemeMode.DARK:
            # 参考项目管理：有壁纸时容器 transparent，让 centralWidget 罩层 + 壁纸透出；无壁纸时纯色
            container_bg = "transparent" if has_wp else "#2d2d2d"
            container_border = "rgba(85, 85, 85, 0.9)" if has_wp else "#555"
            text_color = "#eee"
            selected_bg = "#1e3a5f"
            selected_fg = "#ffffff"
            hover_bg = "rgba(74, 74, 74, 0.6)"
            sb_bg = "rgba(58, 58, 58, 0.5)"
            sb_handle = "#666"
        else:
            container_bg = "transparent" if has_wp else "white"
            container_border = "rgba(208, 208, 208, 0.9)" if has_wp else "#d0d0d0"
            text_color = "#333"
            selected_bg = "#d0e4f7"
            selected_fg = "#1a1a1a"
            hover_bg = "#dfe2e6"
            sb_bg = "#e0e0e0"
            sb_handle = "#c0c0c0"

        style = f"""
            #HelpView {{
                /* 不要写 transparent：Qt 会把该控件的调色板整份算成全黑，
                   挂在它下面的 QMessageBox / QFileDialog 会继承黑调色板、内容看不清。
                   alpha=0 的具体颜色视觉上一样全透，但调色板正常。 */
                background-color: {"rgba(44, 44, 44, 0)" if theme_mode == ThemeMode.DARK else "rgba(245, 246, 250, 0)"};
            }}
            #HelpTreeContainer, #HelpContentContainer {{
                background-color: {container_bg};
                border: 1px solid {container_border};
                border-radius: 8px;
            }}
            #HelpTreeContainer QTreeView {{
                background-color: transparent;
                border: none;
                outline: none;
                /* 关键：禁用 Qt 用 palette.Highlight 绘制 branch/选中区域 */
                selection-background-color: transparent;
                selection-color: transparent;
            }}
            #HelpTreeContainer QTreeView::item {{
                height: 30px;
                color: {text_color};
                border: none;
                outline: none;
            }}
            #HelpTreeContainer QTreeView::item:selected,
            #HelpTreeContainer QTreeView::item:selected:active,
            #HelpTreeContainer QTreeView::item:selected:!active,
            #HelpTreeContainer QTreeView::item:selected:focus {{
                background-color: {selected_bg};
                color: {selected_fg};
                border: none;
                outline: none;
            }}
            #HelpTreeContainer QTreeView::item:hover:!selected {{
                background-color: {hover_bg};
            }}
            /* 这里刻意不写 ::branch 规则：只要给 ::branch 指定属性（哪怕只是
               background: transparent），Qt 就接管分支列的绘制，不再画展开/折叠
               箭头（帮助中心的箭头就是这么丢的）。分支列不出现蓝色色块由上面的
               selection-background-color: transparent + 末尾 palette.Highlight
               置透明两处负责。 */
            #HelpTreeContainer QTreeView QScrollBar:vertical {{
                width: 6px;
                background: {sb_bg};
                border-radius: 3px;
                margin: 0px;
            }}
            #HelpTreeContainer QTreeView QScrollBar::handle:vertical {{
                background: {sb_handle};
                border-radius: 3px;
                min-height: 20px;
            }}
            #HelpTreeContainer QTreeView QScrollBar::add-line:vertical,
            #HelpTreeContainer QTreeView QScrollBar::sub-line:vertical {{
                height: 0px;
                width: 0px;
            }}
            #HelpTreeContainer QTreeView QScrollBar::add-page:vertical,
            #HelpTreeContainer QTreeView QScrollBar::sub-page:vertical {{
                background: transparent;
            }}
            #HelpContentContainer QTextEdit {{
                border: none;
                background: transparent;
                color: {text_color};
                padding: 20px 24px;
                font-size: 14px;
                line-height: 1.8;
            }}
            #HelpContentContainer QTextEdit QScrollBar:vertical {{
                width: 6px;
                background: {sb_bg};
                border-radius: 3px;
                margin: 0px;
            }}
            #HelpContentContainer QTextEdit QScrollBar::handle:vertical {{
                background: {sb_handle};
                border-radius: 3px;
                min-height: 20px;
            }}
            #HelpContentContainer QTextEdit QScrollBar::add-line:vertical,
            #HelpContentContainer QTextEdit QScrollBar::sub-line:vertical {{
                height: 0px;
                width: 0px;
            }}
            #HelpContentContainer QTextEdit QScrollBar::add-page:vertical,
            #HelpContentContainer QTextEdit QScrollBar::sub-page:vertical {{
                background: transparent;
            }}
        """

        self.setStyleSheet(style)

        # 兜底：palette 层把 Highlight 设透明
        try:
            from PyQt6.QtGui import QPalette, QColor
            pal = self.tree.palette()
            pal.setColor(QPalette.ColorRole.Highlight, QColor(0, 0, 0, 0))
            pal.setColor(QPalette.ColorRole.HighlightedText, QColor(0, 0, 0, 0))
            self.tree.setPalette(pal)
            vp = self.tree.viewport()
            if vp is not None:
                vp.setPalette(pal)
        except Exception as e:
            print(f"[HelpView.apply_theme] palette 设置失败: {e}")

        # 刷新当前内容（让 HTML 也跟随主题重新渲染）
        current_index = self.tree.currentIndex()
        if current_index.isValid():
            self.on_tree_clicked(current_index)

    def setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(4)
        splitter.setStyleSheet("QSplitter::handle { background: transparent; width: 4px; }")

        # 左侧容器（树）
        self.tree_container = QFrame()
        self.tree_container.setObjectName("HelpTreeContainer")
        self.tree_container.setFrameStyle(QFrame.Shape.NoFrame)
        tree_layout = QVBoxLayout(self.tree_container)
        tree_layout.setContentsMargins(0, 0, 0, 0)
        self.tree = QTreeView()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(20)
        tree_layout.addWidget(self.tree)
        # 展开状态持久化：默认全折叠，记住用户上次展开的章节（存 data/config.json）。
        # 章节/条目没有业务 id，用"从根到自己的文字路径"当 id
        self._tree_state = tree_state.bind_view(
            self.tree, "help_tree", id_of=tree_state.index_text_path_id)
        splitter.addWidget(self.tree_container)

        # 右侧容器（内容）
        self.content_container = QFrame()
        self.content_container.setObjectName("HelpContentContainer")
        self.content_container.setFrameStyle(QFrame.Shape.NoFrame)
        content_layout = QVBoxLayout(self.content_container)
        content_layout.setContentsMargins(0, 0, 0, 0)
        self.content = QTextEdit()
        self.content.setReadOnly(True)
        self.content.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        # 本页帮助图片的预缩放缓存：key(QUrl) -> QImage。
        # QTextEdit 对 <img> 的现场缩放不走平滑滤波，大图缩小时锯齿严重（发花），
        # 所以在 _get_image_html 里先按最终显示尺寸 + 屏幕倍率高质量缩好，
        # setHtml 前注册进文档资源表，绘制时 1:1 贴图（见 setHtml 调用处）。
        self._help_images = {}
        # 让 QTextEdit 及 viewport 透明，配合 HTML body 的 transparent 让壁纸透出
        self.content.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.content.viewport().setAutoFillBackground(False)
        self.content.setFrameShape(QFrame.Shape.NoFrame)
        content_layout.addWidget(self.content)
        splitter.addWidget(self.content_container)

        splitter.setSizes([220, 600])
        layout.addWidget(splitter)

        self.tree.clicked.connect(self.on_tree_clicked)

    def load_help_data(self):
        self.help_content = self._get_help_content()
        model = QStandardItemModel()
        root = model.invisibleRootItem()

        # 不再使用图标，直接为每个章节创建不带图标的项
        for section, items in self.help_content.items():
            section_item = QStandardItem(section)
            section_item.setEditable(False)
            for subitem in items.keys():
                child = QStandardItem(subitem)
                child.setEditable(False)
                section_item.appendRow(child)
            root.appendRow(section_item)

        self.tree.setModel(model)
        # 展开状态持久化：重建前收状态、填完后按记录还原；没有记录（首次运行）
        # 就保持全折叠（原来是无条件 expandAll）
        self._tree_state.snapshot()
        self._tree_state.restore()
        first_index = model.index(0, 0)
        if first_index.isValid():
            if model.hasChildren(first_index):
                child_index = model.index(0, 0, first_index)
                if child_index.isValid():
                    self.tree.setCurrentIndex(child_index)
                    self.on_tree_clicked(child_index)
            else:
                self.tree.setCurrentIndex(first_index)
                self.on_tree_clicked(first_index)

    # ---------- 辅助方法：显示单张图片（左对齐） ----------
    # 统一显示宽度：Qt 富文本不支持 max-width/max-height（直接被忽略，图片按原始
    # 像素尺寸显示 → 宽高参差、观感失真），必须算好等比宽高写进 width/height 属性。
    # 只缩小不放大：小分辨率截图放大必然模糊，原图比统一宽度小时保持原尺寸。
    HELP_IMG_DISPLAY_WIDTH = 480

    def _get_image_html(self, filename, desc="示例图片", max_height="400px"):
        """生成图片 HTML（左对齐，有截图时无背景，占位图时有背景）"""
        # 未配图名的步骤（空串/纯空白）：不渲染残缺占位框，直接跳过
        if not (filename or '').strip():
            return ""
        if getattr(sys, 'frozen', False):
            base_dir = sys._MEIPASS
        else:
            base_dir = os.path.dirname(os.path.dirname(__file__))
        img_path = os.path.join(base_dir, "resources", "images", "help", filename)

        if os.path.exists(img_path):
            reader = QImage(img_path)
            disp_w, disp_h = None, None
            if not reader.isNull() and reader.width() > 0:
                w, h = reader.width(), reader.height()
                disp_w = min(w, self.HELP_IMG_DISPLAY_WIDTH)
                disp_h = round(h * disp_w / w)
                max_h = int(max_height.rstrip("px"))
                if disp_h > max_h:
                    disp_h = max_h
                    disp_w = round(w * disp_h / h)
            if disp_w and disp_h:
                # 按屏幕倍率生成高清版：150% 缩放屏上生成 1.5x 物理分辨率的图，
                # 标记 devicePixelRatio 后绘制仍然是逻辑尺寸，但物理像素更多更清晰
                dpr = self.devicePixelRatioF() or 1.0
                phys_w = round(disp_w * dpr)
                phys_h = round(disp_h * dpr)
                scaled = reader.scaled(phys_w, phys_h,
                                       Qt.AspectRatioMode.IgnoreAspectRatio,
                                       Qt.TransformationMode.SmoothTransformation)
                scaled.setDevicePixelRatio(dpr)
                key = f"helpimg://{filename}_{phys_w}x{phys_h}"
                self._help_images[key] = scaled
                return f'''
                <div style="margin: 12px 0; text-align: left; background: transparent; border-radius: 0; padding: 0;">
                    <img src="{key}" alt="{desc}" width="{disp_w}" height="{disp_h}"
                         style="border-radius: 6px; display: inline-block;">
                </div>
                '''
        # 图片缺失：占位框里突出显示应放置的文件名，方便补图时对照
        return f'''
        <div style="border: 2px dashed #d0d0d0; border-radius: 8px; padding: 30px 20px; margin: 12px 0; text-align: left; background: #fafafa; color: #999; font-size: 14px;">
            🖼️ {desc}<br>
            <span style="font-size: 12px; color: #bbb;">请将截图放置于 resources/images/help/ 目录，文件名：</span>
            <span style="font-size: 13px; color: #1976d2; font-weight: bold;">{filename}</span>
        </div>
        '''

    # ---------- 辅助方法：多步骤截图序列（描述在标题行） ----------
    def _get_steps_html(self, steps, title="操作步骤"):
        """
        生成步骤序列 HTML
        steps: list of dict, 每个元素 {'image': 'xxx.png', 'desc': '说明文字'}
        """
        if not steps:
            return "<p style='color:#999;'>暂无步骤截图</p>"
        html = f"<h3 style='font-size: 16px; margin-top: 20px;'>{title}</h3>"
        for idx, step in enumerate(steps, 1):
            img_html = self._get_image_html(step['image'], step['desc'], max_height="350px")
            html += f"""
            <div style="margin: 16px 0; border-bottom: 1px solid #eee; padding-bottom: 12px;">
                <div style="display: flex; align-items: center; gap: 12px; font-weight: 600; font-size: 14px; margin-bottom: 4px;">
                    <span>步骤 {idx}</span>
                    <span style="font-weight: normal; font-size: 13px;">{step['desc']}</span>
                </div>
                {img_html}
            </div>
            """
        return html

    # ---------- 帮助内容 ----------
    def _get_help_content(self):
        """返回所有帮助章节和子主题的 HTML 内容"""
        return {
                        "⚙️ 环境准备": {
                "安装 adb": f"""
                <h2 style="font-size: 20px; border-bottom: 2px solid; padding-bottom: 6px;">⚙️ 环境准备：安装 adb</h2>
                <p style="font-size: 15px;">
                    虫师通过 <b>adb</b> 与安卓设备通信，电脑上需要先装好 adb，否则无法连接设备。
                </p>

                {self._get_steps_html([
                    {'image': 'adb_setup_1_download.png', 'desc': '① 下载 adb：https://adbdownload.com/'},
                    {'image': 'adb_setup_2_extract.png', 'desc': '② 解压到任意目录，例如 D:\\platform-tools'},
                    {'image': '', 'desc': '③ 把该目录加入系统 PATH：此电脑右键 → 属性 → 高级系统设置 → 环境变量 → 编辑 Path → 新建'},
                    {'image': 'adb_setup_4_verify.png', 'desc': '④ 打开新的命令行窗口，输入 adb version，显示版本号即成功'},
                    {'image': 'adb_setup_5_restart.png', 'desc': '⑤ 重启虫师，顶部应能看到已连接的设备'},
                ])}

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    💡 手机端需开启「开发者选项 → USB 调试」；首次连接时设备会弹授权框，点允许即可。
                </div>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    ⚠️ 无线连接提示 offline 时，多为设备未授权或网络不通，重新授权或改用 USB 即可。
                </div>
                """,
            },
                        "🪟 窗口与界面": {
                "迷你模式": """
                <h2 style="font-size: 20px; border-bottom: 2px solid; padding-bottom: 6px;">🪟 迷你模式</h2>
                <p style="font-size: 15px;">
                    屏幕不够用时，可把虫师缩成一个只显示 ADB 指令管理的小窗口：点左侧工具栏最底部的
                    <b>迷你模式按钮</b>进入，再点一次恢复全屏。
                </p>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    小窗口里保留：顶部设备选择、ADB 指令管理区、底部日志面板，常用操作不受影响。
                </div>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    💡 迷你窗口是固定尺寸，所以没有最大化按钮；退出后会自动恢复原来的窗口大小。
                </div>
                """
            },
                        "🔧 ADB工具箱": {
                "使用说明": f"""
                <h2 style="font-size: 20px; border-bottom: 2px solid; padding-bottom: 6px;">🔧 ADB工具箱</h2>
                <p style="font-size: 15px;">
                    集成常用 ADB 命令与调试工具，覆盖设备管理、指令执行、文件操作、日志抓取等场景。
                    左侧是<b>指令管理</b>，右侧自上而下是<b>搜索框</b>、<b>弱网模拟</b>、<b>Monkey 测试</b>。
                </p>

                {self._get_steps_html([
                    {'image': 'adb_2_search.png', 'desc': '搜索框支持预设、自定义、ADB 库三类命令'},
                    {'image': 'adb_3_cmdlist.png', 'desc': '左侧命令列表：勾选后执行或停止，互不影响'},
                    {'image': 'adb_4_quick.png', 'desc': '「弱网模拟」「Monkey 测试」面板内嵌在右栏'},
                    {'image': 'adb_5_logs.png', 'desc': '执行结果统一显示在底部「虫师日志」面板'},
                ])}

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    💡 通过「菜单 → 新增指令」可保存自己的常用命令，支持定时执行、循环等。
                </div>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    ⚠️ 用例执行期间，会改动设备状态的命令会被自动禁用，避免干扰测试；
                    日志、录屏、截图等只读命令不受影响，随时可用于抓取现场信息。
                </div>
                """,

                "快捷功能": f"""
                <h2 style="font-size: 20px; border-bottom: 2px solid; padding-bottom: 6px;">🛠️ 快捷功能</h2>
                <p style="font-size: 15px;">
                    常用小工具按用途分布在三处：<b>顶部工具栏</b>、<b>左侧工具栏下方</b>、<b>性能检测页</b>，
                    ADB 工具箱右栏还有弱网与 Monkey 两个内嵌面板。
                </p>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    <b>顶部工具栏</b><br>
                    • <b>无线</b>：无线连接设备　<b>投屏</b>：scrcpy 镜像到电脑，支持同步录屏<br>
                    • <b>安装</b>：装 APK，失败会给出原因　<b>推送</b>：传文件到设备　<b>MD5</b>：算 APK 的 MD5
                </div>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    <b>左侧工具栏下方</b>（点开显示在底部面板，再点收起）<br>
                    • <b>硬件信息</b>：分辨率 / 屏幕密度 / 安卓版本<br>
                    • <b>Crash / ANR 日志</b>：一键拉取对应崩溃日志<br>
                    • <b>日志</b>：虫师运行日志
                </div>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    <b>性能检测页 · 性能工具</b><br>
                    • <b>堆转储</b>：导出应用内存快照　<b>抓包</b>：抓取网络流量（Wireshark 可打开）
                </div>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    ⚠️ 用例执行期间，会改动设备状态的操作会被自动禁用；日志、录屏、截图等只读操作不受影响。
                </div>
                """
            },

            "📁 自动化编辑": {
                "项目管理": f"""
                <h2 style="font-size: 20px; border-bottom: 2px solid; padding-bottom: 6px;">📁 项目管理</h2>
                <p style="font-size: 15px;">
                    <b>项目管理</b> 是组织测试用例的核心。你可以创建 <b>项目</b>，在项目下建立 <b>功能模块</b>，然后在模块中添加具体的 <b>测试用例</b>。
                </p>
                {self._get_steps_html([
                    {'image': 'project_1_create_project.png', 'desc': '右键空白区域 → 选择"创建项目"，输入项目名称'},
                    {'image': 'project_2_create_module.png', 'desc': '右键项目 → "创建功能模块"，输入模块名称'},
                    {'image': 'project_3_create_case.png', 'desc': '右键模块 → "创建用例"，输入用例名称'},
                    {'image': 'project_4_edit_case.png', 'desc': '点击用例，右侧自动加载步骤列表和动作卡片'},
                    {'image': 'project_5_context_menu.png', 'desc': '右键节点可重命名、删除或复制（自动避免重名）'},
                ])}
                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    💡 项目数据会在每次操作后自动保存，无需手动保存。
                </div>
                """,

                "步骤列表": f"""
                <h2 style="font-size: 20px; border-bottom: 2px solid; padding-bottom: 6px;">📋 步骤列表</h2>
                <p style="font-size: 15px;">
                    <b>步骤列表</b> 显示当前选中用例的所有操作步骤。支持拖拽排序、编辑、复制、删除，以及搜索和自然语言生成。
                </p>
                {self._get_steps_html([
                    {'image': 'step_1_list_view.png', 'desc': '选择用例后，步骤自动显示在列表中'},
                    {'image': 'step_2_drag_sort.png', 'desc': '长按卡片可调整顺序'},
                    {'image': 'step_3_edit_step.png', 'desc': '点击铅笔图标编辑步骤名称或参数'},
                    {'image': 'step_4_search_filter.png', 'desc': '在搜索框输入关键词过滤步骤'},
                    {'image': 'step_5_natural_language.png', 'desc': '输入操作描述（如"点击首页，等待3秒"），点击"生成"自动创建步骤'},
                ])}
                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    💡 自然语言支持点击、输入、等待、滑动、断言等，并自动匹配元素库中的元素。
                </div>
                """,

                "动作卡片": f"""
                <h2 style="font-size: 20px; border-bottom: 2px solid; padding-bottom: 6px;">🎴 动作卡片</h2>
                <p style="font-size: 15px;">
                    动作卡片是构建步骤的核心，每种动作有独立的卡片，填写参数后添加即可。
                </p>
                {self._get_steps_html([
                    {'image': 'action_1_select_card.png', 'desc': '从右侧选择需要的动作卡片（如"点击"）'},
                    {'image': 'action_2_fill_params.png', 'desc': '选择定位方式（资源ID/坐标/文本/描述/XPath）并填写定位值'},
                    {'image': 'action_3_element_library.png', 'desc': '点击文件夹图标可从元素库选择，自动填充定位信息'},
                    {'image': 'action_4_add_step.png', 'desc': '填写步骤说明（可选），点击"添加"按钮生成步骤'},
                    {'image': 'action_5_assert_example.png', 'desc': '断言卡片可验证元素是否存在或文本匹配，是自动化测试的关键'},
                ])}
                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    💡 坐标定位使用相对坐标（0~分辨率），可跨设备自适应。
                </div>
                """,

                "录制与生成": f"""
                <h2 style="font-size: 20px; border-bottom: 2px solid; padding-bottom: 6px;">🎥 录制回放</h2>
                <p style="font-size: 15px;">
                    录制功能可自动记录设备操作并生成步骤，极大提高用例编写效率。
                </p>
                {self._get_steps_html([
                    {'image': 'record_1_connect_device.png', 'desc': '确保设备已连接（顶部工具栏显示序列号）'},
                    {'image': 'record_2_select_case.png', 'desc': '在项目树中选择一个用例用于存放生成的步骤'},
                    {'image': 'record_3_start_recording.png', 'desc': '点击步骤列表上方的绿色圆点开始录制（按钮变红录制用户操作中）'},
                    {'image': 'record_4_perform_actions.png', 'desc': '在设备上执行点击、双击、长按、滑动等操作'},
                    {'image': 'record_5_stop_generate.png', 'desc': '再次点击红色圆点停止录制，系统自动匹配步骤'},
                ])}
                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    💡 生成的步骤默认使用坐标定位，适合跨设备回放。
                </div>
                """
            },

            "👁️ 应用可视化": {
                "使用 weditor": f"""
                <h2 style="font-size: 20px; border-bottom: 2px solid; padding-bottom: 6px;">👁️ 应用可视化 (weditor)</h2>
                <p style="font-size: 15px;">
                    利用 <code>weditor</code> 直接在电脑上查看 Android 设备的 UI 层级结构，快速定位元素。
                </p>
                {self._get_steps_html([
                    {'image': 'weditor_1_connect.png', 'desc': '设备连接并开启调试模式'},
                    {'image': 'weditor_2_launch.png', 'desc': '在"应用可视化" Tab 点击"启动 weditor"'},
                    {'image': 'weditor_3_view_ui.png', 'desc': '界面嵌入显示设备截图和 UI 树'},
                    {'image': 'weditor_4_select_element.png', 'desc': '点击 UI 树节点，右侧高亮控件并显示属性'},
                    {'image': 'weditor_5_copy_selector.png', 'desc': '复制资源ID或 XPath 到动作卡片中快速生成步骤'},
                ])}
                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    💡 首次启动需要下载依赖，请耐心等待。
                </div>
                """
            },

            "⚡ 自动化执行": {
                "执行与报告": f"""
                <h2 style="font-size: 20px; border-bottom: 2px solid; padding-bottom: 6px;">⚡ 自动化执行</h2>
                <p style="font-size: 15px;">
                    勾选用例，设置策略，执行测试并实时查看日志与统计，最后生成 HTML 报告。
                </p>
                {self._get_steps_html([
                    {'image': 'execute_1_select_cases.png', 'desc': '在右侧树中勾选要执行的用例（支持全选/取消全选）'},
                    {'image': 'execute_2_set_strategy.png', 'desc': '设置循环次数（1~999）和失败停止开关'},
                    {'image': 'execute_3_load_suite.png', 'desc': '从下拉框选择已保存的套件，自动勾选对应用例'},
                    {'image': 'execute_4_run.png', 'desc': '点击"执行"按钮开始测试'},
                    {'image': 'execute_5_view_logs.png', 'desc': '左侧日志实时显示执行过程，圆盘统计通过/失败数量'},
                    {'image': 'execute_6_generate_report.png', 'desc': '执行完成后点击"测试报告"，生成 HTML 报告（含截图）'},
                ])}
                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    💡 报告保存在「设置 → 输出目录」指定的位置，文件名包含时间戳。
                </div>
                """,

                "定时计划": f"""
                <h2 style="font-size: 20px; border-bottom: 2px solid; padding-bottom: 6px;">⏰ 定时计划</h2>
                <p style="font-size: 15px;">
                    创建定时任务，在指定时间自动执行测试，适合每日回归、夜间构建。
                </p>
                {self._get_steps_html([
                    {'image': 'task_1_add.png', 'desc': '在"定时计划"区域点击"新增"'},
                    {'image': 'task_2_fill_form.png', 'desc': '填写任务名称、关联套件、执行频率（不重复/每天/每周/每月）'},
                    {'image': 'task_3_set_time.png', 'desc': '设置执行时间和循环次数，选择是否失败停止'},
                    {'image': 'task_4_save.png', 'desc': '点击保存，任务出现在列表中'},
                    {'image': 'task_5_manual_run.png', 'desc': '选中任务点击"执行"可立即触发（不影响下次定时）'},
                    {'image': 'task_6_toggle_disable.png', 'desc': '通过开关启用/禁用任务，禁用后不触发执行'},
                ])}
                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    💡 不重复任务若执行时间已过，会自动禁用。
                </div>
                """
            },

            "🧩 应用元素库": {
                "管理元素": f"""
                <h2 style="font-size: 20px; border-bottom: 2px solid; padding-bottom: 6px;">🧩 应用元素库</h2>
                <p style="font-size: 15px;">
                    统一管理 UI 控件的定位信息，便于在步骤中复用，避免重复输入。
                </p>
                {self._get_steps_html([
                    {'image': 'element_1_view.png', 'desc': '表格显示所有元素（应用、模块、名称、定位方式、定位值）'},
                    {'image': 'element_2_search_filter.png', 'desc': '通过搜索框和应用下拉框快速筛选'},
                    {'image': 'element_3_add.png', 'desc': '点击"新增"，填写信息保存'},
                    {'image': 'element_4_edit.png', 'desc': '选中元素点击"编辑"修改'},
                    {'image': 'element_5_delete.png', 'desc': '选中一个或多个元素点击"删除"，确认后移除'},
                    {'image': 'element_6_verify.png', 'desc': '右键单个元素选择"验证元素"，快速检查当前设备是否存在该控件'},
                    {'image': 'element_7_import_export.png', 'desc': '支持导入/导出 Excel 表格，便于备份、共享与批量维护'},
                ])}
                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    📄 <b>导入 / 导出 Excel</b>：一个所属应用一张工作表（表名即应用名），每张表五列 ——
                    <b>所属模块 / 名称 / 定位方式 / 定位值 / 备注</b>，定位方式可下拉选择<br>
                    • 导入时按「模块 + 名称」匹配：已有元素会覆盖定位信息（步骤里的引用不受影响），没有的新增<br>
                    • 导入<b>只新增和覆盖，不会删除</b>；要删请在界面里选中后删<br>
                    • 缺名称、模块或定位值的行会跳过并逐行提示
                </div>
                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    💡 验证元素需要设备连接，且仅支持资源ID、文本、描述、XPath。
                </div>
                """
            },

            "📱 设备管理": {
                "设备连接与输入法恢复": f"""
                <h2 style="font-size: 20px; border-bottom: 2px solid; padding-bottom: 6px;">📱 设备管理</h2>
                <p style="font-size: 15px;">
                    检测连接的 Android 设备，并提供恢复输入法功能：输入法正常时不做改动，异常时自动切回可用的输入法，解决输入法被篡改导致无法输入文字的问题。
                </p>
                {self._get_steps_html([
                    {'image': 'device_1_view_devices.png', 'desc': '顶部工具栏显示已连接设备序列号，若无设备显示"未检测到设备"'},
                    {'image': 'device_2_refresh.png', 'desc': '点击"刷新"重新扫描 ADB 设备'},
                    {'image': 'device_3_select_device.png', 'desc': '从下拉框选择要使用的设备，自动连接'},
                    {'image': 'device_4_restore_ime.png', 'desc': '设置 → 设备维护 → "恢复输入法"（或快捷键），输入法正常时会提示"无需恢复"'},
                ])}
                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    💡 若恢复失败，可手动在设备设置中切换键盘。
                </div>
                """
            },

            "📦 数据导入 / 导出": {
                "导入导出用例与元素": f"""
                <h2 style="font-size: 20px; border-bottom: 2px solid; padding-bottom: 6px;">📦 数据导入 / 导出</h2>
                <p style="font-size: 15px;">
                    把项目结构、步骤导出为 JSON 文件；<b>元素库</b>与<b>语音用例</b>则是
                    <b>Excel 表格</b>（元素库一个应用一张表、语音用例一个分组一张表），
                    便于备份、迁移、团队共享，以及用 Excel 批量维护。
                </p>
                {self._get_steps_html([
                    {'image': 'import_1_export_cases.png', 'desc': '右上角菜单 → "导出用例"，选择保存位置'},
                    {'image': 'import_2_import_cases.png', 'desc': '右上角菜单 → "导入用例"，选择 JSON 文件（增量导入，不覆盖）'},
                    {'image': 'import_3_export_elements.png', 'desc': '在元素库 Tab 点击"导出"，保存为 Excel 表格'},
                    {'image': 'import_4_import_elements.png', 'desc': '在元素库 Tab 点击"导入"，选择元素库表格（只新增+覆盖，不删除）'},
                ])}
                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    💡 导入用例是增量模式，已存在的项目/模块/用例会跳过，不会覆盖。
                    元素表格与语音用例表格的合并规则见各自章节。
                </div>
                """
            },

            "🔌 接口自动化": {
                "使用说明": """
                <h2 style="font-size: 20px; border-bottom: 2px solid; padding-bottom: 6px;">🔌 接口自动化</h2>
                <p style="font-size: 15px;">
                    对 HTTP 接口做自动化测试：配好接口与环境，一键发送、批量执行、查看断言结果并生成报告。
                    <b>不依赖设备</b>，接口数据独立保存，与 UI 用例互不影响。
                </p>
                <p style="font-size: 15px;">
                    页面分三栏：左侧<b>接口管理</b>（分组树）、中间<b>接口列表</b>（发送 / 执行 / 报告）、
                    右侧<b>详情</b>（上半填参数，下半看响应与断言结果）。
                </p>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    <b>新建接口</b><br>
                    • 左侧分组上右键 →「新建接口」，填<b>名称</b>、<b>请求方法</b>与 <b>URL</b><br>
                    • <b>请求头</b>一行一个，格式 <code>名称: 值</code>；<b>请求体</b>支持 JSON / 表单 / 文本，JSON 写错会当场提示<br>
                    • 点「发送」会先自动保存再发，不会出现改了没保存的情况
                </div>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    <b>执行与结果</b><br>
                    • <b>发送</b>：只跑当前选中接口；<b>执行选中</b>：多选后批量跑，可随时停止<br>
                    • 列表的「结果」列实时显示通过 / 失败与耗时，右下角看响应与断言明细<br>
                    • <b>生成报告</b>：把最近一次执行结果保存为 HTML 报告
                </div>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    💡 左下角可导入 / 导出整份接口配置，导入为增量合并，不会覆盖现有接口。
                </div>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    ⚠️ 目前支持接口管理、环境变量、断言、执行与报告；前后置脚本、链路编排、数据驱动暂未支持。
                </div>
                """,

                "环境变量与断言": """
                <h2 style="font-size: 20px; border-bottom: 2px solid; padding-bottom: 6px;">🌐 环境变量与断言</h2>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    <b>环境</b>（接口列表上方的下拉框 + 「设置」按钮）<br>
                    • 一套环境 = 一个 <b>base_url</b> + 若干自定义变量，变量用 <code>{{名称}}</code> 引用<br>
                    • 接口 URL 一般写成 <code>{{base_url}}/api/user</code>，换环境不用改接口<br>
                    • 变量没定义时请求会直接失败，并提示是哪个变量没定义
                </div>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    <b>断言</b>（右下角表格，一行一条，可勾选启用）<br>
                    • <b>状态码</b>：如期望 <code>200</code><br>
                    • <b>响应取值</b>：填 JSON 路径（如 <code>data.name</code>），支持等于、包含、正则等比较<br>
                    • <b>包含文本</b>：期望文本出现在响应里　<b>正则匹配</b>：按正则匹配响应<br>
                    • <b>耗时</b>：限制接口响应时间（毫秒）
                </div>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    💡 没配断言时，请求发送成功即算通过；要让「返回内容不对」也能被发现，需要配断言。
                </div>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    ⚠️ 为兼容内网自签证书，HTTPS 默认不校验证书；连接生产环境时请注意这一风险。
                </div>
                """
            },

            "⚙️ 性能检测": {
                "使用说明": f"""
                <h2 style="font-size: 20px; border-bottom: 2px solid; padding-bottom: 6px;">⚙️ 性能检测</h2>
                <p style="font-size: 15px;">
                    实时采集被测应用的 <b>CPU、内存、FPS、流量</b> 四项指标，支持「独立监控」和
                    「场景化测试」两种模式；卡片区右下角还有堆转储、抓包两个工具入口。
                </p>

                {self._get_steps_html([
                    {'image': 'perf_1_select_app.png', 'desc': '顶部选择目标应用，点「刷新应用」可重新获取列表'},
                    {'image': 'perf_2_choose_metrics.png', 'desc': '勾选要监控的指标（设备不支持的会置灰）'},
                    {'image': 'perf_3_set_interval.png', 'desc': '设置采样间隔：单项 1 秒即可，多项建议 5 秒以上'},
                    {'image': 'perf_4_start_monitor.png', 'desc': '点「开始监控」，曲线与统计实时刷新'},
                    {'image': 'perf_5_view_charts.png', 'desc': '实时查看各项指标曲线'},
                    {'image': 'perf_6_export_report.png', 'desc': '完成后可保存基线、导出 CSV、生成报告'},
                ])}

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    📊 <b>指标说明</b><br>
                    • <b>CPU</b>：多核累计（8 核设备上限 800%）　<b>内存</b>：应用主进程占用<br>
                    • <b>FPS</b>：每秒渲染帧数　<b>流量</b>：应用网络速率（KB/s）
                </div>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    💡 采集本身会占用设备资源：勾选越多、间隔越短，数据越不可信。
                    推荐单项 1 秒 / 两项 2 秒 / 三项以上 5 秒 / 含流量 10 秒。
                </div>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    ⚠️ 某项指标一直为 0 或不变时，多为设备不支持，可换设备或降低采样频率验证。
                </div>
                """,

                "场景化模式": f"""
                <h2 style="font-size: 20px; border-bottom: 2px solid; padding-bottom: 6px;">🎬 场景化模式</h2>
                <p style="font-size: 15px;">
                    场景化模式下，性能采集与用例执行<b>同步进行</b>：执行指定套件期间持续采集性能数据，
                    方便定位性能问题出现在哪一步。
                </p>

                {self._get_steps_html([
                    {'image': 'perf_scenario_1_select_app.png', 'desc': '选择目标应用并保持在前后台'},
                    {'image': 'perf_scenario_2_choose_suite.png', 'desc': '模式切换为「场景化测试」，选择套件'},
                    {'image': 'perf_scenario_3_set_loop.png', 'desc': '设置循环次数（建议 1~3 次），按需勾选「失败停止」'},
                    {'image': 'perf_scenario_4_start.png', 'desc': '点「开始监控」，采集与执行同时进行'},
                    {'image': 'perf_scenario_5_view_logs.png', 'desc': '左侧看执行进度，曲线区看性能数据'},
                ])}

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    ⚠️ 场景化模式的性能数据包含自动化操作开销，与独立监控（手动操作）的数据不宜直接对比。
                </div>
                """
            },

            "🎤 语音播报": {
                "使用说明": f"""
                <h2 style="font-size: 20px; border-bottom: 2px solid; padding-bottom: 6px;">🎤 语音播报</h2>
                <p style="font-size: 15px;">
                    语音播报用来测试车机语音助手：电脑扬声器播放文案，车机麦克风拾音后交给它自己的语音助手处理，
                    无需与车机做任何对接。配置好<b>播什么</b>、<b>等多久</b>、<b>怎么判断听清没有</b>即可。
                </p>
                <p style="font-size: 15px;">
                    页面分三栏：左侧<b>语音管理</b>（语音用例库）、中间<b>用例步骤</b>（文案与检测步骤）、
                    右侧<b>执行</b>（勾选用例、设置语速与循环）。
                </p>

                {self._get_steps_html([
                    {'image': 'voice_1_manage.png', 'desc': '左侧：右键分组 / 用例可新建、复制、重命名、删除'},
                    {'image': 'voice_2_phrases.png', 'desc': '中间：增删改文案，每行可设「播后等待」，可单条试播'},
                    {'image': 'voice_3_execute.png', 'desc': '右侧：勾选用例（勾分组会带全组），设置语速与循环'},
                ])}

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    <b>两类步骤</b><br>
                    • <b>播报步骤</b>：要念的文案；「播后等待」是这句播完后再等的秒数，给车机反应时间，
                    可按文案长度一键估算，也可点「批量重算」补齐全部用例<br>
                    • <b>检测步骤</b>：点「+ 添加检测」添加，填期望出现在车机回话里的关键词，
                    用来判断这句话有没有被正确识别；没配检测的播报不做验证
                </div>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    💡 语音用例与「自动化编辑」里的用例相互独立，互不影响。
                </div>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    📄 <b>导入 / 导出 Excel</b>（「语音管理 ▾」菜单）：一个分组一张工作表，
                    每张表三列 —— <b>用例名称 / 操作步骤 / 预期结果</b>，一行一个用例，步骤和预期带序号
                    （如 <code>1. 你好本田</code>），预期序号对应第几条步骤<br>
                    • 导入按表名匹配分组（没有就新建），同名用例覆盖其步骤；格式不对的行会跳过并提示<br>
                    • 「播后等待」不在表格里，导入后点一次「批量重算」即可补齐
                </div>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    ⚠️ 执行期间请保持车机语音助手可被唤醒、电脑扬声器音量足够大。
                </div>
                """,

                "唤醒词": f"""
                <h2 style="font-size: 20px; border-bottom: 2px solid; padding-bottom: 6px;">🗣️ 唤醒词</h2>
                <p style="font-size: 15px;">
                    车机助手要先被唤醒才听得进指令，用例开头通常都有一句唤醒词。
                    点中栏标题旁的 <b>「唤醒词」按钮</b>，可把它直接追加到当前用例末尾，省去打字。
                </p>

                {self._get_steps_html([
                    {'image': 'voice_4_wake_word.png', 'desc': '点「唤醒词」追加一条唤醒词文案；未选用例时按钮置灰'},
                    {'image': 'voice_5_wake_word_setting.png', 'desc': '文案在「设置 → 语音设置」里修改，默认「你好虫师」'},
                ])}

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    💡 追加在末尾；想让唤醒词当第一句，可在空用例上先点「唤醒词」再补其他步骤。
                    唤醒词改完立即生效，无需重启。
                </div>
                """,

                "执行与设置": f"""
                <h2 style="font-size: 20px; border-bottom: 2px solid; padding-bottom: 6px;">▶️ 执行与设置</h2>
                <p style="font-size: 15px;">
                    右栏勾选要播的用例，点 <b>「执行选中」</b> 依次播报；播报中点 <b>「停止」</b> 立即打断。
                </p>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    <b>勾选方式</b><br>
                    • 点行内任意位置即可勾选 / 取消；勾选分组会带全组内用例<br>
                    • <b>全选 / 取消全选</b> 一次处理整棵树；<b>循环</b>设几次就整轮播几遍
                </div>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    <b>设置 → 语音设置</b><br>
                    • <b>音色</b>：选择发音人　<b>输出设备</b>：选择扬声器，选错车机听不见<br>
                    • <b>唤醒词</b>：唤醒词按钮追加的文案；改完可点「试听」确认
                </div>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    ⚠️ 音量跟随电脑系统音量，程序内不单独调节。
                </div>
                """,

                "回执验证": f"""
                <h2 style="font-size: 20px; border-bottom: 2px solid; padding-bottom: 6px;">✅ 回执验证</h2>
                <p style="font-size: 15px;">
                    开启回执验证后，每句播报之后虫师会自动检查车机日志，按<b>检测步骤</b>里填的关键词
                    判断这句话有没有被正确识别，结果写在「虫师日志」里。
                </p>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    <b>怎么开</b><br>
                    • 在 <b>「设置 → 回执验证」</b> 打开「启用回执验证」<br>
                    • <b>日志标签</b>：填车机语音助手的日志标签，可填多个（空格或逗号分隔）；
                    留空会抓取全部日志，建议至少填一个<br>
                    • <b>失败关键词</b>：车机说「没听清」这类话术里的词，逗号分隔；
                    预期结果里已写明的词不会误判为失败
                </div>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    💡 只有配了<b>检测步骤</b>的播报才会被验证；预期没命中不会中断执行，只记一条失败日志，最后统一汇总。
                </div>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    💡 不知道日志标签填什么：先留空跑一遍，在「虫师日志」里找到车机回话所在的标签，再填进来，判定会更准。
                </div>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    ⚠️ 「播后等待」要留够时间，太短会在车机回话前就去抓日志（默认值已留余量，车机偏慢可调大）。
                </div>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    ⚠️ 回执验证只判断「有没有被正确识别」，判断不了功能是否真的执行（如地图是否切了全屏）——
                    那请用「自动化编辑」里的断言步骤。
                </div>
                """
            },
        }

    def on_tree_clicked(self, index):
        model = self.tree.model()
        item = model.itemFromIndex(index)
        if item is None:
            return
        parent = item.parent()
        if parent is None:
            if item.rowCount() > 0:
                child = item.child(0)
                self.tree.setCurrentIndex(child.index())
                self.on_tree_clicked(child.index())
            return
        section = parent.text()
        subitem = item.text()
        html = self.help_content.get(section, {}).get(subitem, "<p>内容未找到</p>")

        # 根据当前主题调整 HTML 样式
        theme_mode = Settings.get_theme_mode()
        is_dark = theme_mode == THEME_MODE_DARK

        # 检测壁纸：有壁纸时 HTML body 背景透明，让外层容器/壁纸透出
        import os as _os
        has_wp = False
        try:
            wp_path = Settings.get_wallpaper_path()
            has_wp = bool(wp_path and _os.path.exists(wp_path))
        except Exception:
            pass

        # 主题颜色
        if is_dark:
            bg_color = "transparent" if has_wp else "#2d2d2d"
            text_color = "#eee"
            border_color = "#555"
            link_color = "#90caf9"
            code_bg = "rgba(60, 60, 60, 0.7)" if has_wp else "#3c3c3c"
            tip_bg = "rgba(30, 58, 95, 0.7)" if has_wp else "#1e3a5f"
            tip_border = "#90caf9"
            warning_bg = "rgba(58, 42, 26, 0.7)" if has_wp else "#3a2a1a"
            warning_border = "#ffb74d"
            hr_color = "#555"
        else:
            bg_color = "transparent" if has_wp else "#ffffff"
            text_color = "#333"
            border_color = "#d0d0d0"
            link_color = "#1976d2"
            code_bg = "rgba(244, 244, 244, 0.7)" if has_wp else "#f4f4f4"
            tip_bg = "rgba(227, 242, 253, 0.7)" if has_wp else "#e3f2fd"
            tip_border = "#1976d2"
            warning_bg = "rgba(255, 243, 224, 0.7)" if has_wp else "#fff3e0"
            warning_border = "#ff9800"
            hr_color = "#e0e0e0"

        full_html = f"""
        <html>
        <head>
            <style>
                body {{
                    font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;
                    padding: 20px 24px;
                    margin: 0;
                    background-color: {bg_color};
                    color: {text_color};
                }}
                h2 {{
                    color: {link_color};
                    border-bottom: 2px solid {link_color};
                    padding-bottom: 6px;
                }}
                h3 {{ font-size: 16px; margin-top: 20px; color: {text_color}; }}
                ol, ul {{ padding-left: 20px; }}
                li {{ margin-bottom: 6px; }}
                code {{
                    background: {code_bg};
                    padding: 2px 6px;
                    border-radius: 4px;
                    font-size: 13px;
                    color: {text_color};
                }}
                kbd {{
                    background: {code_bg};
                    padding: 2px 6px;
                    border-radius: 4px;
                    font-size: 13px;
                    border: 1px solid {border_color};
                }}
                .tip {{
                    background: {tip_bg};
                    border-left: 4px solid {tip_border};
                    padding: 12px 16px;
                    margin: 12px 0;
                    border-radius: 4px;
                }}
                .warning {{
                    background: {warning_bg};
                    border-left: 4px solid {warning_border};
                    padding: 12px 16px;
                    margin: 12px 0;
                    border-radius: 4px;
                }}
                .success {{
                    background: #e8f5e9;
                    border-left: 4px solid #4caf50;
                    padding: 12px 16px;
                    margin: 12px 0;
                    border-radius: 4px;
                }}
                .danger {{
                    background: #ffebee;
                    border-left: 4px solid #f44336;
                    padding: 12px 16px;
                    margin: 12px 0;
                    border-radius: 4px;
                }}
                hr {{
                    border: 0.5px solid {hr_color};
                    margin: 20px 0;
                }}
                a {{ color: {link_color}; }}
                div.tip, div.warning {{ color: {text_color}; }}
            </style>
        </head>
        <body>
            {html}
            <hr>
            <p style="font-size: 13px; color: #888; text-align: center;">💡 更多问题，欢迎查阅项目文档或联系开发者。</p>
        </body>
        </html>
        """
        # setHtml 前：把本页预缩放好的图片注册进文档资源表，img 的 src 指向这些资源。
        # 这样 QTextEdit 绘制的是已按显示尺寸 + 屏幕倍率高质量缩好的 1:1 位图，
        # 不再对原图现场缩放（现场缩放无平滑滤波，大图缩小会锯齿发花）。
        doc = self.content.document()
        for key, img in self._help_images.items():
            doc.addResource(QTextDocument.ResourceType.ImageResource, QUrl(key), img)
        self._help_images.clear()
        self.content.setHtml(full_html)
        self.content.update()
        self.content.repaint()