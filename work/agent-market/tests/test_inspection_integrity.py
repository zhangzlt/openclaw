import asyncio
import importlib.util
import json
import struct
import sys
import shutil
import uuid
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(WORK))

spec = importlib.util.spec_from_file_location("inspect_daily", ROOT / "inspect_daily.py")
inspection = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inspection)

browser_spec = importlib.util.spec_from_file_location(
    "agent_browser", WORK / "agent_browser_wrapper" / "browser.py"
)
browser_module = importlib.util.module_from_spec(browser_spec)
browser_spec.loader.exec_module(browser_module)


class FakeScreenshotBrowser:
    def __init__(self, url: str, title: str, body: str):
        self.url = url
        self.title = title
        self.body = body

    def screenshot(self, path: str):
        payload = self.url.encode("utf-8")
        raw = (
            b"\x89PNG\r\n\x1a\n"
            + b"\x00" * 8
            + struct.pack(">II", 1280, 720)
            + payload
            + b"x" * 1200
        )
        Path(path).write_bytes(raw)

    def get_url(self):
        return self.url

    def get_title(self):
        return self.title

    def get_body_text(self):
        return self.body


class FakeChatBrowser(browser_module.AgentBrowser):
    def __init__(self, button_works: bool):
        self.button_works = button_works
        self.draft = ""
        self.submitted = False

    def click(self, selector, timeout=10):
        if selector == "[contenteditable]":
            return
        if self.button_works and "发送" in selector:
            self.submitted = True
            self.draft = ""
            return
        raise browser_module.AgentBrowserError("未找到按钮")

    def insert_text(self, text, timeout=10):
        self.draft = text

    def find_and_click(self, text, timeout=10):
        raise browser_module.AgentBrowserError("未找到文本按钮")

    def press(self, key, timeout=10):
        if key == "Enter":
            self.submitted = True
            self.draft = ""

    def eval(self, js, timeout=10):
        if "draft.length === 0" in js:
            return "true" if self.submitted else "false"
        return "true" if self.draft else "false"


class InspectionIntegrityTests(unittest.TestCase):
    def setUp(self):
        base = ROOT / "tests" / ".artifacts" / uuid.uuid4().hex
        base.mkdir(parents=True, exist_ok=True)
        self.temp_path = base
        inspection.REPORTS_DIR = base / "reports"
        inspection.RUNS_DIR = inspection.REPORTS_DIR / "runs"
        inspection.RUN_LOCK_PATH = inspection.REPORTS_DIR / ".inspection.lock"
        inspection.RUN_LOCK_FD = None
        inspection._configure_run_context("20260713_090000")

    def tearDown(self):
        inspection._release_run_lock()
        shutil.rmtree(self.temp_path, ignore_errors=True)

    def test_run_lock_rejects_overlap(self):
        inspection._acquire_run_lock()
        with self.assertRaises(RuntimeError):
            inspection._acquire_run_lock()

    def test_screenshot_metadata_and_order_are_hard_bound(self):
        agents = [{"id": 11, "name": "甲"}, {"id": 22, "name": "乙"}]
        results = []
        for index, agent in enumerate(agents, 1):
            inspection.CURRENT_AGENT_CONTEXT = {
                "inspection_index": index,
                "agent_id": agent["id"],
                "agent_name": agent["name"],
            }
            directory = inspection._agent_screenshot_dir(agent["id"], index)
            screenshot = inspection._try_screenshot(
                FakeScreenshotBrowser(
                    f"https://example.test/{agent['id']}",
                    agent["name"],
                    f"{agent['name']} 最终响应",
                ),
                directory,
                agent["id"],
            )
            result = inspection._bind_result(
                {
                    "status": "ok",
                    "q_results": [{
                        "question": "测试",
                        "response": "有效响应",
                        "success": True,
                        "elapsed": 1,
                    }],
                    "screenshot": screenshot,
                },
                agent,
                index,
            )
            results.append(result)

        validation = inspection._validate_run_results(agents, results)
        self.assertTrue(validation["complete"], validation["errors"])
        self.assertEqual(validation["screenshot_count"], 2)
        for result in results:
            metadata = json.loads(Path(result["evidence_metadata"]).read_text("utf-8"))
            self.assertEqual(metadata["inspection_index"], result["inspection_index"])
            self.assertEqual(metadata["agent_id"], result["agent_id"])

    def test_manifest_keeps_one_image_per_agent_in_market_order(self):
        agents = [{"id": 11, "name": "甲"}, {"id": 22, "name": "乙"}]
        results = []
        for index, agent in enumerate(agents, 1):
            inspection.CURRENT_AGENT_CONTEXT = {
                "inspection_index": index,
                "agent_id": agent["id"],
                "agent_name": agent["name"],
            }
            screenshot = inspection._try_screenshot(
                FakeScreenshotBrowser(
                    f"https://example.test/{agent['id']}",
                    agent["name"],
                    f"{agent['name']} 最终响应",
                ),
                inspection._agent_screenshot_dir(agent["id"], index),
                agent["id"],
            )
            results.append(inspection._bind_result({
                "status": "ok",
                "q_results": [{
                    "question": "测试操作",
                    "response": "有效响应",
                    "success": True,
                    "elapsed": 1,
                }],
                "screenshot": screenshot,
            }, agent, index))

        inspection.RUN_VALIDATION = inspection._validate_run_results(agents, results)
        report_path = inspection.RUN_DIR / "报告.md"
        report_path.write_text("测试", encoding="utf-8")
        manifest_path = inspection.generate_delivery_manifest(
            "总结", results, inspection.datetime.datetime.now(), report_path
        )
        manifest = json.loads(Path(manifest_path).read_text("utf-8"))
        sections = [item for item in manifest["sections"] if item["id"].startswith("agent_")]
        self.assertEqual([item["agent_id"] for item in sections], [11, 22])
        self.assertEqual([len(item["images"]) for item in sections], [1, 1])
        self.assertEqual(
            [item["inspection_index"] for item in sections],
            [1, 2],
        )
    def test_full_dispatcher_executes_actual_market_order(self):
        agents = [
            {"id": 1, "name": "对话", "url": "https://aily.feishu.cn/agents/a"},
            {"id": 2, "name": "工具", "url": "https://example.test/tool"},
            {"id": 3, "name": "接口", "openType": "api", "source": "dify"},
        ]
        calls = []

        async def fake_chat(items, _token):
            calls.append(items[0]["id"])
            return [{"status": "ok", "screenshot": "", "q_results": []}]

        async def fake_non_chat(items, _token):
            calls.append(items[0]["id"])
            return [{"status": "ok", "screenshot": "", "q_results": []}]

        def fake_bind(result, agent, index):
            result.update({
                "agent_id": agent["id"],
                "name": agent["name"],
                "inspection_index": index,
            })
            return result

        with patch.object(inspection, "run_chat_tests", side_effect=fake_chat), \
             patch.object(inspection, "_run_non_chat_tests", side_effect=fake_non_chat), \
             patch.object(inspection, "_bind_result", side_effect=fake_bind):
            results = asyncio.run(inspection._run_full_inspection(agents, "token"))

        self.assertEqual(calls, [1, 2, 3])
        self.assertEqual(
            [item["inspection_index"] for item in results],
            [1, 2, 3],
        )
    def test_wrong_order_fails_final_gate(self):
        agents = [{"id": 1, "name": "甲"}, {"id": 2, "name": "乙"}]
        results = [
            {"agent_id": 2, "inspection_index": 1, "screenshot": ""},
            {"agent_id": 1, "inspection_index": 2, "screenshot": ""},
        ]
        validation = inspection._validate_run_results(agents, results)
        self.assertFalse(validation["complete"])
        self.assertIn("结果顺序与市场顺序不一致", validation["errors"])

    def test_checkpoint_is_written_after_each_item(self):
        agents = [{"id": 1}, {"id": 2}]
        inspection._save_checkpoint(agents, [{"agent_id": 1}])
        payload = json.loads(inspection.CHECKPOINT_PATH.read_text("utf-8"))
        self.assertEqual(payload["completed_count"], 1)
        self.assertEqual(payload["next_inspection_index"], 2)

    @patch.object(browser_module.time, "sleep", return_value=None)
    def test_chat_send_falls_back_to_enter(self, _sleep):
        browser = FakeChatBrowser(button_works=False)
        self.assertEqual(browser.chat_send("你好"), "enter")
        self.assertTrue(browser.submitted)


if __name__ == "__main__":
    unittest.main()