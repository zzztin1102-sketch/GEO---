"""配置数据模型 — 统一管理系统配置（优化版）.

优化点：
    1. LLMConfig 新增 fallback_model、enable_cache、cache_ttl、confidence_threshold、max_issues
    2. APIConfig 的 cors_origins 默认值收紧
    3. AuthConfig 默认启用认证
"""

from typing import Dict, List, Optional
from pydantic import BaseModel, Field


class LLMConfig(BaseModel):
    """LLM 配置."""
    provider: str = Field(default="openai", description="LLM 提供商")
    base_url: Optional[str] = Field(default=None, description="API 基础 URL")
    api_key: Optional[str] = Field(default=None, description="API Key（建议通过环境变量设置）")
    model: str = Field(default="qwen-plus", description="主模型名称")
    # ✅ 新增：降级模型
    fallback_model: Optional[str] = Field(default=None, description="主模型不可用时的降级模型")
    temperature: float = Field(default=0.1, ge=0.0, le=2.0, description="温度参数")
    max_tokens: int = Field(default=4000, ge=100, le=32000, description="最大 token 数")
    timeout: int = Field(default=60, ge=10, le=300, description="超时时间（秒）")
    # ✅ 新增：LLM 结果缓存
    enable_cache: bool = Field(default=False, description="是否启用 LLM 结果缓存")
    cache_ttl: int = Field(default=3600, ge=0, description="缓存过期时间（秒），0 表示不过期")
    # ✅ 新增：审核参数可配置化（之前硬编码在 reviewer.py 中）
    confidence_threshold: float = Field(default=0.6, ge=0.0, le=1.0, description="置信度阈值，低于此值的问题降级为 info")
    max_issues: int = Field(default=20, ge=1, le=100, description="LLM 单次最多返回的问题数")


class CrawlerConfig(BaseModel):
    """爬虫配置."""
    enabled: bool = Field(default=True, description="是否启用官网爬取")
    use_playwright: bool = Field(default=True, description="是否使用 Playwright 处理动态页面")
    max_pages: int = Field(default=5, ge=1, le=50, description="最大爬取页面数")
    timeout: int = Field(default=30, ge=5, le=120, description="爬取超时（秒）")
    cache_ttl: int = Field(default=3600, ge=0, description="缓存过期时间（秒），0 表示不过期")


class RuleEngineConfig(BaseModel):
    """规则引擎配置."""
    default_template: str = Field(default="general", description="默认规则模板")
    enable_length_check: bool = Field(default=True, description="启用长度检查")
    enable_forbidden_keywords: bool = Field(default=True, description="启用禁用词检查")
    enable_required_keywords: bool = Field(default=True, description="启用必含词检查")
    enable_competitor_check: bool = Field(default=True, description="启用竞品检查")
    enable_claim_check: bool = Field(default=True, description="启用声明检查")
    min_content_length: int = Field(default=100, ge=1, description="最短正文长度")
    max_content_length: int = Field(default=50000, ge=100, description="最长正文长度")


class DatabaseConfig(BaseModel):
    """数据库配置."""
    url: str = Field(default="sqlite+aiosqlite:///./review_history.db", description="数据库连接 URL")
    echo: bool = Field(default=False, description="是否输出 SQL 日志")


class BatchConfig(BaseModel):
    """批量审核配置."""
    max_items: int = Field(default=100, ge=1, le=500, description="每批最大项数")
    max_concurrent: int = Field(default=3, ge=1, le=10, description="最大并发数（建议 3-5，避免 LLM API 限流）")
    task_expiry_hours: int = Field(default=24, ge=1, description="任务过期时间（小时）")


class APIConfig(BaseModel):
    """API 服务配置."""
    host: str = Field(default="0.0.0.0", description="监听地址")
    port: int = Field(default=8000, ge=1, le=65535, description="监听端口")
    workers: int = Field(default=1, ge=1, le=16, description="工作进程数")
    # ✅ 安全：默认收紧 CORS 来源
    cors_origins: List[str] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://localhost:8000"],
        description="CORS 允许来源（生产环境请替换为实际域名）",
    )
    docs_url: str = Field(default="/docs", description="Swagger 文档路径")
    redoc_url: str = Field(default="/redoc", description="ReDoc 文档路径")


class LogConfig(BaseModel):
    """日志配置."""
    level: str = Field(default="INFO", description="日志级别")
    format: str = Field(
        default="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        description="日志格式",
    )
    file: Optional[str] = Field(default=None, description="日志文件路径（None 表示仅控制台输出）")


class AuthConfig(BaseModel):
    """认证配置."""
    # ✅ 安全：默认启用认证
    enabled: bool = Field(default=True, description="是否启用 API 认证保护")
    secret_key: Optional[str] = Field(default=None, description="JWT 密钥，None 时自动生成临时密钥（建议通过环境变量 AUTH_SECRET_KEY 设置）")
    token_expire_minutes: int = Field(default=1440, ge=5, le=10080, description="访问令牌过期时间（分钟），默认 24 小时")
    default_admin_username: str = Field(default="admin", description="默认管理员用户名")
    default_admin_password: Optional[str] = Field(default=None, description="默认管理员密码，None 时自动生成临时密码（建议通过环境变量设置）")
    allow_registration: bool = Field(default=False, description="是否允许用户注册")


class FactCheckConfig(BaseModel):
    """联网事实核查配置."""
    enabled: bool = Field(default=True, description="是否启用联网事实核查")
    max_claims: int = Field(default=5, ge=1, le=20, description="单次最多核查的声明数量")
    max_search_results: int = Field(default=5, ge=1, le=10, description="每条声明最多返回的搜索结果数")
    search_timeout: int = Field(default=15, ge=5, le=60, description="搜索超时时间（秒）")
    # 自定义搜索 API（可选，留空则使用 DuckDuckGo 免费搜索）
    search_api_url: Optional[str] = Field(default=None, description="自定义搜索 API URL（如 Bing/SerpAPI），留空则用 DuckDuckGo")
    search_api_key: Optional[str] = Field(default=None, description="自定义搜索 API Key")


class RateLimitConfig(BaseModel):
    """API 限流配置."""
    enabled: bool = Field(default=True, description="是否启用 API 限流")
    # 单次审核接口：每分钟最多 10 次（每次消耗 LLM token）
    review_limit: str = Field(default="10/minute", description="单次审核接口限流")
    # 批量审核接口：每分钟最多 3 次（消耗更大）
    batch_limit: str = Field(default="3/minute", description="批量审核接口限流")
    # 认证接口：每分钟最多 20 次（防暴力破解）
    auth_limit: str = Field(default="20/minute", description="认证接口限流")
    # 默认限流：每分钟 60 次
    default_limit: str = Field(default="60/minute", description="默认 API 限流")


class AppConfig(BaseModel):
    """应用总配置."""
    llm: LLMConfig = Field(default_factory=LLMConfig)
    crawler: CrawlerConfig = Field(default_factory=CrawlerConfig)
    rule_engine: RuleEngineConfig = Field(default_factory=RuleEngineConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    batch: BatchConfig = Field(default_factory=BatchConfig)
    api: APIConfig = Field(default_factory=APIConfig)
    log: LogConfig = Field(default_factory=LogConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    fact_check: FactCheckConfig = Field(default_factory=FactCheckConfig)
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)
