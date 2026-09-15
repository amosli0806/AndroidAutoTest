# models/element_model.py
import json
import os
from typing import List, Optional
from dataclasses import dataclass, field

@dataclass
class Element:
    id: str
    name: str
    app: str              # 所属应用（如“百度地图”）
    module: str           # 所属模块（如“底图”）
    loc_type: str         # 定位方式（资源ID/坐标/文本/描述/XPath）
    loc_value: str        # 定位值
    remark: str = ""      # 备注

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'app': self.app,
            'module': self.module,
            'loc_type': self.loc_type,
            'loc_value': self.loc_value,
            'remark': self.remark
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            id=data['id'],
            name=data['name'],
            app=data.get('app', ''),
            module=data.get('module', ''),
            loc_type=data['loc_type'],
            loc_value=data['loc_value'],
            remark=data.get('remark', '')
        )


class ElementModel:
    DATA_FILE = "elements_data.json"

    def __init__(self):
        self.elements: List[Element] = []
        self.load()

    def load(self):
        if os.path.exists(self.DATA_FILE):
            try:
                with open(self.DATA_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.elements = [Element.from_dict(item) for item in data.get('elements', [])]
            except:
                self.elements = []
        else:
            self.elements = []
            self.save()

    def save(self):
        data = {
            'elements': [e.to_dict() for e in self.elements]
        }
        with open(self.DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def get_elements(self) -> List[Element]:
        return self.elements

    def get_element_by_id(self, elem_id: str) -> Optional[Element]:
        for elem in self.elements:
            if elem.id == elem_id:
                return elem
        return None

    def add_element(self, elem: Element):
        # 确保ID不重复
        if self.get_element_by_id(elem.id):
            raise ValueError(f"元素ID '{elem.id}' 已存在")
        self.elements.append(elem)
        self.save()

    def update_element(self, elem_id: str, **kwargs) -> bool:
        elem = self.get_element_by_id(elem_id)
        if not elem:
            return False
        for key, value in kwargs.items():
            if hasattr(elem, key):
                setattr(elem, key, value)
        self.save()
        return True

    def delete_element(self, elem_id: str) -> bool:
        elem = self.get_element_by_id(elem_id)
        if not elem:
            return False
        self.elements.remove(elem)
        self.save()
        return True

    def get_all_apps(self) -> List[str]:
        """获取所有已存储的应用名称（去重排序）"""
        apps = set()
        for elem in self.elements:
            if elem.app:
                apps.add(elem.app)
        return sorted(list(apps))

    def _generate_id(self) -> str:
        """生成唯一ID"""
        # 简单生成方式：根据当前时间戳和计数器（避免重复）
        import time
        base = int(time.time() * 1000) % 1000000000
        # 确保唯一性
        existing_ids = [e.id for e in self.elements]
        counter = 0
        while True:
            new_id = f"elem_{base}_{counter}"
            if new_id not in existing_ids:
                return new_id
            counter += 1

    def import_from_file(self, file_path: str) -> int:
        """从JSON文件导入元素（追加，不覆盖），若格式无效则抛出 ValueError"""
        import json
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # 校验根结构
        if not isinstance(data, dict) or 'elements' not in data:
            raise ValueError("无效的元素库文件")
        elements_data = data['elements']
        if not isinstance(elements_data, list):
            raise ValueError("无效的元素库文件")

        # 校验每个元素对象
        required_fields = ['id', 'name', 'app', 'module', 'loc_type', 'loc_value']
        for idx, item in enumerate(elements_data):
            if not isinstance(item, dict):
                raise ValueError(f"元素 #{idx + 1} 必须是一个对象")
            for field in required_fields:
                if field not in item:
                    raise ValueError(f"元素 #{idx + 1} 缺少必要字段 '{field}'")

        # 导入逻辑
        count = 0
        for item in elements_data:
            elem = Element.from_dict(item)
            existing = self.get_element_by_id(elem.id)
            if existing:
                continue  # 跳过已存在的ID
            self.elements.append(elem)
            count += 1
        self.save()
        return count

    def export_to_file(self, file_path: str) -> bool:
        """导出所有元素到JSON文件"""
        data = {
            'elements': [e.to_dict() for e in self.elements]
        }
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True