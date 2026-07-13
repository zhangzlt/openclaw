"""手动登录 Agent Market - 第7次，检查表单字段名和提交数据结构"""
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
    all_xhr_requests = []

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
            all_xhr_requests.append({"url": req.url, "method": req.method, "postData": req.post_data, "headers": dict(req.headers)})
            if req.method in ["POST", "GET"] and ("login" in req.url.lower() or "api" in req.url.lower()) else None
        ))

        print("Agent Market 登录 - 第7次（检查字段名）")

        # Visit login page
        print("\n[1] 访问登录页...")
        page.goto("https://agent.digitalchina.com/login", wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(3000)
        take_ss(page, "v7_01")

        # Check input field names
        print("\n[2] 检查表单字段...")
        form_info = page.evaluate("""
            () => {
                var form = document.querySelector('form');
                if (!form) return {error: 'no form found'};
                var inputs = Array.from(form.querySelectorAll('input')).map(function(el) {
                    return {
                        type: el.type,
                        name: el.name || '(no name)',
                        placeholder: el.placeholder,
                        id: el.id || '(no id)',
                        className: el.className.substring(0, 60),
                        autoComplete: el.autoComplete
                    };
                });
                var buttons = Array.from(form.querySelectorAll('button')).map(function(el) {
                    return {
                        text: el.innerText.substring(0, 30),
                        type: el.type
                    };
                });
                return {
                    formAction: form.action,
                    formMethod: form.method,
                    formId: form.id,
                    className: form.className.substring(0, 80),
                    inputs: inputs,
                    buttons: buttons,
                    hasSubmitHandler: form.onsubmit ? 'yes' : 'no'
                };
            }
        """)
        print(f"    form action: {form_info.get('formAction')}")
        print(f"    form method: {form_info.get('formMethod')}")
        print(f"    form class: {form_info.get('className')}")
        print(f"    hasSubmitHandler: {form_info.get('hasSubmitHandler')}")
        print(f"    inputs: {json.dumps(form_info.get('inputs', []), ensure_ascii=False, indent=4)}")
        print(f"    buttons: {json.dumps(form_info.get('buttons', []), ensure_ascii=False, indent=4)}")
        take_ss(page, "v7_02")

        # Fill credentials
        print("\n[3] 填写凭证...")
        page.locator("input[type='text']").first.fill("itcode")
        page.locator("input[type='password']").first.fill("dstest,Aa@11223344")
        page.wait_for_timeout(1000)
        take_ss(page, "v7_03")

        # Submit
        print("\n[4] 提交登录...")
        page.locator("button[type='submit']").first.click()
        page.wait_for_timeout(8000)
        take_ss(page, "v7_04")
        print(f"  URL: {page.url}")

        # Check what data was sent
        print(f"\n[5] 登录请求数据:")
        for req in all_xhr_requests:
            print(f"    URL: {req['url']}")
            print(f"    Method: {req['method']}")
            print(f"    PostData: {req.get('postData', 'N/A')[:500]}")
            print(f"    Headers: Content-Type={req['headers'].get('content-type', 'N/A')}")

        # Check API response
        print(f"\n[6] API 响应:")
        for lr in login_responses:
            print(f"    Status: {lr['status']}")
            print(f"    Body: {lr['body']}")
            print(f"    Set-Cookie: {lr['headers'].get('set-cookie', '无')}")

        # Try different field names for POST
        print(f"\n[7] 尝试不同字段名的POST请求...")
        field_variants = [
            {"itcode": "itcode", "password": "dstest,Aa@11223344"},
            {"username": "itcode", "password": "dstest,Aa@11223344"},
            {"account": "itcode", "password": "dstest,Aa@11223344"},
            {"accountCode": "itcode", "password": "dstest,Aa@11223344"},
            {"code": "itcode", "password": "dstest,Aa@11223344"},
        ]
        
        for i, data in enumerate(field_variants):
            try:
                result = page.evaluate("""
                    (data) => {
                        return fetch('/api/user/login', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
                            credentials: 'include',
                            body: JSON.stringify(data)
                        }).then(r => r.text()).then(t => {
                            return {status: r.status, body: t};
                        }).catch(e => ({error: e.message}));
                    }
                """, data)
                print(f"    变体{i} ({json.dumps(data)}): {result}")
            except Exception as e:
                print(f"    变体{i}: 执行错误 - {e}")

        # Get page state
        print(f"\n[8] 页面状态:")
        body_text = page.locator("body").inner_text()[:300]
        print(f"    {body_text}")
        take_ss(page, "v7_08")

        # Also dump the HTML of the form area
        print(f"\n[9] 登录表单HTML结构:")
        form_html = page.evaluate("""
            () => {
                var form = document.querySelector('form');
                return form ? form.outerHTML.substring(0, 3000) : 'no form';
            }
        """)
        print(f"    {form_html[:1500]}")

        browser.close()

if __name__ == "__main__":
    main()
