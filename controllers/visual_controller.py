# controllers/visual_controller.py
"""「应用可视化」页面的数据层。

职责只有三件，界面一行都不碰：
    1. 后台读一次画面 + 层级（车机单次 dump 8~12 秒，必须离开 GUI 线程）
    2. 缓存层级根，之后每次点击都在本地 XML 里命中，不再 dump
    3. 把命中节点翻译成六种定位方式（值 + 是否可用），供页面右侧列表展示

命中规则与 XPath 生成都在 utils/ui_tree.py，与元素拾取面板、录制反查共用。
"""
import threading
import time

from PyQt6.QtCore import QObject, pyqtSignal

from utils.ui_tree import (TEXT_LOCATOR_MAX, find_node_at, hit_chain, parse_xml,
                           xpath_for)


class VisualController(QObject):
    """页面唯一的数据入口。线程安全约定：只有 start_load 会起线程，
    回主线程一律走信号（Qt 队列连接），界面不直接读线程里的中间状态。"""

    load_finished = pyqtSignal(object, float)   # (PIL Image, 耗时秒)
    load_failed = pyqtSignal(str)

    def __init__(self, device_service=None, parent=None):
        super().__init__(parent)
        self.device_service = device_service
        self._root = None            # 缓存的层级根（<hierarchy>）
        self._loading = False
        self.last_elapsed = 0.0

    # ------------------------------------------------------------------
    def set_device_service(self, device_service):
        """切换/断开设备时清空缓存 —— 旧设备的层级不能拿来命中新画面。"""
        self.device_service = device_service
        self._root = None

    def device_ready(self) -> bool:
        return bool(self.device_service is not None
                    and getattr(self.device_service, "device", None) is not None)

    @property
    def loading(self) -> bool:
        return self._loading

    def has_hierarchy(self) -> bool:
        return self._root is not None

    def node_count(self) -> int:
        if self._root is None:
            return 0
        try:
            return sum(1 for _ in self._root.iter("node"))
        except Exception:
            return 0

    # ------------------------------------------------------------------
    def start_load(self) -> bool:
        """后台截图 + dump 一次。已在加载中、或没设备时返回 False。

        为什么要离开 GUI 线程：车机单次 dump 实测 8~12 秒，同步做的话整个
        窗口会"假死"，用户连切页都做不到。
        """
        if self._loading:
            return False
        if not self.device_ready():
            self.load_failed.emit("未检测到设备，请先在顶部工具栏连接")
            return False
        self._loading = True
        threading.Thread(target=self._worker, daemon=True).start()
        return True

    def _worker(self):
        started = time.time()
        try:
            image = self.device_service.device.screenshot()
        except Exception as e:
            self._loading = False
            self.load_failed.emit(f"截图失败：{e}")
            return
        # 层级失败不算整体失败：画面已经拿到了，用户还能看画面、只是点不出定位
        try:
            xml_str = self.device_service.dump_ui_xml()
        except Exception:
            xml_str = None
        self._root = parse_xml(xml_str) if xml_str else None
        self.last_elapsed = time.time() - started
        self._loading = False
        self.load_finished.emit(image, self.last_elapsed)

    # ------------------------------------------------------------------
    def find(self, x: int, y: int):
        """点击处推荐命中的一个节点（没有则 None）。"""
        return find_node_at(self._root, x, y)

    def chain(self, x: int, y: int):
        """点击处的全部候选，推荐的在第一个（供 ↑↓ 切换）。"""
        return hit_chain(self._root, x, y)

    def locator_rows(self, info, x=None, y=None):
        """命中节点 -> 六种定位方式。

        每行 {'type', 'value', 'ok', 'note', 'recommended'}：
            ok=False 时页面整行置灰、不可复制；
            note 为空时页面不显示说明文字。
        坐标取节点中心点（不是点击点）：同一控件点边缘和点正中的定位值应当一致。
        """
        if not info:
            return []
        rid = (info.get("resourceId") or "").strip()
        text = (info.get("text") or "").strip()
        desc = (info.get("description") or "").strip()

        bounds = info.get("bounds")
        if bounds:
            cx, cy = (bounds[0] + bounds[2]) // 2, (bounds[1] + bounds[3]) // 2
        else:
            cx, cy = int(x or 0), int(y or 0)

        xpath = xpath_for(self._root, info)
        text_ok = bool(text) and len(text) <= TEXT_LOCATOR_MAX
        if text and not text_ok:
            text_note = f"超过 {TEXT_LOCATOR_MAX} 字，建议用资源ID"
        else:
            text_note = "" if text else "无"

        return [
            {"type": "资源ID", "value": rid, "ok": bool(rid),
             "note": "" if rid else "无", "recommended": bool(rid)},
            {"type": "文本", "value": text, "ok": text_ok,
             "note": text_note, "recommended": False},
            {"type": "描述", "value": desc, "ok": bool(desc),
             "note": "" if desc else "无", "recommended": False},
            {"type": "XPath", "value": xpath, "ok": bool(xpath),
             "note": "" if xpath else "无", "recommended": False},
            {"type": "坐标", "value": f"{cx},{cy}", "ok": True,
             "note": "", "recommended": False},
            {"type": "图像", "value": "", "ok": False,
             "note": "二期支持", "recommended": False},
        ]
