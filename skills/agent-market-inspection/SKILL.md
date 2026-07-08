---
name: agent-market-inspection
description: "Agent Market 每日健康巡检：API 采集 + Playwright 对话测试 + 截图 + LLM 评估 + 飞书文档投递"
metadata: { "openclaw": { "emoji": "🌿", "requires": { "bins": ["python3"], "config": ["plugins.entries.feishu.enabled"] } } }
---

# Agent Market 健康巡检

对 [Agent Market](https://agent.digitalchina.com/market) 智能体平台执行全量健康检查：API 数据采集 → Playwright 对话测试 → 截图 → LLM 评估 → 飞书文档投递。

## 前置条件

- Python 3.11+ 环境
- Playwright Chromium（`python3 -m playwright install chromium`）
- 飞书登录态：`work/agent-market/.auth/playwright_state.json`
- Agent Market API token：`work/agent-market/.auth/token.txt`
- 飞书插件已启用

## 执行流程

### 1. 认证检查

```bash
cd {workspace}/work/agent-market
```

- **Token 缓存**：脚本自动验证 `.auth/token.txt`，过期则用 Playwright 重新登录
- **飞书登录态**：`.auth/playwright_state.json`（QR 码扫码获取），有效期约 2-4 周

### 2. 运行巡检脚本

```bash
# 完整巡检（API + 对话测试 + 截图）
cd {workspace}/work/agent-market
CHAT_TEST=1 CHAT_TEST_ALL=1 PYTHONUNBUFFERED=1 python3 -u inspect_daily.py

# 仅 API 采集（3 秒）
python3 inspect_daily.py

# 分批测试（每批 N 个，降低内存压力）
CHAT_TEST=1 CHAT_TEST_BATCH=5 python3 inspect_daily.py
```

脚本输出：
```
REPORT_PATH=workspace/work/agent-market/reports/agent-health-report-YYYYMMDD.md
SCREENSHOT_PATHS_BEGIN
/path/to/screenshots/115/q1_142055.png
/path/to/screenshots/115/q2_142228.png
...
SCREENSHOT_PATHS_END
```

### 3. 投递到飞书文档

**创建文档：**
```
feishu_doc(action="create", title="MM月DD日 HH:MM Agent Market 健康巡检报告")
```
记录返回的 `doc_token`。

**写入报告内容：**

读取 `REPORT_PATH` 输出的 MD 文件，飞书不支持 Markdown 表格，请将表格转换为项目列表（用 `**加粗**` 替代表头）。

```
feishu_doc(action="write", doc_token=上一步的token, content=转换后的报告内容)
```

**上传截图（内嵌图片）：**

对每个截图路径：
```
feishu_doc(action="upload_image", doc_token=同上, file_path=截图绝对路径)
```
每次上传后等待 2 秒。单张失败跳过继续。

**发送通知：**
```
message(action="send", channel="feishu", target="{target_user}",
        message="📊 MM月DD日 HH:MM Agent Market 健康巡检报告\nhttps://digitalchina.feishu.cn/docx/{doc_token}")
```

### 4. 降级方案

| 失败步骤 | 降级行为 |
|----------|----------|
| 脚本超时（15 分钟） | `ls -t work/agent-market/reports/*.md \| head -1` 找最新报告 |
| feishu_doc create 失败 | 用 message 发送报告文本 |
| feishu_doc write 失败 | 用 message 发送报告文本 |
| upload_image 失败 | 跳过，不阻塞主流程 |

## 报告格式

输出 MD 文件结构：
```markdown
# YYYY年MM月DD日 HH:MM Agent Market 健康巡检报告
**一句话总结**: 41 个智能体, 39/41 有指南, 10/41 有评价, 1 零下载
---
## 🤖 对话测试详情
✅ 通过: 18 | ❌ 异常: 3 | 共 22 个
```

### per-agent 格式

```markdown
### ✅ 智能体名称 (ID: XXX)
测试问题1：问题文本
回答结果1：回答文本
用时：10.2s | 平均用时：8.5s

测试问题2：问题文本
回答结果2：回答文本
用时：6.8s | 平均用时：8.5s
```

### 状态图标
- ✅ 通过：有有效回复
- 🟠 对话异常：无回复或回复异常
- 🟡 无法访问：页面无权限或不可达
- ⛔ 跳过：applink 链接（需要客户端）

## 对话型智能体（22 个）

### feishuapp.cn 平台（5 个）
- `a_3687bf8` [110] 折扣问答小助手
- `a_eb9c4b2` [109] CTC智能客服
- `a_c0021ea` [83] 新海量采购系统智能助手
- `a_1f46a3e` [74] 电子签章智能问答助手
- `a_ea846e9` [73] EB智能客服机器人

### aily.feishu.cn 平台（16 个）
- `agent_4km78xx9ykdh` [122] MES 2.0 数据查询助手
- `agent_4kizlorsnna` [119] 业务签约法人体智能推荐
- `agent_4kk2i8qszzmp` [115] DI问答助手
- `agent_4k0s05gxmn2b` [108] 有问妙答-营销管理部
- `agent_4k239vupw3bm` [106] 📝 职场文案速写·全能版
- `agent_4k91r9ngq6h5` [105] Agent上架助手
- `agent_4jo8mumzxqno` [101] 短视频选题策划专家
- `agent_4k7rqzhqp7bk` [99] 美金商务工作/销售开单解答助手
- `agent_4k32eu5jg3mh` [95] 职场解忧大师
- `agent_4jr2pc5twwxh` [92] 企业文化活动策划助手
- `agent_4ip1h8yj7cys` [85] 高效会议评估
- `agent_4hg8g2j7mv56` [82] 小新老师沟通课
- `agent_4k5chpkxx9q4` [79] Figma产品原型创建助手
- `agent_4hnhzbm1idns` [78] 项目复盘顾问
- `agent_4i9918fcqj5n` [76] 神州问学知识库回答助手
- `agent_4j86rzbxjo5p` [72] 文档差异与风险分析专家

### Agent Market 内嵌 Dify（1 个）
- `dify:api` [63] 客户信息查询小助手（openType=api, source=dify）

### 排除（applink，需要飞书客户端）
- [90] 客户小助手（订阅）
- [86] 售前项目管理专家

## 认证信息

### Agent Market
```
IT Code: zhangzlt
密码: Zzl.20041006
Token 缓存: {workspace}/work/agent-market/.auth/token.txt
```

### 飞书登录态
```
维护方式: QR 码扫码
缓存文件: {workspace}/work/agent-market/.auth/playwright_state.json
刷新命令: cd {workspace}/work/agent-market && python3 feishu_login.py
有效期: 约 2-4 周
```

## 定时任务

创建 cron 任务时需要指定：

| 参数 | 推荐值 | 说明 |
|------|--------|------|
| schedule | `0 9 * * *` (Asia/Shanghai) | 每天 9:00 |
| sessionTarget | isolated | 隔离会话 |
| model | deepseek/deepseek-v4-pro | agent 执行用 |
| timeoutSeconds | 900 | 15 分钟 |
| delivery.mode | announce | 结果推送到目标 |
| delivery.channel | feishu | 飞书频道 |
| delivery.to | {target} | 接收人/群 |

### cron agentTurn 提示词模板

```
现在是每天 9:00 的 Agent Market 健康巡检时间。请执行以下步骤：

**Step 1: 运行巡检脚本**
cd {workspace}/work/agent-market && CHAT_TEST=1 CHAT_TEST_ALL=1 PYTHONUNBUFFERED=1 timeout 900 python3 -u inspect_daily.py
记录输出的 REPORT_PATH 和 SCREENSHOT_PATHS_BEGIN/END 之间的截图路径列表。

**Step 2: 创建飞书文档**
feishu_doc(action="create", title="MM月DD日 HH:MM Agent Market 健康巡检报告")
记录返回的 doc_token。

**Step 3: 写入报告内容到飞书文档**
读取 REPORT_PATH 指向的 MD 文件内容。注意：将 Markdown 表格转换为项目列表（飞书不支持表格）。
feishu_doc(action="write", doc_token=上一步的token, content=转换后的报告内容)

**Step 4: 上传截图到飞书文档**
对于 SCREENSHOT_PATHS 列表中的每个截图路径：
feishu_doc(action="upload_image", doc_token=同上, file_path=截图绝对路径)
每次上传后等待 2 秒。失败跳过继续下一个。

**Step 5: 发送文档链接**
message(action="send", channel="feishu", target="{target}",
        message="📊 MM月DD日 HH:MM Agent Market 健康巡检报告\nhttps://digitalchina.feishu.cn/docx/{token}")

⚠️ 约束：
- Step 3 write 失败：降级用 message 发送报告文本
- Step 4 单张截图上传失败跳过
- 脚本超时（900s）：用 ls -t work/agent-market/reports/*.md | head -1 找最新报告
- 不要调用 feishu_doc 的 read 操作
- 不要总结报告内容
```

### 创建 cron 命令

```bash
openclaw cron add \
  --name "Agent Market 每日健康巡检" \
  --schedule "0 9 * * *" --tz "Asia/Shanghai" \
  --session isolated --model deepseek/deepseek-v4-pro \
  --timeout 900 --announce --channel feishu --to {target} \
  --message "（上面的模板提示词）"
```

## 关键设计

### Token 缓存策略
`get_token()`：
1. 检查磁盘缓存（`.auth/token.txt`）→ HTTP 验证
2. 有效 → 直接使用（避免 Playwright 启动开销）
3. 失效 → Playwright 浏览器登录 → 拦截 API Bearer JWT → 写缓存
4. 对 cron 关键：token 缓存有效 = 跳过浏览器，秒级完成 API 采集

### 浏览器崩溃恢复
`run_chat_tests()` 内部：
- 每个 agent 出错后调用 `ensure_browser()` 重建浏览器上下文
- 防止 "browser has been closed" 导致剩余 agent 连锁失败
- 22 个 agent 全量测试中保持可用

### 耗时追踪
- 每条 Q&A 用 `time.time()` 精确计时
- 输出 `用时` 和 `平均用时`（per-agent）
- 帮助发现性能退化

### 回复解析
`_parse_chat_reply()`：
- body_before vs body_after 差分提取新内容
- 过滤 UI 杂物：`/`、`新对话`、`Deep Planning`、`Tools`、`Copy`

## 已知限制

| 问题 | 原因 | 当前方案 |
|------|------|----------|
| 飞书密码登录 | API 拒绝 `zzl20041006` | QR 码扫码登录 |
| applink 不可测 | 需要飞书客户端 | 直接跳过 |
| MES 2.0 无权限 | 创建者限制 | 标记 unreachable |
| Agent Market 内嵌 Dify | 需要市场登录态 | 检测加入，测试待适配 |
| feishu_doc write 表格 | 飞书文档不支持 MD 表格 | 转换表格为项目列表 |
| Gateway scope 限制 | device 默认只有 operator.read | 手动添加 operator.admin |
