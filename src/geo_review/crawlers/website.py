"""官网爬虫 — 支持静态/动态页面爬取，全局缓存复用."""

import logging
import re
import threading
import time
from geo_review.utils.time import now as beijing_now
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urljoin, urlparse

from geo_review.config.models import CrawlerConfig
from geo_review.models import CrawledDomain, CrawledPage

logger = logging.getLogger(__name__)


# 全局缓存：{domain: CrawledDomain}
_CACHE_LOCK = threading.Lock()
_CACHE: Dict[str, CrawledDomain] = {}


def _extract_domain(url: str) -> str:
    """从 URL 提取域名."""
    parsed = urlparse(url)
    domain = parsed.netloc or parsed.path
    # 去除端口号
    if ":" in domain:
        domain = domain.split(":")[0]
    return domain.lower()


def _normalize_url(url: str) -> str:
    """标准化 URL（去除 fragment、排序查询参数等）."""
    parsed = urlparse(url)
    # 去除 fragment
    normalized = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    if parsed.query:
        # 简单排序查询参数
        params = sorted(parsed.query.split("&"))
        normalized += "?" + "&".join(params)
    return normalized.rstrip("/")


class WebsiteCrawler:
    """官网爬虫 — 统一入口，支持全局缓存复用.

    核心特性:
        - 全局缓存：同一域名内容在进程内复用，避免重复爬取
        - 双引擎：静态页面用 requests，动态页面用 Playwright
        - 自动降级：动态爬取失败时降级为静态爬取
        - 内容清洗：去除脚本/样式/广告等噪声
        - 配置驱动：通过 configure() 注入 CrawlerConfig，未注入时使用内置默认值

    使用方式:
        # 启动时一次性注入配置
        WebsiteCrawler.configure(config.crawler)

        # 单域名爬取（使用配置的 max_pages、timeout、use_playwright）
        result = WebsiteCrawler.crawl("https://example.com")

        # 多域名批量爬取
        results = WebsiteCrawler.crawl_multiple([
            "https://example.com",
            "https://other.com"
        ])

        # 清除缓存
        WebsiteCrawler.clear_cache()
    """

    # 类级配置（启动时通过 configure() 注入；未注入时使用内置默认 CrawlerConfig）
    _config: CrawlerConfig = CrawlerConfig()

    @classmethod
    def configure(cls, config: CrawlerConfig) -> None:
        """注入爬虫配置（建议在应用启动时调用一次）.

        未调用时使用 CrawlerConfig() 的默认值（max_pages=5, timeout=30, use_playwright=True）。
        """
        cls._config = config

    @classmethod
    def _get_config(cls) -> CrawlerConfig:
        """获取当前生效的爬虫配置（懒加载保护，避免 import 时序问题）."""
        return cls._config

    # ------------------------------------------------------------------
    # 公共入口
    # ------------------------------------------------------------------
    @classmethod
    def crawl(
        cls,
        start_url: str,
        *,
        max_pages: Optional[int] = None,
        timeout: Optional[int] = None,
        use_dynamic: Optional[bool] = None,
        allowed_patterns: Optional[List[str]] = None,
        excluded_patterns: Optional[List[str]] = None,
        user_agent: Optional[str] = None,
        ignore_cache: bool = False,
    ) -> CrawledDomain:
        """爬取单个官网域名.

        Args:
            start_url: 起始 URL（如首页）
            max_pages: 最大爬取页面数（None 时使用 CrawlerConfig.max_pages）
            timeout: 单页超时秒数（None 时使用 CrawlerConfig.timeout）
            use_dynamic: 是否使用动态爬取（None 时使用 CrawlerConfig.use_playwright）
            allowed_patterns: 允许的 URL 正则列表（如 ["product", "about"]）
            excluded_patterns: 排除的 URL 正则列表（如 ["login", "admin"]）
            user_agent: 自定义 User-Agent
            ignore_cache: 是否忽略缓存（强制重新爬取）

        Returns:
            CrawledDomain 对象（包含页面列表、统计信息等）

        Raises:
            RuntimeError: 当 CrawlerConfig.enabled=False 时拒绝爬取
        """
        cfg = cls._get_config()
        if not cfg.enabled:
            raise RuntimeError(
                "官网爬取已禁用（crawler.enabled=false）。如需启用请修改配置或显式传入 ignore_cache=False 并配置 enabled=true。"
            )

        # 用配置覆盖 None 入参（显式传值优先级最高）
        max_pages = max_pages if max_pages is not None else cfg.max_pages
        timeout = timeout if timeout is not None else cfg.timeout
        use_dynamic = use_dynamic if use_dynamic is not None else cfg.use_playwright

        domain = _extract_domain(start_url)

        # 1) 检查缓存
        if not ignore_cache:
            with _CACHE_LOCK:
                if domain in _CACHE:
                    cached = _CACHE[domain]
                    # 标记为来自缓存
                    cached.from_cache = True
                    for page in cached.pages:
                        page.from_cache = True
                    return cached

        # 2) 执行爬取（受全局并发信号量保护，防止 Playwright 浏览器实例过多）
        from geo_review.utils.concurrency import ConcurrencyManager
        cm = ConcurrencyManager.get_instance()

        with cm.crawl_context(timeout=60.0) as acquired:
            if not acquired:
                # 并发槽位获取超时 — 降级为静态爬取
                logger.warning(f"爬虫并发已满，降级为静态爬取: {domain}")
                use_dynamic = False

            crawled_at = beijing_now().isoformat()
            result = cls._crawl_domain(
                start_url=start_url,
                domain=domain,
                max_pages=max_pages,
                timeout=timeout,
                use_dynamic=use_dynamic,
                allowed_patterns=allowed_patterns,
                excluded_patterns=excluded_patterns,
                user_agent=user_agent,
            )
            result.crawled_at = crawled_at

        # 3) 写入缓存
        with _CACHE_LOCK:
            _CACHE[domain] = result

        return result

    @classmethod
    def crawl_multiple(
        cls,
        urls: List[str],
        *,
        max_pages_per_domain: int = 10,
        timeout: int = 30,
        use_dynamic: bool = True,
        ignore_cache: bool = False,
    ) -> Dict[str, CrawledDomain]:
        """批量爬取多个域名.

        Args:
            urls: URL 列表
            max_pages_per_domain: 每个域名最大页面数
            timeout: 单页超时
            use_dynamic: 是否使用动态爬取
            ignore_cache: 是否忽略缓存

        Returns:
            {domain: CrawledDomain} 字典
        """
        results: Dict[str, CrawledDomain] = {}

        # 按域名分组
        domain_urls: Dict[str, str] = {}
        for url in urls:
            domain = _extract_domain(url)
            if domain not in domain_urls:
                domain_urls[domain] = url

        for domain, start_url in domain_urls.items():
            results[domain] = cls.crawl(
                start_url,
                max_pages=max_pages_per_domain,
                timeout=timeout,
                use_dynamic=use_dynamic,
                ignore_cache=ignore_cache,
            )

        return results

    @classmethod
    def get_cached(cls, domain: str) -> Optional[CrawledDomain]:
        """获取指定域名的缓存结果（不重新爬取）."""
        with _CACHE_LOCK:
            return _CACHE.get(domain)

    @classmethod
    def clear_cache(cls, domain: Optional[str] = None) -> None:
        """清除缓存.

        Args:
            domain: 指定域名（None 表示清空全部）
        """
        with _CACHE_LOCK:
            if domain:
                _CACHE.pop(domain, None)
            else:
                _CACHE.clear()

    @classmethod
    def get_cache_stats(cls) -> Dict[str, Any]:
        """获取缓存统计信息."""
        with _CACHE_LOCK:
            return {
                "cached_domains": len(_CACHE),
                "total_pages": sum(d.total_pages for d in _CACHE.values()),
                "total_chars": sum(d.total_chars for d in _CACHE.values()),
            }

    # ------------------------------------------------------------------
    # 内部爬取逻辑
    # ------------------------------------------------------------------
    @classmethod
    def _crawl_domain(
        cls,
        start_url: str,
        domain: str,
        max_pages: int,
        timeout: int,
        use_dynamic: bool,
        allowed_patterns: Optional[List[str]],
        excluded_patterns: Optional[List[str]],
        user_agent: Optional[str],
    ) -> CrawledDomain:
        """执行域名爬取（复用单个 Playwright 浏览器实例，消除每页重启开销）."""
        result = CrawledDomain(domain=domain)

        visited: Set[str] = set()
        queue: List[str] = [_normalize_url(start_url)]

        # 创建共享的 Playwright 浏览器实例（整个 domain 爬取过程只启动一次）
        playwright_ctx = None
        browser = None
        own_browser = False
        if use_dynamic:
            try:
                from playwright.sync_api import sync_playwright

                playwright_ctx = sync_playwright().start()
                browser = playwright_ctx.chromium.launch(
                    headless=True, args=["--no-sandbox", "--disable-gpu"]
                )
                own_browser = True
            except Exception:
                # Playwright 初始化失败，降级为静态爬取
                browser = None
                own_browser = False
                if playwright_ctx is not None:
                    try:
                        playwright_ctx.stop()
                    except Exception:
                        pass
                    playwright_ctx = None

        try:
            while queue and len(visited) < max_pages:
                current_url = queue.pop(0)
                normalized = _normalize_url(current_url)

                if normalized in visited:
                    continue

                # 检查 URL 过滤规则
                if not cls._is_url_allowed(current_url, domain, allowed_patterns, excluded_patterns):
                    continue

                visited.add(normalized)

                # 尝试爬取（复用共享 browser）
                page_result = cls._crawl_single_page(
                    url=current_url,
                    timeout=timeout,
                    use_dynamic=use_dynamic,
                    user_agent=user_agent,
                    browser=browser,
                )

                result.pages.append(page_result)
                result.total_pages += 1

                if page_result.error:
                    result.failed_pages += 1
                else:
                    result.success_pages += 1
                    result.total_chars += len(page_result.text)

                    # 从成功页面提取新链接
                    new_links = cls._extract_links_from_page(page_result, domain)
                    for link in new_links:
                        norm_link = _normalize_url(link)
                        if norm_link not in visited and norm_link not in queue:
                            queue.append(link)
        finally:
            # 统一关闭共享浏览器实例
            if own_browser:
                try:
                    if browser is not None:
                        browser.close()
                except Exception:
                    pass
                try:
                    if playwright_ctx is not None:
                        playwright_ctx.stop()
                except Exception:
                    pass

        return result

    @classmethod
    def _crawl_single_page(
        cls,
        url: str,
        timeout: int,
        use_dynamic: bool,
        user_agent: Optional[str],
        browser: Optional[Any] = None,
    ) -> CrawledPage:
        """爬取单个页面.

        Args:
            browser: 可选的共享 Playwright 浏览器实例。传入时复用，避免重启浏览器；
                     为 None 且 use_dynamic=True 时自建一次性浏览器（兼容旧调用）。
        """
        crawled_at = beijing_now().isoformat()

        # 优先尝试动态爬取
        if use_dynamic:
            try:
                return cls._crawl_with_playwright(url, timeout, user_agent, crawled_at, browser=browser)
            except Exception:
                # 动态爬取失败，降级为静态爬取
                logger.debug(f"动态爬取失败，降级为静态: {url}")

        # 静态爬取
        try:
            return cls._crawl_with_requests(url, timeout, user_agent, crawled_at)
        except Exception as static_exc:
            return CrawledPage(
                url=url,
                crawled_at=crawled_at,
                from_cache=False,
                error=f"爬取失败: {static_exc}",
            )

    @staticmethod
    def _crawl_with_requests(
        url: str,
        timeout: int,
        user_agent: Optional[str],
        crawled_at: str,
    ) -> CrawledPage:
        """使用 requests 爬取静态页面."""
        import requests
        from bs4 import BeautifulSoup

        headers = {
            "User-Agent": user_agent or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }

        response = requests.get(url, headers=headers, timeout=timeout, verify=True)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        # 提取标题
        title = soup.title.string.strip() if soup.title and soup.title.string else None

        # 提取并清洗文本
        text = WebsiteCrawler._extract_text_from_soup(soup)

        return CrawledPage(
            url=url,
            title=title,
            text=text,
            html=response.text,
            status_code=response.status_code,
            crawled_at=crawled_at,
            from_cache=False,
        )

    @staticmethod
    def _crawl_with_playwright(
        url: str,
        timeout: int,
        user_agent: Optional[str],
        crawled_at: str,
        browser: Optional[Any] = None,
    ) -> CrawledPage:
        """使用 Playwright 爬取动态页面.

        Args:
            browser: 可选的共享浏览器实例。传入时复用；为 None 时自建一次性浏览器。
        """
        from playwright.sync_api import sync_playwright

        own_playwright = False
        playwright_ctx = None
        try:
            if browser is None:
                # 兼容旧调用：自建一次性浏览器实例
                playwright_ctx = sync_playwright().start()
                browser = playwright_ctx.chromium.launch(
                    headless=True, args=["--no-sandbox", "--disable-gpu"]
                )
                own_playwright = True

            context = browser.new_context(
                user_agent=user_agent or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            )
            page = context.new_page()

            try:
                # 使用 domcontentloaded 替代 networkidle，大幅减少等待时间
                response = page.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")

                if not response:
                    raise ValueError("页面加载失败")

                # 短暂等待动态内容渲染（1秒替代原0.5秒+wait_for_load_state双等待）
                time.sleep(1)

                # 提取标题
                title = page.title()

                # 提取文本
                text = page.evaluate("""() => {
                    // 移除脚本、样式、导航等噪声
                    const elementsToRemove = document.querySelectorAll('script, style, nav, header, footer, aside, iframe, noscript');
                    elementsToRemove.forEach(el => el.remove());

                    // 提取可见文本
                    const body = document.body;
                    return body ? body.innerText : '';
                }""")

                # 清洗文本
                text = WebsiteCrawler._clean_text(text)

                # 获取 HTML
                html = page.content()

                return CrawledPage(
                    url=url,
                    title=title,
                    text=text,
                    html=html,
                    status_code=response.status,
                    crawled_at=crawled_at,
                    from_cache=False,
                )
            finally:
                try:
                    context.close()
                except Exception:
                    pass
        finally:
            if own_playwright:
                try:
                    if browser is not None:
                        browser.close()
                except Exception:
                    pass
                try:
                    if playwright_ctx is not None:
                        playwright_ctx.stop()
                except Exception:
                    pass

    @staticmethod
    def _extract_text_from_soup(soup) -> str:
        """从 BeautifulSoup 对象提取并清洗文本."""
        from bs4 import Comment, NavigableString, Tag

        # 移除噪声元素
        for tag in soup.find_all(["script", "style", "nav", "header", "footer", "aside", "iframe", "noscript"]):
            tag.decompose()

        # 移除注释
        for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
            comment.extract()

        # 提取文本
        text = soup.get_text(separator="\n", strip=True)

        return WebsiteCrawler._clean_text(text)

    @staticmethod
    def _clean_text(text: str) -> str:
        """清洗提取的文本."""
        if not text:
            return ""

        # 合并多余空白
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)

        # 去除行首行尾空白
        lines = [line.strip() for line in text.split("\n")]
        text = "\n".join(lines)

        return text.strip()

    @staticmethod
    def _is_url_allowed(
        url: str,
        domain: str,
        allowed_patterns: Optional[List[str]],
        excluded_patterns: Optional[List[str]],
    ) -> bool:
        """检查 URL 是否符合过滤规则."""
        parsed = urlparse(url)

        # 仅允许同域名
        if _extract_domain(url) != domain:
            return False

        # 排除静态资源
        static_extensions = {".jpg", ".jpeg", ".png", ".gif", ".svg", ".ico", ".css", ".js", ".pdf", ".zip", ".mp4", ".mp3"}
        if any(parsed.path.lower().endswith(ext) for ext in static_extensions):
            return False

        # 排除特定路径
        excluded_paths = ["login", "signin", "signup", "register", "admin", "api", "logout"]
        if any(p in parsed.path.lower() for p in excluded_paths):
            return False

        # 应用自定义排除规则
        if excluded_patterns:
            for pattern in excluded_patterns:
                if re.search(pattern, url, re.IGNORECASE):
                    return False

        # 应用自定义允许规则
        if allowed_patterns:
            for pattern in allowed_patterns:
                if re.search(pattern, url, re.IGNORECASE):
                    return True
            return False

        return True

    @staticmethod
    def _extract_links_from_page(page: CrawledPage, domain: str) -> List[str]:
        """从页面中提取同域名的链接."""
        if not page.html:
            return []

        from bs4 import BeautifulSoup

        soup = BeautifulSoup(page.html, "html.parser")
        links: List[str] = []

        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            full_url = urljoin(page.url, href)

            # 仅保留同域名链接
            if _extract_domain(full_url) == domain:
                # 标准化
                normalized = _normalize_url(full_url)
                if normalized.startswith(("http://", "https://")):
                    links.append(normalized)

        return list(set(links))  # 去重