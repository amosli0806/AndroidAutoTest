# services/notification_service.py
"""消息中心的事件总线：全项目唯一的消息生产入口。

任何组件想产生一条消息都只调 post()，不要直接碰 NotificationStore 或视图。
这样以后要加「按来源静音」「相同事件聚合」时，只需要改这一处。

消息只记录、不主动打扰：本服务**不弹任何气泡**（参照 PyCharm 用户最讨厌的
行为）。到达时只更新未读数，由铃铛角标体现。
"""
from typing import List, Optional

from PyQt6.QtCore import QObject, pyqtSignal

from models.notification_model import (
    LEVEL_ERROR, LEVEL_INFO, LEVEL_WARNING, Notification, NotificationStore,
)


class NotificationService(QObject):
    message_added = pyqtSignal(object)   # Notification：新增了一条
    unread_changed = pyqtSignal(int)     # 未读数变化（驱动铃铛角标）
    store_changed = pyqtSignal()         # 列表整体变化（新增/已读/清空）

    def __init__(self, parent=None):
        super().__init__(parent)
        self.store = NotificationStore()

    # ---------------- 生产 ----------------
    def post(self, level, source, title, detail="", actions=None) -> Notification:
        item = self.store.add(level, source, title, detail=detail, actions=actions)
        self.message_added.emit(item)
        self.unread_changed.emit(self.store.unread_count)
        self.store_changed.emit()
        return item

    # 语义化快捷方法，减少调用点噪音
    def info(self, source, title, detail="", actions=None) -> Notification:
        return self.post(LEVEL_INFO, source, title, detail, actions)

    def warning(self, source, title, detail="", actions=None) -> Notification:
        return self.post(LEVEL_WARNING, source, title, detail, actions)

    def error(self, source, title, detail="", actions=None) -> Notification:
        return self.post(LEVEL_ERROR, source, title, detail, actions)

    # ---------------- 已读 / 清空 ----------------
    def mark_read(self, nid: int) -> bool:
        if not self.store.mark_read(nid):
            return False
        self.unread_changed.emit(self.store.unread_count)
        self.store_changed.emit()
        return True

    def mark_all_read(self) -> int:
        changed = self.store.mark_all_read()
        if changed:
            self.unread_changed.emit(self.store.unread_count)
            self.store_changed.emit()
        return changed

    def clear(self):
        self.store.clear()
        self.unread_changed.emit(0)
        self.store_changed.emit()

    # ---------------- 查询 ----------------
    @property
    def unread_count(self) -> int:
        return self.store.unread_count

    @property
    def total(self) -> int:
        return self.store.total

    def all(self) -> List[Notification]:
        return self.store.all()

    def get(self, nid: int) -> Optional[Notification]:
        return self.store.get(nid)
