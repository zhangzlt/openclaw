# Agent Market 健康巡检

对 agent.digitalchina.com 市场的智能体做健康巡检。

## 功能

1. 登录市场平台
2. 采集智能体列表（41个）
3. 逐个点开卡片查看详细介绍
4. 点击"打开"按钮访问智能体
5. 对话型智能体 → LLM 生成问题 → 测试对话
6. 访问不了的如实报告问题
7. 生成巡检报告

## 项目结构

```
work/agent-market/
├── .env.example          # 环境变量模板
├── config.py             # 全局配置
├── main.py               # 主入口
├── browser/              # 浏览器管理 + 登录
├── crawler/              # 采集 + 巡检
├── notifier/             # 飞书通知
├── reporter/             # 报告生成
├── utils/                # 工具函数
├── logs/                 # 运行日志
├── reports/              # 巡检报告
└── screenshots/          # 截图
```

## 关键选择器

- 登录: `input[placeholder*='邮箱']` / `input[placeholder*='密码']`
- 智能体对话页: `/ai/gui/chat/a_{agent_id}`
- 打开按钮 API: `widget/track?agentId={id}&detail={path}`

## 运行

```bash
export PATH="/home/node/.local/bin:$PATH"
cp .env.example .env  # 编辑填入真实值
python main.py
```

## 注意事项

1. 必须先登录再操作
2. 新标签页用 `expect_page()` 拦截，不是 `window.open` 拦截
3. 用 `textContent` 而非 `innerText`（React 虚拟滚动兼容）
