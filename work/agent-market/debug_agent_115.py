#!/usr/bin/env python3
"""调试 Agent 115 (DI问答助手): chat_wait 提前结束问题"""

import json, time, sys
from pathlib import Path
from playwright.sync_api import sync_playwright

AID = 115
ANAME = "DI问答助手"
URL = "https://aily.feishu.cn/agents/agent_4jn4cnjeurc3r"
QUESTION = "BI账号申请"
MAX_WAIT = 180
OUTPUT = Path("/home/node/.openclaw/workspace/work/agent-market/reports/debug")

OUTPUT.mkdir(parents=True, exist_ok=True)

debug_log = []
def log(msg, level="INFO"):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    debug_log.append({"timestamp": ts, "level": level, "message": msg})

def get_body(page):
    try: return page.evaluate("() => document.body ? document.body.innerText : ''")
    except: return ""

def count_assistant_nodes(page):
    """统计 Aily 平台的 assistant 消息节点"""
    try:
        return page.evaluate("""() => {
            const all = document.querySelectorAll('[class*="message"], [class*="bubble"], [class*="chat-item"], [class*="assistant"], [class*="ai-message"], [role="assistant"]');
            return all.length;
        }""")
    except: return -1

def get_latest_assistant_text(page):
    """获取最新 assistant 消息文本和长度"""
    try:
        return page.evaluate("""() => {
            const nodes = document.querySelectorAll('[class*="assistant"], [class*="ai-message"], [class*="bot-message"], [role="assistant"]');
            if (nodes.length === 0) {
                // Aily 特殊选择器
                const msgs = document.querySelectorAll('[class*="message"]');
                let last = null;
                msgs.forEach(m => {
                    const txt = m.textContent || '';
                    if (txt.length > 10 && !txt.includes('DI问答助手') && !txt.includes('为你服务')) {
                        last = txt.trim();
                    }
                });
                if (last) return {text: last, length: last.length, node_count: msgs.length};
                return {text: '', length: 0, node_count: 0};
            }
            const last = nodes[nodes.length - 1];
            const txt = (last.textContent || '').trim();
            return {text: txt, length: txt.length, node_count: nodes.length};
        }""")
    except Exception as e:
        return {"text": "", "length": 0, "node_count": -1, "error": str(e)}

def is_generating(page):
    body = get_body(page)
    return "Stop generating" in body or "停止生成" in body or "停止回答" in body

def detect_input(page):
    for sel in ["textarea", "[contenteditable]", "input[type='text']"]:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                return sel, el
        except: pass
    # Aily 特殊：role=textbox
    try:
        el = page.query_selector('[role="textbox"]')
        if el and el.is_visible():
            return '[role="textbox"]', el
    except: pass
    return None, None

def try_send(page, input_el, sel):
    """尝试发送消息"""
    # 方法1: 查找发送按钮
    send_selectors = [
        'button[type="submit"]',
        'button:has(svg)',
        '[class*="send"]',
        '[aria-label*="send" i]',
        '[aria-label*="发送" i]',
        'svg[class*="send"]',
    ]
    for ss in send_selectors:
        try:
            btn = page.query_selector(ss)
            if btn and btn.is_visible():
                log(f"  点击发送按钮: {ss}")
                btn.click()
                time.sleep(1)
                return True
        except: continue

    # 方法2: Enter
    log(f"  按 Enter 发送")
    input_el.press("Enter")
    time.sleep(1)
    return True

with sync_playwright() as pw:
    browser = pw.chromium.connect_over_cdp("http://127.0.0.1:18800")
    page = browser.contexts[0].new_page()
    page.set_viewport_size({"width": 1280, "height": 900})

    try:
        # ═══════════════════════════════════════
        # STEP 0: 打开页面
        # ═══════════════════════════════════════
        log(f"===== Agent {AID}: {ANAME} 调试 =====")
        log(f"STEP 0: 打开 {URL}")
        page.goto(URL, wait_until="domcontentloaded", timeout=30000)
        time.sleep(5)

        body = get_body(page)
        log(f"  URL: {page.url}")
        log(f"  Body({len(body)}): {body[:150]}")

        input_sel, input_el = detect_input(page)
        log(f"  输入框: {input_sel}")

        if not input_el:
            log("  ❌ 未找到输入框", "ERROR")
            page.screenshot(path=str(OUTPUT / f"{AID}_no_input.png"), full_page=True)
            raise Exception("No input found")

        # ═══════════════════════════════════════
        # STEP 1: 发送前状态记录
        # ═══════════════════════════════════════
        log("===== STEP 1: 发送前状态 =====")
        pre_url = page.url
        pre_body_len = len(get_body(page))
        pre_msg_count = count_assistant_nodes(page)
        pre_assistant = get_latest_assistant_text(page)
        pre_stop = is_generating(page)

        log(f"  URL: {pre_url}")
        log(f"  body长度: {pre_body_len}")
        log(f"  消息节点数: {pre_msg_count}")
        log(f"  assistant节点数: {pre_assistant.get('node_count', -1)}")
        log(f"  Stop generating: {pre_stop}")
        log(f"  最新assistant: {pre_assistant.get('text','')[:100]}")
        log(f"  最新assistant长度: {pre_assistant.get('length', 0)}")

        # ═══════════════════════════════════════
        # STEP 2: 发送问题
        # ═══════════════════════════════════════
        log(f"===== STEP 2: 发送问题: {QUESTION} =====")

        # 聚焦输入框
        input_el.click()
        time.sleep(0.3)

        if input_sel == "[contenteditable]":
            input_el.evaluate("el => { el.innerHTML = ''; el.focus(); }")
            page.keyboard.type(QUESTION, delay=30)
        else:
            input_el.fill("")
            time.sleep(0.1)
            input_el.fill(QUESTION)

        time.sleep(0.5)

        # 验证输入框内容
        if input_sel == "[contenteditable]":
            input_text = input_el.inner_text().strip()
        else:
            input_text = input_el.input_value().strip()

        log(f"  输入框内容: '{input_text[:100]}'")

        if QUESTION not in input_text and input_text not in QUESTION:
            log(f"  ⚠️ 输入框内容不匹配，重新尝试...")
            input_el.click()
            time.sleep(0.3)
            page.keyboard.type(QUESTION, delay=50)
            time.sleep(0.5)

        # 发送
        try_send(page, input_el, input_sel)

        # ═══════════════════════════════════════
        # STEP 2b: 确认用户消息已发送
        # ═══════════════════════════════════════
        time.sleep(2)
        body = get_body(page)
        sent_confirmed = QUESTION[:6] in body
        post_msg_count = count_assistant_nodes(page)
        log(f"  用户消息确认: {sent_confirmed} (页面含'{QUESTION[:8]}'={QUESTION[:8] in body})")
        log(f"  消息节点数变化: {pre_msg_count} → {post_msg_count}")

        # 如果确认失败，再尝试
        if not sent_confirmed:
            log("  ⚠️ 未确认发送，重新尝试...")
            input_el.click()
            time.sleep(0.3)
            page.keyboard.type(QUESTION, delay=50)
            time.sleep(0.5)
            try_send(page, input_el, input_sel)
            time.sleep(2)
            body = get_body(page)
            sent_confirmed = QUESTION[:6] in body
            log(f"  再次确认: {sent_confirmed}")

        if not sent_confirmed:
            log("  ❌ 发送确认失败", "ERROR")
            page.screenshot(path=str(OUTPUT / f"{AID}_send_failed.png"), full_page=True)

        # ═══════════════════════════════════════
        # STEP 3: 等待回答 (最长180s)
        # ═══════════════════════════════════════
        log(f"===== STEP 3: 等待回答 (max {MAX_WAIT}s) =====")

        start_time = time.time()
        stop_was_seen = False
        stop_gone = False
        new_assistant_seen = False
        prev_answer = ""
        stable_count = 0
        STABLE_REQUIRED = 2  # 连续2次相同才算稳定

        poll_count = 0
        answer_text = ""
        final_state = ""

        while time.time() - start_time < MAX_WAIT:
            elapsed = round(time.time() - start_time, 1)
            poll_count += 1

            body = get_body(page)
            stop_now = is_generating(page)
            assistant = get_latest_assistant_text(page)
            answer_now = assistant.get("text", "")
            nodes_now = assistant.get("node_count", -1)

            if stop_now and not stop_was_seen:
                stop_was_seen = True
                log(f"  [{elapsed}s] 🛑 Stop generating 首次出现")

            if stop_was_seen and not stop_now:
                if not stop_gone:
                    stop_gone = True
                    log(f"  [{elapsed}s] ✅ Stop generating 已消失")

            if answer_now and len(answer_now) > 10 and not new_assistant_seen:
                new_assistant_seen = True
                log(f"  [{elapsed}s] 📝 检测到新回答 ({len(answer_now)}chars)")

            # 每3秒记录详细状态
            if poll_count % 3 == 0 or stop_gone:
                log(f"  [{elapsed}s] stop={stop_now} nodes={nodes_now} ans_len={len(answer_now)} "
                    f"prev_len={len(prev_answer)} stable={stable_count}/{STABLE_REQUIRED} "
                    f"ans_preview={answer_now[:100]}")

            # 稳定性检查
            if answer_now and answer_now == prev_answer and len(answer_now) > 10:
                stable_count += 1
            elif answer_now != prev_answer:
                stable_count = 0
                prev_answer = answer_now

            # 完成条件：stop曾出现 + stop已消失 + 有回答 + 连续稳定
            all_done = (stop_was_seen and stop_gone
                        and answer_now and len(answer_now) > 10
                        and stable_count >= STABLE_REQUIRED)

            if all_done:
                answer_text = answer_now
                final_state = "completed"
                log(f"  [{elapsed}s] 🎉 等待完成！回答={len(answer_text)}chars")
                break

            time.sleep(3)

        # 超时处理
        if final_state != "completed":
            elapsed = round(time.time() - start_time, 1)
            assistant = get_latest_assistant_text(page)
            answer_text = assistant.get("text", "")
            final_state = "timeout"
            log(f"  ⏰ 等待超时 elapsed={elapsed}s stop_was_seen={stop_was_seen} "
                f"stop_gone={stop_gone} answer_len={len(answer_text)} "
                f"answer={answer_text[:100]}")

        # ═══════════════════════════════════════
        # STEP 4: 最终截图
        # ═══════════════════════════════════════
        body_final = get_body(page)
        ss_path = str(OUTPUT / f"{AID}_final.png")
        page.screenshot(path=ss_path, full_page=True)
        log(f"===== STEP 4: 最终截图 {ss_path} =====")
        log(f"  body长度: {len(body_final)}")
        log(f"  Stop generating: {is_generating(page)}")
        log(f"  回答长度: {len(answer_text)}")

        # 保存调试日志
        result = {
            "agent_id": AID,
            "agent_name": ANAME,
            "status": "pass" if final_state == "completed" else ("blocked" if final_state == "timeout" else "fail"),
            "question_text": QUESTION,
            "answer_text": answer_text,
            "elapsed": round(time.time() - start_time, 1),
            "final_state": final_state,
            "stop_was_seen": stop_was_seen,
            "stop_gone": stop_gone,
            "screenshot": ss_path,
            "debug_log": debug_log,
        }
        log_path = OUTPUT / f"{AID}_debug.json"
        with open(log_path, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2, default=str)
        log(f"  调试日志: {log_path}")

        print(f"\n{'='*40}")
        print(f"结果: status={result['status']} state={final_state} elapsed={result['elapsed']}s answer={len(answer_text)}c")
        print(json.dumps(result, ensure_ascii=False, indent=2))

    except Exception as e:
        log(f"❌ 异常: {e}", "ERROR")
        import traceback; traceback.print_exc()
        try:
            page.screenshot(path=str(OUTPUT / f"{AID}_error.png"), full_page=True)
        except: pass
PYEOF