"""Sanger 测序全自动分析路由

上传一个或多个 .ab1 文件 → 全自动管线（解析→修剪→比对→拼接→注释）→
结果含自动结论、突变表、共识序列与峰图数据。

参考序列来源二选一：
- 设计结果（POST /api/designs/{design_id}/sequencing/analyze，主路径）
- 载体库中有序列的载体（POST /api/vectors/{vector_id}/sequencing/analyze）

分析记录为进程级内存存储（会话级数据，重启后失效；trace 数据体积大，
不写入设计主线的持久化存储）。
"""

import os
import uuid
import tempfile
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
from starlette.concurrency import run_in_threadpool

from app.design_service import get_vector_library
from core.sanger.pipeline import analyze, _try_tracy_decompose

router = APIRouter(prefix="/api", tags=["sequencing"])

# 进程级内存存储：analysis_id → 完整结果（含 trace 峰图）
_ANALYSES: Dict[str, Dict] = {}
MAX_FILE_SIZE = 20 * 1024 * 1024      # 单文件 20MB
MAX_FILES = 24                        # 单次最多 24 条 read
MAX_STORED = 50                       # 内存最多保留 50 次分析


def _get_analysis(analysis_id: str) -> Dict:
    result = _ANALYSES.get(analysis_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return result


async def _read_ab1_files(files: List[UploadFile]) -> List[Tuple[str, bytes]]:
    if not files or len(files) > MAX_FILES:
        raise HTTPException(status_code=400, detail=f"请上传 1-{MAX_FILES} 个 .ab1 文件")
    blobs: List[Tuple[str, bytes]] = []
    for f in files:
        name = f.filename or "unknown.ab1"
        if not name.lower().endswith(".ab1"):
            raise HTTPException(status_code=400, detail=f"仅支持 .ab1 文件，收到: {name}")
        blob = await f.read()
        if len(blob) > MAX_FILE_SIZE:
            raise HTTPException(status_code=400, detail=f"文件过大（>20MB）: {name}")
        blobs.append((name, blob))
    return blobs


def _run_full_analysis(
    reference: str,
    sample_name: str,
    features: List[Dict],
    ab1_blobs: List[Tuple[str, bytes]],
    min_q: int,
    allow_decompose: bool,
) -> Dict:
    """同步执行全自动分析（调用方负责移交线程池）"""
    result = analyze(ab1_blobs, reference, features, min_q=min_q)

    # 混合样品解卷积（tracy 可用时）：写入临时文件后调用 decompose
    if allow_decompose and result.get("mixed_detected"):
        alleles: Dict[str, List[Dict]] = {}
        with tempfile.TemporaryDirectory() as td:
            ref_fasta = os.path.join(td, "ref.fasta")
            with open(ref_fasta, "w", encoding="utf-8") as fh:
                fh.write(f">{sample_name or 'reference'}\n{reference.upper()}\n")
            by_name = dict(ab1_blobs)
            for filename in result["mixed_detected"]:
                blob = by_name.get(filename)
                if not blob:
                    continue
                ab1_path = os.path.join(td, "sample.ab1")
                with open(ab1_path, "wb") as fh:
                    fh.write(blob)
                dec = _try_tracy_decompose(ab1_path, ref_fasta)
                if dec:
                    alleles[filename] = dec
        result["decomposed_alleles"] = alleles
        if alleles:
            result["engine"] = "internal+biopython+tracy"
    return result


async def _analyze_endpoint(
    reference: str,
    sample_name: str,
    features: List[Dict],
    files: List[UploadFile],
    min_q: int,
    allow_decompose: bool,
) -> Dict:
    if not reference or len(reference) < 50:
        raise HTTPException(status_code=400, detail="参考序列缺失或过短，无法比对")
    ab1_blobs = await _read_ab1_files(files)

    result = await run_in_threadpool(
        _run_full_analysis, reference, sample_name, features, ab1_blobs, min_q, allow_decompose
    )

    analysis_id = f"seq_{uuid.uuid4().hex[:12]}"
    record = {
        "analysis_id": analysis_id,
        "sample_name": sample_name,
        "reference": reference.upper(),
        "features": features,
        "created_at": datetime.now().isoformat(),
        **result,
    }
    # trace 峰图数据按 read 序号存放，供 /trace/{read_index} 取用
    record["_trace_data"] = {i: t for i, t in enumerate(result.get("traces", []))}
    _ANALYSES[analysis_id] = record
    if len(_ANALYSES) > MAX_STORED:
        oldest = sorted(_ANALYSES.items(), key=lambda kv: kv[1]["created_at"])[0][0]
        del _ANALYSES[oldest]

    return _summary(record)


@router.post("/designs/{design_id}/sequencing/analyze")
async def analyze_design_sequencing(
    design_id: str,
    files: List[UploadFile] = File(..., description="一个或多个 .ab1 文件"),
    min_q: int = Form(default=20, ge=5, le=40, description="末端修剪质量阈值"),
    allow_decompose: bool = Form(default=True, description="允许对混合样品执行 tracy 解卷积"),
):
    """上传 AB1 文件，对设计结果（构建体序列）做全自动测序验证"""
    from app.routes.design_routes import _load
    from app.routes.models import DesignStatus

    result = _load(design_id)
    if not result:
        raise HTTPException(status_code=404, detail="Design not found")
    if result.status != DesignStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Design not completed")

    reference = result.construct_sequence or result.optimized_sequence or ""
    return await _analyze_endpoint(
        reference, result.vector_name or "Construct",
        list(result.construct_features or []), files, min_q, allow_decompose,
    )


@router.post("/vectors/{vector_id}/sequencing/analyze")
async def analyze_vector_sequencing(
    vector_id: str,
    files: List[UploadFile] = File(...),
    min_q: int = Form(default=20, ge=5, le=40),
    allow_decompose: bool = Form(default=True),
):
    """上传 AB1 文件，对载体库中有序列的载体做全自动测序验证"""
    library = get_vector_library()
    vector = library.get_vector(vector_id)
    if not vector:
        raise HTTPException(status_code=404, detail="Vector not found")

    features = [
        {"name": e.name, "type": e.element_type.value, "start": e.start,
         "end": e.end, "strand": e.strand, "description": e.description}
        for e in vector.elements
    ]
    return await _analyze_endpoint(
        vector.sequence, vector.name, features, files, min_q, allow_decompose,
    )


@router.get("/sequencing/analyses/{analysis_id}")
async def get_analysis(analysis_id: str):
    """获取分析结果（不含峰图原始数据）"""
    return _summary(_get_analysis(analysis_id))


@router.get("/sequencing/analyses/{analysis_id}/trace/{read_index}")
async def get_read_trace(analysis_id: str, read_index: int):
    """获取单条 read 的峰图数据（四通道 + 碱基 + 质量值 + 峰位置）"""
    record = _get_analysis(analysis_id)
    trace_data = record.get("_trace_data", {})
    if read_index not in trace_data:
        raise HTTPException(status_code=404, detail="Read trace not found")
    return trace_data[read_index]


@router.get("/sequencing/analyses/{analysis_id}/consensus/export")
async def export_consensus(analysis_id: str, format: str = "fasta"):
    """导出拼接结果（共识序列，FASTA / GenBank）"""
    record = _get_analysis(analysis_id)
    seq = record["consensus"]["sequence"]
    safe_name = f"{record['sample_name']}-consensus".replace(" ", "_")

    if format.lower() == "genbank":
        lines = [
            f"LOCUS       {safe_name[:16]:<16} {len(seq)} bp DNA",
            "DEFINITION  Sanger consensus sequence",
            f"ACCESSION   {analysis_id}",
            "FEATURES             Location/Qualifiers",
            "ORIGIN",
        ]
        for i in range(0, len(seq), 60):
            chunk = seq[i:i + 60]
            groups = " ".join(chunk[j:j + 10] for j in range(0, len(chunk), 10))
            lines.append(f"{i + 1:>9} {groups}")
        lines.append("//")
        content = "\n".join(lines)
        ext = "gb"
    else:
        content = (
            f">{safe_name} coverage={record['consensus']['coverage_percent']}%\n{seq}"
        )
        ext = "fasta"

    return PlainTextResponse(
        content=content,
        media_type="text/plain",
        headers={"Content-Disposition": f"attachment; filename={safe_name}.{ext}"},
    )


@router.delete("/sequencing/analyses/{analysis_id}")
async def delete_analysis(analysis_id: str):
    _get_analysis(analysis_id)
    del _ANALYSES[analysis_id]
    return {"deleted": True, "analysis_id": analysis_id}


def _summary(record: Dict) -> Dict:
    """对外摘要：剔除参考序列与 trace 原始数据"""
    reads = []
    for i, r in enumerate(record["reads"]):
        reads.append({
            "index": i,
            "filename": r["filename"],
            "sample_name": r.get("sample_name", ""),
            "raw_length": r["raw_length"],
            "trimmed_length": r["trimmed_length"],
            "mean_q": r["mean_q"],
            "direction": r["alignment"]["direction"],
            "ref_start": r["alignment"]["ref_start"],
            "ref_end": r["alignment"]["ref_end"],
            "identity": r["alignment"]["identity"],
            "mixed_positions": r.get("mixed_positions", []),
        })
    return {
        "analysis_id": record["analysis_id"],
        "sample_name": record.get("sample_name", ""),
        "created_at": record["created_at"],
        "engine": record["engine"],
        "conclusion": record["conclusion"],
        "reads": reads,
        "variants": record["variants"],
        "consensus": record["consensus"],
        "coverage_ranges": record["coverage_ranges"],
        "mixed_detected": record.get("mixed_detected", {}),
        "decomposed_alleles": record.get("decomposed_alleles", {}),
        "errors": record["errors"],
        "reference_length": len(record["reference"]),
        "features": record["features"],
    }
