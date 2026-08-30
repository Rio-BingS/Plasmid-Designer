"""设计任务路由 — 统一走 DesignService"""

import uuid
from datetime import datetime
from typing import Dict, List

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import PlainTextResponse

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
    except Exception:
        pass
    return None


@router.post("", response_model=Dict)
async def create_design(request: DesignRequest, background_tasks: BackgroundTasks):
    design_id = f"design_{uuid.uuid4().hex[:12]}"
    result = DesignResult(
        design_id=design_id,
        status=DesignStatus.PENDING,
        input_sequence=request.sequence,
        vector_id=request.vector_id,
        cloning_method=request.cloning_method,
        created_at=datetime.now(),
    )
    _persist(result)
    background_tasks.add_task(run_design_task, design_id, request)
    return {
        "design_id": design_id,
        "status": "pending",
        "message": "设计任务已提交，请轮询查询结果",
    }


@router.get("/{design_id}", response_model=DesignResult)
async def get_design(design_id: str):
    # 读取缓存：仅完成态结果会被写入，因此缓存命中即为最终结果
    cached_data = cache.get_design_result(design_id)
    if cached_data is not None:
        try:
            return DesignResult.model_validate(cached_data)
        except Exception:
            pass  # 缓存结构与模型不兼容时回退存储层

    result = _load(design_id)
    if not result:
        raise HTTPException(status_code=404, detail="Design not found")

    # 读回填：存储层命中的完成态结果写入缓存，后续轮询不再走存储
    if result.status == DesignStatus.COMPLETED:
        try:
            cache.cache_design_result(design_id, result.model_dump(mode="json"))
        except Exception:
            pass
    return result


@router.get("/{design_id}/download/genbank")
async def download_genbank(design_id: str):
    result = _load(design_id)
    if not result:
        raise HTTPException(status_code=404, detail="Design not found")
    if result.status != DesignStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Design not completed")

    content = generate_genbank_from_result(result)
    return PlainTextResponse(
        content=content,
        media_type="text/plain",
        headers={"Content-Disposition": f"attachment; filename={design_id}.gb"},
    )


@router.get("/{design_id}/download/primers")
async def download_primers(design_id: str):
    result = _load(design_id)
    if not result:
        raise HTTPException(status_code=404, detail="Design not found")
    if result.status != DesignStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Design not completed")

    tsv_content = generate_primer_tsv(result.primers)
    return PlainTextResponse(
        content=tsv_content,
        media_type="text/tab-separated-values",
        headers={"Content-Disposition": f"attachment; filename={design_id}_primers.tsv"},
    )


@router.get("/{design_id}/map", response_model=PlasmidMapData)
async def get_design_map_data(design_id: str):
    result = _load(design_id)
    if not result:
        raise HTTPException(status_code=404, detail="Design not found")
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
    # 保留创建时间
    if pending and pending.created_at:
        result.created_at = pending.created_at
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
