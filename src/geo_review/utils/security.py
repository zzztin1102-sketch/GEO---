"""安全工具模块 — 密码强度检查、密钥生成、安全配置校验."""

import secrets
import string
import re
from typing import List, Tuple, Optional


# 已知弱密码/默认密码黑名单
_PASSWORD_BLACKLIST = {
    "admin123", "password", "123456", "12345678",
    "qwerty", "password123", "admin", "root",
    "geo-review-secret-key-change-in-production",
}

# 已知弱密钥
_SECRET_KEY_BLACKLIST = {
    "geo-review-secret-key-change-in-production",
    "secret", "your-secret-key", "change-me",
}


def generate_secret_key(length: int = 64) -> str:
    """生成安全的随机密钥.

    Args:
        length: 密钥长度，默认 64

    Returns:
        安全的随机字符串（URL-safe base64）
    """
    alphabet = string.ascii_letters + string.digits + "_-"
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def generate_temp_password(length: int = 16) -> str:
    """生成安全的临时密码.

    Args:
        length: 密码长度，默认 16

    Returns:
        包含大小写字母、数字和特殊字符的密码
    """
    special_chars = '!@#$%^&*()_+-='
    alphabet = string.ascii_letters + string.digits + special_chars
    password = ''.join(secrets.choice(alphabet) for _ in range(length))
    while (not re.search(r'[A-Z]', password) or
           not re.search(r'[a-z]', password) or
           not re.search(r'\d', password) or
           not re.search(r'[' + re.escape(special_chars) + ']', password)):
        password = ''.join(secrets.choice(alphabet) for _ in range(length))
    return password


def check_password_strength(password: str) -> Tuple[bool, List[str]]:
    """检查密码强度.

    Returns:
        (是否通过, 未通过的原因列表)
    """
    errors: List[str] = []

    if not password:
        errors.append("密码不能为空")
        return False, errors

    if len(password) < 8:
        errors.append("密码长度至少 8 位")

    if len(password) < 12:
        errors.append("建议密码长度至少 12 位")

    if not re.search(r'[A-Z]', password):
        errors.append("密码必须包含大写字母")

    if not re.search(r'[a-z]', password):
        errors.append("密码必须包含小写字母")

    if not re.search(r'\d', password):
        errors.append("密码必须包含数字")

    if not re.search(r'[!@#$%^&*()_+\-=\[\]{};:\'",.<>?|\\/~`]', password):
        errors.append("密码必须包含特殊字符（如 !@#$% 等）")

    if password.lower() in _PASSWORD_BLACKLIST:
        errors.append("密码过于常见，已被列入黑名单")

    # 检查连续字符
    if _has_consecutive_chars(password, 4):
        errors.append("密码不能包含 4 个及以上连续相同字符")

    # 检查简单序列
    if _has_simple_sequence(password):
        errors.append("密码不能包含简单序列（如 abc, 123, qwer 等）")

    return len(errors) == 0, errors


def check_secret_key_strength(secret_key: str) -> Tuple[bool, List[str]]:
    """检查 JWT 密钥强度.

    Returns:
        (是否通过, 未通过的原因列表)
    """
    errors: List[str] = []

    if not secret_key:
        errors.append("密钥不能为空")
        return False, errors

    if len(secret_key) < 32:
        errors.append(f"密钥长度过短（当前 {len(secret_key)} 位，建议至少 32 位）")

    if len(secret_key) < 48:
        errors.append("建议密钥长度至少 48 位")

    if secret_key.lower() in {k.lower() for k in _SECRET_KEY_BLACKLIST}:
        errors.append("密钥为已知的默认/弱密钥")

    # 检查熵值（是否足够随机）
    unique_chars = len(set(secret_key))
    if unique_chars < len(secret_key) * 0.3:
        errors.append("密钥字符重复率过高，随机性不足")

    return len(errors) == 0, errors


def is_production_env() -> bool:
    """检测是否为生产环境.

    通过检查环境变量判断.
    """
    import os
    env = os.getenv("ENV", os.getenv("NODE_ENV", "")).lower()
    return env in ("production", "prod", "staging")


def _has_consecutive_chars(password: str, threshold: int) -> bool:
    """检查是否有连续重复字符."""
    count = 1
    for i in range(1, len(password)):
        if password[i] == password[i - 1]:
            count += 1
            if count >= threshold:
                return True
        else:
            count = 1
    return False


def _has_simple_sequence(password: str) -> bool:
    """检查是否包含简单序列."""
    lower = password.lower()
    sequences = [
        "abcdefghijklmnopqrstuvwxyz",
        "qwertyuiop", "asdfghjkl", "zxcvbnm",
        "0123456789", "9876543210",
    ]
    for seq in sequences:
        for i in range(len(seq) - 3):
            if seq[i:i+4] in lower:
                return True
    return False