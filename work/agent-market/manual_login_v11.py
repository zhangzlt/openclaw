"""v11: 使用保存的token登录并点击智能体"""
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
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        )
        context = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = context.new_page()

        # Login first (clean session)
        print("[1] 登录...")
        page.goto("https://agent.digitalchina.com/login", wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(3000)
        take_ss(page, "v11_01")
        
        page.locator("input[type='text']").first.fill("dstest")
        page.locator("input[type='password']").first.fill("Aa@11223344")
        page.wait_for_timeout(500)
        
        token = None
        api_resp = None
        def capture_resp(resp):
            nonlocal api_resp
            if "/api/user/login" in resp.url:
                api_resp = resp.text()
        
        page.on("response", capture_resp)
        page.locator("button[type='submit']").first.click()
        page.wait_for_timeout(5000)
        take_ss(page, "v11_02")
        
        # Extract token
        if api_resp:
            data = json.loads(api_resp)
            if data.get("success") and data.get("data") and data["data"].get("token"):
                token = data["data"]["token"]
                print(f"    Token: {token[:50]}...")
        
        # Go to market
        print("[2] 导航到市场...")
        page.goto("https://agent.digitalchina.com/market", wait_until="networkidle", timeout=15000)
        page.wait_for_timeout(5000)
        take_ss(page, "v11_03")
        
        # Save auth state
        state = context.storage_state()
        with open("/home/node/.openclaw/workspace/work/agent-market/.auth/playwright_state.json", "w") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        print("    认证状态已保存")

        # Analyze DOM for agents
        print("[3] 分析页面结构...")
        agent_info = page.evaluate("""
            () => {
                var results = [];
                // Find agent cards by looking for elements with agent names
                var allElements = document.querySelectorAll('[class]');
                for (var i = 0; i < allElements.length; i++) {
                    var el = allElements[i];
                    var text = el.innerText.trim();
                    // Agent name patterns: contains Chinese chars, short text, not nav/header
                    if (text && text.length > 1 && text.length < 30 && text.match(/[一-龥]/)) {
                        var skip = /市场|发布|反馈|分类|推荐|最新|热门|评分|点赞|收藏|收起|全部|打开|关闭|×/.test(text);
                        if (!skip && el.offsetParent !== null) {
                            // Check if this is inside a card container
                            var card = el.closest('[class*="card"]');
                            var links = el.querySelectorAll('a');
                            results.push({
                                text: text,
                                class: el.className.substring(0, 60),
                                tag: el.tagName,
                                cardClass: card ? card.className.substring(0, 60) : '',
                                hasLink: links.length > 0,
                                linkHrefs: Array.from(links).map(l => l.href || l.getAttribute('href') || '').filter(Boolean).slice(0,2)
                            });
                        }
                    }
                }
                // Deduplicate by text
                var seen = {};
                var deduped = [];
                for (var i = 0; i < results.length; i++) {
                    if (!seen[results[i].text]) {
                        seen[results[i].text] = true;
                        deduped.push(results[i]);
                    }
                }
                return deduped.slice(0, 10);
            }
        """)
        
        print(f"  找到 {len(agent_info)} 个候选:")
        for i, a in enumerate(agent_info):
            print(f"    [{i}] '{a['text']}' class={a['class'][:50]} linkHrefs={a['linkHrefs']}")

        # Click first agent
        if agent_info:
            agent = agent_info[0]
            print(f"\n[4] 点击第一个智能体: '{agent['text']}'")
            
            clicked = False
            try:
                # Try to find the card parent and click it
                card = page.locator(f"[class*='card-top']:has-text('{agent['text']}')").first
                if card.is_visible(timeout=3000):
                    print(f"    通过card-top找到")
                    card.click()
                    clicked = True
            except:
                pass
            
            if not clicked:
                try:
                    card = page.locator(f"[class*='agent-card']:has-text('{agent['text']}')").first
                    if card.is_visible(timeout=3000):
                        print(f"    通过agent-card找到")
                        card.click()
                        clicked = True
                except:
                    pass
            
            if not clicked:
                try:
                    card = page.locator(f"text='{agent['text']}'").first
                    if card.is_visible(timeout=3000):
                        print(f"    通过文字找到")
                        card.click()
                        clicked = True
                except:
                    pass
            
            if not clicked:
                try:
                    # JS click on the element
                    result = page.evaluate("""
                        (name) => {
                            var divs = document.querySelectorAll('div');
                            for (var i = 0; i < divs.length; i++) {
                                if (divs[i].innerText.indexOf(name) !== -1) {
                                    var ev = new MouseEvent('click', {bubbles: true, cancelable: true});
                                    divs[i].dispatchEvent(ev);
                                    return 'clicked div';
                                }
                            }
                            return 'not found';
                        }
                    """, agent['text'])
                    print(f"    JS点击结果: {result}")
                except Exception as e:
                    print(f"    JS点击失败: {e}")
            
            take_ss(page, "v11_04")
            print(f"    URL: {page.url}")
            
            body_after = page.locator("body").inner_text()
            print(f"\n[5] 点击后页面状态:")
            print(f"    文本前500字:\n{body_after[:500]}")
            take_ss(page, "v11_05")
            
            # Check for QR code
            if "扫码" in body_after or "二维码" in body_after or "qrcode" in body_after.lower() or "QR" in body_after:
                print(f"\n    ⚡ 检测到扫码登录!")
                take_ss(page, "v11_06_qr")
            elif "退出登录" in body_after or "重新登录" in body_after:
                print(f"\n    ⚠️ 页面提示重新登录")
            elif "chat" in page.url.lower():
                print(f"\n    ✅ 进入了智能体对话页!")
        else:
            print(f"  未找到智能体")
            links = page.locator("a").all()
            print(f"\n  页面所有 {len(links)} 个链接:")
            for i, link in enumerate(links):
                try:
                    if link.is_visible():
                        text = link.inner_text().strip()
                        href = link.get_attribute("href")
                        if text and len(text) > 1:
                            print(f"    [{i}] '{text}' -> {href}")
                except:
                    pass

        # Final auth state
        state = context.storage_state()
        with open("/home/node/.openclaw/workspace/work/agent-market/.auth/playwright_state.json", "w") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        
        browser.close()
        print(f"\n  完成!")

if __name__ == "__main__":
    main()
