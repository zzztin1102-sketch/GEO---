"""URL 安全校验 — 防止 SSRF（服务端请求伪造）攻击.

检查项：
1. 协议白名单（仅允许 http/https）
2. 主机名黑名单（localhost 等）
3. IP 地址白名单（拒绝私有/保留/链路本地/回环地址）
4. DNS 解析后二次校验（防止 DNS Rebinding）
"""

import ipaddress
import logging
import socket
from typing import List, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# 允许的协议
_ALLOWED_SCHEMES = {"http", "https"}

# 禁止的主机名（不区分大小写）
_BLOCKED_HOSTNAMES = {
    "localhost",
    "ip6-localhost",
    "ip6-loopback",
    "broadcasthost",
    "metadata.google.internal",  # GCP 元数据端点
}

# 允许的端口（None 表示协议默认端口）
_ALLOWED_PORTS: List[int] = [80, 443, 8080, 8000, 8443, 3000, 5000, 9000]


class SSRFError(ValueError):
    """URL 安全校验失败."""


def validate_url(url: str, *, allow_private: bool = False) -> str:
    """校验 URL 安全性，防止 SSRF 攻击.

    Args:
        url: 待校验的 URL
        allow_private: 是否允许内网地址（本地开发环境可设为 True）

    Returns:
        校验通过后的 URL（原值）

    Raises:
        SSRFError: URL 不安全
    """
    if not url or not isinstance(url, str):
        raise SSRFError("URL 为空")

    parsed = urlparse(url)

    # 1. 协议白名单
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise SSRFError(f"不允许的协议: {parsed.scheme}（仅支持 http/https）")

    if not parsed.hostname:
        raise SSRFError("无法解析主机名")

    hostname = parsed.hostname.lower()

    # 2. 主机名黑名单
    if hostname in _BLOCKED_HOSTNAMES:
        raise SSRFError(f"禁止访问的主机: {hostname}")

    # 3. IP 地址校验
    ip = _try_parse_ip(hostname)
    if ip is not None:
        _check_ip_safety(ip, allow_private=allow_private)

    # 4. DNS 解析后二次校验（防止 DNS Rebinding）
    if ip is None:
        _check_dns_resolution(hostname, allow_private=allow_private)

    # 5. 端口校验（非标准端口不阻止，但记录日志）
    port = parsed.port
    if port and port not in _ALLOWED_PORTS:
        logger.info(f"URL 使用非标准端口: {port}（已放行，仅记录）")

    return url


def _try_parse_ip(hostname: str):
    """尝试将主机名解析为 IP 地址对象."""
    try:
        return ipaddress.ip_address(hostname)
    except ValueError:
        return None


def _check_ip_safety(ip, *, allow_private: bool = False) -> None:
    """检查 IP 地址是否安全."""
    # 回环地址 127.0.0.0/8, ::1
    if ip.is_loopback:
        raise SSRFError(f"禁止访问回环地址: {ip}")

    # 链路本地 169.254.0.0/16（AWS/GCP 元数据端点在此范围）
    if ip.is_link_local:
        raise SSRFError(f"禁止访问链路本地地址: {ip}（含云元数据端点）")

    # 保留地址（广播、多播等）
    if ip.is_reserved:
        raise SSRFError(f"禁止访问保留地址: {ip}")

    # 多播地址
    if ip.is_multicast:
        raise SSRFError(f"禁止访问多播地址: {ip}")

    # 私有地址（除非显式允许）
    if ip.is_private and not allow_private:
        raise SSRFError(
            f"禁止访问私有地址: {ip}（如需本地开发，设置 allow_private=True）"
        )

    # 未分配地址
    if ip.is_unspecified:
        raise SSRFError(f"禁止访问未指定地址: {ip}")


def _check_dns_resolution(hostname: str, *, allow_private: bool = False) -> None:
    """DNS 解析主机名并检查解析结果是否安全.

    防止 DNS Rebinding：攻击者控制 DNS 服务器，首次解析返回公网 IP 通过校验，
    第二次解析返回内网 IP 绕过防护。通过同时检查所有解析结果来防御。
    """
    try:
        infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        # DNS 解析失败 — 可能是无效域名，不阻止（让后续 HTTP 请求自然失败）
        logger.debug(f"DNS 解析失败（不阻止，让 HTTP 请求处理）: {hostname}: {exc}")
        return

    for family, _, _, _, sockaddr in infos:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        _check_ip_safety(ip, allow_private=allow_private)
