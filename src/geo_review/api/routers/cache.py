"""Cache routes — 公司级资源缓存管理 API."""

import logging

from fastapi import APIRouter, HTTPException, Query, Request

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/v1/cache/stats", tags=["缓存管理"])
async def get_cache_stats(request: Request):
    """获取资源缓存统计信息."""
    cache = getattr(request.app.state, "_resource_cache", None)
    if cache is None:
        return {"enabled": False, "message": "资源缓存未启用"}
    return cache.get_stats()


@router.get("/api/v1/cache/companies", tags=["缓存管理"])
async def list_cached_companies(request: Request):
    """列出所有缓存的公司."""
    cache = getattr(request.app.state, "_resource_cache", None)
    if cache is None:
        return {"enabled": False, "companies": []}
    companies = cache.get_cached_companies()
    return {"total": len(companies), "companies": companies}


@router.get("/api/v1/cache/{company_name}", tags=["缓存管理"])
async def get_company_cache(company_name: str, request: Request):
    """查看指定公司的缓存详情."""
    cache = getattr(request.app.state, "_resource_cache", None)
    if cache is None:
        raise HTTPException(status_code=404, detail="资源缓存未启用")
    detail = cache.get_company_detail(company_name)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"未找到公司 '{company_name}' 的缓存记录")
    return detail


@router.delete("/api/v1/cache/{company_name}", tags=["缓存管理"])
async def clear_company_cache(company_name: str, request: Request):
    """清除指定公司的缓存."""
    cache = getattr(request.app.state, "_resource_cache", None)
    if cache is None:
        raise HTTPException(status_code=404, detail="资源缓存未启用")
    success = cache.clear_company(company_name)
    if not success:
        raise HTTPException(status_code=404, detail=f"未找到公司 '{company_name}' 的缓存记录")
    return {"message": f"已清除公司 '{company_name}' 的缓存", "success": True}


@router.delete("/api/v1/cache", tags=["缓存管理"])
async def clear_all_cache(request: Request):
    """清除所有缓存."""
    cache = getattr(request.app.state, "_resource_cache", None)
    if cache is None:
        raise HTTPException(status_code=404, detail="资源缓存未启用")
    count = cache.clear_all()
    return {"message": f"已清除全部缓存（{count} 条记录）", "cleared": count}


@router.post("/api/v1/cache/cleanup", tags=["缓存管理"])
async def cleanup_expired_cache(request: Request):
    """清除所有已过期的缓存数据."""
    cache = getattr(request.app.state, "_resource_cache", None)
    if cache is None:
        raise HTTPException(status_code=404, detail="资源缓存未启用")
    count = cache.clear_expired()
    return {"message": f"已清除 {count} 条过期缓存记录", "cleared": count}
