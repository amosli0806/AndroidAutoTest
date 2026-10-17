# utils/image_match.py
"""图像识别定位：在设备截图里找参考图，返回中心坐标。

用 OpenCV 模板匹配（TM_CCOEFF_NORMED，相似度 -1~1，越接近 1 越像）。
cv2 延迟导入：只在真正做图像匹配时才 import，主程序启动路径不背这个开销
（与 utils/element_table.py 对 openpyxl 的做法一致）。

路径用 cv2.imdecode + np.fromfile 读：cv2.imread 在 Windows 下读不了中文路径，
而参考图/截图落在用户目录里，中文路径很常见。
"""
import numpy as np


class ImageNotFoundError(Exception):
    """截图里没找到参考图（相似度低于阈值或图比截图大）。"""


def match(screenshot_path: str, template_path: str, threshold: float = 0.8):
    """在截图里找模板图，返回 (中心x, 中心y, 相似度)。

    相似度低于 threshold 或找不到 → 抛 ImageNotFoundError。
    """
    import cv2

    screen = cv2.imdecode(np.fromfile(screenshot_path, dtype=np.uint8), cv2.IMREAD_COLOR)
    templ = cv2.imdecode(np.fromfile(template_path, dtype=np.uint8), cv2.IMREAD_COLOR)
    if screen is None or templ is None:
        raise ValueError("图片读取失败（路径错误或格式不支持）")
    # 纯色模板零方差，TM_CCOEFF_NORMED 会退化（0/0），OpenCV 会乱返回 1.0 定位到
    # 任何纯色区域 —— 这种参考图本来就定位不了，直接报错比给个错误坐标强。
    # 注意不能只看 np.std：那会跨 R/G/B 三通道算，纯蓝图各像素相同但通道值不同，
    # std 反而很大。要判断的是"每个像素是否都同一个颜色"。
    if (templ == templ[0, 0]).all():
        raise ValueError("参考图是纯色、没有可辨识的特征，无法用于图像匹配")
    sh, sw = screen.shape[:2]
    th, tw = templ.shape[:2]
    if th > sh or tw > sw:
        raise ImageNotFoundError(f"参考图比截图还大（参考 {tw}x{th} vs 截图 {sw}x{sh}），无法匹配")

    res = cv2.matchTemplate(screen, templ, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(res)
    if max_val < threshold:
        raise ImageNotFoundError(f"未找到匹配（最高相似度 {max_val:.2f} < 阈值 {threshold:.2f}）")

    cx = max_loc[0] + tw // 2
    cy = max_loc[1] + th // 2
    return cx, cy, float(max_val)
