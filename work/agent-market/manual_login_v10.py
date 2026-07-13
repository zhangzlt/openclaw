"""保存token并测试点击智能体触发扫码"""
from playwright.sync_api import sync_playwright
import os, json, base64

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

        # Intercept login API
        page.on("response", lambda resp: (
            login_responses.append({"status": resp.status, "body": resp.text(), "url": resp.url, "headers": dict(resp.headers)})
            if "api/user/login" in resp.url else None
        ))

        print("Agent Market - 登录并测试扫码")

        # Step 1: Login
        print("\n[1] 登录...")
        page.goto("https://agent.digitalchina.com/login", wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(3000)
        take_ss(page, "v10_01")

        page.locator("input[type='text']").first.fill("dstest")
        page.locator("input[type='password']").first.fill("Aa@11223344")
        page.wait_for_timeout(500)
        take_ss(page, "v10_02")

        # Submit and capture login response
        login_resp = None
        page.on("response", lambda resp: (
            (setattr(__builtins__, '_login_resp', resp) if "api/user/login" in resp.url else None)
        ))
        
        page.locator("button[type='submit']").first.click()
        page.wait_for_timeout(5000)
        take_ss(page, "v10_03")

        print(f"\n[2] 登录状态:")
        body_text = page.locator("body").inner_text()
        print(f"    URL: {page.url}")
        print(f"    文本（前200字）: {body_text[:200]}")

        if "退出登录" in body_text or "重新登录" in body_text:
            print(f"    ⚠️ 登录失败")
            return

        print(f"    ✅ 登录成功!")

        # Step 3: Get token from localStorage
        print(f"\n[3] 获取token...")
        token_info = page.evaluate("""
            () => {
                var ls = {};
                for (var i = 0; i < localStorage.length; i++) {
                    var k = localStorage.key(i);
                    var v = localStorage.getItem(k);
                    ls[k] = v.substring(0, 500);
                }
                return ls;
            }
        """)
        
        # Find token
        token = None
        for k, v in token_info.items():
            if 'token' in k.lower() or k == 'Authorization' or k == 'auth_token':
                token = v
                print(f"    找到token: {k}")
                break
        
        # Also try the eval method from v9
        try:
            lr = eval("_login_resp") if '_login_resp' in dir(__builtins__) else None
            if lr:
                resp_body = lr.text()
                try:
                    data = json.loads(resp_body)
                    if data.get('success') and data.get('data') and data['data'].get('token'):
                        token = data['data']['token']
                        print(f"    从API响应获取token: {token[:50]}...")
                except:
                    pass
        except:
            pass

        if token:
            # Save token for later use
            token_path = "/home/node/.openclaw/workspace/work/agent-market/.auth/session.json"
            token_data = {
                "token": token,
                "username": "dstest",
                "url": "https://agent.digitalchina.com"
            }
            with open(token_path, 'w') as f:
                json.dump(token_data, f, ensure_ascii=False, indent=2)
            print(f"    ✅ Token已保存: {token_path}")
        else:
            print(f"    ❌ 未找到token")
            # Dump all localStorage keys
            print(f"    localStorage keys: {list(token_info.keys())}")

        # Step 4: Navigate to market and find agents
        print(f"\n[4] 导航到智能体市场...")
        page.goto("https://agent.digitalchina.com/market", wait_until="networkidle", timeout=15000)
        page.wait_for_timeout(5000)
        take_ss(page, "v10_04")

        # Step 5: Analyze agent cards
        print(f"\n[5] 查找智能体列表...")
        
        # Get all card-like elements
        cards_html = page.evaluate("""
            () => {
                var allEls = document.querySelectorAll('[class*="agent"], [class*="card"], [class*="item"], [class*="grid"]');
                var results = [];
                allEls.forEach(function(el) {
                    var text = el.innerText.trim();
                    if (text && text.length > 5 && text.length < 500 && el.offsetParent !== null) {
                        results.push({
                            tag: el.tagName,
                            class: el.className.substring(0, 80),
                            text: text.substring(0, 200),
                            hasLink: el.querySelector('a') !== null
                        });
                    }
                });
                return results.slice(0, 20);
            }
        """)
        
        print(f"  找到 {len(cards_html)} 个候选元素:")
        for i, card in enumerate(cards_html[:10]):
            print(f"    [{i}] <{card['tag']}> class={card['class'][:50]}")
            print(f"        链接: {card['hasLink']}")
            print(f"        文本: {card['text'][:100]}")

        # Step 6: Find clickable agent cards
        print(f"\n[6] 查找可点击的智能体...")
        
        # Try to find elements that have agent names and are clickable
        agent_cards = page.evaluate("""
            () => {
                var results = [];
                var allElements = document.querySelectorAll('div, li, section, article');
                allElements.forEach(function(el) {
                    var text = el.innerText.trim();
                    if (text && text.length > 3 && text.length < 100) {
                        var hasClick = el.onclick !== null || 
                                      el.getAttribute('role') === 'button' ||
                                      el.classList.contains('cursor-pointer') ||
                                      el.classList.contains('clickable') ||
                                      el.querySelector('a[href]') !== null ||
                                      el.querySelector('a[class*="agent"]') !== null;
                        
                        if (hasClick && !text.match(/^(所有分类|全部技能|全部来源|推荐|最新|热门|评分|点赞|已收藏|智能体市场|个人发布|建议反馈|收起|打开|AI|市场)$/) && 
                            text.match(/[一-龥]/)) {
                            var links = el.querySelectorAll('a');
                            var hrefs = Array.from(links).map(l => l.href);
                            results.push({
                                text: text,
                                tag: el.tagName,
                                class: el.className.substring(0, 60),
                                hrefs: hrefs.slice(0, 5)
                            });
                        }
                    }
                });
                return results.slice(0, 15);
            }
        """)
        
        print(f"  找到 {len(agent_cards)} 个可点击的智能体:")
        for i, card in enumerate(agent_cards[:5]):
            print(f"    [{i}] '{card['text'][:50]}'")
            print(f"        hrefs: {card['hrefs']}")
            print(f"        class: {card['class']}")

        # Step 7: Click first agent
        if agent_cards:
            first_agent = agent_cards[0]
            print(f"\n[7] 点击第一个智能体: {first_agent['text'][:50]}")
            
            # Find the element and click
            for link_href in first_agent['hrefs']:
                if link_href and 'chat' in link_href:
                    try:
                        link = page.locator(f"a[href*='{link_href.split('/')[-1] if '/' in link_href else link_href}']").first
                        if link.is_visible(timeout=2000):
                            link.click()
                            page.wait_for_timeout(5000)
                            take_ss(page, "v10_07_chat")
                            print(f"    URL: {page.url}")
                            
                            # Check for QR code
                            body_after = page.locator("body").inner_text()
                            if "扫码" in body_after or "二维码" in body_after or "qrcode" in body_after.lower() or "QR" in body_after:
                                print(f"    ⚡ 检测到扫码登录提示!")
                                take_ss(page, "v10_08_qr")
                            else:
                                print(f"    页面文本前300字:\n    {body_after[:300]}")
                            break
                    except Exception as e:
                        print(f"    点击失败: {e}")
                        continue
            
            # If no chat link found, try clicking by text
            if not first_agent['hrefs'] or not any('chat' in h for h in first_agent['hrefs']):
                print(f"    尝试通过文字点击...")
                try:
                    # Find a link containing the agent name
                    link = page.locator(f"a:has-text('{first_agent['text'][:20]}')").first
                    if link.is_visible(timeout=2000):
                        link.click()
                        page.wait_for_timeout(5000)
                        take_ss(page, "v10_07_chat")
                        print(f"    URL: {page.url}")
                        body_after = page.locator("body").inner_text()
                        if "扫码" in body_after or "二维码" in body_after:
                            print(f"    ⚡ 检测到扫码登录提示!")
                            take_ss(page, "v10_08_qr")
                        else:
                            print(f"    页面文本前300字:\n    {body_after[:300]}")
                except Exception as e:
                    print(f"    文字点击也失败: {e}")
        else:
            print(f"  未找到可点击的智能体")
            # Dump page structure for debugging
            print(f"\n[8] 页面结构分析...")
            page_structure = page.evaluate("""
                () => {
                    var navItems = [];
                    document.querySelectorAll('[class*="nav"], [class*="menu"], [class*="tab"]').forEach(function(el) {
                        navItems.push({
                            tag: el.tagName,
                            class: el.className.substring(0, 60),
                            text: el.innerText.trim().substring(0, 100)
                        });
                    });
                    return navItems;
                }
            """)
            for item in page_structure:
                print(f"    {item}")

        # Final screenshot
        take_ss(page, "v10_final")

        # Logout
        print(f"\n[9] 登出...")
        # Try to find logout button
        logout_selectors = [
            "text='退出'",
            "text='登出'",
            "text='Logout'",
            "text='Log out'",
            "text='注销'",
            "[class*='user'] button",
            "[class*='avatar'] button",
        ]
        
        logged_out = False
        for sel in logout_selectors:
            try:
                elem = page.locator(sel).first
                if elem.is_visible(timeout=2000):
                    elem.click()
                    page.wait_for_timeout(3000)
                    logged_out = True
                    print(f"    ✅ 已登出 (匹配选择器: {sel})")
                    break
            except:
                continue
        
        if not logged_out:
            # Try clicking the user avatar/name area
            try:
                user_area = page.locator("text='dstest'").first
                if user_area.is_visible(timeout=2000):
                    user_area.click()
                    page.wait_for_timeout(2000)
                    # Click logout from dropdown
                    logout_btn = page.locator("text='退出'").first
                    if logout_btn.is_visible(timeout=2000):
                        logout_btn.click()
                        page.wait_for_timeout(3000)
                        logged_out = True
                        print(f"    ✅ 已登出 (通过用户菜单)")
            except Exception as e:
                print(f"    ❌ 登出失败: {e}")

        take_ss(page, "v10_logout")

        # Save auth state for playwright
        print(f"\n[10] 保存 Playwright 认证状态...")
        auth_state = context.storage_state()
        state_path = "/home/node/.openclaw/workspace/work/agent-market/.auth/playwright_state.json"
        with open(state_path, 'w') as f:
            json.dump(auth_state, f, ensure_ascii=False, indent=2)
        print(f"    ✅ 认证状态已保存: {state_path}")
        cookies = context.cookies()
        print(f"    Cookies ({len(cookies)} 个):")
        for c in cookies:
            print(f"      {c['name']} = {c['value'][:30]}... domain={c['domain']}")

        browser.close()
        print(f"\n  完成!")

if __name__ == "__main__":
    main()
