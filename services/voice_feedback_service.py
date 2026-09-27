# services/voice_feedback_service.py
"""语音回执验证：播报后从车机 logcat 抓反馈文案，判定这条语音是否被正确识别。

背景（声学耦合的补全）：
    语音播报是「电脑扬声器放音 → 车机麦克风拾取 → 车机自己的语音助手处理」，
    工具侧不跟车机做协议对接。之前播完就结束，无法确认车机到底听没听清、
    有没有执行。本服务补上「回执」一环：车机语音助手处理完一句后，通常会在
    系统 logcat 里打印反馈文案（识别到了什么 / 执行了什么 / 还是没听清），
    播报后抓这段日志，按成功/失败关键词判定，失败时把车机反馈原文带出来。

设计约定：
    1. 不依赖 uiautomator2，直接用内置 adb 子进程 —— 语音播报页与自动化执行
       共用，且不要求持有 uiautomator2 的设备对象。
    2. 规则（log tag / 成功词 / 失败词 / 是否开启）由设置页配置，存在
       voice_data.json 的 settings.verify 段，这里只做执行，不背规则。
    3. 抓取失败（没连设备 / adb 报错）与「抓到但没命中关键词」要区分开：
       前者是环境问题，调用方应跳过验证；后者按「未知 = 判定失败」处理。
"""
import subprocess
import sys

from utils.adb_path import get_adb_path


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

        tag 为空时抓全量日志（按关键词判定）；填了 tag 则用 `-s tag` 只抓该 tag，
        日志更干净、判定更准，代价是必须先知道车机语音助手的 tag。
        """
        cmd = ["logcat", "-d"]
        if tag:
            cmd += ["-s", tag]
        cmd += ["-v", "time"]
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

        未知（没命中任何关键词）按「未验证到 = 失败」处理，message 里带上
        抓到的日志片段，方便用户判断车机到底回了什么。
        """
        result, feedback = self.judge(lines, success_keywords, fail_keywords)
        if result == RESULT_SUCCESS:
            return True, "", feedback
        if result == RESULT_FAIL:
            snippet = " | ".join(feedback[:3])
            return False, f"车机语音反馈疑似失败：{snippet}", feedback
        # unknown：抓到日志但没命中关键词
        if feedback:
            detail = "\n".join(feedback[:6])
            return False, f"未匹配到车机反馈关键词，无法确认识别结果：\n{detail}", feedback
        return False, "未抓到车机语音回执，无法确认识别结果", feedback
