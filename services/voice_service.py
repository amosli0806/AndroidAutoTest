# services/voice_service.py
"""语音播报服务：把文案用电脑扬声器读出来，供车机语音助手拾取。

能力定位（重要）：
    这是「声学耦合」——电脑把语音从扬声器放出来，车机麦克风拾取后交给它自己的
    语音助手处理。工具侧只负责把声音从**正确的扬声器**播出去，
    不跟车机做任何协议对接。典型用法：先播「你好，小爱同学」唤醒，
    隔一会儿再播「打开地图」。

设计约定：
    1. 引擎可插拔：v1 只实现 Windows 内置 SAPI（离线、零新增依赖，
       pywin32 本来就在依赖里）。以后要加云端 TTS 或播报本地音频文件，
       再实现一个同样接口的引擎即可。
    2. speak() 默认**阻塞到播完** —— 用例执行时后续步骤必须等这段语音放完，
       否则整段序列会抢跑崩塌。
    3. 失败一律抛 VoiceError，str(e) 就是给用户看的那句话。
    4. SAPI 是 COM 组件：用例执行跑在 QThread 里，所以每个线程各自
       CoInitialize + 各自持有一个 SpVoice 实例（threading.local），不跨线程共享。
       界面上的「停止」用的是异步播报 + 轮询等待，这样才能打断当前这句。
"""
import threading
import time

# SAPI 的音频输出设备类别（枚举扬声器要用）
SAPI_AUDIO_OUTPUT_CATEGORY = (
    r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech\AudioOutput"
)

# SpVoice.Speak 的 flags
SVSF_DEFAULT = 0            # 同步，播完才返回
SVSF_ASYNC = 1
SVSFPURGE_BEFORE_SPEAK = 2  # 打断当前这句

# 语速取值范围（SAPI 规定）
RATE_MIN, RATE_MAX = -10, 10

# SAPI 音量固定拉满：响度直接交给电脑的系统音量控制，
# 所以界面上不再提供音量调节（两处音量会打架，反而不好判断到底该调哪个）。
SAPI_FULL_VOLUME = 100


class VoiceError(Exception):
    """播报失败。str(e) 是给用户看的简短原因。"""


class WindowsSapiEngine:
    """Windows 内置 SAPI 引擎（pywin32 / COM）。

    音色与输出设备都通过 token 设置；设置保存在 self._cfg 里，
    按线程新建 SpVoice 时会自动套用，这样界面上选的音色/音量/扬声器
    对「用例执行线程」同样生效。
    """

    key = "windows_sapi"
    label = "Windows 内置语音"

    def __init__(self):
        self._local = threading.local()
        self._cfg = {
            "voice_id": None,
            "rate": 0,
            "device_id": None,
        }

    # ---------------- 可用性 ----------------
    @staticmethod
    def is_available() -> bool:
        try:
            import pythoncom          # noqa: F401
            import win32com.client    # noqa: F401
        except ImportError:
            return False
        # 能建出 SpVoice 才算真的可用（有些精简系统会注册类缺失）
        try:
            import pythoncom
            import win32com.client
            pythoncom.CoInitialize()
            win32com.client.Dispatch("SAPI.SpVoice")
            return True
        except Exception:
            return False

    # ---------------- 线程内实例 ----------------
    def _voice(self):
        """取当前线程的 SpVoice；COM 对象不能跨线程共享，按线程各建一个。"""
        voice = getattr(self._local, "voice", None)
        if voice is None:
            try:
                import pythoncom
                import win32com.client
                pythoncom.CoInitialize()
                voice = win32com.client.Dispatch("SAPI.SpVoice")
            except Exception as e:
                raise VoiceError(f"无法初始化 Windows 语音引擎：{str(e)[:80]}")
            self._local.voice = voice
            self._apply_cfg(voice)
        return voice

    def _apply_cfg(self, voice):
        try:
            if self._cfg["voice_id"]:
                self._select_voice(voice, self._cfg["voice_id"])
            voice.Rate = self._cfg["rate"]
            # 音量不给用户调，固定拉满，响度由系统音量决定
            voice.Volume = SAPI_FULL_VOLUME
            if self._cfg["device_id"]:
                self._select_device(voice, self._cfg["device_id"])
        except Exception:
            # 套用失败不能拦住播报：退回系统默认音色/设备
            pass

    # ---------------- token 选择 ----------------
    @staticmethod
    def _select_voice(voice, voice_id) -> bool:
        tokens = voice.GetVoices()
        for i in range(tokens.Count):
            tok = tokens.Item(i)
            if tok.Id == voice_id:
                voice.Voice = tok
                return True
        return False

    def _audio_tokens(self):
        """枚举音频输出设备 token。顺带把 category 挂在 thread local 上，
        避免 token 因 category 被回收而失效。"""
        import win32com.client
        category = win32com.client.Dispatch("SAPI.SpObjectTokenCategory")
        category.SetId(SAPI_AUDIO_OUTPUT_CATEGORY, False)
        self._local.audio_category = category
        self._local.audio_tokens = category.EnumerateTokens()
        return self._local.audio_tokens

    def _select_device(self, voice, device_id) -> bool:
        tokens = self._audio_tokens()
        for i in range(tokens.Count):
            tok = tokens.Item(i)
            if tok.Id == device_id:
                voice.AudioOutput = tok
                return True
        return False

    # ---------------- 查询 ----------------
    def list_voices(self):
        voice = self._voice()
        tokens = voice.GetVoices()
        return [{"id": tokens.Item(i).Id, "label": tokens.Item(i).GetDescription()}
                for i in range(tokens.Count)]

    def list_output_devices(self):
        self._voice()          # 确保本线程已初始化
        tokens = self._audio_tokens()
        return [{"id": tokens.Item(i).Id, "label": tokens.Item(i).GetDescription()}
                for i in range(tokens.Count)]

    def current_voice_id(self) -> str:
        try:
            return self._voice().Voice.Id
        except Exception:
            return ""

    def current_device_id(self) -> str:
        try:
            return self._voice().AudioOutput.Id
        except Exception:
            return ""

    # ---------------- 配置 ----------------
    def set_voice(self, voice_id):
        self._cfg["voice_id"] = voice_id or None
        voice = self._voice()
        if voice_id and not self._select_voice(voice, voice_id):
            raise VoiceError("选中的音色在本机不存在")

    def set_rate(self, rate):
        self._cfg["rate"] = max(RATE_MIN, min(RATE_MAX, int(rate)))
        self._voice().Rate = self._cfg["rate"]

    def set_output_device(self, device_id):
        self._cfg["device_id"] = device_id or None
        if device_id:
            voice = self._voice()
            if not self._select_device(voice, device_id):
                raise VoiceError("选中的输出设备在本机不存在")

    def get_cfg(self) -> dict:
        return dict(self._cfg)

    def apply_cfg(self, cfg: dict):
        """批量套用配置（界面启动时把持久化的设置灌进来）"""
        if not cfg:
            return
        if cfg.get("voice_id"):
            self.set_voice(cfg["voice_id"])
        if cfg.get("rate") is not None:
            self.set_rate(cfg["rate"])
        if cfg.get("device_id"):
            self.set_output_device(cfg["device_id"])

    # ---------------- 播报 ----------------
    def speak(self, text) -> float:
        """同步播报，播完才返回；返回耗时秒数。空文案直接返回 0。"""
        text = (text or "").strip()
        if not text:
            return 0.0
        voice = self._voice()
        started = time.time()
        try:
            voice.Speak(text, SVSF_DEFAULT)
        except Exception as e:
            raise VoiceError(f"播报失败：{str(e)[:80]}")
        return time.time() - started

    def speak_async(self, text):
        """异步起播（界面「停止」要能打断当前这句，所以不能同步等）"""
        text = (text or "").strip()
        if not text:
            return
        try:
            self._voice().Speak(text, SVSF_ASYNC)
        except Exception as e:
            raise VoiceError(f"播报失败：{str(e)[:80]}")

    def wait_done(self, timeout_ms: int = 100) -> bool:
        """等待异步播报结束；返回 True 表示已播完。"""
        try:
            return bool(self._voice().WaitUntilDone(timeout_ms))
        except Exception:
            return True

    def stop(self):
        """打断当前这句（异步 + 清空待播队列）"""
        try:
            self._voice().Speak(
                "", SVSF_ASYNC | SVSFPURGE_BEFORE_SPEAK)
        except Exception:
            pass


class VoiceService:
    """语音播报的统一入口。引擎可插拔，v1 只有 Windows 内置 SAPI。"""

    def __init__(self, engine=None):
        self._engine = engine
        self._engine_resolved = engine is not None

    def _get_engine(self):
        if not self._engine_resolved:
            self._engine_resolved = True
            if WindowsSapiEngine.is_available():
                self._engine = WindowsSapiEngine()
        return self._engine

    def is_available(self) -> bool:
        return self._get_engine() is not None

    def engine_label(self) -> str:
        engine = self._get_engine()
        return engine.label if engine else "不可用"

    def _require(self):
        engine = self._get_engine()
        if engine is None:
            raise VoiceError("本机没有可用的语音引擎（需要 Windows 内置 TTS）")
        return engine

    # ---------------- 透传 ----------------
    def list_voices(self):
        return self._require().list_voices()

    def list_output_devices(self):
        return self._require().list_output_devices()

    def current_voice_id(self) -> str:
        return self._require().current_voice_id()

    def current_device_id(self) -> str:
        return self._require().current_device_id()

    def set_voice(self, voice_id):
        self._require().set_voice(voice_id)

    def set_rate(self, rate):
        self._require().set_rate(rate)

    def set_output_device(self, device_id):
        self._require().set_output_device(device_id)

    def get_cfg(self) -> dict:
        return self._require().get_cfg()

    def apply_cfg(self, cfg):
        self._require().apply_cfg(cfg)

    def speak(self, text) -> float:
        """阻塞播报（用例步骤用）。返回耗时秒数。"""
        return self._require().speak(text)

    def speak_async(self, text):
        self._require().speak_async(text)

    def wait_done(self, timeout_ms: int = 100) -> bool:
        return self._require().wait_done(timeout_ms)

    def stop(self):
        self._require().stop()


_service = None
_service_lock = threading.Lock()


def get_voice_service() -> VoiceService:
    """进程内共享一个 VoiceService（引擎内部按线程各自持有 SpVoice）。"""
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = VoiceService()
    return _service
