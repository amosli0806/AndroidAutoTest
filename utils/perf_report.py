# utils/perf_report.py
"""性能检测 HTML 报告生成器"""
import os
import base64
from datetime import datetime
from models.perf_model import PerfSession, compare_stats, MEM_CATEGORIES


# 各指标的 Chart.js 绘图脚本（普通字符串，非 f-string，花括号无需转义）
CHART_JS_CPU = """new Chart(document.getElementById('cpuChart'), {
    type: 'line',
    data: {
        labels: timestamps,
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
        plugins: { legend: { display: false }, title: { display: true, text: 'CPU 使用率 (%)' } },
        scales: {
            x: { display: true, title: { display: true, text: '时间 (秒)' } },
            y: { beginAtZero: true }
        },
        elements: { point: { radius: 0 } }
    }
});"""

CHART_JS_MEM = """new Chart(document.getElementById('memChart'), {
    type: 'line',
    data: {
        labels: timestamps,
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
        plugins: { legend: { display: false }, title: { display: true, text: '内存占用 (MB)' } },
        scales: {
            x: { display: true, title: { display: true, text: '时间 (秒)' } },
            y: { beginAtZero: true }
        },
        elements: { point: { radius: 0 } }
    }
});"""

CHART_JS_MEM_BREAKDOWN = """new Chart(document.getElementById('memChart'), {
    type: 'line',
    data: {
        labels: timestamps,
        datasets: [
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
        plugins: { legend: { display: true, position: 'top' }, title: { display: true, text: '内存占用 (分类拆解, MB)' } },
        scales: {
            x: { display: true, title: { display: true, text: '时间 (秒)' } },
            y: { beginAtZero: true, title: { display: true, text: 'MB' } }
        },
        elements: { point: { radius: 0 } }
    }
});"""

CHART_JS_FPS = """new Chart(document.getElementById('fpsChart'), {
    type: 'line',
    data: {
        labels: timestamps,
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
        plugins: { legend: { display: false }, title: { display: true, text: 'FPS 帧率' } },
        scales: {
            x: { display: true, title: { display: true, text: '时间 (秒)' } },
            y: { beginAtZero: true }
        },
        elements: { point: { radius: 0 } }
    }
});"""

CHART_JS_TRAFFIC = """new Chart(document.getElementById('trafficChart'), {
    type: 'line',
    data: {
        labels: timestamps,
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
        plugins: { legend: { display: true }, title: { display: true, text: '流量速率 (KB/s)' } },
        scales: {
            x: { display: true, title: { display: true, text: '时间 (秒)' } },
            y: { beginAtZero: true }
        },
        elements: { point: { radius: 0 } }
    }
});"""


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

        # 汇总信息
        app_pkg = session.app_package
        device = session.device_serial
        start = session.start_time
        end = session.end_time
        metrics = session.metrics
        duration = "0s"
        if samples:
            duration = f"{samples[-1].timestamp - samples[0].timestamp:.1f}s"

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
            summary_cards_html += f"""
            <div class="card">
                <div class="value">{s.get('avg', 0):.1f}</div>
                <div class="label">FPS 均值</div>
                <div class="sub">最高 {s.get('max', 0):.0f} · 最低 {s.get('min', 0):.0f}</div>
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
        if baseline is not None:
            rows = compare_stats(baseline.metrics, stats)
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

        # 曲线区域与图表脚本：只渲染本次勾选采集的指标
        charts_section = ""
        chart_scripts = []
        if 'cpu' in metrics:
            charts_section += """
        <div class="chart-wrap">
            <canvas id="cpuChart"></canvas>
        </div>"""
            chart_scripts.append(CHART_JS_CPU)
        if 'mem' in metrics:
            charts_section += """
        <div class="chart-wrap">
            <canvas id="memChart"></canvas>
        </div>"""
            # 采集到了分类拆解时画 Android Studio 风格堆叠面积图，否则保持单线 PSS
            chart_scripts.append(
                CHART_JS_MEM_BREAKDOWN if has_mem_breakdown else CHART_JS_MEM
            )
        if 'fps' in metrics:
            charts_section += """
        <div class="chart-wrap">
            <canvas id="fpsChart"></canvas>
        </div>"""
            chart_scripts.append(CHART_JS_FPS)
        if has_traffic:
            charts_section += """
        <div class="chart-wrap">
            <canvas id="trafficChart"></canvas>
        </div>"""
            chart_scripts.append(CHART_JS_TRAFFIC)
        if charts_section:
            charts_section = '<h2>📈 实时曲线</h2>' + charts_section
        charts_js = "\n\n".join(chart_scripts)

        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>性能报告 - {session.name}</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
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
            margin: 12px 0;
            height: 280px;
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
                <td>{'、'.join(metrics)}</td>
            </tr>
            {scenario_info}
        </table>

        <h2>📊 关键指标</h2>
        <div class="summary">
            {summary_cards_html}
        </div>

        {baseline_html}

        {charts_section}

        {alerts_html}

        <div class="footer">
            <p>报告由虫师自动生成 · © 2026 虫师团队</p>
        </div>
    </div>

    <script>
        const timestamps = [{','.join(timestamps)}];
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

        {charts_js}
    </script>
</body>
</html>"""

        out_dir = os.path.dirname(file_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(html)
        return file_path