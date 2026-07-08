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
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent))

ROOT = Path(__file__).parent
REPORTS_DIR = ROOT / "reports"
TOKEN_CACHE = ROOT / ".auth/token.txt"
STATE_FILE = ROOT / ".auth/chat-test-state.json"
PLAYWRIGHT_STATE = ROOT / ".auth/playwright_state.json"
CHAT_TEST_MODE = os.getenv("CHAT_TEST", "0").lower() in ("1", "true", "yes")
CHAT_TEST_BATCH = int(os.getenv("CHAT_TEST_BATCH", "5"))       # 每次测试多少个（轮询模式）
CHAT_TEST_ALL = os.getenv("CHAT_TEST_ALL", "0").lower() in ("1", "true", "yes")  # 全量检测模式
TIMEOUT_SECONDS = int(os.getenv("CHAT_TEST_TIMEOUT", "30"))     # 单次对话超时秒数


# ──────────────────────────────────────
# Token 获取
# ──────────────────────────────────────

def get_token():
    """获取 API token - 通过 Playwright 登录 Agent Market 并从网络请求中捕获"""
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
    """判断是否为对话型智能体（仅检测 feishuapp.cn/ai/gui/chat URL）"""
    url = agent.get("url", "")
    return "feishuapp.cn/ai/gui/chat" in url or "feishu.cn/ai/gui/chat" in url


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
            chat_agents.append({**a, "_chat_url": url})

    if not chat_agents:
        print("    ⚠️ 未找到对话型智能体的 chat URL，跳过对话测试")
        return []

    print(f"    📋 准备测试 {len(chat_agents)} 个智能体")
    for a in chat_agents:
        print(f"      → [{a['id']}] {a.get('name','?')} | {a['_chat_url'][:80]}")

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                storage_state=str(PLAYWRIGHT_STATE))
            page = await context.new_page()

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

                    # 检查是否需要登录/SSo 弹窗
                    body = await page.evaluate("document.body.innerText")
                    if "Log In With QR Code" in body or "Scan the QR code" in body:
                        all_results.append({
                            "agent_id": agent_id, "name": name, "status": "unreachable",
                            "error": "飞书登录态已过期，需重新扫码",
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
                    for qi, q in enumerate(questions):
                        await editor.click()
                        await asyncio.sleep(0.5)
                        await editor.type(q, delay=30)
                        await asyncio.sleep(0.5)
                        await editor.press("Enter")
                        await asyncio.sleep(10)  # 等 AI 回复

                        # 提取回复：body 里去除初始内容
                        body_after = await page.evaluate("document.body.innerText")
                        reply = _parse_chat_reply(body, body_after, q)
                        q_results.append({
                            "question": q,
                            "response": reply,
                            "success": bool(reply and len(reply) > 5),
                            "error": None if (reply and len(reply) > 5) else "未返回有效回复"})

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
                        "evaluation": evaluation, "description": description, "category": category})

                    await asyncio.sleep(2)

                except asyncio.TimeoutError:
                    all_results.append({"agent_id": agent_id, "name": name, "status": "chat_error",
                                        "error": "页面加载超时", "description": description, "category": category})
                except Exception as e:
                    all_results.append({"agent_id": agent_id, "name": name, "status": "chat_error",
                                        "error": str(e)[:200], "description": description, "category": category})

            await browser.close()
            return all_results

    except Exception as e:
        print(f"    ❌ 浏览器测试异常: {e}")
        return []


def _parse_chat_reply(body_before: str, body_after: str, question: str) -> str:
    """从对话前后的 body 文本中解析 AI 回复"""
    # 简单策略：去掉 body_before 里已有的内容，剩下的"新内容"就是回复
    # 先取 after 独有的行
    before_lines = set(body_before.strip().split("\n"))
    after_lines = body_after.strip().split("\n")
    new_lines = [l for l in after_lines if l.strip() and l not in before_lines]
    new_text = "\n".join(new_lines).strip()

    # 去掉问题本身
    if new_text.startswith(question):
        new_text = new_text[len(question):].strip()

    # 智能体名称开头的一些固定文本不算回复
    skip_prefixes = ["新话题", "收藏", "分享链接", "使用飞书 aily", "创建者", "发布时间"]
    for sp in skip_prefixes:
        if new_text.startswith(sp):
            # 去掉这一行
            first_nl = new_text.find("\n")
            new_text = new_text[first_nl+1:].strip() if first_nl > 0 else ""
            break

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
    """生成 API 方式的 MD 报告（基础统计）"""
    if not agents_data:
        return None

    # agents_data 可能是 dict {"data": [...]} 或直接是 list
    if isinstance(agents_data, dict):
        data = agents_data.get("data", agents_data)
        if isinstance(data, dict):
            agents_list = data.get("items") or data.get("records") or data.get("list") or []
        elif isinstance(data, list):
            agents_list = data
        else:
            return None
    elif isinstance(agents_data, list):
        agents_list = agents_data
    else:
        return None

    if not agents_list:
        return None

    total = len(agents_list)
    total_downloads = sum(a.get("downloads", 0) for a in agents_list)
    total_likes = sum(a.get("likes", 0) for a in agents_list)
    installed_count = sum(1 for a in agents_list if a.get("installed"))
    no_guide = sum(1 for a in agents_list if not a.get("usageGuide"))
    no_reviews = sum(1 for a in agents_list if not a.get("reviews") or len(a.get("reviews", [])) == 0)
    zero_downloads = sum(1 for a in agents_list if a.get("downloads", 0) == 0)
    zero_likes = sum(1 for a in agents_list if a.get("likes", 0) == 0)
    no_rating = sum(1 for a in agents_list if a.get("rating", 0) == 0)

    categories = defaultdict(list)
    for a in agents_list:
        categories[a.get("categoryLabel", "未分类")].append(a)
    sorted_cats = sorted(categories.keys(), key=lambda c: len(categories[c]), reverse=True)

    lines = []
    lines.append("# Agent Market 健康巡检报告")
    lines.append("")
    lines.append(f"**巡检时间**: {now.strftime('%Y-%m-%d %H:%M:%S')} (Asia/Shanghai)")
    lines.append(f"**巡检账号**: zhangzlt (张藻林)")
    lines.append(f"**数据来源**: API 直接采集")
    lines.append("")
    lines.append("## 📊 巡检概览")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("|------|------|")
    lines.append(f"| 智能体总数 | {total} |")
    lines.append(f"| 已安装 | {installed_count} |")
    lines.append(f"| 总下载量 | {total_downloads} |")
    lines.append(f"| 总点赞量 | {total_likes} |")
    lines.append(f"| 有使用指南 | {total - no_guide} / {total} |")
    lines.append(f"| 有用户评价 | {total - no_reviews} / {total} |")
    lines.append(f"| 零下载 | {zero_downloads} 个 |")
    lines.append(f"| 零点赞 | {zero_likes} 个 |")
    lines.append(f"| 无评分 | {no_rating} 个 |")
    lines.append(f"| 分类数 | {len(sorted_cats)} |")
    lines.append("")

    # 问题智能体
    problem_agents = []
    for a in agents_list:
        issues = []
        if not a.get("usageGuide"):
            issues.append("无使用指南")
        if not a.get("reviews") or len(a.get("reviews", [])) == 0:
            issues.append("无用户评价")
        dl = a.get("downloads", 0)
        if dl == 0:
            issues.append("零下载")
        elif dl < 3:
            issues.append("下载量偏低")
        if a.get("likes", 0) == 0:
            issues.append("零点赞")
        if a.get("rating", 0) == 0:
            issues.append("无评分")
        if issues:
            a["_issues"] = issues
            problem_agents.append(a)

    lines.append("## ⚠️ 有问题的智能体")
    lines.append("")

    if not problem_agents:
        lines.append("✅ 全部智能体运行正常，无异常。")
        lines.append("")
    else:
        # 按严重程度分组
        critical = [a for a in problem_agents if any("无使用指南" in i for i in a.get("_issues", []))]
        no_review = [a for a in problem_agents if "无用户评价" in a.get("_issues", [])
                     and not any("无使用指南" in i for i in a.get("_issues", []))]
        low_usage = [a for a in problem_agents if any("零下载" in i or "下载量偏低" in i for i in a.get("_issues", []))]

        lines.append(f"| 类型 | 数量 | 说明 |")
        lines.append(f"|------|------|------|")
        lines.append(f"| 🔴 缺少使用指南 | {len(critical)} | 无使用指南 |")
        lines.append(f"| 🔴 缺少评价 | {len(no_review)} | 无用户评价 |")
        lines.append(f"| 🟡 使用率低 | {len(low_usage)} | 零下载或下载量偏低 |")
        lines.append("")

        if critical:
            lines.append("### 🔴 缺少使用指南")
            lines.append("")
            lines.append("| ID | 名称 | 作者 | 问题 |")
            lines.append("|---|------|------|------|")
            for a in critical:
                issue_text = ", ".join(i for i in a["_issues"] if "无使用指南" in i)
                lines.append(f'| {a["id"]} | {a["name"]} | {a.get("author", "")} | {issue_text} |')
            lines.append("")

        if no_review:
            lines.append("### 🔴 无用户评价")
            lines.append("")
            lines.append("| ID | 名称 | 作者 | 下载 |")
            lines.append("|---|------|------|------|")
            for a in no_review:
                lines.append(f'| {a["id"]} | {a["name"]} | {a.get("author", "")} | {a.get("downloads", 0)} |')
            lines.append("")

        if low_usage:
            lines.append("### 🟡 下载量偏低 / 零下载")
            lines.append("")
            lines.append("| ID | 名称 | 下载 | 点赞 | 问题 |")
            lines.append("|---|------|------|------|------|")
            for a in low_usage:
                issue_text = ", ".join(i for i in a["_issues"] if "零下载" in i or "下载量偏低" in i)
                lines.append(f'| {a["id"]} | {a["name"]} | {a.get("downloads", 0)} | {a.get("likes", 0)} | {issue_text} |')
            lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## 📋 全部智能体列表（按分类）")
    lines.append("")

    for cat in sorted_cats:
        cat_agents = categories[cat]
        lines.append(f"### {cat}（{len(cat_agents)} 个）")
        lines.append("")
        lines.append("| ID | 名称 | 作者 | 下载 | 点赞 | 指南 | 评价 |")
        lines.append("|----|------|------|------|------|------|------|")
        for a in cat_agents:
            has_guide = "✅" if a.get("usageGuide") else "❌"
            has_review = "✅" if a.get("reviews") and len(a["reviews"]) > 0 else "❌"
            lines.append(f'| {a["id"]} | {a["name"]} | {a.get("author", "")} | {a.get("downloads", 0)} | {a.get("likes", 0)} | {has_guide} | {has_review} |')
        lines.append("")

    return "\n".join(lines)


def generate_full_report(api_report_content, chat_results, now, chat_batch_info):
    """生成完整报告：API 基础 + 对话测试详细结果"""
    lines = []

    # API 部分
    if api_report_content:
        lines.append(api_report_content)

    # 对话测试部分
    if not chat_results:
        return "\n".join(lines)

    lines.append("---")
    lines.append("")
    lines.append("## 🤖 对话测试详细报告")
    lines.append("")

    # ── 总体统计 ──
    total = len(chat_results)
    ok_count = sum(1 for r in chat_results if r.get("status") == "ok")
    fail_count = sum(1 for r in chat_results if r.get("status") in ("chat_error", "chat_failed", "unreachable"))
    skip_count = sum(1 for r in chat_results if r.get("status") == "skipped")
    lines.append(f"**测试范围**: {total} 个对话型智能体 | ✅ 通过: {ok_count} | ❌ 异常: {fail_count} | ⏭️ 跳过: {skip_count}")
    lines.append("")

    # ── 逐一展示每个智能体的测试详情 ──
    lines.append("## 📋 逐项测试详情")
    lines.append("")

    for idx, r in enumerate(chat_results, 1):
        name = r.get("name", "?")
        aid = r.get("agent_id", "?")
        status = r.get("status", "?")

        # 状态标签
        status_map = {
            "ok":            "✅ 通过",
            "chat_error":    "🟠 对话异常",
            "chat_failed":   "🟠 回复质量不合格",
            "unreachable":   "🟡 无法访问",
            "skipped":       "⏭️ 跳过",
        }
        status_label = status_map.get(status, f"❓ {status}")

        lines.append(f"### {idx}. {status_label} — {name} (ID: {aid})")
        lines.append("")

        # 智能体基本信息
        desc = r.get("description", "")
        cat = r.get("category", "")
        if desc:
            lines.append(f"**描述**: {desc[:200]}")
        if cat:
            lines.append(f"**分类**: {cat}")
        lines.append("")

        # 异常类型：快速展示
        if status in ("chat_error", "chat_failed", "unreachable"):
            error = r.get("error", "未知错误")
            lines.append(f"**⚠️ 异常原因**: {error}")
            lines.append("")

        # ── 测试问题与回答 ──
        q_results = r.get("q_results", [])
        questions = r.get("questions_tested", [])

        if q_results:
            lines.append("#### 💬 测试对话")
            lines.append("")
            for qi, qr in enumerate(q_results, 1):
                q = qr.get("question", "?")
                resp = qr.get("response", "")
                q_ok = qr.get("success", False)
                q_err = qr.get("error", "")

                lines.append(f"**Q{qi}**: {q}")
                lines.append("")
                if resp:
                    # 截取前 500 字避免报告过长
                    resp_display = resp[:500].replace("\n", "\n> ")
                    if len(resp) > 500:
                        resp_display += f"\n> ...\n> _(回复过长，已截断，共 {len(resp)} 字)_"
                    lines.append(f"> {resp_display}")
                    lines.append("")
                elif q_err:
                    lines.append(f"> ⛔ {q_err}")
                    lines.append("")
                else:
                    lines.append(f"> ⛔ 未返回有效回复")
                    lines.append("")

        elif questions:
            lines.append("**计划测试问题**: " + ", ".join(questions[:5]))
            lines.append("")

        # ── LLM 评估分析 ──
        evaluation = r.get("evaluation")
        if evaluation:
            lines.append("#### 📊 对话分析")
            lines.append("")
            passed = evaluation.get("passed", False)
            score = evaluation.get("score", 0)
            issues = evaluation.get("issues", [])

            passed_icon = "✅ 通过" if passed else "❌ 未通过"
            lines.append(f"**评估结果**: {passed_icon} | **评分**: {score}/10")
            if issues:
                lines.append("")
                lines.append("**发现的问题**:")
                for issue in issues:
                    lines.append(f"- {issue}")
            lines.append("")

        # 分隔线
        if idx < total:
            lines.append("---")
            lines.append("")

    # ── 底部汇总 ──
    lines.append("---")
    lines.append("")
    lines.append("## 📊 对话测试汇总")
    lines.append("")
    lines.append(f"| 状态 | 数量 | 占比 |")
    lines.append(f"|------|------|------|")
    lines.append(f"| ✅ 通过 | {ok_count} | {ok_count*100//total if total else 0}% |")
    lines.append(f"| ❌ 异常 | {fail_count} | {fail_count*100//total if total else 0}% |")
    lines.append(f"| ⏭️ 跳过 | {skip_count} | {skip_count*100//total if total else 0}% |")
    lines.append("")

    if CHAT_TEST_ALL:
        lines.append(f"> 本次为**全量检测模式**，已覆盖全部 {total} 个对话型智能体。")
    else:
        total_chat = len(chat_results)
        lines.append(f"> 本次为**轮询模式**（每批 {CHAT_TEST_BATCH} 个），覆盖全部约需 **{max(1, round(total_chat / CHAT_TEST_BATCH))} 天**。")

    lines.append("")

    return "\n".join(lines)


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

    print(f"  ✅ 报告已保存: {report_path}")
    print(f"\n{'=' * 50}")
    print("✅ 巡检完成")
    print(f"{'=' * 50}")
    print(f"\nREPORT_PATH={report_path}")
    return True, str(report_path)


if __name__ == "__main__":
    success, path = main()
    exit(0 if success else 1)
