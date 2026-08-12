"""Workflow routes — status, transitions, comments."""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from geo_review.auth.schemas import UserResponse
from geo_review.workflow import WorkflowService
from geo_review.workflow.models import WorkflowTransitionRequest

from .deps import get_current_user

router = APIRouter()


@router.get("/api/v1/workflow/{review_id}", tags=["流程管理"])
async def get_workflow_summary(review_id: str, request: Request):
    """获取审核记录的流程状态摘要."""
    workflow_service = request.app.state._workflow
    summary = workflow_service.get_summary(review_id)
    if not summary:
        raise HTTPException(status_code=404, detail=f"审核记录 '{review_id}' 无流程状态")
    return summary.model_dump(mode="json")


@router.post("/api/v1/workflow/{review_id}/transition", tags=["流程管理"])
async def workflow_transition(
    review_id: str,
    req: WorkflowTransitionRequest,
    request: Request,
    current_user: UserResponse = Depends(get_current_user),
):
    """执行审核状态流转（review/approve/reject/revise/archive）."""
    workflow_service = request.app.state._workflow
    operator = req.operator or current_user.username

    # 权限校验
    if req.action in ("approve", "reject", "archive") and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")

    new_status = workflow_service.transition(
        review_id, req.action, operator=operator, note=req.note
    )
    if not new_status:
        current = workflow_service.get_status(review_id)
        allowed = workflow_service.get_allowed_actions(review_id)
        raise HTTPException(
            status_code=400,
            detail=f"状态流转失败：当前状态 '{current}' 不支持操作 '{req.action}'，允许的操作: {allowed}"
        )
    return {
        "status": "ok",
        "review_id": review_id,
        "new_status": new_status,
        "action": req.action,
    }


@router.post("/api/v1/workflow/{review_id}/comments", tags=["流程管理"])
async def add_workflow_comment(
    review_id: str,
    content: str,
    request: Request,
    issue_refs: Optional[List[str]] = None,
    action: Optional[str] = None,
    current_user: UserResponse = Depends(get_current_user),
):
    """添加审核意见/批注."""
    workflow_service = request.app.state._workflow
    comment = workflow_service.add_comment(
        review_id=review_id,
        author=current_user.username,
        content=content,
        author_role=current_user.role,
        issue_refs=issue_refs,
        action=action,
    )
    return comment.model_dump(mode="json")


@router.get("/api/v1/workflow/{review_id}/comments", tags=["流程管理"])
async def get_workflow_comments(review_id: str, request: Request):
    """获取审核意见列表."""
    workflow_service = request.app.state._workflow
    comments = workflow_service.get_comments(review_id)
    return {"data": [c.model_dump(mode="json") for c in comments]}


@router.get("/api/v1/workflow/status/counts", tags=["流程管理"])
async def get_workflow_status_counts(
    request: Request,
    current_user: UserResponse = Depends(get_current_user),
):
    """获取各状态数量统计（仪表盘用）."""
    workflow_service = request.app.state._workflow
    counts = workflow_service.get_all_status_counts()
    total = sum(counts.values())
    return {
        "total": total,
        "counts": counts,
        "status_display": {
            k: WorkflowService._STATUS_DISPLAY.get(k, k)
            for k in counts.keys()
        },
    }
