"""审核结果模型 — 与 review-response.schema.json 完全对齐."""

from datetime import datetime
from geo_review.utils.time import now as beijing_now
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator

from geo_review.rules.issues import Issue, IssueSeverity, IssueType

try:
    from geo_review.llm.models import LLMReviewResult
except ImportError:
    LLMReviewResult = None


class ReviewStatus(str, Enum):
    """审核状态."""
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class ReviewVerdict(str, Enum):
    """审核裁决."""
    PASS = "pass"
    REVISE = "revise"
    REJECT = "reject"


class ReviewStats(BaseModel):
    """问题统计."""
    total: int = 0
    by_severity: Dict[str, int] = Field(
        default_factory=lambda: {"critical": 0, "major": 0, "minor": 0, "info": 0}
    )
    by_type: Dict[str, int] = Field(
        default_factory=lambda: {
            "inconsistent_with_submission": 0,
            "inconsistent_with_website": 0,
            "unsupported_claim": 0,
            "exaggeration": 0,
            "competitor_disparagement": 0,
        }
    )

    @classmethod
    def from_issues(cls, issues: List[Issue]) -> "ReviewStats":
        """从问题列表生成统计."""
        stats = cls()
        stats.total = len(issues)
        for issue in issues:
            sev = issue.severity.value
            stats.by_severity[sev] = stats.by_severity.get(sev, 0) + 1
            itype = issue.type.value
            stats.by_type[itype] = stats.by_type.get(itype, 0) + 1
        return stats


class FailedUrl(BaseModel):
    """爬取失败的 URL."""
    url: str
    reason: str = Field(..., max_length=500)


class ReferencesUsed(BaseModel):
    """参考信息使用情况."""
    submission_source: Optional[str] = None
    content_source: Optional[str] = None
    official_urls_requested: List[str] = Field(default_factory=list)
    official_urls_crawled: List[str] = Field(default_factory=list)
    official_urls_failed: List[FailedUrl] = Field(default_factory=list)


class ReviewWarning(BaseModel):
    """处理警告."""
    code: str
    message: str = Field(..., min_length=1, max_length=1000)


class ReviewError(BaseModel):
    """错误信息."""
    code: str
    message: str = Field(..., min_length=1, max_length=2000)
    details: Optional[Dict[str, Any]] = None


class ReviewResponse(BaseModel):
    """审核响应 — 与 review-response.schema.json 完全一致."""
    review_id: UUID = Field(default_factory=uuid4)
    request_id: Optional[UUID] = None
    status: ReviewStatus
    verdict: ReviewVerdict
    summary: str = Field(..., min_length=1, max_length=2000)
    task_name: Optional[str] = Field(default=None, max_length=200)
    issues: List[Issue] = Field(default_factory=list)
    revision_checklist: List[str] = Field(default_factory=list)
    stats: ReviewStats = Field(default_factory=ReviewStats)
    references_used: Optional[ReferencesUsed] = None
    warnings: List[ReviewWarning] = Field(default_factory=list)
    llm_review: Optional[Any] = Field(default=None, description="LLM语义审核结果")
    plan_summary: Optional[Dict[str, Any]] = Field(default=None, description="TaskPlanner审核计划摘要")
    error: Optional[ReviewError] = None
    reviewed_at: datetime = Field(default_factory=beijing_now)
    duration_ms: int = Field(default=0, ge=0)

    @field_validator("revision_checklist")
    @classmethod
    def _validate_checklist_length(cls, v: List[str]) -> List[str]:
        for item in v:
            if len(item) > 500:
                raise ValueError("修改清单单条不能超过 500 字")
        return v
