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
    QPushButton, QGraphicsDropShadowEffect, QListView
from PyQt6.QtCore import QObject, Qt, pyqtSignal
from models.step_model import StepModel, Step
from utils.dialogs import WarningDialog, ConfirmDeleteDialog, ErrorDialog
from views.step_list_view import StepListView
from views.action_card_view import ActionCardView
from views.element_selector_dialog import ElementSelectorDialog
from utils.toast import show_toast
from utils.theme import ThemeMode
import qtawesome as qta


class StepController(QObject):
    steps_ready = pyqtSignal(list)

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
                'element_id': '元素ID'
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
                if key in ('locationType', 'fromLocationType', 'toLocationType'):
                    widget = QComboBox()
                    widget.addItems(self.FIELD_OPTIONS[key])
                    index = widget.findText(str(value))
                    if index >= 0:
                        widget.setCurrentIndex(index)
                    widget.setStyleSheet(combo_style)
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
                    select_btn.setIcon(qta.icon('fa6s.folder-open', color='#555555'))
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
                form.addRow(f"{label_text}:", widget)

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
        独立线程：从 _dump_queue 取 (ts, x, y)，立即 dump 并按坐标反查。
        - 只被 down 事件喂数据，无轮询开销
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
            try:
                elem = self._get_element_at(int(x), int(y))
            except Exception as e:
                print(f"[dump worker] ({x},{y}) 反查异常: {e}")
                elem = None

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
            # 两个比例理论上应该接近（触摸插值倍数）
            if ratio_x > 0 and ratio_y > 0:
                drift = abs(ratio_x - ratio_y) / max(ratio_x, ratio_y)
                if drift > 0.2:
                    print(f"[录制] ⚠ 量程比例异常: X缩放={ratio_x:.2f}, Y缩放={ratio_y:.2f}, "
                          f"偏差 {drift * 100:.1f}% —— 可能探测到了错误的触摸设备")
                else:
                    print(f"[录制] 量程比例正常: 触摸插值倍数 ≈ {ratio_x:.2f}")

        event_pattern = re.compile(
            r'\[\s*([\d.]+)\]\s+[^:]+:\s+([0-9a-f]{4})\s+([0-9a-f]{4})\s+([0-9a-f]+)'
        )

        # ★ 只监听选定的触摸设备（车机多屏场景关键）
        device_path = dev.get('path')
        if device_path:
            cmd = ['adb', 'shell', 'getevent', '-t', device_path]
        else:
            cmd = ['adb', 'shell', 'getevent', '-t']

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        )

        # 保存引用，停止录制时可强制终止
        self._getevent_process = process

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
                ['adb', 'shell', 'getevent', '-p'],
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

        sx = int(round(raw_x / mx * self._screen_width))
        sy = int(round(raw_y / my * self._screen_height))

        # 越界保护：换算后如果超出屏幕范围，说明量程探测可能错了
        if sx < 0 or sx > self._screen_width or sy < 0 or sy > self._screen_height:
            print(f"[录制] ⚠ 换算越界: raw=({raw_x},{raw_y}) → screen=({sx},{sy}), "
                  f"量程=({mx},{my}), 分辨率=({self._screen_width},{self._screen_height})")
            # 越界时不做换算，让坐标保持原始值，至少不会更糟
            return raw_x, raw_y

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
        def process_events():
            try:
                steps = self._build_steps_from_events(events_copy)
                self.steps_ready.emit(steps)
            except Exception:
                import traceback
                traceback.print_exc()
                self.steps_ready.emit([])

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
                        return dict(base, locationType='资源ID', locationValue=rid)
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
            if hasattr(device, 'dump'):
                try:
                    xml_str = device.dump()
                except Exception as e:
                    print(f"uiautomator2 dump 失败: {e}")
                    xml_str = None

            if xml_str is None:
                try:
                    temp_file = os.path.join(tempfile.gettempdir(), f"ui_dump_{int(time.time())}.xml")
                    local_file = os.path.join(tempfile.gettempdir(), f"ui_dump_local_{int(time.time())}.xml")
                    subprocess.run(['adb', 'shell', 'uiautomator', 'dump', temp_file],
                                   capture_output=True, timeout=5, check=False)
                    subprocess.run(['adb', 'pull', temp_file, local_file],
                                   capture_output=True, timeout=5, check=False)
                    if os.path.exists(local_file):
                        with open(local_file, 'r', encoding='utf-8') as f:
                            xml_str = f.read()
                        os.remove(local_file)
                    subprocess.run(['adb', 'shell', 'rm', temp_file],
                                   capture_output=True, timeout=2, check=False)
                except Exception as e:
                    print(f"备用 dump 失败: {e}")
                    return None

            if not xml_str:
                return None

            root = ET.fromstring(xml_str)
            return self._find_element_in_xml(root, x, y)
        except Exception as e:
            print(f"获取元素信息失败: {e}")
            return None

    def _find_element_in_xml(self, node, x, y):
        bounds = node.get('bounds')
        if bounds:
            import re
            match = re.search(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', bounds)
            if match:
                left = int(match.group(1))
                top = int(match.group(2))
                right = int(match.group(3))
                bottom = int(match.group(4))
                if left <= x <= right and top <= y <= bottom:
                    info = {
                        'resourceId': node.get('resource-id'),
                        'text': node.get('text'),
                        'description': node.get('content-desc'),
                        'className': node.get('class'),  # ← 新增
                    }
                    return info
        for child in node:
            result = self._find_element_in_xml(child, x, y)
            if result:
                return result
        return None