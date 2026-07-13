"""手动登录 Agent Market - 重新测试"""
from playwright.sync_api import sync_playwright
import os

SCREENSHOTS_DIR = "/home/node/.openclaw/workspace/work/agent-market/screenshots"
os.makedirs(SCREENSHOTS_DIR, exist_ok=True)

def take_screenshot(page, name):
    path = os.path.join(SCREENSHOTS_DIR, f"{name}.png")
    page.screenshot(path=path, full_page=False)
    print(f"  📸 截图: {name}")
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
        
        # 拦截网络请求
        responses = {}
        page.on("response", lambda resp: responses.put_nowait(resp))
        import asyncio
        responses = asyncio.Queue()
        page.on("response", lambda resp: asyncio.get_event_loop().create_task(responses.put(resp)))
        
        print("=" * 60)
        print("Agent Market 登录流程 - 第2次尝试")
        print("=" * 60)
        
        # 1. 访问登录页
        print("\n[1] 访问登录页...")
        page.goto("https://agent.digitalchina.com/login", wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(2000)
        take_screenshot(page, "01_login_page")
        
        # 2. 截图所有可见文本
        print("\n[2] 分析页面结构...")
        body_text = page.locator("body").inner_text()
        print(f"  页面文本:\n{body_text}")
        take_screenshot(page, "02_page_structure")
        
        # 3. 尝试点击"账号密码"tab
        print("\n[3] 查找tab切换...")
        tab_selectors = [
            "text='账号密码'",
            "text='手机号登录'",
            "[class*='tab'] button",
            "[class*='tab'] a",
            "[class*='tab']",
            "div[class*='tab']",
            "li[class*='tab']",
        ]
        
        tab_found = False
        for sel in tab_selectors:
            try:
                elem = page.locator(sel).first
                if elem.is_visible(timeout=2000):
                    text = elem.inner_text()[:30]
                    print(f"  找到tab元素: {sel} -> '{text}'")
                    elem.click()
                    tab_found = True
                    page.wait_for_timeout(1000)
                    break
            except Exception as e:
                print(f"  {sel}: {e}")
        
        if not tab_found:
            print("  未找到tab元素，继续...")
        
        take_screenshot(page, "03_after_tab")
        
        # 4. 填写 itcode
        print("\n[4] 填写 itcode...")
        itcode_selectors = [
            "input[placeholder*='itcode']",
            "input[placeholder*='请输入itcode']",
            "input[type='text']",
            "input[placeholder*='请输入']",
            "input[placeholder*='邮箱']",
        ]
        
        itcode_input = None
        for sel in itcode_selectors:
            try:
                elem = page.locator(sel).first
                if elem.is_visible(timeout=2000):
                    print(f"  找到itcode输入框: {sel}")
                    elem.click()
                    elem.fill("itcode")
                    itcode_input = elem
                    break
            except:
                pass
        
        if not itcode_input:
            print("  ❌ 未找到输入框")
            return
        
        page.wait_for_timeout(500)
        take_screenshot(page, "04_itcode_filled")
        
        # 5. 填写密码
        print("\n[5] 填写密码...")
        password_selectors = [
            "input[placeholder*='密码']",
            "input[name='password']",
            "input[type='password']",
        ]
        
        password_input = None
        for sel in password_selectors:
            try:
                elem = page.locator(sel).first
                if elem.is_visible(timeout=2000):
                    print(f"  找到密码输入框: {sel}")
                    elem.click()
                    elem.fill("dstest,Aa@11223344")
                    password_input = elem
                    break
            except:
                pass
        
        if not password_input:
            print("  ❌ 未找到密码框")
            return
        
        page.wait_for_timeout(500)
        take_screenshot(page, "05_password_filled")
        
        # 6. 获取登录按钮
        print("\n[6] 查找登录按钮...")
        btn = page.locator("button[type='submit']").first
        if not btn.is_visible(timeout=2000):
            btn = page.locator("button:has-text('登 录'), button:has-text('登录'), button:has-text('登 录')").first
        if not btn.is_visible(timeout=2000):
            btn = page.locator("button:has-text('登'), button:has-text('登录')").first
        
        if btn.is_visible():
            print(f"  找到登录按钮")
            take_screenshot(page, "06_before_submit")
            
            # 7. 使用更可靠的方式提交 - 通过键盘Enter
            print("\n[7] 通过 Enter 键提交...")
            password_input.press("Enter")
            page.wait_for_timeout(5000)
        else:
            print("  ❌ 未找到按钮")
            take_screenshot(page, "06_no_button")
        
        take_screenshot(page, "07_after_enter")
        print(f"  URL: {page.url}")
        
        # 检查页面状态
        body_text = page.locator("body").inner_text()
        print(f"\n[8] 页面文本:\n{body_text[:500]}")
        
        if "退出登录" in body_text or "重新登录" in body_text:
            print("  ⚠️ 登录失败！页面显示退出/重新登录")
        elif "agent-market" in page.url or "Agent" in page.title():
            print("  ✅ 登录成功！")
        else:
            print("  ? 登录状态不确定")
        
        # 8. 导航到 agent-market
        print("\n[9] 导航到 agent-market...")
        page.goto("https://agent.digitalchina.com/agent-market", wait_until="networkidle", timeout=15000)
        page.wait_for_timeout(3000)
        take_screenshot(page, "09_agent_market")
        print(f"  URL: {page.url}")
        
        # 9. 分析页面
        body_text = page.locator("body").inner_text()
        print(f"\n[10] agent-market 页面文本:\n{body_text[:800]}")
        take_screenshot(page, "10_market_analysis")
        
        # 10. 找到并点击第一个智能体
        print("\n[11] 查找智能体列表...")
        
        # 尝试获取所有可点击的元素
        all_links = page.locator("a").all()
        print(f"  找到 {len(all_links)} 个链接")
        
        for i, link in enumerate(all_links[:20]):
            try:
                if link.is_visible():
                    text = link.inner_text().strip()[:50]
                    href = link.get_attribute("href") or ""
                    if text and len(text) > 1:
                        print(f"  [{i}] '{text}' -> {href}")
            except:
                pass
        
        # 尝试查找所有卡片/列表项
        print("\n  查找卡片/列表元素...")
        card_selectors = [
            "[class*='card']",
            "[class*='item']",
            "[class*='list']",
            "[class*='agent']",
            "[class*='grid']",
            "[class*='row']",
            "div[class*='agent']",
        ]
        
        for sel in card_selectors[:5]:
            try:
                elems = page.locator(sel)
                count = elems.count()
                if count > 0:
                    print(f"  {sel}: {count} 个元素")
                    for j in range(min(count, 3)):
                        try:
                            text = elems.nth(j).inner_text()[:50]
                            visible = elems.nth(j).is_visible(timeout=1000)
                            print(f"    [{j}] '{text}' visible={visible}")
                        except:
                            pass
            except Exception as e:
                print(f"  {sel}: {e}")
        
        # 最终截图
        take_screenshot(page, "11_final")
        
        browser.close()
        print("\n" + "=" * 60)
        print("完成！")
        print("=" * 60)

if __name__ == "__main__":
    main()
