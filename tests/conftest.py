"""pytest 共享 fixtures."""

import sys
import os
from pathlib import Path

import pytest

# 确保 src 在 Python 路径中
PROJECT_ROOT = Path(__file__).parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


@pytest.fixture
def sample_rule_set():
    """创建基础测试规则集."""
    from geo_review.rules.models import RuleSet, RuleSetMeta

    return RuleSet(
        meta=RuleSetMeta(name="test", industry="general"),
        rules={
            "forbidden_claims": [
                {"pattern": "第一", "severity": "critical", "description": "绝对化用语"},
                {"pattern": "最佳", "severity": "major", "description": "绝对化用语"},
                {"pattern": "唯一", "severity": "critical", "description": "绝对化用语"},
                {"pattern": "100%", "severity": "major", "description": "绝对化用语"},
            ],
            "min_length": 100,
            "max_length": 50000,
        },
    )


@pytest.fixture
def sample_submission():
    """创建测试提报表."""
    from geo_review.models import Submission

    return Submission(
        task_name="测试任务",
        company_name="测试科技有限公司",
        product_or_service=["测试产品A"],
        core_topic="产品发布",
        key_points=["核心技术", "行业优势"],
        forbidden_claims=["行业第一", "最好"],
        must_not_mention=["竞争对手X"],
        competitor_names=["竞品公司B"],
    )


@pytest.fixture
def clean_content():
    """合规正文（足够长，无违规）."""
    return (
        "测试科技有限公司今日发布了测试产品A，该产品采用核心技术，在行业中具有竞争优势。"
        "据2024年内部数据报告显示，产品在多个关键指标上表现良好。"
        "测试产品A通过了ISO9001质量管理体系认证，获得多项技术专利。"
        "公司成立于2015年，总部位于北京，目前服务超过500家企业客户。"
        "在产品研发方面，公司持续投入大量资源，致力于为用户提供更优质的服务体验。"
        "测试产品A主要面向企业级客户，提供数据分析、智能决策等核心功能模块。"
        "与传统的解决方案相比，测试产品A在处理效率上有显著提升，同时降低了运维成本。"
        "公司计划在未来三年内，将服务范围扩展至全国主要城市，覆盖更多行业场景。"
    )


@pytest.fixture
def violating_content():
    """违规正文（包含多种违规）."""
    return (
        "我们公司是行业第一，产品最佳，是唯一的选择。"
        "通过技术创新，使得产品性能全面提升，全链条覆盖客户需求。"
        "我们的产品不仅拥有遥遥领先的技术，还实现了全覆盖的市场布局。"
        "据调查，100%的用户都认为我们是最好的选择。"
        "联系我们：手机13812345678，微信test_wechat123。"
        "据众所周知，我们在行业中名列前茅，位居前列。"
        "公司提供全方位、一站式的解决方案，全面超越竞品公司B。"
    )
