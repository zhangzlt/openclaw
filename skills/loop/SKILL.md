---
name: "loop"
description: "定时循环任务：创建、管理、监控定时执行的周期性任务"
---

# /loop — 定时循环任务

## 触发条件
用户需要定时任务、周期性检查、自动化循环、定期报告。

## 执行流程

### 1. 任务分析
- 明确循环频率（每分钟/每小时/每天/每周）
- 确认任务内容
- 判断是否需要独立会话（isolated）

### 2. 创建定时任务
使用 cron 工具创建：
```json
{
  "name": "任务名称",
  "schedule": { "kind": "cron", "expr": "*/30 * * * *", "tz": "Asia/Shanghai" },
  "payload": { "kind": "agentTurn", "message": "任务描述" },
  "sessionTarget": "isolated"
}
```

### 3. 调度类型
- `cron`: 精确时间调度（推荐）
- `every`: 固定间隔（毫秒级）
- `at`: 一次性定时

### 4. 管理操作
- **列出任务**: cron list
- **查看详情**: cron get
- **修改任务**: cron update
- **立即执行**: cron run --force
- **删除任务**: cron remove

### 5. 常见场景
- 每 30 分钟检查邮件/日历
- 每天生成日报
- 每周代码质量扫描
- 定时数据备份
