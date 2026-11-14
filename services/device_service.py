# services/device_service.py
import uiautomator2 as u2
import time
import sys
import subprocess
import os
from datetime import datetime
from typing import Optional, List
from models.step_model import Step
from services.perf_compat import GUI_TIMEOUT, shell_text
from utils.adb_path import get_adb_path
from utils.settings import Settings
from utils.theme import ThemeMode
from utils.vars import current_values as var_values, resolve_params


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
        # 上一步里出现过、但变量表里没有的 ${变量} 名（由 perform 填，执行层读它告警）
        self.last_var_missing: List[str] = []
        # 坐标点击后「界面有没有反应」的判定结果，由 perform 之外（执行层）读取并告警。
        # 元素定位点不到会抛异常，而坐标点击是 input tap —— 点到屏外、被弹窗盖住、
        # 目标页面没加载完，它都返回成功。不校验就会出现「用例全绿、功能全废」
        # （2026-11-07 实测：坐标算错时 8 个步骤全部报通过，界面一步没动）。
        self.last_click_suspect: Optional[tuple] = None
        # 疑似未生效时的补充原因（如「命中项 clickable=false」），执行层拼进警告
        self.last_click_hint: str = ''
        # 最近一次浅层快查命中的节点属性，供点击后判断「为什么没反应」
        self._quick_locate_info: Optional[dict] = None
        # 失败现场的截图路径（元素等待超时时写入），执行层据此收集到报告
        self.last_screenshot: Optional[str] = None
        # 是否开启点击效果校验：每次坐标点击前后各截一张小图对比，
        # 单步多花约 0.6 秒。对自绘界面（游戏、Unity/Flutter、地图 POI）值得开。
        self.verify_click_effect = True
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

    def disconnect(self):
        """设备掉线：把设备对象与序列号一并清掉。

        为什么必须清：这两个字段只在 connect() 里赋值，掉线后若留着旧值，
        各处「`if not serial:` 提示未连接 / `if not device:` 跳过采集」的守卫
        就永远不会触发 —— 表现就是设备拔了以后点按钮**既不提示也不执行**，
        命令拿着一个已经不存在的序列号去发。设备列表为空时由 main.py 调用。
        """
        self.device = None
        self.serial = None
        # 分辨率缓存跟着设备走，换设备/掉线都要失效
        self._cached_screen_size = None

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

    # 恢复输入法时优先尝试的「通用键盘」——设备上装了才用（见 restore_ime）
    PREFERRED_IMES = (
        "com.android.inputmethod.latin/.LatinIME",
        "com.google.android.inputmethod.latin/com.android.inputmethod.latin.LatinIME",
    )

    def current_ime(self) -> str:
        """当前默认输入法 id；未设置或读不到时返回空串。"""
        raw = shell_text(self.device, "settings get secure default_input_method", timeout=GUI_TIMEOUT).strip()
        if not raw or raw.lower() == "null":
            return ""
        return raw.splitlines()[0].strip()

    def available_imes(self) -> List[str]:
        """设备上**已安装**的输入法 id 列表（`ime list -s`）。

        个别 ROM 没有 `ime` 命令，读不到时返回空列表 —— 调用方据此退回「盲试通用键盘」。
        """
        raw = shell_text(self.device, "ime list -s", timeout=GUI_TIMEOUT)
        return [ln.strip() for ln in raw.splitlines() if ln.strip()]

    def restore_ime(self) -> str:
        """确保设备输入法处于「可用」状态，返回一句可直接展示的结论。

        与旧实现的关键差别：**先看设备当前状态，再决定要不要动手**。
        旧实现不看现状，直接盲试两条写死的 AOSP / Google 键盘命令；车机装的是讯飞这类
        第三方输入法，两条命令都会报 Unknown input method，于是明明输入法好好的，
        却提示「恢复输入法失败」。

        现在的判定顺序：
          1. 当前默认输入法已安装（或设备不支持 `ime list`）-> 判定正常，**不做任何改动**；
          2. 否则按「通用键盘优先 -> 设备里任意可用输入法」逐个 enable + set；
          3. 成功与否以**重新读取默认输入法**为准，不再猜 `ime set` 的输出文字。
        """
        if not self.device:
            raise Exception("设备未连接，请先连接设备。")

        cur = self.current_ime()
        avail = self.available_imes()

        if cur and (not avail or cur in avail):
            return "当前输入法正常，无需恢复"

        # 需要恢复：通用键盘优先，其次设备里任意一个可用输入法
        if avail:
            candidates = [i for i in self.PREFERRED_IMES if i in avail]
            candidates += [i for i in avail if i not in candidates]
        else:
            # 读不到已安装列表（个别 ROM 没有 ime 命令）-> 退回旧行为：盲试通用键盘
            candidates = list(self.PREFERRED_IMES)

        if not candidates:
            raise Exception(
                "恢复输入法失败：设备上读不到任何可用输入法。\n"
                "💡 请手动在设备设置里启用一款输入法后再试。")

        for ime_id in candidates:
            # 未启用的输入法 ime set 会失败，先 enable 一次；失败也无妨，下面以实际状态为准
            shell_text(self.device, f"ime enable {ime_id}", timeout=GUI_TIMEOUT)
            shell_text(self.device, f"ime set {ime_id}", timeout=GUI_TIMEOUT)
            if self.current_ime() == ime_id:
                return f"已恢复输入法：{ime_id}"

        shown = "、".join(candidates[:3]) + (" 等" if len(candidates) > 3 else "")
        raise Exception(
            f"恢复输入法失败：尝试过 {shown}，都没能切换成功。\n"
            f"💡 请手动在设备设置里选择输入法。")

    # ---------- 核心方法：perform ----------
    def perform(self, step: Step, next_step: Optional[Step] = None):
        """执行步骤，失败时抛出用户友好的错误信息。

        next_step 是**下一步**（可选）。只用来判断要不要加步骤间隔：
        「点击 → 等待 2 秒」这种写法，原本是 2（固定间隔）+ 2（等待）= 4 秒，
        同一段时间算了两遍。传了下一步就能跳过这次固定间隔。
        """
        self.last_click_suspect = None      # 每步重置，避免上一步的结论串到这一步
        self.last_click_hint = ''
        self._quick_locate_info = None
        self.last_screenshot = None         # 上一步的失败截图不能挂到这一步
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

        # 变量替换：**必须放在元素解析之后**（元素库里的定位值也可能写 ${变量}），
        # 且在这一层做（而不是各个 _perform_* 里）—— 一处生效，覆盖全部步骤类型。
        params = self._apply_vars(params)

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

        # 步骤后间隔（wait / voice 自带等待语义，不再叠加默认间隔）。
        # 下一步本身就是等待类时同样跳过：那一步会等，这里再加一遍等于白等。
        SELF_WAITING = ('wait', 'voice', 'wait_for')
        if step.type not in SELF_WAITING and self.step_interval > 0:
            if next_step is None or next_step.type not in SELF_WAITING:
                time.sleep(self.step_interval)

    def _apply_vars(self, params: dict) -> dict:
        """把参数里的 `${变量}` 换成值，未定义的**原样保留**并记进 last_var_missing。

        为什么未定义不报错：静默变空会让用例莫名失败、还查不出原因；直接抛异常又会
        因为一个手误把整轮用例打断。原样保留 + 交给执行层在日志里点出来，最好排查。
        变量表本身读不出来时也只是跳过替换（不能因为变量表坏了就执行不了步骤）。
        """
        self.last_var_missing = []
        try:
            resolved, missing = resolve_params(params, var_values(serial=self.serial or ""))
        except Exception as e:
            print(f"[vars] 变量替换已跳过（{type(e).__name__}: {e}）")
            return params
        self.last_var_missing = missing
        return resolved

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

        # 检查是否为超时异常。
        # 不能用全文包含 "wait" 来判：报错里带着现场截图路径（wait_fail_xxx.png），
        # 文件名本身就会命中，导致「操作超时：等待元素出现超时：…」这种重复前缀。
        # 中文「超时」已经是完整说法，直接原样返回；底层英文异常才补前缀。
        if '超时' in error_str:
            return error_str
        if 'timeout' in error_str.lower():
            return f"操作超时：{error_str}"

        # 如果是断言异常，保留其原始消息（可能包含截图路径）
        if isinstance(error, AssertionError):
            return str(error)

        # 默认返回
        return f"步骤执行失败（{step_name}）：{error_str}"

    # ---------- 层级导出（录制反查 / 元素拾取共用）----------
    def dump_ui_xml(self) -> Optional[str]:
        """导出当前界面完整层级 XML；失败返回 None。

        优先 uiautomator2 的 `dump_hierarchy()`（一次 RPC，最快），失败再回退
        `adb shell uiautomator dump`：先试 `--compressed`（绕过 idle 检测，
        界面一直有动画/游戏时普通 dump 会卡在 "could not get idle state"）。

        注意：调用方要自己控制频率。车机上单次完整 dump 实测 8~12 秒，
        拾取面板是「打开时 dump 一次、之后本地命中」，不要每点一次都调。
        """
        device = getattr(self, 'device', None)
        if device is None:
            return None

        try:
            if hasattr(device, 'dump_hierarchy'):
                xml_str = device.dump_hierarchy()
            elif hasattr(device, 'dump'):
                xml_str = device.dump()
            else:
                xml_str = None
            if xml_str:
                return xml_str
        except Exception:
            pass

        # 回退：adb shell uiautomator dump → pull 回本地读
        import tempfile
        try:
            stamp = int(time.time() * 1000)
            remote = f"/sdcard/ui_dump_{stamp}.xml"
            local = os.path.join(tempfile.gettempdir(), f"ui_dump_{stamp}.xml")
            for extra in (['--compressed'], []):
                cmd = [get_adb_path(), 'shell', 'uiautomator', 'dump'] + extra + [remote]
                r = subprocess.run(cmd, capture_output=True, timeout=8, check=False)
                out = (r.stdout or b'') + (r.stderr or b'')
                if b'could not get idle state' in out or b'ERROR' in out:
                    continue
                subprocess.run([get_adb_path(), 'pull', remote, local],
                               capture_output=True, timeout=5, check=False)
                if os.path.exists(local):
                    with open(local, 'r', encoding='utf-8', errors='ignore') as f:
                        xml_str = f.read()
                    try:
                        os.remove(local)
                    except Exception:
                        pass
                    subprocess.run([get_adb_path(), 'shell', 'rm', remote],
                                   capture_output=True, timeout=2, check=False)
                    if xml_str:
                        return xml_str
        except Exception:
            pass
        return None

    # ---------- 坐标解析（支持归一化还原）----------
    def _get_current_screen_size(self):
        """获取当前**显示方向**下的分辨率，带缓存。旋转屏幕/换设备后需清空缓存。

        取 `wm size` 的物理尺寸，再按 displayRotation 换算成当前朝向。

        为什么不用 window_size()：它给的是**应用可用区域**，车机/带系统栏或
        分屏的设备会比物理尺寸小一截（比如 2560x1440 只给 2560x1300），拿它
        还原归一化坐标会整体按比例偏移。而 `wm size` 是固定的物理值，改动前后
        行为一致，不会给车机引入新风险。

        为什么必须换算朝向：`wm size` 是**面板原始朝向**。手机横屏游戏下它仍是
        竖屏的 1084x2412，直接用会让点击整体错位（2026-11-07 实测：战斗按钮的
        坐标被算成 (94, 2125)，y 超出屏高 1084，点击落到屏外、界面毫无反应）。
        """
        if getattr(self, '_cached_screen_size', None):
            return self._cached_screen_size
        try:
            out = self.device.shell("wm size")
            if hasattr(out, 'output'):
                out = out.output
            import re as _re
            txt = str(out)
            # 优先 Override size：车机 / 投屏改过分辨率时只认它
            m = (_re.search(r'Override size:\s*(\d+)x(\d+)', txt)
                 or _re.search(r'Physical size:\s*(\d+)x(\d+)', txt)
                 or _re.search(r'(\d+)x(\d+)', txt))
            if m:
                w, h = int(m.group(1)), int(m.group(2))
                # 已经和屏幕朝向一致（本身横屏的设备，如车机）就不再交换
                if self._display_rotation() in (1, 3) and w < h:
                    w, h = h, w
                self._cached_screen_size = (w, h)
                return self._cached_screen_size
        except Exception:
            pass
        return (0, 0)

    def _display_rotation(self) -> int:
        """当前屏幕旋转角度（0/90/180/270 对应 0/1/2/3）"""
        try:
            return int(self.device.info.get('displayRotation', 0))
        except Exception:
            return 0

    def _norm_base_size(self, params, cur_w, cur_h, max_norm=1.0):
        """归一化坐标该乘哪个尺寸：正常情况下用当前朝向尺寸，老数据退回录制尺寸。

        为什么需要这条：录制端（step_controller._record_loop）一直按 `wm size`
        的**物理尺寸**存 normalizedX/Y，但画面可能是旋转过的 —— 横屏游戏里坐标
        最大到 2412，基准却只有 1084，norm 会大于 1。这种数据只能用录制时的
        screenWidth/Height 还原：两端用同一套尺寸才等价，换成当前朝向的尺寸会
        整体旋转错位（实测 2.2 倍偏差，点哪都不是）。

        录制基准与当前朝向一致时（正常竖屏、以及本身就是横屏的车机）才按当前
        尺寸换算 —— 这样换设备、换分辨率仍能按比例自适应。
        """
        step_w = params.get('screenWidth')
        step_h = params.get('screenHeight')
        if not (step_w and step_h and cur_w and cur_h):
            return cur_w, cur_h
        # 方向不一致（录制时基准是竖的、当前是横的）= 老数据，按录制基准还原
        if (step_w >= step_h) != (cur_w >= cur_h):
            return step_w, step_h
        # 比例超过 1 说明坐标跑出了自己的基准，同样只能按录制基准还原
        if max_norm > 1.0001:
            return step_w, step_h
        return cur_w, cur_h

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
            base_w, base_h = self._norm_base_size(
                params, cur_w, cur_h, max(norm_x, norm_y))
            return int(round(norm_x * base_w)), int(round(norm_y * base_h))

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
            base_w, base_h = self._norm_base_size(
                params, cur_w, cur_h,
                max(nsx, nsy, nex, ney))
            return (
                int(round(nsx * base_w)), int(round(nsy * base_h)),
                int(round(nex * base_w)), int(round(ney * base_h)),
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
    def _get_ui_object(self, loc_type, loc_value, instance=None):
        if loc_type == '资源ID':
            sel = self.device(resourceId=loc_value)
            # 资源ID 重复时，用 instance 精确定位到第 N 个（录制时算出来的序号）
            if instance is not None:
                try:
                    instance = int(instance)
                    sel = sel.instance(instance)
                except (TypeError, ValueError):
                    pass
            return sel
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

    # 主定位能占用的预算比例，剩下的留给 fallback。
    # 之前两者各自等满 timeout，填 5 秒实际最坏 10 秒、填 10 秒最坏 20 秒
    # （2026-11-07 实测：配 5 秒跑了 10.24 秒），配了重试还会翻倍。
    PRIMARY_WAIT_RATIO = 0.6

    def _step_timeout(self, params) -> float:
        """这一步允许等待的总秒数（步骤参数 timeout，缺省用默认值）"""
        timeout = params.get('timeout', self.DEFAULT_ELEMENT_WAIT_TIMEOUT)
        try:
            timeout = float(timeout)
        except (TypeError, ValueError):
            timeout = self.DEFAULT_ELEMENT_WAIT_TIMEOUT
        return timeout

    def _wait_for_object(self, obj, params, timeout=None):
        """等元素出现（带超时）。等到返回 True；超时返回 False。

        uiautomator2 的 click/input 不会自己等元素，找不到就立刻抛「元素不存在」，
        所以这里在操作前用 exists() 主动等，给页面渲染留时间。
        timeout 显式传入时以传入为准（总预算封顶用），否则取步骤参数；
        坐标定位无需等待。

        **为什么自己轮询而不是 exists(timeout=N)**：u2 的 timeout 是在它内部循环里
        判的，但单次 UI dump 本身就可能要 10 秒（百度地图这类大页面实测 10~12 秒），
        一个 dump 开始后没法中断。实测配 5 秒的等待跑了 10.24 秒、配 6 秒跑了 27 秒 ——
        预算形同虚设。这里改成「单次 dump（exists(timeout=0)）+ 自己判 deadline」，
        最坏只超支一次 dump 的时间，不会累积。
        """
        if obj is None:
            return True
        if timeout is None:
            timeout = self._step_timeout(params)
        try:
            timeout = float(timeout)
        except (TypeError, ValueError):
            timeout = self.DEFAULT_ELEMENT_WAIT_TIMEOUT
        if timeout <= 0:
            return True

        deadline = time.monotonic() + timeout
        while True:
            if obj.exists(timeout=0):        # 单次 dump，不进入 u2 的内部轮询
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.2)

    def _resolve_ui_object(self, params):
        """取元素对象：主定位等元素出现，失败时自动退到 fallback 定位（双保险）。

        录制/反查时会同时保留资源ID和文本：资源ID 可能因 App 升级失效，
        文本可能因状态变化失效 —— 一个失配就退回另一个，降低步骤偶发失败率。

        **两步共享同一份超时预算**：用户填的 timeout 就是「这一步最多等多久」，
        主定位最多拿走 PRIMARY_WAIT_RATIO，余下的给 fallback。都失败时抛
        ValueError，并在报错里写清各自等了多久 —— 否则只看到一句「超时」，
        根本不知道该调大超时还是该修定位值。
        """
        loc_type = params.get('locationType')
        loc_value = params.get('locationValue')
        obj = self._get_ui_object(loc_type, loc_value, params.get('instance'))
        if obj is None:
            return None                     # 坐标定位：无需元素对象

        total = self._step_timeout(params)
        if total <= 0:
            return obj                      # 配置了 0 = 明确不等

        deadline = time.monotonic() + total
        start = deadline - total
        primary_budget = total * self.PRIMARY_WAIT_RATIO
        if self._wait_for_object(obj, params, timeout=primary_budget):
            return obj
        primary_cost = primary_budget

        # 主定位超时，用剩下的预算尝试 fallback
        fb_type = params.get('fallbackType')
        fb_value = params.get('fallbackValue')
        fb_cost = 0.0
        if fb_type and fb_value:
            remain = deadline - time.monotonic()
            if remain > 0.3:
                fb_obj = self._get_ui_object(fb_type, fb_value, None)
                if fb_obj is not None and self._wait_for_object(
                        fb_obj, params, timeout=remain):
                    return fb_obj
                fb_cost = remain

        used = time.monotonic() - start
        detail = f"主定位 {primary_cost:.1f}s"
        if fb_cost:
            detail += f" + 备用定位 {fb_cost:.1f}s"
        msg = (f"等待元素出现超时：{loc_type} = {loc_value}"
               f"（{detail}，共 {used:.1f}s / 预算 {total:.0f}s）")
        msg += self._capture_failure_context(loc_type, loc_value)
        raise ValueError(msg)

    def _similar_nodes(self, xml, loc_type, loc_value, limit=3):
        """从界面树里挑最接近目标的几个节点，为「是不是文案/ID 改了」提供线索。

        用标准库 difflib 做相似度，不引新依赖；资源ID 只比最后一段，
        否则 com.xxx:id/ 这个共同前缀会把不同控件的相似度拉到差不多。
        """
        attr = {'文本': 'text', '资源ID': 'resource-id',
                '描述': 'content-desc'}.get(loc_type)
        if not attr:
            return []
        import re as _re
        import difflib
        attr_pat = _re.compile(rf'\s{attr}="([^"]*)"')
        bounds_pat = _re.compile(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"')
        key_target = (loc_value.rsplit('/', 1)[-1]
                      if loc_type == '资源ID' else loc_value)
        seen, scored = set(), []
        for m in _re.finditer(r'<node[^>]*>', xml):
            tag = m.group(0)
            v = attr_pat.search(tag)
            if not v:
                continue
            val = v.group(1)
            if not val or val in seen:
                continue
            key_val = (val.rsplit('/', 1)[-1]
                       if loc_type == '资源ID' else val)
            score = difflib.SequenceMatcher(None, key_target, key_val).ratio()
            if key_target in key_val or key_val in key_target:
                score = max(score, 0.85)
            if score < 0.5:
                continue
            seen.add(val)
            b = bounds_pat.search(tag)
            scored.append((round(score, 2), val, attr,
                           b.group(0).replace('bounds="', '').rstrip('"') if b else ''))
        scored.sort(reverse=True)
        return scored[:limit]

    def _capture_failure_context(self, loc_type, loc_value) -> str:
        """元素等不到时把现场留下来：截图 + 界面树 + 最接近的几个候选。

        返回拼在报错后面的说明；任何一步失败都不影响原报错抛出 ——
        现场是加分项，不能因为它出错把主因盖掉。
        """
        try:
            shot_dir = self._screenshot_dir()
            os.makedirs(shot_dir, exist_ok=True)
        except Exception:
            return ''

        ts = datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]
        lines = []

        shot_path = os.path.join(shot_dir, f"wait_fail_{ts}.png")
        try:
            self.device.screenshot(shot_path)
            self.last_screenshot = shot_path    # 执行层据此把图收进报告
            lines.append(f"截图：{shot_path}")
        except Exception:
            pass

        xml = ''
        try:
            xml = self.device.dump_hierarchy()
        except Exception:
            pass
        if xml:
            xml_path = os.path.join(shot_dir, f"wait_fail_{ts}.xml")
            try:
                with open(xml_path, 'w', encoding='utf-8') as f:
                    f.write(xml)
                lines.append(f"界面树：{xml_path}")
            except Exception:
                pass
            candidates = self._similar_nodes(xml, loc_type, loc_value)
            if candidates:
                lines.append("接近的元素（定位值是不是改了？）：")
                for _score, val, attr, bounds in candidates:
                    lines.append(f"  · {val}   [{attr}]   {bounds}")
            else:
                lines.append("页面上没有文本 / ID 相近的元素，可能整块界面都没加载出来")

        return ("\n" + "\n".join(lines)) if lines else ''

    def _capture_screenshot(self) -> str:
        """截图到临时文件（图像匹配用），返回路径；失败抛异常。"""
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".png", prefix="imgmatch_")
        os.close(fd)
        try:
            self.device.screenshot(path)
        except Exception as e:
            raise ValueError(f"截图失败：{e}")
        return path

    @staticmethod
    def _resolve_image_template(value) -> str:
        """图像定位值 → 可读的模板图路径。

        允许两种写法：
            完整路径 —— 「截屏取图」和应用可视化框选给出的都是这种；
            仅文件名 —— 旧版应用可视化存进元素库时只写了 basename，
                       到 data/element_images/ 下找回来，找不到原样返回
                       由调用方报「参考图不存在」。
        """
        text = str(value or "").strip()
        if not text:
            return ""
        if os.path.isfile(text):
            return text
        try:
            from utils.app_paths import get_data_dir
            candidate = os.path.join(get_data_dir(), "element_images",
                                     os.path.basename(text))
            if os.path.isfile(candidate):
                return candidate
        except Exception:
            pass
        return text

    def _image_center(self, params):
        """图像定位：截图 → 模板匹配 → 中心坐标。找不到抛异常。"""
        from utils.image_match import match, ImageNotFoundError
        template = self._resolve_image_template(params.get('locationValue', ''))
        threshold = params.get('imageThreshold', 0.8)
        if not template or not os.path.isfile(template):
            raise ValueError(f"参考图不存在：{params.get('locationValue', '')}")
        shot = self._capture_screenshot()
        try:
            x, y, _ = match(shot, template, threshold=threshold)
        except ImageNotFoundError as e:
            raise ValueError(f"图像未匹配到：{e}") from e
        finally:
            try:
                os.remove(shot)
            except OSError:
                pass
        return x, y

    # ---- 浅层快查（文本 / 资源ID / 描述定位）----
    # 百度地图这类大页面，一次完整 dump 要 8~12 秒，而 uiautomator2 的 selector
    # 查询（exists / count）每次都要重跑一遍全量 dump；页面切换动画期间还可能连续
    # 查两三次才命中，一步就要近 30 秒（2026-11-07 实测）。
    # 限深度 dump 只要 5 秒（16 层足够覆盖底部 tab、按钮这类常用控件），
    # 本地解析 XML 取 bounds 中心直接点。命中即用，未命中回退到完整查找，
    # 只是加速，不改变最终结果。
    QUICK_DUMP_DEPTH = 16
    QUICK_LOCATE_TRIES = 2
    QUICK_LOCATE_GAP = 0.3

    def _quick_locate(self, params):
        """浅层 dump 快速定位。命中返回 (x, y)，未命中或不适用的定位方式返回 None。

        同一段文本常命中多个节点（外层可点的容器 + 内层不可点的文字），所以**优先
        挑 clickable 且 enabled 的**，点容器才是有效点击。只有不可点的命中时不返回
        None 而是照样返回坐标 —— 自绘界面（游戏、Flutter）整棵树都是 clickable=false，
        不返回就彻底用不了；这种情况把属性记进 _quick_locate_info，
        点击没反应时用来给出更准的原因。
        """
        loc_type = params.get('locationType')
        loc_value = params.get('locationValue')
        attr = {'文本': 'text', '资源ID': 'resource-id',
                '描述': 'content-desc'}.get(loc_type)
        if not loc_value or attr is None:
            return None

        import re as _re
        node_pat = _re.compile(r'<node[^>]*>')
        attr_pat = _re.compile(rf'\s{attr}="([^"]*)"')
        bounds_pat = _re.compile(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"')
        flag_pat = _re.compile(r'\s(clickable|enabled)="(\w+)"')

        for attempt in range(self.QUICK_LOCATE_TRIES):
            try:
                xml = self.device.dump_hierarchy(max_depth=self.QUICK_DUMP_DEPTH)
            except Exception:
                return None
            fallback = None          # 第一个不可点的命中，留作兜底
            for node in node_pat.finditer(xml):
                tag = node.group(0)
                m = attr_pat.search(tag)
                if not m:
                    continue
                val = m.group(1)
                # 文本要一字不差；资源ID 允许只写尾部（用户常填 new_user_text）
                hit = (val == loc_value) if loc_type == '文本' else (loc_value in val)
                if not hit:
                    continue
                b = bounds_pat.search(tag)
                if not b:
                    continue
                x1, y1, x2, y2 = map(int, b.groups())
                if x2 <= x1 or y2 <= y1:
                    continue
                cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
                # 只接受落在屏幕内的命中：存在但被滑出视野的控件中心点会在
                # 屏幕外，那种情况交给完整查找去走「等元素出现」更稳妥
                sw, sh = self._get_current_screen_size()
                if sw and sh and not (0 <= cx < sw and 0 <= cy < sh):
                    continue

                flags = dict(flag_pat.findall(tag))
                clickable = flags.get('clickable') == 'true'
                enabled = flags.get('enabled', 'true') == 'true'
                if clickable and enabled:
                    self._quick_locate_info = {
                        'clickable': True, 'enabled': True, 'value': val}
                    return cx, cy
                if fallback is None:
                    fallback = (cx, cy, clickable, enabled, val)

            if fallback is not None:
                cx, cy, clickable, enabled, val = fallback
                self._quick_locate_info = {
                    'clickable': clickable, 'enabled': enabled, 'value': val}
                return cx, cy
            if attempt + 1 < self.QUICK_LOCATE_TRIES:
                time.sleep(self.QUICK_LOCATE_GAP)
        return None

    def _resolve_click_target(self, params):
        """返回点击目标：True 表示坐标 (x, y)，False 表示 uiautomator2 对象。
        图像定位走截图匹配 → 坐标，这样点击族动作就不用各自判断。"""
        loc_type = params.get('locationType')
        if loc_type == '坐标':
            x, y = self._resolve_coord(params)
            return True, x, y
        if loc_type == '图像':
            x, y = self._image_center(params)
            return True, x, y
        # 文本/资源ID/描述：先走浅层快查，命中就直接按坐标点（省掉全量 dump）
        quick = self._quick_locate(params)
        if quick is not None:
            return True, quick[0], quick[1]
        return False, self._resolve_ui_object(params), None

    # ---- 坐标点击的「有没有反应」校验 ----
    CLICK_EFFECT_SETTLE = 0.7       # 点击后等界面反应的时间（秒）
    CLICK_EFFECT_MIN_DIFF = 1.5     # 48x48 灰度平均差低于它 = 疑似没反应

    def _assert_in_screen(self, x, y):
        """坐标越界直接报错。

        原来什么都不判：算错的坐标（比如横屏没换算时的 y=2125）照样 submit 给
        `input tap`，系统静默丢弃，步骤却报成功 —— 这是「假通过」最隐蔽的一种。
        """
        sw, sh = self._get_current_screen_size()
        if not sw or not sh:
            return
        if not (0 <= x < sw and 0 <= y < sh):
            raise ValueError(
                f"点击坐标越界：({x},{y}) 超出屏幕范围 {sw}x{sh}。"
                f"常见原因是录制分辨率与执行分辨率不一致，或坐标未按屏幕朝向换算。")

    def _grab_thumb(self, size=48):
        """截一张小灰度图用于前后对比。失败返回 None（不打断执行）。"""
        try:
            from PIL import Image
            shot = self.device.screenshot()
            if isinstance(shot, (bytes, bytearray)):
                import io
                shot = Image.open(io.BytesIO(shot))
            return shot.convert('L').resize((size, size)).tobytes()
        except Exception:
            return None

    def _click_hint_from_info(self) -> str:
        """命中节点本身有「不该点」的属性时，给一句具体原因"""
        info = self._quick_locate_info
        if not info:
            return ''
        val = info.get('value', '')
        if not info.get('enabled', True):
            return f'命中的「{val}」是禁用状态（enabled=false）'
        if not info.get('clickable', True):
            return (f'命中的「{val}」不可点击（clickable=false）——'
                    f'可能被弹窗盖住、还在渲染，或只是外层容器上的文字')
        return ''

    def _judge_click_effect(self, before, x, y):
        """点击后界面几乎没变化 → 记「疑似未生效」，交给执行层告警。

        阈值按 48x48 灰度平均绝对差定：正常跳转/弹窗在 10 以上，静态页面的
        轻微动画（时钟、波形）一般 < 1.5。只提示不判失败 —— 有些点击本来就
        不改变界面（聚焦输入框、点在地图空白处只移动视野），误判成失败会更烦。
        """
        self.last_click_suspect = None
        self.last_click_hint = ''
        try:
            time.sleep(self.CLICK_EFFECT_SETTLE)
            after = self._grab_thumb()
            if not after or len(after) != len(before):
                return
            diff = sum(abs(a - b) for a, b in zip(before, after)) / len(before)
            if diff < self.CLICK_EFFECT_MIN_DIFF:
                self.last_click_suspect = (x, y, round(diff, 2))
                self.last_click_hint = self._click_hint_from_info()
        except Exception:
            self.last_click_suspect = None

    def _perform_click(self, params):
        is_coord, target, y = self._resolve_click_target(params)
        if not is_coord:
            target.click()
            return
        self._assert_in_screen(target, y)
        before = self._grab_thumb() if self.verify_click_effect else None
        self.device.click(target, y)
        if before is not None:
            self._judge_click_effect(before, target, y)

    def _perform_double_click(self, params):
        is_coord, target, y = self._resolve_click_target(params)
        if is_coord:
            self.device.click(target, y)
            time.sleep(0.05)
            self.device.click(target, y)
        else:
            target.click()
            time.sleep(0.05)
            target.click()

    def _perform_long_press(self, params):
        ms = params.get('longPressMs', 1000)
        is_coord, target, y = self._resolve_click_target(params)
        if is_coord:
            self.device.long_click(target, y, duration=ms / 1000)
        else:
            target.long_click(duration=ms / 1000)

    def _perform_input(self, params):
        text = params.get('text', '')
        is_coord, target, y = self._resolve_click_target(params)
        if is_coord:
            self.device.click(target, y)
            time.sleep(0.5)
            self.device.send_keys(text)
        else:
            target.set_text(text)

    def _perform_wait(self, params):
        duration = params.get('duration', 4)
        time.sleep(duration)

    def _perform_wait_for(self, params):
        """显式等待：等元素出现 / 消失 / 可点击，到点就继续、超时才判失败。

        与「等待」（固定秒数）的区别：不确定要等多久时用它 —— 页面一准备好就继续，
        比拍脑袋 sleep 更稳也更快。复用 uiautomator2 的 exists()/info 轮询。
        """
        wait_type = params.get('wait_type', '元素出现')
        loc_type = params.get('locationType')
        loc_value = params.get('locationValue')
        timeout = self._step_timeout(params)
        if timeout <= 0:
            timeout = self.DEFAULT_ELEMENT_WAIT_TIMEOUT
        if loc_type == '坐标':
            raise ValueError("显式等待不支持坐标定位，请用资源ID、文本、描述或XPath")

        start = time.time()

        if loc_type == '图像':
            # 图像显式等待只支持「元素出现 / 元素消失」（图像没有"可点击"概念）
            if wait_type not in ('元素出现', '元素消失'):
                raise ValueError("图像定位只支持「元素出现 / 元素消失」显式等待")
            deadline = start + timeout
            while True:
                remain = deadline - time.time()
                if remain <= 0:
                    break
                try:
                    self._image_center(params)
                    found = True
                except Exception:
                    found = False
                if wait_type == '元素出现' and found:
                    return
                if wait_type == '元素消失' and not found:
                    return
                # 截图较慢，轮询间隔 1s；但不能睡过 deadline，否则超时后还多等
                gap = min(1.0, max(0.0, deadline - time.time()))
                if gap <= 0:
                    break
                time.sleep(gap)
            raise RuntimeError(
                f"等待超时（{wait_type}，图像='{loc_value}'，"
                f"预算 {timeout:g} 秒，实际 {time.time() - start:.1f} 秒）")

        obj = self._get_ui_object(loc_type, loc_value)
        if obj is None:
            raise ValueError(f"不支持的定位方式: {loc_type}")

        deadline = start + timeout
        while True:
            remain = deadline - time.time()
            if remain <= 0:
                break
            try:
                if wait_type == '元素出现':
                    ok = obj.exists(timeout=min(0.5, remain))
                elif wait_type == '元素消失':
                    ok = not obj.exists(timeout=min(0.5, remain))
                else:  # 元素可点击
                    # 只看 clickable 太宽：按钮还在但已置灰（enabled=False）、或被滑出
                    # 可视区（visible=False）时，clickable 仍可能是 True，等待会提前通过，
                    # 随后点了个「名义上能点、实际点不动」的控件，比超时更难查。
                    info = obj.info
                    ok = bool(info and info.get('clickable')
                              and info.get('enabled', True)
                              and info.get('visible', True))
            except Exception:
                ok = False
            if ok:
                return
            gap = min(0.5, max(0.0, deadline - time.time()))
            if gap <= 0:
                break
            time.sleep(gap)
        raise RuntimeError(
            f"等待超时（{wait_type}，{loc_type}='{loc_value}'，"
            f"预算 {timeout:g} 秒，实际 {time.time() - start:.1f} 秒）")

    def _perform_voice(self, params):
        """语音播报：把文案用电脑扬声器读出来，车机麦克风拾取后交给它的语音助手。

        这里**阻塞到这句播完**才返回 —— 后续步骤必须等这段语音放完，
        否则整段序列会抢跑（典型场景：先播唤醒词，再播指令）。

        afterDelay 字段是「播后等待」= **播完之后的纯缓冲**（与语速、播报时长无关）：
        播报本身已经阻塞等完了，这里再额外睡 afterDelay 秒，给车机
        「听懂 → 处理 → 回话说完」以及页面加载留时间。

        **不做回执验证**：验证的判定依据是「预期结果」关键词，而 voice 步骤
        本身没有这个概念（关键词是按用例配在语音播报页的检测步骤上的）。
        设置页的回执验证开关只对语音播报页里配了检测步骤的用例生效。
        """
        text = (params.get('voiceText') or '').strip()
        if not text:
            raise Exception("语音播报的文案为空")

        from services.voice_service import VoiceError, get_voice_service
        from utils import voice_log

        short = text[:20] + ("…" if len(text) > 20 else "")

        try:
            voice_log.emit("info", f"🔊 开始播报「{short}」")
            get_voice_service().speak(text)
        except VoiceError as e:
            voice_log.emit("error", f"❌ 播报失败「{short}」：{e}")
            raise Exception(str(e))

        # 播后等待：afterDelay 就是纯缓冲，播报耗时不再从里面扣
        delay = float(params.get('afterDelay', 2) or 0)
        remain = max(0.0, delay)
        if remain > 0:
            time.sleep(remain)

        voice_log.emit("success", f"✅ 播报完成「{short}」（语音步骤不做回执验证）")

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

    def _screenshot_dir(self) -> str:
        """断言失败截图的落地目录：`{输出目录}/{设备序列号}/screenshots`。

        与「测试报告」「性能检测截图」同一套约定。原来写的是相对路径 `screenshots/`，
        跟着进程 CWD 走 —— 换个启动方式就找不到图，报告里也会显示"截图文件不存在"。
        """
        from utils.settings import Settings
        from utils.serial_helper import safe_serial
        base = Settings.get_output_dir()
        serial = (getattr(self, "serial", "") or "").strip()
        if serial:
            base = os.path.join(base, safe_serial(serial))
        return os.path.join(base, "screenshots")

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

        if loc_type == '图像':
            # 图像断言只支持「存在 / 不存在」：截图匹配判定
            if assert_type not in ('元素存在', '元素不存在'):
                raise ValueError("图像定位只支持「元素存在 / 元素不存在」断言")
            shot_dir = self._screenshot_dir()
            os.makedirs(shot_dir, exist_ok=True)

            def img_shot():
                fp = os.path.join(shot_dir, f"assert_fail_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]}.png")
                try:
                    self.device.screenshot(fp)
                    return fp
                except Exception:
                    return None

            try:
                self._image_center(params)
                found = True
            except Exception:
                found = False
            if assert_type == '元素存在' and not found:
                p = img_shot()
                raise AssertionError(f"断言失败：图像未匹配到（{loc_value}）" + (f"\n截图已保存：{p}" if p else ""))
            if assert_type == '元素不存在' and found:
                p = img_shot()
                raise AssertionError(f"断言失败：图像不应存在，但匹配到了（{loc_value}）" + (f"\n截图已保存：{p}" if p else ""))
            return

        obj = self._get_ui_object(loc_type, loc_value)
        if obj is None:
            raise ValueError(f"不支持的定位方式: {loc_type} 用于断言")

        screenshot_dir = self._screenshot_dir()
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