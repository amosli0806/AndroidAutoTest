# models/project_model.py
import json
import os
from dataclasses import dataclass, field
from typing import List, Optional

@dataclass
class TreeNode:
    id: str
    name: str
    type: str  # 'project', 'folder', 'case'
    children: List['TreeNode'] = field(default_factory=list)
    expanded: bool = True
    selected: bool = False

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'type': self.type,
            'children': [c.to_dict() for c in self.children],
            'expanded': self.expanded,
            'selected': self.selected
        }

    @classmethod
    def from_dict(cls, data):
        node = cls(
            id=data['id'],
            name=data['name'],
            type=data['type'],
            expanded=data.get('expanded', True),
            selected=data.get('selected', False)
        )
        for child_data in data.get('children', []):
            node.children.append(cls.from_dict(child_data))
        return node


class ProjectModel:
    DATA_FILE = "project_data.json"

    def __init__(self):
        self.root_nodes: List[TreeNode] = []
        self.load()

    def load(self):
        if os.path.exists(self.DATA_FILE):
            try:
                with open(self.DATA_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.root_nodes = [TreeNode.from_dict(item) for item in data]
                return
            except:
                pass
        self._init_sample_data()
        self.save()

    def save(self):
        data = [node.to_dict() for node in self.root_nodes]
        with open(self.DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _init_sample_data(self):
        case1 = TreeNode(id='case1', name='用例示例：检索路网开启导航，进入导航态', type='case')
        case2 = TreeNode(id='case2', name='用例示例：手势操作缩放底图', type='case')
        folder1 = TreeNode(id='folder1', name='功能模块示例：导航态', type='folder', children=[case1], expanded=True)
        folder2 = TreeNode(id='folder2', name='功能模块示例：主组态', type='folder', children=[case2], expanded=False)
        project1 = TreeNode(id='project1', name='项目示例：本田地图QC测试', type='project', children=[folder1, folder2])
        self.root_nodes = [project1]

    def get_node_by_id(self, node_id: str, nodes: List[TreeNode] = None) -> Optional[TreeNode]:
        node_id = str(node_id)
        if nodes is None:
            nodes = self.root_nodes
        for node in nodes:
            if node.id == node_id:
                return node
            if node.children:
                found = self.get_node_by_id(node_id, node.children)
                if found:
                    return found
        return None

    def get_parent_and_index(self, node_id: str):
        """查找节点及其父节点和索引，返回 (parent_node, parent_children_list, index)"""
        node_id = str(node_id)

        # 辅助递归函数
        def find(node, parent=None, parent_list=None, index=-1):
            if node.id == node_id:
                return parent, parent_list, index
            for i, child in enumerate(node.children):
                result = find(child, node, node.children, i)
                if result[0] is not None or result[1] is not None:
                    return result
            return None, None, -1

        # 检查根节点
        for i, root in enumerate(self.root_nodes):
            if root.id == node_id:
                return None, self.root_nodes, i
            # 递归查找子节点
            parent, nodes, idx = find(root)
            if nodes is not None:
                return parent, nodes, idx

        # 未找到
        return None, None, -1

    def add_node(self, parent_id: str, node: TreeNode):
        parent = self.get_node_by_id(parent_id)
        if parent:
            parent.children.append(node)
            self.save()
            return True
        if parent_id is None:
            self.root_nodes.append(node)
            self.save()
            return True
        return False

    def delete_node(self, node_id: str):
        node_id = str(node_id)
        # 先检查节点是否存在
        node = self.get_node_by_id(node_id)
        if not node:
            print(f"[DEBUG] 删除节点失败：节点 {node_id} 不存在")
            return False

        parent, nodes, index = self.get_parent_and_index(node_id)
        if nodes is not None and index != -1:
            nodes.pop(index)
            self.save()
            return True
        else:
            print(f"[DEBUG] 删除节点失败：查找父节点失败, node_id={node_id}, parent={parent}, nodes={nodes}, index={index}")
            # 尝试直接通过节点对象从父节点移除（备用方案）
            if parent:
                # 如果 parent 是 TreeNode，我们可以从 parent.children 中移除
                for i, child in enumerate(parent.children):
                    if child.id == node_id:
                        parent.children.pop(i)
                        self.save()
                        return True
            return False

    def rename_node(self, node_id: str, new_name: str):
        node = self.get_node_by_id(node_id)
        if node:
            node.name = new_name
            self.save()
            return True
        return False

    def get_all_cases(self) -> List[TreeNode]:
        cases = []
        def collect(node):
            if node.type == 'case':
                cases.append(node)
            if node.children:
                for child in node.children:
                    collect(child)
        for root in self.root_nodes:
            collect(root)
        return cases

    def get_descendant_cases(self, node: TreeNode) -> List[TreeNode]:
        cases = []
        if node.type == 'case':
            return [node]
        if node.children:
            for child in node.children:
                cases.extend(self.get_descendant_cases(child))
        return cases

    def create_project(self, name: str) -> TreeNode:
        node = TreeNode(id=f"proj_{id(name)}", name=name, type='project')
        self.root_nodes.append(node)
        self.save()
        return node

    def create_folder(self, parent_id: str, name: str) -> TreeNode:
        parent = self.get_node_by_id(parent_id)
        if parent:
            node = TreeNode(id=f"folder_{id(name)}", name=name, type='folder')
            parent.children.append(node)
            self.save()
            return node
        return None

    def create_case(self, parent_id: str, name: str) -> TreeNode:
        parent = self.get_node_by_id(parent_id)
        if parent:
            node = TreeNode(id=f"case_{id(name)}", name=name, type='case')
            parent.children.append(node)
            self.save()
            return node
        return None

    def is_descendant(self, node_id: str, ancestor_id: str) -> bool:
        ancestor = self.get_node_by_id(ancestor_id)
        if not ancestor:
            return False

        def find(node):
            if node.id == node_id:
                return True
            for child in node.children:
                if find(child):
                    return True
            return False

        return find(ancestor)

    def get_children_names(self, parent_id: str) -> List[str]:
        parent = self.get_node_by_id(parent_id)
        if not parent:
            return []
        return [child.name for child in parent.children]