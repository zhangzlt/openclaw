"""
Agent Market 巡检 — 回复解析工具

解析 Playwright 对话页面的 AI 回复内容，过滤 UI 垃圾、元数据前缀、工具栏。
"""

import re
from typing import Optional

# ── 精确匹配跳过 ──
SKIP_LINES = {
    "新话题", "收藏", "分享链接", "使用飞书 aily", "创建者", "发布时间",
    "/", "新对话", "Invite & Earn", "Copy", "Deep Planning", "Tools",
    "AI can make mistakes. Verify key details.",
    "AI can make mistakes. Verify key detai",
    "Drop files here to upload",
    "赞", "踩", "复制", "重新生成", "停止生成",
    "发布于", "编辑",
}

# ── 正则跳过模式（匹配整行） ──
SKIP_PATTERNS = [
    re.compile(r"^\+\d+$"),    # +2, +0 工具栏数字
    re.compile(r"^\d+$"),      # 纯数字行
    re.compile(r"^/$"),        # 斜杠按钮
    re.compile(r"^新对话$"),   # 新对话按钮
    re.compile(r"^Deep Planning$"),
    re.compile(r"^Tools$"),
    re.compile(r"^Copy$"),
    re.compile(r"^Invite & Earn$"),
    re.compile(r"^智能检索\s*"),  # 检索系统提示
    re.compile(r"^Based on\s+"),  # RAG 来源引用
    re.compile(r"^\d+ 个来源"),   # 来源数量
    re.compile(r"^来源引用"),     # 来源引用标签
    re.compile(r"^🔍"),           # 搜索 emoji 前缀
]

# ── 正文前的元数据前缀（需跳过） ──
META_PREFIXES = ["Based on\n", "智能检索："]


def _should_skip(line: str) -> bool:
    """判断一行是否应该被过滤"""
    stripped = line.strip()
    if not stripped:
        return True
    if stripped in SKIP_LINES:
        return True
    if any(p.match(stripped) for p in SKIP_PATTERNS):
        return True
    return False


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

    before_lines = set(body_before.strip().split("\n"))
    after_lines = body_after.strip().split("\n")

    new_lines = []
    for line in after_lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped in before_lines:
            continue
        if stripped == question.strip():
            continue
        if _should_skip(line):
            continue
        new_lines.append(line)

    new_text = "\n".join(new_lines).strip()

    # 去掉开头的元数据前缀（如 "智能检索：..." 后面的内容才是正文）
    for mp in META_PREFIXES:
        idx = new_text.find(mp)
        if 0 <= idx < 20:
            after_meta = new_text[idx + len(mp):]
            rest_lines = after_meta.strip().split("\n")
            filtered = [rl for rl in rest_lines if not _should_skip(rl)]
            if filtered and len("\n".join(filtered).strip()) > 20:
                return "\n".join(filtered).strip()[:2000]

    # 截断过长回复（保留前 2000 字符）
    if len(new_text) > 2000:
        new_text = new_text[:1997] + "..."

    return new_text if new_text else None


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
