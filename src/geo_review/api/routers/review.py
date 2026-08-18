"""Single review routes — JSON, text+file, upload, preview."""

import asyncio
import base64
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse

from geo_review.parsers.url_fetcher import URLDocumentFetcher
from geo_review.result.builder import ReviewResultFormatter
from geo_review.rules.loader import RuleLoader
from geo_review.middleware.rate_limit import limiter, LIMIT_REVIEW
from geo_review.auth.schemas import UserResponse

from .deps import get_current_user
from .helpers import format_response, get_file_extension
from .schemas import APIReviewRequest

router = APIRouter()


# ================================================================
# 审核 — JSON 方式
# ================================================================

@router.post("/api/v1/review", tags=["审核"])
@limiter.limit(LIMIT_REVIEW)
async def review_content(
    body: APIReviewRequest,
    request: Request,
    output_format: str = "json",
    industry: Optional[str] = None,
    current_user: UserResponse = Depends(get_current_user),
):
    """提交审核请求（JSON 方式）.

    请求体格式参考 review-request.schema.json。

    参数:
    - **request_data**: 审核请求数据
    - **output_format**: 输出格式（json/markdown/html，默认 json）
    - **industry**: 行业标识（如 finance/medical/technology），使用对应行业知识库
    """
    config = request.app.state._config
    agent = request.app.state._agent
    metrics_collector = request.app.state._metrics
    workflow_service = request.app.state._workflow
    history_service = request.app.state._history_service

    request_data = body.model_dump(exclude_none=False)

    start = time.perf_counter()
    try:
        # 自动补充缺失的 submission 字段（提供默认值避免验证失败）
        if "submission" not in request_data or not request_data["submission"]:
            request_data["submission"] = {
                "input_type": "json",
                "data": {
                    "task_name": "未指定任务",
                    "company_name": "未指定公司",
                    "product_or_service": ["未指定"],
                    "core_topic": "未指定",
                    "key_points": ["未指定"],
                    "forbidden_claims": ["行业第一", "唯一", "100%", "最好", "最佳"],
                    "official_urls": ["https://example.com"],
                }
            }
        elif request_data["submission"].get("input_type") == "json":
            data = request_data["submission"].setdefault("data", {})
            data.setdefault("task_name", "未指定任务")
            data.setdefault("company_name", "未指定公司")
            data.setdefault("product_or_service", ["未指定"])
            data.setdefault("core_topic", "未指定")
            data.setdefault("key_points", ["未指定"])
            data.setdefault("forbidden_claims", ["行业第一", "唯一", "100%", "最好", "最佳"])
            data.setdefault("official_urls", ["https://example.com"])

        # 关闭官网爬取（默认不爬取，避免网络问题导致审核失败）
        if "options" not in request_data:
            request_data["options"] = {}
        opts = request_data["options"]
        if "crawl_official_urls" not in opts:
            opts["crawl_official_urls"] = False
        # 启用爬取时，注入 config.yaml 的 crawler 配置（替代 ReviewOptions 默认值 10页/30秒）
        if opts.get("crawl_official_urls"):
            opts.setdefault("crawl_max_pages", config.crawler.max_pages)
            opts.setdefault("crawl_timeout_seconds", config.crawler.timeout)

        # 获取行业知识库
        industry_kb = None
        if industry:
            industry_kb = request.app.state._industry_kbs.get(industry)

        response = await asyncio.to_thread(
            agent.review, request_data, industry_kb=industry_kb, industry=industry
        )

        # 记录指标
        duration_ms = (time.perf_counter() - start) * 1000
        use_llm = bool(response.llm_review and not response.llm_review.error)
        issue_count = len(response.issues)
        verdict = response.verdict.value
        metrics_collector.record_review(use_llm, issue_count, verdict, duration_ms)
        if response.llm_review and response.llm_review.error:
            metrics_collector.record_llm_failure()

        # 初始化流程状态
        workflow_service.init_status(str(response.review_id))

        if history_service:
            try:
                await history_service.save_review(response, request_data=request_data)
            except Exception:
                pass

        return format_response(response, output_format)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"审核处理异常: {type(e).__name__}: {str(e)}")


# ================================================================
# 审核 — 文本输入 + 提报表文件上传
# ================================================================

@router.post("/api/v1/review/text-with-submission", tags=["审核"])
@limiter.limit(LIMIT_REVIEW)
async def review_text_with_submission(
    request: Request,
    content_text: str = Form(..., description="待审正文文本"),
    submission_file: UploadFile = File(..., description="提报表文件（xlsx/json/txt），必填"),
    company_name: Optional[str] = Form(None, description="公司名称"),
    task_name: Optional[str] = Form(None, description="任务名称"),
    official_urls: Optional[str] = Form(None, description="额外官网URL，逗号分隔"),
    rule_template: Optional[str] = Form(None, description="规则模板名称"),
    output_format: str = Form("json", description="输出格式"),
    crawl_official_urls: str = Form("false", description="是否爬取官网"),
    current_user: UserResponse = Depends(get_current_user),
):
    """文本输入 + 提报表文件上传方式提交审核.

    输入文本正文，同时上传提报表文件进行审核。
    """
    crawl_official_urls = crawl_official_urls.lower() in ("true", "1", "yes")
    config = request.app.state._config
    agent = request.app.state._agent
    metrics_collector = request.app.state._metrics
    workflow_service = request.app.state._workflow
    history_service = request.app.state._history_service

    start = time.perf_counter()
    try:
        # 读取提报表文件
        submission_bytes = await submission_file.read()
        submission_b64 = base64.b64encode(submission_bytes).decode("utf-8")
        submission_ext = get_file_extension(submission_file.filename)

        # 构建请求数据
        request_data: Dict[str, Any] = {
            "content": {
                "input_type": "text",
                "text": content_text,
            },
            "submission": {
                "input_type": "file",
                "file": {
                    "content_base64": submission_b64,
                    "filename": submission_file.filename,
                    "format": submission_ext,
                },
            },
            "options": {
                "crawl_official_urls": crawl_official_urls,
                # 接入 config.yaml 的 crawler 配置，替代 ReviewOptions 默认值(10页/30秒)
                "crawl_max_pages": config.crawler.max_pages,
                "crawl_timeout_seconds": config.crawler.timeout,
            },
        }

        # 添加公司名和任务名（如果提供，会覆盖提报表中的值）
        if company_name or task_name:
            request_data.setdefault("submission", {})
            request_data["submission"].setdefault("input_type", "file")
            override_data = {}
            if company_name:
                override_data["company_name"] = company_name
            if task_name:
                override_data["task_name"] = task_name
            if override_data:
                request_data["submission"]["override_data"] = override_data

        # 官网 URL
        if official_urls:
            urls = [u.strip() for u in official_urls.split(",") if u.strip()]
            request_data["official_urls"] = urls

        # 规则模板
        rules = None
        if rule_template:
            try:
                rule_set = RuleLoader.from_template(rule_template)
                rules = rule_set.model_dump()
            except FileNotFoundError:
                raise HTTPException(
                    status_code=400,
                    detail=f"规则模板 '{rule_template}' 不存在",
                )

        response = await asyncio.to_thread(
            agent.review, request_data, rules=rules
        )

        # 记录指标
        duration_ms = (time.perf_counter() - start) * 1000
        use_llm = bool(response.llm_review and not response.llm_review.error)
        issue_count = len(response.issues)
        verdict = response.verdict.value
        metrics_collector.record_review(use_llm, issue_count, verdict, duration_ms)
        if response.llm_review and response.llm_review.error:
            metrics_collector.record_llm_failure()

        # 初始化流程状态
        workflow_service.init_status(str(response.review_id))

        if history_service:
            try:
                await history_service.save_review(response, request_data=request_data)
            except Exception:
                pass

        return format_response(response, output_format)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"审核处理异常: {type(e).__name__}: {str(e)}")


# ================================================================
# 审核 — 文件上传方式
# ================================================================

@router.post("/api/v1/review/upload", tags=["审核"])
@limiter.limit(LIMIT_REVIEW)
async def review_upload(
    request: Request,
    content_file: Optional[UploadFile] = File(None, description="待审正文文件（pdf/docx/doc/txt），与 document_url 二选一"),
    submission_file: UploadFile = File(..., description="提报表文件（xlsx/json/txt）"),
    document_url: Optional[str] = Form(None, description="文档链接（飞书链接等），与 content_file 二选一"),
    official_urls: Optional[str] = Form(None, description="额外官网URL，逗号分隔"),
    rule_template: Optional[str] = Form(None, description="规则模板名称"),
    output_format: str = Form("json", description="输出格式"),
    crawl_official_urls: str = Form("true", description="是否爬取官网"),
    industry: Optional[str] = Form(None, description="行业标识（finance/medical/technology）"),
    current_user: UserResponse = Depends(get_current_user),
):
    """通过文件上传或文档链接方式提交审核.

    上传待审正文文件和提报表文件进行审核，
    或提供文档链接（飞书等）自动抓取正文内容。
    """
    crawl_official_urls = crawl_official_urls.lower() in ("true", "1", "yes")
    config = request.app.state._config
    agent = request.app.state._agent
    metrics_collector = request.app.state._metrics
    workflow_service = request.app.state._workflow
    history_service = request.app.state._history_service

    start = time.perf_counter()
    try:
        # 校验: content_file 和 document_url 至少提供一个
        if not content_file and not document_url:
            raise HTTPException(
                status_code=400,
                detail="请提供待审正文文件或文档链接",
            )

        submission_bytes = await submission_file.read()
        submission_b64 = base64.b64encode(submission_bytes).decode("utf-8")
        submission_ext = get_file_extension(submission_file.filename)

        # 构建请求数据 — 提报表部分
        request_data: Dict[str, Any] = {
            "submission": {
                "input_type": "file",
                "file": {
                    "content_base64": submission_b64,
                    "filename": submission_file.filename,
                    "format": submission_ext,
                },
            },
            "options": {
                "crawl_official_urls": crawl_official_urls,
                # 接入 config.yaml 的 crawler 配置，替代 ReviewOptions 默认值(10页/30秒)
                "crawl_max_pages": config.crawler.max_pages,
                "crawl_timeout_seconds": config.crawler.timeout,
            },
        }

        # 正文部分: 优先使用文档链接，其次使用上传文件
        if document_url:
            try:
                # 在独立线程中运行，避免 Playwright Sync API 与 asyncio 冲突
                fetched = await asyncio.to_thread(URLDocumentFetcher.fetch, document_url)
                request_data["content"] = {
                    "input_type": "text",
                    "text": fetched.text,
                }
                # 保存文档标题供历史记录使用
                request_data["metadata"] = {
                    "document_url": document_url,
                    "document_title": fetched.filename or "",
                    "content_source": fetched.source,
                }
            except Exception as e:
                raise HTTPException(
                    status_code=400,
                    detail=f"文档链接抓取失败: {str(e)}",
                )
        else:
            content_bytes = await content_file.read()
            content_b64 = base64.b64encode(content_bytes).decode("utf-8")
            content_ext = get_file_extension(content_file.filename)
            request_data["content"] = {
                "input_type": "file",
                "file": {
                    "content_base64": content_b64,
                    "filename": content_file.filename,
                    "format": content_ext,
                },
            }

        # 官网 URL
        if official_urls:
            urls = [u.strip() for u in official_urls.split(",") if u.strip()]
            request_data["official_urls"] = urls

        # 规则
        rules = None
        if rule_template:
            try:
                rule_set = RuleLoader.from_template(rule_template)
                rules = rule_set.model_dump()
            except FileNotFoundError:
                raise HTTPException(
                    status_code=400,
                    detail=f"规则模板 '{rule_template}' 不存在",
                )

        # 获取行业知识库
        industry_kb = None
        if industry:
            industry_kb = request.app.state._industry_kbs.get(industry)

        response = await asyncio.to_thread(
            agent.review, request_data, rules=rules,
            industry_kb=industry_kb, industry=industry
        )

        # 记录指标
        duration_ms = (time.perf_counter() - start) * 1000
        use_llm = bool(response.llm_review and not response.llm_review.error)
        issue_count = len(response.issues)
        verdict = response.verdict.value
        metrics_collector.record_review(use_llm, issue_count, verdict, duration_ms)
        if response.llm_review and response.llm_review.error:
            metrics_collector.record_llm_failure()

        # 初始化流程状态
        workflow_service.init_status(str(response.review_id))

        if history_service:
            try:
                await history_service.save_review(response, request_data=request_data)
            except Exception:
                pass

        return format_response(response, output_format)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"审核处理异常: {type(e).__name__}: {str(e)}")


# ================================================================
# 审核结果预览（HTML 报告）
# ================================================================

@router.post("/api/v1/review/preview", tags=["审核"])
@limiter.limit(LIMIT_REVIEW)
async def review_preview(
    body: APIReviewRequest,
    request: Request,
    current_user: UserResponse = Depends(get_current_user),
):
    """提交审核并返回 HTML 格式的可视化报告."""
    agent = request.app.state._agent
    history_service = request.app.state._history_service

    request_data = body.model_dump(exclude_none=False)

    try:
        response = await asyncio.to_thread(agent.review, request_data)

        if history_service:
            try:
                await history_service.save_review(response, request_data=request_data)
            except Exception:
                pass

        html_content = ReviewResultFormatter.to_html(response)
        return HTMLResponse(content=html_content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"审核处理异常: {type(e).__name__}: {str(e)}")
