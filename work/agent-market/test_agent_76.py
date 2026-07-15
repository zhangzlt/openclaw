#!/usr/bin/env python3
"""Agent 76 独立验证脚本 — 使用与生产巡检相同的浏览器 profile"""
import sys, os, json, time
from pathlib import Path

# 设置路径
WORKSPACE = Path("/home/node/.openclaw/workspace")
sys.path.insert(0, str(WORKSPACE / "work"))  # for agent_browser_wrapper
sys.path.insert(0, str(WORKSPACE / "work/agent-market"))

from agent_browser_wrapper.browser import AgentBrowser, AgentBrowserError
from inspect_daily import _handle_feishu_authorize, normalize_chat_evidence, _bind_result

# ── 使用生产 profile ──
FEISHU_BROWSER_PROFILE = WORKSPACE / "work/agent-market/.auth/feishu-browser-profile"
PLAYWRIGHT_STATE = WORKSPACE / "work/agent-market/.auth/playwright_state.json"

profile_path = str(FEISHU_BROWSER_PROFILE) if FEISHU_BROWSER_PROFILE.is_dir() else None
state_path = str(PLAYWRIGHT_STATE) if PLAYWRIGHT_STATE.is_file() else None

print(f"🔧 Profile: {profile_path or '无'}")
print(f"🔧 State: {state_path or '无'}")

AGENT_ID = 76
QUESTION = "介绍神州问学"
AGENT_URL = f"https://agent.digitalchina.com/widget/open?agentId={AGENT_ID}"

OUTPUT_DIR = WORKSPACE / "work/agent-market/reports/runs/test_76"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

session = f"validate-76-{int(time.time())}"
browser = AgentBrowser(
    state_path=state_path,
    profile_path=profile_path,
    session=session,
)

result = {
    "agent_id": AGENT_ID,
    "_test_type": "chat",
    "test_question": QUESTION,
    "status": "pending",
    "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
}

try:
    # ── Step 1: 打开页面 ──
    print(f"\n📂 打开 Agent {AGENT_ID}: {AGENT_URL}")
    browser.open(AGENT_URL, wait_sec=5, wait_timeout=15)
    time.sleep(2)
    
    body = browser.get_body_text()
    current_url = browser.get_url()
    print(f"   页面 URL: {current_url}")
    print(f"   body 长度: {len(body)}")

    # ── Step 2: 处理飞书授权 ──
    if "accounts.feishu.cn" in current_url or "Authorize" in body or "授权" in body:
        print(f"   🔐 检测到授权页，尝试自动点击...")
        authorized = _handle_feishu_authorize(browser, AGENT_URL)
        if authorized:
            print(f"   ✅ 授权通过")
            time.sleep(3)
            body = browser.get_body_text()
            current_url = browser.get_url()
            print(f"   新 URL: {current_url}")
        else:
            print(f"   ❌ 授权失败，当前 URL: {current_url}")
            print(f"   body 片段: {body[:300]}")
    
    # ── Step 3: 检测聊天输入框 ──
    input_sel, input_type = browser._detect_chat_input()
    print(f"\n⌨️ 输入框: {input_sel} (type={input_type})")
    
    if not input_sel:
        # 截图看页面状态
        browser.screenshot(str(OUTPUT_DIR / f"{AGENT_ID}_state.png"))
        print("   截图已保存")
        raise RuntimeError("未检测到聊天输入框")

    # ── Step 4: 发送问题前记录 body ──
    body_before = browser.get_body_text()
    print(f"\n✉️ 发送问题: {QUESTION}")

    start_time = time.time()
    browser.chat_send(QUESTION)
    send_elapsed = time.time() - start_time
    print(f"   消息发送耗时: {send_elapsed:.1f}s")

    # ── Step 5: 等待回答（使用新的 chat_wait）──
    print(f"\n⏳ 等待回答（最长 180s）...")
    wait_start = time.time()
    wait_result = browser.chat_wait(
        timeout=180,
        poll_interval=3.0,
        question=QUESTION,
        body_before=body_before,
        agent_url=current_url,
    )
    wait_elapsed = time.time() - wait_start
    
    answer_text = wait_result.get("answer_text", "")
    wait_status = wait_result.get("status", "unknown")
    
    print(f"\n📊 回答提取结果:")
    print(f"   status: {wait_status}")
    print(f"   等待: {wait_result.get('waited', 0):.1f}s")
    print(f"   stop_seen: {wait_result.get('stop_seen')}")
    print(f"   stop_gone: {wait_result.get('stop_gone')}")
    print(f"   answer_text 长度: {len(answer_text)}")
    print(f"   answer_text 前300字:\n{answer_text[:300]}")

    # ── Step 6: 截图（回答完成后）──
    screenshot_path = str(OUTPUT_DIR / f"{AGENT_ID}_final.png")
    browser.screenshot(screenshot_path)
    # 确认页面没有 Stop generating
    final_body = browser.get_body_text()
    has_stop = any(s in final_body for s in ["Stop generating", "停止生成", "停止回答"])
    print(f"\n📸 截图: {screenshot_path}")
    print(f"   截图中仍有 Stop generating: {has_stop}")
    print(f"   最终 body 长度: {len(final_body)}")

    # ── Step 7: 构建结果 ──
    result.update({
        "answer_text": answer_text,
        "answer_source": f"DOM:{wait_status}" if answer_text else "无",
        "status": "ok" if wait_status == "complete" and answer_text else "chat_error",
        "elapsed_seconds": round(wait_elapsed + send_elapsed, 1),
        "wait_status": wait_status,
        "stop_gone_at_capture": not has_stop,
        "screenshot": screenshot_path,
        "images": [screenshot_path],
        "q_results": [{
            "question": QUESTION,
            "response": answer_text,
            "wait_status": wait_status,
            "success": bool(answer_text),
        }],
        "test_operation": f"对话测试: {QUESTION}" if not bool(answer_text) else QUESTION,
        "test_result": answer_text[:500] if answer_text else "未获取到回答",
        "test_analysis": ("生成完成，已采集回答" if wait_status == "complete"
                          else f"生成未完成(status={wait_status})" if answer_text
                          else "未采集到智能体回答"),
    })

except Exception as e:
    result["status"] = "error"
    result["error"] = str(e)
    result["test_analysis"] = f"测试异常: {e}"
    import traceback
    traceback.print_exc()

finally:
    browser.close()
    print("\n🛑 浏览器已关闭")

# ── Step 8: 归一化证据 ──
normalize_chat_evidence(result)
agent_mock = {"id": AGENT_ID, "name": "神州问学知识库回答助手"}
result = _bind_result(result, agent_mock, 99)

# ── Step 9: 输出结果 ──
result_path = OUTPUT_DIR / "result.json"
with open(result_path, 'w', encoding='utf-8') as f:
    json.dump(result, f, ensure_ascii=False, indent=2)

print(f"\n{'='*60}")
print(f"📋 最终结果 (保存至 {result_path}):")
for k in ["status", "_test_type", "question_text", "answer_text", 
           "answer_source", "elapsed_seconds", "screenshot", 
           "test_analysis", "stop_gone_at_capture"]:
    v = result.get(k, "")
    if isinstance(v, str) and len(v) > 100:
        v = v[:100] + "..."
    print(f"  {k}: {repr(v)}")

print(f"\n  answer_text 非空: {bool(result.get('answer_text'))}")
print(f"  answer_text 长度: {len(result.get('answer_text', ''))}")
print(f"  status: {result.get('status')}")

success = (
    result.get("status") in ("ok", "chat_error") and
    result.get("_test_type") == "chat" and
    result.get("question_text") == QUESTION and
    bool(result.get("answer_text")) and
    result.get("screenshot")
)
print(f"\n{'✅ 验收通过' if success else '❌ 验收未通过'}")
