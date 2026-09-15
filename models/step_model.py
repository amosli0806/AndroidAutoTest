# models/step_model.py
import json
import os
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

@dataclass
class Step:
    id: int
    type: str
    name: str
    params: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self):
        return {
            'id': self.id,
            'type': self.type,
            'name': self.name,
            'params': self.params
        }

    @classmethod
    def from_dict(cls, data):
        return cls(id=data['id'], type=data['type'], name=data['name'], params=data.get('params', {}))


class StepModel:
    DATA_FILE = "steps_data.json"

    def __init__(self):
        self._id_counter = 1
        self._steps: Dict[int, Step] = {}
        self.case_steps: Dict[str, List[int]] = {}
        self.load()

    def load(self):
        if os.path.exists(self.DATA_FILE):
            try:
                with open(self.DATA_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self._id_counter = data.get('id_counter', 1)
                    self.case_steps = data.get('case_steps', {})
                    steps_data = data.get('steps', [])
                    for s in steps_data:
                        step = Step.from_dict(s)
                        self._steps[step.id] = step
                return
            except:
                pass
        self._init_sample_data()
        self.save()

    def save(self):
        data = {
            'id_counter': self._id_counter,
            'case_steps': self.case_steps,
            'steps': [s.to_dict() for s in self._steps.values()]
        }
        with open(self.DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _init_sample_data(self):
        # 示例步骤仅用于 case1
        sample_params = [
            {'locationType': '资源ID', 'locationValue': 'btn_map_open'},
            {'duration': 4},
            {'locationType': '资源ID', 'locationValue': 'input_search'},
            {'duration': 4},
            {'locationType': '资源ID', 'locationValue': 'input_search', 'text': '万达广场'},
            {'duration': 4},
            {'locationType': '资源ID', 'locationValue': 'btn_search'},
            {'duration': 4},
            {'locationType': '资源ID', 'locationValue': 'result_item'},
            {'duration': 4},
            {'locationType': '资源ID', 'locationValue': 'btn_start_nav'},
            {'duration': 4},
            {'locationType': '资源ID', 'locationValue': 'btn_route_switch'},
            {'duration': 4},
            {'locationType': '资源ID', 'locationValue': 'btn_nav_start'},
        ]
        types = ['click', 'wait', 'click', 'wait', 'input', 'wait', 'click', 'wait',
                 'click', 'wait', 'click', 'wait', 'click', 'wait', 'click']
        names = [
            '点击 - 打开地图', '等待 - 4秒', '点击 - 输入框', '等待 - 4秒',
            '输入文字 - 万达广场', '等待 - 4秒', '点击 - 搜索', '等待 - 4秒',
            '点击 - 万达广场(大洋总店)', '等待 - 4秒', '点击 - 开始导航(步行导航)', '等待 - 4秒',
            '点击 - 切换路线', '等待 - 4秒', '点击 - 开始导航'
        ]
        case1_steps = []
        for t, n, p in zip(types, names, sample_params):
            step = Step(id=self._id_counter, type=t, name=n, params=p)
            self._steps[step.id] = step
            case1_steps.append(step.id)
            self._id_counter += 1
        self.case_steps['case1'] = case1_steps

    def get_steps_for_case(self, case_id: str) -> List[Step]:
        step_ids = self.case_steps.get(case_id, [])
        return [self._steps[sid] for sid in step_ids if sid in self._steps]

    def add_step_to_case(self, case_id: str, step: Step) -> int:
        step.id = self._id_counter
        self._id_counter += 1
        self._steps[step.id] = step
        self.case_steps.setdefault(case_id, []).append(step.id)
        self.save()
        return step.id

    def remove_step(self, step_id: int, case_id: str = None):
        if step_id in self._steps:
            del self._steps[step_id]
        if case_id and case_id in self.case_steps:
            if step_id in self.case_steps[case_id]:
                self.case_steps[case_id].remove(step_id)
        self.save()

    def update_step(self, step_id: int, name: str = None, params: dict = None):
        if step_id in self._steps:
            step = self._steps[step_id]
            if name is not None:
                step.name = name
            if params is not None:
                step.params = params
            self.save()
            return True
        return False

    def move_step(self, case_id: str, from_index: int, to_index: int):
        if case_id in self.case_steps:
            steps = self.case_steps[case_id]
            if 0 <= from_index < len(steps) and 0 <= to_index < len(steps):
                steps.insert(to_index, steps.pop(from_index))
                self.save()
                return True
        return False

    def duplicate_step(self, step_id: int, case_id: str) -> Optional[Step]:
        if step_id in self._steps and case_id in self.case_steps:
            orig = self._steps[step_id]
            import copy
            new_step = copy.deepcopy(orig)
            new_step.id = self._id_counter
            self._id_counter += 1
            # 如果原名称不为空才加" 副本"，否则保持空
            if orig.name:
                new_step.name = orig.name + " 副本"
            else:
                new_step.name = ""
            self._steps[new_step.id] = new_step
            self.case_steps[case_id].append(new_step.id)
            self.save()
            return new_step
        return None

    def copy_steps_for_case(self, src_case_id: str, dst_case_id: str):
        """复制一个用例的所有步骤到另一个用例"""
        if src_case_id in self.case_steps:
            src_ids = self.case_steps[src_case_id]
            dst_ids = []
            for sid in src_ids:
                orig = self._steps[sid]
                import copy
                new_step = copy.deepcopy(orig)
                new_step.id = self._id_counter
                self._id_counter += 1
                self._steps[new_step.id] = new_step
                dst_ids.append(new_step.id)
            self.case_steps[dst_case_id] = dst_ids
            self.save()
            return True
        return False