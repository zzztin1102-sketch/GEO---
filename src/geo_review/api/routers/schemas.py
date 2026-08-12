"""API-level Pydantic schemas for request body validation."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ContentInput(BaseModel):
    """正文内容输入."""

    model_config = ConfigDict(extra="allow")

    input_type: str = Field(..., description="输入类型: text / file")

    @model_validator(mode="after")
    def validate_input_type(self):
        if self.input_type not in ("text", "file"):
            raise ValueError(f"content.input_type 必须是 'text' 或 'file'，收到 '{self.input_type}'")
        if self.input_type == "text" and not getattr(self, "text", None):
            raise ValueError("content.input_type='text' 时必须提供 content.text 字段")
        if self.input_type == "file" and not getattr(self, "file", None):
            raise ValueError("content.input_type='file' 时必须提供 content.file 字段")
        return self


class SubmissionInput(BaseModel):
    """提报表输入."""

    model_config = ConfigDict(extra="allow")

    input_type: Optional[str] = Field(None, description="输入类型: text / json / file")


class ReviewOptionsInput(BaseModel):
    """审核选项."""

    model_config = ConfigDict(extra="allow")

    crawl_official_urls: bool = False
    crawl_max_pages: int = 10
    crawl_timeout_seconds: int = 30
    use_llm: bool = True
    use_fact_check: bool = True


class APIReviewRequest(BaseModel):
    """POST /api/v1/review 请求体验证 Schema."""

    model_config = ConfigDict(extra="allow")

    content: Dict[str, Any] = Field(..., description="正文内容")
    submission: Optional[Dict[str, Any]] = Field(None, description="提报表数据")
    official_urls: List[str] = Field(default_factory=list, description="官网 URL 列表")
    options: Optional[Dict[str, Any]] = Field(None, description="审核选项")
    metadata: Optional[Dict[str, Any]] = Field(None, description="元数据")

    @model_validator(mode="after")
    def validate_content(self):
        if not self.content:
            raise ValueError("content 字段不能为空")
        input_type = self.content.get("input_type")
        if not input_type:
            raise ValueError("content.input_type 字段必填（text 或 file）")
        if input_type not in ("text", "file"):
            raise ValueError(f"content.input_type 必须是 'text' 或 'file'，收到 '{input_type}'")
        if input_type == "text" and not self.content.get("text"):
            raise ValueError("content.input_type='text' 时必须提供 content.text")
        if input_type == "file" and not self.content.get("file"):
            raise ValueError("content.input_type='file' 时必须提供 content.file")
        return self


class APIBatchReviewRequest(BaseModel):
    """POST /api/v1/review/batch 请求体验证 Schema."""

    model_config = ConfigDict(extra="allow")

    items: List[Dict[str, Any]] = Field(..., min_length=1, max_length=100, description="审核项列表（1-100 项）")
    shared_submission: Optional[Dict[str, Any]] = None
    shared_official_urls: List[str] = Field(default_factory=list)
    shared_rules: Optional[Dict[str, Any]] = None
    options: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None

    @model_validator(mode="after")
    def validate_items(self):
        for i, item in enumerate(self.items):
            if not item.get("content"):
                raise ValueError(f"items[{i}].content 字段必填")
        return self
