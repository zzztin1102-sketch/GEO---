"""审核结果生成器.

负责：
- 汇总规则引擎 + LLM 语义审核的结果
- 生成审核裁决、统计、修改清单
- 输出 JSON / Markdown / HTML 多格式
"""

import html
import json
import time
from datetime import datetime
from geo_review.utils.time import now as beijing_now
from typing import Any, Dict, List, Optional
from uuid import UUID

from geo_review.crawlers.website import CrawledDomain
from geo_review.models import Submission
from geo_review.result.models import (
    FailedUrl,
    ReferencesUsed,
    ReviewError,
    ReviewResponse,
    ReviewScoreCard,
    ReviewStats,
    ReviewStatus,
    ReviewVerdict,
    ReviewWarning,
)
from geo_review.rules.issues import Issue, IssueSeverity, IssueType


_SEVERITY_ORDER = {
    IssueSeverity.CRITICAL: 0,
    IssueSeverity.MAJOR: 1,
    IssueSeverity.MINOR: 2,
    IssueSeverity.INFO: 3,
}

_SEVERITY_LABEL = {
    IssueSeverity.CRITICAL: "CRITICAL",
    IssueSeverity.MAJOR: "HIGH",
    IssueSeverity.MINOR: "MEDIUM",
    IssueSeverity.INFO: "LOW",
}

_TYPE_LABEL = {
    IssueType.INCONSISTENT_WITH_SUBMISSION: "与提报表不一致",
    IssueType.INCONSISTENT_WITH_WEBSITE: "与官网不一致",
    IssueType.UNSUPPORTED_CLAIM: "无依据表述",
    IssueType.EXAGGERATION: "夸大宣传",
    IssueType.COMPETITOR_DISPARAGEMENT: "竞品拉踩",
    IssueType.SEMANTIC_RISK: "语义风险",
    IssueType.TONE_ISSUE: "语气不当",
    IssueType.GEO_CITABILITY: "GEO可引用性",
    IssueType.GEO_BRAND_CONSISTENCY: "品牌实体一致性",
}


class ReviewResultBuilder:
    """审核结果构建器.

    用法::

        builder = ReviewResultBuilder(submission=submission, content_length=len(content))
        builder.add_rule_issues(rule_issues)
        builder.add_llm_issues(llm_issues)
        builder.set_website_info(crawled, failed_urls)
        result = builder.build()
    """

    def __init__(
        self,
        submission: Optional[Submission] = None,
        content_length: int = 0,
        request_id: Optional[str] = None,
    ):
        self.submission = submission
        self.content_length = content_length
        self.request_id = request_id
        self._rule_issues: List[Issue] = []
        self._llm_issues: List[Issue] = []
        self._all_issues: List[Issue] = []
        self._warnings: List[ReviewWarning] = []
        self._references = ReferencesUsed()
        self._start_time = time.time()
        self._content_truncated = False
        self._website_data: Optional[CrawledDomain] = None
        self._failed_urls: List[tuple] = []  # (url, reason)
        self._llm_result = None
        self._plan = None

    # ------ 设置数据 ------

    def add_rule_issues(self, issues: List[Issue]):
        """添加规则引擎发现的问题（Detection: compliance 层）."""
        for issue in issues:
            if not issue.source:
                issue.source = "rule_engine"
            if not issue.detection_layer:
                issue.detection_layer = "compliance"
        self._rule_issues = list(issues)

    def add_llm_issues(self, issues: List[Issue], llm_result=None):
        """添加 LLM 语义审核发现的问题（Detection: factual / geo_quality 层）."""
        for issue in issues:
            if not issue.source:
                issue.source = "llm_reviewer"
            if not issue.detection_layer:
                # GEO 类型问题归入 geo_quality 层，其余归入 factual
                t = issue.type
                if t in (IssueType.GEO_CITABILITY, IssueType.GEO_BRAND_CONSISTENCY):
                    issue.detection_layer = "geo_quality"
                elif t in (IssueType.UNSUPPORTED_CLAIM, IssueType.INCONSISTENT_WITH_WEBSITE):
                    issue.detection_layer = "factual"
                else:
                    issue.detection_layer = "compliance"
        self._llm_issues = list(issues)
        self._llm_result = llm_result

    def add_warning(self, code: str, message: str):
        """添加警告."""
        self._warnings.append(ReviewWarning(code=code, message=message))

    def set_content_truncated(self, truncated: bool = True):
        """标记正文是否被截断."""
        self._content_truncated = truncated

    def set_plan(self, plan):
        """设置 TaskPlanner 生成的审核计划."""
        self._plan = plan

    def set_submission_source(self, source: str):
        """设置提报表来源."""
        self._references.submission_source = source

    def set_content_source(self, source: str):
        """设置正文来源."""
        self._references.content_source = source

    def set_website_info(
        self,
        crawled: Optional[CrawledDomain] = None,
        requested_urls: Optional[List[str]] = None,
        failed_urls: Optional[List[tuple]] = None,
    ):
        """设置官网爬取信息."""
        if requested_urls:
            self._references.official_urls_requested = list(requested_urls)
        if crawled and crawled.pages:
            self._references.official_urls_crawled = [p.url for p in crawled.pages]
            self._website_data = crawled
        if failed_urls:
            self._failed_urls = list(failed_urls)
            self._references.official_urls_failed = [
                FailedUrl(url=u, reason=r) for u, r in failed_urls
            ]
            # 根据失败比例加警告
            total = (
                len(self._references.official_urls_crawled)
                + len(self._references.official_urls_failed)
            )
            if total > 0 and len(self._references.official_urls_failed) == total:
                self.add_warning(
                    "URL_CRAWL_ALL_FAIL",
                    f"全部 {total} 个官网 URL 爬取失败，将跳过官网一致性检查",
                )
            elif self._references.official_urls_failed:
                self.add_warning(
                    "URL_CRAWL_PARTIAL_FAIL",
                    f"部分官网 URL 爬取失败（{len(self._references.official_urls_failed)}/{total}）",
                )

    # ------ 构建结果 ------

    def build(
        self,
        status: ReviewStatus = ReviewStatus.COMPLETED,
        error: Optional[ReviewError] = None,
    ) -> ReviewResponse:
        """构建最终审核响应."""
        # 合并 + 去重 + 排序
        all_issues = self._merge_and_dedup()
        all_issues = self._reassign_ids(all_issues)
        all_issues = self._sort_issues(all_issues)

        # 裁决逻辑
        verdict = self._compute_verdict(all_issues)

        # 统计
        stats = ReviewStats.from_issues(all_issues)

        # 修改清单
        checklist = self._build_checklist(all_issues)

        # 评分卡
        score_card = self._compute_score_card(all_issues)

        # 摘要
        summary = self._build_summary(all_issues, verdict, stats)

        # 任务名
        task_name = self.submission.task_name if self.submission else None

        # 耗时
        duration_ms = int((time.time() - self._start_time) * 1000)

        return ReviewResponse(
            request_id=UUID(self.request_id) if self.request_id else None,
            status=status,
            verdict=verdict,
            summary=summary,
            task_name=task_name,
            issues=all_issues,
            revision_checklist=checklist,
            stats=stats,
            references_used=self._references,
            warnings=self._warnings,
            llm_review=self._llm_result,
            plan_summary=self._get_plan_summary(),
            score_card=score_card,
            error=error,
            reviewed_at=beijing_now(),
            duration_ms=duration_ms,
        )

    # ------ 评分卡计算 ------

    @staticmethod
    def _compute_score_card(issues: List[Issue]) -> ReviewScoreCard:
        """根据问题列表计算多维度评分卡.

        评分逻辑：
            - 每个维度满分100，按问题严重程度扣分
            - CRITICAL: 扣15分, MAJOR: 扣8分, MINOR: 扣3分, INFO: 扣1分
            - 最低不低于0
        """
        # 扣分权重
        penalty = {
            IssueSeverity.CRITICAL: 15,
            IssueSeverity.MAJOR: 8,
            IssueSeverity.MINOR: 3,
            IssueSeverity.INFO: 1,
        }

        # 维度映射：问题类型 -> 评分维度
        dimension_map = {
            IssueType.INCONSISTENT_WITH_SUBMISSION: "factual_accuracy",
            IssueType.INCONSISTENT_WITH_WEBSITE: "factual_accuracy",
            IssueType.UNSUPPORTED_CLAIM: "factual_accuracy",
            IssueType.EXAGGERATION: "compliance",
            IssueType.COMPETITOR_DISPARAGEMENT: "compliance",
            IssueType.SEMANTIC_RISK: "content_quality",
            IssueType.TONE_ISSUE: "content_quality",
            IssueType.GEO_CITABILITY: "geo_citability",
            IssueType.GEO_BRAND_CONSISTENCY: "brand_consistency",
        }

        # 规则引擎问题（不含上述类型）默认归入 compliance
        scores = {
            "compliance": 100,
            "factual_accuracy": 100,
            "brand_consistency": 100,
            "geo_citability": 100,
            "content_quality": 100,
        }

        for issue in issues:
            dim = dimension_map.get(issue.type, "compliance")
            p = penalty.get(issue.severity, 1)
            # 低置信度问题扣分减弱（confidence=0.4 的问题只扣40%的分）
            effective_penalty = p * getattr(issue, 'confidence', 1.0)
            scores[dim] = max(0, scores[dim] - effective_penalty)

        # 综合评分 = 各维度加权平均
        weights = {
            "compliance": 0.25,
            "factual_accuracy": 0.25,
            "brand_consistency": 0.15,
            "geo_citability": 0.20,
            "content_quality": 0.15,
        }
        overall = int(round(sum(scores[k] * w for k, w in weights.items())))

        return ReviewScoreCard(
            overall=overall,
            compliance=int(round(scores["compliance"])),
            factual_accuracy=int(round(scores["factual_accuracy"])),
            brand_consistency=int(round(scores["brand_consistency"])),
            geo_citability=int(round(scores["geo_citability"])),
            content_quality=int(round(scores["content_quality"])),
        )

    # ------ 内部方法 ------

    def _get_plan_summary(self) -> Optional[Dict[str, Any]]:
        """获取审核计划的摘要信息."""
        if not self._plan:
            return None
        try:
            from geo_review.agent.planner import TaskPlanner
            planner = TaskPlanner()
            return planner.get_plan_summary(self._plan)
        except Exception:
            return {
                "task_type": getattr(self._plan, "task_type", "unknown"),
                "task_type_label": getattr(self._plan, "task_type_label", "未知"),
                "confidence": getattr(self._plan, "confidence", 0.0),
            }

    def _merge_and_dedup(self) -> List[Issue]:
        """合并规则引擎和 LLM 结果，去重."""
        rule_snippets_by_type: dict = {}
        result: List[Issue] = []

        # 先加规则引擎的（优先级高）
        for issue in self._rule_issues:
            itype = issue.type.value
            rule_snippets_by_type.setdefault(itype, []).append(issue.evidence.snippet)
            result.append(issue)

        # 再加 LLM 的，若与规则引擎的重复则跳过
        for issue in self._llm_issues:
            if not self._is_duplicate_of_any(issue, rule_snippets_by_type):
                result.append(issue)

        return result

    @classmethod
    def _is_duplicate_of_any(cls, issue: Issue, snippets_by_type: dict) -> bool:
        """判断问题是否与规则引擎的某个问题重复.

        去重策略（同类型前提下）：
        - 两个 snippet 互相有包含关系（一方完全包含另一方）
        - 或前 15 字完全相同
        """
        itype = issue.type.value
        snippet = issue.evidence.snippet

        for existing in snippets_by_type.get(itype, []):
            if snippet == existing:
                return True
            if len(snippet) >= 15 and len(existing) >= 15:
                if snippet[:15] == existing[:15]:
                    return True
            if snippet in existing or existing in snippet:
                return True
        return False

    @staticmethod
    def _reassign_ids(issues: List[Issue]) -> List[Issue]:
        """重新分配连续的 ISS-001 编号."""
        for idx, issue in enumerate(issues, start=1):
            issue.id = Issue.make_id(idx)
        return issues

    @staticmethod
    def _sort_issues(issues: List[Issue]) -> List[Issue]:
        """按严重程度 → 类型 排序."""
        return sorted(
            issues,
            key=lambda i: (
                _SEVERITY_ORDER.get(i.severity, 99),
                i.type.value,
            ),
        )

    @staticmethod
    def _compute_verdict(issues: List[Issue]) -> ReviewVerdict:
        """根据问题计算裁决.

        CRITICAL级问题 → reject（必须阻断发布）
        HIGH级问题 → revise（强烈建议修改）
        只有 MEDIUM / LOW 或无问题 → pass
        """
        for issue in issues:
            if issue.severity == IssueSeverity.CRITICAL:
                return ReviewVerdict.REJECT
        for issue in issues:
            if issue.severity == IssueSeverity.MAJOR:
                return ReviewVerdict.REVISE
        return ReviewVerdict.PASS

    @staticmethod
    def _build_checklist(issues: List[Issue]) -> List[str]:
        """生成修改清单（仅 critical + major）."""
        checklist = []
        for issue in issues:
            if issue.severity in (IssueSeverity.CRITICAL, IssueSeverity.MAJOR):
                checklist.append(f"[{issue.id}] {issue.suggestion}")
        return checklist

    def _build_summary(
        self, issues: List[Issue], verdict: ReviewVerdict, stats: ReviewStats
    ) -> str:
        """生成人类可读的审核摘要."""
        if verdict == ReviewVerdict.PASS and not issues:
            return "审核通过，未发现违规问题。"

        parts = []

        # 裁决
        if verdict == ReviewVerdict.PASS:
            parts.append("审核通过")
        elif verdict == ReviewVerdict.REJECT:
            parts.append("❌ 拒绝发布（CRITICAL级问题阻断）")
        else:
            parts.append("⚠️ 需修改后重新审核")

        # 问题统计
        if stats.total > 0:
            sev_parts = []
            for sev in ["critical", "major", "minor", "info"]:
                count = stats.by_severity.get(sev, 0)
                if count > 0:
                    label = _SEVERITY_LABEL.get(IssueSeverity(sev), sev)
                    sev_parts.append(f"{label}{count}处")
            parts.append(f"共发现{stats.total}处问题（{'，'.join(sev_parts)}）")

        # CRITICAL级问题特殊提示
        critical_count = stats.by_severity.get("critical", 0)
        if critical_count > 0:
            parts.append("CRITICAL级问题需人工复审确认")

        # 主要问题类型
        type_counts = [(t, c) for t, c in stats.by_type.items() if c > 0]
        if type_counts:
            type_counts.sort(key=lambda x: -x[1])
            type_labels = [
                f"{_TYPE_LABEL.get(IssueType(t), t)}{c}处"
                for t, c in type_counts[:3]
            ]
            parts.append(f"主要问题：{'，'.join(type_labels)}")

        # 警告信息（简短提及）
        if self._warnings:
            parts.append(f"（含{len(self._warnings)}条处理警告）")

        summary = "；".join(parts) + "。"
        if len(summary) > 2000:
            summary = summary[:1997] + "..."
        return summary


# ========================================================================
#  格式化输出
# ========================================================================

class ReviewResultFormatter:
    """审核结果格式化输出（JSON / Markdown / HTML）."""

    @staticmethod
    def to_json(result: ReviewResponse, indent: int = 2) -> str:
        """输出 JSON 格式（与 schema 完全一致）."""
        data = result.model_dump(mode="json", by_alias=True)
        return json.dumps(data, ensure_ascii=False, indent=indent)

    @staticmethod
    def to_markdown(result: ReviewResponse) -> str:
        """输出 Markdown 格式报告."""
        lines = []

        # 标题
        verdict_emoji = "✅" if result.verdict == ReviewVerdict.PASS else "⚠️"
        lines.append(f"# {verdict_emoji} GEO 生文审核报告")
        lines.append("")
        lines.append(f"- **审核 ID**: `{result.review_id}`")
        if result.task_name:
            lines.append(f"- **任务名称**: {result.task_name}")
        lines.append(f"- **审核状态**: {result.status.value}")
        lines.append(f"- **审核裁决**: **{result.verdict.value}**")
        lines.append(f"- **审核时间**: {result.reviewed_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        lines.append(f"- **处理耗时**: {result.duration_ms} ms")
        lines.append("")

        # 摘要
        lines.append("## 📋 审核摘要")
        lines.append("")
        lines.append(result.summary)
        lines.append("")

        # 统计
        lines.append("## 📊 问题统计")
        lines.append("")
        lines.append(f"- **问题总数**: {result.stats.total}")
        lines.append("")
        lines.append("**按严重程度**：")
        lines.append("")
        for sev in ["critical", "major", "minor", "info"]:
            count = result.stats.by_severity.get(sev, 0)
            label = _SEVERITY_LABEL.get(IssueSeverity(sev), sev)
            lines.append(f"- {label}: {count}")
        lines.append("")
        lines.append("**按问题类型**：")
        lines.append("")
        for t in [
            "inconsistent_with_submission",
            "inconsistent_with_website",
            "unsupported_claim",
            "exaggeration",
            "competitor_disparagement",
        ]:
            count = result.stats.by_type.get(t, 0)
            label = _TYPE_LABEL.get(IssueType(t), t)
            lines.append(f"- {label}: {count}")
        lines.append("")

        # 问题清单
        lines.append("## 🔍 问题详情")
        lines.append("")
        if not result.issues:
            lines.append("未发现问题。")
            lines.append("")
        else:
            for idx, issue in enumerate(result.issues, 1):
                sev_label = _SEVERITY_LABEL.get(issue.severity, issue.severity.value)
                type_label = _TYPE_LABEL.get(issue.type, issue.type.value)
                sev_badge = f"`{sev_label}`"
                lines.append(f"### {idx}. [{issue.id}] {issue.title}")
                lines.append("")
                lines.append(f"- **类型**: {type_label}")
                lines.append(f"- **严重程度**: {sev_badge}")
                if issue.evidence.position:
                    lines.append(f"- **位置**: {issue.evidence.position}")
                lines.append("")
                lines.append("**原文片段**：")
                lines.append("")
                lines.append(f"> {issue.evidence.snippet}")
                lines.append("")
                if issue.evidence.reference_detail:
                    lines.append("**对照依据**：")
                    lines.append("")
                    lines.append(f"- 来源: `{issue.evidence.reference_source}`")
                    lines.append(f"- 说明: {issue.evidence.reference_detail}")
                    if issue.evidence.source_url:
                        lines.append(f"- 官网来源: {issue.evidence.source_url}")
                    lines.append("")
                lines.append(f"**修改建议**: {issue.suggestion}")
                lines.append("")

        # 修改清单
        if result.revision_checklist:
            lines.append("## ✅ 修改清单")
            lines.append("")
            for item in result.revision_checklist:
                lines.append(f"- [ ] {item}")
            lines.append("")

        # 警告
        if result.warnings:
            lines.append("## ⚠️ 处理警告")
            lines.append("")
            for w in result.warnings:
                lines.append(f"- **{w.code}**: {w.message}")
            lines.append("")

        # 参考来源
        if result.references_used:
            ref = result.references_used
            lines.append("## 📚 参考信息")
            lines.append("")
            if ref.submission_source:
                lines.append(f"- **提报表来源**: {ref.submission_source}")
            if ref.content_source:
                lines.append(f"- **正文来源**: {ref.content_source}")
            if ref.official_urls_crawled:
                lines.append(f"- **成功爬取官网**: {len(ref.official_urls_crawled)} 页")
            if ref.official_urls_failed:
                lines.append(f"- **爬取失败**: {len(ref.official_urls_failed)} 个 URL")
            lines.append("")

        return "\n".join(lines)

    @staticmethod
    def to_html(result: ReviewResponse) -> str:
        """输出 HTML 格式报告."""
        def esc(s: str) -> str:
            return html.escape(s)

        verdict_cls = "pass" if result.verdict == ReviewVerdict.PASS else "revise"
        verdict_text = "审核通过" if result.verdict == ReviewVerdict.PASS else "需修改"

        issues_html = ""
        if not result.issues:
            issues_html = '<p class="no-issues">未发现问题。</p>'
        else:
            for idx, issue in enumerate(result.issues, 1):
                sev = issue.severity.value
                sev_label = _SEVERITY_LABEL.get(issue.severity, sev)
                type_label = _TYPE_LABEL.get(issue.type, issue.type.value)
                evidence = issue.evidence

                ref_html = ""
                if evidence.reference_detail:
                    source_url_html = f'<div class="ref-url">官网来源: <a href="{esc(evidence.source_url)}" target="_blank">{esc(evidence.source_url)}</a></div>' if evidence.source_url else ""
                    ref_html = f"""
                    <div class="evidence-ref">
                        <div class="ref-label">对照依据</div>
                        <div class="ref-source">来源: <code>{esc(evidence.reference_source)}</code></div>
                        <div class="ref-detail">{esc(evidence.reference_detail)}</div>
                        {source_url_html}
                    </div>
                    """

                issues_html += f"""
                <div class="issue severity-{sev}">
                    <div class="issue-header">
                        <span class="issue-num">{idx}.</span>
                        <span class="issue-id">[{esc(issue.id)}]</span>
                        <span class="issue-title">{esc(issue.title)}</span>
                        <span class="severity-badge sev-{sev}">{esc(sev_label)}</span>
                    </div>
                    <div class="issue-body">
                        <div class="issue-meta">
                            <span>类型: {esc(type_label)}</span>
                            {f'<span>位置: {esc(evidence.position)}</span>' if evidence.position else ''}
                        </div>
                        <div class="snippet">
                            <div class="snippet-label">原文片段</div>
                            <blockquote>{esc(evidence.snippet)}</blockquote>
                        </div>
                        {ref_html}
                        <div class="suggestion">
                            <strong>修改建议:</strong> {esc(issue.suggestion)}
                        </div>
                    </div>
                </div>
                """

        checklist_html = ""
        if result.revision_checklist:
            items = "".join(f"<li>{esc(item)}</li>" for item in result.revision_checklist)
            checklist_html = f"""
            <section class="section">
                <h2>✅ 修改清单</h2>
                <ul class="checklist">{items}</ul>
            </section>
            """

        warnings_html = ""
        if result.warnings:
            items = "".join(
                f'<li><code>{esc(w.code)}</code>: {esc(w.message)}</li>'
                for w in result.warnings
            )
            warnings_html = f"""
            <section class="section warnings">
                <h2>⚠️ 处理警告</h2>
                <ul>{items}</ul>
            </section>
            """

        stats_sev_html = "".join(
            f"<li>{esc(_SEVERITY_LABEL.get(IssueSeverity(s), s))}: <strong>{result.stats.by_severity.get(s, 0)}</strong></li>"
            for s in ["critical", "major", "minor", "info"]
        )
        stats_type_html = "".join(
            f"<li>{esc(_TYPE_LABEL.get(IssueType(t), t))}: <strong>{result.stats.by_type.get(t, 0)}</strong></li>"
            for t in [
                "inconsistent_with_submission",
                "inconsistent_with_website",
                "unsupported_claim",
                "exaggeration",
                "competitor_disparagement",
            ]
        )

        ref_html = ""
        if result.references_used:
            ref = result.references_used
            ref_parts = []
            if ref.submission_source:
                ref_parts.append(f"<li>提报表来源: {esc(ref.submission_source)}</li>")
            if ref.content_source:
                ref_parts.append(f"<li>正文来源: {esc(ref.content_source)}</li>")
            if ref.official_urls_crawled:
                ref_parts.append(f"<li>成功爬取官网: {len(ref.official_urls_crawled)} 页</li>")
            if ref.official_urls_failed:
                ref_parts.append(f"<li>爬取失败: {len(ref.official_urls_failed)} 个 URL</li>")
            if ref_parts:
                ref_html = f"""
                <section class="section">
                    <h2>📚 参考信息</h2>
                    <ul>{''.join(ref_parts)}</ul>
                </section>
                """

        html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>GEO 生文审核报告 - {esc(result.task_name or '未命名任务')}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
      "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
    background: #f5f7fa;
    color: #333;
    line-height: 1.6;
    padding: 20px;
  }}
  .container {{
    max-width: 900px;
    margin: 0 auto;
    background: #fff;
    border-radius: 12px;
    box-shadow: 0 2px 12px rgba(0,0,0,0.08);
    padding: 40px;
  }}
  h1 {{ font-size: 24px; margin-bottom: 20px; }}
  h2 {{
    font-size: 18px;
    margin: 30px 0 15px;
    padding-bottom: 8px;
    border-bottom: 2px solid #e8ecf1;
  }}
  .verdict-badge {{
    display: inline-block;
    padding: 6px 16px;
    border-radius: 20px;
    font-weight: 600;
    font-size: 14px;
    margin-left: 10px;
  }}
  .verdict-badge.pass {{ background: #e8f5e9; color: #2e7d32; }}
  .verdict-badge.revise {{ background: #fff3e0; color: #e65100; }}

  .meta-grid {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 10px 20px;
    margin: 15px 0;
    font-size: 14px;
    color: #666;
  }}
  .meta-grid code {{
    background: #f0f2f5;
    padding: 2px 6px;
    border-radius: 4px;
    font-size: 12px;
  }}

  .summary-box {{
    background: #f8f9fb;
    border-left: 4px solid #4a90d9;
    padding: 15px 20px;
    margin: 15px 0;
    border-radius: 0 8px 8px 0;
  }}

  .stats-grid {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 20px;
    margin: 15px 0;
  }}
  .stats-box {{
    background: #fafbfc;
    padding: 15px;
    border-radius: 8px;
  }}
  .stats-box h3 {{ font-size: 14px; margin-bottom: 10px; color: #555; }}
  .stats-box ul {{ list-style: none; }}
  .stats-box li {{ padding: 4px 0; font-size: 14px; }}

  .issue {{
    border: 1px solid #e8ecf1;
    border-radius: 8px;
    margin: 15px 0;
    overflow: hidden;
  }}
  .issue.severity-critical {{ border-left: 4px solid #d32f2f; }}
  .issue.severity-major {{ border-left: 4px solid #f57c00; }}
  .issue.severity-minor {{ border-left: 4px solid #fbc02d; }}
  .issue.severity-info {{ border-left: 4px solid #1976d2; }}

  .issue-header {{
    background: #fafbfc;
    padding: 12px 15px;
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }}
  .issue-num {{ font-weight: 600; color: #888; }}
  .issue-id {{ color: #888; font-family: monospace; font-size: 13px; }}
  .issue-title {{ font-weight: 600; flex: 1; }}
  .severity-badge {{
    padding: 2px 10px;
    border-radius: 12px;
    font-size: 12px;
    font-weight: 500;
  }}
  .sev-critical {{ background: #ffebee; color: #c62828; }}
  .sev-major {{ background: #fff3e0; color: #e65100; }}
  .sev-minor {{ background: #fffde7; color: #f9a825; }}
  .sev-info {{ background: #e3f2fd; color: #1565c0; }}

  .issue-body {{ padding: 15px; }}
  .issue-meta {{ font-size: 13px; color: #888; margin-bottom: 10px; }}
  .issue-meta span {{ margin-right: 15px; }}

  .snippet blockquote {{
    background: #f5f7fa;
    padding: 10px 15px;
    border-radius: 6px;
    margin: 8px 0;
    font-size: 14px;
    color: #444;
    border-left: 3px solid #ccc;
  }}
  .snippet-label {{ font-size: 13px; color: #666; font-weight: 500; }}

  .evidence-ref {{
    background: #f0f7ff;
    padding: 12px;
    border-radius: 6px;
    margin: 10px 0;
    font-size: 13px;
  }}
  .ref-label {{ font-weight: 600; color: #1565c0; margin-bottom: 6px; }}
  .ref-source code {{ background: #e3f2fd; padding: 1px 5px; border-radius: 3px; font-size: 12px; }}
  .ref-detail {{ margin: 4px 0; }}
  .ref-url {{ margin-top: 4px; }}
  .ref-url a {{ color: #1565c0; }}

  .suggestion {{
    background: #e8f5e9;
    padding: 10px 15px;
    border-radius: 6px;
    margin-top: 10px;
    font-size: 14px;
  }}
  .suggestion strong {{ color: #2e7d32; }}

  .checklist {{ list-style: none; padding-left: 0; }}
  .checklist li {{
    padding: 8px 0;
    border-bottom: 1px dashed #eee;
    font-size: 14px;
  }}
  .checklist li::before {{
    content: "☐ ";
    margin-right: 8px;
    color: #999;
  }}

  .warnings {{ color: #e65100; }}
  .warnings ul {{ list-style: none; padding-left: 0; }}
  .warnings li {{
    background: #fff8e1;
    padding: 10px 15px;
    border-radius: 6px;
    margin: 8px 0;
    font-size: 14px;
  }}

  .no-issues {{ text-align: center; padding: 30px; color: #888; }}

  @media (max-width: 600px) {{
    .container {{ padding: 20px; }}
    .meta-grid, .stats-grid {{ grid-template-columns: 1fr; }}
  }}
</style>
</head>
<body>
<div class="container">
  <h1>
    📝 GEO 生文审核报告
    <span class="verdict-badge {verdict_cls}">{verdict_text}</span>
  </h1>

  <div class="meta-grid">
    <div>审核 ID: <code>{esc(str(result.review_id))}</code></div>
    {"<div>任务名称: " + esc(result.task_name) + "</div>" if result.task_name else ""}
    <div>审核状态: {esc(result.status.value)}</div>
    <div>审核时间: {esc(result.reviewed_at.strftime('%Y-%m-%d %H:%M UTC'))}</div>
    <div>处理耗时: {result.duration_ms} ms</div>
  </div>

  <section class="section">
    <h2>📋 审核摘要</h2>
    <div class="summary-box">{esc(result.summary)}</div>
  </section>

  <section class="section">
    <h2>📊 问题统计</h2>
    <p style="margin-bottom:10px;">问题总数: <strong>{result.stats.total}</strong></p>
    <div class="stats-grid">
      <div class="stats-box">
        <h3>按严重程度</h3>
        <ul>{stats_sev_html}</ul>
      </div>
      <div class="stats-box">
        <h3>按问题类型</h3>
        <ul>{stats_type_html}</ul>
      </div>
    </div>
  </section>

  <section class="section">
    <h2>🔍 问题详情</h2>
    {issues_html}
  </section>

  {checklist_html}
  {warnings_html}
  {ref_html}
</div>
</body>
</html>"""

        return html_content
