"""系统指标收集器 — 收集 API 性能、LLM 调用、审核质量等统计."""

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class APIMetrics:
    """API 指标."""
    total_requests: int = 0
    total_errors: int = 0
    total_duration_ms: float = 0.0
    request_times: deque = field(default_factory=lambda: deque(maxlen=1000))

    @property
    def avg_response_time_ms(self) -> float:
        if not self.request_times:
            return 0.0
        return sum(self.request_times) / len(self.request_times)

    @property
    def error_rate(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return self.total_errors / self.total_requests * 100

    @property
    def p95_response_time_ms(self) -> float:
        if not self.request_times:
            return 0.0
        sorted_times = sorted(self.request_times)
        idx = int(len(sorted_times) * 0.95)
        return sorted_times[min(idx, len(sorted_times) - 1)]


@dataclass
class ReviewMetrics:
    """审核指标."""
    total_reviews: int = 0
    rule_only_reviews: int = 0
    llm_reviews: int = 0
    llm_failures: int = 0
    avg_issues_per_review: float = 0.0
    pass_count: int = 0
    revise_count: int = 0
    review_durations: deque = field(default_factory=lambda: deque(maxlen=500))

    def record_review(self, use_llm: bool, issue_count: int, verdict: str, duration_ms: float):
        self.total_reviews += 1
        if use_llm:
            self.llm_reviews += 1
        else:
            self.rule_only_reviews += 1

        self.review_durations.append(duration_ms)

        if verdict == "pass":
            self.pass_count += 1
        elif verdict == "revise":
            self.revise_count += 1

        # 滚动平均问题数
        total_issues = self.avg_issues_per_review * (self.total_reviews - 1) + issue_count
        self.avg_issues_per_review = total_issues / self.total_reviews

    @property
    def avg_review_duration_ms(self) -> float:
        if not self.review_durations:
            return 0.0
        return sum(self.review_durations) / len(self.review_durations)

    @property
    def llm_success_rate(self) -> float:
        if self.llm_reviews == 0:
            return 100.0
        return (self.llm_reviews - self.llm_failures) / self.llm_reviews * 100


class MetricsCollector:
    """系统指标收集器（线程安全单例）."""

    _instance: Optional["MetricsCollector"] = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init()
        return cls._instance

    def _init(self):
        self.api = APIMetrics()
        self.review = ReviewMetrics()
        self._start_time = time.time()
        self._endpoint_stats: Dict[str, Dict[str, Any]] = {}

    def record_request(self, endpoint: str, duration_ms: float, status_code: int, method: str):
        """记录 API 请求."""
        self.api.total_requests += 1
        self.api.total_duration_ms += duration_ms
        self.api.request_times.append(duration_ms)

        if status_code >= 400:
            self.api.total_errors += 1

        # 按端点统计
        key = f"{method} {endpoint}"
        if key not in self._endpoint_stats:
            self._endpoint_stats[key] = {
                "count": 0,
                "errors": 0,
                "total_duration_ms": 0.0,
                "max_duration_ms": 0.0,
            }
        stat = self._endpoint_stats[key]
        stat["count"] += 1
        stat["total_duration_ms"] += duration_ms
        if status_code >= 400:
            stat["errors"] += 1
        stat["max_duration_ms"] = max(stat["max_duration_ms"], duration_ms)

    def record_llm_failure(self):
        """记录 LLM 调用失败."""
        self.review.llm_failures += 1

    def record_review(
        self,
        use_llm: bool,
        issue_count: int,
        verdict: str,
        duration_ms: float,
    ):
        """记录审核完成."""
        self.review.record_review(use_llm, issue_count, verdict, duration_ms)

    def get_summary(self) -> Dict[str, Any]:
        """获取指标摘要."""
        uptime = time.time() - self._start_time

        return {
            "uptime_seconds": round(uptime, 2),
            "api": {
                "total_requests": self.api.total_requests,
                "total_errors": self.api.total_errors,
                "error_rate_percent": round(self.api.error_rate, 2),
                "avg_response_time_ms": round(self.api.avg_response_time_ms, 2),
                "p95_response_time_ms": round(self.api.p95_response_time_ms, 2),
            },
            "review": {
                "total_reviews": self.review.total_reviews,
                "llm_reviews": self.review.llm_reviews,
                "rule_only_reviews": self.review.rule_only_reviews,
                "llm_success_rate_percent": round(self.review.llm_success_rate, 2),
                "avg_issues_per_review": round(self.review.avg_issues_per_review, 2),
                "avg_review_duration_ms": round(self.review.avg_review_duration_ms, 2),
                "pass_count": self.review.pass_count,
                "revise_count": self.review.revise_count,
            },
            "endpoints": {
                k: {
                    "count": v["count"],
                    "errors": v["errors"],
                    "error_rate": round(v["errors"] / v["count"] * 100, 2) if v["count"] > 0 else 0,
                    "avg_duration_ms": round(v["total_duration_ms"] / v["count"], 2) if v["count"] > 0 else 0,
                    "max_duration_ms": round(v["max_duration_ms"], 2),
                }
                for k, v in self._endpoint_stats.items()
            },
        }

    def reset(self):
        """重置所有指标."""
        self._init()