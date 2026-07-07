"""
浏览器管理 - Playwright BrowserManager

负责浏览器生命周期管理、上下文隔离、标签页操作
"""

from playwright.async_api import async_playwright, Browser, BrowserContext, Page


class BrowserManager:
    """浏览器管理器"""

    def __init__(self):
        self._playwright = None
        self._browser: Browser = None
        self._context: BrowserContext = None
        self._page: Page = None

    async def start(self):
        """启动浏览器"""
        from config import BROWSER_CONFIG

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=BROWSER_CONFIG["headless"],
            args=["--no-sandbox", "--disable-setuid-sandbox"],
        )
        self._context = await self._browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        self._page = await self._context.new_page()
        return self

    async def stop(self):
        """关闭浏览器"""
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    @property
    def browser(self) -> Browser:
        return self._browser

    @property
    def context(self) -> BrowserContext:
        return self._context

    @property
    def page(self) -> Page:
        return self._page

    async def new_page(self) -> Page:
        """创建新标签页"""
        page = await self._context.new_page()
        return page

    async def close_page(self, page: Page):
        """关闭标签页"""
        await page.close()

    async def wait_load(self, timeout: int = None):
        """等待页面加载完成"""
        timeout = timeout or self._page._timeout if hasattr(self._page, '_timeout') else 30000
        await self._page.wait_for_load_state("networkidle", timeout=timeout)

    async def take_screenshot(self, path: str):
        """截图"""
        await self._page.screenshot(path=path, full_page=True)
