"""
Agent Market 巡检 — 单元测试

测试核心函数：智能体筛选、回复解析、Dify API 映射。
运行: python3 -m pytest tests/ -v
"""

import sys
from pathlib import Path

# 将脚本目录加入路径
SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from utils.parser import parse_reply, is_valid_reply, _should_skip
from utils.request import get_dify_app_id, DIFY_APPID_MAP


class TestShouldSkip:
    """行过滤测试"""

    def test_empty(self):
        assert _should_skip("") is True
        assert _should_skip("   ") is True

    def test_ui_noise(self):
        assert _should_skip("新对话") is True
        assert _should_skip("Copy") is True
        assert _should_skip("Deep Planning") is True
        assert _should_skip("Tools") is True
        assert _should_skip("Invite & Earn") is True

    def test_toolbar_numbers(self):
        assert _should_skip("+2") is True
        assert _should_skip("+0") is True
        assert _should_skip("42") is True

    def test_feishu_ui(self):
        assert _should_skip("新话题") is True
        assert _should_skip("收藏") is True
        assert _should_skip("分享链接") is True
        assert _should_skip("赞") is True
        assert _should_skip("踩") is True
        assert _should_skip("复制") is True
        assert _should_skip("重新生成") is True
        assert _should_skip("停止生成") is True

    def test_valid_content(self):
        assert _should_skip("这是正常的回复内容") is False
        assert _should_skip("Hello, how can I help you?") is False


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

    def test_toolbar_filter(self):
        """工具栏数字 → 过滤"""
        result = parse_reply("", "+2\n+0\n实际内容", "test")
        assert result == "实际内容"

    def test_long_truncation(self):
        """长回复 → 截断"""
        long_text = "A" * 2500
        result = parse_reply("", long_text, "test")
        assert result is not None
        assert len(result) <= 2000
        assert result.endswith("...")

    def test_question_excluded(self):
        """问题文本 → 排除"""
        result = parse_reply("", "test", "test")
        assert result is None

    def test_meta_prefix_stripped(self):
        """元数据前缀 → 去除"""
        result = parse_reply(
            "",
            "智能检索：Based on 2 sources\n\n实际的回答内容在这里\n\n这是回复的正文部分",
            "test"
        )
        # 应该跳过 "智能检索：" 前缀
        assert result is not None
        assert "智能检索" not in result

    def test_feishu_aily_ui_stripped(self):
        """飞书 aily UI 元素 → 过滤"""
        result = parse_reply(
            "",
            "新话题\n收藏\n分享链接\n\n这是回复\n\n复制\n重新生成",
            "test"
        )
        assert result is not None
        assert "新话题" not in result
        assert "复制" not in result
        assert "这是回复" in result


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


class TestDifyMapping:
    """Dify appId 映射测试"""

    def test_agent63(self):
        assert get_dify_app_id(63) == 8

    def test_unknown(self):
        assert get_dify_app_id(999) is None

    def test_map_integrity(self):
        assert 63 in DIFY_APPID_MAP
        assert DIFY_APPID_MAP[63] == 8


if __name__ == "__main__":
    # 简单自检
    print("=== Skip Tests ===")
    ts = TestShouldSkip()
    ts.test_empty()
    ts.test_ui_noise()
    ts.test_toolbar_numbers()
    ts.test_feishu_ui()
    ts.test_valid_content()
    print("  ✅ 全部通过")

    print("=== Reply Parser Tests ===")
    tests = TestParseReply()
    tests.test_empty_diff()
    tests.test_new_content()
    tests.test_noise_filter()
    tests.test_toolbar_filter()
    tests.test_long_truncation()
    tests.test_question_excluded()
    tests.test_meta_prefix_stripped()
    tests.test_feishu_aily_ui_stripped()
    print("  ✅ 全部通过")

    print("=== Validity Tests ===")
    vt = TestIsValidReply()
    vt.test_none()
    vt.test_blank()
    vt.test_short()
    vt.test_thinking()
    vt.test_normal()
    print("  ✅ 全部通过")

    print("=== Dify Mapping Tests ===")
    dm = TestDifyMapping()
    dm.test_agent63()
    dm.test_unknown()
    dm.test_map_integrity()
    print("  ✅ 全部通过")

    print("\n🎉 所有测试通过")
