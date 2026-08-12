"""规则数据模型 — 增强版，支持权重、优先级、继承、复合条件."""

import re
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field, field_validator


class RuleSetMeta(BaseModel):
    """规则集元信息."""
    name: Optional[str] = Field(default=None, max_length=100)
    version: Optional[str] = Field(default=None, pattern=r"^\d+\.\d+(\.\d+)?$")
    industry: str = Field(default="general")
    description: Optional[str] = Field(default=None, max_length=500)
    author: Optional[str] = Field(default=None, max_length=50)
    created_at: Optional[str] = None
    extends: Optional[str] = Field(default=None, description="继承的父模板名称")
    priority: int = Field(default=100, ge=0, le=1000, description="规则集优先级，数字越小优先级越高")
    tags: List[str] = Field(default_factory=list, description="规则集标签")

    @field_validator("created_at", mode="before")
    @classmethod
    def _coerce_datetime(cls, v):
        if v is None:
            return None
        if hasattr(v, "isoformat"):
            return v.isoformat()
        return str(v)


class _BaseRule(BaseModel):
    """规则基类 — 增强版."""
    pattern: str = Field(..., min_length=1, max_length=500)
    severity: str = Field(default="major", pattern=r"^(critical|major|minor|info)$")
    description: Optional[str] = Field(default=None, max_length=500)
    enabled: bool = Field(default=True, description="是否启用")
    weight: int = Field(default=100, ge=0, le=1000, description="规则权重，数字越小优先级越高")
    category: Optional[str] = Field(default=None, description="规则分类")
    tags: List[str] = Field(default_factory=list, description="规则标签")


class ForbiddenClaimRule(_BaseRule):
    """禁用词规则."""
    is_regex: bool = Field(default=False)


class MustNotMentionRule(_BaseRule):
    """禁止提及内容规则."""
    is_regex: bool = Field(default=False)


class ExaggerationPatternRule(BaseModel):
    """夸大表述规则（强制正则）."""
    pattern: str = Field(..., min_length=1, max_length=500)
    severity: str = Field(default="major", pattern=r"^(critical|major|minor|info)$")
    description: Optional[str] = Field(default=None, max_length=500)
    enabled: bool = Field(default=True)
    weight: int = Field(default=100, ge=0, le=1000)
    category: Optional[str] = Field(default=None)
    tags: List[str] = Field(default_factory=list)

    @field_validator("pattern")
    @classmethod
    def _validate_regex(cls, v: str) -> str:
        try:
            re.compile(v)
        except re.error as exc:
            raise ValueError(f"正则表达式无效: {exc}") from exc
        return v


class CompetitorDisparagementRule(BaseModel):
    """竞品拉踩检测规则."""
    keywords: List[str] = Field(default_factory=list)
    patterns: List[str] = Field(default_factory=list)
    severity: str = Field(default="major", pattern=r"^(critical|major|minor|info)$")
    require_competitor_match: bool = Field(default=True)
    enabled: bool = Field(default=True)
    weight: int = Field(default=100, ge=0, le=1000)
    category: Optional[str] = Field(default=None)
    tags: List[str] = Field(default_factory=list)

    @field_validator("patterns", mode="before")
    @classmethod
    def _validate_patterns(cls, v):
        if not v:
            return v
        for p in v:
            try:
                re.compile(p)
            except re.error as exc:
                raise ValueError(f"正则表达式 '{p}' 无效: {exc}") from exc
        return v


class FactVerificationRule(BaseModel):
    """事实核查规则."""
    number_pattern: str = Field(
        default=r"\d+(\+\+?)?(个|家|万|%|％)?",
        min_length=1,
        max_length=500,
    )
    require_allowed_match: bool = Field(default=True)
    require_website_match: bool = Field(default=False)
    severity: str = Field(default="major", pattern=r"^(critical|major|minor|info)$")
    enabled: bool = Field(default=True)
    weight: int = Field(default=100, ge=0, le=1000)
    category: Optional[str] = Field(default=None)
    tags: List[str] = Field(default_factory=list)

    @field_validator("number_pattern")
    @classmethod
    def _validate_regex(cls, v: str) -> str:
        try:
            re.compile(v)
        except re.error as exc:
            raise ValueError(f"正则表达式无效: {exc}") from exc
        return v


class CompositeConditionRule(BaseModel):
    """复合条件规则 — 支持 AND/OR 组合多个子条件.

    示例:
        {
            "logic": "all_of",
            "conditions": [
                {"pattern": "第一", "type": "forbidden"},
                {"pattern": "遥遥领先", "type": "forbidden"}
            ],
            "severity": "critical",
            "description": "同时使用多个绝对化用语"
        }
    """
    logic: str = Field(default="all_of", pattern=r"^(all_of|any_of)$")
    conditions: List[Dict[str, Any]] = Field(..., min_length=2)
    severity: str = Field(default="major", pattern=r"^(critical|major|minor|info)$")
    description: Optional[str] = Field(default=None, max_length=500)
    enabled: bool = Field(default=True)
    weight: int = Field(default=100, ge=0, le=1000)
    category: Optional[str] = Field(default=None)
    tags: List[str] = Field(default_factory=list)


class RuleExecutionLog(BaseModel):
    """单条规则执行日志."""
    rule_type: str
    rule_pattern: str
    matched: bool
    match_count: int = 0
    duration_ms: float = 0.0
    enabled: bool = True


class RuleSet(BaseModel):
    """完整规则集 — 增强版."""
    meta: RuleSetMeta = Field(default_factory=RuleSetMeta)
    rules: dict = Field(default_factory=dict)

    @field_validator("rules", mode="before")
    @classmethod
    def _coerce_none_rules(cls, v):
        return v or {}

    def get_forbidden(self) -> List[ForbiddenClaimRule]:
        items = self.rules.get("forbidden_claims", []) or []
        return [ForbiddenClaimRule(**r) for r in items if r.get("enabled", True)]

    def get_must_not_mention(self) -> List[MustNotMentionRule]:
        items = self.rules.get("must_not_mention", []) or []
        return [MustNotMentionRule(**r) for r in items if r.get("enabled", True)]

    def get_exaggeration_patterns(self) -> List[ExaggerationPatternRule]:
        items = self.rules.get("exaggeration_patterns", []) or []
        return [ExaggerationPatternRule(**r) for r in items if r.get("enabled", True)]

    def get_competitor_disparagement(self) -> Optional[CompetitorDisparagementRule]:
        cfg = self.rules.get("competitor_disparagement")
        if not cfg or not cfg.get("enabled", True):
            return None
        return CompetitorDisparagementRule(**cfg)

    def get_fact_verification(self) -> Optional[FactVerificationRule]:
        cfg = self.rules.get("fact_verification")
        if not cfg or not cfg.get("enabled", True):
            return None
        return FactVerificationRule(**cfg)

    def get_composite_rules(self) -> List[CompositeConditionRule]:
        items = self.rules.get("composite_rules", []) or []
        return [CompositeConditionRule(**r) for r in items if r.get("enabled", True)]

    def get_required_keywords(self) -> List[str]:
        return self.rules.get("required_keywords", []) or []

    def get_min_length(self) -> Optional[int]:
        v = self.rules.get("min_length")
        return v if isinstance(v, int) else None

    def get_max_length(self) -> Optional[int]:
        v = self.rules.get("max_length")
        return v if isinstance(v, int) else None

    def get_all_rules_flat(self) -> List[Dict[str, Any]]:
        """获取所有规则项的扁平列表（用于管理和展示）."""
        result = []
        rule_categories = [
            ("forbidden_claims", "禁用词"),
            ("must_not_mention", "禁止提及"),
            ("exaggeration_patterns", "夸大表述"),
            ("composite_rules", "复合条件"),
        ]
        for key, display_name in rule_categories:
            items = self.rules.get(key, []) or []
            for idx, item in enumerate(items):
                if isinstance(item, dict):
                    result.append({
                        "id": f"{key}:{idx}",
                        "type": key,
                        "type_display": display_name,
                        **item,
                    })

        # 单例规则
        for key, display_name in [
            ("competitor_disparagement", "竞品拉踩"),
            ("fact_verification", "事实核查"),
        ]:
            cfg = self.rules.get(key)
            if isinstance(cfg, dict):
                result.append({
                    "id": key,
                    "type": key,
                    "type_display": display_name,
                    **cfg,
                })

        return result

    def update_rule(self, rule_id: str, updates: Dict[str, Any]) -> bool:
        """更新单条规则."""
        if ":" in rule_id:
            key, idx_str = rule_id.split(":", 1)
            items = self.rules.get(key, [])
            idx = int(idx_str)
            if 0 <= idx < len(items) and isinstance(items[idx], dict):
                items[idx].update(updates)
                return True
        else:
            if rule_id in self.rules and isinstance(self.rules[rule_id], dict):
                self.rules[rule_id].update(updates)
                return True
        return False

    def delete_rule(self, rule_id: str) -> bool:
        """删除单条规则."""
        if ":" in rule_id:
            key, idx_str = rule_id.split(":", 1)
            items = self.rules.get(key, [])
            idx = int(idx_str)
            if 0 <= idx < len(items):
                items.pop(idx)
                return True
        else:
            if rule_id in self.rules:
                del self.rules[rule_id]
                return True
        return False

    def add_rule(self, rule_type: str, rule_data: Dict[str, Any]) -> str:
        """添加新规则，返回规则ID."""
        if rule_type not in self.rules:
            self.rules[rule_type] = []
        if isinstance(self.rules[rule_type], list):
            self.rules[rule_type].append(rule_data)
            return f"{rule_type}:{len(self.rules[rule_type]) - 1}"
        else:
            self.rules[rule_type] = rule_data
            return rule_type