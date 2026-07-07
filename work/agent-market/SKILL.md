# Agent Market 健康巡检技能

> 对 agent.digitalchina.com 市场的智能体做自动化健康巡检。

## 功能

1. **登录验证** - 登录 Agent Market 平台
2. **列表采集** - 采集市场页全部 41 个智能体信息
3. **详情页检查** - 逐个点开智能体卡片查看详细介绍
4. **可用性测试** - 点击"打开"按钮访问智能体页面
5. **对话测试** - 对话型智能体自动发送 LLM 生成的问题并验证回复
6. **异常报告** - 访问失败的如实记录问题
7. **报告生成** - 输出 HTML + JSON 巡检报告
8. **飞书通知** - 异常时自动发送飞书消息

## 关键选择器

### 登录页
- 邮箱: `input[placeholder*='邮箱']`
- 密码: `input[placeholder*='密码']`
- ⚠️ 不用 `#username` / `#password`（这些 id 不存在）

### 页面操作
- 智能体对话页 URL: `/ai/gui/chat/a_{agent_id}`
- 打开按钮 API: `widget/track?agentId={id}&detail={path}`
- 新标签页用 `expect_page()` 拦截（不是 `window.open` 拦截）
- 必须用 `textContent` 而非 `innerText`（React 虚拟滚动兼容）

### 运行前设置
```bash
export PATH="/home/node/.local/bin:$PATH"
```

## 技术栈

- Python 3.11+
- Playwright（浏览器自动化）
- Asyncio（异步并发）
- OpenAI API（LLM 对话测试）

## 项目结构

```
work/agent-market/
├── .env.example          # 环境变量模板
├── config.py             # 全局配置 + 已验证 agent 数据
├── main.py               # 主入口（完整流程）
├── browser/
│   ├── __init__.py
│   ├── login.py          # 登录逻辑
│   └── playwright_setup.py  # 浏览器管理
├── crawler/
│   ├── __init__.py
│   ├── collector.py      # 采集智能体列表
│   └── inspector.py      # 巡检（点击+对话测试）
├── notifier/
│   ├── __init__.py
│   └── feishu.py         # 飞书通知
├── reporter/
│   ├── __init__.py
│   └── report.py         # 生成 HTML + JSON 报告
├── scheduler/
│   ├── __init__.py
│   └── scheduler.py      # 定时巡检调度
├── utils/
│   ├── __init__.py
│   ├── helpers.py        # 辅助函数
│   ├── llm.py            # LLM 调用
│   └── logger.py         # 日志
├── logs/                 # 运行日志
├── reports/              # 巡检报告
└── screenshots/          # 截图
```

## 已验证的 Agent 数据

| Agent ID | 名称 | 详情页路径 |
|----------|------|-----------|
| 110 | 折扣问答小助手 | /chat/a_3687bf8dfcc64b378852e86891d042e5 |
| 109 | CTC智能客服 | /chat/a_eb9c4b2f0c4c40ae90ce7dfb8fe665eb |
| 74 | 电子签章智能问答助手 | /chat/a_1f46a3e5ec0c4d59b0e93eae67b638a1 |
| 73 | EB智能客服机器人 | /chat/a_ea846e95d9e645129b6049b74b3cfd04 |

## 运行方式

```bash
# 1. 设置环境
export PATH="/home/node/.local/bin:$PATH"
cp .env.example .env  # 编辑填入真实值

# 2. 安装依赖
pip install playwright
playwright install chromium

# 3. 运行巡检
python main.py

# 4. 查看报告
# reports/health_report_*.html
# reports/health_report_*.json
```

## 巡检状态说明

- 🟢 **正常** - 页面可加载，对话正常
- 🔴 **异常** - 页面加载失败或报错
- 🟠 **对话异常** - 能访问但对话测试失败
- 🟡 **无法访问** - 详情页 URL 无效或跳转失败
- ⚪ **未知** - 信息不全
