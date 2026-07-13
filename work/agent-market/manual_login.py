"""手动登录 Agent Market - 实际操作流程"""
from playwright.sync_api import sync_playwright
import os
from datetime import datetime

SCREENSHOTS_DIR = "/home/node/.openclaw/workspace/work/agent-market/screenshots"
os.makedirs(SCREENSHOTS_DIR, exist_ok=True)

def take_screenshot(page, name):
    path = os.path.join(SCREENSHOTS_DIR, f"{name}.png")
    page.screenshot(path=path, full_page=False)
    print(f"  📸 截图已保存: {path}")
    return path

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        )
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()
        
        print("=" * 60)
        print("Agent Market 手动登录流程")
        print("=" * 60)
        
        # 1. 访问登录页
        print("\n[1] 访问登录页...")
        page.goto("https://agent.digitalchina.com/login", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)
        path1 = take_screenshot(page, "01_login_page")
        print(f"  URL: {page.url}")
        print(f"  标题: {page.title()}")
        
        # 2. 找到并填写 itcode 输入框
        print("\n[2] 填写 itcode...")
        itcode_selectors = [
            "input[placeholder*='itcode']",
            "input[placeholder*='请输入itcode']",
            "input[type='text']",
            "input[placeholder*='请输入']",
            "input[placeholder*='邮箱']",
            "input[name='email']",
        ]
        
        itcode_filled = False
        for sel in itcode_selectors:
            try:
                elem = page.locator(sel).first
                if elem.is_visible(timeout=2000):
                    print(f"  找到 itcode 输入框: {sel}")
                    elem.click()
                    elem.fill("itcode")
                    itcode_filled = True
                    break
            except Exception as e:
                print(f"  {sel}: 不可见 - {e}")
        
        if not itcode_filled:
            print("  ❌ 未找到 itcode 输入框！")
            body_text = page.locator("body").inner_text()[:500]
            print(f"  页面内容:\n{body_text}")
            take_screenshot(page, "02_input_not_found")
            return
        
        take_screenshot(page, "02_itcode_filled")
        
        # 3. 填写密码
        print("\n[3] 填写密码...")
        password_selectors = [
            "input[placeholder*='密码']",
            "input[name='password']",
            "input[type='password']",
        ]
        
        password_filled = False
        for sel in password_selectors:
            try:
                elem = page.locator(sel).first
                if elem.is_visible(timeout=2000):
                    print(f"  找到密码输入框: {sel}")
                    elem.click()
                    elem.fill("dstest,Aa@11223344")
                    password_filled = True
                    break
            except Exception as e:
                print(f"  {sel}: 不可见 - {e}")
        
        if not password_filled:
            print("  ❌ 未找到密码输入框！")
            body_text = page.locator("body").inner_text()[:500]
            print(f"  页面内容:\n{body_text}")
            take_screenshot(page, "03_password_not_found")
            return
        
        take_screenshot(page, "03_password_filled")
        
        # 4. 点击登录按钮
        print("\n[4] 点击登录按钮...")
        submit_selectors = [
            "button[type='submit']",
            "input[type='submit']",
            "button:has-text('登录')",
            "button:has-text('登 录')",
            "[class*='login'] button",
            "[class*='login'] a",
            "button.login",
            ".login-btn",
        ]
        
        submit_clicked = False
        for sel in submit_selectors:
            try:
                elem = page.locator(sel).first
                if elem.is_visible(timeout=2000):
                    print(f"  找到登录按钮: {sel}")
                    elem.click()
                    submit_clicked = True
                    break
            except Exception as e:
                print(f"  {sel}: 不可见 - {e}")
        
        if not submit_clicked:
            print("  ❌ 未找到登录按钮！")
            body_text = page.locator("body").inner_text()[:500]
            print(f"  页面内容:\n{body_text}")
            take_screenshot(page, "04_submit_not_found")
            return
        
        take_screenshot(page, "04_login_clicked")
        
        # 5. 等待登录完成
        print("\n[5] 等待登录完成...")
        try:
            page.wait_for_url("**/agent-market**", timeout=15000)
            print("  ✅ 已跳转到 agent-market 页面")
        except Exception as e:
            print(f"  ⚠️ 未检测到跳转: {e}")
            print(f"  当前URL: {page.url}")
        
        page.wait_for_timeout(3000)
        path5 = take_screenshot(page, "05_after_login")
        print(f"  URL: {page.url}")
        print(f"  标题: {page.title()}")
        
        # 6. 导航到市场首页（如果不在）
        if "agent-market" not in page.url:
            print("\n[5b] 手动导航到市场首页...")
            try:
                page.goto("https://agent.digitalchina.com/agent-market", wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(3000)
            except Exception as e:
                print(f"  跳转失败: {e}")
            
            take_screenshot(page, "05b_market_home")
        
        # 7. 点击一个智能体
        print("\n[6] 点击一个智能体...")
        
        # 先获取页面可见文本
        body_text = page.locator("body").inner_text()
        print(f"  页面可见文本（前800字）:\n{body_text[:800]}")
        
        # 尝试多种选择器
        agent_selectors = [
            "[data-testid='agent-card']",
            "[class*='agent-card']",
            "[class*='agent-item']",
            "[class*='agent-list']",
            "[class*='card'] a",
            "a[href*='chat']",
            "a[href*='agent']",
            "[class*='list-item'] a",
            "[class*='card'] [class*='agent']",
            "[role='listitem'] a",
            "[class*='grid'] a",
        ]
        
        agent_clicked = False
        for sel in agent_selectors:
            try:
                elem = page.locator(sel).first
                if elem.is_visible(timeout=2000):
                    link_text = elem.inner_text()[:50]
                    print(f"  找到智能体元素: {sel} - '{link_text}'")
                    elem.click()
                    agent_clicked = True
                    take_screenshot(page, "06_agent_clicked")
                    page.wait_for_timeout(3000)
                    break
            except Exception as e:
                print(f"  {sel}: 不可见 - {e}")
        
        if not agent_clicked:
            print("  ❌ 未找到可点击的智能体元素")
            print("  尝试获取所有链接...")
            links = page.locator("a").all()
            for i, link in enumerate(links):
                try:
                    if link.is_visible():
                        text = link.inner_text()[:50]
                        href = link.get_attribute("href", "N/A")
                        print(f"  链接 {i}: '{text}' -> {href}")
                except:
                    pass
        
        # 8. 检查是否触发二维码
        print("\n[7] 检查是否触发二维码...")
        page.wait_for_timeout(2000)
        try:
            qr_selectors = [
                "[class*='qrcode']",
                "[class*='qr-code']",
                "[class*='qr_code']",
                "[class*='qrcode-img']",
                "img[src*='qrcode']",
                "img[src*='qr']",
                "[class*='QR']",
                "[class*='scancode']",
                "[class*='scan']",
            ]
            
            qr_found = False
            for sel in qr_selectors:
                try:
                    elem = page.locator(sel).first
                    if elem.is_visible(timeout=2000):
                        print(f"  ✅ 检测到二维码元素: {sel}")
                        take_screenshot(page, "07_qr_detected")
                        qr_found = True
                        break
                except:
                    pass
            
            if not qr_found:
                print("  未检测到二维码元素")
        except Exception as e:
            print(f"  二维码检查异常: {e}")
        
        # 最终截图
        take_screenshot(page, "08_final_state")
        print(f"  最终URL: {page.url}")
        print(f"  最终标题: {page.title()}")
        
        print("\n" + "=" * 60)
        print("操作流程完成！")
        print("截图已保存到:")
        for f in sorted(os.listdir(SCREENSHOTS_DIR)):
            if f.endswith('.png'):
                path = os.path.join(SCREENSHOTS_DIR, f)
                size = os.path.getsize(path)
                print(f"  - {f} ({size/1024:.1f}KB)")
        print("=" * 60)
        
        browser.close()

if __name__ == "__main__":
    main()
