# utils/perf_report.py
"""性能检测 HTML 报告生成器"""
import os
import sys
import base64
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
        datasets: [{
            label: 'CPU (%)',
            data: cpuData,
            borderColor: '#3498db',
            backgroundColor: 'rgba(52,152,219,0.1)',
            borderWidth: 2,
            fill: true,
            tension: 0.3
        }]
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
            label: '内存 (MB)',
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
        datasets: [
            { label: 'Total', data: memData, borderColor: '#27ae60', backgroundColor: 'transparent', borderWidth: 2, fill: false, tension: 0.3 },
            { label: 'Java', data: javaData, borderColor: '#f39c12', backgroundColor: 'transparent', borderWidth: 2, fill: false, tension: 0.3 },
            { label: 'Native', data: nativeData, borderColor: '#9b59b6', backgroundColor: 'transparent', borderWidth: 2, fill: false, tension: 0.3 },
            { label: 'Graphics', data: graphicsData, borderColor: '#e74c3c', backgroundColor: 'transparent', borderWidth: 2, fill: false, tension: 0.3 },
            { label: 'Stack', data: stackData, borderColor: '#1abc9c', backgroundColor: 'transparent', borderWidth: 2, fill: false, tension: 0.3 },
            { label: 'Code', data: codeData, borderColor: '#3498db', backgroundColor: 'transparent', borderWidth: 2, fill: false, tension: 0.3 },
            { label: 'Others', data: othersData, borderColor: '#95a5a6', backgroundColor: 'transparent', borderWidth: 2, fill: false, tension: 0.3 }
        ]
    },
    options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
            legend: { display: true, position: 'top' }, title: { display: true, text: '内存占用 (分类拆解, MB)' },
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
            legend: { display: true }, title: { display: true, text: '流量速率 (KB/s)' },
            zoom: { pan: { enabled: true, mode: 'x' }, zoom: { wheel: { enabled: true }, mode: 'x' } }
        },
        scales: {
            x: { display: true, title: { display: true, text: `时间 (${axisUnit})` } },
            y: { beginAtZero: true }
        },
        elements: { point: { radius: 0 } }
    }
});"""


def _fmt_pct(value) -> str:
    return "-" if value is None else f"{value:.1f}%"


def _render_cpu_analysis(a: dict) -> str:
    """CPU 深度分析章节（分位数 / 阈值分档 / 百分位分档 / 连续段 / 峰值点）。"""
    if not a.get("count"):
        return ""
    p = a.get("percentiles") or {}
    parts = [f"""
        <h2>🔍 CPU 分析</h2>
        <p style="font-size:12px;color:#888;">※ 基于 {a['count']} 个有效采样点（间隔 {a['interval']} 秒）；
           分位数用最近秩法。阈值分档的阈值随本次峰值自适应，适合对比同一场景的不同版本。</p>
        <p style="font-size:12px;color:#888;">分位数：</p>
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
            f"<td>{r['share']:.1f}%</td><td>{r['avg']:.2f}%</td>"
            f"<td>{r['max']:.2f}%</td><td>{_fmt_pct(r['top5'])}</td></tr>"
            for r in a["thresholds"])
        parts.append(f"""
        <p style="font-size:12px;color:#888;">阈值分档（采样值 ≥ 阈值）：</p>
        <table class="compare">
            <thead><tr><th>阈值 (%)</th><th>采样数</th><th>占比</th>
                <th>段内均值</th><th>段内最大</th><th>Top5 线程均值</th></tr></thead>
            <tbody>{rows}</tbody>
        </table>""")

    if a.get("percentile_buckets"):
        rows = "".join(
            f"<tr><td>{r['label']}</td><td>{r['threshold']:.2f}</td>"
            f"<td>{r['count']}</td><td>{r['share']:.1f}%</td>"
            f"<td>{r['avg']:.2f}%</td><td>{r['max']:.2f}%</td>"
            f"<td>{_fmt_pct(r['top5'])}</td></tr>"
            for r in a["percentile_buckets"])
        parts.append(f"""
        <p style="font-size:12px;color:#888;">百分位分档（按采样点数量取最高的 N%）：</p>
        <table class="compare">
            <thead><tr><th>分位段</th><th>对应阈值</th><th>采样数</th>
                <th>实际占比</th><th>段内均值</th><th>段内最大</th><th>Top5 线程均值</th></tr></thead>
            <tbody>{rows}</tbody>
        </table>""")

    segs = a.get("segments") or []
    iso = a.get("isolated_peaks", 0)
    if segs:
        rows = "".join(
            f"<tr><td>#{r['start']} ~ #{r['end']}</td><td>{r['duration']:.1f}s</td>"
            f"<td>{r['avg']:.2f}%</td><td>{r['max']:.2f}%</td>"
            f"<td>{r['dominant'] or '-'}</td></tr>"
            for r in segs)
        parts.append(f"""
        <p style="font-size:12px;color:#888;">连续峰值段（连续 ≥2 个采样点均 ≥ P90）：</p>
        <table class="compare">
            <thead><tr><th>采样点区间</th><th>持续</th><th>段内均值</th>
                <th>段内最大</th><th>主导线程</th></tr></thead>
            <tbody>{rows}</tbody>
        </table>""")
        if iso:
            parts.append(
                f'<p style="font-size:12px;color:#888;">另有 {iso} 个孤立峰值点'
                f'（未形成 ≥2 点的连续段），见下方明细。</p>')
    elif iso:
        parts.append(
            f'<p style="font-size:12px;color:#888;">连续峰值段（≥2 个采样点）：'
            f'本次没有；{iso} 个峰值点均为孤立采样，见下方明细。</p>')

    if a.get("peak_samples"):
        rows = "".join(
            f"<tr><td>#{r['index']}</td><td>{r['value']:.2f}%</td>"
            f"<td>{r['contributors'] or '-'}</td></tr>"
            for r in a["peak_samples"])
        parts.append(f"""
        <p style="font-size:12px;color:#888;">峰值采样点明细（最高的 5 个点；贡献线程按 ≥5% 列出）：</p>
        <table class="compare">
            <thead><tr><th>采样点</th><th>CPU (%)</th><th>主要贡献线程</th></tr></thead>
            <tbody>{rows}</tbody>
        </table>""")

    c = a.get("concentration") or {}
    if c:
        parts.append(f"""
        <p style="font-size:12px;color:#888;">峰值集中度（{c['samples']} 个峰值点）：
            Top1 线程均值 <b>{c['top1']:.1f}%</b> · Top5 线程均值 <b>{c['top5']:.1f}%</b>
            · 判定：<b>{c['mode']}</b></p>""")
    return "".join(parts)


def _render_thread_analysis(thread_samples, cpu_avg: float = 0.0) -> str:
    """线程分析章节（数量统计 + 最忙线程排行）。"""
    ta = analyze_threads(thread_samples)
    if not ta.get("count"):
        return ""
    t = ta["totals"]
    note = ""
    if cpu_avg > 0 and t["cpu_avg"] > 0:
        diff = abs(t["cpu_avg"] - cpu_avg) / cpu_avg * 100.0
        if diff > 20:
            note = (f"<br>※ 线程合计与进程 CPU 均值相差 {diff:.0f}%，"
                    f"多为两个探针的采样窗口不同步所致，趋势仍可参考。")

    rows = "".join(
        f"<tr><td>{r['name']}</td><td>{r['tid']}</td>"
        f"<td>{r['cpu_avg']:.2f}%</td><td>{r['cpu_max']:.2f}%</td>"
        f"<td>{r['share']:.1f}%</td></tr>"
        for r in ta["threads"])
    return f"""
        <h2>🧵 线程分析（所选进程）</h2>
        <p style="font-size:12px;color:#888;">※ 数据源 <code>/proc/&lt;pid&gt;/task/*/stat</code>；
           线程 CPU 为多核累计，单线程最高 100%。全量线程明细见产物目录的
           <code>threads_*.csv</code>，这里只列每个采样点最忙的 15 个线程；
           均值按整个会话平均（某次未进入 Top 15 的线程按 0 计），因此是下限，
           占比合计可能小于 100%。线程名由内核截断到 15 个字符。{note}</p>
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
        <p style="font-size:12px;color:#888;">最忙的线程（按均值排序）：</p>
        <table class="compare">
            <thead><tr><th>线程</th><th>TID</th><th>CPU 均值 (%)</th>
                <th>CPU 峰值 (%)</th><th>占线程合计</th></tr></thead>
            <tbody>{rows}</tbody>
        </table>
        <p style="font-size:12px;color:#888;">共 {ta['count']} 个采样点</p>
        """


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
        else:
            axis_unit = "秒"
            axis_labels = timestamps

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
            detail_rows = [('Total', mem_stat)]
            detail_rows += [(cat, breakdown[cat]) for cat, _lbl, _color in MEM_CATEGORIES
                            if cat in breakdown]
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
        <table class="compare compare4">
            <thead><tr><th>分类</th><th>均值 (MB)</th><th>峰值 (MB)</th><th>最低 (MB)</th></tr></thead>
            <tbody>{detail_tr}</tbody>
        </table>
        """

        # 系统内存（整机探针）：与「内存」卡口径不同，单独成节并标注清楚
        def _avg(v):
            return (sum(v) / len(v)) if v else 0.0

        # ---------- CPU 深度分析（P1）+ 线程分析（P2/P3） ----------
        cpu_analysis_html = ""
        thread_analysis_html = ""
        if 'cpu' in metrics and samples:
            cpu_points = [
                (s.timestamp, float(s.cpu_percent))
                for s in samples
                if not any(str(e).startswith('cpu:') for e in (getattr(s, 'errors', None) or ()))
            ]
            thread_samples = list(getattr(session, 'thread_samples', None) or [])
            analysis = analyze_cpu(cpu_points, session.sample_interval, thread_samples)
            cpu_analysis_html = _render_cpu_analysis(analysis)
            thread_analysis_html = _render_thread_analysis(
                thread_samples, (stats.get('cpu') or {}).get('avg', 0.0))

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
        _sc = [x for x in (getattr(session, "sys_cpu_samples", None) or [])
               if "cpu_pct" in x]
        if _sc:
            cpu_v = [float(x.get("cpu_pct") or 0) for x in _sc]
            user_v = [float(x.get("user_pct") or 0) for x in _sc]
            sys_v = [float(x.get("sys_pct") or 0) for x in _sc]
            cores = _sc[-1].get("cores") or 0
            # 峰值多核累计 = 各点「占用率 × 核数」的最大值（total_pct 是容量，不是占用）
            total_peak = max(
                (float(x.get("cpu_pct") or 0) * (x.get("cores") or cores or 0) for x in _sc),
                default=0.0)
            degraded = any(x.get("degraded") for x in _sc)
            core_txt = f"{cores} 核" if cores else "核数未知"
            deg_note = ("<br>※ 该设备的 <code>top</code> 不支持批量模式，已降级为 "
                        "<code>/proc/stat</code> 差分，因此没有用户 / 内核明细。") if degraded else ""
            sys_cpu_html = f"""
        <h2>⚙️ 整机 CPU</h2>
        <p style="font-size:12px;color:#888;">※ 本项为<b>整机口径</b>（{core_txt}，占用率已归一化到 0~100%，
           峰值多核累计 {total_peak:.0f}%），包含设备上所有进程，与上方「CPU」卡（所选进程）口径不同。{deg_note}</p>
        <table class="compare compare4">
            <thead><tr><th>项目</th><th>均值</th><th>峰值</th><th>最低</th></tr></thead>
            <tbody>
                <tr><td>整机占用 (%)</td><td>{_avg(cpu_v):.1f}</td>
                    <td>{max(cpu_v):.1f}</td><td>{min(cpu_v):.1f}</td></tr>
                <tr><td>用户态 (%)</td><td>{_avg(user_v):.1f}</td>
                    <td>{max(user_v):.1f}</td><td>{min(user_v):.1f}</td></tr>
                <tr><td>内核态 (%)</td><td>{_avg(sys_v):.1f}</td>
                    <td>{max(sys_v):.1f}</td><td>{min(sys_v):.1f}</td></tr>
            </tbody>
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
            return f"""
        <div class="chart-wrap">
            <canvas id="{canvas_id}"></canvas>
        </div>
        <div class="chart-toolbar">
            <button class="zoom-reset" data-chart="{canvas_id}">↺ 重置缩放</button>
        </div>"""

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
        charts_js = "\n\n".join(chart_scripts)

        # 统计口径：让读者一眼看清每个数字统计的是"谁"
        _scopes = getattr(session, "scopes", None) or {}
        _scope_parts = []
        for _k in ("cpu", "mem", "fps", "traffic"):
            if _k in metrics and _scopes.get(_k):
                _scope_parts.append(
                    f"{metric_label(_k)} = {SCOPE_LABELS.get(_scopes[_k], _scopes[_k])}")
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
        .chart-toolbar {{
            text-align: right;
            margin: 0 0 12px;
        }}
        .zoom-reset {{
            padding: 3px 12px;
            font-size: 12px;
            color: #666;
            background: #fff;
            border: 1px solid #d0d0d0;
            border-radius: 6px;
            cursor: pointer;
        }}
        .zoom-reset:hover {{
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

        <h2>📊 关键指标</h2>
        <div class="summary">
            {summary_cards_html}
        </div>

        {cpu_analysis_html}

        {sys_cpu_html}

        {thread_analysis_html}

        {mem_detail_html}

        {sys_mem_html}

        {netdev_html}

        {iotop_html}

        {events_html}

        {baseline_html}

        {charts_section}

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
        const memData = [{','.join(mem_series)}];
        const javaData = [{','.join(mem_cat_series['Java'])}];
        const nativeData = [{','.join(mem_cat_series['Native'])}];
        const graphicsData = [{','.join(mem_cat_series['Graphics'])}];
        const stackData = [{','.join(mem_cat_series['Stack'])}];
        const codeData = [{','.join(mem_cat_series['Code'])}];
        const othersData = [{','.join(mem_cat_series['Others'])}];
        const fpsData = [{','.join(fps_series)}];
        const rxRateData = [{','.join(rx_rate_series)}];
        const txRateData = [{','.join(tx_rate_series)}];

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

        {charts_js}

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