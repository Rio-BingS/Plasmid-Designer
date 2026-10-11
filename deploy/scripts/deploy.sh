#!/bin/bash
# Plasmid Designer Docker 部署脚本
# 在仓库任意位置运行均可（脚本自行切换到 deploy/docker/）

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Plasmid Designer Docker 部署 ==="
echo "项目根目录: $PROJECT_ROOT"
echo ""

# 检查 Docker
if ! command -v docker &> /dev/null; then
    echo "错误: Docker 未安装"
    echo "安装指南: https://docs.docker.com/engine/install/"
    exit 1
fi

# 检查 Docker Compose (支持 v1 和 v2)
if docker compose version &> /dev/null 2>&1; then
    COMPOSE_CMD="docker compose"
elif command -v docker-compose &> /dev/null; then
    COMPOSE_CMD="docker-compose"
else
    echo "错误: Docker Compose 未安装"
    echo "安装指南: https://docs.docker.com/compose/install/"
    exit 1
fi

echo "使用 Compose 命令: $COMPOSE_CMD"
echo ""

# 切换到 compose 文件所在目录（.env.example、docker-compose.yml 都在这里）
cd "$SCRIPT_DIR/../docker"

# 创建环境文件
if [ ! -f .env ]; then
    echo "创建 .env 文件..."
    # .env 存放全部密钥：以 0600 创建，避免写入随机值前后有本机其他用户可读的窗口
    (umask 077 && cp .env.example .env)
    # 生成随机 SECRET_KEY
    if command -v openssl &> /dev/null; then
        SECRET=$(openssl rand -hex 32)
        if [[ "$OSTYPE" == "darwin"* ]]; then
            sed -i '' "s/^SECRET_KEY=.*/SECRET_KEY=$SECRET/" .env
        else
            sed -i "s/^SECRET_KEY=.*/SECRET_KEY=$SECRET/" .env
        fi
        echo "已生成随机 SECRET_KEY"
    else
        echo "警告: openssl 不可用，请手动修改 .env 中的 SECRET_KEY"
    fi
fi

# 每次运行都收紧 .env 权限（含密钥）：修正旧版脚本/手动 cp 留下的 0644；
# 后续 sed -i 改写会保留文件权限
if ! chmod 600 .env; then
    echo "警告: 无法将 .env 权限设为 600，请由文件属主执行 chmod 600 $(pwd)/.env" >&2
fi

# 读取 .env 中某个键的值（不存在时为空）
env_value() {
    grep -E "^$1=" .env | tail -n 1 | cut -d= -f2- | tr -d '[:space:]' || true
}

# compose 项目名决定数据卷名（<项目名>_postgres-data）：
# 环境变量 > .env 中的 COMPOSE_PROJECT_NAME > compose 文件所在目录名
compose_project_name() {
    local name="${COMPOSE_PROJECT_NAME:-$(env_value COMPOSE_PROJECT_NAME)}"
    name="${name:-$(basename "$PWD")}"
    echo "$name" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9_-'
}

DB_VOLUME="$(compose_project_name)_postgres-data"

# PostgreSQL 只在数据卷首次初始化时读取 POSTGRES_PASSWORD；卷已存在时
# 换口令只会让 backend 的 DATABASE_URL 与库内口令不一致、连不上库
refuse_db_password_change() {
    local db_user
    db_user="$(env_value DB_USER)"
    db_user="${db_user:-plasmid}"
    cat >&2 <<MSG

错误: 数据卷 ${DB_VOLUME} 已存在，但 .env 中 DB_PASSWORD 为空或仍是旧版公开占位值。
PostgreSQL 只在数据卷首次初始化时设置口令，自动生成新口令会导致后端连不上库，
已拒绝替换（数据卷未改动）。处理后重新运行本脚本：

  在库内把口令改为新的随机值并写回 .env（在 $(pwd) 下执行）：
    NEW_DB_PASSWORD=\$(openssl rand -hex 16)
    DB_PASSWORD=placeholder ${COMPOSE_CMD} up -d db
    ${COMPOSE_CMD} exec db psql -U ${db_user} -d plasmid_designer \\
      -c "ALTER USER ${db_user} PASSWORD '\$NEW_DB_PASSWORD'"
    sed -i "s/^DB_PASSWORD=.*/DB_PASSWORD=\$NEW_DB_PASSWORD/" .env

  若数据卷初始化时用的是别的口令（非空、非占位值），也可直接把 .env 中
  DB_PASSWORD 设为那个值。旧版默认口令就是占位值 plasmid_secure_2026，
  它已公开，脚本不会沿用，请按上面的步骤改口令。

不要用 docker compose down -v 解决连接问题：它会删除全部数据库数据。
MSG
    exit 1
}

# 生成随机 DB/Redis 密码：.env 中为空或仍是旧版公开占位值时替换
# （DB 口令仅在 postgres 数据卷不存在时才生成，见 refuse_db_password_change）
set_random_secret() {
    local key="$1" placeholder="$2" guard="${3:-}" value
    if grep -qE "^${key}=(${placeholder})?$" .env; then
        if [ -n "$guard" ] && "$guard"; then
            refuse_db_password_change
        fi
        if ! command -v openssl &> /dev/null; then
            echo "错误: openssl 不可用，请手动在 .env 中设置 ${key}" >&2
            exit 1
        fi
        value=$(openssl rand -hex 16)
        if [[ "$OSTYPE" == "darwin"* ]]; then
            sed -i '' -E "s/^${key}=.*/${key}=${value}/" .env
        else
            sed -i -E "s/^${key}=.*/${key}=${value}/" .env
        fi
        echo "已生成随机 ${key}"
    elif ! grep -qE "^${key}=.+" .env; then
        echo "错误: .env 缺少 ${key}，请补上后重试" >&2
        exit 1
    fi
}

# 查不到卷之外的错误（daemon 不可达、无权限）一律中止：误判为「无卷」会先把
# 新口令写进 .env，下次运行时已无从察觉口令与数据卷不一致
db_volume_exists() {
    local err
    if err=$(docker volume inspect "$DB_VOLUME" 2>&1 > /dev/null); then
        return 0
    fi
    if echo "$err" | grep -qi "no such volume"; then
        return 1
    fi
    echo "错误: 无法确认数据卷 ${DB_VOLUME} 是否存在，未改动 DB_PASSWORD: ${err}" >&2
    exit 1
}

# Redis 先生成：其 requirepass 每次启动都随命令行生效，可安全替换；
# 也保证 DB 被拒绝时用户按提示执行 compose 命令不会因 REDIS_PASSWORD 为空报错
set_random_secret REDIS_PASSWORD plasmid_redis_2026
set_random_secret DB_PASSWORD plasmid_secure_2026 db_volume_exists

# 读取端口配置（.env 中可改）
BACKEND_PORT=$(grep -E "^BACKEND_PORT=" .env | cut -d= -f2 | tr -d '[:space:]')
BACKEND_PORT=${BACKEND_PORT:-8000}
FRONTEND_PORT=$(grep -E "^FRONTEND_PORT=" .env | cut -d= -f2 | tr -d '[:space:]')
FRONTEND_PORT=${FRONTEND_PORT:-80}
# compose 插值时进程环境变量优先于 .env；用独立变量名，不改写已导出的 FRONTEND_BIND
FRONTEND_BIND_ADDR="${FRONTEND_BIND:-$(env_value FRONTEND_BIND)}"
FRONTEND_BIND_ADDR="${FRONTEND_BIND_ADDR:-127.0.0.1}"

# 创建必要目录
echo "创建数据目录..."
mkdir -p "$PROJECT_ROOT/data/vectors" "$PROJECT_ROOT/data/codon_tables" "$PROJECT_ROOT/output" "$SCRIPT_DIR/../docker/ssl" 2>/dev/null || true

# 确认数据文件存在
if [ ! -f "$PROJECT_ROOT/data/vectors/pET-28a.yaml" ]; then
    echo "警告: 载体数据文件不存在，请确认 data/ 目录完整"
fi

# 构建镜像
echo ""
echo "构建 Docker 镜像（首次可能需要几分钟）..."
$COMPOSE_CMD build

# 启动服务
echo ""
echo "启动服务..."
if ! $COMPOSE_CMD up -d; then
    echo "错误: 服务启动失败（backend 健康检查含数据库连通性，口令不一致时会失败）" >&2
    echo "请查看日志: $COMPOSE_CMD logs backend db" >&2
    exit 1
fi

# 等待服务启动
echo ""
echo "等待服务启动..."
sleep 10

# 检查服务状态
echo "服务状态:"
$COMPOSE_CMD ps

# 健康检查：/health 含数据库连通性（"db":"ok"），库不通时返回 503
echo ""
echo "后端健康检查（含数据库连通性）..."
HEALTH_RETRIES=${HEALTH_RETRIES:-40}
HEALTH_URL="http://localhost:${BACKEND_PORT}/health"
for i in $(seq 1 "$HEALTH_RETRIES"); do
    if curl -fsS --max-time 10 "$HEALTH_URL" 2>/dev/null | grep -qE '"db" *: *"ok"'; then
        echo "后端服务与数据库连接正常"
        break
    fi
    if [ "$i" -eq "$HEALTH_RETRIES" ]; then
        echo "" >&2
        echo "错误: 后端健康检查未通过（约 $((HEALTH_RETRIES * 3)) 秒内 /health 未报告 db=ok）" >&2
        echo "  当前响应: $(curl -sS --max-time 10 "$HEALTH_URL" 2>&1 || true)" >&2
        echo "  查看日志: $COMPOSE_CMD logs backend db" >&2
        echo "  若为数据库认证失败：.env 中 DB_PASSWORD 须与数据卷 ${DB_VOLUME} 初始化时的口令一致，" >&2
        echo "  或在 db 容器内 ALTER USER 改口令后同步 .env（勿用 down -v，会删除数据）" >&2
        exit 1
    fi
    echo "  等待后端启动... ($i/$HEALTH_RETRIES)"
    sleep 3
done

echo ""
echo "=== 部署完成 ==="
echo ""
echo "访问地址:"
echo "  前端: http://localhost:${FRONTEND_PORT}（发布于 ${FRONTEND_BIND_ADDR}）"
echo "  后端(仅本机): http://localhost:${BACKEND_PORT}"
echo "  API文档: http://localhost:${FRONTEND_PORT}/docs"
echo ""
case "$FRONTEND_BIND_ADDR" in
    127.*|localhost|::1|"[::1]")
        echo "注意: 前端端口只绑定本机 ${FRONTEND_BIND_ADDR}，公网无法直连。"
        echo "  经宿主机 nginx 反代（推荐，见 deploy/docker/README.md 第九节）无需改动；"
        echo "  不用反代、要直接以 http://服务器IP 访问时，在 .env 设 FRONTEND_BIND=0.0.0.0"
        echo "  后重新运行本脚本（Docker 发布的端口不受 ufw 限制）"
        echo ""
        ;;
esac
echo "常用命令:"
echo "  查看日志:    $COMPOSE_CMD logs -f"
echo "  查看后端日志: $COMPOSE_CMD logs -f backend"
echo "  停止服务:    $COMPOSE_CMD down"
echo "  重启服务:    $COMPOSE_CMD restart"
echo "  重新构建:    $COMPOSE_CMD build --no-cache"
