"""FactChecker — Agentic 联网事实核查模块.

架构升级：从固定三步流水线改为 Agentic 循环。

核心流程：
    1. LLM 从正文中提取可验证的事实声明
    2. 对每条声明，进入 Agentic 循环：
       a. LLM 自主决定搜什么（生成搜索 query）
       b. 执行搜索 → 证据切片+相关性排序 → 喂给 LLM
       c. LLM 决定：继续搜（换关键词）还是 判定
       d. 判定时强制附原文引用，程序化回验防幻觉
    3. 生成审核问题

关键改进（vs 旧版）：
    - LLM 自主决定搜索词，不再靠硬编码规则
    - 搜不到不放弃，LLM 可以换关键词再搜（最多 N 轮）
    - 证据累积：多轮搜索的结果累加，而非单轮
    - 引用回验：LLM 输出的 evidence_text 必须在搜索结果中可定位
    - 证据切片：网页按段落切块 + 相关性排序，替代全文

用法::

    from geo_review.tools.fact_checker import FactChecker
    from geo_review.tools.web_search import WebSearchTool
    from geo_review.llm.client import LLMClient

    checker = FactChecker(llm_client=LLMClient(...), search_tool=WebSearchTool())
    issues, results = checker.check(content_text, submission)
"""

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from geo_review.llm.client import LLMClient
from geo_review.rules.issues import Issue, IssueEvidence, IssueSeverity, IssueType
from geo_review.tools.evidence_store import EvidenceStore
from geo_review.tools.web_search import SearchResult, WebSearchTool, WebSearchConfig

logger = logging.getLogger(__name__)


@dataclass
class FactCheckResult:
    """单条声明核查结果 — 证据链模型.

    证据链：Claim → Evidence → Source → Authority → Entailment → Verdict
    """
    claim: str = ""
    verdict: str = "unknown"  # verified / refuted / unverifiable / unknown
    confidence: float = 0.0
    snippet: str = ""
    search_evidence: str = ""
    search_urls: List[str] = field(default_factory=list)
    reason: str = ""
    suggestion: str = ""
    # 证据链增强字段
    evidence_text: str = ""          # 搜索结果中支持/反驳声明的关键证据文本
    source_type: str = ""            # 来源类型: official_website / authoritative_media / third_party / unknown
    source_authority: str = ""       # 来源权威性: high / medium / low / unknown
    entailment: str = ""             # 证据与声明的蕴含关系: supports / refutes / neutral / contradictory
    # Agentic 扩展字段
    search_rounds: int = 0           # 实际搜索轮数
    queries_used: List[str] = field(default_factory=list)  # 使用过的搜索词
    citation_verified: bool = True   # 引用回验是否通过


# ====================================================================
# Prompt 模板
# ====================================================================

CLAIM_EXTRACTION_PROMPT = """从以下正文中提取所有需要联网核实的事实性声明。

事实性声明是指可以用公开信息验证的具体断言，例如：
- 排名/奖项类："连续5年入选期货服务行业五百强"
- 数据/统计类："市场占有率达30%""服务超过100万用户"
- 资质/认证类："通过ISO9001认证"
- 历史事实类："成立于2015年""2023年上市"
- 合作关系类："与XX银行达成战略合作"
- 行业地位类："国内最大的XX平台"

公司名称（如有）：{company_name}

以 JSON 格式输出，最多提取10条最重要的声明：
{{
  "claims": [
    "声明1的原文表述",
    "声明2的原文表述"
  ]
}}"""


# Agentic 核查 System Prompt — 定义 LLM 角色 + 工具 + 输出格式
AGENT_SYSTEM_PROMPT = """你是事实核查研究员。你的任务是验证一条事实声明的真实性。

## 可用工具
你通过输出 JSON 来调用工具。每轮你必须输出以下两种 JSON 之一：

### 工具1：搜索（当你需要更多信息时）
```json
{{
  "action": "search",
  "query": "你的搜索词",
  "reason": "为什么要搜这个（简短说明）"
}}
```

### 工具2：判定（当你有足够证据时）
```json
{{
  "action": "verify",
  "verdict": "verified|refuted|unverifiable|partially_verified",
  "confidence": 0.0-1.0,
  "reason": "判断理由，必须引用具体证据",
  "evidence_text": "支持或反驳该声明的关键证据原文（必须从搜索结果中逐字引用，不可改写或编造）",
  "source_type": "official_website|authoritative_media|third_party|unknown",
  "source_authority": "high|medium|low|unknown",
  "entailment": "supports|refutes|neutral|contradictory",
  "suggestion": "如果声明有问题给出修改建议，否则返回空字符串"
}}
```

## 判定标准
- **verified**（已证实）：搜索结果有明确证据支持该声明
- **refuted**（已证伪）：搜索结果有明确证据反驳该声明
- **unverifiable**（无法验证）：搜索后仍未找到相关证据
- **partially_verified**（部分证实）：部分内容可证实，但关键信息无法核实或存在偏差

## 关键规则
1. **evidence_text 必须是从搜索结果中逐字引用的原文**，不可改写、概括或编造
2. 如果搜索结果不足以判定，优先继续搜索（换关键词），不要急于判定
3. 最多搜索 3 轮，如果 3 轮后仍无足够证据，再输出 verify + unverifiable
4. 搜索词应该有针对性：用公司名+关键事实，避免太宽泛
"""


# ====================================================================
# FactChecker 主类
# ====================================================================

class FactChecker:
    """Agentic 联网事实核查器.

    LLM 自主决定搜索策略，多轮搜索累积证据，判定时附引用并程序化回验。
    """

    def __init__(
        self,
        llm_client: LLMClient,
        search_tool: Optional[WebSearchTool] = None,
        max_claims: int = 5,
        max_search_results: int = 5,
        search_timeout: int = 15,
        max_agent_rounds: int = 3,
        enable_deep_fetch: bool = True,
        max_deep_fetch: int = 2,
        deep_fetch_timeout: int = 10,
    ):
        self.llm_client = llm_client
        self.search_tool = search_tool or WebSearchTool(
            WebSearchConfig(timeout=search_timeout, max_results=max_search_results)
        )
        self.max_claims = max_claims
        self.max_search_results = max_search_results
        self.max_agent_rounds = max_agent_rounds
        # 深度抓取配置：对高相关性搜索结果 URL 抓取完整正文，增强证据质量
        self.enable_deep_fetch = enable_deep_fetch
        self.max_deep_fetch = max_deep_fetch
        self.deep_fetch_timeout = deep_fetch_timeout
        # URL → 正文缓存（避免重复抓取）
        self._page_content_cache: Dict[str, str] = {}

    def check(
        self,
        content: str,
        company_name: str = "",
        starting_id: int = 20001,
        evidence_store: Optional[EvidenceStore] = None,
    ) -> Tuple[List[Issue], List[FactCheckResult]]:
        """执行 Agentic 联网事实核查.

        Args:
            content: 正文文本
            company_name: 公司名称（用于精确搜索）
            starting_id: 问题 ID 起始编号
            evidence_store: 官网结构化证据（可选），作为优先证据来源

        Returns:
            (审核问题列表, 核查结果详情列表)
        """
        if not content or not content.strip():
            return [], []

        start_time = time.time()
        logger.info(f"FactChecker[Agentic]: 开始联网事实核查, 公司={company_name or '未知'}")

        # Step 1: 提取可验证的事实声明
        claims = self._extract_claims(content, company_name)
        if not claims:
            logger.info("FactChecker[Agentic]: 未提取到可验证的事实声明")
            return [], []

        logger.info(f"FactChecker[Agentic]: 提取到 {len(claims)} 条事实声明")

        # Step 2: 逐条 Agentic 核查
        all_results: List[FactCheckResult] = []
        for claim in claims[:self.max_claims]:
            result = self._verify_claim_agentic(claim, content, company_name, evidence_store)
            all_results.append(result)

        # Step 3: 生成审核问题
        issues = self._results_to_issues(all_results, starting_id)

        elapsed = time.time() - start_time
        verified_count = sum(1 for r in all_results if r.verdict == "verified")
        refuted_count = sum(1 for r in all_results if r.verdict == "refuted")
        unverifiable_count = sum(1 for r in all_results if r.verdict == "unverifiable")
        total_rounds = sum(r.search_rounds for r in all_results)

        logger.info(
            f"FactChecker[Agentic]: 核查完成，耗时 {elapsed:.1f}s, "
            f"总搜索轮数={total_rounds}, "
            f"已证实={verified_count}, 已证伪={refuted_count}, 无法验证={unverifiable_count}, "
            f"生成 {len(issues)} 个问题"
        )

        return issues, all_results

    # ------------------------------------------------------------------
    # Step 1: 声明提取（保持原有逻辑）
    # ------------------------------------------------------------------

    def _extract_claims(self, content: str, company_name: str) -> List[str]:
        """使用 LLM 从正文中提取可验证的事实声明."""
        truncated = content[:8000]

        prompt = CLAIM_EXTRACTION_PROMPT.format(
            company_name=company_name or "未提供"
        )

        messages = [
            {"role": "system", "content": "从正文中提取可联网验证的事实性声明，只输出JSON。"},
            {"role": "user", "content": f"{prompt}\n\n【正文】\n{truncated}"},
        ]

        try:
            resp = self.llm_client.chat(messages, response_format="json_object", max_retries=1)
            data = LLMClient._extract_json(resp["content"])
            claims = data.get("claims", [])
            if isinstance(claims, list):
                return [str(c).strip() for c in claims if c and str(c).strip()]
        except Exception as e:
            logger.warning(f"FactChecker: 提取声明失败: {e}")

        return []

    # ------------------------------------------------------------------
    # Step 2: Agentic 核查循环（核心改造）
    # ------------------------------------------------------------------

    def _verify_claim_agentic(
        self,
        claim: str,
        content: str,
        company_name: str,
        evidence_store: Optional[EvidenceStore] = None,
    ) -> FactCheckResult:
        """对单条声明执行 Agentic 循环核查.

        流程：
            1. 优先检查官网结构化证据（如有，快速判定）
            2. 进入 Agentic 循环：
               a. LLM 决定搜什么
               b. 执行搜索 → 证据切片+排序 → 喂给 LLM
               c. LLM 决定继续搜还是判定
               d. 判定时附引用 → 程序化回验
            3. 超过最大轮数 → 强制判定
        """
        result = FactCheckResult(claim=claim)
        snippet = self._find_snippet(content, claim)
        result.snippet = snippet

        # === 优先检查官网结构化证据 ===
        if evidence_store:
            ev = evidence_store.get_evidence_for_claim(claim)
            if ev:
                result.evidence_text = ev.evidence_text
                result.source_type = ev.source_type
                result.source_authority = ev.authority
                result.entailment = ev.entailment

                if ev.entailment == "supports":
                    result.verdict = "verified"
                    result.confidence = 0.85
                    result.reason = f"官网信息支持该声明：{ev.evidence_text[:200]}"
                    result.search_evidence = f"[官网证据] {ev.evidence_text}"
                    if ev.source_url:
                        result.search_urls = [ev.source_url]
                    result.citation_verified = True
                    logger.info(f"FactChecker[Agentic]: 声明「{claim[:30]}...」由官网证据支持，跳过联网搜索")
                    return result
                elif ev.entailment == "refutes":
                    result.verdict = "refuted"
                    result.confidence = 0.7
                    result.reason = f"官网信息与该声明不符：{ev.evidence_text[:200]}"
                    result.suggestion = f"请核实该声明，官网信息显示：{ev.evidence_text[:100]}"
                    result.search_evidence = f"[官网证据] {ev.evidence_text}"
                    if ev.source_url:
                        result.search_urls = [ev.source_url]
                    result.citation_verified = True
                    logger.info(f"FactChecker[Agentic]: 声明「{claim[:30]}...」被官网证据反驳，跳过联网搜索")
                    return result

        # === Agentic 循环 ===
        accumulated_evidence: List[str] = []  # 累积的证据文本
        accumulated_urls: List[str] = []
        conversation_history: List[dict] = []
        queries_used: List[str] = []

        # 初始 user 消息：告诉 LLM 要验证什么
        initial_msg = self._build_initial_agent_message(claim, snippet, company_name, evidence_store)
        conversation_history.append({"role": "user", "content": initial_msg})

        for round_num in range(1, self.max_agent_rounds + 1):
            # 调用 LLM 决定下一步
            messages = [{"role": "system", "content": AGENT_SYSTEM_PROMPT}] + conversation_history

            try:
                resp = self.llm_client.chat(
                    messages,
                    response_format="json_object",
                    max_retries=1,
                    retry_delay=2.0,
                )
                action = LLMClient._extract_json(resp["content"])
            except Exception as e:
                logger.warning(f"FactChecker[Agentic]: 第{round_num}轮 LLM 调用失败: {e}")
                break

            # 解析 LLM 决策
            action_type = action.get("action", "")

            if action_type == "search":
                # LLM 要搜索
                query = action.get("query", "").strip()
                if not query:
                    query = claim  # 回退到原始声明

                queries_used.append(query)
                logger.info(f"FactChecker[Agentic]: 声明「{claim[:30]}...」第{round_num}轮搜索: {query}")

                # 执行搜索
                search_results = self._execute_search(query)
                if search_results:
                    # 深度抓取 top-k 相关性 URL 的完整正文（增强证据质量）
                    page_contents = self._deep_fetch_for_results(search_results, claim)

                    # 段落级切片 + 相关性排序（优先使用深度抓取的正文）
                    evidence_chunks = self._slice_and_rank(
                        search_results, claim, max_chunks=5, page_contents=page_contents
                    )
                    evidence_text = self._format_evidence(evidence_chunks, query, round_num)
                    accumulated_evidence.append(evidence_text)
                    accumulated_urls.extend(sr.url for sr in search_results if sr.url)
                else:
                    evidence_text = f"[第{round_num}轮搜索] 搜索词: {query}\n未返回有效结果。"

                # 把搜索结果喂给 LLM，进入下一轮
                assistant_msg = json.dumps(action, ensure_ascii=False)
                tool_result_msg = (
                    f"搜索结果（第{round_num}轮）：\n{evidence_text}\n\n"
                    f"已搜索 {round_num} 轮，剩余 {self.max_agent_rounds - round_num} 轮。"
                )
                conversation_history.append({"role": "assistant", "content": assistant_msg})
                conversation_history.append({"role": "user", "content": tool_result_msg})

            elif action_type == "verify":
                # LLM 给出判定
                logger.info(
                    f"FactChecker[Agentic]: 声明「{claim[:30]}...」第{round_num}轮判定: {action.get('verdict', 'unknown')}"
                )

                result.verdict = action.get("verdict", "unknown")
                result.confidence = float(action.get("confidence", 0.0))
                result.reason = action.get("reason", "")
                result.suggestion = action.get("suggestion", "")
                result.evidence_text = action.get("evidence_text", "")
                result.source_type = action.get("source_type", "unknown")
                result.source_authority = action.get("source_authority", "unknown")
                result.entailment = action.get("entailment", "neutral")
                result.search_rounds = round_num
                result.queries_used = queries_used
                result.search_urls = list(set(accumulated_urls))
                result.search_evidence = "\n\n".join(accumulated_evidence) if accumulated_evidence else ""

                # 引用回验 — 防幻觉
                result.citation_verified = self._verify_citation(
                    result.evidence_text, accumulated_evidence
                )
                if not result.citation_verified:
                    # LLM 编造了证据 → 降级
                    logger.warning(
                        f"FactChecker[Agentic]: 声明「{claim[:30]}...」引用回验失败 — "
                        f"LLM 输出的 evidence_text 未在搜索结果中找到，疑似幻觉"
                    )
                    # 如果原本判定为 verified，降级为 partially_verified
                    if result.verdict == "verified":
                        result.verdict = "partially_verified"
                        result.reason = (
                            f"证据引用无法在搜索结果中逐字定位，可能为模型概括。"
                            f"原始理由: {result.reason}"
                        )
                        result.confidence = max(0.0, result.confidence - 0.3)

                # 多源交叉验证 — 单一来源的 verified 降级为 partially_verified
                if result.verdict == "verified":
                    distinct_domains = self._count_distinct_domains(accumulated_urls)
                    if distinct_domains < 2:
                        logger.info(
                            f"FactChecker[Agentic]: 声明「{claim[:30]}...」"
                            f"仅 {distinct_domains} 个独立来源，降级为 partially_verified"
                        )
                        result.verdict = "partially_verified"
                        result.reason = (
                            f"仅找到 {distinct_domains} 个独立来源支持该声明，"
                            f"建议补充更多权威来源交叉验证。原始理由: {result.reason}"
                        )
                        result.confidence = max(0.0, result.confidence - 0.2)

                if result.verdict == "unverifiable" and not result.suggestion:
                    result.suggestion = f"建议人工核实：{claim}"

                return result

            else:
                logger.warning(f"FactChecker[Agentic]: 第{round_num}轮未知 action: {action_type}")
                break

        # === 超过最大轮数，强制判定 ===
        logger.info(f"FactChecker[Agentic]: 声明「{claim[:30]}...」达到最大轮数，强制判定")
        return self._force_verdict(claim, snippet, accumulated_evidence, accumulated_urls, queries_used)

    # ------------------------------------------------------------------
    # Agentic 辅助方法
    # ------------------------------------------------------------------

    def _build_initial_agent_message(
        self,
        claim: str,
        snippet: str,
        company_name: str,
        evidence_store: Optional[EvidenceStore],
    ) -> str:
        """构建 Agentic 循环的初始 user 消息."""
        parts = [
            f"【待验证声明】\n{claim}",
            f"\n【声明出处（原文片段）】\n{snippet}",
            f"\n【公司名称】\n{company_name or '未提供'}",
        ]

        # 如果有官网证据，作为初始线索
        if evidence_store:
            ev = evidence_store.get_evidence_for_claim(claim)
            if ev and ev.evidence_text:
                parts.append(
                    f"\n【官网线索（仅供参考）】\n{ev.evidence_text[:300]}"
                )

        parts.append(
            "\n请开始核查。你可以搜索最多 3 轮，每次搜索后我会把结果告诉你。"
            "当你有足够证据时，输出 verify 判定。"
        )

        return "\n".join(parts)

    def _execute_search(self, query: str) -> List[SearchResult]:
        """执行搜索，返回去重后的结果列表."""
        try:
            results = self.search_tool.search(query, max_results=self.max_search_results)
            return results
        except Exception as e:
            logger.debug(f"FactChecker[Agentic]: 搜索失败 [{query}]: {e}")
            return []

    def _fetch_page_content(self, url: str) -> str:
        """深度抓取网页完整正文（增强证据质量）.

        搜索结果摘要(snippet)通常只有一两句话，难以支撑复杂声明验证。
        本方法对高相关性 URL 抓取完整正文，用于段落级证据切片。

        安全措施：
        - SSRF 校验：复用 url_safety.validate_url 阻止内网/回环访问
        - 超时控制：deep_fetch_timeout 秒
        - 内容限长：最多 3000 字符，避免 token 膨胀

        Args:
            url: 待抓取的网页 URL

        Returns:
            清洗后的正文文本（可能为空字符串）
        """
        if not url or not url.startswith(("http://", "https://")):
            return ""

        # 缓存命中
        if url in self._page_content_cache:
            return self._page_content_cache[url]

        # SSRF 防护
        try:
            from geo_review.utils.url_safety import validate_url, SSRFError
            validate_url(url)
        except Exception as e:
            logger.debug(f"FactChecker: 深度抓取 SSRF 校验失败 [{url}]: {e}")
            self._page_content_cache[url] = ""
            return ""

        try:
            import requests
            from bs4 import BeautifulSoup

            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            }

            resp = requests.get(
                url, headers=headers, timeout=self.deep_fetch_timeout, verify=True
            )
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or "utf-8"

            soup = BeautifulSoup(resp.text, "html.parser")

            # 移除脚本、样式、导航等噪声
            for tag in soup.find_all(
                ["script", "style", "nav", "header", "footer", "aside",
                 "iframe", "noscript", "svg", "form", "button"]
            ):
                tag.decompose()

            # 按优先级尝试常见正文容器
            selectors = [
                "article", "main", ".content", ".article-body",
                ".post-content", ".entry-content", "#content",
                ".article", ".news-content", ".detail-content",
            ]

            text = ""
            for selector in selectors:
                el = soup.select_one(selector)
                if el:
                    candidate = el.get_text(separator="\n", strip=True)
                    if len(candidate) > 80:
                        text = candidate
                        break

            # 降级到 body
            if not text:
                body = soup.find("body")
                if body:
                    text = body.get_text(separator="\n", strip=True)

            if not text or len(text) < 30:
                self._page_content_cache[url] = ""
                return ""

            # 清洗：合并多余空白、限制长度
            text = re.sub(r"\n{3,}", "\n\n", text)
            text = re.sub(r"[ \t]+", " ", text)
            # 限制长度避免 token 膨胀
            if len(text) > 3000:
                text = text[:3000]

            self._page_content_cache[url] = text
            logger.debug(f"FactChecker: 深度抓取成功 [{url}] {len(text)} 字符")
            return text

        except Exception as e:
            logger.debug(f"FactChecker: 深度抓取失败 [{url}]: {e}")
            self._page_content_cache[url] = ""
            return ""

    def _deep_fetch_for_results(
        self,
        search_results: List[SearchResult],
        claim: str,
    ) -> Dict[str, str]:
        """对搜索结果中 top-k 相关性 URL 做深度抓取.

        不是所有搜索结果都需要深度抓取，只抓取与声明最相关的前 N 个 URL，
        避免抓取过多页面拖慢审核速度。

        Returns:
            {url: page_content} 字典
        """
        if not self.enable_deep_fetch:
            return {}

        # 简单按关键词命中数排序，取 top-k
        claim_keywords = self._extract_keywords_for_ranking(claim)
        scored: List[Tuple[str, int]] = []
        for sr in search_results:
            if not sr.url:
                continue
            text_lower = f"{sr.title} {sr.snippet}".lower()
            score = sum(2 for kw in claim_keywords if kw.lower() in text_lower)
            scored.append((sr.url, score))

        # 按分数降序，取前 max_deep_fetch 个去重 URL
        scored.sort(key=lambda x: x[1], reverse=True)
        seen = set()
        top_urls = []
        for url, _ in scored:
            if url not in seen:
                seen.add(url)
                top_urls.append(url)
            if len(top_urls) >= self.max_deep_fetch:
                break

        page_contents: Dict[str, str] = {}
        for url in top_urls:
            content = self._fetch_page_content(url)
            if content:
                page_contents[url] = content

        if page_contents:
            logger.info(
                f"FactChecker[Agentic]: 深度抓取 {len(page_contents)}/{len(top_urls)} 个页面成功"
            )

        return page_contents

    @staticmethod
    def _count_distinct_domains(urls: List[str]) -> int:
        """统计 URL 列表中不同域名的数量（用于多源交叉验证）.

        多源交叉验证原则：同一声明需要至少 2 个独立来源（不同域名）支持，
        才能判定为 verified。单一来源即使证据充分也降级为 partially_verified。

        Args:
            urls: 累积的所有搜索结果 URL

        Returns:
            不同域名的数量
        """
        from urllib.parse import urlparse

        domains = set()
        for url in urls:
            if not url:
                continue
            try:
                parsed = urlparse(url)
                domain = parsed.netloc.lower()
                if ":" in domain:
                    domain = domain.split(":")[0]
                # 去除 www. 前缀，视为同一域名
                if domain.startswith("www."):
                    domain = domain[4:]
                if domain:
                    domains.add(domain)
            except Exception:
                continue

        return len(domains)

    @staticmethod
    def _slice_and_rank(
        search_results: List[SearchResult],
        claim: str,
        max_chunks: int = 5,
        page_contents: Optional[Dict[str, str]] = None,
    ) -> List[Tuple[str, str, str]]:
        """证据切片 + 相关性排序（段落级，支持深度抓取正文）.

        改进点（vs 旧版）：
        - 优先使用深度抓取的完整正文，而非仅搜索摘要
        - 段落级切片（按 \\n 分段），保留完整上下文
        - 相关性排序使用 TF（词频）加权，而非简单命中计数

        Args:
            search_results: 搜索结果列表
            claim: 待验证声明
            max_chunks: 返回的最大 chunk 数
            page_contents: URL → 完整正文的映射（可选，来自深度抓取）

        Returns:
            [(chunk_text, source_url, source_title), ...]
        """
        chunks: List[Tuple[str, str, str, int]] = []

        # 从声明中提取关键词
        claim_keywords = FactChecker._extract_keywords_for_ranking(claim)

        for sr in search_results:
            # 优先使用深度抓取的完整正文，否则回退到 title + snippet
            if page_contents and sr.url in page_contents and page_contents[sr.url]:
                full_text = page_contents[sr.url]
            else:
                full_text = f"{sr.title}。{sr.snippet}".strip()

            if not full_text:
                continue

            # 计算整体相关性分数（TF 词频加权）
            full_text_lower = full_text.lower()
            doc_score = 0
            for kw in claim_keywords:
                kw_lower = kw.lower()
                # 词频加权：出现次数越多分数越高
                count = full_text_lower.count(kw_lower)
                doc_score += count * 2

            # 段落级切片（按换行分段，保留完整上下文）
            paragraphs = re.split(r'\n+', full_text)
            added = False
            for para in paragraphs:
                para = para.strip()
                if len(para) < 15:
                    continue

                # 段落级相关性：TF 词频
                para_lower = para.lower()
                para_score = 0
                for kw in claim_keywords:
                    kw_lower = kw.lower()
                    para_score += para_lower.count(kw_lower) * 2

                # 只保留有相关性的段落（段落级或文档级有命中）
                if para_score > 0 or doc_score > 0:
                    chunks.append((para, sr.url, sr.title, para_score + doc_score))
                    added = True

            # 如果没有切出有效段落，把整个 full_text 作为一个 chunk
            if not added and full_text:
                chunks.append((full_text, sr.url, sr.title, doc_score))

        # 按分数降序排序，取 top-k
        chunks.sort(key=lambda x: x[3], reverse=True)
        return [(c[0], c[1], c[2]) for c in chunks[:max_chunks]]

    @staticmethod
    def _extract_keywords_for_ranking(claim: str) -> List[str]:
        """从声明中提取用于相关性排序的关键词."""
        # 去除停用词
        stop_words = {
            "连续", "入选", "达到", "超过", "截至", "据悉", "公开信息显示",
            "荣登", "荣获", "被评为", "位列", "位居", "排名", "公认",
            "通过", "获得", "取得", "实现", "累计", "总计",
            "其", "该", "此", "的", "了", "等", "及", "与", "为", "在",
        }

        # 提取中文词组（2-6字）+ 英文词 + 数字
        cn_words = re.findall(r'[\u4e00-\u9fff]{2,6}', claim)
        en_words = re.findall(r'[a-zA-Z]{3,}', claim)
        numbers = re.findall(r'\d+(?:\.\d+)?%?', claim)

        keywords = []
        for w in cn_words:
            if w not in stop_words:
                keywords.append(w)
        keywords.extend(en_words)
        keywords.extend(numbers)

        return keywords

    @staticmethod
    def _format_evidence(
        chunks: List[Tuple[str, str, str]],
        query: str,
        round_num: int,
    ) -> str:
        """格式化证据片段为 LLM 可读的文本."""
        if not chunks:
            return f"[第{round_num}轮搜索] 搜索词: {query}\n未返回有效结果。"

        parts = [f"[第{round_num}轮搜索] 搜索词: {query}"]
        for i, (text, url, title) in enumerate(chunks, 1):
            parts.append(f"  [{i}] {text}\n      来源: {title} ({url})")

        return "\n".join(parts)

    @staticmethod
    def _verify_citation(evidence_text: str, accumulated_evidence: List[str]) -> bool:
        """引用回验 — 检查 LLM 输出的 evidence_text 是否在搜索结果中可定位.

        防止 LLM 编造证据（幻觉）。采用模糊匹配：取 evidence_text 的连续片段，
        检查是否在累积的搜索结果文本中出现。

        Args:
            evidence_text: LLM 声称引用的证据文本
            accumulated_evidence: 所有搜索轮次的格式化结果文本

        Returns:
            True 如果引用可定位（或为空），False 如果疑似编造
        """
        if not evidence_text or len(evidence_text.strip()) < 5:
            return True  # 空引用不阻止

        # 合并所有搜索结果文本
        all_text = "\n".join(accumulated_evidence)

        # 去除空白和标点后比较
        def normalize(s: str) -> str:
            return re.sub(r'[\s\u3000\W_]+', '', s)

        norm_evidence = normalize(evidence_text)
        norm_all = normalize(all_text)

        if not norm_evidence or not norm_all:
            return True

        # 取 evidence_text 的前 20、中 20、后 20 字符做三段匹配
        # 只要任一段在搜索结果中可定位，就算通过
        check_segments = []
        if len(norm_evidence) > 60:
            check_segments.append(norm_evidence[:20])
            check_segments.append(norm_evidence[len(norm_evidence)//2:len(norm_evidence)//2+20])
            check_segments.append(norm_evidence[-20:])
        else:
            check_segments.append(norm_evidence[:20] if len(norm_evidence) >= 20 else norm_evidence)

        for seg in check_segments:
            if len(seg) >= 5 and seg in norm_all:
                return True

        # 三段都没匹配到 → 疑似编造
        return False

    def _force_verdict(
        self,
        claim: str,
        snippet: str,
        accumulated_evidence: List[str],
        accumulated_urls: List[str],
        queries_used: List[str],
    ) -> FactCheckResult:
        """超过最大轮数后的强制判定 — 让 LLM 基于已有证据做最终判定."""
        result = FactCheckResult(claim=claim)
        result.snippet = snippet
        result.search_rounds = self.max_agent_rounds
        result.queries_used = queries_used
        result.search_urls = list(set(accumulated_urls))
        result.search_evidence = "\n\n".join(accumulated_evidence) if accumulated_evidence else ""

        if not accumulated_evidence:
            result.verdict = "unverifiable"
            result.confidence = 0.3
            result.reason = f"经过 {self.max_agent_rounds} 轮搜索，未找到与该声明相关的有效证据"
            result.suggestion = f"建议人工核实：{claim}"
            result.citation_verified = True
            return result

        # 让 LLM 基于累积证据做最终判定
        messages = [
            {"role": "system", "content": AGENT_SYSTEM_PROMPT},
            {"role": "user", "content": (
                f"【待验证声明】\n{claim}\n\n"
                f"【声明出处】\n{snippet}\n\n"
                f"【已搜索 {self.max_agent_rounds} 轮，以下为全部搜索结果】\n"
                f"{result.search_evidence}\n\n"
                f"搜索已达上限，请基于以上证据给出最终判定。输出 verify JSON。"
            )},
        ]

        try:
            resp = self.llm_client.chat(messages, response_format="json_object", max_retries=1)
            action = LLMClient._extract_json(resp["content"])

            result.verdict = action.get("verdict", "unverifiable")
            result.confidence = float(action.get("confidence", 0.0))
            result.reason = action.get("reason", "")
            result.suggestion = action.get("suggestion", "")
            result.evidence_text = action.get("evidence_text", "")
            result.source_type = action.get("source_type", "unknown")
            result.source_authority = action.get("source_authority", "unknown")
            result.entailment = action.get("entailment", "neutral")

            # 引用回验
            result.citation_verified = self._verify_citation(
                result.evidence_text, accumulated_evidence
            )
            if not result.citation_verified and result.verdict == "verified":
                result.verdict = "partially_verified"
                result.reason = f"证据引用无法逐字定位，可能为模型概括。原始理由: {result.reason}"
                result.confidence = max(0.0, result.confidence - 0.3)

            # 多源交叉验证 — 单一来源的 verified 降级
            if result.verdict == "verified":
                distinct_domains = self._count_distinct_domains(accumulated_urls)
                if distinct_domains < 2:
                    result.verdict = "partially_verified"
                    result.reason = (
                        f"仅找到 {distinct_domains} 个独立来源支持该声明，"
                        f"建议补充更多权威来源交叉验证。原始理由: {result.reason}"
                    )
                    result.confidence = max(0.0, result.confidence - 0.2)

            if result.verdict == "unverifiable" and not result.suggestion:
                result.suggestion = f"建议人工核实：{claim}"

        except Exception as e:
            logger.warning(f"FactChecker[Agentic]: 强制判定 LLM 调用失败: {e}")
            result.verdict = "unverifiable"
            result.confidence = 0.3
            result.reason = f"经过 {self.max_agent_rounds} 轮搜索，LLM 最终判定失败: {e}"
            result.suggestion = f"建议人工核实：{claim}"
            result.citation_verified = True

        return result

    # ------------------------------------------------------------------
    # 保留的辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _find_snippet(content: str, claim: str, context_chars: int = 60) -> str:
        """在原文中定位声明，返回包含上下文的片段."""
        idx = content.find(claim)
        if idx < 0:
            short = claim[:20]
            idx = content.find(short)
        if idx < 0:
            return claim

        start = max(0, idx - context_chars)
        end = min(len(content), idx + len(claim) + context_chars)
        snippet = content[start:end].strip()
        if start > 0:
            snippet = "..." + snippet
        if end < len(content):
            snippet = snippet + "..."
        return snippet

    @staticmethod
    def _results_to_issues(
        results: List[FactCheckResult],
        starting_id: int,
    ) -> List[Issue]:
        """将核查结果转化为审核问题.

        只有 refuted 和 unverifiable（且 confidence > 0.3）的声明才生成问题。
        verified 的不生成问题。
        """
        issues: List[Issue] = []
        idx = starting_id

        for r in results:
            # 已证实的不生成问题
            if r.verdict == "verified":
                continue

            # 无法验证且置信度很低，也不生成问题（避免噪音）
            if r.verdict == "unverifiable" and r.confidence < 0.3:
                continue

            # 确定问题严重程度
            if r.verdict == "refuted":
                severity = IssueSeverity.CRITICAL
                title = f"事实声明与公开信息不符：{r.claim[:50]}"
            elif r.verdict == "partially_verified":
                severity = IssueSeverity.MAJOR
                title = f"事实声明部分内容无法核实：{r.claim[:50]}"
            elif r.verdict == "unverifiable":
                severity = IssueSeverity.MAJOR
                title = f"事实声明无法通过联网核实：{r.claim[:50]}"
            else:
                continue

            # 构建证据
            evidence = IssueEvidence(
                snippet=r.snippet or r.claim,
                position="",
                reference_source="web_search_verification",
                reference_detail=r.reason[:2000] if r.reason else "",
                reference_field=None,
                source_url=r.search_urls[0] if r.search_urls else None,
            )

            # 生成问题
            issue = Issue(
                id=Issue.make_id(idx),
                type=IssueType.UNSUPPORTED_CLAIM,
                severity=severity,
                title=title,
                evidence=evidence,
                reason=r.reason or f"联网核查结果：{r.verdict}（置信度{r.confidence:.0%}）",
                suggestion=r.suggestion or f"建议核实该声明并提供权威来源：{r.claim}",
            )
            issues.append(issue)
            idx += 1

        return issues
