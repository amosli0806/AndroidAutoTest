# models/task_model.py
import json
import os
import time
from dataclasses import dataclass, field
from typing import List, Optional
from datetime import datetime, timedelta

import logging

from utils.app_paths import data_path



@dataclass
class ScheduledTask:
    id: str
    name: str
    suite_name: str                  # 关联的套件名称
    schedule_type: str               # "once", "daily", "weekly", "monthly"
    scheduled_time: str              # "HH:MM" 格式（daily/weekly/monthly）或 ISO 时间（once）
    day_of_week: int = -1            # 0-6, 仅 weekly
    day_of_month: int = -1           # 1-31, 仅 monthly
    loop_count: int = 1
    stop_on_fail: bool = False
    enabled: bool = True
    last_run: str = ""               # ISO 时间
    last_result: str = ""            # "success", "failed", "device_offline", "cancelled"
    next_run: str = ""               # ISO 时间

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "suite_name": self.suite_name,
            "schedule_type": self.schedule_type,
            "scheduled_time": self.scheduled_time,
            "day_of_week": self.day_of_week,
            "day_of_month": self.day_of_month,
            "loop_count": self.loop_count,
            "stop_on_fail": self.stop_on_fail,
            "enabled": self.enabled,
            "last_run": self.last_run,
            "last_result": self.last_result,
            "next_run": self.next_run,
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            id=data["id"],
            name=data["name"],
            suite_name=data["suite_name"],
            schedule_type=data["schedule_type"],
            scheduled_time=data["scheduled_time"],
            day_of_week=data.get("day_of_week", -1),
            day_of_month=data.get("day_of_month", -1),
            loop_count=data.get("loop_count", 1),
            stop_on_fail=data.get("stop_on_fail", False),
            enabled=data.get("enabled", True),
            last_run=data.get("last_run", ""),
            last_result=data.get("last_result", ""),
            next_run=data.get("next_run", ""),
        )


class TaskModel:
    DATA_FILE = data_path("tasks_data.json")

    def __init__(self):
        self.tasks: List[ScheduledTask] = []
        self.load()

    def load(self):
        if os.path.exists(self.DATA_FILE):
            try:
                with open(self.DATA_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.tasks = [ScheduledTask.from_dict(item) for item in data]
                return
            except:
                pass
        self.tasks = []
        self.save()

    def save(self):
        logger = logging.getLogger(__name__)
        logger.info("saving tasks...")
        data = [t.to_dict() for t in self.tasks]
        with open(self.DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info("save completed")

    def get_all_tasks(self) -> List[ScheduledTask]:
        return self.tasks

    def get_task_by_id(self, task_id: str) -> Optional[ScheduledTask]:
        for t in self.tasks:
            if t.id == task_id:
                return t
        return None

    def add_task(self, task: ScheduledTask):
        logger = logging.getLogger(__name__)
        logger.info(f"add_task: id={task.id}, name={task.name}, enabled={task.enabled}")
        if self.get_task_by_id(task.id):
            logger.error(f"Duplicate task id {task.id}")
            raise ValueError(f"Task ID {task.id} already exists")
        self.tasks.append(task)
        self.save()
        logger.info("task saved")

    def update_task(self, task_id: str, **kwargs) -> bool:
        task = self.get_task_by_id(task_id)
        if not task:
            return False
        for key, value in kwargs.items():
            if hasattr(task, key):
                setattr(task, key, value)
        self.save()
        return True

    def delete_task(self, task_id: str) -> bool:
        task = self.get_task_by_id(task_id)
        if not task:
            return False
        self.tasks.remove(task)
        self.save()
        return True

    def _generate_id(self) -> str:
        import time
        base = int(time.time() * 1000)
        existing_ids = [t.id for t in self.tasks]
        counter = 0
        while True:
            new_id = f"task_{base}_{counter}"
            if new_id not in existing_ids:
                return new_id
            counter += 1