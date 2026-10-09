"""设计任务路由 — 统一走 DesignService"""

import logging
import uuid
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import PlainTextResponse

from app.auth.jwt_auth import User, get_current_user
from app.cache import cache
from app.design_service import (
    generate_genbank_from_result,
    map_data_from_result,
    run_design,
)
from app.routes.models import (
    DesignRequest,
    DesignResult,
    DesignStatus,
    PlasmidMapData,
    PrimerInfo,
)
from app.storage import get_design_store

router = APIRouter(prefix="/api/design", tags=["design"])

logger = logging.getLogger(__name__)

# 兼容旧代码/批量路由：内存镜像 + 存储层双写
designs_db: Dict[str, DesignResult] = {}


def _persist(result: DesignResult) -> None:
    designs_db[result.design_id] = result
    try:
        store = get_design_store()
        store.save(result.design_id, result.model_dump(mode="json"))
    except Exception:
        # 存储层失败不阻断主流程（如 DB 未配置）
        pass
    # 完成态写入响应缓存（24h TTL）；中间态不缓存，避免轮询读到过期状态
    if result.status == DesignStatus.COMPLETED:
        try:
            cache.cache_design_result(result.design_id, result.model_dump(mode="json"))
        except Exception:
            pass


def _load(design_id: str) -> DesignResult | None:
    if design_id in designs_db:
        return designs_db[design_id]
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
    background_tasks.add_task(run_design_task, design_id, request)
    return {
        "design_id": design_id,
        "status": "pending",
        "message": "设计任务已提交，请轮询查询结果",
    }


@router.get("/{design_id}", response_model=DesignResult)
async def get_design(design_id: str, user: Optional[User] = Depends(get_current_user)):
    # 读取缓存：仅完成态结果会被写入，因此缓存命中即为最终结果
    cached_data = cache.get_design_result(design_id)
    if cached_data is not None:
        try:
            return _ensure_design_access(DesignResult.model_validate(cached_data), user)
        except HTTPException:
            raise
        except Exception:
            pass  # 缓存结构与模型不兼容时回退存储层

    result = _load(design_id)
    result = _ensure_design_access(result, user)

    # 读回填：存储层命中的完成态结果写入缓存，后续轮询不再走存储
    if result.status == DesignStatus.COMPLETED:
        try:
            cache.cache_design_result(design_id, result.model_dump(mode="json"))
        except Exception:
            pass
    return result


@router.get("/{design_id}/download/genbank")
async def download_genbank(design_id: str, user: Optional[User] = Depends(get_current_user)):
    result = _ensure_design_access(_load(design_id), user)
    if result.status != DesignStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Design not completed")

    content = generate_genbank_from_result(result)
    return PlainTextResponse(
        content=content,
        media_type="text/plain",
        headers={"Content-Disposition": f"attachment; filename={design_id}.gb"},
    )


@router.get("/{design_id}/download/primers")
async def download_primers(design_id: str, user: Optional[User] = Depends(get_current_user)):
    result = _ensure_design_access(_load(design_id), user)
    if result.status != DesignStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Design not completed")

    tsv_content = generate_primer_tsv(result.primers)
    return PlainTextResponse(
        content=tsv_content,
        media_type="text/tab-separated-values",
        headers={"Content-Disposition": f"attachment; filename={design_id}_primers.tsv"},
    )


@router.get("/{design_id}/map", response_model=PlasmidMapData)
async def get_design_map_data(design_id: str, user: Optional[User] = Depends(get_current_user)):
    result = _ensure_design_access(_load(design_id), user)
    if result.status != DesignStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Design not completed")

    data = map_data_from_result(result)
    return PlasmidMapData(**data)


def run_design_task(design_id: str, request: DesignRequest):
    """后台执行设计任务（单任务与批量共用 run_design）。"""
    pending = designs_db.get(design_id)
    if pending:
        pending.status = DesignStatus.RUNNING
        _persist(pending)

    result = run_design(design_id, request)
    # 保留创建时间与属主（后台续跑会重建 result 对象）
    if pending and pending.created_at:
        result.created_at = pending.created_at
    if pending and pending.user_id:
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
