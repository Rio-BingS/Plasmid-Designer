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

脚本会自动: 创建 `.env` → 生成随机 SECRET_KEY / DB_PASSWORD / REDIS_PASSWORD → 构建镜像 → 启动服务 → 健康检查

部署完成后访问:
- **前端**: 默认只发布在本机 `http://127.0.0.1:${FRONTEND_PORT:-80}`，供宿主机 nginx 反代
  （见第九节）；不用反代、要直接以 `http://你的服务器IP` 访问时，在 `.env` 设
  `FRONTEND_BIND=0.0.0.0` 后重跑脚本（Docker 发布的端口绕过 ufw，见 8.2）
- **API 文档**: `http://你的服务器IP/docs`（由前端 nginx 代理）
- **后端直连**: 仅本机 `http://localhost:${BACKEND_PORT:-8000}/docs`（绑定回环，不对公网暴露）

---

## 四、手动部署（详细步骤）

如果一键脚本遇到问题，可按以下步骤手动操作:

### 4.1 创建环境配置

```bash
cd /root/plasmid-designer-v2/deploy/docker

# 从模板创建 .env（含全部密钥，只给属主读写）
(umask 077 && cp .env.example .env)
chmod 600 .env

# 模板中三项密钥均为空，必须填入随机值（留空 compose 拒绝启动）
sed -i "s/^SECRET_KEY=.*/SECRET_KEY=$(openssl rand -hex 32)/" .env
sed -i "s/^DB_PASSWORD=.*/DB_PASSWORD=$(openssl rand -hex 16)/" .env
sed -i "s/^REDIS_PASSWORD=.*/REDIS_PASSWORD=$(openssl rand -hex 16)/" .env

# 查看配置
cat .env
```

**必须确认的配置项:**

```bash
# .env 文件内容
DB_USER=plasmid
DB_PASSWORD=<随机生成>              # 必填，留空 compose 拒绝启动（deploy.sh 自动生成）
REDIS_PASSWORD=<随机生成>           # 同上
SECRET_KEY=<64 位 hex>              # 必填；占位/过短/低熵值后端拒绝启动（deploy.sh 自动生成）
DEBUG=false
LOG_LEVEL=INFO
STORAGE_MODE=database
FRONTEND_PORT=80                   # 80 端口被占用时改为 8080 等
FRONTEND_BIND=127.0.0.1            # 前端发布地址；不经宿主机反代直接对外时改为 0.0.0.0
BACKEND_PORT=8000                  # 后端仅绑定 127.0.0.1；宿主机 8000 被占用时改为 18000 等
CORS_ORIGINS=http://localhost      # 同源反代无需改；前端另域部署时填显式来源，不要用 *
AUTH_COOKIE_SECURE=auto            # 只走 HTTPS 时建议改为 true（见第九节）
TRUSTED_PROXIES=127.0.0.1,::1,172.28.0.10     # 只信前端容器固定地址，勿写大网段
PLASMID_SUBNET=172.28.0.0/24       # compose 网络固定网段；与宿主机网段冲突时改，
PLASMID_GATEWAY=172.28.0.1         # 并同步 FRONTEND_IPV4、TRUSTED_PROXIES 与
FRONTEND_IPV4=172.28.0.10          # nginx.conf 中 geo 的网关地址
```

完整变量说明（管理员引导、邮件渠道等）见 `.env.example` 内注释。

### 4.2 构建镜像

```bash
# ⚠️ 必须在 deploy/docker/ 目录下执行
cd /root/plasmid-designer-v2/deploy/docker

# 构建所有镜像（首次约 5-10 分钟）
docker compose build

# 海外服务器用默认官方源即可；国内机器构建缓慢时用 build-arg 切换国内镜像:
#   docker compose build \
#     --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
#     --build-arg NPM_REGISTRY=https://registry.npmmirror.com
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
# 返回: {"status":"healthy","timestamp":"...","storage_mode":"database","db":"ok"}
# 数据库不可达时返回 HTTP 503 与 "db":"error"

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

# 2. npm/pip 超时（国内机器）→ build-arg 切换国内镜像（命令见 4.2），无需改 Dockerfile
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
# 然后由宿主机 nginx 反代到 127.0.0.1:8080；FRONTEND_BIND=0.0.0.0 时才可直接访问
# http://你的服务器IP:8080
#
# 宿主机 8000 被其他应用占用时（后端启动报 address already in use）:
echo "BACKEND_PORT=18000" >> .env   # 后端仅绑 127.0.0.1，不影响前端经内部网络访问
```

### ❌ 问题 6: 数据库连接失败

```bash
# 检查数据库状态与后端报错（/health 返回 503、"db":"error" 即后端连不上库）
docker compose exec db pg_isready -U plasmid
docker compose logs backend | grep -i "password authentication failed"

# 重启数据库
docker compose restart db
```

最常见的原因是 `.env` 中 `DB_PASSWORD` 与数据卷初始化时的口令不一致：PostgreSQL
只在数据卷首次创建时读取 `POSTGRES_PASSWORD`，之后改 `.env` 不会改库内口令。
**不要用 `docker compose down -v` 解决连接问题，它会删除全部数据库数据。**
在库内把口令同步为 `.env` 中的值即可（容器内本地连接无需口令）：

```bash
docker compose exec db psql -U plasmid -d plasmid_designer \
  -c "ALTER USER plasmid PASSWORD '<.env 中的 DB_PASSWORD>'"
docker compose restart backend
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

### 8.1 密钥与密码

`.env.example` 不再提供任何默认密码：`SECRET_KEY`、`DB_PASSWORD`、`REDIS_PASSWORD`
必须是随机值（deploy.sh 首次运行自动生成）。注意 PostgreSQL 只在数据卷首次初始化时设置
密码，已有数据卷后修改 `DB_PASSWORD` 需同时在库内 `ALTER USER`。deploy.sh 检测到
数据卷已存在而 `DB_PASSWORD` 为空或为旧版占位值时会拒绝自动生成并给出改口令步骤；
部署结束前会等待 `/health` 报告 `"db":"ok"`，未通过则以非零状态退出。
`.env` 含全部密钥，deploy.sh 每次运行都会把它设为 `600`；手动创建或从别处拷来的
`.env` 请自行 `chmod 600 .env`，并由运行 `docker compose` 的同一用户持有。

### 8.2 防火墙

```bash
# 只开放必要端口，不要暴露数据库和 Redis
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
# 不要开放: 8000(后端), 5432(db), 6379(redis) —— 三者均已只绑 127.0.0.1
sudo ufw enable
```

> ufw 管不住 Docker 发布的端口：Docker 在自己的 iptables 链里做 DNAT，发布在
> `0.0.0.0` 的端口即使 ufw 未放行也能从公网访问。因此前端端口默认用
> `FRONTEND_BIND=127.0.0.1` 只绑本机，由宿主机 nginx 对外；只有不使用前置、确实
> 要公网直连容器时才设 `FRONTEND_BIND=0.0.0.0`，此时访问控制只能靠云厂商安全组
> 或 `DOCKER-USER` 链规则。

### 8.3 HTTPS

推荐在宿主机 nginx 终止 HTTPS，见下文「绑定域名 / HTTPS」。容器内 `nginx.conf` 末尾的
443 server 块只是注释示例：compose 既未映射 443 端口也未挂载证书目录，直接取消注释不会生效。

---

## 九、绑定域名 / HTTPS（可选）

应用容器只监听内部端口，域名与 HTTPS 由宿主机 nginx 反向代理完成。示例为 Cloudflare
代理模式（证书用 Cloudflare Origin CA，浏览器信任由 Cloudflare 边缘提供）：

1. DNS 添加 A 记录（如 `plasmid` → 服务器 IP，开启橙色云代理）
2. 宿主机 nginx 新增 server 块（`/etc/nginx/sites-available/`），反代到前端容器端口：

```nginx
server {
    listen 80;
    server_name plasmid.example.com;
    return 301 https://$host$request_uri;          # 明文只做跳转，不反代
}

server {
    listen 443 ssl http2;
    server_name plasmid.example.com;
    ssl_certificate     /etc/ssl/xxx/origin.pem;   # 覆盖该子域的证书
    ssl_certificate_key /etc/ssl/xxx/origin.key;
    # HSTS 只在 HTTPS server 下发；确认整站可走 HTTPS 后再加 includeSubDomains
    add_header Strict-Transport-Security "max-age=31536000" always;
    # 与容器 nginx 的测序上传上限一致（批量交付文件夹常超百 MB），否则在这一层就被 413
    client_max_body_size 520m;

    location / {
        proxy_pass http://127.0.0.1:8080;          # FRONTEND_PORT 对应端口（FRONTEND_BIND 保持 127.0.0.1）
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;   # 前置 nginx 需自行完成真实 IP 解析
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme; # 即 https：容器 nginx 透传给后端
        proxy_read_timeout 600s;                   # 批量测序分析耗时较长
    }
}
```

3. `nginx -t && nginx -s reload`
4. 会话 Cookie 的 Secure 标记：容器 nginx 只信任来自 docker 网桥/本机的前置，透传其
   `X-Forwarded-Proto`（其余来源一律按本连接的 http 处理，防伪造），所以上面示例里的
   `proxy_set_header X-Forwarded-Proto $scheme;` 不能省，`AUTH_COOKIE_SECURE=auto`
   才能判出 https。对外只提供 HTTPS 的站点建议在 `.env` 中直接设 `AUTH_COOKIE_SECURE=true`，
   不依赖前置配置。前端另域部署时再把 `CORS_ORIGINS` 设为显式来源（如
   `https://plasmid.example.com`），然后 `docker compose up -d backend` 使新环境变量生效
   （`restart` 不会重读 `.env`）

> 登录态保存在 httpOnly 会话 Cookie（Path=/api，SameSite=Lax）中。前端与 API
> 同源（经 nginx 反代 `/api`）时无需任何 CORS 配置；前端另域部署时 `CORS_ORIGINS`
> 必须是显式来源——通配 `*` 下浏览器不会携带 Cookie，登录后仍表现为未登录。

> 限流按真实客户端 IP 计：宿主机 nginx 解析真实 IP（Cloudflare 场景用
> `real_ip_header CF-Connecting-IP`）并写入 `X-Real-IP`；docker 前端 nginx 只对
> compose 网络网关（`172.28.0.1`，即宿主机经发布端口进入的来源）透传该头，后端
> 只信前端容器固定地址（`172.28.0.10`）。经 docker-proxy 转发的外部连接（如未开启
> Docker IPv6 时的 IPv6 客户端）同样显示为网关地址，所以有宿主机前置 nginx 时请把
> `FRONTEND_PORT` 设为 `127.0.0.1:8080` 之类只绑本机，外部只能经宿主机 nginx 进入。
> 从旧版升级：把 `.env` 里旧的 `TRUSTED_PROXIES=...,172.16.0.0/12` 改为新值，并先
> `docker compose down` 再 `up -d`，让网络按固定网段重建。

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
