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

## 目录结构

```
agent-market-inspection/
├── SKILL.md                  # 技能定义（本文件）
├── manifest.json             # 权限、依赖、沙箱声明
├── scripts/                  # 可执行代码
│   ├── main.py               # 主入口（Python）
│   ├── entry.sh              # 入口（Shell）
│   └── utils/
│       ├── parser.py         # 回复解析 & 噪声过滤
│       └── request.py        # HTTP 请求 & token 管理
├── references/
│   └── api-schema.json       # API 响应 JSON Schema
├── assets/
│   └── config.tpl            # 配置模板
└── tests/
    └── test_main.py          # 单元测试
```

## 前置条件

- Python 3.11+
- Playwright Chromium: `python3 -m playwright install chromium`
- 飞书登录态文件（QR 扫码，2-4 周有效）
- Token 缓存文件（JWT，约 30 天有效）

## 快速开始

```bash
# 进入技能目录
cd {baseDir}

# 仅 API 采集（3 秒）
python3 scripts/main.py

# 全量对话测试 + 截图
CHAT_TEST=1 CHAT_TEST_ALL=1 python3 -u scripts/main.py

# 分批测试（降低内存压力）
CHAT_TEST=1 CHAT_TEST_BATCH=5 python3 -u scripts/main.py

# 或使用 Shell 入口
bash scripts/entry.sh
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
  4. 等待回复 → parse_reply() 提取内容
  5. page.screenshot() 截图
  6. time.time() 计时
  7. 浏览器崩溃 → ensure_browser() 自动重建
```

### 4. 报告输出
```
REPORT_PATH=reports/agent-health-report-YYYYMMDD.md
SCREENSHOT_PATHS_BEGIN
/path/to/screenshots/...
SCREENSHOT_PATHS_END
```

## 投递流程（cron agent 执行）

```
Step 1: 运行脚本 → 获取 REPORT_PATH + 截图路径列表
Step 2: feishu_doc 创建文档
Step 3: feishu_doc write 写入报告（表格转项目列表，飞书不支持 MD 表格）
Step 4: feishu_doc upload_image 内嵌截图（每次间隔 2s）
Step 5: message 发送文档链接到飞书
```

### 降级方案

| 失败步骤 | 降级行为 |
|----------|----------|
| 脚本超时 (900s) | `ls -t reports/*.md \| head -1` |
| feishu_doc create 失败 | message 发送报告文本 |
| upload_image 失败 | 跳过，继续 |
| feishu_doc write 失败 | message 发送报告文本 |

## 对话型智能体（22 个）

### feishuapp.cn 平台（5 个）
- [110] 折扣问答小助手 → `bba12hub36.feishuapp.cn/ai/gui/chat/a_3687bf8`
- [109] CTC智能客服 → `bba12hub36.feishuapp.cn/ai/gui/chat/a_eb9c4b2`
- [83] 新海量采购系统智能助手 → `bba12hub36.feishuapp.cn/ai/gui/chat/a_c0021ea`
- [74] 电子签章智能问答助手 → `bba12hub36.feishuapp.cn/ai/gui/chat/a_1f46a3e`
- [73] EB智能客服机器人 → `bba12hub36.feishuapp.cn/ai/gui/chat/a_ea846e9`

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

### Agent Market 内嵌（1 个）
- [63] 客户信息查询小助手 → `openType=api` + `source=dify`

### 排除（applink，需飞书客户端）
- [90] 客户小助手（订阅）
- [86] 售前项目管理专家

## 报告格式

```markdown
# YYYY年MM月DD日 HH:MM Agent Market 健康巡检报告
**一句话总结**: 41 个智能体, 39/41 有指南, 10/41 有评价

---
## 🤖 对话测试详情
✅ 通过: 18 | ❌ 异常: 3 | 共 22 个

### ✅ 智能体名称 (ID: XXX)
测试问题1：文本
回答结果1：文本
用时：10.2s | 平均用时：8.5s
```

状态图标：✅ 通过 | 🟠 异常 | 🟡 不可达 | ⛔ applink

## 定时任务

```bash
openclaw cron add \
  --name "Agent Market 每日巡检" \
  --schedule "0 9 * * *" --tz "Asia/Shanghai" \
  --session isolated --model deepseek/deepseek-v4-pro \
  --timeout 900 --announce --channel feishu --to {target} \
  --message "每日巡检提示词"
```

## 认证信息

| 类型 | 账号 | 方式 |
|------|------|------|
| Agent Market | zhangzlt / Zzl.20041006 | HTTP JWT |
| 飞书 | 17265205125 | QR 码扫码 |

刷新登录态: `python3 feishu_login.py`

## 已知限制

- 飞书密码登录被 API 拒绝 → QR 码登录（2-4 周刷新一次）
- applink 链接需要飞书客户端 → 排除测试
- Agent Market 内嵌 Dify 需要市场登录态 → 已检测但测试待适配
- 飞书文档不支持 Markdown 表格 → 投递时转换为项目列表
