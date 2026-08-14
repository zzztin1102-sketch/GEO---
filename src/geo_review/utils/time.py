"""时间工具 — 统一使用北京时间."""

from datetime import datetime, timedelta, timezone


def now() -> datetime:
    """获取当前北京时间."""
    return datetime.now(timezone(timedelta(hours=8)))
