#!/usr/bin/env python3
"""系统性诊断所有问题智能体，保存完整诊断数据"""
import sys, os, time, json, subprocess, re
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from agent_browser_wrapper.browser import AgentBrowser
from inspect_daily import _agent_browser_auth_kwargs, _handle_feishu_authorize

DIAG_DIR = Path(__file__).parent / "diag" / datetime.now().strftime("%Y%m%d_%H%M%S")
DIAG_DIR.mkdir(parents=True, exist_ok=True)
STATE = str(Path(__file__).parent / ".auth" / "playwright_state.json")

# ── 已知智能体URL（从之前测试获取）──
KNOWN_URLS = {
    76: "https://aily.feishu.cn/agents/agent_4jccvuk6yqb1y",
    72: "https://aily.feishu.cn/agents/agent_4jy8r20jaknhz",
    115: "https://aily.feishu.cn/agents/agent_4jn4cnjeurc3r",
    106: "https://aily.feishu.cn/agents/agent_4k4mhq6d81p8a",
    119: "https://aily.feishu.cn/agents/agent_4juccukrzuvxt",
    83: "https://bba12hub36.feishuapp.cn/ai/gui/chat/a_c0021ea58f72473384fa50e8636a43a3",
    73: "https://bba12hub36.feishuapp.cn/ai/gui/chat/a_ea846e95d9e645129b6049b74b3cfd04",
    125: "https://bba12hub36.aiforce.cloud/app/app_4k5wq7xt5hv9f",
}

# ── 问题智能体分组 ──
GROUP_A = [98, 125]          # 类型识别错误
GROUP_B = [119, 115, 108, 106, 105, 101, 95, 92, 85, 82, 79, 78, 76, 72]  # 问题未发送
GROUP_C = [99, 110, 109, 83, 74, 73]  # 回答未提取

ALL = GROUP_A + GROUP_B + GROUP_C

def get_url(aid):
    if aid in KNOWN_URLS:
        return KNOWN_URLS[aid]
    return f"https://agent.digitalchina.com/widget/open?agentId={aid}"

def diagnose_agent(aid, browser):
    """打开智能体，完成授权，保存诊断数据"""
    print(f"\n{'='*60}")
    print(f"🔍 诊断 Agent {aid}")
    
    url = get_url(aid)
    print(f"   URL: {url[:100]}")
    
    try:
        browser.open(url, wait_sec=5, timeout=45)
    except Exception as e:
        print(f"   ❌ 打开失败: {e}")
        return {"agent_id": aid, "error": f"打开失败: {e}"}
    
    time.sleep(3)
    current_url = browser.get_url()
    body = browser.get_body_text()
    
    # 授权检测
    if "Authorize" in body or "授权" in body or "accounts.feishu.cn" in current_url:
        print(f"   🔐 检测到授权页，自动授权...")
        try:
            from inspect_daily import _handle_feishu_authorize
            browser.navigate_to(url)
            time.sleep(3)
            _handle_feishu_authorize(browser, url)
            time.sleep(5)
            body = browser.get_body_text()
            current_url = browser.get_url()
        except Exception as e:
            print(f"   ⚠️ 授权失败: {e}")
    
    # 截图
    diag = {
        "agent_id": aid,
        "url": current_url,
        "redirected": current_url != url,
        "timestamp": datetime.now().isoformat(),
        "platform": "unknown",
    }
    
    # 平台识别
    if "aily.feishu.cn" in current_url:
        diag["platform"] = "aily"
    elif "feishuapp.cn/ai/gui/chat" in current_url:
        diag["platform"] = "feishuapp"
    elif "aiforce.cloud" in current_url:
        diag["platform"] = "aiforce_webapp"
    elif "agent.digitalchina.com" in current_url:
        diag["platform"] = "market_widget"
    else:
        diag["platform"] = "unknown"
    
    # 截图
    ss_path = DIAG_DIR / f"{aid:03d}_page.png"
    browser.screenshot(str(ss_path))
    diag["screenshot"] = str(ss_path)
    
    # Snapshot
    snap = browser._run(["--session", browser.session, "snapshot"], timeout=15)
    diag["snapshot"] = snap[:5000]
    
    # Body
    diag["body_text"] = body[:3000] if body else ""
    diag["body_length"] = len(body or "")
    
    # URL
    diag["final_url"] = current_url
    
    # iframe 检测
    iframes = browser._run(["--session", browser.session, "eval", 
        "Array.from(document.querySelectorAll('iframe')).map(f=>({src:f.src,name:f.name})).length||0"], timeout=5)
    iframe_details = browser._run(["--session", browser.session, "eval",
        "JSON.stringify(Array.from(document.querySelectorAll('iframe')).map(f=>({src:f.src,name:f.name,id:f.id})))"], timeout=5)
    try:
        diag["iframes"] = json.loads(iframe_details.strip()) if iframe_details.strip().startswith('[') else []
    except:
        diag["iframes"] = []
    diag["iframe_count"] = len(diag.get("iframes", []))
    
    # 页面类型判断
    page_type = classify_page_type(body, snap, current_url)
    diag["page_type"] = page_type
    
    # 对话元素检测
    if "chat" in page_type:
        _detect_chat_elements(browser, diag)
    
    # 保存
    result_path = DIAG_DIR / f"{aid:03d}_diag.json"
    with open(result_path, 'w') as f:
        json.dump(diag, f, ensure_ascii=False, indent=2)
    
    print(f"   ✅ platform={diag['platform']} type={diag['page_type']} iframes={diag['iframe_count']}")
    return diag

def classify_page_type(body, snap, url):
    """分类页面类型"""
    body_lower = (body or "").lower()
    snap_lower = (snap or "").lower()
    
    # 纯Web应用（表单/仪表盘/管理页面）
    web_keywords = ['管理', 'dashboard', '工作台', '控制台', '管理后台', '表格', '数据查询', '表单',
                    '风险管理', '报价单', '合同', '审计', '审核', '审批', '查询', '统计',
                    'app not found', '404', 'not found']
    if any(k in body_lower or k in snap_lower for k in web_keywords):
        # 但有对话元素则不是纯web
        chat_clues = ['chat', '对话', 'message', '消息', '发送', '输入', 'input',
                      'stop generating', '停止生成', 'contenteditable']
        if not any(c in body_lower or c in snap_lower for c in chat_clues):
            return "webapp"
    
    # 飞书app对话
    if 'feishuapp.cn/ai/gui/chat' in url:
        return "feishuapp_chat"
    
    # Aily 对话
    if 'aily.feishu.cn/agents/' in url:
        # 检查是否真的显示对话界面
        chat_indicators = ['chat directly here', 'message your agent', 'suggested questions',
                          'start a conversation', 'stop generating', 'deep planning',
                          'how was this result', 'new conversation', 'conversation_']
        if any(c in body_lower for c in chat_indicators):
            return "aily_chat"
        # 测试模式（尚未发送过消息）
        if 'test your agent' in body_lower or 'test your assistant' in body_lower:
            return "aily_test_mode"
        return "aily"
    
    # 市场 Widget（中间页）
    if 'agent.digitalchina.com/widget/' in url:
        return "market_widget"
    
    # aiforce Web应用
    if 'aiforce.cloud/app/' in url:
        return "aiforce_webapp"
    
    return "unknown"

def _detect_chat_elements(browser, diag):
    """检测对话界面关键元素"""
    try:
        # 输入框
        inputs = browser._run(["--session", browser.session, "eval",
            "JSON.stringify(Array.from(document.querySelectorAll('[contenteditable], textarea, input[type=text], [class*=input], [class*=editor]')).slice(0,5).map(e=>({tag:e.tagName,attr:e.getAttribute('contenteditable'),class:e.className?.slice(0,50),placeholder:e.placeholder})))"],
            timeout=5)
        try:
            diag["input_candidates"] = json.loads(inputs.strip()) if inputs.strip().startswith('[') else []
        except:
            diag["input_candidates"] = [{"raw": inputs[:200]}]
        
        # 发送按钮
        btns = browser._run(["--session", browser.session, "eval",
            "JSON.stringify(Array.from(document.querySelectorAll('button')).slice(0,10).map(b=>({text:b.textContent?.slice(0,20),class:b.className?.slice(0,50),visible:b.offsetParent!==null})))"],
            timeout=5)
        try:
            diag["button_candidates"] = json.loads(btns.strip()) if btns.strip().startswith('[') else []
        except:
            diag["button_candidates"] = [{"raw": btns[:200]}]
        
        # 用户消息节点
        user_msgs = browser._run(["--session", browser.session, "eval",
            "document.querySelectorAll('[class*=user], [class*=question], [class*=human], [class*=sent]').length"],
            timeout=5)
        diag["user_msg_count"] = int(user_msgs.strip()) if user_msgs.strip().isdigit() else 0
        
        # assistant消息节点
        asst_msgs = browser._run(["--session", browser.session, "eval",
            "document.querySelectorAll('[class*=assistant], [class*=answer], [class*=bot], [class*=ai], [class*=received], [class*=response]').length"],
            timeout=5)
        diag["assistant_msg_count"] = int(asst_msgs.strip()) if asst_msgs.strip().isdigit() else 0
        
        # 生成中标志
        gen = browser._run(["--session", browser.session, "eval",
            "document.querySelectorAll('[class*=generating], [class*=streaming], [class*=loading], [class*=thinking], [class*=typing]').length"],
            timeout=5)
        diag["generating_indicator_count"] = int(gen.strip()) if gen.strip().isdigit() else 0
        
    except Exception as e:
        diag["chat_elements_error"] = str(e)


def main():
    auth = _agent_browser_auth_kwargs()
    browser = AgentBrowser(**auth, session="diag-bulk")
    
    results = {}
    for aid in ALL:
        try:
            results[aid] = diagnose_agent(aid, browser)
        except Exception as e:
            print(f"   💥 Agent {aid} 诊断崩溃: {e}")
            results[aid] = {"agent_id": aid, "error": str(e)}
    
    # 汇总分组
    summary = {
        "A_group_webapp_misid": [],
        "B_group_no_send": [],
        "C_group_no_extract": [],
        "platforms": {},
    }
    
    for aid in ALL:
        diag = results.get(aid, {})
        plat = diag.get("platform", "unknown")
        ptype = diag.get("page_type", "unknown")
        summary["platforms"][plat] = summary["platforms"].get(plat, 0) + 1
        
        if aid in GROUP_A:
            summary["A_group_webapp_misid"].append({"id": aid, "page_type": ptype, "platform": plat})
        elif aid in GROUP_B:
            summary["B_group_no_send"].append({"id": aid, "page_type": ptype, "platform": plat})
        elif aid in GROUP_C:
            summary["C_group_no_extract"].append({"id": aid, "page_type": ptype, "platform": plat})
    
    with open(DIAG_DIR / "summary.json", 'w') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    
    print(f"\n{'='*60}")
    print(f"📊 诊断完成")
    print(f"   A组（类型误判）: {len(summary['A_group_webapp_misid'])} 个")
    for a in summary['A_group_webapp_misid']:
        print(f"     [{a['id']}] type={a['page_type']} platform={a['platform']}")
    print(f"   B组（未发送）: {len(summary['B_group_no_send'])} 个")
    print(f"   C组（未提取）: {len(summary['C_group_no_extract'])} 个")
    print(f"   平台分布: {summary['platforms']}")
    print(f"   数据保存在: {DIAG_DIR}")
    
    browser.close()

if __name__ == "__main__":
    main()
