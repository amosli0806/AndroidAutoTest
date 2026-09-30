# views/help_view.py
import os
import sys
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QSplitter, QTreeView, QApplication, QStyle, QTextEdit, QVBoxLayout
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QStandardItemModel, QStandardItem
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
    def _get_image_html(self, filename, desc="示例图片", max_height="400px"):
        """生成图片 HTML（左对齐，有截图时无背景，占位图时有背景）"""
        if getattr(sys, 'frozen', False):
            base_dir = sys._MEIPASS
        else:
            base_dir = os.path.dirname(os.path.dirname(__file__))
        img_path = os.path.join(base_dir, "resources", "images", "help", filename)

        if os.path.exists(img_path):
            file_url = f"file:///{img_path.replace(os.sep, '/')}"
            return f'''
            <div style="margin: 12px 0; text-align: left; background: transparent; border-radius: 0; padding: 0;">
                <img src="{file_url}" alt="{desc}" 
                     style="max-width: 80%; max-height: {max_height}; width: auto; height: auto; 
                            border-radius: 6px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);
                            display: inline-block;">
            </div>
            '''
        else:
            return f'''
            <div style="border: 2px dashed #d0d0d0; border-radius: 8px; padding: 30px 20px; margin: 12px 0; text-align: left; background: #fafafa; color: #999; font-size: 14px;">
                🖼️ {desc}<br>
                <span style="font-size: 12px; color: #bbb;">请将截图放置于 resources/images/help/{filename}</span>
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
                    虫师通过 <b>adb</b>（Android Debug Bridge）与安卓设备通信，需要电脑上
                    已安装 adb 并加入 <b>系统 PATH</b>。未安装时启动虫师会提示
                    「未检测到 adb」，此时无法连接设备。
                </p>

                {self._get_steps_html([
                    {'image': '', 'desc': '① 下载 Google 官方 platform-tools：https://developer.android.com/tools/releases/platform-tools（国内可搜「platform-tools 下载」选可靠镜像）'},
                    {'image': '', 'desc': '② 解压到任意目录，例如 D:\\platform-tools（内含 adb.exe）'},
                    {'image': '', 'desc': '③ 把该目录加入系统 PATH：此电脑右键 → 属性 → 高级系统设置 → 环境变量 → 选中 Path → 编辑 → 新建 → 填入目录路径'},
                    {'image': '', 'desc': '④ 验证：打开新的命令行窗口，输入 adb version，能显示版本号即成功'},
                    {'image': '', 'desc': '⑤ 重启虫师，顶部设备下拉框应能发现已连接的设备'},
                ])}

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    💡 <b>手机端</b>还需开启「开发者选项 → USB 调试」；车机/电视设备请在系统设置里找到
                    开发者选项开启 adb 调试。首次连接设备时，设备上会弹「是否允许 USB 调试」授权框，点允许。
                </div>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    ⚠️ 无线设备（adb connect ip:port）连接后若提示 offline，多为设备端未授权或网络不通，
                    可在设备上重新授权或改用 USB 连接。
                </div>
                """,
            },
                        "🪟 窗口与界面": {
                "迷你模式": """
                <h2 style="font-size: 20px; border-bottom: 2px solid; padding-bottom: 6px;">🪟 迷你模式</h2>
                <p style="font-size: 15px;">
                    屏幕不够用时，可以把虫师缩成一个<b>只显示 ADB 指令管理区</b>的小窗口：点左侧工具栏
                    最底部的<b>迷你模式按钮</b>进入，再点一次恢复全屏。
                </p>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    <b>迷你窗口保留什么</b><br>
                    • 顶部：设备下拉框 + 刷新按钮<br>
                    • 主区域：ADB 工具箱的「指令管理」区（右侧的搜索 / 弱网模拟 / Monkey 隐藏）<br>
                    • 右侧工具栏：消息按钮（帮助中心隐藏）<br>
                    • 左下：硬件信息 / Crash / ANR / 日志四个按钮，仍可调出底部面板<br>
                    • 底部状态栏保留
                </div>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    💡 迷你窗口是<b>固定尺寸</b>的，所以标题栏的最大化按钮会一并隐藏 ——
                    避免误点后窗口又铺满全屏。退出迷你模式会自动恢复最大化与全部功能入口。
                </div>
                """
            },
                        "🔧 ADB工具箱": {
                "使用说明": f"""
                <h2 style="font-size: 20px; border-bottom: 2px solid; padding-bottom: 6px;">🔧 ADB工具箱</h2>
                <p style="font-size: 15px;">
                    <b>ADB工具箱</b> 集成了常用 ADB 命令与设备调试工具，
                    覆盖<b>设备管理、指令执行、文件操作、日志抓取、网络诊断</b>等场景。
                </p>
                <p style="font-size: 15px;">
                    页面分左右两栏：左侧是<b>指令管理</b>（命令列表 + 菜单 + 执行选中），
                    右侧自上而下是<b>搜索框</b>、<b>弱网模拟</b>、<b>Monkey 测试</b>。
                    设备相关的常用工具已按用途分散到顶部工具栏、左侧工具栏和性能检测页，
                    详见「快捷功能」一节。
                </p>

                {self._get_steps_html([
                    {'image': 'adb_2_search.png', 'desc': '右侧搜索框支持预设、自定义、ADB 库三类命令'},
                    {'image': 'adb_3_cmdlist.png', 'desc': '左侧命令列表：勾选 + 执行/停止，每条命令独立控制'},
                    {'image': 'adb_4_quick.png', 'desc': '搜索框下方内嵌「弱网模拟」「Monkey 测试」两块面板（弱网在上）'},
                    {'image': 'adb_5_logs.png', 'desc': '结果统一输出到主窗口底部「虫师日志」面板'},
                ])}

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    💡 <b>自定义指令</b>：通过「菜单 → 新增指令」保存常用命令，支持定时执行、循环、保存输出等。
                </div>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    ⚠️ 主项目正在执行用例时，会改动设备状态的命令（权限获取 / monkey / tcpdump / reboot 等）会被自动禁用，避免干扰测试；
                    而 logcat、录屏、截图、导出日志（pull）这类<b>只读采集</b>命令仍可正常执行 —— 用例跑到一半出问题，正好用它把现场日志和视频捞出来。
                </div>
                """,

                "快捷功能": f"""
                <h2 style="font-size: 20px; border-bottom: 2px solid; padding-bottom: 6px;">🛠️ 快捷功能</h2>
                <p style="font-size: 15px;">
                    常用工具按用途分布在三处：<b>顶部工具栏</b>、<b>左侧工具栏下方</b>、
                    <b>性能检测页的「性能工具」卡片</b>，以及 ADB 工具箱右栏的<b>内嵌面板</b>。
                </p>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    <b>顶部工具栏</b>（左侧为设备选择 / 刷新）<br>
                    • 分割线右侧（左对齐）：<b>无线</b>、<b>投屏</b>（scrcpy 镜像，支持参数调节与同步录屏）<br>
                    • 工具栏最右侧（与菜单按钮同组，只显示图标）：<b>安装</b>（APK 安装并智能解析失败原因）、
                    <b>推送</b>（push 文件/文件夹，实时进度）、<b>MD5</b>（计算 APK 的 MD5 值）<br>
                    • 图标按钮的用途看悬浮提示，提示里带对应快捷键
                </div>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    <b>左侧工具栏下方</b>（只显示图标，点击后内容显示在底部面板）<br>
                    • <b>硬件信息</b>：分辨率 / 屏幕密度 / 安卓版本<br>
                    • <b>Crash 日志</b>：拉取 logcat -b crash，完整输出不限行数<br>
                    • <b>ANR 日志</b>：拉取 /data/anr/ 最新一份并解析；设备未 root 时给出提示文案<br>
                    • <b>日志</b>：虫师日志<br>
                    这四者是互斥开关：再点一次当前按钮收起面板
                </div>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    <b>ADB 工具箱右栏内嵌面板</b><br>
                    • <b>弱网模拟</b>：tc netem 模拟延迟 / 丢包 / 带宽限制，需设备已 root；
                    应用/清除的结果会输出到「虫师日志」<br>
                    • <b>Monkey 测试</b>：图形化配置事件数量、随机种子、包名与各类事件比例
                </div>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    <b>性能检测页 · 性能工具卡片</b><br>
                    • <b>堆转储</b>：选择应用 → dump hprof 到本地<br>
                    • <b>抓包</b>：tcpdump 抓取流量到 pcap 文件（Wireshark 可打开）
                </div>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    ⚠️ 主项目正在执行用例时，会改动设备状态的命令（权限获取 / monkey / tcpdump / reboot 等）会被自动禁用，避免干扰测试；
                    而 logcat、录屏、截图、导出日志（pull）这类<b>只读采集</b>命令仍可正常执行 —— 用例跑到一半出问题，正好用它把现场日志和视频捞出来。
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
                    {'image': 'step_2_drag_sort.png', 'desc': '拖拽卡片左侧的六个点图标可调整顺序'},
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
                    {'image': 'record_3_start_recording.png', 'desc': '点击步骤列表上方的红色圆点开始录制（按钮变红闪烁）'},
                    {'image': 'record_4_perform_actions.png', 'desc': '在设备上执行点击、双击、长按、滑动等操作'},
                    {'image': 'record_5_stop_generate.png', 'desc': '再次点击红色圆点停止录制，系统自动生成步骤并添加到用例中'},
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
                    📄 <b>导入 / 导出元素表格</b>：<b>一个所属应用一张工作表</b>，表名即应用名
                    （所以表里不再重复放应用列，要换应用就把整行挪到另一张表）。每张表五列 ——
                    <b>所属模块 / 名称 / 定位方式 / 定位值 / 备注</b>，「定位方式」是下拉，只能选
                    资源ID、坐标、文本、描述、XPath 之一<br>
                    • 导入时按「<b>所属模块 + 名称</b>」匹配本应用下的元素：命中就<b>覆盖</b>它的
                    定位方式 / 定位值 / 备注（<b>保留内部 id</b>，步骤里的引用不会失效），
                    没命中才新增<br>
                    • 导入<b>只新增 + 覆盖，不删除</b>：表格里少一行不代表要删元素。要删请在界面里
                    选中后删（那边会检查引用）<br>
                    • 不合格的行（没名称 / 没模块 / 没定位值 / 定位方式写错）会被跳过并逐行提示
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
                    <b>接口自动化</b> 用来对 HTTP 接口做自动化测试：配好接口与环境，一键发送、
                    批量跑、看断言结果、出报告。它<b>不依赖设备</b>，连不连车机都能用，
                    接口数据单独存在 <code>data/api_data.json</code>，与 UI 用例互不影响。
                </p>
                <p style="font-size: 15px;">
                    页面分三栏：左侧<b>接口管理</b>（分组 → 接口的导航树）、中间<b>接口列表</b>
                    （当前分组的接口 + 环境选择 + 发送 / 执行选中 / 生成报告）、
                    右侧<b>详情</b>（上半填请求参数，下半看响应与断言结果）。
                </p>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    <b>新建接口</b><br>
                    • 在左侧分组上右键 →「在此分组新建接口」，或中间列表里右键 →「新建接口」<br>
                    • 填<b>名称</b>、<b>方法</b>（GET / POST / PUT / DELETE / PATCH / HEAD）与 <b>URL</b><br>
                    • <b>请求头</b>一行一个，格式 <code>名称: 值</code>（空行和 <code>#</code> 开头会被忽略）<br>
                    • <b>请求体</b>先选类型：JSON / 表单 / 原始文本。选 JSON 时会校验合法性，写错会当场提示<br>
                    • 右侧改完点 <b>「保存」</b>；点 <b>「发送」会先自动保存再发</b> ——
                    所见即所跑，不会出现"改了没保存、跑的还是旧参数"
                </div>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    <b>执行与结果</b><br>
                    • <b>发送</b>：只跑当前选中的这一个接口<br>
                    • <b>执行选中</b>：在中间列表里 Ctrl / Shift 多选后批量跑，跑的过程可以点「停止」<br>
                    • 中间列表的<b>「结果」列</b>实时刷新（通过 / 失败 + 耗时），右下角看响应体与断言明细<br>
                    • <b>生成报告</b>：把最近一次执行结果出成 HTML，存到「设置 → 输出目录」，
                    含每条接口的请求 / 响应与断言明细（长文本折叠显示）
                </div>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    💡 左下角可以<b>导入 / 导出</b>整份接口配置（分组 + 接口 + 环境）。
                    导入是增量合并、同名跳过，不会覆盖你现有的接口。
                </div>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    ⚠️ 本轮实现的是<b>最小闭环</b>：接口管理 / 环境变量 / 断言 / 执行 / 报告。
                    前后置脚本、链路场景编排（上一步响应喂给下一步）、数据驱动参数化<b>尚未支持</b>。
                </div>
                """,

                "环境变量与断言": """
                <h2 style="font-size: 20px; border-bottom: 2px solid; padding-bottom: 6px;">🌐 环境变量与断言</h2>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    <b>环境</b>（中间列表上方的下拉框 + 「设置」按钮）<br>
                    • 一套环境 = 一个 <b>base_url</b> + 若干自定义变量<br>
                    • 变量写法 <code>{{名称}}</code>，可以用在 <b>URL / 请求头 / 请求体</b> 里<br>
                    • <code>{{base_url}}</code> 是内置的，取当前环境的 base_url，所以接口 URL 通常写成
                    <code>{{base_url}}/api/user</code>，换环境不用改接口<br>
                    • 变量<b>没定义</b>时请求会直接失败，并告诉你是哪个变量没定义 —— 不会拿半截 URL 硬发
                </div>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    <b>断言</b>：右下请求区底部的表格，一行一条，可勾选启用 / 停用<br>
                    • <b>状态码</b>：期望值填 <code>200</code>；操作符支持 等于 / 不等于 / 大于 / 小于<br>
                    • <b>响应取值</b>：表达式填 JSON 路径，如 <code>data.name</code>、
                    <code>data.list[0].id</code>、<code>$.a.b</code>；操作符支持
                    等于 / 不等于 / 包含 / 不包含 / 正则匹配<br>
                    • <b>包含文本</b>：期望值填要出现在响应体里的文本<br>
                    • <b>正则匹配</b>：期望值填正则表达式<br>
                    • <b>耗时(毫秒)</b>：操作符用 小于 / 大于，期望值填毫秒数（如 <code>800</code>）<br>
                    • 操作符「正则匹配」和类型「正则匹配」不是一回事：前者是拿正则去比取值结果，
                    后者是拿正则去扫整个响应体
                </div>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    💡 一个接口<b>没配任何断言</b>时，只要请求成功发出就算通过（HTTP 层面无异常）。
                    要让"返回内容不对"也能被抓住，就得配断言。
                </div>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    ⚠️ 为兼容内网常见的自签证书，HTTPS <b>默认不校验证书</b>。
                    如果要拿它连生产环境，请把 <code>services/api_service.py</code> 里的
                    <code>VERIFY_SSL</code> 改成 <code>True</code>。
                </div>
                """
            },

            "⚙️ 性能检测": {
                "使用说明": f"""
                <h2 style="font-size: 20px; border-bottom: 2px solid; padding-bottom: 6px;">⚙️ 性能检测</h2>
                <p style="font-size: 15px;">
                    <b>性能检测</b> 通过 ADB 实时采集被测应用的核心指标，包括
                    <b>CPU、内存、FPS、流量</b>（原「卡顿」指标已下线）。支持「独立监控」和「场景化测试」两种模式。
                    卡片区最后一行是<b>性能工具</b>卡片，提供「堆转储」「抓包」入口。
                </p>

                {self._get_steps_html([
                    {'image': 'perf_1_select_app.png', 'desc': '顶部下拉选择目标应用，点「刷新应用」可重新拉取列表'},
                    {'image': 'perf_2_choose_metrics.png', 'desc': '勾选需要监控的指标，支持多选；设备不支持的指标会置灰'},
                    {'image': 'perf_3_set_interval.png', 'desc': '设置采样间隔：单项 1 秒即可，多项建议 ≥ 5 秒'},
                    {'image': 'perf_4_start_monitor.png', 'desc': '点「开始监控」，曲线与统计数据实时刷新'},
                    {'image': 'perf_5_view_charts.png', 'desc': '实时查看 CPU / 内存 / FPS / 流量曲线'},
                    {'image': 'perf_6_export_report.png', 'desc': '采集完成后可保存基线、导出 CSV、生成性能报告'},
                ])}

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    📊 <b>指标口径</b><br>
                    • <b>CPU</b>：多核累计，8 核设备上限 800%（单核满载 = 100%）<br>
                    • <b>内存</b>：主进程 PSS，不含子进程<br>
                    • <b>FPS</b>：基于 gfxinfo 渲染帧数差分<br>
                    • <b>流量</b>：按 UID 汇总，展示为速率（KB/s）<br>
                    • <b>性能工具</b>：卡片区右下角提供「堆转储 / 抓包」入口
                </div>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    💡 采集命令本身会占用设备资源，勾选越多、间隔越短，被测数据越不可信。
                    推荐：单项 1 秒 / 两项 2 秒 / 三项以上 5 秒 / 含流量 10 秒。
                </div>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    ⚠️ 某项指标持续为 0 或恒定不变时，多为命令输出格式与解析不匹配。
                    可在 PC 端执行 <code>adb shell &lt;对应命令&gt;</code> 查看原始输出。
                </div>
                """,

                "场景化模式": f"""
                <h2 style="font-size: 20px; border-bottom: 2px solid; padding-bottom: 6px;">🎬 场景化模式</h2>
                <p style="font-size: 15px;">
                    在「场景化测试」模式下，性能采集与用例执行 <b>同步进行</b>：
                    指定一个测试套件，执行期间持续采集性能数据，采集结束后将会话与应用场景关联，
                    便于定位哪一步导致性能下降。
                </p>

                {self._get_steps_html([
                    {'image': 'perf_scenario_1_select_app.png', 'desc': '选择目标应用，启动并保持在前台'},
                    {'image': 'perf_scenario_2_choose_suite.png', 'desc': '模式切换为「场景化测试」，选择一个套件'},
                    {'image': 'perf_scenario_3_set_loop.png', 'desc': '设置循环次数（建议 1~3 次），按需勾选「失败停止」'},
                    {'image': 'perf_scenario_4_start.png', 'desc': '点「开始监控」，采集与用例执行同时进行'},
                    {'image': 'perf_scenario_5_view_logs.png', 'desc': '左侧日志实时显示用例执行进度，曲线同步展示性能数据'},
                ])}

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    ⚠️ 场景化模式下的性能数据包含用例执行开销（点击、滑动、截图等），
                    与独立监控模式（用户手动操作）的基线不具直接可比性。
                </div>
                """
            },

            "🎤 语音播报": {
                "使用说明": f"""
                <h2 style="font-size: 20px; border-bottom: 2px solid; padding-bottom: 6px;">🎤 语音播报</h2>
                <p style="font-size: 15px;">
                    <b>语音播报</b> 做的是<b>声学耦合</b>：电脑扬声器把文案念出来，车机麦克风拾音后
                    交给它自己的语音助手。所以这页要解决的是<b>「声音从哪个扬声器出去」</b>、
                    <b>「每句之间等多久」</b>，以及<b>「车机到底听清没有」</b>，不跟车机做任何协议对接。
                </p>
                <p style="font-size: 15px;">
                    页面分三栏：左侧<b>语音管理</b>（独立的语音用例库）、中间<b>用例步骤</b>
                    （该用例的文案与检测步骤）、右侧<b>执行</b>
                    （勾选要播的用例 + 语速 / 循环 / 执行选中 / 停止）。
                </p>

                {self._get_steps_html([
                    {'image': 'voice_1_manage.png', 'desc': '左侧语音管理：右键分组/用例可新建、复制、重命名、删除'},
                    {'image': 'voice_2_phrases.png', 'desc': '中间用例步骤：增删改文案，每行可设「播后等待」，可单条播报'},
                    {'image': 'voice_3_execute.png', 'desc': '右侧执行：勾选要播的用例（分组勾选会级联），设置语速与循环'},
                ])}

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    <b>两类步骤</b><br>
                    • <b>播报步骤</b>：要念的文案 + 「播后等待」（这句播完到下一句之间等多久）。
                    等待时间可按语速一键估算；「批量重算」则按语速重算<b>全部用例</b>的等待时间
                    （表格导入后一次性补齐用）；每条步骤还能一键复制<br>
                    • <b>检测步骤</b>（预期结果）：点中栏右上角的 <b>「+ 添加检测」</b> 加一条，
                    填期望在车机日志里出现的关键词。它<b>永远跟着紧挨在它前面的那句播报</b>，
                    播完就按它判定这句有没有被正确识别。没配检测步骤的播报<b>不做任何验证</b>
                </div>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    💡 <b>语音用例与「自动化编辑」里的用例是两套独立的东西</b>：那边是 App 操作序列
                    （点击 / 输入 / 断言…），这里是纯播报脚本，互不影响。
                </div>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    📄 <b>导入 / 导出用例表格</b>（「语音管理 ▾」菜单）走 Excel：<b>一个分组一张工作表</b>，
                    表名即分组名；每张表三列 —— <b>用例名称 / 操作步骤 / 预期结果</b>，一个用例占一行<br>
                    • 操作步骤、预期结果各写在一个单元格里，<b>一行一条、带序号</b>
                    （如 <code>1. 你好本田</code>），单元格内换行<br>
                    • <b>预期的序号就是它对应的步骤号</b>：<code>2. 正在为您切换为全屏地图</code>
                    表示它是第 2 条操作步骤的预期；某条步骤没有预期，它就不出现在预期列里<br>
                    • 一句播报配多条预期时，预期列会出现多行<b>相同序号</b>（各自算一条独立预期）<br>
                    • 导入时<b>按表名匹配分组</b>（没有就新建），组内<b>同名用例覆盖其步骤</b>，
                    没有的按新用例建；空行 / 没写用例名 / 没有操作步骤的行会被跳过并提示<br>
                    • 注意：<b>「播后等待」不在表格里</b>，导入后点一次「批量重算」即可按语速
                    一次性补齐全部用例的等待时间
                </div>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    ⚠️ 执行期间请保持车机语音助手处于可被唤醒的状态，并让电脑扬声器音量足够大 ——
                    车机那边的识别结果取决于拾音质量，与文案本身是否正确无关。
                </div>
                """,

                "唤醒词": f"""
                <h2 style="font-size: 20px; border-bottom: 2px solid; padding-bottom: 6px;">🗣️ 唤醒词</h2>
                <p style="font-size: 15px;">
                    车机助手要先被叫醒才听得进后面的指令，所以每个用例开头通常都有一句唤醒词。
                    中间栏标题旁的 <b>「唤醒词」按钮</b> 就是为省打字准备的：点一下，把它<b>追加到当前用例末尾</b>。
                </p>

                {self._get_steps_html([
                    {'image': 'voice_4_wake_word.png', 'desc': '点「唤醒词」追加一条唤醒词文案；未选用例时按钮置灰'},
                    {'image': 'voice_5_wake_word_setting.png', 'desc': '文案在「设置 → 语音设置」里改，默认「你好虫师」'},
                ])}

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    💡 追加是<b>加在末尾</b>的。想让唤醒词当第一句，就在空用例上先点「唤醒词」，
                    再用「+ 添加步骤」补后面的指令。
                </div>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    💡 唤醒词在<b>「设置 → 语音设置」</b>页面配置，<b>留空则用默认的「你好虫师」</b>；
                    在设置里改完立刻生效，不用重启。
                </div>
                """,

                "执行与设置": f"""
                <h2 style="font-size: 20px; border-bottom: 2px solid; padding-bottom: 6px;">▶️ 执行与设置</h2>
                <p style="font-size: 15px;">
                    右栏勾选要播的用例后点 <b>「执行选中」</b>，会用配置好的音色和输出设备依次播报；
                    播报中 <b>「停止」</b> 会立刻打断当前这一句，不用等它念完。
                </p>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    <b>勾选方式</b><br>
                    • <b>点行内任意位置</b>即可勾选 / 取消，不用对准那个小方框<br>
                    • 勾选<b>分组</b>会级联到组内所有用例；组内只勾了一部分时，分组显示为部分选中<br>
                    • <b>「全选」/「取消全选」</b> 一次处理整棵树<br>
                    • <b>循环</b>设几次就整轮播几遍，<b>语速</b>与用例里的语音步骤共用
                </div>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    <b>设置 → 语音设置</b><br>
                    • <b>音色</b>：引擎里可用的发音人（含在线音色库与方言）<br>
                    • <b>输出设备</b>：声音从哪个扬声器 / 声卡出去，<b>选错车机就完全听不见</b><br>
                    • <b>唤醒词</b>：上面那个按钮追加的文案<br>
                    • 改完可点「试听」确认车机那边真能听见
                </div>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    ⚠️ 响度直接用电脑的系统音量，程序内不单独调音量。
                </div>
                """,

                "回执验证": f"""
                <h2 style="font-size: 20px; border-bottom: 2px solid; padding-bottom: 6px;">✅ 回执验证</h2>
                <p style="font-size: 15px;">
                    车机到底听清没有，以前只能靠人耳一句句判断。开启<b>回执验证</b>后，虫师会在每句播报
                    之后抓一次车机 logcat，按<b>检测步骤</b>里填的关键词判定这句有没有被正确识别；
                    没命中会在「虫师日志」里写一行结论 —— 报「本次抓取了多少行日志」，
                    <b>不会把车机日志原文贴进面板</b>（原文带着时间戳和 PID，大多是无关内容，只会刷屏）。
                </p>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    <b>怎么开</b><br>
                    • 在 <b>「设置 → 回执验证」</b> 打开「启用回执验证」<br>
                    • <b>日志标签</b>：填车机语音助手打日志用的 tag，<b>可以填多个</b>
                    （空格或逗号分隔，如 <code>TtsBusinessManager TestManager</code>）。
                    留空则抓全量日志 —— 量大，而且别的应用打出的日志可能碰巧命中关键词，建议至少填一个<br>
                    • <b>失败关键词</b>：车机说「没听清」这类失败话术里会出现的词，逗号分隔
                </div>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    💡 <b>开关只是总闸</b>：真正决定「验不验」的是这条播报后面有没有检测步骤 ——
                    没配检测步骤的播报一律不验证。
                </div>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    💡 <b>预期结果没命中不会中断执行</b>：只记一行 ❌ 日志，后面的步骤和用例照常跑完，
                    收尾再汇总成一句「共 N 条预期结果未命中」—— 多条用例连跑时不会因为某一条没命中
                    就把剩下的全掐掉。只有点「停止」或播报本身出错（如超时）才会中断。
                </div>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    💡 <b>日志标签怎么找</b>：先留空抓全量跑一遍，在「虫师日志」里看车机那句反馈文案
                    出现在哪个 tag 下面，再把那个 tag 填进来，日志会干净很多、判定也更准。
                </div>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    ⚠️ <b>「播后等待」要留够时间</b>：车机从听到指令到回话通常要好几秒，
                    等待太短就会在车机回话之前去抓日志，自然什么都抓不到。带检测步骤的那句建议调到 5~8 秒以上。
                </div>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    ⚠️ 回执验证只能判断<b>「这句话有没有被正确识别」</b>，判断不了<b>「是否真的执行了」</b>
                    （比如地图有没有真的切成全屏）—— 后者请用「自动化编辑」里的断言步骤。
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
        self.content.setHtml(full_html)
        self.content.update()
        self.content.repaint()