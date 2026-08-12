"""审核流程模型 — 状态机定义."""

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ReviewWorkflowStatus(str, Enum):
    """审核流程状态.

    状态流转:
        PENDING     -> PROCESSING  (开始审核)
        PROCESSING  -> COMPLETED   (审核完成)
        PROCESSING  -> FAILED      (审核异常)
        COMPLETED   -> REVIEWING   (提交人工复核)
        REVIEWING   -> APPROVED    (复核通过)
        REVIEWING   -> REJECTED    (复核不通过)
        APPROVED    -> ARCHIVED    (归档)
        REJECTED    -> REVISING    (返回修改)
        REVISING    -> PENDING     (重新提交)
    """
    PENDING = "pending"         # 待审核
    PROCESSING = "processing"   # 审核中
    COMPLETED = "completed"     # 审核完成（待人工确认）
    FAILED = "failed"           # 审核失败
    REVIEWING = "reviewing"     # 人工复核中
    APPROVED = "approved"       # 复核通过
    REJECTED = "rejected"       # 复核不通过
    REVISING = "revising"       # 返回修改中
    ARCHIVED = "archived"       # 已归档


class ReviewComment(BaseModel):
    """审核意见/批注."""
    comment_id: str = Field(..., description="意见ID")
    review_id: str = Field(..., description="关联审核记录ID")
    author: str = Field(..., description="作者")
    author_role: str = Field(default="reviewer", description="角色: reviewer/admin")
    content: str = Field(..., min_length=1, max_length=5000, description="意见内容")
    issue_refs: List[str] = Field(default_factory=list, description="关联问题ID列表")
    action: Optional[str] = Field(default=None, description="操作建议: approve/reject/revise")
    created_at: str = Field(..., description="创建时间 ISO 格式")


class ReviewAuditLog(BaseModel):
    """审核操作日志."""
    log_id: str = Field(..., description="日志ID")
    review_id: str = Field(..., description="关联审核记录ID")
    action: str = Field(..., description="操作: submit/process/approve/reject/revise")
    from_status: Optional[str] = Field(default=None, description="原状态")
    to_status: Optional[str] = Field(default=None, description="目标状态")
    operator: Optional[str] = Field(default=None, description="操作人")
    note: Optional[str] = Field(default=None, description="备注")
    created_at: str = Field(..., description="创建时间 ISO 格式")


class WorkflowTransitionRequest(BaseModel):
    """状态流转请求."""
    action: str = Field(..., description="操作: review/approve/reject/revise/archive")
    operator: Optional[str] = Field(default=None, description="操作人")
    note: Optional[str] = Field(default=None, description="操作备注")


class WorkflowSummary(BaseModel):
    """流程状态摘要."""
    review_id: str
    current_status: str
    status_display: str
    can_review: bool = False
    can_approve: bool = False
    can_reject: bool = False
    can_revise: bool = False
    can_archive: bool = False
    comments_count: int = 0
    audit_logs: List[ReviewAuditLog] = Field(default_factory=list)