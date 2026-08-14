FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 设置时区
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# 一次性安装系统依赖：编译工具 + Playwright/Chromium 运行所需的所有共享库
# 合并 apt-get update 和 install，减少镜像层
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    # Playwright/Chromium 共享库依赖（详见 https://playwright.dev/linux/）
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    libatspi2 \
    # Chromium 可能用到的可选依赖（字体、下载等）
    fonts-noto-cjk \
    fonts-liberation \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 先复制依赖文件，利用 Docker 缓存层
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ✅ 安装 Playwright Chromium 浏览器二进制
# 使用 PLAYWRIGHT_BROWSERS_PATH 指定安装路径（避免污染系统默认路径）
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN python -m playwright install chromium

# 复制项目文件
COPY . .

# 暴露端口
EXPOSE 8000

# 启动命令
CMD ["python", "run.py", "--host", "0.0.0.0", "--port", "8000"]