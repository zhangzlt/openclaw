#!/usr/bin/env python3
"""
Feishu Doc Builder v2 — 纯 Python REST API 构建飞书巡检报告

采用飞书官方三步流程创建图片块：
1. 创建空 Image Block (image: {})
2. 上传图片素材 (parent_node = image_block_id)
3. replace_image 设置 file_token

所有操作在 Python 内完成，无 LLM 参与。

用法: python3 feishu_build_doc_v2.py [manifest_path]
"""

import hashlib
import json
import io as std_io
import os
import re
import sys
import time
from pathlib import Path
from typing import Tuple

import httpx

# ── 配置 ──────────────────────────────────────
FEISHU_API_BASE = "https://open.feishu.cn/open-apis"
WORKSPACE = Path("/home/node/.openclaw/workspace")

_APP_ID = None
_APP_SECRET = None
_token_cache = None
_token_expires = 0

# ── 状态标准化 ──────────────────────────────
def normalize_status(status: str) -> str:
    """标准化状态字符串"""
    value = str(status or "").strip().upper()
    aliases = {
        "OK": "PASS", "PASSED": "PASS", "通过": "PASS",
        "FAILED": "FAIL", "失败": "FAIL",
        "阻塞": "BLOCKED",
        "跳过": "SKIPPED",
        "警告": "WARNING",
        "CHAT_ERROR": "FAIL",  # chat_error 归入失败
        "UNREACHABLE": "BLOCKED",
        "BROWSER_INFRASTRUCTURE": "INFRA_FAIL",  # 浏览器基础设施故障
    }
    return aliases.get(value, value)

STATUS_ICONS = {
    "PASS": "✅",
    "FAIL": "❌",
    "BLOCKED": "🚫",
    "INFRA_FAIL": "🔧",
    "SKIPPED": "⏭️",
    "WARNING": "⚠️",
}


def _load_app_secret():
    global _APP_ID, _APP_SECRET
    config_path = Path.home() / ".openclaw/openclaw.json"
    with open(config_path) as f:
        cfg = json.load(f)
    feishu = cfg["channels"]["feishu"]
    _APP_ID = feishu["appId"]
    _APP_SECRET = feishu["appSecret"]


def get_token() -> str:
    global _token_cache, _token_expires
    now = time.time()
    if _token_cache and now < _token_expires - 60:
        return _token_cache
    if not _APP_SECRET:
        _load_app_secret()
    resp = httpx.post(
        f"{FEISHU_API_BASE}/auth/v3/tenant_access_token/internal",
        json={"app_id": _APP_ID, "app_secret": _APP_SECRET},
        timeout=15,
    )
    resp.raise_for_status()
    d = resp.json()
    if d.get("code") != 0:
        raise RuntimeError(f"获取 token 失败: {d.get('msg')}")
    _token_cache = d["tenant_access_token"]
    _token_expires = now + d.get("expire", 7200)
    return _token_cache


def api(method, path, **kw):
    token = get_token()
    h = kw.pop("headers", {})
    h["Authorization"] = f"Bearer {token}"
    resp = httpx.request(method, f"{FEISHU_API_BASE}{path}", headers=h, timeout=60, **kw)
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        body = resp.text[:500] if resp.text else "<empty body>"
        raise RuntimeError(
            f"API {method} {path}: HTTP {resp.status_code} — {body}"
        ) from e
    d = resp.json()
    if d.get("code") != 0:
        raise RuntimeError(f"API {method} {path}: {d.get('msg')} (code={d.get('code')})")
    return d


# ── Block builders ───────────────────────────
def heading3_block(content: str) -> dict:
    return {"block_type": 5, "heading3": {"elements": [{"text_run": {"content": content}}], "style": {}}}


def text_block(content: str) -> dict:
    return {"block_type": 2, "text": {"elements": [{"text_run": {"content": content}}], "style": {}}}


def divider_block() -> dict:
    return {"block_type": 22, "divider": {}}


def normalize_block_content(value) -> str:
    """归一化代码块内容：处理 str / list / dict / None。

    严格禁止 "\n".join(str_value) —— 这会把字符串拆成一个字符一行。
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        # 单层 dict → 取其 text/content/answer 字段
        text = (
            value.get("text")
            or value.get("content")
            or value.get("answer")
            or ""
        )
        return str(text).strip()
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = (
                    item.get("text")
                    or item.get("content")
                    or item.get("answer")
                    or ""
                )
                if text:
                    parts.append(str(text))
        return "\n".join(parts).strip()
    return str(value).strip()


def native_code_block(content: str, language: int = 1) -> dict:
    """飞书原生代码块 (block_type=14)。

    飞书 code block 的 text_run.content 不支持内嵌 \n（会被截断），
    必须用多个 text_run 元素表示换行：每行一个 text_run，行间插入 content="\n" 的 text_run。

    language 枚举：
      1 = Plain Text（默认）
      参考：https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/data-structure/code-block
    """
    normalized = normalize_block_content(content)
    lines = normalized.split("\n")
    elements = []
    for i, line in enumerate(lines):
        if i > 0:
            elements.append({"text_run": {"content": "\n"}})
        elements.append({"text_run": {"content": line}})
    return {
        "block_type": 14,
        "code": {
            "elements": elements,
            "style": {"language": language}
        }
    }


def _split_code_blocks(content: str, max_lines: int = 80) -> list:
    """超长文本拆分为多个原生代码块"""
    normalized = normalize_block_content(content)
    lines = normalized.split("\n")
    if len(lines) <= max_lines:
        return [native_code_block(normalized)]
    blocks = []
    for i in range(0, len(lines), max_lines):
        chunk = "\n".join(lines[i:i + max_lines])
        blocks.append(native_code_block(chunk))
    return blocks


def empty_image_block() -> dict:
    """创建空图片块 — token 是只读属性，不可在此传入"""
    return {"block_type": 27, "image": {}}


# ── 飞书文档操作 ─────────────────────────────
def get_children_count(doc_token: str) -> int:
    """读取根级 children 总数（处理分页）"""
    total = 0
    page_token = None
    while True:
        params = {"page_size": 500}
        if page_token:
            params["page_token"] = page_token
        data = api("GET", f"/docx/v1/documents/{doc_token}/blocks/{doc_token}/children",
                   params=params)
        items = data.get("data", {}).get("items", [])
        total += len(items)
        if not data.get("data", {}).get("has_more"):
            break
        page_token = data.get("data", {}).get("page_token")
    return total


def add_blocks(doc_token: str, blocks: list, index: int) -> list:
    """在指定位置一次追加多个 blocks，返回添加的所有 block_id"""
    resp = api("POST", f"/docx/v1/documents/{doc_token}/blocks/{doc_token}/children",
        json={"children": blocks, "index": index})
    return [c["block_id"] for c in resp.get("data", {}).get("children", [])]


def create_image_block(doc_token: str, image_path: str, index: int) -> str:
    """
    三步创建有内容的图片块：
    1. 创建空 Image Block
    2. 上传图片素材 (parent_node=image_block_id)
    3. replace_image 设置 token
    返回 image_block_id
    """
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(image_path)

    resp = api("POST", f"/docx/v1/documents/{doc_token}/blocks/{doc_token}/children",
               json={"children": [empty_image_block()], "index": index})
    img_block_id = resp["data"]["children"][0]["block_id"]

    _fill_image_block(doc_token, image_path, img_block_id)
    return img_block_id


def _fill_image_block(doc_token: str, image_path: str, img_block_id: str):
    """填充已有的空图片块：上传素材 + replace_image"""
    path = Path(image_path)
    size = path.stat().st_size
    with open(path, "rb") as f:
        img_data = f.read()

    token = get_token()
    resp = httpx.post(
        f"{FEISHU_API_BASE}/drive/v1/medias/upload_all",
        headers={"Authorization": f"Bearer {token}"},
        data={"file_name": path.name, "parent_type": "docx_image",
              "parent_node": img_block_id, "size": str(size)},
        files={"file": (path.name, std_io.BytesIO(img_data), "image/png")},
        timeout=60,
    )
    resp.raise_for_status()
    resp_json = resp.json()
    if resp_json.get("code") != 0:
        raise RuntimeError(f"上传图片失败: {resp_json.get('msg')}")
    file_token = resp_json["data"]["file_token"]

    api("PATCH", f"/docx/v1/documents/{doc_token}/blocks/{img_block_id}",
        json={"replace_image": {"token": file_token}})


def verify_image_block(doc_token: str, block_id: str) -> dict:
    """验证图片块已正确填充"""
    data = api("GET", f"/docx/v1/documents/{doc_token}/blocks/{block_id}")
    block = data.get("data", {}).get("block", {})
    errors = []
    if block.get("block_type") != 27:
        errors.append("block_type 不是 27")
    if not block.get("image", {}).get("token"):
        errors.append("image.token 为空")
    return {"valid": len(errors) == 0, "errors": errors, "block": block}


def get_all_blocks(doc_token: str) -> list:
    """分页读取文档全部根级 blocks"""
    all_blocks = []
    page_token = None
    while True:
        params = {}
        if page_token:
            params["page_token"] = page_token
        data = api("GET", f"/docx/v1/documents/{doc_token}/blocks/{doc_token}/children",
                   params=params)
        items = data.get("data", {}).get("items", [])
        all_blocks.extend(items)
        if not data.get("data", {}).get("has_more"):
            break
        page_token = data.get("data", {}).get("page_token")
    return all_blocks


# ── 校验函数 ─────────────────────────────────
def _sha256(file_path: str) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_valid_png(file_path: str) -> bool:
    try:
        with open(file_path, "rb") as f:
            return f.read(8).startswith(b"\x89PNG\r\n\x1a\n")
    except Exception:
        return False


def validate_manifest(manifest: dict) -> Tuple[list, list]:
    """硬门禁：校验所有 agent section 数据完整性。返回 (sections, errors)
    
    SKIPPED 智能体不要求有截图；其他状态必须有有效截图。
    """
    raw_sections = manifest.get("sections", [])
    sections = [s for s in raw_sections if s.get("id", "").startswith("agent_")]
    errors = []

    market_count = manifest.get("total_agents") or len(sections)
    if len(sections) != market_count:
        errors.append(f"section 数量({len(sections)}) != 市场数量({market_count})")

    indices = [s.get("inspection_index", 0) for s in sections]
    expected = list(range(1, len(sections) + 1))
    if sorted(indices) != expected:
        errors.append(f"inspection_index 不连续: {sorted(indices)} != {expected}")

    seen_ids = set()
    seen_paths = set()
    for s in sections:
        idx = s.get("inspection_index", 0)
        aid = s.get("agent_id", 0)
        name = s.get("agent_name", "?")
        status = str(s.get("status", "")).lower()

        if aid in seen_ids:
            errors.append(f"Agent {idx} ({name}): agent_id={aid} 重复")
        seen_ids.add(aid)

        images = s.get("images", [])
        is_skipped = status == "skipped"

        # SKIPPED 智能体不要求截图
        if is_skipped:
            # 但不能有非 skip 截图绑定
            if images and "_skip" not in str(images[0] if images else ""):
                errors.append(f"Agent {idx}/{aid} ({name}): SKIPPED 但绑定了非 skip 截图")
            if not s.get("text") and not s.get("test_analysis"):
                errors.append(f"Agent {idx}/{aid} ({name}): SKIPPED 缺少说明文本")
            continue

        # 非 SKIPPED 必须有截图
        if not images:
            errors.append(f"Agent {idx}/{aid} ({name}): 无截图绑定")
            continue
        img_path = images[0]
        if not os.path.isfile(img_path):
            errors.append(f"Agent {idx}/{aid} ({name}): 截图不存在 {img_path}")
            continue

        if not _is_valid_png(img_path):
            errors.append(f"Agent {idx}/{aid} ({name}): 非有效 PNG")

        size = Path(img_path).stat().st_size
        if size < 100:
            errors.append(f"Agent {idx}/{aid} ({name}): PNG 过小 ({size} bytes)")

        meta_path = Path(img_path).with_suffix(".json")
        if meta_path.is_file():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if str(meta.get("agent_id", "")) != str(aid):
                errors.append(f"Agent {idx}/{aid}: 元数据 agent_id 不匹配")
            if meta.get("inspection_index") != idx:
                errors.append(f"Agent {idx}/{aid}: 元数据 inspection_index 不匹配")

        if s.get("screenshot_sha256") and _sha256(img_path) != s["screenshot_sha256"]:
            errors.append(f"Agent {idx}/{aid}: SHA256 不匹配")

        if img_path in seen_paths:
            errors.append(f"Agent {idx}/{aid}: 截图路径重复 {img_path}")
        seen_paths.add(img_path)

        for field in ["test_operation", "test_result"]:
            if not s.get(field) and not s.get("text", "").strip():
                errors.append(f"Agent {idx}/{aid}: 缺少 {field}")

    return sections, errors


def _build_screenshot_lookup(sections: list) -> dict:
    """通过 agent_id 建立截图路径查找表"""
    lookup = {}
    for s in sections:
        aid = str(s.get("agent_id", ""))
        images = s.get("images", [])
        if images:
            lookup[aid] = images[0]
    return lookup


def normalize_answer_text(section: dict) -> str:
    """从多个可能字段中提取回答文本，返回第一个非空值。

    新数据优先使用 answer_text，旧数据回退 agent_answer 等字段。
    """
    import re
    candidates = [
        section.get("answer_text"),
        section.get("agent_answer"),
        section.get("assistant_answer"),
        section.get("response"),
        section.get("reply"),
        section.get("output"),
        section.get("result_text"),
    ]
    for v in candidates:
        if isinstance(v, str) and v.strip():
            return v.strip()
        elif isinstance(v, dict):
            for k in ("text", "content", "value", "answer", "response"):
                inner = v.get(k)
                if isinstance(inner, str) and inner.strip():
                    return inner.strip()
    # q_results 回退
    for qr in section.get("q_results") or []:
        for k in ("response", "answer", "result"):
            v = qr.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return ""


# ── Section Builders ─────────────────────────

def _build_skipped_section(aid, name, raw_status, norm_status, icon, idx, run_id,
                           test_analysis, elapsed, text):
    """SKIPPED 智能体：不要求 question/answer/截图"""
    blocks = [
        heading3_block(f"{icon} {idx:03d}. {name}"),
        text_block(f"智能体编号：{aid}"),
        text_block(f"状态：{icon} {norm_status}（原始：{raw_status}）"),
        text_block(f"跳过原因：{test_analysis or text[:200] or '巡检策略标记跳过'}"),
        text_block(f"用时：{elapsed or 0} 秒"),
        text_block(f"巡检锚点：{run_id}:{idx}:{aid}"),
        divider_block(),
    ]
    return blocks


def _build_blocked_section(aid, name, raw_status, norm_status, icon, idx, run_id,
                           test_op, test_res, test_analysis, elapsed, text):
    """BLOCKED 智能体：不强制 answer，展示阻塞原因；截图可选"""
    blocks = [
        heading3_block(f"{icon} {idx:03d}. {name}"),
        text_block(f"智能体编号：{aid}"),
        text_block(f"状态：{icon} {norm_status}（原始：{raw_status}）"),
        text_block(f"阻塞原因：{test_analysis or text[:200] or '巡检过程被阻塞'}"),
    ]
    if test_op:
        blocks.append(text_block(f"测试操作：{test_op}"))
    if test_res:
        blocks.append(text_block(f"测试结果：{test_res}"))
    blocks.extend([
        text_block(f"用时：{elapsed or 0} 秒"),
        text_block(f"巡检锚点：{run_id}:{idx}:{aid}"),
        divider_block(),
    ])
    return blocks


def _build_failed_section(aid, name, raw_status, norm_status, icon, idx, run_id,
                          test_op, test_res, test_analysis, elapsed, text, is_chat=False):
    """FAIL 智能体：不强制有效回答，展示实际错误"""
    blocks = [
        heading3_block(f"{icon} {idx:03d}. {name}"),
        text_block(f"智能体编号：{aid}"),
        text_block(f"状态：{icon} {norm_status}（原始：{raw_status}）"),
        text_block(f"测试分析：{test_analysis or text[:200] or '巡检未通过'}"),
    ]
    if test_op:
        blocks.append(text_block(f"测试操作：{test_op}"))
    if test_res:
        blocks.append(text_block(f"测试结果：{test_res}"))
    blocks.extend([
        text_block(f"用时：{elapsed or 0} 秒"),
        text_block(f"巡检锚点：{run_id}:{idx}:{aid}"),
        divider_block(),
    ])
    return blocks


def _build_pass_section(aid, name, raw_status, norm_status, icon, idx, run_id,
                         test_op, test_res, test_analysis, elapsed, test_question, agent_answer, is_chat):
    """PASS 智能体（含 chat 证据完整性已在上层校验）

    对话型智能体生成格式：
      测试操作：     ← 普通文字块
      [原生代码块]   ← 包含 question_text
      测试结果：     ← 普通文字块
      [原生代码块]   ← 包含 answer_text
    """
    blocks = [
        heading3_block(f"{icon} {idx:03d}. {name}"),
        text_block(f"智能体编号：{aid}"),
        text_block(f"状态：{icon} {norm_status}（原始：{raw_status}）"),
    ]
    if is_chat and test_question:
        blocks.append(text_block("测试操作："))
        blocks.append(native_code_block(test_question))
    else:
        blocks.append(text_block(f"测试操作：{test_op or '打开目标页面并检查可用性'}"))

    if is_chat and agent_answer:
        blocks.append(text_block("测试结果："))
        # 超长回答拆分
        normalized_answer = normalize_block_content(agent_answer)
        if len(normalized_answer.split("\n")) > 80:
            blocks.extend(_split_code_blocks(agent_answer, max_lines=80))
        else:
            blocks.append(native_code_block(agent_answer))
    else:
        blocks.append(text_block(f"测试结果：{test_res or '已完成预定操作'}"))

    blocks.append(text_block(f"测试分析：{test_analysis or '页面可访问，已完成预定操作并得到有效响应'}"))
    blocks.extend([
        text_block(f"用时：{elapsed or 0} 秒"),
        text_block(f"巡检锚点：{run_id}:{idx}:{aid}"),
        divider_block(),
    ])
    return blocks


def _build_generic_section(aid, name, raw_status, norm_status, icon, idx, run_id,
                           test_op, test_res, test_analysis, elapsed, text):
    """未知状态的通用 section"""
    blocks = [
        heading3_block(f"{icon} {idx:03d}. {name}"),
        text_block(f"智能体编号：{aid}"),
        text_block(f"状态：{icon} {norm_status}（原始：{raw_status}）"),
    ]
    if test_op:
        blocks.append(text_block(f"测试操作：{test_op}"))
    if test_res:
        blocks.append(text_block(f"测试结果：{test_res}"))
    blocks.append(text_block(f"测试分析：{test_analysis or text[:200] or '状态未知'}"))
    blocks.extend([
        text_block(f"用时：{elapsed or 0} 秒"),
        text_block(f"巡检锚点：{run_id}:{idx}:{aid}"),
        divider_block(),
    ])
    return blocks


# ── 主流程 ───────────────────────────────────
def build_report(manifest: dict) -> dict:
    """返回 {'doc_url': str, 'doc_token': str, 'image_count': int, ...}"""
    run_id = manifest.get("run_id", "unknown")
    sections, errors = validate_manifest(manifest)

    if errors:
        print("❌ 投递前门禁失败:")
        for e in errors:
            print(f"   {e}")
        print(f"\nDELIVERY_STATE=失败")
        print(f"DELIVERY_ERRORS={json.dumps(errors, ensure_ascii=False)}")
        sys.exit(1)

    print(f"✅ 门禁通过: {len(sections)} 个智能体全部有效")

    # ── 建立 agent_id -> 截图路径 查找表 ──
    screenshot_by_aid = _build_screenshot_lookup(sections)

    # ── 创建文档 ──
    doc_title = manifest.get("doc_title", "智能体市场巡检报告")
    doc_data = api("POST", "/docx/v1/documents", json={"title": doc_title})
    doc_token = doc_data["data"]["document"]["document_id"]
    doc_url = f"https://feishu.cn/docx/{doc_token}"
    print(f"📄 文档: {doc_url}")

    # ── 写摘要 ──
    summary = manifest.get("summary_text", "")
    if summary:
        blocks = [heading3_block("📊 巡检摘要"), text_block(summary), divider_block()]
        idx = get_children_count(doc_token)
        add_blocks(doc_token, blocks, idx)

    # ── 逐 agent 构建 ──
    checkpoint_path = Path(f"reports/runs/{run_id}/delivery_checkpoint.json")
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint = {"doc_token": doc_token, "run_id": run_id, "completed_indexes": [],
                  "last_agent_id": None, "updated_at": "", "state": ""}

    # 本地追踪块计数，避免每次调用分页 get_children_count
    # 初始有一个摘要块（如果 summary 非空）
    total_blocks = 0
    if summary:
        # 摘要写入后，children count = 摘要块数
        total_blocks = 3  # heading3 + text + divider

    image_count = 0
    for i, section in enumerate(sections):
        idx = section.get("inspection_index", 0)
        aid = str(section.get("agent_id", ""))
        name = section.get("agent_name", "?")
        raw_status = str(section.get("status", "?")).strip()
        text = section.get("text", "")
        elapsed = section.get("elapsed", section.get("time", 0))

        # 标准化状态 + 图标
        norm_status = normalize_status(raw_status)
        icon = STATUS_ICONS.get(norm_status, "❓")
        is_skipped = (norm_status == "SKIPPED")

        # agent_id 查找截图（非 index）
        screenshot_path = screenshot_by_aid.get(aid, "")

        # 提取结构化字段
        test_op = section.get("test_operation", "")
        test_res = section.get("test_result", "")
        test_analysis = section.get("test_analysis", "")
        if not test_op or not test_res or not test_analysis:
            lines = text.strip().split("\n") if text else []
            for line in lines:
                stripped = line.strip()
                if stripped.startswith("测试操作：") and not test_op:
                    test_op = stripped.replace("测试操作：", "")
                elif stripped.startswith("测试结果：") and not test_res:
                    test_res = stripped.replace("测试结果：", "")
                elif stripped.startswith("测试分析：") and not test_analysis:
                    test_analysis = stripped.replace("测试分析：", "")

        # ── 构建标题（带状态图标）──
        formatted_idx = f"{idx:03d}"
        title = f"{icon} {formatted_idx}. {name}"

        # ── 条件插图片 ──
        should_insert_image = (
            not is_skipped
            and screenshot_path
            and os.path.isfile(screenshot_path)
            and "_skip" not in os.path.basename(screenshot_path)
        )

        # ── 判断是否为对话型智能体 ──
        is_chat_agent = section.get("_test_type") == "chat" or section.get("category") == "chat"

        # ── 提取结构化字段 ──
        test_question = section.get("question_text") or section.get("test_question", "") or test_op.replace("对话测试: ", "")
        agent_answer = section.get("answer_text", "") or normalize_answer_text(section)
        # 从 q_results 回退提取
        if not test_question:
            qr = section.get("q_results", [])
            if qr and qr[0].get("question"):
                test_question = qr[0]["question"]

        # ── 状态优先路由 ──
        if norm_status == "SKIPPED":
            blocks = _build_skipped_section(aid, name, raw_status, norm_status, icon, idx, run_id, test_analysis, elapsed, text)
            should_insert_image = False
        elif norm_status == "BLOCKED":
            blocks = _build_blocked_section(aid, name, raw_status, norm_status, icon, idx, run_id,
                                            test_op, test_res, test_analysis, elapsed, text)
        elif norm_status == "FAIL":
            blocks = _build_failed_section(aid, name, raw_status, norm_status, icon, idx, run_id,
                                           test_op, test_res, test_analysis, elapsed, text, is_chat_agent)
        elif norm_status == "PASS":
            # ── PASS 对话证据完整性校验 ──
            if is_chat_agent:
                # 优先使用归一化后的 answer_text，兼容旧字段 agent_answer
                answer = normalize_answer_text(section)
                if not answer:
                    norm_status = "FAIL"
                    icon = STATUS_ICONS["FAIL"]
                    if not test_question:
                        test_op = "对话测试证据缺失：test_question 为空"
                    test_res = "对话测试证据缺失：未采集到智能体回答"
                    test_analysis = "巡检未采集到实际问题或智能体回答，已由PASS降级为FAIL。"
                    print(f"    ⚠️ [{formatted_idx}] {name[:25]}: chat证据缺失 → 降级为FAIL")
                else:
                    # 使用归一化后的 answer
                    agent_answer = answer
                    # 同时更新 test_question
                    if section.get("question_text"):
                        test_question = section["question_text"]
            blocks = _build_pass_section(aid, name, raw_status, norm_status, icon, idx, run_id,
                                          test_op, test_res, test_analysis, elapsed, test_question, agent_answer, is_chat_agent)
        else:
            # 未知状态 → 按通用格式处理
            blocks = _build_generic_section(aid, name, raw_status, norm_status, icon, idx, run_id,
                                            test_op, test_res, test_analysis, elapsed, text)

        if i % 10 == 0 or i == 0:
            icon_tag = "📸" if should_insert_image else ("⏭" if norm_status == "SKIPPED" else "  ")
            print(f"  {icon_tag} [{i+1}/{len(sections)}] {name[:25]} (status={norm_status})")

        # ── 在 blocks 末尾追加空图片块（如果需要插入截图）──
        if should_insert_image:
            blocks.append(text_block("截图："))
            blocks.append(empty_image_block())

        # ── 逐智能体隔离：单个失败不影响整体 ──
        try:
            added_ids = add_blocks(doc_token, blocks, total_blocks)

            if should_insert_image:
                try:
                    # 从 add_blocks 响应中获取图片块 ID（blocks 末尾是空图片块）
                    img_block_id = added_ids[-1] if added_ids else None
                    if not img_block_id:
                        raise RuntimeError("add_blocks 未返回图片块 ID")

                    _fill_image_block(doc_token, screenshot_path, img_block_id)
                    verify = verify_image_block(doc_token, img_block_id)
                    if not verify["valid"]:
                        raise RuntimeError(f"图片块验证失败: {verify['errors']}")

                    image_count += 1
                except Exception as img_err:
                    print(f"  ⚠️ [{formatted_idx}] {name[:20]} 图片插入失败（不中断）: {img_err}")
                    # 图片失败不中断，继续下一个智能体

            # ── 更新本地块计数 ──
            total_blocks += len(added_ids)

            checkpoint["completed_indexes"].append(idx)
            checkpoint["last_agent_id"] = aid
            checkpoint["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            checkpoint["build_errors"] = checkpoint.get("build_errors", [])
            checkpoint_path.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2))

        except Exception as agent_exc:
            err_info = f"Agent {aid} ({name}) 构建异常: {agent_exc}"
            print(f"  ❌ [{formatted_idx}] {name[:25]} 构建失败（已隔离）total_blocks={total_blocks}: {agent_exc}")
            # 记录错误但继续
            checkpoint.setdefault("build_errors", []).append({
                "agent_id": aid, "agent_name": name, "index": idx,
                "error": str(agent_exc), "stage": "build_section",
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            })
            checkpoint["last_agent_id"] = aid
            checkpoint["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            checkpoint_path.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2))

    # ── 最终验证 ──
    print(f"\n🔍 最终验证中...")
    all_blocks = get_all_blocks(doc_token)
    verify_errors = final_validation(all_blocks, sections)

    if verify_errors:
        print("❌ 最终验证失败:")
        for e in verify_errors:
            print(f"   {e}")
        checkpoint["state"] = "失败"
        checkpoint_path.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2))
        print(f"\nDELIVERY_STATE=失败")
        print(f"DELIVERY_ERRORS={json.dumps(verify_errors, ensure_ascii=False)}")
        sys.exit(1)

    # 最终 checkpoint
    checkpoint["state"] = "完整"
    checkpoint_path.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2))

    print(f"\n{'='*50}")
    print(f"✅ DELIVERY_STATE=完整")
    print(f"DOC_TOKEN={doc_token}")
    print(f"DOC_URL={doc_url}")
    print(f"SECTION_COUNT={len(sections)}")
    print(f"IMAGE_COUNT={image_count}")

    result = {"doc_token": doc_token, "doc_url": doc_url, "section_count": len(sections), "image_count": image_count}

    # 统计
    skipped_count = sum(1 for s in sections if normalize_status(s.get("status", "")) == "SKIPPED")
    non_skipped_with_img = sum(1 for s in sections
                               if normalize_status(s.get("status", "")) != "SKIPPED"
                               and screenshot_by_aid.get(str(s.get("agent_id", "")))
                               and os.path.isfile(screenshot_by_aid[str(s.get("agent_id", ""))]))
    print(f"SKIPPED: {skipped_count}")
    print(f"非 SKIPPED 有截图: {non_skipped_with_img}")
    print(f"实际插入图片: {image_count}")

    return result


def _block_text(block: dict) -> str:
    """提取 block 文本内容"""
    bt = block.get("block_type")
    if bt == 5:
        parts = []
        for e in block.get("heading3", {}).get("elements", []):
            parts.append(e.get("text_run", {}).get("content", ""))
        return "".join(parts)
    elif bt == 2:
        parts = []
        for e in block.get("text", {}).get("elements", []):
            parts.append(e.get("text_run", {}).get("content", ""))
        return "".join(parts)
    elif bt == 14:
        # 代码块：拼接全部 text_run 元素
        parts = []
        for e in block.get("code", {}).get("elements", []):
            parts.append(e.get("text_run", {}).get("content", ""))
        return "".join(parts)
    return ""


def final_validation(blocks: list, sections: list) -> list:
    """最终分页验证全部 blocks。

    - 智能体章节数 = sections 总数
    - 图片数 = 非 SKIPPED 且有效截图的智能体数
    - 每个 SKIPPED 章节内无图片块
    - 图片不在末尾堆叠
    """
    errors = []

    expected_sections = len(sections)
    # 计算预期图片数
    expected_images = 0
    img_aids = set()
    skipped_aids = set()
    for s in sections:
        status = normalize_status(s.get("status", ""))
        aid = str(s.get("agent_id", ""))
        if status == "SKIPPED":
            skipped_aids.add(aid)
            continue
        images = s.get("images", [])
        if images and os.path.isfile(images[0]):
            expected_images += 1
            img_aids.add(aid)

    # 找所有 agent heading3（允许状态图标前缀，匹配 ✅ 或 ⏭️ 等 emoji 后的 NNN.  格式）
    agent_headings = []
    images = []
    anchors = set()
    # 按 section 分组：找到每个 agent 的 heading 索引范围
    agent_ranges = []   # [(heading_block_index, agent_aid)]
    agent_heading_texts = []

    for b in blocks:
        bt = b.get("block_type")
        if bt == 5:
            text = _block_text(b)
            # 匹配格式：可选的 emoji/图标 + 空格 + NNN. + 空格
            if re.match(r'^[^\w\d]*\s*\d{3}\.\s', text):
                agent_headings.append(text)
                # 从 MANIFEST 匹配 agent
                agent_heading_texts.append((b, text))
        elif bt == 27:
            images.append(b)
        elif bt == 2:
            text = _block_text(b)
            if "巡检锚点：" in text:
                anchors.add(text.replace("巡检锚点：", "").strip())

    # ── 1. 标题数 ──
    if len(agent_headings) != expected_sections:
        errors.append(f"标题数({len(agent_headings)}) != 预期({expected_sections})")

    # ── 2. 图片数 ──
    if len(images) != expected_images:
        errors.append(f"图片数({len(images)}) != 预期({expected_images}，非SKIPPED有截图)")

    # ── 3. 图片全部有 token ──
    no_token = [img.get('block_id') for img in images if not img.get("image", {}).get("token")]
    if no_token:
        errors.append(f"{len(no_token)} 个图片块缺少 token: {no_token[:5]}")

    # ── 4. 图片不集中末尾 ──
    agent_heading_indices = []
    for i, b in enumerate(blocks):
        if b.get("block_type") == 5:
            text = _block_text(b)
            if re.match(r'^[^\w\d]*\s*\d{3}\.\s', text):
                agent_heading_indices.append(i)

    image_indices = [i for i, b in enumerate(blocks) if b.get("block_type") == 27]
    if agent_heading_indices and image_indices and len(agent_heading_indices) > 1:
        last_agent_h_idx = agent_heading_indices[-1]
        images_after_last_agent = sum(1 for i in image_indices if i > last_agent_h_idx)
        if images_after_last_agent == len(images) and images:
            errors.append("所有图片都在文档末尾（无交错）")

    # ── 5. 每个 SKIPPED 章节内无图片块 ──
    # 找到每个 SKIPPED agent 的 heading 区间，检查区间内是否有 image
    skipped_heading_texts = set()
    for s in sections:
        if normalize_status(s.get("status", "")) == "SKIPPED":
            name = s.get("agent_name", "?")
            # 构造可能的标题文本（含图标）
            skipped_heading_texts.add(f"⏭️ {s.get('inspection_index', 0):03d}. {name}")

    heading_idx_map = {}  # block_index -> heading_text
    for i, b in enumerate(blocks):
        if b.get("block_type") == 5:
            heading_idx_map[i] = _block_text(b)

    sorted_heading_indices = sorted(heading_idx_map.keys())
    for hi, h_text in heading_idx_map.items():
        if h_text in skipped_heading_texts:
            # 找到下一个 heading 的 index
            next_heading = None
            for si in sorted_heading_indices:
                if si > hi:
                    next_heading = si
                    break
            # 检查区间 (hi, next_heading) 内是否有 image
            for ii in image_indices:
                if hi < ii < (next_heading or len(blocks)):
                    errors.append(f"SKIPPED 章节 '{h_text[:30]}' 内发现图片块(block #{ii})")

    # ── 6. 锚点 ──
    if len(anchors) != expected_sections:
        errors.append(f"锚点数({len(anchors)}) != 预期({expected_sections})")

    # ── 7. 对话型 PASS 章节必须有 2 个 block_type=14 ──
    # 找到每个 chat + PASS 的 agent heading 区间，检查区间内是否有 2 个 code block
    skipped_section_indices = set()
    for s in sections:
        status = normalize_status(s.get("status", ""))
        if status == "SKIPPED":
            skipped_section_indices.add(s.get("inspection_index", 0))

    chat_pass_headings = []
    for i, b in enumerate(blocks):
        if b.get("block_type") == 5:
            text = _block_text(b)
            if re.match(r'^[^\w\d]*\s*\d{3}\.\s', text):
                # 找对应 section
                idx_match = re.match(r'^[^\w\d]*\s*(\d+)\.', text)
                if idx_match:
                    h_idx = int(idx_match.group(1))
                    for s in sections:
                        if s.get("inspection_index") == h_idx:
                            is_chat = s.get("_test_type") == "chat" or s.get("category") == "chat"
                            is_pass = normalize_status(s.get("status", "")) == "PASS"
                            if is_chat and is_pass:
                                chat_pass_headings.append((i, h_idx, s.get("agent_name", "?")))
                            break

    for heading_idx, aidx, aname in chat_pass_headings:
        # 找到下一个 heading
        next_heading = len(blocks)
        for si in sorted_heading_indices:
            if si > heading_idx:
                next_heading = si
                break
        # 统计区间内的 code blocks
        code_count = sum(1 for j in range(heading_idx, next_heading)
                        if blocks[j].get("block_type") == 14)
        if code_count < 2:
            errors.append(f"对话型PASS [{aname}] 缺少2个code块 (实际 {code_count} 个)")
        else:
            # 验证 code block 内容不含围栏
            for j in range(heading_idx, next_heading):
                if blocks[j].get("block_type") == 14:
                    content = _block_text(blocks[j])
                    if "```" in content or '"""' in content:
                        errors.append(f"[{aname}] code block 含围栏字符: {content[:50]}")

    return errors


def main():
    manifest_path = sys.argv[1] if len(sys.argv) > 1 else None

    if manifest_path is None:
        # 兜底：默认路径不存在或过旧时，自动检测最新 run 的 MANIFEST
        default_path = WORKSPACE / "work/agent-market/reports/MANIFEST.json"
        runs_dir = WORKSPACE / "work/agent-market/reports/runs"
        if runs_dir.is_dir():
            run_dirs = sorted(
                [d for d in runs_dir.iterdir() if d.is_dir()],
                key=lambda d: d.name, reverse=True
            )
            for rd in run_dirs:
                candidate = rd / "MANIFEST.json"
                if candidate.is_file():
                    manifest_path = str(candidate)
                    print(f"🔍 自动检测最新 MANIFEST: {manifest_path}")
                    break
        if manifest_path is None and default_path.is_file():
            manifest_path = str(default_path)
            print(f"📄 使用默认 MANIFEST: {manifest_path}")
        if manifest_path is None:
            print("❌ 找不到任何 MANIFEST.json，退出")
            return 1

    print("🚀 Feishu Doc Builder v2（三步图片流程 + 状态图标 + SKIP免图）")

    with open(manifest_path) as f:
        manifest = json.load(f)

    result = build_report(manifest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
