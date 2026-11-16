# mcp_server/logging_setup.py
"""stdio 协议进程的日志重定向。

stdout 必须只留给 MCP JSON-RPC；项目里还有约 200 处 print()，
所以这里把 builtins.print 整体改到 stderr 文件，而不是逐个改业务代码。
"""
from __future__ import annotations

import builtins
import logging
import sys
from typing import IO

from utils.app_paths import data_path

_LOG_FILE: IO | None = None
_ORIGINAL_PRINT = builtins.print


def setup_stdio_logging():
    global _LOG_FILE
    if _LOG_FILE is not None:
        return _LOG_FILE
    path = data_path("mcp.log")
    _LOG_FILE = open(path, "a", encoding="utf-8", buffering=1)
    sys.stderr = _LOG_FILE

    def _print(*args, **kwargs):
        kwargs.pop("file", None)
        _ORIGINAL_PRINT(*args, file=_LOG_FILE, **kwargs)

    builtins.print = _print
    logging.basicConfig(
        stream=_LOG_FILE,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return _LOG_FILE
