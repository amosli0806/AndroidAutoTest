# controllers/step_controller.py
import traceback
import subprocess
import threading
import time
import re
import xml.etree.ElementTree as ET
import tempfile
import os
import sys
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QMessageBox, QDialog, QVBoxLayout, QFormLayout, QLineEdit, QComboBox, \
    QSpinBox, QDoubleSpinBox, QScrollArea, QWidget, QHBoxLayout, QToolButton, QLabel, QFrame, \
    QPushButton, QGraphicsDropShadowEffect, QListView, QCheckBox
from PyQt6.QtCore import QObject, Qt, pyqtSignal
from models.step_model import StepModel, Step
from utils.dialogs import WarningDialog, ConfirmDeleteDialog, ErrorDialog
from views.step_list_view import StepListView
from views.action_card_view import ActionCardView
from views.element_selector_dialog import ElementSelectorDialog
from utils.toast import show_toast
from utils.adb_path import get_adb_path
from utils.theme import ThemeMode
import qtawesome as qta


class StepController(QObject):
    steps_ready = pyqtSignal(list)
    # 录制生成的步骤预览信号：后台线程生成完 → 投递到 GUI 线程弹预览对话框
    steps_preview = pyqtSignal(list)

    FIELD_OPTIONS = {
        'locationType': ['资源ID', '坐标', '文本', '描述', 'XPath'],
        'fromLocationType': ['资源ID', '坐标', '文本', '描述', 'XPath'],
        'toLocationType': ['资源ID', '坐标', '文本', '描述', 'XPath'],
        'keyName': ['返回', '主页', '菜单', '音量+', '音量-', '电源', '相机'],
        'gestureType': ['捏合（缩小）', '放大'],
        'assert_type': ['元素存在', '元素不存在', '文本等于', '文本包含'],
    }

    ACTION_OPTIONS = {
        'app_mgr': ['启动应用', '停止应用', '重启应用', '清空应用数据', '安装应用', '卸载应用'],
        'screen_ctrl': ['唤醒屏幕', '熄灭屏幕', '旋转屏幕', '固定方向(竖屏)', '固定方向(横屏)'],
    }

    def __init__(self, step_model: StepModel, step_view: StepListView, action_view: ActionCardView):
        super().__init__()
        self.model = step_model
        self.step_view = step_view
        self.action_view = action_view
        self.element_controller = None
        self.current_case_id = None
        self.device_service = None

        self._recording = False
        self._recording_thread = None
        self._stop_event = None
        self._events = []
        self._screen_width = 1080
        self._screen_height = 1920

        # ---- 录制时异步反查控件（dump）相关 ----
        # 每个 down 事件会立即推入 _dump_queue，由独立线程消费
        self._dump_queue = None
        self._dump_stop = threading.Event()
        self._dump_thread = None
        self._dump_worker_alive = False
        self._element_cache = {}  # {(down_ts, x, y): elem_dict or None}
        self._element_cache_lock = threading.Lock()
        self._prefer_element_lookup = True  # 生成步骤时是否优先语义定位

        # ---- getevent 子进程引用（用于停止录制时强制终止）----
        self._getevent_process = None

        # ---- 车机多触摸屏：用户选定的目标触摸设备 ----
        # 结构: {'path': '/dev/input/eventN', 'name': str, 'max_x': int|None, 'max_y': int|None}
        self._selected_touch_device = None
        # 触摸量程缓存（从选定设备里取）
        self._touch_max_x = None
        self._touch_max_y = None

        self.steps_ready.connect(self._apply_steps_to_model)
        self.steps_preview.connect(self._on_steps_preview)

        self.step_view.step_dropped.connect(self._on_step_dropped)
        self.step_view.update_step.connect(self._on_update_step)
        self.step_view.delete_step.connect(self._on_delete_step)
        self.step_view.duplicate_step.connect(self._on_duplicate_step)
        self.action_view.add_step_signal.connect(self._on_add_step_from_card)

        self.refresh_steps()

    def apply_theme(self, theme_mode: ThemeMode):
        """应用主题到控制器及关联视图"""
        # 转发到步骤视图
        if self.step_view and hasattr(self.step_view, 'apply_theme'):
            self.step_view.apply_theme(theme_mode)
        # 转发到动作卡片视图（ActionCardView 本身没有 apply_theme，但其内部卡片由主题样式表控制）
        # 但为了保持一致性，如果将来需要，可以在这里添加

    def set_element_controller(self, controller):
        self.element_controller = controller
        self.action_view.set_element_controller(controller)
        self.step_view.set_element_controller(controller)

    def set_device_service(self, service):
        self.device_service = service

    def set_current_case(self, case_id: str):
        if case_id is not None and not self.model.get_steps_for_case(case_id) and case_id not in self.model.case_steps:
            pass
        self.current_case_id = case_id
        self.refresh_steps()
        self.step_view.scroll_to_top()
        main_window = self.step_view.window()
        if hasattr(main_window, '_update_record_button_state'):
            main_window._update_record_button_state()

    def refresh_steps(self):
        try:
            if self.current_case_id is None:
                self.step_view.set_steps([])
                return
            steps = self.model.get_steps_for_case(self.current_case_id)
            self.step_view.set_steps(steps)
            self.step_view._refresh_items()
            self.step_view.update()
            self.step_view.repaint()
        except Exception as e:
            self.step_view.set_steps([])

    def remove_case_steps(self, case_id: str):
        if case_id in self.model.case_steps:
            for sid in self.model.case_steps[case_id]:
                if sid in self.model._steps:
                    del self.model._steps[sid]
            del self.model.case_steps[case_id]
            self.model.save()

    def copy_steps(self, src_case_id, dst_case_id):
        self.model.copy_steps_for_case(src_case_id, dst_case_id)

    def _on_step_dropped(self, from_idx, to_idx):
        if self.current_case_id:
            self.model.move_step(self.current_case_id, from_idx, to_idx)
            self.refresh_steps()

    def _on_update_step(self, step_id):
        try:
            step = self.model._steps.get(step_id)
            if not step:
                WarningDialog.show_warning(self.step_view, "错误", "步骤不存在或已被删除")
                return

            dialog = QDialog(self.step_view)
            dialog.setObjectName("UpdateStepDialog")
            dialog.setWindowTitle("更新步骤")
            dialog.setFixedSize(560, 450)
            dialog.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
            dialog.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

            # 根据当前主题动态生成 QSS
            from utils.settings import Settings as _Settings, THEME_MODE_DARK as _TMD
            _is_dark_dlg = _Settings.get_theme_mode() == _TMD
            if _is_dark_dlg:
                dialog.setStyleSheet("""
                    #UpdateStepDialog QFrame#updateStepContainer {
                        background-color: #3c3c3c;
                        border-radius: 12px;
                        border: 1px solid #555;
                    }
                    #UpdateStepDialog QLabel {
                        color: #eee;
                        background: transparent;
                    }
                    #UpdateStepDialog QLineEdit,
                    #UpdateStepDialog QComboBox,
                    #UpdateStepDialog QSpinBox,
                    #UpdateStepDialog QDoubleSpinBox {
                        border: 1px solid #555;
                        border-radius: 6px;
                        padding: 6px 10px;
                        font-size: 14px;
                        background-color: #2d2d2d;
                        color: #eee;
                    }
                    #UpdateStepDialog QLineEdit:focus,
                    #UpdateStepDialog QComboBox:focus,
                    #UpdateStepDialog QSpinBox:focus,
                    #UpdateStepDialog QDoubleSpinBox:focus {
                        border-color: #90caf9;
                    }
                    #UpdateStepDialog QScrollArea,
                    #UpdateStepDialog QScrollArea > QWidget > QWidget {
                        background: transparent;
                        border: none;
                    }
                    #UpdateStepDialog QComboBox QAbstractItemView {
                        background-color: #3c3c3c;
                        color: #eee;
                        border: 1px solid #555;
                        selection-background-color: #1e3a5f;
                        selection-color: #eee;
                        outline: none;
                    }
                    #UpdateStepDialog QPushButton#cancelBtn {
                        background-color: #555;
                        color: #eee;
                        border: none;
                        border-radius: 6px;
                        font-size: 14px;
                        font-weight: 500;
                    }
                    #UpdateStepDialog QPushButton#cancelBtn:hover {
                        background-color: #666;
                    }
                """)
            else:
                dialog.setStyleSheet("""
                    #UpdateStepDialog QFrame#updateStepContainer {
                        background-color: white;
                        border-radius: 12px;
                        border: 1px solid #d0d0d0;
                    }
                    #UpdateStepDialog QLabel {
                        color: #333;
                        background: transparent;
                    }
                    #UpdateStepDialog QLineEdit,
                    #UpdateStepDialog QComboBox,
                    #UpdateStepDialog QSpinBox,
                    #UpdateStepDialog QDoubleSpinBox {
                        border: 1px solid #d0d0d0;
                        border-radius: 6px;
                        padding: 6px 10px;
                        font-size: 14px;
                        background-color: white;
                        color: #333;
                    }
                    #UpdateStepDialog QLineEdit:focus,
                    #UpdateStepDialog QComboBox:focus,
                    #UpdateStepDialog QSpinBox:focus,
                    #UpdateStepDialog QDoubleSpinBox:focus {
                        border-color: #1976d2;
                    }
                    #UpdateStepDialog QScrollArea,
                    #UpdateStepDialog QScrollArea > QWidget > QWidget {
                        background: transparent;
                        border: none;
                    }
                    #UpdateStepDialog QComboBox QAbstractItemView {
                        background-color: white;
                        color: #333;
                        border: 1px solid #d0d0d0;
                        selection-background-color: #1976d2;
                        selection-color: white;
                        outline: none;
                    }
                    #UpdateStepDialog QPushButton#cancelBtn {
                        background-color: #f0f0f0;
                        color: #333;
                        border: none;
                        border-radius: 6px;
                        font-size: 14px;
                        font-weight: 500;
                    }
                    #UpdateStepDialog QPushButton#cancelBtn:hover {
                        background-color: #e0e0e0;
                    }
                """)

            main_layout = QVBoxLayout(dialog)
            main_layout.setContentsMargins(10, 10, 10, 10)
            main_layout.setSpacing(0)

            container = QFrame()
            container.setObjectName("updateStepContainer")
            # 样式由 dialog 层的 #UpdateStepDialog QSS 统一控制
            shadow = QGraphicsDropShadowEffect()
            shadow.setBlurRadius(20)
            shadow.setOffset(0, 0)
            shadow.setColor(QColor(0, 0, 0, 80))
            container.setGraphicsEffect(shadow)

            container_layout = QVBoxLayout(container)
            container_layout.setContentsMargins(24, 20, 24, 20)
            container_layout.setSpacing(12)

            title_label = QLabel("更新步骤")
            title_label.setStyleSheet("font-size: 16px; font-weight: bold;")
            container_layout.addWidget(title_label)

            line = QFrame()
            line.setFrameShape(QFrame.Shape.HLine)
            _line_color = "#555" if _is_dark_dlg else "#e0e0e0"
            line.setStyleSheet(f"background-color: {_line_color}; max-height: 1px;")
            container_layout.addWidget(line)

            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QScrollArea.Shape.NoFrame)
            # 背景由 #UpdateStepDialog QSS 控制
            container_layout.addWidget(scroll)

            form_widget = QWidget()
            # 背景由 #UpdateStepDialog QSS 控制
            scroll.setWidget(form_widget)
            form = QFormLayout(form_widget)
            form.setSpacing(10)
            form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

            name_edit = QLineEdit(step.name)
            name_edit.setMinimumWidth(200)
            name_edit.setPlaceholderText("请输入步骤名称（可选）")
            # 样式由 #UpdateStepDialog QSS 控制
            form.addRow("步骤名称:", name_edit)

            label_map = {
                'locationType': '定位方式', 'locationValue': '定位值', 'duration': '时长(秒)',
                'text': '输入文字', 'longPressMs': '长按毫秒', 'startX': '起始X', 'startY': '起始Y',
                'endX': '结束X', 'endY': '结束Y', 'keyword': '搜索关键字',
                'fromLocationType': '源定位方式', 'fromValue': '源定位值',
                'toLocationType': '目标定位方式', 'toValue': '目标定位值',
                'direction': '方向', 'points': '路径点', 'durationMs': '总时长(毫秒)',
                'gestureType': '手势类型', 'centerX': '中心点X', 'centerY': '中心点Y',
                'scale': '缩放比例', 'distance': '飞掠距离(像素)', 'velocity': '速度(像素/秒)',
                'sequenceId': '手势序列编号', 'description': '手势描述',
                'keyName': '按键名称', 'action': '操作', 'packageName': '包名/应用名',
                'apkPath': '安装包路径', 'savePath': '保存路径', 'fileName': '文件名前缀',
                'assert_type': '断言类型', 'expected_value': '预期值', 'timeout': '超时(秒)',
                'element_id': '元素ID',
                'instance': '实例序号',
                'fallbackType': '备用定位方式', 'fallbackValue': '备用定位值',
                'voiceText': '播报文案', 'afterDelay': '播后等待(秒)'
            }

            # 各字段控件的样式由 dialog 层的 #UpdateStepDialog QSS 统一控制
            line_style = ""
            combo_style = ""
            spin_style = ""

            param_widgets = {}
            loc_type_widgets = {}
            loc_value_widgets = {}

            # 内部字段：不在 UI 上展示，提交时透传
            INTERNAL_KEYS = {
                'screenWidth', 'screenHeight',
                'normalizedX', 'normalizedY',
                'normalizedStartX', 'normalizedStartY',
                'normalizedEndX', 'normalizedEndY',
            }

            for key, value in step.params.items():
                if key in INTERNAL_KEYS:
                    continue
                widget = None
                if key in ('locationType', 'fromLocationType', 'toLocationType', 'fallbackType'):
                    widget = QComboBox()
                    # fallbackType（备用定位方式）复用 locationType 的选项集
                    widget.addItems(self.FIELD_OPTIONS.get(key, self.FIELD_OPTIONS['locationType']))
                    index = widget.findText(str(value))
                    if index >= 0:
                        widget.setCurrentIndex(index)
                    widget.setStyleSheet(combo_style)
                    if key != 'fallbackType':
                        loc_type_widgets[key] = widget
                elif key == 'action':
                    widget = QComboBox()
                    if step.type in self.ACTION_OPTIONS:
                        options = self.ACTION_OPTIONS[step.type]
                    else:
                        options = []
                    if options:
                        widget.addItems(options)
                        index = widget.findText(str(value))
                        if index >= 0:
                            widget.setCurrentIndex(index)
                        widget.setStyleSheet(combo_style)
                    else:
                        widget = QLineEdit(str(value))
                        widget.setStyleSheet(line_style)
                elif key == 'assert_type':
                    widget = QComboBox()
                    widget.addItems(self.FIELD_OPTIONS['assert_type'])
                    index = widget.findText(str(value))
                    if index >= 0:
                        widget.setCurrentIndex(index)
                    widget.setStyleSheet(combo_style)
                elif key in self.FIELD_OPTIONS:
                    widget = QComboBox()
                    options = self.FIELD_OPTIONS[key]
                    widget.addItems(options)
                    index = widget.findText(str(value))
                    if index >= 0:
                        widget.setCurrentIndex(index)
                    widget.setStyleSheet(combo_style)
                elif key == 'direction':
                    if step.type == 'flick':
                        options = ['上', '下', '左', '右']
                    else:
                        options = ['上滑', '下滑', '左滑', '右滑', '自定义坐标']
                    widget = QComboBox()
                    widget.addItems(options)
                    index = widget.findText(str(value))
                    if index >= 0:
                        widget.setCurrentIndex(index)
                    widget.setStyleSheet(combo_style)
                elif key in ('locationValue', 'fromValue', 'toValue'):
                    widget = QLineEdit(str(value))
                    widget.setStyleSheet(line_style)
                    loc_value_widgets[key] = widget
                    container_layout_widget = QHBoxLayout()
                    container_layout_widget.addWidget(widget)
                    select_btn = QToolButton()
                    # 图标颜色跟随弹窗主题（暗色弹窗上深灰图标看不清，与动作卡片同规则）
                    _sel_icon_color = "#bbbbbb" if _is_dark_dlg else "#555555"
                    select_btn.setIcon(qta.icon('fa6s.folder-open', color=_sel_icon_color))
                    select_btn.setFixedSize(24, 24)
                    select_btn.setStyleSheet("border: none; background: transparent;")
                    select_btn.setToolTip("从元素库选择")
                    loc_type_key = key.replace('Value', 'Type')
                    loc_type_widget = loc_type_widgets.get(loc_type_key)
                    select_btn.setProperty('loc_type_widget', loc_type_widget)
                    select_btn.setProperty('value_widget', widget)
                    select_btn.clicked.connect(self._on_update_select_element)
                    container_layout_widget.addWidget(select_btn)
                    widget = container_layout_widget
                elif isinstance(value, int):
                    widget = QSpinBox()
                    widget.setRange(-999999, 999999)
                    widget.setValue(value)
                    widget.setStyleSheet(spin_style)
                elif isinstance(value, float):
                    widget = QDoubleSpinBox()
                    widget.setRange(-999999.0, 999999.0)
                    widget.setValue(value)
                    widget.setStyleSheet(spin_style)
                else:
                    widget = QLineEdit(str(value))
                    widget.setStyleSheet(line_style)

                if key not in param_widgets:
                    param_widgets[key] = widget

                label_text = label_map.get(key, key)
                # 技术字段加 tooltip 说明用途（录制生成的定位增强字段）
                if key == 'instance':
                    widget.setToolTip("资源ID 在当前页面有重复时的序号（从 0 开始），一般保持录制值即可")
                elif key == 'fallbackType':
                    widget.setToolTip("备用定位方式：主定位（资源ID）失效时自动改用此方式重试")
                elif key == 'fallbackValue':
                    widget.setToolTip("备用定位值：与备用定位方式配套使用")
                form.addRow(f"{label_text}:", widget)

            # 元素操作步骤（点击/双击/长按/输入）补一个「超时(秒)」控件：
            # 老步骤/录制步骤的 params 里没有 timeout 字段，遍历时不会生成该控件，
            # 但执行端默认会等 10 秒。这里补上，让用户编辑老步骤时也能看到并调整。
            _ELEMENT_ACTION_TYPES = ('click', 'double_click', 'long_press', 'input')
            if step.type in _ELEMENT_ACTION_TYPES and 'timeout' not in step.params:
                timeout_spin = QSpinBox()
                timeout_spin.setRange(0, 999999)
                timeout_spin.setValue(10)          # 与执行端 DEFAULT_ELEMENT_WAIT_TIMEOUT 一致
                timeout_spin.setStyleSheet(spin_style)
                param_widgets['timeout'] = timeout_spin
                form.addRow("超时(秒):", timeout_spin)

            btn_layout = QHBoxLayout()
            btn_layout.setSpacing(12)

            cancel_btn = QPushButton("取消")
            cancel_btn.setObjectName("cancelBtn")
            cancel_btn.setFixedSize(100, 34)
            # 样式由 #UpdateStepDialog QPushButton#cancelBtn QSS 控制
            cancel_btn.clicked.connect(dialog.reject)

            ok_btn = QPushButton("确定")
            ok_btn.setFixedSize(100, 34)
            ok_btn.setStyleSheet("""
                QPushButton {
                    background-color: #1976d2;
                    color: white;
                    border: none;
                    border-radius: 6px;
                    font-size: 14px;
                    font-weight: 600;
                }
                QPushButton:hover { background-color: #1565c0; }
                QPushButton:pressed { background-color: #0d47a1; }
            """)
            ok_btn.clicked.connect(dialog.accept)

            btn_layout.addStretch()
            btn_layout.addWidget(cancel_btn)
            btn_layout.addWidget(ok_btn)
            container_layout.addLayout(btn_layout)

            main_layout.addWidget(container)

            if dialog.exec() == QDialog.DialogCode.Accepted:
                new_name = name_edit.text().strip()
                new_params = {}
                element_id = None

                # 透传原有的内部字段（UI 里没展示，避免被覆盖丢失）
                for _k in ('screenWidth', 'screenHeight',
                           'normalizedX', 'normalizedY',
                           'normalizedStartX', 'normalizedStartY',
                           'normalizedEndX', 'normalizedEndY'):
                    if _k in step.params:
                        new_params[_k] = step.params[_k]

                for key, widget in param_widgets.items():
                    if isinstance(widget, QHBoxLayout):
                        item = widget.itemAt(0)
                        if item:
                            w = item.widget()
                            if w and hasattr(w, 'property'):
                                elem_id = w.property('element_id')
                                if elem_id:
                                    element_id = elem_id
                            if isinstance(w, QLineEdit):
                                new_params[key] = w.text()
                            else:
                                continue
                        else:
                            continue
                    elif isinstance(widget, QComboBox):
                        new_params[key] = widget.currentText()
                    elif isinstance(widget, QLineEdit):
                        new_params[key] = widget.text()
                    elif isinstance(widget, QSpinBox):
                        new_params[key] = widget.value()
                    elif isinstance(widget, QDoubleSpinBox):
                        new_params[key] = widget.value()
                    else:
                        pass

                if element_id:
                    new_params['element_id'] = element_id

                if 'element_id' in new_params and self.element_controller:
                    elem_id = new_params['element_id']
                    elem = self.element_controller.get_element_by_id(elem_id)
                    if elem:
                        loc_type_key = None
                        for k in ('locationType', 'fromLocationType', 'toLocationType'):
                            if k in new_params:
                                loc_type_key = k
                                break
                        if loc_type_key:
                            loc_value_key = loc_type_key.replace('Type', 'Value')
                            if (new_params.get(loc_type_key) != elem.loc_type or
                                    new_params.get(loc_value_key) != elem.loc_value):
                                del new_params['element_id']
                        else:
                            pass
                    else:
                        del new_params['element_id']

                self.model.update_step(step_id, name=new_name, params=new_params)
                self.refresh_steps()

        except Exception as e:
            ErrorDialog.show_error(self.step_view, "更新步骤错误",
                                 f"更新步骤时发生错误:\n{str(e)}\n\n详情请查看控制台输出。")
            traceback.print_exc()

    def _on_update_select_element(self):
        if not self.element_controller:
            WarningDialog.show_warning(self.step_view, "提示", "元素库未初始化")
            return

        btn = self.sender()
        if not btn:
            return
        loc_type_widget = btn.property('loc_type_widget')
        value_widget = btn.property('value_widget')
        if not loc_type_widget or not value_widget:
            return

        dialog = ElementSelectorDialog(self.element_controller.element_model, self.step_view)
        dialog.element_selected.connect(
            lambda elem: self._apply_selected_element(elem, loc_type_widget, value_widget)
        )
        dialog.exec()

    def _apply_selected_element(self, elem, loc_type_widget, value_widget):
        index = loc_type_widget.findText(elem.loc_type)
        if index >= 0:
            loc_type_widget.setCurrentIndex(index)
        value_widget.setText(elem.loc_value)
        value_widget.setProperty('element_id', elem.id)

    def _on_delete_step(self, step_id):
        step = self.model._steps.get(step_id)
        if not step:
            return

        # 处理步骤名称为空的情况
        step_name = step.name if step.name else "此步骤"
        message = f"确定要删除步骤 '{step_name}' 吗？"
        if not step.name:
            message = "确定要删除此步骤吗？"

        # 弹出美观确认对话框
        if not ConfirmDeleteDialog.ask(
                self.step_view,
                title="确认删除",
                message=message,
                detail="删除后该步骤将不可恢复。"
        ):
            return

        self.model.remove_step(step_id, self.current_case_id)
        self.refresh_steps()

    def _on_duplicate_step(self, step_id):
        if self.current_case_id:
            new_step = self.model.duplicate_step(step_id, self.current_case_id)
            if new_step:
                self.refresh_steps()

    def _on_add_step_from_card(self, action_type, params, name):
        if self.current_case_id is None:
            show_toast(message="请先在项目树中选择一个用例")
            return
        step = Step(id=-1, type=action_type, name=name, params=params)
        self.model.add_step_to_case(self.current_case_id, step)
        self.refresh_steps()

    # ========== 录制功能 ==========
    def toggle_recording(self):
        if not self.device_service:
            show_toast(message="设备服务未初始化")
            return

        # 快速检查设备是否在线
        if not self.device_service.check_device_online():
            show_toast(message="设备未连接")
            return

        if self.device_service.device is None:
            if self.device_service.serial:
                try:
                    self.device_service.connect(self.device_service.serial)
                    if self.device_service.device is None:
                        show_toast(message="设备连接失败")
                        return
                except Exception:
                    show_toast(message="设备连接失败")
                    return
            else:
                show_toast(message="请先选择设备")
                return

        if not self.current_case_id:
            show_toast(message="请先在项目树中选择一个用例")
            return

        if not self._recording:
            self._start_recording()
        else:
            self._stop_recording()

    def _start_recording(self):
        # ---- 防御：若上一次录制线程仍在跑（join 超时残留），强制清理 ----
        if self._recording_thread and self._recording_thread.is_alive():
            # 先杀旧 getevent 子进程，让 readline 立刻返回
            old_proc = getattr(self, '_getevent_process', None)
            if old_proc is not None:
                try:
                    old_proc.terminate()
                except Exception:
                    pass
                try:
                    old_proc.wait(timeout=1)
                except Exception:
                    try:
                        old_proc.kill()
                    except Exception:
                        pass
            # 强制等旧线程退出
            self._recording_thread.join(timeout=3)
            # 若还活着，说明 readline 完全卡死了，只能靠 daemon 在进程退出时回收

        # ---- 探测触摸设备（车机可能有多块屏）----
        devices = self._detect_touch_devices()
        if not devices:
            show_toast(message="未检测到触摸设备，无法录制")
            return

        if len(devices) == 1:
            self._selected_touch_device = devices[0]
        else:
            chosen = self._ask_touch_device(devices)
            if chosen is None:
                return  # 用户取消
            self._selected_touch_device = chosen

        # 把量程缓存到 self，供 _raw_to_screen 使用
        self._touch_max_x = self._selected_touch_device.get('max_x')
        self._touch_max_y = self._selected_touch_device.get('max_y')

        self._recording = True
        self._events = []
        self._stop_event = threading.Event()
        self._getevent_process = None  # 重置进程引用

        # ---- 初始化 dump 队列和缓存 ----
        import queue as _queue
        self._dump_queue = _queue.Queue()
        self._dump_stop = threading.Event()
        self._element_cache = {}
        self._element_cache_lock = threading.Lock()

        # ---- 先起 dump worker（保证第一个 down 就能被消费）----
        self._dump_worker_alive = True
        self._dump_thread = threading.Thread(
            target=self._dump_worker, daemon=True, name="u2-dump-worker"
        )
        self._dump_thread.start()

        # ---- 再起录制线程 ----
        self._recording_thread = threading.Thread(
            target=self._record_loop, daemon=True, name="recorder"
        )
        self._recording_thread.start()

        self.step_view.set_recording_state(True)
        main_window = self.step_view.window()
        if hasattr(main_window, '_update_record_button_state'):
            main_window._update_record_button_state()

    def _dump_worker(self):
        """
        独立线程：从 _dump_queue 取 (ts, x, y)，先等页面稳定再 dump 反查。
        - 由 up 事件（tap）喂数据，无轮询开销
        - 反查前先 sleep 一小段，等点击触发的动画/路由切换稳定下来，
          提高 dump 到的 UI 与用户实际点击目标的一致性
        - dump 失败时缓存 None，生成步骤时退化为坐标
        """
        import queue as _queue

        while self._dump_worker_alive:
            try:
                item = self._dump_queue.get(timeout=0.2)
            except _queue.Empty:
                if self._dump_stop.is_set():
                    break
                continue

            if item is None:                       # 哨兵，通知退出
                break

            ts, x, y = item
            # 页面稳定等待：点击后 UI 可能还在动（动画、列表刷新、路由切换），
            # 稍等片刻让界面落定，再 dump 反查才准。等的是这段 sleep，不阻塞录制主循环。
            # 若队列里还有待处理项（用户连续快速点击），跳过 sleep —— 排队时间已自然
            # 覆盖了稳定等待，再睡只会让整体反查更慢。
            if self._dump_queue.qsize() == 0:
                time.sleep(0.35)
            print(f"[dump worker] 开始反查 ({x},{y})，队列剩余 {self._dump_queue.qsize()}")
            try:
                elem = self._get_element_at(int(x), int(y))
            except Exception as e:
                print(f"[dump worker] ({x},{y}) 反查异常: {e}")
                elem = None
            print(f"[dump worker] ({x},{y}) 反查结果: {elem}")

            with self._element_cache_lock:
                self._element_cache[(ts, int(x), int(y))] = elem

        self._dump_worker_alive = False

    def _record_loop(self):
        # ---- 获取屏幕分辨率（优先 Override size）----
        try:
            size_output = self.device_service.device.shell("wm size")
            out_str = size_output
            if hasattr(size_output, 'output'):
                out_str = size_output.output or ''
            out_str = str(out_str)

            # ★ 优先 Override（车机/投屏场景常见）
            m = re.search(r'Override size:\s*(\d+)x(\d+)', out_str)
            if not m:
                m = re.search(r'Physical size:\s*(\d+)x(\d+)', out_str)
            if not m:
                m = re.search(r'(\d+)x(\d+)', out_str)

            if m:
                self._screen_width = int(m.group(1))
                self._screen_height = int(m.group(2))
            else:
                self._screen_width, self._screen_height = 1080, 1920
        except Exception:
            self._screen_width, self._screen_height = 1080, 1920

        # ---- 诊断日志 ----
        orientation = "横屏" if self._screen_width > self._screen_height else "竖屏"
        print(f"[录制] 屏幕: {self._screen_width}x{self._screen_height} ({orientation})")

        dev = self._selected_touch_device or {}
        print(f"[录制] 使用触摸设备: {dev.get('path')} ({dev.get('name')})")
        print(f"[录制] 触摸量程: max_x={self._touch_max_x}, max_y={self._touch_max_y}")

        # ---- 量程-分辨率一致性校验（车机上很重要）----
        if self._touch_max_x and self._touch_max_y and self._screen_width and self._screen_height:
            ratio_x = self._touch_max_x / self._screen_width
            ratio_y = self._touch_max_y / self._screen_height
            if ratio_x > 0 and ratio_y > 0:
                drift = abs(ratio_x - ratio_y) / max(ratio_x, ratio_y)
                if drift > 0.2:
                    print(f"[录制] ⚠ 量程比例异常: X缩放={ratio_x:.2f}, Y缩放={ratio_y:.2f}, "
                          f"偏差 {drift * 100:.1f}% —— 可能探测到了错误的触摸设备")
                else:
                    print(f"[录制] 量程比例正常: 触摸插值倍数 ≈ {ratio_x:.2f}")

        # 兼容两种 getevent 输出格式：
        #   指定设备:  [ 12.345678] 0003 0035 00000f3c
        #   不指定设备: [ 12.345678] /dev/input/event0: 0003 0035 00000f3c
        event_pattern = re.compile(
            r'\[\s*([\d.]+)\]\s+'
            r'(?:[^\s:]+:\s+)?'  # 可选的 "devicePath: " 前缀
            r'([0-9a-f]{4})\s+'
            r'([0-9a-f]{4})\s+'
            r'([0-9a-f]+)'
        )

        # ★ 只监听选定的触摸设备（车机多屏场景关键）
        device_path = dev.get('path')
        if device_path:
            cmd = [get_adb_path(), 'shell', 'getevent', '-t', device_path]
        else:
            cmd = [get_adb_path(), 'shell', 'getevent', '-t']

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # ★ 合并到 stdout，方便看错误
            text=True,
            bufsize=1,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        )

        # 保存引用，停止录制时可强制终止
        self._getevent_process = process

        # ============ 以下是主循环（上一轮漏掉的部分）============
        touch_down = False
        current_x = 0
        current_y = 0
        down_time = 0
        down_x = 0
        down_y = 0

        # 调试统计
        _read_lines = 0
        _matched_lines = 0
        _sample_printed = 0

        while not self._stop_event.is_set():
            line = process.stdout.readline()
            if not line:
                if process.poll() is not None:
                    break
                else:
                    continue
            line = line.strip()
            if not line:
                continue
            if 'add device' in line:
                continue

            _read_lines += 1
            # 打印前 5 条原始行，便于核对格式
            if _sample_printed < 5:
                print(f"[录制] 原始行[{_sample_printed}]: {line!r}")
                _sample_printed += 1

            match = event_pattern.match(line)
            if not match:
                continue

            _matched_lines += 1

            timestamp = float(match.group(1))
            ev_type = int(match.group(2), 16)
            ev_code = int(match.group(3), 16)
            ev_value = int(match.group(4), 16)

            if ev_type == 3:
                if ev_code == 0x35:
                    current_x = ev_value
                elif ev_code == 0x36:
                    current_y = ev_value

            elif ev_type == 1 and ev_code == 330:
                if ev_value == 1 and not touch_down:
                    # ========== down 事件 ==========
                    touch_down = True
                    down_time = timestamp

                    # ★ 把触摸屏原始坐标换算为屏幕坐标
                    screen_x, screen_y = self._raw_to_screen(current_x, current_y)
                    down_x = screen_x
                    down_y = screen_y

                    self._events.append({
                        'type': 'down',
                        'x': screen_x,
                        'y': screen_y,
                        'timestamp': timestamp
                    })

                    # 不再在 down 瞬间 dump：点击会触发页面动画/路由切换，
                    # 此时 dump 到的可能是点击前的旧 UI，反查出的控件对应不上。
                    # 改为 up 后、页面稳定了再反查（见 up 分支）。

                elif ev_value == 0 and touch_down:
                    # ========== up 事件 ==========
                    touch_down = False
                    screen_x, screen_y = self._raw_to_screen(current_x, current_y)

                    self._events.append({
                        'type': 'up',
                        'x': screen_x,
                        'y': screen_y,
                        'timestamp': timestamp
                    })

                    # swipe 判定（用换算后的屏幕坐标）
                    dx = screen_x - down_x
                    dy = screen_y - down_y
                    is_swipe = (dx * dx + dy * dy) ** 0.5 >= 80
                    if is_swipe:
                        # 滑动不反查（起点坐标会被置 None，生成步骤时退化为坐标滑动）
                        with self._element_cache_lock:
                            key = (down_time, int(down_x), int(down_y))
                            if key in self._element_cache:
                                self._element_cache[key] = None
                    else:
                        # tap：抬手后才反查。dump worker 拿到后会先等页面稳定再 dump
                        if self._dump_queue is not None:
                            try:
                                self._dump_queue.put_nowait(
                                    (down_time, down_x, down_y)
                                )
                            except Exception:
                                pass

        print(f"[录制] 循环退出：读到 {_read_lines} 行，正则匹配 {_matched_lines} 行")

        try:
            process.terminate()
        except Exception:
            pass
        try:
            process.wait(timeout=2)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    def _detect_touch_devices(self):
        """
        解析 getevent -p 输出，返回所有触摸设备的列表。

        返回: [
            {'path': '/dev/input/event3', 'name': 'input_mt_wrapper',
             'max_x': 8671, 'max_y': 19295, 'has_prop_direct': True},
            ...
        ]

        识别条件（满足任一即可）：
        - input props 里含 INPUT_PROP_DIRECT（标准触摸屏）
        - 同时有 ABS_MT_POSITION_X (0x35) 和 Y (0x36) 的 max 值
        - 设备名含 touch / mt_wrapper / input_mt（车机常见命名）

        注意：getevent -p 里 ABS 事件用 16 进制码表示，0035=X, 0036=Y。
        """
        try:
            result = subprocess.run(
                [get_adb_path(), 'shell', 'getevent', '-p'],
                capture_output=True, text=True, timeout=8,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            )
            out = result.stdout or ""
        except Exception as e:
            print(f"[录制] 探测触摸设备失败: {e}")
            return []

        if not out.strip():
            print("[录制] getevent -p 无输出")
            return []

        # 按 "add device N: /dev/input/eventX" 切块
        blocks = re.split(
            r'(?=^add device\s+\d+:\s*/dev/input/event\d+)',
            out, flags=re.MULTILINE
        )

        devices = []
        for block in blocks:
            block = block.strip()
            if not block:
                continue

            m = re.match(r'add device\s+\d+:\s*(/dev/input/\S+)', block)
            if not m:
                continue
            path = m.group(1)

            # 提取 name
            name = ''
            nm = re.search(r'name:\s*"([^"]*)"', block)
            if nm:
                name = nm.group(1)

            # 提取 max_x / max_y（16 进制码 0035/0036）
            max_x = None
            max_y = None
            for line in block.splitlines():
                m2 = re.match(
                    r'\s*(0035|0036)\s*:\s*value\s+\S+,\s*min\s+\S+,\s*max\s+(-?\d+)',
                    line
                )
                if m2:
                    code = m2.group(1)
                    val = int(m2.group(2))
                    if code == '0035':
                        max_x = val
                    else:
                        max_y = val
                    continue
                # 兜底：部分 ROM 输出带名字
                if 'ABS_MT_POSITION_X' in line and max_x is None:
                    m3 = re.search(r'\bmax\s+(-?\d+)', line)
                    if m3:
                        max_x = int(m3.group(1))
                elif 'ABS_MT_POSITION_Y' in line and max_y is None:
                    m3 = re.search(r'\bmax\s+(-?\d+)', line)
                    if m3:
                        max_y = int(m3.group(1))

            has_prop_direct = 'INPUT_PROP_DIRECT' in block

            # 识别条件
            name_lower = name.lower()
            name_hit = any(k in name_lower for k in ('touch', 'mt_wrapper', 'input_mt'))
            abs_hit = (max_x is not None and max_y is not None
                       and max_x > 0 and max_y > 0)
            is_touch = has_prop_direct or abs_hit or name_hit

            if is_touch:
                devices.append({
                    'path': path,
                    'name': name or path,
                    'max_x': max_x,
                    'max_y': max_y,
                    'has_prop_direct': has_prop_direct,
                })

        print(f"[录制] 探测到 {len(devices)} 个触摸设备:")
        for d in devices:
            print(f"  - {d['path']} ({d['name']}) "
                  f"max_x={d['max_x']} max_y={d['max_y']} "
                  f"direct={d['has_prop_direct']}")
        return devices

    def _ask_touch_device(self, devices):
        """
        多个触摸设备时，弹窗让用户选择。
        返回选中的设备 dict；用户取消返回 None。
        """
        from PyQt6.QtWidgets import QInputDialog

        items = []
        for d in devices:
            label = f"{d['name']}  ({d['path']})"
            if d['max_x'] and d['max_y']:
                label += f"  [{d['max_x']}×{d['max_y']}]"
            if d['has_prop_direct']:
                label += "  ✓"
            items.append(label)

        item, ok = QInputDialog.getItem(
            self.step_view,
            "选择触摸设备",
            f"检测到 {len(devices)} 个触摸设备，请选择要录制的屏幕：\n"
            f"（带 ✓ 的是标准触摸屏，推荐优先选）",
            items, 0, False
        )
        if not ok or not item:
            return None
        idx = items.index(item)
        return devices[idx]

    def _raw_to_screen(self, raw_x, raw_y):
        """把触摸屏原始坐标换算为屏幕坐标。量程未知时原样返回。"""
        mx = getattr(self, '_touch_max_x', None)
        my = getattr(self, '_touch_max_y', None)

        # 车机兼容：量程异常（为 None / <= 0 / 明显不合理）时不做换算
        if not mx or not my or mx <= 0 or my <= 0:
            return raw_x, raw_y

        # 量程和屏幕分辨率几乎 1:1 时直接返回（车机上常见），避免浮点误差
        if (mx == self._screen_width - 1 and my == self._screen_height - 1):
            return raw_x, raw_y

        sx = int(round(raw_x / mx * self._screen_width))
        sy = int(round(raw_y / my * self._screen_height))

        # 越界保护：换算后如果超出屏幕像素索引范围，说明量程探测可能错了
        # 注意：像素索引有效范围是 [0, screen_width - 1]，
        # 所以判断要用 >= 而不是 >，否则 sx == screen_width 会漏网
        if sx < 0 or sx >= self._screen_width or sy < 0 or sy >= self._screen_height:
            print(f"[录制] ⚠ 换算越界: raw=({raw_x},{raw_y}) → screen=({sx},{sy}), "
                  f"量程=({mx},{my}), 分辨率=({self._screen_width},{self._screen_height})")
            # 越界时钳制到有效范围，而不是保留原始值
            sx = max(0, min(sx, self._screen_width - 1))
            sy = max(0, min(sy, self._screen_height - 1))

        return sx, sy

    def _stop_recording(self):
        # ---- 1. 停录制线程 ----
        self._stop_event.set()

        # ★ 关键：杀掉 getevent 子进程，让 _record_loop 里的 readline 收到 EOF 返回，
        #   否则 join 会一直超时，线程残留，导致下次录制多线程叠加
        proc = getattr(self, '_getevent_process', None)
        if proc is not None:
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                proc.wait(timeout=1)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            self._getevent_process = None

        # 现在线程应该能正常退出了，join 基本瞬间返回
        if self._recording_thread and self._recording_thread.is_alive():
            self._recording_thread.join(timeout=3)
        self._recording = False

        # ---- 2. 停 dump worker，等队列消化完 ----
        self._dump_stop.set()
        if self._dump_thread and self._dump_thread.is_alive():
            # 按剩余任务数估算等待时长；每个 dump 约 0.3~0.8s
            remain = self._dump_queue.qsize() if self._dump_queue else 0
            max_wait = min(30.0, max(3.0, remain * 1.0))
            self._dump_thread.join(timeout=max_wait)

            # 超时兜底：强制喂哨兵并标记退出
            if self._dump_thread.is_alive():
                self._dump_worker_alive = False
                try:
                    self._dump_queue.put_nowait(None)
                except Exception:
                    pass
                self._dump_thread.join(timeout=2)

        self.step_view.set_recording_state(False)

        events_copy = self._events.copy()
        self._events = []

        main_window = self.step_view.window()
        if hasattr(main_window, '_update_record_button_state'):
            main_window._update_record_button_state()

        if not events_copy:
            show_toast(message="未录制到操作")
            return

        # ---- 3. 询问用户是否要反查控件（自定义样式，跟随主题）----
        from utils.dialogs import QuestionDialog
        tap_count = sum(1 for e in events_copy if e['type'] == 'down')
        self._prefer_element_lookup = QuestionDialog.ask(
            self.step_view,
            title="录制完成",
            message=f"共录制到 {tap_count} 次按下操作。\n是否自动反查控件（生成资源ID/文本定位）？",
            detail="是：更稳定，但生成步骤时会稍慢；否：只生成坐标步骤，速度快",
            yes_text="反查",
            no_text="不反查",
            default_yes=True,
        )

        # ---- 4. 异步生成步骤 ----
        # 提示用户正在生成（反查 + 生成需要几秒，没有反馈会像卡死）
        show_toast(message=f"正在生成步骤（{tap_count} 次点击反查中），请稍候…", duration=3000)

        def process_events():
            try:
                steps = self._build_steps_from_events(events_copy)
                self.steps_preview.emit(steps)
            except Exception:
                import traceback
                traceback.print_exc()
                self.steps_preview.emit([])

        threading.Thread(target=process_events, daemon=True).start()

    def _build_steps_from_events(self, events):
        gestures = self._detect_gestures(events)
        steps = []
        prefer_element = getattr(self, '_prefer_element_lookup', True)

        # ---------- 从异步缓存取元素，miss 时同步补一次 ----------
        def get_element_for_gesture(g):
            if not prefer_element:
                return None

            key = (g['down_time'], int(g['start_x']), int(g['start_y']))

            with self._element_cache_lock:
                if key in self._element_cache:
                    return self._element_cache[key]

            # 缓存 miss（极少见：down 事件被丢弃 / worker 被杀）
            try:
                elem = self._get_element_at(int(g['start_x']), int(g['start_y']))
            except Exception:
                elem = None

            with self._element_cache_lock:
                self._element_cache[key] = elem
            return elem

        def make_loc_params(x, y, gesture=None):
            """优先语义定位（资源ID/文本/描述），坐标兜底。
            所有分支都带上归一化坐标，回放时可按当前分辨率自适应。"""
            cur_w = self._screen_width or 1080
            cur_h = self._screen_height or 1920
            norm_x = round(int(x) / cur_w, 4)
            norm_y = round(int(y) / cur_h, 4)

            base = {
                'screenWidth': self._screen_width,
                'screenHeight': self._screen_height,
                'normalizedX': norm_x,
                'normalizedY': norm_y,
            }

            if prefer_element and gesture is not None:
                elem = get_element_for_gesture(gesture)
                if elem:
                    rid = (elem.get('resourceId') or '').strip()
                    text = (elem.get('text') or '').strip()
                    desc = (elem.get('description') or '').strip()

                    # 优先级：资源ID > 文本 > 描述
                    if rid:
                        params = dict(base, locationType='资源ID', locationValue=rid)
                        # 资源ID 有重复时带上实例序号，回放时精确定位到第 N 个
                        if (elem.get('resourceIdCount') or 1) > 1:
                            params['instance'] = elem.get('instance', 0)
                        # 双保险：同时保留文本，资源ID 失效时回放可退回文本定位
                        if text and len(text) <= 60:
                            params['fallbackType'] = '文本'
                            params['fallbackValue'] = text
                        return params
                    if text and len(text) <= 60:
                        return dict(base, locationType='文本', locationValue=text)
                    if desc:
                        return dict(base, locationType='描述', locationValue=desc)

            # 兜底：坐标
            return dict(base, locationType='坐标',
                        locationValue=f"{int(x)},{int(y)}")

        def make_name(action, params):
            lt = params.get('locationType', '')
            lv = params.get('locationValue', '')
            if lt == '资源ID':
                short = lv.split('/')[-1] if '/' in lv else lv
                return f"{action} - {short}"
            if lt in ('文本', '描述'):
                short = lv[:20] + ('…' if len(lv) > 20 else '')
                return f"{action} - {short}"
            return f"{action} ({lv})"

        # ---------- 手势 → 步骤 ----------
        i = 0
        while i < len(gestures):
            g = gestures[i]

            # 双击合并
            if (i + 1 < len(gestures)
                    and g['type'] == 'tap'
                    and gestures[i + 1]['type'] == 'tap'
                    and gestures[i + 1]['down_time'] - g['up_time'] < 0.4
                    and abs(g['start_x'] - gestures[i + 1]['start_x']) < 80
                    and abs(g['start_y'] - gestures[i + 1]['start_y']) < 80):
                params = make_loc_params(g['start_x'], g['start_y'], g)
                steps.append({
                    'type': 'double_click',
                    'params': params,
                    'name': make_name('双击', params),
                })
                i += 2
                continue

            if g['type'] == 'tap':
                if g['duration'] >= 0.8:
                    params = make_loc_params(g['start_x'], g['start_y'], g)
                    params['longPressMs'] = int(g['duration'] * 1000)
                    steps.append({
                        'type': 'long_press',
                        'params': params,
                        'name': make_name('长按', params),
                    })
                else:
                    # 输入框识别：点击坐标若落在输入框上、且该框里已有文字，
                    # 就把这步升级为「输入」步骤（点击输入框 + 输入文字），
                    # 覆盖「点输入框 → 打字」这类软键盘操作（触摸 getevent 拿不到字符，
                    # 只能靠录制结束时反查输入框的最终文本）。
                    input_step = self._try_build_input_step(g, prefer_element)
                    if input_step:
                        steps.append(input_step)
                    else:
                        params = make_loc_params(g['start_x'], g['start_y'], g)
                        steps.append({
                            'type': 'click',
                            'params': params,
                            'name': make_name('点击', params),
                        })
            else:
                dx = g['end_x'] - g['start_x']
                dy = g['end_y'] - g['start_y']
                distance = g['distance']
                if abs(dx) > abs(dy):
                    direction = '右滑' if dx > 0 else '左滑'
                else:
                    direction = '下滑' if dy > 0 else '上滑'

                cur_w = self._screen_width or 1080
                cur_h = self._screen_height or 1920
                steps.append({
                    'type': 'swipe',
                    'params': {
                        'direction': direction,
                        'startX': int(g['start_x']),
                        'startY': int(g['start_y']),
                        'endX': int(g['end_x']),
                        'endY': int(g['end_y']),
                        'screenWidth': self._screen_width,
                        'screenHeight': self._screen_height,
                        'normalizedStartX': round(g['start_x'] / cur_w, 4),
                        'normalizedStartY': round(g['start_y'] / cur_h, 4),
                        'normalizedEndX': round(g['end_x'] / cur_w, 4),
                        'normalizedEndY': round(g['end_y'] / cur_h, 4),
                    },
                    'name': f"{direction} ({int(distance)}px)"
                })

            i += 1

        return steps

    # 输入框 class 特征：uiautomator2 dump 里可输入文字的控件类名
    _INPUT_FIELD_CLASS_MARKERS = ('EditText', 'AutoCompleteTextView', 'SearchView')

    def _try_build_input_step(self, gesture, prefer_element):
        """判断一次 tap 是否落在输入框上、且该框里已有文字；是则构造 input 步骤。

        软键盘打字在触摸 getevent 里只有点击软键盘键位的坐标，拿不到字符本身，
        所以靠「录制结束时反查输入框的最终文本」来还原输入内容。
        返回 dict（input 步骤）或 None（不是输入框/没文字，保持普通 click）。

        性能关键：判断「是否输入框」优先用 dump worker 的缓存（tap 反查结果），
        **零额外 dump 开销**；只有确认是输入框、需要拿最终输入文字时才额外 dump
        一次（此时页面正停在输入框上，text 是用户打完的最终值）。
        之前版本对每个 tap 无条件重新 dump 一次（不走缓存），反查时间直接翻倍，
        且停止后页面已切换、反查出的是错误控件（用户实测日志定位到此问题）。
        """
        if not prefer_element:
            return None
        x, y = int(gesture['start_x']), int(gesture['start_y'])

        # 1) 先查缓存：dump worker 在 tap 反查时已存过该坐标的元素信息
        key = (gesture['down_time'], x, y)
        with self._element_cache_lock:
            elem = self._element_cache.get(key)

        if elem is None:
            # 缓存 miss（该 tap 没有反查记录）：保守起见不额外 dump，保持 click
            return None

        # 2) 用缓存的 className 判断是否输入框（类名不随打字变化，缓存值可信）
        cls = (elem.get('className') or '').strip()
        if not any(m in cls for m in self._INPUT_FIELD_CLASS_MARKERS):
            return None

        # 3) 确认是输入框：再反查一次拿**最终输入文字**
        #    （缓存里的 text 是点击时刻的值——当时用户还没打字，是空/旧值）
        try:
            final_elem = self._get_element_at(x, y)
        except Exception:
            final_elem = None
        text = ((final_elem or {}).get('text') or '').strip()
        if not text:
            # 没拿到最终文字（页面已切换/输入框为空）：退回普通 click
            return None

        # 4) 构造 input 步骤：定位到该输入框，输入 text
        params = {
            'screenWidth': self._screen_width,
            'screenHeight': self._screen_height,
            'normalizedX': round(x / (self._screen_width or 1080), 4),
            'normalizedY': round(y / (self._screen_height or 1920), 4),
            'text': text,
        }
        rid = ((final_elem or elem).get('resourceId') or '').strip()
        if rid:
            params['locationType'] = '资源ID'
            params['locationValue'] = rid
            if ((final_elem or elem).get('resourceIdCount') or 1) > 1:
                params['instance'] = (final_elem or elem).get('instance', 0)
        else:
            params['locationType'] = '文本'
            params['locationValue'] = text

        name = f"输入 {text[:20]}{'…' if len(text) > 20 else ''}"
        return {'type': 'input', 'params': params, 'name': name}

    def _detect_gestures(self, events):
        gestures = []
        i = 0
        while i < len(events):
            ev = events[i]
            if ev['type'] == 'down':
                start_x = ev['x']
                start_y = ev['y']
                down_time = ev['timestamp']
                j = i + 1
                while j < len(events) and events[j]['type'] != 'up':
                    j += 1
                if j < len(events):
                    up_ev = events[j]
                    end_x = up_ev['x']
                    end_y = up_ev['y']
                    up_time = up_ev['timestamp']
                    duration = up_time - down_time
                    dx = end_x - start_x
                    dy = end_y - start_y
                    distance = (dx ** 2 + dy ** 2) ** 0.5
                    gesture_type = 'tap' if distance < 80 else 'swipe'
                    gestures.append({
                        'type': gesture_type,
                        'start_x': start_x,
                        'start_y': start_y,
                        'end_x': end_x,
                        'end_y': end_y,
                        'down_time': down_time,
                        'up_time': up_time,
                        'duration': duration,
                        'distance': distance
                    })
                    i = j + 1
                else:
                    i += 1
            else:
                i += 1
        return gestures

    def _on_steps_preview(self, steps):
        """录制生成的步骤预览（GUI 线程）：弹对话框让用户确认要保留的步骤。

        交互设计（用户反馈迭代后定稿）：
        - 复选框用执行页同款 BorderedCheckBox：Fusion 默认绘制（勾选显示 √），
          叠加明显边框，暗色下清晰——**不要**用 QSS 设 ::indicator，会丢 √ 变色块
        - 整行可点击：复选框占满整行宽度，点击行内任意位置即可切换
        - 副标题实时显示「已选择 N / M」
        """
        if not steps:
            show_toast(message="未识别到操作")
            return

        from utils.settings import Settings as _Settings, THEME_MODE_DARK as _TMD
        from views.execute_view import BorderedCheckBox

        is_dark = _Settings.get_theme_mode() == _TMD
        if is_dark:
            container_bg, container_border = "#3c3c3c", "#555"
            title_fg, sub_fg, text_fg = "#ffffff", "#999999", "#eeeeee"
            row_hover = "#333333"
            list_bg, list_border = "#333333", "#4a4a4a"      # 列表卡片：比容器深一档
        else:
            container_bg, container_border = "#ffffff", "#d0d0d0"
            title_fg, sub_fg, text_fg = "#1a1a1a", "#999999", "#333333"
            row_hover = "#f2f4f7"
            list_bg, list_border = "#fafbfc", "#e3e6ea"      # 列表卡片：浅灰底

        type_names = {'click': '点击', 'double_click': '双击', 'long_press': '长按',
                      'input': '输入', 'swipe': '滑动', 'wait': '等待',
                      'assert': '断言', 'voice': '语音'}

        dlg = QDialog(self.step_view)
        dlg.setWindowTitle("录制完成 - 确认步骤")
        dlg.setFixedSize(540, 480)
        dlg.setStyleSheet(f"""
            QDialog {{
                background-color: {container_bg};
            }}
            QLabel {{ background: transparent; }}
            /* 步骤列表卡片：圆角边框 + 微底色，与容器区分层次 */
            QScrollArea {{
                background-color: {list_bg};
                border: 1px solid {list_border};
                border-radius: 10px;
            }}
            QScrollArea > QWidget > QWidget {{ background: transparent; }}
            /* 只给 QCheckBox 设文字色，**不设 ::indicator**——
               让 Fusion 绘制默认带 √ 的复选框（执行页同款做法） */
            QCheckBox {{
                color: {text_fg};
                font-size: 13px;
                spacing: 10px;
                padding: 8px 12px;
                background: transparent;
                border-radius: 6px;
            }}
            QCheckBox:hover {{
                background-color: {row_hover};
            }}
        """)

        root = QVBoxLayout(dlg)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(10)

        title = QLabel("录制完成")
        title.setStyleSheet(f"font-size: 17px; font-weight: bold; color: {title_fg};")
        root.addWidget(title)

        sub = QLabel("")
        sub.setStyleSheet(f"font-size: 12px; color: {sub_fg};")
        root.addWidget(sub)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background-color: {container_border}; max-height: 1px;")
        root.addWidget(divider)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)   # 去掉原生边框，用 QSS 圆角边框
        root.addWidget(scroll, 1)

        list_widget = QWidget()
        list_layout = QVBoxLayout(list_widget)
        list_layout.setSpacing(3)
        # 内边距：让首尾行与卡片边框留出呼吸空间
        list_layout.setContentsMargins(8, 8, 8, 8)

        checks = []
        for i, s in enumerate(steps):
            p = s.get('params', {})
            lt = p.get('locationType', '')
            lv = p.get('locationValue', '')
            if lt == '资源ID' and '/' in lv:
                lv = lv.split('/')[-1]
            tname = type_names.get(s.get('type', ''), s.get('type', ''))

            summary = f"第{i+1}步 {s.get('name', '')}"
            if lv:
                summary += f"（{lt}：{lv[:28]}{'…' if len(lv) > 28 else ''}）"

            cb = BorderedCheckBox(summary)
            cb.setChecked(True)
            # 占满整行宽度：点击行内任意位置（含文字右侧空白）都能切换勾选
            from PyQt6.QtWidgets import QSizePolicy
            cb.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            list_layout.addWidget(cb)
            checks.append(cb)

        list_layout.addStretch()
        scroll.setWidget(list_widget)

        def _update_count():
            n = sum(1 for c in checks if c.isChecked())
            sub.setText(f"共录制到 {len(steps)} 个步骤，已选择 {n} / {len(steps)}"
                        f" —— 点击行即可勾选/取消")

        for c in checks:
            c.toggled.connect(_update_count)
        _update_count()

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        ghost_style = f"""
            QPushButton {{
                background-color: {'#555555' if is_dark else '#f0f0f0'};
                color: {'#eeeeee' if is_dark else '#333333'};
                border: none; border-radius: 6px;
                padding: 6px 14px; font-size: 13px;
            }}
            QPushButton:hover {{ background-color: {'#666666' if is_dark else '#e0e0e0'}; }}
        """
        primary_style = """
            QPushButton {
                background-color: #1976d2; color: white;
                border: none; border-radius: 6px;
                padding: 6px 18px; font-size: 13px; font-weight: 500;
            }
            QPushButton:hover { background-color: #1565c0; }
        """

        all_btn = QPushButton("全选")
        none_btn = QPushButton("全不选")
        all_btn.setStyleSheet(ghost_style)
        none_btn.setStyleSheet(ghost_style)
        all_btn.clicked.connect(lambda: [c.setChecked(True) for c in checks])
        none_btn.clicked.connect(lambda: [c.setChecked(False) for c in checks])
        btn_row.addWidget(all_btn)
        btn_row.addWidget(none_btn)
        btn_row.addStretch()

        cancel_btn = QPushButton("取消")
        cancel_btn.setStyleSheet(ghost_style)
        ok_btn = QPushButton("确认添加")
        ok_btn.setStyleSheet(primary_style)
        ok_btn.setFixedSize(100, 32)
        ok_btn.setDefault(True)
        cancel_btn.clicked.connect(dlg.reject)
        ok_btn.clicked.connect(dlg.accept)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)
        root.addLayout(btn_row)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            show_toast(message="已取消添加")
            return

        selected = [s for s, c in zip(steps, checks) if c.isChecked()]
        if not selected:
            show_toast(message="未选择任何步骤")
            return
        self.steps_ready.emit(selected)

    def _apply_steps_to_model(self, steps_data):
        if not steps_data:
            show_toast(message="未识别到操作")
            return

        self.step_view.setUpdatesEnabled(False)
        try:
            for step_data in steps_data:
                step = Step(
                    id=-1,
                    type=step_data['type'],
                    name=step_data['name'],
                    params=step_data['params']
                )
                self.model.add_step_to_case(self.current_case_id, step)
            self.refresh_steps()
        finally:
            self.step_view.setUpdatesEnabled(True)

        show_toast(message="添加步骤成功")

    # ---------- 自然语言生成步骤 ----------
    def generate_steps_from_text(self, text):
        if not self.current_case_id:
            show_toast(message="请先在项目树中选择一个用例")
            return
        if not text:
            return

        steps = self._parse_text_to_steps(text)

        if not steps:
            show_toast(message="未能识别有效操作")
            return

        self.step_view.setUpdatesEnabled(False)
        try:
            for step_data in steps:
                step = Step(
                    id=-1,
                    type=step_data['type'],
                    name=step_data.get('name', ''),
                    params=step_data['params']
                )
                self.model.add_step_to_case(self.current_case_id, step)
            self.refresh_steps()
        finally:
            self.step_view.setUpdatesEnabled(True)

        show_toast(message="生成步骤成功")

    def _parse_text_to_steps(self, text):
        """将文本解析为步骤列表，并合并 点击+输入 为 输入"""
        connectors = ['然后', '接着', '再', '之后', '随后', '并']
        pattern = '|'.join([re.escape(c) for c in connectors] + [r'[。；\n,，]'])
        parts = re.split(pattern, text)
        parts = [p.strip() for p in parts if p.strip()]

        steps = []
        for part in parts:
            step = self._parse_single_sentence(part)
            if step:
                steps.append(step)

        # 合并 点击 + 输入
        merged = []
        i = 0
        while i < len(steps):
            if (i + 1 < len(steps) and
                steps[i]['type'] == 'click' and
                steps[i+1]['type'] == 'input'):
                click_params = steps[i]['params']
                input_params = steps[i+1]['params']
                if 'locationType' in click_params:
                    input_params['locationType'] = click_params['locationType']
                if 'locationValue' in click_params:
                    input_params['locationValue'] = click_params['locationValue']
                if 'element_id' in click_params:
                    input_params['element_id'] = click_params['element_id']
                input_text = input_params.get('text', '')
                steps[i+1]['name'] = f"点击输入框并输入 {input_text}" if input_text else "点击输入框并输入"
                merged.append(steps[i+1])
                i += 2
            else:
                merged.append(steps[i])
                i += 1

        return merged

    def _parse_single_sentence(self, sent):
        """解析单个句子，支持动作识别、元素匹配、启动应用、断言、滑动变体、拖拽、手势缩放"""
        # ---------- 1. 动作关键词识别 ----------
        action = None
        if '点击' in sent or '单击' in sent:
            action = 'click'
        elif '双击' in sent:
            action = 'double_click'
        elif '长按' in sent:
            action = 'long_press'
        elif '输入' in sent or '键入' in sent:
            action = 'input'
        elif '等待' in sent or '暂停' in sent:
            action = 'wait'
        elif ('滑动' in sent or '上滑' in sent or '下滑' in sent or '左滑' in sent or '右滑' in sent or
              '上划' in sent or '下划' in sent or '左划' in sent or '右划' in sent or '划动' in sent):
            action = 'swipe'
        elif '拖拽' in sent or '拖动' in sent:
            action = 'drag_drop'
        elif '缩小' in sent or '放大' in sent:
            action = 'gesture_zoom'
        elif '启动' in sent or '打开' in sent or '运行' in sent:
            action = 'app_mgr'
            app_name = re.sub(r'启动|打开|运行', '', sent).strip()
            if not app_name:
                app_name = '应用'
            params = {'action': '启动应用', 'packageName': app_name}
            name = f"启动 {app_name}"
            return {'type': action, 'params': params, 'name': name}
        elif '返回' in sent or '主页' in sent or '菜单' in sent:
            action = 'physical_key'
        elif '截图' in sent:
            action = 'screenshot'
        elif '断言' in sent or '验证' in sent or '判断' in sent or '检查' in sent:
            action = 'assert'

        if not action:
            return None

        # ---------- 2. 通用元素匹配（预匹配） ----------
        matched_elem = None
        if self.element_controller:
            elements = self.element_controller.get_elements()
            # 精确匹配
            for e in elements:
                if e.name and e.name in sent:
                    matched_elem = e
                    break
            # 分词匹配
            if matched_elem is None:
                action_words = ['点击', '单击', '双击', '长按', '输入', '滑动', '返回', '断言', '验证', '判断', '检查',
                                '上划', '下划', '左划', '右划', '划动', '拖拽', '拖动', '缩小', '放大']
                clean_sent = sent
                for w in action_words:
                    clean_sent = clean_sent.replace(w, '')
                keywords = re.findall(r'[\u4e00-\u9fa5a-zA-Z0-9]+', clean_sent)
                stop_words = {'的', '了', '在', '是', '我', '有', '和', '就', '不', '人', '都', '一', '一个', '上', '也', '很', '至', '页面', '进入', '关闭', '返回', '点击', '输入', '滑动', '断言', '验证', '判断', '是否', '为'}
                keywords = [kw for kw in keywords if kw not in stop_words and len(kw) >= 2]
                if keywords:
                    candidates = []
                    for e in elements:
                        for kw in keywords:
                            if kw and (kw in e.name or e.name in kw):
                                candidates.append((e, kw, len(e.name)))
                                break
                    if candidates:
                        candidates.sort(key=lambda x: x[2])
                        matched_elem = candidates[0][0]

        # ---------- 3. 根据动作生成参数 ----------
        params = {}
        name = sent[:20]

        if action == 'click':
            if matched_elem:
                params = {
                    'locationType': matched_elem.loc_type,
                    'locationValue': matched_elem.loc_value,
                    'element_id': matched_elem.id
                }
            else:
                text_match = re.search(r'[“"]([^”"]+)[”"]', sent)
                if text_match:
                    params = {'locationType': '文本', 'locationValue': text_match.group(1)}
                else:
                    cleaned = re.sub(r'点击|单击|双击|长按', '', sent).strip()
                    cleaned = re.sub(r'进入|关闭|页面|打开', '', cleaned).strip()
                    params = {'locationType': '文本', 'locationValue': cleaned if cleaned else '元素'}

        elif action == 'input':
            # 提取输入文本
            input_text = ''
            input_match = re.search(r'输入\s*[“"]([^”"]+)[”"]', sent)
            if input_match:
                input_text = input_match.group(1).strip()
            else:
                input_match = re.search(r'(?:输入框|搜索框|编辑框)\s*输入\s*([^，,。；]+)', sent)
                if input_match:
                    raw = input_match.group(1).strip()
                    raw = re.sub(r'^(输入框|搜索框|编辑框)', '', raw).strip()
                    input_text = raw
                else:
                    input_match = re.search(r'输入\s*([^，,。；]+)', sent)
                    if input_match:
                        raw = input_match.group(1).strip()
                        raw = re.sub(r'^(输入框|搜索框|编辑框)', '', raw).strip()
                        input_text = raw
            if not input_text:
                cleaned = re.sub(r'输入|点击|单击|等', '', sent).strip()
                if cleaned:
                    input_text = cleaned

            # 优先匹配输入框元素
            box_elem = None
            if self.element_controller:
                for e in self.element_controller.get_elements():
                    if ('输入框' in e.name or '搜索框' in e.name) and (e.name in sent or '输入框' in sent):
                        box_elem = e
                        break
            if box_elem:
                matched_elem = box_elem

            if matched_elem:
                params = {
                    'locationType': matched_elem.loc_type,
                    'locationValue': matched_elem.loc_value,
                    'text': input_text,
                    'element_id': matched_elem.id
                }
            else:
                box_match = re.search(r'(输入框|搜索框|编辑框)', sent)
                box_name = box_match.group(1) if box_match else '输入框'
                params = {'locationType': '文本', 'locationValue': box_name, 'text': input_text}
            name = f"输入 {input_text}" if input_text else "输入"

        elif action == 'wait':
            time_match = re.search(r'(\d+)\s*秒', sent)
            duration = int(time_match.group(1)) if time_match else 3
            params = {'duration': duration}
            name = f"等待 {duration} 秒"

        elif action == 'swipe':
            # 检测方向：支持"滑"和"划"两种写法
            if '上滑' in sent or '上划' in sent:
                params = {'direction': '上滑', 'startX': 500, 'startY': 800, 'endX': 500, 'endY': 200}
                name = "上划" if '上划' in sent else "上滑"
            elif '下滑' in sent or '下划' in sent:
                params = {'direction': '下滑', 'startX': 500, 'startY': 200, 'endX': 500, 'endY': 800}
                name = "下划" if '下划' in sent else "下滑"
            elif '左滑' in sent or '左划' in sent:
                params = {'direction': '左滑', 'startX': 800, 'startY': 500, 'endX': 200, 'endY': 500}
                name = "左划" if '左划' in sent else "左滑"
            elif '右滑' in sent or '右划' in sent:
                params = {'direction': '右滑', 'startX': 200, 'startY': 500, 'endX': 800, 'endY': 500}
                name = "右划" if '右划' in sent else "右滑"
            elif '划动' in sent or '滑动' in sent:
                params = {'direction': '上滑', 'startX': 500, 'startY': 800, 'endX': 500, 'endY': 200}
                name = "滑动"
            else:
                params = {'direction': '自定义坐标', 'startX': 200, 'startY': 500, 'endX': 800, 'endY': 500}
                name = "滑动"

        elif action == 'drag_drop':
            # ---------- 拖拽动作 ----------
            from_part = sent
            to_part = ''
            if '到' in sent:
                parts = sent.split('到')
                from_part = parts[0].strip()
                to_part = parts[1].strip() if len(parts) > 1 else ''
            elif '至' in sent:
                parts = sent.split('至')
                from_part = parts[0].strip()
                to_part = parts[1].strip() if len(parts) > 1 else ''

            # 尝试匹配源元素
            from_elem = None
            to_elem = None
            if self.element_controller:
                # 从 from_part 中匹配元素
                for e in self.element_controller.get_elements():
                    if e.name and e.name in from_part:
                        from_elem = e
                        break
                if to_part:
                    for e in self.element_controller.get_elements():
                        if e.name and e.name in to_part:
                            to_elem = e
                            break

            if from_elem:
                params['fromLocationType'] = from_elem.loc_type
                params['fromValue'] = from_elem.loc_value
            else:
                # 尝试从 from_part 提取文本作为源
                cleaned_from = re.sub(r'拖拽|拖动', '', from_part).strip()
                if cleaned_from:
                    params['fromLocationType'] = '文本'
                    params['fromValue'] = cleaned_from
                else:
                    params['fromLocationType'] = '坐标'
                    params['fromValue'] = '200,500'

            if to_elem:
                params['toLocationType'] = to_elem.loc_type
                params['toValue'] = to_elem.loc_value
            else:
                if to_part:
                    cleaned_to = to_part.strip()
                    if cleaned_to:
                        params['toLocationType'] = '文本'
                        params['toValue'] = cleaned_to
                    else:
                        params['toLocationType'] = '坐标'
                        params['toValue'] = '800,500'
                else:
                    params['toLocationType'] = '坐标'
                    params['toValue'] = '800,500'

            name = f"拖拽 {from_part} 到 {to_part}" if to_part else "拖拽"

        elif action == 'gesture_zoom':
            # ---------- 手势缩放 ----------
            gesture_type = '放大' if '放大' in sent else '捏合（缩小）'
            params = {
                'gestureType': gesture_type,
                'centerX': 540,
                'centerY': 960,
                'scale': 1.5
            }
            name = gesture_type

        elif action == 'physical_key':
            key_map = {'返回': '返回', '主页': '主页', '菜单': '菜单'}
            for key in key_map:
                if key in sent:
                    params = {'keyName': key}
                    name = f"按 {key}"
                    break
            else:
                params = {'keyName': '返回'}
                name = "按 返回"

        elif action == 'screenshot':
            params = {'savePath': '/sdcard/', 'fileName': 'screenshot'}
            name = "截图"

        elif action == 'assert':
            # ---------- 断言专用处理 ----------
            assert_type = '元素存在'
            expected_value = ''
            if '不存在' in sent:
                assert_type = '元素不存在'
            elif '等于' in sent:
                assert_type = '文本等于'
                val_match = re.search(r'等于\s*[“"]([^”"]+)[”"]', sent)
                if not val_match:
                    val_match = re.search(r'等于\s*([^，,。；]+)', sent)
                if val_match:
                    expected_value = val_match.group(1).strip()
            elif '为' in sent and ('判断' in sent or '是否' in sent):
                assert_type = '文本等于'
                val_match = re.search(r'为\s*[“"]([^”"]+)[”"]', sent)
                if not val_match:
                    val_match = re.search(r'为\s*([^，,。；]+)', sent)
                if val_match:
                    expected_value = val_match.group(1).strip()
                else:
                    cleaned = re.sub(r'判断|是否为|验证|检查', '', sent).strip()
                    if cleaned:
                        expected_value = cleaned
            elif '包含' in sent:
                assert_type = '文本包含'
                val_match = re.search(r'包含\s*[“"]([^”"]+)[”"]', sent)
                if not val_match:
                    val_match = re.search(r'包含\s*([^，,。；]+)', sent)
                if val_match:
                    expected_value = val_match.group(1).strip()

            # 提取定位值
            loc_type = '文本'
            loc_value = '元素'
            element_id = None

            if assert_type in ('元素存在', '元素不存在'):
                cleaned = re.sub(r'验证|判断|是否为|检查|断言', '', sent).strip()
                if not cleaned:
                    cleaned = sent
                # 尝试匹配元素库
                elem = None
                if self.element_controller:
                    keywords = re.findall(r'[\u4e00-\u9fa5a-zA-Z0-9]+', cleaned)
                    stop_words = {'的', '了', '在', '是', '我', '有', '和', '就', '不', '人', '都', '一', '一个', '上', '也', '很', '至', '页面', '进入', '关闭', '返回', '点击', '输入', '滑动', '断言', '验证', '判断', '是否', '为'}
                    keywords = [kw for kw in keywords if kw not in stop_words and len(kw) >= 2]
                    if keywords:
                        keywords.sort(key=len, reverse=True)
                        for e in self.element_controller.get_elements():
                            for kw in keywords:
                                if kw and (kw in e.name or e.name in kw):
                                    elem = e
                                    break
                            if elem:
                                break
                if elem:
                    loc_type = elem.loc_type
                    loc_value = elem.loc_value
                    element_id = elem.id
                else:
                    loc_value = keywords[0] if keywords else cleaned
            else:
                # 文本等于/包含：预期值作为定位值
                loc_value = expected_value if expected_value else '元素'
                if self.element_controller and expected_value:
                    for e in self.element_controller.get_elements():
                        if e.name and e.name == expected_value:
                            loc_type = e.loc_type
                            loc_value = e.loc_value
                            element_id = e.id
                            break

            params = {
                'assert_type': assert_type,
                'timeout': 5,
                'locationType': loc_type,
                'locationValue': loc_value,
            }
            if element_id:
                params['element_id'] = element_id
            if expected_value:
                params['expected_value'] = expected_value

            name = f"断言 {assert_type}"
            if expected_value:
                name += f" '{expected_value}'"

        else:
            # 兜底（不应发生）
            if matched_elem:
                params = {'locationType': matched_elem.loc_type, 'locationValue': matched_elem.loc_value, 'element_id': matched_elem.id}
            else:
                params = {'locationType': '文本', 'locationValue': sent}
            name = sent[:20]

        return {'type': action, 'params': params, 'name': name}

    # ---------- 增强的 _get_element_at 方法 ----------
    def _get_element_at(self, x, y):
        try:
            device = self.device_service.device
            if device is None:
                return None

            xml_str = None
            # uiautomator2 标准 API 是 dump_hierarchy()，不是 dump()。
            # 兼容不同版本：优先 dump_hierarchy，回退到 dump。
            try:
                if hasattr(device, 'dump_hierarchy'):
                    xml_str = device.dump_hierarchy()
                elif hasattr(device, 'dump'):
                    xml_str = device.dump()
                else:
                    print("[反查] 当前 uiautomator2 版本无 dump 方法")
                    xml_str = None
            except Exception as e:
                print(f"[反查] uiautomator2 dump 失败: {e}")
                xml_str = None

            # ★ 调试：dump 是否拿到了内容
            if xml_str:
                print(f"[反查] dump 长度: {len(xml_str)} 字符")
            else:
                print(f"[反查] dump 为空，尝试备用方案")

            if xml_str is None:
                try:
                    temp_file = os.path.join(tempfile.gettempdir(), f"ui_dump_{int(time.time())}.xml")
                    local_file = os.path.join(tempfile.gettempdir(), f"ui_dump_local_{int(time.time())}.xml")

                    # 先试 --compressed（绕过 idle 检测），失败再试普通模式
                    for extra_args in (['--compressed'], []):
                        cmd = [get_adb_path(), 'shell', 'uiautomator', 'dump'] + extra_args + [temp_file]
                        r = subprocess.run(cmd, capture_output=True, timeout=8, check=False)
                        out = (r.stdout or b'') + (r.stderr or b'')
                        if b'could not get idle state' in out or b'ERROR' in out:
                            print(f"[反查] 备用 dump 失败（{'--compressed' if extra_args else '普通'}）: "
                                  f"{out.decode('utf-8', errors='ignore').strip()}")
                            continue
                        # 拉取
                        subprocess.run([get_adb_path(), 'pull', temp_file, local_file],
                                       capture_output=True, timeout=5, check=False)
                        if os.path.exists(local_file):
                            with open(local_file, 'r', encoding='utf-8') as f:
                                xml_str = f.read()
                            os.remove(local_file)
                            subprocess.run([get_adb_path(), 'shell', 'rm', temp_file],
                                           capture_output=True, timeout=2, check=False)
                            break
                except Exception as e:
                    print(f"[反查] 备用 dump 异常: {e}")
                    return None

            if not xml_str:
                return None

            root = ET.fromstring(xml_str)
            return self._find_element_in_xml(root, x, y)
        except Exception as e:
            print(f"获取元素信息失败: {e}")
            return None

    def _find_element_in_xml(self, node, x, y):
        """
        找到包含坐标 (x, y) 的、最有语义信息的最深层节点。

        策略：
        1. 递归收集所有 bounds 覆盖该坐标的节点
        2. 优先从"有 resource-id / text / content-desc 的属性节点"里选面积最小的
           （面积小 = 更靠近用户实际点击的那个控件）
        3. 如果所有命中节点都没属性，选面积最小的那个作为兜底
           （说明 App 是自绘 UI，这时反查失败是合理的）
        """
        candidates = []  # [(area, info, has_attr), ...]
        self._collect_matching_nodes(node, x, y, candidates)

        if not candidates:
            return None

        # 优先：有属性的节点里，面积最小的
        with_attr = [c for c in candidates if c[2]]
        if with_attr:
            with_attr.sort(key=lambda c: c[0])
            area, info, _ = with_attr[0]
            # 若选中了带 resourceId 的节点，统计全树同 rid 的节点数并计算序号，
            # 解决「列表项共用同一 rid」导致回放点错行的问题。
            if info.get('resourceId'):
                count, instance = self._compute_instance(
                    node, info['resourceId'], info.get('bounds'))
                info['resourceIdCount'] = count
                info['instance'] = instance
            else:
                info['resourceIdCount'] = 1
                info['instance'] = 0
            print(f"[反查] ({x},{y}) 命中 {len(candidates)} 个节点，"
                  f"其中 {len(with_attr)} 个有属性，选中面积最小的：")
            print(f"          area={area}px², "
                  f"class={info['className']!r}, "
                  f"rid={info['resourceId']!r}, "
                  f"text={info['text']!r}, "
                  f"desc={info['description']!r}, "
                  f"ridCount={info['resourceIdCount']}, instance={info['instance']}")
            return info

        # 兜底：全都没属性，取面积最小的（自绘控件，反查注定失败）
        candidates.sort(key=lambda c: c[0])
        area, info, _ = candidates[0]
        print(f"[反查] ({x},{y}) 命中 {len(candidates)} 个节点，"
              f"但都没有可用属性，选面积最小的作为兜底：")
        print(f"          area={area}px², class={info['className']!r}")
        return info

    def _compute_instance(self, root, target_rid, target_bounds):
        """统计整棵 UI 树里 resourceId == target_rid 的节点数，并计算
        target_bounds 对应节点在这些同类节点中的序号（按 top、left 排序）。

        返回 (count, instance)。instance 从 0 起；target_bounds 为空时返回 (count, 0)。
        """
        siblings = []   # [(top, left, bounds)]
        def walk(node):
            rid = (node.get('resource-id') or '').strip()
            if rid == target_rid:
                b = node.get('bounds')
                if b:
                    m = re.search(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', b)
                    if m:
                        left = int(m.group(1)); top = int(m.group(2))
                        right = int(m.group(3)); bottom = int(m.group(4))
                        siblings.append((top, left, (left, top, right, bottom)))
            for child in node:
                walk(child)
        walk(root)

        count = len(siblings)
        if count == 0 or target_bounds is None:
            return count, 0

        siblings.sort(key=lambda t: (t[0], t[1]))   # 按 top 再 left 排序
        instance = 0
        for idx, (_, _, b) in enumerate(siblings):
            if b == target_bounds:
                instance = idx
                break
        return count, instance

    def _collect_matching_nodes(self, node, x, y, candidates):
        """递归收集所有 bounds 覆盖坐标 (x, y) 的节点。
        candidates 每个元素: (area, info_dict, has_attr_bool)"""
        import re
        bounds = node.get('bounds')
        if bounds:
            match = re.search(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', bounds)
            if match:
                left = int(match.group(1))
                top = int(match.group(2))
                right = int(match.group(3))
                bottom = int(match.group(4))
                if left <= x <= right and top <= y <= bottom:
                    area = max(0, (right - left)) * max(0, (bottom - top))
                    rid = (node.get('resource-id') or '').strip()
                    text = (node.get('text') or '').strip()
                    desc = (node.get('content-desc') or '').strip()
                    has_attr = bool(rid or text or desc)
                    info = {
                        'resourceId': rid,
                        'text': text,
                        'description': desc,
                        'className': node.get('class'),
                        # 保留 bounds，供后续计算「同类元素序号」(instance) 用
                        'bounds': (left, top, right, bottom),
                    }
                    candidates.append((area, info, has_attr))

        for child in node:
            self._collect_matching_nodes(child, x, y, candidates)