# utils/task_scheduler.py
from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from datetime import datetime
import time


class TaskScheduler(QObject):
    task_triggered = pyqtSignal(str)  # 任务 ID

    def __init__(self, controller, check_interval=60000):  # 默认 60 秒
        super().__init__()
        self.controller = controller
        self.timer = QTimer()
        self.timer.timeout.connect(self._check_tasks)
        self.check_interval = check_interval  # 毫秒
        self.running = False

    def start(self):
        if not self.running:
            self.running = True
            self.timer.start(self.check_interval)
            # 立即检查一次
            QTimer.singleShot(100, self._check_tasks)

    def stop(self):
        if self.running:
            self.running = False
            self.timer.stop()

    def _check_tasks(self):
        if not self.running:
            return
        now = datetime.now().isoformat()
        tasks = self.controller.task_model.get_all_tasks()
        for task in tasks:
            if not task.enabled or not task.next_run:
                continue
            if task.next_run <= now:
                # 触发执行
                self.task_triggered.emit(task.id)