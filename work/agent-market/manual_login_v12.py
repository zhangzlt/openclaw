"""v12: 点击智能体的'打开'按钮"""
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

        # Login
        print("[1] 登录...")
        page.goto("https://agent.digitalchina.com/login", wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(3000)
        take_ss(page, "v12_01")

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
        take_ss(page, "v12_02")

        if api_resp:
            data = json.loads(api_resp)
            if data.get("success") and data.get("data") and data["data"].get("token"):
                token = data["data"]["token"]
                print(f"    Token: {token[:50]}...")

        # Go to market
        print("[2] 导航到市场...")
        page.goto("https://agent.digitalchina.com/market", wait_until="networkidle", timeout=15000)
        page.wait_for_timeout(5000)
        take_ss(page, "v12_03")

        # Save auth
        with open("/home/node/.openclaw/workspace/work/agent-market/.auth/playwright_state.json", "w") as f:
            json.dump(context.storage_state(), f, ensure_ascii=False, indent=2)

        # Analyze the card structure in detail
        print("[3] 分析智能体卡片DOM...")
        card_info = page.evaluate("""
            () => {
                var cards = [];
                var agentCards = document.querySelectorAll('.agent-card');
                for (var i = 0; i < agentCards.length; i++) {
                    var card = agentCards[i];
                    var name = card.querySelector('[class*="name"]') ? card.querySelector('[class*="name"]').innerText.trim() : '';
                    var author = card.querySelector('[class*="author"]') ? card.querySelector('[class*="author"]').innerText.trim() : '';
                    var openBtn = card.querySelector('[class*="open"], [class*="action"], [class*="bottom"]');
                    
                    // Find all buttons/actions
                    var actions = card.querySelectorAll('[class*="action"], [class*="bottom"], [class*="stat"]');
                    var actionTexts = Array.from(actions).map(a => a.innerText.trim());
                    
                    cards.push({
                        name: name,
                        author: author,
                        index: i,
                        actionTexts: actionTexts,
                        className: card.className.substring(0, 80),
                        innerHTML: card.innerHTML.substring(0, 500)
                    });
                }
                return cards;
            }
        """)
        
        print(f"  找到 {len(card_info)} 个agent-card:")
        for c in card_info:
            print(f"    [{c['index']}] {c['name']} by {c['author']}")
            print(f"        actions: {c['actionTexts']}")

        # Try clicking "打开" button on first agent
        if card_info:
            agent = card_info[0]
            print(f"\n[4] 尝试点击 {agent['name']} 的'打开'按钮...")
            
            # Strategy 1: Find the card and click elements containing "打开"
            agent_card = page.locator(f"[class*='agent-card']").filter(has_text=agent['name']).first
            try:
                if agent_card.is_visible(timeout=3000):
                    print(f"    找到agent-card")
                    # Try clicking "打开" text within this card
                    open_btn = agent_card.locator("text='打开'").first
                    if open_btn.is_visible(timeout=2000):
                        print(f"    找到'打开'按钮")
                        open_btn.click()
                        page.wait_for_timeout(8000)
                        take_ss(page, "v12_04_open_clicked")
                        print(f"    URL: {page.url}")
                        
                        body_after = page.locator("body").inner_text()
                        print(f"\n[5] 点击后状态:")
                        print(f"    URL: {page.url}")
                        print(f"    文本前500字:\n{body_after[:500]}")
                        take_ss(page, "v12_05")
                        
                        if "扫码" in body_after or "二维码" in body_after or "qrcode" in body_after.lower():
                            print(f"\n    ⚡ 检测到扫码登录!")
                            take_ss(page, "v12_06_qr")
                        elif "退出登录" in body_after:
                            print(f"\n    ⚠️ 页面提示重新登录")
                        elif page.url != "https://agent.digitalchina.com/market":
                            print(f"\n    ✅ 页面跳转了!")
                    else:
                        print(f"    '打开'按钮不可见")
            except Exception as e:
                print(f"    策略1失败: {e}")
            
            # Strategy 2: Find "aily" button and click it
            if "v12_04_open_clicked" not in [f for f in os.listdir(SCREENSHOTS_DIR) if f.startswith("v12_")]:
                print(f"\n[4b] 尝试点击 'aily' 按钮...")
                try:
                    agent_card = page.locator(f"[class*='agent-card']").filter(has_text=agent['name']).first
                    aily_btn = agent_card.locator("text='aily'").first
                    if aily_btn.is_visible(timeout=2000):
                        print(f"    找到'aily'按钮")
                        aily_btn.click()
                        page.wait_for_timeout(8000)
                        take_ss(page, "v12_04b_aily_clicked")
                        print(f"    URL: {page.url}")
                        body_after = page.locator("body").inner_text()
                        print(f"    文本前500字:\n{body_after[:500]}")
                        take_ss(page, "v12_04b")
                        
                        if "扫码" in body_after or "二维码" in body_after:
                            print(f"\n    ⚡ 检测到扫码登录!")
                            take_ss(page, "v12_06_qr")
                except Exception as e:
                    print(f"    aily点击失败: {e}")
            
            # Strategy 3: Use API to find agent detail URL
            print(f"\n[4c] 通过API查找智能体详情...")
            try:
                # Try the widget/track API or direct agent API
                detail = page.evaluate("""
                    async () => {
                        // Try the widget open API
                        try {
                            var resp = await fetch('/widget/open?agentId=46');
                            var html = await resp.text();
                            return { status: resp.status, length: html.length, preview: html.substring(0, 500) };
                        } catch(e) {
                            return { error: e.message };
                        }
                    }
                """)
                print(f"    widget/open: {json.dumps(detail, ensure_ascii=False)[:300]}")
                
                # Try the chat API
                chat_resp = page.evaluate("""
                    async () => {
                        try {
                            var resp = await fetch('/api/agent/list');
                            var data = await resp.json();
                            return data;
                        } catch(e) {
                            return { error: e.message };
                        }
                    }
                """)
                print(f"    api/agent/list: {json.dumps(chat_resp, ensure_ascii=False)[:300]}")
            except Exception as e:
                print(f"    API查找失败: {e}")
                
            # Strategy 4: Click the card itself (not just text)
            print(f"\n[4d] 直接点击agent-card容器...")
            try:
                agent_card = page.locator(f"[class*='agent-card']").filter(has_text=agent['name']).first
                if agent_card.is_visible(timeout=2000):
                    box = agent_card.bounding_box()
                    if box:
                        # Click center of the card
                        page.mouse.click(box['x'] + box['width']/2, box['y'] + box['height']/2)
                        page.wait_for_timeout(5000)
                        take_ss(page, "v12_04d_click")
                        print(f"    URL: {page.url}")
                        body_after = page.locator("body").inner_text()
                        print(f"    文本前300字:\n{body_after[:300]}")
                        take_ss(page, "v12_04d")
            except Exception as e:
                print(f"    点击容器失败: {e}")
            
            # Final screenshot
            take_ss(page, "v12_final")
        else:
            print("  未找到agent-card元素")
            take_ss(page, "v12_no_cards")

        # Save auth
        with open("/home/node/.openclaw/workspace/work/agent-market/.auth/playwright_state.json", "w") as f:
            json.dump(context.storage_state(), f, ensure_ascii=False, indent=2)
        
        browser.close()
        print(f"\n  完成! 截图: {sorted([f for f in os.listdir(SCREENSHOTS_DIR) if f.startswith('v12')])}")

if __name__ == "__main__":
    main()
