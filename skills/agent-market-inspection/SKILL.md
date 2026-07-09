---
name: agent-market-inspection
description: "Agent Market 每日健康巡检：API 采集 + Playwright 对话测试 + 截图 + LLM 评估 + 飞书文档投递"
version: "1.0.0"
metadata:
  {
    "openclaw":
      {
        "emoji": "🌿",
        "requires":
          { "bins": ["python3"], "config": ["plugins.entries.feishu.enabled"] },
        "envVars":
          [
            {
              "name": "LLM_API_KEY",
              "required": false,
              "description": "LLM API 密钥（可选）",
            },
            {
              "name": "LLM_BASE_URL",
              "required": false,
              "description": "LLM 服务地址",
            },
          ],
      },
  }
---

# Agent Market 健康巡检

对 [Agent Market](https://agent.digitalchina.com/market) 智能体平台执行全量健康检查：API 数据采集 → Playwright 对话测试 → 截图计时 → LLM 评估 → 飞书文档投递。

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
输出: 一句话摘要 (总数/指南/评价/零下载)
```

### 3. 对话测试
```
对 22 个对话型智能体:
  1. Playwright 导航到聊天 URL
  2. LLM 生成 2 个测试问题
  3. contentEditable 输入 → Enter 发送
  4. 轮询等待回复 → parse_reply() 提取内容
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
MANIFEST_PATH=reports/MANIFEST.json                      ← 投递清单
截图 HTTP 服务端口: 18990                                ← 供 feishu_doc write 自动下载
```

## 投递机制：HTTP 截图服务 + write 一步上传

### 架构原理

脚本阶段启动本地 HTTP 文件服务器（端口 18990），将截图目录暴露为 `http://127.0.0.1:18990/screenshots/...`。报告中截图以 `![](http://127.0.0.1:18990/screenshots/119/q1_xxx.png)` Markdown 语法内嵌。

`feishu_doc write` 工具遇到 `![](url)` 会自动下载并上传图片到飞书文档，**图片插入位置完全由 Markdown 结构决定，不会错位**。

### cron agent 投递流程（3 步）

```
Step 1: exec 运行脚本（脚本自动启动 HTTP 服务）
  cd work/agent-market && CHAT_TEST=1 CHAT_TEST_ALL=1 timeout 600 python3 -u inspect_daily.py
  提取 REPORT_PATH= 和 MANIFEST_PATH=

Step 2: 读取报告 markdown → 创建文档 → 写入
  read REPORT_PATH → 获取完整 markdown（含 ![](http://127.0.0.1:18990/...) 截图 URL）
  feishu_doc(action="create", title=doc_title, owner_open_id=owner_open_id) → doc_token
  feishu_doc(action="write", doc_token=doc_token, content=full_markdown)
  ⚠️ write 自动处理 ![](url) 图片上传，无需手动 upload_image

Step 3: 发送链接
  message(action="send", channel="feishu", target="ou_12f4e5dbfd82f5975eaa6afd762b1d20",
    message="🌿 巡检完成\n报告：https://feishu.cn/docx/{doc_token}")
```

### 降级方案

| 失败步骤 | 降级行为 |
|----------|----------|
| 脚本超时 (600s) | `ls -t reports/agent-health-report-*.md \| head -1` 取最新报告 |
| feishu_doc create 失败 | message 发送报告摘要 |
| feishu_doc write 失败 | message 发送报告文本 |
| HTTP 服务不可达 | 降级为 `upload_image` 逐张上传（兜底） |

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

![](http://127.0.0.1:18990/screenshots/XXX/q1_xxxxxx.png)

用时：10.2s | 平均用时：8.5s
```

状态图标：✅ 通过 | 🟠 异常 | 🟡 不可达 | ⛔ applink

## 定时任务

### Cron Job 配置

- Job ID: `25d841bb-d50a-426e-8146-cccabc97821c`
- 调度: 每天 9:00 Asia/Shanghai
- 模型: `deepseek/deepseek-v4-pro`
- 超时: 1200s
- 投递: feishu → `ou_12f4e5dbfd82f5975eaa6afd762b1d20`

### Cron Agent Prompt（精简版）

```
执行 Agent Market 每日健康巡检并投递到飞书文档。

## Step 1: 运行脚本
cd /home/node/.openclaw/workspace/work/agent-market && CHAT_TEST=1 CHAT_TEST_ALL=1 PYTHONUNBUFFERED=1 timeout 600 python3 -u inspect_daily.py
提取 REPORT_PATH= 和 MANIFEST_PATH=。

## Step 2: 创建文档并写入
读取 REPORT_PATH 内容（markdown 已包含 ![](http://127.0.0.1:18990/screenshots/...) 截图 URL）。
feishu_doc(action="create", title=..., owner_open_id="ou_12f4e5dbfd82f5975eaa6afd762b1d20") → doc_token
feishu_doc(action="write", doc_token=doc_token, content=full_markdown)

## Step 3: 发送链接
message(action="send", channel="feishu", target="ou_12f4e5dbfd82f5975eaa6afd762b1d20",
  message="🌿 Agent Market 每日健康巡检完成！\n\n报告文档：https://feishu.cn/docx/{doc_token}")

## 降级
- 脚本超 600s 无输出则 kill，ls -t reports/agent-health-report-*.md | head -1 取最新
- feishu_doc create/write 失败 → message 发报告摘要
```

## 认证信息

| 类型 | 账号 | 方式 |
|------|------|------|
| Agent Market | zhangzlt / Zzl.20041006 | HTTP JWT |
| 飞书 | 17265205125 | QR 码扫码 |

## 已知限制

- 飞书密码登录被 API 拒绝 → QR 码登录（2-4 周刷新一次）
- applink 链接需要飞书客户端 → 排除测试
- 飞书文档不支持 Markdown 表格 → 投递时转换为项目列表
- `feishu_doc write` 的 `![](url)` 自动上传需脚本 HTTP 服务保持运行
- Dify API 测试的 `appId` 需手动从前端 JS 提取映射
