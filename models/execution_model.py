# models/execution_model.py
from dataclasses import dataclass, field
from typing import List, Tuple, Dict, Any, Optional


@dataclass
class FailureContext:
    """一次用例失败的完整上下文，供 AI 分析和报告展示用"""
    case_id: str
    case_name: str
    step_index: int                          # 1-based，与日志里的“步骤 N”一致
    step_type: str
    step_name: str
    step_params: Dict[str, Any] = field(default_factory=dict)
    prev_steps: List[Dict[str, Any]] = field(default_factory=list)
    error_msg: str = ""
    screenshot_path: Optional[str] = None
    loop_index: int = 0                      # 第几轮循环（从 0 开始）
    timestamp: str = ""
    ai_suggestion: Any = None                # FailureSuggestion，AI 分析后才填


class ExecutionModel:
    def __init__(self):
        self.pass_count = 0
        self.fail_count = 0
        self.logs: List[Tuple[str, str]] = []  # (message, type)
        self.screenshot_paths: List[str] = []  # 存储断言失败时的截图路径
        self.failure_contexts: List[FailureContext] = []  # 失败上下文，供 AI 分析

    def reset(self):
        self.pass_count = 0
        self.fail_count = 0
        self.logs.clear()
        self.screenshot_paths.clear()
        self.failure_contexts.clear()

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