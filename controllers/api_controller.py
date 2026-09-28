# controllers/api_controller.py
"""接口自动化控制器：把「发请求」放到后台线程，结果排队投回界面。

为什么必须后台线程：一次请求可能等十几秒（超时），批量执行更久。放在 GUI 线程
里会整窗卡死 —— 与「性能检测」「自动化执行」的处理方式一致。

线程安全约定：worker 只读模型快照（进入 run() 前已把 case 列表拷出来），
结果通过 Qt 信号回投，界面只在 GUI 线程被碰。
"""
import os
import subprocess
from datetime import datetime

from PyQt6.QtCore import QObject, QThread, pyqtSignal
from PyQt6.QtWidgets import QMessageBox

from services.api_service import ApiService
from utils.api_report import ApiReportGenerator
from utils.dialogs import ErrorDialog
from utils.settings import Settings


class _ApiRunWorker(QThread):
    """按顺序跑一批接口，每跑完一条就发一次结果，界面可以实时刷新。"""

    result_ready = pyqtSignal(object)      # ApiResult
    finished_all = pyqtSignal(list)        # [ApiResult, ...]

    def __init__(self, service: ApiService, cases, env, parent=None):
        super().__init__(parent)
        self.service = service
        self.cases = list(cases)           # 先做快照，避免执行中界面改模型
        self.env = env
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        results = []
        for case in self.cases:
            if self._stop:
                break
            result = self.service.run_case(case, self.env)
            results.append(result)
            self.result_ready.emit(result)
        self.finished_all.emit(results)


class ApiController(QObject):
    """接口自动化的执行与报告。编辑类操作由 ApiView 直接落模型，这里只管执行。"""

    def __init__(self, view, api_model, parent=None):
        super().__init__(parent)
        self.view = view
        self.model = api_model
        self.service = ApiService()
        self._worker = None
        self._last_results = []

        self.view.send_requested.connect(self._on_send)
        self.view.run_selected_requested.connect(self._on_run_selected)
        self.view.report_requested.connect(self._on_report)
        self.view.stop_requested.connect(self._on_stop)

    # ------------------------------------------------------------------
    # 执行
    # ------------------------------------------------------------------
    def _busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def _on_send(self, case_id: str):
        if self._busy():
            return
        case = self.model.get_case(case_id)
        if case is None:
            return
        self._start([case])

    def _on_run_selected(self, case_ids):
        if self._busy():
            return
        cases = [c for c in (self.model.get_case(cid) for cid in case_ids) if c]
        if not cases:
            return
        self._start(cases)

    def _start(self, cases):
        self._worker = _ApiRunWorker(self.service, cases, self.model.get_env(), self)
        self._worker.result_ready.connect(self.view.show_result)
        self._worker.finished_all.connect(self._on_finished)
        self.view.set_running(True)
        self.view.summary_label.setText(
            f"正在执行 {len(cases)} 个接口…")
        self._worker.start()

    def _on_stop(self):
        if self._worker is not None:
            self._worker.stop()
            self.view.summary_label.setText("正在停止…")

    def _on_finished(self, results):
        self._last_results = list(results)
        self.view.set_running(False)
        self._worker = None
        if not results:
            self.view.summary_label.setText("已停止，没有执行任何接口")
            return
        passed = sum(1 for r in results if r.ok)
        failed = len(results) - passed
        self.view.summary_label.setText(
            f"执行完成：共 {len(results)} 个接口，通过 {passed}，失败 {failed}。"
            + ("　点「生成报告」看详情。" if failed else ""))

    # ------------------------------------------------------------------
    # 报告
    # ------------------------------------------------------------------
    def _on_report(self):
        if not self._last_results:
            ErrorDialog.show_error(
                self.view, "还没有可报告的结果",
                "先执行至少一个接口，再生成报告。")
            return
        output_dir = Settings.get_output_dir()
        os.makedirs(output_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_path = os.path.join(output_dir, f"ApiReport_{timestamp}.html")
        try:
            report_path = ApiReportGenerator.generate(
                self._last_results, file_path,
                env_name=self.model.current_env)
        except Exception as e:
            ErrorDialog.show_error(self.view, "报告生成失败",
                                   f"生成接口测试报告时发生错误：\n{e}")
            return

        # 与「自动化执行」的报告落地体验保持一致：生成后问要不要打开文件夹
        msg_box = QMessageBox(self.view)
        msg_box.setWindowTitle("报告已生成")
        msg_box.setText(f"接口测试报告已保存到：\n{report_path}\n\n选择操作：")
        open_btn = msg_box.addButton("📂 打开文件夹", QMessageBox.ButtonRole.ActionRole)
        msg_box.addButton("OK", QMessageBox.ButtonRole.AcceptRole)
        msg_box.exec()
        if msg_box.clickedButton() == open_btn:
            folder = os.path.dirname(report_path)
            if os.name == "nt":
                os.startfile(folder)
            else:
                subprocess.Popen(["xdg-open", folder])
