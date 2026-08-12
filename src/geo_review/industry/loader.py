"""领域知识库加载器 — 支持 YAML/JSON 格式."""

import json
import os
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from geo_review.industry.models import IndustryKnowledgeBase


class IndustryLoader:
    """行业知识库加载器.

    支持:
        - 从 YAML/JSON 文件加载单个知识库
        - 从目录批量加载
        - 按行业标识检索
    """

    _cache: Dict[str, IndustryKnowledgeBase] = {}

    @classmethod
    def load(cls, path: str) -> IndustryKnowledgeBase:
        """从文件加载知识库."""
        path_obj = Path(path)
        if not path_obj.exists():
            raise FileNotFoundError(f"知识库文件不存在: {path}")

        with open(path_obj, "r", encoding="utf-8") as f:
            if path_obj.suffix in (".yaml", ".yml"):
                data = yaml.safe_load(f)
            elif path_obj.suffix == ".json":
                data = json.load(f)
            else:
                raise ValueError(f"不支持的文件格式: {path_obj.suffix}")

        kb = IndustryKnowledgeBase(**data)
        cls._cache[kb.industry] = kb
        return kb

    @classmethod
    def load_directory(cls, directory: str) -> Dict[str, IndustryKnowledgeBase]:
        """从目录批量加载所有知识库."""
        dir_path = Path(directory)
        if not dir_path.exists():
            return {}

        results = {}
        for file_path in dir_path.iterdir():
            if file_path.suffix in (".yaml", ".yml", ".json"):
                try:
                    kb = cls.load(str(file_path))
                    results[kb.industry] = kb
                except Exception as e:
                    print(f"[WARN] 加载知识库失败 {file_path}: {e}")

        return results

    @classmethod
    def get(cls, industry: str) -> Optional[IndustryKnowledgeBase]:
        """获取已加载的知识库."""
        return cls._cache.get(industry)

    @classmethod
    def list_industries(cls) -> List[str]:
        """列出所有已加载的行业."""
        return list(cls._cache.keys())

    @classmethod
    def clear_cache(cls) -> None:
        """清空缓存."""
        cls._cache.clear()

    @classmethod
    def auto_load(cls, base_dir: Optional[str] = None) -> Dict[str, IndustryKnowledgeBase]:
        """自动加载项目目录下的行业知识库.

        查找路径:
            1. 指定的 base_dir
            2. 项目根目录下的 industry/ 文件夹
            3. src/geo_review/industry/kb/ 文件夹
        """
        if base_dir:
            return cls.load_directory(base_dir)

        # 尝试查找默认路径
        candidates = [
            Path(os.getcwd()) / "industry",
            Path(os.getcwd()) / "src" / "geo_review" / "industry" / "kb",
            Path(__file__).parent / "kb",
        ]

        for candidate in candidates:
            if candidate.exists():
                return cls.load_directory(str(candidate))

        return {}