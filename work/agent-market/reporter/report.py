"""
生成巡检报告

输出格式: Markdown + HTML + JSON
仅列出有问题的智能体 + 摘要
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
        dict: 报告信息 {path, json_path, md_path}
    """
    screenshots_dir = screenshots_dir or Path("screenshots")
    reports_dir = reports_dir or Path("reports")
    reports_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # 生成 JSON 报告（完整数据）
    json_path = reports_dir / f"health_report_{timestamp}.json"
    json_report = {
        "report_title": "Agent Market 健康巡检报告",
        "generated_at": datetime.now().isoformat(),
        "summary": results.get("summary", {}),
        "agents": results.get("agents", []),
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_report, f, ensure_ascii=False, indent=2)

    # 生成 Markdown 报告（仅列出有问题的智能体）
    md_path = reports_dir / f"health_report_{timestamp}.md"
    md_content = _build_markdown_report(results)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    # 生成 HTML 报告（完整数据）
    report_path = reports_dir / f"health_report_{timestamp}.html"
    html_content = _build_html_report(results)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    return {
        "path": str(report_path),
        "json_path": str(json_path),
        "md_path": str(md_path),
        "timestamp": timestamp,
        "summary": results.get("summary", {}),
    }


def _build_markdown_report(results: dict) -> str:
    """构建 Markdown 报告（仅列出有问题的智能体）"""
    summary = results.get("summary", {})
    agents = results.get("agents", [])
    timestamp = results.get("timestamp", "")

    lines = []
    lines.append("# Agent Market 健康巡检报告")
    lines.append("")
    lines.append(f"**巡检时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} (Asia/Shanghai)")
    lines.append("")

    # ── 摘要 ──
    total = summary.get("total", 0)
    passed = summary.get("passed", 0)
    failed = summary.get("failed", 0)
    unreachable = summary.get("unreachable", 0)
    chat_tested = summary.get("chat_tested", 0)
    failed_count = failed + unreachable

    lines.append("## 📊 巡检摘要")
    lines.append("")
    lines.append(f"| 指标 | 数值 |")
    lines.append(f"|------|------|")
    lines.append(f"| 智能体总数 | {total} |")
    lines.append(f"| ✅ 正常 | {passed} |")
    lines.append(f"| ❌ 异常 | {failed_count} |")
    lines.append(f"|  其中：对话异常 / 访问失败 | {failed} / {unreachable} |")
    lines.append(f"| 💬 已测试对话 | {chat_tested} |")
    lines.append("")

    # ── 仅列出有问题的智能体 ──
    problem_agents = []
    for a in agents:
        status = a.get("status", "unknown")
        if status in ("error", "unreachable", "chat_error", "chat_failed"):
            problem_agents.append(a)

    if not problem_agents:
        lines.append("## 🎉 结果")
        lines.append("")
        lines.append("✅ **全部智能体运行正常，无异常。**")
        lines.append("")
    else:
        lines.append(f"## ⚠️ 有问题的智能体（共 {len(problem_agents)} 个）")
        lines.append("")

        for agent in problem_agents:
            status = agent.get("status", "unknown")
            status_label = {
                "error": "🔴 异常",
                "unreachable": "🟡 无法访问",
                "chat_error": "🟠 对话异常",
                "chat_failed": "🟠 回复质量不合格",
            }.get(status, "⚪ 未知")

            error_msg = agent.get("error", "未知错误")
            name = agent.get("name", "未知")
            aid = agent.get("agent_id", "-")
            agent_type = agent.get("type", "")

            lines.append(f"### {status_label} — {name} (ID: {aid})")
            lines.append("")
            if agent_type:
                lines.append(f"- **类型**: {agent_type}")
            lines.append(f"- **问题**: {error_msg}")

            # 如果有 LLM 评估详情
            chat_result = agent.get("chat_result", {})
            if chat_result:
                question = chat_result.get("question", "")
                if question:
                    lines.append(f"- **测试问题**: {question}")

                evaluation = chat_result.get("evaluation")
                if evaluation:
                    score = evaluation.get("score", 0)
                    passed = evaluation.get("passed", False)
                    passed_str = "✅ 通过" if passed else "❌ 未通过"
                    lines.append(f"- **评估结果**: {passed_str} (评分: {score}/10)")
                    issues = evaluation.get("issues", [])
                    if issues:
                        lines.append(f"- **具体问题**: {'; '.join(issues)}")

                # 测试了多个问题
                questions_tested = chat_result.get("questions_tested", [])
                if len(questions_tested) > 1:
                    lines.append(f"- **测试了 {len(questions_tested)} 个问题**:")
                    for i, qt in enumerate(questions_tested, 1):
                        q = qt.get("question", "?")
                        resp = qt.get("response", "")
                        if resp:
                            resp_preview = resp[:100].replace("\n", " ")
                            lines.append(f"  {i}. 问: {q} → 答: {resp_preview}...")
                        else:
                            lines.append(f"  {i}. 问: {q} → 无回复 ({qt.get('error', '')})")

            if agent.get("page_title"):
                lines.append(f"- **页面标题**: {agent.get('page_title')}")

            lines.append("")

    return "\n".join(lines)


def _build_html_report(results: dict) -> str:
    """构建 HTML 报告（完整数据，用于归档）"""
    summary = results.get("summary", {})
    agents = results.get("agents", [])
    timestamp = results.get("timestamp", "")

    rows = ""
    for agent in agents:
        status_map = {
            "ok": "🟢 正常",
            "error": "🔴 异常",
            "unreachable": "🟡 无法访问",
            "chat_error": "🟠 对话异常",
            "chat_failed": "🟠 回复质量差",
            "unknown": "⚪ 未知",
        }
        status = status_map.get(agent.get("status", "unknown"), "⚪ 未知")
        error = agent.get("error", "-")
        rows += f"""<tr>
            <td>{agent.get('agent_id', '-')}</td>
            <td>{agent.get('name', '-')}</td>
            <td>{agent.get('type', '-')}</td>
            <td>{status}</td>
            <td>{error}</td>
            <td>{agent.get('page_title', '-')}</td>
        </tr>"""

    # 对话测试结果
    chat_rows = ""
    for agent in agents:
        if agent.get("chat_tested") and agent.get("chat_result"):
            cr = agent["chat_result"]
            status_icon = "✅" if cr.get("success") else "❌"
            eval_info = ""
            if cr.get("evaluation"):
                ev = cr["evaluation"]
                passed_str = "通过" if ev.get("passed") else "未通过"
                score = ev.get("score", 0)
                eval_info = f" ({passed_str}, {score}/10)"
            response = (cr.get("response", "") or "")[:200]
            chat_rows += f"""<tr>
                <td>{agent.get('agent_id', '-')}</td>
                <td>{agent.get('name', '-')}</td>
                <td>{status_icon}{eval_info}</td>
                <td>{cr.get('question', '-')}</td>
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
                <div>智能体总数</div>
            </div>
            <div class="stat-card passed">
                <div class="number">{summary.get('passed', 0)}</div>
                <div>正常</div>
            </div>
            <div class="stat-card failed">
                <div class="number">{summary.get('failed', 0)}</div>
                <div>异常</div>
            </div>
            <div class="stat-card unreachable">
                <div class="number">{summary.get('unreachable', 0)}</div>
                <div>无法访问</div>
            </div>
        </div>

        <h2>📋 智能体状态详情</h2>
        <table>
            <thead>
                <tr><th>ID</th><th>名称</th><th>类型</th><th>状态</th><th>错误信息</th><th>页面标题</th></tr>
            </thead>
            <tbody>{rows}</tbody>
        </table>

        <h2>💬 对话测试结果</h2>
        <table>
            <thead>
                <tr><th>ID</th><th>名称</th><th>结果</th><th>测试问题</th><th>智能体回复</th></tr>
            </thead>
            <tbody>{chat_rows if chat_rows else '<tr><td colspan="5" style="text-align:center;color:#999;">无对话测试结果</td></tr>'}</tbody>
        </table>
    </div>
</body>
</html>"""
