# utils/api_report.py
"""接口自动化 HTML 报告生成器。

与 utils/perf_report.py 同构（一个模块一个报告器，静态 generate() 落盘并返回路径），
配色沿用现有报告的一套：主色 #1976d2、通过 #27ae60、失败 #e74c3c、告警 #f39c12。

请求/响应体可能很长，一律塞进 <details> 里折叠，并对超长文本做截断 ——
报告是给人扫一眼定位问题的，不是拿来当抓包存档的。
"""
import html
import os
from datetime import datetime
from typing import List

from services.api_service import ApiResult

# 单个文本块最多展示多少字符（超出截断，避免报告变成几十 MB 的怪物）
MAX_BLOCK_CHARS = 20000

# 与其它报告一致的状态色
COLOR_PRIMARY = "#1976d2"
COLOR_PASS = "#27ae60"
COLOR_FAIL = "#e74c3c"
COLOR_WARN = "#f39c12"
COLOR_BG = "#f5f6fa"
COLOR_CARD = "#fafbfc"
COLOR_BORDER = "#e0e0e0"


def _esc(text) -> str:
    return html.escape(str(text if text is not None else ""))


def _clip(text: str) -> str:
    text = text or ""
    if len(text) <= MAX_BLOCK_CHARS:
        return text
    return text[:MAX_BLOCK_CHARS] + f"\n…（已截断，原文共 {len(text)} 字符）"


def _kv_rows(pairs) -> str:
    if not pairs:
        return "<tr><td colspan='2' class='muted'>（无）</td></tr>"
    return "".join(
        f"<tr><td class='key'>{_esc(k)}</td><td>{_esc(v)}</td></tr>"
        for k, v in pairs.items())


class ApiReportGenerator:
    """生成接口自动化的 HTML 报告"""

    @staticmethod
    def generate(results: List[ApiResult], file_path: str,
                 env_name: str = "", title: str = "接口自动化测试报告") -> str:
        total = len(results)
        passed = sum(1 for r in results if r.ok)
        failed = total - passed
        rate = (passed / total * 100) if total else 0.0
        total_ms = sum(r.elapsed_ms for r in results)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        blocks = "".join(ApiReportGenerator._render_case(r, i)
                         for i, r in enumerate(results, 1))
        if not results:
            blocks = "<div class='empty'>没有执行任何接口</div>"

        html_doc = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{_esc(title)}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; padding: 24px; background: {COLOR_BG}; color: #333;
         font-family: "Microsoft YaHei", "PingFang SC", Arial, sans-serif; font-size: 14px; }}
  h1 {{ font-size: 22px; margin: 0 0 6px; color: {COLOR_PRIMARY}; }}
  .meta {{ color: #888; font-size: 13px; margin-bottom: 18px; }}
  .cards {{ display: flex; gap: 14px; flex-wrap: wrap; margin-bottom: 22px; }}
  .card {{ flex: 1 1 150px; background: #fff; border: 1px solid {COLOR_BORDER};
           border-radius: 8px; padding: 14px 16px; }}
  .card .num {{ font-size: 24px; font-weight: 700; }}
  .card .lbl {{ color: #888; font-size: 12px; margin-top: 4px; }}
  .case {{ background: #fff; border: 1px solid {COLOR_BORDER}; border-radius: 8px;
           margin-bottom: 14px; overflow: hidden; }}
  .case-head {{ display: flex; align-items: center; gap: 10px; padding: 12px 16px;
                background: {COLOR_CARD}; border-bottom: 1px solid {COLOR_BORDER}; }}
  .case-head .idx {{ color: #aaa; font-size: 12px; }}
  .case-head .name {{ font-weight: 600; }}
  .badge {{ margin-left: auto; padding: 2px 10px; border-radius: 10px;
            color: #fff; font-size: 12px; white-space: nowrap; }}
  .case-body {{ padding: 14px 16px; }}
  .url {{ font-family: Consolas, Menlo, monospace; font-size: 13px; word-break: break-all;
          background: {COLOR_CARD}; border: 1px solid {COLOR_BORDER};
          border-radius: 6px; padding: 8px 10px; margin-bottom: 10px; }}
  .url .method {{ color: {COLOR_PRIMARY}; font-weight: 700; margin-right: 8px; }}
  .kv {{ width: 100%; border-collapse: collapse; margin-bottom: 10px; }}
  .kv td {{ border: 1px solid {COLOR_BORDER}; padding: 6px 10px; vertical-align: top;
            word-break: break-all; }}
  .kv td.key {{ width: 200px; color: #666; background: {COLOR_CARD}; }}
  .muted {{ color: #999; }}
  details {{ margin-bottom: 10px; }}
  summary {{ cursor: pointer; color: {COLOR_PRIMARY}; font-size: 13px;
             padding: 4px 0; user-select: none; }}
  pre {{ margin: 6px 0 0; padding: 10px; background: {COLOR_CARD};
         border: 1px solid {COLOR_BORDER}; border-radius: 6px;
         font-family: Consolas, Menlo, monospace; font-size: 12px;
         white-space: pre-wrap; word-break: break-all; max-height: 360px; overflow: auto; }}
  .asserts {{ width: 100%; border-collapse: collapse; }}
  .asserts th, .asserts td {{ border: 1px solid {COLOR_BORDER}; padding: 6px 10px;
                              text-align: left; font-size: 13px; }}
  .asserts th {{ background: {COLOR_CARD}; color: #666; font-weight: 600; }}
  .ok {{ color: {COLOR_PASS}; }}
  .bad {{ color: {COLOR_FAIL}; }}
  .err {{ background: #fdecea; border: 1px solid {COLOR_FAIL}; color: #b3261e;
          border-radius: 6px; padding: 10px 12px; margin-bottom: 10px; }}
  .empty {{ text-align: center; color: #999; padding: 40px; }}
</style>
</head>
<body>
  <h1>{_esc(title)}</h1>
  <div class="meta">环境：{_esc(env_name or "未指定")}　|　生成时间：{now}</div>

  <div class="cards">
    <div class="card"><div class="num">{total}</div><div class="lbl">接口总数</div></div>
    <div class="card"><div class="num ok">{passed}</div><div class="lbl">通过</div></div>
    <div class="card"><div class="num bad">{failed}</div><div class="lbl">失败</div></div>
    <div class="card"><div class="num" style="color:{COLOR_PRIMARY}">{rate:.1f}%</div>
      <div class="lbl">通过率</div></div>
    <div class="card"><div class="num">{total_ms / 1000:.2f}s</div><div class="lbl">总耗时</div></div>
  </div>

  {blocks}
</body>
</html>
"""
        os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(html_doc)
        return file_path

    # ------------------------------------------------------------------
    @staticmethod
    def _render_case(result: ApiResult, index: int) -> str:
        if result.error:
            badge = f'<span class="badge" style="background:{COLOR_FAIL}">请求未发出</span>'
        elif result.ok:
            badge = f'<span class="badge" style="background:{COLOR_PASS}">通过</span>'
        else:
            badge = (f'<span class="badge" style="background:{COLOR_FAIL}">'
                     f'{len(result.failed_asserts)} 条断言未通过</span>')

        parts = [
            '<div class="case">',
            '<div class="case-head">',
            f'<span class="idx">#{index}</span>',
            f'<span class="name">{_esc(result.case_name)}</span>',
            badge,
            '</div>',
            '<div class="case-body">',
        ]

        if result.error:
            parts.append(f'<div class="err">{_esc(result.error)}</div>')

        parts.append(
            f'<div class="url"><span class="method">{_esc(result.method)}</span>'
            f'{_esc(result.url or "（未发出请求）")}</div>')

        if result.status_code:
            parts.append(
                f'<div class="meta">状态码 {result.status_code}　|　'
                f'耗时 {result.elapsed_ms} ms</div>')

        parts.append("<details><summary>请求头</summary><table class='kv'>")
        parts.append(_kv_rows(result.request_headers))
        parts.append("</table></details>")

        if result.request_body:
            parts.append("<details><summary>请求体</summary><pre>"
                         f"{_esc(_clip(result.request_body))}</pre></details>")

        if result.response_text:
            parts.append("<details><summary>响应体</summary><pre>"
                         f"{_esc(_clip(result.response_text))}</pre></details>")

        if result.assert_results:
            parts.append(
                "<table class='asserts'><tr><th>断言</th><th>结果</th>"
                "<th>实际值</th><th>失败原因</th></tr>")
            for item in result.assert_results:
                cls = "ok" if item.ok else "bad"
                mark = "通过" if item.ok else "未通过"
                parts.append(
                    f"<tr><td>{_esc(item.assert_.describe())}</td>"
                    f"<td class='{cls}'>{mark}</td>"
                    f"<td>{_esc(item.actual)}</td>"
                    f"<td>{_esc(item.message)}</td></tr>")
            parts.append("</table>")
        elif not result.error:
            parts.append('<div class="muted">该接口未配置断言，'
                         '仅按「请求成功发出且无异常」判定为通过。</div>')

        parts.append("</div></div>")
        return "".join(parts)
