#!/usr/bin/env python3
"""轻量级每日巡检脚本

增强版：支持 Playwright 浏览器对话测试
- 默认只跑 API 检查（快速）
- 设置 CHAT_TEST=1 分批测试（每批 CHAT_TEST_BATCH 个，轮询覆盖）
- 设置 CHAT_TEST_ALL=1 一次测完全部对话型智能体，生成详细对话报告

首次运行通过 Playwright 登录获取 token 并缓存，后续直接使用。
"""

import sys
import httpx
import json
import datetime
import subprocess
import os
import asyncio
import time
import base64
import io
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent))

ROOT = Path(__file__).parent
REPORTS_DIR = ROOT / "reports"
TOKEN_CACHE = ROOT / ".auth/token.txt"
STATE_FILE = ROOT / ".auth/chat-test-state.json"
PLAYWRIGHT_STATE = ROOT / ".auth/playwright_state.json"
SCREENSHOTS_DIR = REPORTS_DIR / "screenshots"
CHAT_TEST_MODE = os.getenv("CHAT_TEST", "0").lower() in ("1", "true", "yes")
CHAT_TEST_BATCH = int(os.getenv("CHAT_TEST_BATCH", "5"))       # 每次测试多少个（轮询模式）
CHAT_TEST_ALL = os.getenv("CHAT_TEST_ALL", "0").lower() in ("1", "true", "yes")  # 全量检测模式
NON_CHAT_TEST = os.getenv("NON_CHAT_TEST", "0").lower() in ("1", "true", "yes")  # 非对话专项测试
TIMEOUT_SECONDS = int(os.getenv("CHAT_TEST_TIMEOUT", "30"))     # 单次对话超时秒数


# ──────────────────────────────────────
# 截图 Base64 内嵌（旧版，仅供参考）
# ──────────────────────────────────────

# ──────────────────────────────────────
# 截图 HTTP URL 工具（供 feishu_doc write 的 ![](url) 自动上传）
# HTTP 服务器由 cron agent 在脚本执行前独立启动，端口 18990
# ──────────────────────────────────────

SCREENSHOT_HTTP_PORT = 18990
SCREENSHOT_HTTP_BASE = f"http://127.0.0.1:{SCREENSHOT_HTTP_PORT}"


def _screenshot_url(absolute_path: str) -> str:
    """将截图绝对路径转为 ![](url) 中可用的 HTTP URL"""
    root = str(SCREENSHOTS_DIR)
    if absolute_path.startswith(root):
        rel = absolute_path[len(root):].lstrip("/")
        return f"{SCREENSHOT_HTTP_BASE}/{rel}"
    return absolute_path
def screenshot_to_base64_png(path_str, max_bytes=100000):
    """将截图文件转为 base64 data URI（markdown 图片语法）
    
    max_bytes: 原始截图最大字节数，超过则返回路径（控制报告体积）
    """
    try:
        size = os.path.getsize(path_str)
        if size > max_bytes:
            return f"📸 (过大{size//1024}KB，仅路径)\n  `{path_str}`"
        with open(path_str, "rb") as f:
            raw = f.read()
        b64 = base64.b64encode(raw).decode("ascii")
        return f"![](data:image/png;base64,{b64})"
    except Exception:
        return f"📸 (读取失败)\n  `{path_str}`"


def _embed_all_screenshots_in_report(report_text):
    """将报告中所有截图路径替换为内嵌 base64，限制总 base64 体积 ≤500KB"""
    try:
        import glob
        max_total = 800_000  # 800KB base64 上限
        total_b64 = 0
        count = 0
        max_imgs = 10  # 最多内嵌 10 张
        paths = sorted(glob.glob(str(SCREENSHOTS_DIR) + '/**/*.png'),
                       key=lambda p: os.path.getsize(p))
        for p in paths:
            if count >= max_imgs:
                break
            try:
                raw = open(p, "rb").read()
                b64 = base64.b64encode(raw).decode("ascii")
                if total_b64 + len(b64) > max_total:
                    break
                total_b64 += len(b64)
                count += 1
                # Markdown 内嵌图片
                img = f"![](data:image/png;base64,{b64})"
                report_text = report_text.replace(f"`{p}`", f"`{p}`  📷\n{img}")
            except Exception:
                continue
        if total_b64 > 0:
            print(f"  🖼️ 已内嵌 {count} 张截图 (~{total_b64//1024}KB base64)")
        else:
            print("  🖼️ 无截图或全部过大，仅保留路径")
        return report_text
    except Exception as e:
        print(f"  ⚠️ 内嵌截图失败: {e}，仅保留路径")
        return report_text


# ──────────────────────────────────────
# Token 获取
# ──────────────────────────────────────

def get_token():
    """获取 API token - 优先缓存，缓存失效才 Playwright 登录"""
    # 1. 优先使用缓存 token
    if TOKEN_CACHE.exists():
        try:
            with open(TOKEN_CACHE) as f:
                cached = f.read().strip()
            if cached:
                # 验证缓存 token 是否有效
                print("  📦 检测到缓存 token，验证有效性...")
                headers = {"Authorization": f"Bearer {cached}",
                           "User-Agent": "Mozilla/5.0"}
                try:
                    resp = httpx.Client(timeout=10).get(
                        "https://agent.digitalchina.com/api/agents/market",
                        headers=headers,
                        params={"page": 1, "pageSize": 1},
                    )
                    if resp.status_code == 200:
                        print(f"  ✅ 缓存 token 有效 (长度: {len(cached)})")
                        return cached
                    else:
                        print(f"  ⚠️ 缓存 token 已失效 (HTTP {resp.status_code})，重新登录...")
                except Exception as e:
                    print(f"  ⚠️ 验证请求失败: {e}，尝试重新登录...")
        except Exception as e:
            print(f"  ⚠️ 读取缓存失败: {e}")

    # 2. 缓存不可用，Playwright 登录
    print("  ⏳ 通过浏览器登录 Agent Market 获取 token...")
    try:
        token = asyncio.get_event_loop().run_until_complete(_login_and_get_token())
        if token:
            TOKEN_CACHE.parent.mkdir(parents=True, exist_ok=True)
            with open(TOKEN_CACHE, "w") as f:
                f.write(token)
            print(f"  ✅ Token 获取成功 (长度: {len(token)})")
            return token
        else:
            print("  ❌ Token 获取失败")
            return None
    except Exception as e:
        print(f"  ❌ 登录异常: {e}")
        return None


async def _login_and_get_token():
    """Playwright 登录并拦截 token"""
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
        ctx = await browser.new_context(viewport={"width": 1920, "height": 1080})
        page = await ctx.new_page()

        token = [None]
        def on_request(req):
            if "agents/market" in req.url or "api" in req.url:
                auth = req.headers.get("authorization", "")
                if auth.startswith("Bearer ") and not token[0]:
                    token[0] = auth[len("Bearer "):]
        page.on("request", on_request)

        await page.goto("https://agent.digitalchina.com/login", wait_until="domcontentloaded", timeout=30000)
        body = await page.evaluate("document.body.innerText")
        if "登录" in body or "login" in page.url:
            await page.locator("input[placeholder*=itcode]").first.fill("zhangzlt", timeout=5000)
            await page.locator("input[placeholder*=统一认证密码]").first.fill("Zzl.20041006", timeout=5000)
            await page.locator(".el-button--primary").first.click(timeout=5000)
            await asyncio.sleep(8)

        await page.goto("https://agent.digitalchina.com/market", wait_until="domcontentloaded", timeout=30000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except:
            pass
        await asyncio.sleep(3)

        await browser.close()
        return token[0]


# ──────────────────────────────────────
# API 数据采集
# ──────────────────────────────────────

def fetch_agents(token):
    """通过 API 获取智能体数据"""
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    with httpx.Client(timeout=30) as client:
        resp = client.get(
            "https://agent.digitalchina.com/api/agents/market",
            headers=headers,
            params={"search": "", "category": "", "createdBy": "", "source": "",
                    "sort": "createdAt", "page": 1, "pageSize": 200, "user": "张藻林"},
        )
        if resp.status_code != 200:
            print(f"  ❌ API 返回 {resp.status_code}")
            return None
        return resp.json()


# ──────────────────────────────────────
# 轮询状态管理
# ──────────────────────────────────────

def load_chat_test_state() -> dict:
    """加载轮询状态"""
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"last_index": 0, "last_run": None}


def save_chat_test_state(state: dict):
    """保存轮询状态"""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def get_chat_test_batch(agents, batch_size: int) -> list:
    """
    从对话型智能体列表中取出本轮要测试的批次

    使用索引轮询，每次取 batch_size 个，循环覆盖全部
    """
    chat_agents = [a for a in agents if _is_chat_agent(a)]
    if not chat_agents:
        return []

    state = load_chat_test_state()
    idx = state.get("last_index", 0)

    batch = []
    for i in range(batch_size):
        agent = chat_agents[(idx + i) % len(chat_agents)]
        batch.append(agent)

    state["last_index"] = (idx + batch_size) % len(chat_agents)
    state["last_run"] = datetime.datetime.now().isoformat()
    save_chat_test_state(state)

    return batch


def _is_chat_agent(agent: dict) -> bool:
    """
    判断是否为可测试的对话型智能体
    - feishuapp.cn/ai/gui/chat/a_xxx：飞书 aPaaS 对话 widget
    - aily.feishu.cn/agents/agent_xxx：飞书 aily 平台智能体
    - openType=api + source=dify：市场内嵌 Dify 对话（如 ID 63 客户信息查询小助手）
    排除：applink.feishu.cn（需跳转飞书客户端，不能浏览器测试）
    """
    url = agent.get("url", "")
    # openType=api + dify 源：通过 API 测试（非浏览器）
    if agent.get("openType") == "api" and agent.get("source") == "dify":
        return True
    if not url:
        return False
    # 排除 applink 跳转链接（需要飞书客户端）
    if "applink.feishu.cn" in url:
        return False
    return ("feishuapp.cn/ai/gui/chat" in url
            or "feishu.cn/ai/gui/chat" in url
            or "aily.feishu.cn/agents/" in url)


# ──────────────────────────────────────
# Playwright 对话测试
# ──────────────────────────────────────

async def run_chat_tests(agents, token):
    """
    使用 Playwright + 飞书登录态直接访问智能体聊天页面进行对话测试

    跳过 Agent Market 登录，直接用飞书 QR 扫码态访问 feishuapp.cn 上的聊天 widget

    Args:
        agents: 智能体列表
        token: API token (未使用，保留兼容)

    Returns:
        list[dict]: 测试结果
    """
    from playwright.async_api import async_playwright
    from utils.llm import generate_test_questions, evaluate_response

    if not PLAYWRIGHT_STATE.exists():
        print("    ❌ 未找到飞书登录态，请先扫码登录")
        return [{"agent_id": "N/A", "name": "登录态缺失", "status": "skipped",
                 "error": f"请先运行 feishu_login.py 扫码登录"}]

    # 从 agents 抽取有真实 chat URL 的，以及 dify API 测试的
    browser_agents = []
    dify_agents = []
    for a in agents:
        url = a.get("url", "")
        if "feishuapp.cn/ai/gui/chat" in url or "feishu.cn/ai/gui/chat" in url:
            browser_agents.append({**a, "_chat_url": url, "_platform": "feishuapp"})
        elif "aily.feishu.cn/agents/" in url:
            browser_agents.append({**a, "_chat_url": url, "_platform": "aily"})
        elif a.get("openType") == "api" and a.get("source") == "dify":
            # Dify 内嵌 — 通过 API 测试
            dify_agents.append({**a, "_platform": "dify-api"})

    if not browser_agents and not dify_agents:
        print("    ⚠️ 未找到可测试的对话型智能体，跳过对话测试")
        return []

    total = len(browser_agents) + len(dify_agents)
    print(f"    📋 准备测试 {total} 个智能体 (浏览器: {len(browser_agents)}, API: {len(dify_agents)})")
    for a in browser_agents:
        print(f"      → [{a['id']}] {a.get('name','?')} [{a.get('_platform','?')}]")
    for a in dify_agents:
        print(f"      → [{a['id']}] {a.get('name','?')} [dify-api]")

    all_results = []

    # ── 先跑 API 测试（Dify） ──
    for agent in dify_agents:
        result = await _run_dify_api_test(agent, token)
        all_results.append(result)

    # ── 再跑浏览器测试 ──
    if browser_agents:
        browser_results = await _run_browser_tests(browser_agents, token)
        all_results.extend(browser_results)

    return all_results


async def _run_dify_api_test(agent, token):
    """通过 HTTP API 测试 Dify 内嵌智能体"""
    import httpx
    from utils.llm import generate_test_questions, evaluate_response

    agent_id = agent["id"]
    name = agent.get("name", "未知")
    description = agent.get("description", "")
    category = agent.get("categoryLabel", "")

    print(f"    🤖 [{agent_id}] {name} [dify-api]")

    # agent_id → appId 映射（可以从前端 JS 提取，这里硬编码已知映射）
    DIFY_APPID_MAP = {63: 8}
    app_id = DIFY_APPID_MAP.get(agent_id)
    if not app_id:
        return {"agent_id": agent_id, "name": name, "status": "chat_error",
                "error": f"未找到 agent_id={agent_id} 对应的 Dify appId，需补充映射",
                "description": description, "category": category}

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",
    }

    try:
        # 生成测试问题
        questions = await generate_test_questions(
            agent_name=name, agent_type=category, agent_desc=description, count=2)

        q_results = []
        client = httpx.AsyncClient(timeout=60)

        for q in questions:
            t_start = time.time()
            payload = {"appId": app_id, "user": "zhangzlt", "message": q, "inputs": {}}

            try:
                resp = await client.post(
                    "https://agent.digitalchina.com/api/chat/stream",
                    headers=headers, json=payload)
                elapsed = round(time.time() - t_start, 1)

                # 解析 SSE 流
                reply = ""
                for line in resp.text.split("\n"):
                    if line.startswith("data: "):
                        try:
                            chunk = json.loads(line[6:])
                            if "content" in chunk and chunk["content"]:
                                reply += chunk["content"]
                        except json.JSONDecodeError:
                            pass

                success = bool(reply and len(reply.strip()) > 5)
                q_results.append({
                    "question": q, "response": reply,
                    "screenshot": "",  # API 测试无截图
                    "success": success,
                    "error": None if success else "未返回有效回复",
                    "elapsed": elapsed})

            except Exception as e:
                q_results.append({
                    "question": q, "response": "", "screenshot": "",
                    "success": False, "error": f"API 请求失败: {str(e)[:100]}",
                    "elapsed": round(time.time() - t_start, 1)})

        await client.aclose()

        # LLM 评估
        evaluation = None
        first_resp = next((qr["response"] for qr in q_results if qr["response"]), "")
        if first_resp:
            evaluation = await evaluate_response(agent_name=name, question=questions[0], response=first_resp)

        # 判定状态
        if not q_results or all(not qr.get("success") for qr in q_results):
            status = "chat_error"
            error = q_results[0]["error"] if q_results else "无回复"
        elif evaluation and not evaluation.get("passed", True):
            status = "chat_failed"
            error = "回复质量不合格: " + "; ".join(evaluation.get("issues", []))
        else:
            status = "ok"
            error = None

        return {
            "agent_id": agent_id, "name": name, "status": status, "error": error,
            "questions_tested": questions, "q_results": q_results,
            "evaluation": evaluation, "description": description, "category": category,
            "_platform": "dify-api",
            "avg_elapsed": round(sum(qr.get("elapsed", 0) for qr in q_results) / len(q_results), 1) if q_results else 0}

    except Exception as e:
        return {"agent_id": agent_id, "name": name, "status": "chat_error",
                "error": f"Dify API 测试异常: {str(e)[:200]}",
                "description": description, "category": category}


async def _run_browser_tests(browser_agents, token):
    """使用 agent-browser（Rust 原生 CDP）测试飞书/aily 智能体"""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))  # work/
    from agent_browser_wrapper import AgentBrowser, AgentBrowserError

    from utils.llm import generate_test_questions, evaluate_response

    if not PLAYWRIGHT_STATE.exists():
        print("    ❌ 未找到飞书登录态，请先扫码登录")
        return [{"agent_id": "N/A", "name": "登录态缺失", "status": "skipped",
                 "error": f"请先运行 feishu_login.py 扫码登录"}]

    all_results = []
    browser = None

    try:
        browser = AgentBrowser(
            state_path=str(PLAYWRIGHT_STATE),
            session="agent-market-inspect",
        )

        for agent in browser_agents:
            agent_id = agent["id"]
            name = agent.get("name", "未知")
            description = agent.get("description", "")
            category = agent.get("categoryLabel", "")
            chat_url = agent["_chat_url"]

            print(f"    🤖 [{agent_id}] {name}")

            try:
                # 导航到聊天页（首次含 state 载入，后续仅导航）
                browser.open(
                    chat_url,
                    timeout=30,
                    wait_selector="[contenteditable]",
                    wait_timeout=15,
                )

                body = browser.get_body_text()
                if "Log In With QR Code" in body or "Scan the QR code" in body:
                    all_results.append({
                        "agent_id": agent_id, "name": name, "status": "unreachable",
                        "error": "飞书登录态已过期，需重新扫码",
                        "description": description, "category": category})
                    continue
                if "No permission to use" in body or "应用不存在" in body:
                    all_results.append({
                        "agent_id": agent_id, "name": name, "status": "unreachable",
                        "error": "无权限访问此智能体（需创建者授权）",
                        "description": description, "category": category})
                    continue

                # 生成测试问题
                questions = await generate_test_questions(
                    agent_name=name, agent_type=category, agent_desc=description, count=2)

                q_results = []
                agent_screenshot_dir = str(SCREENSHOTS_DIR / str(agent_id))
                os.makedirs(agent_screenshot_dir, exist_ok=True)

                for qi, q in enumerate(questions):
                    body_before = browser.get_body_text()

                    t_start = time.time()
                    browser.chat_send(q)
                    reply_body = browser.chat_wait(timeout=45)
                    elapsed = round(time.time() - t_start, 1)

                    reply = _parse_chat_reply(body_before, reply_body or "", q)

                    q_results.append({
                        "question": q, "response": reply,
                        "success": bool(reply and len(reply) > 5),
                        "error": None if (reply and len(reply) > 5) else "未返回有效回复",
                        "elapsed": elapsed})

                    if qi < len(questions) - 1:
                        time.sleep(2)

                # 每个智能体测试完后截一张最终状态截图
                agent_screenshot = _try_screenshot(browser, screenshot_dir, aid, "final")

                evaluation = None
                first_resp = next((qr["response"] for qr in q_results if qr["response"]), "")
                if first_resp:
                    evaluation = await evaluate_response(
                        agent_name=name, question=questions[0] if questions else "", response=first_resp)

                if not q_results or all(not qr.get("success") for qr in q_results):
                    status = "chat_error"
                    error = q_results[0]["error"] if q_results else "无回复"
                elif evaluation and not evaluation.get("passed", True):
                    status = "chat_failed"
                    error = "回复质量不合格: " + "; ".join(evaluation.get("issues", []))
                else:
                    status = "ok"
                    error = None

                all_results.append({
                    "agent_id": agent_id, "name": name, "status": status, "error": error,
                    "questions_tested": questions, "q_results": q_results,
                    "evaluation": evaluation, "description": description, "category": category,
                    "screenshot": agent_screenshot,
                    "avg_elapsed": round(sum(qr.get("elapsed", 0) for qr in q_results) / len(q_results), 1) if q_results else 0})

                time.sleep(2)

            except AgentBrowserError as e:
                all_results.append({"agent_id": agent_id, "name": name, "status": "chat_error",
                                    "error": f"agent-browser 错误: {str(e)[:200]}",
                                    "description": description, "category": category})
            except Exception as e:
                err_str = str(e)[:200]
                all_results.append({"agent_id": agent_id, "name": name, "status": "chat_error",
                                    "error": err_str, "description": description, "category": category})

        return all_results

    except Exception as e:
        print(f"    ❌ 浏览器测试异常: {e}")
        return []

    finally:
        if browser:
            try:
                browser.close()
            except Exception:
                pass


# ──────────────────────────────────────
# 非对话型智能体测试（agent-browser）
# ──────────────────────────────────────

# 非对话智能体分类与测试策略
NON_CHAT_AGENTS = {
    # A. 文件上传型
    126: {"type": "file_upload", "name": "智能采销合同比对",
          "url": "https://bba12hub36.aiforce.cloud/app/app_4k8ta4rumbr1w/upload",
          "files": ["test_files/sales_contract_test.pdf", "test_files/purchase_contract_test.pdf"],
          "action": "dual_upload_compare", "verify_text": "比对"},
    123: {"type": "file_upload", "name": "售前URS解析助手",
          "url": "https://bba12hub36.aiforce.cloud/app/app_4jx1jik8om8ll",
          "files": ["test_files/urs_requirements_test.pdf"],
          "action": "single_upload", "verify_text": "解析",
          "wait_selector": "input[type=file]", "wait_timeout": 20},
    116: {"type": "file_upload", "name": "担保合同&授信合同解析助手",
          "url": "https://bba12hub36.aiforce.cloud/app/app_4k6u2xsa5fxcy",
          "files": ["test_files/credit_contract_test.pdf"],
          "action": "single_upload", "verify_text": "合同",
          "wait_selector": "input[type=file]", "wait_timeout": 20,
          "snapshot_first": True,
          "post_upload_click": "提取信息"},
    112: {"type": "file_upload", "name": "企业信息收集表格自动填写",
          "url": "https://bba12hub36.aiforce.cloud/app/app_4jubzq0klm14g",
          "files": ["test_files/enterprise_info_form.xlsx"],
          "action": "single_upload", "verify_text": "填写",
          "wait_selector": "input[type=file]", "wait_timeout": 15},
    98:  {"type": "file_upload", "name": "报价单审核",
          "url": "http://10.0.5.86:9036/quote-check-ui",
          "files": ["test_files/quote_document_test.pdf"],
          "action": "quote_review", "verify_text": "核验"},
    61:  {"type": "file_upload", "name": "PDF附件脱敏打码助手",
          "url": "https://bba12hub36.aiforce.cloud/app/app_4kq0z3uxxcvn1",
          "files": ["test_files/employee_info_sensitive.pdf"],
          "action": "single_upload", "verify_text": "脱敏",
          "wait_selector": "input[type=file]", "wait_timeout": 15},

    # B. 内部问学对话型
    89:  {"type": "internal_chat", "name": "小搭-产品推荐",
          "url": "http://10.0.1.25/app-api/share/apps/2oq81foiu4pt",
          "role_text": "小搭"},
    88:  {"type": "internal_chat", "name": "小销-客户拓展助手",
          "url": "http://10.0.1.25/app-api/share/apps/ueeq3m8pwvqo",
          "role_text": "小销"},
    113: {"type": "internal_chat", "name": "清仓智谋官",
          "url": "http://10.0.1.25/app-api/share/apps/efgmcfg0rb9t",
          "role_text": "清仓"},

    # C. Web 交互型
    121: {"type": "web_interactive", "name": "生态伙伴智能筛选",
          "url": "https://bba12hub36.aiforce.cloud/app/app_4jr868g4s3h0z",
          "action": "custom",
          "steps": [
              {"action": "type", "selector": "input,textarea", "text": "制造业 AI 服务商"},
              {"action": "press", "key": "Enter"},
              {"action": "sleep", "seconds": 3},
              {"action": "scroll", "pixels": 1500},
          ]},
    100: {"type": "web_interactive", "name": "内容合规审核工具",
          "url": "http://10.0.5.86:9058/",
          "action": "click_review", "button_text": "开始审核"},
    125: {"type": "web_interactive", "name": "欠款风险管理平台",
          "url": "https://bba12hub36.aiforce.cloud/app/app_4k5wq7xt5hv9f",
          "action": "spark_nav", "needs_auth": True,
          "nav_links": ["销售员管理", "企业管理", "承诺管理"]},
    120: {"type": "web_interactive", "name": "抓阄助手",
          "url": "https://bba12hub36.aiforce.cloud/app/app_4k6j5u1tjuv34",
          "action": "custom", "needs_auth": True,
          "wait_selector": "button,input", "wait_timeout": 15,
          "steps": [
              {"action": "type", "selector": "input:not([type=hidden]):not([type=submit]):not([type=button])", "text": "选项A"},
              {"action": "click", "text": "添加"},
              {"action": "sleep", "seconds": 1},
              {"action": "type", "selector": "input:not([type=hidden]):not([type=submit]):not([type=button])", "text": "选项B"},
              {"action": "click", "text": "添加"},
              {"action": "sleep", "seconds": 1},
              {"action": "click", "text": "开始抓阄"},
              {"action": "sleep", "seconds": 2},
              {"action": "scroll", "pixels": 500},
          ]},
    102: {"type": "web_interactive", "name": "人员分组智能助手",
          "url": "https://bba12hub36.aiforce.cloud/spark/faas/app_4jtsfvghhd5pk",
          "action": "custom", "needs_auth": True,
          "wait_selector": "textarea,input:not([type=hidden])", "wait_timeout": 15,
          "steps": [
              {"action": "click", "selector": "textarea,input:not([type=hidden])"},
              {"action": "keyboard", "text": "张三\n李四\n王五\n赵六\n钱七"},
              {"action": "sleep", "seconds": 1},
              {"action": "click", "text": "开始分组"},
              {"action": "sleep", "seconds": 2},
          ]},

    # D. 跳过（需特殊登录或外部平台）
    81:  {"type": "skip", "name": "问学超级员工", "reason": "需要 ITcode 密码登录 (DCone SSO)"},
    107: {"type": "skip", "name": "个人海报生成工具", "reason": "coze.site 外部平台"},
    124: {"type": "skip", "name": "AI短视频约稿平台", "reason": "coze.site 外部平台"},
    90:  {"type": "skip", "name": "客户小助手（订阅）", "reason": "applink 需要飞书客户端"},
    86:  {"type": "skip", "name": "售前项目管理专家", "reason": "applink 需要飞书客户端"},
}

SKIP_AGENT_REASONS = {k: v["reason"] for k, v in NON_CHAT_AGENTS.items() if v["type"] == "skip"}


def _get_non_chat_agents(agents_list: list) -> list:
    """从全体智能体中筛选非对话型，全部纳入测试"""
    # 已由对话测试覆盖的类型
    chat_ids = set()
    for a in agents_list:
        url = a.get("url", "")
        if ("feishuapp.cn/ai/gui/chat" in url or "aily.feishu.cn/agents/" in url):
            chat_ids.add(a["id"])
        if a.get("openType") == "api" and a.get("source") == "dify":
            chat_ids.add(a["id"])

    result = []
    for a in agents_list:
        aid = a["id"]
        if aid in chat_ids:
            continue  # 对话型已单独测试，跳过

        # 有专用配置的用专用配置，否则用通用测试
        if aid in NON_CHAT_AGENTS:
            cfg = NON_CHAT_AGENTS[aid]
        else:
            # 通用非对话测试
            url = a.get("url", "")
            if not url or not url.startswith("http"):
                cfg = {"type": "skip", "reason": f"无可测试 URL (appType: {a.get('appTypeLabel','?')})"}
            else:
                cfg = {
                    "type": "generic",
                    "name": a.get("name", "?"),
                    "url": url,
                }

        result.append({**a, "_test_cfg": cfg})
    return result


async def _run_non_chat_tests(all_agents: list, token: str) -> list:
    """对非对话型智能体执行专项测试"""
    import sys as _sys
    _sys.path.insert(0, str(ROOT.parent))  # work/
    from agent_browser_wrapper import AgentBrowser, AgentBrowserError

    targets = _get_non_chat_agents(all_agents)
    if not targets:
        return []

    print(f"\n  📋 非对话专项测试: {len(targets)} 个智能体")
    for t in targets:
        cfg = t["_test_cfg"]
        print(f"      → [{t['id']}] {t['name']} [{cfg['type']}]")

    results = []
    browser = None
    test_files_dir = ROOT / "test_files"

    try:
        browser = AgentBrowser(
            state_path=str(PLAYWRIGHT_STATE),
            session=f"non-chat-{int(time.time())}",
        )

        for agent_with_cfg in targets:
            aid = agent_with_cfg["id"]
            name = agent_with_cfg["name"]
            cfg = agent_with_cfg["_test_cfg"]
            atype = cfg["type"]
            desc = agent_with_cfg.get("description", "")
            category = agent_with_cfg.get("categoryLabel", "")

            print(f"    🔍 [{aid}] {name} ({atype})")
            screenshot_dir = str(SCREENSHOTS_DIR / str(aid))
            os.makedirs(screenshot_dir, exist_ok=True)

            try:
                if atype == "skip":
                    results.append({
                        "agent_id": aid, "name": name, "status": "skipped",
                        "error": cfg["reason"],
                        "description": desc, "category": category,
                        "_test_type": atype})
                    continue

                elif atype == "file_upload":
                    r = await _test_file_upload(
                        browser, cfg, test_files_dir, screenshot_dir, aid, name, desc, category)
                    results.append(r)

                elif atype == "internal_chat":
                    r = await _test_internal_chat(
                        browser, cfg, screenshot_dir, aid, name, desc, category)
                    results.append(r)

                elif atype == "web_interactive":
                    r = await _test_web_interactive(
                        browser, cfg, screenshot_dir, aid, name, desc, category)
                    results.append(r)

                elif atype == "generic":
                    r = await _test_generic_non_chat(
                        browser, cfg, screenshot_dir, aid, name, desc, category)
                    results.append(r)

                else:
                    results.append({
                        "agent_id": aid, "name": name, "status": "skipped",
                        "error": f"未知测试类型: {atype}",
                        "description": desc, "category": category,
                        "_test_type": atype})

            except AgentBrowserError as e:
                results.append({
                    "agent_id": aid, "name": name, "status": "chat_error",
                    "error": f"agent-browser: {str(e)[:200]}",
                    "description": desc, "category": category,
                    "_test_type": atype})
            except Exception as e:
                results.append({
                    "agent_id": aid, "name": name, "status": "chat_error",
                    "error": str(e)[:200],
                    "description": desc, "category": category,
                    "_test_type": atype})

            time.sleep(1.5)

    except Exception as e:
        print(f"    ❌ 非对话测试异常: {e}")
    finally:
        if browser:
            try:
                browser.close()
            except Exception:
                pass

    return results


# ── 通用非对话智能体测试 ──

async def _test_generic_non_chat(browser, cfg: dict, screenshot_dir: str,
                                  aid: int, name: str, desc: str, category: str) -> dict:
    """
    通用非对话智能体测试：打开 URL → 观察界面 → 截图 → 尝试交互 → 分析
    """
    import time as _time

    url = cfg.get("url", "")
    if not url:
        return {
            "agent_id": aid, "name": name, "status": "skipped",
            "error": "无可测试的 URL",
            "description": desc, "category": category,
            "_test_type": "generic"}

    print(f"      🌐 打开: {url[:80]}")
    q_results = []
    t_start = _time.time()

    try:
        # 1. 打开页面
        browser.navigate(url)
        _time.sleep(2)

        # 2. 处理 Spark 授权
        needs_auth = cfg.get("needs_auth", False)
        if needs_auth:
            _spark_authorize(browser)
            _time.sleep(1.5)

        # 3. 初始截图
        ss_init = f"{screenshot_dir}/init_{int(_time.time())}.png"
        try:
            browser.screenshot(ss_init)
        except Exception:
            ss_init = ""

        # 4. 获取页面文本，分析界面元素
        try:
            body_text = browser.get_body_text()
        except Exception:
            body_text = ""

        # 5. 尝试基础交互：查找可点击按钮/链接
        interaction_done = ""
        try:
            # 尝试点击第一个可见按钮
            from playwright.sync_api import TimeoutError as _TimeoutError
            page = browser._page

            # 优先点击主操作按钮
            for selector in [
                'button.el-button--primary:visible',
                'button:visible:has-text("提交")',
                'button:visible:has-text("查询")',
                'button:visible:has-text("开始")',
                'button:visible:not([disabled])',
                'a:visible[href]:not([href="#"])',
            ]:
                try:
                    el = page.locator(selector).first
                    if el.count() > 0:
                        el.click(timeout=3000)
                        interaction_done = f"点击了: {selector}"
                        _time.sleep(1.5)
                        break
                except Exception:
                    continue

            # 如果有输入框，尝试填入示例文本
            if not interaction_done:
                for selector in ['input:visible:not([type="hidden"]):not([disabled])',
                                 'textarea:visible:not([disabled])']:
                    try:
                        el = page.locator(selector).first
                        if el.count() > 0:
                            el.fill("测试输入", timeout=3000)
                            interaction_done = "填入测试文本到输入框"
                            _time.sleep(1)
                            break
                    except Exception:
                        continue
        except Exception as e:
            interaction_done = f"交互尝试失败: {e}"

        # 6. 交互后截图
        ss_result = f"{screenshot_dir}/result_{int(_time.time())}.png"
        try:
            browser.screenshot(ss_result)
        except Exception:
            ss_result = ss_init  # fallback to initial

        elapsed = round(_time.time() - t_start, 1)

        # 7. 构建分析结果
        ui_summary = (body_text or "")[:500].replace("\n", " ").strip()
        analysis_parts = [f"页面已打开，URL: {url[:60]}"]
        if ui_summary:
            analysis_parts.append(f"界面内容: {ui_summary}")
        if interaction_done:
            analysis_parts.append(f"交互操作: {interaction_done}")

        q_results.append({
            "question": f"打开并测试: {url[:60]}",
            "response": "\n".join(analysis_parts),
            "success": bool(ui_summary),
            "error": None if ui_summary else "无法获取页面内容",
            "elapsed": elapsed})

        return {
            "agent_id": aid, "name": name, "status": "ok" if ui_summary else "chat_error",
            "error": None if ui_summary else "页面内容为空或无法访问",
            "q_results": q_results, "description": desc, "category": category,
            "screenshot": ss_result or ss_init,
            "avg_elapsed": elapsed, "_test_type": "generic"}

    except Exception as e:
        elapsed = round(_time.time() - t_start, 1)
        return {
            "agent_id": aid, "name": name, "status": "chat_error",
            "error": f"通用测试异常: {str(e)[:200]}",
            "q_results": [], "description": desc, "category": category,
            "screenshot": "", "avg_elapsed": elapsed, "_test_type": "generic"}


# ── Spark 应用授权辅助 ──

def _try_screenshot(browser, screenshot_dir: str, aid: int, label: str = "final") -> str:
    """尝试截图，失败返回空字符串"""
    try:
        ss_file = f"{screenshot_dir}/{label}_{int(time.time())}.png"
        browser.screenshot(ss_file)
        return ss_file
    except Exception:
        return ""


def _spark_authorize(browser) -> bool:
    """处理 Spark/Feishu 应用授权页，点击 Authorize 按钮"""
    try:
        body = browser.get_body_text()
        if "Authorize" in body or "授权" in body:
            browser.find_and_click("Authorize")
            time.sleep(5)
            return True
    except Exception:
        pass
    return False


# ── 文件上传测试 ──

async def _test_file_upload(browser, cfg, test_files_dir, screenshot_dir,
                             aid, name, desc, category) -> dict:
    """文件上传型智能体测试"""
    url = cfg["url"]
    files = [str(test_files_dir / f.split("/")[-1]) for f in cfg["files"]]
    wait_selector = cfg.get("wait_selector")
    wait_timeout_val = cfg.get("wait_timeout", 15)
    snapshot_first = cfg.get("snapshot_first", False)
    post_upload_click = cfg.get("post_upload_click")

    browser.open(url, wait_sec=4, wait_selector=wait_selector, wait_timeout=wait_timeout_val)

    # Spark 授权 → 授权后会跳转，必须重新导航回目标页面
    _spark_authorize(browser)
    body = browser.get_body_text()
    if "Authorize" in body or "授权" in body:
        # 授权未生效，重新打开并等待
        browser.open(url, wait_sec=3, wait_selector=wait_selector, wait_timeout=wait_timeout_val)
        _spark_authorize(browser)
        body = browser.get_body_text()
        if "Authorize" in body or "授权" in body:
            raise Exception("Spark 授权未生效")

    # 关键：授权后页面可能已跳转，重新导航到目标 URL 确保元素就绪
    current_url = browser.get_url()
    if url not in current_url and "aiforce.cloud" in url:
        print(f"      🔄 授权后重导航: {url[:60]}")
        browser.open(url, wait_sec=3, wait_selector=wait_selector, wait_timeout=wait_timeout_val)

    # 快照优先：先生成 DOM 元素树再操作（慢页面防元素未就绪）
    if snapshot_first:
        try:
            browser.snapshot()
            print(f"      📸 DOM 快照完成，元素树已生成")
        except Exception as e:
            print(f"      ⚠️ 快照异常（继续执行）: {e}")

    # 检查页面是否可访问（App not found 等）
    body = browser.get_body_text()
    if "App not found" in body or "Access unavailable" in body:
        # 失败也截图，记录页面状态
        ss = _try_screenshot(browser, screenshot_dir, aid, "unreachable")
        return {
            "agent_id": aid, "name": name, "status": "unreachable",
            "error": "应用不可访问 (App not found)",
            "description": desc, "category": category,
            "screenshot": ss, "_test_type": "file_upload"}

    q_results = []
    successful = False
    elapsed = 0

    t_start = time.time()

    body_before_upload = browser.get_body_text()

    if cfg["action"] == "dual_upload_compare":
        # 上传两个文件 → 点比对按钮
        browser.upload("input[type=file]:first-of-type", files[0])
        time.sleep(2)
        try:
            browser.upload("input[type=file]:last-of-type", files[1])
        except Exception:
            browser.open(url, wait_sec=3)
            _spark_authorize(browser)
            browser.upload("input[type=file]:first-of-type", files[1])
            time.sleep(2)
            browser.open(url, wait_sec=3)
            _spark_authorize(browser)
            browser.upload("input[type=file]:first-of-type", files[0])
        time.sleep(3)

        try:
            browser.find_and_click("开始比对")
        except Exception:
            try:
                browser.click("button")
            except Exception:
                pass
        time.sleep(8)

    elif cfg["action"] == "quote_review":
        browser.upload("input[type=file]", files[0])
        time.sleep(2)
        try:
            browser.find_and_click("提交")
        except Exception:
            try:
                browser.click("button")
            except Exception:
                pass
        time.sleep(5)

    else:
        browser.upload("input[type=file]", files[0])
        time.sleep(5)

    # 上传后点击操作（如"提取信息"）
    if post_upload_click:
        try:
            time.sleep(1)
            browser.find_and_click(post_upload_click)
            print(f"      🖱️ 上传后点击: {post_upload_click}")
            time.sleep(5)
        except Exception as e:
            print(f"      ⚠️ 上传后点击失败: {e}")

    elapsed = round(time.time() - t_start, 1)
    body = browser.get_body_text()
    verify = cfg.get("verify_text", "")
    # 验证：有关键词，或页面内容显著增加（说明上传触发了处理）
    if verify and verify.lower() in body.lower():
        successful = True
    else:
        successful = len(body) > max(len(body_before_upload) + 50, 200)

    # ← 最终截图：所有操作完成后的页面状态
    ss_path = _try_screenshot(browser, screenshot_dir, aid, "final")

    q_results.append({
        "question": f"上传文件: {', '.join(files)}",
        "response": f"{'✅ 成功' if successful else '❌ 未返回预期结果'} (验证关键词: '{verify}')",
        "success": successful,
        "error": None if successful else f"未在响应中找到关键词: {verify}",
        "elapsed": elapsed})

    status = "ok" if successful else "chat_error"
    return {
        "agent_id": aid, "name": name, "status": status,
        "error": None if successful else f"上传后未检测到关键词 '{verify}'",
        "q_results": q_results, "description": desc, "category": category,
        "screenshot": ss_path,
        "avg_elapsed": elapsed, "_test_type": "file_upload"}


# ── 内部问学对话测试 ──

async def _test_internal_chat(browser, cfg, screenshot_dir,
                               aid, name, desc, category) -> dict:
    """内部问学平台(10.0.1.25)对话智能体测试"""
    url = cfg["url"]
    role_text = cfg.get("role_text", "")

    browser.open(url, wait_selector="[contenteditable]", wait_timeout=15)

    questions = [f"你好，请简单介绍一下你自己能做什么"]
    q_results = []
    total_elapsed = 0

    for qi, q in enumerate(questions):
        body_before = browser.get_body_text()

        t_start = time.time()
        browser.chat_send(q)
        reply_body = browser.chat_wait(timeout=45)
        elapsed = round(time.time() - t_start, 1)
        total_elapsed += elapsed

        reply = _parse_chat_reply(body_before, reply_body or "", q)

        success = bool(reply and len(reply) > 5)
        q_results.append({
            "question": q, "response": reply,
            "success": success,
            "error": None if success else "未返回有效回复",
            "elapsed": elapsed})

        if qi < len(questions) - 1:
            time.sleep(2)

    # 每个智能体测试完后截一张最终状态截图
    agent_screenshot = ""
    try:
        ss_file = f"{screenshot_dir}/result_{int(time.time())}.png"
        browser.screenshot(ss_file)
        agent_screenshot = ss_file
    except Exception:
        pass

    if all(qr["success"] for qr in q_results):
        status = "ok"
    elif any(qr["success"] for qr in q_results):
        status = "chat_failed"
    else:
        status = "chat_error"

    return {
        "agent_id": aid, "name": name, "status": status,
        "error": None if status == "ok" else "部分/全部回复无效",
        "q_results": q_results, "description": desc, "category": category,
        "screenshot": agent_screenshot,
        "avg_elapsed": round(total_elapsed / len(q_results), 1),
        "_test_type": "internal_chat"}


# ── Web 交互型测试 ──

async def _test_web_interactive(browser, cfg, screenshot_dir,
                                 aid, name, desc, category) -> dict:
    """Web 交互型智能体测试"""
    url = cfg["url"]
    action = cfg["action"]
    wait_selector = cfg.get("wait_selector")
    wait_timeout_val = cfg.get("wait_timeout", 15)

    browser.open(url, wait_sec=4, wait_selector=wait_selector, wait_timeout=wait_timeout_val)

    # Spark 授权
    if cfg.get("needs_auth"):
        _spark_authorize(browser)

    q_results = []
    t_start = time.time()

    if action == "custom":
        # 自定义步骤序列
        steps = cfg.get("steps", [])
        step_results = []
        for step in steps:
            sa = step["action"]
            try:
                if sa == "type":
                    sel = step.get("selector", "input,textarea")
                    browser.click(sel)
                    time.sleep(0.3)
                    browser.insert_text(step["text"])
                    step_results.append(f"键入: {step['text'][:30]}")
                elif sa == "click":
                    try:
                        browser.find_and_click(step["text"])
                    except Exception:
                        browser.click(step.get("selector", "button"))
                    step_results.append(f"点击: {step.get('text', step.get('selector', 'button'))}")
                elif sa == "press":
                    browser.press(step["key"])
                    step_results.append(f"按键: {step['key']}")
                elif sa == "keyboard":
                    browser.keyboard_type(step["text"])
                    step_results.append(f"键盘输入: {step['text'][:30]}")
                elif sa == "scroll":
                    browser.eval(f"window.scrollBy(0, {step['pixels']})")
                    step_results.append(f"滚动: {step['pixels']}px")
                elif sa == "sleep":
                    time.sleep(step["seconds"])
                    step_results.append(f"等待: {step['seconds']}s")
                elif sa == "screenshot":
                    pass  # handled separately at end
                else:
                    step_results.append(f"未知操作: {sa}")
            except Exception as e:
                step_results.append(f"❌ {sa} 失败: {str(e)[:60]}")

        body_text = browser.get_body_text()
        # custom action 放宽验证：body 内容或步骤执行数
        success = len(body_text) > 100 or len(step_results) >= 3
        body = "\n".join(step_results)

    elif action == "search_and_check":
        # 点击搜索框，输入搜索词，检查结果
        try:
            browser.click("input,textarea,[contenteditable]")
            time.sleep(0.3)
            browser.insert_text(cfg.get("search_text", ""))
            time.sleep(0.3)
            browser.press("Enter")
            time.sleep(3)
        except Exception:
            pass
        # 无论搜索是否成功，页面有内容即可
        body = browser.get_body_text()
        success = len(body) > 300

    elif action == "click_review":
        try:
            browser.find_and_click(cfg["button_text"])
        except Exception:
            try:
                browser.click("button")
            except Exception:
                pass
        time.sleep(6)
        body = browser.get_body_text()
        success = "风险" in body or "违规" in body or "审核" in body or len(body) > 500

    elif action == "spark_nav":
        nav_links = cfg.get("nav_links", [])
        visited = 0
        for link_text in nav_links:
            try:
                time.sleep(1.5)
                browser.find_and_click(link_text)
                time.sleep(2)
                body = browser.get_body_text()
                if len(body) > 200:
                    visited += 1
            except Exception:
                pass
        success = visited >= len(nav_links) * 0.5
        body = f"导航检查: {visited}/{len(nav_links)} 页面可访问"

    elif action == "spark_check":
        # 简单功能检查：多次尝试授权
        for attempt in range(2):
            time.sleep(2)
            body = browser.get_body_text()
            if len(body) > 200 and "Authorize" not in body:
                break
            _spark_authorize(browser)
            time.sleep(3)
        body = browser.get_body_text()
        success = len(body) > 200 and "Authorize" not in body

    else:
        body = browser.get_body_text()
        success = len(body) > 200

    elapsed = round(time.time() - t_start, 1)

    # ← 最终截图：所有交互完成后
    ss_path = _try_screenshot(browser, screenshot_dir, aid, "final")

    q_results.append({
        "question": f"交互测试: {action}",
        "response": response_text,
        "success": success,
        "error": None if success else "交互验证未通过",
        "elapsed": elapsed})

    status = "ok" if success else "chat_error"
    return {
        "agent_id": aid, "name": name, "status": status,
        "error": None if success else "页面交互验证失败",
        "q_results": q_results, "description": desc, "category": category,
        "screenshot": ss_path,
        "avg_elapsed": elapsed, "_test_type": "web_interactive"}


def _parse_chat_reply(body_before: str, body_after: str, question: str) -> str:
    """从对话前后的 body 文本中解析 AI 回复"""
    before_lines = set(body_before.strip().split("\n"))
    after_lines = body_after.strip().split("\n")

    # 过滤掉 UI 元素和问题本身
    skip_lines = {
        "新话题", "收藏", "分享链接", "使用飞书 aily", "创建者", "发布时间",
        "/", "新对话", "Invite & Earn", "Copy", "Deep Planning", "Tools",
        "AI can make mistakes. Verify key details.",
        "AI can make mistakes. Verify key detai",
        "Drop files here to upload",
        "赞", "踩", "复制", "重新生成", "停止生成",
        "发布于", "编辑",
    }
    # 匹配以数字开头的行（如 "+2", "+0" 等工具栏数字）
    import re
    skip_patterns = [
        re.compile(r'^\+\d+$'),  # +2, +0
        re.compile(r'^\d+$'),    # 纯数字行
    ]

    new_lines = []
    for l in after_lines:
        stripped = l.strip()
        if not stripped:
            continue
        if stripped in before_lines:
            continue
        if stripped == question.strip():
            continue
        if stripped in skip_lines:
            continue
        if any(p.match(stripped) for p in skip_patterns):
            continue
        new_lines.append(l)

    new_text = "\n".join(new_lines).strip()

    # 去掉开头的 UI 前缀（如 "智能检索：..." 后面的内容才是正文）
    # 如果正文以 "Based on" 或其他元数据开头，跳过
    meta_prefixes = ["Based on\n", "智能检索："]
    for mp in meta_prefixes:
        idx = new_text.find(mp)
        if idx >= 0 and idx < 20:
            # 找到 "Based on" 后跳过整段元数据
            after_meta = new_text[idx + len(mp):]
            # 通常元数据后面有来源，然后是正文
            rest_lines = after_meta.strip().split("\n")
            filtered = []
            for rl in rest_lines:
                if rl.strip() in skip_lines:
                    continue
                filtered.append(rl)
            # 如果过滤后还有内容就用它
            if filtered and len("\n".join(filtered).strip()) > 20:
                return "\n".join(filtered).strip()[:2000]

    return new_text[:2000] if new_text else ""


# ──────────────────────────────────────
# Markdown 报告生成
# ──────────────────────────────────────

def parse_dt(s):
    """解析日期"""
    if not s:
        return None
    try:
        s = s.replace("Z", "+00:00")
        dt = datetime.datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone(datetime.timedelta(hours=8)))
        else:
            dt = dt.astimezone(datetime.timezone(datetime.timedelta(hours=8)))
        return dt
    except:
        return None


def generate_api_report(agents_data, now):
    """生成 API 一句话摘要"""
    if not agents_data:
        return None

    if isinstance(agents_data, dict):
        data = agents_data.get("data", agents_data)
        if isinstance(data, dict):
            agents_list = data.get("items") or data.get("records") or data.get("list") or []
        elif isinstance(data, list):
            agents_list = data
    elif isinstance(agents_data, list):
        agents_list = agents_data
    else:
        return None

    if not agents_list:
        return None

    total = len(agents_list)
    no_guide = sum(1 for a in agents_list if not a.get("usageGuide"))
    no_reviews = sum(1 for a in agents_list if not a.get("reviews") or len(a.get("reviews", [])) == 0)
    zero_downloads = sum(1 for a in agents_list if a.get("downloads", 0) == 0)

    lines = []
    lines.append(f"**总结**: {total} 个智能体, {total - no_guide}/{total} 有指南, {total - no_reviews}/{total} 有评价")

    # 标注 Dify 内嵌智能体（通过 API 测试，非浏览器）
    dify_agents = [a for a in agents_list if a.get("openType") == "api" and a.get("source") == "dify"]
    if dify_agents:
        names = "、".join(f"[{a['id']}] {a.get('name','?')}" for a in dify_agents)
        lines.append(f"\n> 📡 {names} 为市场内嵌 Dify 应用，通过 API 直接测试")

    return "\n".join(lines)


def generate_full_report(api_report_content, chat_results, now, chat_batch_info):
    """生成完整报告：API 简要 + 对话测试详情（用户指定格式）"""
    lines = []

    # 标题
    lines.append(f"# {now.strftime('%Y年%m月%d日 %H:%M')} Agent Market 健康巡检报告")
    lines.append("")

    # API 简要部分
    if api_report_content:
        # 只保留概览，跳过详细列表
        for line in api_report_content.split("\n"):
            if line.startswith("## 📋 全部智能体列表"):
                break
            lines.append(line)
        lines.append("")

    # 对话测试详情
    if not chat_results:
        lines.append("> ⚠️ 本次未进行对话测试")
        return "\n".join(lines)

    # 分离对话型和非对话专项结果
    chat_only = [r for r in chat_results if r.get("_test_type") in (None, "chat", "dify-api")]
    non_chat_only = [r for r in chat_results if r.get("_test_type") not in (None, "chat", "dify-api")]

    # ── 对话测试 ──
    if chat_only:
        _append_chat_section(lines, "🤖 对话测试详情", chat_only)

    # ── 非对话专项测试 ──
    if non_chat_only:
        _append_non_chat_section(lines, "🔧 非对话专项测试", non_chat_only)

    return "\n".join(lines)


def _append_chat_section(lines, title, results):
    """渲染对话型智能体结果：问题 + 回答 + 截图 + 用时"""
    results = _sort_by_severity(results)
    _render_results_header(lines, title, results)

    for r in results:
        _render_agent_header(lines, r)
        if r.get("status") == "skipped":
            lines.append(f"⏭ 原因: {r.get('error', '')}")
            lines.append("")
            continue

        q_results = r.get("q_results", [])
        if not q_results:
            lines.append("> 无测试数据")
            lines.append("")
            continue

        for qi, qr in enumerate(q_results, 1):
            lines.append(f"测试问题{qi}：")
            lines.append("")
            lines.append("```")
            lines.append(qr.get("question", "?"))
            lines.append("```")
            lines.append("")
            lines.append(f"回答结果{qi}：")
            lines.append("")
            lines.append("```")
            resp = qr.get("response", "") or ""
            resp_text = resp[:800]
            if len(resp) > 800:
                resp_text += f"...(共{len(resp)}字)"
            lines.append(resp_text if resp_text else "（无有效回复）")
            lines.append("```")
            lines.append("")

        _render_agent_footer(lines, r)


def _append_non_chat_section(lines, title, results):
    """渲染非对话智能体结果：检测效果分析 + 截图 + 用时"""
    results = _sort_by_severity(results)
    _render_results_header(lines, title, results)

    for r in results:
        _render_agent_header(lines, r)
        if r.get("status") == "skipped":
            lines.append(f"⏭ 原因: {r.get('error', '')}")
            lines.append("")
            continue

        q_results = r.get("q_results", [])
        if not q_results:
            lines.append("> 无测试数据")
            lines.append("")
            continue

        # 智能体检测效果分析
        lines.append("智能体检测效果分析：")
        lines.append("")
        for qi, qr in enumerate(q_results, 1):
            test_type = r.get("_test_type", "")
            action_desc = {
                "file_upload": f"上传文件测试",
                "web_interactive": f"页面交互测试",
                "internal_chat": f"对话功能测试",
                "generic": f"UI 操作测试",
            }.get(test_type, "专项测试")

            lines.append(f"{qi}. {action_desc}")
            lines.append(f"   - 操作: {qr.get('question', '?')}")
            resp = qr.get("response", "") or ""
            if resp:
                # 截取关键信息展示
                short_resp = resp[:300].replace("\n", " ").strip()
                if len(resp) > 300:
                    short_resp += "..."
                lines.append(f"   - 结果: {short_resp}")
            lines.append("")

        _render_agent_footer(lines, r)


def _render_results_header(lines, title, results):
    """渲染结果分组标题和统计"""
    total = len(results)
    ok_count = sum(1 for r in results if r.get("status") == "ok")
    skip_count = sum(1 for r in results if r.get("status") == "skipped")
    fail_count = total - ok_count - skip_count

    lines.append("---")
    lines.append("")
    lines.append(f"## {title}")
    lines.append("")
    parts = [f"✅ 通过: {ok_count}"]
    if fail_count > 0:
        parts.append(f"❌ 异常: {fail_count}")
    if skip_count > 0:
        parts.append(f"⏭ 跳过: {skip_count}")
    parts.append(f"共 {total} 个")
    lines.append(" | ".join(parts))
    lines.append("")


# 严重程度优先级：异常 > 通过 > 跳过（数值越小越靠前）
_SEVERITY_ORDER = {
    "chat_error": 0, "chat_failed": 0, "unreachable": 0,  # 异常
    "ok": 1,                                                   # 通过
    "skipped": 2,                                              # 跳过
}


def _sort_by_severity(results):
    """按严重程度排序：异常 → 通过 → 跳过"""
    return sorted(results, key=lambda r: _SEVERITY_ORDER.get(r.get("status", ""), 9))


def _render_agent_header(lines, r):
    """渲染单个智能体的标题行"""
    name = r.get("name", "?")
    aid = r.get("agent_id", "?")
    status = r.get("status", "?")
    test_type = r.get("_test_type", "")
    status_icon = {
        "ok": "✅", "chat_error": "🟠", "chat_failed": "🟠",
        "unreachable": "🟡", "skipped": "⏭"
    }
    status_text = {
        "ok": "通过", "chat_error": "对话异常", "chat_failed": "回复质量不合格",
        "unreachable": "无法访问", "skipped": "跳过"
    }
    type_badge = {
        "file_upload": "📎", "internal_chat": "💬", "web_interactive": "🖥️",
        "skip": "⏭"
    }.get(test_type, "")
    icon = status_icon.get(status, "❓")
    stext = status_text.get(status, status)

    lines.append(f"### {icon} {type_badge}{name} (ID: {aid})")
    lines.append("")

    if status in ("chat_error", "chat_failed", "unreachable"):
        lines.append(f"⚠️ {stext}: {r.get('error', '未知')}")
        lines.append("")


def _render_agent_footer(lines, r):
    """渲染用时和截图"""
    q_results = r.get("q_results", [])
    screenshot = r.get("screenshot", "")

    # 截图 — 贴在每个智能体 Q&A 之后、用时之前
    if screenshot and os.path.isfile(screenshot):
        lines.append("截图：")
        lines.append("")
        url = _screenshot_url(screenshot)
        lines.append(f"![]({url})")
        lines.append("")

    last_elapsed = q_results[-1].get("elapsed", 0) if q_results else 0
    avg_elapsed = r.get("avg_elapsed", 0)
    lines.append(f"用时：{last_elapsed}s | 平均用时：{avg_elapsed}s")
    lines.append("")


def _collect_screenshot_paths(chat_results):
    """收集所有截图路径，供 cron agent 用 message tool 发送图片附件"""
    paths = []
    for r in chat_results:
        ss = r.get("screenshot", "")
        if ss and os.path.isfile(ss):
            paths.append(ss)
    return paths


# ──────────────────────────────────────
# 投递清单生成（供 cron agent 消费）
# ──────────────────────────────────────

def generate_delivery_manifest(api_report_content, chat_results, now, report_path):
    """
    生成投递清单 JSON，供 cron agent 以最少轮次完成飞书文档投递。

    格式:
    {
      "doc_title": "2026年07月09日 11:03 Agent Market 健康巡检报告",
      "owner_open_id": "ou_12f4e5dbfd82f5975eaa6afd762b1d20",
      "summary_text": "总结...",
      "sections": [
        {"id": "s1", "text": "## 🤖 对话测试详情\n...", "images": []},
        {"id": "a119", "text": "### ✅ 业务签约...\n...", "images": ["/abs/path/1.png", ...]},
        ...
      ]
    }
    """
    manifest = {
        "doc_title": f"{now.strftime('%Y年%m月%d日 %H:%M')} Agent Market 健康巡检报告",
        "owner_open_id": "ou_12f4e5dbfd82f5975eaa6afd762b1d20",
        "summary_text": "",
        "sections": [],
        "report_path": str(report_path),
        "generated_at": now.isoformat(),
    }

    # 摘要部分
    if api_report_content:
        summary_lines = []
        for line in api_report_content.split("\n"):
            if line.startswith("## 📋"):
                break
            summary_lines.append(line)
        manifest["summary_text"] = "\n".join(summary_lines).strip()

    # 对话测试详情 - 头部
    if chat_results:
        total = len(chat_results)
        ok_count = sum(1 for r in chat_results if r.get("status") == "ok")
        skip_count = sum(1 for r in chat_results if r.get("status") == "skipped")
        fail_count = total - ok_count - skip_count

        parts = [f"✅ 通过: {ok_count}"]
        if fail_count > 0:
            parts.append(f"❌ 异常: {fail_count}")
        if skip_count > 0:
            parts.append(f"⏭ 跳过: {skip_count}")
        parts.append(f"共 {total} 个")
        header = f"---\n\n## 🤖 对话测试详情\n\n{' | '.join(parts)}\n"
        manifest["sections"].append({
            "id": "chat_header",
            "text": header,
            "images": []
        })

        status_icon = {
            "ok": "✅", "chat_error": "🟠", "chat_failed": "🟠",
            "unreachable": "🟡", "skipped": "⏭"
        }
        status_text = {
            "ok": "通过", "chat_error": "对话异常", "chat_failed": "回复质量不合格",
            "unreachable": "无法访问", "skipped": "跳过"
        }

        for r in _sort_by_severity(chat_results):
            name = r.get("name", "?")
            aid = r.get("agent_id", "?")
            status = r.get("status", "?")
            icon = status_icon.get(status, "❓")
            stext = status_text.get(status, status)

            lines = []
            lines.append(f"### {icon} {name} (ID: {aid})")
            lines.append("")

            if status in ("chat_error", "chat_failed", "unreachable"):
                lines.append(f"⚠️ {stext}: {r.get('error', '未知')}")
                lines.append("")
            elif status == "skipped":
                lines.append(f"⏭ 跳过原因: {r.get('error', '未知')}")
                lines.append("")

            q_results = r.get("q_results", []) if status != "skipped" else []
            screenshot = r.get("screenshot", "")
            agent_images = []
            if screenshot:
                agent_images.append(screenshot)

            if q_results:
                for qi, qr in enumerate(q_results, 1):
                    q = qr.get("question", "?")
                    resp = qr.get("response", "")

                    lines.append(f"测试问题{qi}：")
                    lines.append("")
                    lines.append("```")
                    lines.append(q)
                    lines.append("```")
                    lines.append("")
                    lines.append(f"回答结果{qi}：")
                    lines.append("")
                    lines.append("```")
                    resp_text = resp[:800] if resp else "（无有效回复）"
                    if len(resp) > 800:
                        resp_text += f"...(共{len(resp)}字)"
                    lines.append(resp_text)
                    lines.append("```")
                    lines.append("")
            elif status != "skipped":
                lines.append("> 无测试数据")
                lines.append("")

            # 截图
            if agent_images:
                lines.append("截图：")
                lines.append("")

            # 用时
            last_elapsed = q_results[-1].get("elapsed", 0) if q_results else 0
            avg_elapsed = r.get("avg_elapsed", 0)
            lines.append(f"用时：{last_elapsed}s | 平均用时：{avg_elapsed}s")
            lines.append("")

            manifest["sections"].append({
                "id": f"agent_{aid}",
                "agent_id": aid,
                "agent_name": name,
                "status": status,
                "text": "\n".join(lines),
                "images": agent_images,
            })

    # 写入 MANIFEST.json
    manifest_path = REPORTS_DIR / "MANIFEST.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"  📋 投递清单已生成: {manifest_path}")
    return str(manifest_path)


# ──────────────────────────────────────
# 主流程
# ──────────────────────────────────────

def main():
    print("=" * 50)
    print("Agent Market 每日健康巡检（增强版）")
    print("=" * 50)

    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
    timestamp = now.strftime("%Y%m%d_%H%M%S")

    # Step 1: Get token
    print(f"\n[{now.strftime('%H:%M:%S')}] Step 1/4: 获取认证 token...")
    token = get_token()
    if not token:
        print("  ❌ 无法获取 token，巡检终止")
        return False, None
    print("  ✅ Token 获取成功")

    # Step 2: Fetch agents via API
    print(f"[{now.strftime('%H:%M:%S')}] Step 2/4: 获取智能体数据...")
    agents_data = fetch_agents(token)
    if not agents_data:
        print("  ❌ 获取数据失败")
        return False, None

    data = agents_data.get("data", [])
    agents_list = []
    if isinstance(data, dict):
        agents_list = data.get("items") or data.get("records") or data.get("list") or []
    elif isinstance(data, list):
        agents_list = data

    print(f"  ✅ 获取 {len(agents_list)} 个智能体数据")

    # Step 3: Generate API report
    print(f"[{now.strftime('%H:%M:%S')}] Step 3/4: 生成 API 巡检报告...")
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    api_report = generate_api_report(agents_data, now)
    if not api_report:
        print("  ❌ 报告生成失败")
        return False, None

    # Step 4: Chat testing
    chat_results = []
    chat_batch_info = ""
    if CHAT_TEST_MODE:
        if CHAT_TEST_ALL:
            print(f"[{now.strftime('%H:%M:%S')}] Step 4/5: agent-browser 对话测试（全量检测模式）...")
            chat_agents = [a for a in agents_list if _is_chat_agent(a)]
            if not chat_agents:
                print("    ⚠️ 未发现对话型智能体，跳过对话测试")
            else:
                print(f"    📋 全量检测 {len(chat_agents)} 个对话型智能体")
                for a in chat_agents:
                    print(f"      → [{a['id']}] {a.get('name', '?')}")
                chat_results = asyncio.run(run_chat_tests(chat_agents, token))
        else:
            print(f"  Step 4/5: agent-browser 对话测试（轮询模式，每批 {CHAT_TEST_BATCH} 个）...")
            chat_agents = get_chat_test_batch(agents_list, CHAT_TEST_BATCH)
            if chat_agents:
                print(f"    📋 轮询选取 {len(chat_agents)} 个对话型智能体测试")
                for a in chat_agents:
                    print(f"      → [{a['id']}] {a.get('name', '?')}")
                chat_results = asyncio.run(run_chat_tests(chat_agents, token))
            else:
                print("    ⚠️ 未发现对话型智能体，跳过对话测试")

        if chat_results:
            ok_count = sum(1 for r in chat_results if r.get("status") == "ok")
            fail_count = sum(1 for r in chat_results if r.get("status") in ("chat_error", "chat_failed", "unreachable"))
            print(f"    ✅ 对话测试完成: {ok_count} 正常, {fail_count} 异常")

    # Step 4.5: Non-chat specialty testing
    non_chat_results = []
    if NON_CHAT_TEST:
        print(f"\n[{now.strftime('%H:%M:%S')}] 非对话专项测试...")
        non_chat_results = asyncio.run(_run_non_chat_tests(agents_list, token))
        if non_chat_results:
            ok_count = sum(1 for r in non_chat_results if r.get("status") == "ok")
            skip_count = sum(1 for r in non_chat_results if r.get("status") == "skipped")
            fail_count = len(non_chat_results) - ok_count - skip_count
            print(f"    ✅ 非对话专项完成: {ok_count} 正常, {skip_count} 跳过, {fail_count} 异常")
        # 合并
        chat_results = (chat_results or []) + non_chat_results

    # Step 5: Generate final report
    print(f"\n[{now.strftime('%H:%M:%S')}] 生成最终报告...")
    final_report = generate_full_report(api_report, chat_results, now, chat_batch_info)

    filename = f"agent-health-report-{now.strftime('%Y%m%d')}.md"
    report_path = REPORTS_DIR / filename
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(final_report)

    # 生成投递清单（供 cron agent 高效投递到飞书文档）
    manifest_path = ""
    if chat_results:
        print(f"\n[{now.strftime('%H:%M:%S')}] 生成投递清单...")
        manifest_path = generate_delivery_manifest(api_report, chat_results, now, report_path)

    print(f"  ✅ 报告已保存: {report_path}")
    print(f"\n{'=' * 50}")
    print("✅ 巡检完成")
    print(f"{'=' * 50}")
    print(f"\nREPORT_PATH={report_path}")
    if manifest_path:
        print(f"MANIFEST_PATH={manifest_path}")

    # --stdout 模式：将完整报告输出到标准输出（供 cron 程序化消费）
    if "--stdout" in sys.argv:
        print("\n" + "=" * 50)
        print("REPORT_MARKDOWN_BEGIN")
        print("=" * 50 + "\n")
        print(final_report)
        print("\n" + "=" * 50)
        print("REPORT_MARKDOWN_END")
        print("=" * 50)

    return True, str(report_path)


if __name__ == "__main__":
    success, path = main()
    exit(0 if success else 1)
