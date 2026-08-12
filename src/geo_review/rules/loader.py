"""规则文件加载器 — 支持 YAML / JSON 加载、继承、合并、验证."""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Union

import yaml

from geo_review.rules.models import RuleSet

logger = logging.getLogger(__name__)


class RuleLoader:
    """规则文件加载器.

    支持:
        - 从 YAML / JSON 文件加载
        - 从字符串 / 字典加载
        - 规则集继承（extends 机制）
        - 多个规则集合并（按优先级覆盖）
        - 内置行业模板加载
    """

    _TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "rule_templates"

    # ------------------------------------------------------------------
    # 公共加载方法
    # ------------------------------------------------------------------
    @classmethod
    def from_yaml(cls, path: Union[str, Path]) -> RuleSet:
        """从 YAML 文件加载规则."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"规则文件不存在: {path}")
        text = path.read_text(encoding="utf-8")
        return cls.from_yaml_string(text, source=str(path))

    @classmethod
    def from_yaml_string(cls, text: str, source: str = "string") -> RuleSet:
        """从 YAML 字符串加载规则."""
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise ValueError(f"YAML 解析失败 ({source}): {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"规则文件格式错误: 顶层须为对象 ({source})")
        return cls.from_dict(data)

    @classmethod
    def from_json_string(cls, text: str, source: str = "string") -> RuleSet:
        """从 JSON 字符串加载规则."""
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON 解析失败 ({source}): {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"规则文件格式错误: 顶层须为对象 ({source})")
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: Dict) -> RuleSet:
        """从字典加载规则."""
        try:
            return RuleSet.model_validate(data)
        except Exception as exc:
            raise ValueError(f"规则验证失败: {exc}") from exc

    # ------------------------------------------------------------------
    # 内置模板加载（支持继承）
    # ------------------------------------------------------------------
    @classmethod
    def from_template(cls, industry: str = "general") -> RuleSet:
        """从内置行业模板加载规则（自动解析 extends 继承链）.

        Args:
            industry: 行业标识（general/finance/medical/education/...）
        """
        template_path = cls._TEMPLATES_DIR / f"{industry}.yaml"
        if not template_path.exists():
            raise FileNotFoundError(
                f"行业模板 '{industry}' 不存在，可用: {cls._list_templates()}"
            )

        # 加载当前模板
        rule_set = cls.from_yaml(template_path)

        # 解析继承链
        if rule_set.meta.extends:
            parent_name = rule_set.meta.extends
            try:
                parent = cls.from_template(parent_name)
                # 子模板覆盖父模板（子模板优先级更高）
                rule_set = cls.merge(parent, rule_set)
                logger.info(
                    f"规则模板 '{industry}' 继承自 '{parent_name}'，已合并"
                )
            except FileNotFoundError:
                logger.warning(
                    f"规则模板 '{industry}' 声明继承 '{parent_name}'，"
                    f"但父模板不存在，仅使用当前模板"
                )

        return rule_set

    @classmethod
    def _list_templates(cls) -> List[str]:
        if not cls._TEMPLATES_DIR.exists():
            return []
        return [p.stem for p in cls._TEMPLATES_DIR.glob("*.yaml")]

    @classmethod
    def list_templates(cls) -> List[str]:
        """列出所有可用模板."""
        return cls._list_templates()

    # ------------------------------------------------------------------
    # 合并
    # ------------------------------------------------------------------
    @classmethod
    def merge(cls, *rule_sets: RuleSet) -> RuleSet:
        """合并多个规则集.

        合并规则:
            - 后续规则集覆盖前面的
            - 列表类型（如 forbidden_claims）按 pattern 去重合并
            - meta 信息使用最后一个非空的
        """
        if not rule_sets:
            return RuleSet()

        if len(rule_sets) == 1:
            return rule_sets[0]

        merged_meta = None
        merged_rules: Dict = {}

        for rs in rule_sets:
            # 合并 meta（使用最后一个非空 meta）
            if rs.meta and (rs.meta.name or rs.meta.industry != "general"):
                merged_meta = rs.meta.model_copy()

            # 合并各项规则
            for key, value in rs.rules.items():
                if not value:
                    continue
                if key not in merged_rules:
                    merged_rules[key] = value
                    continue

                # 列表类型合并
                if isinstance(value, list) and isinstance(merged_rules[key], list):
                    if key in ("forbidden_claims", "must_not_mention", "exaggeration_patterns", "required_keywords"):
                        merged_rules[key] = cls._merge_list_unique(merged_rules[key], value, key)
                    else:
                        merged_rules[key] = value
                # 字典类型（competitor_disparagement, fact_verification）
                elif isinstance(value, dict) and isinstance(merged_rules[key], dict):
                    merged_rules[key] = {**merged_rules[key], **value}
                else:
                    merged_rules[key] = value

        return RuleSet(meta=merged_meta or RuleSetMeta_default(), rules=merged_rules)

    @staticmethod
    def _merge_list_unique(existing: List, incoming: List, key: str) -> List:
        """合并列表并去重（按 pattern 或字符串本身）."""
        if key == "required_keywords":
            # 字符串列表
            seen = set()
            result = []
            for item in existing + incoming:
                if item not in seen:
                    seen.add(item)
                    result.append(item)
            return result

        # 对象列表，按 pattern 去重
        seen_patterns = set()
        result = []
        for item in existing + incoming:
            if isinstance(item, dict):
                p = item.get("pattern")
                if p and p not in seen_patterns:
                    seen_patterns.add(p)
                    result.append(item)
            else:
                if item not in seen_patterns:
                    seen_patterns.add(item)
                    result.append(item)
        return result


def RuleSetMeta_default():
    """获取默认 meta."""
    from geo_review.rules.models import RuleSetMeta
    return RuleSetMeta()
