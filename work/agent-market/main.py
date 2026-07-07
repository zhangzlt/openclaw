"""
主入口 - Agent Market 健康巡检

流程:
1. 加载环境配置
2. 启动浏览器并登录
3. 采集智能体列表
4. 逐个巡检智能体
5. 生成报告并通知
"""

import asyncio
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).parent
sys.path.insert(0, str(ROOT_DIR))

from config import ensure_dirs, VERIFIED_AGENTS
from browser.login import login
from browser.playwright_setup import BrowserManager
from crawler.collector import AgentCollector
from crawler.inspector import AgentInspector
from reporter.report import generate_report
from notifier.feishu import send_feishu


async def run_inspection():
    """执行完整巡检流程"""
    print("=" * 60)
    print("Agent Market 健康巡检")
    print("=" * 60)

    # 1. 准备目录
    ensure_dirs()
    print(f"\n[1/5] 目录就绪: {ROOT_DIR}")

    # 2. 初始化浏览器
    browser_mgr = BrowserManager()
    await browser_mgr.start()
    print("[2/5] 浏览器已启动")

    # 3. 登录
    await login(browser_mgr)
    print("[3/5] 登录成功")

    # 4. 采集智能体列表
    collector = AgentCollector(browser_mgr)
    agents = await collector.collect_agents()
    print(f"[4/5] 采集完成: 发现 {len(agents)} 个智能体")

    # 5. 巡检每个智能体
    inspector = AgentInspector(browser_mgr, agents)
    results = await inspector.inspect_all()
    print(f"[5/5] 巡检完成: {results.get('summary', {})}")

    # 6. 生成报告
    report = generate_report(results)
    print(f"\n报告已生成: {report['path']}")

    # 7. 飞书通知
    if results.get("summary", {}).get("failed_count", 0) > 0:
        await send_feishu(report)
        print("异常报告已发送到飞书")

    # 8. 清理
    await browser_mgr.stop()
    print("\n✅ 巡检完成")
    return results


if __name__ == "__main__":
    asyncio.run(run_inspection())
