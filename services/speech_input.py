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
import shutil
import sys
import time

from PyQt6.QtCore import QThread, pyqtSignal

SAMPLE_RATE = 16000
# 峰值低于这个值认为「麦克风没收到任何声音」—— 环境底噪通常也有几百，
# 设太低会把静音误判成"有声音但没听清"，给不出正确的排查方向
MIN_PEAK = 500

MODEL_NAME = "vosk-model-small-cn-0.22"


def _model_present(path) -> bool:
    return bool(path) and os.path.isfile(os.path.join(path, "am", "final.mdl"))


def _ascii_cache_root() -> str:
    """Vosk/Kaldi 的 Windows 原生实现不支持非 ASCII 路径。

    安装目录（D:\\虫师）和旧版用户目录（%LOCALAPPDATA%\\虫师）都可能含中文，
    直接传给 Model() 会报 FileNotFoundError。这里选一个全 ASCII、普通用户
    可写的位置缓存模型；Public Documents 在任何 Windows 上都是 ASCII 路径。
    """
    public = os.environ.get("PUBLIC") or r"C:\Users\Public"
    candidates = [
        os.path.join(public, "Documents", "Chongshi", "models"),
        os.path.join(os.environ.get("PROGRAMDATA") or r"C:\ProgramData",
                     "Chongshi", "models"),
    ]
    for path in candidates:
        try:
            os.makedirs(path, exist_ok=True)
            return path
        except Exception:
            continue
    local = os.environ.get("LOCALAPPDATA")
    return os.path.join(local, "Chongshi", "models") if local else ""


def _source_candidates():
    """可能存放模型的目录（未做 ASCII 过滤）。"""
    env = (os.environ.get("CHONGSHI_VOSK_MODEL") or "").strip()
    if env:
        yield env
    # PyInstaller onedir：_MEIPASS 就是安装目录下的 _internal。模型必须放这里，
    # 因为 updater 只整目录替换 _internal（根目录只换 exe）。
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            yield os.path.join(meipass, "models", MODEL_NAME)
        app_dir = os.path.dirname(os.path.abspath(sys.executable))
        yield os.path.join(app_dir, "models", MODEL_NAME)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        yield os.path.join(local, "虫师", "models", MODEL_NAME)
        yield os.path.join(local, "Chongshi", "models", MODEL_NAME)
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    yield os.path.join(here, "_vosk_lab", MODEL_NAME)


def _copy_model(src: str, dst: str) -> str:
    """把非 ASCII 路径下的模型复制到 ASCII 缓存（只在首次使用时做一次）。"""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".copying"
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        shutil.copytree(src, tmp)
        if os.path.isdir(dst):
            shutil.rmtree(dst, ignore_errors=True)
        os.replace(tmp, dst)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return dst


def _find_installed_source() -> str:
    cache_root = _ascii_cache_root()
    cache = os.path.join(cache_root, MODEL_NAME) if cache_root else ""
    if _model_present(cache):
        return cache
    for path in _source_candidates():
        if _model_present(path):
            return path
    return ""


def model_installed() -> bool:
    """模型是否已随包/手动放置（只查文件，不触发复制；供界面快速判断）。"""
    return bool(_find_installed_source())


def resolve_model_dir() -> str:
    """返回可直接交给 vosk 的模型目录，保证路径为 ASCII。

    非 ASCII 来源（安装目录含中文、旧版 %LOCALAPPDATA%\\虫师）会先复制到
    Public Documents 下的 ASCII 缓存，再返回缓存路径。
    """
    source = _find_installed_source()
    if not source:
        return ""
    if source.isascii():
        return source
    cache_root = _ascii_cache_root()
    if not cache_root:
        return ""
    target = os.path.join(cache_root, MODEL_NAME)
    if _model_present(target):
        return target
    if not target.isascii():
        return ""
    try:
        return _copy_model(source, target)
    except Exception:
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
        # 不在 GUI 线程里解析/复制模型：首次可能要把 40MB+ 从安装目录拷到
        # ASCII 缓存，放到 run() 里做，避免点麦克风时界面卡一下。
        self.model_dir = model_dir or ""
        self.max_seconds = max(1.0, float(max_seconds or 8.0))
        self.device_id = device_id
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        if not self.model_dir:
            self.model_dir = resolve_model_dir()
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
