"""时间工具 — 统一使用北京时间."""

from datetime import datetime, timedelta, timezone


def now() -> datetime:
    """获取当前北京时间."""
    return datetime.now(timezone(timedelta(hours=8)))


def now_str(fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """获取当前北京时间字符串."""
    return now().strftime(fmt)


def now_iso() -> str:
    """获取当前北京时间 ISO 格式字符串."""
    return now().isoformat()
