#!/usr/bin/env python3
"""
Feishu Image Inserter
读取 MANIFEST.json，通过飞书 REST API:
1. 列出文档 blocks
2. 找到「截图：」文字块的位置
3. 上传截图到飞书 drive
4. 在「截图：」之后插入图片 block

用法: python3 feishu_insert_images.py <doc_token> [manifest_path]
"""

import json
import os
import sys
import time
from pathlib import Path

import httpx

# ── 配置 ──────────────────────────────────────
FEISHU_APP_ID = "cli_aac1c18a7b7a5cef"
FEISHU_APP_SECRET = None
FEISHU_API_BASE = "https://open.feishu.cn/open-apis"
WORKSPACE = Path("/home/node/.openclaw/workspace")

def _load_app_secret() -> str:
    """从 OpenClaw 配置读取飞书 App Secret"""
    config_path = Path.home() / ".openclaw/openclaw.json"
    with open(config_path) as f:
        cfg = json.load(f)
    return cfg["channels"]["feishu"]["appSecret"]


# ── Token ─────────────────────────────────────
_token_cache = None
_token_expires = 0


def get_tenant_token() -> str:
    global _token_cache, _token_expires, FEISHU_APP_SECRET
    if not FEISHU_APP_SECRET:
        FEISHU_APP_SECRET = _load_app_secret()
    now = time.time()
    if _token_cache and now < _token_expires - 60:
        return _token_cache

    resp = httpx.post(
        f"{FEISHU_API_BASE}/auth/v3/tenant_access_token/internal",
        json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"获取 token 失败: {data.get('msg')}")
    _token_cache = data["tenant_access_token"]
    _token_expires = now + data.get("expire", 7200)
    print(f"  🔑 Token 已获取，有效期 {data.get('expire')}s")
    return _token_cache


def api(method: str, path: str, **kwargs) -> dict:
    """调用飞书 API，自动附带 token"""
    token = get_tenant_token()
    headers = kwargs.pop("headers", {})
    headers["Authorization"] = f"Bearer {token}"
    resp = httpx.request(
        method, f"{FEISHU_API_BASE}{path}",
        headers=headers, timeout=60, **kwargs
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"API 错误 {path}: {data.get('msg')}")
    return data


def list_blocks(doc_token: str) -> list:
    """获取文档全部根级 blocks"""
    print(f"  📋 获取文档 blocks...")
    blocks = api("GET", f"/docx/v1/documents/{doc_token}/blocks/{doc_token}/children")
    items = blocks.get("data", {}).get("items", [])
    print(f"    共 {len(items)} 个根级 block")
    return items


def find_screenshot_blocks(blocks: list) -> list:
    """找到 text 内容为「截图：」的 block 及其在 siblings 中的 index"""
    results = []
    for i, block in enumerate(blocks):
        bt = block.get("block_type", 0)
        if bt == 2:  # text block
            elements = block.get("text", {}).get("elements", [])
            if elements:
                content = elements[0].get("text_run", {}).get("content", "")
                if content.strip() == "截图：":
                    results.append({"block_id": block["block_id"], "index": i})
    print(f"    找到 {len(results)} 个「截图：」block")
    return results


def upload_image(doc_token: str, file_path: str) -> str:
    """上传图片到飞书 drive，返回 file_token"""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"图片不存在: {file_path}")

    file_size = path.stat().st_size
    print(f"    📤 上传: {path.name} ({file_size//1024}KB)")

    import io as _io
    with open(path, "rb") as f:
        file_content = f.read()

    token = get_tenant_token()
    resp = httpx.post(
        f"{FEISHU_API_BASE}/drive/v1/medias/upload_all",
        headers={"Authorization": f"Bearer {token}"},
        data={
            "file_name": path.name,
            "parent_type": "docx_image",
            "parent_node": doc_token,
            "size": str(file_size),
        },
        files={"file": (path.name, _io.BytesIO(file_content), "image/png")},
        timeout=60,
    )
    resp.raise_for_status()
    result = resp.json()
    if result.get("code") != 0:
        raise RuntimeError(f"上传失败: {result.get('msg')}")
    file_token = result["data"]["file_token"]
    return file_token


def insert_image_block(doc_token: str, file_token: str, after_index: int):
    """在指定位置插入图片 block（index = 插入位置）"""
    children = [{
        "block_type": 27,
        "image": {"token": file_token},
    }]

    api(
        "POST",
        f"/docx/v1/documents/{doc_token}/blocks/{doc_token}/children",
        json={"children": children, "index": after_index},
    )
    print(f"      ✅ 插入成功 at index={after_index}")


def main():
    if len(sys.argv) < 2:
        print("用法: python3 feishu_insert_images.py <doc_token> [manifest_path]")
        sys.exit(1)

    doc_token = sys.argv[1]
    manifest_path = sys.argv[2] if len(sys.argv) > 2 else str(
        WORKSPACE / "work/agent-market/reports/MANIFEST.json"
    )

    print(f"🚀 Feishu Image Inserter")
    print(f"   doc_token: {doc_token}")

    # 1. 读取 MANIFEST
    with open(manifest_path) as f:
        manifest = json.load(f)

    # 收集所有截图路径（按文档顺序）
    image_paths = []
    for section in manifest.get("sections", []):
        for img_path in section.get("images", []):
            image_paths.append(img_path)

    if not image_paths:
        print("   ⚠️ 没有截图需要插入")
        return

    print(f"   📸 共 {len(image_paths)} 张截图")

    # 2. 获取文档 blocks
    blocks = list_blocks(doc_token)

    # 3. 找「截图：」位置
    markers = find_screenshot_blocks(blocks)

    if len(markers) != len(image_paths):
        raise RuntimeError(
            f"截图绑定门禁失败：标记数({len(markers)}) != 截图数({len(image_paths)})；"
            "为防止图片错配，本次禁止插入。请使用 feishu_build_doc.py 按 MANIFEST section 绑定上传。"
        )
    count = len(markers)

    # 4. 从后往前插入（保证前面的 index 不被影响）
    for i in range(count - 1, -1, -1):
        marker = markers[i]
        img_path = image_paths[i]
        insert_pos = marker["index"] + 1  # 紧跟在「截图：」之后

        print(f"   [{i+1}/{count}] {Path(img_path).name} → index={insert_pos}")

        try:
            file_token = upload_image(doc_token, img_path)
            insert_image_block(doc_token, file_token, insert_pos)
        except Exception as e:
            print(f"   ❌ 失败: {e}")
            continue

    print(f"  ✅ 完成，共插入 {count} 张图片")


if __name__ == "__main__":
    main()
