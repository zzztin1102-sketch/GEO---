"""领域知识库数据模型 — 行业专业化审核."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator


class IndustryTerm(BaseModel):
    """行业术语定义."""
    term: str = Field(..., min_length=1, max_length=100, description="术语")
    definition: str = Field(..., min_length=1, max_length=1000, description="定义")
    category: str = Field(default="general", description="分类")
    aliases: List[str] = Field(default_factory=list, description="别名/同义词")
    risk_level: str = Field(default="low", pattern=r"^(low|medium|high|critical)$")
    notes: Optional[str] = Field(default=None, max_length=500, description="使用注意事项")


class ComplianceRule(BaseModel):
    """行业合规规则."""
    rule_id: str = Field(..., min_length=1, max_length=100)
    name: str = Field(..., min_length=1, max_length=200)
    description: str = Field(..., min_length=1, max_length=1000)
    regulation_source: Optional[str] = Field(default=None, description="法规来源")
    severity: str = Field(default="major", pattern=r"^(critical|major|minor|info)$")
    category: str = Field(default="general", description="规则分类")
    keywords: List[str] = Field(default_factory=list, description="触发关键词")
    patterns: List[str] = Field(default_factory=list, description="正则模式")
    examples: List[str] = Field(default_factory=list, description="违规示例")
    counter_examples: List[str] = Field(default_factory=list, description="合规示例")
    enabled: bool = Field(default=True)

    @field_validator("patterns", mode="before")
    @classmethod
    def _validate_patterns(cls, v):
        import re
        if not v:
            return v
        for p in v:
            try:
                re.compile(p)
            except re.error as exc:
                raise ValueError(f"正则无效 '{p}': {exc}") from exc
        return v


class RiskPattern(BaseModel):
    """行业风险模式."""
    pattern_id: str = Field(..., min_length=1, max_length=100)
    name: str = Field(..., min_length=1, max_length=200)
    description: str = Field(..., min_length=1, max_length=1000)
    risk_type: str = Field(..., description="风险类型")
    severity: str = Field(default="major", pattern=r"^(critical|major|minor|info)$")
    indicators: List[str] = Field(default_factory=list, description="风险指标关键词")
    context_clues: List[str] = Field(default_factory=list, description="上下文线索")
    mitigation: Optional[str] = Field(default=None, description="规避建议")
    enabled: bool = Field(default=True)


class AuditFocus(BaseModel):
    """行业审核重点维度."""
    dimension: str = Field(..., min_length=1, max_length=100, description="审核维度")
    description: str = Field(..., min_length=1, max_length=500)
    weight: float = Field(default=1.0, ge=0.0, le=10.0, description="权重")
    check_points: List[str] = Field(default_factory=list, description="检查要点")
    llm_prompt_hint: Optional[str] = Field(default=None, description="给LLM的提示")


class IndustryKnowledgeBase(BaseModel):
    """行业知识库 — 完整定义."""

    # 元信息
    industry: str = Field(..., min_length=1, max_length=50, description="行业标识")
    name: str = Field(..., min_length=1, max_length=100, description="行业名称")
    version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+(\.\d+)?$")
    description: Optional[str] = Field(default=None, max_length=500)

    # 行业特征
    characteristics: List[str] = Field(default_factory=list, description="行业特征描述")
    target_audiences: List[str] = Field(default_factory=list, description="目标受众")
    sensitive_topics: List[str] = Field(default_factory=list, description="敏感话题")

    # 知识内容
    terms: List[IndustryTerm] = Field(default_factory=list, description="行业术语表")
    compliance_rules: List[ComplianceRule] = Field(default_factory=list, description="合规规则")
    risk_patterns: List[RiskPattern] = Field(default_factory=list, description="风险模式")
    audit_focus: List[AuditFocus] = Field(default_factory=list, description="审核重点")

    # LLM 增强
    system_prompt_addition: Optional[str] = Field(default=None, description="追加到system prompt的内容")
    review_guidelines: List[str] = Field(default_factory=list, description="审核指南")

    # 禁用词扩展（行业特定）
    forbidden_claims: List[Dict[str, Any]] = Field(default_factory=list, description="行业禁用词")
    exaggeration_patterns: List[Dict[str, Any]] = Field(default_factory=list, description="夸大表述模式")

    def get_enabled_rules(self) -> List[ComplianceRule]:
        """获取启用的合规规则."""
        return [r for r in self.compliance_rules if r.enabled]

    def get_enabled_risks(self) -> List[RiskPattern]:
        """获取启用的风险模式."""
        return [r for r in self.risk_patterns if r.enabled]

    def build_llm_context(self) -> str:
        """构建给 LLM 的行业上下文."""
        lines = [
            f"## 行业背景：{self.name}",
            "",
        ]

        if self.characteristics:
            lines.append("### 行业特征")
            for c in self.characteristics:
                lines.append(f"- {c}")
            lines.append("")

        if self.sensitive_topics:
            lines.append("### 敏感话题（需格外谨慎）")
            for s in self.sensitive_topics:
                lines.append(f"- {s}")
            lines.append("")

        if self.audit_focus:
            lines.append("### 审核重点维度")
            for af in self.audit_focus:
                lines.append(f"- **{af.dimension}**（权重{af.weight}）：{af.description}")
                for cp in af.check_points:
                    lines.append(f"  - {cp}")
            lines.append("")

        if self.terms:
            lines.append("### 关键术语")
            for t in self.terms[:20]:  # 限制数量避免过长
                lines.append(f"- **{t.term}**：{t.definition}")
                if t.notes:
                    lines.append(f"  - 注意：{t.notes}")
            lines.append("")

        if self.review_guidelines:
            lines.append("### 行业审核指南")
            for g in self.review_guidelines:
                lines.append(f"- {g}")
            lines.append("")

        if self.system_prompt_addition:
            lines.append("### 特殊要求")
            lines.append(self.system_prompt_addition)
            lines.append("")

        return "\n".join(lines)

    def to_rule_set_extensions(self) -> Dict[str, Any]:
        """转换为规则集扩展（供规则引擎使用）."""
        extensions = {}

        # 合规规则转 forbidden_claims
        if self.compliance_rules:
            forbidden = []
            for rule in self.get_enabled_rules():
                for kw in rule.keywords:
                    forbidden.append({
                        "pattern": kw,
                        "severity": rule.severity,
                        "description": rule.description,
                        "category": f"industry:{self.industry}",
                    })
            if forbidden:
                extensions["forbidden_claims"] = forbidden

        # 风险模式转 exaggeration_patterns
        if self.risk_patterns:
            patterns = []
            for risk in self.get_enabled_risks():
                for ind in risk.indicators:
                    patterns.append({
                        "pattern": ind,
                        "severity": risk.severity,
                        "description": risk.description,
                        "category": f"industry:{self.industry}",
                    })
            if patterns:
                extensions["exaggeration_patterns"] = patterns

        return extensions