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

        type 取值: contenteditable | textarea | input
        """
        for selector, kind in (
            ("[contenteditable]", "contenteditable"),
            ("textarea[class*='chat'], textarea[class*='input'], textarea[class*='editor']", "textarea"),
            ("textarea", "textarea"),
            ("input[type='text']", "input"),
            ("input:not([type])", "input"),
        ):
            try:
                result = self.eval(f"!!document.querySelector({json.dumps(selector)})")
                if result.strip().lower() == "true":
                    return (selector, kind)
            except AgentBrowserError:
                continue
        return (None, None)

    def _find_send_button(self) -> Optional[str]:
        """查找发送按钮，返回可用的 CSS 选择器或 None。"""
        candidates = [
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

    def chat_send(self, message: str) -> str:
        """发送聊天消息：自动探测输入框类型（contenteditable/textarea/input），
        优先点击发送按钮，失败后回退 Enter。

        返回实际采用的发送方式（button 或 enter）。
        """
        if not message or not message.strip():
            raise AgentBrowserError("聊天消息不能为空")

        selector, input_type = self._detect_chat_input()
        if not selector or not input_type:
            raise AgentBrowserError(
                "未检测到聊天输入框（支持 contenteditable/textarea/input），"
                "页面可能不是标准聊天界面"
            )

        # ── 输入消息 ──
        if input_type == "contenteditable":
            self.click(selector)
            time.sleep(0.2)
            self.insert_text(message)
            time.sleep(0.2)
        else:
            # textarea 或 input：先 click 聚焦，再逐字输入以确保触发表单事件
            self.click(selector)
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

        # 3) 回退 Enter 键（先确认焦点在输入框）
        if not send_method:
            self.click(selector)
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

        Returns:
            {"answer_text": str, "status": "complete"|"timeout"|"no_stop_seen"|"empty",
             "waited": float, "stop_seen": bool, "stop_gone": bool}
        """
        import re
        result = {
            "answer_text": "",
            "status": "empty",
            "waited": 0.0,
            "stop_seen": False,
            "stop_gone": False,
        }

        if not body_before:
            body_before = self.get_body_text()

        # ── 记录发送前的回答节点快照 ──
        existing_answer_ids = self._get_answer_ids()
        existing_msg_count = len(existing_answer_ids)

        stop_generating = ["Stop generating", "停止生成", "停止回答"]
        stop_seen = False
        waited = 0.0

        # ── 阶段1: 等待 AI 开始回答 ──
        while waited < timeout * 0.6:
            time.sleep(poll_interval)
            waited += poll_interval
            try:
                latest = self.eval("document.body ? document.body.innerText : ''")
            except Exception:
                continue

            has_stop = any(s in latest for s in stop_generating)

            if not stop_seen and has_stop:
                stop_seen = True
                result["stop_seen"] = True
                continue

            if stop_seen and not has_stop:
                result["stop_gone"] = True
                break

        # ── 阶段2: 等待内容稳定 ──
        if result["stop_gone"]:
            previous = ""
            stable = 0
            while waited < timeout:
                time.sleep(poll_interval)
                waited += poll_interval

                # 再次确认停止生成没回来
                try:
                    latest = self.eval("document.body ? document.body.innerText : ''")
                except Exception:
                    continue
                if any(s in latest for s in stop_generating):
                    stable = 0
                    previous = ""
                    continue

                current = self._extract_answer_from_page(
                    question=question,
                    body_before=body_before,
                    existing_msg_count=existing_msg_count,
                    agent_url=agent_url,
                )

                if len(current) >= 10 and current == previous:
                    stable += 1
                    if stable >= stable_count:
                        result["answer_text"] = current
                        result["status"] = "complete"
                        result["waited"] = waited
                        return result
                else:
                    stable = 0
                    previous = current

        # ── 超时/异常回退 ──
        result["waited"] = waited

        if stop_seen and not result["stop_gone"]:
            # 生成未结束，尝试提取已生成部分
            fallback = self._extract_answer_from_page(
                question=question,
                body_before=body_before,
                existing_msg_count=existing_msg_count,
                agent_url=agent_url,
            )
            if fallback:
                result["answer_text"] = fallback
                result["status"] = "timeout"
            else:
                result["status"] = "no_stop_seen"
            return result

        # 没有看到停止生成按钮 → 可能不需要等待，直接尝试提取
        direct = self._extract_answer_from_page(
            question=question,
            body_before=body_before,
            existing_msg_count=existing_msg_count,
            agent_url=agent_url,
        )
        if direct:
            result["answer_text"] = direct
            result["status"] = "complete"
        else:
            result["status"] = "empty"
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
        """Aily 平台：提取最后一个 assistant 消息容器中的文本。"""
        js = r"""
        (() => {
            // Aily 助手回答容器选择器（多种布局）
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

            // 排除已有节点
            const startIdx = %d;
            if (startIdx >= containers.length) return '';

            // 取最后一个新容器
            const el = containers[containers.length - 1];
            // 排除按钮、导航、输入框
            const excludes = el.querySelectorAll('button, nav, [role="toolbar"], input, textarea, [contenteditable]');
            const clone = el.cloneNode(true);
            clone.querySelectorAll('button, nav, [role="toolbar"], input, textarea, [contenteditable], [class*="action"], [class*="toolbar"], [class*="source"], [class*="reference"]').forEach(n => n.remove());
            return (clone.textContent || '').trim();
        })()
        """ % existing_msg_count
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
