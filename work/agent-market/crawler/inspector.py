"""
智能体巡检器

逐个访问智能体，执行健康检查
"""

import asyncio
import json
import time
from datetime import datetime
from browser.playwright_setup import BrowserManager


class AgentInspector:
    """智能体巡检器"""

    def __init__(self, browser_mgr: BrowserManager, agents: list):
        self.browser_mgr = browser_mgr
        self.page = browser_mgr.page
        self.agents = agents

    async def inspect_all(self) -> dict:
        """
        巡检所有智能体

        Returns:
            dict: 巡检结果
        """
        results = {
            "agents": [],
            "summary": {},
            "timestamp": datetime.now().isoformat(),
        }

        # 并发控制
        semaphore = asyncio.Semaphore(3)
        tasks = [self._inspect_one(agent, semaphore) for agent in self.agents]
        agent_results = await asyncio.gather(*tasks, return_exceptions=True)

        for agent, result in zip(self.agents, agent_results):
            if isinstance(result, Exception):
                agent["status"] = "error"
                agent["error"] = str(result)
            else:
                agent.update(result)
            results["agents"].append(agent)

        # 统计摘要
        total = len(results["agents"])
        passed = sum(1 for a in results["agents"] if a.get("status") == "ok")
        failed = sum(1 for a in results["agents"] if a.get("status") == "error")
        unreachable = sum(1 for a in results["agents"] if a.get("status") == "unreachable")
        chat_tested = sum(1 for a in results["agents"] if a.get("chat_tested"))

        results["summary"] = {
            "total": total,
            "passed": passed,
            "failed": failed,
            "unreachable": unreachable,
            "chat_tested": chat_tested,
        }

        return results

    async def _inspect_one(self, agent: dict, semaphore) -> dict:
        """巡检单个智能体"""
        async with semaphore:
            agent_id = agent["agent_id"]
            print(f"  巡检中: [{agent_id}] {agent['name']}")

            try:
                # 步骤1: 点击卡片打开新标签页
                detail_url = agent.get("detail_url", "")
                if not detail_url:
                    agent["status"] = "unreachable"
                    agent["error"] = "无详情页 URL"
                    return agent

                # 使用新标签页拦截
                new_page_task = asyncio.create_task(
                    self.browser_mgr.context.expect_page(timeout=10000)
                )

                # 模拟点击"打开"按钮（触发 widget/track API）
                widget_url = f"https://agent.digitalchina.com/widget/open?agentId={agent_id}&detail={agent.get('detail_path', '')}"
                await self.page.goto(widget_url, wait_until="domcontentloaded", timeout=10000)

                try:
                    new_page = await asyncio.wait_for(new_page_task, timeout=5.0)
                except asyncio.TimeoutError:
                    new_page = None

                # 检查目标页面
                if new_page:
                    target_page = new_page
                    agent["chat_tested"] = True
                else:
                    target_page = self.page

                # 步骤2: 检查页面是否正常加载
                await target_page.wait_for_load_state("networkidle", timeout=15000)
                await asyncio.sleep(2)

                # 提取页面标题和内容
                title = await target_page.title()
                content = await target_page.content()
                text_content = await target_page.evaluate(
                    "document.body.textContent"
                )

                # 步骤3: 如果是对话型智能体，测试对话
                if agent.get("type") == "对话型" or "对话" in agent.get("type", ""):
                    chat_result = await self._test_chat(target_page, agent_id)
                    agent["chat_tested"] = True
                    agent["chat_result"] = chat_result
                    if chat_result.get("success"):
                        agent["status"] = "ok"
                    else:
                        agent["status"] = "chat_error"
                        agent["error"] = chat_result.get("error")
                else:
                    agent["status"] = "ok"

                # 步骤4: 截图
                try:
                    import os
                    from config import SCREENSHOTS_DIR
                    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
                    screenshot_path = SCREENSHOTS_DIR / f"agent_{agent_id}.png"
                    await target_page.screenshot(path=str(screenshot_path), full_page=False)
                    agent["screenshot"] = str(screenshot_path)
                except Exception as e:
                    agent["screenshot_error"] = str(e)

                # 保存目标页面的内容供后续分析
                agent["page_title"] = title
                agent["text_length"] = len(text_content) if text_content else 0

                if new_page and new_page != self.page:
                    try:
                        await target_page.close()
                    except:
                        pass

                return agent

            except Exception as e:
                agent["status"] = "error"
                agent["error"] = str(e)
                return agent

    async def _test_chat(self, page, agent_id: int) -> dict:
        """
        测试对话型智能体

        使用 LLM 生成随机问题，发送并检查回复
        """
        import os

        chat_result = {
            "success": False,
            "error": None,
            "response": None,
            "message": None,
        }

        try:
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
                chat_result["error"] = "未找到聊天输入框"
                return chat_result

            # 生成测试问题
            question = self._generate_question(agent_id)
            print(f"    💬 测试问题: {question}")

            # 输入问题
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
                # 尝试按 Enter
                await chat_input.press("Enter")
            else:
                await send_btn.click()

            # 等待回复
            await page.wait_for_load_state("networkidle", timeout=30000)
            await asyncio.sleep(5)

            # 获取回复内容
            reply_selectors = [
                '[class*="chat-message"]',
                '[class*="message-content"]',
                '[class*="ai-response"]',
                '[class*="response-text"]',
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
                # 尝试获取 body text
                reply_text = await page.evaluate("document.body.textContent")
                reply_text = reply_text.strip()[:500] if reply_text else ""

            if reply_text:
                chat_result["success"] = True
                chat_result["response"] = reply_text
                chat_result["message"] = question
            else:
                chat_result["error"] = "智能体未返回有效回复"

        except asyncio.TimeoutError:
            chat_result["error"] = "对话测试超时（30秒）"
        except Exception as e:
            chat_result["error"] = f"对话测试异常: {str(e)}"

        return chat_result

    def _generate_question(self, agent_id: int) -> str:
        """
        生成测试问题

        根据 agentId 类型生成不同的测试问题
        """
        from config import VERIFIED_AGENTS

        questions_by_type = {
            "客服": [
                "你好，请问有什么可以帮助你的？",
                "我想了解一下你们的业务",
                "你们的服务时间是什么？",
            ],
            "问答": [
                "请介绍一下你自己",
                "你能做什么？",
                "请给我一个使用示例",
            ],
            "签章": [
                "如何签署电子合同？",
                "请演示一下签章流程",
                "签章需要哪些材料？",
            ],
            "EB": [
                "EB 是什么？",
                "如何创建 EB？",
                "EB 的基本功能有哪些？",
            ],
        }

        agent_info = VERIFIED_AGENTS.get(agent_id, {})
        name = agent_info.get("name", "")

        for keyword, questions in questions_by_type.items():
            if keyword in name:
                import random
                return random.choice(questions)

        # 默认问题
        return "你好，请介绍一下你自己"
