# -*- coding: utf-8 -*-
"""虫师发版/提交脚本 —— 灵活控制「只传代码」还是「传代码并发版」。

用法（在项目目录 D:/AndroidAutoTest 打开命令行）：

    python release.py                     # 不带参数 → 交互式菜单，逐步选择要做的事
    python release.py push                # 只把当前改动提交并推送到 GitHub main（不改版本号、不发版）
    python release.py 1.1.6               # 完整发版：改版本号 → 提交 → 推送 main → 打 tag（触发 CI）
    python release.py 1.1.6 --no-release  # 改版本号 + 提交 + 推送 main，但**不打 tag 不发版**
    python release.py 1.1.6 --dry-run     # 演练：只打印会做什么，不改文件、不提交

说明：
    - 「push」只上传代码，安全、不触发 CI；「打 tag」才会触发 CI 自动打包发版。
    - 完整发版会自动做 5 件事：改 utils/version.py 的版本号与构建时间 → 在 CHANGELOG.md
      顶部插入占位段 → git add+commit+push main → git tag+push tag（触发 CI）。
    - 幂等：重复执行安全（同名 tag 会先删本地再重打）。
    - 发版前建议先 git pull 拉最新，避免覆盖别人改动。
"""

import re
import subprocess
import sys
from datetime import datetime

ROOT = r"D:\AndroidAutoTest"
VERSION_FILE = ROOT + r"\utils\version.py"
CHANGELOG_FILE = ROOT + r"\CHANGELOG.md"

VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


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


def current_version():
    text = open(VERSION_FILE, encoding="utf-8").read()
    m = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', text)
    return m.group(1) if m else "?"


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


def commit_and_push(commit_msg):
    """提交所有改动并推送到 main。"""
    run(["git", "add", "-A"])
    run(["git", "commit", "-m", commit_msg])
    print("  提交完成，正在推送 main…")
    run(["git", "push", "origin", "main"])
    print("  main 已推送")


def push_tag(tag):
    """打 tag 并推送（触发 CI）。幂等：先删本地同名 tag。"""
    r = subprocess.run(["git", "tag", "-l", tag], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8")
    if r.stdout.strip() == tag:
        run(["git", "tag", "-d", tag], check=False)
        print(f"  [tag] 已删除本地旧的 {tag}")
    run(["git", "tag", tag])
    run(["git", "push", "origin", tag])
    print(f"  {tag} 已推送，CI 开始发版")


def do_push_only():
    """模式：只传代码，不发版。"""
    print(f"== 只上传代码（当前版本 {current_version()}，不改版本号、不发版）==")
    commit_and_push("更新代码")
    print(f"""
== 完成！代码已推送到 main（版本仍为 {current_version()}，未发版）==
  这一步只同步代码，不触发 CI，应用不会收到更新。
  之后想发版时再运行：python release.py <新版本号>
""")


def do_release(version, dry_run, no_release):
    tag = f"v{version}"
    mode = "（演练，不实际执行）" if dry_run else \
           "（只传代码，不打 tag 不发版）" if no_release else ""
    print(f"== 发版 {tag} {mode}==")

    set_version(version, dry_run)
    update_changelog(version, tag, dry_run)

    if dry_run:
        print("\n== 演练结束，未做任何提交/推送/打 tag。请自行检查上面改动。 ==")
        sys.exit(0)

    commit_and_push(f"发布 {tag}")

    if no_release:
        print(f"""
== 完成！代码已推送到 main（版本已改为 {version}，但未打 tag、未发版）==
  之后想正式发版时，只需运行：python release.py --tag-only {tag}
""")
        sys.exit(0)

    push_tag(tag)
    print(f"""
== 完成！已发布 {tag} ==
  代码已推送到 main，tag {tag} 已推送。
  GitHub CI 会自动：打包 → 自测 → 压包 → 建 Release，约 5~10 分钟。
  结果查看：https://github.com/amosli0806/AndroidAutoTest/releases
""")


def do_tag_only(tag):
    """模式：版本号和代码都已就绪，只补打 tag 触发发版。"""
    print(f"== 仅打 tag 触发发版：{tag} ==")
    push_tag(tag)
    print(f"== 完成！{tag} 已推送，CI 开始发版 ==")


def interactive_menu():
    """不带参数时进入交互式选择。"""
    print(f"当前版本：{current_version()}\n")
    print("请选择要执行的操作：")
    print("  1) 只上传代码到 GitHub（不改版本号、不发版）")
    print("  2) 完整发版（改版本号 + 提交 + 推送 + 打 tag）")
    print("  3) 改版本号并推送，但暂不发版（不打 tag）")
    print("  4) 仅补打 tag 触发发版（版本号已改好时用）")
    print("  5) 演练（dry-run，只预览改动）")
    print("  q) 退出")
    choice = input("\n输入序号（1-5 或 q）：").strip()

    if choice == "1":
        do_push_only()
    elif choice == "2":
        v = input("请输入新版本号（如 1.1.6）：").strip().lstrip("vV")
        if not VERSION_RE.match(v):
            print("!! 版本号格式不对，应为 x.y.z")
            sys.exit(1)
        do_release(v, dry_run=False, no_release=False)
    elif choice == "3":
        v = input("请输入新版本号（如 1.1.6）：").strip().lstrip("vV")
        if not VERSION_RE.match(v):
            print("!! 版本号格式不对，应为 x.y.z")
            sys.exit(1)
        do_release(v, dry_run=False, no_release=True)
    elif choice == "4":
        tag = input("请输入要打的 tag（如 v1.1.6）：").strip()
        if not tag.startswith("v"):
            tag = "v" + tag
        push_tag(tag)
    elif choice == "5":
        v = input("请输入要演练的版本号（如 1.1.6）：").strip().lstrip("vV")
        if not VERSION_RE.match(v):
            print("!! 版本号格式不对，应为 x.y.z")
            sys.exit(1)
        do_release(v, dry_run=True, no_release=False)
    else:
        print("已退出。")
        sys.exit(0)


def main():
    args = sys.argv[1:]

    # 不带参数 → 交互式菜单
    if not args:
        interactive_menu()
        return

    first = args[0].strip()

    # 特殊模式：只传代码
    if first == "push":
        do_push_only()
        return

    # 特殊模式：仅打 tag
    if first == "--tag-only":
        if len(args) < 2:
            print("用法：python release.py --tag-only v1.1.6")
            sys.exit(1)
        tag = args[1].strip()
        if not tag.startswith("v"):
            tag = "v" + tag
        do_tag_only(tag)
        return

    # 其余：按版本号处理
    version = first.lstrip("vV")
    if not VERSION_RE.match(version):
        print("用法：")
        print("  python release.py                 # 交互式菜单")
        print("  python release.py push            # 只传代码，不发版")
        print("  python release.py 1.1.6           # 完整发版")
        print("  python release.py 1.1.6 --no-release   # 改版本号+推送，不打tag")
        print("  python release.py 1.1.6 --dry-run      # 演练")
        print("  python release.py --tag-only v1.1.6    # 仅打tag触发发版")
        sys.exit(1)

    dry_run = "--dry-run" in args
    no_release = "--no-release" in args
    do_release(version, dry_run, no_release)


if __name__ == "__main__":
    main()
