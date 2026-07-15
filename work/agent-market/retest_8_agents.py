#!/usr/bin/env python3
"""专项重测 8 个智能体：feishu chat + non-chat"""

import sys, json, time, traceback
from pathlib import Path
from playwright.sync_api import sync_playwright

WORKSPACE = Path("/home/node/.openclaw/workspace")
OUTPUT_DIR = WORKSPACE / "work/agent-market/reports/screenshots"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Agent definitions ──
AGENTS = [
    {"id": 74,  "name": "电子签章智能问答助手", "url": "https://bba12hub36.feishuapp.cn/ai/gui/chat/a_1f46a3e5ec0c4d59b0e93eae67b638a1", "type": "chat",     "question": "电子签章怎么用？"},
    {"id": 73,  "name": "EB智能客服机器人",       "url": "https://bba12hub36.feishuapp.cn/ai/gui/chat/a_ea846e95d9e645129b6049b74b3cfd04", "type": "chat",     "question": "你好，请介绍一下你自己"},
    {"id": 83,  "name": "新海量采购系统智能助手",   "url": "https://bba12hub36.feishuapp.cn/ai/gui/chat/a_c0021ea58f72473384fa50e8636a43a3", "type": "chat",     "question": "采购系统账号怎么申请？"},
    {"id": 102, "name": "人员分组智能助手",         "url": "https://bba12hub36.aiforce.cloud/spark/faas/app_4jtsfvghhd5pk",                      "type": "interact", "needs_auth": True},
    {"id": 109, "name": "CTC智能客服",             "url": "https://bba12hub36.feishuapp.cn/ai/gui/chat/a_eb9c4b2f0c4c40ae90ce7dfb8fe665eb", "type": "chat",     "question": "你好"},
    {"id": 110, "name": "折扣问答小助手",           "url": "https://bba12hub36.feishuapp.cn/ai/gui/chat/a_3687bf8dfcc64b378852e86891d042e5", "type": "chat",     "question": "有哪些折扣类型？"},
    {"id": 123, "name": "售前URS解析助手",          "url": "https://bba12hub36.aiforce.cloud/app/app_4jx1jik8om8ll",                            "type": "upload",   "needs_auth": False},
    {"id": 125, "name": "欠款风险管理平台",         "url": "https://bba12hub36.aiforce.cloud/app/app_4k5wq7xt5hv9f",                            "type": "interact", "needs_auth": True},
]

MAX_AUTH_RETRIES = 3

# ── Helper functions ──

def get_body(page):
    try: return page.evaluate("() => document.body ? document.body.innerText : ''")
    except: return ""

def is_auth_page(body, url=""):
    if "accounts.feishu.cn" in url: return True
    auth_kw = ["Requests permissions", "Permissions that can be granted", "Authorizing indicates", "请求获得以下权限"]
    if any(w in body for w in auth_kw): return True
    return any(w in body for w in ["Authorize", "授权"]) and any(w in body for w in ["Reject", "拒绝"])

def is_login_page(body, url=""):
    return any(w in body or w in url for w in ["扫码登录","验证码","Sign in","Log in","Password"])

def is_generating(body):
    return any(w in body for w in ["Stop generating","停止生成","停止回答"])

def is_error_page(body, url=""):
    return any(w in body for w in ["500","404","Error","Forbidden","访问受限","无权限"])

def detect_input(page):
    for sel in ["[contenteditable]", "textarea", "input[type='text']"]:
        try:
            if page.query_selector(sel): return sel
        except: pass
    return None

def has_business_elements(body, page):
    """判断是否为智能体业务页面（非授权/登录）"""
    if is_auth_page(body, page.url): return False
    if is_login_page(body, page.url): return False
    if is_error_page(body, page.url): return False
    if detect_input(page): return True
    biz_kw = ["询","问","chat","Chat","消息","发送","输入","提问","智能","Agent","agent","助手","助理","知识库","文档","上传","运行","Run","模板"]
    return len(body) > 100 and any(w in body for w in biz_kw)

def click_button(page, *names):
    for name in names:
        try:
            btn = page.get_by_role("button", name=name).first
            if btn.is_visible():
                btn.click(); return name
        except: continue
    return None

def validate_before_screenshot(page, result):
    """截图前最终门禁"""
    body = get_body(page)
    url = page.url
    if is_auth_page(body, url): return False, "最终页面仍为授权页"
    if is_login_page(body, url): return False, "最终页面仍为登录页"
    if is_generating(body): return False, "智能体仍在生成回答"
    if result["type"] == "chat" and not result.get("answer_text"): return False, "未采集到智能体回答"
    if not has_business_elements(body, page): return False, "未确认进入智能体业务页面"
    return True, ""

def extract_answer(page, question, body_before):
    """从页面DOM提取最新回答"""
    try:
        body = get_body(page)
        # feishuapp 平台：chatContainer
        containers = page.query_selector_all('[class*="chatContainer"]')
        if containers:
            clone = containers[0].evaluate("el => { const c = el.cloneNode(true); c.querySelectorAll('button,nav,input,textarea,[contenteditable]').forEach(n => n.remove()); return c.textContent || ''; }")
            if clone:
                # 去除欢迎语
                idx = clone.find("你好")
                if idx > 0 and idx <= 20:
                    clone = clone[:idx].strip()
                return clone.strip()
        # 通用：找 body 中问题后的内容
        q_idx = body.find(question[:8]) if question else -1
        if q_idx >= 0:
            after = body[q_idx + len(question):].strip()
            # 排除欢迎语
            w_idx = after.find("你好")
            if w_idx > 0 and w_idx < 30:
                after = after[:w_idx].strip()
            return after[:3000]
        return ""
    except:
        return ""

# ── Main test loop ──

results = {}

with sync_playwright() as pw:
    browser = pw.chromium.connect_over_cdp("http://127.0.0.1:18800")

    for agent in AGENTS:
        aid = agent["id"]
        aname = agent["name"]
        aurl = agent["url"]
        atype = agent["type"]

        print(f"\n{'='*60}")
        print(f"[{aid}] {aname} ({atype})")
        print(f"URL: {aurl}")
        print(f"{'='*60}")

        result = {
            "agent_id": aid, "agent_name": aname, "url": aurl,
            "type": atype, "status": "unknown",
            "question_text": "", "answer_text": "",
            "screenshot": "",
            "error": "",
            "auth_retries": 0,
        }

        page = browser.contexts[0].new_page()
        page.set_viewport_size({"width": 1280, "height": 900})

        try:
            # ━━ Step 1: Open URL ━━
            print("Step 1: 打开目标URL")
            page.goto(aurl, wait_until="domcontentloaded", timeout=30000)
            time.sleep(5)
            body = get_body(page)
            url = page.url
            print(f"  URL: {url[:100]}")
            print(f"  Body({len(body)}): {body[:150]}")

            # ━━ Step 2: Auth detection ━━
            auth_retries = 0
            while is_auth_page(body, url) and auth_retries < MAX_AUTH_RETRIES:
                auth_retries += 1
                print(f"  🔐 检测到授权页 (retry {auth_retries}/{MAX_AUTH_RETRIES})")
                clicked = click_button(page, "Authorize", "授权", "确认授权", "允许")
                if not clicked:
                    print(f"  ❌ 找不到授权按钮")
                    break
                print(f"  点击: {clicked}")
                # Wait for redirect
                redirected = False
                for i in range(30):
                    time.sleep(1)
                    new_body = get_body(page)
                    new_url = page.url
                    if not is_auth_page(new_body, new_url):
                        print(f"  ✅ 授权完成 t={i+1}s, URL={new_url[:80]}")
                        time.sleep(2)
                        # Re-open target URL if redirected away
                        if new_url != aurl and aurl not in new_url:
                            print(f"  重新打开目标URL")
                            page.goto(aurl, wait_until="domcontentloaded", timeout=30000)
                            time.sleep(5)
                        body = get_body(page)
                        url = page.url
                        redirected = True
                        break
                if not redirected:
                    print(f"  ⚠️ 超时等待跳转")
                    break

            if is_auth_page(body, url):
                result["status"] = "blocked"
                result["error"] = f"授权失败 ({auth_retries}/{MAX_AUTH_RETRIES} 次)"
                ss_path = str(OUTPUT_DIR / f"{aid}_final.png")
                page.screenshot(path=ss_path, full_page=True)
                result["screenshot"] = ss_path
                print(f"  ❌ BLOCKED: {result['error']}")
                results[aid] = result
                page.close()
                continue

            # ━━ Step 3: Confirm business page ━━
            print("Step 3: 确认业务页面")
            if not has_business_elements(body, page):
                body2 = get_body(page)
                if not has_business_elements(body2, page):
                    result["status"] = "blocked"
                    result["error"] = "未进入业务页面"
                    ss_path = str(OUTPUT_DIR / f"{aid}_final.png")
                    page.screenshot(path=ss_path, full_page=True)
                    result["screenshot"] = ss_path
                    print(f"  ❌ BLOCKED: {result['error']} body={body2[:100]}")
                    results[aid] = result
                    page.close()
                    continue

            input_sel = detect_input(page)
            print(f"  输入框: {input_sel}")
            body_before = body

            # ━━ Step 4: Execute test ━━
            if atype == "chat":
                question = agent.get("question", "你好")
                print(f"Step 4: 发送问题: {question}")

                el = page.query_selector(input_sel)
                el.click()
                time.sleep(0.3)
                if input_sel == "[contenteditable]":
                    el.evaluate("el => { el.innerHTML = ''; el.focus(); }")
                    page.keyboard.type(question, delay=30)
                else:
                    el.fill(question)
                time.sleep(0.5)
                # Send
                sent = click_button(page, "发送", "Send")
                if not sent:
                    page.keyboard.press("Enter")

                result["question_text"] = question
                start_wait = time.time()

                # Wait for answer
                stop_seen = False
                stop_gone = False
                answer = ""
                for i in range(60):
                    time.sleep(3)
                    body = get_body(page)
                    if not stop_seen and is_generating(body):
                        stop_seen = True
                        print(f"  stop_seen t={i*3+3}s")
                        continue
                    if stop_seen and not is_generating(body):
                        stop_gone = True
                        print(f"  stop_gone t={i*3+3}s")
                        # Get stable answer
                        time.sleep(2)
                        a1 = extract_answer(page, question, body_before)
                        time.sleep(3)
                        a2 = extract_answer(page, question, body_before)
                        if a2 and len(a2) > 10 and a2[:min(len(a2),len(a1))] == a1[:min(len(a1),len(a2))]:
                            answer = a2
                            break

                result["answer_text"] = answer
                print(f"  answer: {len(answer)} chars")
                if answer:
                    lines = answer.split('\n')[:3]
                    for l in lines:
                        print(f"    {l[:80]}")

            elif atype == "upload":
                print("Step 4: 文件上传测试")
                time.sleep(3)
                # Find upload input and try
                file_input = page.query_selector("input[type='file']")
                if file_input:
                    test_file = WORKSPACE / "work/agent-market/test_files/urs_requirements_test.pdf"
                    if test_file.exists():
                        file_input.set_input_files(str(test_file))
                        print(f"  上传: {test_file.name}")
                        time.sleep(10)
                else:
                    print("  未找到文件上传输入框")

            elif atype == "interact":
                print("Step 4: 交互测试")
                time.sleep(5)
                # Try to find textarea or input
                ia = page.query_selector("textarea, input:not([type='hidden'])")
                if ia:
                    print(f"  找到输入组件")
                # Screenshot the current state
                pass

            # ━━ Step 5: Final validation + screenshot ━━
            print("Step 5: 截图前验证")
            body = get_body(page)
            final_url = page.url

            valid, reason = validate_before_screenshot(page, result)
            print(f"  valid={valid} reason={reason}")
            print(f"  is_auth={is_auth_page(body, final_url)} is_login={is_login_page(body, final_url)} is_generating={is_generating(body)}")

            if not valid and "授权页" in reason:
                print(f"  📍 最终页面为授权页，重新授权...")
                auth_retries = 0
                while is_auth_page(body, final_url) and auth_retries < MAX_AUTH_RETRIES:
                    auth_retries += 1
                    clicked = click_button(page, "Authorize", "授权")
                    if not clicked: break
                    time.sleep(5)
                    for _ in range(30):
                        time.sleep(1)
                        if not is_auth_page(get_body(page), page.url): break
                    if not is_auth_page(get_body(page), page.url): break
                if not is_auth_page(get_body(page), page.url):
                    # Re-test
                    if atype == "chat" and agent.get("question"):
                        print(f"  重新发送问题...")
                        el = page.query_selector(detect_input(page))
                        if el:
                            el.click()
                            time.sleep(0.3)
                            page.keyboard.type(agent["question"], delay=30)
                            time.sleep(0.5)
                            page.keyboard.press("Enter")
                        time.sleep(15)
                    valid, reason = validate_before_screenshot(page, result)

            ss_path = str(OUTPUT_DIR / f"{aid}_final.png")
            page.screenshot(path=ss_path, full_page=True)
            result["screenshot"] = ss_path

            if valid:
                result["status"] = "pass"
                print(f"  ✅ PASS: {ss_path}")
            else:
                result["status"] = "blocked"
                result["error"] = reason
                print(f"  ❌ BLOCKED: {reason}")

        except Exception as e:
            print(f"  ❌ 异常: {e}")
            traceback.print_exc()
            result["status"] = "fail"
            result["error"] = str(e)
            try:
                ss_path = str(OUTPUT_DIR / f"{aid}_final.png")
                page.screenshot(path=ss_path, full_page=True)
                result["screenshot"] = ss_path
            except:
                pass

        finally:
            page.close()
            results[aid] = result
            print(f"  → status={result['status']} page closed")

# ── Summary ──
print(f"\n{'='*60}")
print(f"重测结果汇总")
print(f"{'='*60}")
for a in AGENTS:
    r = results.get(a["id"], {})
    ss = "✅" if r.get("status") == "pass" else ("🚫" if r.get("status") == "blocked" else "❌")
    err = r.get("error", "")
    ans_len = len(r.get("answer_text", "") or "")
    status_line = f"{ss} [{a['id']}] {a['name']}: {r.get('status')}"
    if err: status_line += f" ({err[:50]})"
    if ans_len: status_line += f" answer={ans_len}c"
    print(status_line)

# Save results
result_path = WORKSPACE / "work/agent-market/reports/retest_results.json"
with open(result_path, 'w', encoding='utf-8') as f:
    json.dump(results, f, ensure_ascii=False, indent=2, default=str)
print(f"\n结果保存: {result_path}")
