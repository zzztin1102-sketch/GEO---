"""Prompt 模板库 — LLM 审核用的 system/user prompt（动态注入版）.

架构优化：
    1. 拆分单一 SYSTEM_PROMPT → 基础框架 + 行业规则 + GEO特色 + 输出格式
    2. 根据 TaskPlanner 分类结果动态组合 prompt，减少 token 浪费
    3. 保留 estimate_tokens() 和智能截断逻辑
    4. 新增 PROMPT_PROFILES 映射 prompt_profile 到各模块组合
    5. ✅ 支持 YAML 外置模板热加载（prompt_templates/prompts.yaml）

Prompt 模块：
    - BASE_SYSTEM_PROMPT: 角色定义 + 核心原则 + 问题类型/严重程度 (~800 tokens)
    - GEO_SPECIFIC_PROMPT: GEO特有审核维度（可引用性、品牌一致性）(~400 tokens)
    - LEGAL_RULES_PROMPT: 通用法律禁语 + 擦边球 + 扩展绝对化 (~700 tokens)
    - DEEP_SEMANTIC_PROMPT: 深度语义审核要求 (~500 tokens)
    - INDUSTRY_RULES_FINANCE: 金融行业规则 (~500 tokens)
    - INDUSTRY_RULES_MEDICAL: 医疗行业规则 (~400 tokens)
    - OUTPUT_FORMAT_PROMPT: JSON输出格式 + 要求 (~500 tokens)
"""

import re
import logging
from typing import Dict, Optional

from geo_review.llm.prompt_loader import get_prompt_loader

logger = logging.getLogger(__name__)


# ====================================================================
# Token 估算
# ====================================================================

def estimate_tokens(text: str) -> int:
    """粗略估算文本的 token 数."""
    if not text:
        return 0
    chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
    english_words = len(re.findall(r'[a-zA-Z]+', text))
    other_chars = len(text) - chinese_chars - len(re.findall(r'[a-zA-Z]', text))
    return int(chinese_chars * 1.5 + english_words * 0.25 + other_chars * 0.5)


# Token 预算
TOKEN_BUDGET = 24000
# 动态 system prompt 预估上限（实际会根据 profile 动态计算）
MAX_SYSTEM_PROMPT_TOKENS = 2500
USER_PROMPT_BUDGET = TOKEN_BUDGET - MAX_SYSTEM_PROMPT_TOKENS
CONTENT_TOKEN_BUDGET = USER_PROMPT_BUDGET - 3000


# ====================================================================
# 模块1: 基础框架 Prompt（角色 + 核心原则 + 问题类型 + 严重程度）
# ====================================================================

BASE_SYSTEM_PROMPT = """你是 GEO 内容合规审核员，负责审核面向 AI 搜索引擎的生文内容。确保内容可溯源、可验证、合规且对 AI 搜索引擎友好。

## 审核原则
1. **语义理解优先**：深入理解文案的隐含意义、情感倾向和逻辑关系，不做表面关键词匹配
2. **事实与语义并重**：既核实事实准确性，也判断语义表达是否恰当、是否存在误导
3. **上下文敏感**：结合上下文理解表述的合理性和潜在风险
4. **核心信息核验**：公司名/产品名/关键数据必须与提报表或官网一致，不一致是严重问题
5. **边界模糊标注**：遇到无法确定的场景标注"待人工复审"，不强行判定

## 深度语义审核要点
- **语义一致性**：文案整体语义是否与提报核心主题一致
- **隐含风险**：表面合规但隐含误导、夸大或不当暗示的表述
- **逻辑连贯**：文案逻辑是否自洽，论证是否合理
- **语句质量**：检查成分残缺、语义断裂、搭配不当、指代不明、逻辑矛盾、重复累赘
- **用词规范**：识别擦边球和扩展绝对化用语（暗示排他性或领先地位但未直接使用禁用词的表述）

## 问题类型（type）
- **inconsistent_with_submission**: 与提报表不一致
- **inconsistent_with_website**: 与官网信息不一致
- **unsupported_claim**: 无依据的表述（包括隐含暗示）
- **exaggeration**: 夸大宣传、绝对化用语
- **competitor_disparagement**: 拉踩竞品
- **semantic_risk**: 语义风险（表面合规但隐含误导或歧义）
- **tone_issue**: 语气不当
- **geo_citability**: AI可引用性问题（缺少明确实体、权威来源、结构化信息或事实依据）
- **geo_brand_consistency**: 品牌实体一致性问题

## 严重程度（severity）
- **critical**: 法律禁语、核心信息严重不符、疑似虚构内容、触发行业红线
- **major**: 对比性表述无依据、承诺性表述无限定、事实性描述无来源支撑
- **minor**: 数据引用缺来源、用词不够专业、结构化信息不足
- **info**: 内容合规，可优化但无风险"""


# ====================================================================
# 模块2: GEO 特有审核维度（可引用性 + 品牌一致性）
# ====================================================================

GEO_SPECIFIC_PROMPT = """## GEO 特色审核维度

内容最终要被 AI 搜索引擎抓取和引用，需确保对 AI 友好。

### 1. AI 可引用性（geo_citability）
- **实体明确性**：是否包含明确具体的实体名称（公司全称、产品名），而非泛称、代词
- **权威来源**：关键数据和声明是否有可验证的来源引用、权威机构背书
- **结构化信息**：是否包含列表、对比、分层等结构化表达，利于 AI 提取摘要
- **事实依据**：核心主张是否有具体事实支撑，数字和百分比是否明确

### 2. 品牌实体一致性（geo_brand_consistency）
- **实体可识别性**：从文本中能否清晰提取出公司是谁、做什么的
- **产品定义清晰度**：产品/服务描述是否具体到让 AI 准确分类，避免"我们的产品"等指代不明
- **能力边界明确**：能力描述是否有明确边界和具体场景支撑，而非"全方位""一站式"等模糊表述"""


# ====================================================================
# 模块3: 通用法律禁语规则（绝对化用语 + 擦边球 + 扩展绝对化）
# ====================================================================

LEGAL_RULES_PROMPT = """## 法律合规审核指引

### 审核方向（规则引擎已处理关键词匹配，你应关注语义层面）

**1. 绝对化用语**
识别暗示排他性、最高级、唯一性的表述。注意两类：
- 明文绝对化：直接使用"最""第一""唯一"等词
- 擦边球绝对化：使用"靠前""名列前茅""遥遥领先""独占鳌头"等文学化变体规避审查，语义上仍暗示排他性
- 无边界绝对化：以"全"字开头+名词/动词（如"全链条""全方位"），暗示覆盖完整无遗漏但无法验证

**2. 对比性表述**
"更好""更优""领先"等表述需有权威认证支撑；"性价比高"需有具体数据支撑。
无依据的对比性表述属于夸大宣传。

**3. 数据与来源**
引用调查数据、研究结论时需注明来源机构、样本量、时间范围。
模糊引用如"据说""据悉""众所周知"缺乏事实支撑。

**4. 承诺性表述**
"确保""保证"等需有限定条件；"永久有效""终身保障"需明确具体条件。
无条件承诺性表述具有误导风险。

**5. 行业合规**
- 金融：不得承诺收益、暗示无风险，必须标注风险提示
- 医疗：不得暗示治疗功能，保健品须标注"不能代替药物"
- 其他行业：关注行业特有的合规红线

**6. 联系方式泄露**
正文中明文出现手机号、微信号、邮箱等联系方式。"""


# ====================================================================
# 模块4: 深度语义审核要求（已合并到 BASE_SYSTEM_PROMPT，保留空壳用于向后兼容）
# ====================================================================

DEEP_SEMANTIC_PROMPT = ""  # 已合并到 BASE_SYSTEM_PROMPT，此模块不再单独使用


# ====================================================================
# 模块5: 行业专属规则（动态注入）
# ====================================================================

INDUSTRY_RULES_FINANCE = """## 金融行业专项规则
### 金融行业禁用词
- 保本、零风险、无风险、必赚、稳赚不赔、最高收益、100%收益、无损
- 预期年化收益率（未标注风险提示）、理财产品无风险、保证收益、承诺回报
- 金融产品宣传必须标注风险提示，不得承诺收益、不得暗示无风险
- 资管新规禁止承诺保本保收益

### 金融合规要点
- 理财产品必须标注"理财非存款、产品有风险、投资需谨慎"
- 基金产品必须标注"过往业绩不预示未来表现"
- 保险产品不得使用"存款""储蓄"等误导性表述
- 不得使用"秒杀""抢购"等饥饿营销用语描述金融产品"""

INDUSTRY_RULES_MEDICAL = """## 医疗行业专项规则
### 医疗行业禁用词
- 治疗、疗效、根治、治愈、药用、处方、神药、特效
- 疾病名称+改善/缓解（需有医疗资质）
- 专家推荐、医生建议（未经授权）
- 包治百病、药到病除、立竿见影

### 医疗合规要点
- 不得暗示或声称具有疾病治疗功能（普通食品/保健品）
- 保健品必须标注"本品不能代替药物"
- 医疗广告需标注审查证明文号
- 不得使用患者名义或形象作证明
- 不得含有功效的断言或保证"""


# ====================================================================
# 模块6: 输出格式 Prompt
# ====================================================================

OUTPUT_FORMAT_PROMPT = """## 输出格式
输出 JSON，格式如下:
{
  "summary": "整体评估，一句话概括核心判断",
  "issues": [
    {
      "type": "exaggeration",
      "severity": "critical",
      "title": "问题简述（概括问题本质）",
      "snippet": "包含违规表述的完整句子",
      "reason": "问题原因分析，从语义层面解释（80字以内）",
      "suggestion": "具体修改方向或示例（80字以内）",
      "confidence": 0.95
    }
  ]
}

要求：
- snippet 提取包含违规表述的完整句子，不能只输出单个词
- title 概括问题本质，不要只写"出现禁用词「XX」"
- reason 说明为什么有问题、会给用户带来什么误导
- suggestion 给出可操作的修改方向
- 语境合理的用词不应判定为违规
- 没有发现问题时 issues 为空数组，summary 说明审核通过
- 只输出 JSON，不要额外解释或 Markdown 格式"""


# ====================================================================
# Prompt 组合配置（Profile → 模块组合）
# ====================================================================

# 模块名 → 默认内容的映射（YAML 不存在时回退使用）
_MODULE_DEFAULTS: Dict[str, str] = {
    "base_system": BASE_SYSTEM_PROMPT,
    "geo_specific": GEO_SPECIFIC_PROMPT,
    "legal_rules": LEGAL_RULES_PROMPT,
    "deep_semantic": DEEP_SEMANTIC_PROMPT,
    "industry_finance": INDUSTRY_RULES_FINANCE,
    "industry_medical": INDUSTRY_RULES_MEDICAL,
    "output_format": OUTPUT_FORMAT_PROMPT,
}

# 每个 profile 定义需要组合哪些 prompt 模块（使用模块名，便于 YAML 覆盖）
# 注：deep_semantic 已合并到 base_system，不再单独使用
_PROMPT_PROFILE_DEFAULTS: Dict[str, list] = {
    "general": [
        "base_system", "geo_specific", "legal_rules", "output_format",
    ],
    "finance": [
        "base_system", "geo_specific", "legal_rules",
        "industry_finance", "output_format",
    ],
    "medical": [
        "base_system", "geo_specific", "legal_rules",
        "industry_medical", "output_format",
    ],
    "enterprise": [
        "base_system", "geo_specific", "legal_rules", "output_format",
    ],
    "news": [
        "base_system", "geo_specific", "legal_rules", "output_format",
    ],
    "technology": [
        "base_system", "geo_specific", "legal_rules", "output_format",
    ],
}

# 向后兼容：保留旧的 PROMPT_PROFILES（使用实际字符串，不推荐直接使用）
PROMPT_PROFILES: Dict[str, list] = {
    profile: [_MODULE_DEFAULTS.get(name, "") for name in modules]
    for profile, modules in _PROMPT_PROFILE_DEFAULTS.items()
}


# ====================================================================
# 兼容性：保留 SYSTEM_PROMPT（向后兼容，使用 general profile）
# ====================================================================

def _build_system_prompt(profile: str = "general") -> str:
    """根据 profile 动态组合 system prompt（支持 YAML 热加载）."""
    loader = get_prompt_loader()

    # 从 YAML 获取 profile 的模块列表，回退到默认
    module_names = loader.get_profile_modules(profile, _PROMPT_PROFILE_DEFAULTS)

    # 逐个获取模块内容（YAML 优先，回退到硬编码默认值）
    parts = []
    for name in module_names:
        default_content = _MODULE_DEFAULTS.get(name, "")
        content = loader.get_module(name, default_content)
        if content:
            parts.append(content)

    return "\n\n".join(parts)


# 向后兼容：默认 SYSTEM_PROMPT = general profile
SYSTEM_PROMPT = _build_system_prompt("general")


# ====================================================================
# User Prompt 模板
# ====================================================================

REVIEW_TEMPLATE = """## 待审正文
---
{content}
---

## 提报表信息（审核基准，请逐条核对）
### 任务背景
- 任务名称: {task_name}
- 公司/品牌: {company_name}
- 产品/服务: {product_list}
- 核心主题: {core_topic}

### 核心意图与关键信息
{key_points_list}

### 允许出现的事实/数据边界
{allowed_facts_list}

### 标准表述参考
{reference_copy_list}

### 绝对禁用表述
{forbidden_list}

### 敏感内容边界
{must_not_mention_list}

### 竞品敏感词
{competitor_list}

## 官网信息（事实核对基准）
{website_summary}

---
请基于以上信息对正文进行审核，输出 JSON 格式的审核结果。"""


WEBSITE_SUMMARY_TEMPLATE = """从官网爬取的内容摘要:
{website_text}"""


# ====================================================================
# Prompt 构建函数（支持动态注入）
# ====================================================================

def build_system_prompt(
    prompt_profile: str = "general",
    industry_context: str = "",
    custom_prompt: Optional[str] = None,
) -> str:
    """根据 profile 动态构建 system prompt.

    Args:
        prompt_profile: prompt 模板 profile（general/finance/medical/enterprise/news/technology）
        industry_context: 行业知识库附加上下文（追加到 prompt 末尾）
        custom_prompt: 完全自定义的 system prompt（优先级最高，忽略 profile）

    Returns:
        组合后的 system prompt 字符串
    """
    if custom_prompt:
        system_prompt = custom_prompt
    else:
        system_prompt = _build_system_prompt(prompt_profile)

    if industry_context:
        system_prompt = f"{system_prompt}\n\n## 行业上下文\n{industry_context}"

    return system_prompt


def build_review_messages(
    content: str,
    submission_data: dict,
    website_text: str = "",
    custom_system_prompt: str = None,
    industry_context: str = "",
    prompt_profile: str = "general",
) -> list:
    """构建审核用的 messages 列表（支持动态 prompt 注入 + YAML 热加载）.

    Args:
        content: 待审正文
        submission_data: 提报表数据字典
        website_text: 官网爬取的文本内容
        custom_system_prompt: 自定义 system prompt（可选，优先级最高）
        industry_context: 行业知识库上下文（可选）
        prompt_profile: prompt 模板 profile（general/finance/medical/...）

    Returns:
        OpenAI 格式的 messages 列表
    """
    # 动态构建 system prompt
    system_prompt = build_system_prompt(
        prompt_profile=prompt_profile,
        industry_context=industry_context,
        custom_prompt=custom_system_prompt,
    )

    # 从 YAML 加载 review 模板（回退到硬编码 REVIEW_TEMPLATE）
    loader = get_prompt_loader()
    review_template = loader.get_template("review", REVIEW_TEMPLATE)
    website_summary_template = loader.get_template("website_summary", WEBSITE_SUMMARY_TEMPLATE)

    # 格式化各类列表
    product_list = "\n".join(f"- {p}" for p in submission_data.get("product_or_service", [])) or "（无）"
    key_points_list = "\n".join(f"- {kp}" for kp in submission_data.get("key_points", [])) or "（无）"
    allowed_facts_list = "\n".join(f"- {f}" for f in submission_data.get("allowed_facts", [])) or "（无）"
    reference_copy_list = "\n".join(f"- {r}" for r in submission_data.get("reference_copy", [])) or "（无）"
    forbidden_list = "\n".join(f"- {f}" for f in submission_data.get("forbidden_claims", [])) or "（无）"
    must_not_mention_list = "\n".join(f"- {m}" for m in submission_data.get("must_not_mention", [])) or "（无）"
    competitor_list = "\n".join(f"- {c}" for c in submission_data.get("competitor_names", [])) or "（无）"

    # 智能截断官网文本
    website_summary = ""
    if website_text:
        max_website_chars = 6000
        if len(website_text) > max_website_chars:
            website_text = website_text[:max_website_chars] + f"\n...（已截断，原内容 {len(website_text)} 字符）"
        website_summary = website_summary_template.format(website_text=website_text)
    else:
        website_summary = "（官网信息爬取失败，仅基于提报表审核；如涉及事实性描述，请人工重点核对）"

    # Token 预算检查和智能截断
    content_tokens = estimate_tokens(content)
    template_tokens = estimate_tokens(review_template.format(
        content="", task_name="", company_name="", product_list="", core_topic="",
        key_points_list="", allowed_facts_list="", reference_copy_list="",
        forbidden_list="", must_not_mention_list="", competitor_list="",
        website_summary="",
    ))
    system_tokens = estimate_tokens(system_prompt)
    website_tokens = estimate_tokens(website_summary)

    total_estimated = content_tokens + template_tokens + system_tokens + website_tokens
    budget = TOKEN_BUDGET - 2000  # 留 2000 token 余量

    if total_estimated > budget:
        # 优先截断官网文本
        if website_tokens > 1000 and total_estimated - budget > 500:
            max_ws_chars = 2000
            if len(website_text) > max_ws_chars:
                website_text = website_text[:max_ws_chars] + f"\n...（已截断，原内容 {len(website_text)} 字符）"
            website_summary = website_summary_template.format(website_text=website_text)
            website_tokens = estimate_tokens(website_summary)
            total_estimated = content_tokens + template_tokens + system_tokens + website_tokens
            logger.warning(f"官网文本已截断至 {max_ws_chars} 字符以控制 token 数")

        # 如果仍然超预算，截断正文尾部
        if total_estimated > budget:
            overhead_tokens = template_tokens + system_tokens + website_tokens
            available_for_content = budget - overhead_tokens
            max_content_chars = int(available_for_content / 1.5)
            if len(content) > max_content_chars:
                content = content[:max_content_chars] + "\n\n[注意：正文因长度超出模型上下文限制已被截断]"
                logger.warning(f"正文已截断至 {max_content_chars} 字符")

    user_prompt = review_template.format(
        content=content,
        task_name=submission_data.get("task_name", "（未知）"),
        company_name=submission_data.get("company_name", "（未知）"),
        product_list=product_list,
        core_topic=submission_data.get("core_topic", "（未知）"),
        key_points_list=key_points_list,
        allowed_facts_list=allowed_facts_list,
        reference_copy_list=reference_copy_list,
        forbidden_list=forbidden_list,
        must_not_mention_list=must_not_mention_list,
        competitor_list=competitor_list,
        website_summary=website_summary,
    )

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]