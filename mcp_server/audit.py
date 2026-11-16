# mcp_server/audit.py
"""MCP 调用审计：只记摘要，不落截图 / 完整层级。"""
from __future__ import annotations

import json
import threading
import time
from typing import Any, Dict

from utils.app_paths import data_path

_LOCK = threading.Lock()


def _brief(value: Any, limit: int = 160) -> Any:
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "…"
    if isinstance(value, dict):
        return {str(k): _brief(v, limit) for k, v in list(value.items())[:20]}
    if isinstance(value, (list, tuple)):
        return [_brief(v, limit) for v in list(value)[:20]]
    return value


def write(tool: str, params: Dict[str, Any], result_ok: bool,
          code: str = "", elapsed_ms: int = 0):
    record = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "tool": tool,
        "params": _brief(params or {}),
        "ok": bool(result_ok),
        "error": code,
        "elapsed_ms": int(elapsed_ms),
    }
    try:
        with _LOCK:
            with open(data_path("mcp_audit.log"), "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass
