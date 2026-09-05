# Plasmid Designer 服务器 Docker 部署指南

---

## 一、环境准备

### 1.1 安装 Docker

```bash
# 一键安装 Docker (Ubuntu/Debian)
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER

# 退出重新登录，验证
docker --version
docker compose version
```

> 如果 `docker compose` 不可用，安装 Compose 插件:
> ```bash
> sudo apt-get update
> sudo apt-get install docker-compose-plugin
> ```

### 1.2 服务器配置建议

| 项目 | 最低 | 推荐 |
|------|------|------|
| OS | Ubuntu 20.04 | Ubuntu 22.04+ |
| RAM | 2 GB | 4 GB+ |
| Disk | 5 GB | 20 GB+ |

---

## 二、上传项目到服务器

### 方式一: Git 克隆 (推荐)

```bash
cd /root
git clone <你的仓库地址> plasmid-designer-v2
```

### 方式二: 本地上传

在本地电脑执行:

```bash
# 打包项目（排除 node_modules）
tar --exclude='node_modules' --exclude='.git' \
    -czf plasmid-designer-v2.tar.gz plasmid-designer-v2/

# 上传到服务器
scp plasmid-designer-v2.tar.gz root@你的服务器IP:/root/

# SSH 到服务器
ssh root@你的服务器IP
```

在服务器上:

```bash
cd /root
tar -xzf plasmid-designer-v2.tar.gz
ls /root/plasmid-designer-v2/data/vectors/   # 确认数据文件存在
```

---

## 三、一键部署

```bash
cd /root/plasmid-designer-v2/deploy/docker

# 运行部署脚本
chmod +x ../scripts/deploy.sh
../scripts/deploy.sh
```

脚本会自动: 创建 `.env` → 生成随机密钥 → 构建镜像 → 启动服务 → 健康检查

部署完成后访问:
- **前端**: `http://你的服务器IP`（FRONTEND_PORT 改过则加端口）
- **API 文档**: `http://你的服务器IP/docs`（由前端 nginx 代理）
- **后端直连**: 仅本机 `http://localhost:${BACKEND_PORT:-8000}/docs`（绑定回环，不对公网暴露）

---

## 四、手动部署（详细步骤）

如果一键脚本遇到问题，可按以下步骤手动操作:

### 4.1 创建环境配置

```bash
cd /root/plasmid-designer-v2/deploy/docker

# 从模板创建 .env
cp .env.example .env

# 生成安全的密钥并写入
SECRET=$(openssl rand -hex 32)
sed -i "s/change_this_in_production_use_strong_random_string/$SECRET/" .env

# 查看配置
cat .env
```

**必须确认的配置项:**

```bash
# .env 文件内容
DB_USER=plasmid
DB_PASSWORD=plasmid_secure_2026    # 生产环境请改为强密码（deploy.sh 自动随机化）
REDIS_PASSWORD=plasmid_redis_2026  # 同上，deploy.sh 自动随机化
SECRET_KEY=<自动生成的64位hex>      # 不要用默认值（deploy.sh 自动生成）
DEBUG=false
LOG_LEVEL=INFO
STORAGE_MODE=database
FRONTEND_PORT=80                   # 80 端口被占用时改为 8080 等
BACKEND_PORT=8000                  # 后端仅绑定 127.0.0.1；宿主机 8000 被占用时改为 18000 等
CORS_ORIGINS=*                     # 支持 "*"、逗号分隔或 JSON 数组；生产建议具体域名
```

### 4.2 构建镜像

```bash
# ⚠️ 必须在 deploy/docker/ 目录下执行
cd /root/plasmid-designer-v2/deploy/docker

# 构建所有镜像（首次约 5-10 分钟）
docker compose build

# 海外服务器用默认官方源即可；国内机器构建缓慢时用 build-arg 切换国内镜像:
#   docker compose build #     --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple #     --build-arg NPM_REGISTRY=https://registry.npmmirror.com
```

### 4.3 启动服务

```bash
# 后台启动所有服务
docker compose up -d

# 查看容器状态（等待约 30 秒）
docker compose ps
```

**正常状态下所有容器应为 Up:**

```
NAME               STATUS          PORTS
plasmid-frontend   Up (healthy)    0.0.0.0:80->80/tcp
plasmid-backend    Up (healthy)    127.0.0.1:8000->8000/tcp（仅本机可达）
plasmid-db         Up (healthy)    127.0.0.1:5432->5432/tcp（仅本机可达）
plasmid-redis      Up (healthy)    127.0.0.1:6379->6379/tcp（仅本机可达）
```

### 4.4 验证部署

```bash
# 测试后端（仅本机回环可达）
curl http://localhost:8000/health
# 返回: {"status":"healthy","timestamp":"...","storage_mode":"database"}

# 测试前端
curl -I http://localhost
# 返回: HTTP/1.1 200 OK

# 验证数据库表已由应用自动创建（应列出 designs/users 等表）
docker compose exec db psql -U plasmid -d plasmid_designer -c "\dt"

# 浏览器打开
# http://你的服务器IP → 看到设计界面
# http://你的服务器IP/docs → API 文档（前端 nginx 代理）
```

---

## 五、常见问题排查

### ❌ 问题 1: `docker compose build` 失败

```bash
# 查看具体错误
docker compose build --no-cache 2>&1 | tee build.log

# 常见原因:
# 1. "COPY failed" → 文件路径不对，确认项目结构完整
ls /root/plasmid-designer-v2/src/backend/app/main.py
ls /root/plasmid-designer-v2/src/frontend/package.json
ls /root/plasmid-designer-v2/data/vectors/

# 2. npm/pip 超时（国内机器）→ build-arg 切换国内镜像，无需改 Dockerfile:
# docker compose build #   --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple #   --build-arg NPM_REGISTRY=https://registry.npmmirror.com
```

### ❌ 问题 2: 容器启动后立即退出

```bash
# 查看退出日志
docker compose logs backend
docker compose logs frontend

# 查看退出码
docker compose ps -a
```

### ❌ 问题 3: 前端页面空白 / API 报 502

```bash
# 检查后端是否正常（默认 BACKEND_PORT=8000，改过则替换）
curl http://localhost:${BACKEND_PORT:-8000}/health

# 如果后端不正常
docker compose logs --tail=50 backend

# 检查 Nginx 是否能连通后端
docker compose exec frontend ping -c 3 backend

# 检查 Nginx 配置
docker compose exec frontend nginx -t
```

### ❌ 问题 4: 数据文件找不到 (FileNotFoundError)

```bash
# 检查容器内 data 目录
docker compose exec backend ls /app/data/vectors/
# 应该看到: pET-28a.yaml  pET-21a.yaml 等

# 如果为空，检查宿主机挂载
ls /root/plasmid-designer-v2/data/vectors/

# 确认 docker-compose.yml 中的 volume 配置
grep -A2 volumes /root/plasmid-designer-v2/deploy/docker/docker-compose.yml
```

### ❌ 问题 5: 端口被占用

```bash
# 查看 80 端口占用
ss -tlnp | grep :80

# 解决方式一: 释放端口
sudo kill <占用进程PID>

# 解决方式二: 改用其他端口
# 编辑 .env 文件
echo "FRONTEND_PORT=8080" >> .env
docker compose up -d
# 然后访问 http://你的服务器IP:8080
#
# 宿主机 8000 被其他应用占用时（后端启动报 address already in use）:
echo "BACKEND_PORT=18000" >> .env   # 后端仅绑 127.0.0.1，不影响前端经内部网络访问
```

### ❌ 问题 6: 数据库连接失败

```bash
# 检查数据库状态
docker compose exec db pg_isready -U plasmid

# 重启数据库
docker compose restart db

# 如果数据损坏，重建（⚠️ 会丢失数据）
docker compose down -v
docker compose up -d
```

### ❌ 问题 7: 内存不足

```bash
# 查看内存
free -h

# 增加 swap
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
```

---

## 六、常用运维命令

```bash
cd /root/plasmid-designer-v2/deploy/docker

# === 服务管理 ===
docker compose up -d            # 启动
docker compose down             # 停止（数据保留）
docker compose restart          # 重启
docker compose restart backend  # 仅重启后端

# === 日志 ===
docker compose logs -f                # 实时日志
docker compose logs -f backend        # 仅后端
docker compose logs --tail=100 backend # 最近100行

# === 状态 ===
docker compose ps              # 容器状态
docker stats                   # 资源使用

# === 调试 ===
docker compose exec backend bash     # 进入后端容器
docker compose exec db psql -U plasmid -d plasmid_designer  # 连接数据库

# === 数据库备份 ===
docker compose exec db pg_dump -U plasmid plasmid_designer > backup.sql

# === 完全重置（⚠️ 删除所有数据）===
docker compose down -v
docker compose build --no-cache
docker compose up -d
```

---

## 七、更新部署

代码更新后重新部署:

```bash
cd /root/plasmid-designer-v2

# 拉取最新代码
git pull

# 重新构建并启动
cd deploy/docker
docker compose build
docker compose up -d

# 查看日志确认正常
docker compose logs -f --since 5m
```

---

## 八、生产环境安全加固

### 8.1 修改默认密码

```bash
# 编辑 .env
nano /root/plasmid-designer-v2/deploy/docker/.env

# 必须修改:
# DB_PASSWORD=你的强密码
# SECRET_KEY=你的强密钥
```

### 8.2 防火墙

```bash
# 只开放必要端口，不要暴露数据库和 Redis
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
# 不要开放: 8000(后端), 5432(db), 6379(redis) —— 三者均已只绑 127.0.0.1
sudo ufw enable
```

### 8.3 HTTPS (可选)

```bash
# 使用 Let's Encrypt
sudo apt install certbot
sudo certbot certonly --standalone -d your-domain.com

# 将证书放到 deploy/docker/ssl/ 目录
mkdir -p /root/plasmid-designer-v2/deploy/docker/ssl
cp /etc/letsencrypt/live/your-domain.com/fullchain.pem ssl/cert.pem
cp /etc/letsencrypt/live/your-domain.com/privkey.pem ssl/key.pem

# 取消 nginx.conf 中 HTTPS 部分的注释
# 重启前端容器
docker compose restart frontend
```

---

## 九、绑定域名 / HTTPS（可选）

应用容器只监听内部端口，域名与 HTTPS 由宿主机 nginx 反向代理完成。示例为 Cloudflare
代理模式（证书用 Cloudflare Origin CA，浏览器信任由 Cloudflare 边缘提供）：

1. DNS 添加 A 记录（如 `plasmid` → 服务器 IP，开启橙色云代理）
2. 宿主机 nginx 新增 server 块（`/etc/nginx/sites-available/`），反代到前端容器端口：

```nginx
server {
    listen 443 ssl http2;
    server_name plasmid.example.com;
    ssl_certificate     /etc/ssl/xxx/origin.pem;   # 覆盖该子域的证书
    ssl_certificate_key /etc/ssl/xxx/origin.key;
    client_max_body_size 10m;

    location / {
        proxy_pass http://127.0.0.1:8080;          # FRONTEND_PORT 对应端口
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;   # 前置 nginx 需自行完成真实 IP 解析
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

3. `nginx -t && nginx -s reload`
4. `.env` 中把 `CORS_ORIGINS` 收紧为具体来源（如 `["https://plasmid.example.com"]`）后重启 backend

> 限流按真实客户端 IP 计：宿主机 nginx 解析真实 IP（Cloudflare 场景用
> `real_ip_header CF-Connecting-IP`）并写入 `X-Real-IP`；docker 前端 nginx 仅对
> 内网可信来源透传该头，直连防伪造。

---

## 目录结构参考

```
deploy/docker/
├── docker-compose.yml       # 服务编排
├── Dockerfile.backend       # 后端镜像 (Python 3.11)
├── Dockerfile.frontend      # 前端镜像 (Node 构建 + Nginx 运行)
├── nginx.conf               # Nginx 反向代理
├── requirements.backend.txt # Python 依赖（镜像唯一依赖来源，勿在 Dockerfile 手工罗列）
├── .env.example             # 环境变量模板
└── README.md                # 本文档
```
