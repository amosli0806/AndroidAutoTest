# controllers/project_controller.py
from PyQt6.QtWidgets import QMessageBox, QInputDialog
from PyQt6.QtCore import Qt
from models.project_model import ProjectModel, TreeNode, new_node_id
from views.project_tree_view import ProjectTreeView
from controllers.step_controller import StepController
from models.suite_model import SuiteModel
from utils.toast import show_toast
from utils.dialogs import InputDialog, ConfirmDeleteDialog, WarningDialog
from utils.theme import ThemeMode


class ProjectController:
    def __init__(self, model: ProjectModel, view: ProjectTreeView, step_controller: StepController, execute_view=None, main_window=None):
        self.model = model
        self.view = view
        self.step_controller = step_controller
        self.execute_view = execute_view
        self.main_window = main_window
        self.suite_model = None

        self.view.set_model(model)
        self.view.context_menu_signal.connect(self._on_context_menu)
        self.view.create_node_signal.connect(self._on_create_node)
        self.view.copy_case_signal.connect(self._on_copy_case)
        self.view.batch_delete_cases_signal.connect(self._on_batch_delete_cases)
        self.view.copy_node_signal.connect(self._on_copy_node)
        self.view.clicked.connect(self._on_tree_clicked)

    def apply_theme(self, theme_mode: ThemeMode):
        """应用主题到控制器及关联视图（占位方法，保持接口一致性）"""
        # 转发到视图
        if self.view and hasattr(self.view, 'apply_theme'):
            self.view.apply_theme(theme_mode)
        # 如果 execute_view 存在且有 apply_theme，也转发
        if self.execute_view and hasattr(self.execute_view, 'apply_theme'):
            self.execute_view.apply_theme(theme_mode)

    def set_suite_model(self, suite_model: SuiteModel):
        self.suite_model = suite_model

    def _on_tree_clicked(self, index):
        if not index.isValid():
            return
        node_id = index.data(Qt.ItemDataRole.UserRole)
        if not node_id:
            return
        node = self.model.get_node_by_id(node_id)
        if not node:
            return
        if node.type == 'case':
            self.step_controller.set_current_case(node_id)
            if self.main_window and hasattr(self.main_window, '_update_record_button_state'):
                self.main_window._update_record_button_state()
        else:
            self.step_controller.set_current_case(None)
            if self.main_window and hasattr(self.main_window, '_update_record_button_state'):
                self.main_window._update_record_button_state()

    def _on_context_menu(self, node_id: str, action: str):
        if action == "rename":
            node = self.model.get_node_by_id(node_id)
            if node:
                # 根据节点类型设置中文标题
                type_names = {'project': '项目', 'folder': '功能模块', 'case': '用例'}
                title = f"重命名{type_names.get(node.type, node.type)}"
                name, ok = InputDialog.get_text(
                    self.view,
                    title=title,
                    label="输入新名称:",
                    default_text=node.name,
                    placeholder="例如：主图态"
                )
                if ok and name:
                    self.model.rename_node(node_id, name)
                    self.view.refresh()
                    self.step_controller.refresh_steps()
                    if self.execute_view:
                        self.execute_view.refresh()
        elif action == "delete":
            node = self.model.get_node_by_id(node_id)
            if not node:
                WarningDialog.show_warning(self.view, "错误", f"节点 '{node_id}' 不存在，可能已被删除")
                return
            if not ConfirmDeleteDialog.ask(
                    self.view,
                    title="确认删除",
                    message=f"确定要删除 '{node.name}' 吗？",
                    detail="相关步骤也会被移除。"
            ):
                return
            # 用例：连它的步骤和套件引用一起清；**项目/功能模块：把下面所有子用例一起清** ——
            # 以前只处理 case 类型，删文件夹/整个项目时子用例的步骤会永远留在
            # steps_data.json 里变成没人引用的垃圾（那份文件虚胖的主因之一），
            # 套件里也会留下点不出内容的死引用。
            for case in self.model.get_descendant_cases(node):
                self.step_controller.remove_case_steps(case.id)
                self._remove_case_from_suites(case.id)
            success = self.model.delete_node(node_id)
            if success:
                if self.step_controller.current_case_id == node_id:
                    self.step_controller.set_current_case(None)
                    if self.main_window and hasattr(self.main_window, '_update_record_button_state'):
                        self.main_window._update_record_button_state()
                self.view.refresh()
                if self.execute_view:
                    self.execute_view.refresh()
            else:
                node_exists = self.model.get_node_by_id(node_id) is not None
                error_msg = f"无法删除节点 '{node.name}'，请稍后重试。\n"
                if not node_exists:
                    error_msg += "节点已不存在，请刷新视图。"
                else:
                    error_msg += "可能原因：节点数据异常或查找失败。\n详细信息请查看控制台输出。"
                WarningDialog.show_warning(self.view, "删除失败", error_msg)

    def _on_create_node(self, parent_id, node_type, name):
        if node_type == 'project':
            self.model.create_project(name)
        elif node_type == 'folder':
            self.model.create_folder(parent_id, name)
        elif node_type == 'case':
            self.model.create_case(parent_id, name)
        self.view.refresh()
        if self.execute_view:
            self.execute_view.refresh()

    def _on_copy_case(self, src_case_id, dst_case_id):
        self.step_controller.copy_steps(src_case_id, dst_case_id)
        if self.execute_view:
            self.execute_view.refresh()

    def _on_batch_delete_cases(self, node_ids):
        if not node_ids:
            return
        names = []
        for nid in node_ids:
            node = self.model.get_node_by_id(nid)
            if node:
                names.append(node.name)
        if not names:
            return

        if not ConfirmDeleteDialog.ask(
                self.view,
                title="确认批量删除",
                message=f"确定删除以下 {len(names)} 个用例吗？",
                detail="相关步骤也会被移除。"
        ):
            return

        # 执行删除（原有逻辑）
        deleted_count = 0
        for node_id in node_ids:
            node = self.model.get_node_by_id(node_id)
            if node and node.type == 'case':
                self.step_controller.remove_case_steps(node_id)
                self._remove_case_from_suites(node_id)
                if self.model.delete_node(node_id):
                    deleted_count += 1
        self.view.refresh()
        if self.execute_view:
            self.execute_view.refresh()
        if self.step_controller.current_case_id in node_ids:
            self.step_controller.set_current_case(None)
            if self.main_window and hasattr(self.main_window, '_update_record_button_state'):
                self.main_window._update_record_button_state()
        show_toast(message="删除成功")

    def _remove_case_from_suites(self, case_id: str):
        if self.suite_model:
            try:
                affected = self.suite_model.remove_case_from_all_suites(case_id)
                if affected > 0:
                    if self.execute_view and hasattr(self.execute_view, '_refresh_suite_combo'):
                        self.execute_view._refresh_suite_combo()
            except Exception as e:
                print(f"从套件移除用例时出错: {e}")

    def _on_copy_node(self, src_node_id: str, parent_id: str):
        src_node = self.model.get_node_by_id(src_node_id)
        if not src_node:
            WarningDialog.show_warning(self.view, "错误", "源节点不存在")
            return

        if parent_id:
            target_parent = self.model.get_node_by_id(parent_id)
            if not target_parent:
                WarningDialog.show_warning(self.view, "错误", "目标父节点不存在")
                return
        else:
            target_parent = None

        base_name = src_node.name + " 副本"
        if target_parent:
            existing_names = self.model.get_children_names(target_parent.id)
        else:
            existing_names = [node.name for node in self.model.root_nodes]
        new_name = base_name
        counter = 1
        while new_name in existing_names:
            new_name = f"{src_node.name} 副本{counter}"
            counter += 1

        new_node = self._copy_node_recursive(src_node, new_name)
        if not new_node:
            WarningDialog.show_warning(self.view, "错误", "复制节点失败")
            return

        if target_parent:
            target_parent.children.append(new_node)
        else:
            self.model.root_nodes.append(new_node)

        self.model.save()

        id_map = {}
        self._collect_id_map(src_node, new_node, id_map)

        for src_id, dst_id in id_map.items():
            src_case = self.model.get_node_by_id(src_id)
            if src_case and src_case.type == 'case':
                self.step_controller.copy_steps(src_id, dst_id)

        self.model.save()

        self.view.refresh()
        if self.execute_view:
            self.execute_view.refresh()

        show_toast(message="复制成功")

    def _copy_node_recursive(self, src_node: TreeNode, new_name: str) -> TreeNode:
        new_id = new_node_id(src_node.type)
        new_node = TreeNode(
            id=new_id,
            name=new_name,
            type=src_node.type,
            children=[],
            expanded=src_node.expanded,
            selected=False
        )
        for child in src_node.children:
            child_new_name = child.name
            existing_names = [c.name for c in new_node.children]
            if child_new_name in existing_names:
                counter = 1
                base = child.name + " 副本"
                child_new_name = base
                while child_new_name in existing_names:
                    child_new_name = f"{child.name} 副本{counter}"
                    counter += 1
            copied_child = self._copy_node_recursive(child, child_new_name)
            if copied_child:
                new_node.children.append(copied_child)
        return new_node

    def _collect_id_map(self, src_node: TreeNode, dst_node: TreeNode, id_map: dict):
        id_map[src_node.id] = dst_node.id
        for src_child, dst_child in zip(src_node.children, dst_node.children):
            self._collect_id_map(src_child, dst_child, id_map)