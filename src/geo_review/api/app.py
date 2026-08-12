"""GEO 生文审核 API 服务 — FastAPI 应用.

提供 RESTful API 接口，支持 Web 前端调用：
- POST /api/v1/review        — JSON 方式提交审核
- POST /api/v1/review/upload — 文件上传方式提交审核
- GET  /api/v1/rules/templates — 获取可用规则模板列表
- GET  /api/v1/health        — 健康检查
- GET  /api/v1/metrics       — 系统指标
- GET/POST /api/v1/workflow/* — 审核流程管理
"""

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from geo_review.agent import BatchReviewService, ReviewAgent
from geo_review.auth import AuthService, SecurityUtils
from geo_review.config import AppConfig, load_config
from geo_review.history import HistoryService, init_database
from geo_review.industry.loader import IndustryLoader
from geo_review.llm.models import LLMProviderConfig
from geo_review.monitoring import MetricsMiddleware, MetricsCollector
from geo_review.rules.loader import RuleLoader
from geo_review.utils.security import (
    check_password_strength,
    check_secret_key_strength,
    is_production_env,
)
from geo_review.middleware.rate_limit import setup_rate_limit_middleware
from geo_review.workflow import WorkflowService

# Router imports
from geo_review.api.routers import (
    auth as auth_router,
    batch as batch_router,
    history as history_router,
    review as review_router,
    rules as rules_router,
    system as system_router,
    workflow as workflow_router,
)

logger = logging.getLogger(__name__)


def create_app(
    config: Optional[AppConfig] = None,
    config_path: Optional[str] = None,
) -> FastAPI:
    """创建 FastAPI 应用实例.

    Args:
        config: 应用配置（可选，优先使用）
        config_path: 配置文件路径（可选，当 config 为 None 时加载）
    """
    # 加载配置
    if config is None:
        config = load_config(config_path)

    # Lifespan 处理器 — 负责应用关闭时释放数据库连接
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup: 数据库初始化在 create_app() 中同步完成（此时无运行中的事件循环）
        yield
        # Shutdown: 释放数据库引擎连接池
        engine = getattr(app.state, "_db_engine", None)
        if engine:
            await engine.dispose()
            logger.info("数据库引擎已释放")

    app = FastAPI(
        title="GEO 生文审核 API",
        description="""
GEO 生文审核 Agent 的 RESTful API 接口。

## 功能
- 提交待审正文和提报表进行审核
- 支持 JSON 方式和文件上传方式提交
- 返回结构化审核结果（JSON / Markdown / HTML）
- 支持自定义审核规则
- 支持多 LLM Provider（OpenAI / DeepSeek / Anthropic）

## 审核流程
1. 解析提报表（Excel/JSON/文本）
2. 解析待审正文（PDF/Word/TXT）
3. 爬取官网内容（可选）
4. 规则引擎审核
5. LLM 语义审核
6. 生成审核报告
        """,
        version="1.0.0",
        docs_url=config.api.docs_url,
        redoc_url=config.api.redoc_url,
        lifespan=lifespan,
    )

    # 监控中间件（必须在 CORS 之前）
    app.add_middleware(MetricsMiddleware)

    # CORS 配置
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.api.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 从配置创建 LLM 配置
    llm_config = LLMProviderConfig(
        provider=config.llm.provider,
        base_url=config.llm.base_url,
        api_key=config.llm.api_key,
        model=config.llm.model,
        temperature=config.llm.temperature,
        max_tokens=config.llm.max_tokens,
        timeout=config.llm.timeout,
    )

    # 加载默认规则集（支持动态更新）
    default_rule_set = RuleLoader.from_template(config.rule_engine.default_template)
    app.state._rule_set = default_rule_set

    # 加载行业知识库
    industry_kbs = IndustryLoader.auto_load()
    if not industry_kbs:
        # 尝试从项目目录加载
        kb_dir = Path(__file__).parent.parent / "industry" / "kb"
        if kb_dir.exists():
            industry_kbs = IndustryLoader.load_directory(str(kb_dir))
    app.state._industry_kbs = industry_kbs
    logger.info(f"已加载 {len(industry_kbs)} 个行业知识库: {list(industry_kbs.keys())}")

    # 创建 Agent（传入共享规则集）
    agent = ReviewAgent(
        llm_config=llm_config,
        default_rule_set=default_rule_set,
        fact_check_enabled=config.fact_check.enabled,
        fact_check_max_claims=config.fact_check.max_claims,
        fact_check_max_search_results=config.fact_check.max_search_results,
        fact_check_search_timeout=config.fact_check.search_timeout,
    )
    app.state._agent = agent

    # 预加载 LLM 客户端，提前发现配置问题
    if llm_config and llm_config.api_key:
        try:
            _ = agent.llm_client
            logger.info(f"LLM 客户端初始化成功: {llm_config.provider} / {llm_config.model}")
        except Exception as e:
            logger.warning(f"LLM 客户端初始化失败: {e}")

    batch_service = BatchReviewService(
        agent,
        max_concurrent=config.batch.max_concurrent,
        item_max_retries=2,
        rate_limit_delay=0.5,
    )
    app.state._batch_service = batch_service
    app.state._config = config

    # 创建监控和流程服务
    metrics_collector = MetricsCollector()
    workflow_service = WorkflowService()
    app.state._metrics = metrics_collector
    app.state._workflow = workflow_service

    # 初始化数据库和历史服务
    # NOTE: 此处使用 asyncio.run() 是安全的 — create_app() 在 uvicorn 启动前
    # 同步调用，此时没有运行中的事件循环。数据库引擎的释放由 lifespan 处理器
    # 在应用关闭时负责。
    engine, async_session = asyncio.run(init_database(config.database.url))
    app.state._async_session = async_session

    history_service = HistoryService(async_session)
    app.state._history_service = history_service
    app.state._db_engine = engine

    # 将历史服务注入 Agent 和 BatchService
    agent.history_service = history_service
    batch_service.history_service = history_service

    # 初始化认证服务
    security = SecurityUtils(
        secret_key=config.auth.secret_key,
        access_token_expire_minutes=config.auth.token_expire_minutes,
    )
    auth_service = AuthService(async_session, security)
    app.state._auth_service = auth_service
    app.state._security = security

    # ================================================================
    # 创建/同步默认管理员账户
    # ================================================================
    async def _create_default_admin():
        existing = await auth_service.get_by_username(config.auth.default_admin_username)
        if not existing:
            try:
                await auth_service.register(
                    username=config.auth.default_admin_username,
                    password=config.auth.default_admin_password,
                    email="admin@geo-review.local",
                    full_name="系统管理员",
                    role="admin",
                )
                logger.info(
                    f"[SECURITY] 默认管理员账户已创建: {config.auth.default_admin_username}"
                )
            except ValueError as e:
                # 密码强度不足，使用临时强密码创建
                from geo_review.utils.security import generate_temp_password
                temp_pwd = generate_temp_password(16)
                logger.warning(
                    f"[SECURITY] 默认密码强度不足，已使用临时强密码创建管理员账户。\n"
                    f"         临时密码: {temp_pwd}\n"
                    f"         请使用此密码登录后立即修改。"
                )
                await auth_service.register(
                    username=config.auth.default_admin_username,
                    password=temp_pwd,
                    email="admin@geo-review.local",
                    full_name="系统管理员",
                    role="admin",
                )
            except Exception:
                pass
        else:
            # 已存在则同步 config.yaml 中的密码
            try:
                await auth_service.set_password(
                    username=config.auth.default_admin_username,
                    new_password=config.auth.default_admin_password,
                )
                logger.info(
                    f"[SECURITY] 默认管理员密码已从 config.yaml 同步: {config.auth.default_admin_username}"
                )
            except ValueError as e:
                logger.error(
                    f"[SECURITY] 默认管理员密码同步失败（密码强度不足）: {e}\n"
                    f"         请修改 config.yaml 中的 default_admin_password 为符合强度要求的密码。"
                )
            except Exception as e:
                logger.error(f"[SECURITY] 默认管理员密码同步失败: {e}")

    asyncio.run(_create_default_admin())

    # ================================================================
    # 一次性回填：历史记录中 task_name / company_name 为 NULL 的行
    # 通过文件名启发式推断并补全，仅在 NULL 时更新，幂等
    # ================================================================
    try:
        backfill_result = asyncio.run(history_service.backfill_inferred_names())
        if backfill_result["updated_task_name"] or backfill_result["updated_company_name"]:
            logger.info(
                f"[BACKFILL] 回填历史记录名称字段: "
                f"task_name={backfill_result['updated_task_name']}, "
                f"company_name={backfill_result['updated_company_name']}, "
                f"扫描={backfill_result['scanned']} 条"
            )
        else:
            logger.debug(
                f"[BACKFILL] 历史记录无需回填（扫描 {backfill_result['scanned']} 条，均已有名称）"
            )
    except Exception as e:
        logger.warning(f"[BACKFILL] 回填失败（非致命）: {e}")

    # ================================================================
    # 启动安全检查
    # ================================================================
    _run_startup_security_check(config)

    # ================================================================
    # API 限流
    # ================================================================
    setup_rate_limit_middleware(app, config)

    # ================================================================
    # 注册路由
    # ================================================================
    app.include_router(system_router.router)
    app.include_router(auth_router.router)
    app.include_router(rules_router.router)
    app.include_router(review_router.router)
    app.include_router(batch_router.router)
    app.include_router(history_router.router)
    app.include_router(workflow_router.router)

    # ================================================================
    # Web 前端静态文件
    # ================================================================
    app_dir = os.path.dirname(os.path.abspath(__file__))
    project_dir = os.path.dirname(os.path.dirname(os.path.dirname(app_dir)))
    static_dir = os.path.join(project_dir, "static")

    if os.path.exists(static_dir):
        # 静态文件缓存策略：
        # - 开发环境：禁用缓存，确保每次加载最新文件
        # - 生产环境：ETag + 长缓存（1年），文件更新后通过 ETag 自动失效
        is_prod = is_production_env()

        if is_prod:
            # 生产环境：长缓存 + Etag
            class _CacheStaticFiles(StaticFiles):
                async def get_response(self, path: str, scope):
                    response = await super().get_response(path, scope)
                    # 静态资源长缓存（hash 文件名可安全用 1 年）
                    response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
                    return response

            app.mount("/static", _CacheStaticFiles(directory=static_dir), name="static")
            logger.info("静态文件缓存策略: 生产模式（长缓存 + ETag）")
        else:
            # 开发环境：禁用缓存
            class _NoCacheStaticFiles(StaticFiles):
                async def get_response(self, path: str, scope):
                    response = await super().get_response(path, scope)
                    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
                    response.headers["Pragma"] = "no-cache"
                    response.headers["Expires"] = "0"
                    return response

            app.mount("/static", _NoCacheStaticFiles(directory=static_dir), name="static")
            logger.info("静态文件缓存策略: 开发模式（禁用缓存）")

        @app.get("/", response_class=HTMLResponse, include_in_schema=False)
        async def index():
            with open(os.path.join(static_dir, "index.html"), "r", encoding="utf-8") as f:
                return f.read()

        @app.get("/{full_path:path}", response_class=HTMLResponse, include_in_schema=False)
        async def catch_all(full_path: str):
            with open(os.path.join(static_dir, "index.html"), "r", encoding="utf-8") as f:
                return f.read()
    else:
        @app.get("/", response_class=HTMLResponse, include_in_schema=False)
        async def index_fallback():
            return """
            <!DOCTYPE html>
            <html lang="zh-CN">
            <head><meta charset="UTF-8"><title>GEO 生文审核 Agent</title><style>
            body{font-family:sans-serif;max-width:800px;margin:0 auto;padding:40px;text-align:center;}
            .container{padding:40px;border:1px solid #eee;border-radius:8px;}
            .logo{font-size:48px;margin-bottom:20px;}
            .error{color:#dc3545;margin-bottom:20px;}
            .hint{color:#6c757d;font-size:14px;line-height:1.8;}
            </style></head>
            <body>
            <div class="container">
            <div class="logo">⚠️</div>
            <h1>无法加载前端页面</h1>
            <div class="error">静态文件目录不存在: {}</div>
            <div class="hint">
            <p>请确认您在正确的项目目录下运行服务：</p>
            <p><code>cd "c:\\Users\\zhaoting1\\Desktop\\GEO生文审核"</code></p>
            <p><code>python run.py</code></p>
            <p>或双击运行 <code>start.bat</code></p>
            <p><br>您仍然可以通过 API 访问服务：</p>
            <p>健康检查: <a href="/api/v1/health">/api/v1/health</a></p>
            <p>API 文档: <a href="/docs">/docs</a></p>
            </div>
            </div>
            </body></html>
            """.format(static_dir)

    return app


# ========================================================================
# 启动安全检查
# ========================================================================

def _run_startup_security_check(config: AppConfig):
    """启动时运行安全配置检查并输出报告."""
    issues: List[str] = []
    warnings: List[str] = []
    info_items: List[str] = []

    # 1. 检查认证状态
    if not config.auth.enabled:
        issues.append("auth.enabled = false — API 认证已关闭，所有接口无需认证")
    else:
        info_items.append("API 认证已启用")

    # 2. 检查密钥强度
    if config.auth.secret_key:
        ok, errors = check_secret_key_strength(config.auth.secret_key)
        if not ok:
            for e in errors:
                issues.append(f"JWT 密钥: {e}")
        else:
            info_items.append("JWT 密钥强度合格")

    # 3. 检查管理员密码强度
    if config.auth.default_admin_password:
        ok, errors = check_password_strength(config.auth.default_admin_password)
        if not ok:
            for e in errors:
                warnings.append(f"默认管理员密码: {e}")
        else:
            info_items.append("默认管理员密码强度合格")

    # 4. 检查注册功能
    if config.auth.allow_registration:
        warnings.append(
            "allow_registration = true — 允许任意用户注册，生产环境建议关闭"
        )
    else:
        info_items.append("用户注册已关闭")

    # 5. 检查 CORS
    if "*" in config.api.cors_origins:
        warnings.append(
            'CORS 允许来源包含 "*" — 生产环境应限制为具体域名'
        )

    # 6. 检查 LLM API Key
    if not config.llm.api_key:
        warnings.append("LLM API Key 未配置 — LLM 语义审核将不可用")
    else:
        info_items.append("LLM API Key 已配置")

    # 7. 环境检测
    env_label = "生产环境" if is_production_env() else "开发环境"
    info_items.append(f"当前运行环境: {env_label}")

    # 输出安全报告
    print("\n" + "=" * 64)
    print("  GEO 生文审核 Agent — 安全配置检查报告")
    print("=" * 64)

    if issues:
        print("\n  [严重] 必须修复的安全问题:")
        for item in issues:
            print(f"    - {item}")

    if warnings:
        print("\n  [警告] 建议关注的安全问题:")
        for item in warnings:
            print(f"    - {item}")

    if info_items:
        print("\n  [信息] 配置状态:")
        for item in info_items:
            print(f"    - {item}")

    if issues:
        print("\n  请修复上述严重问题后再运行生产环境。")
        print("  运行 `python scripts/generate_secret_key.py` 生成安全密钥。")
    else:
        print("\n  安全检查通过，无严重安全问题。")

    print("=" * 64 + "\n")


# ========================================================================
# 创建默认应用实例
# ========================================================================
# 注意：模块级直接 create_app() 会在 import 时触发配置加载，
# 若此时工作目录不在项目根（如 run.py 从其他目录调用），
# load_config(None) 会因找不到 config.yaml 而失败。
# 因此改为懒加载：仅在直接运行本文件或 uvicorn 入口需要时才创建。

app: Optional[FastAPI] = None


def get_app() -> FastAPI:
    """获取默认应用实例（懒加载）."""
    global app
    if app is None:
        # 尝试从项目根目录加载 config.yaml
        import os
        _project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        _config_path = os.path.join(_project_root, "config.yaml")
        if os.path.exists(_config_path):
            app = create_app(config_path=_config_path)
        else:
            app = create_app()
    return app
