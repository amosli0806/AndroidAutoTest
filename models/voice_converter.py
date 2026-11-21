# models/voice_converter.py
"""语音用例 → 自动化用例的步骤映射。

单独抽出来是为了能测：转换会写项目树与步骤文件，混在视图方法里没法验证，
出错就得靠人肉回滚。这里只做纯计算，调用方负责落库。

映射规则（一一对应，无损）：
    wake    → voice          播报当前唤醒词
    phrase  → voice          播报文案 + 播后等待
    verify  → voice_receipt  抓 logcat 判定回执（关键字）
    assert  → assert         界面断言（参数原样带过去）

不做的方向：自动化 → 语音。点击操作没法自动变成语音指令，除非维护一张
「点 X 等于说 Y」的映射表，那是无底洞。
"""
from typing import List, Tuple

from models.step_model import Step
from models.voice_model import (KIND_ASSERT, KIND_PHRASE, KIND_VERIFY, KIND_WAKE,
                                WAKE_DELAY)


def _short(text: str, limit: int = 12) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def convert_case_to_steps(case, wake_word: str) -> Tuple[List[Step], int]:
    """把一条语音用例的步骤转成自动化步骤。

    返回 (步骤列表, 跳过的空回执数)。跳过计数用于给用户一句诚实的提示 ——
    配了检测步骤却没填关键词的，转过去也验不了，不如明说。
    """
    wake_word = (wake_word or "").strip()
    steps: List[Step] = []
    skipped_verify = 0

    for st in getattr(case, "steps", []) or []:
        if st.kind == KIND_WAKE:
            if not wake_word:
                continue
            steps.append(Step(id=-1, type="voice", name=f"播报 {wake_word}",
                              params={"voiceText": wake_word,
                                      "afterDelay": float(st.delay or WAKE_DELAY)}))
        elif st.kind == KIND_PHRASE:
            text = (st.text or "").strip()
            if not text:
                continue
            steps.append(Step(id=-1, type="voice", name=f"播报 {_short(text)}",
                              params={"voiceText": text,
                                      "afterDelay": float(st.delay or 0)}))
        elif st.kind == KIND_VERIFY:
            kws = [k for k in (st.keywords or []) if str(k).strip()]
            if not kws:
                skipped_verify += 1
                continue
            steps.append(Step(id=-1, type="voice_receipt",
                              name=f"回执 {_short('、'.join(kws[:2]))}",
                              params={"keywords": "，".join(kws), "timeout": 8}))
        elif st.kind == KIND_ASSERT:
            params = dict(st.params or {})
            if not params.get("locationValue"):
                continue
            steps.append(Step(id=-1, type="assert", name="界面断言",
                              params=params))

    return steps, skipped_verify
