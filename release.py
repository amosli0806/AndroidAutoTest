# -*- coding: utf-8 -*-
"""虫师发版脚本 —— 一条命令完成发版。

用法：
    python release.py 1.1.5              # 发 1.1.5：改版本号→提交→推送→打 tag（触发 CI）
    python release.py 1.1.5 --dry-run    # 只演练，不真的提交/推送

脚本会依次做：
    1. 校验版本号格式（x.y.z）
    2. 改 utils/version.py 的 APP_VERSION 与 BUILD_DATE
    3. 在 CHANGELOG.md 顶部插入 "## v1.1.5" 占位段（若该版本已存在则跳过）
    4. git add + commit + push origin main
    5. git tag v1.1.5 + push origin v1.1.5（这一步触发 GitHub CI 自动打包发版）

注意：
    - 发版前请确认代码已是最新（git pull 过），且你想要的改动都已在本地。
    - CHANGELOG 里新插入的是占位文字，发完可到 GitHub 网页编辑 Release 正文，
      或下次发版前直接改 CHANGELOG.md 再跑本脚本。
    - 若提示网络连接失败，重跑本脚本即可（脚本幂等，重复执行安全）。
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


def main():
    if len(sys.argv) < 2:
        print("用法：python release.py <版本号> [--dry-run]")
        print("示例：python release.py 1.1.5")
        sys.exit(1)

    version = sys.argv[1].strip().lstrip("vV")
    dry_run = "--dry-run" in sys.argv

    m = VERSION_RE.match(version)
    if not m:
        print(f"!! 版本号格式不对：{version!r}，应为 x.y.z，如 1.1.5")
        sys.exit(1)

    tag = f"v{version}"
    print(f"== 准备发布 {tag} {'（演练，不实际执行）' if dry_run else ''} ==")

    # 1. 改版本号
    text = open(VERSION_FILE, encoding="utf-8").read()
    new_text, n1 = re.subn(
        r'APP_VERSION\s*=\s*"[^"]*"', f'APP_VERSION = "{version}"', text)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    new_text, n2 = re.subn(
        r'BUILD_DATE\s*=\s*"[^"]*"', f'BUILD_DATE = "{now}"', new_text)
    if n1 != 1 or n2 != 1:
        print("!! 未能在 utils/version.py 中定位 APP_VERSION / BUILD_DATE，请检查文件")
        sys.exit(1)
    if not dry_run:
        open(VERSION_FILE, "w", encoding="utf-8", newline="\n").write(new_text)
    print(f"  [1/5] 版本号已改为 {version}（BUILD_DATE={now}）")

    # 2. CHANGELOG 顶部插入占位段（若已存在该版本则跳过）
    cl = open(CHANGELOG_FILE, encoding="utf-8").read()
    if re.search(rf"(?m)^##\s+v?{re.escape(version)}\b", cl):
        print(f"  [2/5] CHANGELOG 已存在 {tag} 段，跳过插入")
    else:
        insert = f"## {tag}\n\n- （待补充：这一版的更新说明）\n\n"
        # 插到第一个 "## " 之前，若没有就在文件末尾追加
        m2 = re.search(r"(?m)^##\s+", cl)
        if m2:
            cl = cl[:m2.start()] + insert + cl[m2.start():]
        else:
            cl = cl + insert
        if not dry_run:
            open(CHANGELOG_FILE, "w", encoding="utf-8", newline="\n").write(cl)
        print(f"  [2/5] 已在 CHANGELOG.md 顶部插入 {tag} 占位段（可事后补写内容）")

    if dry_run:
        print("\n== 演练结束，未做任何提交/推送/打 tag。请自行检查上面改动是否符合预期。 ==")
        sys.exit(0)

    # 3. 提交并推送 main
    run(["git", "add", "utils/version.py", "CHANGELOG.md"])
    run(["git", "commit", "-m", f"发布 {tag}"])
    print("  [3/5] 提交完成，正在推送 main…")
    run(["git", "push", "origin", "main"])
    print("  [4/5] main 已推送")

    # 4. 打 tag 并推送（触发 CI）
    # 若本地已有同名 tag，先删掉（幂等，重复执行安全）
    r = subprocess.run(["git", "tag", "-l", tag], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8")
    if r.stdout.strip() == tag:
        run(["git", "tag", "-d", tag], check=False)
        print(f"  [tag] 已删除本地旧的 {tag}")
    run(["git", "tag", tag])
    run(["git", "push", "origin", tag])
    print("  [5/5] tag 已推送")

    print(f"""
== 完成！已发布 {tag} ==
  代码已推送到 main，tag {tag} 已推送。
  GitHub CI 会自动：打包 → 自测 → 压包 → 建 Release，约 5~10 分钟。
  结果查看：https://github.com/amosli0806/AndroidAutoTest/releases
""")


if __name__ == "__main__":
    main()
