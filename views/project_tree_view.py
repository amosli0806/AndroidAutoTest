# views/project_tree_view.py
from PyQt6.QtWidgets import QTreeView, QMenu, QMessageBox, QInputDialog, QAbstractItemView, QLabel
from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtGui import QStandardItemModel, QStandardItem
from models.project_model import TreeNode, ProjectModel
from utils.icons import IconManager
from utils.dialogs import InputDialog, ConfirmDeleteDialog, WarningDialog
from utils.theme import Theme, ThemeMode
from utils.settings import Settings, THEME_MODE_DARK


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
        self.setPalette(pal)

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
                    background-color: rgba(232, 234, 237, 0.85);
                    border: none;
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
                #ProjectTreeView::branch {
                    background: transparent;
                }
                #ProjectTreeView::branch:selected,
                #ProjectTreeView::branch:selected:active,
                #ProjectTreeView::branch:selected:!active {
                    background: transparent;
                }
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
        # 保存当前展开状态
        expanded_ids = self._save_expanded_state()
        # 判断是否为首次加载（无保存状态且有数据）
        is_first_load = not expanded_ids and self.model_obj and self.model_obj.root_nodes

        model = QStandardItemModel()
        if self.model_obj and self.model_obj.root_nodes:
            for node in self.model_obj.root_nodes:
                item = self._create_item(node)
                model.appendRow(item)
            self.setModel(model)
            if is_first_load:
                self.expandAll()
            else:
                self._restore_expanded_state(expanded_ids)
            self.placeholder_label.hide()
        else:
            self.setModel(model)
            self.placeholder_label.show()
            self.placeholder_label.setGeometry(0, 0, self.width(), self.height())

    def _save_expanded_state(self):
        """保存当前所有展开节点的ID"""
        expanded_ids = set()
        model = self.model()
        if model is None:
            return expanded_ids
        for i in range(model.rowCount()):
            root_index = model.index(i, 0)
            self._collect_expanded(root_index, expanded_ids)
        return expanded_ids

    def _restore_expanded_state(self, expanded_ids):
        """恢复指定ID列表的节点为展开状态"""
        model = self.model()
        if model is None:
            return
        for i in range(model.rowCount()):
            root_index = model.index(i, 0)
            self._restore_expanded_recursive(root_index, expanded_ids)

    def _restore_expanded_recursive(self, index, expanded_ids):
        node_id = index.data(Qt.ItemDataRole.UserRole)
        if node_id and node_id in expanded_ids:
            self.setExpanded(index, True)
        for row in range(self.model().rowCount(index)):
            child_index = self.model().index(row, 0, index)
            self._restore_expanded_recursive(child_index, expanded_ids)

    def _collect_expanded(self, index, expanded_ids):
        if self.isExpanded(index):
            node_id = index.data(Qt.ItemDataRole.UserRole)
            if node_id:
                expanded_ids.add(node_id)
        for row in range(self.model().rowCount(index)):
            child_index = self.model().index(row, 0, index)
            self._collect_expanded(child_index, expanded_ids)

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
            icon = IconManager.get_icon('folder_open')
        elif node.type == 'folder':
            icon = IconManager.get_icon('folder')
        else:
            icon = IconManager.get_icon('doc')
        item.setIcon(icon)
        if node.children:
            for child in node.children:
                child_item = self._create_item(child)
                item.appendRow(child_item)
        return item

    def show_context_menu(self, pos):
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
            delete_action = menu.addAction(f"删除选中的 {len(selected_cases)} 个用例")
            delete_action.triggered.connect(lambda: self._batch_delete_cases(selected_cases))
            menu.exec(self.viewport().mapToGlobal(pos))
            return

        # 否则按原有逻辑处理（单节点右键菜单）
        index = self.indexAt(pos)
        if not index.isValid():
            menu = QMenu()
            create_project = menu.addAction("创建项目")
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
            create_folder = menu.addAction("创建功能模块")
            create_folder.triggered.connect(lambda: self._prompt_create(node_id, 'folder'))
            menu.addSeparator()
            # 复制项目
            copy_project = menu.addAction("复制项目")
            copy_project.triggered.connect(lambda: self.copy_node_signal.emit(node_id, None))
        elif node_type == 'folder':
            create_case = menu.addAction("创建用例")
            create_case.triggered.connect(lambda: self._prompt_create(node_id, 'case'))
            menu.addSeparator()
            # 复制功能模块（父节点为当前节点的父节点）
            parent = self.model_obj.get_parent_and_index(node_id)[0]
            parent_id = parent.id if parent else None
            copy_folder = menu.addAction("复制功能模块")
            copy_folder.triggered.connect(lambda: self.copy_node_signal.emit(node_id, parent_id))
        elif node_type == 'case':
            copy_case = menu.addAction("复制用例")
            copy_case.triggered.connect(lambda: self._copy_case(node_id))
            menu.addSeparator()

        # 重命名和删除（对所有类型都可用）
        rename_action = menu.addAction("重命名")
        rename_action.triggered.connect(lambda: self.context_menu_signal.emit(node_id, "rename"))
        delete_action = menu.addAction("删除")
        delete_action.triggered.connect(lambda: self.context_menu_signal.emit(node_id, "delete"))
        menu.addAction(rename_action)
        menu.addAction(delete_action)

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

        new_case = TreeNode(id=f"case_{id(new_name)}", name=new_name, type='case')
        parent.children.append(new_case)
        self.model_obj.save()
        self.refresh()
        self.copy_case_signal.emit(case_id, new_case.id)