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
TIMEOUT_SECONDS = int(os.getenv("CHAT_TEST_TIMEOUT", "30"))     # 单次对话超时秒数


# ──────────────────────────────────────
# 截图 Base64 内嵌
# ──────────────────────────────────────
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
    # openType=api + dify 源是对话智能体（URL 为空，前端动态生成）
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

    # 从 agents 抽取有真实 chat URL 的
    chat_agents = []
    for a in agents:
        url = a.get("url", "")
        if "feishuapp.cn/ai/gui/chat" in url or "feishu.cn/ai/gui/chat" in url:
            chat_agents.append({**a, "_chat_url": url, "_platform": "feishuapp"})
        elif "aily.feishu.cn/agents/" in url:
            chat_agents.append({**a, "_chat_url": url, "_platform": "aily"})
        elif a.get("openType") == "api" and a.get("source") == "dify":
            # Agent Market 内嵌 Dify 对话（如 ID 63 客户信息查询小助手）
            chat_agents.append({**a, "_chat_url": "https://agent.digitalchina.com/market",
                                "_platform": "market-dify", "_market_agent_id": a["id"]})

    if not chat_agents:
        print("    ⚠️ 未找到对话型智能体的 chat URL，跳过对话测试")
        return []

    print(f"    📋 准备测试 {len(chat_agents)} 个智能体")
    for a in chat_agents:
        platform = getattr(a, '_platform', '') or a.get('_platform', '?')
        print(f"      → [{a['id']}] {a.get('name','?')} [{platform}]")

    try:
        async with async_playwright() as p:
            browser = None
            context = None
            page = None

            async def ensure_browser():
                """确保浏览器可用，崩溃则重建"""
                nonlocal browser, context, page
                try:
                    if browser:
                        await browser.close()
                except:
                    pass
                browser = await p.chromium.launch(
                    headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
                context = await browser.new_context(
                    viewport={"width": 1920, "height": 1080},
                    storage_state=str(PLAYWRIGHT_STATE))
                page = await context.new_page()

            await ensure_browser()
            all_results = []

            for agent in chat_agents:
                agent_id = agent["id"]
                name = agent.get("name", "未知")
                description = agent.get("description", "")
                category = agent.get("categoryLabel", "")
                chat_url = agent["_chat_url"]

                print(f"    🤖 [{agent_id}] {name}")

                try:
                    # 打开聊天页
                    await page.goto(chat_url, wait_until="domcontentloaded", timeout=30000)
                    try:
                        await page.wait_for_load_state("networkidle", timeout=20000)
                    except:
                        pass
                    await asyncio.sleep(5)

                    # 检查是否需要登录/权限限制
                    body = await page.evaluate("document.body.innerText")
                    if "Log In With QR Code" in body or "Scan the QR code" in body:
                        all_results.append({
                            "agent_id": agent_id, "name": name, "status": "unreachable",
                            "error": "飞书登录态已过期，需重新扫码",
                            "description": description, "category": category})
                        continue
                    if "No permission to use" in body:
                        all_results.append({
                            "agent_id": agent_id, "name": name, "status": "unreachable",
                            "error": "无权限访问此智能体（需创建者授权）",
                            "description": description, "category": category})
                        continue

                    # 生成测试问题
                    questions = await generate_test_questions(
                        agent_name=name, agent_type=category, agent_desc=description, count=2)

                    # 找 contenteditable 输入框
                    editor = page.locator('[contenteditable="true"]').first
                    if await editor.count() == 0 or not await editor.is_visible():
                        all_results.append({
                            "agent_id": agent_id, "name": name, "status": "chat_error",
                            "error": "未找到聊天输入框（非聊天型智能体？）",
                            "questions_tested": questions, "description": description, "category": category})
                        continue

                    # 逐个测试问题
                    q_results = []
                    agent_screenshot_dir = str(SCREENSHOTS_DIR / str(agent_id))
                    os.makedirs(agent_screenshot_dir, exist_ok=True)
                    
                    for qi, q in enumerate(questions):
                        await editor.click()
                        await asyncio.sleep(0.5)
                        await editor.type(q, delay=30)
                        await asyncio.sleep(0.5)
                        t_start = time.time()
                        await editor.press("Enter")
                        await asyncio.sleep(10)  # 等 AI 回复

                        # 提取回复：body 里去除初始内容
                        body_after = await page.evaluate("document.body.innerText")
                        reply = _parse_chat_reply(body, body_after, q)
                        elapsed = round(time.time() - t_start, 1)

                        # 截图保存
                        screenshot_path = ""
                        try:
                            import datetime as _dt
                            ss_file = f"{agent_screenshot_dir}/q{qi+1}_{_dt.datetime.now().strftime('%H%M%S')}.png"
                            await page.screenshot(path=ss_file, full_page=False)
                            screenshot_path = ss_file
                        except Exception as se:
                            print(f"      ⚠️ 截图失败: {se}")

                        q_results.append({
                            "question": q,
                            "response": reply,
                            "screenshot": screenshot_path,
                            "success": bool(reply and len(reply) > 5),
                            "error": None if (reply and len(reply) > 5) else "未返回有效回复",
                            "elapsed": elapsed})

                        if qi < len(questions) - 1:
                            await asyncio.sleep(2)

                    # LLM 评估
                    evaluation = None
                    first_resp = next((qr["response"] for qr in q_results if qr["response"]), "")
                    if first_resp:
                        evaluation = await evaluate_response(
                            agent_name=name,
                            question=questions[0] if questions else "",
                            response=first_resp)

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

                    all_results.append({
                        "agent_id": agent_id, "name": name, "status": status, "error": error,
                        "questions_tested": questions, "q_results": q_results,
                        "evaluation": evaluation, "description": description, "category": category,
                        "avg_elapsed": round(sum(qr.get("elapsed", 0) for qr in q_results) / len(q_results), 1) if q_results else 0})

                    await asyncio.sleep(2)

                except asyncio.TimeoutError:
                    all_results.append({"agent_id": agent_id, "name": name, "status": "chat_error",
                                        "error": "页面加载超时", "description": description, "category": category})
                    # 浏览器可能超时不稳定，重建
                    try:
                        await ensure_browser()
                    except:
                        pass
                except Exception as e:
                    err_str = str(e)[:200]
                    all_results.append({"agent_id": agent_id, "name": name, "status": "chat_error",
                                        "error": err_str, "description": description, "category": category})
                    # 浏览器崩溃，重建浏览器继续
                    if "closed" in err_str.lower() or "target" in err_str.lower():
                        try:
                            await ensure_browser()
                        except:
                            pass

            try:
                await browser.close()
            except:
                pass
            return all_results

    except Exception as e:
        print(f"    ❌ 浏览器测试异常: {e}")
        return []


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
    lines.append(f"**一句话总结**: {total} 个智能体, {total - no_guide}/{total} 有指南, {total - no_reviews}/{total} 有评价, {zero_downloads} 零下载")

    return "\n".join(lines)


def generate_full_report(api_report_content, chat_results, now, chat_batch_info):
    """生成完整报告：API 简要 + 对话测试详情（用户指定格式）"""
    lines = []

    # 标题
    lines.append(f"# {now.strftime('%Y年%m月%d日 %H:%M')} Agent Market 健康巡检报告")
    lines.append("")

    # API 简要部分
    if api_report_content:
        # 只保留概览表格和问题摘要，跳过详细列表
        for line in api_report_content.split("\n"):
            if line.startswith("## 📋 全部智能体列表"):
                break  # 截断，不要全量列表
            lines.append(line)
        lines.append("")

    # 对话测试详情
    if not chat_results:
        lines.append("> ⚠️ 本次未进行对话测试")
        return "\n".join(lines)

    total = len(chat_results)
    ok_count = sum(1 for r in chat_results if r.get("status") == "ok")
    fail_count = sum(1 for r in chat_results if r.get("status") in ("chat_error", "chat_failed", "unreachable"))

    lines.append("---")
    lines.append("")
    lines.append("## 🤖 对话测试详情")
    lines.append("")
    lines.append(f"✅ 通过: {ok_count} | ❌ 异常: {fail_count} | 共 {total} 个")
    lines.append("")

    status_icon = {
        "ok": "✅", "chat_error": "🟠", "chat_failed": "🟠",
        "unreachable": "🟡", "skipped": "⏭"
    }
    status_text = {
        "ok": "通过", "chat_error": "对话异常", "chat_failed": "回复质量不合格",
        "unreachable": "无法访问", "skipped": "跳过"
    }

    for r in chat_results:
        name = r.get("name", "?")
        aid = r.get("agent_id", "?")
        status = r.get("status", "?")
        icon = status_icon.get(status, "❓")
        stext = status_text.get(status, status)

        lines.append(f"### {icon} {name} (ID: {aid})")
        lines.append("")

        if status in ("chat_error", "chat_failed", "unreachable"):
            lines.append(f"⚠️ {stext}: {r.get('error', '未知')}")
            lines.append("")

        q_results = r.get("q_results", [])
        if not q_results:
            lines.append("> 无测试数据")
            lines.append("")
            continue

        for qi, qr in enumerate(q_results, 1):
            q = qr.get("question", "?")
            resp = qr.get("response", "")
            elapsed = qr.get("elapsed", 0)

            resp_display = resp[:500] if resp else "（无有效回复）"
            if len(resp) > 500:
                resp_display += f"...(共{len(resp)}字)"

            lines.append(f"测试问题{qi}：{q}")
            lines.append(f"回答结果{qi}：{resp_display}")
            if elapsed:
                lines.append(f"用时：{elapsed}s")

        avg_elapsed = r.get("avg_elapsed", 0)
        if avg_elapsed:
            lines[-1] = lines[-1] + f"（平均{avg_elapsed}s）"  # append to last time line
        
        lines.append("")

    return "\n".join(lines)


def _collect_screenshot_paths(chat_results):
    """收集所有截图路径，供 cron agent 用 message tool 发送图片附件"""
    paths = []
    for r in chat_results:
        for qr in r.get("q_results", []):
            ss = qr.get("screenshot", "")
            if ss and os.path.isfile(ss):
                paths.append(ss)
    return paths


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

    # Step 4: Optional chat testing
    chat_results = []
    chat_batch_info = ""
    if CHAT_TEST_MODE:
        if CHAT_TEST_ALL:
            print(f"[{now.strftime('%H:%M:%S')}] Step 4/4: Playwright 对话测试（全量检测模式）...")
            # 全量：直接取所有对话型智能体
            chat_agents = [a for a in agents_list if _is_chat_agent(a)]
            if not chat_agents:
                print("    ⚠️ 未发现对话型智能体，跳过对话测试")
            else:
                print(f"    📋 全量检测 {len(chat_agents)} 个对话型智能体")
                for a in chat_agents:
                    print(f"      → [{a['id']}] {a.get('name', '?')}")
                chat_results = asyncio.run(run_chat_tests(chat_agents, token))
        else:
            print(f"[{now.strftime('%H:%M:%S')}] Step 4/4: Playwright 对话测试（轮询模式，每批 {CHAT_TEST_BATCH} 个）...")
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

    # Step 5: Generate final report
    print(f"\n[{now.strftime('%H:%M:%S')}] 生成最终报告...")
    final_report = generate_full_report(api_report, chat_results, now, chat_batch_info)

    filename = f"agent-health-report-{now.strftime('%Y%m%d')}.md"
    report_path = REPORTS_DIR / filename
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(final_report)

    # 输出截图路径列表（供 cron agent 用 message tool 发送图片附件）
    ss_paths = _collect_screenshot_paths(chat_results) if chat_results else []
    if ss_paths:
        print(f"\nSCREENSHOT_PATHS_BEGIN")
        for p in ss_paths:
            print(p)
        print(f"SCREENSHOT_PATHS_END")
        print(f"共 {len(ss_paths)} 张截图")

    print(f"  ✅ 报告已保存: {report_path}")
    print(f"\n{'=' * 50}")
    print("✅ 巡检完成")
    print(f"{'=' * 50}")
    print(f"\nREPORT_PATH={report_path}")

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
