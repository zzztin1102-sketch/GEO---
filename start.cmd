@echo off
chcp 65001 >nul
title GEO 生文审核 Agent

echo ============================================
echo   GEO 生文审核 Agent — Windows 启动器
echo ============================================
echo.

setlocal

REM 设置项目目录
set "PROJECT_DIR=%~dp0"
set "PYTHONPATH=%PROJECT_DIR%src"

REM 检查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python！
    echo        请先安装 Python 3.8+，安装时务必勾选 "Add to PATH"
    echo        下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

python --version
echo.

REM 安装依赖
echo [1/3] 检查依赖...
python -c "import fastapi, uvicorn, pydantic, sqlalchemy, yaml, requests" >nul 2>&1
if errorlevel 1 (
    echo [安装] 缺少依赖，正在安装...
    pip install fastapi uvicorn pydantic sqlalchemy aiosqlite pyyaml requests python-multipart bcrypt python-jose openai
    if errorlevel 1 (
        echo [错误] 依赖安装失败！
        echo        请手动运行:
        echo        pip install fastapi uvicorn pydantic sqlalchemy aiosqlite pyyaml requests python-multipart bcrypt python-jose openai
        pause
        exit /b 1
    )
)
echo [OK] 依赖检查通过
echo.

REM 自动修复配置
echo [2/3] 检查配置文件...
python -c "
import yaml, os, secrets, string
config_path = 'config.yaml'
if os.path.exists(config_path):
    with open(config_path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f) or {}
    auth = data.setdefault('auth', {})
    modified = False
    if not auth.get('secret_key'):
        auth['secret_key'] = ''.join(secrets.choice(string.ascii_letters + string.digits + '_-') for _ in range(64))
        print('[FIX] 已自动生成 JWT 密钥')
        modified = True
    if not auth.get('default_admin_password'):
        auth['default_admin_password'] = os.environ.get('AUTH_ADMIN_PASSWORD', '')
        if not auth['default_admin_password']:
            auth['default_admin_password'] = ''.join(secrets.choice(string.ascii_letters + string.digits + '!@#$%') for _ in range(16))
        print('[FIX] auth.default_admin_password 已从环境变量读取或随机生成')
        modified = True
    if modified:
        with open(config_path, 'w', encoding='utf-8') as f:
            yaml.dump(data, f, allow_unicode=True, sort_keys=False)
        print('[FIX] 配置文件已修复')
    else:
        print('[OK] 配置文件正常')
else:
    print('[WARN] 配置文件不存在')
"
echo.

REM 启动服务
echo [3/3] 启动 FastAPI 服务...
echo ============================================
echo.
echo 启动中，请稍候...
echo.

python run.py

echo.
echo 服务已停止。
pause