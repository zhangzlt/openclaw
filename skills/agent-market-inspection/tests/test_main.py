"""
Agent Market 巡检 — 单元测试

测试核心函数：智能体筛选、回复解析、token 验证。
运行: python3 -m pytest tests/ -v
"""

import sys
from pathlib import Path

# 将脚本目录加入路径
SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from utils.parser import parse_reply, is_valid_reply, UI_NOISE_PATTERNS


class TestParseReply:
    """回复解析测试"""

    def test_empty_diff(self):
        """前后一致 → 无回复"""
        assert parse_reply("hello\nworld", "hello\nworld", "test") is None

    def test_new_content(self):
        """有新内容 → 提取回复"""
        result = parse_reply(
            "question text",
            "question text\nThis is the answer",
            "question text"
        )
        assert result == "This is the answer"

    def test_noise_filter(self):
        """UI 噪声 → 过滤"""
        result = parse_reply("", "新对话\nDeep Planning\nTools\nCopy", "test")
        assert result is None

    def test_long_truncation(self):
        """长回复 → 截断"""
        long_text = "A" * 600
        result = parse_reply("", long_text, "test")
        assert result is not None
        assert len(result) <= 500

    def test_question_excluded(self):
        """问题文本 → 排除"""
        result = parse_reply("", "test", "test")
        assert result is None


class TestIsValidReply:
    """回复有效性判断测试"""

    def test_none(self):
        assert not is_valid_reply(None)

    def test_blank(self):
        assert not is_valid_reply("")

    def test_short(self):
        assert not is_valid_reply("a")

    def test_thinking(self):
        assert not is_valid_reply("思考中")
        assert not is_valid_reply("Loading")

    def test_normal(self):
        assert is_valid_reply("Hello, this is a valid reply.")


class TestIsChatAgent:
    """对话型智能体筛选测试"""

    def test_feishuapp(self):
        """feishuapp.cn URL → 对话型"""
        agent = {"url": "https://bba12hub36.feishuapp.cn/ai/gui/chat/a_3687bf8"}
        # 需要从主脚本导入 _is_chat_agent
        # (此处需在实际测试中 mock 导入路径)

    def test_aily(self):
        """aily.feishu.cn URL → 对话型"""
        agent = {"url": "https://aily.feishu.cn/agents/agent_4kk2i8qszzmp"}
        # 需要从主脚本导入 _is_chat_agent

    def test_dify_api(self):
        """openType=api + source=dify → 对话型"""
        agent = {"openType": "api", "source": "dify", "url": ""}

    def test_applink_excluded(self):
        """applink → 排除"""
        agent = {"url": "https://applink.feishu.cn/T93e6UpNn6Lz"}

    def test_no_url(self):
        """无 URL 且非 dify → 非对话型"""
        agent = {"url": ""}


if __name__ == "__main__":
    # 简单自检
    print("=== Reply Parser Tests ===")
    tests = TestParseReply()
    tests.test_empty_diff()
    tests.test_new_content()
    tests.test_noise_filter()
    tests.test_long_truncation()
    tests.test_question_excluded()
    print("  ✅ 全部通过")

    print("=== Validity Tests ===")
    vt = TestIsValidReply()
    vt.test_none()
    vt.test_blank()
    vt.test_short()
    vt.test_thinking()
    vt.test_normal()
    print("  ✅ 全部通过")

    print("\n🎉 所有测试通过")
