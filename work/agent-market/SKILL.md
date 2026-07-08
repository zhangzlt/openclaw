# Agent Market 健康巡检技能

> 对 agent.digitalchina.com 市场的智能体做自动化健康巡检。

## 功能

1. **登录验证** - 登录 Agent Market 平台（统一认证）
2. **列表采集** - 通过 API 采集市场页全部智能体信息
3. **可用性检查** - 检查智能体页面可访问性
4. **对话测试** - 使用 Playwright 对话测试对话型智能体，LLM 根据描述生成测试问题并评估回复质量
5. **轮询覆盖** - 每批测试多个智能体，轮流覆盖全部对话型智能体
6. **问题报告** - 报告只列出有问题的智能体和所在问题，正常的不在正文显示
7. **报告生成** - 输出 MD + HTML + JSON 巡检报告
8. **飞书通知** - 自动生成飞书文档发送报告

## 关键选择器

### 登录页（统一认证）
- IT Code: `input[placeholder*='itcode']`
- 密码: `input[placeholder*='统一认证密码']`
- 提交按钮: `button[type='submit']`
- ⚠️ 不用 `#username` / `#password`（这些 id 不存在）

### API 端点
- 市场列表: `GET /api/agents/market?page=1&pageSize=200`
- 授权: `POST /auth/v1/oauth/token`
- 所有 `/api` 请求未登录返回 `401 "未登录，请先登录"`

## 技术栈

- Python 3.11+
- Playwright（浏览器自动化）
- Asyncio（异步并发）
- httpx（API 请求）
- LLM（测试问题生成 + 回复质量评估）

## 项目结构

```
work/agent-market/
├── .env                # 环境变量（登录凭证）
├── .env.example        # 环境变量模板
├── .auth/              # 认证缓存
│   ├── session.json    # Playwright 认证会话
│   ├── token.txt       # API token 缓存
│   └── chat-test-state.json  # 对话测试轮询状态
├── config.py           # 全局配置
├── main.py             # 主入口（完整浏览器流程）
├── inspect_daily.py    # 轻量级每日巡检（API + Playwright 对话测试）
├── browser/
│   ├── __init__.py
│   ├── login.py        # 登录逻辑
│   └── playwright_setup.py
├── crawler/
│   ├── __init__.py
│   ├── collector.py      # 采集智能体列表
│   └── inspector.py      # 巡检（点击+对话测试+LLM 评估）
├── notifier/
│   ├── __init__.py
│   └── feishu.py         # 飞书通知
├── reporter/
│   ├── __init__.py
│   └── report.py         # 生成 HTML + JSON + Markdown 报告
├── scheduler/
│   ├── __init__.py
│   └── scheduler.py      # 定时巡检调度
├── utils/
│   ├── __init__.py
│   ├── helpers.py
│   ├── llm.py            # LLM 调用：问题生成 + 回复评估
│   └── logger.py
├── logs/                 # 运行日志
├── reports/              # 巡检报告
└── screenshots/          # 截图
```

## 运行方式

### 完整巡检（浏览器方式）

```bash
export PATH="/home/node/.local/bin:$PATH"
cd work/agent-market
source .env
pip install playwright
playwright install chromium
python main.py
```

### 每日轻量巡检（API + 对话测试）

```bash
# 基础模式：仅 API 检查（最快）
python inspect_daily.py

# 增强模式：API + Playwright 对话测试（轮询）
CHAT_TEST=1 CHAT_TEST_BATCH=5 python inspect_daily.py
# CHAT_TEST_BATCH 控制每批测试多少个，默认 5 个
# 轮询覆盖全部对话型智能体约需 8-10 天
```

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `CHAT_TEST` | `0` | 是否启用对话测试 (`1`=开启) |
| `CHAT_TEST_BATCH` | `5` | 每批对话测试数量 |

## 定时任务

- **任务**: 每天 9:00 自动触发巡检
- **触发方式**: agentTurn → exec 运行 inspect_daily.py → feishu_doc 创建文档 → 飞书发送链接
- **报告位置**: `work/agent-market/reports/agent-health-report-{date}.md`
- **对话测试**: 轮询模式，每批 3 个，覆盖全部约 14 天

## 巡检指标

### API 基础指标

| 指标 | 说明 |
|------|------|
| 智能体总数 | 平台注册的全部智能体数量 |
| 已安装 | 当前用户已安装的数量 |
| 总下载量 | 全部智能体的累计下载量 |
| 有使用指南 | 提供 usageGuide 的智能体数 |
| 有用户评价 | 有 reviews 的智能体数 |
| 零下载 | 下载量为 0 的智能体数 |
| 零点赞 | 点赞数为 0 的智能体数 |
| 无评分 | 评分为 0 的智能体数 |

### 对话测试指标

| 指标 | 说明 |
|------|------|
| 页面加载 | 对话页面是否正常加载 |
| 输入框 | 是否能找到聊天输入框 |
| 发送成功 | 问题是否能成功发送 |
| 回复获取 | 是否能获取到回复内容 |
| LLM 评估 | 回复质量评分（0-10） |
| 相关性 | 回复是否与问题相关 |
| 可用性 | 回复是否有实际帮助 |
| 完整性 | 回复是否回答了核心问题 |
| 专业性 | 回复是否专业准确 |

## 巡检状态说明

- 🟢 正常 - 页面可加载，功能正常
- 🔴 异常 - 页面加载失败或报错
- 🟠 对话异常 - 能访问但对话测试失败
- 🟠 回复质量不合格 - 对话有回复但 LLM 评估未通过
- 🟡 无法访问 - 详情页 URL 无效
- ⚪ 未知 - 信息不全

## 轮询机制

对话测试采用**轮询模式**，避免每次测试全部智能体导致超时：

1. 每次巡检选取 `CHAT_TEST_BATCH` 个对话型智能体
2. 下次巡检选取下一批，不重复
3. 全部测试完后从头开始新一轮
4. 轮询状态保存在 `.auth/chat-test-state.json`

## LLM 功能

### 测试问题生成（`utils/llm.py::generate_test_questions`）

根据智能体名称、类型和描述，LLM 自动生成 3 个测试问题：
- 问题与智能体功能紧密相关
- 问题自然，像真实用户会问的
- 不同问题覆盖不同使用场景
- LLM 不可用时降级为预定义问题

### 回复质量评估（`utils/llm.py::evaluate_response`）

使用 LLM 评估智能体回复是否合适：
- 相关性：回复是否与问题相关（0-10 分）
- 可用性：回复是否有实际帮助（0-10 分）
- 完整性：回复是否回答了核心问题（0-10 分）
- 专业性：回复是否专业准确（0-10 分）
- 判定规则：相关性 < 4、错误信息、功能不可用等 → passed=false
- LLM 不可用时降级为简单规则评估

## 报告格式

### Markdown 报告（发送给飞书）

- 巡检摘要（总数 / 正常 / 异常）
- ⚠️ **仅列出有问题的智能体**（包含问题详情、测试问题、回复内容、LLM 评估结果）
- 全部智能体列表（按分类，完整数据）

### HTML 报告（归档用）

- 完整数据表格
- 对话测试结果
- 可视化统计卡片

### JSON 报告（程序用）

- 完整结构化数据，供后续分析使用
