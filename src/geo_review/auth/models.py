"""认证数据模型 — 用户与权限."""

from typing import Optional

from geo_review.utils.time import now as beijing_now
from uuid import uuid4

from sqlalchemy import Column, DateTime, String, Boolean

from geo_review.history.models import Base


class User(Base):
    """用户表."""
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    username = Column(String(100), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=True, index=True)
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=True)
    role = Column(String(50), nullable=False, default="user")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=beijing_now)
    updated_at = Column(DateTime, default=beijing_now, onupdate=beijing_now)
    last_login_at = Column(DateTime, nullable=True)
