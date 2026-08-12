#!/usr/bin/env python3
"""GEO 生文审核 Agent — 一键启动脚本（Windows 兼容）.

用法:
    python run.py                    # 使用默认配置启动
    python run.py --host 127.0.0.1   # 指定绑定地址
    python run.py --port 8080        # 指定端口
    python run.py --config config.yaml  # 指定配置文件
"""

import argparse
import os
import sys
import traceback

# 确保 src 在 Python 路径中
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(SCRIPT_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

# 加载 .env 文件中的环境变量
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(SCRIPT_DIR, ".env"))
except ImportError:
    pass


def check_dependencies():
    """检查必要的依赖是否已安装."""
    required = [
        ("fastapi", "FastAPI"),
        ("uvicorn", "Uvicorn"),
        ("pydantic", "Pydantic"),
        ("sqlalchemy", "SQLAlchemy"),
        ("aiosqlite", "aiosqlite"),
        ("yaml", "PyYAML"),
        ("requests", "Requests"),
        ("openai", "OpenAI"),
        ("bs4", "BeautifulSoup4"),
        ("openpyxl", "openpyxl"),
        ("xlrd", "xlrd"),
        ("multipart", "python-multipart"),
        ("jose", "python-jose"),
        ("bcrypt", "bcrypt"),
        ("PyPDF2", "PyPDF2"),
        ("docx", "python-docx"),
        ("docx2txt", "docx2txt"),
    ]
    missing = []
    for module, name in required:
        try:
            __import__(module)
        except ImportError:
            missing.append(name)
    return missing


def fix_config(config_path: str):
    """自动修复配置文件中的常见问题."""
    import yaml

    if not os.path.exists(config_path):
        print(f"[WARN] 配置文件不存在: {config_path}")
        return

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:
        print(f"[WARN] 读取配置文件失败: {e}")
        return

    modified = False

    # 检查并修复 auth 配置
    auth = data.setdefault("auth", {})

    if not auth.get("secret_key"):
        # 生成临时密钥
        import secrets
        import string
        temp_key = ''.join(secrets.choice(string.ascii_letters + string.digits + "_-") for _ in range(64))
        auth["secret_key"] = temp_key
        modified = True
        print(f"[FIX] auth.secret_key 为空，已自动生成临时密钥")
        print(f"      临时密钥: {temp_key[:20]}...（请妥善保管）")

    if not auth.get("default_admin_password"):
        auth["default_admin_password"] = os.environ.get("AUTH_ADMIN_PASSWORD", "")
        if not auth["default_admin_password"]:
            import secrets as _s
            import string as _st
            auth["default_admin_password"] = ''.join(_s.choice(_st.ascii_letters + _st.digits + "!@#$%") for _ in range(16))
        modified = True
        print(f"[FIX] auth.default_admin_password 为空，已从环境变量读取或随机生成")
        print(f"      启动后请立即修改默认密码！")

    if modified:
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                yaml.dump(data, f, allow_unicode=True, sort_keys=False)
            print(f"[FIX] 配置文件已自动修复并保存")
        except Exception as e:
            print(f"[WARN] 保存配置文件失败: {e}")


def main():
    parser = argparse.ArgumentParser(description="GEO 生文审核 Agent 启动脚本")
    parser.add_argument("--host", default="127.0.0.1", help="绑定地址 (默认: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="监听端口 (默认: 8000)")
    parser.add_argument("--config", default="config.yaml", help="配置文件路径 (默认: config.yaml)")
    parser.add_argument("--reload", action="store_true", help="开发模式：代码变更自动重载")
    args = parser.parse_args()

    print("=" * 60)
    print("  GEO 生文审核 Agent — 启动脚本")
    print("=" * 60)

    # 1. 检查依赖
    print("\n[1/4] 检查依赖...")
    missing = check_dependencies()
    if missing:
        print(f"[ERROR] 缺少以下依赖: {', '.join(missing)}")
        print("        请运行: pip install " + " ".join(m.lower().replace("pyyaml", "pyyaml").replace("beautifulsoup4", "beautifulsoup4") for m in missing))
        print("        完整依赖: pip install fastapi uvicorn pydantic sqlalchemy aiosqlite pyyaml requests python-multipart openai bcrypt python-jose beautifulsoup4 openpyxl xlrd PyPDF2 python-docx docx2txt")
        sys.exit(1)
    print("[OK] 所有依赖已安装")

    # 2. 自动修复配置
    print("\n[2/4] 检查配置文件...")
    config_path = os.path.join(SCRIPT_DIR, args.config)
    fix_config(config_path)

    # 3. 加载配置验证
    print("\n[3/4] 加载应用配置...")
    try:
        from geo_review.config import load_config
        config = load_config(config_path)
        print(f"[OK] 配置加载成功")
        print(f"     API 地址: http://{args.host}:{args.port}")
        print(f"     认证状态: {'启用' if config.auth.enabled else '已关闭（所有接口无需认证）'}")
        print(f"     LLM Provider: {config.llm.provider}")
        if not config.llm.api_key:
            print(f"     [WARN] LLM API Key 未配置，LLM 语义审核将不可用")
    except Exception as e:
        print(f"[ERROR] 配置加载失败: {e}")
        traceback.print_exc()
        sys.exit(1)

    # 4. 创建并启动应用
    print("\n[4/4] 启动 FastAPI 服务...")
    print("-" * 60)
    try:
        from geo_review.api.app import create_app
        import uvicorn

        app = create_app(config=config)

        # 打印访问地址
        print(f"\n  服务已启动，请通过以下地址访问：")
        print(f"  ┌────────────────────────────────────────────┐")
        print(f"  │  Web 界面:  http://{args.host}:{args.port}/              │")
        print(f"  │  API 文档:  http://{args.host}:{args.port}/docs          │")
        print(f"  │  健康检查:  http://{args.host}:{args.port}/api/v1/health │")
        print(f"  └────────────────────────────────────────────┘")
        print(f"\n  按 Ctrl+C 停止服务\n")

        uvicorn.run(
            app,
            host=args.host,
            port=args.port,
            reload=args.reload,
            log_level="info",
        )
    except Exception as e:
        print(f"\n[ERROR] 服务启动失败: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()