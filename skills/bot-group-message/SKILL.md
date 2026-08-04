# 机器人在群内接收消息

飞书群机器人消息接收全量配置清单，用于排查"机器人收不到群消息"问题。

---

## 1. 飞书应用凭证

- 应用名称：选品专家
- App ID：`cli_aac1c18a7b7a5cef`
- App Secret：配置于 `~/.openclaw/openclaw.json` 的 `channels.feishu.appSecret`
- 机器人：已在"选品专家"应用下创建，别名"小球藻"

**验证**：
```bash
python3 -c "import json; d=json.load(open('/home/node/.openclaw/openclaw.json')); print(d['channels']['feishu']['appId'])"
```

---

## 2. 应用权限（必须全部授权）

以下权限需在飞书开放平台 → 应用 → 权限管理中确认已授权：

| 权限标识 | 说明 | 状态 |
|---------|------|------|
| `im:message` | 获取与发送单聊、群聊消息 | ✅ |
| `im:chat` | 获取群信息、群列表 | ✅ |
| `im:chat:read` | 获取群信息、群列表 | ✅ |
| `im:message.group_msg` | 接收群聊消息 | ✅ |
| `im:message.group_at_msg.include_bot:readonly` | 接收被机器人@的消息 | ✅ |
| `im:chat.members:bot_access` | 群内机器人成员管理 | ✅ |
| `im:chat:operate_as_owner` | 以群主身份操作 | ✅ |

**验证**：
```bash
# 飞书开放平台查看：应用 → 权限管理
# 或调用 API：GET /open-apis/auth/v3/tenant_access_token/internal
```

---

## 3. OpenClaw 频道配置

文件：`~/.openclaw/openclaw.json`

```json
{
  "channels": {
    "feishu": {
      "groupPolicy": "allowlist",
      "groupAllowFrom": ["oc_ef1fa8a7905d0480a40a477b8ca605ec"]
    }
  }
}
```

| 配置项 | 说明 |
|-------|------|
| `groupPolicy: "allowlist"` | 只接收白名单内群的消息 |
| `groupAllowFrom: [...]` | 允许接收消息的群 ID 列表 |

**修改后需重启 OpenClaw 或触发热重载**：
```bash
# 方式一：重启网关
openclaw gateway restart

# 方式二：发送 SIGUSR1 信号触发热重载
kill -USR1 $(pgrep -f "openclaw")
```

---

## 4. 飞书群设置 — 添加群机器人

在飞书客户端中：

1. 打开目标群聊
2. 点击右上角 **⚙️ 设置** → **群管理** → **群机器人**
3. 点击**添加机器人**，搜索应用名称（如"选品专家"）
4. 添加完成

> ⚠️ 注意：这不是通过 API `im/v1/chats/:chat_id/members` 添加的群成员，而是飞书"群机器人"功能。API 方式无法添加机器人入群。

**验证群机器人状态**：
```bash
# 群设置 → 群机器人列表应显示"选品专家"
```

---

## 5. WebSocket 连接

OpenClaw 启动时自动建立飞书 WebSocket 长连接。

**日志特征**：
```
feishu[default]: WebSocket client started
[ws], 'ws client ready'
```

**验证连接状态**：
```bash
tail -20 /tmp/openclaw/openclaw-2026-08-03.log | grep -i "feishu.*websocket\|ws client ready"
```

**进程状态**：
```bash
ps aux | grep "openclaw" | grep -v grep
# 应看到 openclaw 进程运行中
```

---

## 6. 消息触发方式

群机器人模式下，机器人**只响应被 @提及的消息**。

| 场景 | 行为 |
|------|------|
| 群内 @小球藻 | ✅ 接收并回复 |
| 群内普通消息（未 @） | ❌ 接收但不回复（按 `AGENTS.md` 沉默规则） |

**触发消息格式**：
```
@小球藻 你好，请帮我...
```

---

## 🧪 排查步骤

### 步骤 1：确认应用凭证
```bash
python3 -c "import json; d=json.load(open('/home/node/.openclaw/openclaw.json')); print('App ID:', d['channels']['feishu']['appId'])"
```

### 步骤 2：确认权限
- 登录飞书开放平台
- 进入「选品专家」应用 → 权限管理
- 确认上述 7 项权限全部已授权（345 项总权限，0 项待审批）

### 步骤 3：确认 OpenClaw 配置
```bash
python3 -c "
import json
d = json.load(open('/home/node/.openclaw/openclaw.json'))
f = d['channels']['feishu']
print('groupPolicy:', f.get('groupPolicy'))
print('groupAllowFrom:', f.get('groupAllowFrom'))
"
```

### 步骤 4：确认群机器人已添加
- 飞书客户端 → 打开目标群 → ⚙️ 设置 → 群管理 → 群机器人
- 列表中应包含"选品专家"

### 步骤 5：确认 WebSocket 连接
```bash
tail -50 /tmp/openclaw/openclaw-2026-08-03.log | grep -i "feishu"
```
应看到 `WebSocket client started` 和 `ws client ready`。

### 步骤 6：实际发送测试消息
在群内发送 `@小球藻 测试`，查看日志：
```bash
tail -20 /tmp/openclaw/openclaw-2026-08-03.log | grep -i "feishu.*message\|received message"
```
应出现类似：
```
feishu[default]: received message from <open_id> in oc_ef1fa8a7905d0480a40a477b8ca605ec (group)
```

---

## 📋 快速检查清单

```
[ ] 应用凭证已配置（app_id + app_secret）
[ ] 7 项核心权限已授权
[ ] groupPolicy = "allowlist"
[ ] groupAllowFrom 包含目标群 ID
[ ] 群机器人已添加到目标群
[ ] WebSocket 连接正常（ws client ready）
[ ] 测试消息 @提及后能正常响应
```

---

## 参考信息

- **目标群 ID**：`oc_ef1fa8a7905d0480a40a477b8ca605ec`
- **群名称**：AI 先锋加速器—"选品专家"
- **机器人别名**：小球藻
- **应用名称**：选品专家
- **应用配置路径**：`channels.feishu`
- **OpenClaw 配置**：`~/.openclaw/openclaw.json`
- **日志路径**：`/tmp/openclaw/openclaw-YYYY-MM-DD.log`
- **群聊规则**：被 @提及时回复，未被 @提及时保持沉默（见 `AGENTS.md`）
