from playwright.sync_api import sync_playwright
import os

OUT = "/home/node/.openclaw/workspace/work/agent-market/screenshots"
os.makedirs(OUT, exist_ok=True)

def ss(page, name):
    path = os.path.join(OUT, f"{name}.png")
    page.screenshot(path=path)
    print(f"截图: {path}")

with sync_playwright() as p:
    b = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"])
    ctx = b.new_context(viewport={"width": 1920, "height": 1080})
    page = ctx.new_page()

    # 登录
    page.goto("https://agent.digitalchina.com/login", wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(3000)
    ss(page, "login_page")

    page.fill("input[type='text']", "dstest")
    page.fill("input[type='password']", "Aa@11223344")
    page.wait_for_timeout(500)
    ss(page, "login_filled")

    page.click("button[type='submit']")
    page.wait_for_timeout(5000)
    ss(page, "after_login")

    # 去市场
    page.goto("https://agent.digitalchina.com/market", wait_until="networkidle", timeout=15000)
    page.wait_for_timeout(5000)
    ss(page, "market_home")

    # 点击第一个智能体卡片（按 card-bottom 里"打开"按钮）
    # "打开"是第二张卡片的按钮，但每个卡片都有，取第一个
    card_bottom = page.locator("[class*='card-bottom']").first
    open_btn = card_bottom.locator("text='打开'").first
    print(f"找到'打开'按钮: {open_btn.is_visible(timeout=3000)}")
    
    if open_btn.is_visible(timeout=2000):
        open_btn.click()
        page.wait_for_timeout(8000)
    else:
        # 退而求其次：直接点卡片容器
        card = page.locator("[class*='agent-card']").first
        card.click()
        page.wait_for_timeout(8000)

    ss(page, "after_click_agent")
    print(f"当前URL: {page.url}")
    body_text = page.locator("body").inner_text()[:300]
    print(f"页面文本: {body_text}")

    b.close()
    print("完成")
