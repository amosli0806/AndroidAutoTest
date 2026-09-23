"""更新器冒烟测试：替换 / 回滚。用法： python updater_selftest.py

不碰真实安装目录 —— 全部在 %TEMP%\\chongshi_updater_test 下造"新版本目录"和
"安装目录"两份合成数据来验证。CI 每次发版前都会先跑它（这块写错会毁掉用户安装）。
"""
import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
# 默认取仓库里的默认产物路径；CI 或换目录构建时用环境变量覆盖
UPDATER = os.environ.get(
    "UPDATER_EXE", os.path.join(_REPO_ROOT, "dist_updater", "updater.exe"))
ROOT = os.path.join(tempfile.gettempdir(), "chongshi_updater_test")
ok = True


def check(name, cond, detail=""):
    global ok
    ok = ok and bool(cond)
    print(f"  [{'OK ' if cond else 'FAIL'}] {name}{('  ' + detail) if detail else ''}")


def require_updater():
    if not os.path.isfile(UPDATER):
        print(f"找不到更新器：{UPDATER}\n"
              f"先执行： pyinstaller updater.spec --noconfirm "
              f"--distpath dist_updater --workpath build_updater")
        sys.exit(2)


def make_tree(base, exe_text, dll_text, extra_internal=None):
    os.makedirs(os.path.join(base, "_internal"), exist_ok=True)
    os.makedirs(os.path.join(base, "data"), exist_ok=True)
    with open(os.path.join(base, "虫师.exe"), "w", encoding="utf-8") as f:
        f.write(exe_text)
    with open(os.path.join(base, "_internal", "python313.dll"), "w", encoding="utf-8") as f:
        f.write(dll_text)
    with open(os.path.join(base, "data", "project_data.json"), "w", encoding="utf-8") as f:
        f.write('{"用户数据":"必须保留"}')
    for name in (extra_internal or []):
        with open(os.path.join(base, "_internal", name), "w", encoding="utf-8") as f:
            f.write(name)


def read(path, encoding="utf-8"):
    with open(path, "r", encoding=encoding, errors="replace") as f:
        return f.read()


def run_updater(src, dst, pid=0, restart="", log=""):
    cmd = [UPDATER, "--src", src, "--dst", dst, "--pid", str(pid),
           "--restart", restart, "--log", log, "--silent"]
    t0 = time.perf_counter()
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    return p.returncode, time.perf_counter() - t0, p


def case_happy():
    print("用例 1：正常替换（含等待真实进程退出 + 重启命令 + 清理）")
    base = os.path.join(ROOT, "happy")
    shutil.rmtree(base, ignore_errors=True)
    inst, staged = os.path.join(base, "install"), os.path.join(base, "staged")
    make_tree(inst, "OLD-EXE", "OLD-DLL", ["old_only.txt"])
    make_tree(staged, "NEW-EXE", "NEW-DLL", ["new_only.txt"])
    shutil.copy(UPDATER, os.path.join(staged, "updater.exe"))

    # 指向"替换之后才存在"的真实 exe：暂存目录里的 updater.exe 会被搬进安装目录
    restart_target = os.path.join(inst, "updater.exe")
    sleeper = subprocess.Popen(
        ["powershell", "-NoProfile", "-Command", "Start-Sleep -Seconds 3"])
    log = os.path.join(base, "updater.log")
    rc, elapsed, _ = run_updater(staged, inst, pid=sleeper.pid,
                                 restart=restart_target, log=log)
    check("返回码 0", rc == 0, f"rc={rc}")
    check("确实等主程序退出了（耗时 >= 3s）", elapsed >= 3.0, f"耗时={elapsed:.1f}s")
    check("_internal 已换成新版", read(os.path.join(inst, "_internal", "python313.dll")) == "NEW-DLL")
    check("新版独有文件已就位", os.path.exists(os.path.join(inst, "_internal", "new_only.txt")))
    check("旧版独有文件已消失", not os.path.exists(os.path.join(inst, "_internal", "old_only.txt")))
    check("虫师.exe 已换成新版", read(os.path.join(inst, "虫师.exe")) == "NEW-EXE")
    check("updater.exe 也换进来了", os.path.exists(os.path.join(inst, "updater.exe")))
    check("data/ 用户数据未被动过",
          read(os.path.join(inst, "data", "project_data.json")) == '{"用户数据":"必须保留"}')
    log_text = read(log)
    check("日志记录了启动新版", "已启动新版" in log_text)
    check("备份 .old 已清理", not os.path.exists(os.path.join(inst, "_internal.old")))
    check("暂存目录已清理", not os.path.isdir(staged))


def case_rollback():
    print("用例 2：换到一半失败 -> 必须完整回滚")
    base = os.path.join(ROOT, "rollback")
    shutil.rmtree(base, ignore_errors=True)
    inst, staged = os.path.join(base, "install"), os.path.join(base, "staged")
    make_tree(inst, "OLD-EXE", "OLD-DLL", ["old_only.txt"])
    make_tree(staged, "NEW-EXE", "NEW-DLL", ["new_only.txt"])
    shutil.copy(UPDATER, os.path.join(staged, "updater.exe"))
    # 在目标位置放一个同名"目录"，让搬入 updater.exe 那一步必然失败
    os.makedirs(os.path.join(inst, "updater.exe"), exist_ok=True)
    with open(os.path.join(inst, "updater.exe", "blocker.txt"), "w") as f:
        f.write("x")

    log = os.path.join(base, "updater.log")
    rc, _, _ = run_updater(staged, inst, log=log)
    check("返回码 1（失败）", rc == 1, f"rc={rc}")
    check("_internal 回滚成旧版",
          read(os.path.join(inst, "_internal", "python313.dll")) == "OLD-DLL")
    check("旧版独有文件回来了", os.path.exists(os.path.join(inst, "_internal", "old_only.txt")))
    check("新版文件没有残留", not os.path.exists(os.path.join(inst, "_internal", "new_only.txt")))
    check("虫师.exe 回滚成旧版", read(os.path.join(inst, "虫师.exe")) == "OLD-EXE")
    check("data/ 用户数据未被动过",
          read(os.path.join(inst, "data", "project_data.json")) == '{"用户数据":"必须保留"}')
    check("日志里记录了回滚", "回滚" in read(log))
    check("失败后把暂存目录清掉了（免得被当成「已下好可重启」）", not os.path.isdir(staged))


def case_transient_lock():
    """线上真实故障的复现：改名新目录时被临时锁住（[WinError 5]）。

    杀软/索引器扫描刚解压出来的几千个文件时会以"不允许共享删除"的方式打开它们，
    此时改名这些文件所在的目录会直接被拒 —— updater 日志里那次就是备份成功 3ms 后
    卡在这一步、随后整体回滚。这里用同样的打开方式把锁造出来，1.5 秒后释放，
    断言 updater 靠重试把这次更新做成了。
    """
    print("用例 6：新目录被临时锁住（模拟杀软扫描）-> 重试后仍要成功")
    base = os.path.join(ROOT, "transient_lock")
    shutil.rmtree(base, ignore_errors=True)
    inst, staged = os.path.join(base, "install"), os.path.join(base, "staged")
    make_tree(inst, "OLD-EXE", "OLD-DLL", ["old_only.txt"])
    make_tree(staged, "NEW-EXE", "NEW-DLL", ["new_only.txt"])
    shutil.copy(UPDATER, os.path.join(staged, "updater.exe"))

    held = os.path.join(staged, "_internal", "python313.dll")
    GENERIC_READ, OPEN_EXISTING = 0x80000000, 3
    handle = ctypes.windll.kernel32.CreateFileW(held, GENERIC_READ, 0, None, OPEN_EXISTING, 0, None)
    check("成功造出独占锁", handle != -1, hex(handle) if handle != -1 else "CreateFile 失败")

    def _release():
        time.sleep(1.5)
        ctypes.windll.kernel32.CloseHandle(handle)
    threading.Thread(target=_release, daemon=True).start()

    log = os.path.join(base, "updater.log")
    rc, elapsed, _ = run_updater(staged, inst, log=log)
    check("返回码 0（重试后成功）", rc == 0, f"rc={rc}")
    check("确实等到了锁释放（耗时 >= 1.5s）", elapsed >= 1.5, f"耗时={elapsed:.1f}s")
    check("日志里有重试记录", "重试" in read(log))
    check("_internal 换成新版", read(os.path.join(inst, "_internal", "python313.dll")) == "NEW-DLL")
    check("旧版独有文件已消失", not os.path.exists(os.path.join(inst, "_internal", "old_only.txt")))
    check("没有留下 .old 残留", not os.path.exists(os.path.join(inst, "_internal.old")))


def case_bad_src():
    print("用例 3：新版包不完整 -> 一个文件都不许动")
    base = os.path.join(ROOT, "badsrc")
    shutil.rmtree(base, ignore_errors=True)
    inst, staged = os.path.join(base, "install"), os.path.join(base, "staged")
    make_tree(inst, "OLD-EXE", "OLD-DLL")
    os.makedirs(staged, exist_ok=True)
    with open(os.path.join(staged, "虫师.exe"), "w") as f:
        f.write("NEW-EXE")          # 少了 _internal
    log = os.path.join(base, "updater.log")
    rc, _, _ = run_updater(staged, inst, log=log)
    check("返回码 1", rc == 1, f"rc={rc}")
    check("安装目录原封不动", read(os.path.join(inst, "_internal", "python313.dll")) == "OLD-DLL"
          and read(os.path.join(inst, "虫师.exe")) == "OLD-EXE")
    check("没有产生 .old 备份", not os.path.exists(os.path.join(inst, "_internal.old")))


def case_no_pid():
    print("用例 4：pid 已不存在 -> 不应等待，立刻开始")
    base = os.path.join(ROOT, "nopid")
    shutil.rmtree(base, ignore_errors=True)
    inst, staged = os.path.join(base, "install"), os.path.join(base, "staged")
    make_tree(inst, "OLD-EXE", "OLD-DLL")
    make_tree(staged, "NEW-EXE", "NEW-DLL")
    shutil.copy(UPDATER, os.path.join(staged, "updater.exe"))
    log = os.path.join(base, "updater.log")
    rc, elapsed, _ = run_updater(staged, inst, pid=999999, log=log)
    check("返回码 0", rc == 0, f"rc={rc}")
    check("没有白等（耗时 < 10s）", elapsed < 10, f"耗时={elapsed:.1f}s")
    check("替换成功", read(os.path.join(inst, "_internal", "python313.dll")) == "NEW-DLL")


def case_bad_restart():
    print("用例 5：--restart 指向不存在的文件 -> 更新本身仍要成功，只是记一条错误")
    base = os.path.join(ROOT, "badrestart")
    shutil.rmtree(base, ignore_errors=True)
    inst, staged = os.path.join(base, "install"), os.path.join(base, "staged")
    make_tree(inst, "OLD-EXE", "OLD-DLL")
    make_tree(staged, "NEW-EXE", "NEW-DLL")
    shutil.copy(UPDATER, os.path.join(staged, "updater.exe"))
    log = os.path.join(base, "updater.log")
    rc, _, _ = run_updater(staged, inst, restart=os.path.join(base, "nope.exe"), log=log)
    check("返回码 0（替换成功了）", rc == 0, f"rc={rc}")
    check("替换确实生效", read(os.path.join(inst, "_internal", "python313.dll")) == "NEW-DLL")
    check("日志里有启动失败的记录", "启动新版失败" in read(log))


if __name__ == "__main__":
    require_updater()
    print(f"被测更新器：{UPDATER}")
    print(f"测试根目录：{ROOT}")
    for fn in (case_happy, case_rollback, case_bad_src, case_no_pid, case_bad_restart,
               case_transient_lock):
        fn()
    print("全部通过" if ok else "有失败项")
    sys.exit(0 if ok else 1)
