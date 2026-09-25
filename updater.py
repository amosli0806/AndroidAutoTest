# updater.py
"""虫师的独立更新器（被主程序下载的新包带进来，运行在安装目录之外）。

为什么要单独一个 exe：Windows 下正在运行的 exe 和已映射的 DLL 既不能删也不能覆盖
（只能改名），所以必须有"另一个进程"在主程序退出后动手。它不依赖 PyQt、只用标准库，
打包成 onefile，体积很小。

它做的事（每一步都可回滚）：

  1. 等主程序退出（按 --pid 等；等不到就放弃，绝不在程序还活着时动文件）
  2. 校验 --src 里确实是一份完整的新版本（虫师.exe + _internal 都在），否则什么都不碰
  3. 备份：安装目录下 虫师.exe -> 虫师.exe.old，_internal -> _internal.old
  4. 把新版本从 --src **改名**（同卷，瞬时）进安装目录；根目录下的小文件（虫师.exe 等）逐个改名
  5. 任何一步失败 -> 把 .old 改回来（回滚），并弹一个原生错误框告诉用户日志在哪
  6. 成功 -> 启动新版、删掉暂存目录
  7. 收尾：`_internal.old` 的删除是 best-effort（可能被杀软占用），删不掉就留给下次更新前清

**绝不触碰安装目录下的 data/**（那是用户的项目、用例、元素库）。

用法：
    updater.exe --src <新版目录> --dst <安装目录> --pid <主程序pid> [--restart <要启动的exe>]
"""

import argparse
import ctypes
import logging
import os
import shutil
import subprocess
import sys
import time

APP_EXE = "虫师.exe"
INTERNAL_DIR = "_internal"
BACKUP_SUFFIX = ".old"
WAIT_TIMEOUT = 120          # 等主程序退出的上限（秒）
OTHER_INSTANCE_WAIT = 60    # 等"另一个实例"放开安装目录的上限（秒）
RENAME_RETRY_SECONDS = 20   # 改名被临时占用时重试多久（秒）
SYNCHRONIZE = 0x00100000

# 会被"重试"的 Windows 错误码：5=拒绝访问、32=文件被占用
_RETRY_WINERRORS = (5, 32)

# 根目录下这些文件要一起换（_internal 单独按目录整体换）
ROOT_FILES = (APP_EXE, "updater.exe")

log = logging.getLogger("updater")


# ---------- 等到主程序退出 ----------
def wait_for_exit(pid: int, timeout: float = WAIT_TIMEOUT) -> bool:
    """等 pid 进程结束。返回 True = 已退出（或进程不存在），False = 超时仍在运行。"""
    if not pid:
        return True
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if not handle:
        return True                      # 打不开 = 已经没了
    try:
        waited = kernel32.WaitForSingleObject(handle, int(timeout * 1000))
        return waited == 0               # 0 = 等到了；258 = 超时
    finally:
        kernel32.CloseHandle(handle)


# ---------- 校验新版本包 ----------
def validate_src(src: str) -> str:
    if not os.path.isdir(src):
        raise RuntimeError(f"新版目录不存在：{src}")
    if not os.path.isfile(os.path.join(src, APP_EXE)):
        raise RuntimeError(f"新版目录里没有 {APP_EXE}：{src}")
    if not os.path.isdir(os.path.join(src, INTERNAL_DIR)):
        raise RuntimeError(f"新版目录里没有 {INTERNAL_DIR}\\：{src}")
    return src


def free_space_mb(path: str) -> float:
    try:
        return shutil.disk_usage(path).free / 1024 / 1024
    except Exception:
        return -1.0


def dir_size_mb(path: str) -> float:
    """目录的递归大小（只用于日志，算不出就返回 -1）。"""
    try:
        total = 0
        for base, _dirs, files in os.walk(path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(base, f))
                except OSError:
                    pass
        return total / 1024 / 1024
    except Exception:
        return -1.0


# ---------- 替换 ----------
# ---------- 改名（带重试）----------
def rename_with_retry(src: str, dst: str, timeout: float = RENAME_RETRY_SECONDS) -> None:
    """同卷改名；被临时占用时重试。

    为什么必须重试：Windows 上只要有文件被"独占"打开（不允许共享删除），
    改名它所在的**目录**就会直接失败 [WinError 5/32] —— 杀软/索引器扫描刚解压出来的
    几千个文件时正是这种状态。实测（updater 日志）备份成功 3ms 后改名新目录就被拒，
    随后整个更新被迫回滚；而这类锁通常一两秒就释放，所以重试而不是直接失败。
    """
    deadline = time.time() + timeout
    delay = 0.2
    while True:
        try:
            os.rename(src, dst)
            return
        except OSError as e:
            winerror = getattr(e, "winerror", None)
            if winerror not in _RETRY_WINERRORS or time.time() >= deadline:
                raise
            log.warning("改名被占用，%.1fs 后重试：%s -> %s（%s）", delay, src, dst, e)
            time.sleep(delay)
            delay = min(delay * 1.5, 1.0)


def _remove_quietly(path: str):
    try:
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        elif os.path.exists(path):
            os.remove(path)
    except Exception as e:
        log.warning("删除 %s 失败（忽略）: %s", path, e)


def _remove_with_retry(path: str, attempts: int = 4, delay: float = 1.5) -> bool:
    """删除（目录递归）；被占用删不掉时多试几次，返回是否已删干净。

    和改名的锁是同一回事：杀软/索引器打开着目录里的文件时，rmtree 只能删掉能删的部分。
    rmtree 是幂等的 —— 下一轮把剩下的接着删，所以"部分删除 + 重试"最终能清干净，
    不会因为一个文件被占着就永远留下几百 MB 的旧版本备份。
    """
    for i in range(attempts):
        if not os.path.exists(path):
            return True
        _remove_quietly(path)
        if not os.path.exists(path):
            return True
        if i < attempts - 1:
            log.warning("删除 %s 没清干净，%.1fs 后重试（多半被杀软占用）", path, delay)
            time.sleep(delay)
    log.error("删除 %s 仍失败，留给下次更新时再清", path)
    return not os.path.exists(path)


def stop_legacy_adb_server(internal_dir: str):
    """停掉旧版本内置 adb 起的 adb server（升级收尾的前置清理）。

    为什么必须做：1.1.4 及之前的包在 _internal/tools/ 里带 adb.exe，虫师运行时
    它会拉起一个常驻的 adb server 进程（daemon，主程序退出也不死）。升级把
    _internal 改名 .old 后，server 进程还锁着 .old 里的 adb.exe，第 4 步的
    「删除 .old」永远失败，用户只能手动去任务管理器杀 adb 才能删掉
    （1.1.4 -> 1.1.6 升级实测反馈）。

    做法：用旧 adb 自己发 `kill-server`——server 按 5037 端口全局唯一，
    无论它是哪个 adb 起的都会被停掉；其他程序的 adb（如 Android Studio）
    不走这个 exe，且 server 停掉后它们会自动重连，无实质影响。
    """
    adb = os.path.join(internal_dir, "tools", "adb.exe")
    if not os.path.isfile(adb):
        return
    try:
        subprocess.run(
            [adb, "kill-server"],
            timeout=8,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=0x08000000,   # CREATE_NO_WINDOW
        )
        log.info("已停掉旧内置 adb 的 server（%s）", adb)
    except Exception as e:
        log.warning("停旧 adb server 失败（忽略，不影响更新本身）: %s", e)


def stop_legacy_weditor(install_dir: str):
    """停掉旧版本分发的 weditor 可视化服务（升级收尾的前置清理）。

    为什么必须做：1.1.6 起包里带 tools/weditor.exe（应用可视化的常驻服务）。
    主程序正常退出时 atexit 会 terminate 它，但残留路径依然存在——异常退出、
    terminate 后进程未及时退场、或旧版包的清理逻辑有缺口——它活着就锁住
    .old/tools/weditor.exe，「删除 .old」失败（1.1.6 -> 1.1.7 升级实测反馈，
    与 adb server 锁 .old 是同一类问题）。

    做法：先用 weditor 自带的 `--quit` 协议优雅请求退出（它会通知 17310 端口
    的服务停机），等一拍后 `taskkill /F /IM weditor.exe` 兜底强杀。weditor.exe
    是本包专属分发的名字，误伤面极小（真有用户自装的 weditor 被停，重启即可）。
    """
    exe = os.path.join(install_dir, "tools", "weditor.exe")
    if not os.path.isfile(exe):
        return
    # 1) 优雅退出：weditor 自带 --quit（通知本机服务停机）
    try:
        subprocess.run(
            [exe, "--quit"],
            timeout=6,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=0x08000000,
        )
        time.sleep(1.5)   # 给优雅退出留一拍
    except Exception as e:
        log.warning("weditor --quit 失败（继续用 taskkill 兜底）: %s", e)
    # 2) 兜底强杀：确认进程没了（或优雅失败）时强杀同名进程
    try:
        subprocess.run(
            ["taskkill", "/F", "/IM", "weditor.exe"],
            timeout=6,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=0x08000000,
        )
        log.info("已清理旧版 weditor 服务进程（%s）", exe)
    except Exception as e:
        log.warning("taskkill weditor.exe 失败（忽略，不影响更新本身）: %s", e)


def swap(src: str, dst: str):
    """把 src 里的新版本换进 dst。失败时抛异常，调用方负责回滚。

    顺序刻意设计成"先全部改名备份，再全部搬入"：
    改名是同卷瞬时操作，所以中途失败也能立刻改回来。
    """
    internal_src = os.path.join(src, INTERNAL_DIR)
    internal_dst = os.path.join(dst, INTERNAL_DIR)
    internal_bak = internal_dst + BACKUP_SUFFIX

    # 前置清理：旧版本内置 adb 的 server / weditor 服务进程可能还活着，
    # 锁着旧目录里的 adb.exe / weditor.exe —— 不停掉它们，
    # 下面的「清上次 .old」和「删除 .old」都会失败
    stop_legacy_adb_server(internal_dst)
    stop_legacy_adb_server(internal_bak)
    stop_legacy_weditor(dst)
    stop_legacy_weditor(internal_bak)

    # 上一次更新留下的 .old 先清掉（可能被杀软占着，多试几次；还不行就换个名字继续）
    if os.path.exists(internal_bak):
        _remove_with_retry(internal_bak, attempts=2, delay=0.5)
    if os.path.exists(internal_bak):
        internal_bak = f"{internal_dst}{BACKUP_SUFFIX}.{int(time.time())}"
        log.info("旧的 .old 删不掉，改用 %s", internal_bak)

    renamed = []            # [(备份路径, 原名路径)]，用于回滚
    moved_in = []           # 已经搬进 dst 的路径，回滚时要删掉

    try:
        # 1) 备份 _internal
        if os.path.isdir(internal_dst):
            rename_with_retry(internal_dst, internal_bak)
            renamed.append((internal_bak, internal_dst))
            log.info("已备份 %s -> %s", internal_dst, internal_bak)

        # 2) 备份根目录下要替换的文件
        for name in ROOT_FILES:
            cur = os.path.join(dst, name)
            if os.path.isfile(cur):
                bak = cur + BACKUP_SUFFIX
                _remove_quietly(bak)
                rename_with_retry(cur, bak)
                renamed.append((bak, cur))

        # 3) 搬入新版本（同卷改名，瞬时；被杀软扫着时靠重试扛过去）
        rename_with_retry(internal_src, internal_dst)
        moved_in.append(internal_dst)
        log.info("已换入 %s", internal_dst)

        for name in ROOT_FILES:
            new = os.path.join(src, name)
            if os.path.isfile(new):
                target = os.path.join(dst, name)
                rename_with_retry(new, target)
                moved_in.append(target)

        # 4) 成功：清理备份（旧版本那份有几百 MB，尽量删干净，删不掉留给下次）
        for bak, _ in renamed:
            _remove_with_retry(bak)
        return True

    except Exception:
        log.exception("替换失败，开始回滚")
        for path in moved_in:
            _remove_quietly(path)
        for bak, orig in reversed(renamed):
            try:
                if os.path.exists(bak):
                    rename_with_retry(bak, orig)     # 回滚也可能撞上锁，同样重试
                    log.info("已回滚 %s -> %s", bak, orig)
            except Exception as e:
                log.error("回滚 %s 失败: %s", bak, e)
        raise


def still_in_use(exe_path: str) -> bool:
    """这个 exe 是否正被某个进程运行。

    办法是"试着以写入方式打开它"：没运行的程序能打开，正在运行的 exe 打不开
    （Windows 不允许写运行中的可执行文件）。不需要 psutil / tasklist，也不依赖路径匹配。
    """
    try:
        with open(exe_path, "r+b"):
            return False
    except OSError:
        return True


def wait_until_unlocked(exe_path: str, timeout: float = OTHER_INSTANCE_WAIT) -> bool:
    """等"另一个实例"放锁（同一个安装目录被开了两次时会遇到）。返回是否已放开。"""
    if not still_in_use(exe_path):
        return True
    log.warning("%s 仍被占用（可能有另一个虫师还开着），最多等 %.0f 秒…", exe_path, timeout)
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(1.0)
        if not still_in_use(exe_path):
            log.info("占用已解除，继续更新")
            return True
    return False


def relaunch(exe_path: str, workdir: str):
    """启动新版。exe_path 是**可执行文件路径**（不是命令行）。

    刻意不做"按空格切分命令行"：安装目录带空格（如 D:\\Program Files\\...）时那样会散架。
    需要带参数启动的场景以后再单独加参数项。
    """
    if not exe_path:
        return
    try:
        subprocess.Popen([exe_path], cwd=workdir, close_fds=True)
        log.info("已启动新版：%s（cwd=%s）", exe_path, workdir)
    except Exception as e:
        log.error("启动新版失败：%s", e)


def _fail_box(message: str, log_path: str):
    """失败时用原生弹框告知（更新器没有界面，静默失败最坑人）。"""
    try:
        ctypes.windll.user32.MessageBoxW(
            None,
            f"虫师自动更新失败，已回滚到原版本，可以继续使用。\n\n{message}\n\n"
            f"详细信息见日志：\n{log_path}",
            "虫师更新失败", 0x10)      # MB_ICONERROR
    except Exception:
        pass


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="updater", add_help=True)
    parser.add_argument("--src", required=True, help="解压好的新版目录")
    parser.add_argument("--dst", required=True, help="安装目录")
    parser.add_argument("--pid", type=int, default=0, help="主程序 pid，等它退出")
    parser.add_argument("--restart", default="",
                        help="替换完成后要启动的 exe 路径（默认 <dst>\\虫师.exe）")
    parser.add_argument("--log", default="", help="日志文件路径")
    parser.add_argument("--silent", action="store_true",
                        help="失败时不弹原生提示框（无人值守/自动化用）")
    args = parser.parse_args(argv)

    log_path = args.log or os.path.join(os.environ.get("TEMP", "."), "chongshi_updater.log")
    logging.basicConfig(
        filename=log_path, filemode="a", level=logging.INFO, encoding="utf-8",
        format="%(asctime)s - %(levelname)s - %(message)s")
    log.info("=" * 60)
    log.info("updater 启动：src=%s dst=%s pid=%s restart=%r",
             args.src, args.dst, args.pid, args.restart)

    src = args.src
    dst = os.path.abspath(args.dst)

    try:
        validate_src(src)
        if not os.path.isdir(dst):
            raise RuntimeError(f"安装目录不存在：{dst}")

        log.info("等待主程序退出（pid=%s）…", args.pid)
        if not wait_for_exit(args.pid):
            raise RuntimeError(f"等待主程序退出超时（{WAIT_TIMEOUT} 秒），已放弃更新")

        # 除了我们在等的那一个，可能还有"另一个虫师"开着（同一个安装目录被启动两次）。
        # 它照样锁着 _internal，会让后面的改名失败 —— 提前等它放开，并给出人话提示，
        # 而不是让人看到一串 [WinError 5]。
        if not wait_until_unlocked(os.path.join(dst, APP_EXE)):
            raise RuntimeError(
                f"安装目录里的 {APP_EXE} 还在被占用：\n{dst}\n\n"
                f"多半是还开着另一个虫师窗口（或别的程序正用着这个目录）。\n"
                f"请把它们全部关闭，然后重新点一次「检查更新」→「下载并更新」。")

        need_mb = dir_size_mb(os.path.join(src, INTERNAL_DIR))
        log.info("安装目录可用空间 %.0f MB（新版本 _internal 约 %.0f MB）",
                 free_space_mb(dst), need_mb)

        swap(src, dst)
        log.info("替换完成")

    except Exception as e:
        log.exception("更新失败")
        # 失败就把解压好的那份删掉：否则应用会把这份"看着完整、其实这轮没装成"的暂存
        # 当成"已下载好、可立即重启更新"，下次点按钮又拿它来装一遍同样的失败。
        # 重新下载解压的代价很小（本机实测 255MB 包约 10 秒）。
        if not _remove_with_retry(src, attempts=3, delay=1.0):
            log.warning("暂存目录没能删干净，应用可能仍把它当成可用的更新包")
        if not args.silent:
            _fail_box(str(e), log_path)
        return 1

    # 收尾：删掉暂存目录里剩下的东西（新版本已经被改名搬走了）
    _remove_quietly(src)

    relaunch(args.restart or os.path.join(dst, APP_EXE), dst)
    log.info("updater 结束")
    return 0


if __name__ == "__main__":
    sys.exit(main())
