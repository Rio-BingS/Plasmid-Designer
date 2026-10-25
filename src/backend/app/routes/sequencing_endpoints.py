"""Sanger 测序分析端点（从 sequencing_routes.py 迁出，纯搬运）

实现/helper（存储、并发闸、批量管线等）仍在 app.routes.sequencing_routes：
- 测试大量以 sequencing_routes 为 patch 面（_ANALYSES / 各上限常量），
  保持不动可零测试改动；
- app.routes.sequencing_session_routes 以 sr._batch_pipeline 等复用批量流。

本模块只挂路由：main.py 从这里取 sequencing router。
"""

import os
import secrets
import time
from typing import List, Optional, Tuple
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import PlainTextResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from app.auth.jwt_auth import User, get_current_user
from app import batch_report_store, sequencing_store
from app.gating import require_feature
from app.vector_library_cache import get_vector_library
from app.filename import content_disposition
from app.routes import sequencing_routes
from app.routes.sequencing_routes import (
    _ANALYSES, _STORE_LOCK,
    _aget_analysis, _analyze_endpoint, _batch_pipeline, _batch_read_limits_for,
    _can_access, _heavy_slot, _read_batch_uploads, _read_limited, _reference_circular,
    _request_access_token, _run_heavy, _sweep_expired, _sweep_reports, _summary,
    _token_hash_matches, parse_zone_form, parse_reference_topology,
)
# 上限常量经模块属性访问（而非按值 import）：测试 monkeypatch
# sequencing_routes.MAX_... 时端点级检查同步生效，避免双真相源

router = APIRouter(prefix="/api", tags=["sequencing"])


@router.post(
    "/sequencing/analyze",
    dependencies=[Depends(require_feature("sequencing"))],
)
async def analyze_sequencing_upload(
    reference: UploadFile = File(..., description="参考序列文件（.gb/.gbk/.genbank/.fasta/.fa/.fna/.dna）"),
    reads: List[UploadFile] = File(..., description="一个或多个 .ab1 测序文件"),
    min_q: int = Form(default=20, ge=0, le=60, description="末端修剪质量阈值（0-60）"),
    allow_decompose: bool = Form(default=True, description="允许对混合样品执行 tracy 解卷积"),
    reliable_zone: str = Form(default="", description="read 可信区边界：首端硬区,首端灰区,尾端灰区,尾端硬区（read 坐标 bp），留空用默认 30,60,700,900"),
    user: Optional[User] = Depends(get_current_user),
):
    """上传参考序列文件 + .ab1 测序文件（可来自同一文件夹），全自动测序验证。

    样品名取参考文件名主干，特征注释直接来自参考文件（GenBank/SnapGene 特征表）。
    """
    from core.sanger.reference_parser import ReferenceParseError

    zone = parse_zone_form(reliable_zone)
    ref_name = reference.filename or "reference.gb"
    ref_bytes = await _read_limited(reference)
    if not ref_bytes:
        raise HTTPException(status_code=400, detail="参考文件为空")
    try:
        ref_seq, features, topology = await _run_heavy(
            parse_reference_topology, ref_name, ref_bytes)
    except ReferenceParseError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if len(ref_seq) < 50:
        raise HTTPException(status_code=400, detail=f"参考序列过短（{len(ref_seq)} bp），无法比对")
    # 终审 C-01：长度上限（见 _analyze_endpoint 同款校验）
    if len(ref_seq) > sequencing_routes.MAX_REF_BP:
        raise HTTPException(
            status_code=413,
            detail=f"参考序列过长（{len(ref_seq)} bp > {sequencing_routes.MAX_REF_BP} bp）：请截取待验证区段后重新分析")

    sample_name = os.path.splitext(os.path.basename(ref_name))[0][:60] or "reference"
    return await _analyze_endpoint(ref_seq, sample_name, features, reads, min_q,
                                   allow_decompose, user=user,
                                   circular=_reference_circular(topology), zone=zone)


@router.post(
    "/sequencing/analyze-batch",
    dependencies=[Depends(require_feature("sequencing_batch"))],
)
async def analyze_sequencing_batch(
    files: List[UploadFile] = File(..., description="测序结果文件：.ab1 与参考图谱（.dna/.gb/.fasta 等），可多质粒混在一起"),
    excel: Optional[UploadFile] = File(None, description="信息表 .xlsx（表头含质粒名称/测序引物/测序结果，可含克隆号列）；缺省时按图谱文件名包含关系归组"),
    min_q: int = Form(default=20, ge=0, le=60, description="末端修剪质量阈值（0-60）"),
    reliable_zone: str = Form(default="", description="read 可信区边界：首端硬区,首端灰区,尾端灰区,尾端硬区（read 坐标 bp），留空用默认 30,60,700,900"),
    user: Optional[User] = Depends(get_current_user),
):
    """批量测序分析（独立于单样品 /sequencing/analyze 的入口）。

    测序公司交付的「信息表 + 一批 .ab1/.dna」一次上传，归组后逐个跑全自动
    管线；每个分组返回一句话结论，成功者同时注册为标准分析记录（analysis_id
    可进历史列表与详情页）。信息表带「克隆号」列时按克隆分组——一个质粒的
    每个克隆独立分析，引物用中英文分号分隔均可，ab1 文件名 = 克隆号-引物。

    分析完成后按上传原始字节现场打包「整理包」（同一质粒一个文件夹、其下
    正确/错误 按结论分置副本与各组 测序分析报告.md、整理清单.csv、结论回填的
    信息表）缓存 15 分钟，经 GET /sequencing/batches/{batch_id}/report 下载；
    响应 report_ready=False 表示本次体积超上限未打包。
    """
    zone = parse_zone_form(reliable_zone)
    if not files:
        raise HTTPException(status_code=400, detail="请上传测序结果文件")
    # 文件数上限在读取任何字节之前生效：否则超限请求会把全部文件读进内存
    # （每个 20MB 上限 × 数百个文件 = GB 级 OOM 面）才被拒绝
    if len(files) > sequencing_routes.MAX_BATCH_FILES:
        raise HTTPException(status_code=400, detail=f"文件数超过上限（{sequencing_routes.MAX_BATCH_FILES} 个）")
    # read 数上限按请求者解析（站点设置读库在并发槽外完成）
    read_limits = _batch_read_limits_for(user)

    # 审查 02 M5：上传读入（最多 500MB 进内存）、Excel 解析、逐组比对与
    # 整理包打包（最多 256MB zip，峰值约数据量 2-3 倍）全部在同一个并发槽内
    # 完成，同步部分移交线程池；此前读入/解析/打包都在闸外，并发闸形同半设
    async with _heavy_slot():
        reads, refs, ignored, excel_bytes = await _read_batch_uploads(files, excel)
        # 匿名批量：整批共用一个访问令牌（终审 B-02），随响应下发一次
        batch_token = secrets.token_urlsafe(16) if user is None else None
        return await run_in_threadpool(
            _batch_pipeline, reads, refs, ignored, min_q,
            (excel.filename or "信息表.xlsx") if excel is not None else None,
            excel_bytes, user.id if user else None, batch_token, read_limits,
            zone=zone)

@router.get(
    "/sequencing/batches/{batch_id}/report",
    dependencies=[Depends(require_feature("sequencing_batch"))],
)
async def download_batch_report(batch_id: str, request: Request,
                                user: Optional[User] = Depends(get_current_user)):
    """下载批量分析整理包（同一质粒一个文件夹、其下 正确/错误 按结论分置副本
    与各组分析报告 + 整理清单 + 结论回填的信息表）；生成 15 分钟后随缓存自动
    清理，可重复下载。终审 B-03：非创建者（且非管理员）一律 404——包内含
    全部原始数据，不暴露存在性。"""
    gone = HTTPException(status_code=404,
                         detail="整理包不存在或已过期（生成 15 分钟后自动清理，请重新批量分析）")
    await run_in_threadpool(_sweep_reports)
    rec = await run_in_threadpool(batch_report_store.load_meta, batch_id)
    if rec is None or time.time() - float(rec.get("created_ts", 0)) > sequencing_routes.BATCH_TTL:
        raise gone
    owner = rec.get("owner_id")
    is_admin = bool(user and user.is_admin)
    if owner:
        allowed = is_admin or (user is not None and user.id == owner)
    else:
        # 匿名批次：整理包同样绑定整批访问令牌（此前知道 batch_id 即可下载）
        allowed = is_admin or _token_hash_matches(_request_access_token(request),
                                                  rec.get("access_token_hash"))
    if not allowed:
        raise gone
    # 先打开再流式发送：发送途中即使被清理删除，已打开的句柄仍可读完；
    # 不再把整个 zip 读进内存
    fh = await run_in_threadpool(batch_report_store.open_zip, batch_id)
    if fh is None:
        raise gone
    size = os.fstat(fh.fileno()).st_size

    def _chunks():
        with fh:
            while True:
                buf = fh.read(1024 * 1024)
                if not buf:
                    break
                yield buf

    # HTTP 头仅限 latin-1：中文文件名走 RFC 5987 filename*，ASCII 名兜底
    ascii_name = f"seqbatch_{batch_id[-6:]}.zip"
    utf8_name = quote(f"{rec.get('zip_name') or 'seqbatch'}.zip")
    return StreamingResponse(
        _chunks(),
        media_type="application/zip",
        headers={"Content-Disposition":
                 f"attachment; filename={ascii_name}; filename*=UTF-8''{utf8_name}",
                 "Content-Length": str(size)},
    )

@router.post(
    "/designs/{design_id}/sequencing/analyze",
    dependencies=[Depends(require_feature("sequencing"))],
)
async def analyze_design_sequencing(
    design_id: str,
    files: List[UploadFile] = File(..., description="一个或多个 .ab1 文件"),
    min_q: int = Form(default=20, ge=0, le=60, description="末端修剪质量阈值（0-60）"),
    allow_decompose: bool = Form(default=True, description="允许对混合样品执行 tracy 解卷积"),
    reliable_zone: str = Form(default="", description="read 可信区边界：首端硬区,首端灰区,尾端灰区,尾端硬区（read 坐标 bp），留空用默认 30,60,700,900"),
    user: Optional[User] = Depends(get_current_user),
):
    """上传 AB1 文件，对设计结果（构建体序列）做全自动测序验证"""
    from app.routes.design_routes import _ensure_design_access, _load
    from app.routes.models import DesignStatus

    zone = parse_zone_form(reliable_zone)
    result = _ensure_design_access(_load(design_id), user)

    if result.status != DesignStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Design not completed")

    reference = result.construct_sequence or result.optimized_sequence or ""
    # 有构建体序列即环状质粒；仅优化序列（无载体）时是线性片段
    return await _analyze_endpoint(
        reference, result.vector_name or "Construct",
        list(result.construct_features or []), files, min_q, allow_decompose, user=user,
        circular=bool(result.construct_sequence), zone=zone,
    )

@router.post(
    "/vectors/{vector_id}/sequencing/analyze",
    dependencies=[Depends(require_feature("sequencing"))],
)
async def analyze_vector_sequencing(
    vector_id: str,
    files: List[UploadFile] = File(...),
    min_q: int = Form(default=20, ge=0, le=60),
    allow_decompose: bool = Form(default=True),
    reliable_zone: str = Form(default="", description="read 可信区边界：首端硬区,首端灰区,尾端灰区,尾端硬区（read 坐标 bp），留空用默认 30,60,700,900"),
    user: Optional[User] = Depends(get_current_user),
):
    """上传 AB1 文件，对载体库中有序列的载体做全自动测序验证"""
    zone = parse_zone_form(reliable_zone)
    library = get_vector_library()
    vector = library.get_vector(vector_id)
    if not vector:
        raise HTTPException(status_code=404, detail="Vector not found")

    features = [
        {"name": e.name, "type": e.element_type.value, "start": e.start,
         "end": e.end, "strand": e.strand, "description": e.description}
        for e in vector.elements
    ]
    # 载体库条目均为环状质粒
    return await _analyze_endpoint(
        vector.sequence, vector.name, features, files, min_q, allow_decompose, user=user,
        circular=True, zone=zone,
    )

@router.get("/sequencing/analyses")
async def list_analyses(limit: int = 200, offset: int = 0,
                        user: Optional[User] = Depends(get_current_user)):
    """历史分析列表（摘要，按时间倒序；不含 reads/变体明细）。

    分页：limit（默认 200，0 不限）+ offset。属主校验：只返回自己创建的
    （管理员全可见）。终审 B-02：列表不再把匿名记录的 ID 交出去——匿名
    记录凭创建响应下发的 access_token 访问，匿名列表返回空；改造前的
    无 token 遗留记录只对管理员可见。"""
    _sweep_expired()
    if sequencing_store.db_enabled():
        return sequencing_store.list_records(
            user, is_admin=bool(user and user.is_admin),
            limit=limit, offset=offset)
    # 终审 B-02：匿名记录不进列表（持有 token 也不列——列表接口不该交出
    # 他人记录的 ID）；无 token 遗留记录同样不列（管理员全可见）
    # 终审 C-07：取值快照在锁内完成，避免与写线程并发遍历
    is_admin = bool(user and user.is_admin)
    with _STORE_LOCK:
        items = sorted(
            (r for r in _ANALYSES.values()
             if is_admin or (r.get("owner_id") is not None and _can_access(r, user))),
            key=lambda r: r["created_at"], reverse=True)
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
async def get_analysis(analysis_id: str, request: Request,
                       user: Optional[User] = Depends(get_current_user)):
    """获取分析结果（不含峰图原始数据；非创建者且非管理员返回 403）"""
    return _summary(await _aget_analysis(analysis_id, user,
                                         _request_access_token(request)))

@router.get("/sequencing/analyses/{analysis_id}/trace/{read_index}")
async def get_read_trace(analysis_id: str, read_index: int, request: Request,
                         user: Optional[User] = Depends(get_current_user)):
    """获取单条 read 的峰图数据（四通道 + 碱基 + 质量值 + 峰位置）"""
    record = await _aget_analysis(analysis_id, user, _request_access_token(request))
    trace_data = record.get("_trace_data", {})
    if not trace_data:
        raise HTTPException(status_code=404,
                            detail="峰图原始数据已从内存清理（仅保留最近分析），结论仍可查看")
    if read_index not in trace_data:
        raise HTTPException(status_code=404, detail="Read trace not found")
    return trace_data[read_index]

@router.get("/sequencing/analyses/{analysis_id}/consensus/export")
async def export_consensus(analysis_id: str, request: Request,
                           format: str = "fasta",
                           covered_only: bool = False,
                           user: Optional[User] = Depends(get_current_user)):
    """导出拼接结果（共识序列，FASTA / GenBank）

    covered_only=True 按「部分测序是常规策略」只导出 read 实测覆盖的区域：
    - FASTA：每段连续覆盖区间一条记录（参考坐标入头，可回溯源位置）；
    - GenBank：全长保留、未测位置 N 屏蔽（特征坐标不位移），实测段以
      misc_feature 注记。无覆盖区间信息时回退全量导出。
    """
    record = await _aget_analysis(analysis_id, user, _request_access_token(request))
    # 审查 02 M5：N 屏蔽与排版对 200 kb 序列是秒级 CPU，移出事件循环
    content, ext, safe_name = await run_in_threadpool(
        sequencing_routes._render_consensus_export,
        record, analysis_id, format, covered_only)
    # 头值仅限 latin-1：safe_name 白名单放行了中文样品名，必须走
    # RFC 5987 filename*（此前直接拼 filename= 会让中文样品 500）
    return PlainTextResponse(
        content=content,
        media_type="text/plain",
        headers={"Content-Disposition":
                 content_disposition(f"{safe_name}.{ext}",
                                     ascii_fallback=f"consensus.{ext}")},
    )

@router.delete("/sequencing/analyses/{analysis_id}")
async def delete_analysis(analysis_id: str, request: Request,
                          user: Optional[User] = Depends(get_current_user)):
    await _aget_analysis(analysis_id, user, _request_access_token(request))
    with _STORE_LOCK:  # 终审 C-07
        _ANALYSES.pop(analysis_id, None)
    await run_in_threadpool(sequencing_store.delete_record, analysis_id)
    return {"deleted": True, "analysis_id": analysis_id}

