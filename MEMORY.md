# 小球藻的长期记忆

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

## Agent Market 巡检系统架构（2026-07-09）
- **脚本入口**: `work/agent-market/inspect_daily.py`
- **投递方式**: cron agent 先 `nohup` 启动本地 HTTP 服务（端口 18990）暴露截图 → 脚本生成 markdown `![](http://127.0.0.1:18990/screenshots/...)` → `feishu_doc write` 自动下载上传 → 最后 kill HTTP 服务
- **关键**: HTTP 服务必须由 cron agent 独立启动（非 Python 脚本内），否则脚本退出后服务即死
- **Cron Job ID**: `25d841bb-d50a-426e-8146-cccabc97821c`，每天 9:00，1200s 超时
- **投递目标**: 个人飞书 `ou_12f4e5dbfd82f5975eaa6afd762b1d20`
- **Dify API 映射**: `DIFY_APPID_MAP = {63: 8}`，新增 Dify 智能体需手动补充映射
- **截图位置**: 报告中 `截图：` 行后跟 `![](...)` URL，Q&A 之后、「用时」之前
- **已知问题**: 需持续观察 `feishu_doc write` 从 `http://127.0.0.1:18990` 下载 20+ 张图片的耗时是否在超时范围内

