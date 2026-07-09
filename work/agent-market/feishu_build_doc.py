#!/usr/bin/env python3
"""
Feishu Doc Builder
读取 MANIFEST.json，通过飞书 REST API 完成全部文档构建：
1. 创建文档
2. 写入全部文字（标题、总结、Q&A 等）
3. 上传截图并插入到每个 agent 的「截图：」之后

用法: python3 feishu_build_doc.py [manifest_path]
输出: doc_url
"""

import json
import sys
import time
import io
from pathlib import Path

import httpx

# ── 配置 ──────────────────────────────────────
FEISHU_APP_ID = "cli_aac1c18a7b7a5cef"
FEISHU_APP_SECRET = _load_app_secret()
FEISHU_API_BASE = "https://open.feishu.cn/open-apis"
WORKSPACE = Path("/home/node/.openclaw/workspace")

_token_cache = None
_token_expires = 0


def _load_app_secret() -> str:
    """从 OpenClaw 配置读取飞书 App Secret"""
    config_path = Path.home() / ".openclaw/openclaw.json"
    with open(config_path) as f:
        cfg = json.load(f)
    return cfg["channels"]["feishu"]["appSecret"]


def get_token() -> str:
    global _token_cache, _token_expires
    now = time.time()
    if _token_cache and now < _token_expires - 60:
        return _token_cache
    resp = httpx.post(
        f"{FEISHU_API_BASE}/auth/v3/tenant_access_token/internal",
        json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET},
        timeout=15)
    resp.raise_for_status()
    d = resp.json()
    if d.get("code") != 0:
        raise RuntimeError(f"Token failed: {d.get('msg')}")
    _token_cache = d["tenant_access_token"]
    _token_expires = now + d.get("expire", 7200)
    print(f"  🔑 Token OK")
    return _token_cache


def api(method, path, **kw):
    token = get_token()
    h = kw.pop("headers", {})
    h["Authorization"] = f"Bearer {token}"
    resp = httpx.request(method, f"{FEISHU_API_BASE}{path}", headers=h, timeout=60, **kw)
    resp.raise_for_status()
    d = resp.json()
    if d.get("code") != 0:
        raise RuntimeError(f"API {path}: {d.get('msg')}")
    return d


def create_doc(title: str) -> str:
    """创建文档，返回 doc_token"""
    r = api("POST", "/docx/v1/documents", json={"title": title})
    return r["data"]["document"]["document_id"]


def add_blocks(doc_token: str, blocks: list, index: int = -1):
    """批量添加 block 到文档"""
    api("POST", f"/docx/v1/documents/{doc_token}/blocks/{doc_token}/children",
        json={"children": blocks, "index": index})


def upload_media(doc_token: str, file_path: str) -> str:
    """上传图片，返回 file_token"""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(file_path)
    size = path.stat().st_size
    with open(path, "rb") as f:
        data = f.read()
    token = get_token()
    resp = httpx.post(
        f"{FEISHU_API_BASE}/drive/v1/medias/upload_all",
        headers={"Authorization": f"Bearer {token}"},
        data={"file_name": path.name, "parent_type": "docx_image",
              "parent_node": doc_token, "size": str(size)},
        files={"file": (path.name, io.BytesIO(data), "image/png")},
        timeout=60)
    resp.raise_for_status()
    r = resp.json()
    if r.get("code") != 0:
        raise RuntimeError(f"Upload: {r.get('msg')}")
    return r["data"]["file_token"]


# ── Block builders ───────────────────────────
def text_block(content: str, bold: bool = False) -> dict:
    style = {"bold": True} if bold else {}
    return {
        "block_type": 2,
        "text": {"elements": [{"text_run": {"content": content, "text_element_style": style}}]}
    }


def heading1_block(content: str) -> dict:
    return {"block_type": 3, "heading1": heading_elements(content)}


def heading2_block(content: str) -> dict:
    return {"block_type": 4, "heading2": heading_elements(content)}


def heading3_block(content: str) -> dict:
    return {"block_type": 5, "heading3": heading_elements(content)}


def heading_elements(content: str) -> dict:
    return {"elements": [{"text_run": {"content": content}}]}


def divider_block() -> dict:
    return {"block_type": 22, "divider": {}}


def image_block(file_token: str) -> dict:
    return {"block_type": 27, "image": {"token": file_token}}


def code_block(content: str) -> dict:
    return {"block_type": 14, "code": {"elements": [{"text_run": {"content": content}}]}}


def build_document(manifest: dict, doc_token: str) -> int:
    """构建文档全部内容，返回截图数量"""
    blocks = []

    # Title
    blocks.append(heading1_block(manifest["doc_title"]))

    # Summary
    blocks.append(text_block(""))
    blocks.append(text_block(manifest["summary_text"]))

    # Divider
    blocks.append(divider_block())

    # Chat test section header
    blocks.append(heading2_block("🤖 对话测试详情"))
    blocks.append(text_block(""))

    # Batch write the initial structure
    if blocks:
        add_blocks(doc_token, blocks, -1)
        print(f"  📝 写入 {len(blocks)} 个 header blocks")

    # Process each agent section
    image_count = 0
    total_sections = len(manifest.get("sections", []))
    for si, section in enumerate(manifest.get("sections", [])):
        agent_blocks = []
        text = section.get("text", "")
        images = section.get("images", [])
        lines = text.strip().split("\n")

        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                agent_blocks.append(text_block(""))
            elif stripped.startswith("### "):
                agent_blocks.append(heading3_block(stripped[4:]))
            elif stripped.startswith("截图："):
                agent_blocks.append(text_block(stripped, False))
                # 记录这个位置，upload images right after
                screenshot_pos = len(agent_blocks) - 1
            elif stripped.startswith("用时："):
                agent_blocks.append(text_block(stripped, False))
            elif stripped == "---":
                agent_blocks.append(divider_block())
            elif stripped.startswith("> "):
                # quote - as plain text
                agent_blocks.append(text_block(stripped))
            elif stripped == "**总结**" or stripped.startswith("**总结"):
                agent_blocks.append(text_block(stripped, True))
            elif stripped.startswith("测试问题") or stripped.startswith("回答结果"):
                agent_blocks.append(text_block(stripped, True))
            elif stripped.startswith("⚠️"):
                agent_blocks.append(text_block(stripped, False))
            else:
                # Regular text or code content
                agent_blocks.append(text_block(stripped, False))

        # Write agent blocks in batches
        batch_size = 20  # Feishu API limit
        for bi in range(0, len(agent_blocks), batch_size):
            batch = agent_blocks[bi:bi + batch_size]
            add_blocks(doc_token, batch, -1)

        # Upload and insert images right after "截图："
        if images:
            # Need to find the "截图：" block position
            all_children = api("GET",
                f"/docx/v1/documents/{doc_token}/blocks/{doc_token}/children")
            children = all_children.get("data", {}).get("items", [])

            # Find the last "截图：" block
            marker_index = None
            for i in range(len(children) - 1, -1, -1):
                b = children[i]
                if b.get("block_type") == 2:
                    content = b.get("text", {}).get("elements", [{}])[0].get("text_run", {}).get("content", "")
                    if content.strip() == "截图：":
                        marker_index = i
                        break

            if marker_index is not None:
                for j, img_path in enumerate(images):
                    insert_pos = marker_index + 1 + j  # +1 after marker, +j for multiple images
                    try:
                        file_token = upload_media(doc_token, img_path)
                        add_blocks(doc_token, [image_block(file_token)], insert_pos)
                        image_count += 1
                        print(f"  🖼️  [{image_count}] {Path(img_path).name} at index={insert_pos}")
                    except Exception as e:
                        print(f"  ⚠️  [{image_count}] {Path(img_path).name} FAILED: {e}")
            else:
                print(f"  ⚠️  未找到「截图：」block for section {si}")

        print(f"  [{si+1}/{total_sections}] {section.get('agent_name', '?')} ({len(agent_blocks)} blocks)")

    return image_count


def main():
    manifest_path = sys.argv[1] if len(sys.argv) > 1 else str(
        WORKSPACE / "work/agent-market/reports/MANIFEST.json")

    print("🚀 Feishu Doc Builder")

    with open(manifest_path) as f:
        manifest = json.load(f)

    doc_title = manifest["doc_title"]
    print(f"   Title: {doc_title}")

    # Create doc
    doc_token = create_doc(doc_title)
    print(f"   📄 Doc: {doc_token}")

    # Build content
    image_count = build_document(manifest, doc_token)

    # Output
    url = f"https://feishu.cn/docx/{doc_token}"
    print(f"\n✅ 完成！")
    print(f"   Blocks: text + {image_count} images")
    print(f"   URL: {url}")
    print(f"\nDOC_URL={url}")
    print(f"DOC_TOKEN={doc_token}")


if __name__ == "__main__":
    main()
