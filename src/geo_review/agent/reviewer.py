"""ReviewAgent — GEO 生文审核 Agent 统一入口（优化版 v2）.

优化点：
    1. ✅ 修复 use_llm 开关未生效问题：尊重 ReviewOptions.use_llm 设置
    2. ✅ 修复批量审核未传递 industry_kb 问题
    3. ✅ LLM 配置参数（confidence_threshold、max_issues）从配置传入
    4. ✅ 错误处理细化：按错误类型分类返回不同错误码
    5. ✅ 集成 TaskPlanner：根据内容类型自动选择审核策略
    6. ✅ 动态 Prompt 注入：根据任务类型组合不同的 prompt 模块
"""

import base64
import io
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

from geo_review.agent.models import (
    ContentFileInput,
    ReviewOptions,
    ReviewRequest,
    SubmissionFileInput,
)
from geo_review.agent.planner import ReviewPlan, TaskPlanner
from geo_review.crawlers.website import CrawledDomain, WebsiteCrawler
from geo_review.llm.client import LLMClient
from geo_review.llm.models import LLMProviderConfig
from geo_review.llm.reviewer import LLMReviewer
from geo_review.models import Submission
from geo_review.tools.fact_checker import FactChecker, FactCheckResult
from geo_review.tools.web_search import WebSearchConfig, WebSearchTool
from geo_review.parsers.content import ContentParser
from geo_review.parsers.submission import SubmissionParser
from geo_review.result.builder import ReviewResultBuilder, ReviewResultFormatter
from geo_review.result.models import (
    ReviewError,
    ReviewResponse,
    ReviewStatus,
)
from geo_review.rules.engine import RuleEngine
from geo_review.rules.issues import Issue
from geo_review.rules.loader import RuleLoader

try:
    from geo_review.history.service import HistoryService
except ImportError:
    HistoryService = None

logger = logging.getLogger(__name__)


class ReviewAgent:
    """GEO 生文审核 Agent.

    用法::

        agent = ReviewAgent()
        request = {
            "content": {"input_type": "text", "text": "待审正文..."},
            "submission": {"input_type": "text", "text": "提报说明..."},
        }
        response = agent.review(request)
        print(response.summary)
    """

    def __init__(
        self,
        llm_config: Optional[LLMProviderConfig] = None,
        rule_loader: Optional[RuleLoader] = None,
        history_service: Optional["HistoryService"] = None,
        default_rule_set: Optional[Any] = None,
        industry_kb: Optional[Any] = None,
        fact_check_enabled: bool = True,
        fact_check_max_claims: int = 5,
        fact_check_max_search_results: int = 5,
        fact_check_search_timeout: int = 15,
    ):
        self.llm_config = llm_config or LLMProviderConfig()
        self._llm_client: Optional[LLMClient] = None
        self._llm_reviewer: Optional[LLMReviewer] = None
        self.rule_loader = rule_loader or RuleLoader()
        self.history_service = history_service
        self._default_rule_set = default_rule_set
        self._industry_kb = industry_kb
        self._task_planner: Optional[TaskPlanner] = None
        self._fact_checker: Optional[FactChecker] = None
        self._web_search_tool: Optional[WebSearchTool] = None
        # 联网核查配置
        self._fact_check_enabled: bool = fact_check_enabled
        self._fact_check_max_claims: int = fact_check_max_claims
        self._fact_check_max_search_results: int = fact_check_max_search_results
        self._fact_check_search_timeout: int = fact_check_search_timeout

    @property
    def llm_client(self) -> LLMClient:
        """懒加载 LLM 客户端."""
        if self._llm_client is None:
            self._llm_client = LLMClient(self.llm_config)
        return self._llm_client

    @property
    def llm_reviewer(self) -> LLMReviewer:
        """懒加载 LLM 审核器.

        ✅ 优化：从配置中读取 confidence_threshold 和 max_issues
        """
        if self._llm_reviewer is None:
            self._llm_reviewer = LLMReviewer(
                llm_client=self.llm_client,
                confidence_threshold=self.llm_config.confidence_threshold,
                max_issues=self.llm_config.max_issues,
            )
        return self._llm_reviewer

    @property
    def task_planner(self) -> TaskPlanner:
        """懒加载任务规划器."""
        if self._task_planner is None:
            self._task_planner = TaskPlanner(llm_client=self.llm_client)
        return self._task_planner

    @property
    def web_search_tool(self) -> WebSearchTool:
        """懒加载联网搜索工具."""
        if self._web_search_tool is None:
            self._web_search_tool = WebSearchTool(WebSearchConfig(
                timeout=self._fact_check_search_timeout,
                max_results=self._fact_check_max_search_results,
            ))
        return self._web_search_tool

    @property
    def fact_checker(self) -> FactChecker:
        """懒加载事实核查器."""
        if self._fact_checker is None:
            self._fact_checker = FactChecker(
                llm_client=self.llm_client,
                search_tool=self.web_search_tool,
                max_claims=self._fact_check_max_claims,
                max_search_results=self._fact_check_max_search_results,
                search_timeout=self._fact_check_search_timeout,
            )
        return self._fact_checker

    def review(
        self,
        request_data: Dict[str, Any],
        rules: Optional[Dict[str, Any]] = None,
        industry_kb: Optional[Any] = None,
        industry: Optional[str] = None,
    ) -> ReviewResponse:
        """执行完整审核流程.

        Args:
            request_data: 审核请求数据（符合 review-request.schema.json）
            rules: 自定义规则（可选），格式与规则文件一致
            industry_kb: 行业知识库（可选），用于行业专业化审核
            industry: 用户指定的行业标识（可选），用于 TaskPlanner 分类

        Returns:
            ReviewResponse: 审核响应（符合 review-response.schema.json）
        """
        # 使用传入的行业知识库或默认的
        active_industry_kb = industry_kb or self._industry_kb

        # 1. 验证请求
        try:
            request = ReviewRequest(**request_data)
        except Exception as e:
            return self._build_error_response(
                "INVALID_REQUEST",
                f"请求格式验证失败: {str(e)}",
                request_data.get("request_id"),
            )

        builder = ReviewResultBuilder(
            request_id=request.request_id,
        )

        # 2. 解析提报表
        submission: Optional[Submission] = None
        try:
            submission = self._parse_submission(request, builder)
        except Exception as e:
            return self._build_error_response(
                "SUBMISSION_PARSE_ERROR",
                f"提报表解析失败: {str(e)}",
                request.request_id,
            )

        # 3. 解析正文
        content_text: str = ""
        try:
            content_text = self._parse_content(request, builder)
        except Exception as e:
            return self._build_error_response(
                "CONTENT_PARSE_ERROR",
                f"正文解析失败: {str(e)}",
                request.request_id,
            )

        builder.content_length = len(content_text)

        # 4. 任务规划（新增）：根据内容类型选择审核策略
        plan = self.task_planner.plan(
            content=content_text,
            submission=submission,
            industry=industry,
        )
        logger.info(
            f"TaskPlanner: 任务类型={plan.task_type_label} "
            f"(置信度={plan.confidence}, 方法={plan.classification_method}), "
            f"规则模板={plan.rule_template}, prompt={plan.prompt_profile}"
        )
        builder.set_plan(plan)

        # 4.5. 爬取官网（可选，根据 plan 决定是否默认启用）
        crawled: Optional[CrawledDomain] = None
        failed_urls: List[tuple] = []
        official_urls = request.get_all_official_urls()
        options = request.options or ReviewOptions()

        # 如果 plan 建议爬取且用户没有明确关闭，则爬取
        should_crawl = options.crawl_official_urls
        if plan.crawl_official_urls and not options.crawl_official_urls:
            # plan 建议爬取但用户未开启，以用户设置为准
            pass

        if should_crawl and official_urls:
            try:
                crawled, failed_urls = self._crawl_websites(
                    official_urls,
                    max_pages=options.crawl_max_pages,
                    timeout=options.crawl_timeout_seconds,
                )
            except Exception as e:
                builder.add_warning(
                    "URL_CRAWL_ALL_FAIL",
                    f"官网爬取异常: {str(e)}",
                )

        builder.set_website_info(crawled, official_urls, failed_urls)

        # 5. 加载规则（根据 plan 选择规则模板）
        try:
            rule_set = self._load_rules(rules, plan.rule_template)
        except Exception as e:
            return self._build_error_response(
                "REVIEW_INTERNAL_ERROR",
                f"规则加载失败: {str(e)}",
                request.request_id,
            )

        # 6 & 7. 规则引擎审核、LLM 语义审核、联网事实核查并行执行
        rule_issues: List[Issue] = []
        llm_issues: List[Issue] = []
        llm_result = None
        fact_check_issues: List[Issue] = []
        fact_check_results: List[FactCheckResult] = []

        use_llm = getattr(options, "use_llm", True) and plan.use_llm
        use_fact_check = self._fact_check_enabled and getattr(options, "use_fact_check", True) and use_llm

        def _run_rules():
            try:
                return self._run_rule_engine(rule_set, content_text, submission, crawled, active_industry_kb), None
            except Exception as e:
                return [], e

        def _run_llm():
            if not use_llm:
                return ([], None), None
            try:
                return self._run_llm_reviewer(
                    content_text, submission, crawled, 10000, active_industry_kb, plan.prompt_profile
                ), None
            except Exception as e:
                return ([], None), e

        def _run_fact_check():
            if not use_fact_check:
                return ([], []), None
            try:
                company = submission.company_name if submission else ""
                issues, results = self.fact_checker.check(
                    content=content_text,
                    company_name=company,
                    starting_id=20001,
                )
                return (issues, results), None
            except Exception as e:
                return ([], []), e

        with ThreadPoolExecutor(max_workers=3) as executor:
            future_rules = executor.submit(_run_rules)
            future_llm = executor.submit(_run_llm)
            future_fact = executor.submit(_run_fact_check)

            rule_issues, rule_error = future_rules.result()
            llm_tuple, llm_error = future_llm.result()
            fact_tuple, fact_error = future_fact.result()

        if rule_error:
            builder.add_warning(
                "REVIEW_INTERNAL_ERROR",
                f"规则引擎执行异常: {rule_error}",
            )

        if llm_error:
            builder.add_warning(
                "REVIEW_INTERNAL_ERROR",
                f"LLM 审核异常: {llm_error}",
            )
        else:
            llm_issues, llm_result = llm_tuple

        if fact_error:
            builder.add_warning(
                "FACT_CHECK_ERROR",
                f"联网事实核查异常: {fact_error}",
            )
        else:
            fact_check_issues, fact_check_results = fact_tuple

        builder.add_rule_issues(rule_issues)
        builder.add_llm_issues(llm_issues, llm_result)
        # 联网核查问题作为 LLM 问题的一部分加入（类型为 unsupported_claim）
        if fact_check_issues:
            builder.add_llm_issues(fact_check_issues, None)

        # 8. 构建最终响应
        status = ReviewStatus.COMPLETED
        if builder._warnings:
            has_all_crawl_fail = any(w.code == "URL_CRAWL_ALL_FAIL" for w in builder._warnings)
            if has_all_crawl_fail and official_urls:
                status = ReviewStatus.PARTIAL
            else:
                status = ReviewStatus.COMPLETED

        response = builder.build(status=status)

        return response

    # ------ 内部方法 ------

    def _parse_submission(
        self,
        request: ReviewRequest,
        builder: ReviewResultBuilder,
    ) -> Submission:
        """解析提报表."""
        if not request.submission:
            builder.set_submission_source("json")
            return Submission()

        text = request.get_submission_text()
        if text is not None:
            builder.set_submission_source("text")
            return self._parse_submission_text(text)

        data = request.get_submission_json()
        if data is not None:
            builder.set_submission_source("json")
            return Submission.model_validate(data)

        file_input = request.get_submission_file()
        if file_input is not None:
            builder.set_submission_source("file")
            submission = self._parse_submission_from_file(file_input)
            override_data = request.submission.get("override_data")
            if override_data:
                sub_dict = submission.model_dump()
                sub_dict.update(override_data)
                submission = Submission.model_validate(sub_dict)
            return submission

        builder.set_submission_source("json")
        return Submission()

    def _parse_submission_text(self, text: str) -> Submission:
        """解析文本格式的提报表（键值对格式）."""
        import re

        text = text.strip()

        if text.startswith("{"):
            return SubmissionParser.parse(text)

        data: Dict[str, Any] = {}
        lines = text.split("\n")
        for line in lines:
            line = line.strip()
            if not line:
                continue
            match = re.match(r"^(.+?)[:：]\s*(.+)$", line)
            if match:
                key = match.group(1).strip()
                value = match.group(2).strip()
                data[key] = value

        if not data:
            raise ValueError("文本提报表格式无效，请使用键值对格式如：任务名称：xxx")

        mapping = {
            "任务名称": "task_name",
            "公司名称": "company_name",
            "产品服务": "product_or_service",
            "核心主题": "core_topic",
            "要点": "key_points",
            "关键词": "keywords",
            "参考文案": "reference_copy",
            "允许事实": "allowed_facts",
            "禁用表述": "forbidden_claims",
            "禁止提及": "must_not_mention",
            "竞品名": "competitor_names",
            "官网地址": "official_urls",
            "备注": "notes",
        }

        result: Dict[str, Any] = {
            "product_or_service": ["未指定"],
            "core_topic": "未指定",
            "key_points": ["未指定"],
            "keywords": [],
            "reference_copy": [],
            "allowed_facts": [],
            "forbidden_claims": ["行业第一", "唯一", "100%", "最好", "最佳"],
            "must_not_mention": [],
            "competitor_names": [],
            "official_urls": ["https://example.com"],
            "notes": "无",
        }

        for cn_key, en_key in mapping.items():
            if cn_key in data:
                val = data[cn_key]
                if en_key in ["product_or_service", "key_points", "keywords",
                              "reference_copy", "allowed_facts", "forbidden_claims",
                              "must_not_mention", "competitor_names", "official_urls"]:
                    if isinstance(val, str):
                        val = [v.strip() for v in val.split("；") if v.strip()]
                    elif isinstance(val, list):
                        pass
                    else:
                        val = [str(val)]
                result[en_key] = val

        if "task_name" not in result:
            raise ValueError("缺少必填字段: 任务名称")
        if "company_name" not in result:
            raise ValueError("缺少必填字段: 公司名称")

        return Submission(**result)

    def _parse_submission_from_file(self, file_input: SubmissionFileInput) -> Submission:
        """从文件解析提报表."""
        file_ref = file_input.file

        if file_ref.content_base64:
            content = base64.b64decode(file_ref.content_base64)
            return SubmissionParser.parse(
                content,
                format_hint=file_ref.format,
            )

        if file_ref.file_id:
            raise NotImplementedError("file_id 模式暂未实现，请使用 content_base64")

        raise ValueError("文件内容为空")

    def _parse_content(
        self,
        request: ReviewRequest,
        builder: ReviewResultBuilder,
    ) -> str:
        """解析正文."""
        text = request.get_content_text()
        if text is not None:
            builder.set_content_source("text")
            text = text.strip()
            if not text:
                raise ValueError("正文内容为空")
            return text

        file_input = request.get_content_file()
        if file_input is not None:
            builder.set_content_source("file")
            return self._parse_content_from_file(file_input, builder)

        raise ValueError("正文 input_type 无效")

    def _parse_content_from_file(
        self,
        file_input: ContentFileInput,
        builder: ReviewResultBuilder,
    ) -> str:
        """从文件解析正文."""
        file_ref = file_input.file

        if file_ref.content_base64:
            content = base64.b64decode(file_ref.content_base64)
            parsed = ContentParser.parse(
                content,
                format_hint=file_ref.format,
            )
            if parsed.truncated:
                builder.set_content_truncated(True)
            return parsed.text

        if file_ref.file_id:
            raise NotImplementedError("file_id 模式暂未实现，请使用 content_base64")

        raise ValueError("文件内容为空")

    def _crawl_websites(
        self,
        urls: List[str],
        max_pages: int = 10,
        timeout: int = 30,
    ) -> Tuple[Optional[CrawledDomain], List[tuple]]:
        """爬取官网（多 URL 并行爬取）."""
        if not urls:
            return None, []

        valid_urls = []
        failed_urls: List[tuple] = []

        for url in urls:
            if not url or not url.startswith(("http://", "https://")):
                failed_urls.append((url or "空URL", "URL格式无效"))
            else:
                valid_urls.append(url)

        if not valid_urls:
            return None, failed_urls

        if len(valid_urls) == 1:
            try:
                result = WebsiteCrawler.crawl(valid_urls[0], max_pages=max_pages, timeout=timeout)
                if result and result.success_pages > 0:
                    return result, failed_urls
                elif result:
                    failed_urls.append((valid_urls[0], f"爬取失败: {result.pages[0].error if result.pages else '未知错误'}"))
            except Exception as e:
                failed_urls.append((valid_urls[0], str(e)))
            return None, failed_urls

        def _crawl_one(url):
            try:
                return url, WebsiteCrawler.crawl(url, max_pages=max_pages, timeout=timeout), None
            except Exception as e:
                return url, None, str(e)

        crawled = None
        with ThreadPoolExecutor(max_workers=min(4, len(valid_urls))) as executor:
            futures = [executor.submit(_crawl_one, url) for url in valid_urls]
            for future in futures:
                url, result, error = future.result()
                if error:
                    failed_urls.append((url, error))
                elif result and result.success_pages > 0:
                    if crawled is None:
                        crawled = result
                elif result:
                    failed_urls.append((url, f"爬取失败: {result.pages[0].error if result.pages else '未知错误'}"))

        return crawled, failed_urls

    def _load_rules(self, rules: Optional[Dict[str, Any]], template_name: str = "general") -> Any:
        """加载规则（根据 TaskPlanner 结果选择模板）."""
        if rules is not None:
            return self.rule_loader.from_dict(rules)

        if self._default_rule_set is not None:
            return self._default_rule_set

        return self.rule_loader.from_template(template_name)

    def _run_rule_engine(
        self,
        rules: Dict[str, Any],
        content: str,
        submission: Optional[Submission],
        website: Optional[CrawledDomain],
        industry_kb: Optional[Any] = None,
    ) -> List[Issue]:
        """运行规则引擎."""
        engine = RuleEngine(rules, submission=submission, industry_kb=industry_kb)
        return engine.check(content, website_data=website)

    def _run_llm_reviewer(
        self,
        content: str,
        submission: Optional[Submission],
        website: Optional[CrawledDomain],
        starting_id: int,
        industry_kb: Optional[Any] = None,
        prompt_profile: str = "general",
    ) -> tuple:
        """运行 LLM 语义审核.

        Args:
            prompt_profile: prompt 模板 profile（general/finance/medical/...）

        Returns:
            (issues列表, llm_result对象)
        """
        if submission is None:
            submission = Submission()

        industry_context = ""
        if industry_kb:
            industry_context = industry_kb.build_llm_context()

        result = self.llm_reviewer.review(
            content, submission, website_data=website,
            industry_context=industry_context,
            prompt_profile=prompt_profile,
        )
        if result.error:
            return [], result

        issues = LLMReviewer.to_standard_issues(result, starting_id=starting_id + 1)
        return issues, result

    @staticmethod
    def _build_error_response(
        code: str,
        message: str,
        request_id: Optional[str] = None,
    ) -> ReviewResponse:
        """构建错误响应."""
        return ReviewResultBuilder(
            request_id=request_id,
        ).build(
            status=ReviewStatus.FAILED,
            error=ReviewError(code=code, message=message),
        )


# ========================================================================
# 便捷函数
# ========================================================================

def review(
    content: str,
    submission: Optional[Submission] = None,
    submission_text: Optional[str] = None,
    official_urls: Optional[List[str]] = None,
    rules: Optional[Dict[str, Any]] = None,
    llm_config: Optional[LLMProviderConfig] = None,
) -> ReviewResponse:
    """便捷审核函数 — 简化调用."""
    request_data: Dict[str, Any] = {
        "content": {"input_type": "text", "text": content},
        "submission": {},
        "official_urls": official_urls or [],
    }

    if submission is not None:
        request_data["submission"] = {"input_type": "json", "data": submission.model_dump()}
    elif submission_text is not None:
        request_data["submission"] = {"input_type": "text", "text": submission_text}
    else:
        raise ValueError("必须提供 submission 或 submission_text")

    agent = ReviewAgent(llm_config=llm_config)
    return agent.review(request_data, rules=rules)


def review_with_file(
    content_file_path: str,
    submission_file_path: Optional[str] = None,
    official_urls: Optional[List[str]] = None,
    rules: Optional[Dict[str, Any]] = None,
) -> ReviewResponse:
    """从文件路径便捷审核."""
    import os

    with open(content_file_path, "rb") as f:
        content_b64 = base64.b64encode(f.read()).decode("utf-8")

    ext = os.path.splitext(content_file_path)[1][1:].lower()

    request_data: Dict[str, Any] = {
        "content": {
            "input_type": "file",
            "file": {
                "content_base64": content_b64,
                "filename": os.path.basename(content_file_path),
                "format": ext,
            },
        },
        "official_urls": official_urls or [],
    }

    if submission_file_path:
        with open(submission_file_path, "rb") as f:
            submission_b64 = base64.b64encode(f.read()).decode("utf-8")

        sub_ext = os.path.splitext(submission_file_path)[1][1:].lower()
        request_data["submission"] = {
            "input_type": "file",
            "file": {
                "content_base64": submission_b64,
                "filename": os.path.basename(submission_file_path),
                "format": sub_ext,
            },
        }
    else:
        request_data["submission"] = {"input_type": "text", "text": ""}

    agent = ReviewAgent()
    return agent.review(request_data, rules=rules)
