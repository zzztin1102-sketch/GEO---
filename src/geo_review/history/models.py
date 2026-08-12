"""审核历史记录数据库模型 — 使用 SQLAlchemy ORM."""

from typing import Optional

from geo_review.utils.time import now as beijing_now
from uuid import uuid4

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, JSON, Boolean
from sqlalchemy.ext.asyncio import AsyncAttrs, AsyncSession, create_async_engine
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

Base = declarative_base()


class ReviewHistory(Base):
    """审核历史记录."""
    __tablename__ = "review_history"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    review_id = Column(String(36), unique=True, nullable=False, index=True)
    task_name = Column(String(255), nullable=True)
    company_name = Column(String(255), nullable=True)
    status = Column(String(50), nullable=False)
    verdict = Column(String(50), nullable=False)
    summary = Column(Text, nullable=True)
    total_issues = Column(Integer, default=0)
    critical_issues = Column(Integer, default=0)
    major_issues = Column(Integer, default=0)
    minor_issues = Column(Integer, default=0)
    info_issues = Column(Integer, default=0)
    # 完整内容存储
    content_text = Column(Text, nullable=True)
    content_preview = Column(Text, nullable=True)
    content_title = Column(String(500), nullable=True)
    content_source = Column(String(50), nullable=True)
    submission_data = Column(JSON, nullable=True)
    submission_source = Column(String(50), nullable=True)
    official_urls = Column(JSON, nullable=True)
    crawled_website_data = Column(JSON, nullable=True)
    # 结果存储
    result_data = Column(JSON, nullable=True)
    error_code = Column(String(100), nullable=True)
    error_message = Column(Text, nullable=True)
    reviewed_at = Column(DateTime, nullable=False)
    duration_ms = Column(Integer, default=0)
    submitted_by = Column(String(100), nullable=True)
    batch_id = Column(String(36), nullable=True, index=True)
    item_id = Column(String(100), nullable=True)
    is_deleted = Column(Boolean, default=False)
    created_at = Column(DateTime, default=beijing_now)
    updated_at = Column(DateTime, default=beijing_now, onupdate=beijing_now)

    issues = relationship("ReviewIssue", back_populates="history", cascade="all, delete-orphan")


class ReviewIssue(Base):
    """审核问题记录."""
    __tablename__ = "review_issue"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    history_id = Column(String(36), ForeignKey("review_history.id"), nullable=False, index=True)
    issue_id = Column(String(50), nullable=True)
    type = Column(String(50), nullable=False)
    severity = Column(String(20), nullable=False)
    title = Column(String(200), nullable=False)
    snippet = Column(Text, nullable=True)
    reason = Column(Text, nullable=True)
    suggestion = Column(Text, nullable=True)
    source = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=beijing_now)

    history = relationship("ReviewHistory", back_populates="issues")


async def init_database(db_url: str = "sqlite+aiosqlite:///./review_history.db"):
    """初始化数据库连接."""
    engine = create_async_engine(db_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, async_session
