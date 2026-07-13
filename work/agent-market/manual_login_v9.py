"""手动登录 Agent Market - 第9次，正确凭证"""
from playwright.sync_api import sync_playwright
import os, json

SCREENSHOTS_DIR = "/home/node/.openclaw/workspace/work/agent-market/screenshots"
os.makedirs(SCREENSHOTS_DIR, exist_ok=True)

def take_ss(page, name):
    path = os.path.join(SCREENSHOTS_DIR, f"{name}.png")
    page.screenshot(path=path, full_page=False)
    print(f"  [ss] {name}")
    return path

def main():
    login_responses = []
    login_request_data = None

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        )
        context = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = context.new_page()

        page.on("response", lambda resp: (
            login_responses.append({"status": resp.status, "body": resp.text(), "url": resp.url, "headers": dict(resp.headers)})
            if "api/user/login" in resp.url else None
        ))
        page.on("request", lambda req: (
            (setattr(__builtins__, '_last_login_req', {"url": req.url, "method": req.method, "postData": req.post_data}) and None)
            if "api/user/login" in req.url and req.method == "POST" else None
        ))

        print("Agent Market 登录 - 第9次（正确凭证：dstest / Aa@11223344）")

        # Visit login page
        print("\n[1] 访问登录页...")
        page.goto("https://agent.digitalchina.com/login", wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(3000)
        take_ss(page, "v9_01")

        # Fill credentials with CORRECT username
        print("\n[2] 填写凭证...")
        print(f"    用户名: dstest")
        print(f"    密  码: Aa@11223344")
        page.locator("input[type='text']").first.fill("dstest")
        page.locator("input[type='password']").first.fill("Aa@11223344")
        page.wait_for_timeout(500)
        take_ss(page, "v9_02")

        # Submit
        print("\n[3] 提交登录...")
        page.locator("button[type='submit']").first.click()
        page.wait_for_timeout(8000)
        take_ss(page, "v9_03")

        print(f"\n[4] 请求数据:")
        req = eval("_last_login_req") if '_last_login_req' in dir(__builtins__) else None
        if req:
            print(f"    URL: {req['url']}")
            print(f"    Body: {req['postData']}")

        print(f"\n[5] API 响应:")
        for lr in login_responses:
            print(f"    Status: {lr['status']}")
            print(f"    Body: {lr['body']}")

        # Check cookies
        cookies = context.cookies()
        print(f"\n[6] Cookies ({len(cookies)} 个):")
        for c in cookies:
            print(f"    {c['name']} = {c['value'][:50]}... domain={c['domain']}")

        # Check page state
        body_text = page.locator("body").inner_text()
        print(f"\n[7] 页面状态:")
        print(f"    URL: {page.url}")
        print(f"    标题: {page.title()}")
        if "退出登录" in body_text or "重新登录" in body_text:
            print(f"    ⚠️ 登录失败 - 页面提示重新登录")
        else:
            print(f"    ✅ 可能登录成功!")
        print(f"    文本前500字:\n    {body_text[:500]}")

        # If logged in, go to agent-market and check agents
        if "agent-market" in page.url or "退出登录" not in body_text and "重新登录" not in body_text:
            print(f"\n[8] 导航到 agent-market...")
            page.goto("https://agent.digitalchina.com/agent-market", wait_until="networkidle", timeout=15000)
            page.wait_for_timeout(3000)
            take_ss(page, "v9_08_market")
            print(f"    URL: {page.url}")

            # Analyze agents
            body_text = page.locator("body").inner_text()
            print(f"\n[9] agent-market 页面文本:\n{body_text[:1000]}")
            take_ss(page, "v9_09_agents")

            # Find agent cards
            print(f"\n[10] 查找智能体卡片...")
            # Click first agent to trigger QR code
            links = page.locator("a").all()
            agent_links = []
            for link in links:
                try:
                    if link.is_visible():
                        text = link.inner_text().strip()
                        if text and len(text) > 2 and len(text) < 50:
                            href = link.get_attribute("href") or ""
                            agent_links.append({"text": text, "href": href})
                except:
                    pass
            
            print(f"    找到 {len(agent_links)} 个链接:")
            for al in agent_links[:10]:
                print(f"      '{al['text']}' -> {al['href']}")

            # Try clicking first non-empty link
            if agent_links:
                first_link = page.locator(f"a:has-text('{agent_links[0]['text']}')").first
                try:
                    print(f"\n[11] 点击第一个智能体: {agent_links[0]['text']}")
                    first_link.click()
                    page.wait_for_timeout(5000)
                    take_ss(page, "v9_11_agent_detail")
                    print(f"    URL: {page.url}")
                    body_after = page.locator("body").inner_text()
                    
                    # Check for QR code
                    if "扫码" in body_after or "qrcode" in body_after.lower() or "QR" in body_after or "二维码" in body_after:
                        print(f"    ⚡ 检测到扫码登录提示!")
                        take_ss(page, "v9_12_qr_code")
                    elif "退出登录" in body_after:
                        print(f"    ⚠️ 页面提示重新登录")
                    else:
                        print(f"    页面文本:\n    {body_after[:500]}")
                except Exception as e:
                    print(f"    点击失败: {e}")

        browser.close()

if __name__ == "__main__":
    main()
