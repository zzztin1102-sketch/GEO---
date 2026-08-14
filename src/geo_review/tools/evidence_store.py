"""Evidence Store — 官网爬取结果的结构化证据模型与提取器.

ChatGPT 诊断：
    官网爬取只是拿到文本片段送给 LLM，缺少结构化证据模型。
    需要 Claim → Evidence → Source → Authority → Entailment → Verdict 链路。

本模块负责：
    1. 从 CrawledDomain 提取结构化事实（公司名、产品、关键数据、资质认证等）
    2. 构建 EvidenceStore 缓存，供 LLM 审核和 FactChecker 使用
    3. 将结构化证据（而非原始文本）送入 LLM 审核 Prompt
"""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from geo_review.models import CrawledDomain, CrawledPage


@dataclass
class EvidenceItem:
    """单条结构化证据."""

    claim: str = ""
    """被验证的事实声明（如"成立于2015年"）"""

    evidence_text: str = ""
    """从官网提取的支持/反驳文本片段"""

    source_url: str = ""
    """证据来源页面 URL"""

    source_page_title: str = ""
    """证据来源页面标题"""

    source_type: str = "official_website"
    """证据来源类型: official_website / authoritative_media / third_party / unknown"""

    authority: str = "high"
    """来源权威性: high / medium / low / unknown"""

    category: str = "general"
    """证据分类: company_info / product_info / financial_data / certification / partnership / history / other"""

    @property
    def entailment(self) -> str:
        """证据与声明的逻辑关系: supports / refutes / neutral"""
        if not self.evidence_text or not self.claim:
            return "neutral"
        # 简单判断：如果声明中的关键内容出现在证据文本中
        claim_keywords = [w for w in re.split(r'[\s,，。、（）()]', self.claim) if len(w) >= 2]
        matches = sum(1 for kw in claim_keywords if kw in self.evidence_text)
        if matches >= len(claim_keywords) * 0.6:
            return "supports"
        return "neutral"


@dataclass
class EvidenceStore:
    """官网结构化证据存储."""

    domain: str = ""
    company_name: str = ""
    company_full_name: str = ""
    products: List[str] = field(default_factory=list)
    certifications: List[str] = field(default_factory=list)
    key_data: List[EvidenceItem] = field(default_factory=list)
    company_facts: List[EvidenceItem] = field(default_factory=list)
    product_facts: List[EvidenceItem] = field(default_factory=list)
    raw_text_summary: str = ""
    """前 N 字符的原始文本摘要（作为兜底）"""

    total_pages: int = 0
    total_chars: int = 0
    extracted_at: str = ""

    def get_evidence_for_claim(self, claim: str) -> Optional[EvidenceItem]:
        """查找与声明最相关的证据."""
        if not claim:
            return None

        # 在所有证据中搜索
        all_evidence = self.company_facts + self.product_facts + self.key_data
        best_match = None
        best_score = 0

        claim_keywords = set(w for w in re.split(r'[\s,，。、（）()【】\[\]]', claim) if len(w) >= 2)

        for ev in all_evidence:
            if not ev.evidence_text:
                continue
            ev_keywords = set(w for w in re.split(r'[\s,，。、（）()【】\[\]]', ev.evidence_text) if len(w) >= 2)
            overlap = len(claim_keywords & ev_keywords)
            score = overlap / max(len(claim_keywords), 1)
            if score > best_score:
                best_score = score
                best_match = ev

        if best_match and best_score > 0.2:
            return best_match
        return None

    def to_llm_context(self, max_chars: int = 2000) -> str:
        """生成送入 LLM 的结构化证据上下文（替代原始文本拼接）."""
        lines = []

        # 公司基本信息
        if self.company_full_name or self.company_name:
            lines.append(f"【公司名称】{self.company_full_name or self.company_name}")

        # 产品信息
        if self.products:
            lines.append(f"【产品/服务】{'、'.join(self.products[:10])}")

        # 资质认证
        if self.certifications:
            lines.append(f"【资质认证】{'、'.join(self.certifications[:10])}")

        # 关键事实（按类别组织）
        all_facts = self.company_facts + self.product_facts + self.key_data
        if all_facts:
            lines.append("【官网关键信息】")
            seen = set()
            for fact in all_facts[:20]:  # 最多20条
                text = fact.evidence_text.strip()
                if text and text not in seen and len(text) > 5:
                    seen.add(text)
                    source = f"（来源: {fact.source_page_title}）" if fact.source_page_title else ""
                    lines.append(f"  - {text[:200]}{source}")

        result = "\n".join(lines)

        # 如果结构化信息不足，补充原始文本摘要
        if len(result) < 100 and self.raw_text_summary:
            result = f"{result}\n\n【官网文本摘要】\n{self.raw_text_summary}"

        return result[:max_chars]


def extract_evidence(crawled: CrawledDomain, company_name: str = "") -> EvidenceStore:
    """从 CrawledDomain 提取结构化证据.

    Args:
        crawled: 爬取的域名数据
        company_name: 提报表中的公司名（用于定向提取）

    Returns:
        EvidenceStore: 结构化证据存储
    """
    store = EvidenceStore(
        domain=crawled.domain,
        company_name=company_name,
        total_pages=crawled.success_pages,
        total_chars=crawled.total_chars,
    )

    # 合并所有页面文本
    all_pages_text = ""
    for page in crawled.pages:
        if page.text and not page.error:
            all_pages_text += f"\n--- {page.title or page.url} ---\n{page.text}"

    if not all_pages_text:
        return store

    # 提取公司名
    store.company_full_name = _extract_company_name(all_pages_text, company_name, crawled.domain)

    # 提取产品/服务
    store.products = _extract_products(all_pages_text, store.company_full_name)

    # 提取资质认证
    store.certifications = _extract_certifications(all_pages_text)

    # 提取关键事实
    store.company_facts = _extract_company_facts(all_pages_text, crawled.pages, store.company_full_name)
    store.product_facts = _extract_product_facts(all_pages_text, crawled.pages, store.products)
    store.key_data = _extract_key_data(all_pages_text, crawled.pages)

    # 原始文本摘要（兜底）
    store.raw_text_summary = all_pages_text[:1500].strip()

    return store


def _extract_company_name(text: str, hint: str, domain: str) -> str:
    """从官网文本中提取公司全称."""
    # 1. 如果有提示名，检查是否在官网出现
    if hint and hint in text:
        return hint

    # 2. 正则提取中文公司名
    patterns = [
        r'([\u4e00-\u9fff]{2,20}(?:股份有限公司|有限责任公司|有限公司|集团有限公司|科技有限公司|集团|公司))',
        r'([\u4e00-\u9fff]{2,20}(?:银行|保险|证券|基金管理|期货|信托))',
    ]
    for pattern in patterns:
        matches = re.findall(pattern, text)
        if matches:
            # 取最长的匹配
            return max(matches, key=len)

    # 3. 从域名提取
    if domain:
        parts = domain.split(".")
        if len(parts) >= 2:
            return parts[-2]  # 如 example.com -> example

    return hint or ""


def _extract_products(text: str, company_name: str) -> List[str]:
    """从官网文本中提取产品/服务名称."""
    products = []

    # 模式1: "XX产品""XX服务""XX平台""XX系统"
    product_patterns = [
        r'([\u4e00-\u9fff]{2,15}(?:产品|服务|平台|系统|方案|软件|APP|应用))',
    ]
    for pattern in product_patterns:
        matches = re.findall(pattern, text)
        for m in matches:
            m = m.strip()
            if len(m) >= 4 and m not in products:
                products.append(m)

    # 模式2: 产品列表区域（"产品中心""我们的产品""核心产品"）
    product_section = re.search(
        r'(?:产品中心|我们的产品|核心产品|产品介绍|主营业务|产品与服务)(.*?)(?:\n---|\Z)',
        text, re.DOTALL
    )
    if product_section:
        section_text = product_section.group(1)[:500]
        # 提取列表项
        items = re.findall(r'(?:^|\n)\s*(?:[0-9]+[\.\、\)]|[-•·])\s*([^\n]{2,30})', section_text)
        for item in items:
            item = item.strip()
            if len(item) >= 2 and item not in products:
                products.append(item)

    return products[:15]  # 最多15个


def _extract_certifications(text: str) -> List[str]:
    """从官网文本中提取资质认证信息."""
    certs = []

    # 认证模式
    cert_patterns = [
        r'(ISO\s*\d{4,}(?:[/:]\d+)?(?:[a-zA-Z]|\u4e00-\u9fff)*)',
        r'(GB(?:/T)?\s*\d+[.\-]\d+)',
        r'([\u4e00-\u9fff]{2,10}(?:认证|许可证|资质|证书|牌照|备案))',
        r'((?:CMMI|SOC|PCI[- ]?DSS|GDPR|HIPAA|AAA|ICP)[^\n]{0,20})',
    ]
    for pattern in cert_patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for m in matches:
            m = m.strip()
            if len(m) >= 3 and m not in certs:
                certs.append(m)

    return certs[:10]


def _extract_company_facts(text: str, pages: List[CrawledPage], company_name: str) -> List[EvidenceItem]:
    """提取公司层面的事实（成立时间、规模、地址等）."""
    facts = []

    # 成立时间
    founded_patterns = [
        r'((?:成立于|创立于|创办于|创建于)\s*(\d{4})\s*年)',
        r'((?:成立于|创立于)\s*(\d{4}))',
    ]
    for pattern in founded_patterns:
        for match in re.finditer(pattern, text):
            page_info = _find_page_for_position(match.start(), pages, text)
            facts.append(EvidenceItem(
                claim=f"成立于{match.group(2)}年",
                evidence_text=match.group(1),
                source_url=page_info[0] if page_info else "",
                source_page_title=page_info[1] if page_info else "",
                category="history",
            ))
            break

    # 员工规模
    scale_patterns = [
        r'((?:员工|团队|人员|员工总数)\s*(?:约|大约)?\s*(\d+[万+]*)\s*(?:人|名))',
        r'((?:拥有|现有)\s*(\d+[万+]*)\s*(?:名|位)?\s*(?:员工|专业人才|技术人员))',
    ]
    for pattern in scale_patterns:
        for match in re.finditer(pattern, text):
            page_info = _find_page_for_position(match.start(), pages, text)
            facts.append(EvidenceItem(
                claim=f"员工规模{match.group(2)}人",
                evidence_text=match.group(1),
                source_url=page_info[0] if page_info else "",
                source_page_title=page_info[1] if page_info else "",
                category="company_info",
            ))
            break

    # 总部地址
    addr_pattern = r'((?:总部|公司地址|地址|位于)\s*[：:在]?\s*([\u4e00-\u9fff]{2,10}[市省]))'
    for match in re.finditer(addr_pattern, text):
        page_info = _find_page_for_position(match.start(), pages, text)
        facts.append(EvidenceItem(
            claim=f"总部位于{match.group(2)}",
            evidence_text=match.group(1),
            source_url=page_info[0] if page_info else "",
            source_page_title=page_info[1] if page_info else "",
            category="company_info",
        ))
        break

    return facts[:10]


def _extract_product_facts(text: str, pages: List[CrawledPage], products: List[str]) -> List[EvidenceItem]:
    """提取产品层面的事实（产品功能、覆盖范围等）."""
    facts = []

    for product in products[:5]:  # 最多检查5个产品
        # 在文本中查找产品附近的描述
        idx = text.find(product)
        if idx >= 0:
            # 提取产品前后200字符的上下文
            start = max(0, idx - 100)
            end = min(len(text), idx + 200)
            context = text[start:end].replace("\n", " ").strip()

            # 尝试提取包含产品名的句子
            sentences = re.split(r'[。！？\n]', context)
            for sent in sentences:
                if product in sent and len(sent) > 10:
                    page_info = _find_page_for_position(idx, pages, text)
                    facts.append(EvidenceItem(
                        claim=f"产品{product}的描述",
                        evidence_text=sent.strip()[:200],
                        source_url=page_info[0] if page_info else "",
                        source_page_title=page_info[1] if page_info else "",
                        category="product_info",
                    ))
                    break

    return facts[:10]


def _extract_key_data(text: str, pages: List[CrawledPage]) -> List[EvidenceItem]:
    """提取关键数据（金额、用户数、市场份额等）."""
    facts = []

    # 金额数据
    amount_patterns = [
        r'((?:净资产|资产总额|注册资本|营业收入|营收|利润)\s*(?:约|达|为)?\s*(\d+(?:\.\d+)?\s*(?:亿|万)元))',
        r'((?:市值|估值)\s*(?:约|达|为)?\s*(\d+(?:\.\d+)?\s*(?:亿|万)元))',
    ]
    for pattern in amount_patterns:
        for match in re.finditer(pattern, text):
            page_info = _find_page_for_position(match.start(), pages, text)
            facts.append(EvidenceItem(
                claim=f"{match.group(1)}",
                evidence_text=match.group(1),
                source_url=page_info[0] if page_info else "",
                source_page_title=page_info[1] if page_info else "",
                category="financial_data",
            ))

    # 用户数/客户数
    user_patterns = [
        r'((?:服务|拥有|累计)\s*(?:超过|约|近)?\s*(\d+[万+]*)\s*(?:家|名|位|个)\s*(?:企业|客户|用户|机构))',
        r'((?:用户数|客户数|服务企业数)\s*(?:达|超|约)?\s*(\d+[万+]*))',
    ]
    for pattern in user_patterns:
        for match in re.finditer(pattern, text):
            page_info = _find_page_for_position(match.start(), pages, text)
            facts.append(EvidenceItem(
                claim=f"服务{match.group(2)}客户",
                evidence_text=match.group(1),
                source_url=page_info[0] if page_info else "",
                source_page_title=page_info[1] if page_info else "",
                category="financial_data",
            ))

    # 排名/奖项
    rank_patterns = [
        r'((?:排名|位列|跻身)\s*(?:第?\s*\d+|前\d+|前茅|前列)\s*(?:名|位)?)',
        r'((?:荣获|获得|获评)\s*[^\n，。]{2,30}(?:奖|称号|认证|荣誉))',
    ]
    for pattern in rank_patterns:
        for match in re.finditer(pattern, text):
            page_info = _find_page_for_position(match.start(), pages, text)
            facts.append(EvidenceItem(
                claim=f"排名/奖项: {match.group(1)}",
                evidence_text=match.group(1),
                source_url=page_info[0] if page_info else "",
                source_page_title=page_info[1] if page_info else "",
                category="other",
            ))

    return facts[:15]


def _find_page_for_position(pos: int, pages: List[CrawledPage], full_text: str) -> Tuple[str, str]:
    """根据在合并文本中的位置，找到对应的页面信息."""
    # 页面之间以 "\n--- title ---\n" 分隔
    # 找到 pos 之前最后一个 "---" 标记
    before = full_text[:pos]
    last_sep = before.rfind("\n--- ")
    if last_sep >= 0:
        # 提取页面标题
        sep_end = before.find(" ---\n", last_sep)
        if sep_end > last_sep:
            title = before[last_sep + 5:sep_end]
            # 查找对应的URL
            for page in pages:
                if page.title and page.title == title:
                    return (page.url, page.title)
            return ("", title)
    return ("", "")
