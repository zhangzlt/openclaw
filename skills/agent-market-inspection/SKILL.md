---
name: "agent-market-inspection"
description: "Agent Market 每日健康巡检：全量智能体 API 数据采集 + Playwright 对话测试 + LLM 评估 + 飞书群投递"
---

# Agent Market 每日健康巡检

对 Agent Market 平台 41 个智能体执行每日健康巡检：API 数据采集 → Playwright 对话测试 → LLM 评估 → 飞书群投递报告。

## 前置条件

### 文件清单

| 文件 | 说明 |
|------|------|
| `work/agent-market/inspect_daily.py` | 主巡检脚本（API 采集 + Playwright 对话测试 + 报告生成） |
| `work/agent-market/utils/llm.py` | LLM 工具：生成测试问题、评估回复质量 |
| `work/agent-market/config.py` | 配置（飞书 app 凭证等） |
| `work/agent-market/.auth/token.txt` | Agent Market API token 缓存 |
| `work/agent-market/.auth/playwright_state.json` | 飞书 QR 码扫码登录态（Playwright 复用） |
| `work/agent-market/.auth/market_state.json` | Agent Market 登录态（Playwright 复用） |
| `work/agent-market/reports/agent-health-report-{日期}.md` | 巡检报告输出 |

### 认证体系

**Agent Market（API + 页面登录）**
- 地址：`https://agent.digitalchina.com/market`
- 账号：`itcode: zhangzlt` / `密码: Zzl.20041006` ✅
- Token 缓存：`.auth/token.txt`（有效期约 30 天，脚本自动验证续期）

**飞书（聊天测试）**
- 手机：`17265205125` / 密码：`zzl20041006` ❌ 密码被 `/accounts/auth/pwd/verify` 拒绝
- 替代方案：QR 码扫码登录 → 保存 `playwright_state.json`
- 登录态有时效，过期需重新扫码

## Cron 定时任务

### 任务配置

```
Job ID: 25d841bb-d50a-426e-8146-cccabc97821c
名称: Agent Market 每日健康巡检
计划: 0 9 * * * (Asia/Shanghai) → 每天上午 9:00
会话: isolated (独立隔离 session)
模型: vllm36/Qwen3.6-35B-A3B
超时: 3600 秒
投递: 飞书群聊 oc_bef4f48fb4870602342af652e5501d86
```

### 触发方式
- **自动**：每天 09:00 Shanghai 时间
- **手动**：`cron run force jobId=25d841bb-d50a-426e-8146-cccabc97821c`

### 任务流程（isolated agent 执行）
1. 执行脚本：`CHAT_TEST=1 CHAT_TEST_ALL=1 python3 -u inspect_daily.py`
2. 读取报告：`work/agent-market/reports/agent-health-report-{日期}.md`
3. 发送到飞书群（飞书文档权限不足，降级为消息发送）

## 巡检脚本架构

### 执行流程

```
Step 1: 获取 Token
├─ 检查 .auth/token.txt 缓存 → HTTP 验证有效性
├─ 缓存有效 → 直接使用（cron 快速运行关键）
└─ 缓存失效 → Playwright 登录 Agent Market → 拦截 Bearer token → 写缓存

Step 2: API 数据采集
├─ GET /api/agents/market → 获取全部 41 个智能体
└─ 统计：下载/点赞/评分/使用指南/用户评价

Step 3: 生成 API 报告
├─ 概览：总数/下载/点赞/分类
├─ 问题智能体：缺指南/无评价/零下载
└─ 分类列表：全部智能体按分类排列

Step 4: Playwright 对话测试 (CHAT_TEST=1 + CHAT_TEST_ALL=1)
├─ 筛选对话型智能体：_is_chat_agent() → 21 个
├─ 加载飞书登录态：.auth/playwright_state.json
├─ 对每个智能体：
│   ├─ 导航到聊天 URL
│   ├─ 权限检查（No permission to use → 跳过）
│   ├─ LLM 生成 2 个测试问题（utils/llm.py）
│   ├─ contentEditable 输入框发送问题
│   ├─ 等待 10s 获取回复
│   ├─ _parse_chat_reply() 解析回复
│   └─ LLM 评估回复质量
└─ 生成完整报告（API + 对话测试合并）

Step 5: 输出报告
└─ 写入 reports/agent-health-report-{日期}.md
```

### 关键函数

**`_is_chat_agent(agent)`** — 判断是否为可测试的对话型智能体
- 匹配 URL 模式：`feishuapp.cn/ai/gui/chat/a_xxx`（5 个）或 `aily.feishu.cn/agents/agent_xxx`（16 个）
- 排除：`applink.feishu.cn`（需飞书客户端，不可浏览器测试）
- 排除：空 URL、内部系统 URL

**`run_chat_tests(agents, token)`** — Playwright 对话测试核心
- 使用飞书 QR 扫码登录态直接访问聊天页
- 两种平台统一处理：`feishuapp` 和 `aily`，都是 `DIV[contenteditable="true"]`
- 发送方式：`.click()` → `.type(text, delay=30)` → `.press("Enter")` → 等 10s

**`_parse_chat_reply(body_before, body_after, question)`** — 解析 AI 回复
- 对比前后 body 文本的 diff
- 过滤 UI 元素：`/`、`新对话`、`Deep Planning`、`Tools`、`Copy`、`Invite & Earn` 等
- 过滤元数据：`Based on` 来源块、`智能检索` 前缀

**`generate_full_report(api_report, chat_results)`** — 报告生成
- API 基础统计 + 对话测试详细结果合并
- 每个智能体展示：测试问题、回复原文、LLM 评估分析

## 对话型智能体清单

### feishuapp.cn（5 个）— 飞书 aPaaS 对话 Widget

| ID | 名称 | URL |
|----|------|-----|
| 110 | 折扣问答小助手 | `bba12hub36.feishuapp.cn/ai/gui/chat/a_3687bf8` |
| 109 | CTC智能客服 | `bba12hub36.feishuapp.cn/ai/gui/chat/a_eb9c4b2` |
| 83 | 新海量采购系统智能助手 | `bba12hub36.feishuapp.cn/ai/gui/chat/a_c0021ea` |
| 74 | 电子签章智能问答助手 | `bba12hub36.feishuapp.cn/ai/gui/chat/a_1f46a3e` |
| 73 | EB智能客服机器人 | `bba12hub36.feishuapp.cn/ai/gui/chat/a_ea846e9` |

### aily.feishu.cn（16 个）— 飞书 aily 平台

| ID | 名称 | URL |
|----|------|-----|
| 122 | MES 2.0 数据查询助手 | `aily.feishu.cn/agents/agent_4ju4gsr438msb` |
| 119 | 业务签约法人体智能推荐 | `aily.feishu.cn/agents/agent_4juccukrzuvxt` |
| 115 | DI问答助手 | `aily.feishu.cn/agents/agent_4jn4cnjeurc3r` |
| 108 | 有问妙答-营销管理部 | `aily.feishu.cn/agents/agent_4j3cnehjxtdea` |
| 106 | 📝 职场文案速写·全能版 | `aily.feishu.cn/agents/agent_4k4mhq6d81p8a` |
| 105 | Agent上架助手 | `aily.feishu.cn/agents/agent_4k6rcu7nc9z8s` |
| 101 | 短视频选题策划专家 | `aily.feishu.cn/agents/agent_4k5a9uqezcxnd` |
| 99 | 美金商务解答助手 | `aily.feishu.cn/agents/agent_4jkvqq4ez0evs` |
| 95 | 职场解忧大师 | `aily.feishu.cn/agents/agent_4k4tnzwzb0fcn` |
| 92 | 企业文化活动策划助手 | `aily.feishu.cn/agents/agent_4j3mu0vekgejy` |
| 85 | 高效会议评估 | `aily.feishu.cn/agents/agent_4k4mnzezhgp6x` |
| 82 | 小新老师沟通课 | `aily.feishu.cn/agents/agent_4ja8a79aywqng` |
| 79 | Figma产品原型创建助手 | `aily.feishu.cn/agents/agent_4judthgebxcm6` |
| 78 | 项目复盘顾问 | `aily.feishu.cn/agents/agent_4k35h48er87eq` |
| 76 | 神州问学知识库回答助手 | `aily.feishu.cn/agents/agent_4jccvuk6yqb1y` |
| 72 | 文档差异与风险分析专家 | `aily.feishu.cn/agents/agent_4jy8r20jaknhz` |

### 排除（2 个 applink）
- [90] 客户小助手（订阅）：`applink.feishu.cn/T93e6UpNn6Lz` — 需跳转飞书客户端
- [86] 售前项目管理专家：`applink.feishu.cn/T96L2f1BCUG9` — 需跳转飞书客户端

## 操作手册

### 手动执行巡检

```bash
# 全量对话测试模式
cd /home/node/.openclaw/workspace/work/agent-market
CHAT_TEST=1 CHAT_TEST_ALL=1 python3 -u inspect_daily.py

# 仅 API 数据采集（快速，3 秒）
python3 inspect_daily.py

# 轮询模式（每批 5 个，适合频繁测试）
CHAT_TEST=1 CHAT_TEST_BATCH=5 python3 inspect_daily.py
```

### 触发 Cron 手动跑

```bash
# 在 OpenClaw 会话中
/cron run force 25d841bb-d50a-426e-8146-cccabc97821c
```

或通过 openclaw CLI：
```bash
openclaw cron run 25d841bb-d50a-426e-8146-cccabc97821c --run-mode force
```

### 飞书登录态过期处理

当 `playwright_state.json` 过期（2-4 周）：
```bash
cd /home/node/.openclaw/workspace/work/agent-market
python3 feishu_login.py  # 弹出 QR 码 → 飞书扫码授权
```

### 查看报告

```bash
# 最新报告
cat work/agent-market/reports/agent-health-report-20260708.md

# 汇总统计
grep "通过\|异常\|跳过" work/agent-market/reports/agent-health-report-20260708.md
```

## 已知问题

### 飞书文档创建权限不足
- `feishu_doc` action=create 返回 400
- 缺少：`docx:document`、`drive:drive` 等权限
- 临时方案：降级为消息直接发送摘要

### 3 个异常智能体（2026-07-08）
- #122 MES 2.0：需创建者授权（`No permission to use`）
- #110 折扣问答小助手：仅返回「思考中」
- #105 Agent上架助手：仅返回时间戳

### applink.feishu.cn 无法测试
- 跳转链接需要飞书客户端打开
- 浏览器中无法完成 SSO 流程

## 报告格式

报告包含两部分：

### API 基础统计
- 巡检概况：总数/下载/点赞/分类
- 问题智能体：缺指南/无评价/零下载
- 全部智能体分类列表

### 对话测试详情
每个智能体展示：
- 测试问题（LLM 生成，2 个）
- 智能体回复原文
- LLM 评估分析 + 评分（0-10）

底部汇总表：通过/异常/跳过的数量与占比

## 技术栈

| 组件 | 用途 |
|------|------|
| Python 3.11+ | 脚本语言 |
| Playwright (Chromium headless) | 浏览器对话测试 |
| httpx | API HTTP 请求 |
| vLLM (Qwen3.6-35B-A3B @ 10.0.1.27:8000) | LLM 问题生成 + 回复评估 |
| OpenClaw Cron | 定时调度 + 隔离执行 + 飞书投递 |
| Feishu Playwright State | 飞书登录态保持 |
