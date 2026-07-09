#!/usr/bin/env python3
"""
Agent Market 健康巡检 — 主入口脚本

负责完整巡检流程：
1. Token 缓存验证 / 登录
2. API 采集全体智能体数据
3. Playwright 对话测试（含截图 + 计时）
4. Dify API 测试（openType=api + source=dify）
5. LLM 评估回复质量
6. 生成完整报告（MD）+ 投递清单（MANIFEST.json）

用法:
    # 仅 API 采集
    python3 main.py

    # 全量对话测试
    CHAT_TEST=1 CHAT_TEST_ALL=1 python3 -u main.py

    # 分批测试
    CHAT_TEST=1 CHAT_TEST_BATCH=5 python3 -u main.py

输出 (stdout):
    REPORT_PATH=reports/agent-health-report-YYYYMMDD.md
    MANIFEST_PATH=reports/MANIFEST.json
"""

import sys
import os
from pathlib import Path

# ---------- 路径解析 ----------
BASE_DIR = Path(__file__).resolve().parent.parent  # skill root
WORKSPACE = Path.home() / ".openclaw" / "workspace"
AGENT_MARKET_DIR = WORKSPACE / "work" / "agent-market"

# ---------- 转发到实际脚本 ----------
def main():
    script = AGENT_MARKET_DIR / "inspect_daily.py"
    if not script.exists():
        print(f"❌ 脚本不存在: {script}", file=sys.stderr)
        print(f"   请确认 {AGENT_MARKET_DIR} 目录已部署", file=sys.stderr)
        sys.exit(1)

    # 设置 PYTHONPATH 确保 utils 等模块可导入
    env = os.environ.copy()
    pythonpath = str(AGENT_MARKET_DIR)
    if "PYTHONPATH" in env:
        env["PYTHONPATH"] = f"{pythonpath}:{env['PYTHONPATH']}"
    else:
        env["PYTHONPATH"] = pythonpath

    # 传递所有环境变量
    os.chdir(str(AGENT_MARKET_DIR))
    os.execve(sys.executable, [sys.executable, "-u", str(script)], env)

if __name__ == "__main__":
    main()
