# -*- coding: utf-8 -*-
"""虫师发版脚本 —— 只传代码 / 发预发布 / 发正式版 / 预发布转正。

用法（在项目目录 D:/AndroidAutoTest 打开命令行）：

    python release.py                       # 交互式菜单
    python release.py status                # 查看当前发布状态（最新正式版 / 预发布列表）
    python release.py push                  # 只提交并推送 main（不改版本号、不发版）
    python release.py 1.5.17                # 发版：改版本号 → 提交 → 推送 → 打 tag（CI 建**预发布**）
    python release.py 1.5.17 --final        # 发版并**自动转正**（打 tag → 等 CI → 取消 pre-release）
    python release.py 1.5.17 --no-release   # 只改版本号 + 推送，不打 tag 不发版
    python release.py manual-promote        # 手动转正：打印详细点击步骤并打开网页（推荐）
    python release.py promote               # 自动转正（有令牌走 API，没有走 Actions）
    python release.py promote v1.5.17       # 指定 tag 转正
    python release.py clear-preview         # 只清本机的内测通道文件 data/preview.json
    python release.py 1.5.17 --dry-run      # 演练：只打印会做什么

两条发版路线：

  预发布（默认）  tag 推上去 → CI 建预发布 → GitHub 的 /releases/latest 不返回它，
                  普通客户端收不到，你自己用内测通道验
                  （**本机的 data/preview.json 会被自动写成这一版**，重启即可收到）
  正式发布        --final，或事后 `promote`：取消 pre-release → /releases/latest 返回它 → 全量

转正成功后会**自动删掉本机的 data/preview.json**（安装目录和项目目录都检查）。
不清它的话，这台机器会一直查那个固定 tag，反而收不到后续正式版。安装目录默认
D:/chongshi，换机器时用环境变量 CHONGSHI_INSTALL_DIR 覆盖。

转正有两条路，脚本自动选，你不用关心走哪条：

  有个人令牌    直接调 GitHub API，立即生效
  没有令牌      推一个 promote-<tag> tag，由仓库里的 .github/workflows/promote.yml
                用 Actions 自带的 GITHUB_TOKEN 去改 —— 本机不需要任何凭证，
                只用到已有的 SSH key，约 20~40 秒生效

个人令牌（可选）按顺序取，三处都没有就走 Actions：

  1) 环境变量 GITHUB_TOKEN 或 GH_TOKEN
  2) 项目下 data/.gh_token（一行纯 token；data/ 已被 .gitignore 忽略，不会提交）
  3) ~/.git-credentials 里的 https://user:token@github.com

令牌权限：fine-grained 只需本仓库的 Contents: Read and write；老式 token 勾 repo。

其它说明：
    - 「push」只上传代码，安全、不触发 CI；「打 tag」才会触发 CI 自动打包发版。
    - 幂等：同名 tag 会先删本地再重打。
    - 未转正时发现代码要改，可以直接重打 tag（`1.5.17 --force-tag`），
      已转正后就不能再动 tag，只能发下一个版本号。
"""

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

ROOT = r"D:\AndroidAutoTest"
VERSION_FILE = ROOT + r"\utils\version.py"
CHANGELOG_FILE = ROOT + r"\CHANGELOG.md"

REPO = "amosli0806/AndroidAutoTest"
API_BASE = f"https://api.github.com/repos/{REPO}"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"

# 令牌文件（data/ 在 .gitignore 里，不会被提交）
TOKEN_FILE = os.path.join(ROOT, "data", ".gh_token")

# 打包后的安装目录：转正后要清掉这里的 data/preview.json（内测通道文件），
# 否则这台机器会一直盯着那个固定 tag，收不到后续正式版。
# 换机器/换目录时用环境变量 CHONGSHI_INSTALL_DIR 覆盖。
INSTALL_DIR = os.environ.get("CHONGSHI_INSTALL_DIR", r"D:\chongshi")
PREVIEW_NAME = "preview.json"

VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")

# git push 偶发的网络类错误特征（GitHub 443 端口会 Connection reset）。
# 命中这些才重试；非网络错误（如 push 被拒）重试无意义，直接报错。
#
# 注意 git/curl 的实际措辞有两套，必须都收进来 —— 之前只写了 "connection reset"，
# 而实际输出是 "Recv failure: Connection was reset"（多了个 was），
# 导致网络抖动时一次都不重试、直接退出（2026-10-19 / 2026-10-20 各踩一次）。
_NETWORK_HINTS = (
    "connection reset",
    "connection was reset",
    "recv failure",
    "could not read from remote",
    "couldn't connect to server",
    "connection timed out",
    "failed to connect",
    "could not resolve host",
    "early eof",
    "remote end hung up",
    "network is unreachable",
    "connection refused",
)
PUSH_RETRIES = 3
PUSH_RETRY_DELAY = 5  # 秒

# CI 打包耗时（实测 6 分钟上下，补丁对比上一个 Release 会更久些）
CI_WAIT_TIMEOUT = 25 * 60
CI_POLL_INTERVAL = 20


# ----------------------------------------------------------------------
# 基础工具
# ----------------------------------------------------------------------
def run(cmd, check=True):
    print(f"\n  > {' '.join(cmd)}")
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.stdout.strip():
        print("    " + r.stdout.strip().replace("\n", "\n    "))
    if r.stderr.strip():
        print("    [stderr] " + r.stderr.strip().replace("\n", "\n    "))
    if check and r.returncode != 0:
        print(f"\n  !! 命令失败（退出码 {r.returncode}），已中止。")
        sys.exit(1)
    return r


def run_push(args, retries=PUSH_RETRIES, delay=PUSH_RETRY_DELAY):
    """git push，遇到网络抖动自动重试；非网络错误直接报错退出。"""
    for attempt in range(1, retries + 1):
        tag = f"（第 {attempt}/{retries} 次尝试）" if attempt > 1 else ""
        print(f"\n  > {' '.join(args)}{tag}")
        r = subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.stdout.strip():
            print("    " + r.stdout.strip().replace("\n", "\n    "))
        if r.stderr.strip():
            print("    [stderr] " + r.stderr.strip().replace("\n", "\n    "))
        if r.returncode == 0:
            return r
        err_low = (r.stderr or "").lower()
        is_network = any(h in err_low for h in _NETWORK_HINTS)
        if is_network and attempt < retries:
            print(f"    网络抖动，{delay} 秒后重试…")
            time.sleep(delay)
            continue
        print(f"\n  !! 推送失败（退出码 {r.returncode}，已尝试 {attempt} 次），已中止。")
        sys.exit(1)
    return r


def current_version():
    text = open(VERSION_FILE, encoding="utf-8").read()
    m = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', text)
    return m.group(1) if m else "?"


# ----------------------------------------------------------------------
# 内测通道文件（data/preview.json）
# ----------------------------------------------------------------------
def preview_paths():
    """可能存在的内测通道文件：安装目录 + 开发目录（去重）。"""
    cand = []
    if INSTALL_DIR:
        cand.append(os.path.join(INSTALL_DIR, "data", PREVIEW_NAME))
    cand.append(os.path.join(ROOT, "data", PREVIEW_NAME))
    out, seen = [], set()
    for p in cand:
        k = os.path.abspath(p).lower()
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out


def preview_tag_of(path):
    """读 preview.json 里的 tag（读不到返回空串）。"""
    try:
        with open(path, encoding="utf-8") as f:
            return str((json.load(f) or {}).get("tag", "") or "").strip()
    except (OSError, ValueError):
        return ""


def write_preview_tag(tag):
    """在本机安装目录写内测通道文件，让这台机器能收到预发布。

    发预发布时自动调用：省掉「每次手动建/改 data/preview.json」这一步。
    只在安装目录**确实有 data 子目录**时才写（没装或路径不对就跳过，
    由调用方打印手动方式），不擅自 mkdir 到未知路径。
    """
    if not INSTALL_DIR:
        return False
    data_dir = os.path.join(INSTALL_DIR, "data")
    if not os.path.isdir(data_dir):
        print(f"  [内测通道] 安装目录下没有 data 子目录（{data_dir}），跳过自动写入")
        return False
    path = os.path.join(data_dir, PREVIEW_NAME)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"tag": tag}, f)
        print(f"  [内测通道] 已写入 {path} → {tag}")
        return True
    except OSError as e:
        print(f"  !! 写入内测通道失败 {path}: {e}")
        return False


def clear_preview_tag():
    """删掉内测通道文件。

    转正后必须清：不清的话这台机器会一直查那个固定 tag，永远收不到后续正式版。
    安装目录和开发目录都清，删不掉的只提示不中断。
    """
    removed = []
    for p in preview_paths():
        if not os.path.exists(p):
            continue
        try:
            os.remove(p)
            removed.append(p)
        except OSError as e:
            print(f"  !! 删除失败 {p}: {e}")
    if removed:
        for p in removed:
            print(f"  [内测通道] 已删除 {p}")
    else:
        print("  [内测通道] 没有 preview.json 需要清理")
    return bool(removed)



# ----------------------------------------------------------------------
# GitHub API（转正 / 查状态用）
# ----------------------------------------------------------------------
def get_token():
    """按优先级找一个 GitHub 令牌；都找不到返回空串。"""
    for env in ("GITHUB_TOKEN", "GH_TOKEN"):
        v = os.environ.get(env, "").strip()
        if v:
            return v
    try:
        if os.path.exists(TOKEN_FILE):
            v = open(TOKEN_FILE, encoding="utf-8").read().strip().splitlines()
            if v and v[0].strip():
                return v[0].strip()
    except OSError:
        pass
    try:
        cred = os.path.expanduser("~/.git-credentials")
        if os.path.exists(cred):
            m = re.search(r"https://[^:\s]+:([^@\s]+)@github\.com",
                          open(cred, encoding="utf-8").read())
            if m:
                return m.group(1)
    except OSError:
        pass
    return ""


def api(method, path, payload=None, token=None, allow_404=False, quiet=False):
    """调 GitHub API，返回 (状态码, 解析后的 JSON 或 None)。网络错误返回 (0, None)。"""
    url = path if path.startswith("http") else API_BASE + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "chongshi-release-script",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", "replace")
            return resp.status, (json.loads(body) if body.strip() else None)
    except urllib.error.HTTPError as e:
        if e.code == 404 and allow_404:
            return 404, None
        body = e.read().decode("utf-8", "replace")[:300]
        if not quiet:
            print(f"    !! API {method} {path} → HTTP {e.code}: {body}")
        return e.code, None
    except Exception as e:  # noqa: BLE001 - 网络问题统一降级处理
        if not quiet:
            print(f"    !! API 请求失败: {type(e).__name__}: {e}")
        return 0, None


def release_assets_text(rel):
    """把资产的「名字 + 大小」拼成一行行文本。"""
    lines = []
    for a in rel.get("assets") or []:
        size = round((a.get("size") or 0) / 1024 / 1024, 1)
        lines.append(f"      {a.get('name', '?'):44s} {size:7.1f} MB")
    return "\n".join(lines) or "      （还没有资产，CI 可能还在上传）"


def do_status():
    """只读：打印最新正式版与预发布列表。"""
    print("== 发布状态 ==\n")
    code, latest = api("GET", "/releases/latest", allow_404=True)
    if code == 404:
        print("  最新正式版：还没有正式发布过")
    elif latest:
        print(f"  最新正式版（/releases/latest）：{latest.get('tag_name')}")
        print(f"    本地版本号：{current_version()}")
    elif code != 404:
        print("  !! 查不到最新正式版（网络或限流），可稍后再试")

    code, rels = api("GET", "/releases?per_page=15")
    if rels:
        previews = [r for r in rels if r.get("prerelease")]
        if previews:
            print("\n  预发布（只有内测通道看得到）：")
            for r in previews:
                print(f"    {r.get('tag_name'):12s} {r.get('created_at', '')[:10]}  "
                      f"{len(r.get('assets') or [])} 个资产")
        else:
            print("\n  预发布：无")

    # 本机内测通道：有的话这台机器只会查那个固定 tag，正式版反而收不到
    latest_tag = (latest or {}).get("tag_name")
    for p in preview_paths():
        if os.path.exists(p):
            cur = preview_tag_of(p)
            print(f"\n  ⚠ 内测通道文件：{p}")
            print(f"     当前指向 {cur or '(空 tag)'}")
            if cur and cur == latest_tag:
                print("     它已经是正式版了 → 建议清理掉，否则会一直只查这个固定 tag")
            else:
                print("     转正后请清掉，否则收不到后续正式版")
            print("     清理命令：python release.py clear-preview")

    print(f"\n  发布页：{RELEASES_PAGE}\n")


def wait_for_release(tag, timeout=CI_WAIT_TIMEOUT):
    """等 CI 把 Release 建出来（有全量 zip 且已上传）。成功返回 release dict。"""
    print(f"\n== 等待 CI 产出 {tag}（最多 {timeout // 60} 分钟）==")
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        code, rel = api("GET", f"/releases/tags/{tag}", allow_404=True)
        if rel:
            names = [a.get("name", "") for a in rel.get("assets") or []]
            states = [a.get("state") for a in rel.get("assets") or []]
            has_zip = any(n.endswith("-win64.zip") for n in names)
            uploading = any(s != "uploaded" for s in states)
            if has_zip and not uploading:
                print(f"  Release 已就绪，{len(names)} 个资产全部上传完成")
                return rel
        elapsed = int(time.monotonic() - start)
        print(f"  …{elapsed}s", end="\r", flush=True)
        time.sleep(CI_POLL_INTERVAL)
    print("\n  !! 等待超时。CI 可能失败或还在排队，去发布页看一眼：")
    print(f"     {RELEASES_PAGE}")
    return None


def _promote_trigger_ref():
    """挑一个「远程确实存在、且包含 promote.yml」的提交，用来打触发 tag。

    为什么不能直接指向被转正的那个 tag（2026-11-01 踩过）：
    GitHub 只读 **tag 指向的那个提交**里的 workflow 文件。如果被转正的版本
    是在加 promote.yml 之前发的，那个提交里根本没有这个工作流 —— tag 推上去
    什么都不会发生，Actions 列表里也看不到任何运行，只会干等到超时。
    """
    for ref in ("origin/main", "main", "HEAD"):
        r = subprocess.run(
            ["git", "cat-file", "-e", f"{ref}:.github/workflows/promote.yml"],
            cwd=ROOT, capture_output=True)
        if r.returncode == 0:
            return ref
    return None


def _promote_via_actions(tag, dry_run=False):
    """没有个人令牌时：推 promote-<tag> 触发仓库 Actions 转正（本机无需任何凭证）。

    原理：Actions 自带的 GITHUB_TOKEN 有 contents: write 权限，让它调 API 改
    Release 状态；本机只负责推一个 tag（走已有的 SSH key）。
    """
    pt_tag = f"promote-{tag}"
    ref = _promote_trigger_ref()
    if not ref:
        print("\n  !! 找不到包含 .github/workflows/promote.yml 的提交，Actions 转正用不了。")
        print("     先把 promote.yml 推到 main，再重试。")
        return 1

    print(f"\n  改用 Actions 转正（本机不需要令牌）：推送 tag {pt_tag}")
    print(f"  触发用提交：{ref}（必须含 promote.yml，否则 GitHub 找不到工作流）")
    if dry_run:
        print(f"  [演练] 会执行：git tag -f {pt_tag} {ref} && git push -f origin {pt_tag}")
        return 0

    run(["git", "tag", "-f", pt_tag, ref], check=False)
    run_push(["git", "push", "-f", "origin", pt_tag])

    print("  已触发，等 Actions 取消预发布（约 20~40 秒）…")
    run_url = ""
    # 每轮只查一次 runs（不查 latest），18 轮 ≈ 6 分钟、18 个请求。
    # 未认证 API 只有 60 次/小时，之前每轮查两个接口很容易把自己限流掉。
    for _ in range(18):
        time.sleep(20)
        _c, runs = api("GET", "/actions/runs?per_page=3")
        for r in (runs or {}).get("workflow_runs", []):
            if r.get("name") == "Promote Release" and r.get("head_branch") == pt_tag:
                run_url = r.get("html_url") or run_url
                if r.get("status") != "completed":
                    break
                if r.get("conclusion") == "success":
                    _code, latest = api("GET", "/releases/latest")
                    got = (latest or {}).get("tag_name")
                    print(f"\n  OK：Actions 已转正，latest = {got} {'✅' if got == tag else '（可能被更晚的版本盖住）'}")
                    print(f"  发布页：{RELEASES_PAGE}")
                    return 0
                print(f"\n  !! Actions 运行结束但不是成功：{r.get('conclusion')}")
                print(f"     {run_url}")
                print("     点进去看「取消预发布标记」那步的 HTTP 状态：")
                print("       403 = 令牌权限不足 —— 仓库 Settings → Actions → General")
                print("             → Workflow permissions 改成 Read and write")
                print("       404 = tag 在 Release 里不存在（tag 名写错或还没发版）")
                return 1
        print("  …", end="", flush=True)
    print("\n  !! 等待超时。去这里确认「Promote Release」有没有跑起来：")
    print(f"     https://github.com/{REPO}/actions/workflows/promote.yml")
    if run_url:
        print(f"     最近一次运行：{run_url}")
    print("     列表里没有这次运行 = 工作流没触发（多半是 promote.yml 还没在 main 上）。")
    return 1


def do_manual_promote(tag=None):
    """打印手动转正的详细步骤（在 GitHub 网页上点），并打开浏览器。

    给不想用 API / Actions 的人用：全程在网页上点，脚本只负责把步骤讲清楚。
    """
    token = get_token()
    _code, rels = api("GET", "/releases?per_page=15", token=token, quiet=True)
    previews = [r for r in (rels or []) if r.get("prerelease")]
    if rels and not previews:
        print("  当前没有预发布可转正。")
        return 1

    if not tag and previews:
        if len(previews) == 1:
            tag = previews[0].get("tag_name")
        else:
            print("  存在的预发布：")
            for r in previews:
                print(f"    {r.get('tag_name')}")
            tag = input("\n  要转正哪个？（输入 tag，如 v1.5.17）：").strip()

    tag = (tag or "").strip()
    if tag and not tag.startswith("v"):
        tag = "v" + tag

    url = f"https://github.com/{REPO}/releases/tag/{tag}" if tag else RELEASES_PAGE
    name = tag or "那个带 Pre-release 标记的版本"

    print(f"""
== 手动把预发布变成正式版 ==

  要做的事：把 {name} 的「预发布」标记去掉。去掉之后它就是最新正式版，
  所有用户下次点「检查更新」就能收到。

  ────────────────────────────────────────────────
  第 1 步  打开这个页面（脚本已经试着帮你打开浏览器，没弹出来就手动复制）
           {url}

  第 2 步  找到 {name}。它现在左边有个橙色 / 灰色的 "Pre-release" 标记

  第 3 步  在它右上角点铅笔图标（鼠标悬停会显示 "Edit"）

  第 4 步  往下滚，会看到两个勾选框：
             [✓] Set as a pre-release        ← 把这个勾**去掉**
             [ ] Set as the latest release   ← 把这个勾**勾上**（重要）
           再往下的 "Set as a draft" 不要动、不要勾

  第 5 步  拉到页面最底部，点绿色按钮 "Update release"

  第 6 步  回到发布列表刷新，{name} 左边应该变成绿色 "Latest"，
           橙色 "Pre-release" 消失 —— 这就成功了
  ────────────────────────────────────────────────

  怎么确认真的生效：
    用另一台还是旧版本的电脑，虫师菜单 →「检查更新」，应该提示 {name}

  转正后清掉本机的内测通道文件（否则这台机器会一直只查 {name}）：
    python release.py clear-preview
""")

    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:
        pass
    return 0


def do_promote(tag=None, assume_yes=False, dry_run=False):
    """把预发布转正（取消 pre-release）。tag 为空时取最新的那个预发布。

    有个人令牌 → 直接调 API（立即生效）；
    没有 → 推 promote-<tag> 触发仓库 Actions 转正（不需要任何凭证）。
    """
    token = get_token()
    via = "直接调 API" if token else "通过仓库 Actions（无需令牌）"
    print(f"== 转正（{via}）==\n")

    if not tag:
        _code, rels = api("GET", "/releases?per_page=15", token=token)
        previews = [r for r in (rels or []) if r.get("prerelease")]
        if not previews:
            print("  当前没有预发布可转正。")
            return 1
        if len(previews) == 1:
            tag = previews[0].get("tag_name")
            print(f"  唯一的预发布：{tag}")
        else:
            print("  存在的预发布：")
            for r in previews:
                print(f"    {r.get('tag_name')}")
            tag = input("\n  输入要转正的 tag（如 v1.5.17）：").strip()
    if not tag.startswith("v"):
        tag = "v" + tag

    code, rel = api("GET", f"/releases/tags/{tag}", token=token, allow_404=True)
    if code == 404 or not rel:
        print(f"  !! 找不到 {tag} 对应的 Release。")
        return 1
    if not rel.get("prerelease"):
        print(f"  {tag} 已经是正式发布了，无需转正。")
        return 0

    print(f"\n== 转正 {tag} ==（转正后所有用户都会收到这一版）")
    print(f"  名称：{rel.get('name')}")
    print(f"  资产：\n{release_assets_text(rel)}")
    print(f"  执行方式：{via}")

    if not assume_yes:
        print("\n  转正后 /releases/latest 立刻返回它，全量客户端下次检查更新就能收到。")
        ans = input("  确认转正请输入 tag 全名（直接回车取消）：").strip()
        if ans != tag:
            print("  已取消，未做任何改动。")
            return 1

    if not token:
        rc = _promote_via_actions(tag, dry_run=dry_run)
        if rc == 0 and not dry_run:
            # 转正成功 → 内测通道文件没用了，留着会让这台机器一直盯着旧 tag
            clear_preview_tag()
        return rc

    if dry_run:
        print(f"  [演练] 会 PATCH /releases/{rel['id']} 把 prerelease 设为 false")
        return 0

    code, updated = api("PATCH", f"/releases/{rel['id']}",
                        {"prerelease": False}, token=token)
    if code != 200 or not updated:
        print("  !! 转正失败（见上面的 API 报错）。")
        return 1

    print(f"  OK：{tag} 已转为正式发布")
    _code, latest = api("GET", "/releases/latest", token=token)
    if latest:
        ok = latest.get("tag_name") == tag
        print(f"  校验 /releases/latest → {latest.get('tag_name')}"
              f"{'  ✅' if ok else '  ❌（可能被更晚的版本盖住）'}")
    clear_preview_tag()
    print(f"  发布页：{RELEASES_PAGE}")
    return 0


# ----------------------------------------------------------------------
# 版本号 / CHANGELOG / 提交 / 打 tag
# ----------------------------------------------------------------------
def set_version(version, dry_run):
    """改 utils/version.py 的版本号与构建时间。"""
    text = open(VERSION_FILE, encoding="utf-8").read()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    new_text, n1 = re.subn(
        r'APP_VERSION\s*=\s*"[^"]*"', f'APP_VERSION = "{version}"', text)
    new_text, n2 = re.subn(
        r'BUILD_DATE\s*=\s*"[^"]*"', f'BUILD_DATE = "{now}"', new_text)
    if n1 != 1 or n2 != 1:
        print("!! 未能在 utils/version.py 中定位 APP_VERSION / BUILD_DATE，请检查文件")
        sys.exit(1)
    if not dry_run:
        open(VERSION_FILE, "w", encoding="utf-8", newline="\n").write(new_text)
    print(f"  [版本号] {version}（BUILD_DATE={now}）")


def update_changelog(version, tag, dry_run):
    """在 CHANGELOG.md 顶部插入占位段（若该版本已存在则跳过）。"""
    cl = open(CHANGELOG_FILE, encoding="utf-8").read()
    if re.search(rf"(?m)^##\s+v?{re.escape(version)}\b", cl):
        print(f"  [CHANGELOG] 已存在 {tag} 段，跳过插入")
        return
    insert = f"## {tag}\n\n- （待补充：这一版的更新说明）\n\n"
    m2 = re.search(r"(?m)^##\s+", cl)
    if m2:
        cl = cl[:m2.start()] + insert + cl[m2.start():]
    else:
        cl = cl + insert
    if not dry_run:
        open(CHANGELOG_FILE, "w", encoding="utf-8", newline="\n").write(cl)
    print(f"  [CHANGELOG] 已插入 {tag} 占位段（可事后补写内容）")


def has_unfinished_sections(version):
    """CHANGELOG 里该版本段落若还是占位/空，返回 True（提醒别发出去）。"""
    cl = open(CHANGELOG_FILE, encoding="utf-8").read()
    m = re.search(rf"(?m)^##\s+v?{re.escape(version)}\s*\n(.*?)(?=^##\s+|\Z)",
                  cl, re.DOTALL)
    if not m:
        return True
    body = m.group(1).strip()
    if not body:
        return True
    return "（待补充" in body or "本段留空" in body


def commit_and_push(commit_msg):
    """提交所有改动并推送到 main。"""
    run(["git", "add", "-A"])
    run(["git", "commit", "-m", commit_msg])
    print("  提交完成，正在推送 main…")
    run_push(["git", "push", "origin", "main"])
    print("  main 已推送")


def push_tag(tag, force=False):
    """打 tag 并推送（触发 CI）。幂等：先删本地同名 tag。"""
    r = subprocess.run(["git", "tag", "-l", tag], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8")
    if r.stdout.strip() == tag:
        run(["git", "tag", "-d", tag], check=False)
        print(f"  [tag] 已删除本地旧的 {tag}")
    run(["git", "tag", "-a", tag, "-m", f"虫师 {tag}"])
    if force:
        run_push(["git", "push", "-f", "origin", tag])
    else:
        run_push(["git", "push", "origin", tag])
    print(f"  {tag} 已推送，CI 开始发版")


# ----------------------------------------------------------------------
# 各种模式
# ----------------------------------------------------------------------
def do_push_only():
    """模式：只传代码，不发版。"""
    print(f"== 只上传代码（当前版本 {current_version()}，不改版本号、不发版）==")
    commit_and_push("更新代码")
    print(f"""
== 完成！代码已推送到 main（版本仍为 {current_version()}，未发版）==
  这一步只同步代码，不触发 CI，应用不会收到更新。
  之后想发版时再运行：python release.py <新版本号>
""")


def do_release(version, dry_run, no_release, final=False, force_tag=False):
    tag = f"v{version}"
    mode = "（演练，不实际执行）" if dry_run else \
           "（只传代码，不打 tag 不发版）" if no_release else \
           "（发预发布）" if not final else "（发正式版：CI 后自动转正）"
    print(f"== 发版 {tag} {mode}==")

    set_version(version, dry_run)
    update_changelog(version, tag, dry_run)

    warn = has_unfinished_sections(version)
    if warn and not dry_run:
        print("\n  !! CHANGELOG 里这一版还没写更新说明（空段或占位）。")
        print("     Release 正文会从这段提取，用户看到的更新说明就是它。")
        ans = input("     仍然继续？输入 yes 继续，其他任意键取消：").strip().lower()
        if ans != "yes":
            print("  已取消。先去 CHANGELOG.md 补好这一版的内容再发。")
            sys.exit(1)

    if dry_run:
        print("\n== 演练结束，未做任何提交/推送/打 tag。请自行检查上面改动。 ==")
        sys.exit(0)

    commit_and_push(f"发布 {tag}")

    if no_release:
        print(f"""
== 完成！代码已推送到 main（版本已改为 {version}，但未打 tag、未发版）==
  之后想发版时，只需运行：python release.py --tag-only {tag}
""")
        sys.exit(0)

    push_tag(tag, force=force_tag)

    if not final:
        # 本机自动切成内测通道：省掉手动建/改 data/preview.json
        wrote = write_preview_tag(tag)
        print(f"""
== 已发出【预发布】{tag} ==
  GitHub 的 /releases/latest 不会返回预发布，所以普通用户**收不到**，
  仍停在上一个正式版。""")
        if wrote:
            print(f"""  本机已自动指向 {tag}：完全退出虫师后重新启动，点「检查更新」即可收到。
  ⚠ 这台机器现在只认 {tag} 这一个 tag，验完记得转正（会自动清掉）：
    python release.py promote {tag}
""")
        else:
            print(f"""  本机没能自动配置，手动二选一：
    - 安装目录 data\\preview.json 写 {{"tag": "{tag}"}}
    - 环境变量 CHONGSHI_UPDATE_TAG={tag}
  验完确认没问题，再运行：
    python release.py promote {tag}
""")
        return

    # --final：等 CI 完成后转正
    rel = wait_for_release(tag)
    if not rel:
        print(f"\n  改为手动收尾：CI 完成后运行 python release.py promote {tag}")
        return
    rc = do_promote(tag)
    if rc == 0:
        print(f"""
== 完成！{tag} 已是正式发布 ==
  全量客户端下次检查更新即可收到。
""")
        # 转正后重建「未发布」空段，方便下次记录
        _ensure_unreleased_section()


def _ensure_unreleased_section():
    """发完正式版后，若 CHANGELOG 顶部还没有「未发布」段就补一个。"""
    try:
        cl = open(CHANGELOG_FILE, encoding="utf-8").read()
    except OSError:
        return
    if re.search(r"(?m)^##\s+未发布", cl):
        return
    block = ("## 未发布（下一个版本）\n\n"
             "> 本段留空。下次改代码时把改动写在这里；发版前把标题改成版本号，"
             "**别让空段直接发出去**。\n\n")
    m = re.search(r"(?m)^##\s+", cl)
    new = (cl[:m.start()] + block + cl[m.start():]) if m else (cl + "\n" + block)
    open(CHANGELOG_FILE, "w", encoding="utf-8", newline="\n").write(new)
    print("  [CHANGELOG] 已补回「未发布」空段")


def do_tag_only(tag, force=False):
    """模式：版本号和代码都已就绪，只补打 tag 触发发版。"""
    print(f"== 仅打 tag 触发发版：{tag} ==")
    push_tag(tag, force=force)
    print(f"""
== 完成！{tag} 已推送，CI 开始发版 ==
  CI 建的是**预发布**。验完没问题后运行：python release.py promote {tag}
""")


def interactive_menu():
    """不带参数时进入交互式选择。"""
    print(f"当前版本：{current_version()}\n")
    print("请选择要执行的操作：")
    print("  1) 只上传代码到 GitHub（不改版本号、不发版）")
    print("  2) 发【预发布】（改版本+提交+推送+打tag，并让本机自动能收到；普通用户收不到）")
    print("  3) 发【正式版】（同上，CI 完成后自动转正）")
    print("  4) 手动转正（打开网页 + 详细点击步骤，推荐）")
    print("  5) 查看发布状态（最新正式版 / 预发布列表）")
    print("  6) 改版本号并推送，但暂不发版（不打 tag）")
    print("  7) 仅补打 tag 触发发版（版本号已改好时用）")
    print("  8) 演练（dry-run，只预览改动）")
    print("  q) 退出")
    choice = input("\n输入序号（1-8 或 q）：").strip()

    if choice == "1":
        do_push_only()
    elif choice in ("2", "3"):
        v = input("请输入新版本号（如 1.5.17）：").strip().lstrip("vV")
        if not VERSION_RE.match(v):
            print("!! 版本号格式不对，应为 x.y.z")
            sys.exit(1)
        do_release(v, dry_run=False, no_release=False, final=(choice == "3"))
    elif choice == "4":
        do_manual_promote()
    elif choice == "5":
        do_status()
    elif choice == "6":
        v = input("请输入新版本号（如 1.5.17）：").strip().lstrip("vV")
        if not VERSION_RE.match(v):
            print("!! 版本号格式不对，应为 x.y.z")
            sys.exit(1)
        do_release(v, dry_run=False, no_release=True)
    elif choice == "7":
        tag = input("请输入要打的 tag（如 v1.5.17）：").strip()
        if not tag.startswith("v"):
            tag = "v" + tag
        do_tag_only(tag)
    elif choice == "8":
        v = input("请输入要演练的版本号（如 1.5.17）：").strip().lstrip("vV")
        if not VERSION_RE.match(v):
            print("!! 版本号格式不对，应为 x.y.z")
            sys.exit(1)
        do_release(v, dry_run=True, no_release=False)
    else:
        print("已退出。")
        sys.exit(0)


def main():
    args = sys.argv[1:]

    if not args:
        interactive_menu()
        return

    first = args[0].strip()

    if first in ("status", "-s", "--status"):
        do_status()
        return

    if first in ("clear-preview", "clear_preview"):
        print("== 清理内测通道文件 ==")
        clear_preview_tag()
        return

    if first in ("manual-promote", "manual_promote"):
        tag = next((a for a in args[1:] if not a.startswith("--")), None)
        do_manual_promote(tag)
        return

    if first == "promote":
        tag = next((a for a in args[1:] if not a.startswith("--")), None)
        do_promote(tag,
                   assume_yes=("--yes" in args or "-y" in args),
                   dry_run=("--dry-run" in args))
        return

    if first == "push":
        do_push_only()
        return

    if first == "--tag-only":
        rest = [a for a in args[1:] if not a.startswith("--")]
        if not rest:
            print("用法：python release.py --tag-only v1.5.17 [--force-tag]")
            sys.exit(1)
        tag = rest[0].strip()
        if not tag.startswith("v"):
            tag = "v" + tag
        do_tag_only(tag, force=("--force-tag" in args))
        return

    version = first.lstrip("vV")
    if not VERSION_RE.match(version):
        print("用法：")
        print("  python release.py                      # 交互式菜单")
        print("  python release.py status               # 查看发布状态")
        print("  python release.py push                 # 只传代码，不发版")
        print("  python release.py 1.5.17               # 发预发布")
        print("  python release.py 1.5.17 --final       # 发正式版（CI 后自动转正）")
        print("  python release.py 1.5.17 --no-release  # 改版本号+推送，不打tag")
        print("  python release.py 1.5.17 --dry-run     # 演练")
        print("  python release.py manual-promote [tag] # 手动转正（打印步骤+开网页）")
        print("  python release.py promote [v1.5.17]    # 自动转正（需令牌或 Actions）")
        print("  python release.py --tag-only v1.5.17   # 仅打tag")
        sys.exit(1)

    do_release(version,
               dry_run=("--dry-run" in args),
               no_release=("--no-release" in args),
               final=("--final" in args),
               force_tag=("--force-tag" in args))


if __name__ == "__main__":
    main()
