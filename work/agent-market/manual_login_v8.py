"""手动登录 Agent Market - 第8次，检查JS和尝试不同字段名"""
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

        page.on("response", lambda resp: (
            login_responses.append({"status": resp.status, "body": resp.text(), "url": resp.url})
            if "api/user/login" in resp.url else None
        ))

        print("Agent Market 登录 - 第8次")

        # Visit login page
        print("\n[1] 访问登录页...")
        page.goto("https://agent.digitalchina.com/login", wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(3000)
        take_ss(page, "v8_01")

        # Find the JS file that handles login
        print("\n[2] 查找登录相关JS...")
        scripts = page.evaluate("""
            () => Array.from(document.querySelectorAll('script')).map(s => s.src || s.getAttribute('src') || '(inline)')
        """)
        print(f"  脚本: {scripts[:10]}")

        # Try to find the login handler source
        inline_js = page.evaluate("""
            () => {
                var scripts = Array.from(document.querySelectorAll('script:not([src])'));
                var loginSources = [];
                scripts.forEach(function(s, i) {
                    var text = s.textContent;
                    if (text.indexOf('login') !== -1 || text.indexOf('loginForm') !== -1) {
                        loginSources.push({index: i, length: text.length, preview: text.substring(0, 200)});
                    }
                });
                return loginSources;
            }
        """)
        print(f"  内联JS中与login相关的: {len(inline_js)} 个")
        for item in inline_js:
            print(f"    index={item['index']}, length={item['length']}, preview={item['preview'][:150]}")

        # Also check the loaded JS files for login handler
        js_sources = page.evaluate("""
            () => {
                var scripts = Array.from(document.querySelectorAll('script[src*="index"]'));
                return scripts.map(s => s.src);
            }
        """)
        print(f"  index JS files: {js_sources}")

        # Look for login submission logic in the JS bundle
        if js_sources:
            print(f"\n  [2b] 在JS bundle中搜索登录逻辑...")
            # Read the main JS file source
            try:
                js_url = js_sources[0]
                # We can't read the JS file directly, but we can look at the page structure
                pass
            except:
                pass

        # Check what happens on button click
        print(f"\n[3] 检查表单提交时的网络请求...")
        
        # Fill credentials
        page.locator("input[type='text']").first.fill("itcode")
        page.locator("input[type='password']").first.fill("dstest,Aa@11223344")
        page.wait_for_timeout(1000)
        take_ss(page, "v8_03")

        # Track ALL network requests during login
        all_requests = []
        page.on("request", lambda req: all_requests.append({
            "url": req.url,
            "method": req.method,
            "postData": req.post_data,
            "headers": dict(req.headers)
        }))
        login_responses = []
        page.on("response", lambda resp: (
            login_responses.append({"status": resp.status, "body": resp.text(), "url": resp.url})
            if "login" in resp.url.lower() else None
        ))

        # Submit
        print(f"\n[4] 提交登录...")
        page.locator("button[type='submit']").first.click()
        page.wait_for_timeout(8000)
        take_ss(page, "v8_04")
        print(f"  URL: {page.url}")

        # Analyze all requests
        print(f"\n[5] 所有请求分析:")
        for req in all_requests:
            url = req['url']
            method = req['method']
            if any(kw in url for kw in ['login', 'api', 'auth', 'token', 'session']):
                print(f"  {method} {url}")
                if req.get('postData'):
                    print(f"    Body: {req['postData'][:500]}")

        # Analyze login responses
        print(f"\n[6] 登录相关API响应:")
        for lr in login_responses:
            print(f"  {lr['status']} {lr['url']}")
            print(f"    Body: {lr['body'][:500]}")

        # Check what the form element actually does
        print(f"\n[7] 检查form的onsubmit和action:")
        form_details = page.evaluate("""
            () => {
                var form = document.querySelector('form');
                return {
                    action: form ? form.action : 'none',
                    method: form ? form.method : 'none',
                    onSubmit: form ? form.onsubmit ? 'has onsubmit' : 'no onsubmit' : 'none',
                    outerHTML: form ? form.outerHTML.substring(0, 2000) : 'no form'
                };
            }
        """)
        print(f"    {json.dumps(form_details, ensure_ascii=False, indent=2)[:1000]}")

        # Now try to understand the submission - click with different approaches
        print(f"\n[8] 重置表单并尝试不同提交方式...")
        
        # Reset
        page.goto("https://agent.digitalchina.com/login", wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(2000)
        
        # Fill
        page.locator("input[type='text']").first.fill("itcode")
        page.locator("input[type='password']").first.fill("dstest,Aa@11223344")
        
        # Try clicking the button and intercepting the fetch/XHR
        login_responses = []
        page.on("response", lambda resp: (
            login_responses.append({"status": resp.status, "body": resp.text(), "url": resp.url, "headers": dict(resp.headers)})
            if "api/user/login" in resp.url else None
        ))

        # Use page.on("request") to intercept the login POST
        login_request = []
        page.on("request", lambda req: (
            login_request.append({"url": req.url, "method": req.method, "postData": req.post_data, "headers": dict(req.headers)})
            if "login" in req.url.lower() else None
        ))

        # Method 1: Click submit button
        print(f"  [8a] 点击submit按钮...")
        page.locator("button[type='submit']").first.click()
        page.wait_for_timeout(5000)
        take_ss(page, "v8_08a")

        print(f"\n  [8b] 请求数据:")
        for lr in login_request:
            print(f"    URL: {lr['url']}")
            print(f"    Method: {lr['method']}")
            print(f"    Headers: Content-Type={lr['headers'].get('content-type', 'N/A')}")
            print(f"    PostData: {lr.get('postData', 'N/A')[:500]}")
        
        print(f"\n  [8c] API响应:")
        for lr in login_responses:
            print(f"    Status: {lr['status']}")
            print(f"    Body: {lr['body']}")

        # Also dump full HTML
        print(f"\n[9] 登录区域完整HTML:")
        login_area = page.evaluate("""
            () => {
                var loginSection = document.querySelector('[class*="login"]') || document.body;
                return loginSection.outerHTML.substring(0, 4000);
            }
        """)
        print(login_area[:2000])

        browser.close()

if __name__ == "__main__":
    main()
