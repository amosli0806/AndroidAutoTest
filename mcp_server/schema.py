# mcp_server/schema.py
"""MCP 工具的返回信封与错误码。"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional


class ToolFailure(Exception):
    def __init__(self, code: str, message: str, hint: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint


def ok(data: Optional[Dict[str, Any]] = None) -> str:
    payload: Dict[str, Any] = {"ok": True}
    if data:
        payload.update(data)
    return json.dumps(payload, ensure_ascii=False)


def err(code: str, message: str, hint: str = "") -> str:
    return json.dumps({
        "ok": False,
        "error": {"code": code, "message": message, "hint": hint},
    }, ensure_ascii=False)


def classify_error(exc: Exception) -> str:
    msg = str(exc)
    low = msg.lower()
    if "未检测到设备" in msg or "连接设备失败" in msg or "设备未连接" in msg:
        return "NO_DEVICE"
    if "dump" in low or "层级" in msg:
        return "DUMP_FAILED"
    if "超时" in msg or "timeout" in low:
        return "TIMEOUT"
    if "未找到" in msg or "not found" in low or "no such element" in low:
        return "ELEMENT_NOT_FOUND"
    if "多个" in msg or "ambiguous" in low:
        return "ELEMENT_AMBIGUOUS"
    if "用例" in msg and "不存在" in msg:
        return "CASE_NOT_FOUND"
    return "INTERNAL_ERROR"
