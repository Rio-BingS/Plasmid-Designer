# Plasmid Designer Backend

FastAPI 后端服务。功能说明、完整 API 一览与环境变量见根目录 [README.md](../../../README.md)。

## 项目架构

```
app/
├── main.py                  # 入口（仅组装：CORS/中间件/路由/生命周期）
├── config.py                # 配置（环境变量 / .env）+ sys.path
├── design_service.py        # 单任务与批量共用的设计流水线
├── vector_import.py         # 外部/在线序列 → 载体库 YAML（导入落盘形状唯一入口）
├── routes/                  # 路由模块
│   ├── design_routes.py     # 设计任务 API
│   ├── batch_routes.py      # 批量设计 API
│   ├── vector_routes.py     # 载体库 API
│   ├── online_source_routes.py  # 在线来源导入（SnapGene 在线库/Ensembl/Addgene）
│   ├── codon_routes.py      # 密码子表 API
│   ├── sequencing_routes.py # Sanger 测序分析 API
│   └── models.py            # 共享 Pydantic 模型
├── storage/                 # 双模式存储（STORAGE_MODE 工厂 + 内存/数据库实现）
├── auth/                    # 认证
│   ├── jwt_auth.py          # JWT、会话 Cookie、认证依赖
│   ├── middleware.py        # 请求用户解析（供限流）+ CSRF 校验
│   ├── bootstrap.py         # 管理员引导（ADMIN_EMAIL/ADMIN_PASSWORD）
│   └── routes.py            # 认证路由 (/api/auth)
├── database/                # ORM + CRUD
│   ├── models.py            # SQLAlchemy 模型
│   └── crud.py              # CRUD 操作
├── admin_routes.py          # 站点设置 / 用户管理 API（仅管理员）
├── gating.py / features.py / site_settings.py  # 功能矩阵门控与站点开关
├── sequencing_store.py      # 测序分析记录落库
├── sequencing_batch.py      # 批量测序归组与逐组分析（与离线脚本共用）
├── sequencing_report.py     # 批量测序整理包构建（与离线脚本共用）
├── mailer.py                # 邮箱验证码发送渠道
├── cache.py / cache_routes.py            # 缓存与管理 API
├── rate_limit.py / rate_limit_routes.py  # 速率限制与管理 API
└── analysis_routes.py       # 序列分析 & 导出 API
```

## 安装

```bash
# 开发 / 运行测试：运行时锁 + 开发锁一起按哈希安装
pip install --require-hashes -r requirements.txt -r requirements-dev.txt
# 只部署运行（不跑测试）时去掉 -r requirements-dev.txt
```

## 运行

`SECRET_KEY` 必填（`openssl rand -hex 32`，写入 `src/backend/.env` 或环境变量），
未设置、占位、过短或低熵一律拒绝启动。以下命令均在 `src/backend` 目录执行：

```bash
# 开发模式 (默认数据库模式，SQLite)
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

# 内存模式 (无需数据库)
STORAGE_MODE=memory uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

# 直接运行（等价于 uvicorn，监听 0.0.0.0:8000）
python -m app.main
```

## 存储模式

| 模式 | 环境变量 | 适用场景 |
|------|----------|----------|
| database (默认) | `STORAGE_MODE=database` | Docker/生产，数据持久化 |
| memory | `STORAGE_MODE=memory` | 测试/演示，重启丢失 |

## API 文档

启动后访问:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc
