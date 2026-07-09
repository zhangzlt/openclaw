# Agent Market 巡检 — Agent 配置文件模板
# 复制此文件为 config.py 后修改

# ── Agent Market API ──
MARKET_ITCODE = "zhangzlt"          # IT Code
MARKET_PASSWORD = "Zzl.20041006"    # 密码
MARKET_BASE_URL = "https://agent.digitalchina.com"
MARKET_API_URL = f"{MARKET_BASE_URL}/api/agents/market"

# ── Dify API（内嵌智能体测试） ──
DIFY_CHAT_URL = f"{MARKET_BASE_URL}/api/chat/stream"   # SSE 流式接口
DIFY_APPID_MAP = {63: 8}          # agent_id → Dify appId（需从前端 JS 提取）

# ── 飞书认证 ──
FEISHU_PHONE = "17265205125"
FEISHU_APP_ID = "cli_aac1c18a7b7a5cef"  # 正确 App（来自 channels.feishu 配置）
PLAYWRIGHT_STATE_FILE = "{baseDir}/assets/playwright_state.json"
TOKEN_CACHE_FILE = "{baseDir}/assets/token.txt"

# ── LLM 配置（脚本内对话测试用） ──
LLM_API_KEY = ""                              # 留空使用本地 vLLM
LLM_BASE_URL = "http://10.0.1.27:8000/v1"    # vLLM Qwen3.6-35B-A3B
LLM_MODEL = "Qwen/Qwen3.6-35B-A3B"           # 问题生成 + 评估模型

# ── 对话测试 ──
CHAT_POLL_MAX = 45          # 最大轮询等待秒数
CHAT_POLL_INTERVAL = 2      # 轮询间隔秒数
CHAT_STABLE_COUNT = 2       # 连续稳定次数（body 不变即判定回复完毕）
CHAT_TEST_ALL = True        # 测试全部对话型智能体
CHAT_TEST_BATCH = 0         # 分批大小 (0=全量)
SCREENSHOT_DIR = "{baseDir}/../reports/screenshots"
REPORT_DIR = "{baseDir}/../reports"
MANIFEST_FILE = "MANIFEST.json"  # 投递清单，供 cron agent 消费

# ── 投递目标（飞书） ──
DELIVERY_TARGET = "ou_12f4e5dbfd82f5975eaa6afd762b1d20"  # 个人飞书 OpenID
