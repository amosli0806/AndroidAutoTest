# views/dialogs/image_crop_dialog.py
"""截屏取图对话框：设备当前画面 → 鼠标框选 → 裁剪存图。

用途：图像识别定位（元素库「定位方式 = 图像」）的参考图来源。
框选坐标按「显示缩放比」还原到原图坐标，裁剪结果存到 data/element_images/。
"""
import os
from datetime import datetime

from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                             QPushButton, QRubberBand)
from PyQt6.QtCore import Qt, QRect, QSize
from PyQt6.QtGui import QPixmap, QImage

from utils.app_paths import get_data_dir


class _CropLabel(QLabel):
    """显示缩放后的截图，处理框选；缩放宽高比与原图一致，label 尺寸 = 缩放图尺寸。"""

    def __init__(self, scale: float):
        super().__init__()
        self._scale = scale
        self._band = QRubberBand(QRubberBand.Shape.Rectangle, self)
        self._origin = None
        self._rect = None                 # 缩放坐标下的框选矩形
        self.on_region = None             # 框选完成回调（传缩放坐标下的 QRect）
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMouseTracking(True)

    def region(self):
        return self._rect

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._origin = e.position().toPoint()
            self._band.setGeometry(QRect(self._origin, QSize()))
            self._band.show()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._origin is not None:
            self._band.setGeometry(QRect(self._origin, e.position().toPoint()).normalized())
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._origin is not None:
            self._rect = QRect(self._origin, e.position().toPoint()).normalized()
            self._band.hide()
            self._origin = None
            if self.on_region:
                self.on_region(self._rect)
        super().mouseReleaseEvent(e)


class ImageCropDialog(QDialog):
    """截屏 → 框选 → 裁剪。关闭后由调用方读 `result_path`（None = 取消/没框选）。"""

    def __init__(self, device_service, parent=None):
        super().__init__(parent)
        self.setObjectName("ImageCropDialog")
        self.setWindowTitle("截屏取图")
        self.device_service = device_service
        self.result_path = None
        self._full = None
        self._scale = 1.0
        self._setup_ui()

    def _setup_ui(self):
        # 截图（uiautomator2 返回 PIL.Image）
        try:
            img = self.device_service.device.screenshot()
        except Exception as e:
            raise RuntimeError(f"截图失败（请确认设备已连接）：{e}")
        img = img.convert("RGBA")
        data = img.tobytes("raw", "RGBA")
        qimg = QImage(data, img.width, img.height, QImage.Format.Format_RGBA8888)
        self._full = QPixmap.fromImage(qimg)

        max_w, max_h = 860, 540
        scaled = self._full.scaled(max_w, max_h, Qt.AspectRatioMode.KeepAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation)
        self._scale = self._full.width() / max(1, scaled.width())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        hint = QLabel("在画面上按住左键拖动，框选要识别的区域")
        hint.setStyleSheet("color:#888888;")
        layout.addWidget(hint)

        self.crop_label = _CropLabel(self._scale)
        self.crop_label.setPixmap(scaled)
        self.crop_label.setFixedSize(scaled.size())
        self.crop_label.on_region = self._on_region
        layout.addWidget(self.crop_label, 0, Qt.AlignmentFlag.AlignCenter)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        cancel = QPushButton("取消")
        cancel.setMinimumWidth(88)
        cancel.clicked.connect(self.reject)
        self.ok_btn = QPushButton("确定")
        self.ok_btn.setObjectName("okBtn")
        self.ok_btn.setMinimumWidth(110)
        self.ok_btn.setEnabled(False)
        self.ok_btn.clicked.connect(self._confirm)
        btn_row.addStretch()
        btn_row.addWidget(cancel)
        btn_row.addWidget(self.ok_btn)
        layout.addLayout(btn_row)

        self.resize(scaled.width() + 30, scaled.height() + 116)

    def _on_region(self, rect):
        # 框选有效（够大）才允许确定
        self.ok_btn.setEnabled(rect is not None and rect.width() >= 5 and rect.height() >= 5)

    def _confirm(self):
        rect = self.crop_label.region()
        if rect is None or rect.width() < 5 or rect.height() < 5:
            return
        # 缩放坐标 → 原图坐标
        crop_rect = QRect(int(rect.x() * self._scale), int(rect.y() * self._scale),
                          int(rect.width() * self._scale), int(rect.height() * self._scale))
        crop = self._full.copy(crop_rect)

        out_dir = os.path.join(get_data_dir(), "element_images")
        os.makedirs(out_dir, exist_ok=True)
        name = f"elem_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]}.png"
        path = os.path.join(out_dir, name)
        crop.save(path)
        self.result_path = path
        self.accept()
