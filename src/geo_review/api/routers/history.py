"""History routes — list, detail, delete, statistics."""

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()


@router.get("/api/v1/history", tags=["历史记录"])
async def list_history(
    request: Request,
    page: int = 1,
    page_size: int = 20,
    verdict: Optional[str] = None,
    status: Optional[str] = None,
    company_name: Optional[str] = None,
    task_name: Optional[str] = None,
    content_title: Optional[str] = None,
    submission_name: Optional[str] = None,
    batch_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    sort_by: str = "reviewed_at",
    sort_order: str = "desc",
):
    """分页查询审核历史记录."""
    history_service = request.app.state._history_service
    try:
        histories, total = await history_service.list_reviews(
            page=page,
            page_size=page_size,
            verdict=verdict,
            status=status,
            company_name=company_name,
            task_name=task_name,
            content_title=content_title,
            submission_name=submission_name,
            batch_id=batch_id,
            start_date=start_date,
            end_date=end_date,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        return {
            "data": histories,
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": (total + page_size - 1) // page_size,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"查询历史记录失败: {str(e)}")


@router.get("/api/v1/history/statistics", tags=["历史记录"])
async def get_history_statistics(
    request: Request,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
):
    """获取审核统计数据."""
    history_service = request.app.state._history_service
    try:
        stats = await history_service.get_statistics(start_date=start_date, end_date=end_date)
        return stats
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取统计数据失败: {str(e)}")


@router.get("/api/v1/history/batch/{batch_id}", tags=["历史记录"])
async def get_history_by_batch(batch_id: str, request: Request):
    """获取批量任务的所有审核历史记录."""
    history_service = request.app.state._history_service
    try:
        histories = await history_service.get_batch_results(batch_id)
        return {"data": histories, "total": len(histories)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"查询批量历史失败: {str(e)}")


@router.get("/api/v1/history/{review_id}", tags=["历史记录"])
async def get_history_detail(review_id: str, request: Request):
    """获取单条审核记录详情（含问题列表）."""
    history_service = request.app.state._history_service
    try:
        result = await history_service.get_review_with_issues(review_id)
        if not result:
            raise HTTPException(status_code=404, detail=f"审核记录 '{review_id}' 不存在")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取审核记录失败: {str(e)}")


@router.delete("/api/v1/history/{review_id}", tags=["历史记录"])
async def delete_history(review_id: str, request: Request):
    """删除单条审核记录（软删除）."""
    history_service = request.app.state._history_service
    try:
        success = await history_service.delete_review(review_id)
        if not success:
            raise HTTPException(status_code=404, detail=f"审核记录 '{review_id}' 不存在")
        return {"status": "deleted", "review_id": review_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除审核记录失败: {str(e)}")


@router.post("/api/v1/history/batch-delete", tags=["历史记录"])
async def batch_delete_history(review_ids: List[str], request: Request):
    """批量删除审核记录（软删除）."""
    history_service = request.app.state._history_service
    try:
        count = await history_service.batch_delete(review_ids)
        return {"status": "deleted", "count": count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"批量删除失败: {str(e)}")
