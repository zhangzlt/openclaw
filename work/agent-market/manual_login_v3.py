"""手动登录 Agent Market - 第3次尝试，分析真实DOM结构"""
from playwright.sync_api import sync_playwright
import os

SCREENSHOTS_DIR = "/home/node/.openclaw/workspace/work/agent-market/screenshots"
os.makedirs(SCREENSHOTS_DIR, exist_ok=True)

def take_screenshot(page, name):
    path = os.path.join(SCREENSHOTS_DIR, f"{name}.png")
    page.screenshot(path=path, full_page=False)
    print(f"  📸 {name}")
    return path

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        )
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
        )
        page = context.new_page()
        
        # Intercept requests to see login API
        login_requests = []
        page.on("request", lambda req: login_requests.append(req.url) if "login" in req.url.lower() else None)
        
        print("=" * 60)
        print("Agent Market 登录 - 第3次尝试")
        print("=" * 60)
        
        # 1. 访问登录页
        print("\n[1] 访问登录页...")
        page.goto("https://agent.digitalchina.com/login", wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(3000)
        take_screenshot(page, "v3_01_login")
        
        # 2. 分析所有 input 元素
        print("\n[2] 分析输入框结构...")
        inputs_js = """() => {
            return Array.from(document.querySelectorAll('input')).map(el => ({
                type: el.type,
                name: el.name,
                placeholder: el.placeholder,
                id: el.id,
                className: el.className,
                value: el.value,
                tagName: el.tagName,
                style: el.style.display
            }));
        }"""
        inputs = page.evaluate(inputs_js)
        print(f"  找到 {len(inputs)} 个输入框:")
        for i, inp in enumerate(inputs):
            print(f"    [{i}] type={inp['type']}, name={inp['name']}, placeholder={inp['placeholder']}, id={inp['id']}, class={inp['className'][:50]}, value={inp['value']}")
        take_screenshot(page, "v3_02_inputs")
        
        # 3. 分析所有 button 元素
        print("\n[3] 分析按钮结构...")
        btns_js = """() => {
            return Array.from(document.querySelectorAll('button')).map(el => ({
                text: el.innerText.trim().substring(0, 30),
                type: el.type,
                className: el.className
            }));
        }"""
        buttons = page.evaluate(btns_js)
        print(f"  找到 {len(buttons)} 个按钮:")
        for btn in buttons:
            if btn['text'] or btn['className']:
                print(f"    text='{btn['text']}', type={btn['type']}, class={btn['className'][:80]}")
        take_screenshot(page, "v3_03_buttons")
        
        # 4. 分析 form 元素
        print("\n[4] 分析form结构...")
        forms_js = """() => {
            return Array.from(document.querySelectorAll('form')).map(el => ({
                action: el.action,
                method: el.method,
                id: el.id,
                className: el.className,
                childCount: el.children.length
            }));
        }"""
        forms = page.evaluate(forms_js)
        print(f"  找到 {len(forms)} 个form:")
        for form in forms:
            print(f"    action={form['action']}, method={form['method']}, id={form['id']}, class={form['className'][:60]}")
        take_screenshot(page, "v3_04_forms")
        
        # 5. 查找 tab 切换
        print("\n[5] 查找tab...")
        all_text = page.locator("body").inner_text()
        print(f"  页面可见文本（前300字）:\n{all_text[:300]}")
        
        # 查找所有包含"账号密码"或"tab"的元素
        tab_candidates = page.locator("text='账号密码'").all()
        print(f"  '账号密码' 元素: {len(tab_candidates)} 个")
        for i, t in enumerate(tab_candidates):
            tag = t.evaluate("el => el.tagName")
            cls = t.evaluate("el => el.className")
            print(f"    [{i}] <{tag}> class={cls[:60]}")
        
        forgot_candidates = page.locator("text='忘记密码'").all()
        print(f"  '忘记密码' 元素: {len(forgot_candidates)} 个")
        
        qrcode_text = page.locator("text='使用手机号或微信扫码登录'").all()
        print(f"  QR码文本元素: {len(qrcode_text)} 个")
        
        take_screenshot(page, "v3_05_tab_info")
        
        # 6. 填写itcode
        print("\n[6] 填写itcode...")
        text_inputs = page.locator("input[type='text'], input:not([type])")
        count = text_inputs.count()
        print(f"  文本输入框: {count}")
        if count > 0:
            text_inputs.first.fill("itcode")
            print("  已填写itcode")
        
        take_screenshot(page, "v3_06_itcode")
        
        # 7. 填写密码
        print("\n[7] 填写密码...")
        pw_inputs = page.locator("input[type='password']")
        count = pw_inputs.count()
        print(f"  密码输入框: {count}")
        if count > 0:
            pw_inputs.first.fill("dstest,Aa@11223344")
            print("  已填写密码")
        else:
            # 尝试找非text非hidden的输入框
            other = page.locator("input:not([type='text']):not([type='hidden']):not([type='button']):not([type='submit'])")
            count2 = other.count()
            print(f"  其他输入框: {count2}")
            for i in range(count2):
                inp_type = other.nth(i).evaluate("el => el.type")
                placeholder = other.nth(i).evaluate("el => el.placeholder")
                print(f"    [{i}] type={inp_type}, placeholder={placeholder}")
            if count2 > 0:
                other.first.fill("dstest,Aa@11223344")
        
        page.wait_for_timeout(500)
        take_screenshot(page, "v3_07_password")
        
        # 8. 提交登录 - 点击submit按钮
        print("\n[8] 提交登录...")
        submit_btn = page.locator("button[type='submit']")
        if submit_btn.count() > 0:
            print(f"  找到 {submit_btn.count()} 个type=submit按钮")
            take_screenshot(page, "v3_08_before_submit")
            submit_btn.first.click()
            page.wait_for_timeout(5000)
        else:
            print("  未找到type=submit按钮，尝试其他方式")
            # 尝试按Enter
            pw_inputs = page.locator("input[type='password']")
            if pw_inputs.count() > 0:
                pw_inputs.first.press("Enter")
                page.wait_for_timeout(5000)
        
        take_screenshot(page, "v3_08_after_submit")
        print(f"  URL: {page.url}")
        
        # 9. 检查网络请求
        print("\n[9] 网络请求记录:")
        for url in login_requests:
            print(f"    {url}")
        
        # 10. 最终状态
        body_text = page.locator("body").inner_text()[:500]
        print(f"\n[10] 最终页面文本:\n{body_text}")
        take_screenshot(page, "v3_10_final")
        
        browser.close()

if __name__ == "__main__":
    main()
