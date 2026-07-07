---
name: "init"
description: "初始化 OpenClaw 项目骨架，快速搭建标准项目结构、配置文件和应用模板"
---

# /init — 初始化 OpenClaw 项目骨架

## 触发条件
用户请求创建新项目、初始化项目、搭建项目骨架。

## 执行流程

### 1. 确认需求
- 项目名称、类型（Web/API/CLI/全栈）
- 技术栈偏好（Python/Node.js，框架）
- 是否需要 Playwright 集成
- 是否需要 Docker

### 2. 创建标准目录结构
```
project/
├── src/           # 源代码
├── tests/         # 测试文件
├── configs/       # 配置文件
├── docs/          # 文档
├── scripts/       # 脚本
├── .env.example   # 环境变量模板
├── .gitignore
├── README.md
└── Makefile       # 常用命令
```

### 3. 项目模板选择
- **LLM 应用模板**: fastapi + OpenAI SDK + 流式处理
- **Agent 应用模板**: 多 Agent 协作框架
- **RAG 应用模板**: 向量数据库 + 检索管道
- **自动化脚本模板**: Playwright + 数据采集
- **通用模板**: 基础配置 + 日志 + 错误处理

### 4. 输出
- 完整的项目目录结构
- 核心配置文件和代码骨架
- README 项目说明
- 下一步操作指引（安装依赖、启动开发等）
