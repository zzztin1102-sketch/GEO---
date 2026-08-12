"""API 限流模块 — 基于 slowapi 的请求限流.

按接口类型分级限流：
    - 审核接口（消耗 LLM token）：10/minute
    - 批量审核接口：3/minute
    - 认证接口（防暴力破解）：20/minute
    - 默认：60/minute

使用方式：
    # app.py 中初始化
    from geo_review.middleware.rate_limit import limiter, configure_limiter
    configure_limiter(config.rate_limit)
    app.state.limiter = limiter

    # 路由中使用
    from geo_review.middleware.rate_limit import limiter
    @router.post("/api/v1/review")
    @limiter.limit("10/minute")
    async def review_content(request: Request, ...):
"""

import logging
from typing import Optional

from slowapi import Limiter
from slowapi.util import get_remote_address

logger = logging.getLogger(__name__)

# 模块级 limiter 实例，路由文件直接导入使用
# storage_uri="memory://" 使用内存存储（单进程足够，多进程需换 Redis）
# swallow_errors=True 防止限流内部异常导致 500（错误会被记录日志但请求正常通过）
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["60/minute"],
    storage_uri="memory://",
    swallow_errors=True,
)

# 限流规则常量，路由文件可引用
LIMIT_REVIEW = "10/minute"
LIMIT_BATCH = "3/minute"
LIMIT_AUTH = "20/minute"


def configure_limiter(rate_limit_config) -> None:
    """根据配置调整 limiter 参数.

    Args:
        rate_limit_config: RateLimitConfig 实例
    """
    global LIMIT_REVIEW, LIMIT_BATCH, LIMIT_AUTH

    if not rate_limit_config.enabled:
        # 禁用限流：直接禁用 limiter
        limiter.enabled = False
        LIMIT_REVIEW = "999999/minute"
        LIMIT_BATCH = "999999/minute"
        LIMIT_AUTH = "999999/minute"
        logger.info("API 限流已禁用")
        return

    # 更新路由级别限流常量（不影响已注册的 default_limits）
    LIMIT_REVIEW = rate_limit_config.review_limit
    LIMIT_BATCH = rate_limit_config.batch_limit
    LIMIT_AUTH = rate_limit_config.auth_limit
    logger.info(
        f"API 限流已配置: review={LIMIT_REVIEW}, batch={LIMIT_BATCH}, "
        f"auth={LIMIT_AUTH}, default={rate_limit_config.default_limit}"
    )


def setup_rate_limit_middleware(app, config) -> None:
    """在 FastAPI 应用上注册限流中间件和异常处理器.

    Args:
        app: FastAPI 应用实例
        config: AppConfig 实例
    """
    from slowapi.errors import RateLimitExceeded
    from slowapi.middleware import SlowAPIMiddleware
    from fastapi.responses import JSONResponse

    # 配置 limiter
    configure_limiter(config.rate_limit)

    # 注册到 app
    app.state.limiter = limiter
    app.add_middleware(SlowAPIMiddleware)

    # 自定义限流响应（使用 getattr 防止非标准异常缺少 detail 属性）
    @app.exception_handler(RateLimitExceeded)
    async def rate_limit_handler(request, exc: RateLimitExceeded):
        return JSONResponse(
            status_code=429,
            content={
                "error": "rate_limit_exceeded",
                "message": "请求过于频繁，请稍后再试",
                "detail": str(getattr(exc, "detail", "")),
            },
            headers={
                "Retry-After": "60",
            },
        )

    logger.info("限流中间件和异常处理器已注册")
