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

## 定时任务投递规则（永久生效）
- 所有 isolated cron 定时任务的 `delivery.to` 必须显式指定目标，不能留空
- 巡检类报告默认投递到群聊 `oc_bef4f48fb4870602342af652e5501d86`

## 项目目录管理规则（永久生效）
- **统一根目录**: `work/` 是全部项目开发的唯一根目录
- **规则**: 所有项目代码、资源素材、产出成果，必须创建在 `work/<project-name>/` 下
- **禁止**: 不允许在工作区根目录直接创建项目文件
- **此规则严格持续遵守，无例外**

