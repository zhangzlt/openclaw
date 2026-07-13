"""独立登录脚本 - 获取并缓存 JWT token"""
import asyncio
from playwright.async_api import async_playwright
import os, json, re

async def main():
    state = {"token": None, "login_count": 0, "error": None}
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080}
        )
        page = await context.new_page()

        def handle_response(response):
            if state["token"]:
                return
            url = response.url
            try:
                if any(ep in url for ep in ['/auth/v1/oauth/token', '/api/auth/token', '/api/auth/login', '/api/oauth/token']):
                    body = response.json()
                    t = body.get('access_token') or body.get('token')
                    if t:
                        state["token"] = t
                        print(f"  [API] token found from {url[:60]}...")
                    else:
                        print(f"  [API] no token in response: {list(body.keys())[:5]}")
            except Exception as e:
                if response.status != 200:
                    pass  # non-200 responses might not be login attempts

        def handle_request(request):
            if state["token"] or state["login_count"] >= 3:
                return
            url = request.url
            if any(ep in url for ep in ['/auth/v1/oauth/token', '/api/auth/login', '/api/auth/token', '/login']):
                if request.method == 'POST':
                    state["login_count"] += 1
                    body = request.post_data.decode('utf-8', errors='replace') if request.post_data else ''
                    print(f"  [REQ #{state['login_count']}] POST {url[:80]}...")
                    print(f"    body: {body[:200]}")

        page.on('response', handle_response)
        page.on('request', handle_request)

        # Navigate to login
        print("Step 1: Navigate to login page...")
        try:
            await page.goto('https://agent.digitalchina.com/login', wait_until='domcontentloaded', timeout=30000)
        except Exception as e:
            print(f"  Note: {e}")
        
        current_url = page.url
        print(f"  URL: {current_url}")
        await page.wait_for_timeout(2000)

        # Find inputs
        print("\nStep 2: Find login inputs...")
        all_inputs = page.locator('input')
        count = await all_inputs.count()
        print(f"  Found {count} input elements")
        for i in range(min(count, 10)):
            el = all_inputs.nth(i)
            try:
                placeholder = await el.get_attribute('placeholder')
                itype = await el.get_attribute('type')
                name = await el.get_attribute('name')
                print(f"    [{i}] type={itype}, name={name}, ph={placeholder}")
            except:
                pass

        # Fill form
        print("\nStep 3: Fill form...")
        itcode_found = False
        password_found = False
        
        for sel in ['input[placeholder*="itcode"]', 'input[placeholder*="请输入itcode"]', 'input[type="text"]', 'input[name="username"]']:
            try:
                await page.locator(sel).first.wait_for(timeout=2000)
                await page.locator(sel).first.fill('zhangzlt')
                itcode_found = True
                print(f"  ✅ IT Code filled: {sel}")
                break
            except:
                pass
        
        for sel in ['input[placeholder*="统一认证密码"]', 'input[placeholder*="密码"]', 'input[type="password"]', 'input[name="password"]']:
            try:
                await page.locator(sel).first.wait_for(timeout=2000)
                await page.locator(sel).first.fill(os.environ['AGENT_MARKET_PASSWORD'])
                password_found = True
                print(f"  ✅ Password filled: {sel}")
                break
            except:
                pass

        print(f"  itcode={itcode_found}, password={password_found}")
        
        if not itcode_found or not password_found:
            print("  ❌ Cannot find both inputs")
            await page.screenshot(path='screenshots/login_debug.png')
            await browser.close()
            return None

        # Submit
        print("\nStep 4: Submit...")
        submit_found = False
        for sel in ['button[type="submit"]', 'input[type="submit"]', 'button:has-text("登录")', 'button[type="button"]']:
            try:
                await page.locator(sel).first.click()
                submit_found = True
                print(f"  ✅ Clicked submit: {sel}")
                break
            except:
                pass
        
        if not submit_found:
            try:
                await page.keyboard.press('Enter')
                print("  ✅ Submitted via Enter")
            except:
                print("  ❌ No submit method found")
                await browser.close()
                return None

        # Wait for token
        print("\nStep 5: Wait for token...")
        for i in range(15):
            await page.wait_for_timeout(3000)
            if state["token"]:
                print(f"  ✅ Token captured after {i*3}s")
                break
            
            url = page.url
            if 'login' not in url:
                print(f"  ℹ️  Redirected to: {url[:100]}")
                # Check for token in URL
                match = re.search(r'token=([^&]+)', url)
                if match:
                    state["token"] = match.group(1)
                    print("  ✅ Token in URL")
                    break
            
            # Check cookies
            cookies = await context.cookies()
            for c in cookies:
                if 'token' in c.get('name', '').lower():
                    state["token"] = c.get('value')
                    print(f"  ✅ Token in cookie")
                    break
            if state["token"]:
                break
            
            print(f"  ... waiting ({i*3}s elapsed, login_attempts={state['login_count']})")

        # Final extraction
        if not state["token"]:
            print("\nFinal attempts...")
            cookies = await context.cookies()
            for c in cookies:
                if 'token' in c.get('name', '').lower():
                    state["token"] = c.get('value')
                    print(f"  ✅ Token from final cookie check")
                    break
            
            if not state["token"]:
                try:
                    source = await page.content()
                    jwt_match = re.search(r'eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', source)
                    if jwt_match:
                        state["token"] = jwt_match.group(0)
                        print("  ✅ Token from page source")
                except:
                    pass

        # Save
        if state["token"]:
            cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.auth')
            os.makedirs(cache_dir, exist_ok=True)
            with open(os.path.join(cache_dir, 'token.txt'), 'w') as f:
                f.write(state["token"])
            print(f"\n✅ Token saved to .auth/token.txt")
            
            # Also save session state
            with open(os.path.join(cache_dir, 'session.json'), 'w') as f:
                json.dump({'cookies': cookies, 'originStorage': [], 'localStorage': []}, f)
            print(f"   Session saved to .auth/session.json")
            
            await browser.close()
            return state["token"]
        else:
            print(f"\n❌ Failed to get token (login_attempts={state['login_count']})")
            url = page.url
            body_text = await page.locator('body').inner_text()[:500]
            print(f"  Final URL: {url}")
            print(f"  Body preview: {body_text[:200]}")
            await page.screenshot(path='screenshots/login_debug.png')
            await browser.close()
            return None

result = asyncio.run(main())
print(f"\nResult: {'SUCCESS' if result else 'FAILED'}")
