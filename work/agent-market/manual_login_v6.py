"""手动登录 Agent Market - 第6次，拦截API响应"""
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

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        )
        context = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = context.new_page()

        # Intercept login API response
        page.on("response", lambda resp: (
            login_responses.append({"status": resp.status, "body": resp.text(), "headers": dict(resp.headers)})
            if "api/user/login" in resp.url else None
        ))

        print("Agent Market 登录 - 第6次（拦截API响应）")

        # Visit login page
        print("\n[1] 访问登录页...")
        page.goto("https://agent.digitalchina.com/login", wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(3000)
        take_ss(page, "v6_01")

        # Fill credentials
        print("\n[2] 填写凭证...")
        page.locator("input[type='text']").first.fill("itcode")
        page.locator("input[type='password']").first.fill("dstest,Aa@11223344")
        page.wait_for_timeout(500)
        take_ss(page, "v6_02")

        # Submit login
        print("\n[3] 提交登录...")
        page.locator("button[type='submit']").first.click()
        page.wait_for_timeout(8000)
        take_ss(page, "v6_03")
        print(f"  URL: {page.url}")

        # Analyze login API response
        print(f"\n[4] API /api/user/login 响应: {len(login_responses)} 条")
        for i, lr in enumerate(login_responses):
            print(f"  [{i}] 状态: {lr['status']}")
            print(f"      Content-Type: {lr['headers'].get('content-type', 'N/A')}")
            print(f"      Set-Cookie: {lr['headers'].get('set-cookie', '无')}")
            body_preview = lr['body'][:2000]
            print(f"      响应体: {body_preview}")

        # Check cookies
        cookies = context.cookies()
        print(f"\n[5] 当前Cookies ({len(cookies)} 个):")
        for c in cookies:
            print(f"    {c['name']} = {c['value'][:80]}... domain={c['domain']} path={c['path']}")

        # Check localStorage/sessionStorage
        print(f"\n[6] localStorage/sessionStorage:")
        storage = page.evaluate("""
            () => {
                var result = {};
                try {
                    for (var i = 0; i < localStorage.length; i++) {
                        var k = localStorage.key(i);
                        var v = localStorage.getItem(k);
                        result['localStorage.' + k] = v.substring(0, 200);
                    }
                    for (var i = 0; i < sessionStorage.length; i++) {
                        var k = sessionStorage.key(i);
                        var v = sessionStorage.getItem(k);
                        result['sessionStorage.' + k] = v.substring(0, 200);
                    }
                } catch(e) {}
                return result;
            }
        """)
        for k, v in storage.items():
            print(f"    {k}: {v[:200]}")

        # Try direct POST
        print(f"\n[7] 直接POST到登录API...")
        post_result = page.evaluate("""
            fetch('/api/user/login', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                credentials: 'include',
                body: JSON.stringify({itcode: 'itcode', password: 'dstest,Aa@11223344'})
            }).then(r => r.text()).then(t => {
                return {status: r.status, body: t, headers: Object.fromEntries(r.headers.entries())};
            }).catch(e => ({error: e.message}));
        """)
        print(f"    结果: {json.dumps(post_result, ensure_ascii=False, indent=2)[:2000]}")

        # Check page state
        print(f"\n[8] 页面状态:")
        body_text = page.locator("body").inner_text()[:500]
        print(f"    文本: {body_text}")
        take_ss(page, "v6_08")

        # Also check what's in the form after login attempt
        form_action = page.evaluate("document.querySelector('form')?.action")
        form_method = page.evaluate("document.querySelector('form')?.method")
        print(f"    form action: {form_action}")
        print(f"    form method: {form_method}")

        browser.close()

if __name__ == "__main__":
    main()
