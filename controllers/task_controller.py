# controllers/task_controller.py
from PyQt6.QtCore import QObject, pyqtSignal, QDateTime, Qt, QThread, QTimer
from PyQt6.QtWidgets import QDialog

from models.task_model import TaskModel, ScheduledTask
from models.suite_model import SuiteModel
from models.project_model import ProjectModel
from models.step_model import StepModel
from controllers.execution_controller import ExecutionController, ExecutionWorker
from services.device_service import DeviceService
from utils.toast import show_toast
from utils.task_scheduler import TaskScheduler
from utils.theme import ThemeMode
from views.task_view import TaskView, TaskEditDialog
import logging


class TaskController(QObject):
    task_executed = pyqtSignal(str, str)
    # 消息中心用（纯新增）：定时任务执行结果 / 未能执行的原因
    task_finished = pyqtSignal(str, int, int)   # 任务名, 通过数, 失败数
    task_skipped = pyqtSignal(str, str)         # 任务名, 原因

    def __init__(self, task_model: TaskModel, task_view: TaskView,
                 suite_model: SuiteModel, project_model: ProjectModel,
                 step_model: StepModel, device_service: DeviceService,
                 exec_controller: ExecutionController,
                 logs_view):
        super().__init__()
        self.task_model = task_model
        self.task_view = task_view
        self.suite_model = suite_model
        self.project_model = project_model
        self.step_model = step_model
        self.device_service = device_service
        self.exec_controller = exec_controller
        self.logs_view = logs_view
        self.scheduler = TaskScheduler(self)
        self.scheduler.task_triggered.connect(self._on_task_triggered)

        # 任务执行队列：保证同一时刻只有一个任务在跑
        # （uiautomator2 device 对象不是线程安全的，多任务并发会崩）
        self._pending_tasks = []  # [(task, case_ids), ...]
        self._running_thread = None
        self._running_worker = None
        self._running_task_id = None  # 正在执行的任务 id（同一任务只允许跑一份）

        # 连接视图信号
        self.task_view.task_added.connect(self._on_add_task)
        self.task_view.task_deleted.connect(self._on_delete_task)
        self.task_view.task_updated.connect(self._on_update_task)
        self.task_view.task_toggle_enabled.connect(self._on_toggle_enabled)
        self.task_view.task_run_now.connect(self._on_run_now)
        self.task_view.task_selected.connect(self._on_task_selected)

        # 延迟启动调度器，避免在初始化时触发
        QTimer.singleShot(500, self.scheduler.start)

    def apply_theme(self, theme_mode: ThemeMode):
        """应用主题到控制器及关联视图"""
        # 转发到任务视图
        if self.task_view and hasattr(self.task_view, 'apply_theme'):
            self.task_view.apply_theme(theme_mode)
        # 转发到日志视图
        if self.logs_view and hasattr(self.logs_view, 'apply_theme'):
            self.logs_view.apply_theme(theme_mode)

    def _on_add_task(self):
        logger = logging.getLogger(__name__)
        logger.info("=== _on_add_task START ===")
        suite_names = [s.name for s in self.suite_model.get_all_suites()]
        if not suite_names:
            show_toast("请先创建测试套件")
            return
        dialog = TaskEditDialog(self.task_view, suite_names=suite_names)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            task = dialog.get_task_data()
            logger.info(f"task data: {task}")
            if task is None:
                logger.warning("task is None")
                return
            logger.info("calling _update_next_run")
            self._update_next_run(task)
            logger.info(f"after _update_next_run: next_run={task.next_run}")
            logger.info("calling task_model.add_task")
            self.task_model.add_task(task)
            logger.info("task added, refreshing list")
            self.task_view.refresh_list()
            logger.info("refresh done, showing toast")
            show_toast(f"定时任务已添加")
        else:
            logger.info("dialog cancelled")
        logger.info("=== _on_add_task END ===")

    def _on_delete_task(self, task_id):
        self.task_model.delete_task(task_id)
        self.task_view.refresh_list()
        show_toast("定时任务已删除")

    def _on_update_task(self, task_id):
        task = self.task_model.get_task_by_id(task_id)
        if not task:
            return
        suite_names = [s.name for s in self.suite_model.get_all_suites()]
        dialog = TaskEditDialog(self.task_view, task=task, suite_names=suite_names)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            updated = dialog.get_task_data()
            if updated is None:
                return
            self.task_model.update_task(task_id,
                name=updated.name,
                suite_name=updated.suite_name,
                schedule_type=updated.schedule_type,
                scheduled_time=updated.scheduled_time,
                day_of_week=updated.day_of_week,
                day_of_month=updated.day_of_month,
                loop_count=updated.loop_count,
                stop_on_fail=updated.stop_on_fail,
                enabled=updated.enabled
            )
            task = self.task_model.get_task_by_id(task_id)
            self._update_next_run(task)
            self.task_model.save()
            dialog.deleteLater()
            self.task_view.refresh_list()
            show_toast(f"定时任务已更新")

    def _on_toggle_enabled(self, task_id, enabled):
        # 若视图正在刷新，避免重入
        if self.task_view._refreshing:
            return
        # 原有逻辑保持不变
        task = self.task_model.get_task_by_id(task_id)
        if task:
            self.task_model.update_task(task_id, enabled=enabled)
            if enabled:
                self._update_next_run(task)
            else:
                self.task_model.update_task(task_id, next_run="")
            self.task_model.save()
            self.task_view.refresh_list()

    def _on_run_now(self, task_id):
        self._execute_task(task_id, manual=True)

    def _on_task_selected(self, task):
        pass

    def _on_task_triggered(self, task_id):
        self._execute_task(task_id, manual=False)

    def is_task_busy(self, task_id) -> bool:
        """该任务是否正在执行、或已经在队列里等待执行

        调度器（TaskScheduler）也会用它来跳过已经到期但正在执行中的任务，
        避免同一个任务被重复投递。
        """
        if self._running_task_id is not None and self._running_task_id == task_id:
            return True
        return any(t.id == task_id for t, _ in self._pending_tasks)

    def _execute_task(self, task_id, manual=False):
        task = self.task_model.get_task_by_id(task_id)
        if not task or not task.enabled:
            return

        # 闸门（兜底）：调度器已经会跳过执行中的任务，这里再挡一道，
        # 防止其它入口（手动执行等）把同一个任务投递成多份。
        # 调度器每 60 秒检查一次，而 next_run 只有执行结束后才会推进，
        # 少了这道闸门，一个长任务（比如循环 10 次）在运行期间会被反复触发、
        # 反复入队，表现就是「10 次循环跑完之后还在不停地跑」。
        if self.is_task_busy(task_id):
            self.logs_view.add_log(
                f"[定时] 任务 '{task.name}' 正在执行中，本次触发已跳过", "warning"
            )
            if manual:
                # 手动点「立即执行」时给个明确反馈，否则用户会觉得点击没反应
                show_toast("该任务正在执行中，请稍后再试")
            return

        if not self.device_service.check_device_online():
            self.logs_view.add_log(f"[定时] 设备未连接，任务 '{task.name}' 执行失败", "error")
            self.task_model.update_task(task_id, last_result="device_offline", last_run=QDateTime.currentDateTime().toString(Qt.DateFormat.ISODate))
            self.task_model.save()
            self.task_view.refresh_list()
            show_toast(f"设备未连接")
            self.task_skipped.emit(task.name, "设备未连接")
            return

        suite = self.suite_model.get_suite_by_name(task.suite_name)
        if not suite:
            self.logs_view.add_log(f"[定时] 套件 '{task.suite_name}' 不存在，任务 '{task.name}' 执行失败", "error")
            self.task_model.update_task(task_id, last_result="failed", last_run=QDateTime.currentDateTime().toString(Qt.DateFormat.ISODate))
            self.task_model.save()
            self.task_view.refresh_list()
            show_toast(f"执行失败：套件不存在")
            self.task_skipped.emit(task.name, f"套件「{task.suite_name}」不存在")
            return

        case_ids = []
        for cid in suite.case_ids:
            node = self.project_model.get_node_by_id(cid)
            if node:
                case_ids.append(cid)
        if not case_ids:
            self.logs_view.add_log(f"[定时] 套件 '{task.suite_name}' 无可执行用例", "warning")
            self.task_model.update_task(task_id, last_result="failed", last_run=QDateTime.currentDateTime().toString(Qt.DateFormat.ISODate))
            self.task_model.save()
            self.task_view.refresh_list()
            show_toast(f"执行失败：套件无用例")
            self.task_skipped.emit(task.name, f"套件「{task.suite_name}」无可执行用例")
            return

        # 加入执行队列（同一时刻只跑一个任务，防止 device 对象被并发调用导致崩溃）
        self._pending_tasks.append((task, case_ids))
        pending_count = len(self._pending_tasks)
        if pending_count > 1:
            self.logs_view.add_log(
                f"[定时] 任务 '{task.name}' 已加入执行队列（前面还有 {pending_count - 1} 个任务）",
                "info"
            )
        else:
            self.logs_view.add_log(f"[定时] 开始执行任务 '{task.name}'（套件: {task.suite_name}）", "info")
        self._try_run_next_task()

    def _try_run_next_task(self):
        """从队列取出下一个任务开始执行；若已有任务在跑则等待"""
        if self._running_thread is not None:
            return
        if not self._pending_tasks:
            return
        task, case_ids = self._pending_tasks.pop(0)
        self._execute_worker(task, case_ids)

    def _execute_worker(self, task, case_ids):
        thread = QThread()
        worker = ExecutionWorker(
            case_ids,
            self.project_model,
            self.step_model,
            self.device_service,
            self.exec_controller.exec_model,
            loop_count=task.loop_count,
            stop_on_fail=task.stop_on_fail
        )
        worker.moveToThread(thread)

        # 保存引用，防止被垃圾回收
        self._running_thread = thread
        self._running_worker = worker
        self._running_task_id = task.id

        worker.progress.connect(self._on_worker_progress)
        worker.finished.connect(thread.quit)
        thread.finished.connect(lambda: self._on_worker_finished(task.id, thread, worker))
        thread.finished.connect(thread.deleteLater)

        thread.started.connect(worker.run)
        self.exec_controller.execution_state_changed.emit(True)
        thread.start()

    def _on_worker_progress(self, message, log_type):
        self.logs_view.add_log(message, log_type)
        # 更新统计
        if log_type == 'success' and '用例' in message and '执行通过' in message:
            self.exec_controller.exec_model.increment_pass()
        elif log_type == 'error' and '用例' in message and '执行失败' in message:
            self.exec_controller.exec_model.increment_fail()
        self.logs_view.update_stats()

    def _on_worker_finished(self, task_id, thread, worker):
        stats = self.exec_controller.exec_model.get_stats()
        result = "success" if stats[1] == 0 else "failed"
        self.logs_view.add_log(
            f"[定时] 任务执行完成，通过: {stats[0]}, 失败: {stats[1]}",
            "info" if result == "success" else "error"
        )

        task = self.task_model.get_task_by_id(task_id)
        # 消息中心：定时任务结果留痕（纯新增信号，失败不再只躺在默认隐藏的日志面板里）
        self.task_finished.emit(task.name if task else task_id, stats[0], stats[1])
        if task:
            self.task_model.update_task(task_id,
                                        last_result=result,
                                        last_run=QDateTime.currentDateTime().toString(Qt.DateFormat.ISODate)
                                        )
            self._update_next_run(task)
            self.task_model.save()
            self.task_view.refresh_list()
            # 移除 show_toast，日志已充分展示结果

        # 启用测试报告按钮（定时任务完成后也可生成报告）
        self.exec_controller.exec_view.set_report_enabled(True)

        try:
            worker.deleteLater()
        except Exception:
            pass

        # 清空当前 running 引用
        self._running_thread = None
        self._running_worker = None
        self._running_task_id = None

        # 通知 ADB 工具箱：本次执行结束
        self.exec_controller.execution_state_changed.emit(False)

        # 队列里还有任务，稍后执行下一个（稍延迟一点，让设备状态稳定）
        if self._pending_tasks:
            QTimer.singleShot(800, self._try_run_next_task)

    def _update_next_run(self, task):
        from datetime import datetime, timedelta
        now = datetime.now()
        if not task.enabled:
            task.next_run = ""
            return

        schedule_type = task.schedule_type
        scheduled_time = task.scheduled_time

        if schedule_type == "once":
            try:
                dt = datetime.fromisoformat(scheduled_time)
                if dt > now:
                    task.next_run = dt.isoformat()
                else:
                    task.next_run = ""
                    task.enabled = False
            except:
                task.next_run = ""
        elif schedule_type == "daily":
            try:
                hour, minute = map(int, scheduled_time.split(":"))
                next_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if next_dt <= now:
                    next_dt += timedelta(days=1)
                task.next_run = next_dt.isoformat()
            except:
                task.next_run = ""
        elif schedule_type == "weekly":
            day = task.day_of_week if task.day_of_week >= 0 else 0
            # 将 0=周日 ... 6=周六 转换为 0=周一 ... 6=周日
            target_weekday = (day - 1) % 7  # 周日(0)->6, 周一(1)->0, 周二(2)->1, ...
            try:
                hour, minute = map(int, scheduled_time.split(":"))
                today_weekday = now.weekday()
                days_ahead = (target_weekday - today_weekday) % 7
                if days_ahead == 0 and now.time() >= datetime.strptime(scheduled_time, "%H:%M").time():
                    days_ahead = 7
                next_dt = (now + timedelta(days=days_ahead)).replace(hour=hour, minute=minute, second=0, microsecond=0)
                task.next_run = next_dt.isoformat()
            except:
                task.next_run = ""
        elif schedule_type == "monthly":
            day = task.day_of_month if 1 <= task.day_of_month <= 31 else 1
            try:
                hour, minute = map(int, scheduled_time.split(":"))
                year = now.year
                month = now.month
                if now.day > day or (now.day == day and now.time() >= datetime.strptime(scheduled_time, "%H:%M").time()):
                    month += 1
                    if month > 12:
                        month = 1
                        year += 1
                import calendar
                last_day = calendar.monthrange(year, month)[1]
                actual_day = min(day, last_day)
                next_dt = datetime(year, month, actual_day, hour, minute, 0, 0)
                if next_dt <= now:
                    month += 1
                    if month > 12:
                        month = 1
                        year += 1
                    last_day = calendar.monthrange(year, month)[1]
                    actual_day = min(day, last_day)
                    next_dt = datetime(year, month, actual_day, hour, minute, 0, 0)
                task.next_run = next_dt.isoformat()
            except Exception as e:
                logging.getLogger(__name__).exception("_update_next_run failed")
                task.next_run = ""
        else:
            task.next_run = ""

    def reset_offline_tasks(self):
        logger = logging.getLogger(__name__)
        modified = False
        for task in self.task_model.get_all_tasks():
            if task.last_result == "device_offline":
                task.last_result = ""
                # 不修改 enabled，保持原来状态（通常 enabled 仍为 True）
                self._update_next_run(task)  # 重新计算下次运行时间
                modified = True
                logger.info(f"Reset offline status for task {task.id}")
        if modified:
            self.task_model.save()
            self.task_view.refresh_list()

    def mark_tasks_offline(self):
        """标记所有已启用且状态正常的任务为 device_offline（设备断开时调用）"""
        modified = False
        for task in self.task_model.get_all_tasks():
            # 仅标记已启用且不是已经离线的任务
            if task.enabled and task.last_result != "device_offline":
                task.last_result = "device_offline"
                modified = True
        if modified:
            self.task_model.save()
            self.task_view.refresh_list()
            logging.getLogger(__name__).info("标记任务为 device_offline")