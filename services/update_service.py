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

    @property
    def sha256(self) -> str:
        """期望的 sha256（GitHub 不带 digest 时为空 = 跳过哈希校验）。"""
        d = (self.digest or "").strip().lower()
        return d.split("sha256:", 1)[1] if d.startswith("sha256:") else ""

    @property
    def asset_size_text(self) -> str:
        mb = self.asset_size / 1024 / 1024
        return f"{mb:.1f} MB" if mb >= 1 else f"{self.asset_size / 1024:.0f} KB"


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


def download_asset(info: UpdateInfo, dest_dir: str,
                   progress: Optional[Callable[[int, int], None]] = None,
                   cancel: Optional[Callable[[], bool]] = None) -> str:
    """把更新包下载到 dest_dir，返回本地路径。

    progress(已下载, 总字节) 用于驱动进度条；cancel() 返回 True 时中断并删除半成品。
    下载中先写 .part 再改名，避免半截文件被当成下载完成。

    大文件健壮性（296MB+ 的包经国内镜像下载，镜像抽风很常见）：
      * 读超时 60s（API 的 15s 对流式下载太苛刻，某段卡 15s 就整个失败）
      * 断点续传：失败后从 .part 已有字节数带 Range 头继续（镜像不支持则从头）
      * 最多尝试 3 次，全部失败才把错误抛给用户
    """
    if not info.asset_url:
        raise RuntimeError("该 Release 里没有可用的更新包")
    os.makedirs(dest_dir, exist_ok=True)
    target = os.path.join(dest_dir, info.asset_name or "update.zip")
    part = target + ".part"

    url = _download_url(info.asset_url)
    logger.info("下载更新包（镜像=%s）：%s", _mirror_prefix() or "直连", url)

    last_error = None
    for attempt in range(1, _DOWNLOAD_ATTEMPTS + 1):
        try:
            _download_once(url, part, info, progress, cancel)
            os.replace(part, target)
            logger.info("更新包已下载: %s", target)
            break
        except RuntimeError as e:
            if "已取消" in str(e):
                raise                     # 用户主动取消，不重试
            last_error = e
            logger.warning("下载中断（第 %d/%d 次）：%s",
                           attempt, DOWNLOAD_ATTEMPTS, str(e)[:120])
            if attempt < DOWNLOAD_ATTEMPTS:
                time.sleep(2)             # 稍等再续传
        except Exception as e:
            last_error = e
            logger.warning("下载中断（第 %d/%d 次）：%s",
                           attempt, DOWNLOAD_ATTEMPTS, str(e)[:120])
            if attempt < DOWNLOAD_ATTEMPTS:
                time.sleep(2)
    else:
        raise RuntimeError(
            f"下载更新包失败（已自动重试 {DOWNLOAD_ATTEMPTS - 1} 次）："
            f"{str(last_error)[:150]}")

    # 校验：GitHub 的资产带 digest（sha256:...）时逐个字节核对；没有就只保证 zip 自身完整
    expect = info.sha256
    if expect:
        actual = sha256_of(target)
        if actual != expect:
            os.remove(target)
            raise RuntimeError(
                f"更新包校验失败（sha256 不符，可能下载被篡改或中断）："
                f"期望 {expect[:12]}… 实际 {actual[:12]}…")
        logger.info("更新包 sha256 校验通过")
    else:
        logger.warning("该 Release 资产没有 digest 字段，跳过 sha256 校验（仅校验 zip 完整性）")

    return target


def _download_once(url, part, info, progress, cancel):
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
            or info.asset_size or 0
        with open(part, file_mode) as f:
            for chunk in resp.iter_content(chunk_size=256 * 1024):
                if cancel and cancel():
                    f.close()
                    raise RuntimeError("已取消")
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


def extract_zip(zip_path: str, dest: str,
                progress: Optional[Callable[[int, int], None]] = None) -> str:
    """把更新包解压到 dest，返回 dest。

    **zip-slip 安全**：条目名一律按 / 切开后拼到 dest 下，凡是绝对路径、带盘符、
    或含 .. 的条目都跳过 —— 更新包来自网络，不能让一个畸形 zip 往安装目录外写文件。
    """
    dest_abs = os.path.abspath(dest)
    with zipfile.ZipFile(zip_path) as z:
        bad = z.testzip()                      # 顺带验证压缩包自身完整（CRC）
        if bad:
            raise RuntimeError(f"更新包损坏（第一个坏文件：{bad}）")
        files = [n for n in z.namelist() if not n.replace("\\", "/").endswith("/")]
        for i, name in enumerate(files, 1):
            norm = name.replace("\\", "/")
            parts = [p for p in norm.split("/") if p not in ("", ".")]
            # 可疑条目一律不要：绝对路径、以 / 开头（Windows 上 os.path.isabs 不认这种）、
            # 带盘符、含 ..
            if (os.path.isabs(norm) or norm.startswith("/") or re.match(r"^[A-Za-z]:", norm)
                    or not parts or any(p == ".." for p in parts)):
                logger.warning("更新包里有可疑路径，已跳过：%r", name)
                continue
            target = os.path.join(dest_abs, *parts)
            if not os.path.abspath(target).startswith(dest_abs + os.sep):
                logger.warning("更新包条目越界，已跳过：%r", name)
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


def install_prepare(info: UpdateInfo,
                   progress: Optional[Callable[[int, int], None]] = None,
                   cancel: Optional[Callable[[], bool]] = None,
                   on_stage: Optional[Callable[[str], None]] = None) -> str:
    """把更新准备好（下载 -> 校验 -> 解压 -> 检查），返回交给 updater 的 --src 目录。

    **同步阻塞**，调用方负责放到后台线程并驱动进度条。
    on_stage('download' | 'extract') 用来让界面知道现在处于哪个阶段。
    """
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
