"""Prompt 模板加载器 — 从 YAML 文件加载 prompt，支持热加载.

特性：
    1. 从 prompt_templates/prompts.yaml 加载 prompt 模板
    2. 每 60 秒检查文件修改时间，自动热加载
    3. 如果 YAML 文件不存在，回退到 prompts.py 中的硬编码默认值
    4. 提供 get_module() / get_profile_modules() / get_template() 接口
"""

import logging
import os
import time
from typing import Dict, List, Optional
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# 默认热加载检查间隔（秒）
_RELOAD_INTERVAL = 60

# 查找 prompt_templates 目录的路径列表
_SEARCH_PATHS = [
    # 1. 环境变量指定
    lambda: os.environ.get("PROMPT_TEMPLATES_DIR"),
    # 2. 当前工作目录
    lambda: os.path.join(os.getcwd(), "prompt_templates", "prompts.yaml"),
    # 3. 项目根目录（相对于本文件向上回溯）
    lambda: str(Path(__file__).parent.parent.parent.parent.parent / "prompt_templates" / "prompts.yaml"),
]


class PromptLoader:
    """Prompt 模板加载器，支持热加载."""

    def __init__(self):
        self._yaml_path: Optional[str] = None
        self._data: Optional[dict] = None
        self._last_mtime: float = 0.0
        self._last_check: float = 0.0
        self._find_yaml()

    def _find_yaml(self) -> None:
        """查找 prompts.yaml 文件路径."""
        for path_fn in _SEARCH_PATHS:
            path = path_fn()
            if path and os.path.isfile(path):
                self._yaml_path = path
                logger.info(f"Prompt 模板文件: {path}")
                return
        logger.info("未找到 prompt_templates/prompts.yaml，使用内置默认 prompt")
        self._yaml_path = None

    def _maybe_reload(self) -> None:
        """检查是否需要重新加载（基于文件修改时间）."""
        now = time.time()
        if now - self._last_check < _RELOAD_INTERVAL:
            return
        self._last_check = now

        if not self._yaml_path:
            return

        try:
            mtime = os.path.getmtime(self._yaml_path)
            if mtime > self._last_mtime:
                self._load_yaml()
                self._last_mtime = mtime
                logger.info("Prompt 模板已热加载")
        except OSError:
            pass

    def _load_yaml(self) -> None:
        """从 YAML 文件加载 prompt 模板."""
        if not self._yaml_path:
            return
        try:
            with open(self._yaml_path, "r", encoding="utf-8") as f:
                self._data = yaml.safe_load(f)
            logger.info(f"已加载 prompt 模板: {len(self._data.get('modules', {}))} 个模块, "
                        f"{len(self._data.get('profiles', {}))} 个 profile")
        except Exception as e:
            logger.error(f"加载 prompt 模板失败: {e}")
            self._data = None

    def get_module(self, name: str, default: str = "") -> str:
        """获取 prompt 模块内容.

        Args:
            name: 模块名称（如 base_system, geo_specific）
            default: YAML 不存在时的回退默认值
        """
        self._maybe_reload()

        if self._data and "modules" in self._data:
            return self._data["modules"].get(name, default)

        return default

    def get_profile_modules(self, profile: str, defaults: Dict[str, List[str]]) -> List[str]:
        """获取 profile 对应的模块名列表.

        Args:
            profile: profile 名称
            defaults: YAML 不存在时的回退默认映射
        """
        self._maybe_reload()

        if self._data and "profiles" in self._data:
            modules = self._data["profiles"].get(profile)
            if modules:
                return modules

        return defaults.get(profile, defaults.get("general", []))

    def get_template(self, name: str, default: str = "") -> str:
        """获取 user prompt 模板.

        Args:
            name: 模板名称（如 review, website_summary）
            default: YAML 不存在时的回退默认值
        """
        self._maybe_reload()

        if self._data and "templates" in self._data:
            return self._data["templates"].get(name, default)

        return default


# 全局单例
_loader = PromptLoader()


def get_prompt_loader() -> PromptLoader:
    """获取全局 PromptLoader 实例."""
    return _loader
