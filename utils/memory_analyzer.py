# src/utils/memory_analyzer.py
"""
内存数据分析模块
提供从 output.txt 解析内存数据并生成 HTML 图表和 Excel 报表的功能。
"""
import os
from typing import Dict, List
from openpyxl import Workbook
from pyecharts.charts import Line
from pyecharts import options as opts


# 默认配置
DEFAULT_CHART_COLORS = [
    "#5470C6", "#91CC75", "#EE6666", "#FAC858",
    "#73C0DE", "#3BA272", "#FC8452"
]


def parse_memory_data(file_path: str) -> Dict[str, List]:
    """
    解析内存数据文件，返回包含各指标列表的字典。
    返回格式：
    {
        "timestamp": [...],
        "total": [...],
        "native_heap": [...],
        "stack": [...],
        "java_heap": [...],
        "system": [...],
        "code": [...],
        "graphics": [...]
    }
    """
    memory_data = {
        "timestamp": [],
        "total": [],
        "native_heap": [],
        "stack": [],
        "java_heap": [],
        "system": [],
        "code": [],
        "graphics": []
    }

    active_flag = False
    temp_time = ""

    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()

            # 检测时间戳行（以 "2025-" 开头）
            if line.startswith("2025-"):
                parts = line.split()
                if parts:
                    temp_time = parts[-1]  # 时间部分
                continue

            # 包匹配逻辑：遇到 backup_package 时停止记录，遇到 target_package 时开始记录
            if "[com.baidu.naviauto:remote]" in line:
                active_flag = False
            if "[com.baidu.naviauto]" in line:
                active_flag = True
                continue

            if active_flag:
                _parse_memory_line(line, memory_data)
                # 每解析完一条内存记录，检查时间戳是否匹配
                if len(memory_data["timestamp"]) == len(memory_data["total"]) - 1:
                    memory_data["timestamp"].append(temp_time)

    return memory_data


def _parse_memory_line(line: str, data: Dict[str, List]):
    """解析单行内存数据，填充到 data 字典中"""
    parts = line.split()
    if not parts:
        return

    try:
        if "TOTAL" in line and len(parts) > 2 and "SWAP" not in line:
            data["total"].append(int(parts[1]))
        elif "Native Heap" in line and len(parts) > 5:
            data["native_heap"].append(int(parts[2]))
        elif "Stack" in line and len(parts) > 4:
            data["stack"].append(int(parts[1]))
        elif "Java Heap" in line:
            data["java_heap"].append(int(parts[2]))
        elif "Code" in line:
            data["code"].append(int(parts[1]))
        elif "Graphics" in line:
            data["graphics"].append(int(parts[1]))
        elif "System" in line:
            data["system"].append(int(parts[1]))
    except (IndexError, ValueError):
        pass  # 忽略解析错误


def generate_report(data: Dict[str, List], output_dir: str, car_model: str) -> tuple:
    """
    生成 HTML 图表和 Excel 文件。
    返回 (chart_path, excel_path)
    """
    if not data["timestamp"]:
        raise ValueError("数据为空，无法生成报告")

    # 生成文件名时间戳部分
    start_time = data["timestamp"][0].replace(":", "-")
    end_time = data["timestamp"][-1].replace(":", "-")
    base_name = f"{car_model}_{start_time}_{end_time}"

    chart_path = os.path.join(output_dir, f"{base_name}.html")
    excel_path = os.path.join(output_dir, f"{base_name}.xlsx")

    # 生成图表
    _generate_chart(data, chart_path, car_model)

    # 生成 Excel
    _export_to_excel(data, excel_path)

    return chart_path, excel_path


def _generate_chart(data: Dict[str, List], output_path: str, car_model: str):
    """生成 pyecharts 图表并保存"""
    line = Line(init_opts=opts.InitOpts(width="1280px", height="650px"))

    line.add_xaxis(data["timestamp"])

    metrics = [
        ("total", "总内存"),
        ("native_heap", "Native Heap"),
        ("java_heap", "Java Heap"),
        ("stack", "Stack"),
        ("system", "System"),
        ("code", "Code"),
        ("graphics", "Graphics")
    ]

    for idx, (key, name) in enumerate(metrics):
        if data.get(key):
            line.add_yaxis(
                series_name=name,
                y_axis=data[key],
                is_smooth=True,
                color=DEFAULT_CHART_COLORS[idx % len(DEFAULT_CHART_COLORS)],
                linestyle_opts=opts.LineStyleOpts(width=2),
                label_opts=opts.LabelOpts(is_show=False),
                markpoint_opts=opts.MarkPointOpts(
                    data=[opts.MarkPointItem(type_="max", name="最大值")]
                ),
                markline_opts=opts.MarkLineOpts(
                    data=[opts.MarkLineItem(type_="average", name="平均值")]
                ),
            )

    line.set_global_opts(
        title_opts=opts.TitleOpts(title=f"{car_model} 内存曲线图"),
        tooltip_opts=opts.TooltipOpts(trigger="axis", axis_pointer_type="cross"),
        legend_opts=opts.LegendOpts(pos_left="15%", pos_top="4%"),
        datazoom_opts=[opts.DataZoomOpts()],
        yaxis_opts=opts.AxisOpts(
            name="内存使用量 (KB)",
            type_="value",
            splitline_opts=opts.SplitLineOpts(is_show=True),
        ),
        xaxis_opts=opts.AxisOpts(
            name="时间",
            boundary_gap=False,
            axislabel_opts=opts.LabelOpts(rotate=45)
        )
    )

    line.render(output_path)


def _export_to_excel(data: Dict[str, List], output_path: str):
    """导出数据到 Excel 文件"""
    wb = Workbook()
    ws = wb.active
    ws.title = "内存数据"

    headers = ["时间戳", "总内存", "Native Heap", "Java Heap",
               "Stack", "System", "Code", "Graphics"]
    ws.append(headers)

    n = len(data["timestamp"])
    for i in range(n):
        row = [
            data["timestamp"][i],
            data["total"][i] if i < len(data["total"]) else "",
            data["native_heap"][i] if i < len(data["native_heap"]) else "",
            data["java_heap"][i] if i < len(data["java_heap"]) else "",
            data["stack"][i] if i < len(data["stack"]) else "",
            data["system"][i] if i < len(data["system"]) else "",
            data["code"][i] if i < len(data["code"]) else "",
            data["graphics"][i] if i < len(data["graphics"]) else "",
        ]
        ws.append(row)

    wb.save(output_path)