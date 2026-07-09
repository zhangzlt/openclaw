"""
agent-browser-wrapper — agent-browser CLI 的 Python 封装

将 Rust 原生 agent-browser 包装为 Python API，替代 Playwright 进行
Agent Market 健康巡检的浏览器对话测试。

用法:
    from agent_browser_wrapper import AgentBrowser, create

    browser = create(state_path=".auth/playwright_state.json")
    browser.open("https://bba12hub36.feishuapp.cn/ai/gui/chat/a_xxx")
    browser.chat_send("你好")
    reply = browser.chat_wait(timeout=45)
    browser.screenshot("test.png")
    browser.close()
"""

from .browser import AgentBrowser, create, AgentBrowserError

__all__ = ["AgentBrowser", "create", "AgentBrowserError"]
__version__ = "0.1.0"
