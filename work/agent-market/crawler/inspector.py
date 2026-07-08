"""
智能体巡检器

逐个访问智能体，执行健康检查
新增：LLM 描述生成测试问题 + LLM 评估回复质量
"""

import asyncio
import json
from datetime import datetime
from pathlib import Path

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
        failed = sum(1 for a in results["agents"] if a.get("status") in (
            "error", "unreachable", "chat_error", "chat_failed"
        ))
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
                text_content = await target_page.evaluate("document.body.textContent")

                # 步骤3: 如果是对话型智能体，测试对话
                is_chat_agent = agent.get("type") == "对话型" or "对话" in agent.get("type", "")
                if is_chat_agent:
                    chat_result = await self._test_chat(target_page, agent)
                    agent["chat_tested"] = True
                    agent["chat_result"] = chat_result
                    agent["status"] = self._resolve_status(chat_result)
                else:
                    agent["status"] = "ok"

                # 步骤4: 保存元数据
                agent["page_title"] = title
                agent["text_length"] = len(text_content) if text_content else 0

                if new_page and new_page != self.page:
                    try:
                        await target_page.close()
                    except Exception:
                        pass

                return agent

            except Exception as e:
                agent["status"] = "error"
                agent["error"] = str(e)
                return agent

    def _resolve_status(self, chat_result: dict) -> str:
        """根据对话测试结果解析状态"""
        if not chat_result.get("success"):
            return "chat_error"

        # 如果有 LLM 评估结果
        evaluation = chat_result.get("evaluation")
        if evaluation:
            if not evaluation.get("passed", True):
                return "chat_failed"  # 页面能访问但回复质量差
            return "ok"

        # 降级：只要能获取回复就算通过
        return "ok"

    async def _test_chat(self, page, agent: dict) -> dict:
        """
        测试对话型智能体

        - 使用 LLM 根据 agent 描述生成多个测试问题
        - 逐个发送，获取回复
        - 使用 LLM 评估每个回复的质量
        """
        from utils.llm import generate_test_questions, evaluate_response

        chat_result = {
            "success": False,
            "question": "",
            "response": "",
            "questions_tested": [],  # [{question, response, evaluation}]
            "evaluation": None,      # 整体评估
            "error": None,
        }

        try:
            # 查找聊天输入框
            chat_input = await self._find_element(page, [
                "textarea",
                "input[type='text']",
                "[placeholder*='请输入']",
                "[placeholder*='发送']",
                '[class*="chat-input"]',
                '[class*="message-input"]',
            ])

            if not chat_input:
                chat_result["error"] = "未找到聊天输入框"
                return chat_result

            # 查找发送按钮
            send_btn = await self._find_element(page, [
                "button[type='submit']",
                "[class*='send-btn']",
                "[class*='send-button']",
                '[class*="chat-send"]',
            ])

            # 从 agent 信息中获取描述
            agent_name = agent.get("name", "未知智能体")
            agent_type = agent.get("type", "")
            agent_desc = agent.get("description", "")

            # 使用 LLM 生成测试问题（基于描述）
            questions = await generate_test_questions(
                agent_name=agent_name,
                agent_type=agent_type,
                agent_desc=agent_desc,
                count=3,
            )

            print(f"    💬 使用 {len(questions)} 个 LLM 生成的测试问题")

            # 逐个测试
            results = []
            for q in questions:
                q_result = await self._send_question_and_get_reply(
                    page, chat_input, send_btn, q
                )
                results.append(q_result)

                # 如果第一个问题就彻底失败，提前退出
                if not q_result["success"] and q_result.get("fatal"):
                    break

                # 问题之间间隔，避免太频繁
                if q != questions[-1] and q_result.get("response"):
                    await asyncio.sleep(2)

            # 收集结果
            chat_result["questions_tested"] = results
            chat_result["question"] = questions[0] if questions else ""

            # 使用 LLM 评估整体回复质量
            # 取第一个非空回复进行评估
            first_response = ""
            for r in results:
                if r.get("response"):
                    first_response = r["response"]
                    break

            if first_response:
                evaluation = await evaluate_response(
                    agent_name=agent_name,
                    question=questions[0] if questions else "",
                    response=first_response,
                )
                chat_result["evaluation"] = evaluation
                chat_result["success"] = evaluation.get("passed", True)
            elif not results:
                chat_result["error"] = "所有测试问题均未获得回复"
                chat_result["success"] = False
            else:
                # 至少有一个回复但不完整
                chat_result["success"] = any(r.get("response") for r in results)

            if chat_result.get("error"):
                chat_result["success"] = False

        except asyncio.TimeoutError:
            chat_result["error"] = "对话测试超时（30秒）"
        except Exception as e:
            chat_result["error"] = f"对话测试异常: {str(e)}"

        return chat_result

    async def _find_element(self, page, selectors: list):
        """查找页面元素，返回 locator 或 None"""
        for selector in selectors:
            try:
                elem = page.locator(selector).first
                if await elem.count() > 0:
                    return elem
            except Exception:
                continue
        return None

    async def _send_question_and_get_reply(self, page, chat_input, send_btn, question: str) -> dict:
        """发送一个问题并获取回复"""
        result = {"question": question, "success": False, "response": "", "fatal": False}

        try:
            print(f"    💬 测试问题: {question}")

            # 输入问题
            await chat_input.fill(question)
            await asyncio.sleep(0.5)  # 等待输入完成

            # 发送
            if not send_btn:
                await chat_input.press("Enter")
            else:
                await send_btn.click()

            # 等待回复（流式输出可能很快显示，但网络请求可能还没结束）
            await page.wait_for_load_state("networkidle", timeout=30000)
            await asyncio.sleep(3)

            # 获取回复
            reply_text = await self._extract_reply(page)

            if reply_text:
                result["success"] = True
                result["response"] = reply_text
            else:
                result["response"] = ""
                # 检查是否显示加载状态（可能还在等待）
                loading_selectors = [
                    '[class*="loading"]',
                    '[class*="typing"]',
                    '[class*="thinking"]',
                    '[class*="spinner"]',
                    '[class*="dot-"]',
                ]
                is_loading = False
                for sel in loading_selectors:
                    try:
                        if await page.locator(sel).count() > 0:
                            is_loading = True
                            break
                    except Exception:
                        pass

                if is_loading:
                    result["error"] = "智能体仍在处理中，回复可能未就绪"
                else:
                    result["error"] = "智能体未返回有效回复"

        except asyncio.TimeoutError:
            result["error"] = "对话测试超时（30秒）"
        except Exception as e:
            result["error"] = f"发送异常: {str(e)}"

        return result

    async def _extract_reply(self, page) -> str:
        """从页面提取 AI 回复内容"""
        reply_selectors = [
            '[class*="chat-message"]',
            '[class*="message-content"]',
            '[class*="ai-response"]',
            '[class*="response-text"]',
            '[class*="assistant-message"]',
            '[class*="agent-response"]',
        ]

        for selector in reply_selectors:
            try:
                elems = page.locator(selector)
                count = await elems.count()
                if count > 0:
                    # 取最后一个（最新回复）
                    elem = elems.last
                    reply_text = await elem.text_content()
                    if reply_text and reply_text.strip():
                        return reply_text.strip()
            except Exception:
                continue

        # 降级：尝试获取 body text
        body_text = await page.evaluate("document.body.textContent")
        if body_text and body_text.strip():
            # 过滤掉 UI 文字，尽量提取对话内容
            return body_text.strip()[:1000]

        return ""
