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
    page.fill("input[type='text']", "dstest")
    page.fill("input[type='password']", "Aa@11223344")
    page.wait_for_timeout(500)
    page.click("button[type='submit']")
    page.wait_for_timeout(5000)
    ss(page, "after_login")

    # 去市场
    page.goto("https://agent.digitalchina.com/market", wait_until="networkidle", timeout=15000)
    page.wait_for_timeout(5000)
    ss(page, "market_home")

    # 尝试点击"aily"按钮
    print("点击第一个智能体的'aily'按钮...")
    aily = page.locator("[class*='card-source']").first
    if aily.is_visible(timeout=2000):
        box = aily.bounding_box()
        page.mouse.click(box['x'] + box['width']/2, box['y'] + box['height']/2)
        page.wait_for_timeout(8000)
        ss(page, "after_click_aily")
        print(f"URL: {page.url}")
        body = page.locator("body").inner_text()[:200]
        print(f"文本: {body}")
    else:
        print("没找到aily按钮")

    b.close()
    print("完成")
