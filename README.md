# GEO 内容合规与可引用性审核 Agent

> 基于 Agent 架构的 AI 内容审核系统，专为 **GEO（Generative Engine Optimization，生成式引擎优化）** 场景设计。将审核过程拆分为 **任务规划、证据获取、规则检测、语义判断、结果裁决** 五个阶段，根据内容类型动态选择审核策略和工具。支持多行业动态规则、LLM 可引用性分析、品牌实体一致性校验、联网事实核查（证据链模型）、多维度审核评分卡、人工复核闭环。

---

## 目录

- [背景与痛点](#背景与痛点)
- [产品方案](#产品方案)
- [核心功能](#核心功能)
- [技术架构](#技术架构)
- [快速启动](#快速启动)
- [项目结构](#项目结构)
- [API 文档](#api-文档)
- [部署指南](#部署指南)

---

## 背景与痛点

### 为什么需要 GEO 生文审核？

在 AI 搜索时代，企业内容不仅要"写得好"，更要"被 AI 引用"。然而，传统内容审核存在以下痛点：

| 痛点 | 问题描述 |
|------|----------|
| **规则滞后** | 人工审核依赖经验，新法规、新平台规则难以及时覆盖 |
| **效率低下** | 一篇 2000 字文章人工审核需 15-30 分钟，无法规模化 |
| **标准不一** | 不同审核员对同一内容的判断可能差异巨大 |
| **GEO 盲区** | 传统审核只关注合规性，忽略 AI 搜索引擎的"可引用性"需求 |
| **品牌风险** | AI 生成内容中品牌实体描述不准确，导致用户认知偏差 |
| **缺乏量化** | 审核结果停留在"通过/不通过"，缺少结构化问题分析和改进建议 |

### 目标用户

- **内容运营团队**：需要快速审核大量 AI 生成/人工撰写的营销内容
- **合规部门**：需要确保内容符合行业法规（金融、医疗等）
- **SEO/GEO 团队**：需要确保内容对 AI 搜索引擎友好、可被准确引用

---

## 产品方案

### 核心理念

**GEO 内容合规与可引用性审核 Agent** 将审核过程拆分为五阶段（Plan → Detect → Evidence → Assess → Decide），根据内容类型动态选择审核策略和工具：

```
用户提交内容
    ↓
TaskPlanner（任务规划）
    → 识别内容类型 → 选择规则模板 → 选择 Prompt → 决定是否爬官网
    ↓
┌─────────────────┬─────────────────┬─────────────────┐
│  合规性层         │  事实层          │  GEO质量层       │
│  · 规则引擎       │  · 官网核验      │  · 可引用性      │
│  · 行业规则       │  · 联网搜索      │  · 实体清晰      │
│  · 语义风险       │  · 证据链模型    │  · 信息结构      │
│  (Detection)     │  (Evidence)     │  (Assessment)   │
└─────────────────┴─────────────────┴─────────────────┘
    ↓
决策层（Decision）
    → Pass / Revise / Reject + 多维度评分卡 + 修改建议
    ↓
人工复核闭环
    → 接受 / 误报标记 / 修改 / 重新审核
```

### 四层审核架构

```
                    GEO内容审核Agent
                           │
          ┌────────────────┼────────────────┐
          ↓                ↓                ↓
      合规性层          事实层           GEO质量层
          │                │                │
      规则引擎          证据核验          可引用性
      行业规则          官网核验          实体清晰
      语义风险          联网搜索          信息结构
          └────────────────┼────────────────┘
                           ↓
                      决策层
                           ↓
                 Pass / Revise / Reject
                           ↓
                 多维度评分卡 + 修改建议
```

---

## 核心功能

### 1. 智能任务规划（Task Planner）

提交内容后，系统自动分析内容类型并选择最优审核策略：

- **用户指定优先**：支持手动选择行业（金融/医疗/企业介绍/新闻/科技）
- **LLM 智能分类**：自动识别内容类型和行业属性
- **关键词匹配兜底**：金融/医疗等关键词快速匹配

### 2. 多模式审核

| 模式 | 说明 |
|------|------|
| 📝 文本输入 | 直接粘贴文本内容，支持提报表上传 |
| 📁 文件上传 | 支持 PDF/DOCX/DOC/TXT 正文 + XLSX/XLS 提报表 |
| 🔗 链接导入 | 输入飞书文档或网页链接，自动抓取内容 |

### 3. 动态规则引擎

- 按行业加载不同规则模板（金融、医疗、通用等）
- 支持绝对化用语、禁止词、必含词、竞品检测等多维度规则
- 规则可在线编辑、测试，无需重启服务

### 4. LLM 语义审核

- 基于大语言模型，模拟 6 年经验的 GEO 审核专家
- 9 大问题类型：与提报表不一致、与官网不一致、无依据宣称、夸大宣传、贬低竞品、语义风险、语气不当、GEO 可引用性问题、品牌实体一致性问题
- 4 级严重程度：CRITICAL（法律禁语/核心信息不符）/ MAJOR（无依据对比/承诺）/ MINOR（数据缺来源等）/ INFO（合规可优化）
- 每个问题包含：原文片段、问题原因、修改建议、置信度

### 5. GEO 可引用性审核

专为 AI 搜索引擎优化的审核维度：

- **实体明确性**：文章中涉及的公司、产品、人物是否清晰可识别
- **权威来源**：数据和观点是否有明确的来源引用
- **结构化信息**：关键信息是否以结构化方式呈现（列表、表格等）
- **事实依据**：结论是否有具体的事实支撑

### 6. 品牌实体一致性

确保 AI 模型能准确理解品牌：

- 品牌实体是否被正确识别和描述
- 产品功能描述是否与官网一致
- 品牌能力边界是否清晰（不夸大、不模糊）

### 7. 联网事实核查（证据链模型）

对正文中的关键事实性声明进行多引擎联网搜索核实：

- **Claim → Evidence → Source → Authority → Entailment → Verdict** 完整证据链
- 来源类型标注：官方官网 / 权威媒体 / 第三方来源
- 来源权威性评估：高 / 中 / 低
- 蕴含关系判断：支持 / 反驳 / 中立 / 矛盾

### 8. 多维度审核评分卡

审核结果不只是"通过/不通过"，而是提供量化评分：

```
综合评分       76 / 100
审核结论       ⚠ 建议修改

合规性         91
事实准确性     68
品牌一致性     79
GEO可引用性    65
内容质量       84
```

### 9. 人工复核闭环

AI 审核不是终点，而是人工复核的起点：

- **接受问题**：确认 AI 发现的问题有效
- **标记误报**：标注 AI 的误判，用于后续优化
- **人工修改**：记录修改结果，形成审核闭环
- **驳回**：对审核结果的整体驳回

### 10. 批量审核

- 支持同时提交最多 100 个审核任务
- 并发处理（最多 3 个并发）
- 实时进度展示，支持结果导出

### 11. 审核历史与追溯

- 完整的审核记录存储和搜索
- 支持按公司、结论、时间范围筛选
- 审核详情包含完整的问题列表、修改建议、修改清单
- 支持导出文本/Markdown 格式

### 12. 公司级资源缓存复用

同一公司多次提交审核时，自动复用已缓存的官网爬取数据和结构化证据，避免重复爬取和解析：

- **SQLite 持久化**：服务重启后缓存仍然有效
- **智能缓存键**：按公司名标准化匹配（去除"有限公司"等后缀，忽略大小写）
- **URL 变更检测**：官网 URL 变化时自动失效旧缓存并重新爬取
- **TTL 过期机制**：可配置各类资源的过期时间（爬取数据默认 24 小时，提报表默认 7 天）
- **缓存管理 API**：查看缓存统计、清除指定公司缓存、清除过期缓存
- **审核响应标注**：缓存命中时在 warnings 中显示 `RESOURCE_CACHE_HIT` 标记

---

## 技术架构

```
┌──────────────────────────────────────────────────────────┐
│                      前端 (SPA)                           │
│              HTML5 + CSS3 + Vanilla JS                    │
│         纯原生实现，无框架依赖，零构建步骤                    │
├──────────────────────────────────────────────────────────┤
│                    API 层 (FastAPI)                        │
│         RESTful API + JWT 认证 + CORS 支持                 │
├──────────┬──────────┬──────────┬──────────┬──────────────┤
│ 审核Agent │ 规则引擎  │ LLM客户端 │ 爬虫模块  │  历史服务     │
│ Reviewer │  Engine  │  Client  │ Crawler  │  History     │
│ Planner  │  Loader  │ Prompts  │ Website  │  Workflow    │
│  Batch   │  Issues  │ Reviewer │ Parser   │  Monitoring  │
│  Cache   │          │          │          │              │
├──────────┴──────────┴──────────┴──────────┴──────────────┤
│                    数据存储层                               │
│              SQLite (开发) / PostgreSQL (生产)              │
│              YAML 规则模板 + 行业知识库                      │
│              公司级资源缓存 (resource_cache.db)              │
└──────────────────────────────────────────────────────────┘
```

### 技术选型

| 层级 | 技术 | 说明 |
|------|------|------|
| 前端 | 原生 HTML/CSS/JS | 无框架依赖，加载快，适合演示 |
| 后端 | Python FastAPI | 高性能异步框架，自动生成 API 文档 |
| LLM | OpenAI 兼容 API | 支持 OpenAI / 通义千问 / DeepSeek / 智谱 GLM 等 |
| 爬虫 | Playwright | 无头浏览器，支持 JS 渲染页面 |
| 数据库 | SQLite + SQLAlchemy | 轻量级，零配置 |
| 部署 | Docker + Docker Compose | 一键构建和部署 |

### 审核 Agent 架构

```
                    ┌──────────────┐
                    │  ReviewAgent │  ← 总调度
                    └──────┬───────┘
           ┌───────────────┼───────────────┐
           ▼               ▼               ▼
    ┌──────────────┐ ┌──────────┐ ┌──────────────┐
    │ TaskPlanner  │ │ 规则引擎  │ │ LLMReviewer  │
    │ 任务规划      │ │ 快速过滤  │ │ 语义审核      │
    └──────────────┘ └──────────┘ └──────────────┘
           │                               │
           ▼                               ▼
    ┌──────────────┐               ┌──────────────┐
    │ 动态Prompt   │               │ 结构化输出    │
    │ 分块注入      │               │ JSON格式化    │
    └──────────────┘               └──────────────┘
```

---

## 快速启动

### 环境要求

- Python 3.10+
- pip

### 1. 克隆项目

```bash
git clone <your-repo-url>
cd GEO生文审核
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

安装 Playwright 浏览器内核（官网爬取功能依赖，Docker 部署已内置此步骤）：

```bash
python -m playwright install chromium
```

### 3. 配置环境变量（推荐 .env 方式）

复制 `.env.example` 为 `.env` 并填入配置（`.env` 已被 .gitignore 排除，不会泄露到代码仓库）：

```bash
cp .env.example .env
```

最小配置只需 LLM API Key（支持任意 OpenAI 兼容 API）：

```env
# 必填：LLM API Key
LLM_API_KEY=your-api-key

# 可选：切换模型提供商（默认 OpenAI）
# LLM_BASE_URL=https://api.openai.com/v1
# LLM_MODEL=gpt-4o-mini
# 通义千问:  https://dashscope.aliyuncs.com/compatible-mode/v1 + qwen-plus
# DeepSeek:  https://api.deepseek.com/v1 + deepseek-chat
# 智谱 GLM:  https://open.bigmodel.cn/api/paas/v4 + glm-4-flash

# 推荐：JWT 认证密钥（生成命令见下）
AUTH_SECRET_KEY=<运行 python scripts/generate_secret_key.py 生成>

# 推荐：管理员密码（大小写字母+数字+特殊字符，至少 12 位）
AUTH_ADMIN_PASSWORD=YourStrongPassword@123
```

生成 JWT 密钥：

```bash
python scripts/generate_secret_key.py
```

> 环境变量优先级高于 `config.yaml`。如需修改其余参数（爬虫、规则引擎、限流等）可编辑 `config.yaml`，但**切勿将真实 API Key 写入 config.yaml 后提交到 Git**。
>
> 认证说明：系统内置 JWT 认证，服务启动后使用 `AUTH_ADMIN_PASSWORD` 对应的管理员账号登录，业务用户通过 `/api/v1/auth/register` 注册。生产部署务必完成认证配置，详见 [deploy.md](deploy.md) 的"生产安全清单"。

### 4. 启动服务

```bash
python run.py
```

### 5. 访问系统

打开浏览器访问：

- **Web 界面**：http://127.0.0.1:8000
- **API 文档**：http://127.0.0.1:8000/docs
- **健康检查**：http://127.0.0.1:8000/api/v1/health

### 运行测试（可选）

```bash
pytest
```

### Docker 部署（推荐）

```bash
docker compose up -d --build
```

访问 http://localhost:8000

---

## 项目结构

```
GEO生文审核/
├── run.py                      # 项目入口
├── config.yaml                 # 全局配置（LLM、爬虫、规则引擎等）
├── requirements.txt            # Python 依赖
├── Dockerfile                  # Docker 镜像构建
├── docker-compose.yml          # Docker 编排
├── pyproject.toml              # pytest 配置
├── deploy.md                   # 云服务器部署指南
├── static/                     # 前端静态文件
│   ├── index.html              # SPA 入口
│   ├── css/style.css           # 样式表（蓝色渐变主题）
│   └── js/app.js               # 前端核心逻辑（路由、API、组件）
├── rule_templates/             # 行业规则模板
│   ├── general.yaml            # 通用规则
│   └── finance.yaml            # 金融行业规则
├── prompt_templates/
│   └── prompts.yaml            # Prompt 模块配置（热加载，按 profile 组合）
├── scripts/
│   └── generate_secret_key.py  # JWT 密钥生成工具
├── tests/                      # 测试（pytest）
│   ├── test_rules_engine.py    # 规则引擎测试
│   ├── test_llm_client.py      # LLM 客户端测试
│   ├── test_config_loader.py   # 配置加载测试
│   ├── test_history_search.py  # 历史检索测试
│   ├── test_resource_cache.py  # 资源缓存测试
│   └── test_e2e.py             # 端到端测试
└── src/geo_review/             # 后端核心模块
    ├── agent/                  # 审核 Agent
    │   ├── reviewer.py         # ReviewAgent 总调度
    │   ├── planner.py          # TaskPlanner 任务规划
    │   ├── batch.py            # 批量审核
    │   └── models.py           # Agent 数据模型
    ├── llm/                    # LLM 模块
    │   ├── client.py           # LLM 客户端（OpenAI 兼容，缓存+降级）
    │   ├── reviewer.py         # LLM 语义审核
    │   ├── prompts.py          # 动态 Prompt 管理
    │   ├── prompt_loader.py    # Prompt 热加载
    │   └── models.py           # LLM 数据模型
    ├── rules/                  # 规则引擎
    │   ├── engine.py           # 规则匹配引擎
    │   ├── loader.py           # 规则加载器
    │   ├── issues.py           # 问题类型定义
    │   └── models.py           # 规则数据模型
    ├── tools/                  # 联网核查工具
    │   ├── web_search.py       # 多引擎搜索（360/搜狗/Bing/DuckDuckGo）
    │   ├── fact_checker.py     # 事实核查（证据链模型）
    │   └── evidence_store.py   # 结构化证据提取
    ├── api/                    # API 层
    │   ├── app.py              # FastAPI 应用（中间件、静态资源）
    │   └── routers/            # 路由（review/batch/history/rules/
    │                           #       auth/workflow/cache/system）
    ├── middleware/
    │   └── rate_limit.py       # 接口限流
    ├── auth/                   # 认证模块
    │   ├── service.py          # 用户认证服务
    │   ├── security.py         # JWT 安全
    │   ├── models.py           # 用户数据模型
    │   └── schemas.py          # 请求/响应模型
    ├── crawlers/               # 爬虫模块
    │   └── website.py          # 官网内容爬取（Playwright）
    ├── parsers/                # 解析模块
    │   ├── content.py          # 文件内容解析
    │   ├── submission.py       # 提报表解析
    │   └── url_fetcher.py      # URL 内容获取
    ├── history/                # 历史记录
    │   ├── service.py          # 历史记录服务
    │   └── models.py           # 历史数据模型
    ├── industry/               # 行业知识库
    │   ├── loader.py           # 行业配置加载
    │   ├── models.py           # 行业数据模型
    │   └── kb/finance.yaml     # 金融行业知识
    ├── result/                 # 审核结果
    │   ├── builder.py          # 结果构建器
    │   └── models.py           # 结果数据模型
    ├── workflow/               # 工作流
    │   ├── service.py          # 流程服务
    │   └── models.py           # 流程数据模型
    ├── monitoring/             # 监控
    │   ├── metrics.py          # 指标收集
    │   └── middleware.py       # 监控中间件
    ├── config/                 # 配置管理
    │   ├── loader.py           # 配置加载器
    │   └── models.py           # 配置数据模型
    ├── utils/                  # 工具函数
    │   ├── concurrency.py      # 并发控制（信号量+熔断器）
    │   ├── text.py             # 文本处理
    │   ├── security.py         # 安全工具
    │   └── time.py             # 时间工具
    ├── cache/                  # 资源缓存
    │   └── resource_cache.py   # 公司级资源缓存（SQLite 持久化）
    └── models.py               # 全局数据模型
```

---

## API 文档

启动服务后访问 http://127.0.0.1:8000/docs 查看完整的 Swagger API 文档。

### 核心接口

#### 审核

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/review` | JSON 方式提交审核 |
| POST | `/api/v1/review/text-with-submission` | 文本 + 提报表组合提交 |
| POST | `/api/v1/review/upload` | 文件上传审核（PDF/DOCX/XLSX 等） |
| POST | `/api/v1/review/preview` | 审核预览（不落库） |
| POST | `/api/v1/review/batch` | 批量审核（JSON） |
| POST | `/api/v1/review/batch/upload` | 批量审核（文件上传） |
| POST | `/api/v1/review/batch/urls` | 批量审核（URL 列表） |
| GET | `/api/v1/review/batch/{batch_id}/progress` | 批量审核进度 |
| GET | `/api/v1/review/batch/{batch_id}/result` | 批量审核结果 |
| POST | `/api/v1/review/batch/{batch_id}/cancel` | 取消批量任务 |

#### 认证

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/auth/register` | 用户注册 |
| POST | `/api/v1/auth/login` | 登录（获取 JWT） |
| GET | `/api/v1/auth/me` | 当前用户信息 |
| PUT | `/api/v1/auth/me` | 更新用户信息 |
| POST | `/api/v1/auth/change-password` | 修改密码 |

#### 历史记录与人工复核

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/history` | 审核历史列表（支持筛选） |
| GET | `/api/v1/history/statistics` | 审核统计 |
| GET | `/api/v1/history/{review_id}` | 审核详情 |
| DELETE | `/api/v1/history/{review_id}` | 删除审核记录 |
| POST | `/api/v1/history/batch-delete` | 批量删除 |
| PUT | `/api/v1/history/{review_id}/human-review` | 人工复核（接受/误报/驳回） |
| GET | `/api/v1/workflow/{review_id}` | 审核流程状态 |
| POST | `/api/v1/workflow/{review_id}/transition` | 流程状态流转 |
| POST/GET | `/api/v1/workflow/{review_id}/comments` | 流程评论（写/读） |

#### 规则与行业知识库

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/rules/templates` | 规则模板列表 |
| GET | `/api/v1/rules/templates/{name}` | 规则模板详情 |
| GET | `/api/v1/rules` | 自定义规则列表 |
| POST | `/api/v1/rules/add` | 新增规则 |
| POST | `/api/v1/rules/validate` | 校验规则 YAML |
| POST | `/api/v1/rules/test` | 规则试跑（输入文本返回命中） |
| DELETE | `/api/v1/rules/{rule_id}` | 删除规则 |
| GET | `/api/v1/industry/kb/{industry}` | 行业知识库 |

#### 系统、监控与缓存

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/health` | 健康检查 |
| GET | `/api/v1/metrics` | 系统监控指标 |
| GET | `/api/v1/metrics/llm` | LLM 调用统计 |
| GET | `/api/v1/config` | 运行时配置 |
| GET | `/api/v1/cache/stats` | 缓存统计 |
| GET | `/api/v1/cache/companies` | 缓存公司列表 |
| GET/DELETE | `/api/v1/cache/{company_name}` | 查看/清除公司缓存 |
| DELETE | `/api/v1/cache` | 清除全部缓存 |
| POST | `/api/v1/cache/cleanup` | 清除过期缓存 |

### 审核请求示例

```json
{
  "content": "XX金融平台，年化收益率高达20%，行业第一！",
  "company_name": "XX金融",
  "industry": "finance",
  "rule_template": "finance",
  "official_urls": ["https://www.xxfinance.com"],
  "crawl_official_urls": true
}
```

### 审核响应示例

```json
{
  "review_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "completed",
  "verdict": "revise",
  "summary": "发现2个严重问题：夸大宣传年化收益率、使用绝对化用语'行业第一'",
  "plan_summary": {
    "content_type": "金融广告",
    "industry": "finance",
    "rule_template": "finance",
    "strategy": "金融合规审核",
    "strategies": ["规则引擎快速过滤", "LLM语义深度审核", "官网对比验证"]
  },
  "issues": [
    {
      "id": "iss-001",
      "type": "exaggeration",
      "severity": "critical",
      "title": "夸大宣传年化收益率",
      "evidence": { "snippet": "年化收益率高达20%" },
      "reason": "宣称的收益率远超行业平均水平，且未提供风险提示",
      "suggestion": "修改为'历史年化收益率X%，过往业绩不预示未来表现'",
      "confidence": 0.95
    }
  ],
  "stats": {
    "total": 2,
    "by_severity": { "critical": 2, "major": 0, "minor": 0, "info": 0 }
  },
  "revision_checklist": [
    "将'年化收益率高达20%'修改为含风险提示的表述",
    "删除'行业第一'等绝对化用语"
  ],
  "reviewed_at": "2024-01-15T14:30:00+08:00",
  "duration_ms": 3200
}
```

---

## 部署指南

详细的云服务器部署指南请参考 [deploy.md](deploy.md)。

简要步骤：

1. 购买阿里云 ECS（2核4G，Ubuntu 22.04）
2. 安装 Docker
3. 上传项目文件（注意排除 `.env`、`*.db` 等敏感文件）
4. `docker compose up -d --build`
5. 配置安全组开放 8000 端口
6. 访问 `http://服务器IP:8000`
7. **完成 deploy.md 的"生产安全清单"**（认证密钥、管理员密码、HTTPS）

---

## 许可证

未指定（如需开源发布，请添加 LICENSE 文件并更新此声明）

---

## 作者

GEO 生文审核团队

---

> **面试展示建议**：启动服务后，打开 http://127.0.0.1:8000 直接演示仪表盘和审核流程。重点展示：(1) 提交一篇金融广告文案 → (2) 查看 TaskPlanner 自动识别为金融类 → (3) 展示结构化审核结果（问题列表 + 修改建议）→ (4) 展示 GEO 可引用性分析卡片。