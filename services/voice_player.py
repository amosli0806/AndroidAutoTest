# services/voice_player.py
"""语音播报执行器 —— 按顺序把一组播报念出来，可循环、可被打断。

**从 views/voice_view.py 抽出来的**（2026-11-01）：性能检测的「语音场景」
也要用它（边播语音边采集性能），而它原来长在语音页里、外面拿不到。
抽到 services 层后两处共用同一份实现，行为完全一致。

items 里每条是一个播报组：
    (case_id, 播报步骤下标, 文案, 播后等待, [紧随的检测步骤...], 用例名)
检测步骤元素为 (步骤下标, [成功关键词])。

**只有配了检测步骤的播报才会做回执验证**；预期结果没命中不会中断整轮，
只记一行 ❌ 日志并在收尾汇总。
"""

import time

from PyQt6.QtCore import QThread, pyqtSignal

from services.voice_service import VoiceError


class VoicePlaybackWorker(QThread):
    """按顺序执行若干「播报组」，可循环、可被 stop() 打断。

    items 里每条是一个播报组：
        (case_id, 播报步骤下标, 文案, 播后等待, [紧随的检测步骤...])
    检测步骤元素为 (步骤下标, [成功关键词]) —— 它是对前面那句播报的
    预期结果，播完抓车机日志逐条判定。带上下标是为了让界面能高亮
    "正在播的是哪一条"。

    **只有配了检测步骤的播报才会做回执验证**：没有检测步骤就没有预期结果
    可判，此时一律不验证（设置页的开关只是总闸，决定"配了检测步骤的用例
    要不要真验"）。

    **预期结果没命中不会中断整轮**：只记一行 ❌ 日志并累计，后面的步骤与用例
    照常跑完，收尾再汇总成一句"共 N 条预期结果未命中"。只有用户点「停止」或
    播报本身出错（超时等）才真的中断。
    """

    progress = pyqtSignal(str, int)       # (正在播的用例, 第几条)
    round_changed = pyqtSignal(int, int)  # (当前第几轮, 总轮数)
    done = pyqtSignal(bool, str)          # (是否正常播完, 错误信息)

    def __init__(self, service, items, loop_count=1, feedback=None,
                 verify_block="", parent=None):
        super().__init__(parent)
        self.service = service
        self.items = list(items)          # 先做快照
        self.loop_count = max(1, int(loop_count or 1))
        # feedback：可选的语音回执抓取服务；非 None 时播完抓 logcat 判定
        self.feedback = feedback
        # verify_block：已开启回执验证但环境不满足（如未连接设备）的原因，
        # 用于把「没验证到」的原因在日志里说准，与「没开启验证」区分开
        self.verify_block = verify_block
        self._stop = False
        self._interrupted = False
        # 未命中的预期结果（只用于收尾汇总）。一条预期结果没命中**不再中断**整轮：
        # 多条用例连跑时中途掐掉，剩下的用例全不执行，体验很差。但仍然记下来，
        # 好让最终结果如实反映"这轮有失败"。
        self._failed_verifies = []

    def stop(self):
        self._stop = True

    def run(self):
        error = ""
        try:
            for round_index in range(self.loop_count):
                if self._stop:
                    self._interrupted = True
                    break
                self.round_changed.emit(round_index + 1, self.loop_count)
                for _i, (case_id, phrase_index, text, delay, verifies, case_name) \
                        in enumerate(self.items):
                    if self._stop:
                        self._interrupted = True
                        break
                    self.progress.emit(case_id, phrase_index)

                    short = text[:20] + ("…" if len(text) > 20 else "")
                    # 是否验证只看「这条播报后面有没有配检测步骤」：没有检测步骤
                    # 就没有预期结果可判，一律不验证 —— 设置页那份全局成功关键词
                    # 早已改成按用例配置、界面上不再可编辑，拿它兜底必然误判。
                    # verify_on 表示总闸允许验证（开关开着且设备在线）；
                    # verify_blocked 表示「本想验证但清基线失败」，此时只给 warning，
                    # 不再补「未验证」的日志。
                    verify_on = self.feedback is not None
                    want_verify = verify_on and bool(verifies)
                    verify_blocked = False

                    # 播前清 logcat 基线（只在这条真要验证时）；清失败就把验证
                    # 关掉，避免拿旧日志误判，也不让验证本身拖垮播报
                    if want_verify:
                        try:
                            self.feedback.clear()
                        except Exception:
                            from utils import voice_log
                            voice_log.emit("warning", "语音验证：无法清空车机日志基线，本次跳过回执验证")
                            self.feedback = None
                            want_verify = False
                            verify_blocked = True

                    from utils import voice_log
                    # 带上用例名：一轮跑多个用例时，日志里一眼能看出这句属于哪个用例
                    voice_log.emit("info", f"🔊 [{case_name}] 开始播报「{short}」")
                    self.service.speak_async(text)
                    # 预取：这一条刚开播，就后台把下一条的音频合成好。
                    # 在线语音合成要 2~3 秒，不提前做，这段延迟就变成「上一条播完 →
                    # 下一条出声」之间的静默期，车机会干等指令直到超时回「没听清」。
                    _pf = getattr(self.service, "prefetch", None)
                    if _pf and _i + 1 < len(self.items):
                        try:
                            _pf(self.items[_i + 1][2])
                        except Exception:
                            pass

                    # 等这句播完；分片轮询是为了能及时响应「停止」
                    deadline = time.time() + 30 + len(text) * 0.6
                    while True:
                        if self._stop:
                            self.service.stop()
                            self._interrupted = True
                            break
                        if self.service.wait_done(100):
                            break
                        if time.time() > deadline:
                            error = f"「{text[:12]}」播报超时"
                            from utils import voice_log
                            voice_log.emit("error", f"❌ 播报超时「{short}」")
                            self._interrupted = True
                            break
                    if self._interrupted:
                        break

                    # 播后等待：delay 是**播完之后的纯缓冲**（与语速、播报时长无关）——
                    # 播报本身已经在上面 wait_done 等掉了，不再从等待里扣一遍。
                    # （2026-09-29 改：此前按「总等待 − 实测播报」算，而实测播报总比估算长，
                    #  缓冲被压到 0.8~1.4 秒，车机还没回话就去抓日志了。）
                    waited = 0.0
                    remain = max(0.0, delay)
                    while waited < remain:
                        if self._stop:
                            self._interrupted = True
                            break
                        time.sleep(0.05)
                        waited += 0.05
                    if self._interrupted:
                        break

                    # 回执验证：这条播报后面跟着的检测步骤（预期结果）逐条判定。
                    # 未命中只记一行日志 + 记进 _failed_verifies，**不中断执行** ——
                    # 一条没命中不代表后面几句白播，多用例连跑时更要跑完整轮。
                    if want_verify:
                        try:
                            from models.voice_model import get_verify_config
                            cfg = get_verify_config()
                            lines = self.feedback.capture(cfg["log_tag"])
                            for _v_idx, kw_list in verifies:
                                label = "、".join(kw_list[:3]) + (
                                    "…" if len(kw_list) > 3 else "")
                                ok, msg, _ = self.feedback.judge_result(
                                    lines, kw_list, cfg["fail_keywords"])
                                if ok:
                                    voice_log.emit(
                                        "success",
                                        f"✅ 预期结果「{label}」：车机反馈命中")
                                else:
                                    voice_log.emit(
                                        "error",
                                        f"❌ 预期结果「{label}」未命中：{msg}")
                                    self._failed_verifies.append(label)
                        except Exception:
                            voice_log.emit("warning", "语音验证：读取车机日志失败，本次按通过处理")
                    elif verifies:
                        # 配了检测步骤却没验成：把原因说准（未开启 / 环境不满足）
                        if verify_blocked:
                            pass   # 播前已给过「无法清空基线」的 warning
                        elif self.verify_block:
                            voice_log.emit(
                                "warning",
                                f"⚠ 已开启回执验证，但{self.verify_block}，"
                                f"无法读取车机反馈，{len(verifies)} 条预期结果"
                                f"跳过验证「{short}」（按通过继续执行）")
                        else:
                            voice_log.emit(
                                "warning",
                                f"⚠ 未开启回执验证，{len(verifies)} 条预期结果未验证「{short}」")
                    else:
                        # 没配检测步骤：不验证是正常情况，给个完成回执即可
                        if verify_on:
                            reason = "未配检测步骤，不验证"
                        elif self.verify_block:
                            reason = f"{self.verify_block}，跳过验证"
                        else:
                            reason = "未开启回执验证"
                        voice_log.emit("success", f"✅ 播报完成「{short}」（{reason}）")
                if self._interrupted:
                    break
        except VoiceError as e:
            error = str(e)
            from utils import voice_log
            voice_log.emit("error", f"❌ 播报失败：{e}")
        except Exception as e:
            error = f"{type(e).__name__}: {str(e)[:60]}"
            from utils import voice_log
            voice_log.emit("error", f"❌ 播报失败：{error}")
        self.progress.emit("", -1)
        # 三种收尾：被停止 / 播报本身出错（这两类都中断了整轮，error 里带原因）；
        # 预期结果有未命中的（此时后面的步骤与用例都已跑完，只是整体算失败）；
        # 都没有才算正常播完。
        if self._interrupted or error:
            self.done.emit(False, error)
        elif self._failed_verifies:
            self.done.emit(False, self._verify_summary())
        else:
            self.done.emit(True, "")

    def _verify_summary(self) -> str:
        """收尾汇总：几条未命中 + 前两条的名字。

        逐条明细已经在「虫师日志」里各占一行了，这里只给一句话，避免状态栏太长。
        """
        total = len(self._failed_verifies)
        head = "、".join(f"「{name}」" for name in self._failed_verifies[:2])
        tail = "…" if total > 2 else ""
        return f"{total} 条预期结果未命中：{head}{tail}"
