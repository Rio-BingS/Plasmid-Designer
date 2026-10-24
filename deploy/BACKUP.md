# 数据库备份与恢复

后端的用户数据全部在数据库里（设计结果、载体库、测序记录）与 `data/uploads/`
（ab1 上传文件）。**没有任何备份的部署，一次磁盘故障就等于全部数据清零。**

- 备份：`deploy/scripts/backup.sh`（自动判定 PostgreSQL / SQLite 模式）
- 恢复：`deploy/scripts/restore.sh <备份文件>`

## 备份内容与位置

| 模式 | 产物 | 说明 |
|---|---|---|
| Docker（PostgreSQL） | `plasmid_designer_<时间戳>.sql.gz` | `pg_dump` 纯 SQL 文本，gzip 压缩 |
| 裸机（SQLite） | `plasmid_designer_<时间戳>.db.gz` | 优先 `sqlite3 .backup` 在线一致性快照 |
| 两种模式 | `uploads_<时间戳>.tar.gz` | `data/uploads/` 存在时自动附带 |

默认输出到 `data/backups/`（在宿主机卷上，容器重建不丢）；每类保留最近
14 份，`--keep N` 可调，`--output DIR` 可指定输出目录（建议指向宿主机上
**另一块磁盘**或外部存储，否则机器盘坏时备份与数据一起消失）。

## 定时执行

### cron（裸机 / 宿主机）

```cron
# 每天凌晨 3:17 备份（错开整点），保留策略由脚本自管
17 3 * * * cd /opt/plasmid-designer && bash deploy/scripts/backup.sh >> data/backups/backup.log 2>&1
```

### systemd timer

```ini
# /etc/systemd/system/plasmid-backup.service
[Unit]
Description=Plasmid Designer 数据库备份

[Service]
Type=oneshot
User=plasmid
WorkingDirectory=/opt/plasmid-designer
ExecStart=/usr/bin/env bash deploy/scripts/backup.sh
```

```ini
# /etc/systemd/system/plasmid-backup.timer
[Unit]
Description=每天凌晨备份 Plasmid Designer

[Timer]
OnCalendar=*-*-* 03:17:00
Persistent=true

[Install]
WantedBy=timers.target
```

`sudo systemctl enable --now plasmid-backup.timer`。

## 恢复

```bash
# PostgreSQL（会覆盖现有数据，脚本要求输入 yes 确认）
bash deploy/scripts/restore.sh data/backups/plasmid_designer_20261024_031700.sql.gz
docker compose -f deploy/docker/docker-compose.yml restart backend

# SQLite（先停后端）
sudo systemctl stop plasmid-backend   # 或对应的服务名
bash deploy/scripts/restore.sh data/backups/plasmid_designer_20261024_031700.db.gz
sudo systemctl start plasmid-backend
```

`uploads_*.tar.gz` 手动解回 `data/`：`tar -xzf uploads_*.tar.gz -C data/`。

## 恢复演练

备份只有能恢复才算数。建议每季度：

1. 在测试机/临时目录起一个干净的空库；
2. 挑一份最近的备份跑 `restore.sh`；
3. 启动后端、登录、抽查一份设计结果能打开；
4. 记录演练日期与耗时（本文档底部维护）。

| 日期 | 环境 | 结果 |
|---|---|---|
| | | |
