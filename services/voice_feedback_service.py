# services/voice_feedback_service.py
"""语音回执验证：播报后从车机 logcat 抓反馈文案，判定这条语音是否被正确识别。

背景（声学耦合的补全）：
    语音播报是「电脑扬声器放音 → 车机麦克风拾取 → 车机自己的语音助手处理」，
    工具侧不跟车机做协议对接。之前播完就结束，无法确认车机到底听没听清、
    有没有执行。本服务补上「回执」一环：车机语音助手处理完一句后，通常会在
    系统 logcat 里打印反馈文案（识别到了什么 / 执行了什么 / 还是没听清），
    播报后抓这段日志，按成功/失败关键词判定。判定结论里**不带日志原文**
    （只说是哪个关键词命中 / 抓了多少行），完整原文从 judge_result 的第 3 个
    返回值带出去，供调用方按需展示。

设计约定：
    1. 不依赖 uiautomator2，直接用内置 adb 子进程 —— 语音播报页与自动化执行
       共用，且不要求持有 uiautomator2 的设备对象。
    2. 规则（log tag / 成功词 / 失败词 / 是否开启）由设置页配置，存在
       voice_data.json 的 settings.verify 段，这里只做执行，不背规则。
       log tag 支持一次填多个（空格 / 逗号分隔），见 split_tags。
    3. 抓取失败（没连设备 / adb 报错）与「抓到但没命中关键词」要区分开：
       前者是环境问题，调用方应跳过验证；后者按「未知 = 判定失败」处理。
"""
import re
import subprocess
import sys

from utils.adb_path import get_adb_path


def split_tags(tag) -> list:
    """把设置页填的「日志标签」拆成多个 tag：空格、英文逗号、中文逗号都认。

    为什么要支持多个：一台车机上，语音的反馈文案常常分散在几个 tag 上 ——
    助手自身的话术、TTS 业务层、状态机各打各的，只填一个很容易漏判（例如某车型
    助手的 tip 通道只打「听到了什么」，回复正文却在 TTS 业务层）。
    """
    if not tag:
        return []
    return [t for t in re.split(r"[,，\s]+", str(tag).strip()) if t]


class VoiceFeedbackError(Exception):
    """回执抓取本身失败（无设备 / adb 错误）。str(e) 是简短原因。"""


# 判定结果三态
RESULT_SUCCESS = "success"   # 命中成功关键词
RESULT_FAIL = "fail"         # 命中失败关键词
RESULT_UNKNOWN = "unknown"   # 抓到日志但没命中任何关键词


class VoiceFeedbackService:
    """基于 adb logcat 的车机语音回执抓取与判定。"""

    def __init__(self, serial=None):
        # serial 为空时用 adb 的默认设备（测试环境通常只有一个车机在线）
        self.serial = serial

    # ---------------- adb 通道 ----------------
    def _adb(self, args, timeout=8) -> str:
        cmd = [get_adb_path()]
        if self.serial:
            cmd += ["-s", self.serial]
        cmd += args
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                encoding="utf-8", errors="replace",
            )
        except subprocess.TimeoutExpired:
            raise VoiceFeedbackError("读取车机日志超时")
        except Exception as e:
            raise VoiceFeedbackError(f"无法调用 adb：{str(e)[:80]}")
        if result.returncode != 0:
            err = (result.stderr or "").strip()
            if err:
                raise VoiceFeedbackError(f"adb 报错：{err[:120]}")
        return result.stdout or ""

    # ---------------- 抓取 ----------------
    def clear(self):
        """播报前清空 logcat，建立时间基线。失败抛 VoiceFeedbackError。"""
        self._adb(["logcat", "-c"], timeout=8)

    def capture(self, tag=None) -> list:
        """抓取清基线以来的日志，返回文本行列表（已去空行）。失败抛 VoiceFeedbackError。

        tag 支持填多个（空格 / 逗号分隔），会转成多个 logcat filterspec，一行
        `logcat -d -v time -s A B` 就能同时抓几个 tag；留空则抓全量日志。
        建议至少填一个：全量里混着别的应用，既慢、又容易被无关日志里的词误判。
        """
        cmd = ["logcat", "-d", "-v", "time"]
        tags = split_tags(tag)
        if tags:
            cmd += ["-s", *tags]
        out = self._adb(cmd, timeout=10)
        return [ln for ln in out.splitlines() if ln.strip()]

    # ---------------- 判定 ----------------
    @staticmethod
    def judge(lines, success_keywords, fail_keywords):
        """按关键词判定。返回 (result, feedback_texts)。

        result 是 RESULT_SUCCESS / RESULT_FAIL / RESULT_UNKNOWN 之一；
        feedback_texts 是命中的那部分日志（unknown 时是全部日志，供人工判断）。
        失败优先：一行日志同时含成功词和失败词时判失败（保守，避免假通过）。
        """
        success_keywords = [k for k in (success_keywords or []) if k]
        fail_keywords = [k for k in (fail_keywords or []) if k]

        if fail_keywords:
            fail_hits = [ln for ln in lines if any(k in ln for k in fail_keywords)]
            if fail_hits:
                return RESULT_FAIL, fail_hits

        if success_keywords:
            success_hits = [ln for ln in lines if any(k in ln for k in success_keywords)]
            if success_hits:
                return RESULT_SUCCESS, success_hits

        return RESULT_UNKNOWN, lines

    def judge_result(self, lines, success_keywords, fail_keywords):
        """判定并转成「(ok, message, feedback_texts)」—— 调用方只关心这个。

        未知（没命中任何关键词）按「未验证到 = 失败」处理。

        message 里**不放车机日志原文**：这段文字会直接写进左下角「虫师日志」，
        而原始行带着时间戳/PID，且大多是 `onPlayBegin() called`、`isJokeState`
        这类与判断无关的内容，糊进去只会刷屏。所以只给结论：
        失败时说是哪个失败关键词命中了，未命中时报抓了多少行。
        完整原文仍从第 3 个返回值带出去，将来要做「查看详情」直接用它。
        """
        result, feedback = self.judge(lines, success_keywords, fail_keywords)
        if result == RESULT_SUCCESS:
            return True, "", feedback
        if result == RESULT_FAIL:
            # 关键词本身（如「没听清」）就是车机回话的要点，比整行日志更好读
            hits = [k for k in (fail_keywords or [])
                    if k and any(k in ln for ln in feedback)]
            what = "、".join(f"「{k}」" for k in hits) if hits else "失败关键词"
            return False, f"车机语音反馈疑似失败：车机回话命中{what}", feedback
        # unknown：抓到日志但没命中关键词
        if feedback:
            return False, f"未匹配到车机反馈关键词（本次抓取 {len(feedback)} 行日志）", feedback
        return False, "未抓到车机语音回执，无法确认识别结果", feedback
