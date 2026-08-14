"""规则引擎测试 — 覆盖各检查项的核心逻辑."""

import pytest

from geo_review.rules.engine import RuleEngine
from geo_review.rules.issues import IssueType, IssueSeverity


class TestLengthCheck:
    """长度检查测试."""

    def test_too_short(self, sample_rule_set):
        """正文过短应触发 major 级问题."""
        engine = RuleEngine(sample_rule_set)
        issues = engine.check("短文本")
        short_issues = [i for i in issues if "过短" in i.title]
        assert len(short_issues) == 1
        assert short_issues[0].severity == IssueSeverity.MAJOR

    def test_too_long(self, sample_rule_set):
        """正文过长应触发 minor 级问题."""
        engine = RuleEngine(sample_rule_set)
        long_text = "测试" * 30000
        issues = engine.check(long_text)
        long_issues = [i for i in issues if "过长" in i.title]
        assert len(long_issues) == 1
        assert long_issues[0].severity == IssueSeverity.MINOR

    def test_normal_length(self, sample_rule_set, clean_content):
        """正常长度不应触发长度问题."""
        engine = RuleEngine(sample_rule_set)
        issues = engine.check(clean_content)
        length_issues = [i for i in issues if "过短" in i.title or "过长" in i.title]
        assert len(length_issues) == 0


class TestForbiddenWords:
    """禁用词检查测试."""

    def test_detect_forbidden_word(self, sample_rule_set):
        """检测禁用词「第一」."""
        content = "我们是行业第一的公司，拥有最好的产品。" + "测试" * 50
        engine = RuleEngine(sample_rule_set)
        issues = engine.check(content)
        forbidden_issues = [i for i in issues if "第一" in i.evidence.snippet or "第一" in i.title]
        assert len(forbidden_issues) >= 1
        assert any(i.severity == IssueSeverity.CRITICAL for i in forbidden_issues)

    def test_detect_multiple_forbidden(self, sample_rule_set):
        """检测多个禁用词."""
        content = "行业第一，产品最佳，是唯一的选择。" + "测试" * 50
        engine = RuleEngine(sample_rule_set)
        issues = engine.check(content)
        # 至少检测到 3 个禁用词
        forbidden_issues = [i for i in issues if i.type == IssueType.EXAGGERATION]
        assert len(forbidden_issues) >= 3

    def test_no_forbidden_in_clean_content(self, sample_rule_set, clean_content):
        """合规正文不应触发禁用词."""
        engine = RuleEngine(sample_rule_set)
        issues = engine.check(clean_content)
        forbidden = [i for i in issues if "禁用" in i.title or "绝对化" in i.title]
        assert len(forbidden) == 0


class TestEuphemismAbsolute:
    """擦边球绝对化用语检测测试."""

    def test_detect_euphemism(self, sample_rule_set):
        """检测擦边球用词「名列前茅」."""
        content = "我们在行业中名列前茅，遥遥领先。" + "测试" * 50
        engine = RuleEngine(sample_rule_set)
        issues = engine.check(content)
        euphemism_issues = [i for i in issues if "擦边球" in i.title]
        assert len(euphemism_issues) >= 1
        assert all(i.severity == IssueSeverity.MAJOR for i in euphemism_issues)

    def test_detect_extended_absolute(self, sample_rule_set):
        """检测扩展绝对化用语「全链条」「全覆盖」."""
        content = "我们提供全链条服务，实现全覆盖。" + "测试" * 50
        engine = RuleEngine(sample_rule_set)
        issues = engine.check(content)
        extended_issues = [i for i in issues if "扩展绝对化" in i.title]
        assert len(extended_issues) >= 1


class TestContactLeak:
    """联系方式泄露检测测试."""

    def test_detect_phone(self, sample_rule_set):
        """检测手机号泄露."""
        content = "联系我们：13812345678，了解更多信息。" + "测试" * 50
        engine = RuleEngine(sample_rule_set)
        issues = engine.check(content)
        phone_issues = [i for i in issues if "手机号" in i.title]
        assert len(phone_issues) >= 1
        assert phone_issues[0].severity == IssueSeverity.MAJOR

    def test_detect_email(self, sample_rule_set):
        """检测邮箱泄露."""
        content = "联系邮箱：test@example.com，欢迎咨询。" + "测试" * 50
        engine = RuleEngine(sample_rule_set)
        issues = engine.check(content)
        email_issues = [i for i in issues if "邮箱" in i.title]
        assert len(email_issues) >= 1

    def test_no_contact_in_clean_content(self, sample_rule_set, clean_content):
        """合规正文不应有联系方式泄露."""
        engine = RuleEngine(sample_rule_set)
        issues = engine.check(clean_content)
        contact_issues = [i for i in issues if "泄露" in i.title]
        assert len(contact_issues) == 0


class TestSentenceQuality:
    """语句质量检测测试."""

    def test_detect_missing_subject(self, sample_rule_set):
        """检测「通过...，使得...」缺主语句式."""
        content = "通过技术创新，使得产品性能得到了全面提升。" + "测试" * 50
        engine = RuleEngine(sample_rule_set)
        issues = engine.check(content)
        subject_issues = [i for i in issues if "缺主语" in i.title]
        assert len(subject_issues) >= 1

    def test_detect_incomplete_correlation(self, sample_rule_set):
        """检测「不仅」缺呼应."""
        content = "我们的产品不仅拥有先进的技术。" + "测试" * 50
        engine = RuleEngine(sample_rule_set)
        issues = engine.check(content)
        correlation_issues = [i for i in issues if "不仅" in i.title and "呼应" in i.title]
        assert len(correlation_issues) >= 1


class TestCoreInfoValidation:
    """核心信息交叉验证测试."""

    def test_company_name_mismatch(self, sample_rule_set, sample_submission):
        """公司名称与提报表不一致应触发 critical 级."""
        content = "某其他公司的产品非常好，在行业中有优势。" + "测试" * 50
        engine = RuleEngine(sample_rule_set, submission=sample_submission)
        issues = engine.check(content)
        company_issues = [i for i in issues if "公司名称" in i.title and "不一致" in i.title]
        assert len(company_issues) >= 1
        assert company_issues[0].severity == IssueSeverity.CRITICAL

    def test_company_name_match(self, sample_rule_set, sample_submission):
        """公司名称一致时不应触发."""
        content = "测试科技有限公司发布了新产品。" + "测试" * 50
        engine = RuleEngine(sample_rule_set, submission=sample_submission)
        issues = engine.check(content)
        company_issues = [i for i in issues if "公司名称" in i.title and "不一致" in i.title]
        assert len(company_issues) == 0

    def test_product_missing(self, sample_rule_set, sample_submission):
        """产品名称未在正文中体现."""
        content = "测试科技有限公司发布了新产品。" + "测试" * 50
        engine = RuleEngine(sample_rule_set, submission=sample_submission)
        issues = engine.check(content)
        product_issues = [i for i in issues if "产品" in i.title and "未在正文" in i.title]
        assert len(product_issues) >= 1


class TestGeoCitability:
    """GEO 可引用性审核测试."""

    def test_vague_references(self, sample_rule_set):
        """检测模糊引用词."""
        # _check_geo_citability 要求 len(content) > 200 才检测模糊引用
        content = (
            "据说我们的产品很好，众所周知，业界公认我们是行业领先。"
            + "测试" * 100
        )
        engine = RuleEngine(sample_rule_set)
        issues = engine.check(content)
        vague_issues = [i for i in issues if "模糊引用" in i.title]
        assert len(vague_issues) >= 1
        assert vague_issues[0].type == IssueType.GEO_CITABILITY

    def test_missing_authority_sources(self, sample_rule_set):
        """权威来源检测已移至 LLM 层，规则引擎不再对此产生误报."""
        content = "我们公司提供优质的产品和服务，致力于为客户创造价值。" * 20
        engine = RuleEngine(sample_rule_set)
        issues = engine.check(content)
        source_issues = [i for i in issues if "权威来源" in i.title]
        # 规则精简：移除了正则匹配权威来源（高误报率），改由 LLM 语义判断
        assert len(source_issues) == 0


class TestIssueSorting:
    """问题排序测试."""

    def test_sorted_by_severity(self, sample_rule_set):
        """问题应按严重程度排序（critical → major → minor → info）."""
        content = (
            "我们是行业第一，产品最佳。"  # 触发 critical + major
            "通过技术，使得性能提升。"  # 触发 major（语句）
            "据众所周知，我们领先。"  # 触发 major（模糊引用）
            + "测试" * 50
        )
        engine = RuleEngine(sample_rule_set)
        issues = engine.check(content)

        severity_order = {"critical": 0, "major": 1, "minor": 2, "info": 3}
        for i in range(len(issues) - 1):
            current = severity_order.get(issues[i].severity.value, 9)
            next_val = severity_order.get(issues[i + 1].severity.value, 9)
            assert current <= next_val, (
                f"排序错误: {issues[i].severity} 应 <= {issues[i + 1].severity}"
            )
