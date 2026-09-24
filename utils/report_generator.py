# utils/report_generator.py
import os
import base64
from datetime import datetime
from models.execution_model import ExecutionModel

class ReportGenerator:
    """生成 HTML 格式的执行报告，支持嵌入截图"""

    @staticmethod
    def _encode_image(image_path: str) -> str:
        """读取图片文件并转换为 Base64 编码"""
        if not os.path.exists(image_path):
            return None
        try:
            with open(image_path, 'rb') as f:
                image_data = f.read()
            return base64.b64encode(image_data).decode('utf-8')
        except Exception as e:
            print(f"图片编码失败 {image_path}: {e}")
            return None

    @staticmethod
    def generate(exec_model: ExecutionModel, file_path: str) -> str:
        """
        生成 HTML 报告并保存到指定路径
        :param exec_model: 包含执行结果数据的模型
        :param file_path: 保存的完整文件路径（含 .html）
        :return: 文件路径
        """
        stats = exec_model.get_stats()
        logs = exec_model.logs

        # 开始构建 HTML
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <title>虫师 - 测试报告</title>
            <style>
                body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 20px; background: #f5f6fa; }}
                .container {{ max-width: 1200px; margin: 0 auto; background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
                h1 {{ color: #1976d2; border-bottom: 2px solid #1976d2; padding-bottom: 10px; }}
                .summary {{ display: flex; gap: 20px; margin-bottom: 20px; flex-wrap: wrap; }}
                .card {{ background: #f8f9fa; padding: 15px 25px; border-radius: 8px; min-width: 120px; text-align: center; flex: 1; }}
                .card .number {{ font-size: 28px; font-weight: bold; }}
                .card .label {{ color: #666; margin-top: 5px; }}
                .card.pass .number {{ color: #27ae60; }}
                .card.fail .number {{ color: #e74c3c; }}
                .card.rate .number {{ color: #1976d2; }}
                .card.skip .number {{ color: #f39c12; }}
                .log-entry {{ padding: 2px 0; font-family: monospace; font-size: 13px; }}
                .log-error {{ color: #e74c3c; }}
                .log-success {{ color: #27ae60; }}
                .log-warning {{ color: #f39c12; }}
                .log-info {{ color: #333; }}
                .screenshot-container {{ margin: 6px 0 10px 20px; }}
                .screenshot-container img {{ max-width: 100%; border: 1px solid #ddd; border-radius: 4px; }}
                .footer {{ margin-top: 30px; text-align: center; font-size: 12px; color: #999; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>🐞 虫师 - 测试执行报告</h1>
                <p>生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>

                <div class="summary">
                    <div class="card pass">
                        <div class="number">{stats[0]}</div>
                        <div class="label">✅ 通过</div>
                    </div>
                    <div class="card fail">
                        <div class="number">{stats[1]}</div>
                        <div class="label">❌ 失败</div>
                    </div>
                    <div class="card rate">
                        <div class="number">{stats[2]}%</div>
                        <div class="label">📊 通过率</div>
                    </div>
                </div>

                <h2>📋 执行日志</h2>
                <div style="max-height: 800px; overflow-y: auto; background: #f8f9fa; padding: 10px; border-radius: 4px;">
        """

        # 遍历日志，提取截图信息
        for msg, typ in logs:
            # 确定日志样式
            if typ == 'error':
                cls = 'log-error'
            elif typ == 'success':
                cls = 'log-success'
            elif typ == 'warning':
                cls = 'log-warning'
            else:
                cls = 'log-info'

            safe_msg = msg.replace('<', '&lt;').replace('>', '&gt;')

            # 检查是否包含截图路径（仅对错误日志）
            screenshot_html = ''
            if typ == 'error' and '截图已保存：' in msg:
                # 提取截图路径
                start_idx = msg.find('截图已保存：') + len('截图已保存：')
                path = msg[start_idx:].strip()
                # 可能路径后有换行或空格，取到行尾
                if '\n' in path:
                    path = path.split('\n')[0]
                # 尝试编码图片
                img_data = ReportGenerator._encode_image(path)
                if img_data:
                    screenshot_html = f'''
                    <div class="screenshot-container">
                        <img src="data:image/png;base64,{img_data}" alt="断言失败截图" />
                    </div>
                    '''
                else:
                    # 如果图片加载失败，显示提示
                    screenshot_html = f'<div class="screenshot-container" style="color:#999;">⚠️ 截图文件不存在：{path}</div>'

            html += f'<div class="log-entry {cls}">{safe_msg}</div>'
            if screenshot_html:
                html += screenshot_html

        html += """
                </div>
                <div class="footer">
                    <p>报告由虫师自动生成 · 仅供内部参考</p>
                </div>
            </div>
        </body>
        </html>
        """

        # 写入文件
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(html)
        return file_path