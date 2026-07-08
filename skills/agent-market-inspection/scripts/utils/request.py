"""
Agent Market 巡检 — HTTP 请求工具

封装 API 调用的 token 管理、重试、错误处理。
"""

import httpx
import time
from pathlib import Path
from typing import Optional, Dict, Any

# 默认超时配置
DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)

# 缓存目录
AUTH_DIR = Path.home() / ".openclaw" / "workspace" / "work" / "agent-market" / ".auth"
TOKEN_FILE = AUTH_DIR / "token.txt"


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
