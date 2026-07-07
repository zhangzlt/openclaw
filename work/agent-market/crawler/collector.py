"""
采集智能体列表

从 agent.digitalchina.com 市场页面提取智能体卡片信息
"""

from browser.playwright_setup import BrowserManager


class AgentCollector:
    """智能体列表采集器"""

    def __init__(self, browser_mgr: BrowserManager):
        self.browser_mgr = browser_mgr
        self.page = browser_mgr.page

    async def collect_agents(self) -> list:
        """
        采集市场页面所有智能体卡片

        Returns:
            list: 智能体列表，每个元素为 dict:
                  {agent_id, name, description, type, detail_url}
        """
        from config import MARKET_URL

        await self.page.goto(MARKET_URL, wait_until="domcontentloaded")
        await self.browser_mgr.wait_load()

        agents = []

        # 提取所有智能体卡片
        # 使用 textContent 而非 innerText（React 虚拟滚动兼容）
        cards = self.page.locator('[class*="agent-card"], [class*="agent-item"], [data-agent-id]')
        count = await cards.count()

        for i in range(count):
            try:
                card = cards.nth(i)

                # 提取 agentId（通过点击触发的 API 参数）
                agent_id = await card.get_attribute("data-agent-id") or \
                           await card.get_attribute("data-id") or \
                           await self._extract_agent_id(card)

                # 提取名称
                name = await card.locator('[class*="agent-name"], [class*="agent-title"]').first.text_content()
                name = name.strip() if name else f"智能体-{agent_id}"

                # 提取描述
                desc_elem = card.locator('[class*="agent-desc"], [class*="description"]').first
                description = await desc_elem.text_content() if await desc_elem.count() else ""
                description = description.strip()

                # 提取类型（对话型/工具型等）
                type_elem = card.locator('[class*="agent-type"], [class*="category"]').first
                agent_type = await type_elem.text_content() if await type_elem.count() else "未知"
                agent_type = agent_type.strip()

                # 提取详情页 URL
                detail_url = await self._get_detail_url(card, agent_id)

                if agent_id:
                    agents.append({
                        "agent_id": int(agent_id) if agent_id.isdigit() else agent_id,
                        "name": name,
                        "description": description,
                        "type": agent_type,
                        "detail_url": detail_url,
                        "status": "unknown",
                        "error": None,
                    })
            except Exception as e:
                agents.append({
                    "agent_id": i,
                    "name": f"未知-{i}",
                    "description": "",
                    "type": "未知",
                    "detail_url": "",
                    "status": "error",
                    "error": str(e),
                })

        return agents

    async def _extract_agent_id(self, card) -> str:
        """从点击事件中提取 agentId"""
        # 查找点击后触发的 widget/track API
        track_urls = await self.browser_mgr.context.expect_request(
            lambda req: "widget/track" in req.url,
            timeout=1000,
        ) if False else []

        # 备选方案：从 href 或 data 属性提取
        href = await card.get_attribute("href")
        if href and f"/agentId=" in href:
            parts = href.split("agentId=")
            if len(parts) > 1:
                return parts[1].split("&")[0]

        return ""

    async def _get_detail_url(self, card, agent_id: str) -> str:
        """构建智能体详情页 URL"""
        from config import BASE_URL, CHAT_PATH_PREFIX

        # 查找 detail 路径
        detail = await card.get_attribute("data-detail") or \
                 await card.get_attribute("data-path") or ""

        if not detail and agent_id:
            # 根据已知数据映射
            from config import VERIFIED_AGENTS
            if int(agent_id) in VERIFIED_AGENTS:
                detail = VERIFIED_AGENTS[int(agent_id)]["detail_path"]

        if detail:
            return f"{BASE_URL}{CHAT_PATH_PREFIX}{detail.lstrip('/')}"

        return f"{BASE_URL}/agent/{agent_id}" if agent_id else ""
