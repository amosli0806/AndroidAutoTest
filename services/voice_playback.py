# services/voice_playback.py
"""语音套件播放器（无 Qt 依赖）。

性能检测的「语音场景」= 边播语音边采集性能。GUI 侧用的是
`services/voice_player.VoicePlaybackWorker`（QThread），这里给 MCP 提供一份
纯 Python 版本：按顺序播报 `VoiceModel.build_play_items()` 产出的文案，
支持循环与外部停止；回执验证 / 唤醒重试暂不在 MCP 场景里做。
"""
from __future__ import annotations

import threading
import time
from typing import Optional

from models.voice_model import VoiceModel
from services.voice_service import get_voice_service


class VoicePlaybackRunner:
    def __init__(self, items: list, loop_count: int = 1,
                 stop_event: Optional[threading.Event] = None):
        self.items = list(items or [])
        self.loop_count = max(1, int(loop_count or 1))
        self._stop_evt = stop_event or threading.Event()
        self.played = 0
        self.rounds = 0
        self.error = ""

    def stop(self) -> None:
        self._stop_evt.set()

    def run(self) -> dict:
        if not self.items:
            raise ValueError("套件里没有可播报的文案")
        service = get_voice_service()
        if not service.is_available():
            raise RuntimeError("语音引擎不可用：检查设置页的语音播报引擎与输出设备")
        try:
            service.apply_cfg(dict(VoiceModel().settings or {}))
        except Exception:
            pass

        aborted = False
        try:
            for round_index in range(self.loop_count):
                if self._stop_evt.is_set():
                    aborted = True
                    break
                for item in self.items:
                    if self._stop_evt.is_set():
                        aborted = True
                        break
                    text = (item.text or "").strip()
                    delay = float(item.delay or 0) or 0.0
                    if not text:
                        continue
                    service.speak(text)
                    self.played += 1
                    if delay > 0:
                        self._stop_evt.wait(delay)
                self.rounds += 1
        except Exception as e:
            self.error = str(e)
            raise
        return {"items": self.played, "rounds": self.rounds,
                "total_items": len(self.items), "aborted": aborted}
