"""认证服务 — 用户注册/登录/验证."""

from typing import Optional, List

from geo_review.utils.time import now as beijing_now

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from geo_review.auth.models import User
from geo_review.auth.security import SecurityUtils
from geo_review.utils.security import check_password_strength


class AuthService:
    """认证服务."""

    def __init__(
        self,
        async_session: async_sessionmaker,
        security: Optional[SecurityUtils] = None,
    ):
        self.async_session = async_session
        self.security = security or SecurityUtils()

    async def register(
        self,
        username: str,
        password: str,
        email: Optional[str] = None,
        full_name: Optional[str] = None,
        role: str = "user",
    ) -> User:
        """用户注册.

        Raises:
            ValueError: 用户名已存在或密码强度不足
        """
        # 密码强度校验
        ok, errors = check_password_strength(password)
        if not ok:
            raise ValueError(f"密码强度不足: {'; '.join(errors)}")

        async with self.async_session() as session:
            existing = await self._get_by_username(session, username)
            if existing:
                raise ValueError(f"用户名 '{username}' 已存在")

            if email:
                existing_email = await self._get_by_email(session, email)
                if existing_email:
                    raise ValueError(f"邮箱 '{email}' 已被注册")

            password_hash = self.security.hash_password(password)
            user = User(
                username=username,
                email=email,
                password_hash=password_hash,
                full_name=full_name,
                role=role,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user

    async def login(self, username: str, password: str) -> Optional[User]:
        """用户登录.

        Returns:
            User: 登录成功返回用户对象，失败返回 None
        """
        async with self.async_session() as session:
            user = await self._get_by_username(session, username)
            if not user:
                return None
            if not user.is_active:
                return None
            if not self.security.verify_password(password, user.password_hash):
                return None

            user.last_login_at = beijing_now()
            await session.commit()
            await session.refresh(user)
            return user

    async def get_by_username(self, username: str) -> Optional[User]:
        """按用户名获取用户."""
        async with self.async_session() as session:
            return await self._get_by_username(session, username)

    async def get_by_email(self, email: str) -> Optional[User]:
        """按邮箱获取用户."""
        async with self.async_session() as session:
            return await self._get_by_email(session, email)

    async def get_by_id(self, user_id: str) -> Optional[User]:
        """按 ID 获取用户."""
        async with self.async_session() as session:
            result = await session.execute(
                select(User).where(User.id == user_id)
            )
            return result.scalar_one_or_none()

    async def list_users(
        self,
        page: int = 1,
        page_size: int = 20,
        role: Optional[str] = None,
    ) -> tuple[List[User], int]:
        """用户列表（分页）."""
        async with self.async_session() as session:
            query = select(User)
            if role:
                query = query.where(User.role == role)

            count_stmt = select(func.count()).select_from(query.subquery())
            count_result = await session.execute(count_stmt)
            total = count_result.scalar() or 0

            query = query.offset((page - 1) * page_size).limit(page_size)
            result = await session.execute(query)
            users = list(result.scalars().all())
            return users, total

    async def update_user(
        self,
        user_id: str,
        email: Optional[str] = None,
        full_name: Optional[str] = None,
        role: Optional[str] = None,
        is_active: Optional[bool] = None,
    ) -> Optional[User]:
        """更新用户信息."""
        async with self.async_session() as session:
            result = await session.execute(
                select(User).where(User.id == user_id)
            )
            user = result.scalar_one_or_none()
            if not user:
                return None

            if email is not None:
                user.email = email
            if full_name is not None:
                user.full_name = full_name
            if role is not None:
                user.role = role
            if is_active is not None:
                user.is_active = is_active

            user.updated_at = beijing_now()
            await session.commit()
            await session.refresh(user)
            return user

    async def change_password(
        self,
        user_id: str,
        old_password: str,
        new_password: str,
    ) -> bool:
        """修改密码."""
        # 新密码强度校验
        ok, errors = check_password_strength(new_password)
        if not ok:
            raise ValueError(f"新密码强度不足: {'; '.join(errors)}")

        async with self.async_session() as session:
            result = await session.execute(
                select(User).where(User.id == user_id)
            )
            user = result.scalar_one_or_none()
            if not user:
                return False

            if not self.security.verify_password(old_password, user.password_hash):
                return False

            user.password_hash = self.security.hash_password(new_password)
            user.updated_at = beijing_now()
            await session.commit()
            return True

    async def set_password(
        self,
        username: str,
        new_password: str,
    ) -> bool:
        """重置指定用户密码（无需旧密码，仅用于系统初始化等内部场景）.

        Args:
            username: 用户名
            new_password: 新密码

        Returns:
            bool: 是否成功
        """
        # 新密码强度校验
        ok, errors = check_password_strength(new_password)
        if not ok:
            raise ValueError(f"新密码强度不足: {'; '.join(errors)}")

        async with self.async_session() as session:
            user = await self._get_by_username(session, username)
            if not user:
                return False

            user.password_hash = self.security.hash_password(new_password)
            user.updated_at = beijing_now()
            await session.commit()
            return True

    async def delete_user(self, user_id: str) -> bool:
        """删除用户（软删除，禁用账户）."""
        async with self.async_session() as session:
            result = await session.execute(
                select(User).where(User.id == user_id)
            )
            user = result.scalar_one_or_none()
            if not user:
                return False

            user.is_active = False
            user.updated_at = beijing_now()
            await session.commit()
            return True

    def create_token(self, user: User) -> str:
        """为用户创建访问令牌."""
        return self.security.create_access_token(
            data={"sub": user.id, "username": user.username, "role": user.role}
        )

    def verify_token(self, token: str) -> Optional[str]:
        """验证 Token 并返回用户 ID."""
        payload = self.security.decode_token(token)
        if not payload:
            return None
        return payload.get("sub")

    # ===== 内部方法（接收 session 参数） =====

    @staticmethod
    async def _get_by_username(session: AsyncSession, username: str) -> Optional[User]:
        result = await session.execute(
            select(User).where(User.username == username)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def _get_by_email(session: AsyncSession, email: str) -> Optional[User]:
        result = await session.execute(
            select(User).where(User.email == email)
        )
        return result.scalar_one_or_none()
