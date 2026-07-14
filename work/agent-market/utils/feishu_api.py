"""
飞书文档 API 封装 —— 供 inspect_daily.py 直接创建巡检报告文档。

基于 Feishu Open API (docx blocks) + drive/media upload。
"""
import json
import os
import time
import hmac
import hashlib
from pathlib import Path
from typing import Optional

import urllib.request
import urllib.error

FEISHU_APP_ID = "cli_aac1c18a7b7a5cef"
TOKEN_CACHE = Path(__file__).parent.parent / ".feishu_token.json"


def _get_app_secret() -> str:
    for fp in [
        Path(os.path.expanduser("~/.openclaw/openclaw.json")),
        Path("/home/node/.openclaw/openclaw.json"),
    ]:
        if fp.is_file():
            cfg = json.loads(fp.read_text())
            secret = (
                cfg.get("channels", {}).get("feishu", {}).get("appSecret", "")
                or cfg.get("feishu", {}).get("app_secret", "")
            )
            if secret:
                return secret
    raise RuntimeError("未找到飞书 app_secret")


def _get_tenant_token() -> str:
    if TOKEN_CACHE.exists():
        try:
            c = json.loads(TOKEN_CACHE.read_text())
            if c.get("expires_at", 0) > time.time() + 60:
                return c["token"]
        except Exception:
            pass
    secret = _get_app_secret()
    data = json.dumps({"app_id": FEISHU_APP_ID, "app_secret": secret}).encode()
    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        body = json.loads(r.read())
        token = body["tenant_access_token"]
        expire = body.get("expire", 7200)
        TOKEN_CACHE.write_text(
            json.dumps({"token": token, "expires_at": time.time() + expire})
        )
        return token


def _api(method, path, data=None, headers=None) -> dict:
    token = _get_tenant_token()
    h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"}
    if headers:
        h.update(headers)
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(f"https://open.feishu.cn{path}", data=body, headers=h, method=method)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                return json.loads(raw) if raw else {"code": 0}
        except urllib.error.HTTPError as e:
            err = json.loads(e.read().decode(errors="replace"))
            if err.get("code") == 99991663:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"Feishu API error {path}: {err.get('code')} {err.get('msg','')}")


# ── Markdown → Feishu Blocks ──

BLOCK_TEXT = 2
BLOCK_H2 = 4
BLOCK_H3 = 5
BLOCK_H4 = 6
BLOCK_DIVIDER = 9
BLOCK_IMAGE = 27
BLOCK_TABLE = 31


def _text_element(content: str) -> dict:
    return {"text_run": {"content": content}}


def _md_to_blocks(md: str) -> list[dict]:
    """简单 Markdown → Feishu blocks。支持 ## / ### / --- / 普通段落。"""
    blocks = []
    lines = md.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]

        # 空行：跳过
        if not line.strip():
            i += 1
            continue

        # 水平线
        if line.strip() in ("---", "***", "___"):
            blocks.append({"block_type": BLOCK_DIVIDER, "divider": {}})
            i += 1
            continue

        # 标题
        if line.startswith("## "):
            blocks.append({"block_type": BLOCK_H2, "heading2": {"elements": [_text_element(line[3:])], "style": {}}})
            i += 1
            continue
        if line.startswith("### "):
            blocks.append({"block_type": BLOCK_H3, "heading3": {"elements": [_text_element(line[4:])], "style": {}}})
            i += 1
            continue
        if line.startswith("#### "):
            blocks.append({"block_type": BLOCK_H4, "heading4": {"elements": [_text_element(line[5:])], "style": {}}})
            i += 1
            continue

        # 表格检测（跳过，太复杂，统一当段落处理）
        if line.strip().startswith("|"):
            i += 1
            continue

        # 普通段落
        para_lines = []
        while i < len(lines) and lines[i].strip() and not lines[i].startswith(("#", "|")):
            para_lines.append(lines[i])
            i += 1
        if para_lines:
            text = "\n".join(para_lines)
            blocks.append({"block_type": BLOCK_TEXT, "text": {"elements": [_text_element(text)], "style": {}}})

    return blocks


# ── Public API ──

def create_document(title: str, folder_token: str = "") -> str:
    result = _api("POST", "/open-apis/docx/v1/documents", data={"title": title, **(dict(folder_token=folder_token) if folder_token else {})})
    token = result["data"]["document"]["document_id"]
    print(f"  📄 文档已创建: {title} ({token})")
    return token


def write_markdown(doc_token: str, markdown: str):
    """将 Markdown 写入飞书文档（追加模式，不含图片）。"""
    blocks = _md_to_blocks(markdown)
    if not blocks:
        return
    _api(
        "POST",
        f"/open-apis/docx/v1/documents/{doc_token}/blocks/{doc_token}/children",
        data={"children": blocks[:200]},
    )


def write_section(doc_token: str, markdown: str, image_paths: list[str] = None) -> int:
    """将一个 section 的文字和截图一次性写入文档末尾，保证顺序正确。

    流程：markdown → blocks → 上传图片获取 token → 追加 image blocks → 一次 API 创建。
    返回创建的 block 数量。
    """
    blocks = _md_to_blocks(markdown)
    if not blocks and not image_paths:
        return 0

    # 上传图片，收集 image blocks
    image_blocks = []
    if image_paths:
        for fp in image_paths:
            if not os.path.isfile(fp):
                continue
            file_token = _upload_media(doc_token, fp)
            if file_token:
                image_blocks.append({
                    "block_type": BLOCK_IMAGE,
                    "image": {"token": file_token, "width": 600},
                })

    all_blocks = blocks + image_blocks
    if not all_blocks:
        return 0

    # 分批写入（API 限制约 50-100 block/次）
    chunk_size = 40
    for start in range(0, len(all_blocks), chunk_size):
        chunk = all_blocks[start : start + chunk_size]
        _api(
            "POST",
            f"/open-apis/docx/v1/documents/{doc_token}/blocks/{doc_token}/children",
            data={"children": chunk},
        )
        if len(all_blocks) > chunk_size:
            time.sleep(0.3)

    return len(all_blocks)


def _upload_media(doc_token: str, file_path: str) -> Optional[str]:
    """上传图片到飞书，返回 file_token。不插入文档（由 write_section 统一插入）。"""
    token = _get_tenant_token()
    size = os.path.getsize(file_path)
    filename = os.path.basename(file_path)
    boundary = "----FormBoundary" + hashlib.md5(str(time.time()).encode()).hexdigest()[:12]

    with open(file_path, "rb") as f:
        raw = f.read()

    # 标准 multipart/form-data 格式
    body_lines = []
    body_lines.append(f"--{boundary}")
    body_lines.append(f'Content-Disposition: form-data; name="file"; filename="{filename}"')
    body_lines.append("Content-Type: application/octet-stream")
    body_lines.append("")
    body_bytes = "\r\n".join(body_lines).encode() + b"\r\n"
    body_bytes += raw + b"\r\n"
    body_bytes += f"--{boundary}\r\n".encode()
    body_bytes += f'Content-Disposition: form-data; name="file_name"\r\n\r\n{filename}\r\n'.encode()
    body_bytes += f"--{boundary}\r\n".encode()
    body_bytes += f'Content-Disposition: form-data; name="parent_type"\r\n\r\ndocx_image\r\n'.encode()
    body_bytes += f"--{boundary}\r\n".encode()
    body_bytes += f'Content-Disposition: form-data; name="parent_node"\r\n\r\n{doc_token}\r\n'.encode()
    body_bytes += f"--{boundary}\r\n".encode()
    body_bytes += f'Content-Disposition: form-data; name="size"\r\n\r\n{size}\r\n'.encode()
    body_bytes += f"--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/drive/v1/medias/upload_all",
        data=body_bytes,
        headers={"Authorization": f"Bearer {token}", "Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                result = json.loads(r.read())
                if result.get("code") != 0:
                    print(f"    ⚠️ upload error: {result.get('msg','')}")
                    return None
                return result["data"]["file_token"]
        except Exception as e:
            if attempt < 2:
                time.sleep(3)
            else:
                print(f"    ⚠️ upload failed: {e}")
    return None
