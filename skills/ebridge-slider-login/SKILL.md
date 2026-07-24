---
name: "ebridge-slider-login"
description: "神州商桥双链路滑块登录: 会员+商城SSO"
---

# 神州商桥滑块验证码自动登录

自动登录 `https://t-opservice.e-bridge.com.cn`(神州商桥, B2B IT采购商城测试环境)。登录受「向右滑动完成拼图」验证码保护, 需先用 OpenCV 算出缺口X坐标, 再提交验证, 最后表单登录。

## 两套登录系统(先读!)

该站有两套并行的登录子系统, **会话互不相通**:

| 系统 | 登录入口 | 作用 | 局限 |
|------|---------|------|------|
| **dcOauthLogin** (推荐默认) | `GET /login` → OAuth authorize 链 | 获得**商城已认证会话**: 首页"你好,公司名"、`/homePage/ajaxGetUserInfo` 等已认证接口 | 需走完整 SSO 重定向链 |
| dcNewOauthLogin | `/dcNewOauthLogin/mall/login` | 仅验证会员系统账号密码有效 | 拿到的 JSESSIONID **不能**认证商城, ajaxGetUserInfo 返回"获取用户信息失败" |

**选型规则**:
- 需要以登录态访问商城(看首页"你好"、调 /homePage/* 接口、下单浏览等) → **dcOauthLogin SSO 链**, 用 `assets/ebridge_mall_login.py`
- 只想快速验证账号密码是否有效 → dcNewOauthLogin 链, 用 `assets/ebidge_login.py`(注意文件名 typo, 少个 r)

## 适用场景

- 自动登录神州商桥 / e-bridge 会员系统与商城
- 程序化破解该站的滑块拼图验证码
- 获取商城已认证会话, 查询登录用户信息(公司名/姓名/主账号)
- 把登录流程接入 Dify 工作流或其他自动化平台
- 复用滑块求解逻辑到类似系统

## 总体架构

### 链路 A: dcOauthLogin SSO(商城会话, 推荐)

```
┌────────────────────────────────────────────────────────────┐
│  Step A: GET https://t-opservice.e-bridge.com.cn/login     │
│    → 302 /dcOauthLogin/oauth/authorize?response_type=code  │
│          &client_id=dcmall_249&scope=all                   │
│          &redirect_uri=https://.../client/callback&state=x │
│    → 302 /dcOauthLogin/mall/login (authorize 把 OAuth 请求 │
│          存入 dcOauthLogin 会话 = SavedRequest)            │
│    → cookie: JSESSIONID(Path=/) + lsessionid(Path=/dcOauthLogin)│
├────────────────────────────────────────────────────────────┤
│  Step B: POST /dcOauthLogin/checkImage/getImages           │
│    → srcImage(背景308×150) + markImage(拼图块50×50 BGRA)    │
│      + locationY                                           │
├────────────────────────────────────────────────────────────┤
│  Step C: OpenCV 边缘 NCC 匹配 → moveX = gap_x - min_x      │
├────────────────────────────────────────────────────────────┤
│  Step D: POST /dcOauthLogin/checkImage/checkImageMatch     │
│    body: moveX=<int> → {"success": true/false}             │
├────────────────────────────────────────────────────────────┤
│  Step E: POST /dcOauthLogin/login?                         │
│    body: username/password/clientId="" / mainVersion="-1"  │
│    (allow_redirects=True)                                  │
│    → 302 /dcOauthLogin/login → 302 /login                  │
│    → 302 authorize(会话已认证, 发 code)                     │
│    → 302 /client/callback?code=xxx&state=xxx               │
│    → 200 商城首页 https://t-opservice.e-bridge.com.cn/     │
├────────────────────────────────────────────────────────────┤
│  Step F: POST /homePage/ajaxGetUserInfo                    │
│    (X-Requested-With: XMLHttpRequest)                      │
│    → {success:true, data:{companyName, user:{...}}}        │
└────────────────────────────────────────────────────────────┘
```

### 链路 B: dcNewOauthLogin(仅验证账号, 无商城会话)

```
GET  /dcNewOauthLogin/mall/login            → lsessionid cookie
POST /dcNewOauthLogin/checkImage/getImages  → 验证码图片
POST /dcNewOauthLogin/checkImage/checkImageMatch → moveX 验证
POST /dcNewOauthLogin/login?                → 302, Location 无 error = 成功
```

**关键特性:**
- 密码**明文传输**, 无需 RSA 加密
- 无特殊请求头(不需要 kskip 之类的隐藏头)
- 图片显示比例 **1:1**(308×150 / 50×50, 无缩放), moveX 直接等于图片像素坐标
- 验证码与验证请求通过 **session cookie (lsessionid)** 关联, 按 Path 隔离不能混用
- 两套系统的滑块机制**完全相同**, 仅接口路径前缀不同

## 关键参数

### 接口清单

| 步骤 | 链路A (dcOauthLogin) | 链路B (dcNewOauthLogin) |
|------|---------------------|------------------------|
| 触发会话 | GET `{HOST}/login` (跟随302链) | GET `{BASE_B}/mall/login` |
| 获取验证码 | POST `{BASE_A}/checkImage/getImages` | POST `{BASE_B}/checkImage/getImages` |
| 验证滑块 | POST `{BASE_A}/checkImage/checkImageMatch` | POST `{BASE_B}/checkImage/checkImageMatch` |
| 登录 | POST `{BASE_A}/login?` | POST `{BASE_B}/login?` |
| 用户信息 | POST `{HOST}/homePage/ajaxGetUserInfo` | (不可用, 会话不通) |

其中 `HOST = https://t-opservice.e-bridge.com.cn`, `BASE_A = {HOST}/dcOauthLogin`, `BASE_B = {HOST}/dcNewOauthLogin`

### 请求头(所有请求通用)

```
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36
X-Requested-With: XMLHttpRequest
Accept: application/json, text/javascript, */*; q=0.01
Referer: <对应登录页, 如 {BASE_A}/mall/login>
```

### getImages 响应格式

```json
{
  "success": true,
  "data": {
    "srcImage": "<base64 PNG, 308x150, 背景图含缺口>",
    "markImage": "<base64 PNG, 50x50, 拼图块带alpha通道>",
    "locationX": 0,
    "locationY": 86
  }
}
```

- `srcImage`: 背景大图, 纯base64(无 `data:image/png;base64,` 前缀)
- `markImage`: 拼图小块, 带alpha透明通道(BGRA), 纯base64
- `locationX`: **始终为0, 不是答案**, 忽略
- `locationY`: 缺口Y坐标(每次变化, 用于约束搜索范围)

### 登录表单字段(两套系统相同)

```
username: 登录名(明文)
password: 密码(明文)
clientId: 空字符串 ""
mainVersion: "-1"
```

### 登录结果判断

- 链路B: HTTP 302 且 Location **不含** `error` = 成功; 含 `error` = 账号密码错误
- 链路A: 跟随重定向后最终 URL **不含** `error` 且 ajaxGetUserInfo 返回 `success:true` = 成功

### 首页"你好"显示规则(top.js 逻辑)

商城首页顶栏由 `POST /homePage/ajaxGetUserInfo` 填充:
- `data.user.primaryAccount == true`(主账号) → 显示 `你好, {companyName}`(如"你好, 霸州市利通科技有限公司")
- 子账号(primaryAccount=false) → 显示 `你好, {companyName}_{userName}`
- ajaxGetUserInfo 返回 `{"success":false,"error":"获取用户信息失败"}` = **商城会话未建立**(不是账号密码问题), 检查是否走了链路A

## 求解算法(核心, 两套系统通用)

### 原理

背景图的缺口区域被白色拼图轮廓替代, 颜色信息失真, 所以用**边缘(Canny)匹配**而非颜色匹配。

### 步骤详解

1. **解码图片**: base64 → numpy array → cv2.imdecode
   - 背景图: `cv2.IMREAD_COLOR` (BGR, 308×150×3)
   - 拼图块: `cv2.IMREAD_UNCHANGED` (BGRA, 50×50×4, 含alpha)

2. **提取有效区域**: 从拼图块的alpha通道找非零像素边界
   - `min_x` = alpha通道每列最大值 > 0 的最左列索引 (通常≈7)
   - 裁剪掉透明边距, 得到 `mark_crop` (纯拼图内容)

3. **Canny边缘检测**:
   - 背景: `cv2.Canny(bg_gray, 100, 200)`
   - 拼图块: `cv2.Canny(mark_gray, 100, 200)`

4. **Y约束搜索**: 只在 `[locationY - 20, locationY + 20]` 行范围内搜索

5. **X范围约束(关键!)**: 排除最左边35像素(拼图块初始位置区域)和最右边10像素
   - 不排除会导致误匹配到初始位置附近, 通过率从100%降到75%

6. **模板匹配**: `cv2.matchTemplate(roi_edges, mark_edges, cv2.TM_CCOEFF_NORMED)`
   - `minMaxLoc` 取最大值位置 → `match_x`

7. **计算 moveX**: `moveX = match_x - min_x`
   - 因为 matchTemplate 用的是裁掉左边透明区后的 mark_crop
   - 而实际 slideImage 的 left 定位包含 min_x 偏移

### 算法参数总结

| 参数 | 值 | 说明 |
|------|-----|------|
| Canny 低阈值 | 100 | 背景和小图相同 |
| Canny 高阈值 | 200 | 背景和小图相同 |
| Y搜索范围 | locationY ± 20 | 服务器给出的Y坐标附近 |
| X最小搜索 | 35px | 排除拼图块初始位置 |
| X最大搜索 | 图片宽 - 模板宽 - 10 | 排除右边缘 |
| 匹配方法 | TM_CCOEFF_NORMED | 归一化相关系数 |

## 完整可执行脚本

两个脚本均位于本技能目录 `assets/` 下, 共享求解器 `solve_slider`。

### 使用方法

**重要: 必须先向用户索要账号和密码, 不得假设或使用任何默认凭据。**

```bash
# 1. 确保 Python 环境有 opencv-python-headless, numpy, requests
pip install opencv-python-headless numpy requests

# 2. 商城登录(推荐, 获得已认证会话)
python ebridge_mall_login.py --username <用户提供的账号> --password "<用户提供的密码>"

# 3. 仅验证账号密码(会员系统)
python ebidge_login.py --username <用户提供的账号> --password "<用户提供的密码>"

# 4. 作为模块导入
from ebridge_mall_login import EbridgeMallLogin
client = EbridgeMallLogin()
result = client.login("<账号>", "<密码>")
print(result["user_info"]["data"]["companyName"])  # 首页"你好"后的公司名

from ebidge_login import EbridgeLogin   # 注意文件名 typo: ebidge
client = EbridgeLogin()
result = client.login("<账号>", "<密码>")
```

### 智能体使用协议

1. 检查用户是否已提供账号和密码; 未提供则**必须询问**, 不得跳过
2. 判断目的: 需要商城登录态/用户信息 → `ebridge_mall_login.py`; 仅验证凭据 → `ebidge_login.py`
3. 拿到凭据后再执行脚本
4. 绝不将账号密码写入文件、日志或输出中(仅在命令行参数中传递)

### 脚本核心逻辑(给智能体的伪代码)

```python
# 链路A(商城会话):
session = 新建HTTP会话(保持cookie)
GET  "{HOST}/login" (allow_redirects=True)
     # → 落到 /dcOauthLogin/mall/login, 拿 JSESSIONID + lsessionid(/dcOauthLogin)
data = POST "{BASE_A}/checkImage/getImages".json()["data"]
move_x = solve_slider(data["srcImage"], data["markImage"], data["locationY"])
assert POST "{BASE_A}/checkImage/checkImageMatch" body: moveX=move_x → success
POST "{BASE_A}/login?" body: username/password/clientId="" / mainVersion="-1"
     (allow_redirects=True)
     # → 302链: /dcOauthLogin/login → /login → authorize发code → /client/callback → 商城首页
info = POST "{HOST}/homePage/ajaxGetUserInfo" (X-Requested-With: XMLHttpRequest)
     # → info["data"]["companyName"] 即首页"你好"后的内容

# solve_slider 内部:
bg = imdecode(b64decode(srcImage), IMREAD_COLOR)       # 308×150 BGR
mark = imdecode(b64decode(markImage), IMREAD_UNCHANGED) # 50×50 BGRA
min_x = alpha通道每列最大值>0的最左列号
mark_crop = 裁掉透明边距
bg_edges = Canny(灰度(bg), 100, 200)
mk_edges = Canny(灰度(mark_crop), 100, 200)
roi = bg_edges[loc_y-20 : loc_y+20+mk_height, :]
result = matchTemplate(roi, mk_edges, TM_CCOEFF_NORMED)
排除 x<35 和 x>宽度-模板宽-10
move_x = 最大值位置 - min_x
```

## 步骤(手动复现)

### 1. 环境准备

```bash
python -m venv .venv
.venv\Scripts\activate  # Windows
# source .venv/bin/activate  # Linux/Mac
pip install opencv-python-headless numpy requests
```

### 2. 运行脚本

```bash
python ebridge_mall_login.py --username <用户提供的账号> --password "<用户提供的密码>"
```

### 3. 验证结果

成功输出示例:
```
[A] GET /login 触发 OAuth 链...
    落地: http://t-opservice.e-bridge.com.cn/dcOauthLogin/mall/login
[B~D] 滑块验证码求解...
    滑块验证通过
[E] POST /dcOauthLogin/login? (username=bzltkj)...
    落地: https://t-opservice.e-bridge.com.cn/  重定向链: [302, 302, 302, 302]
[F] POST /homePage/ajaxGetUserInfo ...
    你好,霸州市利通科技有限公司  (登录名: bzltkj, 主账号: True)
```

## 登录后 Cookie 清单

| Cookie | Domain | Path | 用途 |
|--------|--------|------|------|
| JSESSIONID | .t-opservice.e-bridge.com.cn | / | 全域会话标识(注意: 单独拿到不等于商城已认证) |
| lsessionid | t-opservice.e-bridge.com.cn | /dcOauthLogin | OAuth子系统会话(链路A滑块验证依赖) |
| lsessionid | t-opservice.e-bridge.com.cn | /dcNewOauthLogin | 会员登录系统会话(链路B滑块验证依赖) |

链路A登录成功后, 商城认证状态由 `/client/callback?code=xxx` 一步建立(服务端用 code 换会话), 之后 `ajaxGetUserInfo` 才返回 success。

## 浏览器自动化注意事项

### 不要混用 API getImages 和浏览器 UI 验证码

前端点击"点击登录"后会自行调用 getImages 显示滑块。如果你此时通过 `fetch` 再调一次 getImages, **会刷新服务端答案**, 导致浏览器上显示的验证码失效。

### 前端 JS 拦截登录提交

登录按钮的 click 事件被前端 JS 拦截, 要求滑块在 UI 上被**实际拖动**(检查本地 JS 变量), 仅通过 API 调用 checkImageMatch 通过后点击按钮无效。

### 正确的浏览器登录方案

```
1. Python API 走链路A完成全流程登录(GET /login → 滑块 → POST /dcOauthLogin/login?)
2. 用 allow_redirects=True 跟随重定向, 提取所有 cookie
3. 在 Playwright 中注入 cookie:
   page.context().addCookies([
     {name:'JSESSIONID', value:'...', domain:'.t-opservice.e-bridge.com.cn', path:'/', httpOnly:true},
     {name:'lsessionid', value:'...', domain:'t-opservice.e-bridge.com.cn', path:'/dcOauthLogin', httpOnly:true}
   ])
4. 导航到 https://t-opservice.e-bridge.com.cn/ 即可看到已认证页面("你好,公司名")
```

### fetch redirect:'follow' 的坑

浏览器内 `fetch('/dcOauthLogin/login?', {redirect:'follow'})` 会因跨协议(https→http)重定向而抛 `TypeError: Failed to fetch`。不要尝试在浏览器 fetch 中跟随登录重定向。

## 陷阱(Pitfalls)

- **dcNewOauthLogin 登录成功 ≠ 商城已认证!** 商城(client_id=dcmall_249)的 OAuth 注册在 dcOauthLogin, 两套会话不互通。只走链路B时 authorize 会拒绝并重定向回 dcOauthLogin 登录页, ajaxGetUserInfo 返回"获取用户信息失败"。
- **链路A必须先 GET /login 触发 OAuth 链**: 让 authorize 把 SavedRequest 存进 dcOauthLogin 会话, 否则登录 POST 成功后不会自动跳回商城。
- **两套系统滑块接口路径不同**(`/dcOauthLogin/checkImage/*` vs `/dcNewOauthLogin/checkImage/*`), 机制相同; lsessionid 按 Path 隔离, 不能混用。
- **locationX 始终为 0, 不是答案!** 必须用 OpenCV 自己算 moveX。
- **必须排除左侧35px搜索范围**: 拼图块初始位置在左侧, 不排除会误匹配, 通过率从100%降到75%。
- **图片是纯base64, 没有 `data:image/png;base64,` 前缀**: 直接 base64.b64decode。
- **markImage 是 BGRA 四通道**: 必须用 `cv2.IMREAD_UNCHANGED` 解码, 否则丢失 alpha 通道。
- **session cookie 必须保持**: getImages 和 checkImageMatch 必须在同一 session 中调用。
- **密码明文传输**: 不需要任何加密。
- **登录结果看 302 Location / 最终URL**: 不是看 response body。含 "error" = 失败。
- **SSL证书问题**: 测试环境证书可能不受信任, 需要 `verify=False` 或忽略证书警告。
- **验证失败时不要重试同一个 moveX**: 每次 getImages 都会刷新服务器端答案, 必须重新取图重新算。
- **图片尺寸固定 308×150, 拼图块 50×50**: 显示1:1, moveX 直接是像素值, 无需缩放。
- **assets 文件名 typo**: 会员登录脚本叫 `ebidge_login.py`(少个 r), import 时注意。

## 验证清单

- [ ] `pip install opencv-python-headless numpy requests` 成功
- [ ] getImages 返回 `success: true` 且 srcImage/markImage 非空
- [ ] solve_slider 输出 move_x 在 35~280 范围内
- [ ] checkImageMatch 返回 `{"success": true}`
- [ ] 链路A: 登录 POST 后重定向链含 `/client/callback?code=`, 最终落到商城首页
- [ ] 链路A: ajaxGetUserInfo 返回 `success: true` 且含 companyName
- [ ] 链路B: 登录返回 302 且 Location 不含 "error"
