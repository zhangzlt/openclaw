"""
Agent Market 巡检 — 回复解析工具

解析 Playwright 对话页面的 AI 回复内容，过滤 UI 垃圾。
"""

import re
from typing import Optional

# 需要过滤的 UI 文本模式
UI_NOISE_PATTERNS = [
    r"^/$",                          # 斜杠按钮
    r"^新对话$",                     # 新对话按钮
    r"^Deep Planning$",              # 深度规划标签
    r"^Tools$",                      # 工具标签
    r"^Copy$",                       # 复制按钮
    r"^Invite & Earn$",              # 邀请按钮
    r"^智能检索\s*",                 # 检索系统提示
    r"^Based on\s+",                 # RAG 来源引用
    r"^\d+ 个来源",                  # 来源数量
    r"^来源引用",                    # 来源引用标签
    r"^🔍",                          # 搜索 emoji 前缀
]

# 编译正则
_UI_NOISE = [re.compile(p) for p in UI_NOISE_PATTERNS]


def parse_reply(body_before: str, body_after: str, question: str) -> Optional[str]:
    """
    从页面 body 差分中提取 AI 回复内容。

    Args:
        body_before: 发送消息前的 body innerText
        body_after:  等待后的 body innerText
        question:    发送的问题（用于排除）

    Returns:
        提取到的回复文本，无有效回复返回 None
    """
    if not body_after or body_after == body_before:
        return None

    # 找出新增内容
    before_lines = set(body_before.split("\n") if body_before else [])
    new_lines = []
    for line in body_after.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped in before_lines:
            continue
        if stripped == question.strip():
            continue
        # 过滤 UI 噪声
        if any(pat.search(stripped) for pat in _UI_NOISE):
            continue
        new_lines.append(stripped)

    text = "\n".join(new_lines).strip()
    if not text:
        return None

    # 截断过长回复（保留前 500 字符）
    if len(text) > 500:
        text = text[:497] + "..."

    return text


def is_valid_reply(text: Optional[str]) -> bool:
    """
    判断回复是否有效。
    
    Returns:
        True 如果回复包含有意义的内容
    """
    if not text:
        return False
    
    # 排除纯符号/空白
    if len(text.strip()) < 2:
        return False

    # 排除典型的空回复/加载状态
    noise = {"思考中", "正在思考", "loading", "Loading", "..."}
    if text.strip() in noise:
        return False

    return True
