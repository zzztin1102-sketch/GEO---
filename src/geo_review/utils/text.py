"""文本处理工具函数."""

import re


# 中文分号优先，兼容英文逗号/分号及换行符
_SPLIT_PATTERN = re.compile(r"[；;]|\n")


def split_list_field(value: str | None) -> list[str]:
    """将单元格内的列表字符串拆分为数组，自动清洗空值.

    规则:
        - 优先以中文分号「；」分隔
        - 兼容英文分号「;」及换行符
        - 自动 strip 并过滤空字符串
    """
    if not value or not isinstance(value, str):
        return []
    parts = _SPLIT_PATTERN.split(value)
    result = [p.strip() for p in parts if p.strip()]
    return result


def clean_cell_value(value) -> str | None:
    """清洗单元格值: 去除前后空白，将空字符串转为 None."""
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned if cleaned else None
    # 数值型等其他类型转字符串
    return str(value).strip() or None


def is_example_row(row_data: dict) -> bool:
    """简单启发式判断是否为示例行（含「某某」「example」等占位符）.

    使用整词匹配减少误报（如域名中的 'test' 不会触发）.
    """
    import re

    text = " ".join(str(v) for v in row_data.values() if v).lower()
    # 要求 marker 前后为非字母数字字符或边界，避免子串误匹配
    patterns = [
        r"\b某某\b",
        r"\bexample\b",
        r"\b示例\b",
        r"\bxx\b",
        r"\bxxx\b",
        r"\btest\b",
        r"\b测试\b",
    ]
    return any(re.search(p, text) for p in patterns)
