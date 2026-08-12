"""GEO 生文审核核心数据模型."""

from typing import List, Optional
from pydantic import BaseModel, Field, field_validator


class Submission(BaseModel):
    """GEO 生文提报表 — 与 Excel A–N 列及 submission.schema.json 对齐."""

    task_name: str = Field(
        default="未指定任务", min_length=1, max_length=200,
        description="任务名称，建议含产品名与日期"
    )
    submitter: Optional[str] = Field(
        default=None, max_length=50,
        description="业务对接人姓名"
    )
    company_name: str = Field(
        default="未指定公司", min_length=1, max_length=200,
        description="公司/品牌名称，须与官网一致"
    )
    product_or_service: List[str] = Field(
        default_factory=lambda: ["未指定"], min_length=1,
        description="文中涉及的产品或服务"
    )
    core_topic: str = Field(
        default="未指定", min_length=1, max_length=500,
        description="全文核心主题，一句话概括"
    )
    key_points: List[str] = Field(
        default_factory=lambda: ["未指定"], min_length=1,
        description="必须在正文中体现的事实或卖点"
    )
    keywords: List[str] = Field(
        default_factory=list,
        description="希望文中自然覆盖的关键词"
    )
    reference_copy: List[str] = Field(
        default_factory=list,
        description="需原文或高度一致使用的句子"
    )
    allowed_facts: List[str] = Field(
        default_factory=list,
        description="仅允许在正文中出现的数字、资质、合作方等事实"
    )
    forbidden_claims: List[str] = Field(
        default_factory=lambda: ["行业第一", "唯一", "100%", "最好", "最佳"], min_length=1,
        description="绝对化、夸大等禁用词或表述"
    )
    must_not_mention: List[str] = Field(
        default_factory=list,
        description="敏感话题、不可提及的信息"
    )
    competitor_names: List[str] = Field(
        default_factory=list,
        description="用于识别拉踩、不当对比的竞品名称"
    )
    official_urls: List[str] = Field(
        default_factory=lambda: ["https://example.com"], min_length=1, max_length=20,
        description="事实核对基准页面"
    )
    notes: str = Field(
        default="无", max_length=2000,
        description="其他审核要求"
    )

    @field_validator("product_or_service", "key_points", "keywords",
                     "reference_copy", "allowed_facts", "forbidden_claims",
                     "must_not_mention", "competitor_names", mode="before")
    @classmethod
    def _ensure_list(cls, v):
        """统一将标量转为单元素列表，兼容多种输入."""
        if v is None:
            return []
        if isinstance(v, str):
            stripped = v.strip()
            if not stripped:
                return []
            return [stripped]
        return v

    @field_validator("official_urls", mode="before")
    @classmethod
    def _ensure_url_list(cls, v):
        """官方URL须为列表，空时填充默认值."""
        if isinstance(v, str):
            stripped = v.strip()
            if not stripped:
                return ["https://example.com"]
            return [stripped]
        if not v:
            return ["https://example.com"]
        return v

    @field_validator("notes", mode="before")
    @classmethod
    def _default_notes(cls, v):
        """notes 为空时默认为 '无'."""
        if v is None or (isinstance(v, str) and not v.strip()):
            return "无"
        return v


class ContentInput(BaseModel):
    """待审正文输入 — 文本或文件引用."""
    input_type: str = Field(..., pattern="^(text|file)$")
    text: Optional[str] = None
    file: Optional[dict] = None


class SubmissionInput(BaseModel):
    """提报表输入 — 文本/JSON/文件."""
    input_type: str = Field(..., pattern="^(text|json|file)$")
    text: Optional[str] = None
    data: Optional[Submission] = None
    file: Optional[dict] = None


class ParsedContent(BaseModel):
    """解析后的待审正文内容."""
    text: str = Field(..., min_length=1, description="提取的纯文本内容")
    source: str = Field(..., description="来源类型: text | pdf | docx | doc | txt")
    filename: Optional[str] = Field(default=None, description="原始文件名")
    page_count: Optional[int] = Field(default=None, description="PDF页数")
    char_count: int = Field(default=0, description="字符数")
    truncated: bool = Field(default=False, description="内容是否被截断")
    warnings: List[str] = Field(default_factory=list, description="解析警告")


class CrawledPage(BaseModel):
    """单个官网页面爬取结果."""
    url: str = Field(..., description="页面URL")
    title: Optional[str] = Field(default=None, description="页面标题")
    text: str = Field(default="", description="提取的纯文本内容")
    html: Optional[str] = Field(default=None, description="原始HTML（可选保存）")
    status_code: Optional[int] = Field(default=None, description="HTTP状态码")
    crawled_at: str = Field(default="", description="爬取时间（ISO 8601）")
    from_cache: bool = Field(default=False, description="是否来自缓存")
    error: Optional[str] = Field(default=None, description="爬取失败的错误信息")


class CrawledDomain(BaseModel):
    """单个域名的爬取结果汇总."""
    domain: str = Field(..., description="域名（如 example.com）")
    pages: List[CrawledPage] = Field(default_factory=list, description="爬取的页面列表")
    total_pages: int = Field(default=0, description="尝试爬取的总页数")
    success_pages: int = Field(default=0, description="成功爬取的页数")
    failed_pages: int = Field(default=0, description="失败的页数")
    total_chars: int = Field(default=0, description="总字符数")
    crawled_at: str = Field(default="", description="首次爬取时间")
    from_cache: bool = Field(default=False, description="是否全部来自缓存")


class ReviewRequest(BaseModel):
    """完整审核请求."""
    request_id: Optional[str] = None
    content: ContentInput
    submission: SubmissionInput
    official_urls: List[str] = Field(default_factory=list)
    options: Optional[dict] = None
    metadata: Optional[dict] = None
