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
import hashlib
import shutil
import struct
from pathlib import Path
from collections import defaultdict

# Windows 定时任务常继承 GBK 控制台；统一 UTF-8，避免中文/图标输出中断任务。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent))

ROOT = Path(__file__).parent
REPORTS_DIR = ROOT / "reports"
RUNS_DIR = REPORTS_DIR / "runs"
TOKEN_CACHE = ROOT / ".auth/token.txt"
STATE_FILE = ROOT / ".auth/chat-test-state.json"
PLAYWRIGHT_STATE = ROOT / ".auth/playwright_state.json"
FEISHU_BROWSER_PROFILE = Path(
    os.getenv("FEISHU_BROWSER_PROFILE", str(ROOT / ".auth/feishu-browser-profile"))
).expanduser()
SCREENSHOTS_DIR = REPORTS_DIR / "screenshots"
RUN_DIR = REPORTS_DIR
RUN_ID = "legacy"
CHECKPOINT_PATH = REPORTS_DIR / "run.json"
RUN_LOCK_PATH = REPORTS_DIR / ".inspection.lock"
RUN_LOCK_FD = None
RUN_VALIDATION = {"complete": False, "errors": ["尚未执行完整性校验"]}
CURRENT_AGENT_CONTEXT = {}
CHAT_TEST_MODE = os.getenv("CHAT_TEST", "0").lower() in ("1", "true", "yes")
CHAT_TEST_BATCH = int(os.getenv("CHAT_TEST_BATCH", "5"))
CHAT_TEST_ALL = os.getenv("CHAT_TEST_ALL", "0").lower() in ("1", "true", "yes")
NON_CHAT_TEST = os.getenv("NON_CHAT_TEST", "0").lower() in ("1", "true", "yes")
TIMEOUT_SECONDS = int(os.getenv("CHAT_TEST_TIMEOUT", "60"))
CHAT_QUESTION_COUNT = max(1, int(os.getenv("CHAT_QUESTION_COUNT", "1")))
REPORT_RETENTION_DAYS = int(os.getenv("REPORT_RETENTION_DAYS", "7"))
RUN_LOCK_STALE_SECONDS = int(os.getenv("RUN_LOCK_STALE_SECONDS", str(4 * 60 * 60)))


def _atomic_write_json(path: Path, payload: dict):
    """同目录临时文件 + replace，避免进程中断留下半份 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _has_feishu_browser_auth() -> bool:
    """持久 profile 优先；兼容旧版 storage_state 文件。"""
    return FEISHU_BROWSER_PROFILE.is_dir() or PLAYWRIGHT_STATE.is_file()


def _load_credentials() -> dict:
    """从 .auth/credentials.json 加载凭据，不会出现在日志/GitHub。"""
    cred_path = ROOT / ".auth/credentials.json"
    if not cred_path.is_file():
        return {}
    with open(cred_path) as f:
        return json.load(f)


def _agent_browser_auth_kwargs() -> dict:
    """集中生成浏览器认证参数，避免不同测试路径使用不同登录态。"""
    return {
        "profile_path": str(FEISHU_BROWSER_PROFILE)
        if FEISHU_BROWSER_PROFILE.is_dir()
        else None,
        "state_path": str(PLAYWRIGHT_STATE) if PLAYWRIGHT_STATE.is_file() else None,
    }


def _configure_run_context(run_id: str):
    """为本次巡检建立独立目录，禁止不同批次互相覆盖证据。"""
    global RUN_ID, RUN_DIR, SCREENSHOTS_DIR, CHECKPOINT_PATH
    RUN_ID = run_id
    RUN_DIR = RUNS_DIR / run_id
    SCREENSHOTS_DIR = RUN_DIR / "screenshots"
    CHECKPOINT_PATH = RUN_DIR / "run.json"
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)


def _acquire_run_lock():
    """单机互斥锁，防止 Cron 重叠执行导致截图与报告串项。"""
    global RUN_LOCK_FD
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    if RUN_LOCK_PATH.exists():
        age = time.time() - RUN_LOCK_PATH.stat().st_mtime
        if age <= RUN_LOCK_STALE_SECONDS:
            details = RUN_LOCK_PATH.read_text(encoding="utf-8", errors="replace")
            raise RuntimeError(f"已有巡检正在运行：{details.strip()}")
        RUN_LOCK_PATH.unlink(missing_ok=True)
    RUN_LOCK_FD = os.open(str(RUN_LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.write(
        RUN_LOCK_FD,
        f"run_id={RUN_ID}; pid={os.getpid()}; started={datetime.datetime.now().isoformat()}".encode("utf-8"),
    )
    os.fsync(RUN_LOCK_FD)


def _release_run_lock():
    global RUN_LOCK_FD
    if RUN_LOCK_FD is not None:
        try:
            os.close(RUN_LOCK_FD)
        finally:
            RUN_LOCK_FD = None
    RUN_LOCK_PATH.unlink(missing_ok=True)


def _cleanup_old_runs():
    """只清理 runs 下超出保留期的完整运行目录。"""
    if not RUNS_DIR.exists():
        return
    cutoff = time.time() - REPORT_RETENTION_DAYS * 86400
    for child in RUNS_DIR.iterdir():
        if child.is_dir() and child != RUN_DIR and child.stat().st_mtime < cutoff:
            shutil.rmtree(child, ignore_errors=True)


def _save_checkpoint(agents: list, results: list, state: str = "运行中"):
    """每完成一个智能体立即持久化，崩溃后仍可审计已完成部分。"""
    payload = {
        "run_id": RUN_ID,
        "state": state,
        "updated_at": datetime.datetime.now(
            datetime.timezone(datetime.timedelta(hours=8))
        ).isoformat(),
        "expected_count": len(agents),
        "completed_count": len(results),
        "next_inspection_index": min(len(results) + 1, len(agents) + 1),
        "results": results,
    }
    _atomic_write_json(CHECKPOINT_PATH, payload)


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
    """返回相对于本次报告目录的正斜杠路径，便于本地 Markdown 直接显示。"""
    try:
        return Path(absolute_path).resolve().relative_to(RUN_DIR.resolve()).as_posix()
    except (ValueError, OSError):
        return Path(absolute_path).as_posix()
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
            # 优先环境变量，其次 credentials.json
            creds = _load_credentials().get("agent_market", {})
            username = os.getenv("AGENT_MARKET_USERNAME", "").strip() or creds.get("username", "")
            password = os.getenv("AGENT_MARKET_PASSWORD", "") or creds.get("password", "")
            if not username or not password:
                await browser.close()
                raise RuntimeError(
                    "登录态已失效；请设置 AGENT_MARKET_USERNAME/AGENT_MARKET_PASSWORD "
                    "环境变量或 .auth/credentials.json"
                )
            await page.locator("input[placeholder*=itcode]").first.fill(username, timeout=5000)
            await page.locator("input[placeholder*=统一认证密码]").first.fill(password, timeout=5000)
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

    if not _has_feishu_browser_auth():
        print("    ❌ 未找到飞书登录态，请先扫码登录")
        return [{"agent_id": "N/A", "name": "登录态缺失", "status": "skipped",
                 "error": "请先运行 feishu_login.py 完成一次可视化登录"}]

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

    # ── API 测试（Dify）：API 返回后立刻补充页面证据截图 ──
    for agent in dify_agents:
        result = await _run_dify_api_test(agent, token)
        aid = agent["id"]
        screenshot_dir = _agent_screenshot_dir(aid)
        evidence_browser = None
        try:
            import sys
            sys.path.insert(0, str(Path(__file__).parent.parent))
            from agent_browser_wrapper import AgentBrowser
            evidence_browser = AgentBrowser(
                **_agent_browser_auth_kwargs(),
                session=f"dify-evidence-{aid}-{int(time.time())}",
            )
            evidence_url = agent.get("url") or f"https://agent.digitalchina.com/widget/open?agentId={aid}"
            evidence_browser.open(evidence_url, timeout=30)
            result["screenshot"] = _try_screenshot(evidence_browser, screenshot_dir, aid, "final")
        except Exception as exc:
            result["screenshot"] = ""
            result["evidence_error"] = f"Dify 测试完成但页面截图失败: {exc}"
        finally:
            if evidence_browser:
                try:
                    evidence_browser.close()
                except Exception:
                    pass
        # 截图证据不完整不改变通过状态
        if not result.get("screenshot"):
            result["evidence_error"] = "截图元数据缺失"
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
    DIFY_APPID_MAP = {}  # 63 已移除，改用浏览器直接测试
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
            agent_name=name, agent_type=category, agent_desc=description, count=CHAT_QUESTION_COUNT)

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

    if not _has_feishu_browser_auth():
        print("    ❌ 未找到飞书登录态，请先扫码登录")
        return [{"agent_id": "N/A", "name": "登录态缺失", "status": "skipped",
                 "error": "请先运行 feishu_login.py 完成一次可视化登录"}]

    all_results = []
    browser = None

    try:
        browser = AgentBrowser(
            **_agent_browser_auth_kwargs(),
            session=f"chat-{RUN_ID}-{int(time.time() * 1000)}",
        )

        for agent in browser_agents:
            agent_id = agent["id"]
            name = agent.get("name", "未知")
            description = agent.get("description", "")
            category = agent.get("categoryLabel", "")
            chat_url = agent["_chat_url"]
            agent_screenshot_dir = _agent_screenshot_dir(agent_id)
            os.makedirs(agent_screenshot_dir, exist_ok=True)

            print(f"    🤖 [{agent_id}] {name}")

            try:
                # 导航到聊天页（首次含 state 载入，后续仅导航）
                browser.open(
                    chat_url,
                    timeout=30,
                    wait_selector="[contenteditable], textarea, input[type='text']",
                    wait_timeout=15,
                )

                body = browser.get_body_text()
                if "Log In With QR Code" in body or "Scan the QR code" in body:
                    all_results.append({
                        "agent_id": agent_id, "name": name, "status": "unreachable",
                        "error": "飞书登录态已过期，需重新扫码",
                        "description": description, "category": category,
                        "screenshot": _try_screenshot(
                            browser, agent_screenshot_dir, agent_id, "final"
                        )})
                    continue
                if "No permission to use" in body or "应用不存在" in body:
                    all_results.append({
                        "agent_id": agent_id, "name": name, "status": "unreachable",
                        "error": "无权限访问此智能体（需创建者授权）",
                        "description": description, "category": category,
                        "screenshot": _try_screenshot(
                            browser, agent_screenshot_dir, agent_id, "final"
                        )})
                    continue

                # 生成测试问题
                questions = await generate_test_questions(
                    agent_name=name, agent_type=category, agent_desc=description, count=CHAT_QUESTION_COUNT)

                q_results = []

                for qi, q in enumerate(questions):
                    body_before = browser.get_body_text()

                    t_start = time.time()
                    browser.chat_send(q)
                    reply_body = browser.chat_wait(timeout=TIMEOUT_SECONDS, body_before=body_before, question=q)
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
                agent_screenshot = _try_screenshot(browser, agent_screenshot_dir, agent_id, "final")

                evaluation = None
                first_resp = next((qr["response"] for qr in q_results if qr["response"]), "")
                if first_resp:
                    evaluation = await evaluate_response(
                        agent_name=name, question=questions[0] if questions else "", response=first_resp)

                if not q_results or all(not qr.get("success") for qr in q_results):
                    status = "chat_error"
                    error = q_results[0]["error"] if q_results else "无回复"
                else:
                    status = "ok"
                    error = None

                all_results.append({
                    "agent_id": agent_id, "name": name, "status": status, "error": error,
                    "questions_tested": questions, "q_results": q_results,
                    "evaluation": evaluation, "description": description, "category": category,
                    "screenshot": agent_screenshot,
                    "avg_elapsed": round(sum(qr.get("elapsed", 0) for qr in q_results) / len(q_results), 1) if q_results else 0})

            except AgentBrowserError as e:
                all_results.append({"agent_id": agent_id, "name": name, "status": "chat_error",
                                    "error": f"agent-browser 错误: {str(e)[:200]}",
                                    "description": description, "category": category})
            except Exception as e:
                err_str = str(e)[:200]
                all_results.append({"agent_id": agent_id, "name": name, "status": "chat_error",
                                    "error": err_str, "description": description, "category": category})

            # 单项事务收尾：截图绑定成功或明确记录证据失败后，才进入下一项。
            current = all_results[-1]
            if str(current.get("agent_id")) == str(agent_id) and not current.get("screenshot"):
                current["screenshot"] = _try_screenshot(browser, agent_screenshot_dir, agent_id, "final")
            if not current.get("screenshot"):
                current["evidence_error"] = "最终状态截图失败"
            time.sleep(2)

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
    119: {"type": "skip", "name": "业务签约法人体智能推荐", "reason": "缓存剧本回放卡死，需人工排查"},
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
            **_agent_browser_auth_kwargs(),
            session=f"non-chat-{str(int(time.time()))}",
        )

        for agent_with_cfg in targets:
            aid = agent_with_cfg["id"]
            name = agent_with_cfg["name"]
            cfg = agent_with_cfg["_test_cfg"]
            atype = cfg["type"]
            desc = agent_with_cfg.get("description", "")
            category = agent_with_cfg.get("categoryLabel", "")

            print(f"    🔍 [{aid}] {name} ({atype})")
            screenshot_dir = _agent_screenshot_dir(aid)
            os.makedirs(screenshot_dir, exist_ok=True)

            try:
                if atype == "skip":
                    try:
                        target_url = (
                            cfg.get("url")
                            or agent_with_cfg.get("url")
                            or agent_with_cfg.get("openUrl")
                            or f"https://agent.digitalchina.com/market?agentId={aid}"
                        )
                        if target_url:
                            browser.open(target_url, timeout=30)
                    except Exception:
                        pass
                    results.append({
                        "agent_id": aid, "name": name, "status": "skipped",
                        "error": cfg["reason"],
                        "description": desc, "category": category,
                        "_test_type": atype})

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

            # 非对话单项统一收尾，避免异常/跳过分支绕过截图。
            current = results[-1]
            if str(current.get("agent_id")) == str(aid) and not current.get("screenshot"):
                current["screenshot"] = _try_screenshot(browser, screenshot_dir, aid, "final")
            if not current.get("screenshot"):
                current["evidence_error"] = "最终状态截图失败"

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
    """通用测试：打开页面、执行一个安全交互、验证页面仍有响应、最终截图。"""
    url = cfg.get("url", "")
    if not url:
        return {
            "agent_id": aid, "name": name, "status": "skipped",
            "error": "无可测试的 URL", "q_results": [],
            "description": desc, "category": category, "_test_type": "generic",
        }

    t_start = time.time()
    q_results = []
    try:
        browser.open(url, wait_sec=2)
        if cfg.get("needs_auth"):
            _handle_feishu_authorize(browser, url)

        body_before = browser.get_body_text()
        title_before = browser.get_title()
        current_url = browser.get_url()
        known_error = any(
            marker in body_before
            for marker in ("404", "500 Internal Server Error", "无法访问此网站", "应用不存在")
        )
        page_healthy = bool(current_url.startswith(("http://", "https://"))) and bool(
            body_before.strip() or title_before.strip()
        ) and not known_error

        interaction_done = "未发现安全的通用交互控件"
        for selector in (
            "button.el-button--primary",
            "button:not([disabled])",
            "input:not([type=hidden]):not([disabled])",
            "textarea:not([disabled])",
        ):
            try:
                if selector.startswith(("input", "textarea")):
                    browser.fill(selector, "测试输入", timeout=3)
                    interaction_done = f"在 {selector} 中输入测试文本"
                else:
                    browser.click(selector, timeout=3)
                    interaction_done = f"点击 {selector}"
                time.sleep(1)
                break
            except Exception:
                continue

        body_after = browser.get_body_text()
        responsive = page_healthy and bool(body_after.strip() or browser.get_title().strip())
        ss_path = _try_screenshot(browser, screenshot_dir, aid, "final")
        elapsed = round(time.time() - t_start, 1)

        q_results.append({
            "question": f"打开页面并执行通用交互：{interaction_done}",
            "response": (
                f"页面地址：{current_url}\n"
                f"页面响应：{(body_after or body_before)[:500]}"
            ),
            "success": responsive,
            "error": None if responsive else "页面未返回可验证内容或出现错误页",
            "elapsed": elapsed,
        })
        return {
            "agent_id": aid, "name": name,
            "status": "ok" if responsive else "chat_error",
            "error": None if responsive else "页面打开或响应验证失败",
            "q_results": q_results, "description": desc, "category": category,
            "screenshot": ss_path, "avg_elapsed": elapsed, "_test_type": "generic",
        }
    except Exception as exc:
        return {
            "agent_id": aid, "name": name, "status": "chat_error",
            "error": f"通用测试异常: {str(exc)[:200]}",
            "q_results": q_results, "description": desc, "category": category,
            "screenshot": "", "avg_elapsed": round(time.time() - t_start, 1),
            "_test_type": "generic",
        }

# ── Spark 应用授权辅助 ──

def _agent_screenshot_dir(aid: int, inspection_index: int | None = None) -> str:
    index = inspection_index or CURRENT_AGENT_CONTEXT.get("inspection_index")
    dirname = f"{int(index):03d}_{aid}" if index else str(aid)
    return str(SCREENSHOTS_DIR / dirname)


def _try_screenshot(browser, screenshot_dir: str, aid: int, label: str = "final") -> str:
    """截取最终页面并写入与 PNG 同名的证据元数据。"""
    os.makedirs(screenshot_dir, exist_ok=True)
    index = CURRENT_AGENT_CONTEXT.get("inspection_index")
    prefix = f"{int(index):03d}_{aid}" if index else f"{int(aid):03d}"
    ss_file = os.path.abspath(os.path.join(screenshot_dir, f"{prefix}_{label}.png"))
    for attempt in range(1, 4):
        try:
            browser.screenshot(ss_file)
            with open(ss_file, "rb") as fh:
                raw = fh.read()
            if len(raw) < 1000 or raw[:8] != b"\x89PNG\r\n\x1a\n":
                raise ValueError(f"PNG 无效或过小: {len(raw)} bytes")
            width, height = struct.unpack(">II", raw[16:24])
            if width < 200 or height < 150:
                raise ValueError(f"截图尺寸异常: {width}x{height}")

            try:
                current_url = browser.get_url()
            except Exception:
                current_url = ""
            try:
                current_title = browser.get_title()
            except Exception:
                current_title = ""
            try:
                body_text = browser.get_body_text()
            except Exception:
                body_text = ""

            metadata = {
                "run_id": RUN_ID,
                "inspection_index": index,
                "agent_id": aid,
                "agent_name": CURRENT_AGENT_CONTEXT.get("agent_name", ""),
                "captured_at": datetime.datetime.now(
                    datetime.timezone(datetime.timedelta(hours=8))
                ).isoformat(),
                "url": current_url,
                "title": current_title,
                "body_contains_agent_name": bool(
                    CURRENT_AGENT_CONTEXT.get("agent_name")
                    and CURRENT_AGENT_CONTEXT["agent_name"] in body_text
                ),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "width": width,
                "height": height,
                "bytes": len(raw),
            }
            _atomic_write_json(Path(ss_file).with_suffix(".json"), metadata)
            return ss_file
        except Exception as exc:
            print(f"      ⚠️ [{aid}] 截图第 {attempt}/3 次失败: {exc}")
            if attempt < 3:
                time.sleep(2 ** attempt)
    return ""


def _validate_png(path: str) -> tuple[bool, str]:
    if not path or not os.path.isfile(path):
        return False, "截图文件不存在"
    try:
        raw = Path(path).read_bytes()
        if len(raw) < 1000 or raw[:8] != b"\x89PNG\r\n\x1a\n":
            return False, "截图不是有效 PNG"
        width, height = struct.unpack(">II", raw[16:24])
        if width < 200 or height < 150:
            return False, f"截图尺寸异常 {width}x{height}"
        return True, ""
    except Exception as exc:
        return False, f"截图读取失败: {exc}"


def _bind_result(result: dict, agent: dict, inspection_index: int) -> dict:
    """将测试结果、市场序号与唯一证据绑定为一个不可拆分事务。"""
    result["agent_id"] = agent.get("id")
    result["name"] = agent.get("name", "未知")
    result["inspection_index"] = inspection_index
    result["run_id"] = RUN_ID
    result.setdefault("_test_type", "chat" if _is_chat_agent(agent) else "generic")

    q_results = result.get("q_results") or []
    if q_results:
        operations = [item.get("question", "") for item in q_results if item.get("question")]
        result["test_operation"] = "；".join(operations)
        # 提取结构化字段
        result["test_question"] = operations[0] if operations else ""
        responses = [item.get("response", "") for item in q_results if item.get("response")]
        result["agent_answer"] = responses[0] if responses else ""
    else:
        result["test_operation"] = result.get("error") or "打开目标页面并检查可用性"

    if result.get("status") == "ok":
        analysis = result.get("test_analysis") or "页面可访问，已完成预定操作并得到有效响应。"
    elif result.get("status") == "skipped":
        analysis = f"未完成业务操作：{result.get('error', '缺少可执行条件')}。"
    elif result.get("status") == "blocked":
        analysis = f"操作被阻塞：{result.get('error', '外部依赖未就绪')}。"
    else:
        analysis = f"测试异常：{result.get('error', '未知异常')}。"
    evaluation = result.get("evaluation") or {}
    issues = evaluation.get("issues") or []
    if issues:
        analysis += " 回复分析：" + "；".join(str(item) for item in issues)
    result["test_analysis"] = analysis

    valid, reason = _validate_png(result.get("screenshot", ""))
    metadata_path = Path(result.get("screenshot", "")).with_suffix(".json") if valid else None
    if not valid or not metadata_path or not metadata_path.is_file():
        result["evidence_error"] = reason or "截图元数据缺失"
    else:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["inspection_index"] = inspection_index
        metadata["agent_id"] = agent.get("id")
        metadata["agent_name"] = agent.get("name", "未知")
        _atomic_write_json(metadata_path, metadata)
        result["evidence_metadata"] = str(metadata_path)
    return result


async def _run_unified_inspection(agents: list, token: str) -> list:
    """剧本优先 + LLM 降级的统一巡检流程。

    每个智能体：
    1. 命中缓存剧本 → 确定性回放
    2. 无缓存 / 缓存失败 → 页面探测 → LLM 生成受限计划 → 执行 → 缓存成功剧本
    """
    from utils.playbook import PlaybookCache
    from utils.executor import PlaybookExecutor
    from utils.planner import plan_operations, generate_fallback_plan
    from agent_browser_wrapper import AgentBrowser, AgentBrowserError

    global CURRENT_AGENT_CONTEXT, RUN_VALIDATION

    cache = PlaybookCache()
    stats = cache.stats()
    print(f"  📚 剧本缓存: {stats['cached_playbooks']} 个剧本, "
          f"{stats['marked_skip']} 个标记跳过, "
          f"{stats['total_entries']} 个总记录")

    results = []
    _save_checkpoint(agents, results)

    llm_planned_count = 0
    cache_hit_count = 0

    browser = None
    try:
        browser = AgentBrowser(
            **_agent_browser_auth_kwargs(),
            session=f"unified-{RUN_ID}-{int(time.time() * 1000)}",
        )
        executor = PlaybookExecutor(browser)

        # ── 飞行前检查：飞书登录态 + 授权 ──
        print(f"\n  🔐 飞行前检查：飞书登录态...")
        preflight_url = "https://agent.digitalchina.com/widget/open?agentId=126"
        try:
            browser.open(preflight_url,
                         wait_sec=3, wait_selector="button, a, [contenteditable], textarea", wait_timeout=8)
            body_pre = browser.get_body_text()
            url_pre = browser.get_url()
            if "accounts.feishu.cn" in url_pre or "Log In With QR Code" in body_pre:
                print(f"  ⚠️ 飞书登录态已过期，尝试自动登录...")
                _feishu_auto_login(browser)
                time.sleep(3)
            # 登录后也可能遇到授权页
            _handle_feishu_authorize(browser, preflight_url)
            print(f"  ✅ 飞书登录态准备就绪")
        except Exception as e:
            print(f"  ⚠️ 飞书登录预检异常（将继续尝试）: {e}")

        for inspection_index, agent in enumerate(agents, 1):
            aid = agent["id"]
            name = agent.get("name", "未知")
            desc = agent.get("description", "")
            category = agent.get("categoryLabel", "")

            CURRENT_AGENT_CONTEXT = {
                "inspection_index": inspection_index,
                "agent_id": aid,
                "agent_name": name,
            }

            print(f"\n  [{inspection_index:03d}/{len(agents):03d}] {name} (ID: {aid})")
            screenshot_dir = _agent_screenshot_dir(aid)
            os.makedirs(screenshot_dir, exist_ok=True)

            playbook = None
            used_llm = False

            # ── 第一层：尝试缓存剧本 ──
            if cache.should_use_cache(aid):
                playbook = cache.get(aid)
                if playbook and playbook.get("strategy") == "skip":
                    reasoning = playbook.get("reasoning", "")
                    # 如果是飞书授权页，先尝试自动授权
                    if "飞书授权" in reasoning or "授权登录" in reasoning or "授权请求" in reasoning:
                        print(f"    🔐 缓存标记为授权跳过，尝试自动授权...")
                        url = agent.get("url") or agent.get("openUrl", "")
                        if not url:
                            url = f"https://agent.digitalchina.com/widget/open?agentId={aid}"
                        try:
                            browser.open(url, wait_sec=3, wait_selector="button, a", wait_timeout=8)
                            authorized = _handle_feishu_authorize(browser, url)
                            if authorized:
                                time.sleep(2)
                                body = browser.get_body_text()
                                cur_url = browser.get_url()
                                still_auth = ("Authorizing indicates" in body or "请求获得以下权限" in body
                                              or "accounts.feishu.cn" in cur_url)
                                if not still_auth:
                                    print(f"    ✅ 授权成功，清空 skip 剧本走 LLM")
                                    playbook = None
                                else:
                                    print(f"    ⚠️ 授权未生效，仍见授权页")
                            else:
                                print(f"    ⚠️ 授权失败（2 次尝试后仍停留）")
                        except Exception as e:
                            print(f"    ⚠️ 授权尝试异常: {e}")
                    # 仅当 playbook 未被清空时才执行跳过
                    if playbook and playbook.get("strategy") == "skip":
                        print(f"    ⏭️ 缓存标记为 skip: {reasoning[:60]}")
                        result = {
                            "agent_id": aid, "name": name, "status": "skipped",
                            "error": reasoning or "剧本标记跳过",
                            "description": desc, "category": category,
                            "q_results": [],
                            "screenshot": _try_screenshot(browser, screenshot_dir, aid, "skip"),
                        }
                        result = _bind_result(result, agent, inspection_index)
                        results.append(result)
                        _save_checkpoint(agents, results)
                        CURRENT_AGENT_CONTEXT = {}
                        continue

                if playbook:
                    source = cache._data.get(str(aid), {})
                    sc = source.get("success_count", 0)
                    print(f"    📋 命中缓存剧本 (v{playbook.get('version',1)}, 成功{sc}次)")

                    result = executor.execute(playbook, screenshot_dir, aid)

                    if result["status"] == "ok":
                        cache.set(aid, playbook)
                        cache_hit_count += 1
                        result = _bind_result(result, agent, inspection_index)
                        results.append(result)
                        _save_checkpoint(agents, results)
                        CURRENT_AGENT_CONTEXT = {}
                        continue
                    else:
                        cache.mark_failed(aid, result.get("error", ""))
                        print(f"    ⚠️ 缓存剧本回放失败: {result.get('error', '')[:100]}")
                        playbook = None  # 触发 LLM 降级

            # ── 第二层：LLM 智能规划 ──
            print(f"    🧠 LLM 规划中...")
            used_llm = True
            llm_planned_count += 1
            plan = None

            try:
                # 打开页面 + 采集信息
                url = agent.get("url") or agent.get("openUrl", "")
                if not url:
                    url = f"https://agent.digitalchina.com/widget/open?agentId={aid}"

                browser.open(
                    url, wait_sec=5,
                    wait_selector="[contenteditable], textarea, input, button, a",
                    wait_timeout=10,
                )

                body_text = browser.get_body_text()
                current_url = browser.get_url()

                # ── Step A: 先尝试飞书授权（授权页不是登录页，优先级最高）──
                auth_done = _handle_feishu_authorize(browser, url)
                if not auth_done:
                    # 重新检查：授权失败？还是本来就无需授权？
                    body_text = browser.get_body_text()
                    current_url = browser.get_url()
                    still_auth = any(w in body_text for w in [
                        "Authorizing indicates", "请求获得以下权限", "Permissions that can be granted"
                    ]) or ("Authorize" in body_text and "Reject" in body_text)
                    if still_auth:
                        print(f"      ❌ 飞书授权失败，标记 blocked")
                        result = {
                            "agent_id": aid, "name": name, "status": "blocked",
                            "error": "飞书授权按钮点击后未完成跳转",
                            "description": desc, "category": category,
                            "q_results": [],
                            "screenshot": _try_screenshot(browser, screenshot_dir, aid, "auth-blocked"),
                        }
                        result = _bind_result(result, agent, inspection_index)
                        results.append(result)
                        _save_checkpoint(agents, results)
                        CURRENT_AGENT_CONTEXT = {}
                        continue

                # ── Step B: 飞书登录态过期 — 尝试自动登录 ──
                body_text = browser.get_body_text()
                current_url = browser.get_url()
                needs_login = ("Log In With QR Code" in body_text or "Scan the QR code" in body_text
                               or "accounts.feishu.cn" in current_url)
                if needs_login:
                    print(f"      🔐 检测到飞书登录页面，尝试自动登录...")
                    try:
                        _feishu_auto_login(browser)
                        # 登录成功后重导航
                        auth_done = _handle_feishu_authorize(browser, url)
                        if not auth_done:
                            body_text = browser.get_body_text()
                            current_url = browser.get_url()
                            still_auth = any(w in body_text for w in [
                                "Authorizing indicates", "请求获得以下权限"
                            ])
                            if still_auth:
                                raise RuntimeError("飞书自动登录后授权未完成")
                        # 重导航确保到达智能体
                        browser.open(url, wait_sec=5,
                                     wait_selector="[contenteditable], textarea, input, button, a",
                                     wait_timeout=10)
                        body_text = browser.get_body_text()
                        current_url = browser.get_url()
                        if "accounts.feishu.cn" in current_url or "Log In With QR Code" in body_text:
                            raise RuntimeError("飞书自动登录后仍停留在登录页")
                        print(f"      ✅ 飞书登录成功，继续巡检")
                    except Exception as e:
                        err_msg = str(e)[:200]
                        print(f"      ⚠️ 飞书自动登录失败: {err_msg}")
                        screenshot = _try_screenshot(browser, screenshot_dir, aid, "login-expired")
                        result = {
                            "agent_id": aid, "name": name, "status": "unreachable" if "人工" in err_msg else "blocked",
                            "error": f"飞书登录失败: {err_msg}",
                            "description": desc, "category": category,
                            "q_results": [],
                            "screenshot": screenshot,
                        }
                        result = _bind_result(result, agent, inspection_index)
                        results.append(result)
                        _save_checkpoint(agents, results)
                        CURRENT_AGENT_CONTEXT = {}
                        continue

                # 预检：无权限
                if "No permission to use" in body_text or "应用不存在" in body_text:
                    cache.mark_skip(aid, "无权限访问")
                    result = {
                        "agent_id": aid, "name": name, "status": "unreachable",
                        "error": "无权限访问此智能体",
                        "description": desc, "category": category,
                        "q_results": [],
                        "screenshot": _try_screenshot(browser, screenshot_dir, aid, "no-permission"),
                    }
                    result = _bind_result(result, agent, inspection_index)
                    results.append(result)
                    _save_checkpoint(agents, results)
                    CURRENT_AGENT_CONTEXT = {}
                    continue

                # 采集探测数据
                probe_screenshot = _try_screenshot(browser, screenshot_dir, aid, "probe")
                try:
                    snapshot = str(browser.snapshot())
                except Exception:
                    snapshot = body_text[:4000]

                # 调用 LLM 规划
                try:
                    plan = await plan_operations(
                        agent=agent,
                        page_body_text=body_text,
                        page_screenshot_path=probe_screenshot,
                        page_snapshot_text=snapshot,
                        error_context=playbook.get("_meta", {}).get("last_error", "") if playbook else "",
                    )
                    print(f"    ✅ LLM 规划: strategy={plan.get('strategy')}, "
                          f"{plan.get('reasoning', '')[:60]}")
                except Exception as e:
                    print(f"    ⚠️ LLM 规划失败: {e}，使用回退剧本")
                    plan = generate_fallback_plan(agent, str(e)[:200])

                # 执行计划
                result = executor.execute(plan, screenshot_dir, aid)

                # 缓存成功剧本
                if result["status"] == "ok":
                    cache.set(aid, plan)
                    print(f"    💾 剧本已缓存（共 {cache.stats()['cached_playbooks']} 个）")
                elif result["status"] == "skipped":
                    if plan.get("strategy") == "skip":
                        cache.mark_skip(aid, plan.get("reasoning", ""))
                else:
                    cache.mark_failed(aid, result.get("error", ""))

            except AgentBrowserError as e:
                result = {
                    "agent_id": aid, "name": name, "status": "chat_error",
                    "error": f"agent-browser: {str(e)[:200]}",
                    "description": desc, "category": category,
                    "q_results": [],
                    "screenshot": _try_screenshot(browser, screenshot_dir, aid, "error"),
                }
            except Exception as e:
                result = {
                    "agent_id": aid, "name": name, "status": "chat_error",
                    "error": f"巡检异常: {str(e)[:200]}",
                    "description": desc, "category": category,
                    "q_results": [],
                    "screenshot": _try_screenshot(browser, screenshot_dir, aid, "error"),
                }

            # ── 最终截图前授权/登录态复检 ──
            if result["status"] == "ok":
                body_final = browser.get_body_text()
                url_final = browser.get_url()
                # 检测 aPaaS 授权页特征
                is_auth_final = any(w in body_final for w in [
                    "Requests permissions", "Authorizing indicates",
                    "请求获得以下权限", "Use another account",
                ]) or ("Authorize" in body_final and ("Reject" in body_final or "Use another account" in body_final))
                is_login_final = "账号登录" in body_final or "Log In With QR Code" in body_final

                if is_auth_final:
                    print(f"      ❌ 最终页面仍为授权页 → 降级 BLOCKED")
                    result["status"] = "blocked"
                    result["error"] = "自动授权失败，未进入智能体业务页面"
                    if result.get("screenshot"):
                        try:
                            os.remove(result["screenshot"])
                        except Exception:
                            pass
                    result["screenshot"] = _try_screenshot(browser, screenshot_dir, aid, "blocked-auth")
                elif is_login_final:
                    print(f"      ❌ 最终页面仍为登录页 → 降级 BLOCKED")
                    result["status"] = "blocked"
                    result["error"] = "登录未完成，未进入智能体业务页面"
                    if result.get("screenshot"):
                        try:
                            os.remove(result["screenshot"])
                        except Exception:
                            pass
                    result["screenshot"] = _try_screenshot(browser, screenshot_dir, aid, "blocked-login")

            result["_llm_planned"] = used_llm
            result = _bind_result(result, agent, inspection_index)
            results.append(result)

            # 保存执行日志
            log_data = {
                "run_id": RUN_ID,
                "agent_id": aid,
                "name": name,
                "used_llm": used_llm,
                "plan": plan if used_llm else (playbook or {}),
                "execution_log": result.get("log", []),
                "status": result.get("status"),
                "error": result.get("error"),
            }
            cache.save_log(aid, RUN_ID, log_data)

            _save_checkpoint(agents, results)
            CURRENT_AGENT_CONTEXT = {}
            time.sleep(1)

        # 最终统计
        final_stats = cache.stats()
        print(f"\n  📊 巡检结束: {cache_hit_count} 个缓存命中, "
              f"{llm_planned_count} 个 LLM 规划, "
              f"{final_stats['cached_playbooks']} 个剧本已缓存")
        RUN_VALIDATION = _validate_run_results(agents, results)

    finally:
        if browser:
            try:
                browser.close()
            except Exception:
                pass

    return results


def _validate_run_results(agents: list, results: list) -> dict:
    """最终硬门禁：数量、实际顺序、唯一 ID、PNG 和元数据必须全部一致。"""
    errors = []
    expected_ids = [str(agent.get("id")) for agent in agents]
    actual_ids = [str(result.get("agent_id")) for result in results]
    indexes = [result.get("inspection_index") for result in results]

    if len(results) != len(agents):
        errors.append(f"完成数量 {len(results)}，预期 {len(agents)}")
    if actual_ids != expected_ids:
        errors.append("结果顺序与市场顺序不一致")
    if len(set(actual_ids)) != len(actual_ids):
        errors.append("结果中存在重复智能体 ID")
    if indexes != list(range(1, len(results) + 1)):
        errors.append("巡检序号不连续")

    hashes = {}
    for result in results:
        index = result.get("inspection_index")
        aid = result.get("agent_id")
        valid, reason = _validate_png(result.get("screenshot", ""))
        if not valid:
            errors.append(f"第 {index} 项 ID={aid}: {reason}")
            continue
        metadata_path = Path(result["screenshot"]).with_suffix(".json")
        if not metadata_path.is_file():
            errors.append(f"第 {index} 项 ID={aid}: 截图元数据缺失")
            continue
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata.get("inspection_index") != index or str(metadata.get("agent_id")) != str(aid):
            errors.append(f"第 {index} 项 ID={aid}: 截图元数据绑定错误")
        digest = metadata.get("sha256")
        if digest:
            hashes.setdefault(digest, []).append(index)

    duplicate_groups = [items for items in hashes.values() if len(items) > 1]
    if duplicate_groups:
        errors.append(f"发现疑似重复截图序号: {duplicate_groups}")

    return {
        "run_id": RUN_ID,
        "complete": not errors,
        "expected_count": len(agents),
        "completed_count": len(results),
        "screenshot_count": sum(1 for result in results if _validate_png(result.get("screenshot", ""))[0]),
        "errors": errors,
    }

def _order_by_market(results, agents):
    """使用市场 API 原始序号排序，并写入不可变 inspection_index。"""
    order = {str(agent.get("id")): index for index, agent in enumerate(agents, 1)}
    for result in results:
        result["inspection_index"] = order.get(str(result.get("agent_id")), 10**9)
    return sorted(results, key=lambda item: item["inspection_index"])

def _handle_feishu_authorize(browser, target_url: str) -> bool:
    """处理飞书/Spark 应用授权页，自动点击 Authorize/授权 按钮。

    检测特征（任一命中即判定为授权页）：
      - "Requests permissions from the following Feishu account"
      - "Permissions that can be granted"
      - "Authorizing indicates"
      - "请求获得以下权限"
      - "授权后"
      - 页面同时包含 Authorize 和 Reject
      - 页面同时包含 "授权" 和 "拒绝"
      - URL 包含 accounts.feishu.cn

    只点击：Authorize / 授权 / 确认授权 / 允许
    绝不点击：Reject / 拒绝 / Use another account / 使用其他账号

    流程：
      点击授权 → 等待 URL 变化(最多 15s) →
      若未跳转则重新 browser.open(target_url) →
      验证：URL 离开 accounts.feishu.cn 且页面无授权提示

    最多尝试 2 次，仍失败返回 False（调用方截图并标记 blocked）。
    """
    # ── 授权页特征 ──
    AUTH_PATTERNS_EN = [
        "Requests permissions from the following Feishu account",
        "Permissions that can be granted",
        "Authorizing indicates",
    ]
    AUTH_PATTERNS_CN = [
        "请求获得以下权限",
        "授权后",
    ]
    AUTHORIZE_BUTTONS = ["Authorize", "授权", "确认授权", "允许"]
    FORBIDDEN_BUTTONS = ["Reject", "拒绝", "Use another account", "使用其他账号"]

    def _is_auth_page(body: str, url: str) -> bool:
        """判定当前页面是否为授权页（含 aPaaS 授权页）"""
        if "accounts.feishu.cn" in url:
            return True
        for pat in AUTH_PATTERNS_EN + AUTH_PATTERNS_CN:
            if pat in body:
                return True
        # aPaaS 授权页特征：有 Authorize + Use another account
        if "Authorize" in body and "Use another account" in body:
            return True
        # 同时存在 Authorize/授权 和 Reject/拒绝
        has_auth = any(w in body for w in AUTHORIZE_BUTTONS)
        has_reject = any(w in body for w in FORBIDDEN_BUTTONS)
        if has_auth and has_reject:
            return True
        return False

    def _click_authorize() -> bool:
        """在授权页点击 Authorize/授权 按钮，返回是否成功点击"""
        import re
        # 方法1: snapshot + role=button 精确查找 ref
        try:
            snap = browser.snapshot()
            snap_text = snap.get('text', '') if isinstance(snap, dict) else str(snap)
            for line in snap_text.split('\n'):
                for btn_text in AUTHORIZE_BUTTONS:
                    if btn_text in line and 'button' in line.lower():
                        m = re.search(r'ref=(e\d+)', line)
                        if m:
                            browser.click(m.group(1))
                            print(f"      ✅ snapshot ref 点击 '{btn_text}'")
                            return True
        except Exception as e:
            print(f"      ⚠️ snapshot 方法异常: {e}")
        # 方法2: find_and_click
        for btn_text in AUTHORIZE_BUTTONS:
            try:
                browser.find_and_click(btn_text)
                print(f"      ✅ find_and_click '{btn_text}'")
                return True
            except Exception:
                continue
        # 方法3: eval 找第一个非 Reject/拒绝 的 button
        try:
            js = """
            (() => {
                const btns = document.querySelectorAll('button');
                const targets = ['authorize', '授权', '确认授权', '允许'];
                const forbidden = ['reject', '拒绝', 'use another account', '使用其他账号'];
                for (const b of btns) {
                    const t = (b.textContent || '').trim().toLowerCase();
                    if (targets.some(x => t.includes(x)) && !forbidden.some(f => t.includes(f))) {
                        b.click(); return b.textContent.trim();
                    }
                }
                return null;
            })();
            """
            result = browser.eval(js)
            if result:
                print(f"      ✅ eval 点击了 '{result}'")
                return True
        except Exception:
            pass
        return False

    def _verify_success(target: str) -> bool:
        """验证已离开授权页且到达智能体页面"""
        body = browser.get_body_text()
        url = browser.get_url()
        if _is_auth_page(body, url):
            return False
        # 确认不在 accounts.feishu.cn
        if "accounts.feishu.cn" in url:
            return False
        # 确认页面有实际内容
        if len(body) < 50:
            return False
        return True

    # ── 主流程 ──
    for attempt in range(1, 3):
        body = browser.get_body_text()
        url = browser.get_url()

        if not _is_auth_page(body, url):
            # 不是授权页，无需处理
            return True

        print(f"      🔐 检测到授权页 (第{attempt}次尝试)...")
        url_before = url

        if not _click_authorize():
            print(f"      ⚠️ 未找到授权按钮")
            return False

        # 等待 URL 变化（最多 30s）
        for _ in range(30):
            time.sleep(1)
            try:
                new_url = browser.get_url()
                if new_url != url_before:
                    print(f"      🔄 URL 已变化: {new_url[:80]}")
                    break
            except Exception:
                pass

        # 验证
        if _verify_success(target_url):
            print(f"      ✅ 授权成功，已进入智能体页面")
            return True

        # 未成功：重新导航到目标页面
        if attempt < 2:
            print(f"      🔄 授权未生效，重新打开目标页面...")
            try:
                browser.open(target_url, wait_sec=5,
                             wait_selector="[contenteditable], textarea, input, button, a",
                             wait_timeout=10)
            except Exception:
                time.sleep(3)

    print(f"      ❌ 授权尝试 2 次后仍停留在授权页")
    return False


def _spark_authorize(browser) -> bool:
    """[废弃] 旧版授权处理，保留兼容，实际应调用 _handle_feishu_authorize"""
    return _handle_feishu_authorize(browser, "")


def _feishu_auto_login(browser) -> bool:
    """自动登录 accounts.feishu.cn，使用 credentials.json 中的手机号+密码。
    返回 True 表示登录成功或无需登录（已有有效 session）。
    遇到短信验证码/扫码/图形验证码/设备确认时抛出异常通知人工。"""
    current_url = browser.get_url() if hasattr(browser, 'get_url') else ""
    body = browser.get_body_text() if hasattr(browser, 'get_body_text') else ""

    # 不在登录页则直接返回
    if "accounts.feishu.cn" not in current_url and "登录" not in body and "Login" not in body:
        return True

    creds = _load_credentials().get("feishu", {})
    phone = creds.get("phone", "")
    password = creds.get("password", "")
    if not phone or not password:
        raise RuntimeError("飞书登录态已过期，且无 .auth/credentials.json 中的飞书凭据")

    # 检测是否有验证码等人工环节
    danger_signals = ["短信验证码", "图形验证码", "扫码", "设备确认", "二次验证",
                      "SMS", "OTP", "Captcha", "captcha", "QR code", "scan"]
    for sig in danger_signals:
        if sig.lower() in body.lower():
            raise RuntimeError(
                f"飞书登录需要人工处理（检测到'{sig}'），"
                f"请手动完成验证后重试。截图已保存。"
            )

    # 选择手机号登录方式
    for label in ["手机号登录", "手机号", "Phone", "Phone Login", "密码登录"]:
        try:
            browser.find_and_click(label)
            time.sleep(2)
            break
        except Exception:
            continue

    # 输入手机号
    try:
        if hasattr(browser, 'fill'):
            browser.fill("input[type=tel], input[placeholder*=手机], input[placeholder*=phone]", phone)
        else:
            browser.find_and_click("input[type=tel]")
            time.sleep(0.5)
            browser.type(phone)
        time.sleep(1)
    except Exception as e:
        print(f"      ⚠️ 飞书手机号输入失败: {e}")

    # 输入密码
    try:
        if hasattr(browser, 'fill'):
            browser.fill("input[type=password]", password)
        else:
            pw_inputs = browser.find_all("input[type=password]")
            if pw_inputs:
                pw_inputs[0].fill(password)
        time.sleep(1)
    except Exception as e:
        print(f"      ⚠️ 飞书密码输入失败: {e}")

    # 点击登录
    for btn in ["登录", "Log In", "Login", "Sign In", "Continue"]:
        try:
            browser.find_and_click(btn)
            time.sleep(5)
            break
        except Exception:
            continue

    # 登录后再次检测危险信号
    time.sleep(2)
    body_after = browser.get_body_text() if hasattr(browser, 'get_body_text') else ""
    for sig in danger_signals:
        if sig.lower() in body_after.lower():
            raise RuntimeError(f"飞书登录后仍需人工验证（'{sig}'），请手动完成。")

    # 检测是否登录成功（不再在登录页）
    current_url_after = browser.get_url() if hasattr(browser, 'get_url') else ""
    if "accounts.feishu.cn" in current_url_after and ("登录" in body_after or "Login" in body_after):
        raise RuntimeError(
            "飞书自动登录失败，仍在登录页。请检查 credentials.json 中"
            "的账号密码是否正确，或手动登录后重试。"
        )

    print("      ✅ 飞书自动登录成功")
    return True


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

    # 飞书授权
    if not _handle_feishu_authorize(browser, url):
        body = browser.get_body_text()
        cur_url = browser.get_url()
        still_auth = any(w in body for w in ["Authorizing indicates", "请求获得以下权限"]) or "accounts.feishu.cn" in cur_url
        if still_auth:
            raise Exception("飞书授权按钮点击后未完成跳转")

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
            _handle_feishu_authorize(browser, url)
            browser.upload("input[type=file]:first-of-type", files[1])
            time.sleep(2)
            browser.open(url, wait_sec=3)
            _handle_feishu_authorize(browser, url)
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

    browser.open(url, wait_selector="[contenteditable], textarea, input[type='text']", wait_timeout=15)

    questions = [f"你好，请简单介绍一下你自己能做什么"]
    q_results = []
    total_elapsed = 0

    for qi, q in enumerate(questions):
        body_before = browser.get_body_text()

        t_start = time.time()
        browser.chat_send(q)
        reply_body = browser.chat_wait(timeout=TIMEOUT_SECONDS, body_before=body_before, question=q)
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

    # 每个智能体测试完后截取唯一最终状态证据
    agent_screenshot = _try_screenshot(browser, screenshot_dir, aid, "final")

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

    # 飞书授权
    if cfg.get("needs_auth"):
        _handle_feishu_authorize(browser, url)

    q_results = []
    t_start = time.time()
    body_before_action = browser.get_body_text()

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
        failed_steps = [item for item in step_results if item.startswith("❌")]
        meaningful_steps = [
            item for item in step_results
            if not item.startswith(("等待:", "滚动:", "❌"))
        ]
        success = (
            not failed_steps
            and bool(meaningful_steps)
            and body_text.strip() != body_before_action.strip()
        )
        body = "\n".join(step_results) + "\n最终页面响应：" + body_text[:300]

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
        body = browser.get_body_text()
        success = bool(body.strip()) and body.strip() != body_before_action.strip()

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
        success = body.strip() != body_before_action.strip() and any(word in body for word in ("风险", "违规", "审核结果"))

    elif action == "spark_nav":
        nav_links = cfg.get("nav_links", [])
        visited = 0
        previous_fingerprint = hashlib.sha256(
            body_before_action.encode("utf-8", errors="ignore")
        ).hexdigest()
        for link_text in nav_links:
            try:
                browser.find_and_click(link_text)
                time.sleep(2)
                current_body = browser.get_body_text()
                fingerprint = hashlib.sha256(
                    current_body.encode("utf-8", errors="ignore")
                ).hexdigest()
                if current_body.strip() and fingerprint != previous_fingerprint:
                    visited += 1
                    previous_fingerprint = fingerprint
            except Exception:
                pass
        success = bool(nav_links) and visited == len(nav_links)
        body = f"导航检查：{visited}/{len(nav_links)} 个页面产生独立响应"

    elif action == "spark_check":
        # 简单功能检查：多次尝试授权
        for attempt in range(2):
            time.sleep(2)
            body = browser.get_body_text()
            if len(body) > 200 and "Authorize" not in body:
                break
            _handle_feishu_authorize(browser, url)
            time.sleep(3)
        body = browser.get_body_text()
        success = len(body) > 200 and "Authorize" not in body

    else:
        body = browser.get_body_text()
        success = len(body) > 200

    elapsed = round(time.time() - t_start, 1)

    # ← 最终截图：所有交互完成后
    ss_path = _try_screenshot(browser, screenshot_dir, aid, "final")

    response_text = body[:500] if isinstance(body, str) else str(body)[:500]

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
    lines.append(f"**总结**：{total} 个智能体，{total - no_guide}/{total} 有指南，{total - no_reviews}/{total} 有评价")

    # 标注 Dify 内嵌智能体（通过 API 测试，非浏览器）
    dify_agents = [a for a in agents_list if a.get("openType") == "api" and a.get("source") == "dify"]
    if dify_agents:
        names = "、".join(f"[{a['id']}] {a.get('name','?')}" for a in dify_agents)
        lines.append(f"\n> 📡 {names} 为市场内嵌 Dify 应用，通过接口直接测试")

    return "\n".join(lines)


def generate_full_report(api_report_content, chat_results, now, chat_batch_info):
    """生成完整报告：API 简要 + 对话测试详情（用户指定格式）"""
    lines = []

    # 标题与证据完整性结论
    lines.append(f"# {now.strftime('%Y年%m月%d日 %H:%M')} 智能体市场健康巡检报告")
    lines.append("")
    lines.append(f"- 运行编号：{RUN_ID}")
    lines.append(f"- 巡检状态：{'完整' if RUN_VALIDATION.get('complete') else '证据不完整'}")
    lines.append(f"- 已巡检：{RUN_VALIDATION.get('completed_count', len(chat_results))} 个")
    lines.append(f"- 有效截图：{RUN_VALIDATION.get('screenshot_count', 0)} 张")
    if RUN_VALIDATION.get("errors"):
        lines.append("- 完整性问题：")
        for error in RUN_VALIDATION["errors"]:
            lines.append(f"  - {error}")
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

    # 全局只按市场原始序号渲染，禁止按类型拆组后破坏 1..41 顺序。
    _append_ordered_results(lines, "🔎 全量巡检详情", chat_results)
    return "\n".join(lines)


def _append_ordered_results(lines, title, results):
    results = sorted(results, key=lambda r: r.get("inspection_index", 10**9))
    _render_results_header(lines, title, results)
    for r in results:
        _render_agent_header(lines, r)
        lines.append(f"巡检序号：{r.get('inspection_index', '?')}")
        lines.append("")
        lines.append(f"测试操作：{r.get('test_operation', '打开页面并检查可用性')}")
        lines.append("")
        lines.append(f"测试分析：{r.get('test_analysis', '未生成分析')}")
        lines.append("")
        if r.get("status") == "skipped":
            lines.append(f"⏭ 原因: {r.get('error', '')}")
            lines.append("")
        q_results = r.get("q_results", [])
        if not q_results:
            lines.append("> 无测试数据")
            lines.append("")
        elif r.get("_test_type") in (None, "chat", "dify-api"):
            for qi, qr in enumerate(q_results, 1):
                lines.extend([
                    f"测试问题{qi}：", "", "```", qr.get("question", "?"), "```", "",
                    f"回答结果{qi}：", "", "```",
                    (qr.get("response", "") or "（无有效回复）")[:800], "```", ""
                ])
        else:
            lines.append("智能体检测效果分析：")
            lines.append("")
            for qi, qr in enumerate(q_results, 1):
                lines.append(f"{qi}. 操作: {qr.get('question', '?')}")
                lines.append(f"   - 结果: {(qr.get('response', '') or '（无有效结果）')[:300]}")
                lines.append("")
        _render_agent_footer(lines, r)


def _append_chat_section(lines, title, results):
    """渲染对话型智能体结果：问题 + 回答 + 截图 + 用时"""
    results = sorted(results, key=lambda r: r.get("inspection_index", 10**9))
    _render_results_header(lines, title, results)

    for r in results:
        _render_agent_header(lines, r)
        lines.append(f"巡检序号：{r.get('inspection_index', '?')}")
        lines.append("")
        lines.append(f"测试操作：{r.get('test_operation', '打开页面并检查可用性')}")
        lines.append("")
        lines.append(f"测试分析：{r.get('test_analysis', '未生成分析')}")
        lines.append("")
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
    results = sorted(results, key=lambda r: r.get("inspection_index", 10**9))
    _render_results_header(lines, title, results)

    for r in results:
        _render_agent_header(lines, r)
        lines.append(f"巡检序号：{r.get('inspection_index', '?')}")
        lines.append("")
        lines.append(f"测试操作：{r.get('test_operation', '打开页面并检查可用性')}")
        lines.append("")
        lines.append(f"测试分析：{r.get('test_analysis', '未生成分析')}")
        lines.append("")
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

    lines.append(f"### {icon} {type_badge}{name} （编号：{aid}）")
    lines.append("")

    if status in ("chat_error", "chat_failed", "unreachable"):
        lines.append(f"⚠️ {stext}：{r.get('error', '未知')}")
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
        lines.append(f"![{r.get('inspection_index', '?')}-{r.get('name', '智能体')}最终状态]({url})")
        lines.append("")
    else:
        lines.append("截图：❌ 最终状态证据缺失")
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
      "doc_title": "2026年07月09日 11:03 智能体市场健康巡检报告",
      "owner_open_id": "ou_12f4e5dbfd82f5975eaa6afd762b1d20",
      "summary_text": "总结...",
      "sections": [
        {"id": "s1", "text": "## 🔎 全量巡检详情\n...", "images": []},
        {"id": "a119", "text": "### ✅ 业务签约...\n...", "images": ["/abs/path/1.png", ...]},
        ...
      ]
    }
    """
    manifest = {
        "doc_title": f"{now.strftime('%Y年%m月%d日 %H:%M')} 智能体市场健康巡检报告",
        "owner_open_id": "ou_12f4e5dbfd82f5975eaa6afd762b1d20",
        "summary_text": "",
        "sections": [],
        "report_path": str(report_path),
        "generated_at": now.isoformat(),
        "run_id": RUN_ID,
        "run_state": "完整" if RUN_VALIDATION.get("complete") else "证据不完整",
        "validation": RUN_VALIDATION,
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
        header = f"---\n\n## 🔎 全量巡检详情\n\n{' | '.join(parts)}\n"
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

        for r in sorted(chat_results, key=lambda item: item.get("inspection_index", 10**9)):
            name = r.get("name", "?")
            aid = r.get("agent_id", "?")
            status = r.get("status", "?")
            icon = status_icon.get(status, "❓")
            stext = status_text.get(status, status)

            lines = []
            lines.append(f"### {icon} {name}（ID：{aid}）")
            lines.append("")
            lines.append(f"巡检序号：{r.get('inspection_index', '?')}")
            lines.append("")
            lines.append(f"测试操作：{r.get('test_operation', '打开页面并检查可用性')}")
            lines.append("")
            lines.append(f"测试分析：{r.get('test_analysis', '未生成分析')}")
            lines.append("")

            if status in ("chat_error", "chat_failed", "unreachable"):
                lines.append(f"⚠️ {stext}：{r.get('error', '未知')}")
                lines.append("")
            elif status == "skipped":
                lines.append(f"⏭ 跳过原因: {r.get('error', '未知')}")
                lines.append("")

            q_results = r.get("q_results", []) if status != "skipped" else []
            screenshot = r.get("screenshot", "")
            agent_images = []
            if _validate_png(screenshot)[0]:
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
                "inspection_index": r.get("inspection_index"),
                "agent_id": aid,
                "agent_name": name,
                "status": status,
                "text": "\n".join(lines),
                "images": agent_images[:1],
            })

    # 写入 MANIFEST.json
    manifest_path = RUN_DIR / "MANIFEST.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"  📋 投递清单已生成: {manifest_path}")
    return str(manifest_path)


# ──────────────────────────────────────
# 主流程
# ──────────────────────────────────────

def _publish_feishu_report(manifest: dict) -> str:
    """根据 MANIFEST 创建飞书文档，返回 doc_url。失败返回空字符串。

    文字用飞书 Open API 写入（保证速度），截图由 cron agent 用 feishu_doc upload_image 插入。
    输出 MANIFEST_WITH_DOC 供 cron agent 定位每张截图应插入的 section。
    """
    try:
        from utils.feishu_api import create_document, write_markdown
    except ImportError:
        print("  ⚠️ feishu_api 模块不可用，跳过文档发布")
        return ""

    print("\n" + "=" * 50)
    print("📤 发布飞书文档")

    title = manifest.get("doc_title", "智能体市场巡检报告")
    sections = [s for s in manifest.get("sections", []) if s.get("id", "").startswith("agent_")]

    if not sections:
        print("  ⚠️ 无 agent section，跳过")
        return ""

    try:
        doc_token = create_document(title)
    except Exception as e:
        print(f"  ❌ 创建文档失败: {e}")
        return ""

    doc_url = f"https://feishu.cn/docx/{doc_token}"

    # 写摘要
    summary = manifest.get("summary_text", "")
    if summary:
        try:
            write_markdown(doc_token, f"## 📊 巡检摘要\n\n{summary}\n\n---")
        except Exception as e:
            print(f"  ⚠️ 写摘要失败: {e}")

    # 逐 section 写入文字，记录截图路径供 cron agent 后续插入
    ok = 0
    upload_queue = []
    for i, sec in enumerate(sections):
        text = sec.get("text", "")
        images = sec.get("images", [])
        if not text.strip():
            continue

        try:
            write_markdown(doc_token, text)
            ok += 1
            if (i + 1) % 10 == 0:
                print(f"  ... {i+1}/{len(sections)} 已写入")
        except Exception as e:
            print(f"  ⚠️ 写入 section {i} 失败: {e}")
            continue

        # 收集需要上传的截图
        for img in images:
            if isinstance(img, str) and os.path.isfile(img):
                upload_queue.append({
                    "section_id": sec.get("id", ""),
                    "file_path": img,
                })

    # 输出投递清单
    print(f"  ✅ 文字写入完成: {ok} sections, {len(upload_queue)} 张待上传截图")
    print(f"  📋 截图清单: {json.dumps(upload_queue, ensure_ascii=False)}")
    print(f"  DOC_URL={doc_url}")
    print(f"  DOC_TOKEN={doc_token}")
    return doc_url


def _main_impl():
    global RUN_VALIDATION

    print("=" * 60)
    print("智能体市场每日健康巡检")
    print(f"运行编号：{RUN_ID}")
    print("=" * 60)

    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
    token = get_token()
    if not token:
        print("  ❌ 无法获取认证令牌，巡检终止")
        return False, None

    agents_data = fetch_agents(token)
    if not agents_data:
        print("  ❌ 获取智能体数据失败")
        return False, None

    data = agents_data.get("data", [])
    if isinstance(data, dict):
        agents_list = data.get("items") or data.get("records") or data.get("list") or []
    elif isinstance(data, list):
        agents_list = data
    else:
        agents_list = []
    if not agents_list:
        print("  ❌ 市场返回的智能体列表为空")
        return False, None

    print(f"  ✅ 获取 {len(agents_list)} 个智能体，市场顺序已冻结")
    api_report = generate_api_report(agents_data, now)
    if not api_report:
        print("  ❌ 生成市场概览失败")
        return False, None

    results = []
    full_market_run = CHAT_TEST_MODE and CHAT_TEST_ALL and NON_CHAT_TEST

    if full_market_run:
        print("  🔎 开始全量巡检：剧本优先 → LLM 降级 → 逐项执行 → 检查点落盘")
        results = asyncio.run(_run_unified_inspection(agents_list, token))
    else:
        print("  ⚠️ 当前不是全量模式，仅执行已启用的测试范围")
        if CHAT_TEST_MODE:
            chat_agents = (
                [a for a in agents_list if _is_chat_agent(a)]
                if CHAT_TEST_ALL
                else get_chat_test_batch(agents_list, CHAT_TEST_BATCH)
            )
            if chat_agents:
                results.extend(asyncio.run(run_chat_tests(chat_agents, token)))
        if NON_CHAT_TEST:
            results.extend(asyncio.run(_run_non_chat_tests(agents_list, token)))
        results = _order_by_market(results, agents_list)
        RUN_VALIDATION = {
            "run_id": RUN_ID,
            "complete": True,
            "mode": "部分巡检",
            "expected_count": len(results),
            "completed_count": len(results),
            "screenshot_count": sum(
                1 for item in results if _validate_png(item.get("screenshot", ""))[0]
            ),
            "errors": [],
        }

    results = _order_by_market(results, agents_list)
    state = "完成" if RUN_VALIDATION.get("complete") else "证据不完整"
    _save_checkpoint(agents_list, results, state=state)

    final_report = generate_full_report(api_report, results, now, "")
    report_path = RUN_DIR / f"智能体市场巡检报告-{RUN_ID}.md"
    report_path.write_text(final_report, encoding="utf-8")

    manifest_path = ""
    if results:
        manifest_path = generate_delivery_manifest(
            api_report, results, now, report_path
        )

    latest = {
        "run_id": RUN_ID,
        "state": state,
        "report_path": str(report_path),
        "manifest_path": manifest_path,
        "validation": RUN_VALIDATION,
    }
    _atomic_write_json(REPORTS_DIR / "latest.json", latest)

    # 文档创建由 cron agent 负责（逐 section 交错保证截图顺序）
    print(f"  {'✅' if RUN_VALIDATION.get('complete') else '❌'} 巡检状态：{state}")
    print(f"  报告：{report_path}")
    print(f"REPORT_PATH={report_path}")
    if manifest_path:
        print(f"MANIFEST_PATH={manifest_path}")
    print(f"RUN_STATE={state}")

    if "--stdout" in sys.argv:
        print("REPORT_MARKDOWN_BEGIN")
        print(final_report)
        print("REPORT_MARKDOWN_END")

    return bool(RUN_VALIDATION.get("complete")), str(report_path)


def main():
    run_id = datetime.datetime.now(
        datetime.timezone(datetime.timedelta(hours=8))
    ).strftime("%Y%m%d_%H%M%S")
    _configure_run_context(run_id)
    try:
        _acquire_run_lock()
    except Exception as exc:
        print(f"❌ 无法启动巡检：{exc}")
        return False, None

    try:
        _cleanup_old_runs()
        return _main_impl()
    finally:
        _release_run_lock()


if __name__ == "__main__":
    success, path = main()
    exit(0 if success else 1)