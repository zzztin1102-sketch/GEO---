"""资源缓存模块测试 — CompanyResourceCache."""

import os
import tempfile
import time
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from geo_review.cache.resource_cache import (
    CompanyResourceCache,
    _normalize_company_name,
    _is_expired,
    _now_iso,
)
from geo_review.models import CrawledDomain, CrawledPage, Submission
from geo_review.tools.evidence_store import EvidenceItem, EvidenceStore, extract_evidence


# ============ Fixtures ============

@pytest.fixture
def temp_cache(tmp_path):
    """创建临时缓存实例（使用 pytest tmp_path 目录）."""
    db_path = str(tmp_path / "test_cache.db")
    cache = CompanyResourceCache(
        db_path=db_path,
        submission_ttl_hours=168,
        crawl_ttl_hours=24,
        evidence_ttl_hours=24,
        enabled=True,
    )
    yield cache
    # 清理（tmp_path 由 pytest 自动管理）
    cache.clear_all()


@pytest.fixture
def sample_crawled_domain():
    """创建测试用 CrawledDomain."""
    return CrawledDomain(
        domain="example.com",
        pages=[
            CrawledPage(
                url="https://example.com",
                title="测试公司官网",
                text="测试科技有限公司成立于2015年，总部位于北京。"
                     "主要产品包括测试产品A、测试产品B。"
                     "公司拥有ISO9001认证，服务超过500家企业客户。"
                     "注册资本1亿元。",
                status_code=200,
                crawled_at="2024-01-01T00:00:00Z",
            ),
            CrawledPage(
                url="https://example.com/products",
                title="产品中心",
                text="测试产品A是一款企业级数据分析平台。"
                     "测试产品B是智能决策系统。",
                status_code=200,
                crawled_at="2024-01-01T00:00:00Z",
            ),
        ],
        total_pages=2,
        success_pages=2,
        failed_pages=0,
        total_chars=500,
        crawled_at="2024-01-01T00:00:00Z",
    )


@pytest.fixture
def sample_evidence_store(sample_crawled_domain):
    """创建测试用 EvidenceStore."""
    return extract_evidence(sample_crawled_domain, "测试科技有限公司")


@pytest.fixture
def sample_submission_data():
    """创建测试用 Submission."""
    return Submission(
        task_name="2024年Q1产品推广",
        company_name="测试科技有限公司",
        product_or_service=["测试产品A"],
        core_topic="产品发布",
        key_points=["核心技术", "行业优势"],
        forbidden_claims=["行业第一", "最好"],
        official_urls=["https://example.com"],
    )


# ============ 公司名标准化测试 ============

class TestNormalizeCompanyName:
    def test_basic_normalization(self):
        assert _normalize_company_name("  测试科技有限公司  ") == "测试科技"

    def test_remove_suffix_股份有限公司(self):
        assert _normalize_company_name("中国银行股份有限公司") == "中国银行"

    def test_remove_suffix_有限责任公司(self):
        assert _normalize_company_name("某某有限责任公司") == "某某"

    def test_case_insensitive(self):
        assert _normalize_company_name("TestCompany") == "testcompany"

    def test_remove_special_chars(self):
        assert _normalize_company_name("测试-科技_有限公司") == "测试科技"

    def test_empty_string(self):
        assert _normalize_company_name("") == ""

    def test_none(self):
        assert _normalize_company_name(None) == ""


# ============ TTL 过期检测测试 ============

class TestIsExpired:
    def test_not_expired(self):
        ts = _now_iso()
        assert not _is_expired(ts, 24)

    def test_expired(self):
        ts = (datetime.utcnow() - timedelta(hours=25)).isoformat()
        assert _is_expired(ts, 24)

    def test_never_expire(self):
        """ttl=0 表示永不过期."""
        old_ts = (datetime.utcnow() - timedelta(days=365)).isoformat()
        assert not _is_expired(old_ts, 0)

    def test_empty_timestamp(self):
        """空时间戳，ttl>0 时视为过期."""
        assert _is_expired("", 24)

    def test_empty_timestamp_never_expire(self):
        """空时间戳，ttl=0 时永不过期."""
        assert not _is_expired("", 0)


# ============ 缓存读写测试 ============

class TestCacheReadWrite:
    def test_save_and_read_crawl_data(self, temp_cache, sample_crawled_domain):
        """测试保存和读取爬取数据."""
        company = "测试科技有限公司"
        urls = ["https://example.com"]

        # 保存
        temp_cache.save_crawl_data(company, sample_crawled_domain, urls)

        # 读取
        resources = temp_cache.get_resources(company, urls)
        assert "crawled_domain" in resources
        assert resources["crawled_domain"].domain == "example.com"
        assert resources["crawled_domain"].success_pages == 2
        assert resources["crawled_domain"].from_cache is True
        assert all(p.from_cache for p in resources["crawled_domain"].pages)

    def test_save_and_read_evidence(self, temp_cache, sample_crawled_domain, sample_evidence_store):
        """测试保存和读取结构化证据."""
        company = "测试科技有限公司"
        urls = ["https://example.com"]

        # 先保存爬取数据（证据缓存依赖爬取缓存命中）
        temp_cache.save_crawl_data(company, sample_crawled_domain, urls)
        # 保存证据
        temp_cache.save_evidence(company, sample_evidence_store)

        # 读取
        resources = temp_cache.get_resources(company, urls)
        assert "evidence_store" in resources
        assert resources["evidence_store"].domain == "example.com"
        assert resources["evidence_store"].company_name == "测试科技有限公司"

    def test_save_and_read_submission(self, temp_cache, sample_submission_data):
        """测试保存和读取提报表."""
        company = "测试科技有限公司"

        # 保存
        temp_cache.save_submission(company, sample_submission_data)

        # 读取
        resources = temp_cache.get_resources(company, sample_submission_data.official_urls)
        assert "submission" in resources
        assert resources["submission"].company_name == "测试科技有限公司"
        assert resources["submission"].task_name == "2024年Q1产品推广"

    def test_cache_miss_on_empty(self, temp_cache):
        """测试缓存未命中."""
        resources = temp_cache.get_resources("不存在的公司", ["https://nope.com"])
        assert "crawled_domain" not in resources
        assert "evidence_store" not in resources
        assert resources.get("cache_info", {}).get("hit") is False

    def test_cache_miss_on_url_change(self, temp_cache, sample_crawled_domain):
        """URL 变更时缓存未命中."""
        company = "测试科技有限公司"
        urls = ["https://example.com"]

        # 保存
        temp_cache.save_crawl_data(company, sample_crawled_domain, urls)

        # 使用不同 URL 读取
        new_urls = ["https://different.com"]
        resources = temp_cache.get_resources(company, new_urls)
        assert "crawled_domain" not in resources
        assert "crawled_domain" not in resources
        cache_info = resources.get("cache_info", {})
        assert cache_info.get("crawl_miss_reason") == "official_urls_changed"

    def test_cache_hit_on_url_superset(self, temp_cache, sample_crawled_domain):
        """请求的 URL 是缓存 URL 的子集时命中."""
        company = "测试科技有限公司"
        cached_urls = ["https://example.com", "https://other.com"]

        # 保存
        temp_cache.save_crawl_data(company, sample_crawled_domain, cached_urls)

        # 使用子集 URL 读取
        resources = temp_cache.get_resources(company, ["https://example.com"])
        assert "crawled_domain" in resources

    def test_partial_update(self, temp_cache, sample_crawled_domain, sample_evidence_store):
        """测试部分更新（先保存爬取，再保存证据）."""
        company = "测试科技有限公司"
        urls = ["https://example.com"]

        # 先保存爬取
        temp_cache.save_crawl_data(company, sample_crawled_domain, urls)
        # 再保存证据
        temp_cache.save_evidence(company, sample_evidence_store)

        # 两者都应存在
        resources = temp_cache.get_resources(company, urls)
        assert "crawled_domain" in resources
        assert "evidence_store" in resources


# ============ TTL 过期测试 ============

class TestCacheTTL:
    def test_crawl_data_expired(self, temp_cache, sample_crawled_domain):
        """测试爬取数据过期."""
        company = "测试科技有限公司"
        urls = ["https://example.com"]

        # 保存
        temp_cache.save_crawl_data(company, sample_crawled_domain, urls)

        # 模拟过期：直接修改数据库中的时间戳
        import sqlite3
        old_ts = (datetime.utcnow() - timedelta(hours=25)).isoformat()
        with temp_cache._get_conn() as conn:
            conn.execute(
                "UPDATE company_resources SET crawled_at = ? WHERE company_name_normalized = ?",
                (old_ts, _normalize_company_name(company)),
            )
            conn.commit()

        # 读取 — 应该未命中
        resources = temp_cache.get_resources(company, urls)
        assert "crawled_domain" not in resources

    def test_evidence_not_returned_without_crawl(self, temp_cache, sample_crawled_domain, sample_evidence_store):
        """爬取数据未命中时，证据也不返回（即使证据本身未过期）."""
        company = "测试科技有限公司"
        urls = ["https://example.com"]

        temp_cache.save_crawl_data(company, sample_crawled_domain, urls)
        temp_cache.save_evidence(company, sample_evidence_store)

        # 使用不同 URL 使爬取缓存未命中
        resources = temp_cache.get_resources(company, ["https://different.com"])
        assert "crawled_domain" not in resources
        assert "evidence_store" not in resources


# ============ 清除缓存测试 ============

class TestCacheClear:
    def test_clear_company(self, temp_cache, sample_crawled_domain):
        """测试清除单个公司缓存."""
        company = "测试科技有限公司"
        temp_cache.save_crawl_data(company, sample_crawled_domain, ["https://example.com"])

        success = temp_cache.clear_company(company)
        assert success is True

        resources = temp_cache.get_resources(company, ["https://example.com"])
        assert "crawled_domain" not in resources

    def test_clear_nonexistent_company(self, temp_cache):
        """清除不存在的公司."""
        success = temp_cache.clear_company("不存在的公司")
        assert success is False

    def test_clear_all(self, temp_cache, sample_crawled_domain):
        """测试清除全部缓存."""
        temp_cache.save_crawl_data("公司A", sample_crawled_domain, ["https://a.com"])
        temp_cache.save_crawl_data("公司B", sample_crawled_domain, ["https://b.com"])

        count = temp_cache.clear_all()
        assert count == 2

        stats = temp_cache.get_stats()
        assert stats["total_companies"] == 0

    def test_clear_expired(self, temp_cache, sample_crawled_domain, sample_submission_data):
        """测试清除过期缓存."""
        # 保存一条完整记录
        temp_cache.save_crawl_data("过期公司", sample_crawled_domain, ["https://example.com"])
        temp_cache.save_submission("过期公司", sample_submission_data)

        # 模拟全部过期
        import sqlite3
        old_ts = (datetime.utcnow() - timedelta(hours=200)).isoformat()
        with temp_cache._get_conn() as conn:
            conn.execute(
                "UPDATE company_resources SET crawled_at = ?, evidence_at = ?, submission_at = ? "
                "WHERE company_name_normalized = ?",
                (old_ts, old_ts, old_ts, _normalize_company_name("过期公司")),
            )
            conn.commit()

        # 清除过期
        cleared = temp_cache.clear_expired()
        assert cleared == 1

        # 确认已删除
        stats = temp_cache.get_stats()
        assert stats["total_companies"] == 0


# ============ 统计信息测试 ============

class TestCacheStats:
    def test_stats_empty(self, temp_cache):
        """空缓存统计."""
        stats = temp_cache.get_stats()
        assert stats["enabled"] is True
        assert stats["total_companies"] == 0
        assert stats["crawl_cache_count"] == 0

    def test_stats_with_data(self, temp_cache, sample_crawled_domain, sample_evidence_store, sample_submission_data):
        """有数据的缓存统计."""
        temp_cache.save_crawl_data("公司A", sample_crawled_domain, ["https://a.com"])
        temp_cache.save_evidence("公司A", sample_evidence_store)
        temp_cache.save_submission("公司A", sample_submission_data)

        stats = temp_cache.get_stats()
        assert stats["total_companies"] == 1
        assert stats["crawl_cache_count"] == 1
        assert stats["evidence_cache_count"] == 1
        assert stats["submission_cache_count"] == 1

    def test_company_list(self, temp_cache, sample_crawled_domain):
        """测试公司列表."""
        temp_cache.save_crawl_data("公司A", sample_crawled_domain, ["https://a.com"])
        temp_cache.save_crawl_data("公司B", sample_crawled_domain, ["https://b.com"])

        companies = temp_cache.get_cached_companies()
        assert len(companies) == 2

    def test_company_detail(self, temp_cache, sample_crawled_domain):
        """测试公司详情."""
        temp_cache.save_crawl_data("测试科技有限公司", sample_crawled_domain, ["https://example.com"])

        detail = temp_cache.get_company_detail("测试科技有限公司")
        assert detail is not None
        assert detail["company_name"] == "测试科技有限公司"
        assert detail["has_crawl_data"] is True

    def test_company_detail_not_found(self, temp_cache):
        """查询不存在的公司详情."""
        detail = temp_cache.get_company_detail("不存在的公司")
        assert detail is None


# ============ 禁用状态测试 ============

class TestCacheDisabled:
    def test_disabled_cache_no_ops(self, tmp_path):
        """禁用缓存时所有操作无效."""
        db_path = str(tmp_path / "disabled_cache.db")
        cache = CompanyResourceCache(db_path=db_path, enabled=False)
        cache.save_crawl_data("公司", CrawledDomain(domain="x.com"), ["https://x.com"])
        resources = cache.get_resources("公司", ["https://x.com"])
        assert resources == {}
        assert cache.get_stats()["enabled"] is False


# ============ URL 匹配测试 ============

class TestUrlMatch:
    def test_exact_match(self):
        urls1 = ["https://example.com", "https://test.com"]
        urls2 = ["https://example.com", "https://test.com"]
        assert CompanyResourceCache._urls_match(urls1, urls2)

    def test_order_independent(self):
        urls1 = ["https://a.com", "https://b.com"]
        urls2 = ["https://b.com", "https://a.com"]
        assert CompanyResourceCache._urls_match(urls1, urls2)

    def test_trailing_slash(self):
        urls1 = ["https://example.com/"]
        urls2 = ["https://example.com"]
        assert CompanyResourceCache._urls_match(urls1, urls2)

    def test_protocol_insensitive(self):
        urls1 = ["http://example.com"]
        urls2 = ["https://example.com"]
        assert CompanyResourceCache._urls_match(urls1, urls2)

    def test_subset_match(self):
        """请求 URL 是缓存 URL 的子集."""
        cached = ["https://a.com", "https://b.com", "https://c.com"]
        request = ["https://a.com"]
        assert CompanyResourceCache._urls_match(cached, request)

    def test_no_match(self):
        cached = ["https://a.com"]
        request = ["https://b.com"]
        assert not CompanyResourceCache._urls_match(cached, request)

    def test_empty_request(self):
        cached = ["https://a.com"]
        request = []
        assert not CompanyResourceCache._urls_match(cached, request)
