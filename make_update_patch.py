# -*- coding: utf-8 -*-
"""生成增量更新补丁（.dpatch）—— 由 CI 在发版时调用，也可本地手工跑。

思路（文件级增量，不做块级）：
    更新包里真正每次都变的只有业务代码（实测 base_library.zip 约 1.3MB），
    PyQt6 那 489MB 的 Qt 动态库在版本之间一个字节都不变。所以只要「没变的文件
    不下载、客户端从本机旧安装里复用」，290MB 的下载量就能压到几 MB。

补丁内容（一个 zip）：
    manifest.json        新版本的**完整**文件清单 {相对路径: {sha256, size}} + payload 名单
    payload/<相对路径>    其中「新增或变化」的文件内容（其余文件客户端从旧安装复制）

用法：
    python make_update_patch.py --old-tree <旧版本目录> --new-tree <新版本目录> \
        --from 1.2.3 --to 1.2.4 --out patch-1.2.3-to-1.2.4.dpatch \
        [--extra updater.exe=dist_updater/updater.exe] ...

    --extra 用来补上「不在 new-tree 里、但要进更新包」的文件（CI 里是 updater.exe）。

退出码：
    0  正常产出补丁
    2  用法/输入有问题（调用方应视为「跳过」而不是发版失败）
    3  补丁不够小（payload 占比超过 --max-ratio），不值得发，跳过

命名红线（与 services/update_service.py 的常量一致）：
    产物名必须形如 patch-<from>-to-<to>.dpatch，**绝不能**带 .zip / -win64.zip /
    chongshi- 前缀 —— 老版本客户端只挑 .zip 且优先认「chongshi- + -win64.zip」，
    一旦补丁长得像全量包，旧客户端会把补丁当全量包下载，更新直接失败。
"""

import argparse
import hashlib
import json
import os
import sys
import zipfile

# 与 services/update_service.py 对齐（那边是权威定义，这里只是复刻常量以免 import 依赖 requests）
PATCH_FORMAT = 1
PATCH_MANIFEST_NAME = "manifest.json"
PATCH_PAYLOAD_DIR = "payload"

SKIP_EXIT = 2
TOO_BIG_EXIT = 3


def sha256_of(path: str, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _norm_rel(path: str) -> str:
    """统一成 zip 里用的正斜杠相对路径。"""
    return path.replace("\\", "/").strip("/")


def walk_tree(root: str) -> dict:
    """遍历一棵目录树 -> {相对路径: 绝对路径}。只收文件。"""
    out = {}
    root = os.path.abspath(root)
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = _norm_rel(os.path.relpath(full, root))
            if rel:
                out[rel] = full
    return out


def parse_extra(items) -> dict:
    """把 ["updater.exe=dist_updater/updater.exe", ...] 解析成 {相对路径: 绝对路径}。"""
    extra = {}
    for it in items or []:
        if "=" not in it:
            raise ValueError(f"--extra 需要 相对路径=本地路径 的形式，收到：{it!r}")
        rel, path = it.split("=", 1)
        rel = _norm_rel(rel)
        if not rel:
            raise ValueError(f"--extra 的相对路径是空的：{it!r}")
        if not os.path.isfile(path):
            raise ValueError(f"--extra 指向的文件不存在：{path}")
        extra[rel] = os.path.abspath(path)
    return extra


def build_patch(old_tree: str, new_tree: str, from_ver: str, to_ver: str, out_path: str,
                extras=None, max_ratio: float = 0.6) -> dict:
    """对比新旧两棵树，写出 .dpatch，返回统计信息 dict。"""
    new_files = walk_tree(new_tree)
    if not new_files:
        raise ValueError(f"新版本目录是空的（或不存在）：{new_tree}")
    new_files.update(parse_extra(extras))

    old_files = walk_tree(old_tree) if old_tree and os.path.isdir(old_tree) else {}
    if not old_files:
        raise ValueError(f"旧版本目录是空的（或不存在）：{old_tree}")

    # 旧树的 sha256 表（只对同名文件算，省时间）
    old_sha = {}
    for rel in new_files:
        src = old_files.get(rel)
        if src:
            old_sha[rel] = sha256_of(src)

    manifest_files = {}
    payload = []
    payload_bytes = 0
    new_bytes = 0
    for rel in sorted(new_files):
        full = new_files[rel]
        size = os.path.getsize(full)
        digest = sha256_of(full)
        manifest_files[rel] = {"sha256": digest, "size": size}
        new_bytes += size
        if old_sha.get(rel) != digest:          # 新增，或内容变了
            payload.append(rel)
            payload_bytes += size

    ratio = (payload_bytes / new_bytes) if new_bytes else 1.0
    if ratio > max_ratio:
        raise PatchTooBig(
            f"补丁不够小（payload {payload_bytes/1024/1024:.1f}MB / 全量 "
            f"{new_bytes/1024/1024:.1f}MB = {ratio:.0%} > {max_ratio:.0%}），不值得发")

    manifest = {
        "format": PATCH_FORMAT,
        "from": from_ver,
        "to": to_ver,
        "files": manifest_files,
        "payload": payload,
    }

    out_dir = os.path.dirname(os.path.abspath(out_path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    # 先写临时文件再改名，避免半截补丁被当成成品
    tmp_path = out_path + ".part"
    with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        z.writestr(PATCH_MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False))
        for rel in payload:
            z.write(new_files[rel], f"{PATCH_PAYLOAD_DIR}/{rel}")
    os.replace(tmp_path, out_path)

    return {
        "out": out_path,
        "patch_bytes": os.path.getsize(out_path),
        "new_bytes": new_bytes,
        "files_total": len(manifest_files),
        "files_payload": len(payload),
        "payload_bytes": payload_bytes,
        "removed": len([r for r in old_files if r not in manifest_files]),
    }


class PatchTooBig(Exception):
    """补丁相对全量没有明显优势，调用方应跳过而不是发出去。"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="生成增量更新补丁（.dpatch）")
    ap.add_argument("--old-tree", required=True, help="上一个版本的解压目录")
    ap.add_argument("--new-tree", required=True, help="本次打包产物目录（如 dist/虫师）")
    ap.add_argument("--from", dest="from_ver", required=True, help="起点版本号，如 1.2.3")
    ap.add_argument("--to", dest="to_ver", required=True, help="目标版本号，如 1.2.4")
    ap.add_argument("--out", required=True, help="输出的 .dpatch 路径")
    ap.add_argument("--extra", action="append", default=[],
                    metavar="相对路径=本地路径",
                    help="补进更新包但不在 new-tree 里的文件（可多次），如 updater.exe=dist_updater/updater.exe")
    ap.add_argument("--max-ratio", type=float, default=0.6,
                    help="payload 占全量的比例上限，超过则判定不值得发（默认 0.6）")
    args = ap.parse_args(argv)

    try:
        stat = build_patch(args.old_tree, args.new_tree, args.from_ver, args.to_ver,
                           args.out, extras=args.extra, max_ratio=args.max_ratio)
    except PatchTooBig as e:
        print(f"[skip] {e}")
        return TOO_BIG_EXIT
    except Exception as e:
        print(f"[error] {type(e).__name__}: {e}")
        return SKIP_EXIT

    print(f"[ok] {stat['out']}")
    print(f"     全量 {stat['new_bytes']/1024/1024:.1f} MB / {stat['files_total']} 个文件")
    print(f"     补丁 {stat['patch_bytes']/1024/1024:.1f} MB"
          f"（payload {stat['files_payload']} 个文件、{stat['payload_bytes']/1024/1024:.1f} MB，"
          f"其余 {stat['files_total'] - stat['files_payload']} 个文件客户端复用旧安装）")
    if stat["new_bytes"]:
        print(f"     下载量约为原来的 {stat['patch_bytes'] / stat['new_bytes']:.1%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
