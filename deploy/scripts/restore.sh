#!/bin/bash
# Plasmid Designer 数据库恢复脚本
#
# 用法:
#   bash deploy/scripts/restore.sh data/backups/plasmid_designer_20261024_120000.sql.gz
#   bash deploy/scripts/restore.sh data/backups/plasmid_designer_20261024_120000.db.gz
#
# PostgreSQL（.sql.gz）：恢复到 compose 的 db 容器。
# SQLite（.db.gz）：解压后替换库文件。**恢复前先停后端**，脚本会再次确认。
#
# 生成备份见 backup.sh；恢复演练建议每季度做一次（deploy/BACKUP.md）。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
COMPOSE_DIR="$PROJECT_ROOT/deploy/docker"

[ $# -eq 1 ] || { echo "用法: bash restore.sh <备份文件>" >&2; exit 2; }
BACKUP="$1"
[ -f "$BACKUP" ] || { echo "错误: 备份文件不存在: $BACKUP" >&2; exit 1; }

confirm() {
    read -r -p "$1 [yes/N] " reply
    [ "$reply" = "yes" ] || { echo "已取消"; exit 1; }
}

case "$BACKUP" in
    *.sql.gz)
        DB_USER="${DB_USER:-plasmid}"
        DB_NAME="plasmid_designer"
        if [ -f "$COMPOSE_DIR/.env" ]; then
            _u="$(grep -E '^DB_USER=' "$COMPOSE_DIR/.env" | tail -1 | cut -d= -f2- | tr -d '"' || true)"
            [ -n "$_u" ] && DB_USER="$_u"
        fi
        echo "=== PostgreSQL 恢复: $BACKUP → $DB_NAME ==="
        echo "注意: 恢复会覆盖数据库中的现有数据。"
        confirm "确认覆盖?"
        gunzip -c "$BACKUP" | docker compose -f "$COMPOSE_DIR/docker-compose.yml" exec -T db \
            psql -U "$DB_USER" -d "$DB_NAME" -q
        echo "恢复完成，建议重启后端: docker compose restart backend"
        ;;
    *.db.gz)
        DATA_DIR="${DATA_DIR:-$PROJECT_ROOT/data}"
        DB_FILE="$DATA_DIR/plasmid_designer.db"
        echo "=== SQLite 恢复: $BACKUP → $DB_FILE ==="
        echo "注意: 恢复会覆盖现有库文件，恢复前必须已停止后端服务。"
        confirm "已停止后端并确认覆盖?"
        cp "$DB_FILE" "$DB_FILE.bak_$(date +%Y%m%d_%H%M%S)" 2>/dev/null || true
        gunzip -c "$BACKUP" > "$DB_FILE"
        echo "恢复完成（旧库已留备份 $DB_FILE.bak_*），可启动后端。"
        ;;
    *)
        echo "错误: 无法识别备份类型（应为 .sql.gz 或 .db.gz）: $BACKUP" >&2
        exit 1
        ;;
esac
