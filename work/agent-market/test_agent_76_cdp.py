#!/usr/bin/env python3
"""Agent 76 验收 — 使用 OpenClaw CDP 浏览器（已登录）直接测试回答提取"""
import sys, json, time, os, base64
from pathlib import Path

WORKSPACE = Path("/home/node/.openclaw/workspace")
sys.path.insert(0, str(WORKSPACE / "work/agent-market"))

# ── 使用 CDP 浏览器（端口 18800）──
# 先临时安装 playwright 包（只用 evaluate + screenshot）
import subprocess
subprocess.run([sys.executable, "-m", "pip", "install", "playwright", "-q"], capture_output=True)

from playwright.sync_api import sync_playwright

AGENT_ID = 76
QUESTION = "介绍神州问学"
AGENT_URL = f"https://agent.digitalchina.com/widget/open?agentId={AGENT_ID}"

OUTPUT_DIR = WORKSPACE / "work/agent-market/reports/runs/test_76_cdp"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

print(f"🔗 连接 CDP: ws://127.0.0.1:18800")

with sync_playwright() as pw:
    browser = pw.chromium.connect_over_cdp("http://127.0.0.1:18800")
    context = browser.contexts[0]
    
    # 获取现有页面或创建新页面
    if context.pages:
        page = context.new_page()
    else:
        page = context.pages[0]
    
    page.set_viewport_size({"width": 1280, "height": 900})
    
    # ── Step 1: 打开 agent 76 ──
    print(f"\n📂 打开: {AGENT_URL}")
    page.goto(AGENT_URL, wait_until="networkidle", timeout=30000)
    time.sleep(5)
    
    body = page.evaluate("document.body ? document.body.innerText : ''")
    url = page.url
    print(f"   URL: {url}")
    print(f"   body 长度: {len(body)}")
    
    # ── Step 2: 检测授权页面 ──
    if "accounts.feishu.cn" in url or "Authorize" in body or "授权" in body:
        print(f"   🔐 授权页，尝试点击 Authorize...")
        try:
            # 找 Authorize/授权按钮
            for btn_text in ["Authorize", "授权", "确认授权", "允许"]:
                btn = page.get_by_role("button", name=btn_text)
                if btn.count() > 0:
                    btn.first.click()
                    print(f"   ✅ 点击了 '{btn_text}'")
                    break
            time.sleep(3)
            # 可能需要重新导航
            page.goto(AGENT_URL, wait_until="networkidle", timeout=30000)
            time.sleep(5)
            body = page.evaluate("document.body ? document.body.innerText : ''")
            url = page.url
            print(f"   新 URL: {url}")
            print(f"   新 body 长度: {len(body)}")
        except Exception as e:
            print(f"   ⚠️ 授权处理异常: {e}")
    
    # ── Step 3: 找到聊天输入框 ──
    input_sel = None
    for sel in ("[contenteditable]", "textarea", "input[type='text']", "input:not([type])"):
        exists = page.evaluate(f"!!document.querySelector('{sel}')")
        if exists:
            input_sel = sel
            print(f"\n⌨️ 找到输入框: {sel}")
            break
    
    if not input_sel:
        # 截图看状态
        page.screenshot(path=str(OUTPUT_DIR / "state.png"))
        print("   无输入框，截图已保存")
        raise RuntimeError("未检测到聊天输入框")
    
    # ── Step 4: 记录初始状态 ──
    body_before = page.evaluate("document.body ? document.body.innerText : ''")
    existing_ids = page.evaluate("""
        JSON.stringify(Array.from(document.querySelectorAll(
            '[class*="message"], [class*="msg"], [class*="chat"], [class*="answer"], ' +
            '[class*="assistant"], [class*="reply"], [class*="response"], [class*="bubble"]'
        )).map((_, i) => i))
    """)
    existing_count = len(json.loads(existing_ids)) if existing_ids else 0
    print(f"   已有消息节点: {existing_count}")
    
    # ── Step 5: 发送问题 ──
    print(f"\n✉️ 发送: {QUESTION}")
    start = time.time()
    
    el = page.query_selector(input_sel)
    el.click()
    time.sleep(0.3)
    
    if input_sel == "[contenteditable]":
        el.evaluate(f"el => {{ el.innerText = ''; el.focus(); }}")
        page.keyboard.type(QUESTION, delay=20)
    else:
        el.fill("")
        el.fill(QUESTION)
        el.evaluate("el => { el.dispatchEvent(new Event('input', {bubbles: true})); }")
    
    time.sleep(0.5)
    
    # 尝试发送
    sent = False
    # 先找发送按钮
    for btn_sel in ("button[aria-label*='发送']", "button[aria-label*='Send']"):
        btn = page.query_selector(btn_sel)
        if btn:
            btn.click()
            sent = True
            break
    if not sent:
        page.keyboard.press("Enter")
    
    send_time = time.time() - start
    print(f"   发送耗时: {send_time:.1f}s")
    
    # ── Step 6: 等待生成 ──
    print(f"\n⏳ 等待回答...")
    wait_start = time.time()
    stop_keywords = ["Stop generating", "停止生成", "停止回答"]
    
    stop_seen = False
    stop_gone = False
    answer_text = ""
    status = "unknown"
    waited = 0
    
    # 阶段1: 等 stop 出现 → 消失
    max_wait = 180
    while waited < max_wait:
        time.sleep(3)
        waited += 3
        try:
            latest = page.evaluate("document.body ? document.body.innerText : ''")
        except:
            continue
        
        has_stop = any(s in latest for s in stop_keywords)
        
        if not stop_seen and has_stop:
            stop_seen = True
            print(f"   ⏳ Stop generating 出现 (t={waited}s)")
            continue
        
        if stop_seen and not has_stop:
            stop_gone = True
            print(f"   ✅ Stop generating 消失 (t={waited}s)")
            break
        
        if waited > max_wait * 0.5 and not stop_seen:
            pass  # 可能不需要等待
    
    # 阶段2: 等内容稳定
    if stop_gone:
        prev = ""
        stable = 0
        while waited < max_wait:
            time.sleep(3)
            waited += 3
            try:
                latest = page.evaluate("document.body ? document.body.innerText : ''")
            except:
                continue
            
            # 再次确认
            if any(s in latest for s in stop_keywords):
                stable = 0
                prev = ""
                continue
            
            # 提取回答
            current = page.evaluate(f"""
                (() => {{
                    const sel = '[class*="assistant"], [class*="answer"], [class*="reply"], [class*="response"], [class*="message"]';
                    const containers = document.querySelectorAll(sel);
                    if (containers.length === 0) {{
                        const body = document.body.innerText || '';
                        const q = {json.dumps(QUESTION[:20])};
                        const idx = body.indexOf(q);
                        return idx >= 0 ? body.substring(idx + q.length).trim().substring(0, 5000) : '';
                    }}
                    const el = containers[containers.length - 1];
                    const clone = el.cloneNode(true);
                    clone.querySelectorAll('button, nav, input, textarea, [contenteditable], ' +
                        '[class*="source"], [class*="reference"], [class*="action"], [class*="toolbar"]').forEach(n => n.remove());
                    return (clone.textContent || '').trim();
                }})()
            """)
            
            if current and len(current) >= 10 and current == prev:
                stable += 1
                if stable >= 2:
                    answer_text = current
                    status = "complete"
                    break
            else:
                stable = 0
                prev = current
    
    # 超时处理
    if not answer_text:
        # 尝试直接提取
        answer_text = page.evaluate(f"""
            (() => {{
                const sel = '[class*="assistant"], [class*="answer"], [class*="reply"], [class*="response"]';
                const containers = document.querySelectorAll(sel);
                for (let i = containers.length - 1; i >= 0; i--) {{
                    const clone = containers[i].cloneNode(true);
                    clone.querySelectorAll('button, nav, input, textarea, [contenteditable]').forEach(n => n.remove());
                    const txt = (clone.textContent || '').trim();
                    if (txt.length > 50 && !txt.includes({json.dumps(QUESTION[:30])})) return txt;
                }}
                return '';
            }})()
        """)
        status = "timeout" if answer_text else "empty"
    
    elapsed = time.time() - wait_start
    print(f"\n📊 结果:")
    print(f"   status: {status}")
    print(f"   waited: {waited:.1f}s")
    print(f"   stop_seen: {stop_seen}")
    print(f"   stop_gone: {stop_gone}")
    print(f"   answer 长度: {len(answer_text)}")
    print(f"   answer 前200字: {answer_text[:200]}")
    
    # ── Step 7: 截图 ──
    screenshot_path = str(OUTPUT_DIR / f"{AGENT_ID}_final.png")
    page.screenshot(path=screenshot_path)
    final_body = page.evaluate("document.body ? document.body.innerText : ''")
    has_stop = any(s in final_body for s in stop_keywords)
    print(f"\n📸 截图: {screenshot_path}")
    print(f"   仍有 Stop generating: {has_stop}")
    
    # ── Step 8: 构建结果 ──
    result = {
        "agent_id": AGENT_ID,
        "agent_name": "神州问学知识库回答助手",
        "inspection_index": 99,
        "run_id": f"cdp_test_{int(time.time())}",
        "status": "ok" if status == "complete" and answer_text else ("chat_error" if answer_text else "error"),
        "_test_type": "chat",
        "question_text": QUESTION,
        "answer_text": answer_text,
        "answer_source": f"CDP_DOM:{status}",
        "elapsed_seconds": round(elapsed + send_time, 1),
        "wait_status": status,
        "stop_seen": stop_seen,
        "stop_gone": stop_gone,
        "stop_at_screenshot": has_stop,
        "screenshot": screenshot_path,
        "images": [screenshot_path],
        "q_results": [{
            "question": QUESTION,
            "response": answer_text,
            "success": bool(answer_text),
            "wait_status": status,
        }],
        "test_question": QUESTION,
        "test_operation": QUESTION,
        "agent_answer": answer_text,
        "test_result": answer_text[:500] if answer_text else "未获取到回答",
        "test_analysis": (f"生成完成，回答长度{len(answer_text)}字" if status == "complete"
                          else f"生成超时(status={status})，已采集部分回答" if answer_text
                          else "未采集到智能体回答"),
    }
    
    # ── 归一化 ──
    from inspect_daily import normalize_chat_evidence, _bind_result
    normalize_chat_evidence(result)
    
    agent_mock = {"id": AGENT_ID, "name": "神州问学知识库回答助手"}
    result = _bind_result(result, agent_mock, 99)
    
    # ── Step 9: 保存 ──
    result_path = OUTPUT_DIR / "result.json"
    with open(result_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    
    print(f"\n{'='*60}")
    print(f"📋 最终结果:")
    for k in ["status", "_test_type", "question_text", "answer_text", 
               "answer_source", "elapsed_seconds", "screenshot", "test_analysis"]:
        v = result.get(k, "")
        if isinstance(v, str) and len(v) > 80:
            v = v[:80] + "..."
        print(f"  {k}: {repr(v)}")
    
    success = (
        result.get("_test_type") == "chat" and
        result.get("question_text") == QUESTION and
        bool(result.get("answer_text")) and
        result.get("screenshot") and
        status == "complete"
    )
    print(f"\n{'✅ 验收通过' if success else '⚠️ 部分通过'}")
    print(f"\n结果 JSON: {result_path}")
