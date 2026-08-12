"""安全工具 — 密码哈希与 JWT Token."""

from datetime import timedelta
from typing import Any, Dict, Optional

from geo_review.utils.time import now as beijing_now

import bcrypt
from jose import JWTError, jwt


class SecurityUtils:
    """安全工具类."""

    def __init__(
        self,
        secret_key: str = "",
        algorithm: str = "HS256",
        access_token_expire_minutes: int = 60 * 24,
    ):
        if not secret_key:
            import os
            secret_key = os.environ.get("AUTH_SECRET_KEY", "")
            if not secret_key:
                import secrets as _s
                import string as _st
                secret_key = ''.join(_s.choice(_st.ascii_letters + _st.digits + "_-") for _ in range(64))
        self.secret_key = secret_key
        self.algorithm = algorithm
        self.access_token_expire_minutes = access_token_expire_minutes

    def hash_password(self, password: str) -> str:
        """密码哈希."""
        password_bytes = password.encode("utf-8")
        if len(password_bytes) > 72:
            password_bytes = password_bytes[:72]
        salt = bcrypt.gensalt()
        hashed = bcrypt.hashpw(password_bytes, salt)
        return hashed.decode("utf-8")

    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        """验证密码."""
        try:
            plain_bytes = plain_password.encode("utf-8")
            if len(plain_bytes) > 72:
                plain_bytes = plain_bytes[:72]
            return bcrypt.checkpw(plain_bytes, hashed_password.encode("utf-8"))
        except Exception:
            return False

    def create_access_token(
        self,
        data: Dict[str, Any],
        expires_delta: Optional[timedelta] = None,
    ) -> str:
        """创建访问令牌."""
        to_encode = data.copy()
        if expires_delta:
            expire = beijing_now() + expires_delta
        else:
            expire = beijing_now() + timedelta(minutes=self.access_token_expire_minutes)
        to_encode.update({"exp": expire, "iat": beijing_now()})
        return jwt.encode(to_encode, self.secret_key, algorithm=self.algorithm)

    def decode_token(self, token: str) -> Optional[Dict[str, Any]]:
        """解码并验证 Token."""
        try:
            payload = jwt.decode(token, self.secret_key, algorithms=[self.algorithm])
            return payload
        except JWTError:
            return None
