# -*- coding: utf-8 -*-
"""临时脚本：不下载整个补丁，只读它的 manifest，确认更新器/ MCP 是否在 payload。"""
import json
import struct
import urllib.request
import zlib

import release

code, rel = release.api("GET", "/releases/tags/v1.6.0")
asset = [a for a in rel["assets"] if a["name"].endswith(".dpatch")][0]
print("asset:", asset["name"], round(asset["size"] / 1024 / 1024, 1), "MB")

req = urllib.request.Request(
    asset["browser_download_url"],
    headers={"Range": "bytes=0-4194303", "User-Agent": "chongshi-check"},
)
with urllib.request.urlopen(req, timeout=60) as resp:
    head = resp.read()
print("fetched:", len(head), "bytes")

(sig, ver, flag, method, t, d, crc, csize, usize,
 nlen, elen) = struct.unpack("<IHHHHHIIIHH", head[:30])
name = head[30:30 + nlen].decode("utf-8")
start = 30 + nlen + elen
body = head[start:start + csize]
manifest = json.loads(zlib.decompress(body, -15).decode("utf-8"))

print("first entry:", name, "compressed:", csize)
for key in ("updater.exe", "虫师-mcp.exe", "虫师.exe"):
    print(key, "->", manifest["files"].get(key))
print("payload count:", len(manifest["payload"]))
for key in ("updater.exe", "虫师-mcp.exe", "虫师.exe"):
    print("in payload:", key, "->", key in manifest["payload"])
