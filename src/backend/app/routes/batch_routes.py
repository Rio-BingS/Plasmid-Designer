"""批量设计路由 — 复用 DesignService.run_design"""

import io
import logging
import uuid
import zipfile
from datetime import datetime
from typing import Dict, List

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse

from app.design_service import run_design
from app.routes.design_routes import (
    generate_genbank_content,
    generate_primer_tsv,
    _load,
    _persist,
)
from app.routes.models import (
    BatchDesignRequest,
    BatchDesignStatus,
    BatchProgressResponse,
    DesignRequest,
    DesignStatus,
    SequenceType,
)
from app.storage import get_batch_store

router = APIRouter(prefix="/api/design/batch", tags=["batch"])

logger = logging.getLogger(__name__)

batch_jobs: Dict[str, BatchDesignStatus] = {}


def _persist_batch(job: BatchDesignStatus) -> None:
    batch_jobs[job.batch_id] = job
    try:
        store = get_batch_store()
        store.save(job.batch_id, job.model_dump(mode="json"))
    except Exception as e:
        # 持久化失败不中断批量任务，但必须留痕——database 模式下丢持久化意味着
        # 重启后任务无法恢复，静默吞掉会掩盖存储层故障（KNOWN_ISSUES 3.1）
        logger.warning("批量任务 %s 持久化失败: %s", job.batch_id, e)


def _load_batch(batch_id: str) -> BatchDesignStatus | None:
    if batch_id in batch_jobs:
        return batch_jobs[batch_id]
    try:
        data = get_batch_store().get(batch_id)
        if data:
            job = BatchDesignStatus.model_validate(data)
            batch_jobs[batch_id] = job
            return job
    except Exception:
        pass
    return None


@router.post("", response_model=Dict)
async def create_batch_design(request: BatchDesignRequest, background_tasks: BackgroundTasks):
    batch_id = f"batch_{uuid.uuid4().hex[:12]}"
    names = request.sequence_names or [f"sequence_{i+1}" for i in range(len(request.sequences))]

    job = BatchDesignStatus(
        batch_id=batch_id,
        total=len(request.sequences),
        completed=0,
        failed=0,
        status="pending",
        results=[],
        errors=[],
    )
    _persist_batch(job)

    background_tasks.add_task(run_batch_design_task, batch_id, request, names)
    return {
        "batch_id": batch_id,
        "total": len(request.sequences),
        "message": "批量设计任务已提交",
    }


@router.get("/{batch_id}", response_model=BatchProgressResponse)
async def get_batch_progress(batch_id: str):
    job = _load_batch(batch_id)
    if not job:
        raise HTTPException(status_code=404, detail="Batch job not found")

    completed_results = []
    for design_id in job.results:
        result = _load(design_id)
        if result:
            completed_results.append(
                {
                    "design_id": design_id,
                    "status": result.status.value,
                    "cai": result.cai,
                    "gc_content": result.gc_content,
                    "vector_name": result.vector_name,
                }
            )

    return BatchProgressResponse(
        batch_id=batch_id,
        total=job.total,
        completed=job.completed,
        failed=job.failed,
        pending=job.total - job.completed - job.failed,
        status=job.status,
        progress_percent=(job.completed + job.failed) / job.total * 100 if job.total else 0,
        results=completed_results,
        errors=job.errors,
    )


@router.get("/{batch_id}/download")
async def download_batch_results(batch_id: str):
    # 统一走 _load_batch/_load：进程重启后仍可从存储层恢复（与进度端点行为一致）
    job = _load_batch(batch_id)
    if not job:
        raise HTTPException(status_code=404, detail="Batch job not found")
    if job.status != "completed":
        raise HTTPException(status_code=400, detail="Batch job not completed")

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for design_id in job.results:
            result = _load(design_id)
            if result:
                zf.writestr(f"{design_id}.gb", generate_genbank_content(result))
                zf.writestr(f"{design_id}_primers.tsv", generate_primer_tsv(result.primers))

    zip_buffer.seek(0)
    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={batch_id}_results.zip"},
    )


@router.get("/{batch_id}/report")
async def get_batch_report(batch_id: str):
    # 统一走 _load_batch/_load，与下载端点保持一致
    job = _load_batch(batch_id)
    if not job:
        raise HTTPException(status_code=404, detail="Batch job not found")

    report = {
        "batch_id": batch_id,
        "summary": {
            "total": job.total,
            "completed": job.completed,
            "failed": job.failed,
            "success_rate": job.completed / job.total * 100 if job.total > 0 else 0,
        },
        "sequences": [],
    }

    for design_id in job.results:
        result = _load(design_id)
        if result:
            report["sequences"].append(
                {
                    "design_id": design_id,
                    "input_length": len(result.input_sequence),
                    "cai": result.cai,
                    "gc_content": result.gc_content,
                    "final_length": result.final_length,
                    "primer_count": len(result.primers),
                    "validation_passed": result.validation_passed,
                }
            )
    return report


def run_batch_design_task(batch_id: str, request: BatchDesignRequest, names: List[str]):
    """后台批量设计：每条序列调用统一 run_design。"""
    batch_jobs[batch_id].status = "running"
    _persist_batch(batch_jobs[batch_id])

    for i, sequence in enumerate(request.sequences):
        sequence_name = names[i] if i < len(names) else f"sequence_{i+1}"
        design_id = f"design_{uuid.uuid4().hex[:12]}"
        try:
            single = DesignRequest(
                sequence=sequence,
                sequence_type=request.sequence_type,
                sequence_name=sequence_name,
                vector_id=request.vector_id,
                cloning_method=request.cloning_method,
                optimize_codons=request.optimize_codons,
                target_species=request.target_species,
                gc_min=request.gc_min,
                gc_max=request.gc_max,
                homology_arm=request.homology_arm,
                enzyme=request.enzyme,
                oligo_length=request.oligo_length,
                overlap_length=request.overlap_length,
                protocol_language=getattr(request, "protocol_language", "zh") or "zh",
            )
            result = run_design(design_id, single)
            _persist(result)

            if result.status == DesignStatus.COMPLETED:
                batch_jobs[batch_id].results.append(design_id)
                batch_jobs[batch_id].completed += 1
            else:
                batch_jobs[batch_id].errors.append(
                    {
                        "index": i,
                        "sequence_name": sequence_name,
                        "error": "; ".join(result.errors) or "design failed",
                    }
                )
                batch_jobs[batch_id].failed += 1
        except Exception as e:
            batch_jobs[batch_id].errors.append(
                {"index": i, "sequence_name": sequence_name, "error": str(e)}
            )
            batch_jobs[batch_id].failed += 1

        _persist_batch(batch_jobs[batch_id])

    batch_jobs[batch_id].status = "completed"
    _persist_batch(batch_jobs[batch_id])
