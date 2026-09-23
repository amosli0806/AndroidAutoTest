"""临时：验证「下载 -> 校验 -> 解压 -> 交更新器」整条链路（跑完即删）

覆盖：
  * 正常流程（真 zip + 本地桩服务）-> 暂存目录里是一份完整新版本
  * zip-slip 防护（包内塞 ../evil.txt 与绝对路径条目，必须不落到暂存目录外）
  * sha256 校验（digest 不符必须报错且不留下半成品）
  * 包不完整（缺 _internal）-> validate 拦下
  * 版本号当路径用的安全性（"../../evil" 不能逃出暂存根目录）
  * pending_staged() / cleanup_staging(keep=)
  * 拿真实 updater.exe 在合成安装目录上跑完替换（端到端）
"""
import hashlib
import http.server
import io
import json
import os
import shutil
import socketserver
import subprocess
import sys
import threading
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

# 桩版本 = 当前版本 patch+1，**永远比本地新**。
# 为什么必须动态：早期写死 v1.1.4，等项目真发 1.1.4 时，check_for_update 会把桩
# 判成"已是最新"，自测第一项就挂（v1.1.4 发版 CI 实测踩坑）。
from utils.version import APP_VERSION as _CUR
_parts = _CUR.split(".")
NEXT_VER = ".".join(_parts[:-1] + [str(int(_parts[-1]) + 1)])

PORT = 8741
UPDATER_EXE = os.path.join(ROOT, "dist_updater", "updater.exe")
ok = True


def check(name, cond, detail=""):
    global ok
    ok = ok and bool(cond)
    print(f"  [{'OK ' if cond else 'FAIL'}] {name}{('  ' + detail) if detail else ''}")


def build_package(path, version=None, with_evil=True, with_internal=True):
    """造一个和 CI 产出同布局的更新包。版本号缺省 = 桩版本（永远比本地新）。"""
    version = version or NEXT_VER
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("虫师.exe", f"NEW-EXE-{version}")
        if with_internal:
            z.writestr("_internal/python313.dll", f"NEW-DLL-{version}")
            z.writestr("_internal/_tk_data/tk.tcl", "tk")
        z.writestr("updater.exe", "NEW-UPDATER")
        if with_evil:
            z.writestr("../escaped.txt", "should never be written")      # zip-slip
            z.writestr("/abs_escaped.txt", "should never be written")     # 绝对路径
            z.writestr("C:/drive_escaped.txt", "should never be written")  # 盘符
    return path


STATE = {"pkg": b"", "digest": ""}


def serve_zip(zip_path, digest):
    """一个常驻的本地桩服务，内容通过 STATE 切换。

    （踩过：每轮新起一个 TCPServer 而旧的不关，Windows 允许同端口重复绑定，
    请求会继续打到旧服务上 —— 负例因此全部失效，看起来像"代码没报错"。）
    """
    STATE["pkg"] = open(zip_path, "rb").read()
    STATE["digest"] = digest

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/pkg":
                raw = STATE["pkg"]
            else:
                # 桩版本必须**永远比本地新**：早期写死 v1.1.4，等项目发到 1.1.4 时
                # check_for_update 会判成"已是最新"，第一项就挂（v1.1.4 CI 实测踩坑）。
                raw = json.dumps({
                    "tag_name": f"v{NEXT_VER}", "body": "test",
                    "published_at": "2026-09-23T00:00:00Z",
                    "html_url": f"https://example.invalid/r/v{NEXT_VER}",
                    "assets": [{"name": f"chongshi-{NEXT_VER}-win64.zip",
                                "size": len(STATE["pkg"]),
                                "browser_download_url": f"http://127.0.0.1:{PORT}/pkg",
                                "digest": f"sha256:{STATE['digest']}"}],
                }).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *a):
            pass

    socketserver.TCPServer.allow_reuse_address = True
    srv = socketserver.TCPServer(("127.0.0.1", PORT), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main():
    from services import update_service as us
    from utils.app_paths import get_app_dir

    tmp = tempfile.mkdtemp(prefix="update_pipeline_test_")
    zip_path = os.path.join(tmp, "pkg.zip")
    build_package(zip_path)
    digest = hashlib.sha256(open(zip_path, "rb").read()).hexdigest()
    serve_zip(zip_path, digest)

    os.environ[us.UPDATE_API_OVERRIDE_ENV] = f"http://127.0.0.1:{PORT}/latest"
    res = us.check_for_update()
    check("本地桩服务能查到新版本", res.status == "update_available", res.status)
    info = res.info
    check("拿到的期望摘要与实际一致", info.sha256 == digest)

    staged_root = us.staging_root()
    print(f"  暂存根目录：{staged_root}（安装目录下，同卷改名用）")
    shutil.rmtree(staged_root, ignore_errors=True)

    progress_calls, stages = [], []
    staging = us.install_prepare(
        info,
        progress=lambda c, t: progress_calls.append((c, t)),
        on_stage=lambda s: stages.append(s))
    check("阶段回调有序（download -> extract）", stages == ["download", "extract"], str(stages))
    check("进度回调被驱动", len(progress_calls) > 0, f"{len(progress_calls)} 次")
    check("暂存目录就位", os.path.isdir(staging), staging)
    check("新版本文件齐全",
          all(os.path.exists(os.path.join(staging, n))
              for n in ("虫师.exe", "_internal", "updater.exe")))
    check("内容是这一版的", open(os.path.join(staging, "虫师.exe"), encoding="utf-8").read()
          == f"NEW-EXE-{NEXT_VER}")

    # zip-slip：恶意条目必须落在暂存目录**内**、绝不能逃到外面
    escaped = [os.path.join(staging, n) for n in ("../escaped.txt", "abs_escaped.txt",
                                                  "drive_escaped.txt")]
    check("zip-slip 条目没有逃到暂存目录外", not any(os.path.exists(p) for p in escaped))
    check("也没有逃到安装目录/暂存根目录外",
          not any(os.path.exists(os.path.join(d, "escaped.txt"))
                  for d in (get_app_dir(), staged_root)))
    check("三个可疑条目都被拒绝（连暂存目录内都不该出现）",
          not any(os.path.exists(p) for p in
                  [os.path.join(staging, n) for n in ("escaped.txt", "abs_escaped.txt",
                                                      "drive_escaped.txt")]))
    check("TEMP 里的压缩包已清理",
          not os.path.exists(os.path.join(tempfile.gettempdir(), "chongshi_update",
                                          f"chongshi-{NEXT_VER}-win64.zip")))

    # 摘要不符必须拦住，且不留半成品
    build_package(zip_path)
    serve_zip(zip_path, "0" * 64)
    res_bad = us.check_for_update()
    err = ""
    try:
        us.install_prepare(res_bad.info, on_stage=lambda s: None)
    except Exception as e:
        err = str(e)
    check("sha256 不符时报错", "校验失败" in err, err[:60])

    # 包不完整
    build_package(zip_path, with_internal=False)
    digest2 = hashlib.sha256(open(zip_path, "rb").read()).hexdigest()
    serve_zip(zip_path, digest2)
    res_inc = us.check_for_update()
    err2 = ""
    try:
        us.install_prepare(res_inc.info, on_stage=lambda s: None)
    except Exception as e:
        err2 = str(e)
    check("缺 _internal 被拦下", "_internal" in err2, err2[:60])
    check("失败后暂存目录没留下垃圾", not os.path.isdir(us.staging_dir(NEXT_VER)))

    # 版本号当路径的安全
    check("恶意版本号不会逃出暂存根",
          os.path.abspath(us.staging_dir("../../evil")).startswith(
              os.path.abspath(staged_root) + os.sep),
          us.staging_dir("../../evil"))

    # ---- 负例跑完后重新准备一份好的：pending_staged / cleanup / 端到端都用它 ----
    # （注意：上面"包不完整"那一例按设计会把它正在用的暂存目录删掉，所以不能复用旧路径）
    build_package(zip_path)
    digest3 = hashlib.sha256(open(zip_path, "rb").read()).hexdigest()
    serve_zip(zip_path, digest3)
    staging = us.install_prepare(us.check_for_update().info, on_stage=lambda s: None)
    check("重新准备好一份可用的暂存", os.path.isdir(staging), staging)
    check("pending_staged() 认得它", os.path.abspath(us.pending_staged()) == os.path.abspath(staging))

    # cleanup：保留正在等重启的那一份
    keep = staging
    other = os.path.join(staged_root, "9.9.9")
    os.makedirs(other, exist_ok=True)
    open(os.path.join(other, "junk.txt"), "w").write("x")
    removed = us.cleanup_staging(keep=keep)
    check("cleanup 删掉了别的残留、保住了 keep",
          removed >= 1 and os.path.isdir(keep) and not os.path.isdir(other), f"删了 {removed} 项")

    # ---- 端到端：拿真 updater.exe 在合成安装目录上跑一次替换 ----
    if os.path.isfile(UPDATER_EXE):
        fake = os.path.join(ROOT, "_tmp_install_for_test")   # 放同卷，改名才是瞬时的
        shutil.rmtree(fake, ignore_errors=True)
        os.makedirs(os.path.join(fake, "_internal"))
        os.makedirs(os.path.join(fake, "data"))
        open(os.path.join(fake, "虫师.exe"), "w", encoding="utf-8").write("OLD-EXE")
        open(os.path.join(fake, "_internal", "python313.dll"), "w", encoding="utf-8").write("OLD-DLL")
        open(os.path.join(fake, "data", "project_data.json"), "w", encoding="utf-8").write("USER-DATA")
        shutil.copy(UPDATER_EXE, os.path.join(staging, "updater.exe"))
        log = os.path.join(tmp, "updater.log")
        p = subprocess.run([os.path.join(staging, "updater.exe"),
                            "--src", staging, "--dst", fake,
                            "--log", log, "--silent"],
                           capture_output=True, text=True, timeout=180)
        check("updater 替换成功（返回 0）", p.returncode == 0, f"rc={p.returncode}")
        check("安装目录换成了新版",
              open(os.path.join(fake, "虫师.exe"), encoding="utf-8").read() == f"NEW-EXE-{NEXT_VER}"
              and open(os.path.join(fake, "_internal", "python313.dll"), encoding="utf-8").read()
              == f"NEW-DLL-{NEXT_VER}")
        check("data/ 用户数据未被触碰",
              open(os.path.join(fake, "data", "project_data.json"), encoding="utf-8").read()
              == "USER-DATA")
        shutil.rmtree(fake, ignore_errors=True)
    else:
        check("（跳过端到端）找到 updater.exe", False, UPDATER_EXE)

    shutil.rmtree(staged_root, ignore_errors=True)
    shutil.rmtree(tmp, ignore_errors=True)
    print("全部通过" if ok else "有失败项")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

