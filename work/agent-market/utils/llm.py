"""LLM 调用模块

对话型智能体使用 LLM 生成随机测试问题
"""

import os
import json
import asyncio
from config import LLM_CONFIG


async def generate_question(agent_name: str, agent_type: str) -> str:
    """
    生成测试问题

    先尝试通过 LLM API 生成，失败则使用预定义问题

    Args:
        agent_name: 智能体名称
        agent_type: 智能体类型

    Returns:
        str: 测试问题
    """
    # 优先使用预定义问题（更快更可靠）
    predefined = _get_predefined_question(agent_name)
    if predefined:
        return predefined

    # 尝试通过 LLM 生成
    return await _generate_via_llm(agent_name, agent_type)


def _get_predefined_question(agent_name: str) -> str:
    """获取预定义的测试问题"""
    from random import choice

    questions = {
        "客服": [
            "你好，请问有什么可以帮助你的？",
            "我想了解一下你们的业务",
            "你们的服务时间是什么？",
            "如何办理业务？",
        ],
        "问答": [
            "请介绍一下你自己",
            "你能做什么？",
            "请给我一个使用示例",
            "你有什么特别的功能？",
        ],
        "签章": [
            "如何签署电子合同？",
            "请演示一下签章流程",
            "签章需要哪些材料？",
            "电子签章有法律效力吗？",
        ],
        "EB": [
            "EB 是什么？",
            "如何创建 EB？",
            "EB 的基本功能有哪些？",
            "怎么使用 EB 工作流？",
        ],
    }

    for keyword, q_list in questions.items():
        if keyword in agent_name:
            return choice(q_list)

    return "你好，请介绍一下你自己"


async def _generate_via_llm(agent_name: str, agent_type: str) -> str:
    """通过 LLM API 生成测试问题"""
    api_key = LLM_CONFIG.get("api_key", "")
    if not api_key:
        return f"你好，请介绍一下你自己"

    try:
        import httpx

        response = await httpx.AsyncClient().post(
            f"{LLM_CONFIG['base_url']}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": LLM_CONFIG["model"],
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "你是一个测试问题生成器。"
                            "根据智能体名称和类型，生成一个简短的测试问题。"
                            "问题要自然，像真实用户会问的。"
                            "只返回问题文本，不要解释。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"智能体名称：{agent_name}\n"
                            f"智能体类型：{agent_type}\n"
                            f"生成一个测试问题："
                        ),
                    },
                ],
                "temperature": 0.8,
                "max_tokens": 50,
            },
            timeout=10.0,
        )

        if response.status_code == 200:
            result = response.json()
            return result["choices"][0]["message"]["content"].strip()
        else:
            return "你好，请介绍一下你自己"

    except Exception:
        return "你好，请介绍一下你自己"


async def test_chat_with_llm(page, agent_name: str) -> dict:
    """
    使用 LLM 生成的问题测试对话

    Args:
        page: Playwright Page 对象
        agent_name: 智能体名称

    Returns:
        dict: 测试结果
    """
    result = {"success": False, "question": "", "response": None, "error": None}

    try:
        # 生成问题
        question = await generate_question(agent_name, "对话型")
        result["question"] = question
        print(f"    💬 测试问题: {question}")

        # 查找聊天输入框
        input_selectors = [
            "textarea",
            "input[type='text']",
            "[placeholder*='请输入']",
            "[placeholder*='发送']",
            '[class*="chat-input"]',
            '[class*="message-input"]',
        ]

        chat_input = None
        for selector in input_selectors:
            try:
                elem = page.locator(selector).first
                if await elem.count() > 0:
                    chat_input = elem
                    break
            except:
                continue

        if not chat_input:
            result["error"] = "未找到聊天输入框"
            return result

        # 输入问题并发送
        await chat_input.fill(question)

        # 查找发送按钮
        send_selectors = [
            "button[type='submit']",
            "[class*='send-btn']",
            "[class*='send-button']",
            '[class*="chat-send"]',
        ]

        send_btn = None
        for selector in send_selectors:
            try:
                btn = page.locator(selector).first
                if await btn.count() > 0:
                    send_btn = btn
                    break
            except:
                continue

        if not send_btn:
            await chat_input.press("Enter")
        else:
            await send_btn.click()

        # 等待回复
        await asyncio.sleep(10)
        await page.wait_for_load_state("networkidle", timeout=30000)

        # 获取回复内容
        reply_selectors = [
            '[class*="chat-message"]',
            '[class*="message-content"]',
            '[class*="ai-response"]',
        ]

        reply_text = None
        for selector in reply_selectors:
            try:
                elem = page.locator(selector).last
                if await elem.count() > 0:
                    reply_text = await elem.text_content()
                    if reply_text:
                        break
            except:
                continue

        if not reply_text:
            reply_text = (await page.evaluate("document.body.textContent")).strip()[:500]

        if reply_text:
            result["success"] = True
            result["response"] = reply_text
        else:
            result["error"] = "智能体未返回有效回复"

    except asyncio.TimeoutError:
        result["error"] = "对话测试超时（30秒）"
    except Exception as e:
        result["error"] = f"对话测试异常: {str(e)}"

    return result
