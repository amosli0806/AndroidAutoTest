# controllers/notification_controller.py
"""消息中心的事件源适配 + 动作路由。

本文件是「事件 -> 消息」的唯一翻译层：

  - 各控制器 / 服务只负责发出既有信号，不直接调用 NotificationService
  - 这里把信号翻译成带级别与动作的消息
  - 反向：消息上的动作由 route() 分发回主窗口的既有能力（切页 / 开面板 / 打开目录）

接入原则是**只做新增**：不改变任何既有信号、弹窗与 toast 的行为，
因此开启本功能不会影响原有交互（后台 toast 依旧会弹，消息中心只做留痕）。
"""
import os
import subprocess

from PyQt6.QtCore import QObject, QTimer

from models.notification_model import LEVEL_ERROR, LEVEL_INFO

# 消息来源（便于排查，也为将来「按来源静音」留出抓手）
SOURCE_DEVICE = "device"
SOURCE_EXECUTION = "execution"
SOURCE_TASK = "task"
SOURCE_ADB = "adb"
SOURCE_PERF = "perf"
SOURCE_AI = "ai"

# 页面索引：与 views/main_window.py 里 stacked_widget 的布局保持一致
#   0 ADB工具箱 / 1 自动化编辑 / 2 应用可视化(Dock) / 3 自动化执行
#   4 应用元素库 / 5 帮助中心 / 6 欢迎页 / 7 接口自动化 / 8 性能检测
PAGE_EXECUTE = 3
PAGE_PERF = 8


class NotificationController(QObject):
    # 「ADB 连接中断」告警防抖（秒）：断开后在这个时间内恢复的不告警。
    # 设备插拔/开关 USB 调试会让设备端 adbd 重启、USB 重新枚举，track-devices
    # 长连接撞上 protocol fault 短暂退出几秒即自愈——这种抖动不值得打扰用户。
    ADB_DOWN_DEBOUNCE_MS = 10_000

    def __init__(self, service, main_window, parent=None):
        super().__init__(parent)
        self.service = service
        self.main_window = main_window
        # track-devices 长连接上次是否处于「已告警断开」状态：
        # True = 已发过「中断」告警（恢复时要发「已恢复」）；False = 无需留痕
        self._adb_link_down = False
        # 防抖倒计时：断开后 ADB_DOWN_DEBOUNCE_MS 内恢复则静默
        self._adb_down_timer = QTimer(self)
        self._adb_down_timer.setSingleShot(True)
        self._adb_down_timer.setInterval(self.ADB_DOWN_DEBOUNCE_MS)
        self._adb_down_timer.timeout.connect(self._emit_adb_down)

    # ------------------------------------------------------------------
    # 动作路由
    # ------------------------------------------------------------------
    def route(self, action: dict):
        """执行消息上的一个动作。

        动作是**数据**而不是回调，所以消息本身不持有任何界面引用；
        真正的界面操作集中在这里，出问题也只影响这一处。
        """
        if not action:
            return
        key = action.get("action")
        payload = action.get("payload") or {}
        try:
            if key == "open_page":
                self.main_window.switch_view(int(payload.get("index", 0)))
            elif key == "open_panel":
                self.main_window.open_bottom_panel(payload.get("kind", "log"))
            elif key == "open_path":
                self._open_path(payload.get("path", ""))
        except Exception as e:
            print(f"[notification] 动作 {key} 执行失败: {e}")

    @staticmethod
    def _open_path(path):
        if not path:
            return
        target = path if os.path.isdir(path) else os.path.dirname(path)
        if not target or not os.path.exists(target):
            return
        if os.name == 'nt':
            os.startfile(target)
        else:
            subprocess.Popen(['xdg-open', target])

    # ------------------------------------------------------------------
    # 事件源适配
    # ------------------------------------------------------------------
    def on_execution_finished(self, passed, failed):
        """自动化执行（用户手动触发）结束 —— 信号来自 ExecutionController"""
        self.service.post(
            LEVEL_INFO if failed == 0 else LEVEL_ERROR,
            SOURCE_EXECUTION,
            f"用例执行完成 · 通过 {passed} / 失败 {failed}",
            actions=[{"label": "查看", "action": "open_page",
                      "payload": {"index": PAGE_EXECUTE}}],
        )

    def on_task_finished(self, task_name, passed, failed):
        """定时任务执行结束 —— 信号来自 TaskController._on_worker_finished"""
        self.service.post(
            LEVEL_INFO if failed == 0 else LEVEL_ERROR,
            SOURCE_TASK,
            f"定时任务「{task_name}」执行{'成功' if failed == 0 else '失败'}",
            detail=f"通过 {passed} / 失败 {failed}",
            actions=[{"label": "查看", "action": "open_page",
                      "payload": {"index": PAGE_EXECUTE}}],
        )

    def on_task_skipped(self, task_name, reason):
        """定时任务没能跑起来（设备离线 / 套件不存在 / 套件无用例）"""
        self.service.warning(
            SOURCE_TASK,
            f"定时任务「{task_name}」未执行",
            detail=reason,
            actions=[{"label": "查看", "action": "open_page",
                      "payload": {"index": PAGE_EXECUTE}}],
        )

    def on_devices_changed(self, previous, current):
        """设备增删 —— 由 main.py 的 refresh_devices 差分后调用"""
        for serial in [d for d in current if d not in previous]:
            self.service.info(SOURCE_DEVICE, f"设备 {serial} 已连接")
        for serial in [d for d in previous if d not in current]:
            self.service.warning(SOURCE_DEVICE, f"设备 {serial} 已断开")

    def on_adb_command_finished(self, cmd_id, cmd_name, success, tag):
        """ADB 长命令结束 —— 只记失败，避免正常命令刷屏"""
        if success:
            return
        self.service.warning(
            SOURCE_ADB,
            f"命令「{cmd_name}」执行失败",
            detail="完整输出见虫师日志",
            actions=[{"label": "查看日志", "action": "open_panel",
                      "payload": {"kind": "log"}}],
        )

    def on_perf_finished(self, sample_count):
        """性能采集结束"""
        self.service.info(
            SOURCE_PERF,
            f"性能采集完成 · {sample_count} 个采样点",
            actions=[{"label": "查看", "action": "open_page",
                      "payload": {"index": PAGE_PERF}}],
        )

    def on_perf_alert(self, message):
        """性能阈值异常 —— PerfController._check_threshold 已做单次去重"""
        self.service.warning(
            SOURCE_PERF,
            message.lstrip("⚠ ").strip(),
            actions=[{"label": "查看", "action": "open_page",
                      "payload": {"index": PAGE_PERF}}],
        )

    def on_adb_watcher_status(self, ok):
        """track-devices 长连接状态变化 —— 信号来自 main.py 里的 DeviceWatcher。

        告警防抖（用户实测反馈：设备插拔/开关调试时「中断→恢复」闪动太吵）：
        收到「断开」不立即告警，先起 ADB_DOWN_DEBOUNCE 秒的倒计时——
        - 倒计时内恢复（设备插拔/USB 重枚举的常规抖动，几秒自愈）→ 完全静默
        - 倒计时结束仍未恢复（真断了/拔掉没插回）→ 才告警「中断」
        恢复提示「已恢复」只在已告警过「中断」时才发，避免无头消息。
        """
        if ok:
            if self._adb_down_timer.isActive():
                self._adb_down_timer.stop()          # 抖动内恢复：静默，不告警
            if self._adb_link_down:
                self._adb_link_down = False
                self.service.info(SOURCE_ADB, "ADB 连接已恢复")
            return
        if self._adb_link_down or self._adb_down_timer.isActive():
            return                                    # 已在告警/已在倒计时
        self._adb_down_timer.start()

    def _emit_adb_down(self):
        """防抖倒计时结束仍未恢复：确认真断线，此时才告警。"""
        self._adb_link_down = True
        self.service.warning(SOURCE_ADB, "ADB 连接中断，正在重连…")

    def on_ai_analyzed(self, ok, total):
        """AI 失败分析完成 —— 信号来自 LogsView.ai_analysis_done。

        「打开」跳回自动化执行页：AI 的归因结果是渲染在那个页面的日志区里的，
        所以这里只做留痕 + 指路，不搬运正文（与虫师日志的边界约定）。
        """
        open_action = {"label": "打开", "action": "open_page",
                       "payload": {"index": PAGE_EXECUTE}}
        if ok <= 0:
            # 一条都没归因成功：日志区已经写了具体排查提示，这里只留痕避免静默
            self.service.warning(
                SOURCE_AI, "AI 分析未返回结果，查看详情",
                actions=[open_action],
            )
            return
        if ok >= total:
            text = f"AI 分析完成 · {ok} 条失败已归因，查看详情"
        else:
            text = f"AI 分析完成 · {ok}/{total} 条失败已归因，查看详情"
        self.service.info(SOURCE_AI, text, actions=[open_action])
