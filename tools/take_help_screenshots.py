# tools/take_help_screenshots.py
"""
一次性脚本：启动"虫师"应用，遍历各功能页面，把帮助中心缺失的截图
批量抓取并保存到 resources/images/help/。

用法：
    .venv/Scripts/python.exe tools/take_help_screenshots.py

说明：
- 无 Android 真机也能跑（设备相关截图会显示"未检测到设备"）。
- 运行期间会把主题临时切到浅色（与现有帮助截图风格一致），结束后恢复原值。
- 会临时创建一条示例定时任务用于截图，结束后删除。
"""
import os
import sys
import math
import json
import time
import traceback

# ---------- 路径准备：必须在导入项目模块之前 ----------
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)  # app_debug.log 等相对路径落在项目根

from PyQt6.QtWidgets import (
    QApplication, QWidget, QFrame, QGroupBox, QSplitter, QPushButton,
    QToolBar, QMenu, QDialog, QComboBox
)
from PyQt6.QtCore import Qt, QTimer, QPoint, QRect, QPointF
from PyQt6.QtGui import QPainter, QPen, QBrush, QColor, QPolygonF, QAction, QPixmap

from utils.adb_path import get_adb_path

# ---------- 主题先钉成浅色（结束时恢复） ----------
# ---------- 提前设置 OpenGL 共享（与 main.py 一致，必须在 QApplication 之前） ----------
QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
app = QApplication(sys.argv)

# ---------- 主题先钉成浅色（结束时恢复） ----------
from utils.settings import Settings, THEME_MODE_LIGHT
_ORIGINAL_THEME = None
try:
    # 必须用 raw 值（'system'/'light'/'dark'）；
    # get_theme_mode() 会把 'system' 解析成具体明暗，恢复时会污染配置
    _ORIGINAL_THEME = Settings.get_raw_theme_mode()
except Exception:
    pass
Settings.set_theme_mode(THEME_MODE_LIGHT)

# ---------- ================= 装配应用（照搬 main.py） ================= ----------
from utils.adb_path import get_adb_path
from views.main_window import MainWindow
from models.project_model import ProjectModel
from models.step_model import StepModel
from models.execution_model import ExecutionModel
from models.element_model import ElementModel
from models.suite_model import SuiteModel
from models.task_model import TaskModel, ScheduledTask
from views.project_tree_view import ProjectTreeView
from views.step_list_view import StepListView
from views.action_card_view import ActionCardView
from views.execute_view import ExecuteView
from views.logs_view import LogsView
from views.element_manager_view import ElementManagerView
from views.task_view import TaskView
from views.help_view import HelpView
from controllers.project_controller import ProjectController
from controllers.step_controller import StepController
from controllers.execution_controller import ExecutionController
from controllers.element_controller import ElementController
from services.device_service import DeviceService
from services.weditor_service import WeditorService
from utils.toast import show_toast
from utils.app_paths import migrate_legacy_data_files
from utils import log_colors
from utils.theme import Theme, ThemeMode
import logging

logging.basicConfig(
    filename='app_debug.log', level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

import subprocess
creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
try:
    subprocess.run([get_adb_path(), 'start-server'],
                   capture_output=True, text=True, timeout=20,
                   encoding='utf-8', errors='replace', creationflags=creationflags)
except Exception:
    pass

migrate_legacy_data_files()

project_model = ProjectModel()
step_model = StepModel()
exec_model = ExecutionModel()
element_model = ElementModel()
suite_model = SuiteModel()
task_model = TaskModel()

main_window = MainWindow()
main_window.set_models(project_model, step_model)

project_tree = ProjectTreeView()
step_list = StepListView()
action_card = ActionCardView()
execute_view = ExecuteView()
logs_view = LogsView()
element_manager = ElementManagerView(element_model)
help_view = HelpView()

execute_view.set_suite_model(suite_model)

step_controller = StepController(step_model, step_list, action_card)
project_controller = ProjectController(project_model, project_tree, step_controller, execute_view, main_window)
element_controller = ElementController(element_model, step_model)
project_controller.set_suite_model(suite_model)
step_controller.set_element_controller(element_controller)
action_card.set_element_controller(element_controller)
step_list.set_element_controller(element_controller)
element_manager.element_changed.connect(step_list.on_element_changed)

main_window.set_project_controller(project_controller)
main_window.set_step_controller(step_controller)
main_window.set_element_controller(element_controller)

device_svc = DeviceService()
device_svc.set_element_controller(element_controller)
device_svc.set_step_interval(2)
step_controller.set_device_service(device_svc)

exec_controller = ExecutionController(
    exec_model, project_model, step_model,
    execute_view, logs_view, device_svc
)
execute_view.generate_report_signal.connect(exec_controller.generate_report)

task_view = TaskView(task_model, suite_model)
task_view.set_project_model(project_model)
task_view.set_device_service(device_svc)
task_view.set_execution_controller(exec_controller)
task_view.set_logs_view(logs_view)

from controllers.task_controller import TaskController
task_controller = TaskController(
    task_model=task_model, task_view=task_view,
    suite_model=suite_model, project_model=project_model,
    step_model=step_model, device_service=device_svc,
    exec_controller=exec_controller, logs_view=logs_view,
)
execute_view.set_task_view(task_view)
task_view.start_scheduler()

main_window.set_edit_views(project_tree, step_list, action_card)
main_window.set_execute_view_with_logs(execute_view, logs_view)
main_window.set_element_manager_view(element_manager)
main_window.set_visualize_view(None)
main_window.set_help_view(help_view)

from views.adb_toolbox_view import AdbToolboxView
from controllers.adb_toolbox_controller import AdbToolboxController
adb_toolbox_view = AdbToolboxView()
adb_toolbox_controller = AdbToolboxController(view=adb_toolbox_view, device_service=device_svc)
main_window.set_adb_toolbox_view(adb_toolbox_view)
main_window.set_adb_toolbox_controller(adb_toolbox_controller)
main_window.register_sub_view(adb_toolbox_view)

def _append_bottom_log(text: str):
    main_window._bottom_log_entries.append(text)
    main_window._bottom_log_text.append(log_colors.recolor(text))

adb_toolbox_controller.log_emitted.connect(_append_bottom_log)

# 底部"虫师日志"面板（照搬 main.py，adb_5_logs 需要它）
from PyQt6.QtWidgets import QVBoxLayout as _QVL, QTextEdit as _QTE, QLabel as _QL
from PyQt6.QtGui import QFont as _QFont
from PyQt6.QtWidgets import QStackedWidget as _QSW

bottom_log_widget = QWidget()
bottom_log_widget.setObjectName("BottomLogPanel")
bl_layout = _QVL(bottom_log_widget)
bl_layout.setContentsMargins(10, 6, 10, 10)
bl_layout.setSpacing(4)
bl_title = _QL("🐞 虫师日志")
bl_title.setObjectName("BottomLogTitle")
bl_layout.addWidget(bl_title)
bottom_log_text = _QTE()
bottom_log_text.setObjectName("BottomLogText")
bottom_log_text.setReadOnly(True)
bottom_log_text.setFont(_QFont("Consolas", 10))
bottom_log_text.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
bottom_log_text.document().setMaximumBlockCount(MainWindow.BOTTOM_LOG_MAX_BLOCKS)

from services.notification_service import NotificationService
from views.notification_center_view import NotificationCenterView

notification_service = NotificationService(main_window)
notification_view = NotificationCenterView(notification_service, main_window)
bottom_stack = _QSW()
bottom_stack.setObjectName("BottomPanelStack")
bottom_stack.addWidget(bottom_log_text)
bottom_stack.addWidget(notification_view)
bl_layout.addWidget(bottom_stack, 1)

main_window._bottom_log_text = bottom_log_text
main_window._bottom_log_title = bl_title
main_window.set_bottom_log_placeholder(bottom_log_widget)
main_window.set_bottom_panel_stack(bottom_stack, notification_view)
main_window.set_notification_service(notification_service)

from controllers.notification_controller import NotificationController
notification_controller = NotificationController(service=notification_service, main_window=main_window)
notification_view.action_triggered.connect(notification_controller.route)
exec_controller.execution_finished.connect(notification_controller.on_execution_finished)
task_controller.task_finished.connect(notification_controller.on_task_finished)
task_controller.task_skipped.connect(notification_controller.on_task_skipped)
adb_toolbox_controller.pool.command_finished.connect(notification_controller.on_adb_command_finished)
logs_view.ai_analysis_done.connect(notification_controller.on_ai_analyzed)

weditor_svc = WeditorService()
main_window.set_weditor_service(weditor_svc)

main_window.set_device_service(device_svc)

QTimer.singleShot(100, lambda: main_window.apply_theme())

perf_model_data = None
from models.perf_model import PerfModel
from views.perf_view import PerfView
from controllers.perf_controller import PerfController
perf_model = PerfModel()
perf_view = PerfView()
perf_controller = PerfController(
    perf_model=perf_model, perf_view=perf_view,
    device_service=device_svc, project_model=project_model,
    step_model=step_model, suite_model=suite_model, logs_view=logs_view,
)
perf_view.set_suite_model(suite_model)
perf_view.set_project_model(project_model)
main_window.set_perf_view(perf_view)
main_window.register_sub_view(perf_view)
perf_view.hprof_requested.connect(adb_toolbox_controller._action_hprof)
perf_view.packet_requested.connect(adb_toolbox_controller._action_packet)
perf_controller.perf_finished.connect(notification_controller.on_perf_finished)
perf_controller.perf_alert.connect(notification_controller.on_perf_alert)

from models.voice_model import VoiceModel
from services.voice_service import get_voice_service
from views.voice_view import VoiceView
voice_model = VoiceModel()
voice_service = get_voice_service()
if voice_service.is_available():
    try:
        voice_service.apply_cfg(voice_model.settings)
    except Exception as e:
        print(f"[voice] apply cfg failed: {e}")
voice_view = VoiceView(model=voice_model, service=voice_service)
main_window.set_voice_view(voice_view)

main_window.showMaximized()
main_window.activateWindow()
main_window.raise_()

# ---------- ================= 截图工具函数 ================= ----------
OUT_DIR = os.path.join(PROJECT_ROOT, "resources", "images", "help")
os.makedirs(OUT_DIR, exist_ok=True)
DPR = app.primaryScreen().devicePixelRatio()
SHOT_LOG = []

def l2p(v):
    return int(round(v * DPR))

def flatten(pm, bg=QColor(255, 255, 255)):
    """把可能带透明通道的截图合成到白色底上（与现有帮助截图风格一致）"""
    out = QPixmap(pm.size())
    out.fill(bg)
    p = QPainter(out)
    p.drawPixmap(0, 0, pm)
    p.end()
    return out

def _map_to(widget, target, pt):
    return target.mapTo(widget, pt)

def draw_arrow(painter, x1, y1, x2, y2, color="#e53935"):
    """画一支红色箭头（坐标为物理像素）"""
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(QColor(color))
    pen.setWidth(max(4, l2p(4)))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))
    # 箭头三角
    ang = math.atan2(y2 - y1, x2 - x1)
    L = l2p(20)
    W = l2p(11)
    a = QPointF(x2 - L * math.cos(ang) + W * math.sin(ang),
                y2 - L * math.sin(ang) - W * math.cos(ang))
    b = QPointF(x2 - L * math.cos(ang) - W * math.sin(ang),
                y2 - L * math.sin(ang) + W * math.cos(ang))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(QColor(color)))
    painter.drawPolygon(QPolygonF([QPointF(x2, y2), a, b]))

def _target_rects(widget, targets):
    """targets: QWidget 或 (QWidget, fx, fy)。返回控件在 widget 内的完整 rect 列表"""
    rects = []
    for t in targets or []:
        if isinstance(t, tuple):
            w2, fx, fy = t
            if isinstance(fx, float) and fx <= 1.0:
                px = int(w2.width() * fx)
            else:
                px = int(fx)
            if isinstance(fy, float) and fy <= 1.0:
                py = int(w2.height() * fy)
            else:
                py = int(fy)
            p = w2.mapTo(widget, QPoint(px, py))
            rects.append(QRect(p.x(), p.y(), 0, 0))
        else:
            tl = t.mapTo(widget, QPoint(0, 0))
            rects.append(QRect(tl.x(), tl.y(), t.width(), t.height()))
    return rects

def _target_points(widget, targets):
    """箭头目标点：控件中心"""
    pts = []
    for r in _target_rects(widget, targets):
        pts.append(QPoint(r.x() + r.width() // 2, r.y() + r.height() // 2))
    return pts

def crop_shot(widget, filename, targets=None, include=None, pad=64,
              rect=None, full=False):
    """抓取 widget，按 targets/include 的包围盒裁剪并画红色箭头，保存 PNG。"""
    full_pm = widget.grab()
    pts = _target_points(widget, targets)
    trects = _target_rects(widget, targets)

    # 计算裁剪范围（逻辑坐标）
    if full:
        x0, y0, x1, y1 = 0, 0, widget.width(), widget.height()
    else:
        if rect is not None:
            x0, y0, x1, y1 = rect.x(), rect.y(), rect.x() + rect.width(), rect.y() + rect.height()
        else:
            x0 = y0 = 1e9
            x1 = y1 = -1e9
            for r in trects:
                x0 = min(x0, r.x()); y0 = min(y0, r.y())
                x1 = max(x1, r.x() + r.width()); y1 = max(y1, r.y() + r.height())
            x0 -= pad; y0 -= pad; x1 += pad; y1 += pad
        for w2 in (include or []):
            tl = w2.mapTo(widget, QPoint(0, 0))
            br = w2.mapTo(widget, QPoint(w2.width(), w2.height()))
            x0 = min(x0, tl.x()); y0 = min(y0, tl.y())
            x1 = max(x1, br.x()); y1 = max(y1, br.y())
        x0 = max(0, x0); y0 = max(0, y0)
        x1 = min(widget.width(), x1); y1 = min(widget.height(), y1)
        if x1 - x0 < 40 or y1 - y0 < 40:
            x0, y0, x1, y1 = 0, 0, widget.width(), widget.height()

    crop = full_pm.copy(l2p(x0), l2p(y0), l2p(x1 - x0), l2p(y1 - y0))
    crop = flatten(crop)
    if pts:
        painter = QPainter(crop)
        for p in pts:
            # 目标点转裁剪内物理坐标
            tx = l2p(p.x() - x0)
            ty = l2p(p.y() - y0)
            # 选一个箭头起点方向：优先右下 → 左下 → 右上 → 左上
            cands = [(95, 80), (-95, 80), (95, -80), (-95, -80), (0, 110)]
            sx = sy = None
            for dx, dy in cands:
                ax, ay = tx + l2p(dx), ty + l2p(dy)
                if l2p(10) <= ax <= crop.width() - l2p(10) and l2p(10) <= ay <= crop.height() - l2p(10):
                    sx, sy = ax, ay
                    break
            if sx is None:
                sx, sy = tx + l2p(95), ty + l2p(80)
            draw_arrow(painter, sx, sy, tx, ty)
        painter.end()
    path = os.path.join(OUT_DIR, filename)
    crop.save(path, "PNG")
    SHOT_LOG.append(filename)
    print(f"[shot] {filename}  {crop.width()}x{crop.height()}")

def grab_widget(widget, filename):
    pm = flatten(widget.grab())
    path = os.path.join(OUT_DIR, filename)
    pm.save(path, "PNG")
    SHOT_LOG.append(filename)
    print(f"[shot] {filename}  {pm.width()}x{pm.height()}")

def screen_crop(widget, filename, size=None, offset=None, pad=12,
                targets_global=None):
    """抓整个屏幕，按 widget 全局区域(±pad)裁剪。用于弹窗（菜单/下拉）。"""
    geo = widget.frameGeometry()
    gtl = widget.mapToGlobal(QPoint(0, 0))
    if offset is not None:
        gtl = widget.mapToGlobal(QPoint(*offset))
    if size is None:
        w, h = geo.width(), geo.height()
    else:
        w, h = size
    x0 = gtl.x() - pad; y0 = gtl.y() - pad
    x1 = gtl.x() + w + pad; y1 = gtl.y() + h + pad
    screen_pm = app.primaryScreen().grabWindow(0)
    crop = screen_pm.copy(l2p(x0), l2p(y0), l2p(x1 - x0), l2p(y1 - y0))
    crop = flatten(crop)
    if targets_global:
        painter = QPainter(crop)
        for gp in targets_global:
            tx = l2p(gp.x() - x0)
            ty = l2p(gp.y() - y0)
            sx = tx + l2p(90); sy = ty + l2p(70)
            if sx > crop.width() - l2p(8):
                sx = tx - l2p(90)
            draw_arrow(painter, sx, sy, tx, ty)
        painter.end()
    path = os.path.join(OUT_DIR, filename)
    crop.save(path, "PNG")
    SHOT_LOG.append(filename)
    print(f"[shot] {filename}  {crop.width()}x{crop.height()}")

def process(n=8):
    for _ in range(n):
        app.processEvents()

def wait(ms):
    """在步骤函数内部短暂等待（不嵌套事件循环，避免打乱步骤链）"""
    process()
    time.sleep(ms / 1000.0)
    process()

def find_refresh_btn():
    for b in main_window.toolbar.findChildren(QPushButton):
        if b.text() == "刷新":
            return b
    return None

def find_obj(root, name, typ=None):
    """按 objectName 递归查找子控件"""
    for c in root.findChildren(QWidget):
        if c.objectName() == name:
            if typ is not None and not isinstance(c, typ):
                continue
            return c
    return None

def ancestor_of(widget, typ, max_up=6):
    w = widget
    for _ in range(max_up):
        w = w.parentWidget()
        if w is None:
            return None
        if isinstance(w, typ):
            return w
    return None

# 编辑页三个分组（project_group / step_group / action_group 没存 self 引用）
_edit_page = None
def edit_page():
    global _edit_page
    if _edit_page is None:
        _edit_page = main_window.stacked_widget.widget(1)
    return _edit_page

def edit_splitter():
    for s in edit_page().findChildren(QSplitter):
        return s
    return None

def project_group():
    return edit_splitter().widget(0)

def step_group():
    return edit_splitter().widget(1)

def bottom_log_panel():
    w = main_window._bottom_log_text
    while w is not None:
        if w.objectName() == "BottomLogPanel":
            return w
        w = w.parentWidget()
    return None

def select_case_in_tree():
    """在项目树中选中一个在 steps_data 里有步骤的用例"""
    # 找到有步骤的 case
    try:
        with open(os.path.join(PROJECT_ROOT, "data", "steps_data.json"), encoding="utf-8") as f:
            case_steps = json.load(f).get("case_steps", {})
        step_case_ids = set(case_steps.keys())
    except Exception:
        step_case_ids = set()
    try:
        with open(os.path.join(PROJECT_ROOT, "data", "project_data.json"), encoding="utf-8") as f:
            proj = json.load(f)
    except Exception:
        proj = []

    def collect_cases(nodes, out):
        for n in nodes or []:
            if n.get("type") == "case":
                out.append((n.get("id"), n.get("name")))
            collect_cases(n.get("children"), out)
    cases = []
    collect_cases(proj, cases)
    target = None
    for cid, name in cases:
        if cid in step_case_ids:
            target = name
            break
    if target is None and cases:
        target = cases[-1][1]

    if target:
        project_tree.expandAll()
        model = project_tree.model()

        def walk(idx):
            if not idx.isValid():
                return None
            if model.data(idx) == target and model.rowCount(idx) == 0:
                return idx
            for r in range(model.rowCount(idx)):
                found = walk(model.index(r, 0, idx))
                if found is not None:
                    return found
            return None

        found = None
        for r in range(model.rowCount()):
            found = walk(model.index(r, 0))
            if found is not None:
                break
        if found is not None:
            project_tree.setCurrentIndex(found)
            return True
    return False

def select_first_voice_case():
    tree = voice_view.tree
    model = tree.model()
    if model is None:
        return False

    def walk(idx, depth=0):
        if not idx.isValid():
            return None
        if depth >= 1 and model.rowCount(idx) == 0:
            return idx
        for r in range(model.rowCount(idx)):
            f = walk(model.index(r, 0, idx), depth + 1)
            if f is not None:
                return f
        return None

    for r in range(model.rowCount()):
        f = walk(model.index(r, 0))
        if f is not None:
            tree.setCurrentIndex(f)
            return True
    return False

def open_settings_page(item_text):
    """打开设置对话框并导航到指定页，返回对话框"""
    from views.dialogs.settings_dialog import SettingsDialog
    dlg = SettingsDialog(main_window)
    dlg.setModal(False)
    dlg.show()
    process()
    found = None
    for i in range(dlg.nav_tree.topLevelItemCount()):
        top = dlg.nav_tree.topLevelItem(i)
        for j in range(top.childCount()):
            ch = top.child(j)
            if ch.text(0) == item_text:
                found = ch
                break
        if found:
            break
    if found is not None:
        dlg.nav_tree.setCurrentItem(found)
    process()
    return dlg

def popup_menu_shot(menu, anchor_widget, filename, arrow_action=None):
    """弹出菜单并抓屏（含菜单上下文）"""
    pos = anchor_widget.mapToGlobal(QPoint(0, anchor_widget.height() + 2))
    menu.popup(pos)
    process()
    wait(260)
    process()
    targets = []
    if arrow_action is not None:
        r = menu.actionGeometry(arrow_action)
        c = menu.mapToGlobal(r.center())
        targets.append(c)
    screen_crop(menu, filename, pad=10, targets_global=targets)
    menu.close()
    process()

# ---------- ================= 截图步骤序列 ================= ----------
refresh_btn_holder = {}

def step_switch_adb():
    main_window.switch_view(0)
    process()

def step_adb_shots():
    # adb_3 命令列表（左栏整块）
    grab_widget(adb_toolbox_view.left_group, "adb_3_cmdlist.png")
    # adb_2 搜索框（右栏顶部）
    crop_shot(adb_toolbox_view, "adb_2_search.png",
              targets=[adb_toolbox_view.search_edit, adb_toolbox_view.search_btn],
              pad=40)
    # adb_4 弱网 + Monkey 内嵌面板
    grab_widget(adb_toolbox_view.tool_scroll, "adb_4_quick.png")

def step_bottom_log():
    panel = bottom_log_panel()
    # 确保底部面板可见（相当于点开"日志"开关）
    try:
        main_window.toggle_bottom_log(True)
    except Exception:
        pass
    # 注入几行示例日志，让面板有内容
    tw = main_window._bottom_log_text
    tw.clear()
    for line in [
        '<span style="color:#1976d2;">[20:15:01] [信息] 虫师启动完成，开始扫描设备...</span>',
        '<span style="color:#4caf50;">[20:15:02] [成功] 已连接设备 DKS9K23914000082</span>',
        '<span style="color:#1976d2;">[20:15:05] [信息] 执行指令: adb shell dumpsys window | grep mCurrentFocus</span>',
        '<span style="color:#4caf50;">[20:15:06] [成功] 指令执行完成，耗时 312ms</span>',
        '<span style="color:#ff9800;">[20:15:10] [警告] 未检测到已连接设备，部分功能不可用</span>',
    ]:
        tw.append(line)
    process()
    if panel is not None:
        grab_widget(panel, "adb_5_logs.png")

def step_switch_edit():
    main_window.switch_view(1)
    process()

def step_select_case():
    ok = select_case_in_tree()
    print(f"[step] select case -> {ok}")

def step_toolbar_shots():
    # record_1 / weditor_1 / device_1：顶部工具栏左侧（设备区）
    tb = main_window.toolbar
    crop_shot(tb, "record_1_connect_device.png",
              targets=[main_window.device_combo], include=[find_refresh_btn()],
              pad=30, rect=QRect(0, 0, 360, tb.height()))
    crop_shot(tb, "weditor_1_connect.png",
              targets=[main_window.device_combo], include=[find_refresh_btn()],
              pad=30, rect=QRect(0, 0, 360, tb.height()))
    crop_shot(tb, "device_1_view_devices.png",
              targets=[main_window.device_combo], include=[find_refresh_btn()],
              pad=30, rect=QRect(0, 0, 360, tb.height()))
    # device_2：刷新按钮
    crop_shot(tb, "device_2_refresh.png",
              targets=[find_refresh_btn()],
              rect=QRect(0, 0, 360, tb.height()), pad=30)

def step_project_group_shot():
    grab_widget(project_group(), "record_2_select_case.png")

def step_record3_shot():
    sg = step_group()
    crop_shot(sg, "record_3_start_recording.png",
              targets=[main_window.record_btn],
              include=[main_window.step_search_input], pad=26)

def step_record5_shot():
    grab_widget(step_group(), "record_5_stop_generate.png")

def step_switch_execute():
    main_window.switch_view(3)
    process()

def step_execute_select_all():
    execute_view.select_all()
    process()

def step_execute_shots():
    ev = execute_view
    # execute_1 用例树
    grab_widget(ev.tree_view, "execute_1_select_cases.png")
    # execute_2 循环次数 + 失败停止（第一行右半）
    crop_shot(ev, "execute_2_set_strategy.png",
              targets=[ev.loop_spin, ev.stop_on_fail_check], pad=34)
    # execute_4 执行按钮
    crop_shot(ev, "execute_4_run.png",
              targets=[ev.execute_btn],
              include=[ev.select_all_btn, ev.deselect_all_btn], pad=26)
    # execute_3 套件下拉（第二行）
    crop_shot(ev, "execute_3_load_suite.png",
              targets=[ev.suite_combo],
              include=[ev.suite_label, ev.save_suite_btn], pad=26)
    # execute_6 测试报告
    crop_shot(ev, "execute_6_generate_report.png",
              targets=[ev.report_btn],
              include=[ev.del_suite_btn], pad=26)

def step_execute_logs():
    logs_view.add_log("▶ 开始执行：套件「1」共 1 条用例", 'info')
    logs_view.add_log("▶ [1/1] 用例「导航操作态」开始执行", 'info')
    logs_view.add_log("✅ 步骤 3/12：点击元素「搜索框」", 'success')
    logs_view.add_log("✅ 步骤 4/12：输入文本「加油站」", 'success')
    logs_view.add_log("✅ 步骤 5/12：等待 2 秒", 'success')
    logs_view.add_log("❌ 步骤 6/12：断言失败——未找到元素「路线详情」", 'error')
    process()
    grab_widget(logs_view, "execute_5_view_logs.png")

_task_id = "tmp_shot_daily_regression"

def step_add_task():
    task = ScheduledTask(
        id=_task_id, name="每日回归测试", suite_name="1",
        schedule_type="daily", scheduled_time="09:00",
        loop_count=3, stop_on_fail=True, enabled=True,
    )
    try:
        task_model.add_task(task)
    except Exception as e:
        print(f"[step] add task: {e}")
    task_view.refresh_list()
    process()

def step_task1():
    # 顶部区域（标题 + 新增/删除/执行按钮），箭头指向"新增"
    crop_shot(task_view, "task_1_add.png",
              targets=[task_view.add_btn],
              rect=QRect(0, 0, task_view.width(), 80), pad=20)

def step_task_dialog():
    from views.task_view import TaskEditDialog
    global _task_dlg
    suite_names = [s.name for s in suite_model.suites] or ["1"]
    _task_dlg = TaskEditDialog(task_view, task=None, suite_names=suite_names)
    _task_dlg.show()
    process()
    wait(300)
    process()
    grab_widget(_task_dlg, "task_2_fill_form.png")
    # task_3：对话框下半部分（执行时间 / 循环次数）
    full = _task_dlg.grab()
    h = full.height()
    crop = full.copy(0, l2p(h * 0.52), full.width(), l2p(h * 0.48))
    crop.save(os.path.join(OUT_DIR, "task_3_set_time.png"), "PNG")
    SHOT_LOG.append("task_3_set_time.png")
    print("[shot] task_3_set_time.png")

def step_task_dialog_close():
    global _task_dlg
    try:
        _task_dlg.close()
        _task_dlg.deleteLater()
    except Exception:
        pass

def step_task_list_shots():
    # task_4 列表（含任务）
    grab_widget(task_view, "task_4_save.png")
    # task_5 执行按钮（顶部区域）
    crop_shot(task_view, "task_5_manual_run.png",
              targets=[task_view.run_btn],
              rect=QRect(0, 0, task_view.width(), 80), pad=20)
    # task_6 启用/禁用开关（第一行的 enable_check）
    item = None
    for c in task_view.findChildren(QWidget):
        if c.objectName() == "TaskListItem":
            item = c
            break
    if item is not None:
        chk = item.findChild(QWidget)  # fallback
        from PyQt6.QtWidgets import QCheckBox as _QC
        enable = item.findChild(_QC)
        if enable is None:
            enable = (item, 0.88, 0.5)
        crop_shot(task_view, "task_6_toggle_disable.png",
                  targets=[enable], include=[item], pad=30)
    else:
        grab_widget(task_view, "task_6_toggle_disable.png")

def step_remove_task():
    try:
        task_model.delete_task(_task_id)
    except Exception as e:
        print(f"[step] delete task: {e}")
    task_view.refresh_list()

def step_switch_elements():
    main_window.switch_view(4)
    process()

def step_element_shots():
    em = element_manager
    grab_widget(em, "element_1_view.png")
    crop_shot(em, "element_2_search_filter.png",
              targets=[em.search_input, em.app_combo], pad=40)

def step_element_select_row():
    try:
        element_manager.table.selectRow(0)
    except Exception:
        pass
    process()

def step_element_btn_shots():
    em = element_manager
    # 三个按钮截图带按钮行上下文（编辑/删除/导入/导出），箭头分别指向对应按钮
    row_ctx = [em.delete_btn, em.import_btn, em.export_btn]
    crop_shot(em, "element_3_add.png", targets=[em.add_btn], include=row_ctx, pad=24)
    crop_shot(em, "element_4_edit.png",
              targets=[em.edit_btn], include=[em.add_btn, em.delete_btn, em.export_btn], pad=24)
    crop_shot(em, "element_5_delete.png",
              targets=[em.delete_btn], include=[em.add_btn, em.edit_btn], pad=24)
    # 元素导入导出（import_3 / import_4 / element_7）
    crop_shot(em, "element_7_import_export.png",
              targets=[em.import_btn, em.export_btn],
              include=[em.add_btn, em.delete_btn], pad=24)
    crop_shot(em, "import_3_export_elements.png", targets=[em.export_btn],
              include=[em.add_btn, em.import_btn], pad=24)
    crop_shot(em, "import_4_import_elements.png", targets=[em.import_btn],
              include=[em.add_btn, em.export_btn], pad=24)

def step_element_context_menu():
    em = element_manager
    table = em.table
    menu = QMenu(em)
    menu.setObjectName("TempCtxMenu")
    # 显式样式：应用级 QSS 会让临时菜单文字消失，这里固定浅色样式
    menu.setStyleSheet(
        "QMenu { background: #ffffff; color: #333333; border: 1px solid #d0d0d0; }"
        "QMenu::item { padding: 6px 24px 6px 12px; }"
        "QMenu::item:selected { background: #1976d2; color: #ffffff; }"
    )
    a1 = QAction("验证元素", menu)
    a2 = QAction("删除选中的 1 个元素", menu)
    menu.addAction(a1)
    menu.addAction(a2)
    viewport = table.viewport()
    # 在第一行位置弹出
    row_rect = table.visualItemRect(table.item(0, 0)) if table.item(0, 0) else viewport.rect()
    pos_global = viewport.mapToGlobal(QPoint(row_rect.right() - 40, row_rect.bottom() + 4))
    menu.popup(pos_global)
    process()
    wait(300)
    process()
    r1 = menu.actionGeometry(a1)
    target = menu.mapToGlobal(r1.center())
    screen_crop(menu, "element_6_verify.png", pad=22, targets_global=[target])
    menu.close()
    process()

def step_weditor_container_shot():
    """不打开 Dock（打开会自动启动 weditor 子进程并清掉按钮），
    直接离屏抓取还带"启动 weditor"按钮的容器"""
    container = getattr(main_window, "_visualize_container", None)
    btn = getattr(main_window, "visualize_button", None)
    if container is None or btn is None:
        return
    container.resize(900, 520)
    process()
    crop_shot(container, "weditor_2_launch.png", targets=[btn], pad=90)

def step_device_combo_popup():
    """无设备时下拉列表只有"未检测到设备"一项，真实弹窗抓取不可靠，
    改为工具栏设备区特写：与 device_1 同区域，箭头指向下拉框"""
    tb = main_window.toolbar
    crop_shot(tb, "device_3_select_device.png",
              targets=[main_window.device_combo], include=[find_refresh_btn()],
              pad=30, rect=QRect(0, 0, 360, tb.height()))

def step_settings_device_page():
    global _settings_dlg
    _settings_dlg = open_settings_page("设备维护")
    grab_widget(_settings_dlg, "device_4_restore_ime.png")

def step_settings_voice_page():
    global _settings_dlg
    # 导航到语音设置
    found = None
    for i in range(_settings_dlg.nav_tree.topLevelItemCount()):
        top = _settings_dlg.nav_tree.topLevelItem(i)
        for j in range(top.childCount()):
            ch = top.child(j)
            if ch.text(0) == "语音设置":
                found = ch
                break
        if found:
            break
    if found is not None:
        _settings_dlg.nav_tree.setCurrentItem(found)
    process()
    wait(200)
    process()
    grab_widget(_settings_dlg, "voice_5_wake_word_setting.png")

def step_settings_close():
    global _settings_dlg
    try:
        _settings_dlg.close()
        _settings_dlg.deleteLater()
    except Exception:
        pass

def step_project_menu():
    menu = main_window._project_menu
    btn = main_window._project_menu_btn
    acts = menu.actions()
    act_import = acts[0] if len(acts) > 0 else None
    act_export = acts[1] if len(acts) > 1 else None
    pos = btn.mapToGlobal(QPoint(0, btn.height() + 2))
    menu.popup(pos)
    process()
    wait(280)
    process()
    targets = []
    if act_export is not None:
        r = menu.actionGeometry(act_export)
        targets.append(menu.mapToGlobal(r.center()))
    screen_crop(menu, "import_1_export_cases.png", pad=10, targets_global=targets)
    targets = []
    if act_import is not None:
        r = menu.actionGeometry(act_import)
        targets.append(menu.mapToGlobal(r.center()))
    screen_crop(menu, "import_2_import_cases.png", pad=10, targets_global=targets)
    menu.close()
    process()

def step_switch_perf():
    main_window.switch_view(8)
    process()

def step_perf_shots():
    pv = perf_view
    crop_shot(pv, "perf_1_select_app.png",
              targets=[pv.app_combo, pv.app_refresh_btn], pad=44)
    crop_shot(pv, "perf_3_set_interval.png",
              targets=[pv.interval_combo], pad=44)
    crop_shot(pv, "perf_4_start_monitor.png",
              targets=[pv.start_btn], pad=44)
    crop_shot(pv, "perf_2_choose_metrics.png",
              targets=[pv.metric_radios['cpu'], pv.metric_radios['traffic']], pad=40)
    # 底部按钮区
    bottom = find_obj(pv, "PerfBottomBar", QFrame)
    if bottom is not None:
        crop_shot(pv, "perf_6_export_report.png",
                  targets=[pv.report_btn, pv.export_csv_btn],
                  include=[bottom], pad=30)
    # 卡片区（性能曲线 + 性能工具卡片）
    grab_widget(pv.scroll, "perf_5_view_charts.png")

def step_perf_scenario_on():
    perf_view.mode_combo.setCurrentIndex(1)
    process()
    wait(300)
    process()

def step_perf_scenario_shots():
    pv = perf_view
    crop_shot(pv, "perf_scenario_1_select_app.png",
              targets=[pv.app_combo], pad=44)
    crop_shot(pv, "perf_scenario_2_choose_suite.png",
              targets=[pv.mode_combo, pv.suite_combo], pad=40)
    crop_shot(pv, "perf_scenario_3_set_loop.png",
              targets=[pv.loop_spin, pv.stop_on_fail_check], pad=40)
    crop_shot(pv, "perf_scenario_4_start.png",
              targets=[pv.start_btn], pad=44)
    grab_widget(pv, "perf_scenario_5_view_logs.png")

def step_perf_scenario_off():
    perf_view.mode_combo.setCurrentIndex(0)
    process()

def step_switch_voice():
    main_window.switch_view(9)
    process()

def step_voice_shots():
    vv = voice_view
    left = find_obj(vv, "VoiceLeftGroup", QGroupBox)
    if left is not None:
        grab_widget(left, "voice_1_manage.png")
    mid = find_obj(vv, "VoiceMiddleGroup", QGroupBox)
    right = None
    if vv.rate_spin is not None:
        right = ancestor_of(vv.rate_spin, QGroupBox)
    if right is not None:
        grab_widget(right, "voice_3_execute.png")
    # 保存引用给后续步骤
    global _voice_mid
    _voice_mid = mid

def step_voice_select_case():
    ok = select_first_voice_case()
    print(f"[step] voice select case -> {ok}")
    process()

def step_voice_mid_shots():
    mid = _voice_mid
    if mid is None:
        mid = find_obj(voice_view, "VoiceMiddleGroup", QGroupBox)
    if mid is not None:
        grab_widget(mid, "voice_2_phrases.png")
    crop_shot(voice_view, "voice_4_wake_word.png",
              targets=[voice_view.wake_btn],
              include=[voice_view.middle_title, voice_view.case_label], pad=30)

def step_cleanup_and_quit():
    step_remove_task()
    # 恢复主题
    try:
        if _ORIGINAL_THEME:
            Settings.set_theme_mode(_ORIGINAL_THEME)
    except Exception:
        pass
    try:
        main_window._device_watcher.stop()
    except Exception:
        pass
    # 兜底：若 weditor 子进程被意外拉起，结束它避免残留
    try:
        svc = getattr(main_window, "weditor_service", None)
        proc = getattr(svc, "process", None) if svc is not None else None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            print("[cleanup] weditor process terminated")
    except Exception:
        pass
    print("[done] shots:", len(SHOT_LOG))
    app.quit()

_runner = None
_task_dlg = None
_settings_dlg = None
_voice_mid = None

STEPS = [
    (2600, step_switch_adb,          "切换到 ADB工具箱"),
    (700,  step_adb_shots,           "ADB工具箱截图"),
    (200,  step_bottom_log,          "底部日志截图"),
    (700,  step_switch_edit,         "切换到自动化编辑"),
    (700,  step_select_case,         "选中用例"),
    (400,  step_toolbar_shots,       "工具栏截图"),
    (300,  step_project_group_shot,  "项目树截图"),
    (200,  step_record3_shot,        "录制按钮截图"),
    (300,  step_record5_shot,        "步骤列表截图"),
    (700,  step_switch_execute,      "切换到自动化执行"),
    (700,  step_execute_select_all,  "全选用例"),
    (500,  step_execute_shots,       "执行页截图"),
    (200,  step_execute_logs,        "执行日志截图"),
    (400,  step_add_task,            "新增示例任务"),
    (400,  step_task1,               "定时计划新增按钮"),
    (500,  step_task_dialog,         "任务编辑对话框"),
    (200,  step_task_dialog_close,   "关闭对话框"),
    (400,  step_task_list_shots,     "任务列表截图"),
    (200,  step_remove_task,         "删除示例任务"),
    (700,  step_switch_elements,     "切换到应用元素库"),
    (800,  step_element_shots,       "元素库截图"),
    (400,  step_element_select_row,  "选中元素行"),
    (400,  step_element_btn_shots,   "元素库按钮截图"),
    (500,  step_element_context_menu,"元素右键菜单"),
    (500,  step_weditor_container_shot, "weditor 启动按钮页"),
    (500,  step_device_combo_popup,  "设备下拉弹窗"),
    (600,  step_settings_device_page,"设置-设备维护页"),
    (600,  step_settings_voice_page, "设置-语音设置页"),
    (400,  step_settings_close,      "关闭设置"),
    (500,  step_project_menu,        "项目管理菜单"),
    (700,  step_switch_perf,         "切换到性能检测"),
    (900,  step_perf_shots,          "性能检测截图"),
    (400,  step_perf_scenario_on,    "切到场景化模式"),
    (800,  step_perf_scenario_shots, "场景化截图"),
    (300,  step_perf_scenario_off,   "切回独立监控"),
    (700,  step_switch_voice,        "切换到语音播报"),
    (900,  step_voice_shots,         "语音播报截图"),
    (500,  step_voice_select_case,   "选中语音用例"),
    (700,  step_voice_mid_shots,     "语音中栏截图"),
    (600,  step_cleanup_and_quit,    "清理并退出"),
]

def run_steps(i=0):
    if i >= len(STEPS):
        print("[runner] all steps done")
        app.quit()
        return
    delay, fn, name = STEPS[i]
    def _do():
        try:
            fn()
            print(f"[runner] ok: {name}")
        except Exception:
            print(f"[runner] FAILED: {name}")
            traceback.print_exc()
        QTimer.singleShot(30, lambda: run_steps(i + 1))
    QTimer.singleShot(delay, _do)

QTimer.singleShot(1200, run_steps)
app.exec()
print("[exit] total shots:", len(SHOT_LOG))
print("[exit] files:", ", ".join(SHOT_LOG))
