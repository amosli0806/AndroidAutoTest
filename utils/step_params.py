# utils/step_params.py
"""步骤参数的共享定义：中文名、枚举选项、内部字段判定。

原先这份「参数中文名」内联在 `controllers/step_controller.py` 的「更新步骤」对话框里，
现在「批量修改步骤参数」对话框（`views/dialogs/batch_edit_dialog.py`）也要用同一套名字。
两处各存一份，迟早出现「改了一处、另一处不生效」（项目里 `LABEL_MAP` 就栽过），
所以集中到这里，两边都 import 这一份。
"""

# 不参与编辑 / 不在界面上展示的内部字段
#   element_id 是「元素库绑定」这种关联关系，不是可手改的参数
#   normalized* / screenWidth / screenHeight 是按比例换算用的内部快照
INTERNAL_KEYS = {
    'element_id',
    'screenWidth', 'screenHeight',
    'normalizedX', 'normalizedY',
    'normalizedStartX', 'normalizedStartY',
    'normalizedEndX', 'normalizedEndY',
}

# 参数中文名（「更新步骤」与「批量修改参数」共用）
PARAM_LABELS = {
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
    'voiceText': '播报文案', 'afterDelay': '播后等待(秒)',
    'wait_type': '等待方式',
}

# 「定位方式」类字段：取值是固定枚举
LOCATION_TYPE_KEYS = {
    'locationType', 'fromLocationType', 'toLocationType', 'fallbackType',
}

LOCATION_TYPE_OPTIONS = ['资源ID', '坐标', '文本', '描述', 'XPath', '图像']

# 绑定了元素库（element_id）的步骤，运行时 device_service.execute_step 会调
# element_controller.resolve_element() 用**元素库里的定位方式/定位值覆盖**这两个字段，
# 所以对这类步骤改这两个字段不会生效 —— 批量修改时要单独提示并让用户选作用范围。
ELEMENT_OVERRIDDEN_KEYS = {'locationType', 'locationValue'}

# 取值固定、与步骤类型无关的字段
FIXED_OPTIONS = {
    'assert_type': ['元素存在', '元素不存在', '文本等于', '文本包含'],
    'keyName': ['返回', '主页', '菜单', '音量+', '音量-', '电源', '相机'],
    'gestureType': ['捏合（缩小）', '放大'],
}

# 取值随「步骤类型」变化的字段（同一参数名在不同动作下枚举不同）
TYPE_OPTIONS = {
    'action': {
        'app_mgr': ['启动应用', '停止应用', '重启应用', '清空应用数据', '安装应用', '卸载应用'],
        'screen_ctrl': ['唤醒屏幕', '熄灭屏幕', '旋转屏幕', '固定方向(竖屏)', '固定方向(横屏)'],
    },
    'direction': {
        'flick': ['上', '下', '左', '右'],
        '_default': ['上滑', '下滑', '左滑', '右滑', '自定义坐标'],
    },
}

# 语义上不可能为负的参数（时间 / 像素 / 速度）：批量修改时数字框下限给 0
NON_NEGATIVE_KEYS = {
    'timeout', 'duration', 'afterDelay', 'durationMs', 'longPressMs',
    'distance', 'velocity', 'count',
}


def label_of(key: str) -> str:
    """参数键 → 中文名（没有登记的原样返回 key）"""
    return PARAM_LABELS.get(key, key)


def is_location_key(key: str) -> bool:
    """是否为「定位方式」类字段"""
    return key in LOCATION_TYPE_KEYS


def is_element_overridden(key: str) -> bool:
    """该字段是否会被元素库绑定覆盖（改了对运行时不生效）"""
    return key in ELEMENT_OVERRIDDEN_KEYS


def options_for(key: str, step_types) -> list:
    """字段的固定选项列表；没有固定选项时返回空列表（表示用输入框/数字框）。

    :param step_types: 参与本次批量修改的步骤类型集合（用于 action / direction 这类
                       随类型变化的字段；混合类型时取并集）
    """
    if key in LOCATION_TYPE_KEYS:
        return list(LOCATION_TYPE_OPTIONS)
    if key in FIXED_OPTIONS:
        return list(FIXED_OPTIONS[key])
    table = TYPE_OPTIONS.get(key)
    if not table:
        return []
    types = sorted({t for t in step_types if t in table})
    if not types:
        return list(table.get('_default', []))
    out = []
    for t in types:
        for opt in table[t]:
            if opt not in out:
                out.append(opt)
    return out
