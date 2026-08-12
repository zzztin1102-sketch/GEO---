"""FactChecker — 联网事实核查模块.

三步流程：
    1. LLM 从正文中提取可验证的事实声明（如"连续5年入选五百强"）
    2. 对每条声明执行联网搜索
    3. LLM 基于搜索结果验证声明真伪，生成审核问题

用法::

    from geo_review.tools.fact_checker import FactChecker
    from geo_review.tools.web_search import WebSearchTool
    from geo_review.llm.client import LLMClient

    checker = FactChecker(llm_client=LLMClient(...), search_tool=WebSearchTool())
    issues = checker.check(content_text, submission)
"""

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from geo_review.llm.client import LLMClient
from geo_review.rules.issues import Issue, IssueEvidence, IssueSeverity, IssueType
from geo_review.tools.web_search import SearchResult, WebSearchTool, WebSearchConfig

logger = logging.getLogger(__name__)


@dataclass
class FactCheckResult:
    """单条声明核查结果."""
    claim: str = ""
    verdict: str = "unknown"  # verified / refuted / unverifiable / unknown
    confidence: float = 0.0
    snippet: str = ""
    search_evidence: str = ""
    search_urls: List[str] = field(default_factory=list)
    reason: str = ""
    suggestion: str = ""


# ====================================================================
# Prompt 模板
# ====================================================================

CLAIM_EXTRACTION_PROMPT = """你是一个事实声明提取专家。请从以下正文中提取所有需要联网核实的事实性声明。

事实性声明是指可以用公开信息验证的具体断言，包括但不限于：
- 排名/奖项类：如"连续5年入选期货服务行业五百强"、"荣获XX奖"
- 数据/统计类：如"市场占有率达30%"、"服务超过100万用户"
- 资质/认证类：如"通过ISO9001认证"、"获得XX牌照"
- 历史事实类：如"成立于2015年"、"2023年上市"
- 合作关系类：如"与XX银行达成战略合作"
- 行业地位类：如"行业领先"、"国内最大的XX平台"

注意：
- 不要提取主观评价（如"优质服务"）或无法验证的泛泛表述
- 不要提取纯常识性陈述
- 每条声明应尽量精简，保留关键可验证要素
- 最多提取10条最重要的事实声明

公司名称（如有）：{company_name}

请以 JSON 格式输出，格式如下：
{{
  "claims": [
    "声明1的原文表述",
    "声明2的原文表述"
  ]
}}"""


CLAIM_VERIFICATION_PROMPT = """你是一个事实核查专家。请基于联网搜索结果，验证以下事实声明的真实性。

【待验证声明】
{claim}

【声明出处（原文片段）】
{snippet}

【公司名称】
{company_name}

【联网搜索结果】
{search_results}

请根据搜索结果判断该声明的真实性。判断标准：
- verified（已证实）：搜索结果中有明确证据支持该声明
- refuted（已证伪）：搜索结果中有明确证据反驳该声明，或证明声明内容与事实不符
- unverifiable（无法验证）：搜索结果中没有找到相关证据，无法确认也无法否定
- partially_verified（部分证实）：声明部分内容可证实，但有关键信息无法核实或存在偏差

注意事项：
- 严格基于搜索结果判断，不要凭自身知识臆断
- 如果声明包含具体数字、年份、排名等，需在搜索结果中找到精确对应
- 公司名与声明主体必须一致，张冠李戴的视为 refuted
- 搜索结果可能包含广告或无关内容，需甄别

请以 JSON 格式输出：
{{
  "verdict": "verified|refuted|unverifiable|partially_verified",
  "confidence": 0.0-1.0,
  "reason": "判断理由，引用搜索结果中的关键信息",
  "suggestion": "如果声明有问题，给出修改建议；如果声明正确，返回空字符串"
}}"""


class FactChecker:
    """联网事实核查器.

    三步流程：提取声明 → 联网搜索 → LLM 验证
    """

    def __init__(
        self,
        llm_client: LLMClient,
        search_tool: Optional[WebSearchTool] = None,
        max_claims: int = 5,
        max_search_results: int = 5,
        search_timeout: int = 15,
    ):
        self.llm_client = llm_client
        self.search_tool = search_tool or WebSearchTool(
            WebSearchConfig(timeout=search_timeout, max_results=max_search_results)
        )
        self.max_claims = max_claims
        self.max_search_results = max_search_results

    def check(
        self,
        content: str,
        company_name: str = "",
        starting_id: int = 20001,
    ) -> Tuple[List[Issue], List[FactCheckResult]]:
        """执行联网事实核查.

        Args:
            content: 正文文本
            company_name: 公司名称（用于精确搜索）
            starting_id: 问题 ID 起始编号

        Returns:
            (审核问题列表, 核查结果详情列表)
        """
        if not content or not content.strip():
            return [], []

        start_time = time.time()
        logger.info(f"FactChecker: 开始联网事实核查, 公司={company_name or '未知'}")

        # Step 1: 提取可验证的事实声明
        claims = self._extract_claims(content, company_name)
        if not claims:
            logger.info("FactChecker: 未提取到可验证的事实声明")
            return [], []

        logger.info(f"FactChecker: 提取到 {len(claims)} 条事实声明: {claims}")

        # Step 2 & 3: 逐条搜索并验证
        all_results: List[FactCheckResult] = []
        for claim in claims[:self.max_claims]:
            result = self._verify_claim(claim, content, company_name)
            all_results.append(result)

        # 生成审核问题
        issues = self._results_to_issues(all_results, starting_id)

        elapsed = time.time() - start_time
        verified_count = sum(1 for r in all_results if r.verdict == "verified")
        refuted_count = sum(1 for r in all_results if r.verdict == "refuted")
        unverifiable_count = sum(1 for r in all_results if r.verdict == "unverifiable")

        logger.info(
            f"FactChecker: 核查完成，耗时 {elapsed:.1f}s, "
            f"已证实={verified_count}, 已证伪={refuted_count}, 无法验证={unverifiable_count}, "
            f"生成 {len(issues)} 个问题"
        )

        return issues, all_results

    def _extract_claims(self, content: str, company_name: str) -> List[str]:
        """使用 LLM 从正文中提取可验证的事实声明."""
        # 截取前 8000 字符避免 token 超限
        truncated = content[:8000]

        prompt = CLAIM_EXTRACTION_PROMPT.format(
            company_name=company_name or "未提供"
        )

        messages = [
            {"role": "system", "content": "你是事实声明提取专家，只输出JSON。"},
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

    def _verify_claim(
        self,
        claim: str,
        content: str,
        company_name: str,
    ) -> FactCheckResult:
        """对单条声明执行联网搜索 + LLM 验证.

        搜索策略：
        1. 构造多个搜索查询（完整声明、关键词提取、公司名+关键词）
        2. 逐一搜索并合并去重结果
        3. 如搜索结果中有高相关 URL，尝试抓取页面内容补充信息
        """
        result = FactCheckResult(claim=claim)

        # 提取声明在原文中的上下文片段
        snippet = self._find_snippet(content, claim)
        result.snippet = snippet

        # 构造多个搜索查询
        search_queries = self._build_search_queries(claim, company_name)

        # 执行多轮搜索并合并结果
        all_search_results: List[SearchResult] = []
        seen_urls = set()

        for query in search_queries:
            try:
                results = self.search_tool.search(query, max_results=self.max_search_results)
                for sr in results:
                    url_key = sr.url.rstrip("/").split("?")[0]
                    if url_key not in seen_urls:
                        seen_urls.add(url_key)
                        all_search_results.append(sr)
                if len(all_search_results) >= self.max_search_results * 2:
                    break
            except Exception as e:
                logger.debug(f"FactChecker: 搜索失败 [{query}]: {e}")
                continue

        if not all_search_results:
            result.verdict = "unverifiable"
            result.confidence = 0.3
            result.reason = "联网搜索未返回有效结果，无法验证该声明"
            result.suggestion = f"建议人工核实：{claim}"
            return result

        # 取前 N 个最佳结果
        search_results = all_search_results[:self.max_search_results]

        # 格式化搜索结果供 LLM 参考
        search_text_parts = []
        search_urls = []
        for i, sr in enumerate(search_results, 1):
            search_text_parts.append(
                f"[{i}] 标题: {sr.title}\n    摘要: {sr.snippet}\n    来源: {sr.url}"
            )
            if sr.url:
                search_urls.append(sr.url)

        search_text = "\n\n".join(search_text_parts)
        result.search_evidence = search_text
        result.search_urls = search_urls

        # LLM 验证
        prompt = CLAIM_VERIFICATION_PROMPT.format(
            claim=claim,
            snippet=snippet,
            company_name=company_name or "未提供",
            search_results=search_text,
        )

        messages = [
            {"role": "system", "content": "你是事实核查专家，只输出JSON。"},
            {"role": "user", "content": prompt},
        ]

        try:
            resp = self.llm_client.chat(messages, response_format="json_object", max_retries=1)
            data = LLMClient._extract_json(resp["content"])

            result.verdict = data.get("verdict", "unknown")
            result.confidence = float(data.get("confidence", 0.0))
            result.reason = data.get("reason", "")
            result.suggestion = data.get("suggestion", "")

            # 无法验证时也给出建议
            if result.verdict == "unverifiable" and not result.suggestion:
                result.suggestion = f"建议人工核实：{claim}"

        except Exception as e:
            logger.warning(f"FactChecker: LLM 验证失败 [{claim}]: {e}")
            result.verdict = "unknown"
            result.confidence = 0.0
            result.reason = f"验证过程异常: {str(e)}"
            result.suggestion = f"建议人工核实：{claim}"

        return result

    @staticmethod
    def _build_search_queries(claim: str, company_name: str) -> List[str]:
        """为一条声明构造多个搜索查询.

        策略：
        1. 完整声明（适合长声明）
        2. 提取关键词（去除修饰词，保留核心实体）
        3. 公司名 + 核心关键词
        4. 如果有具体数字/年份，单独搜数字
        """
        queries = []

        # 查询1: 完整声明
        queries.append(claim)

        # 查询2: 提取关键词（简化声明）
        keywords = FactChecker._extract_key_search_terms(claim)
        if keywords and keywords != claim:
            queries.append(keywords)

        # 查询3: 公司名 + 关键词
        if company_name:
            if keywords:
                queries.append(f"{company_name} {keywords}")
            else:
                queries.append(f"{company_name} {claim}")

        # 查询4: 提取数字+公司名（对数据类声明有效）
        numbers = re.findall(r'\d+(?:\.\d+)?%?', claim)
        if numbers and company_name:
            num_query = f"{company_name} {numbers[0]}"
            if num_query not in queries:
                queries.append(num_query)

        # 去重并限制数量
        seen = set()
        result = []
        for q in queries:
            q = q.strip()
            if q and q not in seen:
                seen.add(q)
                result.append(q)

        return result[:4]

    @staticmethod
    def _extract_key_search_terms(claim: str) -> str:
        """从声明中提取核心搜索关键词.

        策略：去除修饰词（"连续"、"入选"、"达到"等动词），
        保留名词实体（公司名、产品名、数字、机构名等）。
        """
        # 去除常见修饰动词
        stop_words = [
            "连续", "入选", "达到", "超过", "截至", "据悉", "公开信息显示",
            "荣登", "荣获", "被评为", "位列", "位居", "排名", "公认",
            "通过", "获得", "取得", "实现", "累计", "总计",
            "其", "该", "此", "的", "了", "等", "及", "与",
        ]
        cleaned = claim
        for word in stop_words:
            cleaned = cleaned.replace(word, " ")

        # 压缩空格
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()

        # 如果清理后太短（只剩公司名），返回原声明
        if len(cleaned) < 5:
            return claim

        return cleaned

    @staticmethod
    def _find_snippet(content: str, claim: str, context_chars: int = 60) -> str:
        """在原文中定位声明，返回包含上下文的片段."""
        idx = content.find(claim)
        if idx < 0:
            # 模糊匹配：取声明前 20 个字符搜索
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
