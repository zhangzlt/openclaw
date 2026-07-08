#!/usr/bin/env python3
"""飞书 SSO 登录脚本 —— 用于 Playwright 对话测试时注入登录态"""

import asyncio, json, sys
from pathlib import Path
from playwright.async_api import async_playwright

PHONE = "17265205125"
PASSWORD = "zzl20041006"

async def feishu_login(session_path: Path):
    """登录飞书 SSO，保存 Playwright storage_state"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
        context = await browser.new_context(viewport={"width": 1920, "height": 1080})
        page = await context.new_page()

        # 访问任意飞书聊天链接，触发 SSO 登录
        chat_url = "https://bba12hub36.feishuapp.cn/ai/gui/chat/a_eb9c4b2f0c4c40ae90ce7dfb8fe665eb"
        await page.goto(chat_url, wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(6)

        # 1. 切到账号登录（非扫码）
        await page.locator('.switch-login-mode-box').first.click(force=True, timeout=10000)
        await asyncio.sleep(5)
        print("[1/5] ✅ 切换到账号登录")

        # 2. 填手机号
        await page.locator('input[name="mobile_input"]').first.fill(PHONE, timeout=10000)
        try:
            await page.locator('input[type="checkbox"]').first.check(force=True, timeout=3000)
        except:
            pass
        await asyncio.sleep(1)
        print("[2/5] ✅ 手机号已填")

        # 3. 点 Next
        await page.locator('button:has-text("Next")').first.click(force=True, timeout=10000)
        await asyncio.sleep(8)
        print("[3/5] ✅ 已点 Next")

        # 4. 切到密码登录
        for _ in range(3):  # 重试 3 次
            body = await page.evaluate("document.body.innerText")
            if "Switch to Password Verification" in body:
                await page.evaluate("""
                (function() {
                    var all = document.querySelectorAll('*');
                    for (var i = 0; i < all.length; i++) {
                        if (all[i].textContent.trim() === 'Switch to Password Verification') {
                            all[i].click(); return;
                        }
                    }
                })()
                """)
                await asyncio.sleep(5)
                body2 = await page.evaluate("document.body.innerText")
                if "Enter your password" in body2:
                    print("[4/5] ✅ 已切换到密码登录")
                    break
            await asyncio.sleep(2)
        else:
            # 可能在验证码页面但没有密码切换——重试整个流程
            print("[4/5] ⚠️ 未找到密码切换，正在重试...")
            # Click Back and retry
            try:
                await page.locator('button:has-text("Back")').first.click(force=True, timeout=3000)
                await asyncio.sleep(3)
                await page.locator('button:has-text("Next")').first.click(force=True, timeout=5000)
                await asyncio.sleep(8)
                await page.evaluate("""
                (function() {
                    var all = document.querySelectorAll('*');
                    for (var i = 0; i < all.length; i++) {
                        if (all[i].textContent.trim() === 'Switch to Password Verification') {
                            all[i].click(); return;
                        }
                    }
                })()
                """)
                await asyncio.sleep(5)
            except:
                pass

        # 5. 填密码 + 提交
        body = await page.evaluate("document.body.innerText")
        if "Enter your password" in body:
            # 用 JS 直接设置密码值
            await page.evaluate(f"""
            (function() {{
                var pwd = document.querySelector('input[type="password"]');
                if (pwd) {{
                    var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                    setter.call(pwd, '{PASSWORD}');
                    pwd.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    pwd.dispatchEvent(new Event('change', {{ bubbles: true }}));
                }}
            }})()
            """)
            await asyncio.sleep(1)
            print("[5/5] ✅ 密码已填")
        else:
            print(f"[5/5] ⚠️ 不在密码页，当前页: {body[:200]}")

        # 6. 提交登录
        await page.evaluate("""
        (function() {
            var buttons = document.querySelectorAll('button');
            for (var i = 0; i < buttons.length; i++) {
                var b = buttons[i];
                if (b.offsetParent === null) continue;
                var t = b.textContent.trim();
                if (t === 'Next') { b.click(); return 'Next'; }
                if (t.indexOf('登录') >= 0) { b.click(); return '登录'; }
                if (t.indexOf('Log In') >= 0) { b.click(); return 'Log In'; }
            }
            return 'no submit button';
        })()
        """)
        print("🔘 已点提交")
        await asyncio.sleep(15)

        # 结果检查
        final_url = page.url
        final_body = await page.evaluate("document.body.innerText")
        print(f"\n最终 URL: {final_url[:150]}")
        
        if 'chat' in final_url or 'gui' in final_url:
            print("🎉 登录成功！")
        elif 'Enter your password' in final_body or 'Password' in final_body:
            # 还在密码页——密码可能错了
            error = await page.evaluate("""
            (function() {
                var errs = document.querySelectorAll('[class*="error"], [class*="err-msg"]');
                for (var i = 0; i < errs.length; i++) {
                    if (errs[i].textContent.trim()) return errs[i].textContent.trim();
                }
                return null;
            })()
            """)
            print(f"⚠️ 仍在密码页，错误: {error}")
            print(f"   请确认密码是否正确")
        else:
            print(f"⚠️ 未知状态: {final_body[:300]}")

        # 无论如何保存状态
        storage = await context.storage_state()
        session_path.parent.mkdir(parents=True, exist_ok=True)
        with open(session_path, "w") as f:
            json.dump(storage, f, indent=2, ensure_ascii=False)
        print(f"💾 状态已保存到 {session_path}")

        await page.screenshot(path=str(session_path.parent / "login_screenshot.png"))
        await browser.close()
        return storage


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/home/node/.openclaw/workspace/work/agent-market/.auth/playwright_state.json")
    asyncio.run(feishu_login(path))
