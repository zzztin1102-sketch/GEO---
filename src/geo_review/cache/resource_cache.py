"""CompanyResourceCache — 公司级资源缓存，支持持久化复用.

核心功能：
    1. 按公司名缓存官网爬取数据（CrawledDomain）、结构化证据（EvidenceStore）、提报表（Submission）
    2. SQLite 持久化 — 服务重启后缓存仍然有效
    3. TTL 过期机制 — 可配置各类资源的过期时间
    4. 线程安全 — 使用 threading.Lock 保证并发安全
    5. 官网 URL 变更检测 — URL 变化时自动失效旧缓存

使用场景：
    当同一公司多次提交审核时，跳过重复的官网爬取和证据提取，
    让 LLM 专注于语义审核和事实信息核实，大幅节省算力和时间。
"""

import json
import logging
import os
import re
import sqlite3
import threading
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from geo_review.models import CrawledDomain, CrawledPage, Submission
from geo_review.tools.evidence_store import EvidenceItem, EvidenceStore

logger = logging.getLogger(__name__)


def _normalize_company_name(name: str) -> str:
    """将公司名标准化为缓存键.

    规则：
        1. 去除首尾空白
        2. 转小写
        3. 去除常见后缀（有限公司、股份有限公司等）
        4. 去除所有空白和特殊字符
    """
    if not name:
        return ""
    name = name.strip().lower()
    # 去除常见公司后缀
    suffixes = [
        "股份有限公司", "有限责任公司", "有限公司",
        "集团有限公司", "科技有限公司", "集团",
        "公司", "（集团）", "(集团)",
    ]
    for suffix in suffixes:
        if name.endswith(suffix.lower()):
            name = name[: -len(suffix)]
            break
    # 去除所有空白和特殊字符
    name = re.sub(r"[\s\u3000\(\)（）\-_·・]", "", name)
    return name


def _now_iso() -> str:
    """当前 UTC 时间 ISO 格式."""
    return datetime.utcnow().isoformat()


def _parse_iso(ts_str: str) -> Optional[datetime]:
    """解析 ISO 时间字符串."""
    if not ts_str:
        return None
    try:
        return datetime.fromisoformat(ts_str)
    except (ValueError, TypeError):
        return None


def _is_expired(ts_str: str, ttl_hours: int) -> bool:
    """检查时间戳是否已过期."""
    if not ts_str or ttl_hours <= 0:
        return ttl_hours > 0  # ttl=0 表示永不过期
    ts = _parse_iso(ts_str)
    if ts is None:
        return True
    return datetime.utcnow() - ts > timedelta(hours=ttl_hours)


# -- 序列化 / 反序列化辅助函数 --

def _serialize_crawled_domain(crawled: CrawledDomain) -> str:
    """序列化 CrawledDomain 为 JSON 字符串."""
    return crawled.model_dump_json()


def _deserialize_crawled_domain(json_str: str) -> Optional[CrawledDomain]:
    """从 JSON 字符串反序列化 CrawledDomain."""
    try:
        return CrawledDomain.model_validate_json(json_str)
    except Exception as e:
        logger.warning(f"反序列化 CrawledDomain 失败: {e}")
        return None


def _serialize_evidence_store(store: EvidenceStore) -> str:
    """序列化 EvidenceStore 为 JSON 字符串."""
    data = asdict(store)
    return json.dumps(data, ensure_ascii=False)


def _deserialize_evidence_store(json_str: str) -> Optional[EvidenceStore]:
    """从 JSON 字符串反序列化 EvidenceStore."""
    try:
        data = json.loads(json_str)
        # 重建嵌套的 EvidenceItem 列表
        for key in ("key_data", "company_facts", "product_facts"):
            if key in data and data[key]:
                data[key] = [EvidenceItem(**item) for item in data[key]]
        return EvidenceStore(**data)
    except Exception as e:
        logger.warning(f"反序列化 EvidenceStore 失败: {e}")
        return None


def _serialize_submission(submission: Submission) -> str:
    """序列化 Submission 为 JSON 字符串."""
    return submission.model_dump_json()


def _deserialize_submission(json_str: str) -> Optional[Submission]:
    """从 JSON 字符串反序列化 Submission."""
    try:
        return Submission.model_validate_json(json_str)
    except Exception as e:
        logger.warning(f"反序列化 Submission 失败: {e}")
        return None


class CompanyResourceCache:
    """公司级资源缓存 — 持久化复用官网爬取和提报表解析结果.

    用法::

        cache = CompanyResourceCache(db_path="resource_cache.db")

        # 尝试获取缓存资源
        resources = cache.get_resources("某科技有限公司", official_urls=["https://example.com"])
        if resources:
            crawled = resources["crawled_domain"]
            evidence = resources["evidence_store"]
            # 直接使用缓存数据，跳过爬取和提取

        # 保存资源到缓存
        cache.save_crawl_data("某科技有限公司", crawled, official_urls)
        cache.save_evidence("某科技有限公司", evidence_store)
    """

    def __init__(
        self,
        db_path: str = "resource_cache.db",
        submission_ttl_hours: int = 168,  # 7 天
        crawl_ttl_hours: int = 24,        # 1 天
        evidence_ttl_hours: int = 24,     # 1 天
        enabled: bool = True,
    ):
        self.db_path = db_path
        self.submission_ttl_hours = submission_ttl_hours
        self.crawl_ttl_hours = crawl_ttl_hours
        self.evidence_ttl_hours = evidence_ttl_hours
        self.enabled = enabled
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        """初始化 SQLite 数据库表."""
        try:
            with self._get_conn() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS company_resources (
                        company_name_normalized  TEXT PRIMARY KEY,
                        company_name_display    TEXT NOT NULL,
                        official_urls           TEXT NOT NULL,
                        submission_json         TEXT,
                        crawled_domain_json     TEXT,
                        evidence_store_json     TEXT,
                        submission_at           TEXT,
                        crawled_at              TEXT,
                        evidence_at             TEXT,
                        created_at              TEXT NOT NULL,
                        updated_at              TEXT NOT NULL
                    )
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_company_display
                    ON company_resources(company_name_display)
                """)
                conn.commit()
            logger.debug(f"资源缓存数据库已初始化: {self.db_path}")
        except Exception as e:
            logger.error(f"初始化资源缓存数据库失败: {e}")
            self.enabled = False

    def _get_conn(self) -> sqlite3.Connection:
        """获取 SQLite 连接（每次创建新连接，避免线程问题）."""
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    # ============ 读取缓存 ============

    def get_resources(
        self,
        company_name: str,
        official_urls: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """获取公司的缓存资源.

        Args:
            company_name: 公司名称
            official_urls: 当前请求的官网 URL 列表（用于验证缓存有效性）

        Returns:
            dict: 可包含以下键:
                - "crawled_domain": CrawledDomain（缓存命中且未过期）
                - "evidence_store": EvidenceStore（缓存命中且未过期）
                - "submission": Submission（缓存命中且未过期）
                - "cache_info": 缓存命中详情
            如果缓存未命中或已过期，对应键不会出现在返回值中
        """
        if not self.enabled or not company_name:
            return {}

        key = _normalize_company_name(company_name)
        if not key:
            return {}

        with self._lock:
            try:
                with self._get_conn() as conn:
                    row = conn.execute(
                        "SELECT * FROM company_resources WHERE company_name_normalized = ?",
                        (key,),
                    ).fetchone()
            except Exception as e:
                logger.warning(f"读取缓存失败: {e}")
                return {}

        if not row:
            return {
                "cache_info": {
                    "company_name": company_name,
                    "hit": False,
                    "crawl_miss_reason": "not_cached",
                }
            }

        result: Dict[str, Any] = {}
        cache_info: Dict[str, Any] = {
            "company_name": row["company_name_display"],
            "hit": False,
        }

        # 检查爬取数据缓存
        crawled_json = row["crawled_domain_json"]
        crawled_at = row["crawled_at"] or ""
        if crawled_json and not _is_expired(crawled_at, self.crawl_ttl_hours):
            # 验证 official_urls 是否匹配
            cached_urls = json.loads(row["official_urls"]) if row["official_urls"] else []
            if official_urls and self._urls_match(cached_urls, official_urls):
                crawled = _deserialize_crawled_domain(crawled_json)
                if crawled:
                    crawled.from_cache = True
                    for page in crawled.pages:
                        page.from_cache = True
                    result["crawled_domain"] = crawled
                    cache_info["crawl_hit"] = True
                    cache_info["crawled_at"] = crawled_at
                    logger.info(f"缓存命中 [官网爬取]: {company_name} (爬取于 {crawled_at})")
            elif official_urls:
                cache_info["crawl_miss_reason"] = "official_urls_changed"
                logger.info(f"缓存未命中 [官网爬取]: {company_name} (URL 已变更)")
        elif crawled_json:
            cache_info["crawl_miss_reason"] = "expired"
        else:
            cache_info["crawl_miss_reason"] = "not_cached"

        # 检查证据缓存（只有爬取数据也命中时才使用证据缓存）
        if "crawled_domain" in result:
            evidence_json = row["evidence_store_json"]
            evidence_at = row["evidence_at"] or ""
            if evidence_json and not _is_expired(evidence_at, self.evidence_ttl_hours):
                evidence = _deserialize_evidence_store(evidence_json)
                if evidence:
                    result["evidence_store"] = evidence
                    cache_info["evidence_hit"] = True
                    cache_info["evidence_at"] = evidence_at
                    logger.info(f"缓存命中 [结构化证据]: {company_name} (提取于 {evidence_at})")

        # 检查提报表缓存
        submission_json = row["submission_json"]
        submission_at = row["submission_at"] or ""
        if submission_json and not _is_expired(submission_at, self.submission_ttl_hours):
            submission = _deserialize_submission(submission_json)
            if submission:
                result["submission"] = submission
                cache_info["submission_hit"] = True
                cache_info["submission_at"] = submission_at

        cache_info["hit"] = bool(result)
        result["cache_info"] = cache_info
        return result

    def get_cached_companies(self) -> List[Dict[str, Any]]:
        """获取所有缓存的公司列表."""
        if not self.enabled:
            return []
        with self._lock:
            try:
                with self._get_conn() as conn:
                    rows = conn.execute(
                        "SELECT company_name_display, official_urls, "
                        "crawled_at, evidence_at, submission_at, updated_at "
                        "FROM company_resources ORDER BY updated_at DESC"
                    ).fetchall()
            except Exception as e:
                logger.warning(f"读取缓存列表失败: {e}")
                return []

        result = []
        for row in rows:
            entry = {
                "company_name": row["company_name_display"],
                "official_urls": json.loads(row["official_urls"]) if row["official_urls"] else [],
                "has_crawl_data": bool(row["crawled_at"]),
                "crawled_at": row["crawled_at"],
                "has_evidence": bool(row["evidence_at"]),
                "evidence_at": row["evidence_at"],
                "has_submission": bool(row["submission_at"]),
                "submission_at": row["submission_at"],
                "updated_at": row["updated_at"],
            }
            result.append(entry)
        return result

    def get_company_detail(self, company_name: str) -> Optional[Dict[str, Any]]:
        """获取指定公司的缓存详情."""
        if not self.enabled or not company_name:
            return None
        key = _normalize_company_name(company_name)
        with self._lock:
            try:
                with self._get_conn() as conn:
                    row = conn.execute(
                        "SELECT * FROM company_resources WHERE company_name_normalized = ?",
                        (key,),
                    ).fetchone()
            except Exception as e:
                logger.warning(f"读取缓存详情失败: {e}")
                return None

        if not row:
            return None

        return {
            "company_name": row["company_name_display"],
            "official_urls": json.loads(row["official_urls"]) if row["official_urls"] else [],
            "has_crawl_data": bool(row["crawled_domain_json"]),
            "crawled_at": row["crawled_at"],
            "has_evidence": bool(row["evidence_store_json"]),
            "evidence_at": row["evidence_at"],
            "has_submission": bool(row["submission_json"]),
            "submission_at": row["submission_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "crawl_expired": _is_expired(row["crawled_at"] or "", self.crawl_ttl_hours),
            "evidence_expired": _is_expired(row["evidence_at"] or "", self.evidence_ttl_hours),
            "submission_expired": _is_expired(row["submission_at"] or "", self.submission_ttl_hours),
        }

    # ============ 写入缓存 ============

    def save_crawl_data(
        self,
        company_name: str,
        crawled: CrawledDomain,
        official_urls: List[str],
    ):
        """保存官网爬取数据到缓存."""
        if not self.enabled or not company_name or not crawled:
            return
        key = _normalize_company_name(company_name)
        if not key:
            return

        crawled_json = _serialize_crawled_domain(crawled)
        now = _now_iso()

        with self._lock:
            try:
                with self._get_conn() as conn:
                    self._upsert(
                        conn, key, company_name,
                        official_urls_json=json.dumps(official_urls, ensure_ascii=False),
                        crawled_domain_json=crawled_json,
                        crawled_at=now,
                    )
                    conn.commit()
                logger.info(f"缓存已保存 [官网爬取]: {company_name} ({crawled.success_pages} 页)")
            except Exception as e:
                logger.warning(f"保存爬取缓存失败: {e}")

    def save_evidence(
        self,
        company_name: str,
        evidence_store: EvidenceStore,
    ):
        """保存结构化证据到缓存."""
        if not self.enabled or not company_name or not evidence_store:
            return
        key = _normalize_company_name(company_name)
        if not key:
            return

        evidence_json = _serialize_evidence_store(evidence_store)
        now = _now_iso()

        with self._lock:
            try:
                with self._get_conn() as conn:
                    self._upsert(
                        conn, key, company_name,
                        evidence_store_json=evidence_json,
                        evidence_at=now,
                    )
                    conn.commit()
                logger.info(f"缓存已保存 [结构化证据]: {company_name}")
            except Exception as e:
                logger.warning(f"保存证据缓存失败: {e}")

    def save_submission(
        self,
        company_name: str,
        submission: Submission,
        official_urls: Optional[List[str]] = None,
    ):
        """保存提报表到缓存."""
        if not self.enabled or not company_name or not submission:
            return
        key = _normalize_company_name(company_name)
        if not key:
            return

        submission_json = _serialize_submission(submission)
        now = _now_iso()
        urls_json = json.dumps(official_urls or submission.official_urls, ensure_ascii=False)

        with self._lock:
            try:
                with self._get_conn() as conn:
                    self._upsert(
                        conn, key, company_name,
                        official_urls_json=urls_json,
                        submission_json=submission_json,
                        submission_at=now,
                    )
                    conn.commit()
                logger.info(f"缓存已保存 [提报表]: {company_name}")
            except Exception as e:
                logger.warning(f"保存提报表缓存失败: {e}")

    def _upsert(
        self,
        conn: sqlite3.Connection,
        key: str,
        display_name: str,
        official_urls_json: Optional[str] = None,
        crawled_domain_json: Optional[str] = None,
        evidence_store_json: Optional[str] = None,
        submission_json: Optional[str] = None,
        crawled_at: Optional[str] = None,
        evidence_at: Optional[str] = None,
        submission_at: Optional[str] = None,
    ):
        """插入或更新缓存记录（部分更新）."""
        now = _now_iso()

        # 先检查记录是否存在
        row = conn.execute(
            "SELECT company_name_normalized FROM company_resources WHERE company_name_normalized = ?",
            (key,),
        ).fetchone()

        if row:
            # 更新已有记录
            updates = []
            params = []
            if official_urls_json is not None:
                updates.append("official_urls = ?")
                params.append(official_urls_json)
            if crawled_domain_json is not None:
                updates.append("crawled_domain_json = ?")
                params.append(crawled_domain_json)
            if evidence_store_json is not None:
                updates.append("evidence_store_json = ?")
                params.append(evidence_store_json)
            if submission_json is not None:
                updates.append("submission_json = ?")
                params.append(submission_json)
            if crawled_at is not None:
                updates.append("crawled_at = ?")
                params.append(crawled_at)
            if evidence_at is not None:
                updates.append("evidence_at = ?")
                params.append(evidence_at)
            if submission_at is not None:
                updates.append("submission_at = ?")
                params.append(submission_at)
            updates.append("updated_at = ?")
            params.append(now)
            params.append(key)
            conn.execute(
                f"UPDATE company_resources SET {', '.join(updates)} WHERE company_name_normalized = ?",
                params,
            )
        else:
            # 插入新记录
            conn.execute(
                """INSERT INTO company_resources
                   (company_name_normalized, company_name_display, official_urls,
                    crawled_domain_json, evidence_store_json, submission_json,
                    crawled_at, evidence_at, submission_at, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    key, display_name,
                    official_urls_json or "[]",
                    crawled_domain_json,
                    evidence_store_json,
                    submission_json,
                    crawled_at,
                    evidence_at,
                    submission_at,
                    now, now,
                ),
            )

    # ============ 清除缓存 ============

    def clear_company(self, company_name: str) -> bool:
        """清除指定公司的缓存."""
        if not self.enabled or not company_name:
            return False
        key = _normalize_company_name(company_name)
        with self._lock:
            try:
                with self._get_conn() as conn:
                    cursor = conn.execute(
                        "DELETE FROM company_resources WHERE company_name_normalized = ?",
                        (key,),
                    )
                    conn.commit()
                    deleted = cursor.rowcount > 0
                if deleted:
                    logger.info(f"缓存已清除: {company_name}")
                return deleted
            except Exception as e:
                logger.warning(f"清除缓存失败: {e}")
                return False

    def clear_all(self) -> int:
        """清除所有缓存，返回清除的记录数."""
        if not self.enabled:
            return 0
        with self._lock:
            try:
                with self._get_conn() as conn:
                    cursor = conn.execute("DELETE FROM company_resources")
                    conn.commit()
                    count = cursor.rowcount
                logger.info(f"全部缓存已清除: {count} 条记录")
                return count
            except Exception as e:
                logger.warning(f"清除全部缓存失败: {e}")
                return 0

    def clear_expired(self) -> int:
        """清除所有已过期的缓存数据，返回清除的记录数."""
        if not self.enabled:
            return 0
        with self._lock:
            try:
                with self._get_conn() as conn:
                    rows = conn.execute(
                        "SELECT company_name_normalized, crawled_at, evidence_at, submission_at "
                        "FROM company_resources"
                    ).fetchall()

                    expired_keys = []
                    for row in rows:
                        crawl_expired = _is_expired(row["crawled_at"] or "", self.crawl_ttl_hours)
                        evidence_expired = _is_expired(row["evidence_at"] or "", self.evidence_ttl_hours)
                        submission_expired = _is_expired(row["submission_at"] or "", self.submission_ttl_hours)

                        # 如果所有资源都过期了，清除整条记录
                        if crawl_expired and evidence_expired and submission_expired:
                            expired_keys.append(row["company_name_normalized"])
                        # 否则只清除过期的字段
                        elif crawl_expired:
                            conn.execute(
                                "UPDATE company_resources SET crawled_domain_json = NULL, crawled_at = NULL "
                                "WHERE company_name_normalized = ?",
                                (row["company_name_normalized"],),
                            )
                        if evidence_expired:
                            conn.execute(
                                "UPDATE company_resources SET evidence_store_json = NULL, evidence_at = NULL "
                                "WHERE company_name_normalized = ?",
                                (row["company_name_normalized"],),
                            )

                    for key in expired_keys:
                        conn.execute(
                            "DELETE FROM company_resources WHERE company_name_normalized = ?",
                            (key,),
                        )

                    conn.commit()
                    cleared = len(expired_keys)
                if cleared > 0:
                    logger.info(f"已清除 {cleared} 条过期缓存记录")
                return cleared
            except Exception as e:
                logger.warning(f"清除过期缓存失败: {e}")
                return 0

    # ============ 统计信息 ============

    def get_stats(self) -> Dict[str, Any]:
        """获取缓存统计信息."""
        if not self.enabled:
            return {"enabled": False, "total_companies": 0}

        with self._lock:
            try:
                with self._get_conn() as conn:
                    row = conn.execute(
                        "SELECT COUNT(*) as total, "
                        "COALESCE(SUM(CASE WHEN crawled_domain_json IS NOT NULL THEN 1 ELSE 0 END), 0) as crawl_count, "
                        "COALESCE(SUM(CASE WHEN evidence_store_json IS NOT NULL THEN 1 ELSE 0 END), 0) as evidence_count, "
                        "COALESCE(SUM(CASE WHEN submission_json IS NOT NULL THEN 1 ELSE 0 END), 0) as submission_count "
                        "FROM company_resources"
                    ).fetchone()

                    # 数据库文件大小
                    db_size = 0
                    if os.path.exists(self.db_path):
                        db_size = os.path.getsize(self.db_path)

                    return {
                        "enabled": True,
                        "db_path": self.db_path,
                        "db_size_bytes": db_size,
                        "db_size_mb": round(db_size / 1024 / 1024, 2) if db_size > 0 else 0,
                        "total_companies": row["total"] if row else 0,
                        "crawl_cache_count": row["crawl_count"] if row else 0,
                        "evidence_cache_count": row["evidence_count"] if row else 0,
                        "submission_cache_count": row["submission_count"] if row else 0,
                        "ttl_hours": {
                            "crawl": self.crawl_ttl_hours,
                            "evidence": self.evidence_ttl_hours,
                            "submission": self.submission_ttl_hours,
                        },
                    }
            except Exception as e:
                logger.warning(f"获取缓存统计失败: {e}")
                return {"enabled": True, "error": str(e), "total_companies": 0}

    # ============ 工具方法 ============

    @staticmethod
    def _urls_match(cached_urls: List[str], request_urls: List[str]) -> bool:
        """检查两组 URL 是否匹配（忽略顺序和尾部斜杠）."""
        def _normalize_url(url: str) -> str:
            url = url.strip().lower().rstrip("/")
            # 移除协议前缀（http 和 https 视为相同）
            if url.startswith("https://"):
                url = url[8:]
            elif url.startswith("http://"):
                url = url[7:]
            return url

        cached_set = {_normalize_url(u) for u in cached_urls if u}
        request_set = {_normalize_url(u) for u in request_urls if u}

        # 请求的 URL 全部在缓存中即可（允许缓存包含更多 URL）
        return request_set.issubset(cached_set) if request_set else False
