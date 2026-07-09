---
name: agent-market-inspection
description: "Agent Market 每日健康巡检：API 采集 + Playwright 对话测试 + 截图 + LLM 评估 + 飞书文档投递"
version: "1.1.0"
metadata:
  {
    "openclaw":
      {
        "emoji": "🌿",
        "requires":
          { "bins": ["python3"], "config": ["plugins.entries.feishu.enabled"] },
      },
  }
---

# Agent Market 健康巡检

对 [Agent Market](https://agent.digitalchina.com/market) 智能体平台执行全量健康检查：API 数据采集 → Playwright 对话测试 → 截图计时 → 飞书文档投递。

## 前置条件

- Python 3.11+
- Playwright Chromium: `python3 -m playwright install chromium`
- 飞书登录态文件（QR 扫码，2-4 周有效）
- Token 缓存文件（JWT，约 30 天有效）

## 快速开始

```bash
cd /home/node/.openclaw/workspace/work/agent-market

# 仅 API 采集（3 秒）
python3 inspect_daily.py

# 全量对话测试 + 截图
CHAT_TEST=1 CHAT_TEST_ALL=1 python3 -u inspect_daily.py

# 分批测试（降低内存压力）
CHAT_TEST=1 CHAT_TEST_BATCH=5 python3 -u inspect_daily.py
```

## 执行流程

### 1. 认证
```
Token 缓存验证 (.auth/token.txt)
  ├─ 有效 → 直接使用
  └─ 失效 → Playwright 浏览器登录 → 拦截 JWT → 写缓存
```

### 2. API 采集
```
GET /api/agents/market → 41 个智能体
输出: **总结** (总数/指南/评价/零下载)
```

### 3. 对话测试
```
对 22 个对话型智能体:
  1. Playwright 导航到聊天 URL
  2. LLM 生成 2 个测试问题
  3. contentEditable 输入 → Enter 发送
  4. 轮询等待回复（连续 2 轮 body 不变且长度 > 20，最长 45s）
  5. page.screenshot() 截图
  6. time.time() 计时
  7. 浏览器崩溃 → ensure_browser() 自动重建

Dify API 智能体（如 ID 63）走 HTTP SSE 直连：
  POST https://agent.digitalchina.com/api/chat/stream
  appId 映射表: DIFY_APPID_MAP = {63: 8}
```

### 4. 报告输出
```
REPORT_PATH=reports/agent-health-report-YYYYMMDD.md     ← 完整 Markdown 报告
MANIFEST_PATH=reports/MANIFEST.json                      ← 结构化投递清单
截图存放在 reports/screenshots/<agent_id>/q<1|2>_<timestamp>.png
```

## 投递机制：逐 Agent 追加 + 内嵌截图

### 架构原理

脚本生成结构化 `MANIFEST.json`（含 sections 数组 + 截图路径映射）。cron agent 按以下流程投递：

```
1. 创建飞书文档 → write 写入摘要
2. 逐 agent 处理（一个 turn 一个 agent）:
   turn N: append(agent_text_ending_with_截图：) → upload_image(截图路径) → append(用时：Xs)
3. 全部完成后 message 发送文档链接
```

### 关键规则
- **同一 turn 内顺序执行**：`append` → `upload_image` → `append`，不能并行
- **一个 turn 一个 agent**：绝不能批量处理多个 agent，否则截图顺序会乱
- 无截图的 agent 单次 `append` 即可
- 单个 agent 的 `upload_image` 失败 → 跳过，继续后续

### 降级方案

| 失败步骤 | 降级行为 |
|----------|----------|
| 脚本超时 (600s) | `ls -t reports/agent-health-report-*.md \| head -1` 取最新 |
| feishu_doc create 失败 | message 发送报告摘要 |
| 单个 upload_image 失败 | 跳过该截图，继续后续 agent |
| 整体投递失败 | message 发送报告摘要 + 异常原因 |

## 对话型智能体（22 个）

### feishuapp.cn 平台（5 个）
- [110] 折扣问答小助手
- [109] CTC智能客服
- [83] 新海量采购系统智能助手
- [74] 电子签章智能问答助手
- [73] EB智能客服机器人

### aily.feishu.cn 平台（16 个）
- [122] MES 2.0 数据查询助手
- [119] 业务签约法人体智能推荐
- [115] DI问答助手
- [108] 有问妙答-营销管理部
- [106] 📝 职场文案速写·全能版
- [105] Agent上架助手
- [101] 短视频选题策划专家
- [99] 美金商务工作/销售开单解答助手
- [95] 职场解忧大师
- [92] 企业文化活动策划助手
- [85] 高效会议评估
- [82] 小新老师沟通课
- [79] Figma产品原型创建助手
- [78] 项目复盘顾问
- [76] 神州问学知识库回答助手
- [72] 文档差异与风险分析专家

### Agent Market 内嵌 Dify（1 个，API 直连测试）
- [63] 客户信息查询小助手 → `openType=api` + `source=dify`，HTTP SSE 测试

### 排除（applink，需飞书客户端）
- [90] 客户小助手（订阅）
- [86] 售前项目管理专家

## 报告格式

```markdown
# YYYY年MM月DD日 HH:MM Agent Market 健康巡检报告

**总结**: 41 个智能体, 39/41 有指南, 10/41 有评价

---

## 🤖 对话测试详情

✅ 通过: N | ❌ 异常: M | 共 22 个

### ✅ 智能体名称 (ID: XXX)

测试问题1：

```
文本
```

回答结果1：

```
文本
```

截图：

用时：10.2s | 平均用时：8.5s
```

- `**总结**` 替代 `**一句话总结**`
- Q&A 用 ``` 代码块包裹
- 截图位置：`截图：` 后接 `upload_image`（不写文件路径）
- 时间格式：`用时：Xs | 平均用时：Ys`

## Cron 定时任务

### 配置

| 参数 | 值 |
|------|-----|
| Job ID | `25d841bb-d50a-426e-8146-cccabc97821c` |
| 调度 | 每天 9:00 Asia/Shanghai |
| 模型 | `vllm36/Qwen3.6-35B-A3B`（本地，无需 API Key） |
| 超时 | 1200s |
| 投递目标 | `ou_12f4e5dbfd82f5975eaa6afd762b1d20`（导师个人） |
| 投递方式 | `feishu_doc` 创建文档 + 逐 agent 追加内容截图 + `message` 发链接 |

### Cron Agent Prompt

```
执行 Agent Market 每日健康巡检并投递到飞书文档。

## Step 1: 运行巡检脚本
cd /home/node/.openclaw/workspace/work/agent-market && CHAT_TEST=1 CHAT_TEST_ALL=1 PYTHONUNBUFFERED=1 timeout 600 python3 -u inspect_daily.py
提取 MANIFEST_PATH=。

## Step 2: 创建文档并写入摘要
读取 MANIFEST.json 获取 doc_title。构建 summary markdown（仅含 # 标题 + 总结 + --- + ## 🤖 对话测试详情）。
feishu_doc(action="create", title=doc_title, owner_open_id="ou_12f4e5dbfd82f5975eaa6afd762b1d20") → doc_token
feishu_doc(action="write", doc_token=doc_token, content=summary_markdown)

## Step 3: 逐 agent 追加内容并插入截图
读取 MANIFEST.json 的 sections 数组。

⚠️ 关键：每个 agent 的内容和截图在同一个 turn 内按顺序处理：
  FIRST: feishu_doc(action="append", content=agent_section_text_ending_with_截图：)
  THEN (in same turn): feishu_doc(action="upload_image", file_path=截图路径)
  THEN (in same turn): feishu_doc(action="append", content="用时：Xs | 平均用时：Ys\n")

一个 turn 处理一个 agent。处理完所有 agent 后再下一步。

对于无截图的 agent：只需 feishu_doc(action="append", content=agent_section_full_text)

## Step 4: 发送链接
message(action="send", channel="feishu", target="ou_12f4e5dbfd82f5975eaa6afd762b1d20",
  message="🌿 Agent Market 每日健康巡检完成！\n\n报告文档：https://feishu.cn/docx/{doc_token}")

## 降级
- 脚本超 600s → kill，ls -t reports/agent-health-report-*.md | head -1 取最新
- 单个 agent 的 upload_image 失败 → 跳过，继续后续 agent
```

## 关键文件

| 文件 | 说明 |
|------|------|
| `work/agent-market/inspect_daily.py` | 主巡检脚本 |
| `work/agent-market/reporter/report.py` | 报告生成模块 |
| `work/agent-market/crawler/inspector.py` | API 采集模块 |
| `work/agent-market/notifier/feishu.py` | 飞书通知模块 |
| `skills/agent-market-inspection/SKILL.md` | 本文件 |

## 飞书 App 凭据

| 参数 | 值 |
|------|-----|
| App ID | `cli_aac1c18a7b7a5cef` |
| App Secret | 存储于 `~/.openclaw/openclaw.json` → `channels.feishu` |
| 权限 | `docx:document`（已开启） |

## 认证信息

| 类型 | 账号 | 方式 |
|------|------|------|
| Agent Market | zhangzlt / Zzl.20041006 | HTTP JWT |
| 飞书 | 17265205125 | QR 码扫码 |

## 性能数据

| 模型 | 耗时 | 通过率 | 备注 |
|------|------|--------|------|
| deepseek-v4-pro | ~12-15 分钟 | 19-20/22 | 需 API Key，有时超时 |
| Qwen3.6-35B-A3B (vllm36) | ~17.4 分钟 | 20/22 | 本地部署，无需 API Key ✅ |

## 已知限制

- 飞书密码登录被 API 拒绝 → QR 码登录（2-4 周刷新一次）
- applink 链接需要飞书客户端 → 排除测试
- `feishu_doc write` 的 `![](url)` 不会处理图片 → 必须用 `upload_image` 逐张上传
- Dify API 测试的 `appId` 需手动从前端 JS 提取映射
- 本地 Qwen 模型在复杂多步 tool-calling 场景可能不如 deepseek 稳定
