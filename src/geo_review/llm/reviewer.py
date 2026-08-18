"""LLM 语义审核器 —— 调用大模型对 GEO 生文进行深度语义审核（优化版）.

优化点：
    1. 删除 _extract_json 重复实现，复用 LLMClient._extract_json（消除两套解析逻辑）
    2. 实现 quality_score 和 quality_warnings 计算
    3. ReviewResultMerger 去重逻辑增强（使用 snippet[:60] + 模糊匹配）
    4. 官网文本智能截断：基于关键词相关性提取而非简单前 N 字符
    5. 支持 confidence_threshold 和 max_issues 从配置传入
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from geo_review.crawlers.website import CrawledDomain
from geo_review.llm.client import LLMClient
from geo_review.llm.models import LLMReviewResult, LLMIssue
from geo_review.llm.prompts import build_review_messages
from geo_review.models import Submission
from geo_review.rules.issues import Issue, IssueEvidence, IssueSeverity, IssueType

logger = logging.getLogger(__name__)


class LLMReviewer:
    """基于大模型的语义审核器."""

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        confidence_threshold: float = 0.6,
        max_issues: int = 20,
    ):
        self.llm_client = llm_client
        self.confidence_threshold = confidence_threshold
        self.max_issues = max_issues

    def review(
        self,
        content: str,
        submission: Submission,
        website_data: Optional[CrawledDomain] = None,
        industry_context: str = "",
        prompt_profile: str = "general",
        evidence_context: str = "",
    ) -> LLMReviewResult:
        """对正文执行 LLM 语义审核.

        Args:
            content: 待审正文
            submission: 提报表对象
            website_data: 官网爬取数据（可选）
            industry_context: 行业知识库上下文（可选）
            prompt_profile: prompt 模板 profile（general/finance/medical/...）
            evidence_context: 官网结构化证据上下文（可选），替代原始爬取文本送入 LLM

        Returns:
            LLMReviewResult: 结构化审核结果
        """
        if self.llm_client is None:
            return LLMReviewResult(
                summary="LLM 客户端未配置，跳过语义审核",
                issues=[],
                error="LLM_CLIENT_NOT_CONFIGURED",
            )

        # 优先使用结构化证据上下文，回退到原始爬取文本智能提取
        website_text = ""
        if evidence_context:
            # 结构化证据已由 EvidenceStore.to_llm_context() 生成
            website_text = evidence_context
            logger.debug(f"LLM 审核：使用结构化证据上下文 ({len(website_text)} 字符)")
        elif website_data and website_data.pages:
            # 回退：从原始爬取文本中智能提取
            website_text = self._extract_website_summary(website_data, submission)

        # 构建 messages（支持动态 prompt 注入）
        messages = build_review_messages(
            content=content,
            submission_data=submission.model_dump(),
            website_text=website_text,
            industry_context=industry_context,
            prompt_profile=prompt_profile,
        )

        try:
            raw_response = self.llm_client.chat(
                messages=messages,
                response_format="json_object",
                max_retries=1,
            )
            content_text = raw_response.get("content", "")
        except Exception as exc:
            logger.error(f"LLM 调用失败: {exc}")
            return LLMReviewResult(
                summary="LLM 调用失败，请检查 LLM 配置",
                issues=[],
                error=f"LLM_CALL_FAILED: {exc}",
            )

        result = self._parse_llm_response(content_text, content)

        # ✅ 新增：填充模型信息和 token 使用
        result.model_used = raw_response.get("model", "")
        result.tokens_used = raw_response.get("tokens", {}).get("total", 0)
        result.processing_time = raw_response.get("duration", 0.0)
        result.retries = raw_response.get("retries", 0)
        result.cache_hit = raw_response.get("cache_hit", False)

        return result

    # ✅ 优化：官网文本智能提取
    @staticmethod
    def _extract_website_summary(
        website_data: CrawledDomain,
        submission: Submission,
    ) -> str:
        """从官网爬取数据中智能提取相关内容.

        优化策略：
        1. 基于公司名、产品名、核心主题等关键词提取相关段落
        2. 每页最多取 2000 字符，最多 3 页
        3. 若关键词匹配不足，回退到前 N 字符
        """
        if not website_data or not website_data.pages:
            return ""

        # 构建关键词列表
        keywords = []
        if submission.company_name:
            keywords.append(submission.company_name)
        if submission.product_or_service:
            keywords.extend(submission.product_or_service)
        if submission.core_topic:
            keywords.append(submission.core_topic)
        if submission.key_points:
            keywords.extend(submission.key_points)
        # 过滤掉占位符
        keywords = [k for k in keywords if k and k != "未指定" and len(k) > 1]

        parts = []
        for page in website_data.pages[:3]:
            if not page.text:
                continue

            # 如果有关键词，尝试提取相关段落
            if keywords:
                relevant_paragraphs = []
                paragraphs = page.text.split("\n")
                for para in paragraphs:
                    para = para.strip()
                    if not para or len(para) < 10:
                        continue
                    # 检查是否包含任何关键词
                    if any(kw.lower() in para.lower() for kw in keywords):
                        relevant_paragraphs.append(para)

                if relevant_paragraphs:
                    # 优先取相关段落，最多 2000 字符
                    relevant_text = "\n".join(relevant_paragraphs)
                    if len(relevant_text) > 2000:
                        relevant_text = relevant_text[:2000] + "..."
                    parts.append(f"[{page.url}]\n{relevant_text}")
                else:
                    # 回退到前 2000 字符
                    parts.append(f"[{page.url}]\n{page.text[:2000]}")
            else:
                parts.append(f"[{page.url}]\n{page.text[:2000]}")

        return "\n\n".join(parts)

    def _parse_llm_response(self, raw: str, content: str) -> LLMReviewResult:
        """解析 LLM 返回的 JSON 内容."""
        # ✅ 优化：复用 LLMClient._extract_json，消除重复代码
        try:
            data = LLMClient._extract_json(raw)
        except ValueError as e:
            return LLMReviewResult(
                summary="LLM 返回格式异常，无法解析",
                issues=[],
                error="INVALID_LLM_RESPONSE_FORMAT",
                raw_response=raw,
            )

        if not isinstance(data, dict):
            return LLMReviewResult(
                summary="LLM 返回格式异常，无法解析",
                issues=[],
                error="INVALID_LLM_RESPONSE_FORMAT",
                raw_response=raw,
            )

        summary = data.get("summary", "") or "语义审核完成"

        raw_issues = data.get("issues", [])
        if not isinstance(raw_issues, list):
            raw_issues = []

        llm_issues: List[LLMIssue] = []
        for item in raw_issues[: self.max_issues]:
            issue = self._parse_llm_issue(item, content)
            if issue:
                llm_issues.append(issue)

        result = LLMReviewResult(
            summary=summary,
            issues=llm_issues,
            raw_response=raw,
        )

        # ✅ 新增：计算 quality_score 和 quality_warnings
        result.quality_score, result.quality_warnings = self._evaluate_quality(
            result, content
        )

        return result

    # ✅ 新增：质量评估
    @staticmethod
    def _evaluate_quality(
        result: LLMReviewResult,
        content: str,
    ) -> Tuple[float, List[str]]:
        """评估 LLM 审核结果质量.

        评估维度：
        1. 问题数是否合理（0 个问题可能意味着审核不充分）
        2. snippet 是否都能在正文中定位
        3. confidence 分布是否合理
        4. 是否包含 critical 级问题（长文通常会有风险点）

        Returns:
            (quality_score 0-1, quality_warnings 列表)
        """
        warnings = []
        score = 1.0

        # 维度1：0 个问题但正文较长 — 可能是 Prompt Injection 导致的异常通过
        if not result.issues and len(content) > 500:
            score -= 0.2
            warnings.append("正文较长但未发现任何问题，请人工复核")

            # Prompt Injection 嫌疑检测：长正文 + 0 问题 + summary 含"通过/合规/无问题"
            summary_lower = (result.summary or "").lower()
            pass_keywords = ["审核通过", "合规", "无问题", "未发现", "通过", "pass", "approved"]
            if any(kw in summary_lower for kw in pass_keywords):
                score -= 0.3
                warnings.append(
                    "⚠️ 疑似 Prompt Injection：长正文零问题且 summary 直接判定通过，"
                    "请人工核查待审文本是否包含注入攻击内容"
                )

        # 维度2：snippet 定位率
        if result.issues:
            located = 0
            for issue in result.issues:
                if issue.snippet and issue.snippet[:20] in content:
                    located += 1
            location_rate = located / len(result.issues)
            if location_rate < 0.5:
                score -= 0.2
                warnings.append(f"仅 {located}/{len(result.issues)} 个问题的 snippet 能在正文中定位")

        # 维度3：置信度分布
        if result.issues:
            avg_conf = sum(i.confidence for i in result.issues) / len(result.issues)
            if avg_conf < 0.5:
                score -= 0.15
                warnings.append(f"平均置信度较低（{avg_conf:.2f}），结果可能不够可靠")

        # 维度4：critical 问题占比
        critical_count = sum(1 for i in result.issues if i.severity == IssueSeverity.CRITICAL)
        if critical_count > len(result.issues) * 0.5 and len(result.issues) > 5:
            score -= 0.1
            warnings.append("critical 问题占比过高，请检查是否存在误判")

        score = max(0.0, min(1.0, score))
        return score, warnings

    # ✅ 删除：_extract_json 方法（改用 LLMClient._extract_json 静态方法）

    def _parse_llm_issue(self, item: dict, content: str) -> Optional[LLMIssue]:
        """解析单个 LLM 返回的问题项，含字段校验."""
        if not isinstance(item, dict):
            return None

        snippet = item.get("snippet", "")
        title = item.get("title", "")
        reason = item.get("reason", "")
        suggestion = item.get("suggestion", "")

        if not title or not title.strip():
            return None

        # snippet 后处理：确保提取完整句子上下文
        snippet = snippet.strip()
        if snippet and content:
            if len(snippet) < 8 or snippet[:30] not in content:
                snippet = self._extract_sentence_from_content(content, snippet)
            else:
                has_sentence_boundary = any(c in snippet for c in '。！？.!?\n')
                if not has_sentence_boundary and len(snippet) < 30:
                    snippet = self._extract_sentence_from_content(content, snippet)

        confidence = float(item.get("confidence", 0.8))
        confidence = max(0.0, min(1.0, confidence))

        severity_str = item.get("severity", "minor")
        if confidence < self.confidence_threshold:
            severity_str = "info"

        try:
            issue_type = IssueType(item.get("type", "exaggeration"))
        except ValueError:
            issue_type = IssueType.SEMANTIC_RISK

        try:
            severity = IssueSeverity(severity_str)
        except ValueError:
            severity = IssueSeverity.MINOR

        return LLMIssue(
            type=issue_type,
            severity=severity,
            title=title.strip(),
            snippet=snippet.strip(),
            reason=reason.strip() if reason else "未提供原因",
            suggestion=suggestion.strip() if suggestion else "未提供修改建议",
            confidence=confidence,
        )

    @staticmethod
    def _extract_sentence_from_content(content: str, keyword: str) -> str:
        """从正文中提取包含关键词的完整句子."""
        if not content or not keyword:
            return keyword

        idx = content.find(keyword)
        if idx == -1 and len(keyword) > 3:
            idx = content.find(keyword[:max(3, len(keyword) // 2)])

        if idx == -1:
            return keyword

        sentence_enders = {'.', '。', '!', '！', '?', '？', '\n', ';', '；'}
        start = idx
        for i in range(idx - 1, max(-1, idx - 120), -1):
            if i < 0:
                start = 0
                break
            if content[i] in sentence_enders:
                start = i + 1
                break

        end = idx + len(keyword)
        for i in range(end, min(len(content), end + 120)):
            if content[i] in sentence_enders:
                end = i + 1
                break

        result = content[start:end].strip()
        if len(result) < 5:
            start = max(0, idx - 40)
            end = min(len(content), idx + len(keyword) + 40)
            result = content[start:end].strip()

        return result if result else keyword

    @staticmethod
    def to_standard_issues(result: LLMReviewResult, starting_id: int = 1) -> List[Issue]:
        """将 LLMReviewResult 转换为标准 Issue 列表."""
        issues = []
        for idx, li in enumerate(result.issues, start=starting_id):
            try:
                issue_type = li.type if isinstance(li.type, IssueType) else IssueType(li.type)
            except ValueError:
                issue_type = IssueType.SEMANTIC_RISK

            try:
                severity = li.severity if isinstance(li.severity, IssueSeverity) else IssueSeverity(li.severity)
            except ValueError:
                severity = IssueSeverity.MINOR

            issues.append(
                Issue(
                    id=Issue.make_id(idx),
                    type=issue_type,
                    severity=severity,
                    title=li.title,
                    evidence=IssueEvidence(
                        snippet=li.snippet or li.title,
                        reference_source="llm_semantic_review",
                        reference_detail=f"语义审核置信度: {li.confidence:.2f}",
                    ),
                    reason=li.reason,
                    suggestion=li.suggestion,
                )
            )
        return issues
