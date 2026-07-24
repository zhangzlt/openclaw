"""
agent-browser Python 封装层

将 agent-browser CLI(Rust 原生 CDP 工具)封装为 Python API,
用于替代 Playwright 进行 Agent Market 对话测试。

核心流程:
    browser = AgentBrowser(state_path="...")
    browser.open(chat_url)
    browser.chat_send("你好")        # 自动点击 contentEditable + 输入 + 发送
    reply = browser.chat_wait(timeout=45)  # 轮询 body 稳定
    browser.screenshot("out.png")
    browser.close()

依赖:
    npm install -g agent-browser
    agent-browser install
"""

import subprocess
import time
import json
import os
import shutil
from pathlib import Path
from typing import Optional, List, Dict, Any


class AgentBrowserError(Exception):
    """agent-browser 命令执行异常"""
    def __init__(self, message: str, stderr: str = "", exit_code: int = -1):
        super().__init__(message)
        self.stderr = stderr
        self.exit_code = exit_code


class AgentBrowser:
    """agent-browser 浏览器会话封装"""

    # ── 类配置 ──
    BIN = "agent-browser"                        # 二进制路径(需在 PATH 中)
    DEFAULT_SESSION = "agent-market-inspect"     # 默认会话名
    DEFAULT_TIMEOUT = 30                         # 默认命令超时(秒)

    def __init__(
        self,
        state_path: Optional[str] = None,
        profile_path: Optional[str] = None,
        session: str = DEFAULT_SESSION,
        bin_path: Optional[str] = None,
    ):
        """
        Args:
            state_path: Playwright/agent-browser auth state JSON 路径
            profile_path: agent-browser 持久 Chrome profile 目录
            session: 会话名(用于 daemon 隔离)
            bin_path: agent-browser 二进制路径(默认从 PATH 查找)
        """
        self.state_path = state_path
        self.profile_path = profile_path or os.getenv("FEISHU_BROWSER_PROFILE", "").strip() or None
        # 禁止同时使用（agent-browser 0.31.1 不允许）
        assert not (self.state_path and self.profile_path), (
            "agent-browser 不能同时使用 profile 和 storage_state。"
            "请调用 _agent_browser_auth_kwargs() 获取互斥参数"
        )
        self.session = session
        self._bin = bin_path or self._find_bin()
        self._opened = False
        self._state_loaded = False   # 首次 open 后标记,避免重复 --state
        self._url: Optional[str] = None

    # ── 公开 API ──

    def open(
        self,
        url: str,
        timeout: int = 30,
        wait_sec: float = 3.0,
        wait_selector: Optional[str] = None,
        wait_timeout: float = 15.0,
    ) -> "AgentBrowser":
        """打开 URL(首次含 state 载入,后续仅导航)

        Args:
            url: 目标页面 URL
            timeout: 打开超时秒数
            wait_sec: 页面加载后等待秒数
            wait_selector: 等待指定 CSS 选择器出现
            wait_timeout: 等待选择器的超时秒数
        """
        args = ["--session", self.session]
        if self.state_path and not self._state_loaded:
            args.extend(["--state", self.state_path])

        self._run(args + ["open", url], timeout=timeout)
        self._opened = True
        self._state_loaded = True

        self._url = url

        # 等待页面就绪
        time.sleep(wait_sec)
        if wait_selector:
            self.wait_for_selector(wait_selector, timeout=wait_timeout)

        return self

    def close(self, timeout: int = 10):
        """关闭浏览器"""
        if self._opened:
            try:
                self._run(["--session", self.session, "close"], timeout=timeout)
            except AgentBrowserError:
                pass  # 浏览器可能已关闭
            self._opened = False

    # ── 标签页管理 ──

    def list_tabs(self) -> List[Dict[str, str]]:
        """列出当前会话所有标签页。

        Returns:
            [{"id": "t1", "url": "https://...", "title": "...", "active": bool}, ...]
        """
        out = self._run(["--session", self.session, "tab", "list", "--json"], timeout=10)
        try:
            raw = json.loads(out)
            tabs = raw.get("data", raw).get("tabs", [])
            if isinstance(tabs, list):
                return [
                    {"id": t.get("tabId", t.get("id", "")),
                     "url": t.get("url", ""),
                     "title": t.get("title", ""),
                     "active": t.get("active", False)}
                    for t in tabs
                ]
        except (json.JSONDecodeError, TypeError, AttributeError):
            pass
        return []

    def switch_tab(self, tab_id: str) -> "AgentBrowser":
        """切换到指定标签页。"""
        self._run(["--session", self.session, "tab", tab_id], timeout=5)
        return self

    def close_tab(self, tab_id: str):
        """关闭指定标签页（只能关闭本次新建的标签页，不能关闭来源页）。"""
        self._run(["--session", self.session, "tab", "close", tab_id], timeout=5)

    # ── 新标签页自动跟进（用于 Market/Aily 「打开」按钮）──

    def click_and_follow_popup(
        self,
        selector: str,
        expected_agent: Optional[dict] = None,
        popup_timeout: float = 20.0,
        wait_sec: float = 3.0,
    ) -> dict | None:
        """点击按钮并自动跟进 popup/新标签页中打开的业务智能体。

        操作流程（禁止关闭来源标签页）：
        1. 记录 source_tab_id
        2. 点击按钮
        3. 轮询检测新增标签页（过滤 about:blank、广告等）
        4. 验证候选标签页是否匹配智能体（URL/名称关键词）
        5. 切换到匹配标签页
        6. 处理 OAuth 二次跳转（如有）

        Returns:
            成功: {"source_tab_id": str, "created_tab_ids": [str], "target_tab_id": str, "launch_mode": "new_tab"|"redirect"}
            失败: None
        """
        # 1) 记录来源
        tabs_before = self.list_tabs()
        before_ids = {t["id"] for t in tabs_before}
        source_tab_id = next((t["id"] for t in tabs_before if t.get("active")), 
                            tabs_before[0]["id"] if tabs_before else "t1")
        current_url_before = self.get_url()

        # 2) 点击
        self.click(selector)

        # 3) 轮询检测新标签页
        deadline = time.time() + popup_timeout
        created_tab_ids: list = []
        target_tab_id: str | None = None
        launch_mode: str | None = None

        while time.time() < deadline:
            tabs_after = self.list_tabs()
            after_ids = {t["id"] for t in tabs_after}
            new_ids = after_ids - before_ids

            if new_ids:
                # 过滤候选标签页
                oauth_new_id = None  # OAuth 标签页需要单独处理
                for new_id in sorted(new_ids, key=lambda x: int(x[1:])):
                    new_tab = next((t for t in tabs_after if t["id"] == new_id), None)
                    if not new_tab:
                        continue
                    new_url = new_tab.get("url", "")

                    # 排除无效页
                    if self._is_noise_tab(new_url):
                        continue

                    created_tab_ids.append(new_id)

                    # OAuth 页单独处理（需要先授权才能验证是否匹配）
                    if "accounts.feishu.cn" in new_url:
                        oauth_new_id = new_id
                        continue

                    # 验证是否匹配目标智能体
                    if expected_agent and not self._is_expected_agent_page(new_url, expected_agent):
                        continue

                    # ✅ 找到匹配标签页
                    self.switch_tab(new_id)
                    target_tab_id = new_id
                    launch_mode = "new_tab"
                    break

                # 如果只发现 OAuth 标签页，尝试处理
                if not target_tab_id and oauth_new_id:
                    self.switch_tab(oauth_new_id)
                    target_tab_id = oauth_new_id
                    launch_mode = "new_tab"
                    break

                if target_tab_id:
                    break

            # 同时检查当前页是否直接跳转（非 popup 场景）
            cur_url = self.get_url()
            if cur_url != current_url_before and cur_url != "about:blank":
                if not self._is_noise_tab(cur_url):
                    if expected_agent is None or self._is_expected_agent_page(cur_url, expected_agent):
                        target_tab_id = source_tab_id
                        launch_mode = "redirect"
                        break

            time.sleep(1.0)

        if not target_tab_id:
            return None

        # 4) 等待新页面就绪
        time.sleep(wait_sec)

        # 5) 处理 OAuth 二次跳转（如有）
        cur_url = self.get_url()
        if "accounts.feishu.cn" in cur_url:
            # 在 popup 标签页上的 OAuth — 需要点击 Authorize
            # 授权后可能关闭此 popup 并跳回当前页，或再次产生新标签页
            after_auth = self._handle_oauth_follow(target_tab_id, source_tab_id, created_tab_ids)
            if after_auth:
                return after_auth

        return {
            "source_tab_id": source_tab_id,
            "created_tab_ids": created_tab_ids,
            "target_tab_id": target_tab_id,
            "launch_mode": launch_mode,
        }

    def restore_source_tab(self, tab_context: dict):
        """清理：关闭本次新建的业务标签页，切回来源标签页。

        禁止关闭来源标签页（source_tab_id）和 before 中的旧标签页。
        """
        if not tab_context:
            return

        # 只关闭本次新建的标签页
        for tab_id in tab_context.get("created_tab_ids", []):
            try:
                self.close_tab(tab_id)
            except AgentBrowserError:
                pass

        # 切回来源页
        source_id = tab_context.get("source_tab_id")
        if source_id:
            try:
                self.switch_tab(source_id)
            except AgentBrowserError:
                pass

    # ┄ 私有辅助 ───────────────────────────────────────

    @staticmethod
    def _is_noise_tab(url: str) -> bool:
        """排除非业务标签页（OAuth 授权页不排除，需单独处理）。"""
        if not url or url == "about:blank":
            return True
        noise = [
            "agent.digitalchina.com/agents/market",  # 市场列表页
            "agent.digitalchina.com/login",
            "aily.feishu.cn/ai/agents",               # Aily 开发后台
            "aily.feishu.cn/agents?",                  # Aily 应用列表
            "google.com", "bing.com", "baidu.com",     # 搜索引擎
            "chrome-error://", "chrome://",            # 浏览器错误/内部页
        ]
        return any(n in url for n in noise)

    @staticmethod
    def _is_expected_agent_page(url: str, agent: dict) -> bool:
        """验证标签页 URL 是否匹配目标智能体。"""
        name = (agent.get("name") or "").lower()

        # URL 特征匹配
        url_lower = url.lower()
        if "feishuapp.cn/ai/gui/chat" in url_lower:
            return True
        if "aily.feishu.cn/agents" in url_lower:
            return True
        if "aiforce.cloud/app" in url_lower:
            return True

        # 检查页面内容是否包含智能体名称（在调用者处验证）
        return True  # URL 级放过，内容级由调用者检查

    def _handle_oauth_follow(
        self, current_tab_id: str, source_tab_id: str, created_tab_ids: list
    ) -> dict | None:
        """处理 OAuth 授权页：在 popup 标签页上点击 Authorize，处理授权后的跳转。

        授权可能：
        - 当前 popup 直接跳转到业务页
        - 当前 popup 关闭，source_tab 跳转到业务页
        - 再产生一个新标签页
        """
        # 记录授权前的标签页
        tabs_before_auth = self.list_tabs()
        before_auth_ids = {t["id"] for t in tabs_before_auth}

        # 尝试点击 Authorize
        try:
            auth_refs = self.snapshot()
            # 查找 Authorize / 授权 按钮
            for item in auth_refs.get("children", []):
                text = (item.get("name") or "").lower()
                ref = item.get("ref", "")
                if text in ("authorize", "授权") and ref:
                    self._run(["--session", self.session, "click", ref], timeout=5)
                    break
        except Exception:
            return None

        time.sleep(3)

        # 检查授权后的状态
        tabs_after_auth = self.list_tabs()
        after_auth_ids = {t["id"] for t in tabs_after_auth}

        # 检查是否产生了新标签页
        new_auth_ids = after_auth_ids - before_auth_ids
        for new_id in sorted(new_auth_ids, key=lambda x: int(x[1:])):
            new_tab = next((t for t in tabs_after_auth if t["id"] == new_id), None)
            new_url = new_tab.get("url", "") if new_tab else ""
            if not self._is_noise_tab(new_url) and "accounts.feishu.cn" not in new_url:
                self.switch_tab(new_id)
                created_tab_ids.append(new_id)
                return {
                    "source_tab_id": source_tab_id,
                    "created_tab_ids": created_tab_ids,
                    "target_tab_id": new_id,
                    "launch_mode": "new_tab",
                }

        # 检查当前标签页是否已跳转到业务页
        cur_url = self.get_url()
        if "accounts.feishu.cn" not in cur_url and not self._is_noise_tab(cur_url):
            return {
                "source_tab_id": source_tab_id,
                "created_tab_ids": created_tab_ids,
                "target_tab_id": current_tab_id,
                "launch_mode": "redirect",
            }

        # 检查 source_tab 是否跳转
        try:
            self.switch_tab(source_tab_id)
            source_url = self.get_url()
            if "accounts.feishu.cn" not in source_url and not self._is_noise_tab(source_url):
                return {
                    "source_tab_id": source_tab_id,
                    "created_tab_ids": created_tab_ids,
                    "target_tab_id": source_tab_id,
                    "launch_mode": "redirect",
                }
            self.switch_tab(current_tab_id)  # 切回当前
        except AgentBrowserError:
            pass

        return None

    def wait_for_selector(
        self,
        selector: str,
        timeout: float = 15.0,
        poll_interval: float = 1.0,
    ) -> bool:
        """轮询等待 CSS 选择器出现

        Args:
            selector: CSS 选择器
            timeout: 最长等待秒数
            poll_interval: 轮询间隔秒数

        Returns:
            True 如果找到,False 超时
        """
        waited = 0.0
        while waited < timeout:
            # 用 eval 检查元素是否存在
            js = f"!!document.querySelector('{selector}')"
            try:
                result = self.eval(js)
                if result.strip() == "true":
                    return True
            except AgentBrowserError:
                pass
            time.sleep(poll_interval)
            waited += poll_interval
        return False

    def snapshot(self, timeout: int = 10) -> Dict[str, Any]:
        """获取无障碍树 snapshot

        Returns:
            {"text": "...", "elements": [...]} 或原始输出
        """
        output = self._run(["--session", self.session, "snapshot"], timeout=timeout)
        return self._parse_snapshot(output)

    def click(self, selector: str, timeout: int = 10):
        """点击元素

        Args:
            selector: CSS 选择器或 ref(如 @e14、[contenteditable])
        """
        self._run(["--session", self.session, "click", selector], timeout=timeout)

    def focus(self, selector: str, timeout: int = 10):
        """聚焦元素"""
        self._run(["--session", self.session, "focus", selector], timeout=timeout)

    def fill(self, selector: str, text: str, timeout: int = 10):
        """填充 input/textarea(非 contentEditable)"""
        self._run(
            ["--session", self.session, "fill", selector, text], timeout=timeout
        )

    def type_text(self, selector: str, text: str, delay_ms: int = 30, timeout: int = 10):
        """逐字输入(模拟键盘,含延迟)"""
        self._run(
            ["--session", self.session, "type", selector, text], timeout=timeout
        )

    def keyboard_type(self, text: str, timeout: int = 10):
        """在当前焦点输入键盘文本"""
        self._run(
            ["--session", self.session, "keyboard", "type", text], timeout=timeout
        )

    def insert_text(self, text: str, timeout: int = 10):
        """在当前焦点插入文本(不触发键盘事件,contentEditable 专用)"""
        self._run(
            ["--session", self.session, "keyboard", "inserttext", text],
            timeout=timeout,
        )

    def upload(self, selector: str, *file_paths: str, timeout: int = 15):
        """上传文件到文件选择器

        Args:
            selector: CSS 选择器定位 input[type=file]
            *file_paths: 一个或多个文件路径
        """
        args = ["--session", self.session, "upload", selector] + list(file_paths)
        self._run(args, timeout=timeout)

    def find_and_click(self, text: str, timeout: int = 10):
        """语义查找文本并点击"""
        self._run(
            ["--session", self.session, "find", "text", text, "click"],
            timeout=timeout,
        )

    def press(self, key: str, timeout: int = 10):
        """按键(Enter, Tab, Control+a 等)"""
        self._run(["--session", self.session, "press", key], timeout=timeout)

    def hover(self, selector: str, timeout: int = 10):
        """悬停元素"""
        self._run(["--session", self.session, "hover", selector], timeout=timeout)

    def eval(self, js: str, timeout: int = 10) -> str:
        """执行 JavaScript 并返回结果(自动解析 JSON 字符串)"""
        output = self._run(
            ["--session", self.session, "eval", js], timeout=timeout
        )
        # agent-browser eval 返回 JSON 编码的字符串(如 "..."),
        # 尝试解析为原生字符串
        try:
            parsed = json.loads(output)
            if isinstance(parsed, str):
                return parsed
            return output
        except (json.JSONDecodeError, ValueError):
            return output

    def screenshot(
        self,
        path: Optional[str] = None,
        full_page: bool = False,
        timeout: int = 10,
    ) -> str:
        """截图

        Args:
            path: 保存路径(默认生成临时路径)
            full_page: 是否全页截图
            timeout: 超时秒数

        Returns:
            截图文件路径
        """
        if path is None:
            import tempfile
            path = tempfile.mktemp(suffix=".png")

        args = ["--session", self.session, "screenshot"]
        if full_page:
            args.append("--full")
        args.append(path)

        self._run(args, timeout=timeout)
        return path

    def get_text(self, selector: str, timeout: int = 10) -> str:
        """获取元素文本内容"""
        return self._run(
            ["--session", self.session, "get", "text", selector], timeout=timeout
        ).strip()

    def get_url(self, timeout: int = 10) -> str:
        """获取当前 URL"""
        return self._run(
            ["--session", self.session, "get", "url"], timeout=timeout
        ).strip()

    def get_title(self, timeout: int = 10) -> str:
        """获取页面标题"""
        return self._run(
            ["--session", self.session, "get", "title"], timeout=timeout
        ).strip()

    # ── 高级封装 ──

    def _detect_chat_input(self) -> tuple:
        """探测页面聊天输入框，返回 (selector, type)。

        使用 JS 智能检测：优先 contenteditable，其次 textarea（排除 search），最后 input（排除 search）。
        type 取值: contenteditable | textarea | input
        """
        try:
            result = self.eval(
                """(() => {
                    // 1. 优先检测 contenteditable（99% 的真实聊天输入框）
                    const ce = document.querySelector('[contenteditable]');
                    if (ce && !ce.closest('nav, [role="navigation"], header, footer, [class*="search" i], [class*="Search"]')) {
                        return JSON.stringify({selector: '[contenteditable]', type: 'contenteditable'});
                    }
                    // 2. 检测 textarea（排除搜索/hidden）
                    const ta = document.querySelector('textarea:not([class*="search" i]):not([class*="Search"]):not([aria-label*="search" i]):not([aria-label*="Search"]):not([placeholder*="search" i])');
                    if (ta && !ta.closest('nav, [role="navigation"], header, footer')) {
                        return JSON.stringify({selector: 'textarea:not([class*="search" i]):not([class*="Search"])', type: 'textarea'});
                    }
                    // 3. 检测 input[type='text']（排除搜索）
                    const inp = document.querySelector('input[type="text"]:not([class*="search" i]):not([class*="Search"]):not([aria-label*="search" i]):not([aria-label*="Search"]):not([placeholder*="search" i]):not([placeholder*="搜索"])');
                    if (inp && !inp.closest('nav, [role="navigation"], header, footer, [class*="toolbar" i], [class*="header" i]')) {
                        return JSON.stringify({selector: 'input[type="text"]:not([class*="search" i]):not([class*="Search"])', type: 'input'});
                    }
                    return JSON.stringify({selector: null, type: null});
                })()"""
            )
            data = json.loads(result.strip())
            sel = data.get("selector")
            typ = data.get("type")
            if sel and typ:
                return (sel, typ)
        except (AgentBrowserError, json.JSONDecodeError):
            pass
        return (None, None)

    def _find_send_button(self) -> Optional[str]:
        """查找发送按钮，返回可用的 CSS 选择器或 None。"""
        candidates = [
            # Aily 平台专用选择器
            'button.input-icon-button',  # Aily: icon button next to input
            'button.btn-pc',             # Aily: PC send button
            # 通用选择器
            'button[aria-label*="发送"]',
            'button[aria-label*="Send"]',
            'button[aria-label*="send"]',
            'button[data-testid*="send"]',
            '[role="button"][aria-label*="发送"]',
            'button:has(svg)',   # 飞书/aily 常见发送按钮
            'svg[class*="send"]',
        ]
        for sel in candidates:
            try:
                result = self.eval(f"!!document.querySelector({json.dumps(sel)})")
                if result.strip().lower() == "true":
                    return sel
            except AgentBrowserError:
                continue
        return None

    def _close_dialogs(self) -> bool:
        """关闭 Aily Settings 对话框等弹窗。返回是否关闭了弹窗。"""
        closed = False
        time.sleep(2)
        # 关闭 Settings 对话框（Back 按钮或 dialog 内的关闭按钮）
        for sel in (
            'dialog button:first-child',
            'button[aria-label="Close"]',
            'dialog [role="dialog"] button:first-child',
        ):
            try:
                self._run(["--session", self.session, "click", sel], timeout=5)
                closed = True
                time.sleep(1)
                break
            except AgentBrowserError:
                continue
        # JS 兜底：移除所有 dialog
        if not closed:
            try:
                result = self.eval(
                    """(() => {
                        const dialogs = document.querySelectorAll('dialog[open], [role="dialog"]');
                        if (dialogs.length > 0) {
                            dialogs.forEach(d => d.remove());
                            return 'closed';
                        }
                        return 'none';
                    })()"""
                )
                if result.strip().lower() == "closed":
                    closed = True
                    time.sleep(1)
            except AgentBrowserError:
                pass
        return closed

    def _ensure_chat_page(self) -> bool:
        """确保页面处于可聊天的状态：关弹窗、处理详情页跳转到 Aily 首页。
        返回是否做了页面跳转。"""
        jumped = False

        # 1. 关闭可能的弹窗（Settings / Archived 对话框）
        self._close_dialogs()

        # 2. 详情页 /detail 或 /agents/ → 跳转到 Aily 首页（New Task 页面有 contenteditable）
        cur = self.eval("window.location.href").strip()
        if "/detail" in cur or "/agents/" in cur:
            try:
                self._run(["--session", self.session, "open", "https://aily.feishu.cn/"], timeout=30)
                jumped = True
                time.sleep(4)
                self._close_dialogs()
            except Exception:
                pass

        return jumped

    def chat_send(self, message: str) -> str:
        """发送聊天消息：自动探测输入框类型（contenteditable/textarea/input），
        优先点击发送按钮，失败后回退 Enter。

        feishuapp.cn 特殊处理：使用 snapshot ref 点击 + keyboard inserttext + Enter。
        """
        if not message or not message.strip():
            raise AgentBrowserError("聊天消息不能为空")

        # ── feishuapp.cn 特殊路径：ref 点击 + keyboard inserttext + Enter ──
        try:
            is_fsp = self.eval("!!window.location.href.includes('feishuapp.cn/ai/gui/chat')").strip()
            if is_fsp.lower() == 'true':
                return self._feishuapp_chat_send(message)
        except AgentBrowserError:
            pass

        # ── 尝试从详情页跳转到聊天界面 ──
        self._ensure_chat_page()

        selector, input_type = self._detect_chat_input()
        if not selector or not input_type:
            raise AgentBrowserError(
                "未检测到聊天输入框（支持 contenteditable/textarea/input），"
                "页面可能不是标准聊天界面"
            )

        # ── 输入消息 ──
        if input_type == "contenteditable":
            # 优先用 Playwright click 初始化编辑器（设置光标/焦点事件）
            # 如果被 floating overlay 遮挡 → 回退 JS click（绕过 hit-test，等效初始化）
            try:
                self.click(selector)
            except AgentBrowserError as e:
                err_msg = str(e).lower()
                if "covered" in err_msg or "obscured" in err_msg or "intercepted" in err_msg:
                    # overlay 遮挡 → JS click 绕过 hit-test，保留完整 click 初始化
                    self.eval(f"document.querySelector({json.dumps(selector)}).click()")
                else:
                    raise
            time.sleep(0.2)
            self.insert_text(message)
            time.sleep(0.2)
            # Ace-line 编辑器（Aily）清理：移除末尾空行，
            # 否则 Enter 会创建新 ace-line 而非发送消息
            try:
                self.eval(
                    f"""(() => {{
                        const el = document.querySelector({json.dumps(selector)});
                        if (!el) return;
                        // 检测 ace-line 编辑器
                        const hasAceLine = el.innerHTML.includes('ace-line');
                        if (!hasAceLine) return;
                        // 获取原始文本并重建干净的内容
                        const clean = {json.dumps(message, ensure_ascii=False)};
                        // 清空所有子节点
                        while (el.firstChild) el.removeChild(el.firstChild);
                        // 重建单个 ace-line 并聚焦
                        const div = document.createElement('div');
                        div.className = 'ace-line';
                        div.setAttribute('data-node', 'true');
                        div.setAttribute('dir', 'auto');
                        const span = document.createElement('span');
                        span.setAttribute('data-string', 'true');
                        span.textContent = clean;
                        div.appendChild(span);
                        el.appendChild(div);
                        el.focus();
                        // 触发框架输入事件
                        el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    }})()"""
                )
            except AgentBrowserError:
                pass
        else:
            # textarea 或 input：优先 focus 聚焦（跳过 click 避免 overlay 遮挡）
            try:
                self.focus(selector)
            except AgentBrowserError:
                self.eval(f"document.querySelector({json.dumps(selector)}).focus()")
            time.sleep(0.2)
            self.fill(selector, message)
            time.sleep(0.2)
            # 额外 dispatch input 事件，触发前端框架的响应式绑定
            try:
                self.eval(
                    f"""(() => {{
                        const el = document.querySelector({json.dumps(selector)});
                        if (el) {{
                            el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                            el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                        }}
                    }})()"""
                )
            except AgentBrowserError:
                pass

        quoted = json.dumps(message, ensure_ascii=False)
        draft_ok = self.eval(
            f"""(() => {{
                const ce = document.querySelector('[contenteditable]');
                const ta = document.querySelector('textarea');
                const inp = document.querySelector('input[type="text"], input:not([type])');
                let draft = '';
                if (ce) draft = (ce.innerText || ce.textContent || '');
                else if (ta) draft = ta.value || '';
                else if (inp) draft = inp.value || '';
                return draft.includes({quoted});
            }})()"""
        )
        if draft_ok.strip().lower() != "true":
            raise AgentBrowserError("消息未进入聊天输入框，可能被弹窗或错误焦点拦截")

        # ── 发送消息 ──
        send_method = None

        # 1) 尝试属性明确的发送按钮
        send_btn = self._find_send_button()
        if send_btn:
            try:
                self.click(send_btn, timeout=3)
                if self._wait_for_message_sent(message, timeout=5.0):
                    send_method = "button"
            except AgentBrowserError:
                pass

        # 2) 语义查找「发送」文本
        if not send_method:
            try:
                self.find_and_click("发送", timeout=3)
                if self._wait_for_message_sent(message, timeout=5.0):
                    send_method = "button"
            except AgentBrowserError:
                pass

        # 3) 回退 Enter 键（先 click 输入框确保光标就绪）
        if not send_method:
            try:
                self.click(selector)
            except AgentBrowserError as e:
                err_msg = str(e).lower()
                if "covered" in err_msg or "obscured" in err_msg or "intercepted" in err_msg:
                    self.eval(f"document.querySelector({json.dumps(selector)}).click()")
                else:
                    raise
            time.sleep(0.2)
            self.press("Enter")
            if not self._wait_for_message_sent(message, timeout=5.0):
                raise AgentBrowserError("点击发送按钮和按 Enter 均未提交消息")
            send_method = "enter"

        return send_method

    def _wait_for_message_sent(self, message: str, timeout: float = 5.0) -> bool:
        """轮询确认消息已发送：输入框清空 + 消息出现在页面中。"""
        import time as _time
        quoted = json.dumps(message, ensure_ascii=False)
        deadline = _time.time() + timeout

        while _time.time() < deadline:
            try:
                result = self.eval(
                    f"""(function() {{
                        var ce = document.querySelector('[contenteditable]');
                        var ta = document.querySelector('textarea');
                        var inp = document.querySelector('input[type="text"], input:not([type])');
                        var draft = '';
                        if (ce) draft = ce.innerText || ce.textContent || '';
                        else if (ta) draft = ta.value || '';
                        else if (inp) draft = inp.value || '';
                        // 去除零宽字符 (\u200b 等) 后再判断空白
                        var cleaned = draft.replace(/[\u200b\u200c\u200d\uFEFF]/g, '').trim();
                        var body = document.body.innerText || '';
                        var draftEmpty = cleaned.length === 0;
                        var msgInBody = body.indexOf({quoted}) !== -1;
                        return draftEmpty && msgInBody;
                    }})()"""
                )
                if result.strip().lower() == "true":
                    return True
            except AgentBrowserError:
                pass
            _time.sleep(0.8)

        return False

    # 保留旧名兼容
    def _message_was_submitted(self, message: str) -> bool:
        return self._wait_for_message_sent(message, timeout=2.0)

    def _feishuapp_chat_send(self, message: str) -> str:
        """feishuapp.cn 专用发送流程：snapshot ref 点击 + keyboard inserttext + Enter。

        agent-browser click [contenteditable] 对 feishuapp.cn 不生效（CSS 选择器可见性
        判断更严格），但 snapshot ref 点击正常工作。
        """
        import time as _time, re

        # 1) 获取 snapshot 找到 contenteditable 的 ref
        result = self._run(["--session", self.session, "snapshot"], timeout=10)
        refs = re.findall(r'\[ref=(e\d+)\].*?contenteditable', result)
        if not refs:
            raise AgentBrowserError("feishuapp.cn: 未找到 contenteditable 输入框")

        input_ref = refs[0]

        # 2) 点击输入框 + 输入文字
        self._run(["--session", self.session, "click", input_ref], timeout=5)
        _time.sleep(0.3)
        self._run(["--session", self.session, "keyboard", "inserttext", message], timeout=5)
        _time.sleep(0.3)

        # 3) 按 Enter 发送
        self._run(["--session", self.session, "press", "Enter"], timeout=5)

        # 4) 确认消息发送
        _time.sleep(1.0)
        body = self.get_body_text()
        if not body:
            # body 可能为空（feishuapp 使用 Shadow DOM），改为 eval 检查
            try:
                check = self.eval(f"!!(document.body?.innerText||'').includes('{message}')")
                if check.strip().lower() != 'true':
                    raise AgentBrowserError("feishuapp.cn: 消息未确认发送")
            except AgentBrowserError:
                raise
        else:
            if message not in body:
                raise AgentBrowserError("feishuapp.cn: 消息未确认发送")

        return "enter"

    def _feishuapp_get_answers(self, question: str, timeout: float = 180.0) -> str:
        """feishuapp.cn 平台：等待并提取 AI 回答。

        feishuapp.cn 消息布局: 回答出现在用户消息之「前」
        提取策略: body 文本中，第一段不含页面模板关键词的文本。
        """
        import time as _time, re
        deadline = _time.time() + timeout
        last_text = ""
        stable_count = 0

        while _time.time() < deadline:
            _time.sleep(3.0)
            body = self.get_body_text()
            if not body:
                try:
                    body = self.eval("document.body?.innerText || ''")
                except:
                    body = ""
            if not body:
                continue

            # feishuapp.cn 消息布局: AI 回答出现在用户消息之前
            # 提取: 在「你好」之前的内容 = AI 回答（排除欢迎语）
            template_markers = [
                '你好，我是', '为了更好地帮助您', '提问小技巧',
                '示例', '创建者：', '发布时间：', '新话题',
                '海量采购', '收藏', '分享链接', '用飞书 aily 创建',
            ]

            # 找到用户消息的位置
            q_idx = body.find(question) if question else -1
            if q_idx > 0:
                # 提取用户消息之前的内容（AI 回答）
                prefix = body[:q_idx].strip()
                # 移除模板关键词
                lines = [l.strip() for l in prefix.split('\n') if l.strip()]
                answer_lines = [l for l in lines 
                              if not any(m in l for m in template_markers)
                              and len(l) > 2]
                answer = '\n'.join(answer_lines) if answer_lines else prefix
            else:
                answer = body.strip()

            if answer and len(answer) > 5:
                if answer == last_text:
                    stable_count += 1
                    if stable_count >= 3:
                        return answer.strip()
                else:
                    last_text = answer
                    stable_count = 0

        return last_text.strip() if last_text else ""

    def chat_wait(
        self,
        timeout: int = 90,
        poll_interval: float = 3.0,
        stable_count: int = 2,
        body_before: str = "",
        question: str = "",
        extract_answer: bool = True,
        agent_url: str = "",
    ) -> dict:
        """等待提问后的新增回复稳定，返回结构化结果。

        两阶段策略：
        1. 检测 Stop generating 出现/消失（用于有流式控制按钮的平台）
        2. 内容稳定性检测（通用，适用于 Aily 等不显示 Stop generating 的平台）

        Returns:
            {"answer_text": str, "status": "complete"|"timeout"|"empty",
             "waited": float, "stop_seen": bool, "stop_gone": bool}
        """
        # ── feishuapp.cn 专用提取 ──
        try:
            is_fsp = self.eval("!!window.location.href.includes('feishuapp.cn/ai/gui/chat')").strip()
            if is_fsp.lower() == 'true':
                answer = self._feishuapp_get_answers(question or "", timeout=timeout)
                result = {
                    "answer_text": answer,
                    "status": "complete" if answer else "timeout",
                    "waited": timeout,
                    "stop_seen": False,
                    "stop_gone": False,
                }
                return result
        except AgentBrowserError:
            pass
        result = {
            "answer_text": "",
            "status": "empty",
            "waited": 0.0,
            "stop_seen": False,
            "stop_gone": False,
        }

        if not body_before:
            body_before = self.get_body_text()

        existing_answer_ids = self._get_answer_ids()
        existing_msg_count = len(existing_answer_ids)

        stop_generating = ["Stop generating", "停止生成", "停止回答"]
        stop_seen = False
        waited = 0.0
        previous = ""
        stable = 0
        content_started = False

        # ── 主循环：混合检测 ──
        while waited < timeout:
            time.sleep(poll_interval)
            waited += poll_interval

            # 读取当前 body 文本
            try:
                latest_body = self.eval("document.body ? document.body.innerText : ''")
            except Exception:
                continue

            has_stop = any(s in latest_body for s in stop_generating)

            # Stop generating 追踪
            if not stop_seen and has_stop:
                stop_seen = True
                result["stop_seen"] = True
            if stop_seen and not has_stop:
                result["stop_gone"] = True

            # 提取当前回答
            current = self._extract_answer_from_page(
                question=question,
                body_before=body_before,
                existing_msg_count=existing_msg_count,
                agent_url=agent_url,
            )

            # 内容稳定性检测
            if current and len(current) >= 10:
                content_started = True
                if current == previous:
                    stable += 1
                else:
                    stable = 0
                    previous = current
            else:
                # 还没产生内容，重置
                stable = 0

            # 完成条件（满足任一）:
            # A. stop 已出现且已消失 + 内容稳定
            # B. 内容已产生 + 无 stop 相关文本 + 内容稳定（适用于 Aily）
            # C. 内容已产生 + 内容长度不再增长（不受 stop 影响）
            stop_done = result["stop_seen"] and result["stop_gone"]
            no_stop_visible = not has_stop
            content_stable = stable >= stable_count and content_started

            if content_stable and (stop_done or no_stop_visible):
                result["answer_text"] = current
                result["status"] = "complete"
                result["waited"] = waited
                return result

        # ── 超时回退 ──
        result["waited"] = waited

        # 最后尝试提取
        fallback = self._extract_answer_from_page(
            question=question,
            body_before=body_before,
            existing_msg_count=existing_msg_count,
            agent_url=agent_url,
        )
        if fallback and len(fallback) >= 10:
            result["answer_text"] = fallback
            result["status"] = "timeout"
        else:
            result["status"] = "empty" if not content_started else "timeout"

        return result

    def _get_answer_ids(self) -> set:
        """获取当前页面中回答节点的 ID 集合，用于增量检测。"""
        js = """
        (() => {
            const containers = document.querySelectorAll(
                '[class*="message"], [class*="msg"], [class*="chat"], [class*="answer"], ' +
                '[class*="assistant"], [class*="reply"], [class*="response"], [class*="bubble"], ' +
                '[class*="ai"], [class*="bot"], [class*="agent"], [class*="card"]'
            );
            return JSON.stringify(Array.from(containers).map((el, i) => {
                el.__answer_idx = i;
                return i;
            }));
        })()
        """
        try:
            raw = self.eval(js)
            return set(json.loads(raw))
        except Exception:
            return set()

    def _extract_answer_from_page(
        self,
        question: str = "",
        body_before: str = "",
        existing_msg_count: int = 0,
        agent_url: str = "",
    ) -> str:
        """从页面中提取最新的 assistant 回答。

        优先级：
        1. Aily 平台适配器
        2. 飞书应用适配器
        3. 通用消息列表选择器
        4. body 文本差量提取（兜底）
        """
        import re

        url = agent_url or self._url or ""

        # ── Aily 适配器 ──
        if "aily.feishu.cn" in url or "agent.digitalchina.com" in url:
            answer = self._extract_aily_answer(question, existing_msg_count)
            if answer:
                return answer

        # ── 飞书应用适配器 ──
        if "app.feishu.cn" in url or "feishu.cn" in url:
            answer = self._extract_feishu_app_answer(question, existing_msg_count)
            if answer:
                return answer

        # ── 通用消息列表选择器 ──
        answer = self._extract_generic_answer(question, existing_msg_count)
        if answer:
            return answer

        # ── body 文本差量兜底 ──
        return self._extract_answer_text_diff(body_before, question)

    def _extract_aily_answer(self, question: str, existing_msg_count: int) -> str:
        """Aily 平台：提取最新回答。

        分两阶段：
        1. DOM 容器选择器（有 class 标记的消息组件）
        2. body 文本截取（Aily 渲染为原生 HTML h1/p/table，无容器 class）
        """
        # ── 阶段1: DOM 容器选择器 ──
        js = r"""
        (() => {
            const selectors = [
                '[class*="ThreadMessage-module__assistant"]',
                '[class*="message-assistant"]',
                '[class*="chat-message-assistant"]',
                '[class*="MessageItem-assistant"]',
                '[class*="chatMessage-assistant"]',
                '[class*="assistantMessage"]',
                '[class*="ai-message"]',
                '[class*="bot-message"]',
            ];
            let containers = [];
            for (const sel of selectors) {
                containers = document.querySelectorAll(sel);
                if (containers.length > 0) break;
            }
            if (containers.length === 0) return '';
            const startIdx = %d;
            if (startIdx >= containers.length) return '';
            const el = containers[containers.length - 1];
            const clone = el.cloneNode(true);
            clone.querySelectorAll('button, nav, [role="toolbar"], input, textarea, [contenteditable], [class*="action"], [class*="toolbar"], [class*="source"], [class*="reference"]').forEach(n => n.remove());
            return (clone.textContent || '').trim();
        })()
        """ % existing_msg_count
        try:
            raw = self.eval(js)
            if raw and raw.strip() and len(raw.strip()) > 10:
                cleaned = AgentBrowser._clean_answer(raw.strip())
                if cleaned:
                    return cleaned
        except Exception:
            pass

        # ── 阶段2: body 文本截取（Aily 原生 HTML 渲染） ──
        return self._extract_aily_answer_body(question)

    def _extract_aily_answer_body(self, question: str) -> str:
        """Aily 平台 body 文本截取：回答位于用户消息和 'How was this result?' 之间。

        Aily 多数智能体用原生 HTML（h1/p/li/table）渲染回答，没有 class 标记的
        消息容器，不能用 DOM 选择器定位。此类情况下通过 body.innerText 全文解析。
        """
        js = r"""
        (() => {
            const allText = document.body ? document.body.innerText : '';
            const q = %s;
            let startIdx = -1;
            if (q && q.length > 3) {
                // 用问题前6个字符定位用户消息
                const key = q.substring(0, 6);
                startIdx = allText.indexOf(key);
                if (startIdx < 0) startIdx = allText.indexOf(q);
            }

            // 结束标记（Aily 页面底部固定文本）
            const endMarkers = [
                'How was this result?', 'Got a question?', 'Ask away!',
                'AI can make mistakes', 'Deep Planning',
            ];
            let endIdx = allText.length;
            const searchFrom = Math.max(startIdx, 0);
            for (const m of endMarkers) {
                const idx = allText.indexOf(m, searchFrom);
                if (idx > 0) { endIdx = Math.min(endIdx, idx); break; }
            }

            let answer = '';
            if (startIdx >= 0) {
                answer = allText.substring(startIdx + q.length + 1, endIdx).trim();
            }

            // 清理: 跳过前几行的元信息行（智能检索/Based on/found/Sources/Copy）
            const lines = answer.split('\n');
            const cleaned = [];
            let started = false;
            let skipCount = 0;
            for (const line of lines) {
                const t = line.trim();
                if (!started) {
                    if (t.includes('智能检索') || t.includes('Based on') ||
                        t.includes('found') && t.includes('source') ||
                        t === 'Copy' || t.length < 3 ||
                        t.startsWith('Invite') || t.includes('Earn')) {
                        skipCount++;
                        if (skipCount > 6) started = true;
                        continue;
                    }
                    started = true;
                }
                cleaned.push(line);
            }
            return cleaned.join('\n').trim();
        })()
        """ % json.dumps(question, ensure_ascii=False)
        try:
            raw = self.eval(js)
            if raw and raw.strip() and len(raw.strip()) > 10:
                return AgentBrowser._clean_answer(raw.strip())
        except Exception:
            pass
        return ""

    def _extract_feishu_app_answer(self, question: str, existing_msg_count: int) -> str:
        """飞书应用（feishuapp.cn）：提取最新回答卡片或消息气泡。"""
        js = r"""
        (() => {
            // feishuapp.cn 平台特有容器
            const selectors = [
                '[class*="chatContainer"]',
                '[class*="chatMainContainer"]',
                '[class*="copilotBotContainer"]',
                '[class*="answer"]',
                '[class*="reply"]',
                '[class*="response"]',
                '[class*="result"]',
                '[class*="output"]',
            ];
            let containers = [];
            for (const sel of selectors) {
                containers = document.querySelectorAll(sel);
                if (containers.length > 0) break;
            }
            if (containers.length === 0) return '';
            const el = containers[0];  // feishuapp.cn 使用第一个容器（回答在顶部）
            const clone = el.cloneNode(true);
            // 排除 UI 元素
            clone.querySelectorAll('button, nav, [role="toolbar"], input, textarea, [contenteditable], [class*="source"], [class*="reference"], [class*="action"], [class*="toolbar"], [class*="Profile"], [class*="profile"], [class*="Bottom"], [class*="bottom"], [class*="Header"], [class*="header"]').forEach(n => n.remove());
            return (clone.textContent || '').trim();
        })()
        """
        try:
            raw = self.eval(js)
            if raw and raw.strip() and len(raw.strip()) > 10:
                return AgentBrowser._clean_answer(raw.strip())
        except Exception:
            pass
        return ""

    def _extract_generic_answer(self, question: str, existing_msg_count: int) -> str:
        """通用消息列表：提取最新回答节点文本。"""
        js = r"""
        (() => {
            const selectors = [
                '[class*="message"][class*="assistant"]',
                '[class*="msg"][class*="assistant"]',
                '[class*="answer"]',
                '[class*="reply"]',
                '[class*="response"]',
                '[role="listitem"]',
                '[class*="chat-bubble"]:not([class*="user"])',
                '[class*="message-body"]',
            ];
            let containers = [];
            for (const sel of selectors) {
                containers = document.querySelectorAll(sel);
                if (containers.length > %d) break;
            }
            if (containers.length <= %d) {
                // 回退：找最后一个含大量文本的元素
                const allDivs = document.querySelectorAll('div, section, article');
                let best = null, bestLen = 0;
                for (const d of allDivs) {
                    const txt = (d.textContent || '').trim();
                    const len = txt.length;
                    // 排除用户输入、导航等短文本元素
                    if (len > 200 && len < 20000 && !txt.includes('%s'.substring(0,10))) {
                        if (len > bestLen) { best = d; bestLen = len; }
                    }
                }
                if (best) {
                    const clone = best.cloneNode(true);
                    clone.querySelectorAll('button, nav, input, textarea, [contenteditable]').forEach(n => n.remove());
                    return (clone.textContent || '').trim();
                }
                return '';
            }
            const el = containers[containers.length - 1];
            const clone = el.cloneNode(true);
            clone.querySelectorAll('button, nav, input, textarea, [contenteditable], [class*="source"], [class*="reference"], [class*="action"]').forEach(n => n.remove());
            return (clone.textContent || '').trim();
        })()
        """ % (existing_msg_count, existing_msg_count, json.dumps(question, ensure_ascii=False))
        try:
            raw = self.eval(js)
            if raw and raw.strip() and len(raw.strip()) > 10:
                return AgentBrowser._clean_answer(raw.strip())
        except Exception:
            pass
        return ""

    def _extract_answer_text_diff(self, body_before: str, question: str) -> str:
        """body 文本差量提取兜底。"""
        try:
            latest = self.eval("document.body ? document.body.innerText : ''")
        except Exception:
            return ""

        before_lines = set(
            line.strip() for line in (body_before or "").splitlines() if line.strip()
        )
        new_lines = []
        for line in latest.splitlines():
            stripped = line.strip()
            if not stripped or stripped == question.strip():
                continue
            if stripped not in before_lines:
                new_lines.append(stripped)
        diff = "\n".join(new_lines).strip()
        if diff and len(diff) >= 10:
            return AgentBrowser._clean_answer(diff)
        return ""

    @staticmethod
    def _clean_answer(raw_text: str) -> str:
        """从页面新增文本中提取纯智能体回答。

        过滤：导航文本、时间戳、操作按钮、状态提示等。
        """
        if not raw_text:
            return ""
        lines = raw_text.splitlines()
        clean = []
        skip_prefixes = [
            "停止生成", "复制", "点赞", "踩", "重新生成", "继续生成",
            "收藏", "分享", "举报", "编辑", "删除", "确认", "取消",
            "发送", "输入", "请输入", "在这里", "Ctrl", "Enter",
            "刚刚", "秒前", "分钟前", "小时前", "昨天",
            "AI Agent市场", "账号登录", "Log In",
        ]
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            # 跳过单字操作按钮
            if len(stripped) <= 3 and stripped in {"复制", "点赞", "踩", "分享", "举报", "编辑", "删除", "确认", "取消", "发送"}:
                continue
            # 跳过固定前缀行
            if any(stripped.startswith(p) for p in skip_prefixes):
                continue
            # 跳过纯时间戳
            import re
            if re.match(r'^\d{1,2}:\d{2}(:\d{2})?$', stripped):
                continue
            if re.match(r'^\d{4}-\d{2}-\d{2}', stripped):
                continue
            clean.append(stripped)
        return "\n".join(clean).strip()
    def get_body_text(self) -> str:
        """获取当前页面 body.innerText，带重试容错"""
        for attempt in range(3):
            try:
                result = self.eval("document.body ? document.body.innerText : ''")
                if result and result.strip():
                    return result
                # 空结果：页面可能还在加载
                if attempt < 2:
                    time.sleep(1)
            except AgentBrowserError as e:
                if attempt < 2:
                    print(f"      ⚠️ get_body_text 重试 {attempt+1}/3: {e}")
                    time.sleep(2)
                else:
                    raise
        return ""

    def find_editable_ref(self) -> Optional[str]:
        """从 snapshot 中找到 contentEditable 区域的 ref

        Returns:
            ref 字符串(如 @e15),未找到返回 None
        """
        out = self._run(
            ["--session", self.session, "snapshot"], timeout=10
        )
        for line in out.split("\n"):
            if "contenteditable" in line.lower() and "ref=" in line:
                # 提取 ref=e15 部分
                import re
                m = re.search(r"ref=(e\d+)", line)
                if m:
                    return m.group(1)
        return None

    # ── 内部方法 ──

    def _find_bin(self) -> str:
        """查找 agent-browser 二进制"""
        # 优先检查 npm global
        candidates = [
            os.path.expanduser("~/.npm-global/bin/agent-browser"),
            "/usr/local/bin/agent-browser",
            "/usr/bin/agent-browser",
        ]
        for p in candidates:
            if os.path.isfile(p):
                return p

        # 回退到 PATH 查找
        found = shutil.which("agent-browser")
        if found:
            return found

        raise AgentBrowserError(
            "未找到 agent-browser 二进制,请先执行: npm install -g agent-browser"
        )

    def _run(
        self,
        args: List[str],
        timeout: int = DEFAULT_TIMEOUT,
        check: bool = True,
    ) -> str:
        """执行 agent-browser 命令

        Args:
            args: 命令参数(不含 'agent-browser' 本身)
            timeout: 超时秒数
            check: 是否检查返回码(False 允许失败)

        Returns:
            stdout 输出

        Raises:
            AgentBrowserError: 命令执行失败
        """
        cmd = [self._bin] + args
        env = os.environ.copy()
        if self.profile_path:
            env["AGENT_BROWSER_PROFILE"] = self.profile_path

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
        except subprocess.TimeoutExpired as e:
            raise AgentBrowserError(
                f"agent-browser 命令超时 ({timeout}s): {' '.join(args)}",
                stderr=str(e),
            )

        stdout = result.stdout.strip()
        stderr = result.stderr.strip()

        if check and result.returncode != 0:
            error_msg = stderr or stdout or "未知错误"
            raise AgentBrowserError(
                f"agent-browser 命令失败 (exit={result.returncode}): {' '.join(args)}\n{error_msg}",
                stderr=stderr,
                exit_code=result.returncode,
            )

        return stdout

    def _parse_snapshot(self, raw: str) -> Dict[str, Any]:
        """解析 snapshot 输出为结构化数据

        Returns:
            {"text": "...", "lines": [...], "editable_refs": [...]}
        """
        lines = [l for l in raw.split("\n") if l.strip()]
        editable_refs = []
        for line in lines:
            if "contenteditable" in line.lower():
                import re
                m = re.search(r"ref=(e\d+)", line)
                if m:
                    editable_refs.append(m.group(1))

        return {
            "text": raw,
            "lines": lines,
            "editable_refs": editable_refs,
        }


# ── 便利工厂 ──

def create(
    state_path: Optional[str] = None,
    profile_path: Optional[str] = None,
    session: str = "agent-market-inspect",
) -> AgentBrowser:
    """创建并返回 AgentBrowser 实例(不打开页面)"""
    return AgentBrowser(state_path=state_path, profile_path=profile_path, session=session)
