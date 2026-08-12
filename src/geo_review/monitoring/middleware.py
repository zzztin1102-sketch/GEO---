"""FastAPI 监控中间件 — 自动记录请求性能和错误."""

import time
from typing import Optional

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from geo_review.monitoring.metrics import MetricsCollector


class MetricsMiddleware(BaseHTTPMiddleware):
    """API 监控中间件.

    自动记录：
        - 每个请求的响应时间
        - 状态码分布
        - 按端点的统计
        - 错误率
    """

    def __init__(self, app, exclude_paths: Optional[list] = None):
        super().__init__(app)
        self.exclude_paths = set(exclude_paths or ["/api/v1/health", "/static/", "/docs", "/redoc", "/openapi.json"])
        self.metrics = MetricsCollector()

    async def dispatch(self, request: Request, call_next):
        # 跳过排除的路径
        path = request.url.path
        if any(path.startswith(ep) or path == ep for ep in self.exclude_paths):
            return await call_next(request)

        start = time.perf_counter()
        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception:
            status_code = 500
            raise
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            self.metrics.record_request(
                endpoint=path,
                duration_ms=duration_ms,
                status_code=status_code,
                method=request.method,
            )

        return response