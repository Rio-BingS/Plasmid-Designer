#!/bin/bash
# Plasmid Designer Docker 部署脚本
# 在 deploy/docker/ 目录下运行

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

# 切换到 docker 目录
cd "$SCRIPT_DIR"

# 创建环境文件
if [ ! -f .env ]; then
    echo "创建 .env 文件..."
    cp .env.example .env
    # 生成随机 SECRET_KEY
    if command -v openssl &> /dev/null; then
        SECRET=$(openssl rand -hex 32)
        if [[ "$OSTYPE" == "darwin"* ]]; then
            sed -i '' "s/change_this_in_production_use_strong_random_string/$SECRET/" .env
        else
            sed -i "s/change_this_in_production_use_strong_random_string/$SECRET/" .env
        fi
        echo "已生成随机 SECRET_KEY"
    else
        echo "警告: openssl 不可用，请手动修改 .env 中的 SECRET_KEY"
    fi
fi

# 生成随机 DB/Redis 密码（.env.example 中的默认值仅用于本地体验）
if grep -q "^DB_PASSWORD=plasmid_secure_2026$" .env; then
    sed -i "s/^DB_PASSWORD=plasmid_secure_2026$/DB_PASSWORD=$(openssl rand -hex 12)/" .env
    echo "已生成随机 DB_PASSWORD"
fi
if grep -q "^REDIS_PASSWORD=plasmid_redis_2026$" .env; then
    sed -i "s/^REDIS_PASSWORD=plasmid_redis_2026$/REDIS_PASSWORD=$(openssl rand -hex 12)/" .env
    echo "已生成随机 REDIS_PASSWORD"
fi

# 读取端口配置（.env 中可改）
BACKEND_PORT=$(grep -E "^BACKEND_PORT=" .env | cut -d= -f2 | tr -d '[:space:]')
BACKEND_PORT=${BACKEND_PORT:-8000}
FRONTEND_PORT=$(grep -E "^FRONTEND_PORT=" .env | cut -d= -f2 | tr -d '[:space:]')
FRONTEND_PORT=${FRONTEND_PORT:-80}

# 创建必要目录
echo "创建数据目录..."
mkdir -p "$PROJECT_ROOT/data/vectors" "$PROJECT_ROOT/data/codon_tables" "$PROJECT_ROOT/output" "$SCRIPT_DIR/ssl" 2>/dev/null || true

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
$COMPOSE_CMD up -d

# 等待服务启动
echo ""
echo "等待服务启动..."
sleep 10

# 检查服务状态
echo "服务状态:"
$COMPOSE_CMD ps

# 健康检查
echo ""
echo "后端健康检查..."
for i in $(seq 1 30); do
    if curl -sf "http://localhost:${BACKEND_PORT}/health" > /dev/null 2>&1; then
        echo "后端服务正常"
        break
    fi
    if [ $i -eq 30 ]; then
        echo "警告: 后端健康检查超时，请查看日志: $COMPOSE_CMD logs backend"
    else
        echo "  等待后端启动... ($i/30)"
        sleep 3
    fi
done

echo ""
echo "=== 部署完成 ==="
echo ""
echo "访问地址:"
echo "  前端: http://localhost:${FRONTEND_PORT}"
echo "  后端(仅本机): http://localhost:${BACKEND_PORT}"
echo "  API文档: http://localhost:${FRONTEND_PORT}/docs"
echo ""
echo "常用命令:"
echo "  查看日志:    $COMPOSE_CMD logs -f"
echo "  查看后端日志: $COMPOSE_CMD logs -f backend"
echo "  停止服务:    $COMPOSE_CMD down"
echo "  重启服务:    $COMPOSE_CMD restart"
echo "  重新构建:    $COMPOSE_CMD build --no-cache"
