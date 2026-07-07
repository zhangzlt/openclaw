---
name: "long-term-memory"
description: "每次对话启动时自动读取 memory/long-term/ 目录下的所有长期记忆文件"
---

# Long-Term Memory Skill

## 触发时机
每次新会话启动时，在执行任何其他任务之前，执行本技能。

## 步骤

### 1. 读取长期记忆索引
读取 `/home/node/.openclaw/workspace/memory/long-term/INDEX.md` 了解有哪些记忆文件。

### 2. 读取所有长期记忆文件
依次读取 `memory/long-term/` 目录下的所有 `.md` 文件：
- `preferences.md` — 用户偏好与习惯
- `projects.md` — 进行中的项目
- `decisions.md` — 重要决策记录
- `contacts.md` — 重要联系人

### 3. 加载 MEMORY.md
同时读取工作区根目录的 `MEMORY.md`（如存在），作为长期记忆的补充。

### 4. 读取最近的每日笔记
读取最近 3 天的 `memory/YYYY-MM-DD.md` 日志，了解最近动态。

## 原则
- 只在静态上下文（BOOTSTRAP/AGENTS/SOUL/USER 之外的 Project Context）中首次启动时执行
- 如果上下文已经包含了记忆内容（如通过 `memory_search` 已加载），则跳过重复读取
- 不替代 `memory_search` 和 `memory_get` 工具的使用
