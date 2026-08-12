"""System routes — health check, metrics, config."""

import time

from fastapi import APIRouter, Depends, HTTPException, Request

from geo_review.auth.schemas import UserResponse

from .deps import get_current_user

router = APIRouter()


@router.get("/api/v1/health", tags=["系统"])
async def health_check(request: Request):
    """健康检查 — 返回各组件状态."""
    config = request.app.state._config
    agent = request.app.state._agent
    async_session = request.app.state._async_session
    workflow_service = request.app.state._workflow

    checks = {
        "status": "ok",
        "service": "geo-review-api",
        "version": "1.0.0",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    # 数据库状态
    try:
        async with async_session() as session:
            from sqlalchemy import text
            await session.execute(text("SELECT 1"))
        checks["database"] = {"status": "ok"}
    except Exception as e:
        checks["database"] = {"status": "error", "message": str(e)}
        checks["status"] = "degraded"

    # LLM 状态
    try:
        if agent:
            llm_client = agent.llm_client
            checks["llm"] = {
                "status": "ok",
                "provider": config.llm.provider,
                "model": config.llm.model,
                "stats": llm_client.stats,
            }
        else:
            checks["llm"] = {"status": "not_configured"}
    except Exception as e:
        checks["llm"] = {"status": "error", "message": str(e)}

    # 工作流状态
    wf_counts = workflow_service.get_all_status_counts()
    checks["workflow"] = {
        "status": "ok",
        "total_records": sum(wf_counts.values()),
        "status_distribution": wf_counts,
    }

    # 认证状态（供前端动态获取）
    checks["auth_enabled"] = config.auth.enabled

    return checks


@router.get("/api/v1/metrics", tags=["监控"])
async def get_metrics(
    request: Request,
    current_user: UserResponse = Depends(get_current_user),
):
    """获取系统运行指标（需要管理员权限）."""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    metrics_collector = request.app.state._metrics
    return metrics_collector.get_summary()


@router.get("/api/v1/metrics/llm", tags=["监控"])
async def get_llm_stats(
    request: Request,
    current_user: UserResponse = Depends(get_current_user),
):
    """获取 LLM 调用统计."""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    agent = request.app.state._agent
    llm_client = agent._llm_client if agent else None
    if not llm_client:
        raise HTTPException(status_code=503, detail="LLM 客户端未初始化")
    return llm_client.stats


@router.get("/api/v1/config", tags=["系统"])
async def get_config(request: Request):
    """获取当前运行配置（隐藏敏感信息）."""
    config = request.app.state._config
    return {
        "llm": {
            "provider": config.llm.provider,
            "model": config.llm.model,
            "base_url": config.llm.base_url,
            "api_key": "***" if config.llm.api_key else None,
            "temperature": config.llm.temperature,
            "max_tokens": config.llm.max_tokens,
            "timeout": config.llm.timeout,
        },
        "crawler": config.crawler.model_dump(),
        "rule_engine": config.rule_engine.model_dump(),
        "database": {
            "url": config.database.url,
            "echo": config.database.echo,
        },
        "batch": config.batch.model_dump(),
        "api": {
            "host": config.api.host,
            "port": config.api.port,
            "workers": config.api.workers,
        },
        "log": config.log.model_dump(),
    }
