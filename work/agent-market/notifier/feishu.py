"""
飞书通知模块

发送巡检异常报告到飞书群
"""

import json
import aiohttp
from config import FEISHU_WEBHOOK


async def send_feishu(report: dict):
    """
    发送飞书异常报告

    Args:
        report: generate_report 返回的报告字典
    """
    if not FEISHU_WEBHOOK:
        print("  ⚠️ 未配置 FEISHU_WEBHOOK，跳过飞书通知")
        return

    try:
        summary = report.get("summary", {})
        failed_count = summary.get("failed", 0)
        unreachable_count = summary.get("unreachable", 0)
        total = summary.get("total", 0)

        # 构建消息内容
        text_parts = [
            f"🔍 **Agent Market 健康巡检报告**\n",
            f"**巡检时间**: {report.get('timestamp', '未知')}\n",
            f"**智能体总数**: {total}\n",
            f"**正常运行**: {summary.get('passed', 0)}\n",
            f"**异常**: {failed_count}\n",
            f"**访问失败**: {unreachable_count}\n",
        ]

        if failed_count > 0:
            text_parts.append("\n**异常详情:**\n")
            for agent in report.get("agents", []):
                if agent.get("status") in ("error", "chat_error", "unreachable"):
                    text_parts.append(
                        f"- `{agent['agent_id']}` {agent['name']}: {agent.get('error', '未知错误')}\n"
                    )

        content = "".join(text_parts)

        payload = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {
                        "tag": "plain_text",
                        "content": f"🔍 巡检报告: {failed_count} 个异常 / {total} 个智能体",
                    },
                    "template": "red" if failed_count > 5 else ("orange" if failed_count > 0 else "green"),
                },
                "elements": [
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": content,
                        },
                    },
                    {
                        "tag": "action",
                        "actions": [
                            {
                                "tag": "button",
                                "text": {
                                    "tag": "plain_text",
                                    "content": "查看详细报告",
                                },
                                "type": "primary",
                                "url": report.get("path", ""),
                            }
                        ],
                    },
                ],
            },
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                FEISHU_WEBHOOK,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                result = await resp.json()
                if result.get("code") == 0:
                    print("  ✅ 飞书通知发送成功")
                else:
                    print(f"  ⚠️ 飞书通知失败: {result}")

    except Exception as e:
        print(f"  ⚠️ 飞书通知异常: {e}")
