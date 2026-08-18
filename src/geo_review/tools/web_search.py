"""WebSearch — 联网搜索工具（多引擎，免费无需 API Key）.

提供网页搜索能力，用于事实核查。支持多个搜索引擎自动降级：
    1. 360搜索（中文首选）
    2. 搜狗搜索
    3. Bing 搜索
    4. DuckDuckGo HTML
    5. DuckDuckGo Lite
    6. 自定义搜索 API（可选配置）

用法::

    from geo_review.tools.web_search import WebSearchTool

    tool = WebSearchTool()
    results = tool.search("永安期货 净资产 132.7亿元")
    # results: [{"title": "...", "snippet": "...", "url": "..."}, ...]
"""

import logging
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """单条搜索结果."""
    title: str = ""
    snippet: str = ""
    url: str = ""
    source: str = ""

    def to_dict(self) -> Dict[str, str]:
        return {"title": self.title, "snippet": self.snippet, "url": self.url, "source": self.source}


@dataclass
class WebSearchConfig:
    """联网搜索配置."""
    enabled: bool = True
    timeout: int = 15
    max_results: int = 5
    # 自定义搜索 API（可选）
    api_url: Optional[str] = None
    api_key: Optional[str] = None
    # 请求头
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
    # 启用的搜索引擎列表（按优先级排序）
    engines: List[str] = field(default_factory=lambda: ["360", "sogou", "bing", "ddg", "ddg_lite"])


class WebSearchTool:
    """联网搜索工具（多引擎）.

    搜索策略：
    1. 对中文查询优先使用 360搜索（国内最全面的中文搜索引擎）
    2. 按优先级降级到其他引擎：搜狗 → Bing → DDG → DDG Lite
    3. 多引擎自动尝试，取第一个有结果的引擎
    4. 如果所有引擎都无结果，返回空列表
    """

    _SOGOU_URL = "https://www.sogou.com/web"
    _SOGOU_EN_URL = "https://en.sogou.com/web"
    _360_URL = "https://www.so.com/s"
    _BING_URL = "https://www.bing.com/search"
    _DDG_URL = "https://html.duckduckgo.com/html/"
    _DDG_LITE_URL = "https://lite.duckduckgo.com/lite/"

    def __init__(self, config: Optional[WebSearchConfig] = None):
        self.config = config or WebSearchConfig()
        self._session: Optional[requests.Session] = None

    @property
    def session(self) -> requests.Session:
        """懒加载的 requests Session."""
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update({
                "User-Agent": self.config.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Accept-Encoding": "gzip, deflate",
                "Connection": "keep-alive",
            })
        return self._session

    def search(self, query: str, max_results: Optional[int] = None) -> List[SearchResult]:
        """执行搜索（多引擎自动降级）.

        Args:
            query: 搜索关键词
            max_results: 最大返回结果数

        Returns:
            搜索结果列表
        """
        if not query or not query.strip():
            return []

        max_results = max_results or self.config.max_results
        query = query.strip()

        # 如果配置了自定义 API，优先使用
        if self.config.api_url and self.config.api_key:
            results = self._search_custom_api(query, max_results)
            if results:
                return results
            logger.debug("自定义搜索 API 无结果，降级到多引擎搜索")

        # 检测查询语言（中文 vs 英文）
        is_chinese = self._is_chinese(query)

        # 根据语言选择引擎顺序
        if is_chinese:
            engine_order = ["360", "sogou", "bing", "ddg", "ddg_lite"]
        else:
            engine_order = ["bing", "360", "sogou_en", "ddg", "ddg_lite"]

        # 逐引擎尝试
        for engine in engine_order:
            if engine == "360":
                results = self._search_360(query, max_results)
            elif engine == "sogou":
                results = self._search_sogou(query, max_results, english=False)
            elif engine == "sogou_en":
                results = self._search_sogou(query, max_results, english=True)
            elif engine == "bing":
                results = self._search_bing(query, max_results)
            elif engine == "ddg":
                results = self._search_ddg(query, max_results)
            elif engine == "ddg_lite":
                results = self._search_ddg_lite(query, max_results)
            else:
                continue

            if results:
                logger.info(f"搜索成功: engine={engine}, results={len(results)}, query={query[:50]}")
                return results

        logger.warning(f"所有搜索引擎均无结果: query={query[:50]}")
        return []

    def _is_chinese(self, text: str) -> bool:
        """检测文本是否包含中文."""
        chinese_count = len(re.findall(r"[\u4e00-\u9fff]", text))
        return chinese_count >= 2

    # ====================================================================
    # 360搜索（中文首选）
    # ====================================================================

    def _search_360(self, query: str, max_results: int) -> List[SearchResult]:
        """通过360搜索."""
        try:
            params = {"q": query}
            resp = self.session.get(self._360_URL, params=params, timeout=self.config.timeout)
            resp.raise_for_status()

            # 验证响应是否为有效搜索结果页（非验证码/错误页）
            if not any(marker in resp.text for marker in (
                'class="result"', 'class="res-list"', 'class="title"',
                'search-result', 'so-result',
            )):
                logger.debug("360搜索返回非搜索结果页（可能是验证码或错误页），降级到下一个引擎")
                return []

            return self._parse_360_html(resp.text, max_results)
        except Exception as e:
            logger.debug(f"360搜索失败: {e}")
            return []

    @staticmethod
    def _parse_360_html(html: str, max_results: int) -> List[SearchResult]:
        """解析360搜索结果页（健壮版）.

        360搜索结果有多种HTML结构：
        1. 标准结果: <div class="result"> / <div class="res-list"> / <li class="res-list">
        2. 链接形式: 直接 href、data-url 属性、/link?url= 加密重定向
        3. 摘要类名: .content / .res-desc / p 标签
        """
        results = []

        # 尝试多种块分割模式
        block_patterns = [
            r'<div[^>]*class="[^"]*\bresult\b[^"]*"',
            r'<div[^>]*class="[^"]*\bres-list\b[^"]*"',
            r'<li[^>]*class="[^"]*\bres-list\b[^"]*"',
            r'<div[^>]*class="[^"]*\btitle\b[^"]*"',
        ]

        blocks = []
        for pattern in block_patterns:
            blocks = re.split(pattern, html, flags=re.IGNORECASE)
            if len(blocks) > 1:
                break

        if len(blocks) > 1:
            for block in blocks[1:]:
                if len(results) >= max_results:
                    break

                # 提取标题和链接（优先级: data-url > href）
                title = ""
                url = ""

                # 模式A: 同时有 data-url 和 href
                link_match = re.search(
                    r'<a[^>]*data-url="([^"]*)"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
                    block, re.DOTALL | re.IGNORECASE
                )
                if link_match:
                    url = link_match.group(1).strip() or link_match.group(2).strip()
                    title = re.sub(r"<[^>]+>", "", link_match.group(3)).strip()

                # 模式B: 只有 href
                if not title:
                    link_match = re.search(
                        r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
                        block, re.DOTALL | re.IGNORECASE
                    )
                    if link_match:
                        url = link_match.group(1).strip()
                        title = re.sub(r"<[^>]+>", "", link_match.group(2)).strip()

                if not title:
                    continue

                # 清理标题
                title = re.sub(r"\s+", " ", title).strip()
                if len(title) < 2:
                    continue

                # 处理URL（支持360加密链接）
                url = WebSearchTool._decode_360_url(url)
                if not url or url.startswith("/"):
                    continue

                # 过滤广告
                if any(kw in title for kw in ["推广", "广告", "充值", "优惠", "特价"]):
                    continue
                if any(kw in url.lower() for kw in ["ad.", "ads.", "promo", "sponsor"]):
                    continue

                # 提取摘要（多种类名尝试）
                snippet = ""
                snippet_patterns = [
                    r'class="[^"]*\bcontent\b[^"]*"[^>]*>(.*?)</p>',
                    r'class="[^"]*\bres-desc\b[^"]*"[^>]*>(.*?)</p>',
                    r'class="[^"]*\babstract\b[^"]*"[^>]*>(.*?)</p>',
                    r'<p[^>]*class="[^"]*"[^>]*>(.*?)</p>',
                    r'<p[^>]*>(.*?)</p>',
                ]
                for sp in snippet_patterns:
                    content_match = re.search(sp, block, re.DOTALL | re.IGNORECASE)
                    if content_match:
                        snippet = re.sub(r"<[^>]+>", "", content_match.group(1)).strip()
                        if len(snippet) >= 10:
                            break

                results.append(SearchResult(
                    title=title,
                    snippet=snippet[:500],
                    url=url,
                    source="360",
                ))

        # 兜底
        if not results:
            results = WebSearchTool._parse_generic_links(html, max_results, "360")

        return results

    @staticmethod
    def _decode_360_url(url: str) -> str:
        """解码360搜索的加密重定向链接.

        360搜索常返回 /link?url=xxx 格式的重定向路径，需解码为真实URL。
        同时处理 data-url 属性和其他内部链接格式。
        """
        if not url:
            return ""

        # 已经是完整URL
        if url.startswith("http://") or url.startswith("https://"):
            return url

        # 空锚点或javascript
        if url.startswith("#") or url.startswith("javascript:"):
            return ""

        # 360加密链接格式: /link?url=base64编码 or /link?url=urlencoded
        if "/link" in url or "/relation" in url:
            try:
                # 提取 url 参数
                match = re.search(r'[?&]url=([^&]+)', url)
                if match:
                    encoded = match.group(1)
                    # 先尝试 URL 解码
                    decoded = urllib.parse.unquote(encoded)
                    if decoded.startswith("http"):
                        return decoded

                    # 再尝试 base64 解码
                    import base64
                    try:
                        decoded = base64.b64decode(encoded).decode('utf-8', errors='ignore')
                        if decoded.startswith("http"):
                            return decoded
                    except Exception:
                        pass
            except Exception:
                pass

        # data-url 属性值（可能是完整URL或加密值）
        if url.startswith("data:"):
            return ""

        # 其他相对路径，跳过
        if url.startswith("/"):
            return ""

        return url

    # ====================================================================
    # 搜狗搜索
    # ====================================================================

    def _search_sogou(self, query: str, max_results: int, english: bool = False) -> List[SearchResult]:
        """通过搜狗搜索."""
        try:
            url = self._SOGOU_EN_URL if english else self._SOGOU_URL
            params = {"query": query}
            resp = self.session.get(url, params=params, timeout=self.config.timeout)
            resp.raise_for_status()
            return self._parse_sogou_html(resp.text, max_results)
        except Exception as e:
            logger.debug(f"搜狗搜索{'(英文)' if english else ''}失败: {e}")
            return []

    @staticmethod
    def _parse_sogou_html(html: str, max_results: int) -> List[SearchResult]:
        """解析搜狗搜索结果页.

        搜狗搜索结果结构：
        <div class="vrwrap">
          <h3 class="vr-title"><a href="...">标题</a></h3>
          <p>摘要内容</p>
        </div>

        注意：搜狗的 URL 有两种形式：
        1. 直接链接（如 http://mp.weixin.qq.com/...）
        2. 加密链接（如 /link?url=encrypted...），需要从页面中提取
        """
        results = []

        # 按 vrwrap 块分割
        blocks = re.split(r'<div[^>]*class="[^"]*vrwrap[^"]*"', html, flags=re.IGNORECASE)
        if len(blocks) > 1:
            for block in blocks[1:]:
                if len(results) >= max_results:
                    break

                # 提取链接和标题（优先从 vr-title 中提取）
                title = ""
                url = ""

                # 方法1: 从 vr-title 中提取
                title_match = re.search(
                    r'class="[^"]*vr-title[^"]*"[^>]*>\s*<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
                    block, re.DOTALL | re.IGNORECASE
                )
                if title_match:
                    url = title_match.group(1).strip()
                    title = re.sub(r"<[^>]+>", "", title_match.group(2)).strip()

                # 方法2: 兜底，提取第一个 <a> 标签
                if not title:
                    link_match = re.search(
                        r'<a[^>]*href="([^"]*)"[^>]*>\s*(.*?)\s*</a>',
                        block, re.DOTALL | re.IGNORECASE
                    )
                    if link_match:
                        url = link_match.group(1).strip()
                        title = re.sub(r"<[^>]+>", "", link_match.group(2)).strip()

                if not title:
                    continue

                # 过滤广告
                if "广告" in title or "sponsor" in url.lower():
                    continue

                # 处理加密 URL
                if url.startswith("/link"):
                    url = WebSearchTool._decode_sogou_url(url)
                elif url.startswith("/"):
                    continue  # 其他内部链接跳过

                # 提取摘要（<p> 标签中的文本）
                snippet = ""
                p_match = re.search(
                    r'<p[^>]*>(.*?)</p>',
                    block, re.DOTALL | re.IGNORECASE
                )
                if p_match:
                    snippet = re.sub(r"<[^>]+>", "", p_match.group(1)).strip()

                results.append(SearchResult(
                    title=title,
                    snippet=snippet[:500],
                    url=url,
                    source="sogou",
                ))

        # 兜底：通用链接提取
        if not results:
            results = WebSearchTool._parse_generic_links(html, max_results, "sogou")

        return results

    @staticmethod
    def _decode_sogou_url(encrypted_url: str) -> str:
        """解码搜狗加密链接.

        搜狗的 /link?url= 参数使用 base64 编码。
        """
        try:
            import base64
            # 提取 url 参数
            match = re.search(r'url=([^&]+)', encrypted_url)
            if match:
                encoded = match.group(1)
                # Base64 解码
                decoded = base64.b64decode(encoded)
                return decoded.decode('utf-8', errors='ignore')
        except Exception:
            pass
        return encrypted_url

    # ====================================================================
    # Bing 搜索
    # ====================================================================

    def _search_bing(self, query: str, max_results: int) -> List[SearchResult]:
        """通过 Bing 搜索."""
        try:
            params = {"q": query, "setlang": "zh-CN"}
            resp = self.session.get(self._BING_URL, params=params, timeout=self.config.timeout)
            resp.raise_for_status()
            return self._parse_bing_html(resp.text, max_results)
        except Exception as e:
            logger.debug(f"Bing 搜索失败: {e}")
            return []

    @staticmethod
    def _parse_bing_html(html: str, max_results: int) -> List[SearchResult]:
        """解析 Bing 搜索结果页.

        Bing 搜索结果结构：
        <li class="b_algo">
          <h2><a href="...">标题</a></h2>
          <p>摘要内容</p>
        </li>
        """
        results = []

        # 按 b_algo 块分割
        blocks = re.split(r'<li[^>]*class="[^"]*b_algo[^"]*"', html, flags=re.IGNORECASE)
        if len(blocks) > 1:
            for block in blocks[1:]:
                if len(results) >= max_results:
                    break

                # 提取链接和标题
                link_match = re.search(
                    r'<a[^>]*href="([^"]*)"[^>]*>\s*(.*?)\s*</a>',
                    block, re.DOTALL | re.IGNORECASE
                )
                if not link_match:
                    continue

                url = link_match.group(1).strip()
                title = re.sub(r"<[^>]+>", "", link_match.group(2)).strip()

                if not title or not url.startswith("http"):
                    continue

                # 提取摘要
                snippet = ""
                snippet_match = re.search(
                    r'<p[^>]*>(.*?)</p>',
                    block, re.DOTALL | re.IGNORECASE
                )
                if snippet_match:
                    snippet = re.sub(r"<[^>]+>", "", snippet_match.group(1)).strip()

                results.append(SearchResult(
                    title=title,
                    snippet=snippet[:500],
                    url=url,
                    source="bing",
                ))

        # 兜底
        if not results:
            results = WebSearchTool._parse_generic_links(html, max_results, "bing")

        return results

    # ====================================================================
    # DuckDuckGo 搜索
    # ====================================================================

    def _search_ddg(self, query: str, max_results: int) -> List[SearchResult]:
        """通过 DuckDuckGo HTML 接口搜索."""
        try:
            params = {"q": query, "s": "0", "a": "h"}
            resp = self.session.post(self._DDG_URL, data=params, timeout=self.config.timeout)
            resp.raise_for_status()
            return self._parse_ddg_html(resp.text, max_results)
        except Exception as e:
            logger.debug(f"DuckDuckGo HTML 搜索失败: {e}")
            return []

    def _search_ddg_lite(self, query: str, max_results: int) -> List[SearchResult]:
        """通过 DuckDuckGo Lite 接口搜索."""
        try:
            params = {"q": query}
            resp = self.session.post(self._DDG_LITE_URL, data=params, timeout=self.config.timeout)
            resp.raise_for_status()
            return self._parse_ddg_lite_html(resp.text, max_results)
        except Exception as e:
            logger.debug(f"DuckDuckGo Lite 搜索失败: {e}")
            return []

    @staticmethod
    def _parse_ddg_html(html: str, max_results: int) -> List[SearchResult]:
        """解析 DuckDuckGo HTML 搜索结果页."""
        results = []

        link_pattern = re.compile(
            r'<a[^>]+class="result__a"[^>]+href="([^"]*)"[^>]*>(.*?)</a>',
            re.DOTALL,
        )
        snippet_pattern = re.compile(
            r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
            re.DOTALL,
        )

        links = link_pattern.findall(html)
        snippets = snippet_pattern.findall(html)

        for i, (href, title) in enumerate(links[:max_results]):
            title_clean = re.sub(r"<[^>]+>", "", title).strip()
            snippet_clean = ""
            if i < len(snippets):
                snippet_clean = re.sub(r"<[^>]+>", "", snippets[i]).strip()

            url = href
            if "uddg=" in href:
                uddg_match = re.search(r"uddg=([^&]+)", href)
                if uddg_match:
                    url = urllib.parse.unquote(uddg_match.group(1))

            if title_clean:
                results.append(SearchResult(
                    title=title_clean,
                    snippet=snippet_clean[:500],
                    url=url,
                    source="ddg",
                ))

        return results

    @staticmethod
    def _parse_ddg_lite_html(html: str, max_results: int) -> List[SearchResult]:
        """解析 DuckDuckGo Lite 搜索结果页."""
        results = []

        link_pattern = re.compile(
            r'<a[^>]*class="result-link"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
            re.DOTALL,
        )
        snippet_pattern = re.compile(
            r'<td[^>]*class="result-snippet"[^>]*>(.*?)</td>',
            re.DOTALL,
        )

        links = link_pattern.findall(html)
        snippets = snippet_pattern.findall(html)

        for i, (href, title) in enumerate(links[:max_results]):
            title_clean = re.sub(r"<[^>]+>", "", title).strip()
            snippet_clean = ""
            if i < len(snippets):
                snippet_clean = re.sub(r"<[^>]+>", "", snippets[i]).strip()

            if title_clean:
                results.append(SearchResult(
                    title=title_clean,
                    snippet=snippet_clean[:500],
                    url=href.strip(),
                    source="ddg_lite",
                ))

        return results

    # ====================================================================
    # 通用解析与自定义 API
    # ====================================================================

    @staticmethod
    def _parse_generic_links(html: str, max_results: int, source: str) -> List[SearchResult]:
        """通用链接提取（兜底方案）.

        提取页面中所有有意义的超链接（排除导航、脚本等）。
        """
        results = []

        # 提取所有 <a> 标签
        link_pattern = re.compile(
            r'<a[^>]*href="(https?://[^"]+)"[^>]*>\s*(.*?)\s*</a>',
            re.DOTALL | re.IGNORECASE,
        )

        seen_urls = set()
        for url, title in link_pattern.findall(html):
            if len(results) >= max_results:
                break

            # 去重
            if url in seen_urls:
                continue
            seen_urls.add(url)

            title_clean = re.sub(r"<[^>]+>", "", title).strip()

            # 过滤无效标题
            if not title_clean or len(title_clean) < 4:
                continue

            # 过滤常见非结果链接
            skip_domains = ["google.com", "bing.com", "baidu.com", "sogou.com",
                            "duckduckgo.com", "microsoft.com", "github.com/login"]
            if any(d in url for d in skip_domains):
                continue

            results.append(SearchResult(
                title=title_clean,
                snippet="",
                url=url,
                source=f"{source}_generic",
            ))

        return results

    def _search_custom_api(self, query: str, max_results: int) -> List[SearchResult]:
        """通过自定义搜索 API 搜索."""
        try:
            params = {
                "q": query,
                "count": max_results,
                "key": self.config.api_key,
            }
            resp = self.session.get(
                self.config.api_url,
                params=params,
                timeout=self.config.timeout,
            )
            resp.raise_for_status()
            data = resp.json()

            items = (
                data.get("webPages", {}).get("value", [])
                or data.get("results", [])
                or data.get("items", [])
            )
            results = []
            for item in items[:max_results]:
                results.append(SearchResult(
                    title=item.get("name") or item.get("title", ""),
                    snippet=item.get("snippet") or item.get("description", ""),
                    url=item.get("url") or item.get("link", ""),
                    source="custom_api",
                ))
            return results
        except Exception as e:
            logger.debug(f"自定义搜索 API 失败: {e}")
            return []
