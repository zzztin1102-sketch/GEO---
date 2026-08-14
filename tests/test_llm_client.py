"""LLM 客户端测试 — JSON 提取逻辑的各种容错路径."""

import pytest
import json

from geo_review.llm.client import LLMClient


class TestExtractJson:
    """_extract_json 静态方法测试."""

    def test_direct_json(self):
        """直接 JSON 字符串."""
        content = '{"summary": "通过", "issues": []}'
        result = LLMClient._extract_json(content)
        assert result["summary"] == "通过"
        assert result["issues"] == []

    def test_json_with_whitespace(self):
        """带前后空白的 JSON."""
        content = '  \n  {"summary": "通过", "issues": []}  \n  '
        result = LLMClient._extract_json(content)
        assert result["summary"] == "通过"

    def test_json_in_markdown_block(self):
        """Markdown 代码块中的 JSON."""
        content = '```json\n{"summary": "通过", "issues": []}\n```'
        result = LLMClient._extract_json(content)
        assert result["summary"] == "通过"

    def test_json_in_plain_code_block(self):
        """无语言标记的代码块中的 JSON."""
        content = '```\n{"summary": "通过", "issues": []}\n```'
        result = LLMClient._extract_json(content)
        assert result["summary"] == "通过"

    def test_json_with_think_tags(self):
        """过滤 <think> 标签（DeepSeek-R1 推理模型）."""
        content = '<think>让我分析一下这个内容...</think>\n{"summary": "通过", "issues": []}'
        result = LLMClient._extract_json(content)
        assert result["summary"] == "通过"
        assert result["issues"] == []

    def test_json_embedded_in_text(self):
        """JSON 嵌在文本中."""
        content = '这是审核结果：\n{"summary": "发现问题", "issues": [{"type": "exaggeration"}]}\n以上是结果。'
        result = LLMClient._extract_json(content)
        assert result["summary"] == "发现问题"
        assert len(result["issues"]) == 1

    def test_json_with_nested_objects(self):
        """带嵌套对象的 JSON."""
        content = '{"summary": "测试", "issues": [{"type": "exaggeration", "evidence": {"snippet": "测试"}}]}'
        result = LLMClient._extract_json(content)
        assert result["issues"][0]["evidence"]["snippet"] == "测试"

    def test_empty_content_raises(self):
        """空内容应抛出 ValueError."""
        with pytest.raises(ValueError, match="为空"):
            LLMClient._extract_json("")

    def test_whitespace_only_raises(self):
        """纯空白内容应抛出 ValueError."""
        with pytest.raises(ValueError, match="为空"):
            LLMClient._extract_json("   \n  \t  ")

    def test_no_json_raises(self):
        """无 JSON 内容应抛出 ValueError."""
        with pytest.raises(ValueError, match="无法从 LLM 响应中提取 JSON"):
            LLMClient._extract_json("这不是JSON格式的文本")

    def test_json_with_chinese(self):
        """含中文的 JSON."""
        content = '{"summary": "审核通过，未发现违规问题", "issues": []}'
        result = LLMClient._extract_json(content)
        assert result["summary"] == "审核通过，未发现违规问题"

    def test_json_with_think_and_markdown(self):
        """同时有 <think> 标签和 Markdown 代码块."""
        content = '<think>分析中...</think>\n```json\n{"summary": "通过", "issues": []}\n```'
        result = LLMClient._extract_json(content)
        assert result["summary"] == "通过"

    def test_json_array_at_end(self):
        """JSON 在文本末尾."""
        content = '审核完成。结果如下：\n{"summary": "完成", "issues": []}'
        result = LLMClient._extract_json(content)
        assert result["summary"] == "完成"


class TestLLMCache:
    """LLM 缓存测试."""

    def test_cache_set_and_get(self):
        """缓存写入和读取."""
        from geo_review.llm.client import _LLMCache

        cache = _LLMCache(ttl=3600)
        messages = [{"role": "user", "content": "测试"}]
        result = {"content": "测试回复", "model": "test"}

        cache.set(messages, result)
        cached = cache.get(messages)

        assert cached is not None
        assert cached["content"] == "测试回复"
        assert cached.get("cache_hit") is True

    def test_cache_miss(self):
        """缓存未命中."""
        from geo_review.llm.client import _LLMCache

        cache = _LLMCache(ttl=3600)
        messages = [{"role": "user", "content": "测试"}]
        assert cache.get(messages) is None

    def test_cache_expired(self):
        """缓存过期."""
        from geo_review.llm.client import _LLMCache

        cache = _LLMCache(ttl=0)  # TTL=0 立即过期
        messages = [{"role": "user", "content": "测试"}]
        cache.set(messages, {"content": "测试"})

        # TTL=0，下次获取应过期
        import time
        time.sleep(0.01)
        assert cache.get(messages) is None

    def test_cache_different_messages(self):
        """不同 messages 的缓存隔离."""
        from geo_review.llm.client import _LLMCache

        cache = _LLMCache(ttl=3600)
        msg1 = [{"role": "user", "content": "问题1"}]
        msg2 = [{"role": "user", "content": "问题2"}]

        cache.set(msg1, {"content": "回答1"})
        assert cache.get(msg1) is not None
        assert cache.get(msg2) is None

    def test_cache_clear(self):
        """清空缓存."""
        from geo_review.llm.client import _LLMCache

        cache = _LLMCache(ttl=3600)
        messages = [{"role": "user", "content": "测试"}]
        cache.set(messages, {"content": "测试"})
        cache.clear()
        assert cache.get(messages) is None
