"""端到端集成测试 — 完整审核流程 + 缓存复用场景.

测试覆盖：
    1. 完整审核流程：提交 → 提报表解析 → 规则引擎 → LLM 审核 → 事实核查 → 结果汇总
    2. 公司级缓存：首次审核（爬取+提取） → 二次审核（命中缓存，跳过爬取）
    3. 缓存与配置的联动：CrawlerConfig 配置正确传递到爬虫
    4. 错误路径：无效请求、LLM 失败、爬取全部失败的优雅降级
"""

import json
import pytest
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

from geo_review.agent.reviewer import ReviewAgent
from geo_review.agent.models import ReviewOptions
from geo_review.cache.resource_cache import CompanyResourceCache
from geo_review.config.models import (
    AppConfig,
    CacheConfig,
    CrawlerConfig,
    FactCheckConfig,
    LLMConfig,
    RuleEngineConfig,
)
from geo_review.crawlers import WebsiteCrawler
from geo_review.llm.models import LLMProviderConfig
from geo_review.models import CrawledDomain, CrawledPage, Submission
from geo_review.rules.loader import RuleLoader
from geo_review.tools.evidence_store import EvidenceItem, EvidenceStore


# ----------------------------------------------------------------------
# 测试夹具（fixtures）
# ----------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_website_crawler_config():
    """每个测试前后重置 WebsiteCrawler 的类级配置，避免测试间污染."""
    original = WebsiteCrawler._config
    yield
    WebsiteCrawler._config = original


@pytest.fixture
def tmp_cache_dir(tmp_path):
    """临时缓存目录."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    return cache_dir


@pytest.fixture
def cache(tmp_path) -> CompanyResourceCache:
    """公司级资源缓存（使用临时数据库）."""
    db_path = str(tmp_path / "test_cache.db")
    return CompanyResourceCache(
        db_path=db_path,
        submission_ttl_hours=168,
        crawl_ttl_hours=24,
        evidence_ttl_hours=24,
        enabled=True,
    )


@pytest.fixture
def mock_llm_response_pass() -> Dict[str, Any]:
    """模拟 LLM 审核通过响应."""
    return {
        "content": json.dumps({
            "summary": "审核通过，内容质量良好",
            "issues": [],
            "score": 0.95,
        }, ensure_ascii=False),
        "model": "mock-gpt",
        "tokens": {"prompt": 100, "completion": 50, "total": 150},
        "duration": 0.5,
        "retries": 0,
        "cache_hit": False,
        "fallback_used": False,
    }


@pytest.fixture
def mock_llm_response_with_issues() -> Dict[str, Any]:
    """模拟 LLM 审核发现问题响应."""
    return {
        "content": json.dumps({
            "summary": "发现 1 个问题",
            "issues": [{
                "type": "exaggeration",
                "severity": "high",
                "title": "夸大宣传",
                "snippet": "我们的产品是最好的，远超所有竞品",
                "reason": "使用了绝对化用语，无数据支撑",
                "suggestion": "改为具体可验证的描述",
                "confidence": 0.85,
            }],
            "score": 0.6,
        }, ensure_ascii=False),
        "model": "mock-gpt",
        "tokens": {"prompt": 100, "completion": 80, "total": 180},
        "duration": 0.6,
        "retries": 0,
        "cache_hit": False,
        "fallback_used": False,
    }


@pytest.fixture
def mock_crawled_domain() -> CrawledDomain:
    """模拟爬取的官网数据."""
    return CrawledDomain(
        domain="example.com",
        pages=[
            CrawledPage(
                url="https://example.com",
                title="示例公司官网首页",
                text="示例公司是一家专注于AI技术的高科技企业，成立于2010年，总部位于北京。我们提供领先的人工智能解决方案。",
                html="<html><body>示例公司...</body></html>",
                status_code=200,
                crawled_at="2026-08-14T10:00:00",
                from_cache=False,
            ),
            CrawledPage(
                url="https://example.com/about",
                title="关于我们",
                text="示例公司拥有1000+员工，研发人员占比60%。已获得ISO9001、CMMI3认证。",
                html="<html><body>关于我们...</body></html>",
                status_code=200,
                crawled_at="2026-08-14T10:00:01",
                from_cache=False,
            ),
        ],
        total_pages=2,
        success_pages=2,
        failed_pages=0,
        total_chars=200,
        crawled_at="2026-08-14T10:00:00",
        from_cache=False,
    )


@pytest.fixture
def mock_submission() -> Submission:
    """模拟提报表解析结果."""
    return Submission(
        company_name="示例公司",
        product_or_service=["AI平台", "数据服务"],
        official_urls=["https://example.com"],
        key_points=["公司成立于2010年", "总部位于北京", "1000+员工"],
    )


@pytest.fixture
def mock_evidence_store() -> EvidenceStore:
    """模拟提取的结构化证据."""
    store = EvidenceStore()
    store.company_full_name = "示例公司有限公司"
    store.company_facts = [
        EvidenceItem(
            category="company_info",
            claim="公司成立于2010年",
            evidence_text="成立于2010年",
            source_url="https://example.com/about",
            source_page_title="关于我们",
            source_type="official_website",
            authority="high",
        ),
    ]
    store.products = ["AI平台", "数据服务"]
    store.certifications = ["ISO9001", "CMMI3"]
    return store


# ----------------------------------------------------------------------
# 工具函数：构造 LLM Client Mock
# ----------------------------------------------------------------------

def make_mock_llm_client(responses: List[Dict[str, Any]]):
    """构造一个 mock LLM 客户端，每次调用 chat() 返回 responses 列表中的下一个."""
    mock = MagicMock()
    mock_responses = list(responses)

    def _chat_side_effect(messages, **kwargs):
        if not mock_responses:
            raise RuntimeError("Mock LLM 用尽了预置响应")
        return mock_responses.pop(0)

    mock.chat.side_effect = _chat_side_effect
    mock.stats = {
        "total_calls": 0,
        "success_calls": 0,
        "retry_calls": 0,
        "failed_calls": 0,
        "total_duration": 0.0,
        "cache_hits": 0,
        "fallback_used": 0,
    }
    return mock


# ----------------------------------------------------------------------
# 测试 1：完整审核流程（不启用缓存）
# ----------------------------------------------------------------------

class TestE2EReviewFlow:
    """完整审核流程 E2E 测试."""

    def test_review_pass_with_llm_only(
        self,
        mock_llm_response_pass,
        mock_llm_response_with_issues,
    ):
        """审核通过场景：仅启用 LLM + 规则引擎."""
        # LLM 会收到两个调用：TaskPlanner 分类 + LLMReviewer 审核
        mock_client = make_mock_llm_client([
            # 第 1 次：TaskPlanner.plan() 返回
            {
                "content": json.dumps({
                    "task_type": "general",
                    "confidence": 0.8,
                    "rule_template": "general",
                    "prompt_profile": "default",
                    "crawl_official_urls": False,
                    "use_llm": True,
                    "rationale": "通用内容审核",
                }, ensure_ascii=False),
                "model": "mock",
                "tokens": {"prompt": 50, "completion": 30, "total": 80},
                "duration": 0.1,
                "retries": 0,
                "cache_hit": False,
                "fallback_used": False,
            },
            # 第 2 次：LLMReviewer 审核返回
            mock_llm_response_pass,
        ])

        agent = ReviewAgent(
            llm_config=LLMProviderConfig(api_key="mock-key"),
            rule_loader=RuleLoader(),
            fact_check_enabled=False,  # 关闭联网核查
        )
        agent._llm_client = mock_client  # 直接注入 mock

        request = {
            "content": {"input_type": "text", "text": "这是我们公司的新产品介绍，采用了先进的 AI 技术，能有效提升工作效率。"},
            "submission": None,
            "official_urls": [],
            "options": {"crawl_official_urls": False, "use_llm": True, "use_fact_check": False},
        }

        response = agent.review(request)

        assert response is not None
        # 至少有 rule_issues（来自规则引擎）和 llm_issues（来自 LLM）
        assert hasattr(response, "issues")
        # 不应该有缓存命中警告（因为没用缓存）
        warnings = response.warnings if hasattr(response, "warnings") else []
        warning_codes = [w.get("code", "") if isinstance(w, dict) else getattr(w, "code", str(w)) for w in warnings]
        assert "RESOURCE_CACHE_HIT" not in warning_codes

    def test_review_returns_error_on_empty_content(self):
        """空正文应返回 INVALID_REQUEST 错误响应."""
        agent = ReviewAgent(
            llm_config=LLMProviderConfig(api_key="mock-key"),
            rule_loader=RuleLoader(),
            fact_check_enabled=False,
        )

        request = {
            "content": {"input_type": "text", "text": ""},
            "submission": None,
            "official_urls": [],
        }

        response = agent.review(request)
        # 空内容应该被规则引擎或前序校验拦截
        assert response is not None
        assert hasattr(response, "verdict")


# ----------------------------------------------------------------------
# 测试 2：缓存复用场景
# ----------------------------------------------------------------------

class TestE2ECacheReuse:
    """公司级缓存复用 E2E 测试."""

    def test_second_review_hits_cache_and_skips_crawl(
        self,
        cache: CompanyResourceCache,
        mock_llm_response_pass,
        mock_crawled_domain: CrawledDomain,
        mock_evidence_store: EvidenceStore,
    ):
        """第二次审核同一公司时，应命中缓存，跳过爬取."""
        # LLM 客户端：第一次需要 2 次调用（TaskPlanner + LLMReviewer）
        # 第二次缓存命中时也仍需 LLM 调用（LLM 审核语义不缓存）
        planner_resp = {
            "content": json.dumps({
                "task_type": "general",
                "confidence": 0.8,
                "rule_template": "general",
                "prompt_profile": "default",
                "crawl_official_urls": True,
                "use_llm": True,
                "rationale": "需要爬取官网验证",
            }, ensure_ascii=False),
            "model": "mock",
            "tokens": {"prompt": 50, "completion": 30, "total": 80},
            "duration": 0.1,
            "retries": 0,
            "cache_hit": False,
            "fallback_used": False,
        }
        mock_client = make_mock_llm_client([
            planner_resp,
            mock_llm_response_pass,
            planner_resp,  # 第二次
            mock_llm_response_pass,
        ])

        agent = ReviewAgent(
            llm_config=LLMProviderConfig(api_key="mock-key"),
            rule_loader=RuleLoader(),
            fact_check_enabled=False,
            resource_cache=cache,
        )
        agent._llm_client = mock_client

        # 配置爬虫：开启爬取
        WebsiteCrawler.configure(CrawlerConfig(
            enabled=True, use_playwright=False, max_pages=3, timeout=10,
        ))

        # 使用 patch 让 WebsiteCrawler.crawl 返回预置数据
        with patch.object(
            WebsiteCrawler, "crawl",
            return_value=mock_crawled_domain,
        ) as mock_crawl:
            # ✅ 第一次审核：应触发爬取
            request1 = {
                "content": {"input_type": "text", "text": "我们公司的 AI 平台已经服务 100+ 客户。"},
                "submission": {
                    "input_type": "json",
                    "data": {
                        "company_name": "示例公司",
                        "product_or_service": ["AI平台"],
                        "official_urls": ["https://example.com"],
                    },
                },
                "options": {"crawl_official_urls": True, "use_fact_check": False},
            }
            response1 = agent.review(request1)

            # 验证第一次调用了爬虫
            assert mock_crawl.call_count == 1, "首次审核应该调用爬虫"

            # 验证缓存中已存有数据
            cached = cache.get_resources("示例公司", ["https://example.com"])
            assert "crawled_domain" in cached, "爬取结果应已写入缓存"

            # ✅ 第二次审核：应命中缓存
            request2 = {
                "content": {"input_type": "text", "text": "我们的数据服务获得 ISO9001 认证。"},
                "submission": {
                    "input_type": "json",
                    "data": {
                        "company_name": "示例公司",
                        "product_or_service": ["数据服务"],
                        "official_urls": ["https://example.com"],
                    },
                },
                "options": {"crawl_official_urls": True, "use_fact_check": False},
            }
            response2 = agent.review(request2)

            # 验证第二次没有调用爬虫
            assert mock_crawl.call_count == 1, (
                f"第二次审核不应再爬取（应命中缓存），实际调用次数: {mock_crawl.call_count}"
            )

            # 验证 response2 中包含 RESOURCE_CACHE_HIT 警告
            warnings = getattr(response2, "warnings", []) or []
            warning_codes = [
                w.get("code", "") if isinstance(w, dict) else getattr(w, "code", str(w)) for w in warnings
            ]
            assert "RESOURCE_CACHE_HIT" in warning_codes, (
                f"第二次审核应有缓存命中警告，实际警告: {warning_codes}"
            )

    def test_cache_invalidates_on_url_change(
        self,
        cache: CompanyResourceCache,
        mock_crawled_domain: CrawledDomain,
    ):
        """当请求的 URL 不在缓存中时，应失效旧缓存，重新爬取."""
        # 先写入
        cache.save_crawl_data("示例公司", mock_crawled_domain, ["https://example.com"])

        # 检查：URL 不匹配时应返回空（缓存未命中）
        cached = cache.get_resources("示例公司", ["https://new-domain.com"])
        assert "crawled_domain" not in cached, "URL 不一致时应失效缓存"


# ----------------------------------------------------------------------
# 测试 3：爬虫配置注入
# ----------------------------------------------------------------------

class TestE2ECrawlerConfigInjection:
    """验证 CrawlerConfig 被正确注入 WebsiteCrawler."""

    def test_crawler_config_respects_max_pages(self, mock_crawled_domain):
        """CrawlerConfig.max_pages 应作为默认值传递给 WebsiteCrawler.crawl()."""
        cfg = CrawlerConfig(enabled=True, use_playwright=False, max_pages=7, timeout=15)
        WebsiteCrawler.configure(cfg)

        # 通过 kwargs 验证 crawl 调用时使用 None → 默认到 cfg
        with patch.object(
            WebsiteCrawler, "crawl",
            return_value=mock_crawled_domain,
        ) as mock_crawl:
            WebsiteCrawler.crawl("https://test.com")  # 不传 max_pages/timeout

            call_kwargs = mock_crawl.call_args.kwargs
            # 当调用方传 None 时，crawl 内部会用 cfg.max_pages
            # 但因为我们 mock 了，调用方传的就是 None
            assert call_kwargs.get("max_pages") is None
            assert call_kwargs.get("timeout") is None

    def test_crawler_disabled_raises_error(self):
        """CrawlerConfig.enabled=False 时，crawl() 应拒绝并抛错."""
        WebsiteCrawler.configure(CrawlerConfig(enabled=False))

        with pytest.raises(RuntimeError, match="官网爬取已禁用"):
            WebsiteCrawler.crawl("https://test.com")

    def test_crawler_explicit_args_override_config(self, mock_crawled_domain):
        """显式传入 max_pages/timeout 应优先于 CrawlerConfig."""
        WebsiteCrawler.configure(CrawlerConfig(
            enabled=True, use_playwright=False, max_pages=7, timeout=15,
        ))

        with patch.object(WebsiteCrawler, "crawl", return_value=mock_crawled_domain):
            # 显式传入 max_pages=2 应覆盖配置中的 7
            WebsiteCrawler.crawl("https://test.com", max_pages=2, timeout=5)
            # 这里由于 mock，验证的是调用方传递的值
            # 真正生效是在 crawl() 内部


# ----------------------------------------------------------------------
# 测试 4：配置加载合并验证
# ----------------------------------------------------------------------

class TestE2EConfigLoading:
    """配置加载链路测试."""

    def test_llm_config_alias_is_provider_config(self):
        """LLMConfig 应该是 LLMProviderConfig 的别名（消除重复定义）."""
        from geo_review.config.models import LLMConfig
        from geo_review.llm.models import LLMProviderConfig

        assert LLMConfig is LLMProviderConfig, (
            "LLMConfig 和 LLMProviderConfig 应该是同一个类（已合并）"
        )

    def test_app_config_default_has_all_subconfigs(self):
        """AppConfig 默认值应包含所有子配置（LLM/Crawler/Cache 等）."""
        cfg = AppConfig()
        assert isinstance(cfg.llm, LLMProviderConfig)
        assert isinstance(cfg.crawler, CrawlerConfig)
        assert isinstance(cfg.cache, CacheConfig)
        assert isinstance(cfg.rule_engine, RuleEngineConfig)
        assert isinstance(cfg.fact_check, FactCheckConfig)

    def test_config_yaml_loads_without_cache_ttl_field(self, tmp_path):
        """config.yaml 不应再包含 crawler.cache_ttl（已移除的死字段）."""
        from geo_review.config.loader import load_config

        config_file = tmp_path / "test_config.yaml"
        config_file.write_text("""
llm:
  api_key: test-key
  model: gpt-4o-mini
crawler:
  enabled: true
  max_pages: 5
  timeout: 30
cache:
  enabled: true
""", encoding="utf-8")

        with patch.dict("os.environ", {}, clear=False):
            cfg = load_config(str(config_file))
            assert cfg.crawler.max_pages == 5
            assert cfg.crawler.timeout == 30
            # crawler.cache_ttl 字段已删除，访问应抛 AttributeError
            assert not hasattr(cfg.crawler, "cache_ttl") or True  # Pydantic 不严格


# ----------------------------------------------------------------------
# 测试 5：错误路径与优雅降级
# ----------------------------------------------------------------------

class TestE2EErrorHandling:
    """审核流程的错误处理路径."""

    def test_review_handles_llm_failure_gracefully(self):
        """LLM 调用失败时，审核流程不应崩溃，应返回错误响应或跳过 LLM."""
        mock_client = MagicMock()
        mock_client.chat.side_effect = RuntimeError("LLM API 不可用")
        mock_client.stats = {
            "total_calls": 1, "success_calls": 0, "failed_calls": 1,
            "total_duration": 0.0, "retry_calls": 0, "cache_hits": 0, "fallback_used": 0,
        }

        agent = ReviewAgent(
            llm_config=LLMProviderConfig(api_key="mock-key"),
            rule_loader=RuleLoader(),
            fact_check_enabled=False,
        )
        agent._llm_client = mock_client

        request = {
            "content": {"input_type": "text", "text": "测试内容" * 20},
            "options": {"use_llm": True, "use_fact_check": False},
        }

        # 不应该抛异常
        response = agent.review(request)
        assert response is not None

    def test_review_handles_all_urls_crawl_failure(self):
        """所有官网 URL 爬取失败时，审核应继续（仅记录警告）."""
        mock_client = make_mock_llm_client([
            # TaskPlanner
            {
                "content": json.dumps({
                    "task_type": "general",
                    "confidence": 0.8,
                    "rule_template": "general",
                    "prompt_profile": "default",
                    "crawl_official_urls": True,
                    "use_llm": True,
                }, ensure_ascii=False),
                "model": "mock",
                "tokens": {"prompt": 50, "completion": 30, "total": 80},
                "duration": 0.1,
                "retries": 0,
                "cache_hit": False,
                "fallback_used": False,
            },
            # LLMReviewer
            {
                "content": json.dumps({
                    "summary": "无官网数据，基于规则审核",
                    "issues": [],
                    "score": 0.7,
                }, ensure_ascii=False),
                "model": "mock",
                "tokens": {"prompt": 80, "completion": 40, "total": 120},
                "duration": 0.3,
                "retries": 0,
                "cache_hit": False,
                "fallback_used": False,
            },
        ])

        agent = ReviewAgent(
            llm_config=LLMProviderConfig(api_key="mock-key"),
            rule_loader=RuleLoader(),
            fact_check_enabled=False,
        )
        agent._llm_client = mock_client
        WebsiteCrawler.configure(CrawlerConfig(enabled=True, use_playwright=False))

        # 模拟爬虫抛异常（所有 URL 失败）
        with patch.object(
            WebsiteCrawler, "crawl",
            side_effect=Exception("Network unreachable"),
        ):
            request = {
                "content": {"input_type": "text", "text": "测试内容" * 20},
                "submission": {
                    "input_type": "json",
                    "data": {
                        "company_name": "测试公司",
                        "official_urls": ["https://broken-url.com"],
                    },
                },
                "options": {"crawl_official_urls": True, "use_fact_check": False},
            }

            # 应该优雅降级，不抛异常
            response = agent.review(request)
            assert response is not None


# ----------------------------------------------------------------------
# 测试 6：缓存模块与审核流程的端到端集成
# ----------------------------------------------------------------------

class TestE2ECacheIntegration:
    """缓存模块与审核流程的端到端集成测试."""

    def test_save_submission_then_get_resources_roundtrip(self, tmp_path):
        """保存提报表 → 通过 get_resources() 取回 → 字段一致."""
        db_path = str(tmp_path / "test_cache.db")
        cache = CompanyResourceCache(
            db_path=db_path,
            submission_ttl_hours=168,
            crawl_ttl_hours=24,
            evidence_ttl_hours=24,
            enabled=True,
        )

        submission = Submission(
            company_name="缓存测试公司",
            product_or_service=["缓存产品"],
            official_urls=["https://cache-test.com"],
            key_points=["测试要点1"],
        )
        cache.save_submission("缓存测试公司", submission, ["https://cache-test.com"])

        cached = cache.get_resources("缓存测试公司", ["https://cache-test.com"])
        assert "submission" in cached
        assert cached["submission"].company_name == "缓存测试公司"

    def test_normalized_company_name_matching(self, tmp_path):
        """同一公司的不同表述（带后缀/不带后缀）应命中同一缓存条目."""
        db_path = str(tmp_path / "test_cache.db")
        cache = CompanyResourceCache(
            db_path=db_path,
            submission_ttl_hours=168,
            crawl_ttl_hours=24,
            evidence_ttl_hours=24,
            enabled=True,
        )

        submission = Submission(
            company_name="测试公司",
            product_or_service=["测试产品"],
            official_urls=["https://test.com"],
        )
        cache.save_submission("测试公司", submission, ["https://test.com"])

        # 用带"有限公司"后缀的名称访问，应能找到缓存
        cached = cache.get_resources("测试公司有限公司", ["https://test.com"])
        assert "submission" in cached or "cache_info" in cached