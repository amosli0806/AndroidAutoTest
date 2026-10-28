# utils/perf_report.py
"""性能检测 HTML 报告生成器"""
import os
import sys
import base64
import json
from datetime import datetime
from models.perf_model import (PerfSession, compare_stats, MEM_CATEGORIES,
                               SCOPE_LABELS, SCOPE_SYSTEM)
from utils.perf_cpu_analysis import analyze_cpu, analyze_threads


# 指标键 -> 报告里显示的中文名（低层键名只在代码里用，报告要给人看）
METRIC_LABELS = {
    "cpu": "CPU",
    "mem": "内存",
    "fps": "帧率",
    "traffic": "流量",
    "sys_mem": "系统内存",
}


def metric_label(key: str) -> str:
    return METRIC_LABELS.get(key, key)


# 图表库（离线内嵌，见 _chart_js_tags 的说明）
_CHART_JS_FILES = (
    ("chart.umd.min.js",
     "https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"),
    ("hammer.min.js",
     "https://cdn.jsdelivr.net/npm/hammerjs@2.0.8/hammer.min.js"),
    ("chartjs-plugin-zoom.min.js",
     "https://cdn.jsdelivr.net/npm/chartjs-plugin-zoom@2.0.1/dist/chartjs-plugin-zoom.min.js"),
)


def _resource_base() -> str:
    """开发环境取项目根目录；打包后取 PyInstaller 解包目录。"""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _chart_js_tags() -> str:
    """把三个图表库**内嵌**进报告，而不是从 CDN 外链加载。

    为什么：报告经常要发给别人看，对方网络（尤其国内）访问 jsDelivr 常常失败，
    结果就是曲线区域一片空白（2026-10-20 用户反馈）。内嵌后报告是单文件自包含的，
    断网、换台电脑、直接发微信都能正常看图。三个库合计约 240KB，可接受。
    找不到本地文件时退回 CDN 外链（老版本资源缺失时不至于整页没图）。
    """
    js_dir = os.path.join(_resource_base(), "resources", "js")
    parts = []
    for name, url in _CHART_JS_FILES:
        try:
            with open(os.path.join(js_dir, name), "r", encoding="utf-8") as f:
                code = f.read()
            # 避免库代码里偶然出现 </script> 提前闭合标签
            code = code.replace("</script", "<\\/script")
            parts.append(f"<script>\n{code}\n</script>")
        except OSError:
            parts.append(f'<script src="{url}"></script>')
    return "\n    ".join(parts)


# 各指标的 Chart.js 绘图脚本（普通字符串，非 f-string，花括号无需转义）
CHART_JS_CPU = """new Chart(document.getElementById('cpuChart'), {
    type: 'line',
    data: {
        labels: axisLabels,
        datasets: cpuSeries
    },
    options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
            legend: { display: false }, title: { display: true, text: 'CPU 使用率 (%)' },
            zoom: { pan: { enabled: true, mode: 'x' }, zoom: { wheel: { enabled: true }, mode: 'x' } }
        },
        scales: {
            x: { display: true, title: { display: true, text: `时间 (${axisUnit})` } },
            y: { beginAtZero: true }
        },
        elements: { point: { radius: 0 } }
    }
});"""

CHART_JS_MEM = """new Chart(document.getElementById('memChart'), {
    type: 'line',
    data: {
        labels: axisLabels,
        datasets: [{
            label: 'total_pss',
            data: memData,
            borderColor: '#27ae60',
            backgroundColor: 'rgba(39,174,96,0.1)',
            borderWidth: 2,
            fill: true,
            tension: 0.3
        }]
    },
    options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
            legend: { display: false }, title: { display: true, text: '内存占用 (MB)' },
            zoom: { pan: { enabled: true, mode: 'x' }, zoom: { wheel: { enabled: true }, mode: 'x' } }
        },
        scales: {
            x: { display: true, title: { display: true, text: `时间 (${axisUnit})` } },
            y: { beginAtZero: true }
        },
        elements: { point: { radius: 0 } }
    }
});"""

CHART_JS_MEM_BREAKDOWN = """new Chart(document.getElementById('memChart'), {
    type: 'line',
    data: {
        labels: axisLabels,
        datasets: memSeries
    },
    options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
            legend: { display: false }, title: { display: true, text: '内存占用（MB）' },
            zoom: { pan: { enabled: true, mode: 'x' }, zoom: { wheel: { enabled: true }, mode: 'x' } }
        },
        scales: {
            x: { display: true, title: { display: true, text: `时间 (${axisUnit})` } },
            y: { beginAtZero: true, title: { display: true, text: 'MB' } }
        },
        elements: { point: { radius: 0 } }
    }
});"""

CHART_JS_FPS = """new Chart(document.getElementById('fpsChart'), {
    type: 'line',
    data: {
        labels: axisLabels,
        datasets: [{
            label: 'FPS',
            data: fpsData,
            borderColor: '#f39c12',
            backgroundColor: 'rgba(243,156,18,0.1)',
            borderWidth: 2,
            fill: true,
            tension: 0.3
        }]
    },
    options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
            legend: { display: false }, title: { display: true, text: 'FPS 帧率' },
            zoom: { pan: { enabled: true, mode: 'x' }, zoom: { wheel: { enabled: true }, mode: 'x' } }
        },
        scales: {
            x: { display: true, title: { display: true, text: `时间 (${axisUnit})` } },
            y: { beginAtZero: true }
        },
        elements: { point: { radius: 0 } }
    }
});"""

CHART_JS_TRAFFIC = """new Chart(document.getElementById('trafficChart'), {
    type: 'line',
    data: {
        labels: axisLabels,
        datasets: [
            {
                label: '接收 (KB/s)',
                data: rxRateData,
                borderColor: '#9b59b6',
                backgroundColor: 'rgba(155,89,182,0.1)',
                borderWidth: 2,
                fill: true,
                tension: 0.3
            },
            {
                label: '发送 (KB/s)',
                data: txRateData,
                borderColor: '#e67e22',
                backgroundColor: 'rgba(230,126,34,0.1)',
                borderWidth: 2,
                fill: false,
                tension: 0.3
            }
        ]
    },
    options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
            legend: { display: false }, title: { display: true, text: '流量速率 (KB/s)' },
            zoom: { pan: { enabled: true, mode: 'x' }, zoom: { wheel: { enabled: true }, mode: 'x' } }
        },
        scales: {
            x: { display: true, title: { display: true, text: `时间 (${axisUnit})` } },
            y: { beginAtZero: true }
        },
        elements: { point: { radius: 0 } }
    }
});"""


def _time_axis_labels(timestamps, t0):
    """相对 t0 的秒数 → (单位, 标签列表)，规则与主曲线一致（时:分 / 分:秒 / 秒）。"""
    rel = [max(0.0, float(ts or 0) - t0) for ts in timestamps]
    total = rel[-1] if rel else 0.0
    if total >= 3600:
        return "时:分", [f"{int(ts // 3600)}:{int(ts % 3600 // 60):02d}" for ts in rel]
    if total >= 60:
        return "分:秒", [f"{int(ts // 60)}:{int(ts % 60):02d}" for ts in rel]
    return "秒", [f"{ts:.1f}" for ts in rel]


def _chart_wrap(canvas_id: str) -> str:
    """图表容器：画布 + 图例（曲线参数）+ 工具条（全选 / 反选 / 重置缩放）。

    图例不用 Chart.js 内置的（系列多时会盖住曲线），改为画布下方的 HTML 图例：
    点一项隐藏/显示该曲线，「全选 / 反选」批量操作，「重置缩放」回到全量视图。
    """
    return f"""
        <div class="chart-wrap">
            <canvas id="{canvas_id}"></canvas>
        </div>
        <div class="chart-legend" id="{canvas_id}-legend"></div>
        <div class="chart-toolbar">
            <button class="chart-select-all" data-chart="{canvas_id}">全选</button>
            <button class="chart-invert" data-chart="{canvas_id}">反选</button>
            <button class="zoom-reset" data-chart="{canvas_id}">↺ 重置缩放</button>
        </div>"""


CHART_JS_CPU_PEAK = """new Chart(document.getElementById('cpuPeakChart'), {
    type: 'line',
    data: {
        labels: axisLabels,
        datasets: [
            { label: 'CPU (%)', data: cpuData, borderColor: '#3498db', backgroundColor: 'rgba(52,152,219,0.08)', borderWidth: 2, fill: true, tension: 0.3 },
            { label: 'P90 阈值', data: cpuThresholdLine, borderColor: '#e74c3c', borderDash: [6, 4], borderWidth: 1.2, pointRadius: 0, fill: false },
            { label: '峰值点', data: cpuPeakPoints, borderColor: '#e74c3c', backgroundColor: '#e74c3c', showLine: false, pointRadius: 4 }
        ]
    },
    options: {
        responsive: true, maintainAspectRatio: false,
        plugins: {
            legend: { display: false }, title: { display: true, text: 'CPU 峰值分析（≥P90 的点已高亮）' },
            zoom: { pan: { enabled: true, mode: 'x' }, zoom: { wheel: { enabled: true }, mode: 'x' } }
        },
        scales: {
            x: { display: true, title: { display: true, text: `时间 (${axisUnit})` } },
            y: { beginAtZero: true, title: { display: true, text: '%' } }
        },
        elements: { point: { radius: 0 } }
    }
});"""

CHART_JS_SYS_CPU = """new Chart(document.getElementById('sysCpuChart'), {
    type: 'line',
    data: {
        labels: sysCpuLabels,
        datasets: [
            { label: 'D_total', data: sysCpuTotal, borderColor: '#95a5a6', backgroundColor: 'transparent', borderWidth: 1.2, borderDash: [6, 4], pointRadius: 0, fill: false },
            { label: 'D_busy', data: sysCpuData, borderColor: '#e74c3c', backgroundColor: 'rgba(231,76,60,0.06)', borderWidth: 2, fill: true, tension: 0.2 },
            { label: 'D_user', data: sysCpuUser, borderColor: '#3498db', backgroundColor: 'transparent', borderWidth: 1.4, fill: false, tension: 0.2 },
            { label: 'D_nice', data: sysCpuNice, borderColor: '#16a085', backgroundColor: 'transparent', borderWidth: 1.2, fill: false, tension: 0.2 },
            { label: 'D_sys', data: sysCpuSys, borderColor: '#9b59b6', backgroundColor: 'transparent', borderWidth: 1.4, fill: false, tension: 0.2 },
            { label: 'D_idle', data: sysCpuIdle, borderColor: '#27ae60', backgroundColor: 'transparent', borderWidth: 1.4, fill: false, tension: 0.2 },
            { label: 'D_iow', data: sysCpuIow, borderColor: '#f39c12', backgroundColor: 'transparent', borderWidth: 1.2, borderDash: [4, 3], fill: false, tension: 0.2 },
            { label: 'D_irq', data: sysCpuIrq, borderColor: '#8e6e53', backgroundColor: 'transparent', borderWidth: 1, fill: false, tension: 0.2 },
            { label: 'D_sirq', data: sysCpuSirq, borderColor: '#e84393', backgroundColor: 'transparent', borderWidth: 1, fill: false, tension: 0.2 },
            { label: 'D_host', data: sysCpuHost, borderColor: '#7f8c8d', backgroundColor: 'transparent', borderWidth: 1, borderDash: [2, 3], fill: false, tension: 0.2 }
        ]
    },
    options: {
        responsive: true, maintainAspectRatio: false,
        plugins: {
            legend: { display: false },
            title: { display: true, text: '系统整体 CPU 趋势（多核累计）' },
            zoom: { pan: { enabled: true, mode: 'x' }, zoom: { wheel: { enabled: true }, mode: 'x' } }
        },
        scales: {
            x: { display: true, title: { display: true, text: sysCpuAxisTitle } },
            y: { beginAtZero: true, title: { display: true, text: '多核累计 (%)' } }
        },
        elements: { point: { radius: 0 } }
    }
});"""

CHART_JS_THREAD_COUNT = """new Chart(document.getElementById('threadCountChart'), {
    type: 'line',
    data: {
        labels: threadLabels,
        datasets: [
            { label: '线程总数', data: threadTotalData, borderColor: '#2980b9', backgroundColor: 'rgba(41,128,185,0.06)', borderWidth: 2, fill: true, tension: 0.2 },
            { label: '活跃线程数', data: threadActiveData, borderColor: '#27ae60', backgroundColor: 'transparent', borderWidth: 1.6, fill: false, tension: 0.2 }
        ]
    },
    options: {
        responsive: true, maintainAspectRatio: false,
        plugins: {
            legend: { display: false }, title: { display: true, text: '线程数量趋势' },
            zoom: { pan: { enabled: true, mode: 'x' }, zoom: { wheel: { enabled: true }, mode: 'x' } }
        },
        scales: {
            x: { display: true, title: { display: true, text: threadAxisTitle } },
            y: { beginAtZero: true, title: { display: true, text: '线程数' } }
        },
        elements: { point: { radius: 0 } }
    }
});"""

CHART_JS_THREAD_CPU = """new Chart(document.getElementById('threadCpuChart'), {
    type: 'line',
    data: {
        labels: threadLabels,
        datasets: threadCpuSeries.map(function (s) {
            return { label: s.label, data: s.data, borderColor: s.color,
                     backgroundColor: 'transparent', borderWidth: s.width || 1.2,
                     fill: false, tension: 0.2, spanGaps: false };
        })
    },
    options: {
        responsive: true, maintainAspectRatio: false,
        plugins: {
            legend: { display: false },
            title: { display: true, text: '线程 CPU 趋势（TOTAL + 最忙的 45 个线程，多核累计 %）' },
            zoom: { pan: { enabled: true, mode: 'x' }, zoom: { wheel: { enabled: true }, mode: 'x' } }
        },
        scales: {
            x: { display: true, title: { display: true, text: threadAxisTitle } },
            y: { beginAtZero: true, title: { display: true, text: '%' } }
        },
        elements: { point: { radius: 0 } }
    }
});"""


def _fmt_pct(value) -> str:
    return "-" if value is None else f"{value:.1f}%"


def _sparkline(total_vals, thread_vals, width: int = 120, height: int = 26) -> str:
    """线程贡献表里的迷你趋势图：灰线 = TOTAL，蓝线 = 本线程。

    两个系列共用同一纵轴（按 TOTAL 的峰值缩放），所以能一眼看出
    「这条线程在 TOTAL 抬起来的时候有没有跟着动」。
    """
    n = len(total_vals)
    if n < 2 or len(thread_vals) != n:
        return ""
    vmax = max(max(total_vals), max(thread_vals), 0.01)

    def _points(vals):
        step = width / (n - 1)
        return " ".join(
            f"{i * step:.2f},{height - 1 - (v / vmax) * (height - 2):.2f}"
            for i, v in enumerate(vals))

    return (
        f'<svg class="spark" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" preserveAspectRatio="none">'
        f'<polyline points="{_points(total_vals)}" fill="none" '
        f'stroke="#c8c8c8" stroke-width="1"/>'
        f'<polyline points="{_points(thread_vals)}" fill="none" '
        f'stroke="#1976d2" stroke-width="1"/></svg>')


def _render_cpu_analysis(a: dict) -> str:
    """CPU 峰值分析章节：分位数 / 多阈值分档 / 百分位分档 / 贡献汇总 / 峰值明细。

    章节顺序与列名沿用「阈值分档 → 百分位分档 → 贡献汇总」的分析逻辑：
    先看整段负载分布，再看峰值集中在哪几个采样点，最后看峰值被哪些线程推高。
    """
    if not a.get("count"):
        return ""
    p = a.get("percentiles") or {}
    n = a["count"]
    sub = '<h3 style="font-size:14px;margin:20px 0 6px;color:#333;">{}</h3>'
    note = '<p style="font-size:12px;color:#888;margin:4px 0 8px;">{}</p>'

    parts = [f"""
        <h2>🔍 CPU 峰值分析</h2>
        <p style="font-size:12px;color:#888;">※ 基于 {n} 个有效采样点（间隔 {a['interval']} 秒），
           分位数用最近秩法；阈值按本次峰值自适应，适合横向对比同一场景的不同版本。</p>
        {_chart_wrap('cpuPeakChart')}
        {sub.format('分位数总览')}
        {note.format('把采样值从小到大排序后取对应位置的值，P90 即 90% 的采样点不高于它。')}
        <table class="compare">
            <thead><tr><th>P50</th><th>P75</th><th>P90</th><th>P95</th><th>P99</th><th>峰值</th></tr></thead>
            <tbody><tr>
                <td>{p.get('p50', 0):.2f}%</td><td>{p.get('p75', 0):.2f}%</td>
                <td>{p.get('p90', 0):.2f}%</td><td>{p.get('p95', 0):.2f}%</td>
                <td>{p.get('p99', 0):.2f}%</td><td>{p.get('max', 0):.2f}%</td>
            </tr></tbody>
        </table>"""]

    if a.get("thresholds"):
        rows = "".join(
            f"<tr><td>{r['threshold']:.2f}</td><td>{r['count']}</td>"
            f"<td>{r['share']:.1f}</td><td>{r['avg']:.2f}</td>"
            f"<td>{r['max']:.2f}</td><td>{_fmt_pct(r['top5'])}</td></tr>"
            for r in a["thresholds"])
        parts.append(f"""
        {sub.format(f'多阈值分档（总采样 {n}）')}
        {note.format('阈值取本次峰值的 25% / 50% / 75% / 90% / 100%，'
                     '每一档统计「采样值 ≥ 阈值」的采样点。')}
        <table class="compare">
            <thead><tr><th>阈值（TOTAL ≥）</th><th>采样数</th><th>占比（%）</th>
                <th>段内均值（%）</th><th>段内最大（%）</th><th>Top5 线程均值</th></tr></thead>
            <tbody>{rows}</tbody>
        </table>""")

    if a.get("fixed_thresholds"):
        rows = "".join(
            f"<tr><td>{r['threshold']:.0f}</td><td>{r['count']}</td>"
            f"<td>{r['share']:.1f}</td><td>{r['avg']:.2f}</td>"
            f"<td>{r['max']:.2f}</td><td>{_fmt_pct(r['top5'])}</td></tr>"
            for r in a["fixed_thresholds"])
        parts.append(f"""
        {sub.format('固定阈值分档（150 / 200 / 300）')}
        {note.format('固定阈值便于同一应用不同版本的横向对比；本次没达到的档位保留 0 行。')}
        <table class="compare">
            <thead><tr><th>阈值（TOTAL ≥）</th><th>采样数</th><th>占比（%）</th>
                <th>段内均值（%）</th><th>段内最大（%）</th><th>Top5 线程均值</th></tr></thead>
            <tbody>{rows}</tbody>
        </table>""")

    if a.get("percentile_buckets"):
        rows = "".join(
            f"<tr><td>{r['label']}</td><td>{r['threshold']:.2f}</td>"
            f"<td>{r['count']}</td><td>{r['share']:.1f}</td>"
            f"<td>{r['avg']:.2f}</td><td>{r['max']:.2f}</td>"
            f"<td>{_fmt_pct(r['top5'])}</td></tr>"
            for r in a["percentile_buckets"])
        parts.append(f"""
        {sub.format(f'百分位分档（总采样 {n}，阈值自适应）')}
        {note.format('按采样点数量取最忙的前 2% / 5% / 10% / 25%，'
                     '「对应阈值」是这一档里最低的那个采样值。')}
        <table class="compare">
            <thead><tr><th>分位段</th><th>对应阈值（TOTAL ≥）</th><th>采样数</th>
                <th>实际占比（%）</th><th>段内均值（%）</th><th>段内最大（%）</th>
                <th>Top5 线程均值</th></tr></thead>
            <tbody>{rows}</tbody>
        </table>""")

    c = a.get("concentration") or {}
    if c:
        bucket = next((r for r in a.get("percentile_buckets") or []
                       if r.get("label") == "前 5%"), None)
        scope = f"前 5%，TOTAL ≥ {c.get('threshold', bucket['threshold'] if bucket else 0):.1f}"
        if bucket:
            scope += (f"，{bucket['count']}/{n} 采样，占 {bucket['share']:.1f}%，"
                      f"段内均值 {bucket['avg']:.2f}，峰值最大 {bucket['max']:.2f}")
        parts.append(f"""
        {sub.format(f'贡献汇总（{scope}）')}
        <p class="chips">
            <span class="chip">Top1 均值 <b>{c['top1']:.1f}%</b></span>
            <span class="chip">Top5 均值 <b>{c['top5']:.1f}%</b></span>
            <span class="chip">Top1 最大 <b>{c.get('top1_max', 0):.1f}%</b></span>
            <span class="chip">模式 <b>{c['mode']}</b></span>
        </p>
        {note.format('占比都相对「线程 CPU 合计」计算：Top1 是单个最忙线程，'
                     'Top5 是前 5 个最忙线程之和。')}
        {sub.format('集中度指标含义')}
        <table class="compare">
            <thead><tr><th>指标</th><th>含义</th><th>当前值（%）</th><th>解读</th></tr></thead>
            <tbody>
                <tr><td>Top1 均值</td>
                    <td>平均每个峰值采样点中，最忙线程占线程合计的比例</td>
                    <td>{c['top1']:.1f}</td><td>值越大 → 峰值越被单个线程主导</td></tr>
                <tr><td>Top5 均值</td>
                    <td>平均每个峰值采样点中，前 5 个最忙线程合计占线程合计的比例</td>
                    <td>{c['top5']:.1f}</td><td>值越大 → 峰值集中在少数几个线程</td></tr>
                <tr><td>Top1 最大</td>
                    <td>所有峰值采样点中，单个线程占线程合计的最高比例</td>
                    <td>{c.get('top1_max', 0):.1f}</td>
                    <td>{'存在单线程独占的时刻' if c.get('top1_max', 0) >= 50 else '没有单线程独占'}</td></tr>
                <tr><td>整体判定</td><td>综合上述三项给出的模式</td>
                    <td>-</td><td>{c['mode']}</td></tr>
            </tbody>
        </table>""")

    # ---- 峰值段线程贡献：分类规则 + 逐线程明细（含迷你趋势）----
    # 只列峰值段均值最高的 15 个（与性能报告的 Top15 一致）；完整排行看「线程 CPU」页
    all_pt = a.get("peak_threads") or []
    pt = all_pt[:15]
    meta = a.get("peak_thread_meta") or {}
    if pt:
        type_colors = {
            "持续型": "#1976d2", "爆发型": "#e74c3c",
            "常态型": "#34495e", "偶发型": "#888888",
        }
        trows = "".join(
            f"<tr><td>{r['name']}</td>"
            f"<td>{r['cpu_avg']:.2f}</td><td>{r['peak_avg']:.2f}</td>"
            f"<td>{r['peak_max']:.1f}</td>"
            f"<td>{r['activity_all']:.1f}</td><td>{r['activity_peak']:.1f}</td>"
            f"<td>{r['share']:.1f}</td>"
            f"<td style=\"color:{type_colors.get(r['type'], '#333')};\">{r['type']}</td>"
            f"<td class=\"spark-cell\">{_sparkline(r.get('trend_total') or [], r.get('trend') or [])}</td>"
            f"</tr>"
            for r in pt)
        parts.append(f"""
        {sub.format('贡献类型分类规则')}
        <details class="rules" open>
            <summary>贡献类型分类规则（点击展开/收起）</summary>
            <table class="compare">
                <thead><tr><th>类型</th><th>判定条件</th><th>含义</th></tr></thead>
                <tbody>
                    <tr><td style="color:#1976d2;">持续型</td>
                        <td>整体活跃率 ≥ 80% 且 峰值段活跃率 ≥ 80%</td>
                        <td>常驻热线程，全程持续占用 CPU</td></tr>
                    <tr><td style="color:#e74c3c;">爆发型</td>
                        <td>峰值段活跃率 ≥ 60% 且（峰值段活跃率 − 整体活跃率）≥ 30</td>
                        <td>平时较闲，峰值期间被触发，是 CPU 飙升的主要推手</td></tr>
                    <tr><td style="color:#34495e;">常态型</td>
                        <td>峰值段活跃率 ≥ 60%，但不满足持续型 / 爆发型</td>
                        <td>峰值期活跃，整体也有一定负载，属常规工作线程</td></tr>
                    <tr><td style="color:#888888;">偶发型</td>
                        <td>峰值段活跃率 &lt; 60%</td>
                        <td>峰值期间不常出现，对峰值贡献偶发、较弱</td></tr>
                </tbody>
            </table>
        </details>
        {sub.format(f'峰值段线程贡献（Top 15，共 {len(all_pt)} 个线程，按峰值段均值降序）')}
        {note.format(f'峰值段 = 进程 CPU 最忙的前 5% 采样点（{meta.get("peak_total", 0)} 个），'
                     f'其中 {meta.get("matched", 0)} 个能对到线程采样点；'
                     '线程不在该轮 Top N 里按 0 计，所以「整体均值 / 峰值段均值」都是下限。'
                     '趋势图灰线为 TOTAL、蓝线为本线程，两条线共用一个纵轴。')}
        <table class="compare">
            <thead><tr><th>线程名</th><th>整体均值（%）</th><th>峰值段均值（%）</th>
                <th>峰值段最大（%）</th><th>整体活跃率（%）</th><th>峰值段活跃率（%）</th>
                <th>占TOTAL（%）</th><th>贡献类型</th><th>CPU 趋势（灰=TOTAL，蓝=本线程）</th></tr></thead>
            <tbody>{trows}</tbody>
        </table>""")

    segs = a.get("segments") or []
    iso = a.get("isolated_peaks", 0)
    if segs:
        rows = "".join(
            f"<tr><td>#{r['start']}</td><td>#{r['end']}</td>"
            f"<td>{r.get('points', '-')}（{r['duration']:.1f}s）</td>"
            f"<td>{r['max']:.2f}%</td><td>{r['avg']:.2f}%</td>"
            f"<td>{r['dominant'] or '-'}</td></tr>"
            for r in segs)
        parts.append(f"""
        {sub.format('连续峰值段')}
        {note.format('连续 ≥2 个采样点都 ≥ P90 才算一段；只有单个点的归到「峰值采样点明细」。')}
        <table class="compare">
            <thead><tr><th>起始#</th><th>结束#</th><th>时长（采样）</th>
                <th>段内最大</th><th>段内均值</th><th>主导线程</th></tr></thead>
            <tbody>{rows}</tbody>
        </table>""")
        if iso:
            parts.append(note.format(
                f'另有 {iso} 个孤立峰值点（未形成 ≥2 点的连续段），见下方明细。'))
    elif iso:
        parts.append(f"""
        {sub.format('连续峰值段')}
        {note.format(f'本次没有 ≥2 个采样点的连续峰值段；{iso} 个峰值点均为孤立采样，见下方明细。')}""")

    if a.get("peak_samples"):
        rows = "".join(
            f"<tr><td>#{r['index']}</td><td>{r['value']:.2f}%</td>"
            f"<td>{r['contributors'] or '-'}</td></tr>"
            for r in a["peak_samples"])
        parts.append(f"""
        {sub.format(f'峰值采样点明细（全部 {len(a["peak_samples"])} 个 ≥ P90 的点）')}
        {note.format('按 CPU 降序；贡献线程只列占该点线程合计 ≥5% 的线程。')}
        <table class="compare">
            <thead><tr><th>采样点#</th><th>TOTAL（%）</th>
                <th>主要贡献线程（≥5%）</th></tr></thead>
            <tbody>{rows}</tbody>
        </table>""")
    return "".join(parts)


def _render_thread_analysis(thread_samples, cpu_avg: float = 0.0, rank=None):
    """返回 (线程数量 HTML, 线程 CPU HTML)。

    拆成两块是为了放进两个 Tab（对齐百度报告的「线程数量 / 线程cpu」）。
    rank：线程探针在会话结束时给出的全量排行；旧会话没有时回退到 Top 15 聚合。
    """
    ta = analyze_threads(thread_samples)
    if not ta.get("count"):
        return "", ""
    t = ta["totals"]
    rows_data = rank or ta.get("threads") or []
    complete = bool(rank)
    note = ""
    if cpu_avg > 0 and t["cpu_avg"] > 0:
        diff = abs(t["cpu_avg"] - cpu_avg) / cpu_avg * 100.0
        if diff > 20:
            note = (f"<br>※ 线程合计与进程 CPU 均值相差 {diff:.0f}%，"
                    f"多为两个探针的采样窗口不同步所致，趋势仍可参考。")
    coverage = (
        "列出会话中出现过的全部线程（按均值排序）"
        if complete else
        "旧会话没有全量排行，这里从每个采样点的 Top N 中聚合，可能缺少部分线程"
    )

    rows = "".join(
        f"<tr><td>{r['name']}</td><td>{r['tid']}</td>"
        f"<td>{r['cpu_avg']:.2f}%</td><td>{r['cpu_max']:.2f}%</td>"
        f"<td>{r.get('active', '-')}</td><td>{r.get('activity', 0):.2f}</td>"
        f"<td>{r.get('s_avg', 0):.2f}%</td><td>{r.get('number', 1)}</td>"
        f"<td>{r['share']:.1f}%</td></tr>"
        for r in rows_data)

    count_html = f"""
        <h2>🧵 线程数量</h2>
        <p style="font-size:12px;color:#888;">※ 数据源 <code>/proc/&lt;pid&gt;/task/*/stat</code>；
           线程 CPU 为多核累计，单线程最高 100%。{coverage}；
           均值按整个会话平均（某轮不存在的线程按 0 计），因此是下限。
           线程名由内核截断到 15 个字符。全量线程明细见产物目录的
           <code>threads_*.csv</code>。{note}</p>
        <table class="compare compare4">
            <thead><tr><th>项目</th><th>均值</th><th>峰值</th><th>最低</th></tr></thead>
            <tbody>
                <tr><td>线程总数</td><td>{t['total_avg']:.0f}</td>
                    <td>{t['total_max']}</td><td>{t['total_min']}</td></tr>
                <tr><td>活跃线程数（≥1%）</td><td>{t['active_avg']:.0f}</td>
                    <td>{t['active_max']}</td><td>{t['active_min']}</td></tr>
                <tr><td>线程 CPU 合计 (%)</td><td>{t['cpu_avg']:.2f}</td>
                    <td>{t['cpu_max']:.2f}</td><td>{t['cpu_min']:.2f}</td></tr>
            </tbody>
        </table>
        {_chart_wrap('threadCountChart')}
        <p style="font-size:12px;color:#888;">共 {ta['count']} 个采样点</p>
        """

    cpu_html = f"""
        <h2>🧵 线程 CPU</h2>
        <p style="font-size:12px;color:#888;">※ 按线程名聚合（同名线程池合并）；
           采样数 = 该线程名任一线程 CPU &gt; 0 的采样点个数，活跃度 = 采样数 / 总采样数，
           活跃均值只在这些采样上平均。共 {len(rows_data)} 个线程名，全部列出、按均值降序。</p>
        {_chart_wrap('threadCpuChart')}
        <p style="font-size:12px;color:#888;">线程排行（共 {len(rows_data)} 个线程，按均值排序）：</p>
        <table class="compare">
            <thead><tr><th>线程</th><th>TID</th><th>CPU 均值 (%)</th>
                <th>CPU 峰值 (%)</th><th>采样数</th><th>活跃度</th>
                <th>活跃均值 (%)</th><th>实例数</th><th>占比</th></tr></thead>
            <tbody>{rows}</tbody>
        </table>
        """
    return count_html, cpu_html


class PerfReportGenerator:
    """生成性能检测的 HTML 报告"""

    @staticmethod
    def generate(session: PerfSession, file_path: str, baseline=None) -> str:
        stats = session.get_stats()
        samples = session.samples

        # 准备曲线数据（用 JS 数组内联）
        timestamps = []
        cpu_series = []
        mem_series = []
        mem_cat_series = {cat: [] for cat, _l, _c in MEM_CATEGORIES}
        fps_series = []
        rx_rate_series = []   # 流量速率（KB/s），按相邻采样差分
        tx_rate_series = []
        mem_native_system = []  # native+system（MB）

        if samples:
            t0 = samples[0].timestamp
            prev_rx = prev_tx = None
            prev_ts = None
            for s in samples:
                t = s.timestamp
                timestamps.append(f"{t - t0:.1f}")
                # 没采到有效值的样本用 null 占位：Chart.js 会画成断点，
                # 而不是把"没测到"画成 0
                if any(e.startswith('cpu:') for e in getattr(s, 'errors', ())):
                    cpu_series.append("null")
                else:
                    cpu_series.append(f"{s.cpu_percent:.2f}")
                mem_series.append(f"{s.mem_pss_mb:.2f}")
                for cat, _l, _c in MEM_CATEGORIES:
                    mem_cat_series[cat].append(
                        f"{s.mem_breakdown.get(cat, 0.0):.2f}"
                    )
                mem_native_system.append(round(
                    float(s.mem_breakdown.get("Native", 0.0))
                    + float(s.mem_breakdown.get("System", 0.0)), 2))
                fps_series.append(str(s.fps))
                # 流量速率：相邻采样点差分换算 KB/s（首次只记基准不画点）
                if prev_rx is not None and prev_ts is not None and (t - prev_ts) > 0:
                    dt = t - prev_ts
                    rx_rate_series.append(f"{max(0, s.rx_bytes - prev_rx) / dt / 1024:.2f}")
                    tx_rate_series.append(f"{max(0, s.tx_bytes - prev_tx) / dt / 1024:.2f}")
                else:
                    rx_rate_series.append("0")
                    tx_rate_series.append("0")
                prev_rx, prev_tx, prev_ts = s.rx_bytes, s.tx_bytes, t

            # 根据总时长选横轴单位，长会话（如 12 小时）显示「时:分」而非几万秒
            total_secs = samples[-1].timestamp - samples[0].timestamp
            if total_secs >= 3600:
                axis_unit = "时:分"
                axis_labels = [
                    f"{int(ts // 3600)}:{int(ts % 3600 // 60):02d}"
                    for ts in (s.timestamp - t0 for s in samples)
                ]
            elif total_secs >= 60:
                axis_unit = "分:秒"
                axis_labels = [
                    f"{int(ts // 60)}:{int(ts % 60):02d}"
                    for ts in (s.timestamp - t0 for s in samples)
                ]
        else:
            axis_unit = "秒"
            axis_labels = timestamps

        # 实时曲线的数据集：单进程 → 只有合计一条；多进程 → 合计 + 每个进程一条
        _cpu_proc_names = []
        for _n in (getattr(session, "app_processes", None) or []):
            if _n and _n not in _cpu_proc_names:
                _cpu_proc_names.append(_n)
        for _s in samples:
            for _n in (_s.cpu_by_process or {}):
                if _n and _n not in _cpu_proc_names:
                    _cpu_proc_names.append(_n)
        _cpu_multi = len(_cpu_proc_names) > 1
        _cpu_palette = ['#e67e22', '#16a085', '#8e44ad', '#c0392b',
                        '#2c3e50', '#d35400', '#7f8c8d', '#27ae60']
        cpu_chart_series = [{
            "label": "CPU 合计" if _cpu_multi else "CPU (%)",
            "data": [None if v == "null" else float(v) for v in cpu_series],
            "borderColor": "#3498db",
            "backgroundColor": "rgba(52,152,219,0.10)",
            "borderWidth": 2,
            "fill": True,
            "tension": 0.3,
        }]
        if _cpu_multi:
            for _i, _name in enumerate(_cpu_proc_names):
                _vals, _has = [], False
                for _s in samples:
                    _v = (_s.cpu_by_process or {}).get(_name)
                    if _v is None:
                        _vals.append(None)
                    else:
                        _vals.append(round(float(_v), 2))
                        _has = True
                if not _has:
                    continue
                cpu_chart_series.append({
                    "label": _name,
                    "data": _vals,
                    "borderColor": _cpu_palette[_i % len(_cpu_palette)],
                    "backgroundColor": "transparent",
                    "borderWidth": 1.6,
                    "fill": False,
                    "tension": 0.3,
                })
        cpu_chart_series_js = json.dumps(cpu_chart_series, ensure_ascii=False)

        # 内存曲线：参数名与性能报告一致（total_pss / res / java_heap / native_heap /
        # code_mem / stack_mem / graphics_mem / other_mem / system_mem / native+system_mem）
        mem_chart_series = []
        if samples and ('mem' in session.metrics):
            def _cat_vals(key):
                return [round(float(s.mem_breakdown.get(key, 0.0)), 2) for s in samples]

            mem_chart_series.append({
                "label": "total_pss",
                "data": [round(float(s.mem_pss_mb), 2) for s in samples],
                "borderColor": "#2c3e50", "borderWidth": 2.2,
                "fill": False, "tension": 0.3,
            })
            if any(s.mem_breakdown for s in samples):
                for key, label, color in (
                    ("Java", "java_heap", "#f39c12"),
                    ("Native", "native_heap", "#3498db"),
                    ("Code", "code_mem", "#f1c40f"),
                    ("Stack", "stack_mem", "#e84393"),
                    ("Graphics", "graphics_mem", "#9b59b6"),
                    ("Others", "other_mem", "#27ae60"),
                ):
                    mem_chart_series.append({
                        "label": label, "data": _cat_vals(key),
                        "borderColor": color, "borderWidth": 1.5,
                        "fill": False, "tension": 0.3,
                    })
                if any("System" in s.mem_breakdown for s in samples):
                    mem_chart_series.append({
                        "label": "system_mem", "data": _cat_vals("System"),
                        "borderColor": "#34495e", "borderWidth": 1.5,
                        "fill": False, "tension": 0.3,
                    })
                    mem_chart_series.append({
                        "label": "native+system_mem", "data": list(mem_native_system),
                        "borderColor": "#95a5a6", "borderWidth": 1.5,
                        "fill": False, "tension": 0.3,
                    })
        mem_chart_series_js = json.dumps(mem_chart_series, ensure_ascii=False)


        # 汇总信息
        app_pkg = session.app_package
        device = session.device_serial

        def _fmt_time(raw: str) -> str:
            """把 ISO 格式的起止时间（如 2026-10-10T20:14:40.070283）格式化成
            「2026-10-10 20:14:40」；解析失败或为空时原样返回，不做破坏。"""
            if not raw:
                return raw
            try:
                return datetime.fromisoformat(raw).strftime('%Y-%m-%d %H:%M:%S')
            except (ValueError, TypeError):
                # 兼容 'Z' 结尾等 fromisoformat 不认的写法
                try:
                    return raw.replace('T', ' ').split('.')[0].rstrip('Z')
                except Exception:
                    return raw

        start = _fmt_time(session.start_time)
        end = _fmt_time(session.end_time)
        metrics = session.metrics
        duration = "0 秒"
        if samples:
            _secs = samples[-1].timestamp - samples[0].timestamp
            _h, _rem = divmod(int(round(_secs)), 3600)
            _m, _s = divmod(_rem, 60)
            if _h:
                duration = f"{_h} 小时 {_m} 分 {_s} 秒"
            elif _m:
                duration = f"{_m} 分 {_s} 秒"
            else:
                duration = f"{_s} 秒"

        # 设备信息行（分辨率 / 屏幕密度 / 安卓版本）——旧会话无此数据时自动省略
        device_info_rows = ""
        for _label, _val in (
            ("分辨率", getattr(session, 'screen_resolution', '')),
            ("屏幕密度", getattr(session, 'screen_density', '')),
            ("安卓版本", getattr(session, 'android_version', '')),
        ):
            if _val:
                device_info_rows += f"""
            <tr>
                <td class="label">{_label}</td>
                <td>{_val}</td>
            </tr>"""

        # 场景化信息
        scenario_info = ""
        if session.suite_name:
            cases_str = "、".join(session.case_names) if session.case_names else "-"
            scenario_info = f"""
                <tr>
                    <td class="label">关联套件</td>
                    <td>{session.suite_name}</td>
                </tr>
                <tr>
                    <td class="label">执行用例</td>
                    <td>{cases_str}</td>
                </tr>
            """

        # 告警信息
        alerts_html = ""
        if session.alerts:
            alert_items = "".join(
                f"<li>{datetime.fromtimestamp(ts).strftime('%H:%M:%S')} - {msg}</li>"
                for ts, _, msg in session.alerts[:50]
            )
            alerts_html = f"""
                <div class="section">
                    <h2>⚠ 告警记录</h2>
                    <ul>{alert_items}</ul>
                </div>
            """

        # 是否采集了流量指标
        has_traffic = 'traffic' in metrics
        # 是否采集到了内存分类拆解（旧数据无拆解时退回单线 PSS）
        has_mem_breakdown = any(s.mem_breakdown for s in samples)

        # 关键指标汇总卡片：只展示本次勾选采集的指标，每项含峰值/均值/最低
        summary_cards_html = ""
        if 'cpu' in metrics:
            s = stats.get('cpu', {})
            summary_cards_html += f"""
            <div class="card">
                <div class="value">{s.get('max', 0):.1f}%</div>
                <div class="label">CPU 峰值</div>
                <div class="sub">均值 {s.get('avg', 0):.1f}% · 最低 {s.get('min', 0):.1f}%</div>
            </div>"""
        if 'mem' in metrics:
            s = stats.get('mem', {})
            summary_cards_html += f"""
            <div class="card">
                <div class="value">{s.get('max', 0):.0f}MB</div>
                <div class="label">内存峰值</div>
                <div class="sub">均值 {s.get('avg', 0):.0f}MB · 最低 {s.get('min', 0):.0f}MB</div>
            </div>"""
        if 'fps' in metrics:
            s = stats.get('fps', {})
            # 与 CPU / 内存卡保持同一排布：主值是峰值，均值 / 最低放小字
            summary_cards_html += f"""
            <div class="card">
                <div class="value">{s.get('max', 0):.0f}</div>
                <div class="label">FPS 峰值</div>
                <div class="sub">均值 {s.get('avg', 0):.0f} · 最低 {s.get('min', 0):.0f}</div>
            </div>"""
        if has_traffic and 'traffic' in stats:
            t = stats['traffic']
            summary_cards_html += f"""
            <div class="card">
                <div class="value">{t['rx_mb']:.2f}MB</div>
                <div class="label">流量接收</div>
            </div>
            <div class="card">
                <div class="value">{t['tx_mb']:.2f}MB</div>
                <div class="label">流量发送</div>
            </div>"""

        # 基线对比（自动匹配同应用基线）
        baseline_html = ""
        baseline_rows = []
        if baseline is not None:
            rows = compare_stats(baseline.metrics, stats)
            baseline_rows = rows
            if rows:
                def _badge(status):
                    if status == 'worse':
                        return ('<span class="badge worse">⬆ 劣化</span>', '#e74c3c')
                    if status == 'better':
                        return ('<span class="badge better">⬇ 优化</span>', '#27ae60')
                    return ('<span class="badge stable">— 持平</span>', '#888')
                tr_rows = ""
                worse_count = 0
                for r in rows:
                    badge, color = _badge(r['status'])
                    if r['status'] == 'worse':
                        worse_count += 1
                    tr_rows += f"""
                    <tr>
                        <td>{r['label']}</td>
                        <td>{r['base']:.2f}</td>
                        <td>{r['cur']:.2f}</td>
                        <td style="color:{color};font-weight:600;">{r['change_pct']:+.1f}%</td>
                        <td>{badge}</td>
                    </tr>"""
                baseline_verdict = (
                    f"共 {worse_count} 项指标明显劣化" if worse_count
                    else "各项指标与基线相比无劣化"
                )
                baseline_html = f"""
        <h2>📉 基线对比（{baseline.name}）</h2>
        <p style="color:#888;font-size:13px;margin-top:-12px;">判定：{baseline_verdict}（相对变化超过 10% 计为明显）</p>
        <table class="compare">
            <thead><tr><th>指标</th><th>基线</th><th>当前</th><th>变化</th><th>结论</th></tr></thead>
            <tbody>{tr_rows}</tbody>
        </table>
        """

        # 内存详情：**不依赖基线** —— 把 Total + 各分类（Java/Native/Graphics/
        # Stack/Code/Others）的 均值/峰值/最低 全列出来。以前这些数字只在
        # 「基线对比」表里出现，没配基线就整段消失，看不到明细。
        # 有基线且基线带内存数据时不再渲染：明细已经在基线对比表里了，
        # 再列一遍纯属冗余。判据看的是**基线对比表里是否真有内存行**，
        # 而不是「有没有基线对象」—— 老基线只有 CPU 数据时，内存明细仍要保留。
        mem_detail_html = ""
        baseline_covers_mem = any(
            str(r.get('key', '')).startswith('mem') for r in baseline_rows)
        mem_stat = stats.get('mem')
        if 'mem' in metrics and mem_stat and not baseline_covers_mem:
            breakdown = stats.get('mem_breakdown') or {}
            # 参数名与性能报告对齐：total_pss / res / java_heap / ...
            _mem_names = {
                "Java": "java_heap", "Native": "native_heap",
                "Graphics": "graphics_mem", "Stack": "stack_mem",
                "Code": "code_mem", "System": "system_mem", "Others": "other_mem",
            }

            def _stat(values):
                vals = [v for v in values if v > 0]
                if not vals:
                    return None
                return {"avg": sum(vals) / len(vals), "max": max(vals), "min": min(vals)}

            detail_rows = [('total_pss', mem_stat)]
            res_stat = _stat([float(getattr(x, 'mem_res_mb', 0.0) or 0.0)
                              for x in samples])
            if res_stat:
                detail_rows.append(('res', res_stat))
            detail_rows += [(k, breakdown[cat]) for cat, k in _mem_names.items()
                            if cat in breakdown]
            ns_stat = _stat([
                float(x.mem_breakdown.get("Native", 0.0))
                + float(x.mem_breakdown.get("System", 0.0)) for x in samples])
            if ns_stat:
                detail_rows.append(('native+system_mem', ns_stat))
            detail_tr = ""
            for label, st in detail_rows:
                detail_tr += f"""
                    <tr>
                        <td>{label}</td>
                        <td>{st.get('avg', 0):.2f}</td>
                        <td>{st.get('max', 0):.2f}</td>
                        <td>{st.get('min', 0):.2f}</td>
                    </tr>"""
            mem_detail_html = f"""
        <h2>🧩 内存详情</h2>
        <p style="font-size:12px;color:#888;">※ 参数名与性能报告一致；total_pss 与各分类来自
           <code>dumpsys meminfo</code>，res 为 TOTAL RSS，单位 MB。</p>
        <table class="compare compare4">
            <thead><tr><th>参数</th><th>均值 (MB)</th><th>峰值 (MB)</th><th>最低 (MB)</th></tr></thead>
            <tbody>{detail_tr}</tbody>
        </table>
        """

        # 系统内存（整机探针）：与「内存」卡口径不同，单独成节并标注清楚
        def _avg(v):
            return (sum(v) / len(v)) if v else 0.0

        # ---------- CPU 深度分析（P1）+ 线程分析（P2/P3） ----------
        cpu_analysis_html = ""
        thread_count_html = ""
        thread_cpu_html = ""
        cpu_p90 = 0.0
        thread_samples = list(getattr(session, 'thread_samples', None) or [])
        thread_rank = list(getattr(session, 'thread_rank', None) or [])
        # 旧版本会话的 thread_rank 是按 TID 聚合的旧口径（没有 active/activity 字段），
        # 直接渲染会是一排 "-"；丢掉它，回退到 Top15 聚合。
        if thread_rank and 'active' not in thread_rank[0]:
            thread_rank = []
        if 'cpu' in metrics and samples:
            cpu_points = [
                (s.timestamp, float(s.cpu_percent))
                for s in samples
                if not any(str(e).startswith('cpu:') for e in (getattr(s, 'errors', None) or ()))
            ]
            analysis = analyze_cpu(cpu_points, session.sample_interval, thread_samples)
            cpu_p90 = float((analysis.get('percentiles') or {}).get('p90') or 0.0)
            cpu_analysis_html = _render_cpu_analysis(analysis)
            thread_count_html, thread_cpu_html = _render_thread_analysis(
                thread_samples,
                (stats.get('cpu') or {}).get('avg', 0.0),
                thread_rank or None)

        sys_mem_html = ""
        _sm = [x for x in (getattr(session, "sys_mem_samples", None) or [])
               if x.get("total_mb")]
        if _sm:
            def _col(key):
                return [float(x.get(key) or 0) for x in _sm]

            used_v, pct_v = _col("used_mb"), _col("used_pct")
            free_v, zram_v = _col("free_mb"), _col("zram_mb")
            total_gb = _sm[-1].get("total_mb", 0) / 1024.0
            sys_mem_html = f"""
        <h2>🖥️ 系统内存（整机）</h2>
        <p style="font-size:12px;color:#888;">※ 本项为<b>整机口径</b>（Total RAM {total_gb:.1f} GB），包含设备上所有进程；
           与上方「内存」卡（所选进程）口径不同，两者不可直接相加或对比。</p>
        <table class="compare compare4">
            <thead><tr><th>项目</th><th>均值</th><th>峰值</th><th>最低</th></tr></thead>
            <tbody>
                <tr><td>已用 (MB)</td><td>{_avg(used_v):.0f}</td>
                    <td>{max(used_v):.0f}</td><td>{min(used_v):.0f}</td></tr>
                <tr><td>已用率 (%)</td><td>{_avg(pct_v):.1f}</td>
                    <td>{max(pct_v):.1f}</td><td>{min(pct_v):.1f}</td></tr>
                <tr><td>空闲 (MB)</td><td>{_avg(free_v):.0f}</td>
                    <td>{max(free_v):.0f}</td><td>{min(free_v):.0f}</td></tr>
                <tr><td>ZRAM 物理占用 (MB)</td><td>{_avg(zram_v):.0f}</td>
                    <td>{max(zram_v):.0f}</td><td>{min(zram_v):.0f}</td></tr>
            </tbody>
        </table>
        <p style="font-size:12px;color:#888;">共 {len(_sm)} 个采样点</p>
        """

        # 整机 CPU（探针）：与「CPU」卡的所选进程口径不同，单独成节
        sys_cpu_html = ""
        sys_raw_series = {}
        _sc = [x for x in (getattr(session, "sys_cpu_samples", None) or [])
               if "cpu_pct" in x]
        if _sc:
            cores = _sc[-1].get("cores") or 0

            def _raw_series(raw_key, norm_key):
                """优先用探针记录的 raw（多核累计）；旧会话用 归一化值 × 核数 还原。"""
                if any(raw_key in x for x in _sc):
                    return [float(x.get(raw_key) or 0) for x in _sc]
                if norm_key:
                    return [float(x.get(norm_key) or 0) * (x.get("cores") or cores or 0)
                            for x in _sc]
                return [0.0] * len(_sc)

            total_v = [float(x.get("total_pct") or (x.get("cores") or cores or 0) * 100)
                       for x in _sc]
            _CPU_DESC = {
                "D_total": "总容量（核数 × 100）",
                "D_busy": "占用 = 总容量 − 空闲",
                "D_user": "用户态",
                "D_nice": "低优先级（nice）",
                "D_sys": "内核态",
                "D_idle": "空闲",
                "D_iow": "IO 等待",
                "D_irq": "硬中断",
                "D_sirq": "软中断",
                "D_host": "虚拟化损失（steal）",
            }
            _rows = [
                ("D_total", total_v),
                ("D_busy", _raw_series("busy_raw", "cpu_pct")),
                ("D_user", _raw_series("user_raw", "user_pct")),
                ("D_nice", _raw_series("nice_raw", None)),
                ("D_sys", _raw_series("sys_raw", "sys_pct")),
                ("D_idle", _raw_series("idle_raw", "idle_pct")),
                ("D_iow", _raw_series("iow_raw", "iow_pct")),
                ("D_irq", _raw_series("irq_raw", None)),
                ("D_sirq", _raw_series("sirq_raw", None)),
                ("D_host", _raw_series("host_raw", None)),
            ]
            sys_raw_series = {name: vals for name, vals in _rows}
            table_rows = "".join(
                f"<tr><td>{name}</td><td>{_CPU_DESC.get(name, '')}</td>"
                f"<td>{_avg(vals):.2f}</td>"
                f"<td>{max(vals):.1f}</td><td>{min(vals):.1f}</td></tr>"
                for name, vals in _rows)
            degraded = any(x.get("degraded") for x in _sc)
            core_txt = f"{cores} 核" if cores else "核数未知"
            deg_note = ("<br>※ 该设备的 <code>top</code> 不支持批量模式，已降级为 "
                        "<code>/proc/stat</code> 差分。") if degraded else ""
            sys_cpu_html = f"""
        <h2>⚙️ 整机 CPU</h2>
        <p style="font-size:12px;color:#888;">※ 本项为<b>整机口径</b>（{core_txt}）：各项都是 <b>多核累计值</b>
           （满负荷上限 {cores * 100 if cores else '核数×100'}%），包含设备上所有进程，不做 0~100% 归一化。
           D_total 是总容量，D_busy = D_total − D_idle，其余各项分别是 CPU 时间花在了哪里；
           与上方「CPU」卡（所选进程）口径不同，两者不能互相换算。{deg_note}</p>
        {_chart_wrap('sysCpuChart')}
        <table class="compare">
            <thead><tr><th>指标</th><th>含义</th><th>平均值</th><th>最大值</th><th>最小值</th></tr></thead>
            <tbody>{table_rows}</tbody>
        </table>
        <p style="font-size:12px;color:#888;">共 {len(_sc)} 个采样点</p>
        """

        # 网卡流量（探针）：整机口径，按主网卡统计，多网卡不做求和
        netdev_html = ""
        _nd = [x for x in (getattr(session, "netdev_samples", None) or [])
               if "rx_kbps" in x]
        if _nd:
            rx_v = [float(x.get("rx_kbps") or 0) for x in _nd]
            tx_v = [float(x.get("tx_kbps") or 0) for x in _nd]
            primary = _nd[-1].get("primary", "")
            last_ifaces = _nd[-1].get("ifaces") or []
            iface_rows = "".join(
                f"<tr><td>{i.get('name', '')}</td><td>{float(i.get('rx_mb') or 0):.0f}</td>"
                f"<td>{float(i.get('tx_mb') or 0):.0f}</td></tr>"
                for i in last_ifaces)
            netdev_html = f"""
        <h2>📶 网卡流量（整机）</h2>
        <p style="font-size:12px;color:#888;">※ 本项为<b>整机口径</b>（设备上所有应用合计），
           按主网卡 <code>{primary}</code> 统计；车上同时存在多张网卡时它们是同一通道的不同层，
           相加会重复计数，故提供各网卡明细而不给「合计」。与上方「流量速率」卡（所选应用）口径不同。</p>
        <table class="compare compare4">
            <thead><tr><th>项目</th><th>均值</th><th>峰值</th><th>最低</th></tr></thead>
            <tbody>
                <tr><td>下行 (KB/s)</td><td>{_avg(rx_v):.1f}</td>
                    <td>{max(rx_v):.1f}</td><td>{min(rx_v):.1f}</td></tr>
                <tr><td>上行 (KB/s)</td><td>{_avg(tx_v):.1f}</td>
                    <td>{max(tx_v):.1f}</td><td>{min(tx_v):.1f}</td></tr>
            </tbody>
        </table>
        <p style="font-size:12px;color:#888;">各网卡累计（截止最后一次采样）：</p>
        <table class="compare">
            <thead><tr><th>网卡</th><th>累计下行 (MB)</th><th>累计上行 (MB)</th></tr></thead>
            <tbody>{iface_rows}</tbody>
        </table>
        <p style="font-size:12px;color:#888;">共 {len(_nd)} 个采样点</p>
        """

        # 磁盘 IO（探针，需 root）：没数据就不出这一节
        iotop_html = ""
        _io = [x for x in (getattr(session, "iotop_samples", None) or [])
               if "read_kbps" in x]
        if _io:
            rd_v = [float(x.get("read_kbps") or 0) for x in _io]
            wr_v = [float(x.get("write_kbps") or 0) for x in _io]
            top_map = {}
            for item in _io:
                for t in (item.get("top") or []):
                    key = t.get("comm", "")
                    cur = top_map.get(key, [0.0, 0.0])
                    cur[0] = max(cur[0], float(t.get("read_mb") or 0))
                    cur[1] = max(cur[1], float(t.get("write_mb") or 0))
                    top_map[key] = cur
            top_rows = "".join(
                f"<tr><td>{k}</td><td>{v[0]:.0f}</td><td>{v[1]:.0f}</td></tr>"
                for k, v in sorted(top_map.items(),
                                   key=lambda kv: kv[1][0] + kv[1][1], reverse=True)[:8])
            iotop_html = f"""
        <h2>💾 磁盘 IO（整机）</h2>
        <p style="font-size:12px;color:#888;">※ 本项为<b>整机口径</b>（设备上所有进程的读写合计），
           需要设备 root 才能采集（系统限制），与「所选进程」口径无关。</p>
        <table class="compare compare4">
            <thead><tr><th>项目</th><th>均值</th><th>峰值</th><th>最低</th></tr></thead>
            <tbody>
                <tr><td>读 (KB/s)</td><td>{_avg(rd_v):.1f}</td>
                    <td>{max(rd_v):.1f}</td><td>{min(rd_v):.1f}</td></tr>
                <tr><td>写 (KB/s)</td><td>{_avg(wr_v):.1f}</td>
                    <td>{max(wr_v):.1f}</td><td>{min(wr_v):.1f}</td></tr>
            </tbody>
        </table>
        <p style="font-size:12px;color:#888;">累计读写最多的进程（截止最后一次采样）：</p>
        <table class="compare">
            <thead><tr><th>进程</th><th>读 (MB)</th><th>写 (MB)</th></tr></thead>
            <tbody>{top_rows}</tbody>
        </table>
        <p style="font-size:12px;color:#888;">共 {len(_io)} 个采样点</p>
        """

        # 诊断事件 → 报告「诊断产物」章节 + 曲线上的事件竖线
        _EVENT_META = {
            "heap": ("堆转储", "#e67e22"),
            "capture": ("抓包", "#9b59b6"),
            "screenshot": ("截图", "#16a085"),
            "record": ("录屏", "#e84393"),
            "smaps": ("内存明细", "#27ae60"),
        }
        _events = sorted(getattr(session, "events", None) or [],
                         key=lambda x: x.get("ts", 0))
        events_html = ""
        event_markers_js = "[]"
        if _events:
            _rel_secs = [s.timestamp - samples[0].timestamp for s in samples] if samples else []
            rows, markers = [], []
            for e in _events:
                label, color = _EVENT_META.get(e.get("type", ""), (e.get("type", "") or "其他", "#999999"))
                rel = (e.get("ts", 0) - samples[0].timestamp) if samples else None
                rel_txt = f"{rel:.1f} 秒" if rel is not None else "—"
                rows.append(
                    f"<tr><td>{rel_txt}</td><td>{label}</td>"
                    f"<td>{e.get('file', '')}</td><td>{e.get('note', '')}</td></tr>")
                if rel is not None and _rel_secs and 0 <= rel <= _rel_secs[-1]:
                    idx = min(range(len(_rel_secs)), key=lambda i: abs(_rel_secs[i] - rel))
                    markers.append(f"{{x:{idx}, color:'{color}', label:'{label}'}}")
            events_html = f"""
        <h2>🧾 诊断产物</h2>
        <p style="font-size:12px;color:#888;">本次会话内产生的堆转储 / 抓包 / 截图 / 录屏 / 内存明细，按发生时刻列出（相对会话开始）：</p>
        <table class="compare">
            <thead><tr><th>时刻</th><th>类型</th><th>文件</th><th>备注</th></tr></thead>
            <tbody>{''.join(rows)}</tbody>
        </table>
        """
            event_markers_js = "[" + ",".join(markers) + "]"

        # 曲线区域与图表脚本：只渲染本次勾选采集的指标
        charts_section = ""
        chart_scripts = []

        def _wrap(canvas_id):
            """图表容器 + 重置缩放按钮（拖拽平移/滚轮缩放后一键回到全量视图）"""
            return _chart_wrap(canvas_id)

        if 'cpu' in metrics:
            charts_section += _wrap('cpuChart')
            chart_scripts.append(CHART_JS_CPU)
        if 'mem' in metrics:
            charts_section += _wrap('memChart')
            # 采集到了分类拆解时画 Android Studio 风格堆叠面积图，否则保持单线 PSS
            chart_scripts.append(
                CHART_JS_MEM_BREAKDOWN if has_mem_breakdown else CHART_JS_MEM
            )
        if 'fps' in metrics:
            charts_section += _wrap('fpsChart')
            chart_scripts.append(CHART_JS_FPS)
        if has_traffic:
            charts_section += _wrap('trafficChart')
            # 流量只能按 UID 统计（系统只有这个口径），不说清楚会被当成"该进程的流量"
            charts_section += (
                '<p style="font-size:12px;color:#888;margin:2px 0 10px;">'
                '※ 网络流量按应用 UID 统计，包含该应用的全部进程，不区分主 / 子进程。</p>'
            )
            chart_scripts.append(CHART_JS_TRAFFIC)
        if charts_section:
            charts_section = '<h2>📈 实时曲线</h2>' + charts_section

        # 分析区的折线图脚本（画布已嵌在对应章节 HTML 里）
        if cpu_analysis_html:
            chart_scripts.append(CHART_JS_CPU_PEAK)
        if sys_cpu_html:
            chart_scripts.append(CHART_JS_SYS_CPU)
        if thread_count_html:
            chart_scripts.append(CHART_JS_THREAD_COUNT)
            chart_scripts.append(CHART_JS_THREAD_CPU)
        charts_js = "\n\n".join(chart_scripts)

        # ---- 分析区折线图的数据（x 轴相对各自首个采样点，按总时长自动选时分/分秒）----
        _sys_pts = [x for x in (getattr(session, 'sys_cpu_samples', None) or [])
                    if 'cpu_pct' in x]
        # 横轴与主曲线同一套规则：时长 ≥1h 用「时:分」，≥1min 用「分:秒」，否则秒
        if _sys_pts:
            _sys_unit, _sys_labels = _time_axis_labels(
                [x.get('ts') for x in _sys_pts], float(_sys_pts[0].get('ts') or 0))
        else:
            _sys_unit, _sys_labels = "秒", []
        sys_cpu_axis_title = f"时间 ({_sys_unit})"
        sys_cpu_labels_js = ",".join(f"'{l}'" for l in _sys_labels)

        def _sys_js(key):
            """D_* 系列转 JS 数组（多核累计，不做归一化）。"""
            return ",".join(f"{v:.1f}" for v in sys_raw_series.get(key, []))

        sys_cpu_total_js = _sys_js("D_total")
        sys_cpu_data_js = _sys_js("D_busy")
        sys_cpu_user_js = _sys_js("D_user")
        sys_cpu_nice_js = _sys_js("D_nice")
        sys_cpu_sys_js = _sys_js("D_sys")
        sys_cpu_idle_js = _sys_js("D_idle")
        sys_cpu_iow_js = _sys_js("D_iow")
        sys_cpu_irq_js = _sys_js("D_irq")
        sys_cpu_sirq_js = _sys_js("D_sirq")
        sys_cpu_host_js = _sys_js("D_host")

        _th_pts = [x for x in (getattr(session, 'thread_samples', None) or [])
                   if 'thread_total' in x]
        if _th_pts:
            _th_unit, _th_labels = _time_axis_labels(
                [x.get('ts') for x in _th_pts], float(_th_pts[0].get('ts') or 0))
        else:
            _th_unit, _th_labels = "秒", []
        thread_axis_title = f"时间 ({_th_unit})"
        thread_labels_js = ",".join(f"'{l}'" for l in _th_labels)
        thread_total_js = ",".join(str(int(x.get('thread_total') or 0)) for x in _th_pts)
        thread_active_js = ",".join(str(int(x.get('thread_active') or 0)) for x in _th_pts)

        # 线程 CPU 多线图：TOTAL + 均值最高的 45 个线程名（与百度 topn=45 对齐）
        # 按线程名聚合（同名线程池求和）；某采样点没进 Top 45 → null（断点，不臆造 0）
        _rank = list(thread_rank)
        if not _rank and _th_pts:
            _rank = analyze_threads(_th_pts).get('threads') or []

        def _hsl(i, n):
            """等间隔色相，series 再多也能区分开。"""
            hue = int(360.0 * (i % max(n, 1)) / max(n, 1))
            return f"hsl({hue},65%,48%)"

        _thread_cpu_series = []
        if _th_pts:
            _thread_cpu_series.append({
                'label': 'TOTAL',
                'color': '#e74c3c',
                'width': 2.2,
                'data': [round(float(s.get('cpu_total') or 0), 2) for s in _th_pts],
            })
        _top_names = []
        _seen_names = set()
        for _th in _rank:
            _n = _th.get('name')
            if not _n or _n in _seen_names:
                continue                 # 旧会话的 rank 按 TID 存，同名会重复出现
            _seen_names.add(_n)
            _top_names.append(_n)
            if len(_top_names) >= 45:
                break
        for _i, _name in enumerate(_top_names):
            _data = []
            for _s in _th_pts:
                _val = None
                for _t in (_s.get('top') or []):
                    if _t.get('name') == _name:
                        _val = (_val or 0.0) + float(_t.get('cpu') or 0)
                _data.append(round(_val, 2) if _val is not None else None)
            _thread_cpu_series.append({
                'label': str(_name),
                'color': _hsl(_i, len(_top_names)),
                'data': _data,
            })
        thread_cpu_series_js = json.dumps(_thread_cpu_series, ensure_ascii=False)

        # ---- 测试详情汇总表（对齐百度报告的「一、性能测试详情」）----
        # 目标进程（主进程在第一个）：多进程时详情表按进程逐行给出均值/峰值/最低
        _procs = list(getattr(session, "app_processes", None) or []) or [session.app_package]
        _proc_row = ""
        if len(_procs) > 1:
            _proc_row = ('<tr><td class="label">目标进程</td><td>'
                         + "<br>".join(_procs) + "</td></tr>")

        _detail_rows = []
        if 'cpu' in metrics:
            _s = stats.get('cpu') or {}
            _detail_rows.append((
                "进程 CPU 合计 (%)" if len(_procs) > 1 else "进程 CPU (%)",
                f"{_s.get('avg', 0):.1f}", f"{_s.get('max', 0):.1f}",
                f"{_s.get('min', 0):.1f}",
                f"P90 {cpu_p90:.1f}%"))
            if len(_procs) > 1:
                for _name in _procs:
                    _vals = [float((s.cpu_by_process or {}).get(_name))
                             for s in samples if _name in (s.cpu_by_process or {})]
                    if not _vals:
                        continue
                    _detail_rows.append((
                        f"CPU：{_name}", f"{sum(_vals) / len(_vals):.1f}",
                        f"{max(_vals):.1f}", f"{min(_vals):.1f}", "单进程"))
        if _sys_pts:
            # 与「设备 CPU」页同口径：多核累计，不做 0~100% 归一化
            _v = list(sys_raw_series.get('D_busy') or [])
            if _v:
                _detail_rows.append((
                    "整机 CPU (%)", f"{sum(_v) / len(_v):.1f}", f"{max(_v):.1f}",
                    f"{min(_v):.1f}", f"整机口径，多核累计（{cores or '?'} 核）"))
        _ta = analyze_threads(_th_pts) if _th_pts else {}
        _tt = _ta.get('totals') or {}
        if _tt:
            _detail_rows.append((
                "线程总数", f"{_tt['total_avg']:.0f}", f"{_tt['total_max']}",
                f"{_tt['total_min']}", f"活跃均值 {_tt['active_avg']:.0f}"))
            _detail_rows.append((
                "线程 CPU 合计 (%)", f"{_tt['cpu_avg']:.1f}", f"{_tt['cpu_max']:.1f}",
                f"{_tt['cpu_min']:.1f}", "多核累计"))
        if 'mem' in metrics:
            _s = stats.get('mem') or {}
            _detail_rows.append((
                "total_pss (MB)", f"{_s.get('avg', 0):.1f}", f"{_s.get('max', 0):.1f}",
                f"{_s.get('min', 0):.1f}",
                "多进程合计" if len(_procs) > 1 else "所选进程"))
            _res_vals = [float(getattr(x, 'mem_res_mb', 0.0) or 0.0) for x in samples]
            _res_vals = [v for v in _res_vals if v > 0]
            if _res_vals:
                _detail_rows.append((
                    "res (MB)", f"{sum(_res_vals) / len(_res_vals):.1f}",
                    f"{max(_res_vals):.1f}", f"{min(_res_vals):.1f}", "TOTAL RSS"))
        if 'fps' in metrics:
            _s = stats.get('fps') or {}
            _detail_rows.append((
                "FPS", f"{_s.get('avg', 0):.1f}", f"{_s.get('max', 0):.0f}",
                f"{_s.get('min', 0):.0f}", ""))
        if 'traffic' in stats:
            _t = stats['traffic']
            _detail_rows.append((
                "流量 (MB)", f"↓{_t['rx_mb']:.1f} / ↑{_t['tx_mb']:.1f}", "-", "-", "按 UID"))
        detail_html = ""
        if _detail_rows:
            _rows = "".join(
                f"<tr><td>{r[0]}</td><td>{r[1]}</td><td>{r[2]}</td>"
                f"<td>{r[3]}</td><td>{r[4]}</td></tr>" for r in _detail_rows)
            detail_html = f"""
        <h2>📌 测试详情</h2>
        <table class="compare">
            <thead><tr><th>指标</th><th>均值</th><th>峰值</th><th>最低</th><th>备注</th></tr></thead>
            <tbody>{_rows}</tbody>
        </table>"""

        # ---- 详细分析改成 Tab（对齐百度报告：设备cpu / 线程数量 / 线程cpu / 峰值分析）----
        _tabs = []
        if sys_cpu_html:
            _tabs.append(("tab-sys", "设备 CPU", sys_cpu_html))
        if thread_count_html:
            _tabs.append(("tab-thread-count", "线程数量", thread_count_html))
        if thread_cpu_html:
            _tabs.append(("tab-thread-cpu", "线程 CPU", thread_cpu_html))
        if cpu_analysis_html:
            _tabs.append(("tab-cpu", "CPU 峰值分析", cpu_analysis_html))
        analysis_html = ""
        if _tabs:
            _btns = "".join(
                f'<button class="tab-btn{" active" if _i == 0 else ""}" '
                f'data-tab="{_tid}">{_label}</button>'
                for _i, (_tid, _label, _h) in enumerate(_tabs))
            _panels = "".join(
                f'<div class="tab-panel{" active" if _i == 0 else ""}" id="{_tid}">{_h}</div>'
                for _i, (_tid, _label, _h) in enumerate(_tabs))
            analysis_html = (
                '<h2>🔬 详细分析</h2>'
                f'<div class="tabs"><div class="tab-bar">{_btns}</div>{_panels}</div>')

        # 统计口径：让读者一眼看清每个数字统计的是"谁"
        _scopes = getattr(session, "scopes", None) or {}
        _scope_parts = []
        for _k in ("cpu", "mem", "fps", "traffic"):
            if _k in metrics and _scopes.get(_k):
                _label = SCOPE_LABELS.get(_scopes[_k], _scopes[_k])
                if len(_procs) > 1:
                    # 多进程目标：CPU / 内存按进程求和，FPS / 流量按主进程
                    _label += "（多进程合计）" if _k in ("cpu", "mem") else "（主进程）"
                _scope_parts.append(f"{metric_label(_k)} = {_label}")
        # 整机探针也标出来（它不在 metrics 里，是按勾选启动的旁路采集）
        if getattr(session, "sys_mem_samples", None):
            _scope_parts.append(f"系统内存 = {SCOPE_LABELS.get(SCOPE_SYSTEM, '整机')}")
        if getattr(session, "sys_cpu_samples", None):
            _scope_parts.append(f"整机 CPU = {SCOPE_LABELS.get(SCOPE_SYSTEM, '整机')}")
        if getattr(session, "netdev_samples", None):
            _scope_parts.append(f"网卡流量 = {SCOPE_LABELS.get(SCOPE_SYSTEM, '整机')}")
        if getattr(session, "iotop_samples", None):
            _scope_parts.append(f"磁盘 IO = {SCOPE_LABELS.get(SCOPE_SYSTEM, '整机')}")
        scope_text = "、".join(_scope_parts) or "未记录"
        if getattr(session, "scopes_inferred", False):
            scope_text += "（旧会话，按当前默认推断）"

        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>性能报告 - {session.name}</title>
    {_chart_js_tags()}
    <style>
        * {{ box-sizing: border-box; }}
        body {{
            font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;
            margin: 0;
            padding: 20px;
            background: #f5f6fa;
            color: #333;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
            background: #fff;
            padding: 24px 32px;
            border-radius: 10px;
            box-shadow: 0 2px 12px rgba(0,0,0,0.08);
        }}
        h1 {{
            color: #1976d2;
            border-bottom: 2px solid #1976d2;
            padding-bottom: 12px;
            margin-top: 0;
        }}
        h2 {{
            font-size: 18px;
            color: #333;
            margin-top: 28px;
            border-left: 4px solid #1976d2;
            padding-left: 12px;
        }}
        table.info {{
            width: 100%;
            border-collapse: collapse;
            margin-bottom: 20px;
        }}
        table.info td {{
            padding: 8px 12px;
            border-bottom: 1px solid #eee;
            font-size: 13px;
        }}
        table.info td.label {{
            width: 100px;
            color: #666;
            font-weight: 500;
        }}
        .summary {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
            gap: 16px;
            margin: 20px 0;
        }}
        .card {{
            background: #f8f9fa;
            border: 1px solid #e0e0e0;
            border-radius: 8px;
            padding: 16px;
            text-align: center;
        }}
        .card .value {{
            font-size: 24px;
            font-weight: bold;
            color: #1976d2;
        }}
        .card .label {{
            font-size: 12px;
            color: #888;
            margin-top: 4px;
        }}
        .card .sub {{
            font-size: 12px;
            color: #666;
            margin-top: 6px;
            padding-top: 6px;
            border-top: 1px dashed #e0e0e0;
        }}
        .chart-wrap {{
            background: #fafbfc;
            border: 1px solid #e8e8e8;
            border-radius: 8px;
            padding: 16px;
            margin: 12px 0 4px;
            /* 图表高度：Chart.js 是 responsive + maintainAspectRatio:false，
               完全跟着这个容器走（原 280px 时绘图区只剩 ~130px，太扁） */
            height: 400px;
        }}
        .chart-legend {{
            display: flex;
            flex-wrap: wrap;
            gap: 4px 14px;
            margin: 8px 2px 6px;
            font-size: 12px;
            color: #555;
        }}
        .chart-legend .legend-item {{
            display: inline-flex;
            align-items: center;
            gap: 5px;
            cursor: pointer;
            user-select: none;
            line-height: 18px;
        }}
        .chart-legend .legend-item i {{
            width: 12px;
            height: 3px;
            border-radius: 2px;
            display: inline-block;
            flex: none;
        }}
        .chart-legend .legend-item.off {{
            opacity: 0.35;
            text-decoration: line-through;
        }}
        .chart-toolbar {{
            text-align: right;
            margin: 0 0 12px;
        }}
        .chart-toolbar button + button {{
            margin-left: 8px;
        }}
        .zoom-reset,
        .chart-select-all,
        .chart-invert {{
            padding: 3px 12px;
            font-size: 12px;
            color: #666;
            background: #fff;
            border: 1px solid #d0d0d0;
            border-radius: 6px;
            cursor: pointer;
            font-family: inherit;
        }}
        .zoom-reset:hover,
        .chart-select-all:hover,
        .chart-invert:hover {{
            color: #1976d2;
            border-color: #1976d2;
        }}
        .footer {{
            text-align: center;
            font-size: 12px;
            color: #999;
            margin-top: 40px;
            padding-top: 16px;
            border-top: 1px solid #eee;
        }}
        .alert-item {{
            color: #e74c3c;
        }}
        table.compare {{
            width: 100%;
            border-collapse: collapse;
            margin: 8px 0 20px;
        }}
        table.compare th, table.compare td {{
            padding: 8px 12px;
            border-bottom: 1px solid #eee;
            text-align: center;
            font-size: 13px;
        }}
        table.compare th {{
            background: #f8f9fa;
            color: #666;
            font-weight: 600;
        }}
        /* 4 列表（内存详情 / 系统内存 / 整机 CPU）用固定列宽，三张表的列位置才能对齐；
           否则「ZRAM 物理占用 (MB)」这类长标签会把第一列撑宽，后三列整体右移 */
        table.compare4 {{
            table-layout: fixed;
        }}
        table.compare4 th:first-child, table.compare4 td:first-child {{
            width: 22%;
        }}
        .badge {{
            display: inline-block;
            padding: 2px 10px;
            border-radius: 10px;
            font-size: 12px;
            font-weight: 600;
        }}
        .badge.worse {{ background: #fdecea; color: #e74c3c; }}
        .badge.better {{ background: #eafaf1; color: #27ae60; }}
        .badge.stable {{ background: #f0f0f0; color: #888; }}
        /* 贡献汇总：一行指标小标签 */
        .chips {{
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            margin: 4px 0 8px;
        }}
        .chip {{
            display: inline-block;
            padding: 3px 12px;
            font-size: 12px;
            color: #444;
            background: #f4f7fb;
            border: 1px solid #e3ecf7;
            border-radius: 12px;
        }}
        .chip b {{ color: #1976d2; }}
        /* 贡献类型规则：原生 details 折叠，默认展开 */
        details.rules {{
            margin: 6px 0 10px;
        }}
        details.rules > summary {{
            cursor: pointer;
            font-size: 13px;
            color: #555;
            margin-bottom: 6px;
        }}
        details.rules > summary:hover {{ color: #1976d2; }}
        /* 线程贡献表里的迷你趋势图 */
        td.spark-cell {{ padding: 2px 8px; }}
        svg.spark {{ display: block; }}
        /* 表格「复制」按钮：贴在表格右上角上方，不压表头文字 */
        .table-wrap {{ position: relative; padding-top: 18px; }}
        .table-wrap > .copy-table {{
            position: absolute;
            top: 0;
            right: 2px;
            z-index: 2;
            padding: 1px 9px;
            font-size: 11px;
            color: #888;
            background: #fff;
            border: 1px solid #dcdcdc;
            border-radius: 5px;
            cursor: pointer;
            font-family: inherit;
        }}
        .table-wrap > .copy-table:hover {{
            color: #1976d2;
            border-color: #1976d2;
        }}
        /* 详细分析：Tab 切换（隐藏面板里的图表在切过来时 resize） */
        .tabs {{ margin: 8px 0 22px 0; }}
        .tab-bar {{
            display: flex;
            flex-wrap: wrap;
            gap: 4px;
            border-bottom: 2px solid #e8eaed;
            margin-bottom: 16px;
        }}
        .tab-btn {{
            background: transparent;
            border: none;
            border-bottom: 2px solid transparent;
            margin-bottom: -2px;
            padding: 8px 16px;
            font-size: 14px;
            color: #666;
            cursor: pointer;
            font-family: inherit;
        }}
        .tab-btn:hover {{ color: #1976d2; }}
        .tab-btn.active {{
            color: #1976d2;
            border-bottom-color: #1976d2;
            font-weight: 600;
        }}
        .tab-panel {{ display: none; }}
        .tab-panel.active {{ display: block; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>⚡ 性能检测报告</h1>
        <p style="color:#888;font-size:13px;">生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>

        <h2>📋 基本信息</h2>
        <table class="info">
            <tr>
                <td class="label">会话名称</td>
                <td>{session.name}</td>
            </tr>
            <tr>
                <td class="label">应用包名</td>
                <td>{app_pkg}</td>
            </tr>
            {_proc_row}
            <tr>
                <td class="label">设备</td>
                <td>{device}</td>
            </tr>
            {device_info_rows}
            <tr>
                <td class="label">开始时间</td>
                <td>{start}</td>
            </tr>
            <tr>
                <td class="label">结束时间</td>
                <td>{end}</td>
            </tr>
            <tr>
                <td class="label">持续时间</td>
                <td>{duration}</td>
            </tr>
            <tr>
                <td class="label">采样间隔</td>
                <td>{session.sample_interval} 秒</td>
            </tr>
            <tr>
                <td class="label">监控指标</td>
                <td>{'、'.join(metric_label(m) for m in metrics)}</td>
            </tr>
            <tr>
                <td class="label">统计口径</td>
                <td>{scope_text}</td>
            </tr>
            {scenario_info}
        </table>

        {detail_html}

        {mem_detail_html}

        <h2>📊 关键指标</h2>
        <div class="summary">
            {summary_cards_html}
        </div>

        {charts_section}

        {analysis_html}

        {sys_mem_html}

        {netdev_html}

        {iotop_html}

        {events_html}

        {baseline_html}

        {alerts_html}

        <div class="footer">
            <p>报告由虫师自动生成 · © 2026 虫师团队</p>
        </div>
    </div>

    <script>
        const timestamps = [{','.join(timestamps)}];
        const axisLabels = [{','.join(f"'{l}'" for l in axis_labels)}];
        const axisUnit = '{axis_unit}';
        const cpuData = [{','.join(cpu_series)}];
        const cpuSeries = {cpu_chart_series_js};
        const memData = [{','.join(mem_series)}];
        const memSeries = {mem_chart_series_js};
        const javaData = [{','.join(mem_cat_series['Java'])}];
        const nativeData = [{','.join(mem_cat_series['Native'])}];
        const graphicsData = [{','.join(mem_cat_series['Graphics'])}];
        const stackData = [{','.join(mem_cat_series['Stack'])}];
        const codeData = [{','.join(mem_cat_series['Code'])}];
        const othersData = [{','.join(mem_cat_series['Others'])}];
        const fpsData = [{','.join(fps_series)}];
        const rxRateData = [{','.join(rx_rate_series)}];
        const txRateData = [{','.join(tx_rate_series)}];

        // 分析区折线图（CPU 峰值 / 设备 CPU / 线程数量 / 线程 CPU）
        const cpuP90 = {cpu_p90};
        const cpuThresholdLine = cpuData.map(function () {{ return cpuP90; }});
        const cpuPeakPoints = cpuData.map(function (v) {{ return (v !== null && v >= cpuP90) ? v : null; }});
        const sysCpuLabels = [{sys_cpu_labels_js}];
        const sysCpuAxisTitle = '{sys_cpu_axis_title}';
        const sysCpuTotal = [{sys_cpu_total_js}];
        const sysCpuData = [{sys_cpu_data_js}];
        const sysCpuUser = [{sys_cpu_user_js}];
        const sysCpuNice = [{sys_cpu_nice_js}];
        const sysCpuSys = [{sys_cpu_sys_js}];
        const sysCpuIdle = [{sys_cpu_idle_js}];
        const sysCpuIow = [{sys_cpu_iow_js}];
        const sysCpuIrq = [{sys_cpu_irq_js}];
        const sysCpuSirq = [{sys_cpu_sirq_js}];
        const sysCpuHost = [{sys_cpu_host_js}];
        const threadLabels = [{thread_labels_js}];
        const threadAxisTitle = '{thread_axis_title}';
        const threadTotalData = [{thread_total_js}];
        const threadActiveData = [{thread_active_js}];
        const threadCpuSeries = {thread_cpu_series_js};

        // 诊断事件竖线：在每条曲线上对应时刻画一条彩色虚线（颜色对应产物类型）
        const eventMarkers = {event_markers_js};
        const eventLinePlugin = {{
            id: 'eventLines',
            afterDatasetsDraw: function(chart) {{
                if (!eventMarkers.length) return;
                var area = chart.chartArea;
                var xScale = chart.scales.x;
                eventMarkers.forEach(function(m) {{
                    var x = xScale.getPixelForValue(m.x);
                    var ctx = chart.ctx;
                    ctx.save();
                    ctx.strokeStyle = m.color;
                    ctx.lineWidth = 1;
                    ctx.setLineDash([4, 3]);
                    ctx.globalAlpha = 0.75;
                    ctx.beginPath();
                    ctx.moveTo(x, area.top);
                    ctx.lineTo(x, area.bottom);
                    ctx.stroke();
                    ctx.restore();
                }});
            }}
        }};
        Chart.register(eventLinePlugin);
        // 图例统一放到画布下方（HTML 图例），内置图例在系列多时会压住曲线
        Chart.defaults.plugins.legend.display = false;

        {charts_js}

        // 画布下方图例：点一项隐藏/显示对应曲线
        function buildLegend(chart) {{
            var box = document.getElementById(chart.canvas.id + '-legend');
            if (!box) return;
            box.innerHTML = '';
            if (chart.data.datasets.length <= 1) {{
                // 单条曲线：不显示图例，也不需要「全选 / 反选」
                document.querySelectorAll(
                    '.chart-toolbar button[data-chart="' + chart.canvas.id + '"]'
                ).forEach(function (b) {{
                    if (!b.classList.contains('zoom-reset')) b.style.display = 'none';
                }});
                return;
            }}
            chart.data.datasets.forEach(function (ds, i) {{
                var item = document.createElement('span');
                item.className = 'legend-item';
                var swatch = document.createElement('i');
                swatch.style.background = ds.borderColor || ds.backgroundColor || '#999';
                var text = document.createElement('span');
                text.textContent = ds.label;
                item.appendChild(swatch);
                item.appendChild(text);
                item.addEventListener('click', function () {{
                    var visible = chart.isDatasetVisible(i);
                    chart.setDatasetVisibility(i, !visible);
                    chart.update('none');
                    item.classList.toggle('off', visible);
                }});
                box.appendChild(item);
            }});
            syncLegend(chart);
        }}

        function syncLegend(chart) {{
            var box = document.getElementById(chart.canvas.id + '-legend');
            if (!box) return;
            chart.data.datasets.forEach(function (ds, i) {{
                var item = box.children[i];
                if (item) item.classList.toggle('off', !chart.isDatasetVisible(i));
            }});
        }}

        function chartOf(btn) {{
            var canvas = document.getElementById(btn.dataset.chart);
            return canvas ? Chart.getChart(canvas) : null;
        }}

        document.querySelectorAll('canvas').forEach(function (c) {{
            var ch = Chart.getChart(c);
            if (ch) buildLegend(ch);
        }});

        // 「全选 / 反选」：批量切换曲线的显示状态
        document.querySelectorAll('.chart-select-all').forEach(function (btn) {{
            btn.addEventListener('click', function () {{
                var chart = chartOf(btn);
                if (!chart) return;
                chart.data.datasets.forEach(function (_, i) {{
                    chart.setDatasetVisibility(i, true);
                }});
                chart.update('none');
                syncLegend(chart);
            }});
        }});
        document.querySelectorAll('.chart-invert').forEach(function (btn) {{
            btn.addEventListener('click', function () {{
                var chart = chartOf(btn);
                if (!chart) return;
                chart.data.datasets.forEach(function (_, i) {{
                    chart.setDatasetVisibility(i, !chart.isDatasetVisible(i));
                }});
                chart.update('none');
                syncLegend(chart);
            }});
        }});

        // 表格右上角「复制」：整表按 Tab 分隔复制，可直接粘到 Excel
        function copyTable(table, btn) {{
            var lines = [];
            table.querySelectorAll('tr').forEach(function (tr) {{
                var cells = [];
                tr.querySelectorAll('th,td').forEach(function (c) {{
                    cells.push(c.innerText.replace(/\\s+/g, ' ').trim());
                }});
                if (cells.length) lines.push(cells.join('\\t'));
            }});
            var text = lines.join('\\n');

            function done() {{
                var old = btn.textContent;
                btn.textContent = '已复制';
                setTimeout(function () {{ btn.textContent = old; }}, 1200);
            }}

            function fallback() {{
                var ta = document.createElement('textarea');
                ta.value = text;
                ta.style.position = 'fixed';
                ta.style.opacity = '0';
                document.body.appendChild(ta);
                ta.select();
                try {{ document.execCommand('copy'); done(); }}
                catch (e) {{ btn.textContent = '复制失败'; }}
                document.body.removeChild(ta);
            }}

            if (navigator.clipboard && navigator.clipboard.writeText) {{
                navigator.clipboard.writeText(text).then(done, fallback);
            }} else {{
                fallback();
            }}
        }}

        document.querySelectorAll('table.compare').forEach(function (t) {{
            var wrap = document.createElement('div');
            wrap.className = 'table-wrap';
            t.parentNode.insertBefore(wrap, t);
            wrap.appendChild(t);
            var btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'copy-table';
            btn.textContent = '复制';
            btn.addEventListener('click', function () {{ copyTable(t, btn); }});
            wrap.appendChild(btn);
        }});

        // 「详细分析」Tab：切到隐藏面板时，Chart.js 需要重新量一次尺寸（否则是 0）
        document.querySelectorAll('.tab-btn').forEach(function (btn) {{
            btn.addEventListener('click', function () {{
                var root = btn.closest('.tabs');
                if (!root) return;
                root.querySelectorAll('.tab-btn').forEach(function (b) {{
                    b.classList.remove('active');
                }});
                root.querySelectorAll('.tab-panel').forEach(function (p) {{
                    p.classList.remove('active');
                }});
                btn.classList.add('active');
                var panel = root.querySelector('#' + btn.dataset.tab);
                if (!panel) return;
                panel.classList.add('active');
                panel.querySelectorAll('canvas').forEach(function (c) {{
                    var ch = Chart.getChart(c);
                    if (ch) ch.resize();
                }});
            }});
        }});

        // 「重置缩放」：回到全量视图（拖拽平移/滚轮缩放后用）
        document.querySelectorAll('.zoom-reset').forEach(function (btn) {{
            btn.addEventListener('click', function () {{
                var canvas = document.getElementById(btn.dataset.chart);
                var chart = canvas && Chart.getChart(canvas);
                if (chart) chart.resetZoom();
            }});
        }});
    </script>
</body>
</html>"""

        out_dir = os.path.dirname(file_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(html)
        return file_path