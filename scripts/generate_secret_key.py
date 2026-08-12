#!/usr/bin/env python3
"""生成安全的 JWT Secret Key 和临时密码.

用法:
    python scripts/generate_secret_key.py
    python scripts/generate_secret_key.py --length 64
    python scripts/generate_secret_key.py --password
"""

import argparse
import sys
import os

# 确保能导入项目模块
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from geo_review.utils.security import generate_secret_key, generate_temp_password


def main():
    parser = argparse.ArgumentParser(
        description="生成安全的 JWT Secret Key 和临时密码",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python scripts/generate_secret_key.py              # 生成 64 位密钥
  python scripts/generate_secret_key.py -l 128       # 生成 128 位密钥
  python scripts/generate_secret_key.py --password   # 生成临时密码
""",
    )
    parser.add_argument(
        "-l", "--length",
        type=int,
        default=64,
        help="密钥长度（默认 64）",
    )
    parser.add_argument(
        "--password",
        action="store_true",
        help="生成临时密码而非密钥",
    )

    args = parser.parse_args()

    if args.password:
        password = generate_temp_password()
        print("=" * 60)
        print("  临时密码（请妥善保管，仅显示一次）")
        print("=" * 60)
        print(f"\n  {password}\n")
        print("=" * 60)
        print("\n使用方式:")
        print("  1. 在 config.yaml 中设置:")
        print(f'     default_admin_password: "{password}"')
        print("  2. 或通过环境变量:")
        print(f'     set AUTH_DEFAULT_ADMIN_PASSWORD={password}')
    else:
        secret = generate_secret_key(args.length)
        print("=" * 60)
        print("  安全密钥（请妥善保管，仅显示一次）")
        print("=" * 60)
        print(f"\n  {secret}\n")
        print("=" * 60)
        print("\n使用方式:")
        print("  1. 在 config.yaml 中设置:")
        print(f'     secret_key: "{secret}"')
        print("  2. 或通过环境变量:")
        print(f'     set AUTH_SECRET_KEY={secret}')
        print("\n安全提示:")
        print("  - 请勿将密钥提交到版本控制")
        print("  - 定期轮换密钥（建议每 90 天）")
        print("  - 生产环境强烈建议通过环境变量设置")


if __name__ == "__main__":
    main()