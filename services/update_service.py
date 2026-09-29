# services/update_service.py
"""检查更新：查 GitHub Releases、挑更新包、下载与校验。

分层约定：**这里只有网络与版本比较，不碰界面、不自己开线程** —— 谁调它、在哪个
线程跑、结果怎么显示，都由调用方决定（main.py 里是在后台 daemon 线程里跑，
结果用信号投递回 GUI 线程）。

另一个约定：**任何失败都不抛异常**，而是把原因放进返回值里。公司网络访问不了
GitHub 是很正常的事，不该让用户的启动流程因此弹错误框。

本地版本号来自 utils/version.py（单点来源），远端取 Release 的 tag。
"""

import hashlib
import json
import logging
import os
import re
import shutil
import zipfile
from dataclasses import dataclass
from typing import Callable, Optional

import requests
from packaging.version import InvalidVersion, Version

from utils.app_paths import get_app_dir
from utils.version import APP_VERSION

logger = logging.getLogger(__name__)

# ---------- 配置（都在这一处，改完重新打包即可） ----------
# 你的 GitHub 仓库，"owner/repo"。**留空 = 关闭更新检查**（不会联网、不亮红点），
# 这样没建好仓库之前也不会误报。
UPDATE_REPO = "amosli0806/AndroidAutoTest"

# 用来做自更新的那个资产的后缀，CI 里按这个规则产出（如 chongshi-1.1.4-win64.zip）。
# 找不到匹配的资产时会退而取第一个 .zip。
UPDATE_ASSET_SUFFIX = "-win64.zip"

# 更新包名的固定前缀（CI 压包时用纯英文名，见 .github/workflows/release.yml）。
# 为什么挑包时优先认它：v1.1.3 首次发版中文文件名被 GitHub 丢弃了前缀，留下一个
# 残缺的 "-1.1.3-win64.zip"，它同样以 -win64.zip 结尾、还排在列表里，会抢走匹配。
# 优先认带前缀的完整名，就能免疫这种残缺资产；前缀匹配不到再退后缀匹配。
UPDATE_ASSET_PREFIX = "chongshi-"

# 把接口地址整个换掉的开关（本地联调、或以后想改成内网镜像时用得上）。
UPDATE_API_OVERRIDE_ENV = "CHONGSHI_UPDATE_API"

# ---------- 下载加速镜像 ----------
# 国内直连 GitHub 的文件实体（objects.githubusercontent.com）经常超时/极慢，
# 应用内「检查更新」下载更新包就是撞在这里。下面这个前缀只在**下载 asset 时**拼到
# browser_download_url 前面，让下载走国内加速通道；**检查更新仍直连 GitHub API**，
# 版本号、摘要（sha256）都来自官方，下载完还会逐字节核对摘要，镜像只负责传输、
# 无法篡改内容。
#
# 实测（2026-09-23，本机）：gh-proxy.com 约 5 MB/s；ghproxy.cn 4 KB/s（形同断网）；
# ghfast.top / github.moeyy.xyz / gh.llkk.cc 均超时。镜像站时效性不稳定，换哪个改这里。
# **留空 = 直连 GitHub，不走镜像**（海外/能直连的用户用空串）。
DOWNLOAD_MIRROR = "https://gh-proxy.com/"

# 用环境变量整体覆盖镜像（打包后不改代码也能换，运维排障用）。
DOWNLOAD_MIRROR_OVERRIDE_ENV = "CHONGSHI_DOWNLOAD_MIRROR"

_CONNECT_TIMEOUT = 5
_READ_TIMEOUT = 15

# 下载大文件（300MB+ 的更新包经国内镜像）专用：
# 读超时 15s 对流式下载太苛刻——镜像某一段卡 15s 就整个失败（用户实测）。
# 断点续传 + 重试次数见 download_asset。
_DOWNLOAD_READ_TIMEOUT = 60
_DOWNLOAD_ATTEMPTS = 3

# ---------- 增量更新（差分补丁） ----------
# 补丁资产命名：patch-<起点版本>-to-<目标版本>.dpatch，例如 patch-1.2.3-to-1.2.4.dpatch。
#
# 命名为什么必须避开 ".zip" / "-win64.zip" / "chongshi-" 前缀：
# 老版本客户端的 _pick_asset 只挑 .zip 资产、且优先认「chongshi- 开头 + -win64.zip 结尾」，
# 一旦补丁带上这些特征，**旧客户端会把补丁当成全量包下载**，解压后缺 虫师.exe/_internal
# 直接更新失败。现在这个命名对老客户端完全不可见（它的 .zip 过滤直接把它排除），零影响。
PATCH_ASSET_SUFFIX = ".dpatch"
PATCH_ASSET_TEMPLATE = "patch-{from_ver}-to-{to_ver}.dpatch"

# 补丁包内部结构：
#   manifest.json          —— 新版本的**完整**文件清单 {相对路径: {sha256, size}}
#   payload/<相对路径>      —— 其中「新增或变化」的文件内容（其余文件从本机旧安装复制）
PATCH_MANIFEST_NAME = "manifest.json"
PATCH_PAYLOAD_DIR = "payload"
PATCH_FORMAT = 1


class UpdateCancelled(RuntimeError):
    """用户主动取消（下载或组装过程中）。调用方据此区分「取消」与「真失败」。"""


def _digest_to_sha256(digest: str) -> str:
    """GitHub 的资产摘要 "sha256:xxxx" -> "xxxx"；不带摘要时返回空串（= 跳过哈希校验）。"""
    d = (digest or "").strip().lower()
    return d.split("sha256:", 1)[1] if d.startswith("sha256:") else ""


@dataclass
class UpdateInfo:
    """一次「发现新版本」的全部事实。"""
    version: str = ""           # 去掉 v 前缀的版本号，如 1.1.4
    tag: str = ""               # 原始 tag，如 v1.1.4
    notes: str = ""             # Release 说明（Markdown 原文）
    published_at: str = ""
    html_url: str = ""          # Release 页面
    asset_name: str = ""
    asset_url: str = ""
    asset_size: int = 0
    digest: str = ""            # GitHub 给的资产摘要，形如 "sha256:xxxx"
    # 增量补丁（这次 Release 里有、且起点版本正好是本机当前版本时才有值）
    patch_name: str = ""
    patch_url: str = ""
    patch_size: int = 0
    patch_digest: str = ""
    patch_from: str = ""        # 补丁的起点版本；必须等于本机当前版本才可用

    @property
    def sha256(self) -> str:
        """期望的 sha256（GitHub 不带 digest 时为空 = 跳过哈希校验）。"""
        return _digest_to_sha256(self.digest)

    @property
    def patch_sha256(self) -> str:
        return _digest_to_sha256(self.patch_digest)

    @property
    def asset_size_text(self) -> str:
        mb = self.asset_size / 1024 / 1024
        return f"{mb:.1f} MB" if mb >= 1 else f"{self.asset_size / 1024:.0f} KB"

    @property
    def patch_size_text(self) -> str:
        mb = self.patch_size / 1024 / 1024
        return f"{mb:.1f} MB" if mb >= 1 else f"{self.patch_size / 1024:.0f} KB"


@dataclass
class CheckResult:
    """检查结果。status 取值：
    update_available / up_to_date / error / disabled
    """
    status: str
    info: Optional[UpdateInfo] = None
    error: str = ""
    current_version: str = APP_VERSION


def is_enabled() -> bool:
    """更新检查是否可用：填了仓库地址，**或者**用 UPDATE_API_OVERRIDE_ENV 指了别的接口。

    后者是本地联调 / 内网镜像的口子 —— 只改环境变量就能把整个检查指向别处，
    不该因为 UPDATE_REPO 是空的而被判成"未配置"。
    """
    if os.environ.get(UPDATE_API_OVERRIDE_ENV, "").strip():
        return True
    return bool(UPDATE_REPO and "/" in UPDATE_REPO)


def _api_url() -> str:
    override = os.environ.get(UPDATE_API_OVERRIDE_ENV, "").strip()
    if override:
        return override
    return f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest"


def _strip_v(tag: str) -> str:
    return (tag or "").strip().lstrip("vV")


def _is_newer(remote: str, local: str) -> bool:
    """远端版本是否比本地新。任一侧不是规范版本号时一律判「不是」，宁可不提示。"""
    try:
        return Version(_strip_v(remote)) > Version(_strip_v(local))
    except InvalidVersion:
        logger.warning("版本号无法比较：远端=%r 本地=%r", remote, local)
        return False


def _pick_asset(assets) -> dict:
    """挑自更新用的包：优先「前缀+版本+后缀」的完整名，其次后缀匹配，最后第一个 zip。

    优先级这么排的原因见 UPDATE_ASSET_PREFIX 的注释：残缺资产（中文名丢前缀后的
    "-x.y.z-win64.zip"）也会命中后缀，但它不是我们想发的那个包。
    """
    zips = [a for a in assets if str(a.get("name", "")).lower().endswith(".zip")]
    # 1) 完整名：前缀 + 任意版本 + 后缀
    for a in zips:
        name = a.get("name", "")
        if name.startswith(UPDATE_ASSET_PREFIX) and name.endswith(UPDATE_ASSET_SUFFIX):
            return a
    # 2) 后缀匹配（兜底：前缀换过名、或 CI 改名了）
    for a in zips:
        if a.get("name", "").endswith(UPDATE_ASSET_SUFFIX):
            return a
    # 3) 第一个 zip
    return zips[0] if zips else {}


def _patch_from_of_name(name: str) -> str:
    """从补丁资产名里取出起点版本：patch-<from>-to-<to>.dpatch -> <from>。"""
    m = re.match(r"^patch-(.+?)-to-(.+)\.dpatch$", (name or "").strip())
    return m.group(1) if m else ""


def _pick_patch_asset(assets, from_version: str, to_version: str) -> dict:
    """挑「起点=本机当前版本」的增量补丁资产；没有则返回 {}。

    只做**精确名字匹配**（patch-<from>-to-<to>.dpatch），不做模糊匹配 ——
    补丁必须严格对应「本机这一版 → 目标版」这一对，配错了会组装出一棵坏目录树。
    也正因为只认这个精确名，老客户端（只挑 .zip）根本看不到补丁，不受任何影响。
    """
    want = PATCH_ASSET_TEMPLATE.format(from_ver=from_version, to_ver=to_version)
    for a in assets or []:
        if str(a.get("name", "")) == want:
            return a
    return {}


def check_for_update() -> CheckResult:
    """查最新 Release 并与本地版本比较。**同步阻塞**，别在 GUI 线程里直接调。"""
    if not is_enabled():
        return CheckResult(status="disabled",
                           error="未配置更新仓库（services/update_service.py 的 UPDATE_REPO）")

    url = _api_url()
    try:
        resp = requests.get(
            url,
            timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT),
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "chongshi-updater",
            },
        )
    except Exception as e:
        logger.warning("检查更新失败（网络）: %s: %s", type(e).__name__, e)
        return CheckResult(status="error", error=f"网络不可达：{type(e).__name__}")

    if resp.status_code == 404:
        # 仓库里还没有任何 Release —— 不算错误
        return CheckResult(status="up_to_date")
    if resp.status_code != 200:
        logger.warning("检查更新失败: HTTP %s %s", resp.status_code, resp.text[:200])
        return CheckResult(status="error", error=f"HTTP {resp.status_code}")

    try:
        data = resp.json()
    except Exception as e:
        return CheckResult(status="error", error=f"返回内容不是合法 JSON：{e}")

    tag = str(data.get("tag_name", ""))
    if not tag:
        return CheckResult(status="error", error="Release 里没有 tag_name")

    if not _is_newer(tag, APP_VERSION):
        return CheckResult(status="up_to_date")

    asset = _pick_asset(data.get("assets") or [])
    info = UpdateInfo(
        version=_strip_v(tag),
        tag=tag,
        notes=str(data.get("body", "") or ""),
        published_at=str(data.get("published_at", "") or ""),
        html_url=str(data.get("html_url", "") or ""),
        asset_name=str(asset.get("name", "") or ""),
        asset_url=str(asset.get("browser_download_url", "") or ""),
        asset_size=int(asset.get("size", 0) or 0),
        digest=str(asset.get("digest", "") or ""),
    )
    # 增量补丁（best-effort）：Release 里若带了「本机当前版本 → 目标版本」的补丁就记下；
    # 找不到不影响更新，这次走全量下载。任何解析异常都按「没有补丁」处理。
    try:
        patch = _pick_patch_asset(data.get("assets") or [], APP_VERSION, info.version)
        if patch:
            info.patch_name = str(patch.get("name", "") or "")
            info.patch_url = str(patch.get("browser_download_url", "") or "")
            info.patch_size = int(patch.get("size", 0) or 0)
            info.patch_digest = str(patch.get("digest", "") or "")
            info.patch_from = _patch_from_of_name(info.patch_name)
            logger.info("发现增量补丁 %s（%s），本次可只下载它",
                        info.patch_name, info.patch_size_text)
    except Exception as e:
        logger.warning("增量补丁信息解析失败（按全量处理）: %s: %s", type(e).__name__, e)
    logger.info("发现新版本 %s（当前 %s）", info.version, APP_VERSION)
    return CheckResult(status="update_available", info=info)


def sha256_of(path: str, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _mirror_prefix() -> str:
    """当前生效的下载镜像前缀（末尾带 /），空串 = 直连。环境变量可整体覆盖。"""
    override = os.environ.get(DOWNLOAD_MIRROR_OVERRIDE_ENV, "").strip()
    if override:
        return override if override.endswith("/") else override + "/"
    if not DOWNLOAD_MIRROR:
        return ""
    return DOWNLOAD_MIRROR if DOWNLOAD_MIRROR.endswith("/") else DOWNLOAD_MIRROR + "/"


def _download_url(asset_url: str) -> str:
    """下载 asset 用的 URL：只对指向 GitHub 官方域的 URL 拼镜像前缀。

    刻意不镜像「检查更新」那个 API——版本与 sha256 摘要必须来自官方；
    镜像只负责搬运字节，下载完仍逐字节核对摘要，保证内容没被篡改。

    为什么只对 GitHub 域名拼前缀：自测/联调时 asset_url 会被指到本地桩服务
    （http://127.0.0.1:...），或以后指向内网镜像——那些本来就能直连，拼上
    gh-proxy 反而会坏。判断依据是 URL 的 host 属于 github.com / *.github.com。
    """
    prefix = _mirror_prefix()
    if not prefix:
        return asset_url
    host = ""
    try:
        from urllib.parse import urlparse
        host = (urlparse(asset_url).hostname or "").lower()
    except Exception:
        host = ""
    if not (host == "github.com" or host.endswith(".github.com")):
        return asset_url          # 非 GitHub 官方域：保持直连
    return prefix + asset_url


def _download_file(url: str, name: str, size_hint: int, expect_sha256: str,
                   dest_dir: str,
                   progress: Optional[Callable[[int, int], None]] = None,
                   cancel: Optional[Callable[[], bool]] = None) -> str:
    """把 url 下载到 dest_dir/name，返回本地路径。全量包与增量补丁共用这一套。

    progress(已下载, 总字节) 用于驱动进度条；cancel() 返回 True 时中断并删除半成品。
    下载中先写 .part 再改名，避免半截文件被当成下载完成。

    大文件健壮性（290MB+ 的包经国内镜像下载，镜像抽风很常见）：
      * 读超时 60s（API 的 15s 对流式下载太苛刻，某段卡 15s 就整个失败）
      * 断点续传：失败后从 .part 已有字节数带 Range 头继续（镜像不支持则从头）
      * 最多尝试 3 次，全部失败才把错误抛给用户
    """
    if not url:
        raise RuntimeError("该 Release 里没有可用的更新包")
    os.makedirs(dest_dir, exist_ok=True)
    target = os.path.join(dest_dir, name or "update.bin")
    part = target + ".part"

    dl_url = _download_url(url)
    logger.info("下载更新文件（镜像=%s）：%s", _mirror_prefix() or "直连", dl_url)

    last_error = None
    for attempt in range(1, _DOWNLOAD_ATTEMPTS + 1):
        try:
            _download_once(dl_url, part, size_hint, progress, cancel)
            os.replace(part, target)
            logger.info("更新文件已下载: %s", target)
            break
        except UpdateCancelled:
            raise                         # 用户主动取消，不重试
        except Exception as e:
            last_error = e
            logger.warning("下载中断（第 %d/%d 次）：%s",
                           attempt, _DOWNLOAD_ATTEMPTS, str(e)[:120])
            if attempt < _DOWNLOAD_ATTEMPTS:
                time.sleep(2)             # 稍等再续传
    else:
        raise RuntimeError(
            f"下载更新文件失败（已自动重试 {_DOWNLOAD_ATTEMPTS - 1} 次）："
            f"{str(last_error)[:150]}")

    # 校验：GitHub 的资产带 digest（sha256:...）时逐个字节核对；没有就只保证文件自身完整
    if expect_sha256:
        actual = sha256_of(target)
        if actual != expect_sha256:
            os.remove(target)
            raise RuntimeError(
                f"更新文件校验失败（sha256 不符，可能下载被篡改或中断）："
                f"期望 {expect_sha256[:12]}… 实际 {actual[:12]}…")
        logger.info("更新文件 sha256 校验通过")
    else:
        logger.warning("该资产没有 digest 字段，跳过 sha256 校验")

    return target


def download_asset(info: UpdateInfo, dest_dir: str,
                   progress: Optional[Callable[[int, int], None]] = None,
                   cancel: Optional[Callable[[], bool]] = None) -> str:
    """下载**全量**更新包。内部复用 _download_file，对外行为与历史一致。"""
    return _download_file(info.asset_url, info.asset_name or "update.zip",
                          info.asset_size, info.sha256, dest_dir, progress, cancel)


def download_patch(info: UpdateInfo, dest_dir: str,
                   progress: Optional[Callable[[int, int], None]] = None,
                   cancel: Optional[Callable[[], bool]] = None) -> str:
    """下载**增量补丁**（patch-<from>-to-<to>.dpatch）。"""
    return _download_file(info.patch_url, info.patch_name or "update.dpatch",
                          info.patch_size, info.patch_sha256, dest_dir, progress, cancel)


def _download_once(url, part, size_hint, progress, cancel):
    """单次下载尝试：支持从 .part 已有字节断点续传（镜像支持 Range 时）。

    - 206 Partial Content：续传成功，从断点追加
    - 200 OK：镜像忽略了 Range，从头发，已有 .part 作废
    """
    done = os.path.getsize(part) if os.path.exists(part) else 0
    headers = {"Range": f"bytes={done}-"} if done > 0 else {}
    file_mode = "ab" if done > 0 else "wb"

    with requests.get(url, stream=True,
                      timeout=(_CONNECT_TIMEOUT, _DOWNLOAD_READ_TIMEOUT),
                      headers=headers) as resp:
        if done > 0 and resp.status_code == 200:
            done = 0                       # 镜像不支持 Range，推倒重来
            file_mode = "wb"
        resp.raise_for_status()
        total = (int(resp.headers.get("Content-Length") or 0) + done) \
            or size_hint or 0
        with open(part, file_mode) as f:
            for chunk in resp.iter_content(chunk_size=256 * 1024):
                if cancel and cancel():
                    f.close()
                    raise UpdateCancelled("已取消")
                if not chunk:
                    continue
                f.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)


# ---------- 暂存与解压：下载 -> 校验 -> 解压 -> 交给 updater.exe ----------
# 为什么暂存放在**安装目录下**而不是 %TEMP%：
#   安装目录通常在 D:、%TEMP% 在 C:，跨卷"移动" 600MB 实际是复制+删除（慢，且中途失败
#   要回滚）；同卷下整个交换只是两次瞬时 os.rename，失败也能瞬时改回来（见 updater.py）。
APP_EXE_NAME = "虫师.exe"
INTERNAL_DIR_NAME = "_internal"
UPDATER_EXE_NAME = "updater.exe"
STAGING_DIR_NAME = "_update_staging"
REQUIRED_IN_PACKAGE = (APP_EXE_NAME, INTERNAL_DIR_NAME, UPDATER_EXE_NAME)


def _safe_version(version: str) -> str:
    """版本号要当路径的一段用，只允许字母数字和 . _ -（防止 tag 里塞 '../' 之类）。"""
    safe = re.sub(r"[^0-9A-Za-z._-]", "_", version or "")
    return safe.strip("._-") or "unknown"


def staging_root() -> str:
    return os.path.join(get_app_dir(), STAGING_DIR_NAME)


def staging_dir(version: str) -> str:
    return os.path.join(staging_root(), _safe_version(version))


def free_space_mb(path: str) -> float:
    try:
        return shutil.disk_usage(path).free / 1024 / 1024
    except Exception:
        return -1.0


def prepare_staging_dir(version: str, asset_size: int = 0) -> str:
    """准备（并先清空）这一版的暂存目录；安装目录不可写、磁盘不够时直接报错。"""
    root = staging_root()
    try:
        os.makedirs(root, exist_ok=True)
        probe = os.path.join(root, ".write_test")
        with open(probe, "w") as f:
            f.write("")
        os.remove(probe)
    except Exception as e:
        raise RuntimeError(
            f"安装目录不可写，无法自动更新：\n{root}\n{e}\n\n"
            f"请把虫师放在可写目录，或以管理员身份运行后再更新。")

    need_mb = max(600.0, (asset_size / 1024 / 1024) * 3.2)   # 解压后约为压缩包的 3 倍
    free_mb = free_space_mb(root)
    if 0 <= free_mb < need_mb:
        raise RuntimeError(f"磁盘空间不足：更新需要约 {need_mb:.0f} MB，当前可用 {free_mb:.0f} MB")

    dest = staging_dir(version)
    if os.path.isdir(dest):
        shutil.rmtree(dest, ignore_errors=True)
    os.makedirs(dest, exist_ok=True)
    return dest


def _safe_join(root_abs: str, name: str, what: str = "更新包") -> Optional[str]:
    """把压缩包条目名安全地拼到 root_abs 下；可疑条目记日志并返回 None。

    **zip-slip 安全**：绝对路径、以 / 开头（Windows 上 os.path.isabs 不认这种）、
    带盘符、含 .. 的条目一律拒绝 —— 更新包来自网络，不能让一个畸形 zip 往安装目录外
    写文件。extract_zip 与增量补丁组装共用这一套判断，避免两处规则走偏。
    """
    norm = (name or "").replace("\\", "/")
    parts = [p for p in norm.split("/") if p not in ("", ".")]
    if (os.path.isabs(norm) or norm.startswith("/") or re.match(r"^[A-Za-z]:", norm)
            or not parts or any(p == ".." for p in parts)):
        logger.warning("%s里有可疑路径，已跳过：%r", what, name)
        return None
    target = os.path.join(root_abs, *parts)
    if not os.path.abspath(target).startswith(root_abs + os.sep):
        logger.warning("%s条目越界，已跳过：%r", what, name)
        return None
    return target


def extract_zip(zip_path: str, dest: str,
                progress: Optional[Callable[[int, int], None]] = None) -> str:
    """把更新包解压到 dest，返回 dest。

    **zip-slip 安全**：条目一律经 _safe_join 过滤，可疑条目跳过。
    """
    dest_abs = os.path.abspath(dest)
    with zipfile.ZipFile(zip_path) as z:
        bad = z.testzip()                      # 顺带验证压缩包自身完整（CRC）
        if bad:
            raise RuntimeError(f"更新包损坏（第一个坏文件：{bad}）")
        files = [n for n in z.namelist() if not n.replace("\\", "/").endswith("/")]
        for i, name in enumerate(files, 1):
            target = _safe_join(dest_abs, name)
            if target is None:
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with z.open(name) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out, 1024 * 1024)
            if progress:
                progress(i, len(files))
    return dest


def validate_package(root: str):
    """解压出来的东西是不是一份完整的新版本。返回 (是否可用, 原因)。"""
    missing = [n for n in REQUIRED_IN_PACKAGE if not os.path.exists(os.path.join(root, n))]
    if missing:
        return False, "更新包里缺少：" + "、".join(missing)
    return True, ""


def patch_available(info: UpdateInfo) -> bool:
    """这次更新能否走增量：Release 里带了补丁，且补丁的起点正好是本机当前版本。"""
    if not info or not info.patch_url or not info.patch_name:
        return False
    return info.patch_from == APP_VERSION


def _install_root() -> str:
    """本机当前安装目录 —— 增量组装时从这里复用「没变」的文件。

    抽成函数是为了自测能替换它：自测跑在源码目录里，那里并不是一份安装树。
    """
    return get_app_dir()


def _sha256_stream_to(src_fileobj, dst_path: str) -> str:
    """把 src_fileobj 写到 dst_path，边写边算 sha256，返回十六进制摘要（只读一遍）。"""
    h = hashlib.sha256()
    with open(dst_path, "wb") as out:
        while True:
            block = src_fileobj.read(1024 * 1024)
            if not block:
                break
            h.update(block)
            out.write(block)
    return h.hexdigest()


def apply_patch(patch_path: str, old_root: str, dest: str,
                progress: Optional[Callable[[int, int], None]] = None,
                cancel: Optional[Callable[[], bool]] = None) -> str:
    """按补丁在 dest 组装出新版本目录，返回 dest。

    补丁的 manifest 列出**新版本的完整文件清单**：清单里进了 payload 的文件用补丁内容，
    其余从 old_root（本机旧安装）复制 —— 复制时顺带算 sha256 与清单比对，对不上就整体
    失败（调用方据此回退全量下载）。于是「没变的 600MB 大文件」一个字节都不用下。

    任何一步不满足都抛异常 —— 宁可回退全量，也不要组装出一棵坏目录树。
    """
    old_abs = os.path.abspath(old_root)
    dest_abs = os.path.abspath(dest)
    with zipfile.ZipFile(patch_path) as z:
        bad = z.testzip()
        if bad:
            raise RuntimeError(f"增量补丁损坏（第一个坏文件：{bad}）")
        try:
            manifest = json.loads(z.read(PATCH_MANIFEST_NAME).decode("utf-8"))
        except KeyError:
            raise RuntimeError("增量补丁缺少 manifest.json")
        except Exception as e:
            raise RuntimeError(f"增量补丁清单无法解析：{e}")

        if int(manifest.get("format", 0) or 0) != PATCH_FORMAT:
            raise RuntimeError(f"增量补丁格式不支持：{manifest.get('format')!r}")
        files = manifest.get("files") or {}
        if not files:
            raise RuntimeError("增量补丁清单是空的")
        payload = set(manifest.get("payload") or [])

        total = len(files)
        for i, (rel, meta) in enumerate(files.items(), 1):
            if cancel and cancel():
                raise UpdateCancelled("已取消")
            expect = str((meta or {}).get("sha256", "") or "").lower()
            target = _safe_join(dest_abs, rel, what="增量补丁")
            if target is None:
                raise RuntimeError(f"增量补丁清单里有可疑路径：{rel!r}")
            os.makedirs(os.path.dirname(target), exist_ok=True)

            if rel in payload:
                entry = f"{PATCH_PAYLOAD_DIR}/" + rel.replace("\\", "/")
                try:
                    with z.open(entry) as src:
                        actual = _sha256_stream_to(src, target)
                except KeyError:
                    raise RuntimeError(f"增量补丁缺少文件内容：{rel}")
            else:
                src = _safe_join(old_abs, rel, what="本机安装目录")
                if src is None or not os.path.isfile(src):
                    raise RuntimeError(f"本机安装目录缺少文件，无法增量更新：{rel}")
                with open(src, "rb") as f:
                    actual = _sha256_stream_to(f, target)

            if expect and actual != expect:
                raise RuntimeError(f"增量校验失败（内容与清单不符）：{rel}")
            if progress:
                progress(i, total)
    return dest


def install_prepare(info: UpdateInfo,
                   progress: Optional[Callable[[int, int], None]] = None,
                   cancel: Optional[Callable[[], bool]] = None,
                   on_stage: Optional[Callable[[str], None]] = None) -> str:
    """把更新准备好（下载 -> 校验 -> 解压/组装 -> 检查），返回交给 updater 的 --src 目录。

    **同步阻塞**，调用方负责放到后台线程并驱动进度条。
    on_stage('download' | 'extract' | 'apply') 用来让界面知道现在处于哪个阶段。

    有可用增量补丁时优先走增量（下载量从 290MB 降到几 MB）；**任何一步失败都自动回退
    全量下载** —— 增量只是"更快的一条路"，绝不能因为它让更新本身失败。
    """
    if patch_available(info):
        try:
            return _install_prepare_patch(info, progress, cancel, on_stage)
        except UpdateCancelled:
            raise                                  # 用户主动取消，不当作失败回退
        except Exception as e:
            logger.warning("增量更新不可用，回退全量下载：%s: %s", type(e).__name__, e)
            shutil.rmtree(staging_dir(info.version), ignore_errors=True)
    return _install_prepare_full(info, progress, cancel, on_stage)


def _install_prepare_patch(info: UpdateInfo, progress, cancel, on_stage) -> str:
    """增量路径：下补丁 -> 按清单组装 -> 检查。"""
    import tempfile
    if on_stage:
        on_stage("download")
    cache_dir = os.path.join(tempfile.gettempdir(), "chongshi_update")
    patch_path = download_patch(info, cache_dir, progress=progress, cancel=cancel)
    try:
        if on_stage:
            on_stage("apply")
        # 磁盘空间按**全量包**估算：组装出来的目录树与走全量时一模一样大，
        # 补丁只是少下载，落盘体积并没有变少。
        dest = prepare_staging_dir(info.version, info.asset_size)
        apply_patch(patch_path, _install_root(), dest, progress=progress, cancel=cancel)
    finally:
        try:
            os.remove(patch_path)      # 组装完就没用了，别占着 TEMP
        except Exception:
            pass

    ok, why = validate_package(dest)
    if not ok:
        shutil.rmtree(dest, ignore_errors=True)
        raise RuntimeError(why)
    logger.info("增量更新已就绪：%s", dest)
    return dest


def _install_prepare_full(info: UpdateInfo, progress, cancel, on_stage) -> str:
    """全量路径（与历史行为一致）：下整包 -> 校验 -> 解压 -> 检查。"""
    import tempfile
    if on_stage:
        on_stage("download")
    cache_dir = os.path.join(tempfile.gettempdir(), "chongshi_update")
    zip_path = download_asset(info, cache_dir, progress=progress, cancel=cancel)
    try:
        if on_stage:
            on_stage("extract")
        dest = prepare_staging_dir(info.version, info.asset_size)
        extract_zip(zip_path, dest, progress=progress)
    finally:
        try:
            os.remove(zip_path)      # 解压完就没用了，别占着 TEMP
        except Exception:
            pass

    ok, why = validate_package(dest)
    if not ok:
        shutil.rmtree(dest, ignore_errors=True)
        raise RuntimeError(why)
    logger.info("更新包已就绪：%s", dest)
    return dest


def pending_staged() -> str:
    """上次已经下载解压好、但用户点了「稍后」的更新包；没有则返回空串。

    有它就不用重新下载 —— 启动时可以直接提示「重启即生效」。
    """
    root = staging_root()
    if not os.path.isdir(root):
        return ""
    for name in sorted(os.listdir(root), reverse=True):     # 版本号倒序，取最新的一版
        path = os.path.join(root, name)
        if os.path.isdir(path) and validate_package(path)[0]:
            return path
    return ""


def updater_exe_in(staging: str) -> str:
    return os.path.join(staging, UPDATER_EXE_NAME)


def cleanup_staging(keep: str = "") -> int:
    """清掉暂存目录里除 keep 之外的内容。

    更新成功或失败都会留下残留（还有失败时的半截解压），启动时清一次即可。
    正在用的那一份（keep）不动。
    """
    root = staging_root()
    if not os.path.isdir(root):
        return 0
    keep_abs = os.path.abspath(keep) if keep else ""
    removed = 0
    for name in os.listdir(root):
        path = os.path.join(root, name)
        if keep_abs and os.path.abspath(path) == keep_abs:
            continue
        try:
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                os.remove(path)
            removed += 1
        except Exception as e:
            logger.warning("清理暂存失败 %s: %s", path, e)
    try:
        if not os.listdir(root):
            os.rmdir(root)
    except Exception:
        pass
    return removed
