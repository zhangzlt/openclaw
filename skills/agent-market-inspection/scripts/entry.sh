#!/bin/bash
# Agent Market 健康巡检 — Shell 入口
# 适用于需要 shell 前置处理的场景（环境检查、目录创建等）
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
WORKSPACE="${HOME}/.openclaw/workspace"
AGENT_MARKET_DIR="${WORKSPACE}/work/agent-market"

# 检查前置条件
check_deps() {
    if ! command -v python3 &>/dev/null; then
        echo "❌ 需要 Python 3.11+" >&2
        exit 1
    fi
    if ! python3 -c "import playwright" 2>/dev/null; then
        echo "⚠️  playwright 未安装，对话测试将跳过"
        echo "   安装: python3 -m pip install playwright && python3 -m playwright install chromium"
    fi
    if ! python3 -c "import httpx" 2>/dev/null; then
        echo "⚠️  httpx 未安装，API 请求将失败"
        echo "   安装: python3 -m pip install httpx"
    fi
}

# 确保输出目录存在
ensure_dirs() {
    mkdir -p "${AGENT_MARKET_DIR}/reports/screenshots"
    mkdir -p "${AGENT_MARKET_DIR}/.auth"
}

# 运行巡检
run_inspection() {
    cd "${AGENT_MARKET_DIR}"
    export PYTHONPATH="${AGENT_MARKET_DIR}:${PYTHONPATH:-}"
    exec python3 -u "${AGENT_MARKET_DIR}/inspect_daily.py" "$@"
}

check_deps
ensure_dirs
run_inspection "$@"
