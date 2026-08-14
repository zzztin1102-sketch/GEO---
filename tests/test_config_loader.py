"""配置加载器测试 — 环境变量替换、${VAR:default} 语法."""

import os
import pytest
import tempfile
import yaml

from geo_review.config.loader import _replace_env_vars, _ENV_VAR_PATTERN


class TestEnvVarReplacement:
    """${ENV_VAR} 和 ${ENV_VAR:default} 语法测试."""

    def test_simple_env_var(self, monkeypatch):
        """${VAR} 简单替换."""
        monkeypatch.setenv("TEST_VAR_SIMPLE", "hello")
        result = _replace_env_vars("${TEST_VAR_SIMPLE}")
        assert result == "hello"

    def test_env_var_not_set_returns_none(self, monkeypatch):
        """${VAR} 未设置时返回 None."""
        monkeypatch.delenv("TEST_VAR_NOT_SET", raising=False)
        result = _replace_env_vars("${TEST_VAR_NOT_SET}")
        assert result is None

    def test_env_var_with_default(self, monkeypatch):
        """${VAR:default} 环境变量未设置时使用默认值."""
        monkeypatch.delenv("TEST_VAR_DEFAULT", raising=False)
        result = _replace_env_vars("${TEST_VAR_DEFAULT:fallback_value}")
        assert result == "fallback_value"

    def test_env_var_with_default_overridden(self, monkeypatch):
        """${VAR:default} 环境变量设置时优先使用环境变量."""
        monkeypatch.setenv("TEST_VAR_DEFAULT", "env_value")
        result = _replace_env_vars("${TEST_VAR_DEFAULT:fallback_value}")
        assert result == "env_value"

    def test_env_var_in_nested_dict(self, monkeypatch):
        """嵌套字典中的环境变量替换."""
        monkeypatch.setenv("TEST_NESTED_VAR", "nested_value")
        data = {
            "level1": {
                "level2": "${TEST_NESTED_VAR}",
                "other": "normal",
            },
        }
        result = _replace_env_vars(data)
        assert result["level1"]["level2"] == "nested_value"
        assert result["level1"]["other"] == "normal"

    def test_env_var_in_list(self, monkeypatch):
        """列表中的环境变量替换."""
        monkeypatch.setenv("TEST_LIST_VAR", "list_value")
        data = ["${TEST_LIST_VAR}", "normal"]
        result = _replace_env_vars(data)
        assert result[0] == "list_value"
        assert result[1] == "normal"

    def test_database_url_with_default(self, monkeypatch):
        """数据库 URL 带默认值（SQLite 回退）."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        result = _replace_env_vars("${DATABASE_URL:sqlite+aiosqlite:///./test.db}")
        assert result == "sqlite+aiosqlite:///./test.db"

    def test_database_url_with_env(self, monkeypatch):
        """数据库 URL 从环境变量读取（PostgreSQL）."""
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/db")
        result = _replace_env_vars("${DATABASE_URL:sqlite+aiosqlite:///./test.db}")
        assert result == "postgresql+asyncpg://user:pass@localhost:5432/db"

    def test_no_env_vars_in_string(self):
        """不含环境变量的字符串保持不变."""
        result = _replace_env_vars("普通字符串")
        assert result == "普通字符串"

    def test_non_string_types_preserved(self):
        """非字符串类型保持不变."""
        data = {"int": 42, "bool": True, "float": 3.14, "none": None}
        result = _replace_env_vars(data)
        assert result["int"] == 42
        assert result["bool"] is True
        assert result["float"] == 3.14
        assert result["none"] is None


class TestConfigLoad:
    """配置加载集成测试."""

    def test_load_config_from_file(self, tmp_path, monkeypatch):
        """从临时 YAML 文件加载配置.

        注意：必须使用 _SENSITIVE_ENV_MAP 中定义的真实环境变量名
        （LLM_API_KEY / AUTH_SECRET_KEY / AUTH_ADMIN_PASSWORD），
        否则 _SENSITIVE_ENV_MAP 会用 .env 中的值覆盖测试值。
        """
        # 使用真实环境变量名，monkeypatch 会覆盖 .env 中已加载的值
        monkeypatch.setenv("LLM_API_KEY", "sk-test-key")
        monkeypatch.setenv("AUTH_SECRET_KEY", "test-secret-key-with-enough-length-for-testing")
        monkeypatch.setenv("AUTH_ADMIN_PASSWORD", "TestPassword@12345")

        config_data = {
            "llm": {
                "provider": "openai",
                "api_key": "${LLM_API_KEY}",
                "model": "test-model",
            },
            "auth": {
                "enabled": True,
                "secret_key": "${AUTH_SECRET_KEY}",
                "default_admin_password": "${AUTH_ADMIN_PASSWORD}",
            },
            "database": {
                "url": "${DATABASE_URL:sqlite+aiosqlite:///./test.db}",
            },
        }
        config_file = tmp_path / "test_config.yaml"
        config_file.write_text(yaml.dump(config_data), encoding="utf-8")

        from geo_review.config import load_config
        config = load_config(str(config_file))

        assert config.llm.api_key == "sk-test-key"
        assert config.llm.model == "test-model"
        assert config.auth.secret_key == "test-secret-key-with-enough-length-for-testing"
        assert config.auth.default_admin_password == "TestPassword@12345"
        assert "sqlite" in config.database.url

    def test_load_config_rate_limit(self, tmp_path, monkeypatch):
        """加载限流配置."""
        monkeypatch.setenv("LLM_API_KEY", "sk-test")
        monkeypatch.setenv("AUTH_SECRET_KEY", "x" * 40)
        monkeypatch.setenv("AUTH_ADMIN_PASSWORD", "TestPwd@12345")

        config_data = {
            "llm": {"api_key": "${LLM_API_KEY}"},
            "auth": {"secret_key": "${AUTH_SECRET_KEY}", "default_admin_password": "${AUTH_ADMIN_PASSWORD}"},
            "rate_limit": {
                "enabled": True,
                "review_limit": "5/minute",
                "batch_limit": "1/minute",
            },
        }
        config_file = tmp_path / "test_rl_config.yaml"
        config_file.write_text(yaml.dump(config_data), encoding="utf-8")

        from geo_review.config import load_config
        config = load_config(str(config_file))

        assert config.rate_limit.enabled is True
        assert config.rate_limit.review_limit == "5/minute"
        assert config.rate_limit.batch_limit == "1/minute"
