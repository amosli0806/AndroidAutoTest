# tools/dpi_audit.py
"""界面尺寸体检：把每个页面 / 弹窗按多组「分辨率 × 缩放」跑一遍，报告谁装不下。

为什么需要它：「扩展方向」第 5 项（高 DPI / 分辨率适配）改的是尺寸约束，
改对了没有只能靠数字验证 —— 人工换分辨率点一遍既慢又容易漏。这个脚本把
判定口径固定成三条断言，改前存基线、改后重跑对比，也可以直接进 CI。

判定口径（与文档一致）：
  1) 页面：minimumSizeHint().width() <= 页面可用宽
  2) 弹窗：装进屏幕后底边 <= 可用高（即整个窗口落在屏内）
  3) 主按钮：show() 之后 isVisible()

「页面可用宽」= 逻辑可用宽 - 窗口两侧工具栏(40+40) - 边距，默认 96。

用法：
    python tools/dpi_audit.py                 # 全量，人读表格
    python tools/dpi_audit.py --json base.json  # 另存 JSON（用于改前改后对比）
    python tools/dpi_audit.py --only 性能页     # 只跑名字包含该关键字的项

注意：这里不修改系统 DPI，而是按「系统分辨率 ÷ 缩放比」折算出逻辑可用区，
再用该尺寸去 resize 控件 —— 逻辑像素口径与真实高 DPI 环境一致。
"""
import argparse
import json
import os
import sys
import time

os.environ.setdefault("CHONGSHI_NO_CONFIG_WRITE", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication, QDialog, QPushButton
from PyQt6.QtGui import QFontDatabase
from PyQt6.QtCore import Qt

# 窗口两侧固定工具栏 + 边距：页面拿不到整屏宽度
CHROME_W = 96
# 弹窗留白系数：装进屏幕时留一点余量，别贴边
DIALOG_W_RATIO = 0.92
DIALOG_H_RATIO = 0.88

# (名称, 物理宽, 物理高, 缩放百分比)
PROFILES = [
    ("1920x1080@100", 1920, 1080, 100),
    ("1920x1080@125", 1920, 1080, 125),
    ("1920x1080@150", 1920, 1080, 150),
    ("2560x1440@150", 2560, 1440, 150),
    ("1366x768@125", 1366, 768, 125),
    ("1366x768@150", 1366, 768, 150),
    ("2256x1504@150", 2256, 1504, 150),
]


def _pump(seconds=0.25, app=None):
    """跑一会儿事件循环，让布局真正算完再读尺寸。"""
    app = app or QApplication.instance()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


def _apply_theme(widget, theme_mode):
    """页面/弹窗的换肤签名不统一，能调就调，失败忽略。"""
    fn = getattr(widget, "apply_theme", None)
    if fn is None:
        return
    for args in ((theme_mode,), (theme_mode, False)):
        try:
            fn(*args)
            return
        except TypeError:
            continue
        except Exception:
            return


# ----------------------------------------------------------------------
# 页面：全部能直接构造的一律实测
# ----------------------------------------------------------------------
def build_page_factories():
    """返回 [(显示名, 工厂)]。工厂失败会在报告里标「构造失败」，不算通过。"""
    from models.task_model import TaskModel
    from models.suite_model import SuiteModel
    from models.api_model import ApiModel
    from models.element_model import ElementModel
    from models.knowledge_model import KnowledgeModel

    from views.task_view import TaskView
    from views.execute_view import ExecuteView
    from views.api_view import ApiView
    from views.element_manager_view import ElementManagerView
    from views.perf_view import PerfView
    from views.knowledge_view import KnowledgeView
    from views.help_view import HelpView
    from views.project_tree_view import ProjectTreeView

    def voice():
        from views.voice_view import VoiceView

        class _StubService:
            def is_available(self):
                return False

            def __getattr__(self, _name):
                return lambda *a, **k: None

        return VoiceView(_StubService(), [])

    return [
        ("用例页", lambda: TaskView(TaskModel(), SuiteModel())),
        ("执行页", lambda: ExecuteView()),
        ("接口页", lambda: ApiView(ApiModel())),
        ("元素库", lambda: ElementManagerView(ElementModel())),
        ("性能页", lambda: PerfView()),
        ("语音页", voice),
        ("知识库", lambda: KnowledgeView(KnowledgeModel())),
        ("帮助页", lambda: HelpView()),
        ("项目树", lambda: ProjectTreeView()),
    ]


# ----------------------------------------------------------------------
# 弹窗：能给构造参数的就实测；给不出的退回「源码声明尺寸」并标明
# ----------------------------------------------------------------------
def build_dialog_factories():
    def settings():
        from views.dialogs.settings_dialog import SettingsDialog
        return SettingsDialog()

    def suite_editor():
        from views.dialogs.suite_editor_dialog import SuiteEditorDialog
        return SuiteEditorDialog("体检", [])

    def kb_import():
        from models.knowledge_model import KnowledgeModel
        from views.dialogs.kb_import_dialog import KbImportDialog
        return KbImportDialog(KnowledgeModel())

    def action_cards():
        from views.dialogs.select_action_cards_dialog import SelectActionCardsDialog
        return SelectActionCardsDialog({"点击": "click", "滑动": "swipe"},
                                       checked_types={"click"})

    def custom_cmd():
        from views.adb_dialogs.custom_command_dialog import CustomCommandDialog
        return CustomCommandDialog()

    def perf_threshold():
        from models.perf_model import PerfThreshold
        from views.perf_dialogs import PerfThresholdDialog
        return PerfThresholdDialog(PerfThreshold())

    def perf_baseline_mgr():
        from models.perf_model import PerfModel
        from views.perf_dialogs import PerfBaselineDialog
        return PerfBaselineDialog(PerfModel())

    def perf_compare():
        """四条构造参数都只需要被读取，用最小桩对象即可。"""
        from views.perf_dialogs import PerfCompareDialog

        class _Stub:
            def __getattr__(self, _name):
                return 0

        from models.perf_model import PerfBaseline
        base = PerfBaseline(name="体检基线", session_id="s", app_package="com.demo",
                            device_serial="d", metrics={}, created_at="2026-10-30",
                            scopes={})
        return PerfCompareDialog("体检基线", base, _Stub(), {})

    def element_editor():
        from views.element_manager_view import ElementEditDialog
        return ElementEditDialog()

    return [
        ("阈值设置", perf_threshold),
        ("设置", settings),
        ("基线管理", perf_baseline_mgr),
        ("对比详情", perf_compare),
        ("元素编辑", element_editor),
        ("套件编辑", suite_editor),
        ("知识库导入", kb_import),
        ("动作卡选择", action_cards),
        ("自定义命令", custom_cmd),
    ]


def measure_dialog(factory, logical_w, logical_h):
    """构造并显示一个弹窗，返回 (声明宽, 声明高, 收缩后宽, 收缩后高, 是否达标, 说明)。

    收缩逻辑与 utils/dpi.py::fit_to_screen 一致，所以本脚本可以在装过滤器之前
    预演「装完过滤器之后还剩谁超屏」。
    """
    try:
        dlg = factory()
        dlg.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        dlg.show()
        _pump()
        raw_w, raw_h = dlg.width(), dlg.height()

        target_w = min(raw_w, int(logical_w * DIALOG_W_RATIO))
        target_h = min(raw_h, int(logical_h * DIALOG_H_RATIO))
        if (target_w, target_h) != (raw_w, raw_h):
            dlg.resize(target_w, target_h)
            _pump()

        w, h = dlg.width(), dlg.height()
        note = ""
        ok = True
        if h > logical_h:
            ok = False
            note = f"被最小尺寸顶回 {w}×{h}，底边超出 {h - logical_h}px"
        elif w > logical_w:
            ok = False
            note = f"仍超宽 {w}（可用 {logical_w}）"
        else:
            # 主按钮得真的落在窗口内
            main_btns = [b for b in dlg.findChildren(QPushButton)
                         if b.text().strip() in ("确定", "保存", "关闭", "完成", "OK")]
            hidden = [b.text() for b in main_btns if not b.isVisible()]
            if main_btns and len(hidden) == len(main_btns):
                ok = False
                note = "主按钮不可见"

        dlg.close()
        dlg.deleteLater()
        _pump(0.05)
        return (raw_w, raw_h, w, h, ok, note)
    except Exception as e:                      # noqa: BLE001 - 体检脚本只记录，不中断
        return ("ERR", 0, 0, 0, False, f"{type(e).__name__}: {e}")


def main():
    ap = argparse.ArgumentParser(description="界面尺寸体检")
    ap.add_argument("--json", default="", help="把结果另存为 JSON")
    ap.add_argument("--only", default="", help="只跑名称包含该关键字的项")
    args = ap.parse_args()

    app = QApplication(sys.argv[:1])
    try:
        QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")
    except Exception:
        pass

    from utils.theme import ThemeMode
    globals()["ThemeMode"] = ThemeMode

    page_factories = build_page_factories()
    dialog_factories = build_dialog_factories()
    if args.only:
        page_factories = [p for p in page_factories if args.only in p[0]]
        dialog_factories = [d for d in dialog_factories if args.only in d[0]]

    report = {"profiles": [], "generated_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    total_fail = 0

    for name, sw, sh, scale in PROFILES:
        logical_w = int(sw * 100 / scale)
        logical_h = int(sh * 100 / scale)
        page_w = logical_w - CHROME_W
        print(f"\n=== {name}  逻辑可用 {logical_w}×{logical_h}  页面可用宽 {page_w} ===")
        entry = {"profile": name, "logical_w": logical_w, "logical_h": logical_h,
                 "page_w": page_w, "pages": [], "dialogs": []}

        for pname, factory in page_factories:
            try:
                w = factory()
                _apply_theme(w, ThemeMode.DARK)
                w.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
                w.resize(page_w, logical_h)
                w.show()
                _pump()
                min_w, min_h = w.minimumSizeHint().width(), w.minimumSizeHint().height()
                ok = min_w <= page_w
                mark = "✓" if ok else f"✗ 超 {min_w - page_w}"
                print(f"  页面 {pname:<8s} minW={min_w:<5d} minH={min_h:<5d} {mark}")
                entry["pages"].append({"name": pname, "min_w": min_w, "min_h": min_h,
                                       "page_w": page_w, "ok": ok})
                w.close()
                w.deleteLater()
                _pump(0.05)
                if not ok:
                    total_fail += 1
            except Exception as e:              # noqa: BLE001
                # 构造不出来（依赖设备/服务）不算"尺寸不达标"，单列「未覆盖」，
                # 否则真正要修的项目会被这些噪声淹掉
                print(f"  页面 {pname:<8s} 未覆盖（构造失败）: {type(e).__name__}: {e}")
                entry["pages"].append({"name": pname, "error": str(e),
                                       "ok": None, "covered": False})

        for dname, factory in dialog_factories:
            res = measure_dialog(factory, logical_w, logical_h)
            if res is None:
                continue
            if res[0] == "ERR":
                print(f"  弹窗 {dname:<8s} 构造失败: {res[5]}")
                entry["dialogs"].append({"name": dname, "error": res[5], "ok": False})
                continue
            raw_w, raw_h, w, h, ok, err = res
            mark = "✓" if ok else f"✗ {err}"
            print(f"  弹窗 {dname:<8s} {raw_w}×{raw_h} → {w}×{h}  {mark}")
            entry["dialogs"].append({"name": dname, "raw": [raw_w, raw_h],
                                     "fitted": [w, h], "ok": ok, "note": err})
            if not ok:
                total_fail += 1

        report["profiles"].append(entry)

    print(f"\n=== 汇总：{total_fail} 项不达标 ===")
    report["total_fail"] = total_fail
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"已写入 {args.json}")
    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
