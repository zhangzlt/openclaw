"""
Agent Market 巡检 — HTTP 请求工具

封装 API 调用的 token 管理、重试、错误处理，以及 Dify 流式聊天。
"""

import httpx
import json
import time
from pathlib import Path
from typing import Optional, Dict, Any, List

# 默认超时配置
DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)

# 缓存目录
AUTH_DIR = Path.home() / ".openclaw" / "workspace" / "work" / "agent-market" / ".auth"
TOKEN_FILE = AUTH_DIR / "token.txt"

# Dify API 基础配置
DIFY_BASE_URL = "https://agent.digitalchina.com/api/chat/stream"
DIFY_APPID_MAP: Dict[int, int] = {63: 8}  # agent_id → Dify appId


def get_token() -> Optional[str]:
    """
    获取 Agent Market API Bearer Token。

    优先从缓存读取并验证，无效时返回 None（由上层触发 Playwright 登录）。

    Returns:
        JWT token 字符串，不可用时返回 None
    """
    if TOKEN_FILE.exists():
        token = TOKEN_FILE.read_text().strip()
        if _validate_token(token):
            print("  ✅ 缓存 token 有效")
            return token
        print("  ⚠️ 缓存 token 失效，需重新登录")

    return None


def _validate_token(token: str) -> bool:
    """HTTP 验证 token 有效性（调用 /api/agents/market 快速检查）"""
    try:
        resp = httpx.get(
            "https://agent.digitalchina.com/api/agents/market",
            headers={
                "Authorization": f"Bearer {token}",
                "User-Agent": "AgentMarket-Inspection/1.0"
            },
            params={"page": 1, "pageSize": 1, "user": "张藻林"},
            timeout=10.0,
        )
        return resp.status_code == 200 and "data" in resp.json()
    except Exception:
        return False


def save_token(token: str):
    """保存 token 到缓存文件"""
    AUTH_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(token)
    print(f"  ✅ Token 已缓存 ({len(token)} 字符)")


def fetch_with_retry(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, Any]] = None,
    max_retries: int = 3,
    timeout: float = 30.0,
) -> httpx.Response:
    """
    带重试的 GET 请求。

    Args:
        url: 请求 URL
        headers: 请求头
        params: 查询参数
        max_retries: 最大重试次数
        timeout: 超时秒数

    Returns:
        httpx.Response 对象

    Raises:
        httpx.HTTPError: 所有重试均失败
    """
    last_err = None
    for attempt in range(max_retries):
        try:
            resp = httpx.get(url, headers=headers, params=params, timeout=timeout)
            resp.raise_for_status()
            return resp
        except httpx.HTTPError as e:
            last_err = e
            if attempt < max_retries - 1:
                wait = (attempt + 1) * 2
                print(f"  ⚠️ 请求失败 (重试 {attempt+1}/{max_retries}，等待 {wait}s): {e}")
                time.sleep(wait)

    raise last_err


async def fetch_dify_chat(
    app_id: int,
    message: str,
    token: str,
    user: str = "zhangzlt",
    timeout: float = 60.0,
) -> Dict[str, Any]:
    """
    通过 Dify SSE 流式 API 发送消息并收集完整回复。

    用于测试 Market 内嵌 Dify 智能体（openType=api + source=dify）。

    Args:
        app_id: Dify 应用 ID（如 8）
        message: 发送的消息
        token: Agent Market Bearer token
        user: 用户标识
        timeout: 请求超时秒数

    Returns:
        {
            "success": True/False,
            "reply": "完整回复文本",
            "error": "错误信息（失败时）",
            "elapsed": float  # 耗时秒数
        }
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",
    }

    payload = {"appId": app_id, "user": user, "message": message, "inputs": {}}

    t_start = time.time()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(DIFY_BASE_URL, headers=headers, json=payload)

        elapsed = round(time.time() - t_start, 1)
        reply = ""

        # 解析 SSE 流
        for line in resp.text.split("\n"):
            if line.startswith("data: "):
                try:
                    chunk = json.loads(line[6:])
                    if "content" in chunk and chunk["content"]:
                        reply += chunk["content"]
                except json.JSONDecodeError:
                    pass

        success = bool(reply and len(reply.strip()) > 5)
        return {
            "success": success,
            "reply": reply,
            "error": None if success else "未返回有效回复",
            "elapsed": elapsed,
        }

    except Exception as e:
        return {
            "success": False,
            "reply": "",
            "error": f"API 请求失败: {str(e)[:200]}",
            "elapsed": round(time.time() - t_start, 1),
        }


def get_dify_app_id(agent_id: int) -> Optional[int]:
    """
    根据 Agent Market 智能体 ID 查找对应的 Dify appId。

    Args:
        agent_id: Market 中的智能体 ID（如 63）

    Returns:
        Dify appId，未找到返回 None
    """
    return DIFY_APPID_MAP.get(agent_id)
