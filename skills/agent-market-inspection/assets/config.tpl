# Agent Market 巡检 — Agent 配置文件模板
# 复制此文件为 config.py 后修改

# ── Agent Market API ──
MARKET_ITCODE = "zhangzlt"          # IT Code
MARKET_PASSWORD = "Zzl.20041006"    # 密码
MARKET_BASE_URL = "https://agent.digitalchina.com"
MARKET_API_URL = f"{MARKET_BASE_URL}/api/agents/market"

# ── 飞书认证 ──
FEISHU_PHONE = "17265205125"
PLAYWRIGHT_STATE_FILE = "{baseDir}/assets/playwright_state.json"
TOKEN_CACHE_FILE = "{baseDir}/assets/token.txt"

# ── LLM 配置 (脚本内评估用) ──
LLM_API_KEY = ""                              # 留空使用内置 API
LLM_BASE_URL = "http://10.0.1.27:8000/v1"    # vLLM 服务
LLM_MODEL = "Qwen/Qwen3.6-35B-A3B"           # 问题生成 + 评估模型

# ── 对话测试 ──
CHAT_TIMEOUT = 10          # 每个问题等待回复的秒数
CHAT_TEST_ALL = True       # 测试全部对话型智能体
CHAT_TEST_BATCH = 0        # 分批大小 (0=全量)
SCREENSHOT_DIR = "{baseDir}/../reports/screenshots"
REPORT_DIR = "{baseDir}/../reports"

# ── 投递目标 (飞书) ──
DELIVERY_TARGET = "ou_12f4e5dbfd82f5975eaa6afd762b1d20"  # 个人飞书 OpenID
DELIVERY_GROUP = "oc_bef4f48fb4870602342af652e5501d86"   # Agent Market 群聊
