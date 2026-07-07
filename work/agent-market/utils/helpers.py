"""
辅助函数

通用工具函数
"""

import re
import time
import json
from pathlib import Path


def clean_text(text: str) -> str:
    """清理文本: 去除多余空白、换行"""
    if not text:
        return ""
    text = re.sub(r'\s+', ' ', text)
    text = text.strip()
    return text


def safe_get(data: dict, *keys, default=None):
    """安全获取嵌套字典的值"""
    for key in keys:
        if isinstance(data, dict):
            data = data.get(key, default)
        else:
            return default
    return data


def parse_agent_url(url: str) -> dict:
    """解析智能体 URL"""
    result = {
        "agent_id": None,
        "detail_path": None,
        "base": "https://agent.digitalchina.com",
    }

    # widget/open?agentId=XXX&detail=YYY
    match = re.search(r'agentId=(\d+)', url)
    if match:
        result["agent_id"] = int(match.group(1))

    match = re.search(r'detail=(.+?)(?:&|$)', url)
    if match:
        result["detail_path"] = match.group(1)

    # /ai/gui/chat/a_xxx
    match = re.search(r'/chat/(a_[a-z0-9]+)', url)
    if match:
        result["detail_path"] = f"/chat/{match.group(1)}"

    return result


def throttle(delay: float = 1.0):
    """请求节流"""
    time.sleep(delay)


def save_json(data: dict, path: Path):
    """保存 JSON 文件"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_json(path: Path) -> dict:
    """加载 JSON 文件"""
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
