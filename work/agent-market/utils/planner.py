"""
LLM 智能规划器

当固定剧本命中失败或遇到新智能体时，获取页面截图+文字+无障碍树，
由 LLM 生成受限 JSON 操作计划（严格白名单），成功剧本自动缓存。
"""

import json
import base64
import os
from pathlib import Path

# Whitelist of allowed operations (must match executor.py)
ALLOWED_ACTIONS = [
    "open",
    "click",
    "fill",
    "chat_send",
    "chat_wait",
    "press",
    "hover",
    "find_and_click",
    "upload",
    "snapshot",
    "screenshot",
    "eval",
    "scroll",
    "wait",
    "verify",
]

SYSTEM_PROMPT = """你是一个自动化测试规划器。根据智能体的页面截图、无障碍树和描述，
生成一个严格受限的 JSON 操作计划。只需要输出 JSON，不要任何解释文字。

## 可用操作（白名单）

| 操作 | 参数 | 说明 |
|------|------|------|
| open | {url, wait_sec?, wait_selector?, wait_timeout?} | 打开 URL，可选等待选择器 |
| click | {selector? 或 text?} | 点击元素（selector 优先） |
| fill | {selector, text} | 填充 input/textarea |
| chat_send | {message} | 发送聊天消息（自动探测输入框） |
| chat_wait | {timeout: 秒} | 等待聊天回复 |
| press | {key} | 按键（Enter, Tab 等）|
| hover | {selector} | 悬停元素 |
| find_and_click | {text} | 语义搜索文本并点击 |
| upload | {selector, files: [路径]} | 上传文件 |
| snapshot | {} | 获取无障碍树 |
| screenshot | {} | 截图 |
| eval | {js} | 执行 JavaScript |
| scroll | {pixels} | 滚动指定像素 |
| wait | {seconds} | 等待 N 秒 |
| verify | {expected_text, description} | 验证结果（必需，最后一步） |

## 规划规则

1. **第一个操作必须是 open**
2. **最后一个操作必须是 screenshot，倒数第二个必须是 verify**
3. 确认页面无 404、无登录拦截、无 App not found 后才规划交互
4. 如果页面无法测试（需特殊登录、403、404 等）→ strategy 设为 "skip"，steps 仅含 open + screenshot
5. 如果是聊天界面 → strategy="chat"，用 chat_send + chat_wait
6. 如果是文件上传界面 → strategy="file_upload"，用 upload
7. 如果是 web 表单/交互 → strategy="web_interactive"
8. **根据页面实际可见元素规划**，不要凭空想象

## 输出 JSON 结构

{
  "strategy": "chat|file_upload|web_interactive|internal_chat|skip|generic",
  "reasoning": "简短推理（1-2句话）",
  "chat_input_type": "contenteditable|textarea|input|null",
  "chat_input_selector": "CSS选择器或null",
  "verify": {"expected_text": "期望在页面中看到的关键词", "description": "验证说明"},
  "steps": [
    {"action": "open", "url": "...", "wait_selector": "..."},
    ...
    {"action": "verify", "expected_text": "关键词", "description": "..."},
    {"action": "screenshot", "label": "final"}
  ]
}

## 严格输出约束

- 仅输出 JSON，不含 ```json 标记
- steps 至少包含 open + verify + screenshot
- chat_input_selector: 从页面 snapshot 中提取真实 CSS 选择器
- 所有 selector 必须是真实存在的 CSS 选择器（.class, #id, [attr], tag 等）
- 不确定的属性不要编造"""


def _get_api_config() -> dict:
    """获取 LLM API 配置，优先级：环境变量 > gateway 配置文件。"""
    import json as _json

    # 1) 环境变量优先（开发调试）
    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("DEEPSEEK_API_KEY", "")
    base_url = os.getenv("OPENAI_BASE_URL", "")
    model = os.getenv("PLANNER_MODEL", "")

    if api_key:
        return {
            "api_key": api_key,
            "base_url": base_url or "https://api.deepseek.com",
            "model": model or "deepseek-chat",
        }

    # 2) 从 OpenClaw gateway 配置读取
    gw_candidates = [
        os.path.expanduser("~/.openclaw/openclaw.json"),
        "/home/node/.openclaw/openclaw.json",
    ]
    for gw_path in gw_candidates:
        if not os.path.isfile(gw_path):
            continue
        try:
            with open(gw_path, "r") as f:
                gw = _json.load(f)
            # 查找 deepseek 或其他 OpenAI 兼容提供商配置
            m = gw.get("models", {})
            providers = m.get("providers", {})
            for provider_name in ["deepseek", "openai", "vllm"]:
                provider = providers.get(provider_name, {})
                key = provider.get("apiKey", "")
                if key and key not in ("not-needed", ""):
                    return {
                        "api_key": key,
                        "base_url": provider.get("baseUrl", "https://api.deepseek.com"),
                        "model": model or "deepseek-chat",
                    }
        except Exception:
            continue

    return {"api_key": "", "base_url": "https://api.deepseek.com", "model": "deepseek-chat"}


async def plan_operations(
    agent: dict,
    page_body_text: str,
    page_screenshot_path: str,
    page_snapshot_text: str,
    error_context: str = "",
) -> dict:
    """调用 LLM 生成受限 JSON 操作计划。

    Args:
        agent: {id, name, description, categoryLabel, url, appTypeLabel, openType}
        page_body_text: 页面 body.innerText
        page_screenshot_path: 页面截图文件路径
        page_snapshot_text: 无障碍树文本
        error_context: 上次失败的错误信息（用于重规划）

    Returns:
        解析后的 JSON 操作计划 dict
    """
    import httpx

    cfg = _get_api_config()
    api_key = cfg["api_key"]
    base_url = cfg["base_url"]
    model = cfg["model"]

    if not api_key:
        raise ValueError("LLM API key 未配置：请设置 OPENAI_API_KEY 环境变量或在 gateway 配置中设置 providers.deepseek.apiKey")

    # 构建用户提示
    user_parts = _build_user_prompt(agent, page_body_text, page_snapshot_text, error_context)

    # 读取截图 base64
    screenshot_b64 = ""
    if page_screenshot_path and os.path.isfile(page_screenshot_path):
        with open(page_screenshot_path, "rb") as f:
            screenshot_b64 = base64.b64encode(f.read()).decode()

    # 构建消息
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
    ]

    if screenshot_b64:
        messages.append({
            "role": "user",
            "content": [
                {"type": "text", "text": user_parts},
                {"type": "image_url", "image_url": {
                    "url": f"data:image/png;base64,{screenshot_b64}",
                    "detail": "auto",
                }},
            ],
        })
    else:
        messages.append({"role": "user", "content": user_parts})

    # 调用 LLM
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{base_url}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": messages,
                "temperature": 0.2,
                "max_tokens": 4000,
                "response_format": {"type": "json_object"},
            },
        )
        resp.raise_for_status()
        data = resp.json()

    raw = data["choices"][0]["message"]["content"].strip()

    # 清理可能的 markdown 包裹
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:])
        if raw.endswith("```"):
            raw = raw[:-3]
    raw = raw.strip()

    plan = json.loads(raw)
    _validate_plan(plan)
    return plan


def _build_user_prompt(agent: dict, body: str, snapshot: str, error: str) -> str:
    lines = [f"""## 智能体信息
- ID: {agent.get('id')}
- 名称: {agent.get('name', '未知')}
- 描述: {agent.get('description', '无')}
- 分类: {agent.get('categoryLabel', '未知')}
- 类型: {agent.get('appTypeLabel', '未知')}
- 开放类型: {agent.get('openType', '未知')}
- URL: {agent.get('url', '未知')}"""]

    if error:
        lines.append(f"\n⚠️ 上一次尝试失败：{error[:300]}")

    lines.append(f"""
## 页面文字内容（前 3000 字符）
{body[:3000] or '(空)'}

## 页面无障碍树（前 4000 字符）
{snapshot[:4000] or '(空)'}

请生成操作计划（仅输出 JSON）。""")

    return "\n".join(lines)


def _validate_plan(plan: dict):
    """校验 LLM 生成的计划，不合法则抛异常。"""
    if not isinstance(plan, dict):
        raise ValueError(f"计划必须是 JSON 对象，收到: {type(plan).__name__}")

    strategy = plan.get("strategy")
    valid_strategies = {"chat", "file_upload", "web_interactive",
                        "internal_chat", "skip", "generic"}
    if strategy not in valid_strategies:
        raise ValueError(f"未知策略 '{strategy}'，允许: {valid_strategies}")

    steps = plan.get("steps", [])
    if strategy == "skip":
        return  # skip 策略不需要步骤验证

    if not steps:
        raise ValueError("操作步骤不能为空")

    # 第一个操作必须是 open
    if steps[0].get("action") != "open":
        raise ValueError(f"第一个操作必须是 open，实际: {steps[0].get('action')}")

    # 检查白名单
    for i, step in enumerate(steps):
        action = step.get("action")
        if action not in ALLOWED_ACTIONS:
            raise ValueError(f"步骤 {i} 使用了非白名单操作: {action}")
        if action == "open" and i > 0:
            raise ValueError(f"open 操作只能作为第一步，当前位置: {i}")

    # 最后两步是 verify + screenshot（软校验，仅警告）
    last_two = [s.get("action") for s in steps[-2:]]
    if "verify" not in last_two:
        print(f"    ⚠️ 剧本最后两步缺少 verify，末尾操作: {last_two}")
    if "screenshot" not in last_two:
        print(f"    ⚠️ 剧本最后两步缺少 screenshot，末尾操作: {last_two}")


def generate_fallback_plan(agent: dict, error: str = "") -> dict:
    """当 LLM 规划失败时生成最小安全回退剧本。

    仅执行 open + screenshot + 标记 skip，确保不会漏掉智能体。
    """
    url = agent.get("url", "")
    if not url:
        return {
            "strategy": "skip",
            "reasoning": f"无可测试 URL（{agent.get('appTypeLabel', '?')}），{error}",
            "steps": [],
            "verify": {"expected_text": "", "description": "no-url"},
        }

    return {
        "strategy": "generic",
        "reasoning": f"LLM 规划失败，回退为最小通用测试。原因: {error[:200]}",
        "steps": [
            {"action": "open", "url": url, "wait_sec": 5,
             "wait_selector": "[contenteditable], textarea, input[type='text'], button, a"},
            {"action": "verify", "expected_text": "",
             "description": "页面可访问（仅检查是否返回错误页）"},
            {"action": "screenshot", "label": "final"},
        ],
        "verify": {"expected_text": "", "description": "minimal"},
    }
