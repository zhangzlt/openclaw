#!/usr/bin/env python3
"""Agent 76 完整验收 — Aily 平台，使用 CDP"""
from playwright.sync_api import sync_playwright
import json, time, sys
from pathlib import Path

WORKSPACE = Path("/home/node/.openclaw/workspace")
sys.path.insert(0, str(WORKSPACE / "work/agent-market"))

OUTPUT_DIR = WORKSPACE / "work/agent-market/reports/runs/test_76_aily"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

QUESTION = "介绍神州问学"
AILY_URL = "https://aily.feishu.cn/agents/agent_4jccvuk6yqb1y"

print(f"🔗 连接 CDP 浏览器...")

with sync_playwright() as pw:
    browser = pw.chromium.connect_over_cdp("http://127.0.0.1:18800")
    page = browser.contexts[0].new_page()
    page.set_viewport_size({"width": 1280, "height": 900})

    # ── Step 1: 打开页面 ──
    print(f"\n📂 打开: {AILY_URL}")
    page.goto(AILY_URL, wait_until="domcontentloaded", timeout=30000)
    time.sleep(8)
    print(f"   URL: {page.url}")
    
    # ── Step 2: 找输入框 ──
    input_sel = None
    for sel in ["[contenteditable]", "textarea", "input[type='text']", "input:not([type])"]:
        try:
            if page.evaluate(f"!!document.querySelector({json.dumps(sel)})"):
                input_sel = sel
                break
        except:
            continue
    
    if not input_sel:
        body = page.evaluate("() => document.body ? document.body.innerText : ''")
        print(f"   ❌ 无输入框 body={body[:200]}")
        page.screenshot(path=str(OUTPUT_DIR / "no_input.png"))
        raise SystemExit(1)
    
    print(f"   ⌨️ 输入框: {input_sel}")

    # ── Step 3: 点建议问题 "📋 介绍神州问学" ──
    try:
        sugg = page.get_by_text("📋 介绍神州问学").first
        if sugg and sugg.is_visible():
            print(f"\n   💡 点击建议问题: 📋 介绍神州问学")
            sugg.click()
            time.sleep(2)
    except:
        # fallback: type manually
        pass

    # ── Step 4: 记录发送前的状态 ──
    body_before = page.evaluate("() => document.body ? document.body.innerText : ''")
    # 记录现有消息容器数量
    existing_count = page.evaluate("""
        () => document.querySelectorAll('[class*="message"], [class*="Message"], [class*="chat-message"], [class*="assistant"], [class*="Assistant"], [class*="answer"], [class*="reply"], [class*="response"]').length
    """)
    print(f"   发送前 body 长度: {len(body_before)}")
    print(f"   现有消息容器: {existing_count}")

    # ── Step 5: 如果建议点没发起，手动输入 ──
    current_body = page.evaluate("() => document.body ? document.body.innerText : ''")
    msg_count_now = page.evaluate("""
        () => document.querySelectorAll('[class*="message"], [class*="Message"], [class*="assistant"], [class*="answer"]').length
    """)
    
    if msg_count_now <= existing_count:
        # 手动发送
        print(f"   ✉️ 手动输入: {QUESTION}")
        el = page.query_selector(input_sel)
        el.click()
        time.sleep(0.5)
        
        if input_sel == "[contenteditable]":
            page.keyboard.type(QUESTION, delay=20)
        else:
            el = page.query_selector(input_sel)
            el.fill(QUESTION)
            el.evaluate("el => el.dispatchEvent(new Event('input', {bubbles: true}))")
        
        time.sleep(0.5)
        
        # 点发送
        sent = False
        for btn_text in ["发送", "Send"]:
            try:
                btn = page.get_by_role("button", name=btn_text).first
                if btn and btn.is_visible():
                    btn.click()
                    sent = True
                    break
            except:
                pass
        if not sent:
            page.keyboard.press("Enter")
    
    send_time = time.time()
    print(f"   发送完成 at {send_time}")

    # ── Step 6: 等待生成 ──
    stop_keywords = ["Stop generating", "停止生成", "停止回答"]
    max_wait = 180
    stop_seen = False
    stop_gone = False
    answer_text = ""
    status = "unknown"
    waited = 0
    prev_answer = ""
    stable_count = 0

    print(f"\n⏳ 等待回答生成...")
    
    while waited < max_wait:
        time.sleep(3)
        waited += 3
        try:
            body = page.evaluate("() => document.body ? document.body.innerText : ''")
        except:
            body = ""
        
        has_stop = any(s in body for s in stop_keywords)
        
        if not stop_seen and has_stop:
            stop_seen = True
            print(f"   ⏳ Stop generating 出现 (t={waited}s)")
            continue
        
        if stop_seen and not has_stop:
            stop_gone = True
            print(f"   ✅ Stop generating 消失 (t={waited}s)")
            break
        
        if waited >= max_wait and not stop_seen:
            break
    
    # 阶段2: 等内容稳定 + 提取回答
    if stop_gone:
        print(f"   等待内容稳定...")
        while waited < max_wait:
            time.sleep(3)
            waited += 3
            body = page.evaluate("() => document.body ? document.body.innerText : ''")
            
            if any(s in body for s in stop_keywords):
                stable_count = 0
                prev_answer = ""
                continue
            
            # 用 Aily 适配器提取（模拟 _extract_aily_answer）
            answer = page.evaluate("""
                (() => {
                    const selectors = [
                        '[class*="ThreadMessage-module__assistant"]',
                        '[class*="message-assistant"]',
                        '[class*="chat-message-assistant"]',
                        '[class*="MessageItem-assistant"]',
                        '[class*="chatMessage-assistant"]',
                        '[class*="assistantMessage"]',
                        '[class*="ai-message"]',
                        '[class*="bot-message"]',
                    ];
                    let containers = [];
                    for (const sel of selectors) {
                        containers = document.querySelectorAll(sel);
                        if (containers.length > 0) break;
                    }
                    if (containers.length === 0) return '';
                    const el = containers[containers.length - 1];
                    const clone = el.cloneNode(true);
                    clone.querySelectorAll('button, nav, [role="toolbar"], input, textarea, [contenteditable], [class*="source"], [class*="reference"], [class*="action"], [class*="toolbar"]').forEach(n => n.remove());
                    return (clone.textContent || '').trim();
                })()
            """)
            
            if answer and len(answer) >= 20 and answer == prev_answer:
                stable_count += 1
                if stable_count >= 2:
                    answer_text = answer
                    status = "complete"
                    break
            else:
                stable_count = 0
                prev_answer = answer
    
    elapsed = waited
    print(f"\n📊 回答提取结果:")
    print(f"   status: {status}")
    print(f"   waited: {elapsed}s")
    print(f"   stop_seen: {stop_seen}")
    print(f"   stop_gone: {stop_gone}")
    print(f"   stable_count: {stable_count}")
    print(f"   answer 长度: {len(answer_text)}")
    print(f"   answer 前300字:")
    for line in answer_text[:300].split('\n'):
        print(f"     {line}")

    # ── Step 7: 截图 ──
    screenshot_path = str(OUTPUT_DIR / f"76_final.png")
    page.screenshot(path=screenshot_path, full_page=True)
    final_body = page.evaluate("() => document.body ? document.body.innerText : ''")
    has_stop = any(s in final_body for s in stop_keywords)
    print(f"\n📸 截图: {screenshot_path}")
    print(f"   仍有 Stop generating: {has_stop}")

    # ── Step 8: 构建结果 ──
    import re
    cleaned_answer = answer_text
    # 去除 stop 按钮 & 导航
    for kw in stop_keywords + ["Deep Planning", "Tools", "AI can make mistakes", "Verify key details",
                                "神州问学知识库回答助手", "关联神州问学", "Suggested questions", 
                                "📋", "📚", "🔍", "神州问学案例", "神州问学产品介绍"]:
        cleaned_answer = cleaned_answer.replace(kw + '\n', '')
        cleaned_answer = cleaned_answer.replace(kw, '')
    cleaned_answer = re.sub(r'\n{3,}', '\n\n', cleaned_answer).strip()

    result = {
        "agent_id": 76,
        "agent_name": "神州问学知识库回答助手",
        "inspection_index": 99,
        "run_id": "aily_cdp_test",
        "status": "ok" if status == "complete" else "chat_error",
        "_test_type": "chat",
        "_platform": "aily",
        "question_text": QUESTION,
        "answer_text": cleaned_answer,
        "answer_source": f"aily_dom:{status}",
        "elapsed_seconds": round(elapsed, 1),
        "wait_status": status,
        "stop_seen": stop_seen,
        "stop_gone": stop_gone,
        "stop_at_screenshot": has_stop,
        "screenshot": screenshot_path,
        "images": [screenshot_path],
        "q_results": [{
            "question": QUESTION,
            "response": cleaned_answer,
            "success": bool(cleaned_answer),
            "wait_status": status,
        }],
        "test_question": QUESTION,
        "test_operation": QUESTION,
        "agent_answer": cleaned_answer,
        "test_result": cleaned_answer[:500] if cleaned_answer else "未获取到回答",
        "test_analysis": (f"生成完成，回答长度{len(cleaned_answer)}字" if status == "complete"
                          else f"生成超时，已采集部分回答" if cleaned_answer
                          else "未采集到智能体回答"),
    }

    # ── 归一化 ──
    from inspect_daily import normalize_chat_evidence, _bind_result
    normalize_chat_evidence(result)
    agent_mock = {"id": 76, "name": "神州问学知识库回答助手"}
    result = _bind_result(result, agent_mock, 99)

    # ── Step 9: 保存 ──
    result_path = OUTPUT_DIR / "result.json"
    with open(result_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}")
    print(f"📋 最终验收结果:")
    for k in ["status", "_test_type", "question_text", "answer_text",
               "answer_source", "elapsed_seconds", "stop_seen", "stop_gone",
               "stop_at_screenshot", "screenshot"]:
        v = result.get(k, "")
        if isinstance(v, str) and len(v) > 80:
            v = v[:80] + "..."
        print(f"  {k}: {repr(v)}")

    success = (
        result.get("_test_type") == "chat" and
        result.get("question_text") == QUESTION and
        bool(result.get("answer_text")) and
        "介绍" in (result.get("answer_text", "") or "")[:10] or "神州" in (result.get("answer_text", "") or "")[:50] and
        status == "complete" and
        not has_stop
    )
    print(f"\n{'✅ 全部验收通过！' if success else '⚠️ 部分条件未满足'}")
    print(f"结果 JSON: {result_path}")
