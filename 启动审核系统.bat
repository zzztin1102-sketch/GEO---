@echo off
chcp 65001 >nul 2>&1
title GEO 生文审核系统
cd /d "%~dp0"

echo ════════════════════════════════════════════════
echo   GEO 生文审核系统 — 正在启动...
echo ════════════════════════════════════════════════
echo.

REM 延迟 3 秒后自动打开浏览器
start "" cmd /c "timeout /t 3 /nobreak >nul & start http://127.0.0.1:8000"

REM 启动服务
python run.py

echo.
echo 服务已停止，按任意键退出...
pause >nul
