"""
剧本执行引擎

按白名单操作集执行 JSON 操作计划，所有操作通过 agent-browser 完成。
输出结构化执行日志和验证结果。
"""

import json
import time
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent.parent))  # work/
from agent_browser_wrapper import AgentBrowser, AgentBrowserError

CST = timezone(timedelta(hours=8))

# ── 白名单操作集 ──
ALLOWED_ACTIONS = {
    "open", "click", "fill", "chat_send", "chat_wait", "press",
    "hover", "find_and_click", "upload", "snapshot", "screenshot",
    "eval", "scroll", "wait", "verify",
}

# ── 容错交互操作 ──
# 这些操作属于「可选交互」而非健康检查：元素缺失时降级为警告并继续，
# 健康检查以最终的 verify 步骤为准。避免 LLM 生成的可选 click（如点击
# 一个并不存在的「刷新」按钮）误报整个智能体为失败。
TOLERANT_ACTIONS = {"click", "find_and_click", "hover", "press"}


def _is_element_not_found(err: str) -> bool:
    """判断 agent-browser 错误是否为「元素未找到」（可容忍降级）。"""
    low = (err or "").lower()
    markers = [
        "no element found",
        "not found by text",
        "not found by selector",
        "element not found",
        "未找到元素",
        "找不到元素",
    ]
    return any(m in low for m in markers)


class PlaybookExecutor:
    """按白名单执行 JSON 操作计划，输出结构化结果。"""

    def __init__(self, browser: AgentBrowser):
        self.browser = browser
        self.log: list[dict] = []
        self._tab_context: dict | None = None  # click_and_follow_popup 的结果

    # ── 主入口 ──

    @staticmethod
    def _preset_question_before(steps: list, index: int) -> str:
        """查找 chat_wait 之前最近的预设问题文本（find_and_click / click 的 text）。

        部分剧本用 find_and_click 点击预设问题而非 chat_send 输入，
        此时 chat_wait 的 else 分支无法从 chat_send 拿到 question，
        需要向前回溯找点击的问题文本，避免 question_text 落空。
        """
        for step in reversed(steps[:index]):
            action = step.get("action")
            if action == "find_and_click":
                return step.get("text", "")
            if action == "click" and step.get("text"):
                return step.get("text", "")
            if action in ("open", "chat_wait", "chat_send"):
                # 新页面 / 上一轮对话边界，停止向前查找
                break
        return ""

    def execute(self, plan: dict, screenshot_dir: str, agent_id: int) -> dict:
        """执行一个操作计划。

        Returns:
            {
                status: "ok"|"chat_error"|"skipped",
                error: str | None,
                q_results: [{question, response, success, elapsed}],
                screenshot: str,
                log: [{step, action, status, detail, timestamp}],
                avg_elapsed: float,
                verified: bool,
                verify_detail: str,
            }
        """
        start_time = time.time()
        self.log = []

        strategy = plan.get("strategy", "generic")

        if strategy == "skip":
            return {
                "status": "skipped",
                "error": plan.get("reasoning", "剧本标记为跳过"),
                "q_results": [],
                "screenshot": self._screenshot(screenshot_dir, agent_id, "skip"),
                "log": self.log,
                "avg_elapsed": 0,
                "verified": True,
                "verify_detail": "skip",
            }

        steps = plan.get("steps", [])
        if not steps:
            return self._error("剧本无操作步骤", screenshot_dir, agent_id)

        # 验证白名单
        for i, step in enumerate(steps):
            if step.get("action") not in ALLOWED_ACTIONS:
                return self._error(
                    f"步骤 {i} 使用了非白名单操作: {step.get('action')}",
                    screenshot_dir, agent_id,
                )

        q_results = []

        for i, step in enumerate(steps):
            action = step["action"]
            self._log(i, action, "start", "")

            try:
                result = self._dispatch(action, step)

                if action == "chat_send":
                    q_results.append({
                        "question": step.get("message", ""),
                        "step_index": i,
                    })
                elif action == "chat_wait":
                    wait_result = result or {}
                    # chat_wait 现在返回 dict: {answer_text, status, waited, ...}
                    if isinstance(wait_result, dict):
                        answer_text = wait_result.get("answer_text", "")
                    else:
                        # 旧版兼容（返回 str）
                        answer_text = str(wait_result) if wait_result else ""

                    if q_results:
                        # 有 chat_send 时：将回答挂到上一个问题下
                        q_results[-1]["response"] = answer_text
                        if isinstance(wait_result, dict):
                            q_results[-1]["wait_status"] = wait_result.get("status", "empty")
                            q_results[-1]["waited"] = wait_result.get("waited", 0)
                            q_results[-1]["stop_seen"] = wait_result.get("stop_seen", False)
                            q_results[-1]["stop_gone"] = wait_result.get("stop_gone", False)
                    else:
                        # 无 chat_send 时（如 find_and_click 点了预设问题）：新建条目
                        question_text = self._preset_question_before(steps, i)
                        ok = bool(answer_text and len(str(answer_text)) > 10)
                        q_results.append({
                            "question": question_text,
                            "response": answer_text,
                            "wait_status": wait_result.get("status", "empty") if isinstance(wait_result, dict) else "",
                            "waited": wait_result.get("waited", 0) if isinstance(wait_result, dict) else 0,
                            "stop_seen": wait_result.get("stop_seen", False) if isinstance(wait_result, dict) else False,
                            "stop_gone": wait_result.get("stop_gone", False) if isinstance(wait_result, dict) else False,
                            "success": ok,
                            "elapsed": step.get("timeout", 0),
                            "error": None if ok else "未返回有效回复",
                        })
                    # chat_wait 成功返回回答即视为通过（不依赖 verify）
                    if q_results and q_results[-1].get("success"):
                        # 如果还没有 verify 步骤，将 verified 标记为 True
                        pass
                elif action == "verify":
                    # 最后一步验证
                    pass

                self._log(i, action, "ok", str(result)[:200] if result else "")

            except (AgentBrowserError, Exception) as e:
                err_str = str(e)[:200]
                # 交互型步骤元素未找到时降级为警告并继续（非致命）
                if action in TOLERANT_ACTIONS and _is_element_not_found(err_str):
                    self._log(i, action, "warning", f"元素未找到，跳过: {err_str[:150]}")
                    print(f"      ⚠️ 步骤 {i} ({action}) 元素未找到，跳过（非致命）")
                    continue
                self._log(i, action, "error", err_str)
                screenshot = self._screenshot(screenshot_dir, agent_id, f"error_step{i}")
                # 区分：无聊天输入框（企业 agent 无 web 入口）vs 真正的对话错误
                status = "no_web_chat" if "未检测到聊天输入框" in err_str else "chat_error"
                return self._build_result(
                    status=status,
                    error=f"步骤 {i} ({action}) 失败: {err_str}",
                    q_results=q_results,
                    screenshot=screenshot,
                    elapsed=round(time.time() - start_time, 1),
                )

        # ── 验证阶段 ──
        verify_spec = plan.get("verify", {})
        verified, verify_detail = self._verify(verify_spec)
        final_screenshot = self._screenshot(screenshot_dir, agent_id, "final")
        elapsed = round(time.time() - start_time, 1)

        status = "ok" if verified else "chat_error"
        return self._build_result(
            status=status,
            error=None if verified else f"验证失败: {verify_detail}",
            q_results=q_results,
            screenshot=final_screenshot,
            elapsed=elapsed,
            verified=verified,
            verify_detail=verify_detail,
        )

    # ── 操作分发 ──

    def _dispatch(self, action: str, step: dict) -> str:
        """按白名单分发单个操作到 agent-browser。"""
        if action == "open":
            self.browser.open(
                step["url"],
                wait_sec=step.get("wait_sec", 3.0),
                wait_selector=step.get("wait_selector"),
                wait_timeout=step.get("wait_timeout", 15),
            )
            self._auto_authorize_after_open(step["url"])
            self._auto_login_dcone_after_open(step["url"])
            return self.browser.get_url()

        elif action == "click":
            selector = step.get("selector")
            text = step.get("text")
            if selector:
                self.browser.click(selector, timeout=step.get("timeout", 10))
            elif text:
                self.browser.find_and_click(text, timeout=step.get("timeout", 10))
            else:
                raise ValueError("click 需要 selector 或 text")
            return "ok"

        elif action == "fill":
            self.browser.fill(step["selector"], step["text"],
                              timeout=step.get("timeout", 10))
            return f"filled"

        elif action == "chat_send":
            msg = step.get("message") or step.get("text", "")
            # 发送前采集 body_before，供 chat_wait 提取差量回复
            body_before = self.browser.get_body_text()
            agent_url = self.browser._url or ""
            self._chat_state = {
                "body_before": body_before,
                "question": msg,
                "agent_url": agent_url,
            }
            return self.browser.chat_send(msg)

        elif action == "chat_wait":
            # 注入 chat_send 自动捕获的 body_before / question / agent_url
            state = getattr(self, "_chat_state", None) or {}
            return self.browser.chat_wait(
                timeout=step.get("timeout", 60),
                body_before=step.get("body_before", "") or state.get("body_before", ""),
                question=step.get("question", "") or state.get("question", ""),
                agent_url=step.get("agent_url", "") or state.get("agent_url", ""),
            )

        elif action == "press":
            self.browser.press(step["key"], timeout=step.get("timeout", 10))
            return "ok"

        elif action == "hover":
            self.browser.hover(step["selector"], timeout=step.get("timeout", 10))
            return "ok"

        elif action == "find_and_click":
            self.browser.find_and_click(step["text"], timeout=step.get("timeout", 10))
            return "ok"

        elif action == "upload":
            self.browser.upload(step["selector"], *step["files"],
                                timeout=step.get("timeout", 15))
            return f"uploaded {len(step['files'])}"

        elif action == "snapshot":
            snap = self.browser.snapshot(timeout=step.get("timeout", 10))
            return str(snap)[:500]

        elif action == "screenshot":
            path = step.get("path")
            return self.browser.screenshot(path, timeout=step.get("timeout", 10))

        elif action == "eval":
            return self.browser.eval(step["js"], timeout=step.get("timeout", 10))

        elif action == "scroll":
            self.browser.eval(f"window.scrollBy(0, {step['pixels']})")
            return "ok"

        elif action == "wait":
            time.sleep(step["seconds"])
            return "ok"

        elif action == "verify":
            expected = step.get("expected_text", "")
            body = self.browser.get_body_text()
            ok = expected.lower() in body.lower() if expected else True
            return json.dumps({"ok": ok, "expected": expected,
                               "body_snippet": body[:200]})

        else:
            raise ValueError(f"未实现的操作: {action}")

    # ── 辅助方法 ──

    def _auto_authorize_after_open(self, target_url: str = "", attempts: int = 2) -> bool:
        """open 后检测飞书 OAuth 授权页，自动点击 Authorize/授权 按钮。

        覆盖所有剧本（缓存/LLM/fallback）：非对话 Web 应用打开后可能被重定向到
        accounts.feishu.cn 授权页，若不在 open 后处理，最终截图会停留在授权页。

        target_url: 授权成功后的回跳目标 URL（授权后仍停留时重新导航）

        返回 True 表示已离开授权页或无需授权，False 表示授权失败仍停留在授权页。
        """
        for attempt in range(attempts):
            try:
                url = self.browser.get_url() or ""
            except Exception:
                return True
            # 仅处理飞书 OAuth 授权页（URL 级判定，最高优先级）
            if "accounts.feishu.cn" not in url:
                return True
            # 是授权页：等待页面渲染完成后点击授权按钮
            time.sleep(1)
            clicked = False
            for btn in ("Authorize", "授权", "确认授权", "允许",
                        "同意", "Accept", "继续", "Continue", "同意并继续"):
                try:
                    self.browser.find_and_click(btn, timeout=5)
                    clicked = True
                    break
                except Exception:
                    continue
            if not clicked:
                # 兜底：eval 点击非 Reject/拒绝 的填充按钮
                try:
                    r = self.browser.eval(
                        """(() => {
                            const btns = document.querySelectorAll('button');
                            const targets = ['authorize', '授权', '确认授权', '允许', '同意', 'accept', 'continue', '继续'];
                            const forbidden = ['reject', '拒绝', 'use another account', '使用其他账号'];
                            for (const b of btns) {
                                const t = (b.textContent || '').trim().toLowerCase();
                                if (targets.some(x => t.includes(x)) && !forbidden.some(f => t.includes(f))) {
                                    b.click(); return b.textContent.trim();
                                }
                            }
                            return null;
                        })()"""
                    )
                    clicked = bool(r)
                except Exception:
                    pass
            # 等待 URL 变化（最多 12s）
            for _ in range(12):
                time.sleep(1)
                try:
                    if "accounts.feishu.cn" not in (self.browser.get_url() or ""):
                        return True
                except Exception:
                    pass
            # 仍停留授权页：重新导航目标 URL（授权可能已生效但未自动回跳）
            if target_url and attempt < attempts - 1:
                try:
                    self.browser.open(target_url, wait_sec=3)
                except Exception:
                    pass
        return False

    def _load_dcone_credentials(self) -> dict:
        """读取 DCone 登录凭据（神州数码 IT 身份统一认证平台）。

        凭据存放于 <agent-market>/.auth/credentials.json 的 agent_market 段
        （dstest 账号为统一认证账号，可登录 DCone）。
        """
        try:
            cred_path = Path(__file__).parent.parent / ".auth" / "credentials.json"
            if not cred_path.exists():
                return {}
            with open(cred_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # 优先 dcone 段，回退 agent_market 段
            return data.get("dcone") or data.get("agent_market") or {}
        except Exception:
            return {}

    def _auto_login_dcone_after_open(self, target_url: str) -> bool:
        """open 后检测 DCone 登录页（神州数码 IT 身份统一认证平台），自动登录。

        DCone 登录页特征：title 含「认证平台」/「登录」，且存在
        usernameInput + passwordInput + login_submit() 的 UUiP 登录表单。
        登录成功后 pkmslogin.form 无 redirect 参数，需重新导航回目标 URL。

        返回 True 表示已离开登录页或无需登录，False 表示登录失败。
        """
        try:
            title = self.browser.get_title() or ""
        except Exception:
            return True
        # 仅处理 DCone 登录页（title 级判定）
        if "认证平台" not in title and "登录" not in title:
            return True

        creds = self._load_dcone_credentials()
        username = creds.get("username", "")
        password = creds.get("password", "")
        if not username or not password:
            return True  # 无凭据，跳过

        # 确认登录表单存在
        try:
            has_form = self.browser.eval(
                "!!document.getElementById('usernameInput') && !!document.getElementById('passwordInput')"
            ).strip().lower() == "true"
        except Exception:
            has_form = False
        if not has_form:
            return True  # 不是 DCone 登录表单，跳过

        # 填账号密码并提交
        try:
            self.browser.eval(
                "(() => {"
                f"document.getElementById('usernameInput').value = {json.dumps(username)};"
                f"document.getElementById('passwordInput').value = {json.dumps(password)};"
                "return 'ok';"
                "})()"
            )
            self.browser.eval("login_submit()")
        except Exception:
            return False

        # 登录后 pkmslogin.form 无 redirect，重新导航回目标 URL
        time.sleep(4)
        try:
            self.browser.open(
                target_url,
                wait_sec=5,
                wait_selector="[contenteditable], textarea, input, button, a",
                wait_timeout=15,
            )
        except Exception:
            pass
        time.sleep(2)
        try:
            title_after = self.browser.get_title() or ""
            if "认证平台" in title_after or "登录" in title_after:
                return False
        except Exception:
            pass
        return True

    def _verify(self, spec: dict) -> tuple[bool, str]:
        """执行业务验证。"""
        if not spec:
            return (True, "无验证规则")
        expected = spec.get("expected_text", "")
        description = spec.get("description", "")
        if not expected:
            return (True, "无预期文本")
        try:
            body = self.browser.get_body_text()
            ok = expected.lower() in body.lower()
            detail = f"✓ {description}" if ok else f"✗ 未找到 '{expected}'"
            return (ok, detail)
        except Exception as e:
            return (False, f"验证异常: {e}")

    def _screenshot(self, directory: str, agent_id: int, label: str) -> str:
        """截图并返回路径，同时生成对应 .json 元数据文件。

        _bind_result() 通过 Path(ss).with_suffix(".json") 查找元数据，
        因此必须保持 PNG 与 JSON 文件名一致。
        """
        import os
        import hashlib
        import struct
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, f"{agent_id}_{label}.png")
        self.browser.screenshot(path)

        # 读取截图并写入 JSON 元数据（与 inspect_daily.py 的 _try_screenshot 保持一致）
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
            if len(raw) >= 1000 and raw[:8] == b"\x89PNG\r\n\x1a\n":
                width, height = struct.unpack(">II", raw[16:24])
                try:
                    current_url = self.browser.get_url()
                except Exception:
                    current_url = ""
                try:
                    current_title = self.browser.get_title()
                except Exception:
                    current_title = ""
                try:
                    body_text = self.browser.get_body_text()
                except Exception:
                    body_text = ""

                metadata = {
                    "run_id": "EXECUTOR",
                    "agent_id": agent_id,
                    "label": label,
                    "captured_at": datetime.now(CST).isoformat(),
                    "url": current_url,
                    "title": current_title,
                    "body_contains_agent_name": False,
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "width": width,
                    "height": height,
                    "bytes": len(raw),
                }
                metadata_path = Path(path).with_suffix(".json")
                with open(metadata_path, "w", encoding="utf-8") as mf:
                    json.dump(metadata, mf, ensure_ascii=False, indent=2)
        except Exception:
            pass  # 元数据写入失败不阻断截图
        return path

    def _error(self, msg: str, screenshot_dir: str, agent_id: int) -> dict:
        screenshot = self._screenshot(screenshot_dir, agent_id, "error")
        return self._build_result("chat_error", msg, [], screenshot, 0)

    def _build_result(self, status: str, error: str | None, q_results: list,
                      screenshot: str, elapsed: float,
                      verified: bool = False, verify_detail: str = "") -> dict:
        return {
            "status": status,
            "error": error,
            "q_results": q_results,
            "screenshot": screenshot,
            "log": self.log,
            "avg_elapsed": elapsed,
            "verified": verified,
            "verify_detail": verify_detail,
        }

    def _log(self, step_idx: int, action: str, status: str, detail: str):
        self.log.append({
            "step": step_idx,
            "action": action,
            "status": status,
            "detail": str(detail)[:300] if detail else "",
            "timestamp": datetime.now(CST).isoformat(),
        })
