# services/anr_parser.py
"""ANR 报告解析

从 ANR trace / bugreport 文本里提取主题、进程、CPU 占用和主线程堆栈。
主窗口底部日志区（内联展示）和 ANR 分析对话框共用这里的实现。
"""
import re


def parse_anr(content: str) -> str:
    """把 ANR 原文解析成便于阅读的摘要"""
    result = []

    subject = re.search(r'Subject:\s*(.*)', content)
    if subject:
        result.append(f"【ANR 主题】\n{subject.group(1)}\n")

    pid_match = re.search(r'PID:\s*(\d+)', content)
    if pid_match:
        result.append(f"PID: {pid_match.group(1)}")

    proc_match = re.search(r'Process:\s*(.*)', content)
    if proc_match:
        result.append(f"进程: {proc_match.group(1)}")

    cpu_section = re.search(
        r'CPU usage from.*?\n(.*?)(?=\n\n|\Z)', content, re.DOTALL
    )
    if cpu_section:
        result.append("\n【CPU 使用情况】")
        result.append(cpu_section.group(1).strip())

    main_thread = re.search(
        r'"main" prio=.*?\n(.*?)(?=\n\n|\Z)', content, re.DOTALL
    )
    if main_thread:
        result.append("\n【主线程堆栈】")
        result.append(main_thread.group(1).strip())
    else:
        stack_start = content.find("DALVIK THREADS")
        if stack_start != -1:
            sub = content[stack_start:]
            m = re.search(
                r'"main" .*?\n(.*?)(?=\n\n|\Z)', sub, re.DOTALL
            )
            if m:
                result.append("\n【主线程堆栈】")
                result.append(m.group(1).strip())

    if len(result) <= 2:
        result = [
            "【ANR 分析结果】",
            "未检测到 ANR 相关信息，可能当前文件不包含 ANR 记录。",
        ]
    return "\n".join(result)
