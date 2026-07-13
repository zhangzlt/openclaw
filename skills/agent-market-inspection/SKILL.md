---
name: agent-market-inspection
description: "Agent Market 每日健康巡检：API 采集 + agent-browser 对话/非对话测试 + 截图 + LLM 评估 + 飞书文档投递"
version: "2.1.0"
metadata:
  {
    "openclaw":
      {
        "emoji": "🌿",
        "requires":
          {
            "bins": ["python3", "agent-browser"],
            "config": ["plugins.entries.feishu.enabled"],
          },
      },
  }
---

# 智能体市场健康巡检

对 [Agent Market](https://agent.digitalchina.com/market) 智能体平台执行全量健康检查：API 数据采集 → agent-browser 对话/非对话测试 → 截图计时 → LLM 评估 → 飞书文档投递。

## 前置条件

- Python 3.11+
- `agent-browser` CLI（Rust 原生 CDP 工具，替代 Playwright）
  ```bash
  npm install -g agent-browser
  agent-browser install
  ```
- 飞书持久浏览器 profile（手机号、密码、验证码或扫码只需在失效时人工完成）
- Token 缓存文件（JWT，约 30 天有效）

## 快速开始

```bash
cd /home/node/.openclaw/workspace/work/agent-market

# 仅 API 采集（3 秒）
python3 inspect_daily.py

# 全量对话测试 + 截图
CHAT_TEST=1 CHAT_TEST_ALL=1 python3 -u inspect_daily.py

# 非对话智能体专项测试
NON_CHAT_TEST=1 python3 -u inspect_daily.py

# 全量测试（对话 + 非对话 + Dify API）
CHAT_TEST=1 CHAT_TEST_ALL=1 NON_CHAT_TEST=1 python3 -u inspect_daily.py
```

## 2.1 版强制运行契约

- 定时任务必须同时设置 `CHAT_TEST=1 CHAT_TEST_ALL=1 NON_CHAT_TEST=1`。
- 实际执行顺序就是市场 API 顺序，不允许先按类型分组后再排序报告。
- 每完成一个智能体立即写截图、截图元数据和 `run.json` 检查点，然后才允许进入下一项。
- 每次运行使用独立 `run_id` 目录，并由 `.inspection.lock` 阻止重叠执行。
- 数量、顺序、唯一 ID、截图或元数据任一不完整时，脚本必须非零退出。
- 报告正文、状态、操作、结果和分析统一使用中文；技术 URL 与产品专名可保留原文。
## 执行流程

### 1. 认证
```
Agent Market Token 缓存验证 (.auth/token.txt)

飞书网页登录态：
  ├─ 优先复用 .auth/feishu-browser-profile
  ├─ 兼容旧版 .auth/playwright_state.json
  └─ 失效 → python3 feishu_login.py → 可视浏览器人工完成一次验证
```

禁止在源码、Skill、定时任务提示词或命令行中保存飞书密码。Cron 与人工登录必须使用同一个 `FEISHU_BROWSER_PROFILE` 路径，并由同一系统用户运行。

### 2. API 采集
```
GET /api/agents/market → 41 个智能体
输出: **总结** (总数/指南/评价/零下载)
```

### 3. 对话测试（22 个）
```
对 22 个对话型智能体:
  1. agent-browser 导航到聊天 URL
  2. 默认使用 1 个稳定冒烟问题（可用 CHAT_QUESTION_COUNT 调整）
  3. contentEditable 输入 → 优先点击发送按钮，失败后按 Enter
  4. chat_wait 只观察本次提问后的新增回复，连续 2 轮稳定，默认最长 60s
  5. _try_screenshot() 截图（成功/失败均截图，返回路径或空字符串）
  6. LLM 评估回复质量（passed/score/issues）
  7. time.time() 计时

Dify API 智能体（如 ID 63）走 HTTP SSE 直连：
  POST https://agent.digitalchina.com/api/chat/stream
  appId 映射表: DIFY_APPID_MAP = {63: 8}
```

### 4. 非对话测试（19 个）
```
智能体按 openType/url 特征自动分类，未经硬编码列表限制:

类型检测:
  ├─ openType=applink → 跳过（需飞书客户端）
  ├─ coze.site 域名 → 跳过（外部平台）
  ├─ SSO 登录 → 跳过（需企业账号）
  ├─ file_upload 型 → 上传测试文件 + 验证关键词
  ├─ web_interactive 型 → 点击/输入/导航交互
  └─ generic → 基础页面可访问性检测

交互定制 (_NON_CHAT_CONFIGS):
  - file_upload: wait_selector / wait_timeout / snapshot_first / post_upload_click / verify
  - web_interactive: custom steps (type/click/press/scroll/sleep)
  - spark_nav: Spark 应用多页面导航检测
  - click_review: 内容审核工具按钮交互

不可达检测: 检查 body 中 "App not found" / "Access unavailable" → unreachable 状态
```

### 5. 报告输出
```
REPORT_PATH=reports/runs/<run_id>/智能体市场巡检报告-<run_id>.md
MANIFEST_PATH=reports/runs/<run_id>/MANIFEST.json
CHECKPOINT=reports/runs/<run_id>/run.json
截图=reports/runs/<run_id>/screenshots/<NNN_agent_id>/<NNN_agent_id_final>.png
元数据=与截图同名的 .json 文件
```


#### 截图与顺序硬门禁
- 以市场 API 返回顺序生成 `inspection_index=1..41`，该序号是测试、截图、MANIFEST 和报告的唯一排序依据。
- 每次只处理一个智能体：完成实际测试 → 停留在最终状态 → 截图 → 校验 PNG → 将截图绑定到同一 `agent_id` → 写入结果；完成前禁止开始下一项。
- 每个智能体只保留一张最终截图，固定保存为 `<NNN_agent_id>/<NNN_agent_id>_final.png`；失败、不可达和受阻页面也必须截图。
- 截图最多重试 3 次，并校验 PNG 签名、文件大小和尺寸。失败不得静默吞掉，必须把该项标记为证据失败后才能继续。
- 报告和飞书文档必须严格按 `inspection_index` 升序渲染，禁止按严重度、状态、智能体类型或截图是否存在重新排序。
- `MANIFEST.sections[i]` 必须携带相同的 `agent_id`、`inspection_index` 和最多一张 `images`；插图时按 section 内绑定关系上传，禁止把标记和图片分别拉平后按位置配对。
## 投递机制：MANIFEST 驱动 + Sub-Agent 模式

### ⚠️ 为什么用 Sub-Agent 而非 Isolated Cron

Isolated cron session **无法使用 `message` 工具**（即使设置 `delivery.mode: none` 也会被拒绝），导致无法向飞书推送文档链接。

**解决方案**：用 `sessions_spawn` 创建 sub-agent，子 agent 继承父 session 上下文，可正常使用 `feishu_doc` 和 `message` 工具。

### 投递流程

```
Main Session (cron/手动触发)
  └─ sessions_spawn → Sub-Agent 执行:
      1. python3 inspect_daily.py（~25 分钟）
      2. read MANIFEST.json
      3. feishu_doc create（标题用 MANIFEST.doc_title）
      4. feishu_doc append(summary_text)
      5. 逐 section:
         ├─ images 为空 → append(section.text)
         └─ images 非空 → append("截图："之前) → upload_image(file_path) → append("用时：...")
      6. message send → 导师飞书
```

### 关键规则

- **append → upload_image → append 必须同 turn 顺序执行**，不能并行
- **upload_image 不传 block_id 和 index**，天然追加到末尾
- **一个 turn 处理一个 section**，不能批量
- **upload_image(file_path=绝对路径, doc_token=文档token)**
- 单个智能体图片上传失败 → 标记投递不完整；不得宣称完整巡检已交付

### 降级方案

| 失败步骤 | 降级行为 |
|----------|----------|
| 脚本超时 | 取 MANIFEST 继续投递流程 |
| feishu_doc create 失败 | message 发送报告摘要 |
| 单个图片上传失败 | 标记投递不完整并保留本地报告，不得宣称交付完成 |
| Sub-agent 整体失败 | message 发送异常原因 |

## 智能体类型总览（41 个）

### 对话型（22 个）

#### feishuapp.cn 平台（5 个）
- [110] 折扣问答小助手
- [109] CTC智能客服
- [83] 新海量采购系统智能助手
- [74] 电子签章智能问答助手
- [73] EB智能客服机器人

#### aily.feishu.cn 平台（16 个）
- [122] MES 2.0 数据查询助手
- [119] 业务签约法人体智能推荐
- [115] DI问答助手
- [108] 有问妙答-营销管理部
- [106] 📝 职场文案速写·全能版
- [105] Agent上架助手
- [101] 短视频选题策划专家
- [99] 美金商务工作/销售开单解答助手
- [95] 职场解忧大师
- [92] 企业文化活动策划助手
- [85] 高效会议评估
- [82] 小新老师沟通课
- [79] Figma产品原型创建助手
- [78] 项目复盘顾问
- [76] 神州问学知识库回答助手
- [72] 文档差异与风险分析专家

#### Agent Market 内嵌 Dify（1 个，API 直连）
- [63] 客户信息查询小助手

### 非对话型（19 个）

#### 交互定制配置（需特殊处理）
```python
_NON_CHAT_CONFIGS = {
    126: {"action": "file_upload", "files": ["sales_contract_test.pdf", "purchase_contract_test.pdf"], "verify": "比对"},
    125: {"action": "spark_nav"},  # Spark 多页导航
    121: {"action": "custom", "steps": [...]},  # type→Enter→scroll
    120: {"action": "custom", "steps": [...]},  # 9 步 input→add→抓阄
    116: {"action": "file_upload", "snapshot_first": True, "post_upload_click": "提取信息"},  # ⚠ App not found
    102: {"action": "custom", "steps": [...]},  # 5 人名→分组
    100: {"action": "click_review"},  # 内容审核
     98: {"action": "file_upload", "files": ["quote_document_test.pdf"], "verify": "核验"},
}
```

#### 不可达（App not found，4 个）
- [123] 售前URS解析助手
- [116] 担保合同&授信合同解析助手
- [112] 企业信息收集表格自动填写
- [61] PDF附件脱敏打码助手

#### 自动跳过（5 个）
- [124] AI短视频约稿平台 → coze.site
- [107] 个人海报生成工具 → coze.site
- [90] 客户小助手 → applink
- [86] 售前项目管理专家 → applink
- [81] 问学超级员工 → SSO 登录

## 报告格式

```markdown
# YYYY年MM月DD日 HH:MM Agent Market 健康巡检报告

**总结**: 41 个智能体, 39/41 有指南, 10/41 有评价

> 📡 [63] 客户信息查询小助手 为市场内嵌 Dify 应用，通过 API 直接测试

---

## 🤖 对话测试详情

✅ 通过: N | ❌ 异常: M | ⏭ 跳过: K | 共 22 个

### ✅ 智能体名称 (ID: XXX)

测试问题1：

```
文本
```

回答结果1：

```
文本
```

截图：

用时：10.2s | 平均用时：8.5s

---

## 🔧 非对话专项测试

✅ 通过: N | ❌ 异常: M | ⏭ 跳过: K | 共 19 个

### ✅ 智能体名称 (ID: XXX)

智能体检测效果分析：

1. 上传文件测试
   - 操作: 上传文件: /path/to/file.pdf
   - 结果: ✅ 成功 (验证关键词: '比对')

截图：

用时：7.0s | 平均用时：7.0s
```

## Knowned Bugs & Fixes（2026-07-10）

### 三连 Bug 导致对话测试全部失败

| 提交 | Bug | 影响 |
|------|-----|------|
| `67b20f0` | `response_text` 变量未定义 | 对话回复解析直接崩溃 |
| `a0315ae` | `screenshot_dir` 应为 `agent_screenshot_dir` | 对话截图函数 NameError |
| `efd98e3` | `aid` 应为 `agent_id`（非对话变量泄露到对话路径） | 21/22 对话 agent 0s 崩溃 |

**教训**：对话和非对话测试路径变量命名不统一（`agent_id` vs `aid`），copy-paste 时容易出错。后续可考虑统一。

### known_good 验证状态（v4）
- 28/36 = 78% 通过率（排除 5 个跳过）
- 4 个对话异常是真问题（非代码 bug）：MES 无权限、折扣问答无回复、Agent上架无回复、电子签章回答质量不合格
- 4 个非对话不可达：全部 App not found（应用权限/下架问题）

## Cron 定时任务

### 配置

| 参数 | 值 |
|------|-----|
| Job ID | `25d841bb-d50a-426e-8146-cccabc97821c` |
| 调度 | 每天 9:00 Asia/Shanghai |
| 模型 | deepseek/deepseek-v4-pro |
| 超时 | 7200s（2 小时，覆盖 41 个智能体顺序执行与证据校验） |
| 投递方式 | Sub-agent 模式（isolated cron 不支持 message 工具） |
| 投递目标 | `ou_12f4e5dbfd82f5975eaa6afd762b1d20`（导师个人飞书） |

## 关键文件

| 文件 | 说明 |
|------|------|
| `work/agent-market/inspect_daily.py` | 主巡检脚本（含对话 + 非对话测试） |
| `work/agent_browser_wrapper/browser.py` | agent-browser Python 封装层 |
| `work/agent-market/utils/llm.py` | LLM 问题生成 + 回复评估 |
| `work/agent-market/crawler/inspector.py` | API 采集模块 |
| `work/agent-market/reports/` | 报告 + MANIFEST + 截图输出目录 |
| `skills/agent-market-inspection/SKILL.md` | 本文件 |

## 飞书 App 凭据

| 参数 | 值 |
|------|-----|
| App ID | `cli_aac1c18a7b7a5cef` |
| App Secret | 存储于 `~/.openclaw/openclaw.json` → `channels.feishu` |
| 权限 | `docx:document`（已开启） |

## 认证信息

| 类型 | 账号 | 方式 |
|------|------|------|
| Agent Market | 从环境变量或本地凭据文件读取，禁止写入 Skill | HTTP JWT |
| 飞书 | 17265205125 | QR 码扫码 |
