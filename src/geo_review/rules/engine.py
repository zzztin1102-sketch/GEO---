"""规则执行器 — 执行硬性规则、模式规则、复合规则，输出问题列表."""

import re
import time
from typing import Any, Dict, List, Optional, Tuple

from geo_review.models import Submission, CrawledDomain
from geo_review.rules.issues import (
    Issue,
    IssueEvidence,
    IssueSeverity,
    IssueType,
)
from geo_review.rules.models import RuleSet, RuleExecutionLog


class RuleEngine:
    """规则执行器.

    执行流程:
        1. 长度检查（min/max）
        2. 禁用词检查（forbidden_claims + 提报表）
        3. 禁止提及内容（must_not_mention + 提报表）
        4. 必含关键词检查（required_keywords + 提报表 key_points）
        5. 夸大表述正则（exaggeration_patterns）
        6. 拉踩竞品检测（competitor_disparagement）
        7. 事实核查（数字/数据 vs 提报表 allowed_facts / 官网）

    提报表中的规则（forbidden_claims/must_not_mention/competitor_names）
    会与规则文件自动合并检查。
    """

    def __init__(
        self,
        rule_set: RuleSet,
        submission: Optional[Submission] = None,
        industry_kb: Optional[Any] = None,
    ):
        self.rule_set = rule_set
        self.submission = submission
        self.industry_kb = industry_kb
        self.execution_logs: List[RuleExecutionLog] = []

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    def check(
        self,
        content: str,
        *,
        website_data: Optional[CrawledDomain] = None,
    ) -> List[Issue]:
        """执行所有规则检查，返回问题列表.

        Args:
            content: 待审正文
            website_data: 官网爬取数据（用于事实核查）

        Returns:
            Issue 列表（按严重程度排序）
        """
        counter = [0]  # 共享计数器，确保 ID 全局唯一

        issues: List[Issue] = []

        # 1. 长度检查
        for issue in self._check_length(content, counter):
            issues.append(issue)

        # 2. 禁用词检查（规则文件 + 提报表）
        for issue in self._check_forbidden(content, counter):
            issues.append(issue)

        # 2.5 用词规范性检查（擦边球绝对化 + 扩展绝对化用语）
        for issue in self._check_word_regularity(content, counter):
            issues.append(issue)

        # 3. 禁止提及内容
        for issue in self._check_must_not_mention(content, counter):
            issues.append(issue)

        # 4. 联系方式泄露检测
        for issue in self._check_contact_leak(content, counter):
            issues.append(issue)

        # 5. 必含关键词（required_keywords + 提报表 key_points）
        for issue in self._check_required_keywords(content, counter):
            issues.append(issue)

        # 5.5 语句质量检测（病句、成分残缺、语义断裂）
        for issue in self._check_sentence_quality(content, counter):
            issues.append(issue)

        # 6. 核心信息交叉验证（红旗机制）
        for issue in self._check_core_info_validation(content, counter, website_data):
            issues.append(issue)

        # 7. 夸大表述
        for issue in self._check_exaggeration(content, counter):
            issues.append(issue)

        # 8. 拉踩竞品
        for issue in self._check_competitor_disparagement(content, counter):
            issues.append(issue)

        # 9. 事实核查
        if website_data:
            for issue in self._check_facts(content, website_data, counter):
                issues.append(issue)

        # 10. 复合条件规则
        for issue in self._check_composite_rules(content, counter):
            issues.append(issue)

        # 11. 行业特定合规规则
        if self.industry_kb:
            for issue in self._check_industry_compliance(content, counter):
                issues.append(issue)
            for issue in self._check_industry_risks(content, counter):
                issues.append(issue)

        # 12. GEO 可引用性审核（新增）
        for issue in self._check_geo_citability(content, counter):
            issues.append(issue)

        # 13. 品牌实体一致性审核（新增）
        for issue in self._check_geo_brand_consistency(content, counter):
            issues.append(issue)

        # 按严重程度 + 权重排序
        severity_order = {"critical": 0, "major": 1, "minor": 2, "info": 3}
        issues.sort(key=lambda x: (severity_order.get(x.severity.value, 9), x.id))

        return issues

    # ------------------------------------------------------------------
    # 1. 长度检查
    # ------------------------------------------------------------------
    def _check_length(self, content: str, counter: List[int]) -> List[Issue]:
        issues = []
        char_count = len(content)

        min_len = self.rule_set.get_min_length()
        max_len = self.rule_set.get_max_length()

        if min_len and char_count < min_len:
            counter[0] += 1
            issues.append(Issue(
                id=Issue.make_id(counter[0]),
                type=IssueType.INCONSISTENT_WITH_SUBMISSION,
                severity=IssueSeverity.MAJOR,
                title=f"正文过短（{char_count} 字符）",
                evidence=IssueEvidence(
                    snippet=content[:50] + "..." if char_count > 50 else content,
                    position="全文",
                    reference_source="review_rule",
                    reference_detail=f"规则要求至少 {min_len} 字符",
                ),
                suggestion=f"扩展正文内容至 {min_len} 字符以上",
            ))

        if max_len and char_count > max_len:
            counter[0] += 1
            issues.append(Issue(
                id=Issue.make_id(counter[0]),
                type=IssueType.INCONSISTENT_WITH_SUBMISSION,
                severity=IssueSeverity.MINOR,
                title=f"正文过长（{char_count} 字符）",
                evidence=IssueEvidence(
                    snippet=content[:50] + "...",
                    position="全文",
                    reference_source="review_rule",
                    reference_detail=f"规则要求不超过 {max_len} 字符",
                ),
                suggestion=f"精简正文至 {max_len} 字符以内",
            ))

        return issues

    # ------------------------------------------------------------------
    # 2. 禁用词检查
    # ------------------------------------------------------------------
    def _check_forbidden(self, content: str, counter: List[int]) -> List[Issue]:
        issues = []

        # 合并规则文件 + 提报表的 forbidden_claims
        rules = list(self.rule_set.get_forbidden())
        if self.submission and self.submission.forbidden_claims:
            for claim in self.submission.forbidden_claims:
                if not any(r.pattern == claim for r in rules):
                    from geo_review.rules.models import ForbiddenClaimRule
                    rules.append(ForbiddenClaimRule(pattern=claim, severity="major"))

        for rule in rules:
            matches = self._find_matches_with_positions(content, rule.pattern, rule.is_regex)
            for match_text, match_start, match_end in matches:
                snippet = self._extract_sentence_context(content, match_start, match_end)
                counter[0] += 1

                # 生成语义化的标题和原因
                if rule.pattern in ("最好", "最佳", "第一", "行业第一", "唯一", "100%"):
                    title = f"使用绝对化用语「{rule.pattern}」夸大宣传"
                    reason = f"「{rule.pattern}」属于无法验证的绝对化表述，容易使消费者产生产品/服务具有排他性优势的误解，违反广告法对绝对化用语的规范。"
                    suggestion = f"建议改为客观描述性表述，如\"在XX方面具有优势\"\"受到众多用户认可\"等，避免使用无法证实的绝对化判断。"
                else:
                    title = f"出现禁用表述「{rule.pattern}」"
                    reason = f"该表述「{rule.pattern}」在提报表或规则文件中被列为禁用，可能在宣传语境下引发合规风险或误导受众。"
                    suggestion = f"删除或替换「{rule.pattern}」，使用更客观、可验证的表述方式。"

                issues.append(Issue(
                    id=Issue.make_id(counter[0]),
                    type=IssueType.EXAGGERATION,
                    severity=IssueSeverity(rule.severity),
                    title=title,
                    evidence=IssueEvidence(
                        snippet=snippet,
                        reference_source="review_rule" if not self.submission else "submission_and_website",
                        reference_detail=rule.description or f"规则文件/提报表禁用的绝对化表述",
                        reference_field="forbidden_claims" if self.submission else None,
                    ),
                    reason=reason,
                    suggestion=suggestion,
                ))

        return issues

    # ------------------------------------------------------------------
    # 2.5 用词规范性检查（擦边球绝对化 + 扩展绝对化用语）
    # ------------------------------------------------------------------
    # 两类用词规范性问题：
    #   A. 擦边球绝对化：用"靠前/名列前茅/位居前列"等规避"第一/最佳"等绝对化用语，
    #      但语义上仍暗示排他性优势，违反广告法精神
    #   B. 扩展绝对化用语："全链条/全流程/全覆盖/全方位/全面"等无边界绝对化表述，
    #      无法验证且易产生误导
    # ------------------------------------------------------------------
    _EUPHEMISM_ABSOLUTE_PATTERNS = [
        # (模式, 标准用语, 说明)
        ('靠前', '第一/领先', '以"靠前"暗示排名优势，规避"第一"等绝对化用语，但语义仍暗示排他性优势'),
        ('名列前茅', '第一/领先', '"名列前茅"暗示排名领先地位，属于规避绝对化用语的擦边球表述'),
        ('位居前列', '第一/领先', '"位居前列"暗示排名靠前，规避绝对化用语但保留排他性暗示'),
        ('排名靠前', '第一/领先', '"排名靠前"暗示领先地位，属于规避绝对化用语的变体'),
        ('稳居前列', '第一/领先', '"稳居前列"暗示稳定领先，规避绝对化用语但保留排他性暗示'),
        ('跻身前列', '第一/领先', '"跻身前列"暗示进入领先阵营，属于规避绝对化用语的擦边球'),
        ('遥遥领先', '领先', '"遥遥领先"中程度副词"遥遥"构成绝对化暗示，无法验证且具有排他性'),
        ('一骑绝尘', '领先', '"一骑绝尘"暗示无可匹敌的领先地位，属于变相绝对化表述'),
        ('独占鳌头', '第一', '"独占鳌头"等同于"第一"的文学化表达，属于绝对化用语的变体'),
        ('傲视群雄', '领先', '"傲视群雄"暗示无可匹敌，属于变相绝对化表述'),
        ('首屈一指', '第一', '"首屈一指"等同于"第一"的成语化表达，属于绝对化用语的变体'),
        ('无出其右', '第一', '"无出其右"暗示无可超越，属于变相绝对化表述'),
    ]

    _EXTENDED_ABSOLUTE_PATTERNS = [
        # (模式, 说明, 建议替换)
        ('全链条', '"全链条"是无边界的绝对化表述，无法验证覆盖范围，易产生误导', '完整链条/主要环节'),
        ('全流程', '"全流程"是无边界的绝对化表述，无法验证是否覆盖所有流程环节', '主要流程/关键流程'),
        ('全覆盖', '"全覆盖"是无边界的绝对化表述，无法验证覆盖范围是否完整', '广泛覆盖/主要覆盖'),
        ('全方位', '"全方位"是无边界的绝对化表述，无法验证是否真正覆盖所有维度', '多维度/多个方面'),
        ('全面覆盖', '"全面覆盖"是无边界的绝对化表述，无法验证覆盖完整性', '广泛覆盖/主要覆盖'),
        ('全面领先', '"全面领先"是无依据的绝对化表述，无法在所有维度验证领先地位', '在XX方面具有优势'),
        ('全面优势', '"全面优势"是无边界的绝对化表述，无法验证所有维度均具优势', '在XX方面具有优势'),
        ('全面超越', '"全面超越"是无依据的绝对化表述，无法在所有维度验证超越', '在XX方面有所提升'),
        ('全网第一', '"全网第一"是无依据的绝对化表述，无法验证全网范围', '在XX方面领先'),
        ('全网最低', '"全网最低"是无依据的绝对化表述，无法验证全网范围', '具有价格优势'),
        ('行业领军', '"行业领军"是无依据的绝对化表述，无法验证行业领导地位', '在行业内具有影响力'),
        ('行业标杆', '"行业标杆"是无依据的绝对化表述，无法验证标杆地位', '具有行业参考价值'),
        ('无死角', '"无死角"是无边界的绝对化表述，无法验证覆盖完整性', '主要方面/关键领域'),
        ('零死角', '"零死角"是无边界的绝对化表述，无法验证覆盖完整性', '主要方面/关键领域'),
        ('一站式', '"一站式"暗示覆盖所有需求，属于无边界表述', '提供XX服务'),
        ('全面', '"全面"是无边界的绝对化表述，需具体说明覆盖范围', '在XX方面'),
    ]

    def _check_word_regularity(self, content: str, counter: List[int]) -> List[Issue]:
        """检查用词规范性：擦边球绝对化用语 + 扩展绝对化用语."""
        issues: List[Issue] = []

        # A. 擦边球绝对化用语检测
        for pattern, standard_form, explanation in self._EUPHEMISM_ABSOLUTE_PATTERNS:
            matches = self._find_matches_with_positions(content, pattern, is_regex=False)
            for match_text, match_start, match_end in matches:
                snippet = self._extract_sentence_context(content, match_start, match_end)
                counter[0] += 1
                issues.append(Issue(
                    id=Issue.make_id(counter[0]),
                    type=IssueType.EXAGGERATION,
                    severity=IssueSeverity.MAJOR,
                    title=f"使用擦边球用词「{match_text}」规避绝对化用语规范",
                    evidence=IssueEvidence(
                        snippet=snippet,
                        reference_source="review_rule",
                        reference_detail=f"用词规范性检查：{explanation}",
                    ),
                    reason=(
                        f"「{match_text}」是规避「{standard_form}」等绝对化用语的变体表述。"
                        f"虽然字面未直接使用绝对化用语，但语义上仍暗示排他性或领先地位，"
                        f"违反广告法对绝对化用语的限制精神。{explanation}"
                    ),
                    suggestion=(
                        f'建议改为可验证的客观描述，如"在XX领域具有竞争优势"'
                        f'"受到众多用户认可"，或提供权威机构认证的具体排名数据。'
                    ),
                    confidence=0.7,
                ))

        # B. 扩展绝对化用语检测
        for pattern, explanation, replacement in self._EXTENDED_ABSOLUTE_PATTERNS:
            is_regex = ".*" in pattern
            matches = self._find_matches_with_positions(content, pattern, is_regex=is_regex)
            for match_text, match_start, match_end in matches:
                snippet = self._extract_sentence_context(content, match_start, match_end)
                counter[0] += 1
                issues.append(Issue(
                    id=Issue.make_id(counter[0]),
                    type=IssueType.EXAGGERATION,
                    severity=IssueSeverity.MAJOR,
                    title=f"使用扩展绝对化用语「{match_text}」",
                    evidence=IssueEvidence(
                        snippet=snippet,
                        reference_source="review_rule",
                        reference_detail=f"用词规范性检查：{explanation}",
                    ),
                    reason=explanation + "。此类表述无法验证且易使受众产生产品/服务覆盖完整、无遗漏的误解，属于不规范宣传用语。",
                    suggestion=f'建议改为"{replacement}"等有边界、可验证的表述。',
                    confidence=0.65,
                ))

        return issues

    # ------------------------------------------------------------------
    # 3. 禁止提及内容
    # ------------------------------------------------------------------
    def _check_must_not_mention(self, content: str, counter: List[int]) -> List[Issue]:
        issues = []

        rules = list(self.rule_set.get_must_not_mention())
        if self.submission and self.submission.must_not_mention:
            for item in self.submission.must_not_mention:
                if not any(r.pattern == item for r in rules):
                    from geo_review.rules.models import MustNotMentionRule
                    rules.append(MustNotMentionRule(pattern=item, severity="critical"))

        for rule in rules:
            matches = self._find_matches_with_positions(content, rule.pattern, rule.is_regex)
            for match_text, match_start, match_end in matches:
                snippet = self._extract_sentence_context(content, match_start, match_end)
                counter[0] += 1
                issues.append(Issue(
                    id=Issue.make_id(counter[0]),
                    type=IssueType.INCONSISTENT_WITH_SUBMISSION,
                    severity=IssueSeverity(rule.severity),
                    title=f"提及禁止内容「{rule.pattern}」",
                    evidence=IssueEvidence(
                        snippet=snippet,
                        reference_source="submission_and_website" if self.submission else "review_rule",
                        reference_detail=rule.description or "敏感话题，不可提及",
                        reference_field="must_not_mention" if self.submission else None,
                    ),
                    reason=f"正文中出现了提报表/规则文件明确禁止提及的内容「{rule.pattern}」，该内容可能涉及敏感话题或不符合品牌宣传策略，存在引发争议或违规的风险。",
                    suggestion=f"删除「{rule.pattern}」相关表述，或联系审核负责人确认是否可替换为其他合规表述。",
                ))

        return issues

    # ------------------------------------------------------------------
    # 4. 联系方式泄露检测
    # ------------------------------------------------------------------
    def _check_contact_leak(self, content: str, counter: List[int]) -> List[Issue]:
        """检测正文中是否包含明文联系方式（手机号、固话、微信号、QQ号、邮箱地址）."""
        issues = []

        patterns = [
            (r"1[3-9]\d{9}", "手机号", IssueSeverity.MAJOR),
            (r"0\d{2,3}-?\d{7,8}", "固定电话", IssueSeverity.MINOR),
            (r"微信[号:：]?\s*[a-zA-Z][a-zA-Z0-9_-]{5,19}", "微信号", IssueSeverity.MAJOR),
            (r"[a-zA-Z][a-zA-Z0-9_-]{5,19}\s*[微信]", "微信号", IssueSeverity.MAJOR),
            (r"QQ[号:：]?\s*\d{5,11}", "QQ号", IssueSeverity.MINOR),
            (r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", "邮箱地址", IssueSeverity.MINOR),
        ]

        for pattern, contact_type, severity in patterns:
            try:
                regex = re.compile(pattern)
                matches = list(regex.finditer(content))
                for match in matches:
                    matched_text = match.group(0)
                    snippet = self._extract_sentence_context(content, match.start(), match.end())
                    counter[0] += 1
                    issues.append(Issue(
                        id=Issue.make_id(counter[0]),
                        type=IssueType.SEMANTIC_RISK,
                        severity=severity,
                        title=f"明文{contact_type}泄露",
                        evidence=IssueEvidence(
                            snippet=snippet,
                            reference_source="review_rule",
                            reference_detail=f"检测到明文{contact_type}：{matched_text}",
                        ),
                        reason=f"正文中出现了明文{contact_type}「{matched_text}」，可能导致客户联系信息被恶意采集或滥用，违反个人信息保护相关规定。",
                        suggestion=f"删除正文中的明文{contact_type}，如需提供联系方式，建议通过官网或联系表单等正规渠道。",
                    ))
            except re.error:
                continue

        return issues

    # ------------------------------------------------------------------
    # 5. 必含关键词（语义级提示，非强制字面匹配）
    # ------------------------------------------------------------------
    def _check_required_keywords(self, content: str, counter: List[int]) -> List[Issue]:
        """检查必含关键词 —— 仅作提示，不强制字面匹配，由LLM做语义判断."""
        issues = []

        # 合并规则文件 + 提报表 key_points
        required = list(self.rule_set.get_required_keywords())
        if self.submission and self.submission.key_points:
            for kp in self.submission.key_points:
                if kp not in required:
                    required.append(kp)

        # 仅检查明显缺失（关键词长度>3且完全未出现），降级为info
        for keyword in required:
            if not keyword.strip() or len(keyword.strip()) <= 3:
                continue
            if keyword not in content:
                counter[0] += 1
                issues.append(Issue(
                    id=Issue.make_id(counter[0]),
                    type=IssueType.INCONSISTENT_WITH_SUBMISSION,
                    severity=IssueSeverity.INFO,  # 降级为提示，不强制
                    title=f"建议关注：核心信息「{keyword[:30]}」未明确出现",
                    evidence=IssueEvidence(
                        snippet="（正文中未找到该关键词的字面表述）",
                        position="全文",
                        reference_source="submission" if self.submission else "review_rule",
                        reference_detail=f"提报表建议体现此信息点，但允许通过语义等效表达",
                        reference_field="key_points" if self.submission else None,
                    ),
                    reason=f"提报表中标注的核心信息点「{keyword}」未在正文中找到字面表述。请注意，这仅作为提示，正文可能已通过语义等效的方式表达了该信息的核心含义。",
                    suggestion=f"确认正文是否已通过其他方式表达了「{keyword}」的核心含义；如确实未涉及，建议补充相关内容以确保文案与提报意图一致。",
                    confidence=0.4,
                ))

        return issues

    # ------------------------------------------------------------------
    # 5.5 语句质量检测（病句、成分残缺、语义断裂）
    # ------------------------------------------------------------------
    def _check_sentence_quality(self, content: str, counter: List[int]) -> List[Issue]:
        """检测语句质量 — 成分残缺、语义断裂（仅保留高置信度模式）."""
        issues = []

        # 模式1: "通过...，使得..." 缺主语
        pattern1 = re.compile(r"通过[^，。；\n]{3,60}，使得[^，。；\n]{3,60}")
        for match in pattern1.finditer(content):
            counter[0] += 1
            snippet = self._extract_sentence_context(content, match.start(), match.end())
            issues.append(Issue(
                id=Issue.make_id(counter[0]),
                type=IssueType.SEMANTIC_RISK,
                severity=IssueSeverity.MAJOR,
                title="语句成分残缺（缺主语）",
                evidence=IssueEvidence(
                    snippet=snippet,
                    reference_source="review_rule",
                    reference_detail="检测到'通过...，使得...'结构，该句式缺少主语",
                ),
                reason="「通过……，使得……」是典型的成分残缺句式，缺少动作的执行者（主语），导致句子语法不通、语义模糊，严重影响阅读体验和品牌专业形象。",
                suggestion="修改为主谓宾完整的句式，如「公司通过技术创新，大幅提升了产品性能」或「技术创新使得产品性能大幅提升」。",
                confidence=0.85,
            ))

        # 模式2: "不仅..." 后面没有 "而且/还/也" 等呼应
        pattern2 = re.compile(r"不仅[^，。；\n]{3,60}(?![^，。；\n]{0,60}(?:而且|还|也|更|甚至))")
        for match in pattern2.finditer(content):
            start = max(0, match.start() - 120)
            window = content[start:match.end() + 120]
            if "而且" not in window and "还" not in window and "也" not in window and "更" not in window:
                counter[0] += 1
                snippet = self._extract_sentence_context(content, match.start(), match.end())
                issues.append(Issue(
                    id=Issue.make_id(counter[0]),
                    type=IssueType.SEMANTIC_RISK,
                    severity=IssueSeverity.MINOR,
                    title="关联词语搭配不当（不仅缺呼应）",
                    evidence=IssueEvidence(
                        snippet=snippet,
                        reference_source="review_rule",
                        reference_detail="检测到'不仅'后缺少'而且/还/也'等呼应关联词",
                    ),
                    reason="「不仅」是表示递进关系的关联词，必须与「而且」「还」「也」等呼应使用。单独使用「不仅」会导致语义断裂，读者无法获取完整信息。",
                    suggestion="补充递进呼应，如「不仅拥有先进技术，而且服务覆盖全国」。",
                    confidence=0.75,
                ))

        return issues

    # ------------------------------------------------------------------
    # 6. 核心信息交叉验证（红旗机制）
    # ------------------------------------------------------------------
    def _check_core_info_validation(self, content: str, counter: List[int], website_data=None) -> List[Issue]:
        """核心信息交叉验证 —— 对比公司名称、产品名称、业务范围与提报表/官网的一致性.

        红旗机制：
        - 公司名称不一致 → CRITICAL级
        - 产品名称不一致 → MAJOR级
        - 关键数据矛盾 → CRITICAL级
        """
        issues = []

        if not self.submission:
            return issues

        # 1. 公司名称一致性检查（仅当用户实际填写了公司名称时才检查）
        if self.submission.company_name:
            company_name = self.submission.company_name.strip()
            if company_name and company_name not in ("未指定公司", "未指定", ""):
                if company_name not in content:
                    matched = False
                    if len(company_name) > 4:
                        parts = [p.strip() for p in company_name.replace('(', ' ').replace(')', ' ').split() if len(p) > 2]
                        if parts and any(p in content for p in parts[:2]):
                            matched = True
                    if not matched:
                        counter[0] += 1
                        issues.append(Issue(
                            id=Issue.make_id(counter[0]),
                            type=IssueType.INCONSISTENT_WITH_SUBMISSION,
                            severity=IssueSeverity.CRITICAL,
                            title=f"公司名称与提报表不一致",
                            evidence=IssueEvidence(
                                snippet=content[:100] + "..." if len(content) > 100 else content,
                                position="全文",
                                reference_source="submission",
                                reference_detail=f"提报表中标注的公司名称为「{company_name}」，但正文中未明确出现",
                            ),
                            reason=f"正文中未明确提及提报表中标注的公司名称「{company_name}」，这属于核心信息不一致，可能导致读者无法确认内容归属主体，必须修改。",
                            suggestion=f"在正文开头或合适位置添加公司名称「{company_name}」，确保内容与提报主体一致。",
                        ))

        # 2. 产品/服务名称一致性检查
        if self.submission.product_or_service:
            for product in self.submission.product_or_service:
                product = product.strip()
                if not product or product in ("未指定", "未指定产品"):
                    continue
                if product not in content:
                    counter[0] += 1
                    issues.append(Issue(
                        id=Issue.make_id(counter[0]),
                        type=IssueType.INCONSISTENT_WITH_SUBMISSION,
                        severity=IssueSeverity.MAJOR,
                        title=f"产品/服务名称「{product}」未在正文中体现",
                        evidence=IssueEvidence(
                            snippet=content[:100] + "..." if len(content) > 100 else content,
                            position="全文",
                            reference_source="submission",
                            reference_detail=f"提报表中标注的产品/服务为「{product}」，但正文中未找到对应表述",
                        ),
                        reason=f"提报表明确要求文案涉及产品/服务「{product}」，但正文中未体现，可能导致文案与提报意图偏离。",
                        suggestion=f"在正文中适当位置提及产品/服务「{product}」，确保文案与提报要求一致。",
                    ))

        # 3. 核心主题偏离检查
        if self.submission.core_topic and self.submission.core_topic not in ("未指定", "未指定主题"):
            core_topic = self.submission.core_topic.strip()
            if len(core_topic) > 3 and core_topic not in content:
                counter[0] += 1
                issues.append(Issue(
                    id=Issue.make_id(counter[0]),
                    type=IssueType.INCONSISTENT_WITH_SUBMISSION,
                    severity=IssueSeverity.MINOR,
                    title=f"核心主题「{core_topic}」未在正文中直接体现",
                    evidence=IssueEvidence(
                        snippet=content[:100] + "..." if len(content) > 100 else content,
                        position="全文",
                        reference_source="submission",
                        reference_detail=f"提报表中标注的核心主题为「{core_topic}」",
                    ),
                    reason=f"提报表标注的核心主题「{core_topic}」未在正文中找到直接表述，建议确认文案是否已通过语义等效方式表达该主题。",
                    suggestion=f"如文案确实围绕「{core_topic}」展开，可不必修改；如主题明显偏离，请调整文案方向。",
                ))

        # 4. 疑似虚构内容检测 — 仅保留可验证的高风险模式
        # 注意：不检测"权威/资深/知名"等通用营销词（误报率太高）
        # 仅检测"研究显示/据调查"等需要来源支撑的表述
        unverified_source_patterns = [
            (r"(?:研究显示|据调查|数据表明|研究表明|统计显示)", "研究数据", IssueSeverity.MINOR),
        ]

        for pattern, content_type, severity in unverified_source_patterns:
            try:
                regex = re.compile(pattern)
                matches = list(regex.finditer(content))
                for match in matches:
                    matched_text = match.group(0)
                    snippet = self._extract_sentence_context(content, match.start(), match.end())
                    counter[0] += 1
                    issues.append(Issue(
                        id=Issue.make_id(counter[0]),
                        type=IssueType.UNSUPPORTED_CLAIM,
                        severity=severity,
                        title=f"疑似未标注来源的{content_type}",
                        evidence=IssueEvidence(
                            snippet=snippet,
                            reference_source="review_rule",
                            reference_detail=f"检测到{content_type}表述：{matched_text}，需核实来源",
                        ),
                        reason=f"正文中出现了{content_type}表述「{matched_text}」，但该{content_type}未在提报表或官网中找到对应依据，需人工核实来源是否真实有效。",
                        suggestion=f"请核实{content_type}「{matched_text}」的真实性和来源，如需保留请在提报表中补充相关证明材料或引用来源。",
                        confidence=0.6,
                    ))
            except re.error:
                continue

        return issues

    # ------------------------------------------------------------------
    # 7. 夸大表述
    # ------------------------------------------------------------------
    def _check_exaggeration(self, content: str, counter: List[int]) -> List[Issue]:
        issues = []

        for rule in self.rule_set.get_exaggeration_patterns():
            try:
                pattern = re.compile(rule.pattern)
            except re.error:
                continue

            for match in pattern.finditer(content):
                matched_text = match.group(0)
                snippet = self._extract_sentence_context(content, match.start(), match.end())

                counter[0] += 1
                issues.append(Issue(
                    id=Issue.make_id(counter[0]),
                    type=IssueType.EXAGGERATION,
                    severity=IssueSeverity(rule.severity),
                    title=f"存在夸大宣传表述「{matched_text}」",
                    evidence=IssueEvidence(
                        snippet=snippet,
                        reference_source="review_rule",
                        reference_detail=rule.description or "夸大宣传表述",
                    ),
                    reason=f"「{matched_text}」属于夸大或过度宣传的表述方式，容易让消费者对产品/服务的效果产生不切实际的期望，可能违反广告法关于禁止虚假或夸大宣传的规定。",
                    suggestion=f"建议改为客观、可量化的描述，如提供具体数据支撑、用户调研结果或第三方认证，避免使用无法验证的夸张表述。",
                ))

        return issues

    # ------------------------------------------------------------------
    # 6. 拉踩竞品
    # ------------------------------------------------------------------
    def _check_competitor_disparagement(self, content: str, counter: List[int]) -> List[Issue]:
        rule = self.rule_set.get_competitor_disparagement()
        if not rule:
            return []

        issues = []
        # 收集所有竞品名（提报表 + 规则）
        competitors: List[str] = []
        if self.submission and self.submission.competitor_names:
            competitors.extend(self.submission.competitor_names)

        # 如果要求竞品名匹配但竞品列表为空，不触发检测
        if rule.require_competitor_match and not competitors:
            return issues

        # 找出所有拉踩触发点
        disparagement_triggers: List[Tuple[str, int, int]] = []  # (text, start, end)

        for kw in rule.keywords:
            idx = 0
            while True:
                idx = content.find(kw, idx)
                if idx == -1:
                    break
                disparagement_triggers.append((kw, idx, idx + len(kw)))
                idx += len(kw)

        for pattern_str in rule.patterns:
            try:
                pattern = re.compile(pattern_str)
            except re.error:
                continue
            for match in pattern.finditer(content):
                disparagement_triggers.append((match.group(0), match.start(), match.end()))

        if not disparagement_triggers:
            return issues

        # 如果需要竞品名匹配，检查附近是否有竞品名
        for trigger_text, start, end in disparagement_triggers:
            if rule.require_competitor_match and competitors:
                # 向前向后各 100 字符内查找竞品
                window = content[max(0, start - 100):min(len(content), end + 100)]
                has_competitor = any(c in window for c in competitors)
                if not has_competitor:
                    continue

            # 确定竞品名
            competitor_name = None
            if competitors:
                for c in competitors:
                    if c in content[max(0, start - 100):min(len(content), end + 100)]:
                        competitor_name = c
                        break

            snippet = self._extract_sentence_context(content, start, end)

            counter[0] += 1
            issues.append(Issue(
                id=Issue.make_id(counter[0]),
                type=IssueType.COMPETITOR_DISPARAGEMENT,
                severity=IssueSeverity(rule.severity),
                title=f"存在不当对比或贬低{competitor_name or '竞品'}的表述",
                evidence=IssueEvidence(
                    snippet=snippet,
                    reference_source="review_rule",
                    reference_detail=f"拉踩触发词: 「{trigger_text}」",
                    reference_field="competitor_names" if self.submission else None,
                ),
                reason=f"正文中使用了「{trigger_text}」等带有对比贬低性质的表述，可能构成对{competitor_name or '其他品牌'}的不当贬损，违反公平竞争原则和广告法关于禁止贬低其他经营者商品的规定。",
                suggestion=f"建议删除贬低性对比表述，改为客观介绍自身产品优势，如\"我们专注于XX领域，致力于为用户提供XX价值\"，避免直接或间接贬低其他品牌。",
            ))

        return issues

    # ------------------------------------------------------------------
    # 7. 事实核查（仅作参考提示，由LLM做语义级判断）
    # ------------------------------------------------------------------
    def _check_facts(self, content: str, website_data: CrawledDomain, counter: List[int]) -> List[Issue]:
        """事实核查 —— 仅标记潜在风险点，由LLM做最终语义判断."""
        rule = self.rule_set.get_fact_verification()
        if not rule:
            return []

        issues = []

        try:
            num_pattern = re.compile(rule.number_pattern)
        except re.error:
            return issues

        # 收集允许的事实集合
        allowed_facts: List[str] = []
        if rule.require_allowed_match and self.submission:
            allowed_facts.extend(self.submission.allowed_facts)

        # 收集官网文本
        website_text = ""
        if rule.require_website_match:
            for page in website_data.pages:
                if page.text:
                    website_text += "\n" + page.text

        for match in num_pattern.finditer(content):
            number = match.group(0)
            snippet = self._extract_sentence_context(content, match.start(), match.end())

            # 仅当明显矛盾时才标记，降级为info级别
            if rule.require_allowed_match and allowed_facts:
                if not any(self._number_in_fact(number, fact) for fact in allowed_facts):
                    counter[0] += 1
                    issues.append(Issue(
                        id=Issue.make_id(counter[0]),
                        type=IssueType.UNSUPPORTED_CLAIM,
                        severity=IssueSeverity.INFO,  # 降级为提示
                        title=f"数据核对提示：「{number}」未在提报表中标注",
                        evidence=IssueEvidence(
                            snippet=snippet,
                            reference_source="submission",
                            reference_detail=f"提报表 allowed_facts 中未找到「{number}」，请确认数据来源",
                            reference_field="allowed_facts",
                        ),
                        reason=f"正文中出现了数据「{number}」，但该数据未在提报表的允许事实列表中标注来源。在宣传文案中使用无来源的数据可能构成虚假或误导性宣传。",
                        suggestion=f"核实「{number}」的数据来源，如确有其事可在提报表中补充；如无法确认，建议删除该数据或改为更保守的表述方式。",
                        confidence=0.4,
                    ))
                    continue

            if rule.require_website_match and website_text:
                if number not in website_text:
                    counter[0] += 1
                    issues.append(Issue(
                        id=Issue.make_id(counter[0]),
                        type=IssueType.INCONSISTENT_WITH_WEBSITE,
                        severity=IssueSeverity.INFO,  # 降级为提示
                        title=f"数据核对提示：「{number}」未在官网找到对应",
                        evidence=IssueEvidence(
                            snippet=snippet,
                            reference_source="website",
                            reference_detail=f"官网中未直接找到「{number}」，可能因表述方式不同",
                        ),
                        reason=f"正文中出现了数据「{number}」，但该数据未在官网内容中找到对应表述。如果该数据与官方口径不一致，可能引发信息不一致的风险。",
                        suggestion=f"核对「{number}」的官网表述方式，确认是否语义一致；如官网未提及该数据，建议删除或改为官网已确认的信息。",
                        confidence=0.35,
                    ))

        return issues

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------
    @staticmethod
    def _find_matches(content: str, pattern: str, is_regex: bool) -> List[str]:
        """查找匹配项."""
        if is_regex:
            try:
                regex = re.compile(pattern)
                return [m.group(0) for m in regex.finditer(content)]
            except re.error:
                return []
        else:
            # 简单关键词匹配
            matches = []
            idx = 0
            while True:
                idx = content.find(pattern, idx)
                if idx == -1:
                    break
                matches.append(pattern)
                idx += len(pattern)
            return matches

    @staticmethod
    def _find_matches_with_positions(content: str, pattern: str, is_regex: bool) -> List[tuple]:
        """查找匹配项并返回位置信息.

        Returns:
            List[(match_text, start, end)]
        """
        if is_regex:
            try:
                regex = re.compile(pattern)
                return [(m.group(0), m.start(), m.end()) for m in regex.finditer(content)]
            except re.error:
                return []
        else:
            matches = []
            idx = 0
            while True:
                idx = content.find(pattern, idx)
                if idx == -1:
                    break
                matches.append((pattern, idx, idx + len(pattern)))
                idx += len(pattern)
            return matches

    @staticmethod
    def _extract_sentence_context(content: str, match_start: int, match_end: int, max_chars: int = 120) -> str:
        """提取包含匹配文本的完整句子上下文.

        向前向后扩展到句子边界，确保返回有语义完整的句子或子句。

        Args:
            content: 正文内容
            match_start: 匹配文本起始位置
            match_end: 匹配文本结束位置
            max_chars: 最大长度限制

        Returns:
            包含匹配文本的完整句子上下文
        """
        if not content or match_start < 0 or match_end > len(content):
            return content[max(0, match_start - 20):match_end + 20]

        # 句子边界标点
        sentence_enders = {'.', '。', '!', '！', '?', '？', '\n', ';', '；'}
        # 向前找句子起始
        start = match_start
        found_start = False
        for i in range(match_start - 1, max(-1, match_start - max_chars), -1):
            if i < 0:
                start = 0
                found_start = True
                break
            if content[i] in sentence_enders:
                start = i + 1
                found_start = True
                break
        if not found_start:
            start = max(0, match_start - max_chars // 2)

        # 向后找句子结束
        end = match_end
        found_end = False
        for i in range(match_end, min(len(content), match_end + max_chars)):
            if content[i] in sentence_enders:
                end = i + 1
                found_end = True
                break
        if not found_end:
            end = min(len(content), match_end + max_chars // 2)

        result = content[start:end].strip()
        # 如果结果太短，扩展一些上下文
        if len(result) < 10:
            start = max(0, match_start - 30)
            end = min(len(content), match_end + 30)
            result = content[start:end].strip()

        return result

    # ------------------------------------------------------------------
    # 8. 复合条件规则
    # ------------------------------------------------------------------
    def _check_composite_rules(self, content: str, counter: List[int]) -> List[Issue]:
        issues = []

        for rule in self.rule_set.get_composite_rules():
            start_time = time.perf_counter()
            conditions_met = []

            for cond in rule.conditions:
                cond_type = cond.get("type", "forbidden")
                pattern = cond.get("pattern", "")
                is_regex = cond.get("is_regex", False)

                if cond_type == "forbidden":
                    matches = self._find_matches(content, pattern, is_regex)
                    conditions_met.append(len(matches) > 0)
                elif cond_type == "must_not_mention":
                    matches = self._find_matches(content, pattern, is_regex)
                    conditions_met.append(len(matches) > 0)
                elif cond_type == "contains":
                    conditions_met.append(pattern in content)
                elif cond_type == "regex":
                    try:
                        pat = re.compile(pattern)
                        conditions_met.append(bool(pat.search(content)))
                    except re.error:
                        conditions_met.append(False)
                else:
                    conditions_met.append(False)

            # 判断逻辑
            triggered = False
            if rule.logic == "all_of":
                triggered = all(conditions_met) and len(conditions_met) == len(rule.conditions)
            elif rule.logic == "any_of":
                triggered = any(conditions_met)

            duration_ms = (time.perf_counter() - start_time) * 1000
            self.execution_logs.append(RuleExecutionLog(
                rule_type="composite_rule",
                rule_pattern=f"{rule.logic}:{len(rule.conditions)} conditions",
                matched=triggered,
                match_count=sum(conditions_met),
                duration_ms=duration_ms,
                enabled=True,
            ))

            if triggered:
                counter[0] += 1
                # 尝试找到一个触发条件的位置来提取上下文
                trigger_snippet = content[:100] + "..." if len(content) > 100 else content
                for cond in rule.conditions:
                    pattern = cond.get("pattern", "")
                    is_regex = cond.get("is_regex", False)
                    if pattern:
                        matches = self._find_matches_with_positions(content, pattern, is_regex)
                        if matches:
                            trigger_snippet = self._extract_sentence_context(
                                content, matches[0][1], matches[0][2]
                            )
                            break

                issues.append(Issue(
                    id=Issue.make_id(counter[0]),
                    type=IssueType.EXAGGERATION,
                    severity=IssueSeverity(rule.severity),
                    title=rule.description or f"复合条件触发（{rule.logic}）",
                    evidence=IssueEvidence(
                        snippet=trigger_snippet,
                        reference_source="review_rule",
                        reference_detail=f"复合规则: {rule.logic}，满足 {sum(conditions_met)}/{len(rule.conditions)} 个条件",
                    ),
                    reason=f"该表述同时满足多个风险条件（{rule.logic}），属于复合型违规场景，存在叠加风险，需要综合评估修改。",
                    suggestion="请根据复合规则的具体条件，逐项检查并修改相关表述，确保同时满足所有合规要求。",
                ))

        return issues

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------
    @staticmethod
    def _number_in_fact(number: str, fact: str) -> bool:
        """检查数字是否在 fact 表述中."""
        # 直接包含
        if number in fact:
            return True
        # 提取 fact 中的所有数字
        numbers = re.findall(r"\d+", fact)
        return number in numbers

    # ------------------------------------------------------------------
    # 9. 行业特定合规规则
    # ------------------------------------------------------------------
    def _check_industry_compliance(self, content: str, counter: List[int]) -> List[Issue]:
        """执行行业合规规则检查."""
        if not self.industry_kb:
            return []

        issues = []
        for rule in self.industry_kb.get_enabled_rules():
            for kw in rule.keywords:
                # 使用位置查找来获取上下文
                matches = self._find_matches_with_positions(content, kw, False)
                if matches:
                    match_text, match_start, match_end = matches[0]
                    snippet = self._extract_sentence_context(content, match_start, match_end)
                    counter[0] += 1
                    issues.append(Issue(
                        id=Issue.make_id(counter[0]),
                        type=IssueType(rule.category.lower().replace(" ", "_")) if rule.category.lower().replace(" ", "_") in [t.value for t in IssueType] else IssueType.SEMANTIC_RISK,
                        severity=IssueSeverity(rule.severity),
                        title=f"【{self.industry_kb.name}】{rule.name}",
                        evidence=IssueEvidence(
                            snippet=snippet,
                            reference_source="industry_kb",
                            reference_detail=f"{rule.description}（来源：{rule.regulation_source or '行业规则'}）",
                        ),
                        reason=f"该表述触发了{self.industry_kb.name}的合规规则「{rule.name}」，{rule.description}。在行业宣传中，此类表述可能面临监管风险或引发消费者投诉。",
                        suggestion=f"请按照{self.industry_kb.name}的合规要求修改相关表述，建议参考{rule.regulation_source or '行业规范'}中的标准表述方式，确保内容符合行业监管要求。",
                    ))
                    break  # 每个规则只触发一次
        return issues

    # ------------------------------------------------------------------
    # 10. 行业风险模式
    # ------------------------------------------------------------------
    def _check_industry_risks(self, content: str, counter: List[int]) -> List[Issue]:
        """执行行业风险模式检查."""
        if not self.industry_kb:
            return []

        issues = []
        for risk in self.industry_kb.get_enabled_risks():
            for indicator in risk.indicators:
                try:
                    pattern = re.compile(indicator)
                    matches = list(pattern.finditer(content))
                    if matches:
                        counter[0] += 1
                        first_match = matches[0]
                        snippet = self._extract_sentence_context(content, first_match.start(), first_match.end())

                        issues.append(Issue(
                            id=Issue.make_id(counter[0]),
                            type=IssueType.SEMANTIC_RISK,
                            severity=IssueSeverity(risk.severity),
                            title=f"【{self.industry_kb.name}】{risk.name}",
                            evidence=IssueEvidence(
                                snippet=snippet,
                                reference_source="industry_kb",
                                reference_detail=f"{risk.description}\n风险类型：{risk.risk_type}",
                            ),
                            reason=f"该表述触发了{self.industry_kb.name}的风险模式「{risk.name}」。{risk.description}，属于{risk.risk_type}风险，可能在特定场景下引发用户误解或监管关注。",
                            suggestion=risk.mitigation or f"请检查相关表述，规避{risk.risk_type}风险。建议采用更客观、中性的表达方式，避免使用可能引发{risk.risk_type}的措辞。",
                        ))
                        break  # 每个风险模式只触发一次
                except re.error:
                    continue
        return issues

    # ------------------------------------------------------------------
    # 12. GEO 可引用性审核（精简版 — 仅保留高置信度检查）
    # ------------------------------------------------------------------
    def _check_geo_citability(self, content: str, counter: List[int]) -> List[Issue]:
        """GEO 可引用性审核 — 仅做确定性检查，语义判断交给 LLM.

        保留检查：
            A. 明确实体：泛称/代词占比过高（可量化检测）
            B. 模糊引用：检测"据说/据悉/众所周知"等无来源表述
        已移除（交给 LLM 做语义判断）：
            - 权威来源引用（正则匹配误报率高）
            - 结构化信息（LLM 判断更准确）
        """
        issues: List[Issue] = []

        # A. 明确实体检测 — 泛称/代词占比
        generic_words = re.findall(r'我们|其|该司|该公司|本平台|此产品|该产品|该服务', content)
        entity_names = re.findall(
            r'(?:[\u4e00-\u9fff]{2,8}(?:公司|集团|科技|平台|银行|保险|证券|基金|医院|大学|学院|研究院))|'
            r'(?:[A-Z][a-zA-Z]*(?:\s[A-Z][a-zA-Z]*){0,3})',
            content
        )

        if len(generic_words) > 5 and len(entity_names) < 2:
            generic_count = len(generic_words)
            counter[0] += 1
            issues.append(Issue(
                id=Issue.make_id(counter[0]),
                type=IssueType.GEO_CITABILITY,
                severity=IssueSeverity.MINOR,
                title="实体名称不够明确，AI搜索可能难以识别内容归属",
                evidence=IssueEvidence(
                    snippet=content[:100] + "..." if len(content) > 100 else content,
                    position="全文",
                    reference_source="review_rule",
                    reference_detail=f"检测到 {generic_count} 处泛称/代词，但仅 {len(entity_names)} 处明确实体名称",
                ),
                reason=f"正文中使用了大量泛称或代词（{generic_count}处），而明确的实体名称较少（{len(entity_names)}处）。AI搜索引擎在提取实体时可能无法准确判断内容归属，影响GEO排名效果。",
                suggestion="建议在正文开头及关键位置使用完整的公司名称、品牌名称，减少'我们''该平台'等泛称，确保AI能准确识别内容主体。",
                confidence=0.8,
            ))

        # B. 模糊引用检测 — 仅检测需要来源支撑的表述
        vague_refs = re.findall(r'据说|据悉|众所周知|大家都知道|业界公认', content)
        if vague_refs and len(content) > 200:
            counter[0] += 1
            issues.append(Issue(
                id=Issue.make_id(counter[0]),
                type=IssueType.GEO_CITABILITY,
                severity=IssueSeverity.MAJOR,
                title="使用了模糊引用，缺乏具体事实支撑",
                evidence=IssueEvidence(
                    snippet=self._extract_sentence_context(content, content.find(vague_refs[0]), content.find(vague_refs[0]) + len(vague_refs[0])),
                    reference_source="review_rule",
                    reference_detail=f"检测到模糊引用词：{'、'.join(vague_refs[:3])}",
                ),
                reason=f"正文中使用了{'、'.join(vague_refs[:3])}等模糊引用词，这些表述无法验证且缺乏具体事实支撑。AI搜索引擎对这类模糊表述的信任度较低，可能影响内容在搜索结果中的排名。",
                suggestion="将模糊引用改为具体事实，如'据XX行业报告2024年数据''通过XX权威机构认证''在XX评测中排名前X'等可验证的表述。",
                confidence=0.75,
            ))

        return issues

    # ------------------------------------------------------------------
    # 13. 品牌实体一致性审核（精简版 — 仅保留与提报表的确定性比对）
    # ------------------------------------------------------------------
    def _check_geo_brand_consistency(self, content: str, counter: List[int]) -> List[Issue]:
        """品牌实体一致性审核 — 仅做确定性比对，语义判断交给 LLM.

        保留检查：
            A. 品牌名称一致性（与提报表比对）
        已移除（交给 LLM 做语义判断）：
            - 产品定义清晰度（语义层面，正则误报率高）
            - 能力边界（与 _check_word_regularity 重叠）
        """
        issues: List[Issue] = []

        if not self.submission:
            return issues

        # A. 品牌名称一致性（与提报表对比）
        if self.submission.company_name and self.submission.company_name not in ("未指定公司", "未指定", ""):
            company = self.submission.company_name.strip()
            if company not in content:
                short_names = [company[:2], company[:3], company[:4]]
                has_short = any(sn in content and sn != company for sn in short_names if len(sn) >= 2)
                if has_short:
                    counter[0] += 1
                    issues.append(Issue(
                        id=Issue.make_id(counter[0]),
                        type=IssueType.GEO_BRAND_CONSISTENCY,
                        severity=IssueSeverity.MINOR,
                        title="品牌名称使用了简称，AI可能无法准确关联品牌实体",
                        evidence=IssueEvidence(
                            snippet=content[:100] + "..." if len(content) > 100 else content,
                            position="全文",
                            reference_source="submission",
                            reference_detail=f"提报表中的公司全称为「{company}」，但正文中未使用全称",
                        ),
                        reason=f"正文中未使用完整的公司名称「{company}」，AI搜索引擎在实体识别时可能无法准确将简称与品牌知识图谱关联，影响品牌搜索的准确性和优先级。",
                        suggestion=f"建议在正文首次出现时使用完整公司名称「{company}」，后续可适当使用简称，确保AI能建立准确的品牌实体关联。",
                        confidence=0.9,
                    ))

        return issues