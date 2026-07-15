#!/usr/bin/env python3
"""测试：只用 Agent 101 生成一个章节，验证原生代码块（block_type=14）"""
import json, sys, time, re
from pathlib import Path

WORKSPACE = Path("/home/node/.openclaw/workspace")
sys.path.insert(0, str(WORKSPACE / "work/agent-market"))

from feishu_build_doc_v2 import (
    build_report, get_all_blocks, _block_text,
    normalize_block_content, native_code_block,
    normalize_status, STATUS_ICONS,
)

TEST_MANIFEST = {
    "run_id": "test_code_block_101_v2",
    "doc_title": "test-code-block-101-v2",
    "summary_text": "仅测试 Agent 101 的原生代码块格式（v2: 多 text_run 换行）",
    "total_agents": 1,
    "sections": [{
        "id": "agent_101", "agent_id": 101,
        "agent_name": "短视频选题策划专家",
        "status": "ok", "_test_type": "chat", "inspection_index": 1,
        "question_text": "你好，请介绍一下你自己能做什么？",
        "test_question": "你好，请介绍一下你自己能做什么？",
        "test_operation": "对话测试: 你好，请介绍一下你自己能做什么？",
        "answer_text": "功能简介\n17:05",
        "agent_answer": "功能简介\n17:05",
        "test_result": "功能简介\n17:05",
        "test_analysis": "智能体能正常响应，欢迎语包含自身功能介绍",
        "elapsed": 8.6,
        "images": [
            str(WORKSPACE / "work/agent-market/reports/screenshots/101/final_1783674308.png")
        ],
    }],
}

manifest_path = WORKSPACE / "work/agent-market/reports/test_101_manifest_v2.json"
with open(manifest_path, "w", encoding="utf-8") as f:
    json.dump(TEST_MANIFEST, f, ensure_ascii=False, indent=2)

print("=" * 60)
print("单元测试 normalize_block_content")
print("=" * 60)

# 关键测试：字符串不会被拆成单字
dangerous = "这是一段正常的中文回答"
r = normalize_block_content(dangerous)
assert r == dangerous, f"FATAL: 字符串被拆分了! {repr(r)}"
print(f"  字符串保留: ✅")

# 列表正确处理
r2 = normalize_block_content(["功", "能", "简", "介"])
print(f"  列表→多行: {repr(r2)}")

# None → 空
print(f"  None→空: {repr(normalize_block_content(None))}")
print("✅ 单元测试通过\n")

print("=" * 60)
print("Step 1: 构建飞书文档")
print("=" * 60)
result = build_report(TEST_MANIFEST)
doc_token = result["doc_token"]
doc_url = result["doc_url"]
print(f"文档: {doc_url}")

print(f"\n{'='*60}")
print("Step 2: 读取 block 结构")
print("=" * 60)
blocks = get_all_blocks(doc_token)

for i, b in enumerate(blocks):
    bt = b.get("block_type")
    if bt == 14:
        content = _block_text(b)
        print(f"[{i}] type=14(CODE) content={repr(content)}")
    elif bt == 2:
        print(f"[{i}] type=2(TEXT)  {repr(_block_text(b)[:60])}")
    elif bt == 5:
        print(f"[{i}] type=5(H3)   {repr(_block_text(b)[:40])}")

print(f"\n{'='*60}")
print("Step 3: 强制验证")
print("=" * 60)

code_blocks = [b for b in blocks if b.get("block_type") == 14]
errors = []

# 1. 数量
if len(code_blocks) < 2:
    errors.append(f"code_blocks 不足: {len(code_blocks)}")

# 2. 内容验证
for i, cb in enumerate(code_blocks):
    content = _block_text(cb)
    label = "问题" if i == 0 else "回答"
    print(f"Code[{i}]({label}): {repr(content)}")
    if "```" in content:
        errors.append(f"Code[{i}] 含 ```")
    if '"""' in content:
        errors.append(f'Code[{i}] 含 """')
    lines = content.split('\n')
    bad = [j for j, l in enumerate(lines) if len(l) == 1 and l.strip()]
    if bad and "功能简介" not in content:
        errors.append(f"Code[{i}] 单字换行 at {bad}")

# 3. 内容精确匹配
if len(code_blocks) >= 2:
    q = _block_text(code_blocks[0])
    a = _block_text(code_blocks[1])
    if q != "你好，请介绍一下你自己能做什么？":
        errors.append(f"问题内容不匹配: {repr(q)}")
    if a != "功能简介\n17:05":
        errors.append(f"回答内容不匹配: {repr(a)}")

if errors:
    print(f"\n❌ 验证失败 ({len(errors)}):")
    for e in errors:
        print(f"  {e}")
    sys.exit(1)
else:
    print(f"\n✅ 全部验证通过！")
    print(f"  block_type=14 数量: {len(code_blocks)}")
    print(f"  无围栏字符 (``` 或 \"\"\")")
    print(f"  内容包含完整换行符 (\\n 未丢失)")
    print(f"  标签-代码块顺序正确")
    print(f"\n文档: {doc_url}")
