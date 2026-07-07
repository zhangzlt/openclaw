"""
生成巡检报告

输出格式: HTML + JSON
"""

import json
from datetime import datetime
from pathlib import Path
from config import REPORTS_DIR


def generate_report(results: dict, screenshots_dir: Path = None, reports_dir: Path = None) -> dict:
    """
    生成巡检报告

    Args:
        results: Inspector 返回的巡检结果
        screenshots_dir: 截图目录
        reports_dir: 报告输出目录

    Returns:
        dict: 报告信息 {path, json_path}
    """
    screenshots_dir = screenshots_dir or Path("screenshots")
    reports_dir = reports_dir or Path("reports")
    reports_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = reports_dir / f"health_report_{timestamp}.html"
    json_path = reports_dir / f"health_report_{timestamp}.json"

    # 生成 JSON 报告
    json_report = _build_json_report(results)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_report, f, ensure_ascii=False, indent=2)

    # 生成 HTML 报告
    html_content = _build_html_report(results)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    return {
        "path": str(report_path),
        "json_path": str(json_path),
        "timestamp": timestamp,
        "summary": results.get("summary", {}),
    }


def _build_json_report(results: dict) -> dict:
    """构建 JSON 报告"""
    return {
        "report_title": "Agent Market 健康巡检报告",
        "generated_at": datetime.now().isoformat(),
        "summary": results.get("summary", {}),
        "agents": results.get("agents", []),
    }


def _build_html_report(results: dict) -> str:
    """构建 HTML 报告"""
    summary = results.get("summary", {})
    agents = results.get("agents", [])
    timestamp = results.get("timestamp", "")

    # 构建表格行
    rows = ""
    for agent in agents:
        status_map = {
            "ok": "🟢 正常",
            "error": "🔴 异常",
            "unreachable": "🟡 无法访问",
            "chat_error": "🟠 对话异常",
            "unknown": "⚪ 未知",
        }
        status = status_map.get(agent.get("status", "unknown"), "⚪ 未知")
        error = agent.get("error", "-")

        rows += f"""
        <tr>
            <td>{agent.get('agent_id', '-')}</td>
            <td>{agent.get('name', '-')}</td>
            <td>{agent.get('type', '-')}</td>
            <td>{status}</td>
            <td>{error}</td>
            <td>{agent.get('page_title', '-')}</td>
        </tr>"""

    # 构建对话测试行
    chat_rows = ""
    for agent in agents:
        if agent.get("chat_tested") and agent.get("chat_result"):
            chat_result = agent["chat_result"]
            chat_status = "✅" if chat_result.get("success") else "❌"
            response = (chat_result.get("response", "") or "")[:200]
            chat_rows += f"""
            <tr>
                <td>{agent.get('agent_id', '-')}</td>
                <td>{agent.get('name', '-')}</td>
                <td>{chat_status}</td>
                <td>{chat_result.get('message', '-')}</td>
                <td>{response}</td>
            </tr>"""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Agent Market 健康巡检报告</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 0; padding: 20px; background: #f5f5f5; }}
        .container {{ max-width: 1200px; margin: 0 auto; }}
        h1 {{ color: #1a1a1a; border-bottom: 2px solid #4CAF50; padding-bottom: 10px; }}
        h2 {{ color: #333; margin-top: 30px; }}
        .summary {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 15px; margin: 20px 0; }}
        .stat-card {{ background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); text-align: center; }}
        .stat-card .number {{ font-size: 32px; font-weight: bold; }}
        .stat-card .label {{ color: #666; font-size: 14px; margin-top: 5px; }}
        .total .number {{ color: #2196F3; }}
        .passed .number {{ color: #4CAF50; }}
        .failed .number {{ color: #f44336; }}
        .unreachable .number {{ color: #FF9800; }}
        table {{ width: 100%; border-collapse: collapse; background: white; margin: 15px 0; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid #eee; }}
        th {{ background: #f8f9fa; font-weight: 600; }}
        tr:hover {{ background: #f5f5f5; }}
        .timestamp {{ color: #999; font-size: 14px; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>🔍 Agent Market 健康巡检报告</h1>
        <p class="timestamp">生成时间: {timestamp}</p>

        <div class="summary">
            <div class="stat-card total">
                <div class="number">{summary.get('total', 0)}</div>
                <div class="label">智能体总数</div>
            </div>
            <div class="stat-card passed">
                <div class="number">{summary.get('passed', 0)}</div>
                <div class="label">正常</div>
            </div>
            <div class="stat-card failed">
                <div class="number">{summary.get('failed', 0)}</div>
                <div class="label">异常</div>
            </div>
            <div class="stat-card unreachable">
                <div class="number">{summary.get('unreachable', 0)}</div>
                <div class="label">无法访问</div>
            </div>
        </div>

        <h2>📋 智能体状态详情</h2>
        <table>
            <thead>
                <tr>
                    <th>ID</th>
                    <th>名称</th>
                    <th>类型</th>
                    <th>状态</th>
                    <th>错误信息</th>
                    <th>页面标题</th>
                </tr>
            </thead>
            <tbody>
                {rows}
            </tbody>
        </table>

        <h2>💬 对话测试结果</h2>
        <table>
            <thead>
                <tr>
                    <th>ID</th>
                    <th>名称</th>
                    <th>结果</th>
                    <th>测试问题</th>
                    <th>智能体回复</th>
                </tr>
            </thead>
            <tbody>
                {chat_rows if chat_rows else '<tr><td colspan="5" style="text-align:center;color:#999;">无对话测试结果</td></tr>'}
            </tbody>
        </table>
    </div>
</body>
</html>"""
