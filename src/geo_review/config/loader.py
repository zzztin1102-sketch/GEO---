"""配置加载器 — 从 YAML 文件和环境变量加载配置（优化版）.

优化点：
    1. ✅ 支持 ${ENV_VAR} 语法：配置文件中写 ${LLM_API_KEY}，自动从环境变量读取
    2. ✅ 优先从环境变量读取敏感信息（api_key、secret_key、password）
"""

import os
import re
from typing import Any, Optional

import yaml

from geo_review.config.models import AppConfig, LLMConfig, AuthConfig

# 尝试加载 .env 文件（如果 python-dotenv 可用）
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# 环境变量替换正则：匹配 ${VAR_NAME} 或 ${VAR_NAME:default_value}
_ENV_VAR_PATTERN = re.compile(r'\$\{([A-Z_][A-Z0-9_]*)(?::([^}]*))?\}')

# 敏感字段的环境变量映射
_SENSITIVE_ENV_MAP = {
    "llm.api_key": "LLM_API_KEY",
    "auth.secret_key": "AUTH_SECRET_KEY",
    "auth.default_admin_password": "AUTH_ADMIN_PASSWORD",
    "database.url": "DATABASE_URL",
}

# 类型转换键
_BOOL_KEYS = {
    "llm.enable_cache",
    "crawler.enabled", "crawler.use_playwright",
    "rule_engine.enable_length_check", "rule_engine.enable_forbidden_keywords",
    "rule_engine.enable_required_keywords", "rule_engine.enable_competitor_check",
    "rule_engine.enable_claim_check",
    "database.echo",
    "auth.enabled", "auth.allow_registration",
    "fact_check.enabled",
    "rate_limit.enabled",
}
_INT_KEYS = {
    "llm.max_tokens", "llm.timeout", "llm.cache_ttl", "llm.max_issues",
    "crawler.max_pages", "crawler.timeout", "crawler.cache_ttl",
    "rule_engine.min_content_length", "rule_engine.max_content_length",
    "batch.max_items", "batch.max_concurrent", "batch.task_expiry_hours",
    "api.port", "api.workers",
    "auth.token_expire_minutes",
    "fact_check.max_claims", "fact_check.max_search_results", "fact_check.search_timeout",
}
_FLOAT_KEYS = {
    "llm.temperature", "llm.confidence_threshold",
}


def load_config(path: Optional[str] = None) -> AppConfig:
    """从 YAML 文件加载配置.

    支持的特性：
        1. ${ENV_VAR} 语法：自动替换为环境变量值
        2. 敏感字段优先从环境变量读取
        3. 类型自动转换
        4. 安全补齐：缺失的敏感字段自动生成临时值

    Args:
        path: 配置文件路径。若为 None，则按优先级查找：
              ① 环境变量 GEO_REVIEW_CONFIG
              ② 当前工作目录下的 config.yaml
              ③ 调用方文件所在项目根目录的 config.yaml
              都找不到则返回全默认配置。
    """
    resolved_path = _resolve_config_path(path)
    if resolved_path is None:
        # 找不到配置文件，使用全默认配置
        config = AppConfig()
        _apply_security_defaults(config)
        return config

    with open(resolved_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    # ✅ 新增：递归替换 ${ENV_VAR}
    data = _replace_env_vars(data=raw)

    # ✅ 新增：敏感字段优先从环境变量读取
    for config_path, env_name in _SENSITIVE_ENV_MAP.items():
        env_value = os.environ.get(env_name)
        if env_value:
            _set_nested(data, config_path, env_value)

    # 类型转换
    data = _convert_types(data)

    config = AppConfig(**data)

    # 安全补齐
    _apply_security_defaults(config)

    return config


def _resolve_config_path(path: Optional[str]) -> Optional[str]:
    """按优先级解析配置文件路径.

    优先级：
        1. 显式传入的 path（若存在则直接返回，若不存在也尊重，返回原值让 open 报错）
        2. 环境变量 GEO_REVIEW_CONFIG
        3. 当前工作目录下的 config.yaml
        4. 本文件向上回溯找到的项目根 config.yaml
           （loader.py 位于 <root>/src/geo_review/config/loader.py，向上 4 层即为根）
    """
    # 1. 显式传入
    if path is not None:
        return path

    # 2. 环境变量
    env_path = os.environ.get("GEO_REVIEW_CONFIG")
    if env_path and os.path.exists(env_path):
        return env_path

    # 3. 当前工作目录
    cwd_path = os.path.join(os.getcwd(), "config.yaml")
    if os.path.exists(cwd_path):
        return cwd_path

    # 4. 项目根目录（loader.py 向上 4 层）
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(os.path.dirname(here)))
    root_path = os.path.join(root, "config.yaml")
    if os.path.exists(root_path):
        return root_path

    return None


def _replace_env_vars(data: Any) -> Any:
    """递归替换字典中的 ${ENV_VAR} 或 ${ENV_VAR:default} 为环境变量值.

    支持的语法：
        ${VAR_NAME}          — 替换为环境变量值，未设置则返回 None（触发默认值/安全补齐）
        ${VAR_NAME:default}  — 替换为环境变量值，未设置则使用 default
    """
    if isinstance(data, dict):
        return {k: _replace_env_vars(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [_replace_env_vars(v) for v in data]
    elif isinstance(data, str):
        def _replace_match(match):
            var_name = match.group(1)
            default_value = match.group(2)  # 可能为 None
            env_value = os.environ.get(var_name)
            if env_value is not None:
                return env_value
            # 如果有默认值，使用默认值
            if default_value is not None:
                return default_value
            # 没有默认值，返回 None（让 Pydantic 使用模型默认值或触发安全补齐）
            return match.group(0)

        result = _ENV_VAR_PATTERN.sub(_replace_match, data)
        # 如果替换后仍然包含 ${...} 且没有默认值，说明环境变量未设置
        if '${' in result and '}' in result:
            if _ENV_VAR_PATTERN.match(result):
                return None
        return result
    else:
        return data


def _convert_types(data: dict) -> dict:
    """转换配置值的类型."""
    def _convert_recursive(d: dict, prefix: str = ""):
        for key, value in list(d.items()):
            config_path = f"{prefix}.{key}" if prefix else key

            if isinstance(value, dict):
                _convert_recursive(value, config_path)
            elif isinstance(value, str):
                if config_path in _BOOL_KEYS:
                    d[key] = value.lower() in ("true", "1", "yes", "on")
                elif config_path in _INT_KEYS:
                    d[key] = int(value)
                elif config_path in _FLOAT_KEYS:
                    d[key] = float(value)

    _convert_recursive(data)
    return data


def _set_nested(data: dict, path: str, value: object):
    """在嵌套字典中设置值."""
    keys = path.split(".")
    current = data
    for key in keys[:-1]:
        if key not in current or not isinstance(current[key], dict):
            current[key] = {}
        current = current[key]
    current[keys[-1]] = value


def _apply_security_defaults(config: AppConfig):
    """应用安全默认值."""
    # LLM API Key 安全补齐
    if not config.llm.api_key:
        import secrets
        # 尝试从环境变量读取
        env_key = os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if env_key:
            config.llm.api_key = env_key
        else:
            # 生成临时密钥并提示
            temp_key = f"sk-temp-{secrets.token_hex(16)}"
            config.llm.api_key = temp_key
            import logging
            logging.getLogger(__name__).warning(
                "⚠️ LLM_API_KEY 未设置！已生成临时密钥，请在环境变量中设置 LLM_API_KEY"
            )

    # Auth Secret Key 安全补齐
    if config.auth.enabled and not config.auth.secret_key:
        import secrets
        env_key = os.environ.get("AUTH_SECRET_KEY")
        if env_key:
            config.auth.secret_key = env_key
        else:
            config.auth.secret_key = secrets.token_hex(32)
            import logging
            logging.getLogger(__name__).warning(
                "⚠️ AUTH_SECRET_KEY 未设置！已生成临时密钥，请在环境变量中设置 AUTH_SECRET_KEY"
            )

    # Auth Admin Password 安全补齐
    if config.auth.enabled and not config.auth.default_admin_password:
        import secrets
        import string
        env_pass = os.environ.get("AUTH_ADMIN_PASSWORD")
        if env_pass:
            config.auth.default_admin_password = env_pass
        else:
            chars = string.ascii_letters + string.digits + "!@#$%"
            temp_pass = ''.join(secrets.choice(chars) for _ in range(16))
            config.auth.default_admin_password = temp_pass
            import logging
            logging.getLogger(__name__).warning(
                f"⚠️ AUTH_ADMIN_PASSWORD 未设置！已生成临时密码: {temp_pass}"
            )


def save_config(config: AppConfig, path: str):
    """保存配置到 YAML 文件.

    ✅ 优化：保存时不写入敏感信息（api_key、secret_key、password）
    """
    data = config.model_dump()

    # 安全：清除敏感字段（保存到文件时不包含）
    if "llm" in data and "api_key" in data["llm"]:
        data["llm"]["api_key"] = "${LLM_API_KEY}"
    if "auth" in data:
        if "secret_key" in data["auth"]:
            data["auth"]["secret_key"] = "${AUTH_SECRET_KEY}"
        if "default_admin_password" in data["auth"]:
            data["auth"]["default_admin_password"] = "${AUTH_ADMIN_PASSWORD}"

    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
