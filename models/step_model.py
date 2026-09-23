# models/step_model.py
import json
import os
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from utils.app_paths import data_path


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
    DATA_FILE = data_path("steps_data.json")

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

    # ------------------------------------------------------------------
    # 无用数据回收
    # ------------------------------------------------------------------
    def gc_orphan_steps(self, valid_case_ids) -> dict:
        """回收无用步骤，返回 {'keys': 删掉的失效映射数, 'steps': 删掉的步骤数, 'freed': 省下的字节数}。

        清两类东西：
          1. 指向「已经不存在的用例」的 case_steps 键（删项目/功能模块时留下的悬空键）
          2. 不被任何现存用例引用的 Step 对象（悬空键下面的步骤、导入用例时被跳过的
             用例的步骤，都会变成这种没人认领的孤儿）

        `_steps` 只增不减，而这个文件**每改一个步骤就整个重写一遍**，垃圾占的每 1 MB
        都是每次编辑要搬一次的成本（实测 8265 个步骤里只有 63 个有用，占 1.39 MB 的 99%）。

        为什么 valid_case_ids 必须由调用方传进来：不能自己读 project_data.json —— 两个
        模型各读一份容易不一致，更要紧的是**用例清单一旦拿不到（空集合）就会把所有步骤
        当垃圾删光**。可信性由调用方判断（见 main.py 的启动自愈、MainWindow._import_cases）。
        """
        valid = set(valid_case_ids or ())
        removed_keys = [key for key in self.case_steps if key not in valid]
        for key in removed_keys:
            del self.case_steps[key]

        referenced = {sid for ids in self.case_steps.values() for sid in ids}
        removed_ids = [sid for sid in self._steps if sid not in referenced]

        if not removed_keys and not removed_ids:
            return {'keys': 0, 'steps': 0, 'freed': 0}

        before = os.path.getsize(self.DATA_FILE) if os.path.exists(self.DATA_FILE) else 0
        for sid in removed_ids:
            del self._steps[sid]
        self.save()
        after = os.path.getsize(self.DATA_FILE) if os.path.exists(self.DATA_FILE) else 0
        return {
            'keys': len(removed_keys),
            'steps': len(removed_ids),
            'freed': max(0, before - after),
        }
