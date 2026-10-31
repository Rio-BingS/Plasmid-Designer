"""设计任务路由 — 统一走 DesignService"""

import hashlib
import logging
import uuid
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from starlette.concurrency import run_in_threadpool

from app.auth.jwt_auth import User, get_current_user
from app.cache import cache
from app.filename import content_disposition, sanitize_filename
from app.design_service import (
    generate_genbank_from_result,
    run_design,
)
from app.routes.models import (
    DesignRequest,
    DesignResult,
    DesignStatus,
    MapDocument,
    PrimerInfo,
    RestrictionPreviewRequest,
)
from app.storage import get_design_store
from app.mirror_store import BoundedMirror

router = APIRouter(prefix="/api/design", tags=["design"])

logger = logging.getLogger(__name__)

# 兼容旧代码/批量路由：内存镜像 + 存储层双写。
# 镜像带上限 + TTL（此前无界：匿名持续提交即可撑大内存，第二轮审计 P2）；
# 淘汰只清镜像，存储层仍是权威，下次读取自动回源
designs_db: Dict[str, DesignResult] = BoundedMirror(max_items=500)


def _persist(result: DesignResult) -> None:
    designs_db[result.design_id] = result
    try:
        store = get_design_store()
        store.save(result.design_id, result.model_dump(mode="json"))
    except Exception:
        # 存储层失败不阻断主流程（如 DB 未配置），但结果重启即丢——必须留痕
        logger.warning("设计 %s 落库失败（结果仅存内存，重启丢失）",
                       result.design_id, exc_info=True)
    # 完成态写入响应缓存（24h TTL）；中间态不缓存，避免轮询读到过期状态
    if result.status == DesignStatus.COMPLETED:
        try:
            cache.cache_design_result(result.design_id, result.model_dump(mode="json"))
        except Exception:
            logger.warning("设计 %s 响应缓存写入失败", result.design_id, exc_info=True)


def _load(design_id: str) -> DesignResult | None:
    result = designs_db.get(design_id)  # get() 持锁；in + [] 两步之间条目可能被淘汰（BoundedMirror 竞态）
    if result is not None:
        return result
    try:
        store = get_design_store()
        data = store.get(design_id)
        if data:
            result = DesignResult.model_validate(data)
            designs_db[design_id] = result
            return result
    except Exception as e:
        # 终审 D-01：此处裸 pass 曾把 model_validate 的 ValidationError
        # 静默吞成 404——DB 模式下失败态设计永久读不回来且毫无痕迹。
        # 存储层读取失败不是「记录不存在」，必须留日志。
        logger.warning("读取设计 %s 失败（存储层数据无法回载）: %s", design_id, e)
    return None


def _ensure_design_access(result: DesignResult | None, user: Optional[User]) -> DesignResult:
    """设计结果属主校验：管理员全可见；创建者可见；匿名创建（无属主）公开"""
    if result is None:
        raise HTTPException(status_code=404, detail="Design not found")
    owner = result.user_id
    if owner and (user is None or (user.id != owner and not user.is_admin)):
        raise HTTPException(status_code=403, detail="无权访问该设计结果")
    return result


@router.post("", response_model=Dict)
async def create_design(request: DesignRequest, background_tasks: BackgroundTasks,
                        user: Optional[User] = Depends(get_current_user)):
    design_id = f"design_{uuid.uuid4().hex[:12]}"
    result = DesignResult(
        design_id=design_id,
        status=DesignStatus.PENDING,
        input_sequence=request.sequence,
        vector_id=request.vector_id,
        cloning_method=request.cloning_method,
        created_at=datetime.now(),
        user_id=user.id if user else None,
    )
    _persist(result)
    # 属主在创建时随参数传入：后台续跑前镜像可能已淘汰该条目（BoundedMirror
    # 容量/TTL 淘汰），届时新建的 result 若不带 user_id 会落成「无属主公开」
    background_tasks.add_task(run_design_task, design_id, request,
                              user.id if user else None)
    return {
        "design_id": design_id,
        "status": "pending",
        "message": "设计任务已提交，请轮询查询结果",
    }


@router.post("/restriction-preview", response_model=Dict)
async def restriction_preview(request: RestrictionPreviewRequest,
                              user: Optional[User] = Depends(get_current_user)):
    """双酶切实时预览：选好两个酶就把构建体长什么样算出来（不落库、不出任务）"""
    from app.design_service import build_restriction_preview

    try:
        # 酶切模拟是 CPU 密集同步计算，移交线程池避免阻塞事件循环
        return await run_in_threadpool(build_restriction_preview, request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


def _load_design_through_cache(design_id: str) -> Optional[DesignResult]:
    """读穿透（同步：缓存可能走 Redis、存储层是 DB，都有 IO）"""
    cached_data = cache.get_design_result(design_id)
    if cached_data is not None:
        try:
            cached = DesignResult.model_validate(cached_data)
        except Exception:
            cached = None  # 缓存结构与模型不兼容时回退存储层
        # 缓存里「无属主」不可信：v2.4.x 从库回载时丢了 user_id，把属主设计
        # 以 user_id=None 写进了 24h 缓存。无属主命中一律回存储层核对属主，
        # 不一致即作废该缓存条目
        if cached is not None and not cached.user_id:
            stored = _load(design_id)
            if stored is not None and stored.user_id:
                cache.invalidate_design(design_id)
                cached = None
        if cached is not None:
            return cached

    result = _load(design_id)

    # 读回填：存储层命中的完成态结果写入缓存，后续轮询不再走存储
    if result is not None and result.status == DesignStatus.COMPLETED:
        try:
            cache.cache_design_result(design_id, result.model_dump(mode="json"))
        except Exception:
            pass
    return result


@router.get("/{design_id}", response_model=DesignResult)
async def get_design(design_id: str, user: Optional[User] = Depends(get_current_user)):
    # 缓存（Redis）与存储层（DB）都是同步 IO，整体移交线程池
    result = await run_in_threadpool(_load_design_through_cache, design_id)
    return _ensure_design_access(result, user)


@router.get("/{design_id}/download/genbank")
async def download_genbank(design_id: str, user: Optional[User] = Depends(get_current_user)):
    result = _ensure_design_access(
        await run_in_threadpool(_load_design_through_cache, design_id), user)
    if result.status != DesignStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Design not completed")

    content = await run_in_threadpool(generate_genbank_from_result, result)
    # 下载名优先用质粒名（序列名或「载体名-序列名」），中文走 RFC 5987
    gb_name = sanitize_filename(result.plasmid_name or design_id,
                                fallback=design_id) + ".gb"
    return PlainTextResponse(
        content=content,
        media_type="text/plain",
        headers={"Content-Disposition": content_disposition(
            gb_name, ascii_fallback=f"{design_id}.gb")},
    )


@router.get("/{design_id}/download/primers")
async def download_primers(design_id: str, user: Optional[User] = Depends(get_current_user)):
    result = _ensure_design_access(
        await run_in_threadpool(_load_design_through_cache, design_id), user)
    if result.status != DesignStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Design not completed")

    tsv_content = generate_primer_tsv(result.primers)
    return PlainTextResponse(
        content=tsv_content,
        media_type="text/tab-separated-values",
        headers={"Content-Disposition": f"attachment; filename={design_id}_primers.tsv"},
    )


@router.get("/{design_id}/map", responses={200: {"model": MapDocument}})
async def get_design_map_data(
    design_id: str,
    enzyme_set: str = Query(default="common", max_length=40),
    orf_min_aa: int = Query(default=100, ge=10, le=5000),
    user: Optional[User] = Depends(get_current_user),
):
    """构建体图谱（契约 plasmid-map/1）：插入片段 source=design，载体元件 source=vector"""
    from app.map_documents import design_map_document
    from app.routes.map_routes import cached_document

    result = _ensure_design_access(
        await run_in_threadpool(_load_design_through_cache, design_id), user)
    if result.status != DesignStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Design not completed")
    key = {
        "design": design_id,
        "seq": hashlib.sha256((result.construct_sequence or result.optimized_sequence or "")
                              .encode("utf-8")).hexdigest(),
        "features": result.construct_features,
        "insert": [result.insert_start, result.insert_end],
        "enzyme_set": enzyme_set, "orf_min_aa": orf_min_aa,
    }
    return await cached_document("design", key, design_map_document, result,
                                 enzyme_set=enzyme_set, orf_min_aa=orf_min_aa)


def run_design_task(design_id: str, request: DesignRequest, user_id: Optional[str] = None):
    """后台执行设计任务（单任务与批量共用 run_design）。

    user_id 由创建端点随任务传入（批量路径同口径）：镜像淘汰时 pending 为 None，
    显式参数保证属主不丢。"""
    pending = designs_db.get(design_id)
    if pending:
        pending.status = DesignStatus.RUNNING
        _persist(pending)

    try:
        result = run_design(design_id, request)
    except Exception:
        # run_design 内部有 catch-all，这里兜的是它之外的意外（结果对象构造、
        # 存储交互等）——不接住的话任务静默死亡，前端轮询永久 pending
        logger.exception("design task %s crashed outside run_design", design_id)
        result = DesignResult(
            design_id=design_id, status=DesignStatus.FAILED,
            input_sequence="", vector_id=request.vector_id,
            cloning_method=request.cloning_method,
            errors=["设计任务异常终止，请重试；详情见服务端日志"],
            completed_at=datetime.now(),
        )
    # 保留创建时间与属主（后台续跑会重建 result 对象）
    if pending and pending.created_at:
        result.created_at = pending.created_at
    if user_id:
        result.user_id = user_id
    elif pending and pending.user_id:
        result.user_id = pending.user_id
    _persist(result)


def generate_genbank_content(result: DesignResult) -> str:
    """兼容旧批量下载入口。"""
    return generate_genbank_from_result(result)


def generate_primer_tsv(primers: List[PrimerInfo]) -> str:
    lines = ["Name\tSequence\tFull Sequence\tLength\tTm\tGC%\tNotes"]
    for p in primers:
        lines.append(
            f"{p.name}\t{p.sequence}\t{p.full_sequence}\t"
            f"{p.length}\t{p.tm:.1f}\t{p.gc_content:.1f}\t{p.notes or ''}"
        )
    return "\n".join(lines)
