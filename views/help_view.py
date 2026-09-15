# views/help_view.py
import os
import sys
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QSplitter, QTreeView, QApplication, QStyle, QTextEdit, QVBoxLayout
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QStandardItemModel, QStandardItem
from utils.theme import Theme, ThemeMode
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
                background-color: transparent;
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
            /* branch 全部状态统一为透明/选中色，显式指定，让 Qt 不再用 Highlight 画 */
            #HelpTreeContainer QTreeView::branch {{
                background: transparent;
                border: none;
            }}
            #HelpTreeContainer QTreeView::branch:selected,
            #HelpTreeContainer QTreeView::branch:selected:active,
            #HelpTreeContainer QTreeView::branch:selected:!active,
            #HelpTreeContainer QTreeView::branch:selected:focus {{
                background-color: {selected_bg};
                border: none;
                outline: none;
            }}
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
        self.tree.expandAll()
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
            "🔧 ADB工具箱": {
                "功能规划": """
                <h2 style="font-size: 20px; border-bottom: 2px solid; padding-bottom: 6px;">🔧 ADB工具箱</h2>
                <p style="font-size: 15px;">
                    <b>ADB工具箱</b> 是驭虫师即将推出的设备调试模块，用于对 Android 设备执行常用 ADB 命令，
                    覆盖设备管理、应用管理、文件操作、日志抓取、性能诊断等场景。
                </p>

                <h3 style="font-size: 16px; margin-top: 20px;">🎯 规划中的核心能力</h3>
                <ul style="font-size: 14px; line-height: 1.9;">
                    <li><b>设备管理</b>：设备列表刷新、无线联调、重启 / 恢复出厂</li>
                    <li><b>应用管理</b>：安装 / 卸载 / 清除数据 / 强制停止 / 查看组件信息</li>
                    <li><b>文件操作</b>：推送 / 拉取文件或文件夹，实时显示进度</li>
                    <li><b>日志抓取</b>：logcat、内核日志、崩溃日志，支持过滤与保存</li>
                    <li><b>快捷功能</b>：一键截图、录屏、Monkey 测试、投屏（scrcpy）</li>
                    <li><b>性能诊断</b>：内存监控、堆转储、ANR / Crash 分析</li>
                    <li><b>网络工具</b>：网络抓包（tcpdump）、弱网模拟（netem）</li>
                    <li><b>自定义指令</b>：把常用命令保存为快捷按钮，支持导入 / 导出</li>
                </ul>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 16px 0; border-radius: 4px; background: #fff3e0; border-color: #ff9800;">
                    ⏳ <b>当前状态</b>：功能开发中，敬请期待。<br>
                    如需优先支持，欢迎通过「关于」页面联系开发者反馈。
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
                    💡 报告默认保存在 reports/ 目录，文件名包含时间戳。
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
                    {'image': 'element_7_import_export.png', 'desc': '支持导入/导出 JSON 文件，便于备份与共享'},
                ])}
                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    💡 验证元素需要设备连接，且仅支持资源ID、文本、描述、XPath。
                </div>
                """
            },

            "📱 设备管理": {
                "设备连接与输入法恢复": f"""
                <h2 style="font-size: 20px; border-bottom: 2px solid; padding-bottom: 6px;">📱 设备管理</h2>
                <p style="font-size: 15px;">
                    检测连接的 Android 设备，并提供恢复输入法功能，解决输入法被篡改的问题。
                </p>
                {self._get_steps_html([
                    {'image': 'device_1_view_devices.png', 'desc': '顶部工具栏显示已连接设备序列号，若无设备显示"未检测到设备"'},
                    {'image': 'device_2_refresh.png', 'desc': '点击"刷新"重新扫描 ADB 设备'},
                    {'image': 'device_3_select_device.png', 'desc': '从下拉框选择要使用的设备，自动连接'},
                    {'image': 'device_4_restore_ime.png', 'desc': '点击右上角菜单 → "恢复输入法"，切换为 AOSP 键盘'},
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
                    将项目结构、步骤、元素库导出为 JSON 文件，便于备份、迁移或团队共享。
                </p>
                {self._get_steps_html([
                    {'image': 'import_1_export_cases.png', 'desc': '右上角菜单 → "导出用例"，选择保存位置'},
                    {'image': 'import_2_import_cases.png', 'desc': '右上角菜单 → "导入用例"，选择 JSON 文件（增量导入，不覆盖）'},
                    {'image': 'import_3_export_elements.png', 'desc': '在元素库 Tab 点击"导出"，保存元素数据'},
                    {'image': 'import_4_import_elements.png', 'desc': '在元素库 Tab 点击"导入"，选择有效的元素库 JSON 文件'},
                ])}
                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    💡 导入用例是增量模式，已存在的项目/模块/用例会跳过，不会覆盖。
                </div>
                """
            },

            "🔌 接口自动化": {
                "功能规划": """
                <h2 style="font-size: 20px; border-bottom: 2px solid; padding-bottom: 6px;">🔌 接口自动化</h2>
                <p style="font-size: 15px;">
                    <b>接口自动化</b> 是驭虫师即将推出的能力模块，用于对 HTTP / WebSocket / gRPC 等接口进行自动化测试。
                </p>

                <h3 style="font-size: 16px; margin-top: 20px;">🎯 规划中的核心能力</h3>
                <ul style="font-size: 14px; line-height: 1.9;">
                    <li><b>接口管理</b>：按项目 / 模块组织 API，支持路径参数、请求头、Body 模板</li>
                    <li><b>环境变量</b>：多套环境（开发 / 测试 / 生产）一键切换，变量跨接口复用</li>
                    <li><b>前置 / 后置脚本</b>：Python 脚本钩子，处理签名、加解密、数据提取</li>
                    <li><b>断言引擎</b>：状态码、响应体、JSON Path、JSON Schema、响应时间多维断言</li>
                    <li><b>链路场景</b>：接口串联，上一步响应作为下一步入参</li>
                    <li><b>数据驱动</b>：CSV / JSON / Excel 参数化，批量跑数据</li>
                    <li><b>测试报告</b>：与现有 HTML 报告统一风格，含请求 / 响应详情</li>
                </ul>

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 16px 0; border-radius: 4px; background: #fff3e0; border-color: #ff9800;">
                    ⏳ <b>当前状态</b>：功能开发中，敬请期待。<br>
                    如需优先支持，欢迎通过"关于"页面联系开发者反馈。
                </div>
                """
            },

            "⚙️ 性能检测": {
                "使用说明": f"""
                <h2 style="font-size: 20px; border-bottom: 2px solid; padding-bottom: 6px;">⚙️ 性能检测</h2>
                <p style="font-size: 15px;">
                    <b>性能检测</b> 通过 ADB 实时采集被测应用的核心指标，包括
                    <b>CPU、内存、FPS、卡顿、流量</b>。支持「独立监控」和「场景化测试」两种模式。
                </p>

                {self._get_steps_html([
                    {'image': 'perf_1_select_app.png', 'desc': '顶部下拉选择目标应用，点「刷新应用」可重新拉取列表'},
                    {'image': 'perf_2_choose_metrics.png', 'desc': '勾选需要监控的指标，支持多选；设备不支持的指标会置灰'},
                    {'image': 'perf_3_set_interval.png', 'desc': '设置采样间隔：单项 1 秒即可，多项建议 ≥ 5 秒'},
                    {'image': 'perf_4_start_monitor.png', 'desc': '点「开始监控」，曲线与统计数据实时刷新'},
                    {'image': 'perf_5_view_charts.png', 'desc': '实时查看 CPU / 内存 / FPS / 流量 / 卡顿曲线'},
                    {'image': 'perf_6_export_report.png', 'desc': '采集完成后可保存基线、导出 CSV、生成性能报告'},
                ])}

                <div style="border-left: 4px solid; padding: 12px 16px; margin: 12px 0; border-radius: 4px;">
                    📊 <b>指标口径</b><br>
                    • <b>CPU</b>：多核累计，8 核设备上限 800%（单核满载 = 100%）<br>
                    • <b>内存</b>：主进程 PSS，不含子进程<br>
                    • <b>FPS / 卡顿</b>：基于 gfxinfo 渲染帧数差分<br>
                    • <b>流量</b>：按 UID 汇总，展示为速率（KB/s）
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