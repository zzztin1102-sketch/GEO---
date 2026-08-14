"""FastAPI dependency functions — retrieve services from request.app.state."""

from typing import Optional

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from geo_review.auth.schemas import UserResponse
from geo_review.utils.time import now as beijing_now

# --- Security scheme (module-level, stateless) ---

bearer_scheme = HTTPBearer(auto_error=False)


# --- Service getters ---

def get_config(request: Request):
    return request.app.state._config


def get_agent(request: Request):
    return request.app.state._agent


def get_batch_service(request: Request):
    return request.app.state._batch_service


def get_history_service(request: Request):
    return request.app.state._history_service


def get_auth_service(request: Request):
    return request.app.state._auth_service


def get_security(request: Request):
    return request.app.state._security


def get_workflow_service(request: Request):
    return request.app.state._workflow


def get_rule_set(request: Request):
    return request.app.state._rule_set


def get_industry_kbs(request: Request):
    return request.app.state._industry_kbs


def get_async_session(request: Request):
    return request.app.state._async_session


# --- Auth dependencies ---

async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
):
    """获取当前认证用户."""
    config = request.app.state._config
    auth_service = request.app.state._auth_service

    if not config.auth.enabled:
        user = await auth_service.get_by_username(config.auth.default_admin_username)
        if user and user.is_active:
            return user
        return UserResponse(
            id="admin",
            username=config.auth.default_admin_username,
            email="admin@geo-review.local",
            full_name="系统管理员",
            role="admin",
            is_active=True,
            created_at=beijing_now(),
        )

    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=401,
            detail="未提供认证令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user_id = auth_service.verify_token(credentials.credentials)
    if not user_id:
        raise HTTPException(
            status_code=401,
            detail="令牌无效或已过期",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = await auth_service.get_by_id(user_id)
    if not user or not user.is_active:
        raise HTTPException(
            status_code=401,
            detail="用户不存在或已被禁用",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def get_current_user_optional(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
):
    """可选认证 — 认证启用时强制验证，关闭时返回 None."""
    config = request.app.state._config
    if not config.auth.enabled:
        return None
    return await get_current_user(request, credentials)
