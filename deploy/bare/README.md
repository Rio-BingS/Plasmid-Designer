# Ubuntu 直接部署指南

在 Ubuntu Focal (20.04) 上直接安装 Plasmid Designer，无需 Docker。

## 环境要求

- Ubuntu 20.04 LTS
- 2GB+ RAM（编译 scipy/numpy 需要）
- 10GB+ 磁盘空间
- root 权限（sudo）

## 一键安装

### SQLite 模式（推荐，零外部依赖）

```bash
# 将项目复制到服务器后执行
sudo bash deploy/bare/install.sh
```

### PostgreSQL 模式

```bash
sudo bash deploy/bare/install.sh --db postgresql
```

### 自定义安装目录

```bash
sudo bash deploy/bare/install.sh --project-dir /home/user/plasmid
```

安装脚本会自动完成：
- 安装系统依赖（gcc、make 等）
- 安装 Python 3.11（通过 deadsnakes PPA）
- 安装 Node.js 20（通过 NodeSource）
- 安装 Nginx
- 创建 venv 并安装 Python 依赖
- 构建前端
- 配置数据库（SQLite 或 PostgreSQL）
- 生成 `.env`（含随机 SECRET_KEY）
- 配置 systemd 服务和 Nginx 反向代理
- 健康检查验证

安装完成后访问 `http://<服务器IP>` 即可使用。

## 手动安装

如果需要更多控制，可以按以下步骤手动安装：

### 1. 安装系统依赖

```bash
sudo apt update
sudo apt install -y gcc g++ gfortran libpq-dev make git curl \
    software-properties-common apt-transport-https ca-certificates gnupg
```

### 2. 安装 Python 3.11

```bash
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3.11-dev python3.11-distutils
```

### 3. 安装 Node.js 20

```bash
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo bash -
sudo apt install -y nodejs
```

### 4. 安装 Nginx

```bash
sudo apt install -y nginx
```

### 5. 设置项目目录

```bash
# 复制项目到安装目录
sudo cp -r /path/to/plasmid-designer-v2 /opt/plasmid-designer

# 创建系统用户
sudo useradd -r -s /bin/false plasmid
```

### 6. 创建虚拟环境并安装依赖

```bash
sudo python3.11 -m venv /opt/plasmid-designer/venv
sudo /opt/plasmid-designer/venv/bin/pip install --upgrade pip

# 轻量依赖
sudo /opt/plasmid-designer/venv/bin/pip install \
    fastapi uvicorn python-multipart \
    sqlalchemy alembic \
    pydantic pydantic-settings python-dotenv loguru \
    passlib PyJWT email-validator \
    openpyxl reportlab httpx

# C 编译依赖（耗时较长）
sudo /opt/plasmid-designer/venv/bin/pip install \
    numpy pandas scipy matplotlib biopython primer3-py

# Git 依赖
sudo /opt/plasmid-designer/venv/bin/pip install "pydna>=5.5.0"
```

### 7. 构建前端

```bash
cd /opt/plasmid-designer/src/frontend
sudo npm install
sudo npm run build
```

### 8. 配置环境变量

```bash
# 从模板复制
sudo cp deploy/bare/.env.example /opt/plasmid-designer/.env

# 生成随机密钥并替换
SECRET=$(openssl rand -hex 32)
sudo sed -i "s/change_this_in_production/$SECRET/" /opt/plasmid-designer/.env
```

编辑 `/opt/plasmid-designer/.env`，根据需要调整配置。

### 9. 配置 systemd 服务

```bash
sudo cp deploy/bare/plasmid-backend.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now plasmid-backend
```

### 10. 配置 Nginx

```bash
sudo cp deploy/bare/nginx.conf /etc/nginx/sites-available/plasmid-designer
sudo ln -sf /etc/nginx/sites-available/plasmid-designer /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

### 11. 设置权限

```bash
sudo chown -R plasmid:plasmid /opt/plasmid-designer
sudo chmod 600 /opt/plasmid-designer/.env
```

### 12. 验证

```bash
curl http://localhost:8000/health
# 应返回: {"status":"healthy",...}
```

## 服务管理

```bash
# 查看状态
sudo systemctl status plasmid-backend

# 启动/停止/重启
sudo systemctl start plasmid-backend
sudo systemctl stop plasmid-backend
sudo systemctl restart plasmid-backend

# 查看日志
sudo journalctl -u plasmid-backend -f

# 查看最近 100 行日志
sudo journalctl -u plasmid-backend -n 100

# Nginx 日志
sudo tail -f /var/log/nginx/access.log
sudo tail -f /var/log/nginx/error.log
```

## 配置 HTTPS

使用 Let's Encrypt 免费证书：

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com
```

certbot 会自动修改 Nginx 配置，添加 SSL 证书和 HTTP→HTTPS 重定向。

证书自动续期已由 certbot timer 处理，可通过以下命令确认：

```bash
sudo systemctl status certbot.timer
sudo certbot renew --dry-run
```

## 更新部署

```bash
cd /opt/plasmid-designer

# 拉取最新代码
sudo -u plasmid git pull

# 更新 Python 依赖（如有变化）
sudo -u plasmid /opt/plasmid-designer/venv/bin/pip install -r src/backend/requirements.txt

# 重新构建前端（如有变化）
cd src/frontend
sudo -u plasmid npm install
sudo -u plasmid npm run build
cd /opt/plasmid-designer

# 重启服务
sudo systemctl restart plasmid-backend
```

## 卸载

```bash
sudo bash deploy/bare/install.sh --uninstall
```

这会停止服务、删除 systemd 配置、Nginx 配置、数据库（PostgreSQL 模式）、项目目录和系统用户。

## PostgreSQL 配置详情

使用 `--db postgresql` 安装时，脚本会自动：

1. 安装 PostgreSQL 12
2. 创建数据库用户 `plasmid` 和数据库 `plasmid_designer`
3. 生成随机密码并写入 `.env`
4. 初始化表结构

如需手动配置 PostgreSQL：

```bash
sudo apt install -y postgresql-12

sudo -u postgres createuser plasmid
sudo -u postgres createdb plasmid_designer -O plasmid
sudo -u postgres psql -c "ALTER USER plasmid PASSWORD 'your_password';"

# 更新 .env
# DATABASE_URL=postgresql://plasmid:your_password@localhost/plasmid_designer
```

## 故障排查

### 后端启动失败

```bash
# 查看详细日志
sudo journalctl -u plasmid-backend -n 50 --no-pager

# 手动测试启动
cd /opt/plasmid-designer/src/backend
sudo -u plasmid /opt/plasmid-designer/venv/bin/python -c "from app.main import app; print('OK')"

# 检查 .env 文件
sudo cat /opt/plasmid-designer/.env
```

### 前端页面空白

```bash
# 确认前端构建产物存在
ls /opt/plasmid-designer/src/frontend/dist/index.html

# 重新构建
cd /opt/plasmid-designer/src/frontend
sudo -u plasmid npm run build
```

### Nginx 502 Bad Gateway

后端服务未运行或端口不对：

```bash
sudo systemctl status plasmid-backend
curl http://127.0.0.1:8000/health
```

### Python 依赖安装失败

scipy/numpy 需要 C 编译器和 Fortran 编译器：

```bash
sudo apt install -y gcc g++ gfortran libpq-dev make
```

如 primer3-py 编译失败，确认 `make` 已安装。如 pydna 安装失败，确认 `git` 已安装。

### 权限问题

```bash
# 修复项目目录权限
sudo chown -R plasmid:plasmid /opt/plasmid-designer
sudo chmod 600 /opt/plasmid-designer/.env
```

### 端口冲突

默认使用 80（Nginx）和 8000（后端）。如需修改：

- 后端端口：编辑 `/etc/systemd/system/plasmid-backend.service` 中 `ExecStart` 的 `--port` 参数，以及 `/etc/nginx/sites-available/plasmid-designer` 中 `proxy_pass` 的端口
- Nginx 端口：编辑 `/etc/nginx/sites-available/plasmid-designer` 中 `listen` 的端口号

修改后：

```bash
sudo systemctl daemon-reload
sudo systemctl restart plasmid-backend
sudo nginx -t && sudo systemctl reload nginx
```
