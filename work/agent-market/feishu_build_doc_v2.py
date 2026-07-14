#!/usr/bin/env python3
"""
Feishu Doc Builder v2 — 纯指令生成器（不操作飞书 API）

功能：
1. 加载 MANIFEST.json
2. 校验所有截图（存在、PNG、元数据一致）
3. 对每个智能体生成文字 markdown + 截图路径
4. 输出 INSTRUCTIONS.json 供 cron agent 逐 section 执行：
   feishu_doc append(doc_token, text_markdown)
   feishu_doc upload_image(doc_token, file_path)

用法: python3 feishu_build_doc_v2.py [manifest_path]
输出: INSTRUCTIONS_PATH + SUMMARY
"""

import hashlib
import json
import os
import sys
from pathlib import Path


WORKSPACE = Path("/home/node/.openclaw/workspace")


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


def build_instructions(manifest: dict) -> dict:
    """校验 manifest 并生成逐 agent 指令"""
    sections = [s for s in manifest.get("sections", []) if s.get("id", "").startswith("agent_")]

    if not sections:
        raise RuntimeError("MANIFEST 中无 agent section")

    # ── 硬门禁 ──
    errors = []
    seen_paths = set()
    for s in sections:
        idx = s.get("inspection_index", -1)
        aid = s.get("agent_id", -1)
        images = s.get("images", [])
        if not images:
            errors.append(f"Agent {idx} (ID={aid}): 无截图绑定")
            continue
        img = images[0]
        if not os.path.isfile(img):
            errors.append(f"Agent {idx} (ID={aid}): 截图不存在 {img}")
            continue
        if not _is_valid_png(img):
            errors.append(f"Agent {idx} (ID={aid}): 非有效 PNG")
            continue
        meta_path = Path(img).with_suffix(".json")
        if meta_path.is_file():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if str(meta.get("agent_id", "")) != str(aid):
                errors.append(f"Agent {idx} (ID={aid}): 元数据 agent_id 不匹配")
            if meta.get("inspection_index") != idx:
                errors.append(f"Agent {idx} (ID={aid}): 元数据 inspection_index 不匹配")
        if img in seen_paths:
            errors.append(f"Agent {idx} (ID={aid}): 截图路径重复")
        seen_paths.add(img)

    if errors:
        print("❌ 投递前门禁失败:")
        for e in errors:
            print(f"   {e}")
        raise RuntimeError(f"门禁失败: {len(errors)} 个错误")

    print(f"✅ 门禁通过: {len(sections)} agents, 所有截图有效")

    # ── 生成指令 ──
    instructions = []
    for s in sections:
        idx = s.get("inspection_index", 0)
        aid = s.get("agent_id", 0)
        name = s.get("agent_name", "?")
        status = s.get("status", "?")
        text = s.get("text", "")
        images = s.get("images", [])
        img_path = images[0] if images else ""

        # 构建 markdown 文本
        status_icon = {"chat_ok": "✅", "skipped": "⏭", "unreachable": "❌"}.get(status, "🟠")
        md_lines = [f"### {status_icon} {name}（ID：{aid}）", ""]

        raw_lines = text.strip().split("\n")
        for line in raw_lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("### "):
                continue
            if stripped == "截图：" or stripped == "📷 截图：":
                continue  # 后面用图片块代替
            md_lines.append(stripped)
            md_lines.append("")

        md_lines.append("📷 截图：")
        md_text = "\n".join(md_lines)

        instructions.append({
            "inspection_index": idx,
            "agent_id": aid,
            "agent_name": name,
            "status": status,
            "markdown": md_text,
            "image_path": img_path,
            "image_sha256": _sha256(img_path) if img_path else "",
        })

    # ── 汇总 ──
    summary_text = manifest.get("summary_text", "")
    doc_title = manifest.get("doc_title", "智能体市场巡检报告")
    stats = {}
    for inst in instructions:
        st = inst["status"]
        stats[st] = stats.get(st, 0) + 1

    return {
        "doc_title": doc_title,
        "summary": summary_text,
        "agent_count": len(instructions),
        "stats": stats,
        "instructions": instructions,
    }


def main():
    manifest_path = sys.argv[1] if len(sys.argv) > 1 else str(
        WORKSPACE / "work/agent-market/reports/MANIFEST.json"
    )

    print("🚀 Feishu Doc Builder v2（指令生成模式）")

    with open(manifest_path) as f:
        manifest = json.load(f)

    result = build_instructions(manifest)

    output_path = WORKSPACE / "work/agent-market/reports/INSTRUCTIONS.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2))

    print(f"\n📦 指令文件: {output_path}")
    print(f"📊 Agent 数: {result['agent_count']}")
    print(f"📊 状态分布: {result['stats']}")
    print(f"\nINSTRUCTIONS_PATH={output_path}")
    print("DELIVERY_STATE=指令就绪")
    return 0


if __name__ == "__main__":
    sys.exit(main())
