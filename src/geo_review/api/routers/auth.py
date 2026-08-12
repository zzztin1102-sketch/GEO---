"""Authentication routes — register, login, profile."""

from fastapi import APIRouter, Depends, HTTPException, Request

from geo_review.auth import (
    ChangePasswordRequest,
    TokenResponse,
    UserLoginRequest,
    UserRegisterRequest,
    UserResponse,
    UserUpdateRequest,
)
from geo_review.middleware.rate_limit import limiter, LIMIT_AUTH

from .deps import get_current_user

router = APIRouter()


@router.post("/api/v1/auth/register", response_model=UserResponse, tags=["认证"])
@limiter.limit(LIMIT_AUTH)
async def register(req: UserRegisterRequest, request: Request):
    """用户注册."""
    config = request.app.state._config
    auth_service = request.app.state._auth_service

    if not config.auth.allow_registration:
        raise HTTPException(status_code=403, detail="注册功能已关闭")
    try:
        user = await auth_service.register(
            username=req.username,
            password=req.password,
            email=req.email,
            full_name=req.full_name,
        )
        return user
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"注册失败: {str(e)}")


@router.post("/api/v1/auth/login", response_model=TokenResponse, tags=["认证"])
@limiter.limit(LIMIT_AUTH)
async def login(req: UserLoginRequest, request: Request):
    """用户登录，返回访问令牌."""
    auth_service = request.app.state._auth_service
    security = request.app.state._security

    try:
        user = await auth_service.login(req.username, req.password)
        if not user:
            raise HTTPException(status_code=401, detail="用户名或密码错误")
        token = auth_service.create_token(user)
        return TokenResponse(
            access_token=token,
            token_type="bearer",
            expires_in=security.access_token_expire_minutes * 60,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"登录失败: {str(e)}")


@router.get("/api/v1/auth/me", response_model=UserResponse, tags=["认证"])
async def get_me(current_user=Depends(get_current_user)):
    """获取当前登录用户信息."""
    return current_user


@router.put("/api/v1/auth/me", response_model=UserResponse, tags=["认证"])
async def update_me(
    req: UserUpdateRequest,
    request: Request,
    current_user=Depends(get_current_user),
):
    """更新当前用户信息."""
    auth_service = request.app.state._auth_service
    try:
        user = await auth_service.update_user(
            current_user.id,
            email=req.email,
            full_name=req.full_name,
        )
        return user
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"更新失败: {str(e)}")


@router.post("/api/v1/auth/change-password", tags=["认证"])
@limiter.limit(LIMIT_AUTH)
async def change_password(
    req: ChangePasswordRequest,
    request: Request,
    current_user=Depends(get_current_user),
):
    """修改密码."""
    auth_service = request.app.state._auth_service
    try:
        success = await auth_service.change_password(
            current_user.id,
            req.old_password,
            req.new_password,
        )
        if not success:
            raise HTTPException(status_code=400, detail="原密码错误")
        return {"status": "success", "message": "密码修改成功"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"密码修改失败: {str(e)}")
