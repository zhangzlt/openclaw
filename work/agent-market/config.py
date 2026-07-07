"""
全局配置 - Agent Market 健康巡检

登录页选择器: input[placeholder*='邮箱'] / input[placeholder*='密码']
URL 格式:
  - 市场首页: https://agent.digitalchina.com/
  - 智能体对话: https://agent.digitalchina.com/ai/gui/chat/{path}
  - 打开按钮 API: widget/track?agentId={id}&detail={path}
"""

import os
from pathlib import Path

# 项目根目录
ROOT_DIR = Path(__file__).parent

# 浏览器配置
BROWSER_CONFIG = {
    "headless": os.getenv("BROWSER_HEADLESS", "true").lower() == "true",
    "timeout": int(os.getenv("BROWSER_TIMEOUT", "30000")),
    "auth_file": os.getenv("AUTH_FILE", ".auth/session.json"),
}

# 登录选择器（重要！用 placeholder 不用 id）
LOGIN_SELECTORS = {
    "email": "input[placeholder*='邮箱']",
    "password": "input[placeholder*='密码']",
    "submit": "button[type='submit']",
}

# Agent Market URL
BASE_URL = "https://agent.digitalchina.com"
LOGIN_URL = f"{BASE_URL}/login"
MARKET_URL = f"{BASE_URL}/agent-market"

# 智能体 URL 格式
CHAT_PATH_PREFIX = "/ai/gui/chat/"
WIDGET_TRACK_URL = f"{BASE_URL}/widget/track"

# 巡检配置
MAX_AGENTS = int(os.getenv("MAX_AGENTS", "41"))
MAX_RETRIES = 3
RETRY_DELAY = 2  # 秒

# 输出目录
SCREENSHOTS_DIR = ROOT_DIR / "screenshots"
REPORTS_DIR = ROOT_DIR / "reports"
LOGS_DIR = ROOT_DIR / "logs"

# LLM 配置
LLM_CONFIG = {
    "model": os.getenv("LLM_MODEL", "deepseek-v4-pro"),
    "base_url": os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1"),
    "api_key": os.getenv("LLM_API_KEY", ""),
}

# 飞书通知
FEISHU_WEBHOOK = os.getenv("FEISHU_WEBHOOK_URL", "")

# 已验证的 Agent 数据
VERIFIED_AGENTS = {
    110: {
        "name": "折扣问答小助手",
        "detail_path": "/chat/a_3687bf8dfcc64b378852e86891d042e5",
        "entry_url": "https://agent.digitalchina.com/widget/open?agentId=110&detail=/chat/a_3687bf8dfcc64b378852e86891d042e5",
    },
    109: {
        "name": "CTC智能客服",
        "detail_path": "/chat/a_eb9c4b2f0c4c40ae90ce7dfb8fe665eb",
        "entry_url": "https://agent.digitalchina.com/widget/open?agentId=109&detail=/chat/a_eb9c4b2f0c4c40ae90ce7dfb8fe665eb",
    },
    74: {
        "name": "电子签章智能问答助手",
        "detail_path": "/chat/a_1f46a3e5ec0c4d59b0e93eae67b638a1",
        "entry_url": "https://agent.digitalchina.com/widget/open?agentId=74&detail=/chat/a_1f46a3e5ec0c4d59b0e93eae67b638a1",
    },
    73: {
        "name": "EB智能客服机器人",
        "detail_path": "/chat/a_ea846e95d9e645129b6049b74b3cfd04",
        "entry_url": "https://agent.digitalchina.com/widget/open?agentId=73&detail=/chat/a_ea846e95d9e645129b6049b74b3cfd04",
    },
}


def ensure_dirs():
    """确保输出目录存在"""
    for d in [SCREENSHOTS_DIR, REPORTS_DIR, LOGS_DIR]:
        d.mkdir(parents=True, exist_ok=True)


def get_agent_url(agent_id: int, detail_path: str) -> str:
    """构建智能体对话页 URL"""
    return f"{BASE_URL}{CHAT_PATH_PREFIX}{detail_path.lstrip('/')}"
