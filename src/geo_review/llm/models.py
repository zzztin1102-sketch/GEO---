"""LLM 审核器模型 — 语义审核相关的数据结构（优化版）.

优化点：
    1. LLMProviderConfig 新增 fallback_model、enable_cache、cache_ttl、confidence_threshold、max_issues
    2. LLMReviewResult 新增 cache_hit 字段
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from geo_review.rules.issues import IssueType, IssueSeverity


class LLMReviewResult(BaseModel):
    """LLM 语义审核结果."""
    issues: List["LLMIssue"] = Field(default_factory=list)
    total_issues: int = 0
    model_used: str = ""
    tokens_used: int = 0
    processing_time: float = 0.0  # 秒
    error: Optional[str] = None
    summary: Optional[str] = None  # LLM 给出的整体评估
    retries: int = 0  # 重试次数
    quality_score: float = Field(default=0.0, ge=0.0, le=1.0)  # 结果质量评分
    quality_warnings: List[str] = Field(default_factory=list)  # 质量警告
    # ✅ 新增：缓存命中标记
    cache_hit: bool = False  # 是否命中缓存
    # ✅ 新增：原始响应保留（用于调试）
    raw_response: Optional[str] = None


class LLMIssue(BaseModel):
    """LLM 发现的问题."""
    type: IssueType
    severity: IssueSeverity
    title: str = Field(..., min_length=1, max_length=200)
    snippet: str = Field(..., min_length=1, max_length=2000)
    reason: str = Field(..., min_length=1, max_length=2000)
    suggestion: str = Field(..., min_length=1, max_length=2000)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class LLMProviderConfig(BaseModel):
    """LLM Provider 配置."""
    provider: str = Field(default="openai")
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model: str = Field(default="qwen-plus")
    # ✅ 新增：降级模型
    fallback_model: Optional[str] = Field(default=None, description="主模型不可用时的降级模型")
    temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    max_tokens: int = Field(default=4000, ge=100, le=32000)
    timeout: int = Field(default=60, ge=10, le=300)
    # ✅ 新增：LLM 结果缓存
    enable_cache: bool = Field(default=False, description="是否启用 LLM 结果缓存")
    cache_ttl: int = Field(default=3600, ge=0, description="缓存过期时间（秒）")
    # ✅ 新增：审核参数可配置化
    confidence_threshold: float = Field(default=0.6, ge=0.0, le=1.0, description="置信度阈值")
    max_issues: int = Field(default=20, ge=1, le=100, description="最大问题数")
