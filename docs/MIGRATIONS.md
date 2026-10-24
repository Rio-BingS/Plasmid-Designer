# 数据库结构迁移（Alembic）

v2.18.1 起表结构变更走 Alembic；此前的 `init_db()`（`create_all` + 各表轻量
列迁移）**原样保留**：它负责新装环境的首次建表与存量库的列补齐，与 Alembic
并存不冲突（两者都幂等）。

## 首次接入（存量部署一次性）

已经用 `create_all` 建好表的环境不需要重放建表迁移，只对齐版本号：

```bash
cd src/backend
python -m alembic -c alembic.ini stamp head
```

## 日常流程：改了 models.py 之后

```bash
cd src/backend
# 1. 生成迁移脚本（对比模型与库结构；生成后必须人工审查，autogenerate
#    对列重命名会判成「删旧列 + 建新列」，有数据丢失风险，需手改成 rename）
python -m alembic -c alembic.ini revision --autogenerate -m "加 xxx 列"
# 2. 本地验证（regression 锁 tests/test_migrations.py 也会盯这个）
python -m alembic -c alembic.ini upgrade head
# 3. 连迁移脚本一起提交
```

部署升级时（后端启动前）：

```bash
python -m alembic -c alembic.ini upgrade head
```

## 数据库地址解析

优先级从高到低（见 `alembic/env.py`）：

1. 命令行 `-x db_url=postgresql://...`
2. 环境变量 `ALEMBIC_DATABASE_URL`
3. `app.config` 的 `settings.DATABASE_URL`（与后端运行时同一来源，含 `.env`）

Docker 部署在 backend 容器内执行：`docker compose exec backend python -m alembic -c alembic.ini upgrade head`。

## 回滚

```bash
python -m alembic -c alembic.ini downgrade -1     # 退一个版本
python -m alembic -c alembic.ini downgrade base   # 退到空库（会删表，慎用）
```

** downgrade 前先备份**（见 `deploy/BACKUP.md`）；含数据变换的迁移必须在
`upgrade()` 里可逆或在文档中写明不可逆。

## 约定与坑

- **迁移脚本一经合入 main 不得修改**：别的环境可能已经跑过。发现错误就
  追加新修订修复。
- SQLite 的 `render_as_batch` 已在 `env.py` 打开（改列会生成 batch 系列操作）；
  但 SQLite 仍不支持多数约束变更，复杂变更建议直接在 PostgreSQL 上验证。
- autogenerate 的基线是在 SQLite 方言上生成的；在 PostgreSQL 上跑
  `revision --autogenerate` 时可能报出类型表述差异（DATETIME vs TIMESTAMP
  等），逐条确认后再写入迁移，不要盲目接受。
- `tests/test_migrations.py` 是回归锁：**upgrade head 后模型与库结构必须
  零差异**，改了 models.py 而没有对应迁移时该测试失败。
- `alembic.ini` 保持 ASCII（configparser 按 locale 编码读取，zh-CN Windows
  是 GBK，中文注释会直接炸）。
