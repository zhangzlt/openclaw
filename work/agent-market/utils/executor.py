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


class PlaybookExecutor:
    """按白名单执行 JSON 操作计划，输出结构化结果。"""

    def __init__(self, browser: AgentBrowser):
        self.browser = browser
        self.log: list[dict] = []
        self._tabs_before: list = []

    def _snapshot_tabs(self):
        """记录当前标签页快照（在可能触发新标签页的操作前调用）。"""
        self._tabs_before = self.browser.list_tabs()

    def _follow_if_new_tab(self, wait_sec: float = 3.0) -> bool:
        """检测并跟进新标签页。

        用于处理 Market/Aily 页面点击「打开」按钮后智能体在新标签页中打开的场景。

        Returns: True 如果检测到新标签页并已切换
        """
        if not self._tabs_before:
            return False

        import time as _time
        before_ids = {t["id"] for t in self._tabs_before}
        deadline = _time.time() + 15.0

        while _time.time() < deadline:
            try:
                after = self.browser.list_tabs()
            except Exception:
                _time.sleep(1)
                continue

            after_ids = {t["id"] for t in after}
            new_ids = after_ids - before_ids

            for new_id in sorted(new_ids, key=lambda x: int(x[1:])):
                new_tab = next((t for t in after if t["id"] == new_id), None)
                new_url = new_tab.get("url", "") if new_tab else ""
                if new_url and new_url not in ("about:blank", ""):
                    self.browser.switch_tab(new_id)
                    # 关闭旧标签页（保留最多 2 个）
                    for old_id in sorted(before_ids)[:-1]:
                        try:
                            self.browser.close_tab(old_id)
                        except Exception:
                            pass
                    _time.sleep(wait_sec)
                    return True

            _time.sleep(1.0)

        return False

    # ── 主入口 ──

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

            # 在可能触发新标签页的操作前记录标签页快照
            if action in ("open", "click"):
                self._snapshot_tabs()

            try:
                result = self._dispatch(action, step)

                # 检测并跟进新标签页
                if action in ("open", "click"):
                    if self._follow_if_new_tab():
                        self._log(i, action, "tabswitched", "检测到新标签页，已自动切换")

                if action == "chat_send":
                    q_results.append({
                        "question": step.get("message", ""),
                        "step_index": i,
                    })
                elif action == "chat_wait" and q_results:
                    wait_result = result or {}
                    # chat_wait 现在返回 dict: {answer_text, status, waited, ...}
                    if isinstance(wait_result, dict):
                        q_results[-1]["response"] = wait_result.get("answer_text", "")
                        q_results[-1]["wait_status"] = wait_result.get("status", "empty")
                        q_results[-1]["waited"] = wait_result.get("waited", 0)
                        q_results[-1]["stop_seen"] = wait_result.get("stop_seen", False)
                        q_results[-1]["stop_gone"] = wait_result.get("stop_gone", False)
                        answer_text = wait_result.get("answer_text", "")
                        ok = bool(answer_text and len(str(answer_text)) > 10)
                        q_results[-1]["success"] = ok
                        q_results[-1]["elapsed"] = step.get("timeout", 0)
                        q_results[-1]["error"] = None if ok else "未返回有效回复"
                    else:
                        # 旧版兼容（返回 str）
                        q_results[-1]["response"] = str(wait_result) if wait_result else ""
                        ok = bool(wait_result and len(str(wait_result)) > 10)
                        q_results[-1]["success"] = ok
                        q_results[-1]["elapsed"] = step.get("timeout", 0)
                        q_results[-1]["error"] = None if ok else "未返回有效回复"
                elif action == "verify":
                    # 最后一步验证
                    pass

                self._log(i, action, "ok", str(result)[:200] if result else "")

            except (AgentBrowserError, Exception) as e:
                self._log(i, action, "error", str(e)[:200])
                screenshot = self._screenshot(screenshot_dir, agent_id, f"error_step{i}")
                return self._build_result(
                    status="chat_error",
                    error=f"步骤 {i} ({action}) 失败: {str(e)[:200]}",
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
                follow_new_tab=True,   # 处理 target="_blank" / window.open 打开的新标签页
                new_tab_timeout=step.get("new_tab_timeout", 20.0),
            )
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
            return self.browser.chat_send(step.get("message") or step.get("text", ""))

        elif action == "chat_wait":
            return self.browser.chat_wait(timeout=step.get("timeout", 60))

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
        """截图并返回路径。"""
        import os
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, f"{agent_id}_{label}.png")
        return self.browser.screenshot(path)

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
