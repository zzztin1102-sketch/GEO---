"""审核 Agent 数据模型.

定义 ReviewAgent 与 BatchReviewService 所需的请求/响应/选项模型。
这些模型面向 Agent 内部使用，与顶层 `geo_review.models` 的对外模型互补：
本模块的 `ReviewRequest` 额外提供 `get_*` 便捷访问方法，并强类型化
文件输入（`ContentFileInput` / `SubmissionFileInput`）。
"""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


# ----------------------------------------------------------------------
# 文件引用
# ----------------------------------------------------------------------
class FileRef(BaseModel):
    """文件引用 — base64 内容或 file_id 占位."""

    content_base64: Optional[str] = None
    filename: Optional[str] = None
    format: Optional[str] = None
    file_id: Optional[str] = None


class ContentFileInput(BaseModel):
    """正文文件输入."""

    input_type: str = "file"
    file: FileRef


class SubmissionFileInput(BaseModel):
    """提报表文件输入."""

    input_type: str = "file"
    file: FileRef


# ----------------------------------------------------------------------
# 审核选项与元数据
# ----------------------------------------------------------------------
class ReviewOptions(BaseModel):
    """审核选项.

    控制是否爬取官网、是否启用 LLM 审核等行为开关。
    crawl_max_pages / crawl_timeout_seconds 默认为 None 时由 CrawlerConfig 提供。
    """

    crawl_official_urls: bool = False
    crawl_max_pages: Optional[int] = Field(default=None, description="最大爬取页面数（None 时使用 CrawlerConfig.max_pages）")
    crawl_timeout_seconds: Optional[int] = Field(default=None, description="单页爬取超时秒数（None 时使用 CrawlerConfig.timeout）")
    use_llm: bool = True
    use_fact_check: bool = True


class RequestMetadata(BaseModel):
    """请求元数据（允许任意扩展字段）."""

    model_config = ConfigDict(extra="allow")


# ----------------------------------------------------------------------
# 审核请求
# ----------------------------------------------------------------------
class ReviewRequest(BaseModel):
    """完整审核请求（Agent 内部模型）.

    与顶层 `geo_review.models.ReviewRequest` 的区别：
        - `content` / `submission` 采用 dict 形式以保留原始结构（如 override_data）
        - 提供一组 `get_*` 便捷访问方法，统一处理输入类型分支
        - `options` 强类型为 `ReviewOptions`
    """

    request_id: Optional[str] = None
    content: Dict[str, Any]
    submission: Optional[Dict[str, Any]] = None
    official_urls: List[str] = Field(default_factory=list)
    options: Optional[ReviewOptions] = None
    metadata: Optional[RequestMetadata] = None

    def get_all_official_urls(self) -> List[str]:
        """合并请求级与提报表级的官网 URL."""
        urls = list(self.official_urls)
        if self.submission and self.submission.get("input_type") == "json":
            data = self.submission.get("data") or {}
            if isinstance(data, dict):
                extra = data.get("official_urls") or []
                if isinstance(extra, list):
                    urls.extend(extra)
                elif isinstance(extra, str) and extra:
                    urls.append(extra)
        return urls

    def get_submission_text(self) -> Optional[str]:
        """获取文本形式的提报表内容."""
        if not self.submission:
            return None
        if self.submission.get("input_type") == "text":
            return self.submission.get("text")
        return None

    def get_submission_json(self) -> Optional[Dict[str, Any]]:
        """获取 JSON 形式的提报表数据."""
        if not self.submission:
            return None
        if self.submission.get("input_type") == "json":
            data = self.submission.get("data")
            if data is None:
                return None
            if hasattr(data, "model_dump"):
                return data.model_dump()
            return data
        return None

    def get_submission_file(self) -> Optional[SubmissionFileInput]:
        """获取文件形式的提报表输入."""
        if not self.submission:
            return None
        if self.submission.get("input_type") == "file":
            file_dict = self.submission.get("file")
            if not file_dict:
                return None
            if isinstance(file_dict, dict):
                return SubmissionFileInput(file=FileRef(**file_dict))
            return SubmissionFileInput(file=file_dict)
        return None

    def get_content_text(self) -> Optional[str]:
        """获取文本形式的正文内容."""
        if self.content.get("input_type") == "text":
            return self.content.get("text")
        return None

    def get_content_file(self) -> Optional[ContentFileInput]:
        """获取文件形式的正文输入."""
        if self.content.get("input_type") == "file":
            file_dict = self.content.get("file")
            if not file_dict:
                return None
            if isinstance(file_dict, dict):
                return ContentFileInput(file=FileRef(**file_dict))
            return ContentFileInput(file=file_dict)
        return None


# ----------------------------------------------------------------------
# 批量审核
# ----------------------------------------------------------------------
class BatchStatus(str, Enum):
    """批量任务状态."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class BatchItemResult(BaseModel):
    """单个批量项的审核结果."""

    item_id: str
    review_id: Optional[str] = None
    verdict: Optional[str] = None
    status: str
    error: Optional[str] = None
    processing_time: float = 0.0
    result: Optional[Dict[str, Any]] = None


class BatchProgress(BaseModel):
    """批量任务进度."""

    batch_id: str
    status: BatchStatus
    total: int
    completed: int
    success: int
    failed: int
    current_item: Optional[str] = None
    active_items: List[str] = Field(default_factory=list)
    started_at: str = ""
    estimated_remaining_seconds: Optional[float] = None
    last_updated: Optional[str] = None


class BatchReviewResponse(BaseModel):
    """批量审核完整响应."""

    batch_id: str
    status: BatchStatus
    total: int
    completed: int
    success: int
    failed: int
    results: List[BatchItemResult]
    summary: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    processing_time: Optional[float] = None


class BatchReviewRequest(BaseModel):
    """批量审核请求."""

    batch_id: Optional[str] = None
    items: List[Dict[str, Any]]
    shared_submission: Optional[Dict[str, Any]] = None
    shared_official_urls: List[str] = Field(default_factory=list)
    shared_rules: Optional[Dict[str, Any]] = None
    options: Optional[ReviewOptions] = None
    metadata: Optional[RequestMetadata] = None
