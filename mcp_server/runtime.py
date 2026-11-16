# mcp_server/runtime.py
"""MCP 进程内的设备 / 层级缓存。"""
from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Any, Optional

from services.device_service import DeviceService
from utils.ui_tree import compute_instance, parse_bounds, parse_xml, xpath_for
from mcp_server.schema import ToolFailure

DUMP_CACHE_SECONDS = 3.0


def _node_info(node) -> Optional[dict]:
    bounds = parse_bounds(node.get("bounds"))
    if not bounds:
        return None
    rid = (node.get("resource-id") or "").strip()
    text = (node.get("text") or "").strip()
    desc = (node.get("content-desc") or "").strip()
    return {
        "resourceId": rid,
        "text": text,
        "description": desc,
        "className": node.get("class") or "",
        "package": node.get("package") or "",
        "clickable": node.get("clickable") == "true",
        "enabled": node.get("enabled") != "false",
        "bounds": bounds,
    }


def _window_map(root):
    mapping = {}
    for window in list(root):
        try:
            for node in window.iter("node"):
                mapping[node] = window
        except Exception:
            continue
    return mapping


def search_nodes(root, *, text: Optional[str] = None,
                 resource_id: Optional[str] = None,
                 description: Optional[str] = None,
                 contains: bool = False,
                 clickable_only: bool = False,
                 limit: int = 20) -> list[dict]:
    """在已解析层级里搜索节点，返回 ui_find 的候选结构。"""
    if root is None:
        return []
    limit = max(1, min(100, int(limit or 20)))
    text_v = (text or "").strip()
    rid_v = (resource_id or "").strip()
    desc_v = (description or "").strip()
    if not (text_v or rid_v or desc_v):
        clickable_only = True

    def match(value: str, query: str) -> bool:
        if not query:
            return True
        if contains:
            return query in value
        return value == query

    window_map = _window_map(root)
    out = []
    for node in root.iter("node"):
        info = _node_info(node)
        if info is None:
            continue
        rid, txt, desc = info["resourceId"], info["text"], info["description"]
        if text_v and not match(txt, text_v):
            continue
        if rid_v:
            # 资源 ID 允许只写末段（和浅层快查一致）
            if not (rid == rid_v or rid.endswith("/" + rid_v)):
                if not (contains and rid_v in rid):
                    continue
        if desc_v and not match(desc, desc_v):
            continue
        if clickable_only and not info["clickable"]:
            continue
        if not (rid or txt or desc) and not clickable_only:
            # 无属性节点对 Agent 没意义，除非显式要可点击容器
            continue

        window = window_map.get(node)
        if window is None:
            count, instance = 1, 0
        else:
            count, instance = compute_instance(window, rid, info.get("bounds"))
        b = info["bounds"]
        cx, cy = (b[0] + b[2]) // 2, (b[1] + b[3]) // 2
        out.append({
            "index": len(out) + 1,
            "class": info["className"],
            "text": txt,
            "resource_id": rid,
            "description": desc,
            "package": info["package"],
            "bounds": list(b),
            "center": [cx, cy],
            "clickable": info["clickable"],
            "enabled": info["enabled"],
            "instance": instance,
            "same_id_count": count,
            "locators": {
                "资源ID": rid,
                "文本": txt,
                "描述": desc,
                "XPath": xpath_for(root, info),
                "坐标": f"{cx},{cy}",
            },
        })
        if len(out) >= limit:
            break
    return out


def summary_nodes(root, max_nodes: int = 200) -> list[dict]:
    """ui_dump summary：只返回可点击/有文案/有 ID 的节点，token 友好。"""
    if root is None:
        return []
    max_nodes = max(1, min(1000, int(max_nodes or 200)))
    out = []
    for node in root.iter("node"):
        info = _node_info(node)
        if info is None:
            continue
        if not (info["clickable"] or info["text"] or info["description"] or info["resourceId"]):
            continue
        b = info["bounds"]
        out.append({
            "class": info["className"],
            "text": info["text"],
            "resource_id": info["resourceId"],
            "description": info["description"],
            "bounds": list(b),
            "center": [(b[0] + b[2]) // 2, (b[1] + b[3]) // 2],
            "clickable": info["clickable"],
            "enabled": info["enabled"],
        })
        if len(out) >= max_nodes:
            break
    return out


class MCPRuntime:
    def __init__(self):
        self.lock = threading.RLock()
        self.service: Optional[DeviceService] = None
        self.serial: Optional[str] = None
        self.root = None
        self.xml = ""
        self.candidates: list[dict] = []
        self.dumped_at = 0.0
        self.dump_ms = 0
        self._screen = (0, 0)

    def ensure_device(self, serial: Optional[str] = None) -> DeviceService:
        serial = (serial or "").strip() or None
        if self.service is not None and (serial is None or serial == self.serial):
            if self.service.device is not None:
                return self.service
        svc = DeviceService(serial)
        devices = svc.get_devices()
        if not devices:
            raise ToolFailure(
                "NO_DEVICE",
                "未检测到设备",
                "请连接 USB 并开启调试，或在 ADB 工具箱里完成无线联调",
            )
        if serial is None and len(devices) > 1:
            raise ToolFailure(
                "NO_DEVICE",
                f"检测到 {len(devices)} 台设备，请指定 serial",
                "可先调用 device_list 查看序列号",
            )
        try:
            svc.connect(serial or devices[0])
        except Exception as e:
            raise ToolFailure("NO_DEVICE", str(e), "请确认设备在线且已授权调试")
        self.service = svc
        self.serial = svc.serial
        self.invalidate()
        return svc

    def invalidate(self):
        self.root = None
        self.xml = ""
        self.candidates = []
        self.dumped_at = 0.0

    def dump(self, force: bool = False) -> bool:
        """刷新层级；返回 True 表示本次真的 dump 了。"""
        svc = self.ensure_device(self.serial)
        now = time.time()
        if (not force and self.root is not None
                and (now - self.dumped_at) <= DUMP_CACHE_SECONDS):
            return False
        started = time.time()
        xml = svc.dump_ui_xml()
        if not xml:
            raise ToolFailure(
                "DUMP_FAILED",
                "读取界面层级失败",
                "界面可能一直在动或为自绘 UI；可改用 screenshot + 图像定位",
            )
        root = parse_xml(xml)
        if root is None:
            raise ToolFailure("DUMP_FAILED", "界面层级 XML 解析失败", "请刷新后重试")
        self.root = root
        self.xml = xml
        self.dumped_at = time.time()
        self.dump_ms = int((self.dumped_at - started) * 1000)
        self.candidates = []
        try:
            self._screen = tuple(svc._get_current_screen_size() or (0, 0))
        except Exception:
            self._screen = (0, 0)
        return True

    def screen(self) -> dict:
        if not self._screen or not self._screen[0]:
            try:
                self._screen = tuple(self.ensure_device(self.serial)._get_current_screen_size() or (0, 0))
            except Exception:
                self._screen = (0, 0)
        return {"w": int(self._screen[0] or 0), "h": int(self._screen[1] or 0)}

    def dumped_at_iso(self) -> str:
        if not self.dumped_at:
            return ""
        return datetime.fromtimestamp(self.dumped_at).isoformat(timespec="seconds")


RUNTIME = MCPRuntime()
