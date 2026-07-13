"""手动登录 Agent Market - 第4次尝试，详细诊断"""
from playwright.sync_api import sync_playwright
import os

SCREENSHOTS_DIR = "/home/node/.openclaw/workspace/work/agent-market/screenshots"
os.makedirs(SCREENSHOTS_DIR, exist_ok=True)

def take_ss(page, name):
    path = os.path.join(SCREENSHOTS_DIR, f"{name}.png")
    page.screenshot(path=path, full_page=False)
    print(f"  [ss] {name}")
    return path

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        )
        context = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = context.new_page()

        # Capture ALL network requests
        reqs = []
        page.on("request", lambda r: reqs.append(r.url))
        page.on("response", lambda r: reqs.append((r.status, r.url)))

        print("Agent Market 登录 - 诊断")

        # Step 1: Visit login
        print("\n[1] 访问登录页...")
        page.goto("https://agent.digitalchina.com/login", wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(3000)
        take_ss(page, "v4_01_login")

        # Step 2: Dump all input details
        print("\n[2] 输入框详情:")
        inputs_raw = page.evaluate("document.querySelectorAll('input').forEach(el => console.log(el.type + '|' + el.name + '|' + el.placeholder + '|' + el.id + '|' + el.className))")
        # Use querySelectorAll via eval
        inputs_info = page.evaluate("""
            Array.from(document.querySelectorAll('input')).map(function(el) {
                return el.type + '|' + el.name + '|' + el.placeholder + '|' + el.id + '|' + el.className;
            })
        """)
        for i, info in enumerate(inputs_info):
            print(f"    [{i}] {info}")
        take_ss(page, "v4_02_inputs")

        # Step 3: Dump all button details
        print("\n[3] 按钮详情:")
        btns_info = page.evaluate("""
            Array.from(document.querySelectorAll('button')).map(function(el) {
                return el.innerText.substring(0,30) + '|' + el.type + '|' + el.className;
            })
        """)
        for i, info in enumerate(btns_info):
            print(f"    [{i}] {info}")
        take_ss(page, "v4_03_buttons")

        # Step 4: Dump all form details
        print("\n[4] Form详情:")
        forms_info = page.evaluate("""
            Array.from(document.querySelectorAll('form')).map(function(el) {
                return el.action + '|' + el.method + '|' + el.id + '|' + el.className;
            })
        """)
        for i, info in enumerate(forms_info):
            print(f"    [{i}] {info}")
        if not forms_info:
            print("    (无form元素)")
        take_ss(page, "v4_04_forms")

        # Step 5: Get visible text
        body_text = page.locator("body").inner_text()[:600]
        print("\n[5] 页面可见文本:")
        print(body_text)
        take_ss(page, "v4_05_text")

        # Step 6: Fill itcode
        print("\n[6] 填写itcode...")
        text_inputs = page.locator("input[type='text']")
        pw_inputs = page.locator("input[type='password']")
        print(f"    type=text: {text_inputs.count()}, type=password: {pw_inputs.count()}")
        
        text_inputs.first.fill("itcode")
        print("    已填写itcode")
        page.wait_for_timeout(500)
        take_ss(page, "v4_06_itcode")

        # Step 7: Fill password
        print("\n[7] 填写密码...")
        if pw_inputs.count() > 0:
            pw_inputs.first.fill("dstest,Aa@11223344")
            print("    已填写密码（type=password）")
        else:
            # Find all non-text inputs
            all_inps = page.locator("input")
            for i in range(all_inps.count()):
                itype = all_inps.nth(i).evaluate("el => el.type")
                ph = all_inps.nth(i).evaluate("el => el.placeholder")
                if itype != "text" and itype != "hidden":
                    print(f"    输入框[{i}] type={itype} placeholder={ph}")
                    all_inps.nth(i).fill("dstest,Aa@11223344")
        page.wait_for_timeout(500)
        take_ss(page, "v4_07_pw")

        # Step 8: Submit
        print("\n[8] 提交登录...")
        reqs_before = len(reqs)
        submit_btn = page.locator("button[type='submit']")
        if submit_btn.count() > 0:
            print(f"    找到 {submit_btn.count()} 个type=submit按钮")
            take_ss(page, "v4_08_before_submit")
            submit_btn.first.click()
        else:
            print("    无type=submit按钮，尝试Enter键")
            if pw_inputs.count() > 0:
                pw_inputs.first.press("Enter")
        page.wait_for_timeout(8000)

        take_ss(page, "v4_08_after_submit")
        print(f"    URL: {page.url}")
        print(f"    标题: {page.title()}")

        # Step 9: Network requests analysis
        print("\n[9] 登录前后网络请求:")
        for r in reqs:
            print(f"    {r}")

        # Step 10: Page text after submit
        body_after = page.locator("body").inner_text()[:600]
        print(f"\n[10] 提交后页面文本:")
        print(body_after)
        take_ss(page, "v4_10_final")

        # Check for error messages
        if "退出登录" in body_after or "重新登录" in body_after:
            print("\n    !!! 登录失败 - 页面显示退出/重新登录")
        elif "agent-market" in page.url:
            print("\n    !!! 登录成功 - 已跳转到agent-market")

        browser.close()

if __name__ == "__main__":
    main()
