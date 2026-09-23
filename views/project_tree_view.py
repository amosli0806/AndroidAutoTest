# views/project_tree_view.py
from PyQt6.QtWidgets import QTreeView, QMenu, QMessageBox, QInputDialog, QAbstractItemView, QLabel
from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtGui import QStandardItemModel, QStandardItem
import qtawesome as qta
from models.project_model import TreeNode, ProjectModel, new_node_id
from utils import tree_state
from utils.dialogs import InputDialog, ConfirmDeleteDialog, WarningDialog
from utils.theme import Theme, ThemeMode
from utils.settings import Settings, THEME_MODE_DARK

# 树节点图标：与「语音播报」页的分组/用例图标保持同一套（同族图标 + 同色），
# 那边是 fa6s.folder #f0b429 / fa6s.comment-dots #8a9099，两页放一起看才是一套东西。
# 原来走 IconManager 的 Qt 标准图标（SP_DirClosedIcon 等），是系统风格的蓝色文件夹。
ICON_COLOR_GROUP = "#f0b429"    # 琥珀：项目 / 功能模块
ICON_COLOR_CASE = "#8a9099"     # 中性灰：用例


class ProjectTreeView(QTreeView):
    context_menu_signal = pyqtSignal(str, str)  # node_id, action
    create_node_signal = pyqtSignal(str, str, str)  # parent_id, type, name
    copy_case_signal = pyqtSignal(str, str)  # src_case_id, dst_case_id
    batch_delete_cases_signal = pyqtSignal(list)  # node_id list
    # 新增：复制节点信号（src_node_id, parent_id，parent_id为None表示根）
    copy_node_signal = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ProjectTreeView")
        self.model_obj = None
        self.setHeaderHidden(True)
        self.setIndentation(20)
        # 移除硬编码样式，由主题控制
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)
        self.setModel(QStandardItemModel())
        self.setSizePolicy(self.sizePolicy().horizontalPolicy(), self.sizePolicy().verticalPolicy())
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)

        # 展开状态持久化：默认全折叠，记住用户上次展开的项目/模块（存 data/config.json）
        self._tree_state = tree_state.bind_view(self, "project_tree")


        self.placeholder_label = QLabel("右键创建用例", self)
        self.placeholder_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder_label.setStyleSheet("color: #999; font-size: 16px; background-color: transparent;")
        self.placeholder_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.placeholder_label.hide()

        # 从 palette 层面把 branch 区域的高亮色改成与选中背景一致
        # 否则 QTreeView 会在选中项左侧用 palette.Highlight (#1976d2) 画一条竖条
        from PyQt6.QtGui import QPalette, QColor
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Highlight, QColor(0, 0, 0, 0))
        # 关键：让 viewport 的底色透明。
        # QTreeView 的圆角边框画在外层 view 上，但 viewport 会用它自己的
        # palette.Base 再铺一层方块底，把四角露成直角。这两步一起做，
        # QSS 里的 border-radius 才能真正在四角显出来。
        pal.setColor(QPalette.ColorRole.Base, QColor(0, 0, 0, 0))
        self.setPalette(pal)
        self.viewport().setAutoFillBackground(False)

    def apply_theme(self, theme_mode: ThemeMode = None):
        """应用主题到视图（由主窗口调用）"""
        if theme_mode is None:
            theme_mode = ThemeMode.DARK if Settings.get_theme_mode() == THEME_MODE_DARK else ThemeMode.LIGHT

        # 亮色 + 有壁纸：使用半透明背景
        import os
        has_wp = False
        if theme_mode != ThemeMode.DARK:
            wp_path = Settings.get_wallpaper_path()
            has_wp = bool(wp_path and os.path.exists(wp_path))

        if has_wp:
            self.setStyleSheet("""
                #ProjectTreeView {
                    /* 0.85 与中央区域叠加后壁纸几乎看不见了，改成与深色主题一致的 0.7，
                       让「项目管理」和「步骤列表」透出壁纸的程度对齐 */
                    background-color: rgba(232, 234, 237, 0.7);
                    border: none;
                    border-radius: 6px;
                    padding: 4px;
                    outline: none;
                }
                #ProjectTreeView::item {
                    height: 30px;
                    color: #333;
                    border: none;
                    outline: none;
                }
                #ProjectTreeView::item:selected,
                #ProjectTreeView::item:selected:active,
                #ProjectTreeView::item:selected:!active,
                #ProjectTreeView::item:selected:focus {
                    background-color: rgba(208, 228, 247, 0.9);
                    color: #1a1a1a;
                    border: none;
                    outline: none;
                }
                #ProjectTreeView::item:hover:!selected {
                    background-color: rgba(223, 226, 230, 0.7);
                }
                /* 同 theme.PROJECT_TREE_LIGHT：不写 ::branch 规则，否则展开/折叠箭头不画 */
                #ProjectTreeView QScrollBar:vertical {
                    width: 6px;
                    background: #e0e0e0;
                    border-radius: 3px;
                }
                #ProjectTreeView QScrollBar::handle:vertical {
                    background: #c0c0c0;
                    border-radius: 3px;
                    min-height: 20px;
                }
                #ProjectTreeView QScrollBar::add-line:vertical,
                #ProjectTreeView QScrollBar::sub-line:vertical {
                    height: 0px;
                }
            """)
        else:
            Theme.apply_theme_to_widget(self, theme_mode)

        # 刷新占位标签的背景色（保持透明，让壁纸透出）
        if theme_mode == ThemeMode.DARK:
            self.placeholder_label.setStyleSheet("color: #888; font-size: 16px; background-color: transparent;")
        else:
            self.placeholder_label.setStyleSheet("color: #999; font-size: 16px; background-color: transparent;")

    def set_model(self, model: ProjectModel):
        self.model_obj = model
        self.refresh()

    def refresh(self):
        # 重建前先把当前展开态收下来（setModel 会把展开态全部丢掉）
        self._tree_state.snapshot()

        model = QStandardItemModel()
        if self.model_obj and self.model_obj.root_nodes:
            for node in self.model_obj.root_nodes:
                item = self._create_item(node)
                model.appendRow(item)
            self.setModel(model)
            # 按上次记录还原展开态；没有记录（首次运行）就保持全折叠
            self._tree_state.restore()
            self.placeholder_label.hide()
        else:
            self.setModel(model)
            self._tree_state.restore()
            self.placeholder_label.show()
            self.placeholder_label.setGeometry(0, 0, self.width(), self.height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.placeholder_label.isVisible():
            self.placeholder_label.setGeometry(0, 0, self.width(), self.height())

    def _create_item(self, node: TreeNode):
        item = QStandardItem(node.name)
        item.setData(node.id, Qt.ItemDataRole.UserRole)
        item.setData(node.type, Qt.ItemDataRole.UserRole + 1)
        item.setEditable(False)
        if node.type == 'project':
            icon = qta.icon("fa6s.folder-open", color=ICON_COLOR_GROUP)
        elif node.type == 'folder':
            icon = qta.icon("fa6s.folder", color=ICON_COLOR_GROUP)
        else:
            icon = qta.icon("fa6s.comment-dots", color=ICON_COLOR_CASE)
        item.setIcon(icon)
        if node.children:
            for child in node.children:
                child_item = self._create_item(child)
                item.appendRow(child_item)
        return item

    def show_context_menu(self, pos):
        # 图标颜色跟主题走：qta 出的是位图，QSS 改不了颜色，只能建菜单时按主题选。
        # 取色与「语音管理 ▾」下拉菜单、语音页右键菜单保持一致。
        is_dark = Settings.get_theme_mode() == THEME_MODE_DARK
        icon_color = "#bbbbbb" if is_dark else "#555555"
        icon_add = qta.icon('fa6s.plus', color=icon_color)
        icon_copy = qta.icon('fa6s.copy', color=icon_color)
        icon_rename = qta.icon('fa6s.pen', color=icon_color)
        icon_delete = qta.icon('fa6s.trash', color=icon_color)

        # 获取选中的节点（仅用例类型）
        selected_indexes = self.selectedIndexes()
        selected_cases = []
        for idx in selected_indexes:
            node_id = idx.data(Qt.ItemDataRole.UserRole)
            if node_id:
                node = self.model_obj.get_node_by_id(node_id)
                if node and node.type == 'case':
                    selected_cases.append((node_id, node))

        # 如果有多个用例被选中，显示"删除选中"菜单
        if len(selected_cases) > 1:
            menu = QMenu()
            delete_action = menu.addAction(
                icon_delete, f"删除选中的 {len(selected_cases)} 个用例")
            delete_action.triggered.connect(lambda: self._batch_delete_cases(selected_cases))
            menu.exec(self.viewport().mapToGlobal(pos))
            return

        # 否则按原有逻辑处理（单节点右键菜单）
        index = self.indexAt(pos)
        if not index.isValid():
            menu = QMenu()
            create_project = menu.addAction(icon_add, "创建项目")
            create_project.triggered.connect(lambda: self._prompt_create(None, 'project'))
            menu.exec(self.viewport().mapToGlobal(pos))
            return

        node_id = index.data(Qt.ItemDataRole.UserRole)
        node_type = index.data(Qt.ItemDataRole.UserRole + 1)
        node = self.model_obj.get_node_by_id(node_id)
        if not node:
            return

        menu = QMenu()

        # 根据节点类型添加菜单项
        if node_type == 'project':
            create_folder = menu.addAction(icon_add, "创建功能模块")
            create_folder.triggered.connect(lambda: self._prompt_create(node_id, 'folder'))
            menu.addSeparator()
            # 复制项目
            copy_project = menu.addAction(icon_copy, "复制项目")
            copy_project.triggered.connect(lambda: self.copy_node_signal.emit(node_id, None))
        elif node_type == 'folder':
            create_case = menu.addAction(icon_add, "创建用例")
            create_case.triggered.connect(lambda: self._prompt_create(node_id, 'case'))
            menu.addSeparator()
            # 复制功能模块（父节点为当前节点的父节点）
            parent = self.model_obj.get_parent_and_index(node_id)[0]
            parent_id = parent.id if parent else None
            copy_folder = menu.addAction(icon_copy, "复制功能模块")
            copy_folder.triggered.connect(lambda: self.copy_node_signal.emit(node_id, parent_id))
        elif node_type == 'case':
            copy_case = menu.addAction(icon_copy, "复制用例")
            copy_case.triggered.connect(lambda: self._copy_case(node_id))
            menu.addSeparator()

        # 重命名和删除（对所有类型都可用）
        rename_action = menu.addAction(icon_rename, "重命名")
        rename_action.triggered.connect(lambda: self.context_menu_signal.emit(node_id, "rename"))
        delete_action = menu.addAction(icon_delete, "删除")
        delete_action.triggered.connect(lambda: self.context_menu_signal.emit(node_id, "delete"))

        menu.exec(self.viewport().mapToGlobal(pos))

    def _batch_delete_cases(self, selected_nodes):
        """批量删除：直接发送信号，由控制器处理确认和删除"""
        if not selected_nodes:
            return
        node_ids = [node_id for node_id, _ in selected_nodes]
        self.batch_delete_cases_signal.emit(node_ids)

    def _prompt_create(self, parent_id, node_type):
        type_names = {'project': '项目', 'folder': '功能模块', 'case': '用例'}
        title = f"创建{type_names[node_type]}"
        label = f"请输入{type_names[node_type]}名称:"
        name, ok = InputDialog.get_text(self, title, label, placeholder="例如：主图态")
        if ok and name:
            self.create_node_signal.emit(parent_id, node_type, name)

    def _copy_case(self, case_id):
        node = self.model_obj.get_node_by_id(case_id)
        if not node:
            return
        parent, nodes, idx = self.model_obj.get_parent_and_index(case_id)
        if not parent:
            WarningDialog.show_warning(self, "错误", "无法获取用例的父节点")
            return

        base_name = node.name + " 副本"
        existing_names = self.model_obj.get_children_names(parent.id)
        new_name = base_name
        counter = 1
        while new_name in existing_names:
            new_name = f"{node.name} 副本{counter}"
            counter += 1

        new_case = TreeNode(id=new_node_id('case'), name=new_name, type='case')
        parent.children.append(new_case)
        self.model_obj.save()
        self.refresh()
        self.copy_case_signal.emit(case_id, new_case.id)