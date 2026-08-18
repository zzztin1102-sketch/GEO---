# GEO 生文审核系统 — 云服务器部署指南

## 整体流程

```
购买云服务器 → 连接服务器 → 上传项目 → Docker 部署 → 配置安全组 → 用户通过公网 IP 访问
```

---

## 第一步：购买阿里云 ECS 服务器

1. 打开 https://www.aliyun.com ，注册/登录账号
2. 进入**控制台** → **云服务器 ECS** → **创建实例**
3. 推荐配置：

| 项目 | 推荐选择 |
|------|---------|
| 地域 | 华北2（北京）或 华东1（杭州） |
| 实例规格 | 2核4G（ecs.c6.large）起步 |
| 镜像 | Ubuntu 22.04 64位 |
| 系统盘 | 40G SSD |
| 带宽 | 按固定带宽 1Mbps（或按流量计费） |
| 安全组 | 开放 22(SSH)、8000(Web)、443(HTTPS) 端口 |

4. 设置 root 密码，完成购买（约 50-100元/月）

---

## 第二步：连接服务器

### Windows 用户
1. 下载 PuTTY：https://www.putty.org/
2. 打开 PuTTY，填入服务器**公网 IP**，端口 22，点击 Open
3. 用户名输入 `root`，输入你设置的密码

### Mac/Linux 用户
直接在终端执行：
```bash
ssh root@你的服务器公网IP
```

---

## 第三步：安装 Docker

连接到服务器后，依次执行以下命令：

```bash
# 更新系统
apt update && apt upgrade -y

# 安装 Docker
curl -fsSL https://get.docker.com | bash

# 启动 Docker 并设置开机自启
systemctl start docker
systemctl enable docker

# 验证安装
docker --version
```

---

## 第四步：上传项目文件

### 方法 A：使用 SCP（推荐）

在你的**本地电脑**终端执行：

```bash
# 将项目打包（--exclude 排除敏感文件和本地数据，切勿省略）
# 排除项说明：
#   .env / *.env        — 真实 API Key、JWT 密钥、管理员密码（服务器上单独配置）
#   *.db                — 本地审核历史数据（含企业文案，不应上传）
#   .workbuddy          — 本地会话记忆
#   __pycache__ 等      — 编译缓存，无需上传
cd "c:\Users\zhaoting1\Desktop"
tar -czf geo-review.tar.gz \
  --exclude=".env" \
  --exclude="*.env" \
  --exclude="*.db" \
  --exclude=".workbuddy" \
  --exclude="__pycache__" \
  --exclude="*.pyc" \
  --exclude=".pytest_cache" \
  "GEO生文审核 - 副本"

# 上传到服务器（替换为你的服务器 IP）
scp geo-review.tar.gz root@你的服务器公网IP:/root/

# 上传完成后删除本地压缩包（含项目源码，避免留存泄露）
del geo-review.tar.gz
```

> ⚠️ 注意：`tar` 不会读取 `.gitignore`，如不使用上述 `--exclude` 参数，`.env`（真实 LLM API Key、JWT 密钥）和 `review_history.db`（真实审核数据）会被一并上传到服务器。

### 方法 B：使用宝塔面板（图形化）

1. 在服务器上安装宝塔面板：
```bash
curl -sSO https://download.bt.cn/install/install_panel.sh && bash install_panel.sh
```
2. 浏览器打开宝塔面板 → 文件管理 → 上传项目压缩包 → 解压

---

## 第五步：Docker 部署

在**服务器上**执行：

```bash
# 解压项目
cd /root
tar -xzf geo-review.tar.gz
cd "GEO生文审核 - 副本"

# 修改 config.yaml 中的绑定地址
# 将 host 改为 0.0.0.0（如已是则不用改）

# 构建并启动
docker compose up -d --build

# 查看运行状态
docker compose ps
docker compose logs -f
```

启动成功后，服务运行在容器的 8000 端口。

---

## 第六步：配置安全组（开放端口）

1. 进入阿里云控制台 → **ECS** → 点击你的实例
2. 点击**安全组** → **配置规则** → **手动添加**
3. 添加规则：

| 方向 | 端口范围 | 授权对象 | 说明 |
|------|---------|---------|------|
| 入方向 | 8000 | 0.0.0.0/0 | 允许所有 IP 访问 Web |

4. 保存

---

## 第七步：访问系统

在任意浏览器输入：

```
http://你的服务器公网IP:8000
```

例如：`http://你的服务器公网IP:8000`

用户即可访问审核系统，无需任何本地文件。

---

## 第八步：生产安全清单（重要，公网开放前必读）

> ⚠️ 安全组开放 `0.0.0.0/0` 意味着**任何人都能访问你的服务**。本系统涉及 LLM API 费用消耗和审核数据存储，公网部署前请逐项确认：

### ✅ 部署前检查表

| # | 检查项 | 说明 | 未做的后果 |
|---|--------|------|-----------|
| 1 | **配置认证密钥** | 在服务器上创建 `.env`，设置 `AUTH_SECRET_KEY`（运行 `python scripts/generate_secret_key.py` 生成 64+ 字符随机密钥） | JWT 可被伪造，任何人可冒充管理员 |
| 2 | **设置强管理员密码** | `.env` 中 `AUTH_ADMIN_PASSWORD` 需包含大小写字母、数字、特殊字符，至少 12 位，且勿与示例相同 | 弱密码可被暴力破解 |
| 3 | **注册并要求用户登录** | 首次启动用管理员账号登录，业务用户通过 `/api/v1/auth/register` 注册；前端登录后才可调用审核接口 | 任何人可调用 `POST /api/v1/review` 消耗你的 LLM API 费用；`DELETE /api/v1/cache` 清空缓存；`GET /api/v1/history` 拉取全部审核记录 |
| 4 | **配置 API Key** | LLM API Key 只写入服务器上的 `.env` 或 `config.yaml`，不要打包上传、不要提交到 Git | API Key 泄露后被盗刷 |
| 5 | **启用 HTTPS** | 见下方"绑定域名 + HTTPS"章节 | 登录密码、审核数据明文传输，可被中间人截获 |
| 6 | **确认限流生效** | 检查 `config.yaml` 中 rate_limit 配置未被关闭 | 接口被恶意高频调用，服务崩溃或费用激增 |
| 7 | **安全组最小开放** | 8000 端口可临时全网开放用于验证，验证后建议收敛为办公网 IP 段 | 服务暴露面过大 |

### 服务器操作建议

```bash
# 1. 在服务器上创建 .env（不要在本地打包上传）
cd "GEO生文审核 - 副本"
cp .env.example .env

# 2. 生成 JWT 密钥并写入 .env
python scripts/generate_secret_key.py
# 将输出的密钥填入 .env 的 AUTH_SECRET_KEY

# 3. 修改管理员密码
# 编辑 .env，设置 AUTH_ADMIN_PASSWORD 为你的强密码

# 4. 重启服务生效
docker compose restart
```

> 💡 建议：日常运维避免直接使用 root 账户操作服务器，可创建普通用户并加入 sudo 组；执行 `curl ... | bash` 这类管道脚本前，先下载脚本审阅内容再执行。

---

## 常用运维命令

```bash
# 查看服务状态
docker compose ps

# 查看实时日志
docker compose logs -f

# 重启服务
docker compose restart

# 停止服务
docker compose down

# 更新代码后重新部署
docker compose up -d --build

# 修改配置后重启
docker compose restart
```

---

## 可选：绑定域名 + HTTPS

> ⚠️ **重要提醒**：本系统处理企业营销文案、提报表等商业敏感数据，审核记录含企业信息。未配置 HTTPS 时，所有数据（包括登录密码）均以明文在公网传输，仅适合**短期演示**。正式使用必须完成本节配置。

### 1. 购买域名
在阿里云购买域名（约 30-80元/年）

### 2. 域名解析
控制台 → **域名** → **解析** → 添加 A 记录：
- 记录类型：A
- 主机记录：@（或 www）
- 记录值：你的服务器公网 IP

### 3. 配置 Nginx 反向代理 + SSL

```bash
# 安装 Nginx
apt install nginx -y

# 安装 Certbot（免费 SSL 证书）
apt install certbot python3-certbot-nginx -y

# 配置反向代理
cat > /etc/nginx/sites-available/geo-review << 'EOF'
server {
    listen 80;
    server_name 你的域名.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
EOF

# 启用站点
ln -s /etc/nginx/sites-available/geo-review /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx

# 申请 SSL 证书（自动配置 HTTPS）
certbot --nginx -d 你的域名.com
```

完成后用户可通过 `https://你的域名.com` 直接访问。

---

## 费用估算

| 项目 | 费用 |
|------|------|
| 阿里云 ECS 2核4G | 约 60-100元/月 |
| 域名（可选） | 约 30-80元/年 |
| SSL 证书 | 免费（Let's Encrypt） |
| LLM API | 按量计费 |

**最低成本约 60元/月**即可让用户通过链接访问系统。
