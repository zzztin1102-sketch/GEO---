"""URL 文档抓取器 — 从飞书链接等 URL 中提取文档内容.

支持:
    - 飞书文档 (feishu.cn / larksuite.com)
    - 飞书 Wiki (feishu.cn/wiki)
    - 通用网页 (自动提取正文)

策略:
    1. 优先使用 Playwright 动态渲染（飞书页面为 JS 渲染）
    2. 降级为 requests 静态抓取
    3. 针对 飞书 DOM 结构做定向提取
"""

import re
import time
import logging
from typing import Optional
from urllib.parse import urlparse

from geo_review.models import ParsedContent

logger = logging.getLogger(__name__)

_MAX_CONTENT_LENGTH = 200000
_MIN_TEXT_LENGTH = 10

# 飞书 URL 特征
_FEISHU_DOMAINS = {"feishu.cn", "feishu.net", "larksuite.com"}


def _is_feishu_url(url: str) -> bool:
    """判断是否为飞书链接."""
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    return any(d in domain for d in _FEISHU_DOMAINS)


def _clean_text(text: str) -> str:
    """清洗提取的文本."""
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    lines = [line.strip() for line in text.split("\n")]
    return "\n".join(lines).strip()


def _extract_feishu_content(html: str) -> tuple:
    """从飞书页面 HTML 中定向提取文档内容.

    飞书文档的正文通常在特定容器中:
    - docx 页面: .docx-page-block 或 [data-page-id] 容器
    - wiki 页面: .wiki-body 或 .render-unit-wrapper
    - 通用: .suite-markdown 或 .doc-content
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")

    # 移除脚本、样式等噪声
    for tag in soup.find_all(["script", "style", "noscript", "svg", "iframe"]):
        tag.decompose()

    # 尝试多种飞书文档容器选择器
    selectors = [
        ".docx-page-block",
        "[data-page-id]",
        ".wiki-body",
        ".render-unit-wrapper",
        ".suite-markdown",
        ".doc-content",
        ".doc-body",
        ".page-content",
        # 飞书新版本可能用的容器
        ".doc-render",
        ".docx-container",
    ]

    for selector in selectors:
        elements = soup.select(selector)
        if elements:
            text_parts = []
            for el in elements:
                t = el.get_text(separator="\n", strip=True)
                if t and len(t) > _MIN_TEXT_LENGTH:
                    text_parts.append(t)
            if text_parts:
                return _clean_text("\n\n".join(text_parts)), soup.title.string if soup.title else None

    # 降级: 提取整个 body 文本
    body = soup.find("body")
    if body:
        text = body.get_text(separator="\n", strip=True)
        cleaned = _clean_text(text)
        if len(cleaned) > _MIN_TEXT_LENGTH:
            return cleaned, soup.title.string if soup.title else None

    return "", soup.title.string if soup.title else None


def _extract_general_content(html: str) -> tuple:
    """从通用网页 HTML 中提取正文内容."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")

    # 移除噪声
    for tag in soup.find_all(["script", "style", "nav", "header", "footer", "aside", "iframe", "noscript", "svg"]):
        tag.decompose()

    # 尝试常见的正文容器
    selectors = ["article", "main", ".content", ".article-body", ".post-content", ".entry-content", "#content"]

    for selector in selectors:
        el = soup.select_one(selector)
        if el:
            text = el.get_text(separator="\n", strip=True)
            cleaned = _clean_text(text)
            if len(cleaned) > _MIN_TEXT_LENGTH:
                return cleaned, soup.title.string if soup.title else None

    # 降级: 整个 body
    body = soup.find("body")
    if body:
        text = body.get_text(separator="\n", strip=True)
        cleaned = _clean_text(text)
        if len(cleaned) > _MIN_TEXT_LENGTH:
            return cleaned, soup.title.string if soup.title else None

    return "", soup.title.string if soup.title else None


class URLDocumentFetcher:
    """URL 文档抓取器 — 从 URL 中提取文档文本内容."""

    @classmethod
    def fetch(
        cls,
        url: str,
        *,
        timeout: int = 30,
        max_length: int = _MAX_CONTENT_LENGTH,
    ) -> ParsedContent:
        """从 URL 抓取文档内容.

        Args:
            url: 文档链接（飞书链接或通用网页 URL）
            timeout: 超时秒数
            max_length: 最大内容长度

        Returns:
            ParsedContent 对象

        Raises:
            ValueError: 抓取失败或内容为空
        """
        if not url or not url.startswith(("http://", "https://")):
            raise ValueError("URL 格式无效，需以 http:// 或 https:// 开头")

        is_feishu = _is_feishu_url(url)
        warnings = []
        title = None

        # 1. 优先使用 Playwright（飞书页面为 JS 渲染）
        html = None
        try:
            html, title = cls._fetch_with_playwright(url, timeout)
        except Exception as exc:
            warnings.append(f"动态渲染失败: {exc}")
            logger.warning(f"Playwright 抓取失败，降级为静态: {exc}")

        # 2. 降级为 requests 静态抓取
        if not html:
            try:
                html, title = cls._fetch_with_requests(url, timeout)
            except Exception as exc:
                warnings.append(f"静态抓取失败: {exc}")
                raise ValueError(f"无法获取页面内容: {exc}")

        if not html:
            raise ValueError("页面内容为空")

        # 3. 提取正文
        if is_feishu:
            text, page_title = _extract_feishu_content(html)
            source = "feishu"
        else:
            text, page_title = _extract_general_content(html)
            source = "webpage"

        if not text or len(text) < _MIN_TEXT_LENGTH:
            raise ValueError(f"未能从页面中提取到有效文本内容（仅 {len(text)} 字符）")

        # 使用页面标题
        if page_title and not title:
            title = page_title

        # 截断处理
        truncated = False
        if len(text) > max_length:
            text = text[:max_length]
            truncated = True
            warnings.append(f"内容已截断至 {max_length} 字符")

        return ParsedContent(
            text=text,
            source=source,
            filename=title or url,
            char_count=len(text),
            truncated=truncated,
            warnings=warnings,
        )

    @staticmethod
    def _fetch_with_playwright(url: str, timeout: int) -> tuple:
        """使用 Playwright 动态渲染页面.

        Returns:
            (html, title) 元组
        """
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu"])
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            )
            page = context.new_page()

            try:
                # 使用 domcontentloaded 替代 networkidle，避免飞书长连接导致超时
                response = page.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")
                if not response:
                    raise ValueError("页面加载失败")

                # 短暂等待 JS 渲染（1秒替代原3秒+wait_for_load_state双重等待）
                time.sleep(1)

                title = page.title()
                html = page.content()

                if not html or len(html) < 100:
                    raise ValueError("页面内容过短，可能未正确加载")

                return html, title
            finally:
                browser.close()

    @staticmethod
    def _fetch_with_requests(url: str, timeout: int) -> tuple:
        """使用 requests 静态抓取.

        Returns:
            (html, title) 元组
        """
        import requests

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }

        response = requests.get(url, headers=headers, timeout=timeout, verify=True)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"

        html = response.text
        if not html:
            raise ValueError("响应内容为空")

        # 从 HTML 中提取标题
        title = None
        title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        if title_match:
            title = title_match.group(1).strip()

        return html, title
