# utils/perf_report.py
"""性能检测 HTML 报告生成器"""
import os
import base64
from datetime import datetime
from models.perf_model import PerfSession


class PerfReportGenerator:
    """生成性能检测的 HTML 报告"""

    @staticmethod
    def generate(session: PerfSession, file_path: str) -> str:
        stats = session.get_stats()
        samples = session.samples

        # 准备曲线数据（用 JS 数组内联）
        timestamps = []
        cpu_series = []
        mem_series = []
        fps_series = []
        rx_series = []
        tx_series = []

        if samples:
            t0 = samples[0].timestamp
            for s in samples:
                timestamps.append(f"{s.timestamp - t0:.1f}")
                cpu_series.append(f"{s.cpu_percent:.2f}")
                mem_series.append(f"{s.mem_pss_mb:.2f}")
                fps_series.append(str(s.fps))
                rx_series.append(f"{s.rx_bytes / 1024 / 1024:.2f}")
                tx_series.append(f"{s.tx_bytes / 1024 / 1024:.2f}")

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
            <div class="card">
                <div class="value">{stats.get('cpu', {}).get('max', 0):.1f}%</div>
                <div class="label">CPU 峰值</div>
            </div>
            <div class="card">
                <div class="value">{stats.get('cpu', {}).get('avg', 0):.1f}%</div>
                <div class="label">CPU 均值</div>
            </div>
            <div class="card">
                <div class="value">{stats.get('mem', {}).get('max', 0):.0f}MB</div>
                <div class="label">内存峰值</div>
            </div>
            <div class="card">
                <div class="value">{stats.get('fps', {}).get('avg', 0):.0f}</div>
                <div class="label">FPS 均值</div>
            </div>
        </div>

        <h2>📈 实时曲线</h2>
        <div class="chart-wrap">
            <canvas id="cpuChart"></canvas>
        </div>
        <div class="chart-wrap">
            <canvas id="memChart"></canvas>
        </div>
        <div class="chart-wrap">
            <canvas id="fpsChart"></canvas>
        </div>

        {alerts_html}

        <div class="footer">
            <p>报告由虫师自动生成 · © 2026 虫师团队</p>
        </div>
    </div>

    <script>
        const timestamps = [{','.join(timestamps)}];
        const cpuData = [{','.join(cpu_series)}];
        const memData = [{','.join(mem_series)}];
        const fpsData = [{','.join(fps_series)}];

        const commonOptions = {{
            responsive: true,
            maintainAspectRatio: false,
            plugins: {{ legend: {{ display: false }} }},
            scales: {{
                x: {{ display: true, title: {{ display: true, text: '时间 (秒)' }} }},
                y: {{ beginAtZero: true }}
            }},
            elements: {{ point: {{ radius: 0 }} }}
        }};

        new Chart(document.getElementById('cpuChart'), {{
            type: 'line',
            data: {{
                labels: timestamps,
                datasets: [{{
                    label: 'CPU (%)',
                    data: cpuData,
                    borderColor: '#3498db',
                    backgroundColor: 'rgba(52,152,219,0.1)',
                    borderWidth: 2,
                    fill: true,
                    tension: 0.3
                }}]
            }},
            options: commonOptions
        }});

        new Chart(document.getElementById('memChart'), {{
            type: 'line',
            data: {{
                labels: timestamps,
                datasets: [{{
                    label: '内存 (MB)',
                    data: memData,
                    borderColor: '#27ae60',
                    backgroundColor: 'rgba(39,174,96,0.1)',
                    borderWidth: 2,
                    fill: true,
                    tension: 0.3
                }}]
            }},
            options: commonOptions
        }});

        new Chart(document.getElementById('fpsChart'), {{
            type: 'line',
            data: {{
                labels: timestamps,
                datasets: [{{
                    label: 'FPS',
                    data: fpsData,
                    borderColor: '#f39c12',
                    backgroundColor: 'rgba(243,156,18,0.1)',
                    borderWidth: 2,
                    fill: true,
                    tension: 0.3
                }}]
            }},
            options: commonOptions
        }});
    </script>
</body>
</html>"""

        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(html)
        return file_path