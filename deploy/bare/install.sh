#!/bin/bash
# Plasmid Designer v2 — Ubuntu Focal (20.04) 直接部署脚本
# 用法:
#   sudo bash install.sh                    # 默认 SQLite
#   sudo bash install.sh --db postgresql    # PostgreSQL 模式
#   sudo bash install.sh --uninstall        # 卸载
#   sudo bash install.sh --db postgresql --project-dir /home/user/plasmid  # 自定义路径

set -euo pipefail

# ==================== 配置 ====================

INSTALL_DIR="/opt/plasmid-designer"
DB_MODE="sqlite"
UNINSTALL=false
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# PostgreSQL 配置
PG_USER="plasmid"
PG_DB="plasmid_designer"
PG_PASSWORD=""

# ==================== 参数解析 ====================

while [[ $# -gt 0 ]]; do
    case $1 in
        --db)
            DB_MODE="$2"
            shift 2
            ;;
        --uninstall)
            UNINSTALL=true
            shift
            ;;
        --project-dir)
            INSTALL_DIR="$2"
            shift 2
            ;;
        -h|--help)
            echo "用法: sudo bash install.sh [选项]"
            echo ""
            echo "选项:"
            echo "  --db sqlite|postgresql   数据库模式 (默认: sqlite)"
            echo "  --project-dir PATH       安装目录 (默认: /opt/plasmid-designer)"
            echo "  --uninstall              卸载 Plasmid Designer"
            echo "  -h, --help               显示帮助"
            exit 0
            ;;
        *)
            echo "未知参数: $1"
            exit 1
            ;;
    esac
done

# ==================== 卸载 ====================

if $UNINSTALL; then
    echo "=== 卸载 Plasmid Designer ==="
    echo ""

    # 停止服务
    if systemctl is-active --quiet plasmid-backend 2>/dev/null; then
        echo "停止后端服务..."
        systemctl stop plasmid-backend
    fi
    systemctl disable plasmid-backend 2>/dev/null || true

    # 删除 systemd 服务文件
    if [ -f /etc/systemd/system/plasmid-backend.service ]; then
        echo "删除 systemd 服务文件..."
        rm -f /etc/systemd/system/plasmid-backend.service
        systemctl daemon-reload
    fi

    # 删除 Nginx 配置
    if [ -L /etc/nginx/sites-enabled/plasmid-designer ] || [ -f /etc/nginx/sites-enabled/plasmid-designer ]; then
        echo "删除 Nginx 配置..."
        rm -f /etc/nginx/sites-enabled/plasmid-designer
        rm -f /etc/nginx/sites-available/plasmid-designer
        systemctl reload nginx 2>/dev/null || true
    fi

    # 删除 PostgreSQL 数据库和用户
    if command -v psql &>/dev/null; then
        echo "删除 PostgreSQL 数据库和用户..."
        sudo -u postgres psql -c "DROP DATABASE IF EXISTS $PG_DB;" 2>/dev/null || true
        sudo -u postgres psql -c "DROP USER IF EXISTS $PG_USER;" 2>/dev/null || true
    fi

    # 删除项目目录
    if [ -d "$INSTALL_DIR" ]; then
        echo "删除项目目录: $INSTALL_DIR"
        rm -rf "$INSTALL_DIR"
    fi

    # 删除系统用户
    if id "plasmid" &>/dev/null; then
        echo "删除系统用户: plasmid"
        userdel plasmid 2>/dev/null || true
    fi

    echo ""
    echo "=== 卸载完成 ==="
    exit 0
fi

# ==================== 安装 ====================

echo "╔══════════════════════════════════════════════╗"
echo "║  Plasmid Designer v2 — Ubuntu 直接部署      ║"
echo "╚══════════════════════════════════════════════╝"
echo ""
echo "安装目录: $INSTALL_DIR"
echo "数据库模式: $DB_MODE"
echo ""

# ---------- 前置检查 ----------

if [[ $EUID -ne 0 ]]; then
    echo "错误: 需要 root 权限，请使用 sudo 运行"
    exit 1
fi

if [[ ! -f /etc/lsb-release ]] || ! grep -q "20.04" /etc/lsb-release 2>/dev/null; then
    echo "警告: 此脚本为 Ubuntu 20.04 设计，当前系统可能不兼容"
    echo "按 Enter 继续，Ctrl+C 取消..."
    read -r
fi

# ---------- Step 1: 系统依赖 ----------

echo ""
echo "[1/9] 安装系统依赖..."

export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y --no-install-recommends \
    gcc g++ gfortran \
    libpq-dev make git curl wget \
    software-properties-common apt-transport-https \
    ca-certificates gnupg

echo "  ✓ 系统依赖已安装"

# ---------- Step 2: Python 3.11 ----------

echo ""
echo "[2/9] 安装 Python 3.11..."

if python3.11 --version &>/dev/null; then
    echo "  ✓ Python 3.11 已安装: $(python3.11 --version)"
else
    # 添加 deadsnakes PPA
    add-apt-repository -y ppa:deadsnakes/ppa
    apt-get update

    apt-get install -y --no-install-recommends \
        python3.11 python3.11-venv python3.11-dev python3.11-distutils

    echo "  ✓ Python 3.11 已安装: $(python3.11 --version)"
fi

# ---------- Step 3: Node.js 20 ----------

echo ""
echo "[3/9] 安装 Node.js 20..."

if node --version &>/dev/null && [[ "$(node --version)" == v20.* ]]; then
    echo "  ✓ Node.js 20 已安装: $(node --version)"
else
    # NodeSource 20.x
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
    apt-get install -y nodejs

    echo "  ✓ Node.js 已安装: $(node --version), npm: $(npm --version)"
fi

# ---------- Step 4: Nginx ----------

echo ""
echo "[4/9] 安装 Nginx..."

if nginx -v &>/dev/null; then
    echo "  ✓ Nginx 已安装: $(nginx -v 2>&1)"
else
    apt-get install -y --no-install-recommends nginx
    echo "  ✓ Nginx 已安装"
fi

# ---------- Step 5: PostgreSQL（可选） ----------

if [[ "$DB_MODE" == "postgresql" ]]; then
    echo ""
    echo "[5/9] 配置 PostgreSQL..."

    if ! command -v psql &>/dev/null; then
        apt-get install -y --no-install-recommends postgresql-12 postgresql-client-12
    fi

    # 启动 PostgreSQL
    systemctl start postgresql 2>/dev/null || true
    systemctl enable postgresql 2>/dev/null || true

    # 生成随机密码
    PG_PASSWORD=$(openssl rand -base64 18 | tr -d '=/+' | head -c 24)

    # 创建用户和数据库
    sudo -u postgres psql -c "CREATE USER $PG_USER WITH PASSWORD '$PG_PASSWORD';" 2>/dev/null || \
        echo "  用户 $PG_USER 已存在，跳过创建"
    sudo -u postgres psql -c "CREATE DATABASE $PG_DB OWNER $PG_USER;" 2>/dev/null || \
        echo "  数据库 $PG_DB 已存在，跳过创建"
    sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE $PG_DB TO $PG_USER;"

    # 初始化表结构
    sudo -u postgres psql -d "$PG_DB" -f "$SCRIPT_DIR/../docker/init.sql" 2>/dev/null || true

    echo "  ✓ PostgreSQL 已配置"
    echo "    用户: $PG_USER"
    echo "    数据库: $PG_DB"
    echo "    密码: $PG_PASSWORD"
else
    echo ""
    echo "[5/9] 跳过 PostgreSQL（使用 SQLite）"
fi

# ---------- Step 6: 项目目录和用户 ----------

echo ""
echo "[6/9] 设置项目目录..."

# 创建系统用户
if ! id "plasmid" &>/dev/null; then
    useradd -r -s /bin/false plasmid
    echo "  ✓ 创建系统用户: plasmid"
fi

# 创建安装目录
if [[ "$INSTALL_DIR" == "/opt/plasmid-designer" ]] && [[ ! -d "$INSTALL_DIR" ]]; then
    # 从项目源码复制
    mkdir -p /opt
    cp -r "$PROJECT_ROOT" "$INSTALL_DIR"
    echo "  ✓ 项目文件已复制到 $INSTALL_DIR"
elif [[ ! -d "$INSTALL_DIR" ]]; then
    mkdir -p "$INSTALL_DIR"
    cp -r "$PROJECT_ROOT/"* "$INSTALL_DIR/"
    echo "  ✓ 项目文件已复制到 $INSTALL_DIR"
else
    echo "  ✓ 安装目录已存在: $INSTALL_DIR"
fi

# 创建 venv
echo "  创建 Python 虚拟环境..."
python3.11 -m venv "$INSTALL_DIR/venv"

echo "  ✓ 项目目录就绪"

# ---------- Step 7: 安装 Python 依赖 ----------

echo ""
echo "[7/9] 安装 Python 依赖..."

VENV_PIP="$INSTALL_DIR/venv/bin/pip"

# 升级 pip
$VENV_PIP install --upgrade pip setuptools wheel

# 分层安装：轻量依赖（不易失败）
echo "  安装轻量依赖..."
$VENV_PIP install --no-cache-dir --timeout=120 --retries=5 \
    fastapi uvicorn python-multipart \
    sqlalchemy alembic \
    pydantic pydantic-settings python-dotenv loguru \
    passlib PyJWT email-validator \
    openpyxl reportlab httpx

# SQLite 支持
$VENV_PIP install --no-cache-dir --timeout=60 \
    pysqlite3 2>/dev/null || true

# PostgreSQL 模式额外依赖
if [[ "$DB_MODE" == "postgresql" ]]; then
    echo "  安装 PostgreSQL 驱动..."
    $VENV_PIP install --no-cache-dir --timeout=120 \
        asyncpg psycopg2-binary
fi

# 分层安装：需要 C 编译的重量依赖
echo "  安装计算依赖（需要编译，请耐心等待）..."
$VENV_PIP install --no-cache-dir --timeout=300 --retries=5 \
    numpy pandas scipy \
    matplotlib biopython \
    primer3-py

# 分层安装：需要 git 的依赖
echo "  安装 git 依赖..."
$VENV_PIP install --no-cache-dir --timeout=120 --retries=5 \
    "pydna>=5.5.0"

echo "  ✓ Python 依赖安装完成"

# ---------- Step 8: 构建前端 ----------

echo ""
echo "[8/9] 构建前端..."

cd "$INSTALL_DIR/src/frontend"

if [ ! -d node_modules ]; then
    npm install
fi

npm run build

if [ -f "$INSTALL_DIR/src/frontend/dist/index.html" ]; then
    echo "  ✓ 前端构建完成"
else
    echo "  ✗ 前端构建失败，dist/index.html 不存在"
    exit 1
fi

cd "$INSTALL_DIR"

# ---------- Step 9: 配置服务和环境 ----------

echo ""
echo "[9/9] 配置服务和环境..."

# 生成 .env
if [ ! -f "$INSTALL_DIR/.env" ]; then
    SECRET_KEY=$(openssl rand -hex 32)

    if [[ "$DB_MODE" == "postgresql" ]]; then
        DATABASE_URL="postgresql://$PG_USER:$PG_PASSWORD@localhost/$PG_DB"
    else
        DATABASE_URL="sqlite:////$INSTALL_DIR/plasmid_designer.db"
    fi

    cat > "$INSTALL_DIR/.env" << ENVEOF
# Plasmid Designer 环境配置 — 由 install.sh 自动生成

# 存储模式
STORAGE_MODE=database

# 数据库
DATABASE_URL=$DATABASE_URL

# Redis（直接部署默认关闭）
REDIS_ENABLED=false

# 安全
SECRET_KEY=$SECRET_KEY
DEBUG=false
LOG_LEVEL=INFO
ENVEOF

    echo "  ✓ .env 已生成"
else
    echo "  ✓ .env 已存在，跳过"
fi

# 设置目录权限
chown -R plasmid:plasmid "$INSTALL_DIR"
chmod 750 "$INSTALL_DIR"
chmod 600 "$INSTALL_DIR/.env"

# 创建数据目录和输出目录
mkdir -p "$INSTALL_DIR/data/vectors" "$INSTALL_DIR/data/codon_tables"
mkdir -p /tmp/plasmid_designer/uploads /tmp/plasmid_designer/output
chown -R plasmid:plasmid /tmp/plasmid_designer

# 安装 systemd 服务
cp "$SCRIPT_DIR/plasmid-backend.service" /etc/systemd/system/

# 更新服务文件中的路径（如果安装目录不是默认值）
if [[ "$INSTALL_DIR" != "/opt/plasmid-designer" ]]; then
    sed -i "s|/opt/plasmid-designer|$INSTALL_DIR|g" /etc/systemd/system/plasmid-backend.service
fi

systemctl daemon-reload
systemctl enable plasmid-backend
systemctl start plasmid-backend

echo "  ✓ 后端服务已启动"

# 配置 Nginx
cp "$SCRIPT_DIR/nginx.conf" /etc/nginx/sites-available/plasmid-designer

# 更新 Nginx 配置中的路径
if [[ "$INSTALL_DIR" != "/opt/plasmid-designer" ]]; then
    sed -i "s|/opt/plasmid-designer|$INSTALL_DIR|g" /etc/nginx/sites-available/plasmid-designer
fi

# 创建 symlink
ln -sf /etc/nginx/sites-available/plasmid-designer /etc/nginx/sites-enabled/plasmid-designer

# 删除默认站点（如果存在）
if [ -L /etc/nginx/sites-enabled/default ]; then
    rm -f /etc/nginx/sites-enabled/default
fi

# 测试并重载 Nginx
nginx -t && systemctl reload nginx

echo "  ✓ Nginx 已配置"

# ==================== 健康检查 ====================

echo ""
echo "等待后端服务启动..."

for i in $(seq 1 20); do
    if curl -sf http://localhost:8000/health >/dev/null 2>&1; then
        echo "  ✓ 后端服务健康"
        break
    fi
    if [ $i -eq 20 ]; then
        echo "  ⚠ 健康检查超时，请查看日志: journalctl -u plasmid-backend"
    else
        sleep 3
    fi
done

# ==================== 完成 ====================

# 获取服务器 IP
SERVER_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║           部署完成！                         ║"
echo "╚══════════════════════════════════════════════╝"
echo ""
echo "访问地址:"
echo "  前端:    http://$SERVER_IP"
echo "  后端:    http://$SERVER_IP:8000"
echo "  API文档: http://$SERVER_IP:8000/docs"
echo ""

if [[ "$DB_MODE" == "postgresql" ]]; then
    echo "数据库连接信息:"
    echo "  URL:    postgresql://$PG_USER:****@localhost/$PG_DB"
    echo "  密码:   $PG_PASSWORD"
    echo "  （密码已保存在 $INSTALL_DIR/.env）"
    echo ""
fi

echo "服务管理命令:"
echo "  查看状态: systemctl status plasmid-backend"
echo "  查看日志: journalctl -u plasmid-backend -f"
echo "  重启服务: systemctl restart plasmid-backend"
echo "  停止服务: systemctl stop plasmid-backend"
echo ""
echo "更新部署:"
echo "  cd $INSTALL_DIR && git pull"
echo "  source venv/bin/activate && pip install -r src/backend/requirements.txt"
echo "  cd src/frontend && npm install && npm run build"
echo "  sudo systemctl restart plasmid-backend"
echo ""
echo "配置 HTTPS:"
echo "  sudo apt install certbot python3-certbot-nginx"
echo "  sudo certbot --nginx -d your-domain.com"
