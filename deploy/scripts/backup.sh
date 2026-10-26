#!/bin/bash
# Plasmid Designer 数据库备份脚本
#
# 用法:
#   bash deploy/scripts/backup.sh                        # 备份到默认目录 data/backups/
#   bash deploy/scripts/backup.sh --output /path/dir     # 指定输出目录
#   bash deploy/scripts/backup.sh --keep 30              # 保留最近 30 份（默认 14）
#
# 模式自动判定：
#   - DATABASE_URL 指向 postgresql（或 deploy/docker/.env 配置了 DB_PASSWORD）
#     → 通过 docker compose exec 调 pg_dump 导出（gzip 压缩）
#   - 否则视为 SQLite 裸机部署 → 优先用 sqlite3 .backup（在线一致性快照），
#     没有 sqlite3 时退回文件拷贝（后端运行中拷贝可能不一致，会给出警告）
#
# 定时执行见 deploy/BACKUP.md（cron / systemd timer 示例）；恢复见 restore.sh。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
COMPOSE_DIR="$PROJECT_ROOT/deploy/docker"

OUTPUT_DIR="$PROJECT_ROOT/data/backups"
KEEP=14
STAMP="$(date +%Y%m%d_%H%M%S)"

while [ $# -gt 0 ]; do
    case "$1" in
        --output) OUTPUT_DIR="$2"; shift 2 ;;
        --keep)   KEEP="$2"; shift 2 ;;
        *) echo "未知参数: $1（支持 --output DIR / --keep N）" >&2; exit 2 ;;
    esac
done

mkdir -p "$OUTPUT_DIR"

# ---- 判定数据库模式 -------------------------------------------------------
# DATABASE_URL 优先取环境变量，其次 deploy/docker/.env（compose 部署的配置处）
DB_URL="${DATABASE_URL:-}"
ENV_FILE="$COMPOSE_DIR/.env"
if [ -z "$DB_URL" ] && [ -f "$ENV_FILE" ]; then
    DB_URL="$(grep -E '^DATABASE_URL=' "$ENV_FILE" | tail -1 | cut -d= -f2- | tr -d '"' || true)"
fi
# .env 里 DB_PASSWORD 存在即视为 compose PostgreSQL 部署（DATABASE_URL 由 compose 拼装）
if [ -z "$DB_URL" ] && [ -f "$ENV_FILE" ] && grep -qE '^DB_PASSWORD=.+' "$ENV_FILE"; then
    DB_URL="postgresql:compose"
fi

# DATA_DIR 在两种模式都要用（PostgreSQL 模式的 uploads 打包也读它），
# 必须在分支前初始化——set -u 下引用未定义变量会直接退出
DATA_DIR="${DATA_DIR:-$PROJECT_ROOT/data}"

if [ -n "$DB_URL" ] && echo "$DB_URL" | grep -q '^postgresql'; then
    # ---- PostgreSQL（Docker compose） ----
    DB_USER="${DB_USER:-plasmid}"
    DB_NAME="plasmid_designer"
    if [ -f "$ENV_FILE" ]; then
        _u="$(grep -E '^DB_USER=' "$ENV_FILE" | tail -1 | cut -d= -f2- | tr -d '"' || true)"
        [ -n "$_u" ] && DB_USER="$_u"
    fi
    OUT="$OUTPUT_DIR/plasmid_designer_${STAMP}.sql.gz"
    echo "=== PostgreSQL 备份 → $OUT ==="
    if [ -x "$(command -v docker)" ] && docker compose -f "$COMPOSE_DIR/docker-compose.yml" ps -q db 2>/dev/null | grep -q .; then
        docker compose -f "$COMPOSE_DIR/docker-compose.yml" exec -T db \
            pg_dump -U "$DB_USER" -d "$DB_NAME" --no-owner | gzip > "$OUT"
    else
        echo "错误: db 容器未在运行（cd $COMPOSE_DIR && docker compose up -d db）" >&2
        exit 1
    fi
else
    # ---- SQLite（裸机部署） ----
    DB_FILE="$DATA_DIR/plasmid_designer.db"
    [ -f "$DB_FILE" ] || { echo "错误: 未找到 SQLite 库 $DB_FILE" >&2; exit 1; }
    OUT="$OUTPUT_DIR/plasmid_designer_${STAMP}.db.gz"
    echo "=== SQLite 备份 → $OUT ==="
    if command -v sqlite3 > /dev/null 2>&1; then
        sqlite3 "$DB_FILE" ".backup '$DATA_DIR/.backup_tmp.db'"
        gzip -c "$DATA_DIR/.backup_tmp.db" > "$OUT"
        rm -f "$DATA_DIR/.backup_tmp.db"
    else
        echo "警告: 未安装 sqlite3，退回文件拷贝；后端运行中拷贝可能不一致，" >&2
        echo "       建议先停后端或安装 sqlite3（apt install sqlite3）。" >&2
        gzip -c "$DB_FILE" > "$OUT"
    fi
fi

# 附件/上传目录（测序 ab1 等）单独打包，可选
if [ -d "$DATA_DIR/uploads" ]; then
    tar -czf "$OUTPUT_DIR/uploads_${STAMP}.tar.gz" -C "$DATA_DIR" uploads
    echo "已附带上传目录备份: uploads_${STAMP}.tar.gz"
fi

SIZE="$(du -h "$OUT" | cut -f1)"
echo "备份完成: $OUT ($SIZE)"

# ---- 保留策略 -------------------------------------------------------------
# 按前缀分组各自保留最近 KEEP 份（db 与 uploads 互不挤占）。
# 某前缀无备份时 ls 以 2 退出，pipefail+set -e 会把「备份成功」误判为失败——
# 整条管线 || true 兜底（清不动旧文件不该让本次备份报错）
for prefix in plasmid_designer_ uploads_; do
    ls -1t "$OUTPUT_DIR"/${prefix}*.gz 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do
        rm -f "$old"
        echo "清理旧备份: $old"
    done || true
done
