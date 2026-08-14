"""TaskPlanner — 任务规划器，根据内容类型选择合适的审核策略.

职责：
    1. 分析正文 + 提报表 → 判断任务类型
    2. 根据任务类型选择审核策略（规则模板、行业知识库、Prompt模板）
    3. 支持 LLM 分类（优先）和关键词匹配（fallback）

任务类型：
    - finance: 金融广告、理财、保险、银行
    - medical: 医疗健康、药品、保健品
    - enterprise_intro: 企业介绍、品牌宣传
    - news: 新闻稿、公关稿
    - technology: 科技产品、技术方案
    - general: 通用类型（默认）
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# 审核计划
# ------------------------------------------------------------------

@dataclass
class ReviewPlan:
    """审核计划 — TaskPlanner 的输出."""
    task_type: str                                    # 任务类型标识
    task_type_label: str                              # 任务类型中文标签
    confidence: float                                 # 分类置信度 0-1
    rule_template: str = "general"                    # 规则模板名称
    industry_kb_key: Optional[str] = None             # 行业知识库 key
    prompt_profile: str = "general"                   # Prompt 模板 profile
    crawl_official_urls: bool = True                  # 是否爬取官网
    use_llm: bool = True                              # 是否启用 LLM 审核
    classification_method: str = "keyword"            # 分类方法: llm / keyword
    classification_reason: str = ""                   # 分类理由
    extra_notes: List[str] = field(default_factory=list)  # 额外备注


# ------------------------------------------------------------------
# 任务类型定义
# ------------------------------------------------------------------

TASK_TYPE_CONFIG: Dict[str, dict] = {
    "finance": {
        "label": "金融/理财",
        "rule_template": "finance",
        "industry_kb_key": "finance",
        "prompt_profile": "finance",
        "crawl_official_urls": True,
        "keywords": [
            # 金融产品
            "理财", "基金", "股票", "债券", "保险", "信托", "期货", "外汇",
            "贷款", "存款", "利率", "收益", "分红", "投资", "资产", "配置",
            # 金融机构
            "银行", "证券", "保险", "金融", "支付", "征信", "担保",
            # 金融术语
            "年化", "保本", "风险", "净值", "申购", "赎回", "定投", "复利",
            "风控", "杠杆", "对冲", "量化",
        ],
    },
    "medical": {
        "label": "医疗/健康",
        "rule_template": "general",  # 暂无独立医疗模板，回退 general
        "industry_kb_key": "medical",
        "prompt_profile": "medical",
        "crawl_official_urls": True,
        "keywords": [
            # 医疗
            "治疗", "疗效", "治愈", "患者", "病症", "疾病", "临床", "诊断",
            "手术", "康复", "处方", "药品", "药物", "疫苗", "医疗", "医院",
            "门诊", "体检", "病理", "药理",
            # 健康
            "健康", "保健", "养生", "营养", "膳食", "维生素", "矿物质",
            "免疫力", "抗氧化", "排毒", "调理",
        ],
    },
    "enterprise_intro": {
        "label": "企业介绍",
        "rule_template": "general",
        "industry_kb_key": None,
        "prompt_profile": "enterprise",
        "crawl_official_urls": True,
        "keywords": [
            "成立于", "注册资本", "总部位于", "旗下", "子公司",
            "企业简介", "公司简介", "关于我们", "企业文化", "企业愿景",
            "发展历程", "组织架构", "团队", "使命", "价值观",
        ],
    },
    "news": {
        "label": "新闻稿",
        "rule_template": "general",
        "industry_kb_key": None,
        "prompt_profile": "news",
        "crawl_official_urls": False,
        "keywords": [
            "发布", "宣布", "签约", "揭牌", "开幕", "启动", "达成",
            "合作", "战略", "峰会", "论坛", "发布会", "荣获", "获得",
            "近日", "日前", "日前从", "记者", "报道",
        ],
    },
    "technology": {
        "label": "科技/技术",
        "rule_template": "general",
        "industry_kb_key": "technology",
        "prompt_profile": "technology",
        "crawl_official_urls": True,
        "keywords": [
            "AI", "人工智能", "大数据", "云计算", "区块链", "物联网",
            "算法", "模型", "平台", "SaaS", "PaaS", "API", "SDK",
            "解决方案", "技术架构", "系统", "架构", "部署", "集成",
            "自动化", "智能化", "数字化", "信息化",
        ],
    },
}

# 通用类型（兜底）
GENERAL_CONFIG = {
    "label": "通用",
    "rule_template": "general",
    "industry_kb_key": None,
    "prompt_profile": "general",
    "crawl_official_urls": False,
}


# ------------------------------------------------------------------
# LLM 分类 Prompt
# ------------------------------------------------------------------

CLASSIFICATION_PROMPT = """分析以下文本的内容类型，输出 JSON。

## 候选类型
- finance: 金融/理财/保险/银行/投资
- medical: 医疗/健康/药品/保健品
- enterprise_intro: 企业介绍/品牌宣传
- news: 新闻稿/公关稿/媒体报道
- technology: 科技产品/技术方案/软件
- general: 以上都不匹配

## 输出格式
{{
  "task_type": "finance",
  "confidence": 0.85,
  "reason": "分类理由"
}}

## 待分类文本
{content}

## 提报表参考
{context}

只输出 JSON。"""


# ------------------------------------------------------------------
# TaskPlanner
# ------------------------------------------------------------------

class TaskPlanner:
    """任务规划器 — 根据内容类型选择合适的审核策略.

    用法::

        planner = TaskPlanner(llm_client=llm_client)
        plan = planner.plan(content="待审正文...", submission=submission)
        print(plan.task_type)  # "finance"
        print(plan.rule_template)  # "finance"
    """

    def __init__(self, llm_client=None):
        """初始化规划器.

        Args:
            llm_client: LLM 客户端（可选），用于 LLM 分类
        """
        self._llm_client = llm_client

    def plan(
        self,
        content: str,
        submission: Optional[Any] = None,
        industry: Optional[str] = None,
    ) -> ReviewPlan:
        """分析内容并生成审核计划.

        Args:
            content: 待审正文
            submission: 提报表对象（可选）
            industry: 用户指定的行业（可选，优先级最高）

        Returns:
            ReviewPlan: 审核计划
        """
        # 优先级1: 用户显式指定的行业
        if industry and industry in TASK_TYPE_CONFIG:
            config = TASK_TYPE_CONFIG[industry]
            return ReviewPlan(
                task_type=industry,
                task_type_label=config["label"],
                confidence=1.0,
                rule_template=config["rule_template"],
                industry_kb_key=config["industry_kb_key"],
                prompt_profile=config["prompt_profile"],
                crawl_official_urls=config["crawl_official_urls"],
                classification_method="user_specified",
                classification_reason=f"用户指定行业: {industry}",
            )

        # 优先级2: LLM 分类（如果客户端可用）
        if self._llm_client:
            try:
                llm_plan = self._classify_by_llm(content, submission)
                if llm_plan and llm_plan.confidence >= 0.6:
                    return llm_plan
            except Exception as e:
                logger.warning(f"LLM 分类失败，回退到关键词匹配: {e}")

        # 优先级3: 关键词匹配（fallback）
        return self._classify_by_keywords(content)

    def _classify_by_llm(
        self,
        content: str,
        submission: Optional[Any] = None,
    ) -> Optional[ReviewPlan]:
        """使用 LLM 进行内容分类."""
        if not self._llm_client:
            return None

        # 构建上下文
        context_parts = []
        if submission:
            if hasattr(submission, 'company_name') and submission.company_name:
                context_parts.append(f"公司名称: {submission.company_name}")
            if hasattr(submission, 'core_topic') and submission.core_topic:
                context_parts.append(f"核心主题: {submission.core_topic}")
            if hasattr(submission, 'product_or_service') and submission.product_or_service:
                products = [p for p in submission.product_or_service if p != "未指定"]
                if products:
                    context_parts.append(f"产品/服务: {', '.join(products)}")
        context = "\n".join(context_parts) if context_parts else "（无提报信息）"

        # 截断正文（分类不需要全文）
        content_snippet = content[:2000]

        prompt = CLASSIFICATION_PROMPT.format(
            content=content_snippet,
            context=context,
        )

        try:
            messages = [{"role": "user", "content": prompt}]
            raw = self._llm_client.chat(messages=messages, max_retries=1)
            data = json.loads(raw.get("content", "{}"))
        except Exception:
            return None

        task_type = data.get("task_type", "general")
        confidence = float(data.get("confidence", 0.5))
        reason = data.get("reason", "")

        if task_type not in TASK_TYPE_CONFIG:
            task_type = "general"

        config = TASK_TYPE_CONFIG.get(task_type, GENERAL_CONFIG)
        return ReviewPlan(
            task_type=task_type,
            task_type_label=config["label"],
            confidence=confidence,
            rule_template=config["rule_template"],
            industry_kb_key=config["industry_kb_key"],
            prompt_profile=config["prompt_profile"],
            crawl_official_urls=config["crawl_official_urls"],
            classification_method="llm",
            classification_reason=reason,
        )

    @staticmethod
    def _classify_by_keywords(content: str) -> ReviewPlan:
        """基于关键词匹配进行内容分类.

        统计每种类型的命中关键词数，返回得分最高的类型。
        """
        scores: Dict[str, Tuple[int, int]] = {}  # type -> (score, keyword_count)

        for task_type, config in TASK_TYPE_CONFIG.items():
            keywords = config.get("keywords", [])
            hit_count = 0
            total_weight = 0
            for kw in keywords:
                if kw in content:
                    hit_count += 1
                    # 长关键词权重更高
                    total_weight += len(kw)
            if hit_count > 0:
                scores[task_type] = (hit_count, total_weight)

        if not scores:
            return ReviewPlan(
                task_type="general",
                task_type_label="通用",
                confidence=0.3,
                rule_template="general",
                industry_kb_key=None,
                prompt_profile="general",
                crawl_official_urls=False,
                classification_method="keyword",
                classification_reason="未匹配到任何行业关键词，使用通用审核策略",
            )

        # 按命中数 + 权重排序
        best = max(scores.items(), key=lambda x: (x[1][0], x[1][1]))
        task_type = best[0]
        config = TASK_TYPE_CONFIG[task_type]

        # 计算置信度（基于命中数和内容长度）
        hit_count = best[1][0]
        total_keywords = len(config.get("keywords", []))
        confidence = min(0.9, 0.3 + (hit_count / max(total_keywords, 1)) * 0.6)

        return ReviewPlan(
            task_type=task_type,
            task_type_label=config["label"],
            confidence=round(confidence, 2),
            rule_template=config["rule_template"],
            industry_kb_key=config["industry_kb_key"],
            prompt_profile=config["prompt_profile"],
            crawl_official_urls=config["crawl_official_urls"],
            classification_method="keyword",
            classification_reason=f"关键词匹配: 命中 {hit_count}/{total_keywords} 个关键词",
        )

    def get_plan_summary(self, plan: ReviewPlan) -> Dict[str, Any]:
        """获取计划的摘要信息（用于日志/API返回）."""
        return {
            "task_type": plan.task_type,
            "task_type_label": plan.task_type_label,
            "confidence": plan.confidence,
            "classification_method": plan.classification_method,
            "classification_reason": plan.classification_reason,
            "strategy": {
                "rule_template": plan.rule_template,
                "industry_kb": plan.industry_kb_key,
                "prompt_profile": plan.prompt_profile,
                "crawl_official_urls": plan.crawl_official_urls,
                "use_llm": plan.use_llm,
            },
        }