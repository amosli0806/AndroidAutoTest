# views/dialogs/element_pick_dialog.py
"""元素拾取面板：设备画面点一下 → 自动命中控件 → 生成定位方式与定位值。

跟「截屏取图」（image_crop_dialog.py）是一对：
    截屏取图 —— 框一块区域，存成图像模板（定位方式 = 图像）
    元素拾取 —— 点一个控件，生成语义定位（资源 ID / 文本 / 描述 / 坐标）

两个关键约束（都在 2026-11 的调研里踩过）：
1. **dump 只做一次**：车机单次完整 dump 实测 8~12 秒，所以打开面板时 dump 一次并缓存，
   之后点击全部在本地 XML 里命中；换页面了点「刷新」重新截图 + dump。
2. **不做坐标换算**：点击的是截图，截图是当前显示方向的尺寸，uiautomator 的 bounds
   也是显示方向，两者同一套坐标系（录制那边需要换算是 getevent 给的是物理面板坐标，
   这里不涉及）。但会校验一次截图与层级根节点尺寸，差太多说明画面对不上，直接提示。

命中规则不在这里，在 utils/ui_tree.py —— 与录制反查共用同一份。
"""
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
                             QPushButton, QFrame, QApplication, QCheckBox,
                             QWidget, QProgressBar)
from PyQt6.QtCore import Qt, QTimer, QRect
from PyQt6.QtGui import QPixmap, QImage, QPainter, QColor, QPen

from utils.settings import Settings, THEME_MODE_DARK
from utils.ui_tree import (find_node_at, parse_xml, locator_for, describe_node,
                           suggest_element_name)


class _PickLabel(QLabel):
    """显示缩放后的截图；点击回调原图坐标，命中后画高亮框。

    高亮框存的是**原图坐标**，绘制时再乘缩放比 —— 这样窗口 resize / 重新截图
    都不会让框错位。
    """

    def __init__(self):
        super().__init__()
        self._scale = 1.0
        self._hit_bounds = None          # 原图坐标 (left, top, right, bottom)
        self.on_click = None             # 回调：原图坐标 (x, y)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def set_scale(self, scale: float):
        self._scale = max(0.0001, float(scale))

    def set_hit(self, bounds):
        self._hit_bounds = bounds
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.on_click:
            p = e.position().toPoint()
            # 点击点在 label 内的位置 → 原图坐标
            x = int(p.x() * self._scale)
            y = int(p.y() * self._scale)
            self.on_click(x, y)
        super().mousePressEvent(e)

    def paintEvent(self, e):
        super().paintEvent(e)            # 先画截图
        b = self._hit_bounds
        if not b:
            return
        s = self._scale
        rect = QRect(int(b[0] / s), int(b[1] / s),
                     max(1, int((b[2] - b[0]) / s)),
                     max(1, int((b[3] - b[1]) / s)))
        painter = QPainter(self)
        # 命中区域外压暗，一眼看出点到了哪
        mask = QColor(0, 0, 0, 90)
        painter.fillRect(QRect(0, 0, self.width(), rect.top()), mask)
        painter.fillRect(QRect(0, rect.bottom() + 1, self.width(),
                               self.height() - rect.bottom() - 1), mask)
        painter.fillRect(QRect(0, rect.top(), rect.left(), rect.height()), mask)
        painter.fillRect(QRect(rect.right() + 1, rect.top(),
                               self.width() - rect.right() - 1, rect.height()), mask)
        painter.setPen(QPen(QColor("#1976d2"), 2))
        painter.drawRect(rect)
        painter.end()


class ElementPickDialog(QDialog):
    """拾取结果在 `self.result`：

        {'loc_type': '资源ID', 'loc_value': 'com.x:id/btn',
         'x': 512, 'y': 300, 'info': {...}}     取消时保持 None
    """

    MAX_W, MAX_H = 860, 640

    def __init__(self, device_service, parent=None, element_model=None):
        """element_model 传了才显示「同时存入元素库」——从动作卡片拾取时传，
        在元素库自己的「创建/编辑」对话框里不传（那里本来就在建元素）。"""
        super().__init__(parent)
        self.setObjectName("ElementPickDialog")
        self.setWindowTitle("从画面拾取元素")
        self.setModal(True)
        self.device_service = device_service
        self.element_model = element_model
        self.result = None
        self._full = None            # 原图 QPixmap
        self._scale = 1.0
        self._root = None            # 层级 XML 根
        self._hit = None             # 当前命中节点 info
        self._loading = False        # 正在截图 + dump（防连点重入）
        self._pick_x = self._pick_y = 0

        self._setup_ui()
        self._apply_theme()
        # 截图 + dump 可能几秒（车机更久），放到事件循环里做，先把窗口显示出来
        QTimer.singleShot(0, self._reload)

    # ------------------------------------------------------------------
    def _setup_ui(self):
        self.root_layout = QVBoxLayout(self)
        self.root_layout.setContentsMargins(12, 12, 12, 12)
        self.root_layout.setSpacing(8)

        self.hint_label = QLabel("正在读取设备画面…")
        self.hint_label.setWordWrap(True)
        self.root_layout.addWidget(self.hint_label)

        self.pic = _PickLabel()
        self.pic.on_click = self._on_click
        self.pic.setMinimumSize(320, 180)
        self.root_layout.addWidget(self.pic, 1, Qt.AlignmentFlag.AlignCenter)

        # 预览条：定位方式 + 定位值 + 命中说明
        self.preview = QFrame()
        self.preview.setObjectName("PickPreview")
        pv = QVBoxLayout(self.preview)
        pv.setContentsMargins(10, 8, 10, 8)
        pv.setSpacing(2)

        self.loc_label = QLabel("定位方式：—")
        self.loc_label.setObjectName("PickLoc")
        self.value_label = QLabel("定位值：—")
        self.value_label.setObjectName("PickValue")
        self.value_label.setWordWrap(True)
        self.detail_label = QLabel("点画面上任意控件即可拾取")
        self.detail_label.setObjectName("PickDetail")
        self.detail_label.setWordWrap(True)
        pv.addWidget(self.loc_label)
        pv.addWidget(self.value_label)
        pv.addWidget(self.detail_label)
        self.root_layout.addWidget(self.preview)

        # 「同时存入元素库」：仅从动作卡片拾取时出现。勾选后展开三行，默认值都能自动补，
        # 用户直接点「确定」就既填了卡片、又存进了元素库。
        self.save_to_lib = None
        self.lib_app = self.lib_module = self.lib_name = None
        if self.element_model is not None:
            self.save_to_lib = QCheckBox("同时存入元素库")
            self.save_to_lib.toggled.connect(self._toggle_lib_fields)
            self.root_layout.addWidget(self.save_to_lib)

            self.lib_box = QFrame()
            self.lib_box.setObjectName("PickLibBox")
            lib_layout = QHBoxLayout(self.lib_box)
            lib_layout.setContentsMargins(10, 6, 10, 6)
            lib_layout.setSpacing(6)
            self.lib_app = QLineEdit()
            self.lib_app.setPlaceholderText("所属应用")
            self.lib_module = QLineEdit()
            self.lib_module.setPlaceholderText("所属模块")
            self.lib_name = QLineEdit()
            self.lib_name.setPlaceholderText("元素名称")
            for w in (self.lib_app, self.lib_module, self.lib_name):
                lib_layout.addWidget(w, 1)
            self.lib_box.setVisible(False)
            self.root_layout.addWidget(self.lib_box)

        # 只有两个按钮，都靠右、等宽、同一套主按钮样式（宽度在 _apply_theme 里按字体定）。
        # 不设「取消」：点右上角 × 或按 Esc 就是取消（QDialog 默认行为），少一个按钮更清爽。
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch()

        # 不挂悬停提示（2026-11-09 用户要求）：按钮文案已经说明动作
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.setObjectName("refreshBtn")
        self.refresh_btn.clicked.connect(self._reload)
        btn_row.addWidget(self.refresh_btn)

        self.ok_btn = QPushButton("确定")
        self.ok_btn.setObjectName("okBtn")
        self.ok_btn.setEnabled(False)
        self.ok_btn.setDefault(True)
        self.ok_btn.clicked.connect(self._confirm)
        btn_row.addWidget(self.ok_btn)
        self.root_layout.addLayout(btn_row)

        # 加载遮罩：截图 + dump 期间盖住空内容，只显示居中的加载提示；
        # 加载完成后一次性换成完整画面，避免「先弹一个大空窗、再突然缩小」两段式跳动。
        self.loading_overlay = QWidget(self)
        self.loading_overlay.setObjectName("PickLoadingOverlay")
        self.loading_overlay.setAttribute(
            Qt.WidgetAttribute.WA_StyledBackground, True)
        overlay_layout = QVBoxLayout(self.loading_overlay)
        overlay_layout.setContentsMargins(24, 24, 24, 24)
        overlay_layout.addStretch(1)
        self.loading_label = QLabel("正在读取设备画面…")
        self.loading_label.setObjectName("PickLoadingLabel")
        self.loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        overlay_layout.addWidget(self.loading_label)
        self.loading_bar = QProgressBar()
        self.loading_bar.setObjectName("PickLoadingBar")
        self.loading_bar.setRange(0, 0)          # 不确定进度：走忙碌动画
        self.loading_bar.setTextVisible(False)
        self.loading_bar.setFixedSize(180, 6)
        overlay_layout.addWidget(self.loading_bar, 0,
                                 Qt.AlignmentFlag.AlignHCenter)
        overlay_layout.addStretch(1)
        # 初始就是个紧凑的加载窗；拿到截图后再按图片比例调整一次大小
        self.resize(420, 260)
        self.loading_overlay.setGeometry(self.rect())
        self.loading_overlay.show()
        self.loading_overlay.raise_()

    # ------------------------------------------------------------------
    def resizeEvent(self, event):
        super().resizeEvent(event)
        overlay = getattr(self, "loading_overlay", None)
        if overlay is not None:
            overlay.setGeometry(self.rect())

    def _show_loading(self):
        self.loading_overlay.setGeometry(self.rect())
        self.loading_overlay.show()
        self.loading_overlay.raise_()

    def _hide_loading(self):
        self.loading_overlay.hide()

    def _reload(self):
        """重新截图 + 读取层级（各一次）。

        两点必须注意：
        1. 截图和 dump 都是**同步阻塞**的（车机能到十几秒），期间事件循环不转，
           所以要在阻塞前先重绘一次，让加载遮罩真正显示出来。
        2. 光靠置灰挡不住连点 —— 点击事件会排在消息循环里，等这轮结束依次触发。
           所以再加 _loading 重入保护，第二次进来直接返回。
        """
        if self._loading:
            return
        self._loading = True
        self.refresh_btn.setEnabled(False)
        self.ok_btn.setEnabled(False)
        self._set_hint("正在读取设备画面…")
        self._show_loading()
        self.refresh_btn.repaint()
        QApplication.processEvents()          # 先让加载遮罩画出来，再去干阻塞的活
        try:
            self._load_screen_and_hierarchy()
        except Exception as e:                # 兜底：任何异常都不能让按钮永久灰掉
            self._set_hint(f"读取画面失败：{e}", error=True)
        finally:
            self._loading = False
            self._hide_loading()              # 内容已就绪/已给出错误，整窗一次性呈现
            self.refresh_btn.setEnabled(True)
            self.ok_btn.setEnabled(bool(self._hit))

    def _available_rect(self):
        """当前屏幕的可用区域（已扣掉任务栏）。"""
        screen = self.screen() or QApplication.primaryScreen()
        return screen.availableGeometry() if screen else QRect(0, 0, 1280, 720)

    def _frame_overhead(self):
        """窗口边框/标题栏额外占的宽高（用于把整个窗口框算进屏幕范围）。"""
        fg = self.frameGeometry()
        return (max(0, fg.width() - self.width()),
                max(0, fg.height() - self.height()))

    def _fit_image_size(self):
        """把图片限制在屏幕可用区域内，给下方预览/按钮留出约 250px。"""
        avail = self._available_rect()
        dw, dh = self._frame_overhead()
        return (max(320, min(self.MAX_W, avail.width() - dw - 60)),
                max(180, min(self.MAX_H, avail.height() - dh - 250)))

    def _fit_window(self, scaled):
        """按图片大小调整窗口，并重新居中 —— 不能只 resize，否则会向右下角长。"""
        avail = self._available_rect()
        dw, dh = self._frame_overhead()
        final_w = min(max(560, scaled.width() + 40),
                      max(420, avail.width() - dw - 20))
        final_h = min(scaled.height() + 230,
                      max(260, avail.height() - dh - 20))
        self.resize(final_w, final_h)
        w, h = self.width(), self.height()
        x = avail.x() + max(0, (avail.width() - w - dw) // 2)
        y = avail.y() + max(0, (avail.height() - h - dh) // 2)
        self.move(x, y)

    def _load_screen_and_hierarchy(self):
        """真正的加载：截图 → 缩放显示 → dump 层级 → 校验尺寸。"""
        if self.device_service is None or getattr(self.device_service, 'device', None) is None:
            self._set_hint("设备未连接，无法拾取", error=True)
            self.pic.setEnabled(False)
            return

        self.pic.update()

        # 截图
        try:
            img = self.device_service.device.screenshot()
            img = img.convert("RGBA")
            qimg = QImage(img.tobytes("raw", "RGBA"), img.width, img.height,
                          QImage.Format.Format_RGBA8888)
            self._full = QPixmap.fromImage(qimg)
        except Exception as e:
            self._full = None
            self._set_hint(f"截图失败：{e}", error=True)
            return

        scaled = self._full.scaled(
            *self._fit_image_size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        self._scale = self._full.width() / max(1, scaled.width())

        # 层级（一次）。图片先不挂到界面：等 dump 也完成后再一起呈现，
        # 避免加载过程中窗口被图片撑大、出现第二次尺寸跳动。
        try:
            self._root = parse_xml(self.device_service.dump_ui_xml())
        except Exception:
            self._root = None

        self._hit = None
        self.loc_label.setText("定位方式：—")
        self.value_label.setText("定位值：—")
        self.detail_label.setText("点画面上任意控件即可拾取")

        # 图片和窗口尺寸一次性就位（dump 失败也保留截图，便于对照）
        self.pic.set_scale(self._scale)
        self.pic.setPixmap(scaled)
        self.pic.setFixedSize(scaled.size())
        self.pic.set_hit(None)
        # 按图片比例调整一次窗口，并重新居中：只 resize 会保持左上角不动，
        # 小窗变大窗时会一路往右下长，最后超出屏幕。
        self._fit_window(scaled)

        if self._root is None:
            self._set_hint("无法读取界面层级（可能界面一直在动，或应用是自绘 UI）——"
                           "可以重试「刷新」，或改用「截屏取图」/ 手动填写", error=True)
            return

        # 截图画布与层级根节点的尺寸对不上时，命中会整体偏移，先提示出来
        mismatch = self._check_size_match()
        if mismatch:
            self._set_hint(mismatch, error=True)
            return

        self._set_hint("在画面上点击要定位的控件")

    def _check_size_match(self):
        """截图与层级根节点尺寸做一次粗校验，差太多说明画面对不上。"""
        if self._full is None or self._root is None:
            return None
        from utils.ui_tree import parse_bounds
        b = parse_bounds(self._root.get("bounds"))
        if not b:
            return None
        sw, sh = self._full.width(), self._full.height()
        dw, dh = abs((b[2] - b[0]) - sw), abs((b[3] - b[1]) - sh)
        if dw > sw * 0.05 or dh > sh * 0.05:
            return (f"画面尺寸 {sw}×{sh} 与界面层级 {b[2] - b[0]}×{b[3] - b[1]} 不一致，"
                    f"命中可能偏移 —— 建议重试「刷新」")
        return None

    def _on_click(self, x, y):
        if self._root is None:
            return
        self._pick_x, self._pick_y = x, y
        info = find_node_at(self._root, x, y)
        self._hit = info

        if not info:
            self.pic.set_hit(None)
            self.loc_label.setText("定位方式：—")
            self.value_label.setText("定位值：—")
            self.detail_label.setText("该位置没有命中任何控件")
            self._set_hint("该位置没有命中任何控件 —— 点图标/按钮的中间再试，"
                           "或改用「截屏取图」", error=True)
            self.ok_btn.setEnabled(False)
            return

        loc_type, loc_value = locator_for(info, x, y)
        self.pic.set_hit(info.get("bounds"))
        self.loc_label.setText(f"定位方式：{loc_type}")
        self.value_label.setText(f"定位值：{loc_value}")
        self.ok_btn.setEnabled(bool(loc_value))

        # describe_node 里已经包含「同 ID 共 N 个（第 K 个）」，这里不再重复拼
        # 勾了「存入元素库」但名称还空着时，按控件猜一个（用户可改）
        if self.lib_name is not None and not self.lib_name.text().strip():
            self.lib_name.setText(self._guess_name(info))

        detail = describe_node(info)
        if loc_type == "坐标":
            detail = (f"该控件没有可用的资源 ID / 文本 / 描述，已按坐标定位（{x},{y}）；"
                      f"同分辨率回放才稳，换设备可能点偏")
        self.detail_label.setText(detail)
        self._set_hint(f"已命中，点「确定」填入卡片（{loc_type}）")

    def _toggle_lib_fields(self, checked):
        """勾选「同时存入元素库」才展开三个输入框，不勾时保持紧凑。

        勾上就把能自动填的直接填好（应用=当前包名、模块=默认、名称=控件名），
        免得用户面对三个空框不知道要不要填 —— 不改也能直接点「确定」。
        """
        self.lib_box.setVisible(bool(checked))
        if not checked or not self._hit:
            return
        if not self.lib_app.text().strip():
            self.lib_app.setText(self._hit.get("package") or "")
        if not self.lib_module.text().strip():
            self.lib_module.setText("默认")
        if not self.lib_name.text().strip():
            self.lib_name.setText(suggest_element_name(self._hit))

    def _guess_name(self, info) -> str:
        """按控件猜一个元素名（规则在 ui_tree，元素库对话框也用同一份）。"""
        return suggest_element_name(info)

    def _confirm(self):
        if not self._hit:
            return
        loc_type, loc_value = locator_for(self._hit, self._pick_x, self._pick_y)
        self.result = {
            "loc_type": loc_type,
            "loc_value": loc_value,
            "x": self._pick_x,
            "y": self._pick_y,
            "info": self._hit,
        }

        if (self.save_to_lib is not None and self.save_to_lib.isChecked()
                and self.element_model is not None):
            info = self._hit
            app = self.lib_app.text().strip() or (info.get("package") or "未分类")
            module = self.lib_module.text().strip() or "默认"
            name = self.lib_name.text().strip() or self._guess_name(info)
            try:
                # 与「抓取界面」导入同一套合并规则：应用 + 模块 + 名称 命中就覆盖定位值，
                # 保留原 id（步骤是通过 element_id 引用元素的，重建 id 会让引用失效）
                stats = self.element_model.upsert_elements(
                    [(app, module, name, loc_type, loc_value, "")])
                self.result["library"] = {
                    "app": app, "module": module, "name": name, **stats}
            except Exception as e:
                self.result["library_error"] = str(e)

        self.accept()

    # ------------------------------------------------------------------
    def _set_hint(self, text, error=False):
        self.hint_label.setText(text)
        self.hint_label.setProperty("isError", "1" if error else "0")
        # 改 property 后要重新 polish 样式才会生效
        self.hint_label.style().unpolish(self.hint_label)
        self.hint_label.style().polish(self.hint_label)

    def _apply_theme(self):
        is_dark = Settings.get_theme_mode() == THEME_MODE_DARK
        if is_dark:
            bg, fg, sub = "#2d2d2d", "#eeeeee", "#a0a0a0"
            card_bg, card_border = "#3c3c3c", "#555555"
            btn_bg, btn_fg, btn_border = "#4a4a4a", "#eeeeee", "#5a5a5a"
            primary, primary_hover = "#1976d2", "#1565c0"
        else:
            bg, fg, sub = "#ffffff", "#1a1a1a", "#888888"
            card_bg, card_border = "#f8f9fa", "#e0e0e0"
            btn_bg, btn_fg, btn_border = "#f0f0f0", "#333333", "#d0d0d0"
            primary, primary_hover = "#1976d2", "#1565c0"

        self.setStyleSheet(f"""
            QDialog#ElementPickDialog {{ background-color: {bg}; }}
            QDialog#ElementPickDialog QWidget#PickLoadingOverlay {{ background-color: {bg}; }}
            QDialog#ElementPickDialog QLabel#PickLoadingLabel {{ color: {fg}; font-size: 14px; }}
            QDialog#ElementPickDialog QProgressBar#PickLoadingBar {{
                background-color: {card_bg};
                border: none;
                border-radius: 3px;
            }}
            QDialog#ElementPickDialog QProgressBar#PickLoadingBar::chunk {{
                background-color: {primary};
                border-radius: 3px;
            }}
            QDialog#ElementPickDialog QLabel {{ color: {fg}; background: transparent; }}
            QDialog#ElementPickDialog QLabel#PickDetail {{ color: {sub}; font-size: 12px; }}
            QDialog#ElementPickDialog QLabel#PickValue {{ font-weight: 600; }}
            QDialog#ElementPickDialog QLabel[isError="1"] {{ color: #e74c3c; }}
            QDialog#ElementPickDialog QFrame#PickLibBox {{
                background-color: {card_bg};
                border: 1px solid {card_border};
                border-radius: 8px;
            }}
            QDialog#ElementPickDialog QCheckBox {{ color: {fg}; }}
            QDialog#ElementPickDialog QLineEdit {{
                background-color: {bg};
                color: {fg};
                border: 1px solid {card_border};
                border-radius: 6px;
                padding: 4px 8px;
                font-size: 12px;
            }}
            QDialog#ElementPickDialog QLineEdit:focus {{ border-color: {primary}; }}
            QDialog#ElementPickDialog QFrame#PickPreview {{
                background-color: {card_bg};
                border: 1px solid {card_border};
                border-radius: 8px;
            }}
            QDialog#ElementPickDialog QPushButton {{
                background-color: {btn_bg};
                color: {btn_fg};
                border: 1px solid {btn_border};
                border-radius: 6px;
                padding: 6px 14px;
                font-size: 13px;
            }}
            QDialog#ElementPickDialog QPushButton:hover {{ border-color: {primary}; }}
            QDialog#ElementPickDialog QPushButton:disabled {{
                color: {sub};
                border-color: {card_border};
            }}
            /* 「刷新」和「确定」是同一套主按钮样式：两个都是主动作，看起来要一样 */
            QDialog#ElementPickDialog QPushButton#okBtn,
            QDialog#ElementPickDialog QPushButton#refreshBtn {{
                background-color: {primary};
                color: #ffffff;
                border: 1px solid {primary};
            }}
            QDialog#ElementPickDialog QPushButton#okBtn:hover,
            QDialog#ElementPickDialog QPushButton#refreshBtn:hover {{
                background-color: {primary_hover};
            }}
            QDialog#ElementPickDialog QPushButton#okBtn:disabled,
            QDialog#ElementPickDialog QPushButton#refreshBtn:disabled {{
                background-color: {card_border};
                border-color: {card_border};
                color: {sub};
            }}
        """)

        # 两个按钮等宽、都靠右：QSS 里的字号此刻才生效，所以按实际字宽定，
        # 不写死像素（高 DPI 下写死会把字裁掉）。
        self.refresh_btn.ensurePolished()
        self.ok_btn.ensurePolished()
        fm = self.ok_btn.fontMetrics()
        btn_w = max(fm.horizontalAdvance(self.refresh_btn.text()),
                    fm.horizontalAdvance(self.ok_btn.text())) + 46
        self.refresh_btn.setFixedWidth(btn_w)
        self.ok_btn.setFixedWidth(btn_w)
