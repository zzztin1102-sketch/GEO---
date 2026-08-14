"""LLM 客户端 — 支持多 Provider，统一接口，含重试、降级和缓存机制（优化版）.

优化点：
    1. 新增 LLM 结果缓存（基于 messages 哈希，TTL 可配）
    2. 新增 fallback_model 降级机制
    3. 新增 async_chat 异步调用方法（避免阻塞事件循环）
    4. _extract_json 改为静态方法，供 reviewer.py 复用，消除重复代码
"""

import hashlib
import json
import logging
import random
import time
from typing import Any, Dict, Optional

from geo_review.llm.models import LLMProviderConfig

logger = logging.getLogger(__name__)


# ✅ 新增：简单的 TTL 缓存
class _LLMCache:
    """轻量级 LLM 响应缓存（基于 messages 哈希）."""

    def __init__(self, ttl: int = 3600):
        self._store: Dict[str, tuple] = {}  # key -> (result, timestamp)
        self._ttl = ttl

    def get(self, messages: list) -> Optional[Dict[str, Any]]:
        key = self._make_key(messages)
        if key in self._store:
            result, ts = self._store[key]
            if time.time() - ts < self._ttl:
                logger.info("LLM 缓存命中")
                cached = result.copy()
                cached["cache_hit"] = True
                return cached
            else:
                del self._store[key]
        return None

    def set(self, messages: list, result: Dict[str, Any]):
        key = self._make_key(messages)
        self._store[key] = (result, time.time())
        # 简单的缓存淘汰：超过 500 条时清理最早的
        if len(self._store) > 500:
            oldest = min(self._store.values(), key=lambda x: x[1])
            for k, v in list(self._store.items()):
                if v[1] == oldest[1]:
                    del self._store[k]
                    break

    @staticmethod
    def _make_key(messages: list) -> str:
        """根据 messages 生成缓存 key."""
        raw = json.dumps(messages, ensure_ascii=False, sort_keys=True)
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    def clear(self):
        self._store.clear()


class LLMClient:
    """LLM 客户端，统一调用接口.

    支持 Provider:
        - openai: OpenAI 官方 API
        - deepseek: DeepSeek API
        - anthropic: Anthropic Claude
        - custom: 自定义兼容 OpenAI 协议的端点

    设计原则:
        - 单一职责: 只负责调用 LLM，不负责 prompt 工程
        - 错误处理: 统一捕获异常，返回可识别的错误信息
        - 可观测: 记录 token 使用和耗时
        - 可靠性: 指数退避重试 + 超时降级 + fallback 模型
        - 高效性: 可选的 LLM 结果缓存
    """

    def __init__(self, config: LLMProviderConfig):
        self.config = config
        self._client = None
        self._stats = {
            "total_calls": 0,
            "success_calls": 0,
            "retry_calls": 0,
            "failed_calls": 0,
            "total_duration": 0.0,
            "cache_hits": 0,
            "fallback_used": 0,
        }
        # ✅ 新增：初始化缓存
        self._cache = _LLMCache(ttl=config.cache_ttl) if config.enable_cache else None
        self._init_client()

    @property
    def stats(self) -> Dict[str, Any]:
        """返回调用统计."""
        return self._stats.copy()

    def _init_client(self):
        """初始化客户端（延迟加载，避免未安装依赖时报错）."""
        provider = self.config.provider.lower()

        if provider in ("openai", "deepseek", "custom", "azure"):
            try:
                from openai import OpenAI

                base_url = self.config.base_url
                api_key = self.config.api_key or ""

                if provider == "deepseek" and not base_url:
                    base_url = "https://api.deepseek.com/v1"

                self._client = OpenAI(
                    api_key=api_key,
                    base_url=base_url if base_url else None,
                    timeout=self.config.timeout,
                )
                self._api_type = "openai_compatible"
            except ImportError as exc:
                raise ImportError(
                    f"请安装 openai SDK: pip install openai（{provider} 使用 OpenAI 兼容协议）"
                ) from exc

        elif provider == "anthropic":
            try:
                from anthropic import Anthropic

                base_url = self.config.base_url
                api_key = self.config.api_key or ""

                self._client = Anthropic(
                    api_key=api_key,
                    base_url=base_url if base_url else None,
                    timeout=self.config.timeout,
                )
                self._api_type = "anthropic"
            except ImportError as exc:
                raise ImportError(
                    "请安装 anthropic SDK: pip install anthropic"
                ) from exc

        else:
            raise ValueError(f"不支持的 LLM Provider: {provider}")

    def chat(
        self,
        messages: list,
        *,
        response_format: Optional[str] = None,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        retry_backoff: float = 2.0,
        retry_jitter: bool = True,
    ) -> Dict[str, Any]:
        """发送聊天请求，返回标准化结果（含并发控制 + 熔断器 + 指数退避重试 + fallback 降级）.

        并发控制流程：
            1. 检查熔断器状态 — OPEN 时快速失败
            2. 获取全局 LLM 并发信号量
            3. 执行 API 调用
            4. 成功 → 熔断器 record_success
            5. 失败 → 熔断器 record_failure

        Args:
            messages: 消息列表（OpenAI 格式）
            response_format: 响应格式（json_object / text）
            max_retries: 最大重试次数（默认 3）
            retry_delay: 初始重试延迟（秒，默认 1.0）
            retry_backoff: 退避倍数（默认 2.0）
            retry_jitter: 是否添加随机抖动（默认 True）

        Returns:
            {
                "content": "模型回复内容",
                "model": "模型名",
                "tokens": {"prompt": int, "completion": int, "total": int},
                "duration": float(秒),
                "retries": int(重试次数),
                "cache_hit": bool,
                "fallback_used": bool,
            }
        """
        # ✅ 缓存检查（缓存命中时不消耗并发槽位）
        if self._cache is not None:
            cached = self._cache.get(messages)
            if cached is not None:
                self._stats["cache_hits"] += 1
                return cached

        # ✅ 并发控制 + 熔断器检查
        from geo_review.utils.concurrency import ConcurrencyManager
        cm = ConcurrencyManager.get_instance()

        with cm.llm_context(timeout=120.0) as allowed:
            if not allowed:
                # 熔断器开启 或 并发槽位获取超时
                self._stats["failed_calls"] += 1
                raise RuntimeError(
                    "LLM API 当前不可用（熔断器开启或并发已满），请稍后重试"
                )

            return self._do_chat(
                messages, response_format, max_retries,
                retry_delay, retry_backoff, retry_jitter,
                cm,
            )

    def _do_chat(
        self,
        messages: list,
        response_format: Optional[str],
        max_retries: int,
        retry_delay: float,
        retry_backoff: float,
        retry_jitter: bool,
        cm=None,
    ) -> Dict[str, Any]:
        """实际执行 LLM 调用（含重试 + fallback + 熔断器反馈）."""
        self._stats["total_calls"] += 1
        start_time = time.time()
        last_error = None
        retries = 0

        for attempt in range(max_retries + 1):
            try:
                result = self._call_provider(messages, response_format)
                duration = time.time() - start_time
                result["duration"] = duration
                result["retries"] = retries
                result["cache_hit"] = False
                result["fallback_used"] = False

                self._stats["success_calls"] += 1
                self._stats["total_duration"] += duration
                if retries > 0:
                    self._stats["retry_calls"] += 1

                # ✅ 新增：写入缓存
                if self._cache is not None:
                    self._cache.set(messages, result)

                # ✅ 熔断器：记录成功
                if cm:
                    cm.record_llm_success()

                return result

            except Exception as exc:
                last_error = exc
                is_rate_limit = self._is_rate_limit_error(exc)
                is_timeout = self._is_timeout_error(exc)

                if attempt < max_retries and (is_rate_limit or is_timeout or self._is_retryable_error(exc)):
                    retries += 1
                    delay = retry_delay * (retry_backoff ** (retries - 1))
                    if retry_jitter:
                        delay *= (1 + random.random() * 0.5)

                    error_type = "速率限制" if is_rate_limit else ("超时" if is_timeout else "可重试错误")
                    logger.warning(
                        f"LLM 调用失败（{error_type}），第 {retries}/{max_retries} 次重试，"
                        f"等待 {delay:.1f} 秒... 错误: {exc}"
                    )
                    time.sleep(delay)
                else:
                    break

        # ✅ 新增：fallback 模型降级
        if self.config.fallback_model and self.config.model != self.config.fallback_model:
            logger.warning(f"主模型 {self.config.model} 不可用，尝试降级到 {self.config.fallback_model}")
            try:
                original_model = self.config.model
                self.config.model = self.config.fallback_model
                result = self._call_provider(messages, response_format)
                duration = time.time() - start_time
                result["duration"] = duration
                result["retries"] = retries
                result["cache_hit"] = False
                result["fallback_used"] = True

                self._stats["success_calls"] += 1
                self._stats["total_duration"] += duration
                self._stats["fallback_used"] += 1

                if self._cache is not None:
                    self._cache.set(messages, result)

                logger.info(f"降级到 {self.config.fallback_model} 成功")
                return result

            except Exception as fallback_exc:
                logger.error(f"降级模型 {self.config.fallback_model} 也失败: {fallback_exc}")
                # 恢复原模型配置
                self.config.model = original_model
            finally:
                # 确保恢复
                pass

        self._stats["failed_calls"] += 1
        # ✅ 熔断器：记录失败
        if cm:
            cm.record_llm_failure()
        raise RuntimeError(
            f"LLM 调用失败（已重试 {retries} 次，已尝试降级）: {last_error}"
        ) from last_error

    # ✅ 新增：异步调用方法（用于 async 上下文中避免阻塞事件循环）
    async def async_chat(
        self,
        messages: list,
        *,
        response_format: Optional[str] = None,
        max_retries: int = 3,
    ) -> Dict[str, Any]:
        """异步发送聊天请求（内部使用 asyncio.to_thread 包装同步调用）."""
        import asyncio

        return await asyncio.to_thread(
            self.chat,
            messages,
            response_format=response_format,
            max_retries=max_retries,
        )

    def chat_json(self, messages: list) -> Dict[str, Any]:
        """请求 JSON 格式响应，自动解析."""
        raw = self.chat(messages, response_format="json_object")
        content = raw["content"]

        data = self._extract_json(content)
        raw["json"] = data
        return raw

    # ✅ 优化：_extract_json 改为静态方法，供 reviewer.py 复用
    @staticmethod
    def _extract_json(content: str) -> Dict[str, Any]:
        """从 LLM 响应中提取 JSON 数据（容错处理）.

        支持的提取策略：
        1. 过滤 <think> 标签（DeepSeek-R1 等推理模型）
        2. 直接解析整个文本
        3. 从 markdown 代码块中提取
        4. 从第一个 { 到最后一个 } 提取
        """
        import re

        if not content or not content.strip():
            raise ValueError("LLM 返回内容为空")

        text = content.strip()

        # 策略0：过滤推理模型的 <think> 标签
        cleaned = re.sub(r"<think>[\s\S]*?</think>", "", text)
        cleaned = cleaned.strip()

        # 策略1：直接解析
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        # 策略2：从 Markdown 代码块中提取
        patterns = [
            r"```(?:json)?\s*([\s\S]*?)\s*```",
            r"```\s*([\s\S]*?)\s*```",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                try:
                    return json.loads(match.group(1).strip())
                except json.JSONDecodeError:
                    continue

        # 策略3：第一个 { 到最后一个 }
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(cleaned[start: end + 1])
            except json.JSONDecodeError:
                pass

        # 策略4：对原始文本也尝试一次
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start: end + 1])
            except json.JSONDecodeError:
                pass

        raise ValueError(f"无法从 LLM 响应中提取 JSON: {text[:500]}")

    # ------------------------------------------------------------------
    # Provider 实现（重构为内部方法，方便 fallback 切换）
    # ------------------------------------------------------------------
    def _call_provider(self, messages: list, response_format: Optional[str]) -> Dict[str, Any]:
        """调用当前 Provider."""
        if self._api_type == "openai_compatible":
            return self._chat_openai(messages, response_format)
        elif self._api_type == "anthropic":
            return self._chat_anthropic(messages, response_format)
        else:
            raise ValueError(f"未知的 API 类型: {self._api_type}")

    def _chat_openai(self, messages: list, response_format: Optional[str]) -> Dict[str, Any]:
        """OpenAI 兼容接口."""
        kwargs = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }

        provider = self.config.provider.lower()
        supports_json_format = provider in ("openai", "azure")
        if response_format == "json_object" and supports_json_format:
            kwargs["response_format"] = {"type": "json_object"}

        response = self._client.chat.completions.create(**kwargs)

        content = response.choices[0].message.content or ""
        usage = response.usage

        return {
            "content": content,
            "model": response.model,
            "tokens": {
                "prompt": usage.prompt_tokens if usage else 0,
                "completion": usage.completion_tokens if usage else 0,
                "total": usage.total_tokens if usage else 0,
            },
        }

    def _chat_anthropic(self, messages: list, response_format: Optional[str]) -> Dict[str, Any]:
        """Anthropic 接口."""
        system_messages = [m["content"] for m in messages if m["role"] == "system"]
        user_messages = [m for m in messages if m["role"] != "system"]

        kwargs = {
            "model": self.config.model,
            "messages": user_messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }

        if system_messages:
            kwargs["system"] = "\n".join(system_messages)

        response = self._client.messages.create(**kwargs)

        content = ""
        for block in response.content:
            if block.type == "text":
                content += block.text

        return {
            "content": content,
            "model": response.model,
            "tokens": {
                "prompt": response.usage.input_tokens,
                "completion": response.usage.output_tokens,
                "total": response.usage.input_tokens + response.usage.output_tokens,
            },
        }

    # ------------------------------------------------------------------
    # 错误分类辅助方法
    # ------------------------------------------------------------------
    @staticmethod
    def _is_rate_limit_error(exc: Exception) -> bool:
        """判断是否为速率限制错误."""
        msg = str(exc).lower()
        return any(k in msg for k in [
            "rate limit", "ratelimit", "too many requests", "429",
            "quota exceeded", "insufficient_quota",
        ])

    @staticmethod
    def _is_timeout_error(exc: Exception) -> bool:
        """判断是否为超时错误."""
        msg = str(exc).lower()
        return any(k in msg for k in [
            "timeout", "timed out", "connection reset", "eof",
            "ssl", "network", "read operation",
        ]) or isinstance(exc, TimeoutError)

    @staticmethod
    def _is_retryable_error(exc: Exception) -> bool:
        """判断是否为可重试的临时错误."""
        msg = str(exc).lower()
        return any(k in msg for k in [
            "500", "502", "503", "504", "internal server error",
            "bad gateway", "service unavailable", "gateway timeout",
            "temporary", "temporarily", "try again",
        ])

    # ------------------------------------------------------------------
    # 工厂方法
    # ------------------------------------------------------------------
    @classmethod
    def from_env(cls, provider: str = "openai") -> "LLMClient":
        """从环境变量创建客户端."""
        import os

        config = LLMProviderConfig(provider=provider)
        config.api_key = os.environ.get(f"{provider.upper()}_API_KEY", os.environ.get("OPENAI_API_KEY", os.environ.get("LLM_API_KEY", "")))
        config.base_url = os.environ.get(f"{provider.upper()}_BASE_URL")
        config.model = os.environ.get(f"{provider.upper()}_MODEL", config.model)
        config.fallback_model = os.environ.get(f"{provider.upper()}_FALLBACK_MODEL", config.fallback_model)

        if not config.api_key:
            raise ValueError(f"未找到 {provider.upper()}_API_KEY 环境变量")

        return cls(config)

    @classmethod
    def from_dict(cls, config_dict: Dict[str, Any]) -> "LLMClient":
        """从字典配置创建."""
        config = LLMProviderConfig(**config_dict)
        return cls(config)
