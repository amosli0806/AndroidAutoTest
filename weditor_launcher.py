# -*- coding: utf-8 -*-
"""weditor 独立启动器（由 weditor.spec 打成 tools/weditor.exe 随主包分发）。

为什么需要它：打包版的 `sys.executable` 是虫师.exe，不支持 `-m weditor`；
打包版的应用可视化必须 spawn 独立的 weditor.exe（见 services/weditor_service.py）。
"""
from weditor.__main__ import main

if __name__ == "__main__":
    main()
