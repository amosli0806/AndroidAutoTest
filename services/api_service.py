# services/api_service.py
"""接口自动化的执行层：渲染变量 → 发请求 → 跑断言 → 产出可展示的结果对象。

几个刻意的取舍：
1. 用 requests（已在 requirements 里），不引新依赖。
2. JSON 取值自己实现一个子集（`a.b[0].c` / `$.a.b`），不引 jsonpath-ng ——
   断言场景用不到通配、过滤、递归下降这些完整 JSONPath 能力，为它们背一个依赖不划算。
3. 失败原因一律转成中文人话（"状态码 期望 200，实际 500"），不把 requests 的
   英文异常直接甩给用户。
"""
import json
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import requests

from models.api_model import (
    ASSERT_CONTAINS, ASSERT_JSONPATH, ASSERT_REGEX, ASSERT_STATUS, ASSERT_TIME,
    BODY_FORM, BODY_JSON, BODY_NONE, BODY_RAW, OP_CONTAINS, OP_EQ, OP_GT, OP_LT,
    OP_NE, OP_NOT_CONTAINS, OP_REGEX, ApiAssert, ApiCase, ApiEnv)

# 单次请求超时（秒）。接口测试不该挂死在一次请求上。
DEFAULT_TIMEOUT = 15

# 是否校验 HTTPS 证书。默认关：内网测试环境大量使用自签证书，开着会让用户
# 一上来就撞 SSL 错误且无从解决（本轮还没有"跳过证书校验"的界面开关）。
# 要连生产环境时把这里改成 True。
VERIFY_SSL = False

# 变量占位符：{{name}}，名字允许字母数字下划线点和横线
_VAR_RE = re.compile(r"\{\{\s*([A-Za-z0-9_.\-]+)\s*\}\}")

# JSON 路径分词：普通键 或 [数字]
_PATH_TOKEN_RE = re.compile(r"[^.\[\]]+|\[\d+\]")


class ApiRunError(Exception):
    """请求根本没发出去（URL 不合法、变量没定义、请求体不是合法 JSON 等）。"""


# ============================================================
# 文本 <-> 结构化数据 的小工具（界面保存/回填也复用）
# ============================================================
def parse_headers_text(text: str) -> List[List[str]]:
    """把「一行一个 Key: Value」的文本解析成 [[k, v], ...]。忽略空行与 # 注释。"""
    pairs = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            raise ValueError(f"请求头「{line}」缺少冒号，格式应为 名称: 值")
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if not key:
            raise ValueError(f"请求头「{line}」名称为空")
        pairs.append([key, value])
    return pairs


def format_headers_text(pairs) -> str:
    """[[k, v], ...] -> 「一行一个 Key: Value」，供界面回填。"""
    return "\n".join(f"{k}: {v}" for k, v in (pairs or []) if k)


def parse_kv_text(text: str) -> List[List[str]]:
    """把 `k=v` 多行文本解析成 [[k, v], ...]，用于环境变量表。"""
    pairs = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"变量「{line}」缺少等号，格式应为 名称=值")
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key:
            pairs.append([key, value])
    return pairs


def format_kv_text(pairs) -> str:
    return "\n".join(f"{k}={v}" for k, v in (pairs or []) if k)


def parse_form_text(text: str) -> dict:
    """表单体：支持 `a=1&b=2` 与「一行一个 a=1」两种写法。"""
    raw = (text or "").strip()
    if not raw:
        return {}
    data = {}
    for chunk in re.split(r"[&\n]", raw):
        chunk = chunk.strip()
        if not chunk:
            continue
        key, _, value = chunk.partition("=")
        if key.strip():
            data[key.strip()] = value.strip()
    return data


# ============================================================
# 变量渲染
# ============================================================
def render(text: str, table: dict, missing: set) -> str:
    """把 {{var}} 替换成环境变量。未定义的变量原样留着并记进 missing。"""
    if not text:
        return text

    def _sub(match):
        name = match.group(1)
        if name in table:
            return str(table[name])
        missing.add(name)
        return match.group(0)

    return _VAR_RE.sub(_sub, text)


# ============================================================
# JSON 取值（子集）
# ============================================================
def json_get(data, path: str) -> Tuple[bool, object]:
    """按简易路径取值，支持 `a.b[0].c` 与 `$.a.b`。返回 (是否取到, 值)。"""
    if data is None:
        return False, None
    p = (path or "").strip()
    if p.startswith("$"):
        p = p[1:]
    if p.startswith("."):
        p = p[1:]
    if not p:
        return True, data

    current = data
    for token in _PATH_TOKEN_RE.findall(p):
        if token.startswith("["):
            index = int(token[1:-1])
            if isinstance(current, list) and -len(current) <= index < len(current):
                current = current[index]
            else:
                return False, None
        else:
            if isinstance(current, dict) and token in current:
                current = current[token]
            else:
                return False, None
    return True, current


# ============================================================
# 比较
# ============================================================
def _as_number(value) -> Optional[float]:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _loose_eq(actual, expect: str) -> bool:
    """先按数值比，比不了再按字符串比 —— 免得 "200" 和 "200.0" 被判不等。"""
    left, right = _as_number(actual), _as_number(expect)
    if left is not None and right is not None:
        return left == right
    return str(actual).strip() == str(expect).strip()


def _compare(actual, op: str, expect: str) -> Tuple[bool, str]:
    """返回 (是否通过, 说明)。说明只在失败时给，用于拼中文错误信息。"""
    if op == OP_EQ:
        return _loose_eq(actual, expect), f"期望等于「{expect}」"
    if op == OP_NE:
        return not _loose_eq(actual, expect), f"期望不等于「{expect}」"
    if op in (OP_CONTAINS, OP_NOT_CONTAINS):
        hit = str(expect) in str(actual)
        if op == OP_CONTAINS:
            return hit, f"期望包含「{expect}」"
        return not hit, f"期望不包含「{expect}」"
    if op == OP_REGEX:
        try:
            hit = re.search(str(expect), str(actual)) is not None
        except re.error as e:
            return False, f"正则表达式无效（{e}）"
        return hit, f"期望匹配正则「{expect}」"
    if op in (OP_GT, OP_LT):
        left, right = _as_number(actual), _as_number(expect)
        if left is None or right is None:
            return False, f"「{actual}」与「{expect}」不能按数值比较"
        return (left > right) if op == OP_GT else (left < right), \
            f"期望{op} {expect}，实际 {actual}"
    return False, f"未知的操作符「{op}」"


# ============================================================
# 结果对象
# ============================================================
@dataclass
class AssertResult:
    assert_: ApiAssert
    ok: bool
    actual: str = ""
    message: str = ""      # 失败原因（中文）

    def to_dict(self):
        return {
            "type": self.assert_.type,
            "describe": self.assert_.describe(),
            "ok": self.ok,
            "actual": self.actual,
            "message": self.message,
        }


@dataclass
class ApiResult:
    case_id: str
    case_name: str
    method: str = ""
    url: str = ""                 # 渲染后的最终 URL
    ok: bool = False
    status_code: int = 0
    elapsed_ms: int = 0
    error: str = ""               # 请求阶段就失败时的原因（此时 assert_results 为空）
    request_headers: dict = field(default_factory=dict)
    request_body: str = ""
    response_headers: dict = field(default_factory=dict)
    response_text: str = ""
    assert_results: List[AssertResult] = field(default_factory=list)

    @property
    def failed_asserts(self) -> List[AssertResult]:
        return [a for a in self.assert_results if not a.ok]

    def summary(self) -> str:
        """一行摘要，给日志/toast/报告列表用。"""
        if self.error:
            return f"请求未发出：{self.error}"
        if self.ok:
            return f"{self.status_code} · {self.elapsed_ms}ms · 断言全通过"
        failed = self.failed_asserts
        if failed:
            return f"{self.status_code} · {self.elapsed_ms}ms · {len(failed)} 条断言未通过"
        return f"{self.status_code} · {self.elapsed_ms}ms"

    def to_dict(self):
        return {
            "case_id": self.case_id, "case_name": self.case_name,
            "method": self.method, "url": self.url, "ok": self.ok,
            "status_code": self.status_code, "elapsed_ms": self.elapsed_ms,
            "error": self.error,
            "request_headers": dict(self.request_headers),
            "request_body": self.request_body,
            "response_headers": dict(self.response_headers),
            "response_text": self.response_text,
            "asserts": [a.to_dict() for a in self.assert_results],
        }


# ============================================================
# 执行
# ============================================================
class ApiService:
    """发一次请求并判定断言。无状态，可被后台线程反复调用。"""

    def __init__(self, timeout: int = DEFAULT_TIMEOUT):
        self.timeout = timeout

    # ---------- 请求体 ----------
    def _build_body(self, case: ApiCase, table: dict, missing: set):
        """返回 (json_body, data_body)。两者最多一个非 None。"""
        if case.body_type == BODY_NONE or not (case.body or "").strip():
            return None, None

        text = render(case.body, table, missing)
        if case.body_type == BODY_JSON:
            try:
                return json.loads(text), None
            except json.JSONDecodeError as e:
                raise ApiRunError(
                    f"请求体不是合法 JSON：{e.msg}（第 {e.lineno} 行第 {e.colno} 列）")
        if case.body_type == BODY_FORM:
            return None, parse_form_text(text)
        return None, text   # 原始文本

    # ---------- 断言 ----------
    def _run_asserts(self, case: ApiCase, status_code: int, elapsed_ms: int,
                     response_text: str, parsed_json) -> List[AssertResult]:
        results = []
        for item in case.asserts:
            if not item.enabled:
                continue
            results.append(self._run_one_assert(
                item, status_code, elapsed_ms, response_text, parsed_json))
        return results

    def _run_one_assert(self, item: ApiAssert, status_code: int, elapsed_ms: int,
                        response_text: str, parsed_json) -> AssertResult:
        if item.type == ASSERT_STATUS:
            ok, why = _compare(status_code, item.op, item.expect)
            return AssertResult(item, ok, str(status_code),
                                "" if ok else f"状态码不符：{why}，实际 {status_code}")

        if item.type == ASSERT_TIME:
            ok, why = _compare(elapsed_ms, item.op, item.expect)
            return AssertResult(item, ok, f"{elapsed_ms}ms",
                                "" if ok else f"耗时不符：{why}")

        if item.type == ASSERT_JSONPATH:
            if parsed_json is None:
                return AssertResult(item, False, "",
                                    "响应体不是合法 JSON，无法按路径取值")
            found, value = json_get(parsed_json, item.expr)
            if not found:
                return AssertResult(item, False, "",
                                    f"响应里找不到路径「{item.expr}」")
            actual = value if isinstance(value, str) else json.dumps(
                value, ensure_ascii=False)
            ok, why = _compare(actual, item.op, item.expect)
            return AssertResult(item, ok, actual,
                                "" if ok else f"取值不符：{why}，实际「{actual}」")

        if item.type == ASSERT_CONTAINS:
            ok, why = _compare(response_text, item.op, item.expect)
            return AssertResult(item, ok, "", "" if ok else f"响应体不符：{why}")

        if item.type == ASSERT_REGEX:
            ok, why = _compare(response_text, OP_REGEX, item.expect)
            return AssertResult(item, ok, "", "" if ok else f"响应体不符：{why}")

        return AssertResult(item, False, "", f"未知的断言类型「{item.type}」")

    # ---------- 主流程 ----------
    def run_case(self, case: ApiCase, env: ApiEnv = None) -> ApiResult:
        """执行一个接口：渲染 → 发送 → 断言。任何异常都转成中文原因塞进结果，
        不往外抛 —— 批量执行时一条失败不该打断整批。"""
        table = env.as_map() if env is not None else {}
        result = ApiResult(case_id=case.id, case_name=case.name,
                           method=case.method)
        missing = set()
        try:
            url = render((case.url or "").strip(), table, missing).strip()
            if not url:
                raise ApiRunError("URL 为空")
            if missing:
                raise ApiRunError(
                    "变量未定义：" + "、".join(f"{{{{{n}}}}}" for n in sorted(missing))
                    + f"（当前环境：{env.name if env else '未选择'}）")
            if not re.match(r"^https?://", url, re.I):
                raise ApiRunError(f"URL 必须以 http:// 或 https:// 开头（当前是「{url}」）")

            headers = {}
            for key, value in (case.headers or []):
                if key:
                    headers[render(key, table, missing)] = render(value, table, missing)
            if missing:
                raise ApiRunError(
                    "请求头里的变量未定义：" + "、".join(
                        f"{{{{{n}}}}}" for n in sorted(missing)))

            json_body, data_body = self._build_body(case, table, missing)
            if missing:
                raise ApiRunError(
                    "请求体里的变量未定义：" + "、".join(
                        f"{{{{{n}}}}}" for n in sorted(missing)))

            result.url = url
            result.request_headers = dict(headers)
            result.request_body = (
                json.dumps(json_body, ensure_ascii=False, indent=2)
                if json_body is not None else
                (data_body if isinstance(data_body, str) else json.dumps(
                    data_body or {}, ensure_ascii=False)))

            response = requests.request(
                case.method, url,
                headers=headers or None,
                json=json_body if case.body_type == BODY_JSON else None,
                data=(None if case.body_type in (BODY_NONE, BODY_JSON) else data_body),
                timeout=self.timeout,
                verify=VERIFY_SSL,
            )
        except ApiRunError as e:
            result.error = str(e)
            return result
        except requests.exceptions.Timeout:
            result.error = f"请求超时（超过 {self.timeout} 秒）"
            return result
        except requests.exceptions.SSLError as e:
            result.error = f"HTTPS 证书校验失败：{str(e)[:120]}"
            return result
        except requests.exceptions.ConnectionError:
            # 不带 requests 的原文：那是一大段英文重试栈，对定位问题没帮助
            result.error = "连接不上：请检查地址是否正确、网络是否通、服务是否已启动"
            return result
        except requests.exceptions.MissingSchema:
            result.error = "URL 格式不合法"
            return result
        except Exception as e:
            result.error = f"{type(e).__name__}: {str(e)[:150]}"
            return result

        result.status_code = response.status_code
        result.elapsed_ms = int(response.elapsed.total_seconds() * 1000)
        result.response_headers = dict(response.headers)
        result.response_text = self._decode_body(response)

        parsed_json = None
        try:
            parsed_json = json.loads(result.response_text)
        except Exception:
            parsed_json = None

        result.assert_results = self._run_asserts(
            case, result.status_code, result.elapsed_ms,
            result.response_text, parsed_json)
        # 没配断言时：HTTP 层面成功即算通过（否则这个接口永远显示失败，没有意义）
        result.ok = not result.failed_asserts
        return result

    @staticmethod
    def _decode_body(response) -> str:
        """取响应文本；是 JSON 就顺手美化一下，方便人看。"""
        try:
            text = response.text
        except Exception:
            text = ""
        try:
            parsed = json.loads(text)
            return json.dumps(parsed, ensure_ascii=False, indent=2)
        except Exception:
            return text
