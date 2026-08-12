"""审核问题模型 — 与 review-response.schema.json 对齐."""

from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field


class IssueType(str, Enum):
    """问题类型."""
    INCONSISTENT_WITH_SUBMISSION = "inconsistent_with_submission"
    INCONSISTENT_WITH_WEBSITE = "inconsistent_with_website"
    UNSUPPORTED_CLAIM = "unsupported_claim"
    EXAGGERATION = "exaggeration"
    COMPETITOR_DISPARAGEMENT = "competitor_disparagement"
    SEMANTIC_RISK = "semantic_risk"
    TONE_ISSUE = "tone_issue"
    # GEO 特有审核维度
    GEO_CITABILITY = "geo_citability"          # LLM可引用性：实体、来源、结构化信息、事实依据
    GEO_BRAND_CONSISTENCY = "geo_brand_consistency"  # 品牌实体一致性：AI可识别性


class IssueSeverity(str, Enum):
    """严重程度."""
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"
    INFO = "info"


class IssueEvidence(BaseModel):
    """问题证据."""
    snippet: str = Field(..., min_length=1, max_length=2000)
    position: Optional[str] = Field(default=None, max_length=200)
    reference_source: str = Field(default="review_rule")
    reference_detail: Optional[str] = Field(default=None, max_length=2000)
    reference_field: Optional[str] = None
    source_url: Optional[str] = None


class Issue(BaseModel):
    """审核问题."""
    id: str = Field(..., pattern=r"^ISS-\d{3,}$")
    type: IssueType
    severity: IssueSeverity
    title: str = Field(..., min_length=1, max_length=200)
    evidence: IssueEvidence
    reason: str = Field(default="", max_length=2000, description="问题原因分析")
    suggestion: str = Field(..., min_length=1, max_length=2000)

    @classmethod
    def make_id(cls, idx: int) -> str:
        """生成问题 ID（ISS-001 格式）."""
        return f"ISS-{idx:03d}"
