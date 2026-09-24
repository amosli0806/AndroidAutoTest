# services/device_service.py
import uiautomator2 as u2
import time
import sys
import subprocess
import os
from datetime import datetime
from typing import Optional, List
from models.step_model import Step
from utils.adb_path import get_adb_path
from utils.settings import Settings
from utils.theme import ThemeMode


class DeviceService:
    # 不依赖设备的步骤类型：没连设备也能执行。
    # 用途：测试环境里设备连不上时，仍然可以跑纯语音的「文案用例」。
    DEVICE_FREE_STEP_TYPES = frozenset({'voice', 'wait'})

    @classmethod
    def step_needs_device(cls, step_type: str) -> bool:
        """该步骤类型是否必须有设备才能执行"""
        return step_type not in cls.DEVICE_FREE_STEP_TYPES

    def __init__(self, serial: Optional[str] = None):
        self.device = None
        self.serial = serial
        self.element_controller = None
        self.step_interval = 2
        if serial:
            self.connect(serial)

    def apply_theme(self, theme_mode: ThemeMode):
        """应用主题到服务（占位方法，保持接口一致性）"""
        # 设备服务不涉及界面样式，无需实际操作
        pass

    def set_element_controller(self, controller):
        self.element_controller = controller

    def set_step_interval(self, seconds: float):
        self.step_interval = max(0.0, seconds)

    def connect(self, serial: Optional[str] = None) -> bool:
        try:
            self.device = u2.connect(serial) if serial else u2.connect()
            self.serial = serial or self.device.serial
            # 换设备后清空分辨率缓存
            self._cached_screen_size = None
            return True
        except Exception as e:
            if serial:
                self.serial = serial
            self.device = None
            raise Exception(f"连接设备失败: {e}")

    def get_devices(self) -> List[str]:
        """当前可用的 adb 设备序列号列表。

        两个要点：
        1. 统一用内置 adb（utils.adb_path.get_adb_path），与 DeviceWatcher 的
           track-devices 长连接是同一个可执行文件。若这里改用 PATH 里的 adb，
           两个二进制版本不一致时 adb 会互相杀掉对方的 server，
           把长连接一起打断。
        2. 必须带 timeout：调用方是 GUI 线程上的定时刷新，adb 卡住不能把界面拖死。

        注：原先首选的 u2.device.get_devices() 是死路径 —— uiautomator2 模块
        并没有 device 属性，每次都会抛 AttributeError 被静默吞掉，实际一直是
        走下面的 adb 子进程，所以这里直接去掉。
        """
        try:
            result = subprocess.run(
                [get_adb_path(), 'devices'],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                encoding='utf-8', errors='replace',
            )
            lines = result.stdout.strip().split('\n')[1:]
            devices = []
            for line in lines:
                # offline / unauthorized 都不可用，不算数
                if line.strip() and 'device' in line and 'offline' not in line:
                    devices.append(line.split()[0])
            return devices
        except Exception:
            return []

    def restore_ime(self) -> str:
        if not self.device:
            raise Exception("设备未连接，请先连接设备。")

        ime_cmds = [
            "ime set com.android.inputmethod.latin/.LatinIME",
            "ime set com.google.android.inputmethod.latin/com.android.inputmethod.latin.LatinIME"
        ]
        last_error = None
        for cmd in ime_cmds:
            try:
                result = self.device.shell(cmd)
                if hasattr(result, 'output'):
                    output = result.output
                else:
                    output = str(result)
                if output is None:
                    output = ""

                lower_output = output.lower()
                if ("unknown" in lower_output or
                        "cannot be selected" in lower_output or
                        "error" in lower_output or
                        "exception" in lower_output):
                    last_error = output
                    continue

                if "selected" in lower_output or "now" in lower_output:
                    return f"✅ 成功执行命令: {cmd}\n输出: {output}"

                if not output.strip():
                    return f"✅ 成功执行命令: {cmd}\n输出: (无输出，已设置)"

                return f"✅ 成功执行命令: {cmd}\n输出: {output}"

            except Exception as e:
                last_error = str(e)

        raise Exception(
            f"恢复输入法失败，尝试了多种键盘方案均未成功。\n"
            f"最后错误信息: {last_error}\n"
            f"💡 请确保设备已连接且具有调试权限，或手动在设备上设置输入法为 AOSP 键盘。"
        )

    # ---------- 核心方法：perform ----------
    def perform(self, step: Step):
        """执行步骤，失败时抛出用户友好的错误信息"""
        # 设备重连逻辑。
        # 语音播报 / 等待这两类步骤不碰设备，没有连接也要能执行 ——
        # 用途是测试环境版本连不上设备时，仍然可以跑纯语音的「文案用例」。
        if self.step_needs_device(step.type) and not self.device:
            if self.serial:
                try:
                    self.connect(self.serial)
                except Exception as e:
                    raise Exception(f"设备未连接，且重连失败（{self.serial}）: {e}")
            else:
                raise Exception("未连接设备，请先连接设备。")

        # 解析元素ID
        params = step.params.copy()
        if self.element_controller and 'element_id' in params:
            try:
                self.element_controller.resolve_element(params)
            except Exception as e:
                raise Exception(f"元素解析失败: {e}")

        method_name = f"_perform_{step.type}"
        method = getattr(self, method_name, None)
        if method:
            try:
                method(params)
            except Exception as e:
                # 增强错误信息
                friendly_msg = self._format_execution_error(e, step)
                raise RuntimeError(friendly_msg) from e
        else:
            raise ValueError(f"不支持的动作类型: {step.type}")

        # 步骤后间隔（wait / voice 自带等待语义，不再叠加默认间隔）
        if step.type not in ('wait', 'voice') and self.step_interval > 0:
            time.sleep(self.step_interval)

    # ---------- 错误信息格式化 ----------
    def _format_execution_error(self, error, step):
        """将底层异常转换为用户可读的中文错误信息"""
        error_str = str(error)
        loc_type = step.params.get('locationType', '未知')
        loc_value = step.params.get('locationValue', '未知')
        elem_id = step.params.get('element_id', None)
        step_name = step.name or step.type

        # 检查是否为元素未找到异常
        is_not_found = False
        try:
            if isinstance(error, u2.exceptions.UiObjectNotFoundException):
                is_not_found = True
        except AttributeError:
            pass
        if not is_not_found:
            if 'UiObjectNotFoundException' in error_str or 'not found' in error_str.lower():
                is_not_found = True

        if is_not_found:
            base_msg = f"未找到元素，定位方式「{loc_type}」，定位值「{loc_value}」"
            if elem_id:
                base_msg += f"（元素库ID: {elem_id}）"
            base_msg += "。\n可能原因：①当前界面不包含该元素；②定位值已失效或应用版本更新；③页面未加载完成。\n建议：使用「应用可视化」查看实际界面，或改用文本/XPath定位。"
            return base_msg

        # 检查是否为超时异常
        if 'timeout' in error_str.lower() or 'wait' in error_str.lower():
            return f"操作超时：{error_str}"

        # 如果是断言异常，保留其原始消息（可能包含截图路径）
        if isinstance(error, AssertionError):
            return str(error)

        # 默认返回
        return f"步骤执行失败（{step_name}）：{error_str}"

    # ---------- 坐标解析（支持归一化还原）----------
    def _get_current_screen_size(self):
        """获取当前设备分辨率，带缓存。旋转屏幕/换设备后需清空缓存。"""
        if getattr(self, '_cached_screen_size', None):
            return self._cached_screen_size
        try:
            out = self.device.shell("wm size")
            if hasattr(out, 'output'):
                out = out.output
            import re as _re
            m = _re.search(r'(\d+)x(\d+)', str(out))
            if m:
                self._cached_screen_size = (int(m.group(1)), int(m.group(2)))
                return self._cached_screen_size
        except Exception:
            pass
        return (0, 0)

    def _resolve_coord(self, params):
        """
        解析点击类坐标，优先使用归一化坐标按当前设备分辨率还原。
        返回 (x, y)
        """
        loc_value = params.get('locationValue', '')
        screen_w = params.get('screenWidth')
        screen_h = params.get('screenHeight')
        norm_x = params.get('normalizedX')
        norm_y = params.get('normalizedY')

        cur_w, cur_h = self._get_current_screen_size()

        # 优先：归一化坐标还原
        if norm_x is not None and norm_y is not None and cur_w and cur_h:
            return int(round(norm_x * cur_w)), int(round(norm_y * cur_h))

        # 兼容老步骤：直接解析 "x,y"
        try:
            x, y = map(int, loc_value.split(','))
        except Exception:
            raise ValueError(f"无效的坐标定位值: {loc_value}")

        # 若录制分辨率与当前不同，按比例缩放
        if screen_w and screen_h and cur_w and cur_h \
                and (screen_w != cur_w or screen_h != cur_h):
            x = int(round(x / screen_w * cur_w))
            y = int(round(y / screen_h * cur_h))
        return x, y

    def _resolve_swipe_coords(self, params):
        """解析 swipe 的起止坐标，优先用归一化还原。
        返回 (start_x, start_y, end_x, end_y)"""
        cur_w, cur_h = self._get_current_screen_size()
        screen_w = params.get('screenWidth')
        screen_h = params.get('screenHeight')

        nsx = params.get('normalizedStartX')
        nsy = params.get('normalizedStartY')
        nex = params.get('normalizedEndX')
        ney = params.get('normalizedEndY')

        if None not in (nsx, nsy, nex, ney) and cur_w and cur_h:
            return (
                int(round(nsx * cur_w)), int(round(nsy * cur_h)),
                int(round(nex * cur_w)), int(round(ney * cur_h)),
            )

        sx = params.get('startX', 500)
        sy = params.get('startY', 800)
        ex = params.get('endX', 500)
        ey = params.get('endY', 200)

        if screen_w and screen_h and cur_w and cur_h \
                and (screen_w != cur_w or screen_h != cur_h):
            sx = int(round(sx / screen_w * cur_w))
            sy = int(round(sy / screen_h * cur_h))
            ex = int(round(ex / screen_w * cur_w))
            ey = int(round(ey / screen_h * cur_h))

        return sx, sy, ex, ey

    # ---------- 以下为动作方法 ----------
    def _get_ui_object(self, loc_type, loc_value):
        if loc_type == '资源ID':
            return self.device(resourceId=loc_value)
        elif loc_type == '坐标':
            return None
        elif loc_type == '文本':
            return self.device(text=loc_value)
        elif loc_type == '描述':
            return self.device(description=loc_value)
        elif loc_type == 'XPath':
            return self.device.xpath(loc_value)
        else:
            raise ValueError(f"未知定位方式: {loc_type}")

    # 元素操作前的默认等待超时（秒）：页面/动画没渲染完时，先等元素出现再点，
    # 避免「页面还没出来就操作」导致的偶发失败。步骤参数里可传 timeout 覆盖。
    DEFAULT_ELEMENT_WAIT_TIMEOUT = 10

    def _wait_for_object(self, obj, params):
        """等元素出现（带超时）。等到返回 True；超时返回 False。

        uiautomator2 的 click/input 不会自己等元素，找不到就立刻抛「元素不存在」，
        所以这里在操作前用 exists(timeout=…) 主动等，给页面渲染留时间。
        默认超时 10 秒，步骤参数里的 timeout 可覆盖；坐标定位无需等待。
        """
        if obj is None:
            return True
        timeout = params.get('timeout', self.DEFAULT_ELEMENT_WAIT_TIMEOUT)
        try:
            timeout = float(timeout)
        except (TypeError, ValueError):
            timeout = self.DEFAULT_ELEMENT_WAIT_TIMEOUT
        if timeout <= 0:
            return True
        return bool(obj.exists(timeout=timeout))

    def _perform_click(self, params):
        loc_type = params.get('locationType')
        loc_value = params.get('locationValue')
        if loc_type == '坐标':
            x, y = self._resolve_coord(params)
            self.device.click(x, y)
        else:
            obj = self._get_ui_object(loc_type, loc_value)
            if not self._wait_for_object(obj, params):
                raise ValueError(f"等待元素出现超时：{loc_type} = {loc_value}")
            obj.click()

    def _perform_double_click(self, params):
        loc_type = params.get('locationType')
        loc_value = params.get('locationValue')
        if loc_type == '坐标':
            x, y = self._resolve_coord(params)
            self.device.click(x, y)
            time.sleep(0.05)
            self.device.click(x, y)
        else:
            obj = self._get_ui_object(loc_type, loc_value)
            if not self._wait_for_object(obj, params):
                raise ValueError(f"等待元素出现超时：{loc_type} = {loc_value}")
            obj.click()
            time.sleep(0.05)
            obj.click()

    def _perform_long_press(self, params):
        loc_type = params.get('locationType')
        loc_value = params.get('locationValue')
        ms = params.get('longPressMs', 1000)
        if loc_type == '坐标':
            x, y = self._resolve_coord(params)
            self.device.long_click(x, y, duration=ms / 1000)
        else:
            obj = self._get_ui_object(loc_type, loc_value)
            if not self._wait_for_object(obj, params):
                raise ValueError(f"等待元素出现超时：{loc_type} = {loc_value}")
            obj.long_click(duration=ms / 1000)

    def _perform_input(self, params):
        loc_type = params.get('locationType')
        loc_value = params.get('locationValue')
        text = params.get('text', '')
        if loc_type == '坐标':
            x, y = self._resolve_coord(params)
            self.device.click(x, y)
            time.sleep(0.5)
            self.device.send_keys(text)
        else:
            obj = self._get_ui_object(loc_type, loc_value)
            if not self._wait_for_object(obj, params):
                raise ValueError(f"等待元素出现超时：{loc_type} = {loc_value}")
            obj.set_text(text)

    def _perform_wait(self, params):
        duration = params.get('duration', 4)
        time.sleep(duration)

    def _perform_voice(self, params):
        """语音播报：把文案用电脑扬声器读出来，车机麦克风拾取后交给它的语音助手。

        这里**阻塞到这句播完**才返回 —— 后续步骤必须等这段语音放完，
        否则整段序列会抢跑（典型场景：先播唤醒词，再播指令）。
        播完还要再等 afterDelay 秒，留给车机语音助手处理时间。
        """
        text = (params.get('voiceText') or '').strip()
        if not text:
            raise Exception("语音播报的文案为空")

        from services.voice_service import VoiceError, get_voice_service
        try:
            get_voice_service().speak(text)
        except VoiceError as e:
            raise Exception(str(e))

        delay = float(params.get('afterDelay', 0) or 0)
        if delay > 0:
            time.sleep(delay)

    def _perform_swipe(self, params):
        direction = params.get('direction', '自定义坐标')
        if direction == '上滑':
            self.device.swipe(500, 800, 500, 200, duration=0.5)
        elif direction == '下滑':
            self.device.swipe(500, 200, 500, 800, duration=0.5)
        elif direction == '左滑':
            self.device.swipe(800, 500, 200, 500, duration=0.5)
        elif direction == '右滑':
            self.device.swipe(200, 500, 800, 500, duration=0.5)
        elif direction == '自定义坐标':
            start_x, start_y, end_x, end_y = self._resolve_swipe_coords(params)
            self.device.swipe(start_x, start_y, end_x, end_y, duration=0.5)
        else:
            raise ValueError(f"未知滑动方向: {direction}")

    def _perform_drag_drop(self, params):
        from_type = params.get('fromLocationType')
        from_value = params.get('fromValue')
        to_type = params.get('toLocationType')
        to_value = params.get('toValue')

        def get_position(loc_type, loc_value):
            if loc_type == '坐标':
                x, y = map(int, loc_value.split(','))
                return x, y
            else:
                obj = self._get_ui_object(loc_type, loc_value)
                if not obj.exists(timeout=2):
                    raise ValueError(f"元素未找到: {loc_type} = {loc_value}")
                info = obj.info
                bounds = info.get('bounds', {})
                if bounds:
                    x = (bounds['left'] + bounds['right']) // 2
                    y = (bounds['top'] + bounds['bottom']) // 2
                    return x, y
                else:
                    raise ValueError(f"无法获取元素位置: {loc_type} = {loc_value}")

        from_x, from_y = get_position(from_type, from_value)
        to_x, to_y = get_position(to_type, to_value)
        self.device.swipe(from_x, from_y, to_x, to_y, duration=0.5)

    def _perform_multi_swipe(self, params):
        points_str = params.get('points', '')
        duration_ms = params.get('durationMs', 1000)
        points = []
        for p in points_str.split(';'):
            if p.strip():
                x, y = map(int, p.strip().split(','))
                points.append((x, y))
        if len(points) >= 2:
            self.device.swipe_points(points, duration=duration_ms / 1000)

    def _perform_gesture_zoom(self, params):
        gesture_type = params.get('gestureType', '放大')
        center_x = params.get('centerX', 540)
        center_y = params.get('centerY', 960)
        scale = params.get('scale', 1.5)
        if gesture_type == '捏合（缩小）':
            self.device.pinch_in(center=(center_x, center_y), percent=1.0/scale)
        else:
            self.device.pinch_out(center=(center_x, center_y), percent=scale-1.0)

    def _perform_flick(self, params):
        direction = params.get('direction', '上')
        distance = params.get('distance', 300)
        if direction == '上':
            self.device.swipe(500, 800, 500, 800-distance, duration=0.1)
        elif direction == '下':
            self.device.swipe(500, 200, 500, 200+distance, duration=0.1)
        elif direction == '左':
            self.device.swipe(800, 500, 800-distance, 500, duration=0.1)
        elif direction == '右':
            self.device.swipe(200, 500, 200+distance, 500, duration=0.1)

    def _perform_gesture_seq(self, params):
        seq_id = params.get('sequenceId', 'pattern_01')
        print(f"执行手势序列: {seq_id}")

    def _perform_physical_key(self, params):
        key_name = params.get('keyName', '返回')
        key_map = {
            '返回': 'BACK',
            '主页': 'HOME',
            '菜单': 'MENU',
            '音量+': 'VOLUME_UP',
            '音量-': 'VOLUME_DOWN',
            '电源': 'POWER',
            '相机': 'CAMERA'
        }
        key = key_map.get(key_name)
        if key:
            self.device.press(key)
        else:
            raise ValueError(f"未知按键: {key_name}")

    def _perform_screen_ctrl(self, params):
        action = params.get('action', '唤醒屏幕')
        if action == '唤醒屏幕':
            if hasattr(self.device, 'screen_on'):
                self.device.screen_on()
            elif hasattr(self.device, 'wake'):
                self.device.wake()
            else:
                self.device.press("power")
        elif action == '熄灭屏幕':
            if hasattr(self.device, 'screen_off'):
                self.device.screen_off()
            else:
                self.device.press("power")
        elif action == '旋转屏幕':
            self.device.set_orientation('natural')
            self._cached_screen_size = None
        elif action == '固定方向(竖屏)':
            self.device.set_orientation('portrait')
            self._cached_screen_size = None
        elif action == '固定方向(横屏)':
            self.device.set_orientation('landscape')
            self._cached_screen_size = None

    def _perform_app_mgr(self, params):
        action = params.get('action', '启动应用')
        package = params.get('packageName', '')
        if action == '启动应用':
            self.device.app_start(package)
        elif action == '停止应用':
            self.device.app_stop(package)
        elif action == '重启应用':
            self.device.app_stop(package)
            time.sleep(1)
            self.device.app_start(package)
        elif action == '清空应用数据':
            self.device.app_clear(package)
        elif action == '安装应用':
            apk_path = params.get('apkPath', '')
            if apk_path:
                self.device.app_install(apk_path)
        elif action == '卸载应用':
            self.device.app_uninstall(package)

    def _perform_screenshot(self, params):
        # 没传保存路径时用本机配置的输出目录（原来这里写死了某个用户的桌面绝对路径）
        save_path = params.get('savePath') or Settings.get_output_dir()
        if not save_path.endswith('/'):
            save_path += '/'
        file_name = params.get('fileName', 'screenshot')
        timestamp = time.strftime('%Y%m%d_%H%M%S')
        full_path = f"{save_path}{file_name}_{timestamp}.png"
        dir_path = os.path.dirname(full_path)
        self.device.shell(f"mkdir -p {dir_path}")
        try:
            self.device.screenshot(full_path)
            print(f"截图保存成功: {full_path}")
            return
        except Exception as e:
            print(f"uiautomator2 截图失败: {e}")
        try:
            import subprocess
            local_temp = f"temp_screenshot_{timestamp}.png"
            cmd = [get_adb_path(), 'exec-out', 'screencap', '-p']
            with open(local_temp, 'wb') as f:
                subprocess.run(cmd, stdout=f, check=True, timeout=10)
            subprocess.run([get_adb_path(), 'push', local_temp, full_path], check=True, timeout=10)
            os.remove(local_temp)
            print(f"截图保存成功: {full_path} (通过 adb exec-out)")
            return
        except Exception as e2:
            print(f"adb exec-out 截图失败: {e2}")
        try:
            self.device.shell(f"screencap -p {full_path}")
            check = self.device.shell(f"ls {full_path}")
            if 'No such file' not in str(check):
                print(f"截图保存成功: {full_path} (通过 shell)")
                return
        except Exception as e3:
            print(f"shell 截图失败: {e3}")
        raise Exception("截图失败，所有可用方法均无效")

    def _perform_assert(self, params):
        assert_type = params.get('assert_type', '元素存在')
        loc_type = params.get('locationType')
        loc_value = params.get('locationValue')
        expected_value = params.get('expected_value', '')
        timeout = params.get('timeout', 5)
        if not isinstance(timeout, (int, float)):
            timeout = 5

        if loc_type == '坐标':
            raise ValueError("坐标定位不支持断言，请使用资源ID、文本、描述或XPath")

        obj = self._get_ui_object(loc_type, loc_value)
        if obj is None:
            raise ValueError(f"不支持的定位方式: {loc_type} 用于断言")

        screenshot_dir = "screenshots"
        os.makedirs(screenshot_dir, exist_ok=True)

        def take_screenshot():
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            filename = f"assert_fail_{timestamp}.png"
            filepath = os.path.join(screenshot_dir, filename)
            try:
                self.device.screenshot(filepath)
                return filepath
            except Exception as e:
                print(f"截图失败: {e}")
                return None

        if assert_type == '元素存在':
            try:
                exists = obj.exists(timeout=timeout)
            except Exception:
                exists = False
            if not exists:
                screenshot_path = take_screenshot()
                msg = f"断言失败：未找到元素（{loc_type}='{loc_value}'）"
                if screenshot_path:
                    msg += f"\n截图已保存：{screenshot_path}"
                raise AssertionError(msg)

        elif assert_type == '元素不存在':
            exists = obj.exists(timeout=timeout)
            if exists:
                screenshot_path = take_screenshot()
                msg = f"断言失败：元素不应该存在，但找到了（{loc_type}='{loc_value}'）"
                if screenshot_path:
                    msg += f"\n截图已保存：{screenshot_path}"
                raise AssertionError(msg)

        elif assert_type in ('文本等于', '文本包含'):
            if not obj.exists(timeout=timeout):
                screenshot_path = take_screenshot()
                msg = f"断言失败：未找到元素（{loc_type}='{loc_value}'）"
                if screenshot_path:
                    msg += f"\n截图已保存：{screenshot_path}"
                raise AssertionError(msg)
            actual_text = obj.get_text()
            if actual_text is None:
                actual_text = ""
            if assert_type == '文本等于':
                if actual_text != expected_value:
                    screenshot_path = take_screenshot()
                    msg = f"断言失败：文本等于失败，预期值='{expected_value}'，实际值='{actual_text}'"
                    if screenshot_path:
                        msg += f"\n截图已保存：{screenshot_path}"
                    raise AssertionError(msg)
            else:
                if expected_value not in actual_text:
                    screenshot_path = take_screenshot()
                    msg = f"断言失败：文本包含失败，预期包含='{expected_value}'，实际值='{actual_text}'"
                    if screenshot_path:
                        msg += f"\n截图已保存：{screenshot_path}"
                    raise AssertionError(msg)

        else:
            raise ValueError(f"不支持的断言类型: {assert_type}")

    def check_device_online(self, serial: str = None) -> bool:
        """
        快速检查指定设备是否在线（通过 adb devices）
        :param serial: 设备序列号，若为 None 则使用当前 self.serial
        :return: True 表示设备在线，False 表示不在线或检查失败
        """
        if serial is None:
            serial = self.serial
        if not serial:
            return False

        try:
            result = subprocess.run(
                [get_adb_path(), 'devices'],
                capture_output=True,
                text=True,
                timeout=2,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            )
            lines = result.stdout.strip().split('\n')[1:]
            for line in lines:
                if line.strip():
                    parts = line.split()
                    if len(parts) >= 2:
                        dev_serial, state = parts[0], parts[1]
                        if dev_serial == serial and state == 'device':
                            return True
            return False
        except Exception:
            return False