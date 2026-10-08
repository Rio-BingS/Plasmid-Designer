# Plasmid Designer Backend

FastAPI 后端服务

## 项目架构

```
app/
├── main.py                 # 入口 (~97行)
├── config.py                # 配置 + sys.path
├── routes/                  # 路由模块
│   ├── design_routes.py     # 设计任务 API
│   ├── batch_routes.py      # 批量设计 API
│   ├── vector_routes.py     # 载体库 API
│   ├── codon_routes.py      # 密码子表 API
│   └── models.py            # 共享 Pydantic 模型
├── storage/                 # 双模式存储
│   ├── __init__.py          # STORAGE_MODE 工厂
│   ├── base.py              # 抽象基类
│   ├── memory_store.py      # 内存实现
│   └── db_store.py          # 数据库实现
├── auth/                    # JWT 认证
│   ├── jwt_auth.py          # JWT 工具 + 认证依赖
│   └── routes.py            # 认证路由 (/api/auth)
├── database/                # ORM + CRUD
│   ├── models.py            # SQLAlchemy 模型
│   └── crud.py              # CRUD 操作
├── cache_routes.py          # 缓存管理 API
├── rate_limit_routes.py     # 速率限制 API
└── analysis_routes.py       # 序列分析 & 导出 API
```

## 安装

```bash
pip install -r requirements.txt
```

## 运行

```bash
# 开发模式 (默认数据库模式)
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 内存模式 (无需数据库)
STORAGE_MODE=memory uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 直接运行
python main.py
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
