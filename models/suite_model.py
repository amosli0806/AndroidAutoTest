# models/suite_model.py
import json
import os
from dataclasses import dataclass, field
from typing import List, Optional

@dataclass
class TestSuite:
    name: str
    case_ids: List[str] = field(default_factory=list)

    def to_dict(self):
        return {
            'name': self.name,
            'case_ids': self.case_ids
        }

    @classmethod
    def from_dict(cls, data):
        return cls(name=data['name'], case_ids=data.get('case_ids', []))


class SuiteModel:
    DATA_FILE = "suites_data.json"

    def __init__(self):
        self.suites: List[TestSuite] = []
        self.load()

    def load(self):
        if os.path.exists(self.DATA_FILE):
            try:
                with open(self.DATA_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.suites = [TestSuite.from_dict(item) for item in data]
                return
            except:
                pass
        self.suites = []
        self.save()

    def save(self):
        data = [s.to_dict() for s in self.suites]
        with open(self.DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def get_all_suites(self) -> List[TestSuite]:
        return self.suites

    def get_suite_by_name(self, name: str) -> Optional[TestSuite]:
        for s in self.suites:
            if s.name == name:
                return s
        return None

    def create_suite(self, name: str, case_ids: List[str]) -> TestSuite:
        if self.get_suite_by_name(name):
            raise ValueError(f"套件 '{name}' 已存在")
        suite = TestSuite(name=name, case_ids=case_ids)
        self.suites.append(suite)
        self.save()
        return suite

    def update_suite(self, name: str, case_ids: List[str]) -> bool:
        suite = self.get_suite_by_name(name)
        if not suite:
            return False
        suite.case_ids = case_ids
        self.save()
        return True

    def delete_suite(self, name: str) -> bool:
        suite = self.get_suite_by_name(name)
        if not suite:
            return False
        self.suites.remove(suite)
        self.save()
        return True

    def remove_case_from_all_suites(self, case_id: str) -> int:
        """从所有套件中移除指定用例ID，返回影响套件数"""
        affected = 0
        for suite in self.suites:
            if case_id in suite.case_ids:
                suite.case_ids.remove(case_id)
                affected += 1
        if affected > 0:
            self.save()
        return affected