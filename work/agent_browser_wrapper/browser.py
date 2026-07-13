"""
agent-browser Python 封装层

将 agent-browser CLI（Rust 原生 CDP 工具）封装为 Python API，
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
    BIN = "agent-browser"                        # 二进制路径（需在 PATH 中）
    DEFAULT_SESSION = "agent-market-inspect"     # 默认会话名
    DEFAULT_TIMEOUT = 30                         # 默认命令超时（秒）

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
            session: 会话名（用于 daemon 隔离）
            bin_path: agent-browser 二进制路径（默认从 PATH 查找）
        """
        self.state_path = state_path
        self.profile_path = profile_path or os.getenv("FEISHU_BROWSER_PROFILE", "").strip() or None
        self.session = session
        self._bin = bin_path or self._find_bin()
        self._opened = False
        self._state_loaded = False   # 首次 open 后标记，避免重复 --state
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
        """打开 URL（首次含 state 载入，后续仅导航）

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
            True 如果找到，False 超时
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
            selector: CSS 选择器或 ref（如 @e14、[contenteditable]）
        """
        self._run(["--session", self.session, "click", selector], timeout=timeout)

    def focus(self, selector: str, timeout: int = 10):
        """聚焦元素"""
        self._run(["--session", self.session, "focus", selector], timeout=timeout)

    def fill(self, selector: str, text: str, timeout: int = 10):
        """填充 input/textarea（非 contentEditable）"""
        self._run(
            ["--session", self.session, "fill", selector, text], timeout=timeout
        )

    def type_text(self, selector: str, text: str, delay_ms: int = 30, timeout: int = 10):
        """逐字输入（模拟键盘，含延迟）"""
        self._run(
            ["--session", self.session, "type", selector, text], timeout=timeout
        )

    def keyboard_type(self, text: str, timeout: int = 10):
        """在当前焦点输入键盘文本"""
        self._run(
            ["--session", self.session, "keyboard", "type", text], timeout=timeout
        )

    def insert_text(self, text: str, timeout: int = 10):
        """在当前焦点插入文本（不触发键盘事件，contentEditable 专用）"""
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
        """按键（Enter, Tab, Control+a 等）"""
        self._run(["--session", self.session, "press", key], timeout=timeout)

    def hover(self, selector: str, timeout: int = 10):
        """悬停元素"""
        self._run(["--session", self.session, "hover", selector], timeout=timeout)

    def eval(self, js: str, timeout: int = 10) -> str:
        """执行 JavaScript 并返回结果（自动解析 JSON 字符串）"""
        output = self._run(
            ["--session", self.session, "eval", js], timeout=timeout
        )
        # agent-browser eval 返回 JSON 编码的字符串（如 "..."），
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
            path: 保存路径（默认生成临时路径）
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

    def chat_send(self, message: str) -> str:
        """发送聊天消息：优先点击发送按钮，失败后回退 Enter。

        返回实际采用的发送方式（button 或 enter）。输入后会先确认文本
        已进入编辑框，避免文件上传弹窗或焦点漂移造成“看似发送”。
        """
        if not message or not message.strip():
            raise AgentBrowserError("聊天消息不能为空")

        selector = "[contenteditable]"
        self.click(selector)
        time.sleep(0.2)
        self.insert_text(message)
        time.sleep(0.2)

        quoted = json.dumps(message, ensure_ascii=False)
        draft_ok = self.eval(
            f"""(() => {{
                const el = document.querySelector('[contenteditable]');
                return !!el && (el.innerText || el.textContent || '').includes({quoted});
            }})()"""
        )
        if draft_ok.strip().lower() != "true":
            raise AgentBrowserError("消息未进入聊天输入框，可能被弹窗或错误焦点拦截")

        # 可访问名称和明确属性优先，避免误点附件上传按钮。
        for selector_candidate in (
            'button[aria-label*="发送"]',
            'button[aria-label*="Send"]',
            'button[data-testid*="send"]',
            '[role="button"][aria-label*="发送"]',
        ):
            try:
                self.click(selector_candidate, timeout=3)
                time.sleep(0.6)
                if self._message_was_submitted(message):
                    return "button"
            except AgentBrowserError:
                continue

        try:
            self.find_and_click("发送", timeout=3)
            time.sleep(0.6)
            if self._message_was_submitted(message):
                return "button"
        except AgentBrowserError:
            pass

        self.click(selector)
        self.press("Enter")
        time.sleep(0.6)
        if not self._message_was_submitted(message):
            raise AgentBrowserError("点击发送按钮和按 Enter 均未提交消息")
        return "enter"

    def _message_was_submitted(self, message: str) -> bool:
        quoted = json.dumps(message, ensure_ascii=False)
        result = self.eval(
            f"""(() => {{
                const el = document.querySelector('[contenteditable]');
                const draft = el ? (el.innerText || el.textContent || '').trim() : '';
                const body = document.body.innerText || '';
                return draft.length === 0 && body.includes({quoted});
            }})()"""
        )
        return result.strip().lower() == "true"

    def chat_wait(
        self,
        timeout: int = 60,
        poll_interval: float = 2.0,
        stable_count: int = 2,
        body_before: str = "",
        question: str = "",
    ) -> Optional[str]:
        """等待本次提问之后出现的新增回复稳定，而不是等待整页静止。"""
        if not body_before:
            body_before = self.get_body_text()

        before_lines = {
            line.strip() for line in body_before.splitlines() if line.strip()
        }
        previous_delta = ""
        stable = 0
        latest = body_before
        waited = 0.0

        while waited < timeout:
            time.sleep(poll_interval)
            waited += poll_interval
            latest = self.eval("document.body.innerText")
            new_lines = []
            for line in latest.splitlines():
                stripped = line.strip()
                if not stripped or stripped == question.strip():
                    continue
                if stripped not in before_lines:
                    new_lines.append(stripped)
            delta = "\n".join(new_lines).strip()

            # 至少出现 5 个字符的新内容，并连续两轮保持稳定。
            if len(delta) >= 5 and delta == previous_delta:
                stable += 1
                if stable >= stable_count:
                    return latest
            else:
                stable = 0
                previous_delta = delta

        return latest if latest != body_before else None
    def get_body_text(self) -> str:
        """获取当前页面 body.innerText"""
        return self.eval("document.body.innerText")

    def find_editable_ref(self) -> Optional[str]:
        """从 snapshot 中找到 contentEditable 区域的 ref

        Returns:
            ref 字符串（如 @e15），未找到返回 None
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
            "未找到 agent-browser 二进制，请先执行: npm install -g agent-browser"
        )

    def _run(
        self,
        args: List[str],
        timeout: int = DEFAULT_TIMEOUT,
        check: bool = True,
    ) -> str:
        """执行 agent-browser 命令

        Args:
            args: 命令参数（不含 'agent-browser' 本身）
            timeout: 超时秒数
            check: 是否检查返回码（False 允许失败）

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
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
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
    state_path: str,
    profile_path: Optional[str] = None,
    session: str = "agent-market-inspect",
) -> AgentBrowser:
    """创建并返回 AgentBrowser 实例（不打开页面）"""
    return AgentBrowser(state_path=state_path, profile_path=profile_path, session=session)
