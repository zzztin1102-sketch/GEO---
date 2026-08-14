"""并发控制管理器 — 全局信号量 + 熔断器.

解决高并发场景下的核心瓶颈：
    1. LLM API 并发控制 — 防止超出 API 速率限制（QPM/QPS）
    2. 爬虫并发控制 — 防止 Playwright 浏览器实例耗尽内存
    3. 审核任务级并发控制 — 防止线程爆炸
    4. LLM API 熔断器 — 连续失败时快速降级，避免级联故障

架构说明：
    - 使用 threading.Semaphore（非 asyncio），因为审核核心逻辑在线程中运行
    - 全局单例模式，所有模块共享同一组信号量
    - 熔断器使用三态机：CLOSED（正常）→ OPEN（熔断）→ HALF_OPEN（试探）
"""

import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """熔断器状态."""
    CLOSED = "closed"        # 正常运行，请求通过
    OPEN = "open"            # 熔断中，请求被快速拒绝
    HALF_OPEN = "half_open"  # 半开状态，允许一个试探请求


@dataclass
class CircuitBreakerConfig:
    """熔断器配置."""
    failure_threshold: int = 5        # 连续失败次数阈值，超过后熔断
    cooldown_seconds: float = 60.0    # 熔断后冷却时间（秒）
    half_open_max_calls: int = 1      # 半开状态下允许的试探请求数


class CircuitBreaker:
    """LLM API 熔断器.

    状态机：
        CLOSED → 连续失败达阈值 → OPEN
        OPEN → 冷却时间到 → HALF_OPEN
        HALF_OPEN → 成功 → CLOSED
        HALF_OPEN → 失败 → OPEN（重置冷却时间）
    """

    def __init__(self, config: CircuitBreakerConfig):
        self._config = config
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._opened_at: Optional[float] = None
        self._half_open_calls = 0
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            if self._state == CircuitState.OPEN:
                # 检查是否应该转为半开
                if self._opened_at and (time.time() - self._opened_at) >= self._config.cooldown_seconds:
                    self._state = CircuitState.HALF_OPEN
                    self._half_open_calls = 0
                    logger.info("熔断器: OPEN → HALF_OPEN（冷却结束，允许试探请求）")
            return self._state

    def can_proceed(self) -> bool:
        """是否允许请求通过."""
        with self._lock:
            if self._state == CircuitState.CLOSED:
                return True
            elif self._state == CircuitState.OPEN:
                # 检查冷却时间
                if self._opened_at and (time.time() - self._opened_at) >= self._config.cooldown_seconds:
                    self._state = CircuitState.HALF_OPEN
                    self._half_open_calls = 0
                    logger.info("熔断器: OPEN → HALF_OPEN（冷却结束）")
                    return True
                return False
            else:  # HALF_OPEN
                if self._half_open_calls < self._config.half_open_max_calls:
                    self._half_open_calls += 1
                    return True
                return False

    def record_success(self):
        """记录一次成功调用."""
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                self._state = CircuitState.CLOSED
                self._failure_count = 0
                self._opened_at = None
                logger.info("熔断器: HALF_OPEN → CLOSED（试探请求成功，恢复正常）")
            elif self._state == CircuitState.CLOSED:
                self._failure_count = 0  # 重置连续失败计数

    def record_failure(self):
        """记录一次失败调用."""
        with self._lock:
            self._failure_count += 1
            if self._state == CircuitState.HALF_OPEN:
                # 半开状态失败 → 重新熔断
                self._state = CircuitState.OPEN
                self._opened_at = time.time()
                logger.warning("熔断器: HALF_OPEN → OPEN（试探请求失败，重新熔断）")
            elif self._state == CircuitState.CLOSED:
                if self._failure_count >= self._config.failure_threshold:
                    self._state = CircuitState.OPEN
                    self._opened_at = time.time()
                    logger.warning(
                        f"熔断器: CLOSED → OPEN（连续失败 {self._failure_count} 次，触发熔断）"
                    )

    def reset(self):
        """重置熔断器（手动恢复）."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._success_count = 0
            self._opened_at = None
            self._half_open_calls = 0

    @property
    def stats(self) -> dict:
        """返回熔断器状态信息."""
        with self._lock:
            return {
                "state": self._state.value,
                "failure_count": self._failure_count,
                "success_count": self._success_count,
                "opened_at": self._opened_at,
            }


@dataclass
class ConcurrencyConfig:
    """并发控制配置."""
    max_concurrent_reviews: int = 5       # 全局最大并发审核数
    max_concurrent_llm_calls: int = 5     # 全局最大并发 LLM API 调用数
    max_concurrent_crawls: int = 2        # 全局最大并发爬虫数（Playwright 很重）
    # 熔断器
    circuit_breaker_enabled: bool = True
    circuit_breaker_threshold: int = 5
    circuit_breaker_cooldown: float = 60.0


class ConcurrencyManager:
    """全局并发管理器 — 单例模式.

    管理三类资源并发：
        1. 审核任务级：限制同时运行的 review() 数量
        2. LLM API 级：限制同时发起的 LLM API 调用数
        3. 爬虫级：限制同时运行的 Playwright 浏览器实例数

    用法::

        manager = ConcurrencyManager.get_instance()
        manager.configure(ConcurrencyConfig(max_concurrent_llm_calls=3))

        # 在 LLM 调用前
        with manager.llm_context() as allowed:
            if not allowed:
                raise RuntimeError("LLM API 熔断中，请稍后重试")
            result = llm_client.chat(messages)

        # 在爬虫前
        with manager.crawl_context() as allowed:
            if not allowed:
                raise RuntimeError("爬虫并发已满")
            result = crawler.crawl(url)
    """

    _instance: Optional["ConcurrencyManager"] = None
    _instance_lock = threading.Lock()

    def __init__(self, config: Optional[ConcurrencyConfig] = None):
        self._config = config or ConcurrencyConfig()
        self._review_semaphore = threading.Semaphore(self._config.max_concurrent_reviews)
        self._llm_semaphore = threading.Semaphore(self._config.max_concurrent_llm_calls)
        self._crawl_semaphore = threading.Semaphore(self._config.max_concurrent_crawls)
        self._circuit_breaker = CircuitBreaker(CircuitBreakerConfig(
            failure_threshold=self._config.circuit_breaker_threshold,
            cooldown_seconds=self._config.circuit_breaker_cooldown,
        )) if self._config.circuit_breaker_enabled else None
        self._configured = False

    @classmethod
    def get_instance(cls) -> "ConcurrencyManager":
        """获取全局单例（线程安全）."""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def configure(cls, config: ConcurrencyConfig):
        """配置全局并发管理器（仅在启动时调用一次）.

        重新创建信号量和熔断器。如果已有实例，会替换其内部状态。
        """
        instance = cls.get_instance()
        instance._config = config
        instance._review_semaphore = threading.Semaphore(config.max_concurrent_reviews)
        instance._llm_semaphore = threading.Semaphore(config.max_concurrent_llm_calls)
        instance._crawl_semaphore = threading.Semaphore(config.max_concurrent_crawls)
        instance._circuit_breaker = CircuitBreaker(CircuitBreakerConfig(
            failure_threshold=config.circuit_breaker_threshold,
            cooldown_seconds=config.circuit_breaker_cooldown,
        )) if config.circuit_breaker_enabled else None
        instance._configured = True
        logger.info(
            f"并发管理器已配置: reviews={config.max_concurrent_reviews}, "
            f"llm={config.max_concurrent_llm_calls}, crawls={config.max_concurrent_crawls}, "
            f"circuit_breaker={'on' if config.circuit_breaker_enabled else 'off'}"
        )

    @property
    def config(self) -> ConcurrencyConfig:
        return self._config

    @property
    def circuit_breaker(self) -> Optional[CircuitBreaker]:
        return self._circuit_breaker

    # ------------------------------------------------------------------
    # 审核任务级并发控制
    # ------------------------------------------------------------------

    @contextmanager
    def review_context(self, timeout: float = 30.0):
        """获取审核任务并发槽位.

        超时未获取到槽位时，allowed=False（调用方可选择排队等待或拒绝请求）。
        """
        acquired = self._review_semaphore.acquire(timeout=timeout)
        try:
            yield acquired
        finally:
            if acquired:
                self._review_semaphore.release()

    # ------------------------------------------------------------------
    # LLM API 级并发控制 + 熔断器
    # ------------------------------------------------------------------

    @contextmanager
    def llm_context(self, timeout: float = 60.0):
        """获取 LLM API 调用并发槽位 + 检查熔断器.

        流程：
            1. 检查熔断器状态 — OPEN 时直接拒绝（快速失败）
            2. 获取 LLM 并发信号量
            3. 执行 LLM 调用
            4. 成功 → 熔断器 record_success
            5. 失败 → 熔断器 record_failure
        """
        # 1. 检查熔断器
        if self._circuit_breaker and not self._circuit_breaker.can_proceed():
            yield False
            return

        # 2. 获取并发槽位
        acquired = self._llm_semaphore.acquire(timeout=timeout)
        try:
            yield acquired
        finally:
            if acquired:
                self._llm_semaphore.release()

    def record_llm_success(self):
        """记录 LLM 调用成功（供熔断器使用）."""
        if self._circuit_breaker:
            self._circuit_breaker.record_success()

    def record_llm_failure(self):
        """记录 LLM 调用失败（供熔断器使用）."""
        if self._circuit_breaker:
            self._circuit_breaker.record_failure()

    # ------------------------------------------------------------------
    # 爬虫级并发控制
    # ------------------------------------------------------------------

    @contextmanager
    def crawl_context(self, timeout: float = 30.0):
        """获取爬虫并发槽位.

        Playwright 浏览器实例很重（每个约 100-200MB），必须严格限制并发数。
        """
        acquired = self._crawl_semaphore.acquire(timeout=timeout)
        try:
            yield acquired
        finally:
            if acquired:
                self._crawl_semaphore.release()

    # ------------------------------------------------------------------
    # 状态查询
    # ------------------------------------------------------------------

    @property
    def stats(self) -> dict:
        """返回并发管理器状态信息（用于监控/健康检查）."""
        return {
            "config": {
                "max_concurrent_reviews": self._config.max_concurrent_reviews,
                "max_concurrent_llm_calls": self._config.max_concurrent_llm_calls,
                "max_concurrent_crawls": self._config.max_concurrent_crawls,
                "circuit_breaker_enabled": self._config.circuit_breaker_enabled,
            },
            "circuit_breaker": self._circuit_breaker.stats if self._circuit_breaker else None,
        }
