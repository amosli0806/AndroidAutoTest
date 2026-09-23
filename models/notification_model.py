# models/notification_model.py
"""消息中心的数据模型（纯内存，不落盘）。

与「虫师日志」的边界（这是本功能最需要守住的一条线）：
  - 虫师日志 = 过程正文：按行、会滚屏、有 50 行上限（BOTTOM_LOG_MAX_BLOCKS），
    只承接 ADB 工具箱的指令输出
  - 消息中心 = 事件索引：一条 = 一件发生过的事，有级别、时间、动作、未读态；
    正文永远留在原地（日志面板 / 执行页 / 报告文件），消息只提供跳转入口

    所以消息**不搬运日志正文**，只带一个动作指回去。否则同一份 Crash 日志会
    同时出现在两个地方，两个面板互相污染。

为什么不做磁盘持久化：
    本项目所有事件源都在进程内产生 —— TaskScheduler 也是进程内 QTimer 轮询
    （utils/task_scheduler.py），应用关闭后不会有任何新事件。因此「消息只在本次
    会话内有意义」这个前提成立。需要长期留痕的内容看 app_debug.log 与
    reports/ 下的产物。
"""
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

# ---------- 级别 ----------
LEVEL_ERROR = "error"
LEVEL_WARNING = "warning"
LEVEL_INFO = "info"

# 级别 -> 条目左侧圆点颜色（红 / 黄 / 蓝）
LEVEL_COLORS = {
    LEVEL_ERROR: "#e53935",
    LEVEL_WARNING: "#f9a825",
    LEVEL_INFO: "#1e88e5",
}

# 消息条数上限：按条数截断，不因滚屏丢弃（与虫师日志的「50 行」策略刻意不同）
MAX_MESSAGES = 200


@dataclass
class Notification:
    """一条消息 = 一件发生过的事。

    actions 中每个元素形如::

        {"label": "查看", "action": "open_panel", "payload": {"kind": "crash"}}

    约定第一个 action 同时作为「单击条目」的跳转目标；没有 action 的消息
    只做记录（例如「已导入 42 条自定义指令」）。
    动作是**数据**而不是回调，消息因此不持有任何界面引用。
    """
    id: int
    level: str
    source: str
    title: str
    detail: str = ""
    time: str = ""
    read: bool = False
    actions: List[dict] = field(default_factory=list)

    @property
    def primary_action(self) -> Optional[dict]:
        return self.actions[0] if self.actions else None


class NotificationStore:
    """消息的内存仓库：定长 deque + 未读计数。"""

    def __init__(self, maxlen: int = MAX_MESSAGES):
        self._items: deque = deque(maxlen=maxlen)
        self._next_id = 1
        self._unread = 0

    # ---------------- 写入 ----------------
    def add(self, level, source, title, detail="", actions=None,
            timestamp=None) -> Notification:
        if level not in LEVEL_COLORS:
            level = LEVEL_INFO
        item = Notification(
            id=self._next_id,
            level=level,
            source=source,
            title=title,
            detail=detail or "",
            time=timestamp or datetime.now().strftime("%H:%M:%S"),
            read=False,
            actions=list(actions or []),
        )
        self._next_id += 1

        # deque 满时 append 会自动丢弃最旧的一条；如果被丢的那条还没读，
        # 未读计数要同步扣减，否则角标会永远比实际多
        maxlen = self._items.maxlen
        if maxlen and len(self._items) == maxlen:
            dropped = self._items[0]
            if not dropped.read:
                self._unread = max(0, self._unread - 1)

        self._items.append(item)
        self._unread += 1
        return item

    # ---------------- 读取 ----------------
    def all(self) -> List[Notification]:
        """按时间倒序返回（新的在前，与消息面板的展示顺序一致）"""
        return list(reversed(self._items))

    def get(self, nid: int) -> Optional[Notification]:
        for item in self._items:
            if item.id == nid:
                return item
        return None

    # ---------------- 已读管理 ----------------
    def mark_read(self, nid: int) -> bool:
        item = self.get(nid)
        if item is None or item.read:
            return False
        item.read = True
        self._unread = max(0, self._unread - 1)
        return True

    def mark_all_read(self) -> int:
        changed = 0
        for item in self._items:
            if not item.read:
                item.read = True
                changed += 1
        self._unread = 0
        return changed

    def clear(self):
        self._items.clear()
        self._unread = 0

    @property
    def unread_count(self) -> int:
        return self._unread

    @property
    def total(self) -> int:
        return len(self._items)
