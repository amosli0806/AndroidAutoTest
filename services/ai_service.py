# services/ai_service.py
"""AI 辅助服务。

设计约定（重要）：
  1. 按场景暴露方法，不做通用 chat 接口 —— UI 层拿到的永远是结构化结果，
     不是一段要靠用户自己读的文字
  2. 所有 prompt 在 service 内部封装，UI 层不碰
  3. AI 未启用 / 调用失败 / 解析失败时，一律返回 None 或空结果，
     绝不抛异常打断主流程
  4. 结果里的 actions 是「可执行建议」，UI 可以据此渲染按钮，
     但第一版不强求落地执行（只展示）
"""
import json
import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional

from utils.settings import Settings

logger = logging.getLogger(__name__)


# ============================================================
# 数据结构
# ============================================================
@dataclass
class SuggestedAction:
    """AI 给出的单条可执行建议"""
    type: str                        # 'add_wait' | 'update_element' | 'view_stack' | 'inspect' | 'none'
    label: str                       # 按钮文案
    payload: dict = field(default_factory=dict)


@dataclass
class FailureSuggestion:
    """一次失败分析的结果"""
    summary: str                     # 一句话结论
    cause: str                       # 可能原因（多行）
    actions: List[SuggestedAction] = field(default_factory=list)
    raw: str = ""                    # 原始返回，调试用


# ============================================================
# 服务
# ============================================================
class AIService:
    """按场景封装的 AI 辅助能力"""

    # ---------- 对外场景接口 ----------
    def analyze_failure(
        self,
        step: dict,
        prev_steps: List[dict],
        error_msg: str,
        screenshot_path: Optional[str] = None,
        logcat_snippet: Optional[str] = None,
    ) -> Optional[FailureSuggestion]:
        """分析一次用例失败。

        :param step: {'type': 'click', 'name': '...', 'params': {...}}
        :param prev_steps: 前 2~3 步的简略快照
        :param error_msg: 执行层已经格式化好的错误信息
        :param screenshot_path: 断言失败时的截图（如有）
        :param logcat_snippet: 该时段 logcat 的 crash/error 行（可选）
        :return: FailureSuggestion 或 None（AI 未启用 / 调用失败）
        """
        if not Settings.is_ai_ready():
            return None

        system = self._system_prompt()
        user = self._failure_prompt(
            step, prev_steps, error_msg, screenshot_path, logcat_snippet
        )
        raw = self._call_llm(system, user)
        if not raw:
            return None
        return self._parse_failure_response(raw)

    # ---------- Prompt 构造 ----------
    @staticmethod
    def _system_prompt() -> str:
        return (
            "你是一名资深 Android UI 自动化测试工程师。"
            "用户的 uiautomator2 用例执行失败了，你需要基于给定上下文给出诊断。\n"
            "\n"
            "严格按以下 JSON 格式回复，不要输出任何其它内容：\n"
            "{\n"
            '  "summary": "一句话结论（不超过 40 字）",\n'
            '  "cause": "可能原因，2~4 条，每条一行，用 · 开头",\n'
            '  "actions": [\n'
            '    {"type": "add_wait", "label": "在该步骤前加 2 秒等待", "payload": {"seconds": 2}},\n'
            '    {"type": "update_element", "label": "改用文本定位", "payload": {}},\n'
            '    {"type": "view_stack", "label": "查看完整堆栈", "payload": {}}\n'
            "  ]\n"
            "}\n"
            "\n"
            "action 的 type 只能是以下之一：\n"
            "  add_wait        —— 建议在失败步骤前插入等待，payload.seconds 为秒数\n"
            "  update_element  —— 建议修改元素定位方式，payload 可为空\n"
            "  view_stack      —— 建议查看完整堆栈/日志\n"
            "  inspect         —— 建议人工查看当前界面\n"
            "  none            —— 无具体可执行建议\n"
            "\n"
            "如果信息不足以判断，actions 返回一条 type=none 的建议即可，不要编造。"
        )

    @staticmethod
    def _failure_prompt(step, prev_steps, error_msg, screenshot_path, logcat_snippet) -> str:
        lines = []
        lines.append("【失败的步骤】")
        lines.append(f"  类型：{step.get('type', '?')}")
        lines.append(f"  名称：{step.get('name', '')}")
        params = step.get("params", {}) or {}
        for k, v in params.items():
            # 内部字段不进 prompt，避免干扰
            if k in ("screenWidth", "screenHeight",
                     "normalizedX", "normalizedY",
                     "normalizedStartX", "normalizedStartY",
                     "normalizedEndX", "normalizedEndY"):
                continue
            lines.append(f"  {k} = {v}")

        if prev_steps:
            lines.append("")
            lines.append("【前序步骤（越靠后越近）】")
            for i, s in enumerate(prev_steps, 1):
                p = s.get("params", {}) or {}
                brief = ", ".join(f"{k}={v}" for k, v in p.items()
                                  if k not in ("screenWidth", "screenHeight",
                                               "normalizedX", "normalizedY",
                                               "normalizedStartX", "normalizedStartY",
                                               "normalizedEndX", "normalizedEndY"))
                lines.append(f"  {i}. [{s.get('type')}] {s.get('name', '')}  ({brief})")

        lines.append("")
        lines.append("【错误信息】")
        lines.append(f"  {error_msg}")

        if logcat_snippet:
            lines.append("")
            lines.append("【该时段 logcat 中的 crash / error】")
            lines.append(logcat_snippet[:2000])

        if screenshot_path:
            lines.append("")
            lines.append(f"【失败截图路径】{screenshot_path}（当前分析未上传图片，仅作为线索提示）")

        return "\n".join(lines)

    # ---------- HTTP 调用 ----------
    @staticmethod
    def _chat_completions_url(base_url: str) -> str:
        """把用户填的 base_url 规整成 chat/completions 端点。

        用户可能只填到 /v1（推荐），也可能把文档里的完整端点
        .../v1/chat/completions 整段粘进来（很多服务商文档给的就是完整端点）。
        后者若只做 rstrip("/") 再拼一次，会变成
        .../chat/completions/chat/completions，直接 404。
        """
        base = (base_url or "").strip().rstrip("/")
        suffix = "/chat/completions"
        if base.endswith(suffix):
            base = base[: -len(suffix)].rstrip("/")
        return base + suffix

    def _call_llm(self, system: str, user: str,
                  cfg: Optional[dict] = None) -> Optional[str]:
        cfg = cfg or Settings.get_ai_config()
        try:
            import requests
        except ImportError:
            logger.warning("[AIService] requests 未安装，AI 功能不可用")
            return None

        url = self._chat_completions_url(cfg.get("base_url", ""))
        try:
            resp = requests.post(
                url,
                headers={
                    "Authorization": f"Bearer {cfg.get('api_key', '')}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": cfg.get("model") or "gpt-4o-mini",
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "temperature": 0.2,
                },
                timeout=cfg.get("timeout") or 30,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except Exception as e:
            logger.warning(f"[AIService] 调用失败: {e}")
            return None

    # ---------- 连通性自测（设置页用） ----------
    def test_connection(self, cfg: dict):
        """用给定配置发一次最小请求，返回 (是否成功, 给用户看的说明)。

        与 _call_llm 的区别：这里要把失败原因**具体**带回去（401 / 404 / 连不上），
        而不是一律吞掉返回 None —— 用户在设置页点了「测试连接」就是要知道错在哪。
        故意不校验 enabled：用户往往是先测通再打开开关。
        """
        try:
            import requests
        except ImportError:
            return False, "未安装 requests，AI 功能不可用"

        api_key = (cfg.get("api_key") or "").strip()
        if not api_key:
            return False, "请先填写 API Key"

        url = self._chat_completions_url(cfg.get("base_url", ""))
        model = cfg.get("model") or "gpt-4o-mini"
        try:
            resp = requests.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                # max_tokens 压到 1，把这次自测的花费降到最低
                json={"model": model,
                      "messages": [{"role": "user", "content": "ping"}],
                      "max_tokens": 1},
                timeout=cfg.get("timeout") or 30,
            )
        except Exception as e:
            return False, f"无法连接：{str(e)[:80]}"

        if resp.status_code == 401:
            return False, "认证失败（401）：API Key 不正确"
        if resp.status_code == 404:
            return False, "端点不存在（404）：请检查端点地址，一般填到 /v1 即可"
        if resp.status_code >= 400:
            return False, f"HTTP {resp.status_code}：{(resp.text or '')[:80]}"

        try:
            data = resp.json()
        except Exception:
            return False, "返回不是 JSON，该端点可能不是 OpenAI 兼容接口"
        if not data.get("choices"):
            return False, "返回格式不符合 OpenAI 协议（缺 choices）"
        return True, f"连接正常，模型 {model} 已响应"

    # ---------- 结果解析 ----------
    @staticmethod
    def _extract_json_object(raw: str) -> Optional[dict]:
        """从模型返回里把 JSON 对象抠出来。

        提示词里写明「只输出 JSON」也没用，模型经常在前后加一句解释。
        所以做三级降级：整体解析 -> 剥 ``` 代码块 -> 扫第一个能解析成对象的 {...}。
        原实现只覆盖了前两级，第三种情况下整条分析会被静默丢弃，
        而 UI 还会提示「请检查 API Key / 网络 / 模型名」，把用户带偏。
        """
        text = (raw or "").strip()
        if not text:
            return None

        # 1) 整个返回就是 JSON（最理想）
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

        # 2) 被 ```json ... ``` 包起来
        m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(1).strip())
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                pass

        # 3) 前后有解释文字：逐个 '{' 试着解析，取第一个能解析成对象的
        decoder = json.JSONDecoder()
        for i, ch in enumerate(text):
            if ch != "{":
                continue
            try:
                obj, _ = decoder.raw_decode(text, i)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                return obj
        return None

    @staticmethod
    def _as_text(value) -> str:
        """把模型给的字段规整成字符串。

        模型可能返回 null（字段在但没值），也可能返回数组（比如 cause 直接给 2 条原因）。
        原实现直接 .strip()，这两种情况都会抛 AttributeError，导致整条分析白丢。
        """
        if value is None:
            return ""
        if isinstance(value, (list, tuple)):
            lines = []
            for item in value:
                text = str(item).strip()
                if not text:
                    continue
                # 数组形态统一补上 '· '，与 prompt 里要求的行格式对齐
                lines.append(text if text[0] in "·-•*" else f"· {text}")
            return "\n".join(lines)
        if isinstance(value, dict):
            return "\n".join(f"{k}: {v}" for k, v in value.items())
        return str(value).strip()

    @classmethod
    def _parse_failure_response(cls, raw: str) -> Optional[FailureSuggestion]:
        data = cls._extract_json_object(raw)
        if data is None:
            logger.warning(f"[AIService] 返回里找不到合法 JSON: {raw[:200]}")
            return None

        actions = []
        for item in data.get("actions", []) or []:
            if not isinstance(item, dict):
                continue
            actions.append(SuggestedAction(
                type=cls._as_text(item.get("type")) or "none",
                label=cls._as_text(item.get("label")),
                payload=item.get("payload", {}) or {},
            ))

        return FailureSuggestion(
            summary=cls._as_text(data.get("summary")),
            cause=cls._as_text(data.get("cause")),
            actions=actions,
            raw=raw,
        )