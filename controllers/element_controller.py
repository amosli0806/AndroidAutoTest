# controllers/element_controller.py
from typing import List, Optional
from models.element_model import ElementModel, Element
from models.step_model import StepModel
from utils.theme import ThemeMode


class ElementController:
    """
    元素库控制器
    职责：
    1. 元素的增删改查
    2. 检查元素是否被步骤引用
    3. 清理步骤中的元素引用
    4. 解析步骤中的 element_id（执行时）
    5. 导入导出
    """

    def __init__(self, element_model: ElementModel, step_model: StepModel):
        self.element_model = element_model
        self.step_model = step_model

    def apply_theme(self, theme_mode: ThemeMode):
        """应用主题到控制器（占位方法，保持接口一致性）"""
        # 控制器不涉及界面样式，无需实际操作
        pass

    # ---------- 查询 ----------
    def get_elements(self) -> List[Element]:
        """获取所有元素"""
        return self.element_model.get_elements()

    def get_element_by_id(self, elem_id: str) -> Optional[Element]:
        """根据 ID 获取元素"""
        return self.element_model.get_element_by_id(elem_id)

    def get_all_apps(self) -> List[str]:
        """获取所有应用名称（用于下拉过滤）"""
        return self.element_model.get_all_apps()

    # ---------- 增删改 ----------
    def add_element(self, name: str, app: str, module: str,
                   loc_type: str, loc_value: str, remark: str = "") -> Element:
        """新增元素"""
        elem = Element(
            id=self.element_model._generate_id(),
            name=name,
            app=app,
            module=module,
            loc_type=loc_type,
            loc_value=loc_value,
            remark=remark
        )
        self.element_model.add_element(elem)
        return elem

    def update_element(self, elem_id: str, **kwargs) -> bool:
        """更新元素信息（支持 name, app, module, loc_type, loc_value, remark）"""
        return self.element_model.update_element(elem_id, **kwargs)

    def delete_element(self, elem_id: str) -> bool:
        """
        删除元素（仅删除自身，不清理引用）
        调用者应先调用 find_referencing_steps() 检查，再决定是否清理
        """
        return self.element_model.delete_element(elem_id)

    # ---------- 引用管理 ----------
    def find_referencing_steps(self, elem_id: str) -> List[dict]:
        """
        查找所有引用指定元素的步骤
        返回格式：[{'case_id': str, 'step_id': int, 'step_name': str}, ...]
        """
        refs = []
        for case_id, step_ids in self.step_model.case_steps.items():
            for step_id in step_ids:
                step = self.step_model._steps.get(step_id)
                if step and step.params.get('element_id') == elem_id:
                    refs.append({
                        'case_id': case_id,
                        'step_id': step_id,
                        'step_name': step.name
                    })
        return refs

    def remove_element_references(self, elem_id: str) -> int:
        """
        清理所有引用指定元素的步骤中的 element_id 字段
        返回清理的步骤数量
        """
        count = 0
        for case_id, step_ids in self.step_model.case_steps.items():
            for step_id in step_ids:
                step = self.step_model._steps.get(step_id)
                if step and step.params.get('element_id') == elem_id:
                    step.params.pop('element_id', None)
                    # 注意：定位方式和值保留，避免误删用户手动输入的内容
                    count += 1
        if count > 0:
            self.step_model.save()
        return count

    def safe_delete_element(self, elem_id: str, auto_cleanup: bool = False) -> bool:
        """
        安全删除元素（整合引用检查和清理）
        :param elem_id: 元素ID
        :param auto_cleanup: 是否自动清理引用（若为False，仅当无引用时才删除）
        :return: 是否删除成功
        """
        refs = self.find_referencing_steps(elem_id)
        if refs:
            if auto_cleanup:
                self.remove_element_references(elem_id)
            else:
                return False  # 有引用且不允许自动清理
        return self.delete_element(elem_id)

    # ---------- 执行时解析 ----------
    def resolve_element(self, step_params: dict) -> dict:
        """
        解析步骤参数中的元素ID，返回完整的定位方式和值
        若元素失效，抛出 ValueError
        """
        elem_id = step_params.get('element_id')
        if elem_id:
            elem = self.element_model.get_element_by_id(elem_id)
            if elem:
                # 用元素库中的定位方式和值覆盖步骤中的值
                step_params['locationType'] = elem.loc_type
                step_params['locationValue'] = elem.loc_value
            else:
                raise ValueError(f"元素ID '{elem_id}' 已失效，请更新元素库")
        return step_params

    # ---------- 导入导出 ----------
    def import_elements(self, file_path: str) -> int:
        """从 JSON 文件导入元素，返回导入数量"""
        return self.element_model.import_from_file(file_path)

    def export_elements(self, file_path: str) -> bool:
        """导出元素到 JSON 文件"""
        return self.element_model.export_to_file(file_path)