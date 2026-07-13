"""v13: 深入分析智能体卡片点击逻辑"""
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
        take_ss(page, "v13_01")

        page.locator("input[type='text']").first.fill("dstest")
        page.locator("input[type='password']").first.fill("Aa@11223344")
        page.wait_for_timeout(500)

        api_events = []
        def capture_api(resp):
            url = resp.url
            if "/api/" in url or "/widget/" in url:
                try:
                    api_events.append({
                        "url": url,
                        "method": resp.request.method,
                        "status": resp.status,
                        "body_preview": resp.text()[:200],
                        "headers": dict(resp.headers)
                    })
                except:
                    api_events.append({"url": url, "method": resp.request.method, "status": resp.status})
        
        page.on("response", capture_api)

        page.locator("button[type='submit']").first.click()
        page.wait_for_timeout(5000)
        take_ss(page, "v13_02")

        # Go to market
        print("[2] 导航到市场...")
        page.goto("https://agent.digitalchina.com/market", wait_until="networkidle", timeout=15000)
        page.wait_for_timeout(5000)
        take_ss(page, "v13_03")

        # Deep DOM analysis of first card
        print("[3] 深度分析第一个agent-card DOM...")
        card_dom = page.evaluate("""
            () => {
                var card = document.querySelector('.agent-card');
                if (!card) return null;
                
                function walk(el, depth) {
                    if (depth > 3 || !el.offsetParent) return [];
                    var result = [];
                    var children = Array.from(el.children).filter(c => c.offsetParent !== null);
                    for (var i = 0; i < children.length; i++) {
                        var c = children[i];
                        var info = {
                            tag: c.tagName,
                            class: c.className.substring(0, 60),
                            text: c.innerText.trim().substring(0, 50),
                            attributes: {}
                        };
                        // Get all attributes
                        for (var j = 0; j < c.attributes.length; j++) {
                            var attr = c.attributes[j];
                            if (attr.value && attr.value.length < 200) {
                                info.attributes[attr.name] = attr.value;
                            }
                        }
                        info.children = walk(c, depth + 1);
                        result.push(info);
                    }
                    return result;
                }
                
                return walk(card, 0);
            }
        """)
        print(json.dumps(card_dom, ensure_ascii=False, indent=2)[:2000])

        # Try to find all click handlers on the card
        click_handlers = page.evaluate("""
            () => {
                var card = document.querySelector('.agent-card');
                if (!card) return [];
                
                // Get inline onclick
                var results = [];
                
                // Find all event listeners (if possible)
                function findHandlers(el, path) {
                    var result = {path: path, onclick: el.onclick !== null, hasEventListener: false};
                    // Check for data attributes that might indicate click behavior
                    var dataAttrs = {};
                    for (var i = 0; i < el.attributes.length; i++) {
                        var a = el.attributes[i];
                        if (a.name.startsWith('data-')) {
                            dataAttrs[a.name] = a.value.substring(0, 100);
                        }
                    }
                    result.dataAttributes = dataAttrs;
                    result.href = el.getAttribute('href') || '';
                    result.role = el.getAttribute('role') || '';
                    
                    // Try to find parent card container
                    result.cardId = el.closest('[data-agent-id]')?.getAttribute('data-agent-id') || 
                                   el.closest('[data-id]')?.getAttribute('data-id') || '';
                    
                    return result;
                }
                
                // Check the whole card
                return findHandlers(card, 'agent-card');
            }
        """)
        print(f"\n[4] 点击处理器分析: {json.dumps(click_handlers, ensure_ascii=False)}")

        # Strategy: Try clicking the "aily" button and monitor navigation
        print(f"\n[5] 尝试点击'aily'按钮并监控导航...")
        
        # First, let's also try clicking directly on the agent name
        name_elem = page.locator('[class*="card-name"]').first
        print(f"    card-name 元素: {name_elem.evaluate('e => e.outerHTML')[:200]}")
        
        # Try clicking on the agent name (DI问答助手)
        try:
            name_elem.click()
            page.wait_for_timeout(5000)
            take_ss(page, "v13_04a_name_clicked")
            print(f"    点击名称后 URL: {page.url}")
        except Exception as e:
            print(f"    点击名称失败: {e}")

        # Navigate back
        page.goto("https://agent.digitalchina.com/market", wait_until="networkidle", timeout=15000)
        page.wait_for_timeout(3000)
        
        # Try clicking "aily" on the first card
        aily_btn = page.locator('[class*="stat"] button, [class*="stat"] [class*="btn"]').first
        print(f"\n[6] 尝试点击 stat 区域...")
        stat_info = page.evaluate("""
            () => {
                var card = document.querySelector('.agent-card');
                var stat = card.querySelector('[class*="stat"]');
                if (!stat) return 'no stat found';
                return {
                    innerHTML: stat.innerHTML.substring(0, 300),
                    tagName: stat.tagName,
                    class: stat.className,
                    children: Array.from(stat.children).map(c => ({
                        tag: c.tagName,
                        class: c.className.substring(0, 40),
                        text: c.innerText.trim().substring(0, 30),
                        html: c.innerHTML.substring(0, 100)
                    }))
                };
            }
        """)
        print(f"    stat: {json.dumps(stat_info, ensure_ascii=False)}")

        # Try clicking directly on the stat area
        stat_elem = page.locator('[class*="stat"]')
        if stat_elem.is_visible(timeout=2000):
            box = stat_elem.bounding_box()
            if box:
                print(f"    stat 位置: x={box['x']}, y={box['y']}, w={box['width']}, h={box['height']}")
                # Click the specific "aily" button
                try:
                    page.mouse.click(box['x'] + box['width'] * 0.7, box['y'] + box['height'] * 0.7)
                    page.wait_for_timeout(5000)
                    take_ss(page, "v13_04b_stat_clicked")
                    print(f"    点击stat后 URL: {page.url}")
                except Exception as e:
                    print(f"    点击stat失败: {e}")

        # Navigate back and try the "打开" dropdown's actual content
        page.goto("https://agent.digitalchina.com/market", wait_until="networkidle", timeout=15000)
        page.wait_for_timeout(3000)
        
        # The dropdown might have a way to select the agent
        print(f"\n[7] 分析'打开'下拉选项...")
        dropdown_info = page.evaluate("""
            () => {
                var openBtn = document.querySelector('[class*="actions"] button:last-child, [class*="bottom"] button:last-child');
                if (!openBtn) {
                    var allBtns = document.querySelectorAll('button');
                    return {
                        totalButtons: allBtns.length,
                        btns: Array.from(allBtns).map(b => ({
                            text: b.innerText.trim().substring(0, 30),
                            class: b.className.substring(0, 40),
                            tag: b.tagName
                        }))
                    };
                }
                return {
                    text: openBtn.innerText.trim(),
                    class: openBtn.className,
                    tag: openBtn.tagName,
                    onClick: openBtn.onclick !== null,
                    html: openBtn.innerHTML.substring(0, 200)
                };
            }
        """)
        print(f"    按钮信息: {json.dumps(dropdown_info, ensure_ascii=False)}")
        
        # Try clicking the "打开" button
        try:
            open_btn = page.locator('[class*="bottom"] button').nth(1)  # Second button is "打开"
            if open_btn.is_visible(timeout=2000):
                text = open_btn.inner_text()
                print(f"    '打开'按钮文本: '{text}'")
                # Try clicking the card container
                card = page.locator('[class*="agent-card"]').first
                card.scroll_into_view_if_needed()
                card.click(button='middle')  # Middle click might open in new tab
                page.wait_for_timeout(5000)
                print(f"    中键点击卡片后 URL: {page.url}")
                take_ss(page, "v13_04c_middle_click")
        except Exception as e:
            print(f"    点击操作失败: {e}")

        # Final analysis: try to find any link or API call pattern
        print(f"\n[8] 所有API调用记录:")
        for e in api_events:
            print(f"    {e['method']} {e['url']} -> {e.get('status', '?')}")
            if 'body_preview' in e:
                print(f"      preview: {e['body_preview'][:100]}")

        # Try a direct API call to find the correct agent interaction URL
        print(f"\n[9] 尝试直接访问agent详情页面...")
        # The card shows the first agent is "DI问答助手"
        # Try common URL patterns
        test_urls = [
            "https://agent.digitalchina.com/agent/detail/1",
            "https://agent.digitalchina.com/agent/chat/1",
            "https://agent.digitalchina.com/chat/1",
            "https://agent.digitalchina.com/widget/1",
        ]
        for url in test_urls:
            try:
                resp = page.evaluate_async(f"""
                    async () => {{
                        try {{
                            var r = await fetch('{url}', {{method:'GET'}});
                            return {{url: '{url}', status: r.status, length: await r.text().then(t => t.length)}};
                        }} catch(e) {{
                            return {{url: '{url}', error: e.message}};
                        }}
                    }}
                """)
                print(f"    {resp}")
            except:
                pass

        take_ss(page, "v13_final")
        
        # Save auth
        with open("/home/node/.openclaw/workspace/work/agent-market/.auth/playwright_state.json", "w") as f:
            json.dump(context.storage_state(), f, ensure_ascii=False, indent=2)
        
        browser.close()
        print(f"\n  完成!")

if __name__ == "__main__":
    main()
