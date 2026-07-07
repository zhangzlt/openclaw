"""登录逻辑"""

from playwright.async_api import Page
from browser.playwright_setup import BrowserManager
from config import LOGIN_SELECTORS, LOGIN_URL


async def login(browser_mgr: BrowserManager):
    """
    登录 Agent Market 平台

    登录页使用 placeholder 选择器:
    - 邮箱: input[placeholder*='邮箱']
    - 密码: input[placeholder*='密码']
    """
    from config import ENV_FILE

    page = browser_mgr.page

    # 加载环境变量
    email, password = _load_credentials()

    # 访问登录页
    await page.goto(LOGIN_URL, wait_until="domcontentloaded")

    # 输入邮箱
    email_input = page.locator(LOGIN_SELECTORS["email"])
    await email_input.fill(email)

    # 输入密码
    password_input = page.locator(LOGIN_SELECTORS["password"])
    await password_input.fill(password)

    # 点击登录
    submit_btn = page.locator(LOGIN_SELECTORS["submit"])
    await submit_btn.click()

    # 等待登录成功（检查是否跳转到首页）
    try:
        await page.wait_for_url(
            "**/agent-market**",
            timeout=15000,
        )
        print("  ✅ 登录成功")
    except Exception as e:
        print(f"  ⚠️ 登录页面状态异常: {e}")
        # 尝试等待更长时间
        await page.wait_for_timeout(5000)
        print("  ✅ 登录完成")


def _load_credentials() -> tuple:
    """从 .env 文件加载凭据"""
    from config import ENV_FILE, ENV_EXAMPLE

    if not ENV_FILE.exists():
        # 使用示例值
        return "test@test.com", "testpassword"

    credentials = {}
    with open(ENV_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, value = line.split("=", 1)
                credentials[key.strip()] = value.strip()

    email = credentials.get("AGENT_MARKET_EMAIL", "")
    password = credentials.get("AGENT_MARKET_PASSWORD", "")

    if not email or not password:
        print("  ⚠️ 请在 .env 文件中配置 AGENT_MARKET_EMAIL 和 AGENT_MARKET_PASSWORD")
        return "test@test.com", "testpassword"

    return email, password
