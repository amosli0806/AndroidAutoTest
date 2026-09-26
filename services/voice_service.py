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
import os
import tempfile

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


class EdgeTtsEngine(WindowsSapiEngine):
    """微软 Edge 在线语音（edge-tts），音色多、自然度高，需要联网。

    合成用 edge-tts（异步），播放复用 SAPI 的 SpVoice —— SpVoice 在这里只当
    「能指定输出设备、可被打断」的播放器，不做 TTS 合成。所以本引擎仍依赖
    Windows 的 SAPI 组件（虫师本就依赖 pywin32），只是不再依赖系统的 TTS 音色：
    没有中文音色的机器，也能用 edge 在线音色念出标准中文。
    """

    key = "edge_tts"
    label = "Edge 在线语音"

    # edge-tts 有效语速范围（百分比字符串），映射自 SAPI 的 -10~10
    _RATE_PCT_MIN, _RATE_PCT_MAX = -50, 100
    # 无指定音色时的默认（晓晓，中文女声）
    _DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"

    # 中文音色官方命名映射（ShortName 人名段 -> 中文名），展示给用户的是中文
    _ZH_VOICE_NAMES = {
        "Xiaoxiao": "晓晓", "Xiaoyi": "晓伊", "Yunjian": "云健", "Yunxi": "云希",
        "Yunxia": "云夏", "Yunyang": "云扬", "Xiaobei": "晓北", "Xiaoni": "晓妮",
        "HiuGaai": "曌佳", "HiuMaan": "曌曼", "WanLung": "云龙",
        "HsiaoChen": "晓臣", "HsiaoYu": "晓雨", "YunJhe": "云哲",
    }
    # 地区文案（Locale -> 中文描述）
    _ZH_LOCALE_LABELS = {
        "zh-CN": "普通话", "zh-CN-liaoning": "辽宁话", "zh-CN-shaanxi": "陕西话",
        "zh-HK": "粤语", "zh-TW": "台湾话",
    }

    def __init__(self):
        super().__init__()
        self._async_state = "idle"     # idle / synthing / playing / done
        self._cancel = False

    # ---------------- 可用性 ----------------
    _voices_cache = None   # 进程级音色缓存（edge 音色列表是网络拉取，拉一次复用）

    @staticmethod
    def is_available() -> bool:
        # 用 find_spec 只检查模块是否存在，不真正 import ——
        # 设置页打开时会遍历所有引擎的可用性，若这里真 import edge_tts/aiohttp
        # 会冷加载几百 ms~1s，导致设置页「等几秒才弹」。
        import importlib.util
        return importlib.util.find_spec("edge_tts") is not None

    # ---------------- 配置覆盖 ----------------
    def _apply_cfg(self, voice):
        # SpVoice 只当播放器：不设 TTS 音色 token、不设 Rate（语速在合成侧控制），
        # 只套输出设备与音量
        try:
            voice.Volume = SAPI_FULL_VOLUME
            if self._cfg["device_id"]:
                self._select_device(voice, self._cfg["device_id"])
        except Exception:
            pass

    def set_rate(self, rate):
        # edge 语速是合成参数，不落 SpVoice.Rate
        self._cfg["rate"] = max(RATE_MIN, min(RATE_MAX, int(rate)))

    def set_voice(self, voice_id):
        self._cfg["voice_id"] = voice_id or None

    def _rate_str(self) -> str:
        pct = max(self._RATE_PCT_MIN, min(self._RATE_PCT_MAX, int(self._cfg["rate"]) * 10))
        return f"{'+' if pct >= 0 else ''}{pct}%"

    # ---------------- 音色列表 ----------------
    def list_voices(self):
        import edge_tts
        if EdgeTtsEngine._voices_cache is None:
            voices = _run_async(edge_tts.list_voices())
            # 只保留中国相关音色（普通话/辽宁/陕西/粤语/台湾），且展示为中文文案
            # —— 322 个音色里绝大多数是外语，对车机中文播报毫无用处，全列出来
            #    只会让用户在几百项里翻找
            items = []
            for v in voices:
                locale = str(v.get("Locale", ""))
                if not locale.startswith("zh-"):
                    continue
                short = v["ShortName"]
                stem = short.rsplit("-", 1)[-1].replace("Neural", "")
                name = self._ZH_VOICE_NAMES.get(stem, stem)
                gender = "女" if v.get("Gender") == "Female" else "男"
                area = self._ZH_LOCALE_LABELS.get(locale, locale)
                items.append({"id": short, "label": f"{name}（{gender}·{area}）"})
            items.sort(key=lambda x: x["id"])   # zh-CN < zh-CN-liaoning < ... < zh-TW，顺序自然合理
            EdgeTtsEngine._voices_cache = items
        return EdgeTtsEngine._voices_cache

    # ---------------- 合成 ----------------
    # 合成超时：国内到微软语音服务的连接时好时坏（实测最长挂 20s+ 才报
    # ConnectionTimeoutError），必须设上限让失败尽快浮出来
    _SYNTH_TIMEOUT = 12

    def _synth_wav(self, text, wav_path):
        """edge-tts 合成（内存中收 mp3 块）→ miniaudio 解码 → 写 wav。

        为什么不直接把 mp3 交给 SAPI 播：SpFileStream 播 mp3 依赖系统 ACM
        解码器，实测会截断（「你好本田」只念出个「在」）甚至无声（1.1.8 反馈）。
        miniaudio 自带 mp3 解码，在内存里转成标准 wav，SAPI 播 wav 是原生路径。
        """
        import edge_tts
        voice_id = self._cfg.get("voice_id") or self._DEFAULT_VOICE
        communicate = edge_tts.Communicate(text, voice_id, rate=self._rate_str())
        mp3 = bytearray()

        async def _collect():
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    mp3.extend(chunk["data"])

        try:
            _run_async(_collect(), timeout=self._SYNTH_TIMEOUT)
        except Exception as e:
            raise VoiceError(
                f"在线语音合成失败（{str(e)[:60]}）—— 请检查网络，"
                "或在设置里切换回「Windows 内置语音」")
        if not mp3:
            raise VoiceError("在线语音合成失败（服务未返回音频，请检查网络）")
        _decode_mp3_to_wav(bytes(mp3), wav_path)

    def _play_file(self, path, flags):
        import win32com.client
        voice = self._voice()
        stream = win32com.client.Dispatch("SAPI.SpFileStream")
        stream.Open(path)
        try:
            voice.SpeakStream(stream, flags)
        finally:
            stream.Close()

    # ---------------- 播报 ----------------
    def speak(self, text) -> float:
        """同步：合成 + 播放，播完才返回。"""
        text = (text or "").strip()
        if not text:
            return 0.0
        started = time.time()
        fd, wav_path = tempfile.mkstemp(prefix="chongshi_edge_", suffix=".wav")
        os.close(fd)
        try:
            self._synth_wav(text, wav_path)
            self._play_file(wav_path, SVSF_DEFAULT)
        finally:
            try:
                os.remove(wav_path)
            except Exception:
                pass
        return time.time() - started

    def speak_async(self, text):
        text = (text or "").strip()
        if not text:
            return
        self._cancel = False
        self._async_state = "synthing"
        # 唯一临时文件：固定名会被「上一条还在播、下一条开始合成」的覆盖冲突打坏
        import uuid
        self._async_wav_path = os.path.join(
            tempfile.gettempdir(), f"chongshi_edge_{uuid.uuid4().hex[:8]}.wav")
        threading.Thread(target=self._async_worker, args=(text,), daemon=True).start()

    def _async_worker(self, text):
        try:
            self._synth_wav(text, self._async_wav_path)
            if self._cancel:
                self._async_state = "done"
                return
            self._async_state = "playing"
            self._play_file(self._async_wav_path, SVSF_ASYNC)
        except Exception as e:
            print(f"[weditor/edge] 异步播报失败: {type(e).__name__}: {str(e)[:150]}")
            self._async_state = "done"

    def _cleanup_async_wav(self):
        path = getattr(self, "_async_wav_path", None)
        if path:
            try:
                os.remove(path)
            except Exception:
                pass
            self._async_wav_path = None

    def wait_done(self, timeout_ms: int = 100) -> bool:
        """区分「合成中 / 播放中 / 完成」——合成有网络延迟，不能只看 SpVoice。"""
        state = self._async_state
        if state == "synthing":
            return False
        if state == "playing":
            try:
                if self._voice().WaitUntilDone(timeout_ms):
                    self._async_state = "done"
                    self._cleanup_async_wav()
                    return True
            except Exception:
                self._async_state = "done"
                self._cleanup_async_wav()
                return True
            return False
        return True

    def stop(self):
        self._cancel = True
        self._async_state = "done"
        self._cleanup_async_wav()
        try:
            self._voice().Speak("", SVSF_ASYNC | SVSFPURGE_BEFORE_SPEAK)
        except Exception:
            pass


def _run_async(coro, timeout=None):
    """在当前线程独立跑一个 asyncio 协程，同步等待结果（可设超时）。

    edge-tts 是异步库，播报跑在 QThread 里，每个线程都要独立 event loop。
    用 new_event_loop 而非 asyncio.run，避免「当前线程已有 loop」时的冲突。
    """
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        if timeout:
            return loop.run_until_complete(asyncio.wait_for(coro, timeout=timeout))
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _decode_mp3_to_wav(mp3_bytes: bytes, wav_path: str):
    """mp3 bytes -> miniaudio 解码 -> 标准 16bit wav 文件。

    edge-tts 输出固定是 mp3（硬编码），SAPI 的 SpFileStream 播 mp3 依赖系统
    ACM 解码器、实测会截断/无声；miniaudio 自带 mp3 解码，转成 wav 后走
    SAPI 原生播放路径，完整可靠。
    """
    import wave
    import miniaudio
    decoded = miniaudio.decode(mp3_bytes, nchannels=1, sample_rate=24000,
                               output_format=miniaudio.SampleFormat.SIGNED16)
    with wave.open(wav_path, "wb") as w:
        w.setnchannels(decoded.nchannels)
        w.setsampwidth(2)      # SIGNED16
        w.setframerate(decoded.sample_rate)
        w.writeframes(decoded.samples.tobytes())


class VoiceService:
    """语音播报的统一入口。引擎可插拔：Windows 内置 SAPI / Edge 在线语音。"""

    # 引擎注册表（顺序即默认优先级）
    ENGINE_CLASSES = [WindowsSapiEngine, EdgeTtsEngine]

    def __init__(self, engine=None):
        self._engine = engine
        self._engine_resolved = engine is not None
        self._engine_key = None

    def _get_engine(self):
        if not self._engine_resolved:
            self._engine_resolved = True
            # 按配置 key 解析；未指定则按注册表顺序选第一个可用的（SAPI 优先）
            for cls in self.ENGINE_CLASSES:
                if cls.is_available() and (self._engine_key is None or cls.key == self._engine_key):
                    self._engine = cls()
                    self._engine_key = cls.key
                    break
        return self._engine

    def available_engines(self) -> list:
        """返回 [{key, label, available}]，供设置页引擎下拉使用。"""
        return [{"key": c.key, "label": c.label, "available": c.is_available()}
                for c in self.ENGINE_CLASSES]

    def current_engine_key(self) -> str:
        engine = self._get_engine()
        return engine.key if engine else ""

    def set_engine(self, key) -> bool:
        """切换到指定引擎（不可用则返回 False）。切换后需重新 set_voice 选音色。"""
        for cls in self.ENGINE_CLASSES:
            if cls.key == key and cls.is_available():
                self._engine = cls()
                self._engine_key = key
                self._engine_resolved = True
                return True
        return False

    def is_available(self) -> bool:
        return self._get_engine() is not None

    def engine_label(self) -> str:
        engine = self._get_engine()
        return engine.label if engine else "不可用"

    def _require(self):
        engine = self._get_engine()
        if engine is None:
            raise VoiceError("本机没有可用的语音引擎（需要 Windows 内置 TTS 或联网使用 Edge 在线语音）")
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
        """批量套用配置。cfg 可含 engine（引擎 key）、voice_id、rate、device_id。"""
        if not cfg:
            return
        # 先定引擎，再灌该引擎自己的配置
        engine_key = cfg.get("engine") or ""
        if engine_key:
            if self.set_engine(engine_key):
                pass
            elif not self._get_engine():
                return
        engine = self._require()
        if cfg.get("voice_id"):
            engine.set_voice(cfg["voice_id"])
        if cfg.get("rate") is not None:
            engine.set_rate(cfg["rate"])
        if cfg.get("device_id"):
            engine.set_output_device(cfg["device_id"])

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
