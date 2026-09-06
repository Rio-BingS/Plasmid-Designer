"""Sanger 测序全自动分析路由

上传参考序列文件（.gb/.fasta/.dna，与 .ab1 放同一文件夹一起导入）→
全自动管线（解析→修剪→比对→拼接→注释）→ 结果含自动结论、突变表、
共识序列与峰图数据。

- POST /api/sequencing/analyze：单样品入口（1 个参考序列 + 它的 .ab1）
- POST /api/sequencing/analyze-batch：批量入口（Excel 信息表/整个交付文件夹
  里的多个质粒，逐质粒归组分析，返回每质粒一句话结论；逻辑与
  scripts/batch_sequencing_report.py 共用 core/sanger/batch.py）

另保留两个按 ID 取参考的便捷端点：
- 设计结果（POST /api/designs/{design_id}/sequencing/analyze）
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
from core.sanger.batch import (
    REF_EXTS, READ_EXTS, excel_conclusion, load_excel, match_files, norm_stem,
)
from core.sanger.pipeline import analyze, _try_tracy_decompose
from core.sanger.reference_parser import parse_reference

router = APIRouter(prefix="/api", tags=["sequencing"])

# 进程级内存存储：analysis_id → 完整结果（含 trace 峰图）
_ANALYSES: Dict[str, Dict] = {}
MAX_FILE_SIZE = 20 * 1024 * 1024      # 单文件 20MB
MAX_FILES = 24                        # 单次最多 24 条 read
MAX_STORED = 50                       # 内存最多保留 50 次分析
MAX_BATCH_FILES = 200                 # 批量单次最多 200 个文件
MAX_BATCH_PLASMIDS = 60               # 批量单次最多 60 个质粒


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


def _register_analysis(sample_name: str, reference: str, features: List[Dict], result: Dict) -> str:
    """把一次完整分析写入内存存储（单样品与批量共用），返回 analysis_id"""
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
    return analysis_id


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

    analysis_id = _register_analysis(sample_name, reference, features, result)
    return _summary(_ANALYSES[analysis_id])


@router.post("/sequencing/analyze")
async def analyze_sequencing_upload(
    reference: UploadFile = File(..., description="参考序列文件（.gb/.gbk/.genbank/.fasta/.fa/.fna/.dna）"),
    reads: List[UploadFile] = File(..., description="一个或多个 .ab1 测序文件"),
    min_q: int = Form(default=20, ge=0, le=60, description="末端修剪质量阈值（0-60）"),
    allow_decompose: bool = Form(default=True, description="允许对混合样品执行 tracy 解卷积"),
):
    """上传参考序列文件 + .ab1 测序文件（可来自同一文件夹），全自动测序验证。

    样品名取参考文件名主干，特征注释直接来自参考文件（GenBank/SnapGene 特征表）。
    """
    from core.sanger.reference_parser import parse_reference, ReferenceParseError

    ref_name = reference.filename or "reference.gb"
    ref_bytes = await reference.read()
    if not ref_bytes:
        raise HTTPException(status_code=400, detail="参考文件为空")
    try:
        ref_seq, features = await run_in_threadpool(parse_reference, ref_name, ref_bytes)
    except ReferenceParseError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if len(ref_seq) < 50:
        raise HTTPException(status_code=400, detail=f"参考序列过短（{len(ref_seq)} bp），无法比对")

    sample_name = os.path.splitext(os.path.basename(ref_name))[0][:60] or "reference"
    return await _analyze_endpoint(ref_seq, sample_name, features, reads, min_q, allow_decompose)


# ---------------------------------------------------------------- 批量分析（独立入口）


def _group_batch_uploads(
    reads: List[Dict], refs: List[Dict], rows: Optional[List[Dict]]
):
    """把上传的 reads/refs 归组到各质粒

    reads/refs 每项：{"name", "ext", "stem", "bytes"}。
    带信息表（rows 非 None）时与离线脚本同一实现（core.sanger.match_files：
    引物列→reads、质粒名称→图谱）；无信息表时按包含关系归组——每个图谱
    自成一个质粒，测序文件名主干包含且仅包含一个图谱主干时归入该质粒。

    返回 (ordered_names, per_plasmid, unmatched)：
    per_plasmid: 质粒名 -> {"reference": {"file","how"}|None, "reads": [{"file","how"}]}
    """
    if rows is not None:
        per_plasmid, unmatched = match_files(rows, reads + refs)
        ordered = [r["name"] for r in rows]
        ordered += [n for n in per_plasmid if n not in set(ordered)]
        return ordered, per_plasmid, unmatched

    per_plasmid: Dict[str, Dict] = {}
    unmatched: List[Dict] = []
    for r in refs:
        raw_stem = r["name"][: r["name"].rfind(".")]
        if norm_stem(raw_stem) in {norm_stem(k) for k in per_plasmid}:
            unmatched.append({"file": r, "reason": "已存在同名图谱，本文件未采用"})
            continue
        per_plasmid[raw_stem] = {
            "reference": {"file": r, "how": "文件名即质粒名"},
            "reads": [],
        }
    for f in reads:
        cands = {k: norm_stem(k) for k in per_plasmid if norm_stem(k) and norm_stem(k) in f["stem"]}
        if not cands:
            unmatched.append({"file": f, "reason": "文件名不包含任何图谱名"})
            continue
        # 图谱名互为前缀时（MX 与 MX2）取最长包含（最具体者），仍有并列才放弃
        best_len = max(len(v) for v in cands.values())
        best = sorted(k for k, v in cands.items() if len(v) == best_len)
        if len(best) > 1:
            unmatched.append({"file": f,
                              "reason": f"文件名同时包含多个图谱名（{'、'.join(best)[:80]}），无法唯一归组"})
        else:
            per_plasmid[best[0]]["reads"].append({"file": f, "how": "文件名包含图谱名"})
    return list(per_plasmid.keys()), per_plasmid, unmatched


def _run_batch(
    reads: List[Dict], refs: List[Dict], rows: Optional[List[Dict]],
    ignored: List[str], min_q: int,
) -> Dict:
    """同步执行批量分析（调用方负责移交线程池）：归组 → 逐质粒跑管线 → 一句话结论"""
    if len(reads) + len(refs) > MAX_BATCH_FILES:
        raise HTTPException(status_code=400, detail=f"文件数超过上限（{MAX_BATCH_FILES} 个）")

    ordered, per_plasmid, unmatched = _group_batch_uploads(reads, refs, rows)
    if len(ordered) > MAX_BATCH_PLASMIDS:
        raise HTTPException(status_code=400, detail=f"质粒数超过上限（{MAX_BATCH_PLASMIDS} 个）")

    items: List[Dict] = []
    for plasmid in ordered:
        slot = per_plasmid[plasmid]
        ref_file = slot["reference"]
        n_reads = len(slot["reads"])
        item = {
            "plasmid": plasmid,
            "status": "analyzed",
            "conclusion": "",
            "analysis_id": None,
            "reference_name": ref_file["file"]["name"] if ref_file else None,
            "reference_length": None,
            "read_count": n_reads,
            "variant_count": 0,
            "pending_count": 0,
            "coverage_percent": None,
        }
        if ref_file is None and n_reads == 0:
            item["status"] = "not_found"
            item["conclusion"] = "未在文件夹中找到该质粒的测序文件或图谱"
            items.append(item)
            continue

        res = None
        if ref_file and n_reads:
            try:
                ref_seq, features = parse_reference(
                    ref_file["file"]["name"], ref_file["file"]["bytes"])
                if len(ref_seq) < 50:
                    raise ValueError(f"参考序列过短（{len(ref_seq)} bp），无法比对")
                ab1s = [(r["file"]["name"], r["file"]["bytes"]) for r in slot["reads"]]
                res = analyze(ab1s, ref_seq, features, min_q=min_q)
                item["analysis_id"] = _register_analysis(plasmid, ref_seq, features, res)
                item["reference_length"] = len(ref_seq)
                confirmed = [v for v in res["variants"] if v.get("confidence") != "low"]
                item["variant_count"] = len(confirmed)
                item["pending_count"] = len(res["variants"]) - len(confirmed)
                item["coverage_percent"] = res["consensus"]["coverage_percent"]
            except Exception as e:  # noqa: BLE001 单个质粒失败不阻断批次
                item["status"] = "failed"
                item["conclusion"] = f"分析失败：{e}"
                items.append(item)
                continue

        if ref_file and n_reads == 0:
            item["status"] = "no_reads"
            item["conclusion"] = "已找到参考图谱，但未匹配到它的测序文件（.ab1）"
        elif ref_file is None:
            item["status"] = "no_reference"
            item["conclusion"] = excel_conclusion(plasmid, None, n_reads, False)
        else:
            item["conclusion"] = excel_conclusion(plasmid, res, n_reads, True)
        items.append(item)

    return {
        "batch_id": f"seqbatch_{uuid.uuid4().hex[:12]}",
        "created_at": datetime.now().isoformat(),
        "min_q": min_q,
        "excel_mode": rows is not None,
        "items": items,
        "unmatched": [
            {"filename": u["file"].get("name") or str(u["file"]["path"].name),
             "reason": u["reason"]}
            for u in unmatched
        ],
        "ignored_files": ignored,
    }


@router.post("/sequencing/analyze-batch")
async def analyze_sequencing_batch(
    files: List[UploadFile] = File(..., description="测序结果文件：.ab1 与参考图谱（.dna/.gb/.fasta 等），可多质粒混在一起"),
    excel: Optional[UploadFile] = File(None, description="信息表 .xlsx（表头含质粒名称/测序引物/测序结果）；缺省时按图谱文件名包含关系归组"),
    min_q: int = Form(default=20, ge=0, le=60, description="末端修剪质量阈值（0-60）"),
):
    """批量测序分析（独立于单样品 /sequencing/analyze 的入口）。

    测序公司交付的「信息表 + 一批 .ab1/.dna」一次上传，按质粒归组后逐个
    跑全自动管线；每个质粒返回一句话结论（与离线脚本同一口径），成功者
    同时注册为标准分析记录（analysis_id 可进历史列表与详情页）。
    """
    if not files:
        raise HTTPException(status_code=400, detail="请上传测序结果文件")

    reads: List[Dict] = []
    refs: List[Dict] = []
    ignored: List[str] = []
    for f in files:
        name = f.filename or "unnamed"
        blob = await f.read()
        if len(blob) > MAX_FILE_SIZE:
            raise HTTPException(status_code=400, detail=f"文件过大（>20MB）: {name}")
        if name.startswith("~$") or not blob:
            continue
        ext = os.path.splitext(name)[1].lower()
        entry = {
            "name": name, "ext": ext, "bytes": blob,
            "stem": norm_stem(os.path.splitext(os.path.basename(name))[0]),
        }
        if ext in READ_EXTS:
            reads.append(entry)
        elif ext in REF_EXTS:
            refs.append(entry)
        else:
            ignored.append(name)

    if not reads and not refs:
        raise HTTPException(
            status_code=400,
            detail="未找到可用的测序文件（.ab1）或参考图谱（.dna/.gb/.fasta）")

    excel_rows = None
    if excel is not None:
        excel_bytes = await excel.read()
        if not excel_bytes:
            raise HTTPException(status_code=400, detail="信息表文件为空")
        try:
            _, _, _, excel_rows = await run_in_threadpool(load_excel, excel_bytes)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    return await run_in_threadpool(_run_batch, reads, refs, excel_rows, ignored, min_q)


@router.post("/designs/{design_id}/sequencing/analyze")
async def analyze_design_sequencing(
    design_id: str,
    files: List[UploadFile] = File(..., description="一个或多个 .ab1 文件"),
    min_q: int = Form(default=20, ge=0, le=60, description="末端修剪质量阈值（0-60）"),
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
    min_q: int = Form(default=20, ge=0, le=60),
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


@router.get("/sequencing/analyses")
async def list_analyses():
    """历史分析列表（摘要，按时间倒序；不含 reads/变体明细）"""
    items = sorted(_ANALYSES.values(), key=lambda r: r["created_at"], reverse=True)
    return [
        {
            "analysis_id": r["analysis_id"],
            "sample_name": r.get("sample_name", ""),
            "created_at": r["created_at"],
            "engine": r["engine"],
            "conclusion": r["conclusion"],
            "read_count": len(r["reads"]),
            "variant_count": len(r["variants"]),
            "coverage_percent": r["consensus"]["coverage_percent"],
            "reference_length": len(r["reference"]),
        }
        for r in items
    ]


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
            "grade": r.get("grade"),
            "q20_ratio": r.get("q20_ratio"),
            "direction": r["alignment"]["direction"],
            "ref_start": r["alignment"]["ref_start"],
            "ref_end": r["alignment"]["ref_end"],
            "identity": r["alignment"]["identity"],
            "mixed_positions": r.get("mixed_positions", []),
            # 逐列对齐视图（read 与参考的原始证据，供人工核对）
            "alignment_view": r["alignment"].get("aligned"),
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
        "coverage_gaps": record.get("coverage_gaps", []),
        "cds_reports": record.get("cds_reports", []),
        "mixed_detected": record.get("mixed_detected", {}),
        "decomposed_alleles": record.get("decomposed_alleles", {}),
        "errors": record["errors"],
        "reference_length": len(record["reference"]),
        "features": record["features"],
    }
