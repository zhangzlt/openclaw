---
name: "agent-market-inspection"
description: "Agent Market 每日健康巡检：全量智能体 API 采集 + Playwright 对话测试 + LLM 评估 + 截图 + 飞书群投递"
---

# Agent Market 每日健康巡检

对 Agent Market 平台 41 个智能体执行每日健康巡检：API 数据采集 → Playwright 对话测试 → 自动截图 → LLM 问答评估 → 飞书群投递（含截图附件）。

## 架构总览

```
┌─────────────────────────────────────────────────┐
│           OpenClaw Cron (每日 09:00)             │
│  Job ID: 25d841bb-d50a-426e-8146-cccabc97821c  │
│  Model: vllm36/Qwen3.6-35B-A3B                  │
│  Session: isolated                              │
│  超时: 3600s                                    │
└──────────────────┬──────────────────────────────┘
                   │ agentTurn
                   ▼
┌─────────────────────────────────────────────────┐
│              inspect_daily.py                    │
│  Step 1: Token 缓存验证 → API 登录              │
│  Step 2: API 采集 41 个智能体数据               │
│  Step 3: 生成 API 统计报告                       │
│  Step 4: Playwright 对话测试 (21 个)             │
│          ├─ LLM 生成测试问题 (vLLM)              │
│          ├─ contentEditable 发送消息             │
│          ├─ 📸 page.screenshot() 截图            │
│          └─ LLM 评估回复质量 (vLLM)              │
│  Step 5: 合并报告 → reports/*.md                 │
└──────────────────┬──────────────────────────────┘
                   │ 读取报告
                   ▼
┌─────────────────────────────────────────────────┐
│            cron agent (消息投递)                  │
│  ① API 统计简洁汇总 (2-3 句)                     │
│  ② 逐个智能体发送: 问题+回答+截图附件             │
│  Target: 飞书群 oc_bef4f48...                    │
└─────────────────────────────────────────────────┘
```

## Cron 任务配置

### 完整 payload

```json
{
  "jobId": "25d841bb-d50a-426e-8146-cccabc97821c",
  "name": "Agent Market 每日健康巡检",
  "schedule": { "kind": "cron", "expr": "0 9 * * *", "tz": "Asia/Shanghai" },
  "sessionTarget": "isolated",
  "delivery": {
    "mode": "announce",
    "channel": "feishu",
    "to": "oc_bef4f48fb4870602342af652e5501d86"
  },
  "payload": {
    "kind": "agentTurn",
    "model": "vllm36/Qwen3.6-35B-A3B",
    "timeoutSeconds": 3600
  }
}
```

### 定时任务提示词（agentTurn message）

```
现在是每天 9:00 的 Agent Market 健康巡检时间。请执行以下步骤：

1. 运行巡检脚本：
   CHAT_TEST=1 CHAT_TEST_ALL=1 python3 -u /home/node/.openclaw/workspace/work/agent-market/inspect_daily.py
   - 如果脚本超过 30 分钟未完成，kill 它，直接用已有报告

2. 读取报告文件 work/agent-market/reports/agent-health-report-20260708.md

3. 发送消息：使用 message 工具（channel=feishu）发送：

   第一条消息（text only）：API 基础统计简洁汇总（2-3 句话 + 问题智能体列表），格式随意

   后续消息：对话测试结果详情。逐个智能体发送，每条消息格式：

   智能体：{名称} (ID: {id})
   测试问题1：{问题}
   回答结果1：{回答}
   测试问题2：{问题}
   回答结果2：{回答}

   每条消息用 attachments 参数附带对应的截图 PNG 文件，类型为 image

   ⚠️ 注意：
   - 不要总结、不要省略回答内容！
   - 截图必须作为图片附件发送（attachments 参数），不要只发路径
   - API 统计部分用 2-3 句话概括就行
```

## 文件清单

| 文件 | 说明 |
|------|------|
| `work/agent-market/inspect_daily.py` | 主巡检脚本 |
| `work/agent-market/utils/llm.py` | LLM 工具：生成测试问题 + 评估回复 |
| `work/agent-market/config.py` | 配置（飞书 app 凭证） |
| `work/agent-market/.auth/token.txt` | API token 缓存（~30 天有效） |
| `work/agent-market/.auth/playwright_state.json` | 飞书 QR 扫码登录态 |
| `work/agent-market/.auth/market_state.json` | Agent Market 登录态 |
| `work/agent-market/reports/agent-health-report-{日期}.md` | 巡检报告 |
| `work/agent-market/reports/screenshots/{id}/q{n}.png` | 对话截图 |

## 认证体系

### Agent Market API（token 缓存优先）
```
IT Code: zhangzlt
密码:    Zzl.20041006
Token 缓存: .auth/token.txt
```

脚本 `get_token()` 逻辑：
1. 检查 `.auth/token.txt` 是否存在 → HTTP 验证（`GET /api/agents/market`）
2. 有效 → 直接使用（cron 快速模式，无 Playwright 开销）
3. 失效 → Playwright 登录 market → 拦截 Bearer JWT → 写缓存

### 飞书聊天测试（QR 码登录态）
```
手机: 17265205125
密码: zzl20041006 ❌ 被 API 拒绝，不可用
替代: QR 码扫码 → .auth/playwright_state.json
时效: 约 2-4 周后过期
```

刷新登录态：
```bash
cd work/agent-market && python3 feishu_login.py
```

## 巡检脚本执行流程

### 完整命令
```bash
# 全量对话测试 + 截图
cd /home/node/.openclaw/workspace/work/agent-market
CHAT_TEST=1 CHAT_TEST_ALL=1 python3 -u inspect_daily.py

# 仅 API 采集（3 秒）
python3 inspect_daily.py

# 轮询模式（每批 5 个）
CHAT_TEST=1 CHAT_TEST_BATCH=5 python3 inspect_daily.py
```

### Step 1: Token 获取
```
检查 .auth/token.txt → HTTP 验证 → 有效/重新登录
```

### Step 2: API 数据采集
```
GET /api/agents/market → 41 个智能体
统计: 下载/点赞/评分/指南/评价
```

### Step 3: API 报告
```
概览 → 问题智能体 → 分类列表
```

### Step 4: Playwright 对话测试
```
对每个对话型智能体:
  1. 导航到聊天 URL (feishuapp 或 aily)
  2. 权限检查 (No permission to use → 标记跳过)
  3. LLM 生成 2 个测试问题
  4. 逐题:
     a. contentEditable 输入框: .click() → .type() → .press("Enter")
     b. 等待 10s
     c. _parse_chat_reply() 解析回复 (body diff)
     d. page.screenshot() 保存 PNG
  5. LLM 评估回复质量 (评分 + 问题)
```

### Step 5: 合并输出
```
reports/agent-health-report-{日期}.md
reports/screenshots/{agent_id}/q{n}.png
```

## 关键函数说明

### `get_token()` — Token 管理
```python
# 优先缓存验证，失败才 Playwright 登录
# 缓存文件: .auth/token.txt
```

### `_is_chat_agent(agent)` — 对话型智能体筛选
```python
匹配: feishuapp.cn/ai/gui/chat/a_xxx    (5 个)
      aily.feishu.cn/agents/agent_xxx    (16 个)
排除: applink.feishu.cn                   (2 个，需客户端)
      空 URL / 内部系统 URL               (18 个)
```

### `run_chat_tests()` — Playwright 浏览器测试
```python
# 核心调用链:
# 1. 加载 playwright_state.json (飞书登录态)
# 2. 逐个 agent: page.goto(chat_url) → 找 contenteditable → 发消息
# 3. 平台统一: feishuapp 和 aily 都用 DIV[contenteditable="true"]
# 4. 截图: page.screenshot() → reports/screenshots/{id}/q{n}.png
```

### `_parse_chat_reply()` — 回复解析
```python
# body_before vs body_after → diff 出新内容
# 过滤 UI 元素: /, 新对话, Deep Planning, Tools, Copy, Invite & Earn
# 过滤元数据: Based on 来源块, 智能检索 前缀
```

### `generate_full_report()` — 报告生成
```python
# 三部分组成:
# 1. API 统计 (概览 + 问题列表 + 分类)
# 2. 对话测试详情 (逐项展示 + 截图引用)
# 3. 测试结果详情 (紧凑汇总格式)
```

## 对话型智能体完整清单

### feishuapp.cn 平台（5 个）
| ID | 名称 | Chat URL |
|----|------|----------|
| 110 | 折扣问答小助手 | `bba12hub36.feishuapp.cn/ai/gui/chat/a_3687bf8` |
| 109 | CTC智能客服 | `bba12hub36.feishuapp.cn/ai/gui/chat/a_eb9c4b2` |
| 83 | 新海量采购系统智能助手 | `bba12hub36.feishuapp.cn/ai/gui/chat/a_c0021ea` |
| 74 | 电子签章智能问答助手 | `bba12hub36.feishuapp.cn/ai/gui/chat/a_1f46a3e` |
| 73 | EB智能客服机器人 | `bba12hub36.feishuapp.cn/ai/gui/chat/a_ea846e9` |

### aily.feishu.cn 平台（16 个）
| ID | 名称 |
|----|------|
| 122 | MES 2.0 数据查询助手 |
| 119 | 业务签约法人体智能推荐 |
| 115 | DI问答助手 |
| 108 | 有问妙答-营销管理部 |
| 106 | 📝 职场文案速写·全能版 |
| 105 | Agent上架助手 |
| 101 | 短视频选题策划专家 |
| 99 | 美金商务工作/销售开单解答助手 |
| 95 | 职场解忧大师 |
| 92 | 企业文化活动策划助手 |
| 85 | 高效会议评估 |
| 82 | 小新老师沟通课 |
| 79 | Figma产品原型创建助手 |
| 78 | 项目复盘顾问 |
| 76 | 神州问学知识库回答助手 |
| 72 | 文档差异与风险分析专家 |

### 排除（2 个 applink）
- [90] 客户小助手（订阅）
- [86] 售前项目管理专家

## 投递消息格式

### 第一条：API 统计（简洁）
```
🌿 Agent Market 巡检 - 2026-07-08

📊 API 统计：
・41 个智能体，总下载 796，总点赞 92
・2 个缺指南 | 31 个无评价 | 1 个零下载

⚠️ 问题智能体：
・ID 81 问学超级员工（无指南/0 下载）
・ID 63 客户信息查询小助手（无指南）
```

### 后续：对话测试详情（逐个智能体）
```
🤖 智能体：DI问答助手 (ID: 115)

测试问题1：请介绍一下你自己
回答结果1：I'm DI问答助手, your BI hotline assistant provided by 神州数码...

测试问题2：BI账号如何申请？
回答结果2：智能检索：神州数码 BI账号申请流程 1. 登录...

[截图作为图片附件]
```

## 操作命令速查

```bash
# 手动触发 cron
/cron run force 25d841bb-d50a-426e-8146-cccabc97821c

# 手动跑脚本（全量）
cd work/agent-market && CHAT_TEST=1 CHAT_TEST_ALL=1 python3 -u inspect_daily.py

# 手动跑脚本（仅 API）
cd work/agent-market && python3 inspect_daily.py

# 刷新飞书登录态
cd work/agent-market && python3 feishu_login.py

# 查看截图
ls -la work/agent-market/reports/screenshots/*/

# 查看最新报告
cat work/agent-market/reports/agent-health-report-20260708.md

# 推送代码
cd ~/.openclaw/workspace && git add -A && git commit -m "..." && git push origin master
```

## 已知问题与解决方案

| 问题 | 原因 | 方案 |
|------|------|------|
| 密码登录失败 | 飞书 `/accounts/auth/pwd/verify` 拒绝 | QR 码扫码，每 2-4 周刷新一次 |
| applink 无法测试 | 需要飞书客户端 | 排除检测 |
| MES 2.0 无权限 | 创建者限制访问 | 标记为 unreachable |
| 飞书文档创建失败 | bot 缺少 docx:document 权限 | 降级为消息发送 |
| 全量测试耗时长 | 21 agent × 3 LLM 调用 | 约 40 分钟，cron 超时设为 3600s |
| 截图仅显示路径 | cron delivery 不走附件 | 用 message 工具 attachments 参数 |
| Cron agent 总结报告 | 默认行为 | 提示词明确禁止总结 |

## 技术栈

| 组件 | 版本/地址 | 用途 |
|------|-----------|------|
| Python | 3.11+ | 脚本语言 |
| Playwright | Chromium headless | 浏览器对话测试 + 截图 |
| httpx | latest | HTTP 请求 |
| vLLM | Qwen3.6-35B-A3B @ 10.0.1.27:8000 | LLM 问题生成 + 评估 |
| OpenClaw Cron | isolated agentTurn | 定时调度 + 执行 + 投递 |
| Feishu | playwright_state.json | 飞书登录态保持 |
