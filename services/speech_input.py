# services/speech_input.py
"""语音输入：麦克风录音 + 本地 vosk 识别，转成文字交给界面填入输入框。

与「语音播报」是两回事：
    播报是电脑往外说（TTS），这里是电脑听人说（ASR），互不干扰。

设计约定：
    1. **模型懒加载 + 全局缓存**：首次约 0.5 秒，之后复用同一个 Model；
       加载放在工作线程里，不阻塞 UI。
    2. **录音与识别都在 QThread**：miniaudio 的回调跑在音频线程，
       vosk 解码也吃 CPU，任何一步都不能放在界面线程。
    3. **只负责「录 + 转文字」**：填哪个框、插入还是替换，由视图决定。
    4. vosk / miniaudio 缺失时给出可读原因，不抛栈给用户。
"""
import os
import sys
import time

from PyQt6.QtCore import QThread, pyqtSignal

SAMPLE_RATE = 16000
# 峰值低于这个值认为「麦克风没收到任何声音」—— 环境底噪通常也有几百，
# 设太低会把静音误判成"有声音但没听清"，给不出正确的排查方向
MIN_PEAK = 500


def resolve_model_dir() -> str:
    """按优先级找中文模型目录，找不到返回空串。

    1. 环境变量 CHONGSHI_VOSK_MODEL（内网部署时指定共享盘最方便）
    2. 用户数据目录 %LOCALAPPDATA%\\虫师\\models\\...（正式分发走这里，不进安装包）
    3. 项目内 _vosk_lab/...（开发调试）
    """
    name = "vosk-model-small-cn-0.22"
    candidates = []
    env = (os.environ.get("CHONGSHI_VOSK_MODEL") or "").strip()
    if env:
        candidates.append(env)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(os.path.join(local, "虫师", "models", name))
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates.append(os.path.join(here, "_vosk_lab", name))
    for path in candidates:
        if path and os.path.isfile(os.path.join(path, "am", "final.mdl")):
            return path
    return ""


_model_cache = {"dir": "", "model": None}


def _load_model(model_dir: str):
    """加载并缓存 model；同一目录只解析一次。"""
    if _model_cache["model"] is not None and _model_cache["dir"] == model_dir:
        return _model_cache["model"]
    from vosk import Model, SetLogLevel
    SetLogLevel(-1)                     # 关掉 kaldi 的调试输出，避免刷屏
    model = Model(model_dir)
    _model_cache["dir"] = model_dir
    _model_cache["model"] = model
    return model


class SpeechInputWorker(QThread):
    """录一段音并识别，结果通过信号送回界面线程。

    生命周期：
        start() -> 录音中（可随时 stop()）-> 自动停止并识别 -> 发 recognized / failed
    超时（max_seconds）到点也会自动停止并识别，防止用户忘了点结束。
    """

    tick = pyqtSignal(float)            # 已录音秒数，供界面显示计时
    recognized = pyqtSignal(str)        # 识别出的文本（已去空格）
    failed = pyqtSignal(str)            # 失败原因（给用户看的一句话）

    def __init__(self, model_dir="", max_seconds=8.0, device_id=None, parent=None):
        super().__init__(parent)
        self.model_dir = model_dir or resolve_model_dir()
        self.max_seconds = max(1.0, float(max_seconds or 8.0))
        self.device_id = device_id
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        if not self.model_dir:
            self.failed.emit("未安装语音模型，无法识别")
            return
        try:
            import miniaudio
        except Exception as e:                       # noqa: BLE001
            self.failed.emit(f"录音组件不可用：{type(e).__name__}")
            return
        try:
            model = _load_model(self.model_dir)
        except Exception as e:                       # noqa: BLE001
            self.failed.emit(f"语音模型加载失败：{type(e).__name__}")
            return

        frames = []

        def gen():
            while True:
                data = yield
                if data:
                    frames.append(bytes(data))

        g = gen()
        next(g)
        try:
            dev = miniaudio.CaptureDevice(
                nchannels=1, sample_rate=SAMPLE_RATE,
                input_format=miniaudio.SampleFormat.SIGNED16,
                device_id=self.device_id)
            dev.start(g)
        except Exception as e:                       # noqa: BLE001
            self.failed.emit(f"麦克风打开失败：{type(e).__name__}")
            return

        t0 = time.time()
        try:
            while not self._stop:
                elapsed = time.time() - t0
                if elapsed >= self.max_seconds:
                    break
                self.tick.emit(elapsed)
                time.sleep(0.1)
        finally:
            try:
                dev.stop()
                dev.close()
            except Exception:
                pass

        pcm = b"".join(frames)
        if not pcm:
            self.failed.emit("没录到音频，检查麦克风权限")
            return

        try:
            import numpy as np
            samples = np.frombuffer(pcm, dtype=np.int16)
            peak = int(np.abs(samples).max()) if len(samples) else 0
        except Exception:
            peak = -1

        if 0 <= peak < MIN_PEAK:
            self.failed.emit("没听到声音，检查麦克风和系统音量")
            return

        try:
            import json
            from vosk import KaldiRecognizer
            rec = KaldiRecognizer(model, SAMPLE_RATE)
            rec.AcceptWaveform(pcm)
            text = (json.loads(rec.Result()).get("text") or "").replace(" ", "")
        except Exception as e:                       # noqa: BLE001
            self.failed.emit(f"识别失败：{type(e).__name__}")
            return

        if not text:
            self.failed.emit("没听清，再说一次")
            return
        self.recognized.emit(text)


def model_installed() -> bool:
    """给设置页显示状态用：模型是否就位。"""
    return bool(resolve_model_dir())
