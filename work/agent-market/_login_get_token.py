#!/usr/bin/env python3
"""登录 agent.digitalchina.com 并缓存 JWT token"""
import asyncio
import json
import os
import re

async def main():
    from playwright.async_api import async_playwright

    token = {"value": None}
    login_count = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        # Capture requests (sync - only record URL)
        def on_request(request):
            nonlocal login_count
            if token["value"] is not None:
                return
            if request.method == 'POST' and any(ep in request.url for ep in ['/api/auth/', '/oauth/token', '/auth/login', '/login']):
                login_count += 1
                print(f"  [POST #{login_count}] {request.url[:100]}")

        page.on('request', on_request)

        print("Step 1: Navigate to login...")
        await page.goto('https://agent.digitalchina.com/login', wait_until='domcontentloaded', timeout=30000)
        await page.wait_for_timeout(2000)

        # Find and fill form
        print("\nStep 2: Fill login form...")
        itcode_ok = False
        password_ok = False

        for sel in [
            'input[placeholder*="itcode"]',
            'input[placeholder*="请输入itcode"]',
            'input[placeholder*="IT"]',
            'input[type="text"]',
        ]:
            try:
                el = page.locator(sel)
                await el.first.wait_for(timeout=2000)
                await el.first.fill('zhangzlt')
                itcode_ok = True
                print(f"  IT code filled: {sel}")
                break
            except:
                pass

        for sel in [
            'input[placeholder*="统一认证密码"]',
            'input[placeholder*="密码"]',
            'input[type="password"]',
        ]:
            try:
                el = page.locator(sel)
                await el.first.wait_for(timeout=2000)
                await el.first.fill('Zzl.20041006')
                password_ok = True
                print(f"  Password filled: {sel}")
                break
            except:
                pass

        print(f"  itcode={itcode_ok}, password={password_ok}")
        await page.screenshot(path='screenshots/after_fill.png')

        if not (itcode_ok and password_ok):
            print("  [DEBUG] Available inputs:")
            inputs = page.locator('input')
            cnt = await inputs.count()
            for i in range(min(cnt, 15)):
                try:
                    ph = await inputs.nth(i).get_attribute('placeholder')
                    it = await inputs.nth(i).get_attribute('type')
                    print(f"    [{i}] type={it}, ph={ph}")
                except:
                    pass
            await browser.close()
            return None

        # Submit
        print("\nStep 3: Submit login...")
        clicked = False
        for sel in ['button[type="submit"]', 'input[type="submit"]', 'button:has-text("登录")', '.login-btn', '.btn-login']:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=3000):
                    await el.click()
                    clicked = True
                    print(f"  Clicked: {sel}")
                    break
            except:
                pass

        if not clicked:
            print("  Trying Enter key...")
            try:
                await page.locator('form').first.press('Enter')
                clicked = True
            except:
                print("  ❌ No submit method found")
                await browser.close()
                return None

        # Wait for redirect
        print("\nStep 4: Wait for redirect and capture token...")
        for i in range(25):
            await page.wait_for_timeout(3000)
            if token["value"]:
                print(f"  ✅ Token captured at {i*3}s")
                break

            url = page.url
            current_cookies = await context.cookies()

            # Check cookies for JWT
            if not token["value"]:
                for c in current_cookies:
                    name_lower = c.get('name', '').lower()
                    if any(kw in name_lower for kw in ['token', 'jwt', 'session', 'auth']):
                        val = c.get('value', '')
                        if val and len(val) > 30:
                            if val.startswith('eyJ'):
                                token["value"] = val
                                print(f"  [Cookie-JWT] {c['name']} at {i*3}s (len={len(val)})")
                                break
                            # Could be base64-encoded JWT
                            if len(val) > 50 and '.' in val:
                                parts = val.split('.')
                                if len(parts) == 3 and all(len(p) > 10 for p in parts):
                                    token["value"] = val
                                    print(f"  [Cookie-3part] {c['name']} at {i*3}s")
                                    break

            # Check URL for token
            if not token["value"]:
                m = re.search(r'token=([a-zA-Z0-9_-]+\.eyJ[a-zA-Z0-9_.-]+\.[A-Za-z0-9_-]+)', url)
                if m:
                    token["value"] = m.group(1)
                    print(f"  [URL] at {i*3}s")

            print(f"  ... {i*3}s url={url[:60]} cookies={len(current_cookies)} logins={login_count}")

            if 'login' not in url and 'Login' not in url and not token["value"]:
                print(f"  [Redirect to: {url}] - checking storage...")
                await page.wait_for_timeout(2000)

        # Final extraction
        if not token["value"]:
            print("\n  [Final extraction attempts...]")

            # Check cookies one more time
            current_cookies = await context.cookies()
            for c in current_cookies:
                name_lower = c.get('name', '').lower()
                if any(kw in name_lower for kw in ['token', 'jwt', 'session', 'auth']):
                    val = c.get('value', '')
                    if val and len(val) > 30:
                        if val.startswith('eyJ'):
                            token["value"] = val
                            print(f"  [Cookie-2-JWT] {c['name']} (len={len(val)})")
                            break

            # Check localStorage
            if not token["value"]:
                try:
                    storage = await page.evaluate("() => JSON.stringify(window.localStorage)")
                    storage_dict = json.loads(storage)
                    for k, v in storage_dict.items():
                        if any(kw in k.lower() for kw in ['token', 'jwt', 'auth', 'session']):
                            if v and str(v).startswith('eyJ'):
                                token["value"] = v
                                print(f"  [localStorage] {k} (len={len(str(v))})")
                except Exception as e:
                    print(f"  [localStorage error] {e}")

            # Check page source for JWT
            if not token["value"]:
                try:
                    src = await page.content()
                    m = re.search(r'eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', src)
                    if m:
                        token["value"] = m.group(0)
                        print(f"  [Page source] JWT found (len={len(m.group(0))})")
                except:
                    pass

            # Dump cookies for debugging
            if not token["value"]:
                print(f"\n  [Debug cookies ({len(current_cookies)})]:")
                for c in current_cookies:
                    print(f"    {c.get('name','?')} (domain={c.get('domain','?')}) len={len(c.get('value',''))}")

        if token["value"]:
            auth_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.auth')
            os.makedirs(auth_dir, exist_ok=True)

            with open(os.path.join(auth_dir, 'token.txt'), 'w') as f:
                f.write(token["value"])
            print(f"\n✅ Token saved to .auth/token.txt (length={len(token['value'])})")

            session_data = {"cookies": current_cookies, "localStorage": {}}
            try:
                storage = await page.evaluate("() => JSON.stringify(window.localStorage)")
                session_data["localStorage"] = json.loads(storage)
            except:
                pass
            with open(os.path.join(auth_dir, 'session.json'), 'w') as f:
                json.dump(session_data, f, ensure_ascii=False)

            await browser.close()
            return token["value"]
        else:
            print(f"\n❌ Failed to get token (login_attempts={login_count}, url={page.url})")
            await browser.close()
            return None

result = asyncio.run(main())
print(f"\n{'='*60}")
print(f"Result: {'SUCCESS' if result else 'FAILED'}")
if result:
    print(f"Token: {result[:30]}...{result[-10:]}")
