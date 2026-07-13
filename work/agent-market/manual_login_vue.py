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
    ss(page, "login_done")

    # 去市场
    page.goto("https://agent.digitalchina.com/market", wait_until="networkidle", timeout=15000)
    page.wait_for_timeout(5000)
    ss(page, "market")

    # 用Vue触发第一个agent-card的点击
    print("尝试通过Vue触发agent-card点击...")
    vue_result = page.evaluate("""
        () => {
            var card = document.querySelector('.agent-card');
            if (!card) return 'no card';
            
            // 获取Vue实例
            var app = document.querySelector('#app');
            var vueKey = Object.keys(app).find(k => k.startsWith('__vue__'));
            if (!vueKey) return 'no vue instance';
            
            // 尝试触发card-info上的点击（通常是router-link或点击处理的地方）
            var info = card.querySelector('[class*="card-info"]');
            if (info) {
                var e = new MouseEvent('click', {bubbles: true, cancelable: true, composed: true});
                info.dispatchEvent(e);
                return 'clicked card-info, url=' + window.location.href;
            }
            return 'clicked card, url=' + window.location.href;
        }
    """)
    print(f"Vue点击结果: {vue_result}")
    ss(page, "vue_click")
    print(f"URL: {page.url}")

    # 获取localStorage中的token
    ls = page.evaluate("Object.fromEntries(Object.entries(localStorage).map(([k,v])=>[k,v.substring(0,100)]))")
    print(f"localStorage keys: {list(ls.keys())}")
    for k, v in ls.items():
        if 'token' in k.lower() or 'auth' in k.lower():
            print(f"  {k}: {v[:100]}")

    # 尝试通过fetch访问agent API获取ID
    api_result = page.evaluate("""
        async () => {
            var token = localStorage.getItem('token') || localStorage.getItem('Authorization') || '';
            var headers = {'Content-Type': 'application/json'};
            if (token) headers['Authorization'] = token;
            
            // 尝试获取agent列表
            var resp1 = await fetch('/api/widget/list?page=1&pageSize=3', {headers}).catch(()=>null);
            if (resp1) {
                var data1 = await resp1.json();
                return {status1: resp1.status, data1: JSON.stringify(data1).substring(0, 500)};
            }
            return 'no resp1';
        }
    """)
    print(f"\nAgent API: {json.dumps(api_result, ensure_ascii=False)}")

    # 直接尝试访问agent详情页面
    print("\n尝试直接访问agent页面...")
    page2 = ctx.new_page()
    page2.goto("https://agent.digitalchina.com/login", wait_until="networkidle", timeout=30000)
    page2.wait_for_timeout(2000)
    page2.fill("input[type='text']", "dstest")
    page2.fill("input[type='password']", "Aa@11223344")
    page2.wait_for_timeout(500)
    page2.click("button[type='submit']")
    page2.wait_for_timeout(5000)
    ss(page2, "login2")

    # 尝试访问 /agent/detail/1 或类似URL
    page2.goto("https://agent.digitalchina.com/agent/chat", wait_until="networkidle", timeout=15000)
    page2.wait_for_timeout(3000)
    ss(page2, "agent_chat_url")
    print(f"URL after /agent/chat: {page2.url}")
    body = page2.locator("body").inner_text()[:200]
    print(f"文本: {body}")

    b.close()
    page2.close()
    print("完成")
