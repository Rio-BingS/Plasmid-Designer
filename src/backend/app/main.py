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

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime

from app.config import settings
from app.storage import STORAGE_MODE

_logger = logging.getLogger(__name__)

# ==================== 生命周期 ====================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动/关停生命周期（替代已弃用的 @app.on_event）"""
    print(f"🧬 Plasmid Designer API v{settings.APP_VERSION}")
    print(f"📦 Storage mode: {STORAGE_MODE}")

    # JWT 密钥治理（终审 B-04）：导入 jwt_auth 时已校验；这里对运行期的
    # settings 再校验一次（与 DEBUG 无关），占位/过短/低熵密钥拒绝启动
    from app.config import validate_secret_key
    validate_secret_key(settings.SECRET_KEY)

    # 无条件初始化数据库表：SQLite 幂等建表，保证本地默认模式下认证可用；
    # PostgreSQL 连接失败仅告警，不阻断主流程（设计主路径不依赖数据库）
    try:
        from app.database import init_db
        init_db()
    except Exception as e:
        print(f"⚠️ 数据库初始化失败（认证/持久化功能将不可用）: {e}")

    # 匿名测序记录的明文访问令牌改存摘要（幂等，失败仅留痕）
    try:
        from app.sequencing_store import migrate_plaintext_tokens
        migrate_plaintext_tokens()
    except Exception as e:
        print(f"⚠️ 测序记录令牌迁移失败: {e}")

    # 管理员引导：配置了 ADMIN_EMAIL/ADMIN_PASSWORD 且该邮箱未注册时创建管理员
    try:
        from app.auth.bootstrap import bootstrap_admin
        action = bootstrap_admin()
        if action == "created":
            print(f"✅ 已创建管理员账号 {settings.ADMIN_EMAIL}")
        elif action == "promoted":
            print(f"✅ 已将现有用户 {settings.ADMIN_EMAIL} 提升为管理员")
    except Exception as e:
        print(f"⚠️ 管理员引导失败: {e}")

    # Addgene 用户自备会话只适合单用户自托管：多用户部署时告警（审查 M2）
    try:
        from app.online_import import log_addgene_shared_session_warning
        log_addgene_shared_session_warning()
    except Exception as e:  # pragma: no cover - 告警失败不阻断启动
        print(f"⚠️ Addgene 会话检查失败: {e}")

    # 批量测序分块上传会话：启动时清理上次残留的暂存目录，之后周期清理过期会话
    from app.routes.sequencing_session_routes import sweeper_loop
    sweeper = asyncio.create_task(sweeper_loop())

    yield

    sweeper.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await sweeper


# ==================== 创建应用 ====================

def api_docs_kwargs(enabled: bool) -> dict:
    """ENABLE_API_DOCS=false 时三个文档端点一并关闭（返回 404）"""
    if enabled:
        return {}
    return {"docs_url": None, "redoc_url": None, "openapi_url": None}


app = FastAPI(
    title="Plasmid Designer API",
    description="自动化质粒构建设计平台 API",
    version=settings.APP_VERSION,
    lifespan=lifespan,
    **api_docs_kwargs(settings.ENABLE_API_DOCS),
)

# ==================== 中间件 ====================

# （CORS 中间件在本文件末尾、所有中间件之后添加——add_middleware
# 后添加者为外层，CORS 必须位于最外层才能给 429/5xx 响应补 CORS 头，
# 否则浏览器只看到 Network Error 而非真实状态码，见终审 C-05）


# ==================== 根路径 & 健康检查 ====================

@app.get("/")
async def root():
    """API 根路径"""
    return {
        "name": "Plasmid Designer API",
        "version": settings.APP_VERSION,
        "status": "running",
        "storage_mode": STORAGE_MODE
    }


# /health 查库超时（秒）：库不可达时尽快返回 503，不拖住部署脚本/compose 健康检查
HEALTH_DB_TIMEOUT = 3.0


def _db_ping() -> None:
    """对当前引擎执行一次 SELECT 1（SQLite/PostgreSQL 通用）"""
    from sqlalchemy import text
    from app import database

    with database.engine.connect() as conn:
        conn.execute(text("SELECT 1"))


@app.get("/health")
async def health_check():
    """健康检查：含数据库连通性，库不可用时返回 503。

    init_db 失败只告警不阻断启动，若 /health 不查库，口令与数据卷不一致等
    故障会被部署脚本/compose 误判为部署成功（审查 03 H1）"""
    try:
        await asyncio.wait_for(run_in_threadpool(_db_ping), timeout=HEALTH_DB_TIMEOUT)
        db_status = "ok"
    except Exception as e:  # 含超时；细节只进日志，不回给调用方
        _logger.warning("健康检查：数据库不可用: %s", type(e).__name__)
        db_status = "error"
    body = {
        "status": "healthy" if db_status == "ok" else "unhealthy",
        "timestamp": datetime.now(),
        "storage_mode": STORAGE_MODE,
        "db": db_status,
    }
    if db_status != "ok":
        return JSONResponse(status_code=503, content=jsonable_encoder(body))
    return body


# ==================== 挂载路由 ====================

from fastapi import Depends

from app.routes.design_routes import router as design_router
from app.routes.batch_routes import router as batch_router
from app.routes.vector_routes import router as vector_router
from app.routes.online_source_routes import router as online_source_router
from app.routes.codon_routes import router as codon_router
from app.auth.routes import router as auth_router
from app.cache_routes import router as cache_router
from app.rate_limit_routes import router as rate_limit_router
from app.analysis_routes import router as analysis_router
from app.routes.map_routes import router as map_router
from app.routes.map_online_routes import router as map_online_router
from app.routes.sequencing_routes import router as sequencing_router
from app.routes.sequencing_session_routes import router as sequencing_session_router
from app.admin_routes import router as admin_router
from app.gating import require_feature, require_any_feature

# 功能门控：site_settings 中各层级功能开关在此落到 API（管理员不受限）；
# 前端导航/路由已按 site-config 隐藏，此处兜底防绕过前端直连
app.include_router(design_router, dependencies=[Depends(require_feature("design"))])
app.include_router(batch_router, dependencies=[Depends(require_feature("batch"))])
# 在线来源导入（SnapGene 在线库 / Ensembl / Addgene）：注册在 vector_router 之前，
# 避免 /api/vectors/{vector_id} 这类单段模板在匹配顺序上先截获子路径
app.include_router(online_source_router, dependencies=[Depends(require_feature("vectors"))])
app.include_router(vector_router, dependencies=[Depends(require_feature("vectors"))])
app.include_router(codon_router, dependencies=[Depends(require_feature("codon"))])
app.include_router(auth_router)
app.include_router(cache_router)
app.include_router(rate_limit_router)
app.include_router(analysis_router, dependencies=[Depends(require_feature("analysis"))])
app.include_router(map_router, dependencies=[Depends(require_feature("map"))])
# 图谱页的在线来源导入（SnapGene 在线库 / Ensembl）：同样挂 map 开关；
# 集合/索引缓存与载体库侧共用（app/online_import）
app.include_router(map_online_router, dependencies=[Depends(require_feature("map"))])
# 测序路由拆分门控：单样品分析 / 批量分析各自独立开关，分析记录的查看/导出/
# 删除为两者共用（任一开放即可）；各端点的专属开关在 sequencing_routes 内落地
app.include_router(
    sequencing_router,
    dependencies=[Depends(require_any_feature("sequencing", "sequencing_batch"))],
)
# 批量测序分块上传会话（各端点自带 sequencing_batch 门控）
app.include_router(sequencing_session_router)
app.include_router(admin_router)

# 速率限制中间件
from app.rate_limit import RateLimitMiddleware
app.add_middleware(RateLimitMiddleware)

# 认证状态中间件：解析 Bearer Token 写入 request.state.user（用户级限流依赖该字段）。
# add_middleware 后添加者为外层，因此必须位于 RateLimitMiddleware 之后才能先于其执行
from app.auth.middleware import AuthStateMiddleware
app.add_middleware(AuthStateMiddleware)

# CSRF：会话 Cookie 认证的 POST/PUT/PATCH/DELETE 须带 X-Requested-With 头
# （Bearer 头请求不检查）。位于限流之外：被拒的伪造请求不消耗业务配额
from app.auth.middleware import CsrfMiddleware
app.add_middleware(CsrfMiddleware)

# 请求追踪 + 慢请求监控中间件与统一日志（此前已实现但从未接线）
from app.middleware import setup_middleware
setup_middleware(app)

# CORS 中间件最后添加 = 最外层（终审 C-05）：429/5xx 响应必须带 CORS 头，
# 否则前端 fetch 只看到 Network Error；预检 OPTIONS 也不再被内层限流计费。
# 来源接线 settings.cors_origins_list（.env / 环境变量，支持 "*"、逗号分隔或
# JSON 数组）；生产环境务必配置具体域名而非通配
_cors_origins = settings.cors_origins_list
_allow_all_origins = "*" in _cors_origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    # 浏览器登录态走 httpOnly 会话 Cookie：前端另域部署时必须配置显式来源
    # 才能携带凭证；「通配源 + 允许凭证」不符合 CORS 规范（浏览器会拒绝），
    # 通配时关闭凭证模式（同源部署不受影响，脚本客户端用 Bearer 头）
    allow_credentials=not _allow_all_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==================== 直接运行 ====================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
