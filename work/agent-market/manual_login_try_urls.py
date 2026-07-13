from playwright.sync_api import sync_playwright
import os, json

OUT = "/home/node/.openclaw/workspace/work/agent-market/screenshots"
os.makedirs(OUT, exist_ok=True)

def ss(page, name):
    path = os.path.join(OUT, f"{name}.png")
    page.screenshot(path=path)
    print(f"截图: {path}")

with sync_playwright() as p:
    b = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"])
    ctx = b.new_context(viewport={"width": 1920, "height": 1080})
    page = ctx.new_page()

    # 登录
    page.goto("https://agent.digitalchina.com/login", wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(3000)
    page.fill("input[type='text']", "dstest")
    page.fill("input[type='password']", "Aa@11223344")
    page.wait_for_timeout(500)
    page.click("button[type='submit']")
    page.wait_for_timeout(5000)
    ss(page, "after_login")

    # 去市场
    page.goto("https://agent.digitalchina.com/market", wait_until="networkidle", timeout=15000)
    page.wait_for_timeout(5000)
    ss(page, "market_home")

    # 用JS获取所有事件监听器和点击处理
    print("\n分析页面点击机制...")
    js_result = page.evaluate("""
        () => {
            var results = [];
            
            // 查找所有有onclick处理器的元素
            var allEls = document.querySelectorAll('*');
            for (var i = 0; i < allEls.length; i++) {
                var el = allEls[i];
                if (el.onclick !== null && el.innerText && el.innerText.length < 50) {
                    results.push({
                        tag: el.tagName,
                        text: el.innerText.trim().substring(0, 30),
                        class: el.className.substring(0, 60),
                        onclick_type: typeof el.onclick
                    });
                }
            }
            
            // 查找所有有data-agent-id或类似属性的元素
            var cardContainers = document.querySelectorAll('[data-agent-id], [data-id], [data-uid], [data-key]');
            var cardIds = [];
            cardContainers.forEach(function(el) {
                cardIds.push({
                    tag: el.tagName,
                    text: el.innerText.trim().substring(0, 30),
                    class: el.className.substring(0, 60),
                    attrs: {}
                });
                for (var j = 0; j < el.attributes.length; j++) {
                    var a = el.attributes[j];
                    if (a.value && a.value.length < 100) {
                        cardIds[cardIds.length-1].attrs[a.name] = a.value;
                    }
                }
            });
            
            return { onclicks: results.slice(0, 20), cardIds: cardIds.slice(0, 10) };
        }
    """)
    print(json.dumps(js_result, ensure_ascii=False)[:1000])

    # 用JS触发第一个agent card的点击
    print("\n用JS点击第一个agent-card...")
    js_click_result = page.evaluate("""
        () => {
            var card = document.querySelector('.agent-card');
            if (!card) return 'no card';
            
            // 查找所有可能的交互元素
            var actions = card.querySelectorAll('[class*="action"], [class*="bottom"] button, [class*="stat"]');
            var results = [];
            actions.forEach(function(el) {
                results.push({
                    text: el.innerText.trim().substring(0, 30),
                    tag: el.tagName,
                    class: el.className.substring(0, 40),
                    onclick: el.onclick !== null
                });
            });
            
            // 尝试点击card本身
            var clickEvent = new MouseEvent('click', {bubbles: true, cancelable: true});
            card.dispatchEvent(clickEvent);
            
            // 同时尝试点击card-name-row
            var nameRow = card.querySelector('[class*="name-row"]');
            if (nameRow) {
                nameRow.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
            }
            
            // 尝试点击card-info
            var cardInfo = card.querySelector('[class*="card-info"]');
            if (cardInfo) {
                cardInfo.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
            }
            
            return {cardResults: results, urlAfterClick: window.location.href};
        }
    """)
    print(json.dumps(js_click_result, ensure_ascii=False)[:500])

    ss(page, "after_js_click")
    print(f"当前URL: {page.url}")

    # 尝试直接导航到可能的agent URL
    print("\n尝试直接导航到可能的agent页面...")
    test_urls = [
        "/agent",
        "/agent/chat",
        "/chat",
        "/widget",
        "/agent/1/chat",
        "/chat/1",
    ]
    for url in test_urls:
        try:
            resp = page.evaluate_async(f"""
                async () => {{
                    try {{
                        var r = await fetch('/{url.lstrip("/")}', {{method:'GET'}});
                        return {{url: '/{url}', status: r.status, len: (await r.text()).length}};
                    }} catch(e) {{
                        return {{url: '/{url}', error: e.message}};
                    }}
                }}
            """)
            print(f"  {resp}")
        except:
            pass

    ss(page, "final")
    b.close()
    print("完成")
