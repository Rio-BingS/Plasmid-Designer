"""
Plasmid Designer - FastAPI 应用入口
精简版：仅包含 app 创建、中间件、路由挂载、启动事件
"""

import sys

# Windows GBK 控制台下 print 表情/中文会抛 UnicodeEncodeError，统一转 UTF-8 并容错
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # pragma: no cover
            pass

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime

from app.config import settings
from app.storage import STORAGE_MODE

# ==================== 生命周期 ====================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动/关停生命周期（替代已弃用的 @app.on_event）"""
    print(f"🧬 Plasmid Designer API v2.0.0")
    print(f"📦 Storage mode: {STORAGE_MODE}")

    # 无条件初始化数据库表：SQLite 幂等建表，保证本地默认模式下认证可用；
    # PostgreSQL 连接失败仅告警，不阻断主流程（设计主路径不依赖数据库）
    try:
        from app.database import init_db
        init_db()
    except Exception as e:
        print(f"⚠️ 数据库初始化失败（认证/持久化功能将不可用）: {e}")

    yield


# ==================== 创建应用 ====================

app = FastAPI(
    title="Plasmid Designer API",
    description="自动化质粒构建设计平台 API",
    version="2.0.0",
    lifespan=lifespan,
)

# ==================== 中间件 ====================

# CORS 来源接线 settings.CORS_ORIGINS（.env / 环境变量，支持逗号分隔或 JSON 数组），
# 生产环境务必配置具体域名而非通配
_cors_origins = settings.CORS_ORIGINS or ["*"]
_allow_all_origins = "*" in _cors_origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    # 「通配源 + 允许凭证」组合不符合 CORS 规范（浏览器会拒绝）；本项目认证走
    # Bearer Token、无 Cookie 凭证诉求，通配时关闭凭证模式
    allow_credentials=not _allow_all_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==================== 根路径 & 健康检查 ====================

@app.get("/")
async def root():
    """API 根路径"""
    return {
        "name": "Plasmid Designer API",
        "version": "2.0.0",
        "status": "running",
        "storage_mode": STORAGE_MODE
    }


@app.get("/health")
async def health_check():
    """健康检查"""
    return {"status": "healthy", "timestamp": datetime.now(), "storage_mode": STORAGE_MODE}


# ==================== 挂载路由 ====================

from app.routes.design_routes import router as design_router
from app.routes.batch_routes import router as batch_router
from app.routes.vector_routes import router as vector_router
from app.routes.codon_routes import router as codon_router
from app.auth.routes import router as auth_router
from app.cache_routes import router as cache_router
from app.rate_limit_routes import router as rate_limit_router
from app.analysis_routes import router as analysis_router

app.include_router(design_router)
app.include_router(batch_router)
app.include_router(vector_router)
app.include_router(codon_router)
app.include_router(auth_router)
app.include_router(cache_router)
app.include_router(rate_limit_router)
app.include_router(analysis_router)

# 速率限制中间件
from app.rate_limit import RateLimitMiddleware
app.add_middleware(RateLimitMiddleware)

# 认证状态中间件：解析 Bearer Token 写入 request.state.user（用户级限流依赖该字段）。
# add_middleware 后添加者为外层，因此必须位于 RateLimitMiddleware 之后才能先于其执行
from app.auth.middleware import AuthStateMiddleware
app.add_middleware(AuthStateMiddleware)

# 请求追踪 + 慢请求监控中间件与统一日志（此前已实现但从未接线）
from app.middleware import setup_middleware
setup_middleware(app)


# ==================== 直接运行 ====================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
