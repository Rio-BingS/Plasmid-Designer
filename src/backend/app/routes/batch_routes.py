"""批量设计路由 — 复用 DesignService.run_design"""

import io
import logging
import uuid
import zipfile
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import (
    APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile)
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from app.auth.jwt_auth import User, get_current_user
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
    DesignStatus)
from app.storage import get_batch_store
from app.mirror_store import BoundedMirror

router = APIRouter(prefix="/api/design/batch", tags=["batch"])

logger = logging.getLogger(__name__)

# 内存镜像带上限 + TTL（同 designs_db：只清镜像，存储层权威）
batch_jobs: Dict[str, BatchDesignStatus] = BoundedMirror(max_items=200)


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
        logger.warning("批量任务 %s 存储层回载失败", batch_id, exc_info=True)
    return None


def _ensure_batch_access(job: Optional[BatchDesignStatus], user: Optional[User]) -> BatchDesignStatus:
    """批量任务属主校验：管理员全可见；创建者可见；匿名创建（无属主）公开"""
    if job is None:
        raise HTTPException(status_code=404, detail="Batch job not found")
    owner = job.user_id
    if owner and (user is None or (user.id != owner and not user.is_admin)):
        raise HTTPException(status_code=403, detail="无权访问该批量任务")
    return job


@router.post("", response_model=Dict)
async def create_batch_design(request: BatchDesignRequest, background_tasks: BackgroundTasks,
                              user: Optional[User] = Depends(get_current_user)):
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
        user_id=user.id if user else None,
    )
    _persist_batch(job)

    background_tasks.add_task(run_batch_design_task, batch_id, request, names)
    return {
        "batch_id": batch_id,
        "total": len(request.sequences),
        "message": "批量设计任务已提交",
    }


@router.get("/template")
async def download_batch_template(
    user: Optional[User] = Depends(get_current_user),
):
    """批量上传表格模板 .xlsx：序列清单 + 填写说明（与解析规则同源）。"""
    from app.spreadsheet_parse import generate_template_xlsx

    data = generate_template_xlsx()
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition":
                 'attachment; filename=batch_sequences_template.xlsx'},
    )


@router.post("/parse-spreadsheet")
async def parse_batch_spreadsheet(
    file: UploadFile,
    sequence_type: str = "amino_acid",
    user: Optional[User] = Depends(get_current_user),
):
    """上传表格（.xlsx/.xlsm/.csv/.tsv）解析为批量序列清单。

    返回与 FASTA/粘贴相同的 [{name, sequence, named}] 语义，前端预览表
    对三种入口一视同仁。解析在后端做：npm 上的 xlsx 库有未修复的安全
    公告，前端不引 Excel 解析库。
    """
    from app.spreadsheet_parse import UPLOAD_SUFFIXES, parse_spreadsheet

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="文件为空")
    name = file.filename or ""
    suffix = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    if suffix not in UPLOAD_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail="仅支持 .xlsx/.xlsm/.csv/.tsv 表格（含「名称」与「序列」两列）")
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="文件超过 5MB 上限")
    try:
        rows = parse_spreadsheet(data, suffix, sequence_type)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"sequences": rows, "count": len(rows)}


@router.get("/{batch_id}", response_model=BatchProgressResponse)
async def get_batch_progress(batch_id: str, user: Optional[User] = Depends(get_current_user)):
    job = _ensure_batch_access(_load_batch(batch_id), user)

    completed_results = []
    for design_id in job.results:
        result = _load(design_id)
        if result:
            completed_results.append(
                {
                    "design_id": design_id,
                    "status": result.status.value,
                    # 成品质粒名（默认=序列名；开启前缀则带载体名）为主显，
                    # 设计 ID 是内部标识；警告数让用户不用点进结果页就知道
                    # 哪条要人工核对
                    "sequence_name": result.plasmid_name or design_id,
                    "cai": result.cai,
                    "gc_content": result.gc_content,
                    "vector_name": result.vector_name,
                    "primer_count": len(result.primers),
                    "warnings_count": len(result.warnings),
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
async def download_batch_results(batch_id: str, user: Optional[User] = Depends(get_current_user)):
    # 统一走 _load_batch/_load：进程重启后仍可从存储层恢复（与进度端点行为一致）
    job = _ensure_batch_access(_load_batch(batch_id), user)
    if job.status != "completed":
        raise HTTPException(status_code=400, detail="Batch job not completed")

    zip_buffer = io.BytesIO()
    all_primers: List[tuple] = []          # 合并订购表：按批量顺序收集
    used_names: set = set()                # ZIP 内文件名去重
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for design_id in job.results:
            result = _load(design_id)
            if result:
                gene = result.plasmid_name or design_id
                base = _zip_entry_name(gene, design_id, "", used_names)
                zf.writestr(f"{base}.gb", generate_genbank_content(result))
                zf.writestr(f"{base}_primers.tsv",
                            generate_primer_tsv(result.primers))
                all_primers.extend((gene, p) for p in result.primers)
        if all_primers:
            zf.writestr("all_primers.tsv", generate_batch_primer_tsv(all_primers))

    zip_buffer.seek(0)
    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={batch_id}_results.zip"},
    )


@router.get("/{batch_id}/download/primers")
async def download_batch_primers(batch_id: str, user: Optional[User] = Depends(get_current_user)):
    """合并引物订购表：整批所有引物汇总成一张 TSV（按基因分组核对订购单）"""
    job = _ensure_batch_access(_load_batch(batch_id), user)
    if job.status != "completed":
        raise HTTPException(status_code=400, detail="Batch job not completed")

    entries: List[tuple] = []
    for design_id in job.results:
        result = _load(design_id)
        if result:
            gene = result.plasmid_name or design_id
            entries.extend((gene, p) for p in result.primers)
    if not entries:
        raise HTTPException(status_code=404, detail="批量任务没有可导出的引物")
    content = generate_batch_primer_tsv(entries)
    return StreamingResponse(
        io.BytesIO(content.encode("utf-8")),
        media_type="text/tab-separated-values; charset=utf-8",
        headers={"Content-Disposition":
                 f"attachment; filename={batch_id}_all_primers.tsv"},
    )


@router.get("/{batch_id}/report")
async def get_batch_report(batch_id: str, user: Optional[User] = Depends(get_current_user)):
    # 统一走 _load_batch/_load，与下载端点保持一致
    job = _ensure_batch_access(_load_batch(batch_id), user)

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


def _oligo_type(p) -> str:
    """引物/oligo 的订购类型标注。

    合成 oligo（target_start 非 None）按命名细分 Sense/Antisense；PCR
    克隆引物按 _F/_R 后缀标 Forward/Reverse——与结果页引物表同一口径。
    """
    if p.target_start is not None:
        return "Antisense" if "_AS" in p.name else "Sense"
    if p.name.endswith("_R"):
        return "Reverse"
    return "Forward"


def generate_batch_primer_tsv(entries: List[tuple]) -> str:
    """合并引物订购表：整个批量任务的所有引物汇总成一张 TSV。

    entries = [(序列名, PrimerInfo), ...]（按批量顺序）。基因名列让
    公司/同事按基因分组核对订购单，不必拆开每条设计的 TSV。
    """
    lines = ["#\t基因名\t引物名\t类型\t序列(5'→3')\t长度\tTm\tGC%\t备注"]
    for gene_name, p in entries:
        lines.append(
            f"{p.name}\t{gene_name}\t{p.name}\t{_oligo_type(p)}\t"
            f"{p.full_sequence}\t{p.length}\t{p.tm:.1f}\t{p.gc_content:.1f}\t"
            f"{p.notes or ''}"
        )
    return "\n".join(lines)


def _zip_entry_name(sequence_name: str, design_id: str, suffix: str,
                    used: set) -> str:
    """ZIP 内文件名 = 序列名 + 后缀；重名（或清洗后为空）缀 design_id 末 4 位"""
    base = "".join(
        c if (c.isalnum() or c in "-_") else "_" for c in (sequence_name or "")
    ).strip("_") or design_id[-4:]
    name = f"{base}{suffix}"
    if name in used:
        name = f"{base}_{design_id[-4:]}{suffix}"
    used.add(name)
    return name


def _format_row_error(exc: Exception) -> str:
    """单条失败原因转成可读文本：pydantic 校验错误只保留业务消息，
    去掉 "1 validation error for DesignRequest ... [type=value_error]" 这类样板"""
    if isinstance(exc, ValidationError):
        msgs = []
        for err in exc.errors():
            msg = str(err.get("msg", ""))
            if msg.startswith("Value error, "):
                msg = msg[len("Value error, "):]
            field = ".".join(str(x) for x in err.get("loc", ()) if x != "body")
            msgs.append(f"{field}: {msg}" if field else msg)
        return "；".join(msgs) or "参数校验失败"
    return str(exc)


def _resolve_library_vector(value: str) -> str:
    """Excel 载体列的值（载体名或 ID）→ 库内 vector_id。

    先按 ID 精确查，再按 name 精确匹配；不用模糊搜索（会对 description
    产生歧义命中）。查不到抛 ValueError，落该行 errors。"""
    from app.vector_library_cache import get_vector_library
    library = get_vector_library()
    if library.get_vector(value) is not None:
        return value
    for v in library.list_vectors():
        if v.name == value:
            return v.id
    raise ValueError(f"载体「{value}」不在载体库：请填库内载体名或 ID"
                     "（一次性载体请在批量页上传，不能进表格）")


def run_batch_design_task(batch_id: str, request: BatchDesignRequest, names: List[str]):
    """后台批量设计：每条序列调用统一 run_design。"""
    job = _load_batch(batch_id)
    if job is None:
        logger.error("批量任务 %s 不存在，无法执行", batch_id)
        return
    job.status = "running"
    _persist_batch(job)

    for i, sequence in enumerate(request.sequences):
        sequence_name = names[i] if i < len(names) else f"sequence_{i+1}"
        design_id = f"design_{uuid.uuid4().hex[:12]}"
        try:
            # 整体转发 DesignOptions 与内联载体的全部参数，避免逐字段复制
            # 漏掉新增选项导致单任务与批量结果不一致；行级非 None 覆盖
            # 批级（row_params 与 sequences 对齐，见 RowParams）
            row = (request.row_params[i]
                   if request.row_params and i < len(request.row_params) else None)
            merged = request.model_dump(
                exclude={"sequences", "sequence_names", "row_params"})
            if row is not None:
                overrides = row.model_dump(exclude_none=True)
                vec = overrides.pop("vector", None)
                if vec:
                    overrides["vector_id"] = _resolve_library_vector(vec)
                merged.update(overrides)   # 行级非 None 覆盖批级
            single = DesignRequest(
                **merged,
                sequence=sequence,
                sequence_name=sequence_name,
            )
            result = run_design(design_id, single)
            # 批量任务内创建的 design 继承批量属主（匿名批量 → None 公开）
            result.user_id = job.user_id
            _persist(result)

            if result.status == DesignStatus.COMPLETED:
                job.results.append(design_id)
                job.completed += 1
            else:
                job.errors.append(
                    {
                        "index": i,
                        "sequence_name": sequence_name,
                        "error": "; ".join(result.errors) or "design failed",
                    }
                )
                job.failed += 1
        except Exception as e:
            job.errors.append(
                {"index": i, "sequence_name": sequence_name, "error": _format_row_error(e)}
            )
            job.failed += 1

        _persist_batch(job)

    job.status = "completed"
    _persist_batch(job)
