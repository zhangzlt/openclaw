# 小球藻的长期记忆

## 语言偏好（永久生效）
- **回答语言**: 始终用中文回答导师的问题，除非导师明确要求使用其他语言

## GitHub 代码仓库

## GitHub 代码仓库
- **仓库地址**: https://github.com/zhangzlt/openclaw.git
- **账号**: zhangzlt
- **邮箱**: zhangzlt@digitalchina.com
- **认证**: Personal Access Token (已配置于 git remote)
- **用途**: 存储 OpenClaw 工作区全部代码、配置、skill 模块

### 快捷指令
导师说「上传 GitHub」「推送到 GitHub」「上传代码到 GitHub」等 → 直接 `cd /home/node/.openclaw/workspace && git add -A && git commit && git push origin master`

### 文档规范
所有 Markdown 文档必须使用中文撰写（含 AGENTS.md、SOUL.md、MEMORY.md、README 等全部 .md 文件）。

## 飞书联系方式
- **个人飞书 OpenID**: `ou_12f4e5dbfd82f5975eaa6afd762b1d20`
- **Agent Market 群聊 chat_id**: `oc_bef4f48fb4870602342af652e5501d86`
- **飞书 App 凭据**: app_id=`cli_aac1c18a7b7a5cef`，app_secret 存于 `~/.openclaw/openclaw.json` 的 `channels.feishu` 路径

## 定时任务投递规则（永久生效）
- 所有 isolated cron 定时任务的 `delivery.to` 必须显式指定目标，不能留空
- **巡检报告投递到个人飞书**: `ou_12f4e5dbfd82f5975eaa6afd762b1d20`

## 项目目录管理规则（永久生效）
- **统一根目录**: `work/` 是全部项目开发的唯一根目录
- **规则**: 所有项目代码、资源素材、产出成果，必须创建在 `work/<project-name>/` 下
- **禁止**: 不允许在工作区根目录直接创建项目文件
- **此规则严格持续遵守，无例外**

## Agent Market 巡检系统架构（2026-07-14 重构版）

### 核心流程：剧本优先 + LLM 降级
```
读取市场数据
 → 命中已有剧本 → 确定性回放
 → 无缓存 / 剧本失败 → 页面探测(snapshot+text+screenshot)
   → LLM 生成受限 JSON 操作计划
   → agent-browser 按白名单执行
   → 验证业务结果
   → 最终截图
   → 保存执行日志
   → 成功剧本缓存到 playbooks/cache.json
```

### 关键模块
- **脚本入口**: `work/agent-market/inspect_daily.py`（`_run_unified_inspection` 统管全量巡检）
- **剧本缓存**: `work/agent-market/utils/playbook.py` — `PlaybookCache` 管理 `playbooks/cache.json`
- **剧本执行**: `work/agent-market/utils/executor.py` — `PlaybookExecutor` 按白名单执行 JSON 计划
- **LLM 规划**: `work/agent-market/utils/planner.py` — `plan_operations()` 生成受限操作计划
- **执行日志**: `work/agent-market/playbooks/logs/` — 每次执行的结构化日志
- **白名单操作**: open, click, fill, chat_send, chat_wait, press, hover, find_and_click, upload, snapshot, screenshot, eval, scroll, wait, verify

### 投递方式
cron agent 运行脚本 → 提取 MANIFEST.json → 创建飞书文档 → 逐 agent append 文字 + upload_image 截图 → 发送链接
- **关键**: `feishu_doc append` 的 `![]()` markdown 不会自动下载本地图片（已验证 images_processed=0），必须用 `feishu_doc(action="upload_image", file_path=...)` 单独上传
- **agent-browser PATH**: 需确保 cron 环境中 `agent-browser` 可访问（已软链到 `~/.local/bin`）
- **Cookie 同步（2026-07-13）**: OpenClaw browser（CDP）和 agent-browser（独立 Chromium）cookie 不互通。登录后需从 CDP 浏览器导出 cookie → `playwright_state.json`
- **Cron Job ID**: `25d841bb-d50a-426e-8146-cccabc97821c`，每天 9:00，1800s 超时，`timeout 1200`
- **投递目标**: 个人飞书 `ou_12f4e5dbfd82f5975eaa6afd762b1d20`
- **Dify API 映射**: `DIFY_APPID_MAP = {63: 8}`，新增 Dify 智能体需手动补充映射
- **agent-browser CLI**: 全局安装于 `/home/node/.npm-global/bin/agent-browser`（v0.31.1），封装于 `work/agent_browser_wrapper/browser.py`

### chat_send 输入框探测（2026-07-14 修复）
- `browser.py` 的 `chat_send` 新增 `_detect_chat_input()` 方法，按优先级探测: `[contenteditable]` → `textarea` → `input`
- 解决 13 个 textarea/input 型智能体因硬编码 `[contenteditable]` 而批量报 `exit=1` 的问题

### 已知平台问题
- Agent Market 平台部分 agent 页面间歇性返回 `ApiError: 遇到问题了，请稍后重试`，导致 agent-browser 无法加载聊天 UI。此为平台侧故障，非我方代码或登录态问题
- **飞书登录态过期处理规则（2026-07-13 生效）**: 巡检过程中检测到飞书登录态过期 → 截图登录页面 → 通过飞书消息发送截图给导师（个人飞书 `ou_12f4e5dbfd82f5975eaa6afd762b1d20`）→ 等待导师扫码登录 → 确认登录成功后继续执行剩余巡检任务。禁止在登录态过期时直接标记为异常跳过。
- **飞书登录态图片发送问题（2026-07-13）**: 飞书消息发送截图可能因文件被清理而无法显示。改用 webchat 的 MEDIA 附件方式展示二维码，导师扫码后再通过飞书消息确认登录成功。
- **飞书登录态恢复操作流程（2026-07-13 验证有效）**:
  1. 用 `browser open` 打开全新标签页（URL 带 `_t=<时间戳>` 防缓存）：`https://accounts.feishu.cn/accounts/page/login?app_id=149&no_trap=1&query_scope=all&redirect_uri=https%3A%2F%2Faily.feishu.cn%2Fai%2Fagents%2Fagent_4jn4cnjeurc3r`
  2. 等页面加载（sleep 3s），用 Python Playwright 通过 CDP（`http://127.0.0.1:18800`）连接浏览器
  3. `page.evaluate` 读取 `document.querySelector('canvas').toDataURL('image/png')` 获取二维码 base64
  4. base64 解码保存为 PNG，复制到 workspace 根目录
  5. 通过 webchat `MEDIA:` 展示给导师（飞书消息图片不可靠）
  6. 导师飞书扫码后，用 `browser snapshot` 确认页面跳转到 aily.feishu.cn/ai/agents 即为成功
  7. ⚠️ 关键：不要用飞书消息发图片（文件路径可能被清理），用 webchat MEDIA；每次用全新标签页避免缓存；二维码有效期短，需尽快操作

