"""
定时巡检调度

支持 cron 表达式或简单间隔调度
"""

import asyncio
import os
from datetime import datetime


class InspectionScheduler:
    """巡检调度器"""

    def __init__(self, interval_hours: int = 24):
        self.interval = interval_hours * 3600  # 转换为秒
        self._running = False

    async def start(self, callback):
        """
        启动调度器

        Args:
            callback: 巡检异步函数
        """
        self._running = True
        print(f"⏰ 巡检调度已启动，间隔: {self.interval / 3600:.0f} 小时")

        while self._running:
            try:
                print(f"\n▶ 定时巡检开始: {datetime.now().isoformat()}")
                await callback()
                print(f"✓ 巡检完成")
            except Exception as e:
                print(f"✗ 巡检失败: {e}")

            await asyncio.sleep(self.interval)

    async def stop(self):
        """停止调度器"""
        self._running = False
        print("⏹ 巡检调度已停止")


def create_scheduler() -> InspectionScheduler:
    """
    创建调度器实例

    从环境变量读取间隔时间，默认 24 小时
    """
    interval = int(os.getenv("INSPECTION_INTERVAL_HOURS", "24"))
    return InspectionScheduler(interval=interval)
