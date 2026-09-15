# models/execution_model.py
from typing import List, Tuple

class ExecutionModel:
    def __init__(self):
        self.pass_count = 0
        self.fail_count = 0
        self.logs: List[Tuple[str, str]] = []  # (message, type)
        self.screenshot_paths: List[str] = []  # 存储断言失败时的截图路径

    def reset(self):
        self.pass_count = 0
        self.fail_count = 0
        self.logs.clear()
        self.screenshot_paths.clear()

    def add_log(self, message: str, log_type: str = 'info'):
        self.logs.append((message, log_type))

    def increment_pass(self):
        self.pass_count += 1

    def increment_fail(self):
        self.fail_count += 1

    def get_stats(self):
        total = self.pass_count + self.fail_count
        rate = round(self.pass_count / total * 100) if total > 0 else 0
        return self.pass_count, self.fail_count, rate