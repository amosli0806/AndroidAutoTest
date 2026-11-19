# -*- coding: utf-8 -*-
"""POC：DeepSeek 网页端 → 虫师 MCP → 设备。

目的只有一个：验证「网页端的 DeepSeek 能不能被驱动着调用虫师的工具」。
不是产品功能，跑通了也别当正式能力用 —— 页面一改版就断。

用法：
    python tools/poc_web_bridge.py                 # 交互式，首次会让你手动登录
    python tools/poc_web_bridge.py --check         # 只打开页面看看能不能连上，不发消息
    python tools/poc_web_bridge.py --task "看看设备现在是什么界面"
    python tools/poc_web_bridge.py --reset         # 清掉登录态重来

依赖：playwright（用 channel="chrome" 复用本机 Chrome，不下载 Chromium）
"""
import argparse
import asyncio
import json
import os
import re
import shutil
import sys
import time

from playwright.async_api import async_playwright

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

PROFILE_DIR = os.path.join(os.path.expanduser("~"), ".chongshi_poc_profile")
URL = "https://chat.deepseek.com"
MCP_EXE = r"D:\虫师\虫师-mcp.exe"

# POC 只给这几个：工具太多模型会挑花眼，先验证「会不会用」而不是「能用多少」
POC_TOOLS = ["device_list", "screenshot", "ui_dump", "ui_find", "tap", "assert"]

# 让模型按这个格式输出工具调用；解析器同时容忍裸 JSON
TOOL_RE = re.compile(r"```(?:tool|json)?\s*(\{.*?\"name\"\s*:.*?\})\s*```", re.S)
# 主格式：CALL: 工具名 {json}   —— 比 markdown 代码块简单，网页版模型更容易照做
CALL_RE = re.compile(r"CALL:\s*([a-z_][a-z0-9_]*)\s*(\{.*?\})?\s*$", re.I | re.M)


def brief_tools(tools) -> str:
    """把 MCP 的工具清单压成一行一个，省 token 也省模型注意力。"""
    lines = []
    for t in tools:
        schema = getattr(t, "inputSchema", None) or getattr(t, "input_schema", None) or {}
        props = (schema.get("properties") or {})
        args = ", ".join(props.keys()) if props else "无参数"
        desc = (t.description or "").strip().split("\n")[0][:60]
        lines.append(f"- {t.name}({args})：{desc}")
    return "\n".join(lines)


def build_prompt(tools_text: str, task: str) -> str:
    return f"""你要通过工具操作一台安卓设备（车机或手机）。

每次回复**只输出一行**，格式严格如下：

CALL: 工具名 {{"参数": "值"}}

不要写任何解释、不要写代码块、不要加标点以外的内容。没有参数就用 {{}}。

示例（假设有个 get_time 工具）：
CALL: get_time {{}}

可用工具：
{tools_text}

策略（很重要，照做能少走弯路）：
1. 不确定界面上有什么，先用 ui_dump(mode="summary") 看全貌。不要连续猜关键词 —— 猜错一次就白烧一轮。
2. ui_dump 会返回一行一个控件：文字/描述/id/是否可点/中心坐标。看清了再动手。
3. ui_find 的 text 匹配控件**文字**；控件只有 content-desc 时要传 description。
4. 找到目标后用 tap(element_index=序号) 点击，不要用坐标。
5. 不要用 screenshot：你收不到图片，只会浪费一轮。
6. 不要用 ui_dump(mode="full")：只会给你一个文件路径，你读不到内容。
7. 点击之后要用 ui_dump 或 ui_find 确认界面真的变了，再下结论。

任务：{task}"""


async def new_chat(page) -> bool:
    """点一下「新对话」，避免上一轮的上下文干扰。找不到就静默跳过。

    注意只在开头调一次：多轮工具调用必须留在**同一个**会话里，
    否则模型看不到上一轮的工具结果，会一直重新开始。
    """
    for sel in ('[aria-label*="新对话"]', 'button:has-text("新对话")',
                '[role="button"]:has-text("新对话")', 'a:has-text("新对话")'):
        try:
            loc = page.locator(sel).first
            if await loc.count() and await loc.is_visible():
                await loc.click()
                await asyncio.sleep(1.5)
                return True
        except Exception:
            continue
    return False


async def find_input(page):
    """找输入框：优先 textarea，其次 contenteditable。返回 Locator 或 None。"""
    for sel in ("textarea", "div[contenteditable='true']", "[contenteditable='true']"):
        loc = page.locator(sel).first
        try:
            if await loc.count() and await loc.is_visible():
                return loc
        except Exception:
            continue
    return None


async def wait_reply(page, known_text: str, timeout_s: int = 150) -> str:
    """等回复生成完：文本连续 5 次采样不变即视为完成（比盯「停止」按钮稳）。"""
    deadline = time.time() + timeout_s
    last, stable = "", 0
    while time.time() < deadline:
        await asyncio.sleep(0.8)
        try:
            text = await page.evaluate(
                """() => {
                    const nodes = document.querySelectorAll('.ds-markdown, [class*="markdown"]');
                    return nodes.length ? nodes[nodes.length - 1].innerText : document.body.innerText;
                }"""
            )
        except Exception:
            continue
        if not text or text == known_text:
            continue
        if text == last:
            stable += 1
            if stable >= 5:
                return text
        else:
            last, stable = text, 0
    return last


def compact_dump(text: str, limit: int = 60) -> str:
    """把 ui_dump 的 nodes 压成「序号. 文案 | id | 可点 | 中心」的清单。

    为什么要压：网页端模型收不到图片，只能读文字。原始 nodes 每条带 class/bounds
    等字段，3000 字符装不下几个节点，模型看到的是残缺界面，于是继续瞎猜关键词
    （实测 6 轮里 4 轮都在猜）。压完一行一个，60 个控件也就 2~3KB。
    """
    try:
        obj = json.loads(text)
    except Exception:
        return text[:4000]
    nodes = obj.get("nodes") or []
    lines = []
    for i, n in enumerate(nodes[:limit], 1):
        label = (n.get("text") or "").strip()
        desc = (n.get("description") or "").strip()
        rid = (n.get("resource_id") or "").strip()
        if not label and not desc and not rid:
            continue
        bits = [f"{i}."]
        if label:
            bits.append(f'文字「{label}」')
        if desc:
            bits.append(f"描述「{desc}」")
        if rid:
            bits.append(rid.split("/")[-1])
        if n.get("clickable"):
            bits.append("可点")
        c = n.get("center")
        if c:
            bits.append(f"{c[0]},{c[1]}")
        lines.append(" ".join(bits))
    head = f'界面 {obj.get("screen", {})} 共 {len(nodes)} 个节点：\n'
    return head + "\n".join(lines)


def parse_tool(text: str):
    """从回复里抠出工具调用；没有就返回 None。"""
    m = CALL_RE.search(text or "")
    if m:
        name = m.group(1)
        raw = (m.group(2) or "{}").strip()
        try:
            args = json.loads(raw) if raw else {}
            if isinstance(args, dict):
                return name, args
        except Exception:
            pass
    for rx in (TOOL_RE,):
        m = rx.search(text or "")
        if not m:
            continue
        raw = m.group(1)
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict) and obj.get("name"):
                return obj["name"], obj.get("args") or {}
        except Exception:
            continue
    return None


async def send_message(page, text: str):
    box = await find_input(page)
    if box is None:
        raise RuntimeError("找不到输入框 —— 可能没登录，或页面结构变了")
    await box.click()
    await page.keyboard.press("Control+A")
    await page.keyboard.press("Delete")
    # 长文本用 insert_text 一次灌进去，比逐字 type 快得多
    await page.keyboard.insert_text(text)
    await asyncio.sleep(0.3)
    await page.keyboard.press("Enter")


async def call_tool(session, name: str, args: dict) -> str:
    res = await asyncio.wait_for(session.call_tool(name, args), timeout=120)
    text = "".join(getattr(c, "text", "") or "" for c in (res.content or []))
    err = getattr(res, "isError", getattr(res, "is_error", None))
    # 不能在这里截太狠：ui_dump 的 JSON 一旦被切断，compact_dump 就解析失败、
    # 只能原样退回，等于没精简。截断放到压缩之后再做（见主循环 result[:4000]）。
    return ("[工具报错] " if err else "") + text[:30000]


async def run(task: str, check_only: bool, turns: int = 3):
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            PROFILE_DIR, channel="chrome", headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(URL, wait_until="domcontentloaded")
        await asyncio.sleep(2)

        box = await find_input(page)
        if box is None:
            # --check 或非交互环境不阻塞，截图让人自己看
            if check_only:
                shot = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "poc_login.png")
                await page.screenshot(path=shot)
                print("没检测到输入框（多半未登录）。截图：", shot)
                await ctx.close()
                return
            # 交互式：轮询等登录，扫完码自动继续，不用回来按回车
            print("\n>>> 请在浏览器窗口里扫码登录 DeepSeek（最多等 5 分钟，登录后自动继续）……")
            deadline = time.time() + 300
            while time.time() < deadline:
                await asyncio.sleep(2)
                box = await find_input(page)
                if box is not None:
                    print(">>> 已检测到输入框，继续。")
                    await asyncio.sleep(1.5)
                    break
            if box is None:
                print("等了 5 分钟还没登录，退出。")
                await ctx.close()
                return

        print("页面就绪，输入框已找到。")
        if check_only:
            shot = os.path.join(os.path.dirname(os.path.abspath(__file__)), "poc_check.png")
            await page.screenshot(path=shot)
            print("截图已存:", shot)
            await ctx.close()
            return

        params = StdioServerParameters(command=MCP_EXE, args=[])
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                tools = await session.list_tools()
                picked = [t for t in tools.tools if t.name in POC_TOOLS]
                known = {t.name for t in picked}
                print(f"虫师已连接，暴露 {len(picked)} 个工具给网页端。")

                if await new_chat(page):
                    print("已开启新对话，避免上一轮上下文干扰。")

                prompt = build_prompt(brief_tools(picked), task)
                current = prompt
                last_sig = None      # 上次 (工具名, 参数)，用来拦重复调用
                err_streak = 0       # 连续失败次数，防死循环

                for turn in range(1, turns + 1):
                    print(f"\n=== 第 {turn} 轮：发给 DeepSeek ===")
                    print(current[:300] + ("…" if len(current) > 300 else ""))
                    await send_message(page, current)
                    reply = await wait_reply(page, current)
                    print(f"\n--- DeepSeek 回复 ---\n{reply[:800]}")

                    call = parse_tool(reply)
                    if not call:
                        print("\n没有识别到工具调用，结束。")
                        break
                    name, args = call

                    # ① 模型编了个不存在的工具名
                    if name not in known:
                        print(f">>> 工具名不存在：{name}，退回提示")
                        current = (f"没有名为 {name} 的工具。可用工具只有："
                                   f"{'、'.join(sorted(known))}。请重选一个。")
                        continue

                    # ② 同工具 + 同参数连调，结果不会变，拦一次
                    sig = (name, json.dumps(args, sort_keys=True, ensure_ascii=False))
                    if sig == last_sig:
                        print(">>> 重复调用，已阻断")
                        current = (f"你刚才已经调用过 {name} 且参数完全相同，结果不会变。"
                                   f"请换一种做法，或用「完成：」说明为什么做不下去。")
                        last_sig = None      # 只拦一次，避免真的卡死
                        continue
                    last_sig = sig

                    print(f"\n>>> 调用虫师：{name} {sig[1]}")
                    try:
                        if name == "screenshot":
                            # 网页端模型收不到图片，截了也白截，直接拦掉省一轮
                            result = ('[已拦截] 你在网页对话里看不到图片，截图对你没用。'
                                      '请改用 ui_dump(mode="summary") 看界面上有什么。')
                        else:
                            result = await call_tool(session, name, args)
                            if name == "ui_dump":
                                result = compact_dump(result)
                        err_streak = 0
                    except Exception as e:
                        err_streak += 1
                        result = f"[调用异常] {type(e).__name__}: {e}"
                        print(f"<<< {result}")
                        if err_streak >= 2:
                            print(">>> 连续两次调用失败，停止。")
                            break
                    print(f"<<< 结果：{result[:500]}")

                    # ③ 空结果 / 失败时主动提示换策略，别让它再调一遍
                    hint = ""
                    if '"devices": []' in result or '"ok": false' in result:
                        hint = ("\n注意：这次结果为空或失败，重复同样的调用不会改变结果，"
                                "请换思路或直接说明卡在哪。")
                    current = (f"工具 {name} 的返回结果：\n{result[:3000]}{hint}\n\n"
                               f"继续任务，或用「完成：」总结。")
                await ctx.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", default="看看设备现在是什么界面，把主要控件列出来")
    ap.add_argument("--check", action="store_true", help="只打开页面确认能连上")
    ap.add_argument("--turns", type=int, default=3, help="最多几轮工具调用（默认 3）")
    ap.add_argument("--reset", action="store_true", help="清掉浏览器 profile 重来")
    a = ap.parse_args()

    if a.reset and os.path.isdir(PROFILE_DIR):
        shutil.rmtree(PROFILE_DIR, ignore_errors=True)
        print("已清除登录态:", PROFILE_DIR)

    if not os.path.isfile(MCP_EXE):
        print(f"找不到虫师 MCP：{MCP_EXE}")
        sys.exit(1)

    try:
        asyncio.run(run(a.task, a.check, a.turns))
    except KeyboardInterrupt:
        print("\n已中断")


if __name__ == "__main__":
    main()
