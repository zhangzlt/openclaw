"""手动登录 Agent Market - 第5次，拦截API响应"""
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
    login_response_body = None
    login_response_status = None
    login_response_headers = None

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        )
        context = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = context.new_page()

        # Intercept the login API response
        page.on("response", lambda resp: (
            setattr(__builtins__, '_login_resp', resp) if "api/user/login" in resp.url else None
        ))

        print("Agent Market 登录 - 第5次（拦截API响应）")

        # Visit login page
        print("\n[1] 访问登录页...")
        page.goto("https://agent.digitalchina.com/login", wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(3000)
        take_ss(page, "v5_01")

        # Fill credentials
        print("\n[2] 填写凭证...")
        page.locator("input[type='text']").first.fill("itcode")
        page.locator("input[type='password']").first.fill("dstest,Aa@11223344")
        page.wait_for_timeout(500)
        take_ss(page, "v5_02")

        # Submit login - capture response
        print("\n[3] 提交登录...")
        
        # Use request_finished to capture the login response
        login_resp_future = page.wait_for_response("**/api/user/login**")
        
        page.locator("button[type='submit']").first.click()
        page.wait_for_timeout(8000)
        
        try:
            login_resp = login_resp_future.value()
            login_response_body = login_resp.text()
            login_response_status = login_resp.status()
            login_response_headers = dict(login_resp.headers())
            print(f"\n  API响应状态: {login_response_status}")
            print(f"  API响应头（部分）: { {k:v for k,v in login_response_headers.items() if k.lower() in ['content-type', 'set-cookie', 'x-request-id']} }")
            print(f"  API响应体: {login_response_body[:2000]}")
        except Exception as e:
            print(f"\n  未捕获到login API响应: {e}")

        take_ss(page, "v5_03")
        print(f"  URL: {page.url}")

        # Check cookies
        cookies = context.cookies()
        print(f"\n[4] 当前Cookies: {len(cookies)} 个")
        for c in cookies:
            print(f"    {c['name']} = {c['value'][:50]}... domain={c['domain']}")

        # Check local storage
        try:
            ls = page.evaluate("JSON.stringify(localStorage)")
            print(f"\n[5] localStorage keys:")
            ls_data = json.loads(ls) if ls != "{}" else {}
            for k in ls_data:
                print(f"    {k}: {ls_data[k][:100]}")
        except:
            print("  (无localStorage数据)")

        # Check alert/toast
        body_text = page.locator("body").inner_text()
        print(f"\n[6] 页面文本（前500字）:")
        print(body_text[:500])

        # Check for alert dialogs
        print(f"\n[7] 当前弹窗: 无")

        # Try direct POST to login API
        print(f"\n[8] 尝试直接POST到登录API...")
        post_resp = page.evaluate("""
            async () => {
                try {
                    const resp = await fetch('/api/user/login', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({itcode: 'itcode', password: 'dstest,Aa@11223344'})
                    });
                    return { status: resp.status, body: await resp.text(), headers: Object.fromEntries(resp.headers.entries()) };
                } catch(e) {
                    return { error: e.message };
                }
            }
        """)
        print(f"    直接POST结果: {json.dumps(post_resp, ensure_ascii=False, indent=2)}")
        take_ss(page, "v5_04")

        browser.close()

if __name__ == "__main__":
    main()
