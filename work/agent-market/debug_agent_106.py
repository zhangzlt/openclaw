#!/usr/bin/env python3
"""调试 Agent 106 (职场文案速写·全能版): 发送未成功问题"""

import json, time
from pathlib import Path
from playwright.sync_api import sync_playwright

AID = 106
ANAME = "职场文案速写·全能版"
URL = "https://aily.feishu.cn/agents/agent_4k4mhq6d81p8a"
QUESTION = "请帮我写一则周五下午3点在第一会议室召开的项目复盘会议通知。"
OUTPUT = Path("/home/node/.openclaw/workspace/work/agent-market/reports/debug")
OUTPUT.mkdir(parents=True, exist_ok=True)

debug_log = []
def log(msg):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")
    debug_log.append({"ts": ts, "msg": msg})

def get_body(page):
    try: return page.evaluate("() => document.body ? document.body.innerText : ''")
    except: return ""

def detect_inputs(page):
    """检测所有可能的输入框"""
    results = []
    for sel in ['textarea', '[contenteditable]', '[role="textbox"]',
                'input[type="text"]', 'input:not([type])']:
        try:
            els = page.query_selector_all(sel)
            for el in els:
                if el.is_visible():
                    ph = ""
                    try:
                        ph = el.get_attribute("placeholder") or ""
                    except: pass
                    results.append({
                        "selector": sel,
                        "visible": True,
                        "placeholder": ph,
                        "tag": el.evaluate("el => el.tagName"),
                    })
        except:
            pass
    return results

def count_user_msgs(page):
    """统计页面上用户消息节点数"""
    return page.evaluate("""() => {
        const msgs = document.querySelectorAll('[class*="user"], [class*="User"], [class*="human"], [class*="Human"], [class*="sender"]');
        return msgs.length;
    }""")

with sync_playwright() as pw:
    browser = pw.chromium.connect_over_cdp("http://127.0.0.1:18800")
    page = browser.contexts[0].new_page()
    page.set_viewport_size({"width": 1280, "height": 900})

    try:
        log(f"===== Agent {AID}: {ANAME} =====")

        # Step 0: Open
        log(f"打开 {URL}")
        page.goto(URL, wait_until="domcontentloaded", timeout=30000)
        time.sleep(5)

        body = get_body(page)
        log(f"URL: {page.url}")
        log(f"Body({len(body)}): {body[:200]}")

        # Step 1: Detect all inputs
        log("===== 检测输入框 =====")
        inputs = detect_inputs(page)
        for inp in inputs:
            log(f"  {inp['tag']} [{inp['selector']}] placeholder='{inp['placeholder'][:60]}'")

        if not inputs:
            log("❌ 没有可见输入框")
        else:
            log(f"找到 {len(inputs)} 个输入框")

        # Step 1b: Check for suggested questions
        page.evaluate("""() => {
            const btns = document.querySelectorAll('button');
            const qBtns = [];
            btns.forEach(b => {
                const t = b.textContent.trim();
                if (t.length > 5 && t.length < 60) qBtns.push(t);
            });
            return qBtns;
        }""")
        log("查找推荐问题按钮...")
        q_btns = page.query_selector_all('button')
        for btn in q_btns[:20]:
            try:
                txt = btn.inner_text().strip()
                if 5 < len(txt) < 60:
                    log(f"  推荐问题: '{txt}'")
            except: pass

        # Step 2: Try to fill the input
        log(f"===== 填入测试问题 =====")
        log(f"问题: {QUESTION}")

        # Method: find [contenteditable] - most common on Aily
        input_el = None
        for sel in ['[contenteditable]', 'textarea', '[role="textbox"]']:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    input_el = el
                    input_sel = sel
                    log(f"使用选择器: {sel}")
                    break
            except: pass

        if not input_el:
            log("❌ 未找到可用输入框")
            page.screenshot(path=str(OUTPUT / f"{AID}_no_input.png"), full_page=True)
            exit(1)

        # Click and fill
        input_el.click()
        time.sleep(0.5)

        if input_sel == "[contenteditable]":
            input_el.evaluate("el => { el.innerHTML = ''; el.focus(); }")
            page.keyboard.type(QUESTION, delay=30)
        elif input_sel == "textarea":
            input_el.fill("")
            time.sleep(0.2)
            input_el.fill(QUESTION)
        else:
            input_el.fill(QUESTION)

        time.sleep(0.5)

        # Step 3: Verify input content
        log("===== 验证输入内容 =====")
        if input_sel == "[contenteditable]":
            input_text = input_el.inner_text().strip()
        else:
            input_text = input_el.input_value().strip()

        log(f"输入框内容: '{input_text[:100]}'")
        log(f"问题匹配: {QUESTION[:20] in input_text}")

        # Log any input-related properties
        try:
            attrs = input_el.evaluate("""el => {
                return {
                    contentEditable: el.contentEditable,
                    isContentEditable: el.isContentEditable,
                    innerHTML: el.innerHTML.substring(0, 100),
                    textContent: (el.textContent || '').substring(0, 100),
                    value: el.value || '',
                    placeholder: el.placeholder || el.getAttribute('placeholder') || '',
                };
            }""")
            log(f"输入框属性: {json.dumps(attrs, ensure_ascii=False)}")
        except: pass

        # Step 4: Try to send
        log(f"===== 发送 =====")
        pre_send_body = get_body(page)
        pre_user_msgs = count_user_msgs(page)
        log(f"发送前 body_len={len(pre_send_body)} user_msgs={pre_user_msgs}")

        sent_ok = False
        send_method = ""

        # Method 1: Click send arrow button
        send_selectors = [
            'button[type="submit"]',
            'button:has(svg)',
            'button:has([class*="send"])',
            'button[class*="send"]',
            'button[class*="submit"]',
            '[aria-label*="send" i]',
            '[aria-label*="发送" i]',
        ]
        for ss in send_selectors:
            try:
                btn = page.query_selector(ss)
                if btn and btn.is_visible():
                    log(f"  尝试点击: {ss}")
                    btn.click()
                    send_method = f"click:{ss}"
                    time.sleep(2)
                    sent_ok = True
                    break
            except: continue

        # Method 2: Press Enter
        if not sent_ok:
            log(f"  尝试按 Enter")
            input_el.press("Enter")
            send_method = "enter"
            time.sleep(2)

        # Method 3: Re-focus + click again
        if not sent_ok:
            log(f"  重新聚焦并尝试点击")
            input_el.click()
            time.sleep(0.3)
            input_el.press("Enter")
            send_method = "refocus+enter"
            time.sleep(2)

        # Step 5: Verify send success
        log(f"===== 验证发送结果 =====")
        time.sleep(3)
        post_send_body = get_body(page)
        post_user_msgs = count_user_msgs(page)

        input_text_after = ""
        try:
            if input_sel == "[contenteditable]":
                input_text_after = input_el.inner_text().strip()
            else:
                input_text_after = input_el.input_value().strip()
        except: pass

        log(f"发送后 body_len={len(post_send_body)} user_msgs={post_user_msgs}")
        log(f"输入框清空: '{input_text_after[:50]}' (原='{input_text[:50]}')")
        log(f"body包含问题: {QUESTION[:15] in post_send_body}")
        log(f"body包含完整问题: {QUESTION in post_send_body}")
        log(f"user_msgs变化: {pre_user_msgs} -> {post_user_msgs}")

        # Full body check
        body_preview = post_send_body
        q_idx = body_preview.find(QUESTION[:10])
        if q_idx >= 0:
            log(f"  问题出现在 body 位置: {q_idx}")
            context = body_preview[max(0,q_idx-20):q_idx+len(QUESTION)+50]
            log(f"  上下文: ...{context}...")

        # Check for user message bubbles
        user_bubbles = page.evaluate("""() => {
            const bubbles = [];
            // Aily user message indicators
            document.querySelectorAll('[class*="message"], [class*="bubble"], [class*="chat"]').forEach(el => {
                const txt = el.textContent.trim();
                if (txt.length > 10 && txt.length < 2000) {
                    const cls = el.className || '';
                    bubbles.push({cls: cls.substring(0, 60), txt: txt.substring(0, 80)});
                }
            });
            return bubbles;
        }""")
        log(f"用户气泡: {len(user_bubbles)} 个")
        for i, b in enumerate(user_bubbles[:5]):
            log(f"  [{i}] cls={b['cls']} txt='{b['txt']}'")

        send_confirmed = (
            (post_user_msgs > pre_user_msgs) or
            (QUESTION[:15] in post_send_body and "📝" not in post_send_body[:50]) or
            (len(input_text_after) < len(input_text) * 0.5)
        )

        log(f"发送确认: {send_confirmed}")
        log(f"发送方式: {send_method}")

        # Screenshot
        page.screenshot(path=str(OUTPUT / f"{AID}_send_debug.png"), full_page=True)

        # Step 6: If confirmed, wait for answer
        if send_confirmed:
            log(f"===== 等待回答 =====")
            stop_seen = False
            stop_gone = False
            prev = ""
            stable = 0

            for i in range(60):
                time.sleep(3)
                body = get_body(page)
                has_stop = "Stop generating" in body or "停止生成" in body

                if not stop_seen and has_stop:
                    stop_seen = True
                    log(f"  [{i*3+3}s] Stop generating 出现")
                if stop_seen and not has_stop:
                    stop_gone = True
                    log(f"  [{i*3+3}s] Stop generating 消失")

                # Extract answer
                try:
                    answer = page.evaluate("""() => {
                        const body = document.body;
                        if (!body) return '';
                        const all = body.innerText || '';
                        const end = all.indexOf('How was this result?');
                        const q_start = all.indexOf('项目复盘会议通知');
                        if (q_start >= 0 && end > q_start) {
                            return all.substring(q_start, end).trim();
                        }
                        if (q_start >= 0) {
                            return all.substring(q_start).trim();
                        }
                        return '';
                    }""")
                except:
                    answer = ""

                if answer and len(answer) > 10 and answer == prev:
                    stable += 1
                elif answer != prev:
                    stable = 0
                    prev = answer

                log(f"  [{i*3+3}s] stop={has_stop} ans_len={len(answer)} stable={stable}/2 prev_len={len(prev)}")

                if answer and len(answer) > 10 and stable >= 2 and (not has_stop):
                    log(f"  ✅ 等待完成! answer={len(answer)}c")
                    page.screenshot(path=str(OUTPUT / f"{AID}_final.png"), full_page=True)
                    break

            log(f"  最终 answer={len(answer)}c")

        # Save debug log
        with open(str(OUTPUT / f"{AID}_debug.json"), 'w') as f:
            json.dump({
                "agent_id": AID, "agent_name": ANAME,
                "send_confirmed": send_confirmed, "send_method": send_method,
                "log": debug_log,
            }, f, ensure_ascii=False, indent=2)
        log(f"日志保存: {OUTPUT}/{AID}_debug.json")

    except Exception as e:
        log(f"❌ Error: {e}")
        import traceback; traceback.print_exc()
        page.screenshot(path=str(OUTPUT / f"{AID}_error.png"), full_page=True)
