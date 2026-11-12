"""Sanger 测序分析端点（从 sequencing_routes.py 迁出，纯搬运）

实现/helper（存储、并发闸、批量管线等）仍在 app.routes.sequencing_routes：
- 测试大量以 sequencing_routes 为 patch 面（_ANALYSES / 各上限常量），
  保持不动可零测试改动；
- app.routes.sequencing_session_routes 以 sr._batch_pipeline 等复用批量流。

本模块只挂路由：main.py 从这里取 sequencing router。
"""

import json
import os
import secrets
import time
from datetime import datetime
from typing import List, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.auth.jwt_auth import User, get_current_user
from app import batch_report_store, sequencing_store
from app.gating import require_feature
from app.vector_library_cache import get_vector_library
from app.design_service import _vector_is_circular
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


def _owner_id(user: Optional[User]) -> Optional[str]:
    """范围记忆的 owner（goal v3.1 X5）：登录用户取其 ID，匿名返回 None。

    None 由 store 归一为 `ANONYMOUS_OWNER`——单用户部署下所有记忆仍按参考
    隔离；多用户库则各记各的，不会串味（SM1）。
    """
    return getattr(user, "id", None) if user is not None else None


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
    cds_mode: bool = Form(default=False, description="目的基因模式：按文件名识别参考图谱内的目的 CDS，以其+接合区为需测范围分析（W8 起全质粒比对、不裁参考）"),
    cds_name: str = Form(default="", description="显式指定的目的 CDS 名称（前端下拉选择时传入）；留空按文件名自动匹配"),
    scope: str = Form(default="", description="需测范围（W10，可选）：元件名 / 301-900 / 多段(;) / 「全质粒」「插入片段」「接合区」；声明后结论只对范围负责"),
    primer: str = Form(default="", description='引物校验（T17，可选）：JSON {"name": "M13F", "seq"?: "ACGT...", "direction"?: "+"}；seq 省略时按 name 查 data/primers/*.yaml 引物库'),
    user: Optional[User] = Depends(get_current_user),
):
    """上传参考序列文件 + .ab1 测序文件（可来自同一文件夹），全自动测序验证。

    样品名取参考文件名主干，特征注释直接来自参考文件（GenBank/SnapGene 特征表）。
    cds_mode=True 时启用目的基因模式（core.sanger.cds_extract）：从参考特征
    定位目的 CDS（cds_name 显式指定优先，否则按文件名自动匹配），以其编码
    序列为比对参考——坐标即 CDS 内坐标，蛋白结论由 cds_reports 按伪全长
    特征自动产出。
    """
    from core.sanger.reference_parser import ReferenceParseError
    from core.sanger import cds_extract

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
    scope_obj = None
    full_ref = None
    full_circular = False
    # W10（PL §C5）：用户 Form 声明的范围——来源优先级 user > cds_target，
    # 显式范围存在时覆盖目的基因模式的自动范围
    if scope.strip():
        from core.sanger.scope import parse_scope_cell
        scope_obj, scope_err = parse_scope_cell(scope, features, len(ref_seq))
        if scope_obj is None and scope_err:
            raise HTTPException(status_code=400, detail=scope_err)
    if scope_obj is None and not cds_mode:
        # W11 / v3.1 X5：范围记忆——同一用户在同一参考上的上次**显式声明**
        # 范围自动带出。source 保留原来源，另标 remembered=true 与
        # reason「沿用上次范围」（headline 据此加注记、前端显示「清除」）
        from core.sanger.scope import Scope as _Scope
        _mem = await run_in_threadpool(
            sequencing_store.load_scope_memory, ref_seq, _owner_id(user))
        if _mem:
            scope_obj = _Scope.from_dict(_mem)
            scope_obj.reason = "沿用上次范围"
            scope_obj.remembered = True
    if cds_mode and scope_obj is None:
        # 目的基因模式（goal v3 W8，CM P0-A/P0-B/P1-C/P1-F）：不再裁参考——
        # 全质粒按真实拓扑比对，scope = 目标 CDS + 接合区（source=cds_target），
        # 全部注释保留。只有质粒超 MAX_REF_BP 时才裁剪取窗（侧翼
        # max(1500, 最长 read+200)、环状绕回、跨原点 CDS 连续化），窗口外
        # 溢出段由 full_ref 重定位兜底（「窗口外已定位」不算异常）。
        # prepare_scope_mode 契约：未裁剪时原样返回同一 ref 对象——据此
        # 判定是否发生裁剪，裁剪时把完整质粒交给管线做窗口外重定位
        ref_stem = os.path.splitext(os.path.basename(ref_name))[0]
        try:
            ref2, feats2, scope_obj, circ2 = cds_extract.prepare_scope_mode(
                ref_seq, features, ref_stem,
                [r.filename or "" for r in reads], cds_name.strip() or None,
                circular=_reference_circular(topology),
                max_ref_bp=sequencing_routes.MAX_REF_BP)
        # 用户已显式声明范围（user > cds_target）时不再裁剪：窗口坐标会
        # 使 scope 错位；超大参考直接走下方 413 诚实拒绝
        except cds_extract.CdsResolveError as e:
            detail = str(e)
            if e.candidates:
                detail += "；可选 CDS：" + "、".join(c["name"] for c in e.candidates[:10])
            raise HTTPException(status_code=400, detail=detail)
        if ref2 is not ref_seq:
            full_ref = ref_seq
            full_circular = _reference_circular(topology)
        ref_seq, features, topology = ref2, feats2, circ2
    if len(ref_seq) < 50:
        raise HTTPException(status_code=400, detail=f"参考序列过短（{len(ref_seq)} bp），无法比对")
    # 终审 C-01：长度上限（见 _analyze_endpoint 同款校验）。W8 裁剪路径
    # 的窗口已在 prepare_scope_mode 内压到上限以内，此处只拦未裁剪路径
    if len(ref_seq) > sequencing_routes.MAX_REF_BP:
        raise HTTPException(
            status_code=413,
            detail=f"参考序列过长（{len(ref_seq)} bp > {sequencing_routes.MAX_REF_BP} bp）：请截取待验证区段后重新分析")

    # T17 引物校验（可选）：JSON 解析失败按未提供处理（不阻塞分析）
    primer_dict = None
    if primer.strip():
        try:
            data = json.loads(primer)
            if isinstance(data, dict):
                primer_dict = data
        except ValueError:
            raise HTTPException(status_code=400, detail="引物参数格式错误（应为 JSON 对象）")

    sample_name = os.path.splitext(os.path.basename(ref_name))[0][:60] or "reference"
    return await _analyze_endpoint(ref_seq, sample_name, features, reads, min_q,
                                   allow_decompose, user=user,
                                   circular=_reference_circular(topology), zone=zone,
                                   primer=primer_dict, scope=scope_obj,
                                   full_ref=full_ref, full_circular=full_circular)


@router.post(
    "/sequencing/parse-reference",
    dependencies=[Depends(require_feature("sequencing"))],
)
async def parse_sequencing_reference(
    reference: UploadFile = File(..., description="参考序列文件（.gb/.gbk/.genbank/.fasta/.fa/.fna/.dna）"),
    read_names: List[str] = Form(default=[], description="已暂存 read 的文件名（自动匹配的辅助线索，可省略）"),
    user: Optional[User] = Depends(get_current_user),
):
    """目的基因模式的前置解析：候选 CDS 清单 + 按文件名的自动匹配建议。

    轻量只读端点（不落库、不分析）：解析参考特征 → 嵌套去重后的 CDS/gene
    候选列表 → 参考文件名与 read 文件名的自动匹配建议。前端在「目的基因
    模式」开关打开且参考暂存后调用，渲染 CDS 下拉并预选 auto_match。"""
    from core.sanger.reference_parser import ReferenceParseError
    from core.sanger import cds_extract

    ref_name = reference.filename or "reference.gb"
    ref_bytes = await _read_limited(reference)
    if not ref_bytes:
        raise HTTPException(status_code=400, detail="参考文件为空")
    try:
        ref_seq, features, topology = await _run_heavy(
            parse_reference_topology, ref_name, ref_bytes)
    except ReferenceParseError as e:
        raise HTTPException(status_code=400, detail=str(e))
    candidates = cds_extract.cds_candidates(features)
    ref_stem = os.path.splitext(os.path.basename(ref_name))[0]
    cand, note = cds_extract.resolve_auto(candidates, ref_stem, read_names)
    # W10（PL §C4）：范围预设与默认项——前端「验证范围」下拉数据源，
    # 默认项附带 reason（文件名命中 → 该 CDS；未命中 → 全质粒）
    scope_presets = [{"value": "全质粒", "label": "全质粒",
                      "reason": "整条参考都在需测范围"}]
    for c in candidates:
        scope_presets.append({
            "value": c["name"],
            "label": f"{c['name']}（{c['start']}–{c['end']}）",
            "reason": f"以「{c['name']}」及其接合区为需测范围",
        })
    default_preset = ({"value": cand["name"], "reason": note} if cand else
                      {"value": "全质粒",
                       "reason": "文件名未命中目标 CDS，默认按全质粒验证"})
    return {
        "reference_length": len(ref_seq),
        "circular": _reference_circular(topology),
        "candidates": [
            {"name": c["name"], "start": c["start"], "end": c["end"],
             "strand": c["strand"], "length": c["length"]}
            for c in candidates
        ],
        "auto_match": ({"name": cand["name"], "note": note} if cand else None),
        "note": note,
        "scope_presets": scope_presets,
        "default_preset": default_preset,
    }


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
    # _load 走存储层（SQLite/PG），移交线程池避免阻塞事件循环（KNOWN_ISSUES 2.1 口径）
    result = _ensure_design_access(await run_in_threadpool(_load, design_id), user)

    if result.status != DesignStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Design not completed")

    reference = result.construct_sequence or result.optimized_sequence or ""
    # T25.2：拓扑读参考——载体记录的真实 topology 是权威（防回潮「载体拓扑
    # 按真实值落盘」），不再以 insert_start != 1 推断。骨架参与组装 → 构建
    # 体随载体真实拓扑（线性骨架载体 → 线性构建体）；载体记录已删（回退
    # 纯插入片段）或仅优化序列 → 线性片段
    circular = False
    if result.construct_sequence:
        vector = get_vector_library().get_vector(result.vector_id)
        circular = (_vector_is_circular(vector)
                    if vector is not None else result.insert_start != 1)
    # W10（PL §C5）：设计流把插入边界作为 design_insert 来源传入——
    # 声明范围后结论只对「插入片段 ± 接合区」负责；插入边界未知或即
    # 全长时不声明（退回未声明口径）
    scope_obj = None
    _is, _ie = getattr(result, "insert_start", None), getattr(result, "insert_end", None)
    if _is and _ie and not (_is == 1 and _ie == len(reference)):
        from core.sanger.scope import infer_scope
        scope_obj = infer_scope("", [], [], insert_range=(int(_is), int(_ie)),
                                ref_length=len(reference))
    return await _analyze_endpoint(
        reference, result.vector_name or "Construct",
        list(result.construct_features or []), files, min_q, allow_decompose, user=user,
        circular=circular, zone=zone, scope=scope_obj,
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
async def list_analyses(limit: int = Query(default=200, ge=0, le=1000), offset: int = Query(default=0, ge=0),
                        user: Optional[User] = Depends(get_current_user)):
    """历史分析列表（摘要，按时间倒序；不含 reads/变体明细）。

    分页：limit（默认 200，0 不限，上限 1000）+ offset。属主校验：只返回自己创建的
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


class VerdictRecomputeRequest(BaseModel):
    """范围重算请求体（goal v3 W11）：scope 为范围文本（元件名/区间/
    「全质粒」「插入片段」等），留空退回未声明口径"""
    scope: str = ""


@router.post("/sequencing/analyses/{analysis_id}/verdict")
async def recompute_verdict(analysis_id: str, request: Request,
                            body: VerdictRecomputeRequest,
                            user: Optional[User] = Depends(get_current_user)):
    """按新范围从存档 result 重算判定并落库（goal v3 W11，PL §C6）。

    build_verdict 是纯函数（W1）：不重跑比对，毫秒级返回；verdict/scope
    更新进存档，结论头行按新 verdict.headline 重建（W5 同源，正文保留）；
    追加 scope_history（时间/用户/新旧范围）；声明范围按参考指纹覆盖
    范围记忆。鉴权与读取同 GET 端点（属主或访问令牌）。"""
    record = await _aget_analysis(analysis_id, user,
                                  _request_access_token(request))
    reference = record.get("reference") or ""
    if len(reference) < 50:
        raise HTTPException(status_code=400, detail="存档记录缺参考序列，无法重算范围")
    scope_text = (body.scope or "").strip()
    old_scope = dict(((record.get("verdict") or {}).get("scope")) or {})
    if scope_text:
        from core.sanger.scope import coerce_scope
        scope_obj = coerce_scope(scope_text, record.get("features") or [],
                                 len(reference))
        if scope_obj is None:
            raise HTTPException(status_code=400,
                                detail=f"范围无法识别：「{scope_text}」"
                                       "（可用元件名 / 数字区间 / 「全质粒」「插入片段」）")
    else:
        scope_obj = None  # 显式清空声明 → 退回未声明（measured）口径
    from core.sanger.judge import rejudge

    def _recompute():
        # W14：人工确认记录随重算传入（换范围不清留痕），复核队列同步刷新
        from core.sanger.review import build_queue
        _decisions = list(record.get("review_decisions") or [])
        # S6 唯一判定入口：重算 Verdict 并重新拼装结论（judge.rejudge）
        verdict, record["conclusion"] = rejudge(
            record, len(reference), scope_obj,
            review_decisions=_decisions or None)
        record["verdict"] = verdict
        record["review_queue"] = build_queue(record, _decisions or None)
        hist = list(record.get("scope_history") or [])
        hist.append({
            "at": datetime.now().isoformat(timespec="seconds"),
            "user": (getattr(user, "username", None) or getattr(user, "email", None)
                     or ("匿名" if user is None else str(getattr(user, "id", "?")))),
            "old": old_scope or None,
            "new": verdict.get("scope") or {},
        })
        record["scope_history"] = hist
        sequencing_store.persist_record(record)
        return verdict, hist

    verdict, hist = await run_in_threadpool(_recompute)
    # 记忆更新：声明的范围按参考指纹覆盖旧值（清声明不覆盖，保留上次）
    new_scope = verdict.get("scope") or {}
    if new_scope.get("declared"):
        await run_in_threadpool(sequencing_store.save_scope_memory,
                                reference, new_scope, _owner_id(user))
    return {"verdict": verdict, "conclusion": record.get("conclusion"),
            "scope_history": hist}


class ReviewConfirmRequest(BaseModel):
    """复核确认请求体（goal v3 W14，PL F3）：对复核队列某位点一键定夺。

    decision：artifact（伪影，移出阻断）/ mutation（真实突变，升级确证）；
    note 可选备注。留痕 review_decisions=[{ref_pos, decision, by, at,
    note}]，重算 verdict 后 headline 追加「人工确认 N 处」。"""
    ref_pos: int
    decision: str
    note: str = ""


@router.post("/sequencing/analyses/{analysis_id}/review")
async def confirm_review(analysis_id: str, request: Request,
                         body: ReviewConfirmRequest,
                         user: Optional[User] = Depends(get_current_user)):
    """复核队列一键确认并重算判定（goal v3 W14，PL F3）。

    纯函数重算（build_verdict 接 review_decisions）：伪影条目移出 blocking
    （info 留痕 reviewed_artifact），突变条目升级确证（fail_impact）；结论
    头行同步 verdict.headline（自带「人工确认 N 处」）；复核队列按已确认
    位点刷新。留痕随 payload 落库（by/at/note 可审计）。"""
    if body.decision not in ("artifact", "mutation"):
        raise HTTPException(status_code=400,
                            detail="decision 只支持 artifact（伪影）或 mutation（真实突变）")
    record = await _aget_analysis(analysis_id, user,
                                  _request_access_token(request))
    reference = record.get("reference") or ""
    if len(reference) < 50:
        raise HTTPException(status_code=400, detail="存档记录缺参考序列，无法重算")
    by = (getattr(user, "username", None) or getattr(user, "email", None)
          or ("匿名" if user is None else str(getattr(user, "id", "?"))))
    _scope_d = ((record.get("verdict") or {}).get("scope")) or None

    def _confirm():
        from core.sanger.scope import Scope as _Scope
        from core.sanger.judge import rejudge
        from core.sanger.review import build_queue, apply_review_decision
        decisions = list(record.get("review_decisions") or [])
        apply_review_decision(decisions, body.ref_pos, body.decision, by,
                              body.note)
        scope_obj = (_Scope.from_dict(_scope_d)
                     if _scope_d and _scope_d.get("declared") else None)
        verdict, record["conclusion"] = rejudge(
            record, len(reference), scope_obj, review_decisions=decisions)
        record["verdict"] = verdict
        record["review_decisions"] = decisions
        record["review_queue"] = build_queue(record, decisions)
        sequencing_store.persist_record(record)
        return verdict

    verdict = await run_in_threadpool(_confirm)
    return {"verdict": verdict, "conclusion": record.get("conclusion"),
            "review_decisions": record.get("review_decisions"),
            "review_queue": record.get("review_queue")}


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
                           mark_unverified: bool = True,
                           user: Optional[User] = Depends(get_current_user)):
    """导出拼接结果（共识序列，FASTA / GenBank）

    covered_only=True 按「部分测序是常规策略」只导出 read 实测覆盖的区域：
    - FASTA：每段连续覆盖区间一条记录（参考坐标入头，可回溯源位置）；
    - GenBank：全长保留、未测位置 N 屏蔽（特征坐标不位移），实测段以
      misc_feature 注记。无覆盖区间信息时回退全量导出。
    mark_unverified=True（默认，goal T3）：verified 覆盖外的实测碱基以
    小写标注——「测到但没读清/不可信」不再与验证过的序列无差别交付。
    """
    record = await _aget_analysis(analysis_id, user, _request_access_token(request))
    # 审查 02 M5：N 屏蔽与排版对 200 kb 序列是秒级 CPU，移出事件循环
    content, ext, safe_name = await run_in_threadpool(
        sequencing_routes._render_consensus_export,
        record, analysis_id, format, covered_only, mark_unverified)
    # 头值仅限 latin-1：safe_name 白名单放行了中文样品名，必须走
    # RFC 5987 filename*（此前直接拼 filename= 会让中文样品 500）
    return PlainTextResponse(
        content=content,
        media_type="text/plain",
        headers={"Content-Disposition":
                 content_disposition(f"{safe_name}.{ext}",
                                     ascii_fallback=f"consensus.{ext}")},
    )

@router.get("/sequencing/analyses/{analysis_id}/evidence-pack")
async def get_evidence_pack(analysis_id: str, request: Request,
                            format: str = "json",
                            user: Optional[User] = Depends(get_current_user)):
    """证据包（goal T22；A 5.4）：blocking 项与建议核对/复测变体的逐 read
    峰图小图 + 调用/Q/峰比/P(真实) 表与判定理由。

    format=json（默认）：结构化数据（前端证据面板多 read 堆叠显示）；
    format=md：内联小图的 Markdown 报告，单样品下载。
    """
    from core.sanger.evidence_pack import build_pack_data, render_markdown
    record = await _aget_analysis(analysis_id, user,
                                  _request_access_token(request))
    pack = await run_in_threadpool(build_pack_data, record)
    if format.lower() in ("md", "markdown"):
        md = await run_in_threadpool(render_markdown, pack)
        return PlainTextResponse(
            content=md,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition":
                     content_disposition(f"{record.get('sample_name') or 'analysis'}"
                                         "-evidence-pack.md",
                                         ascii_fallback="evidence-pack.md")},
        )
    return pack

@router.delete("/sequencing/analyses/{analysis_id}")
async def delete_analysis(analysis_id: str, request: Request,
                          user: Optional[User] = Depends(get_current_user)):
    await _aget_analysis(analysis_id, user, _request_access_token(request))
    with _STORE_LOCK:  # 终审 C-07
        _ANALYSES.pop(analysis_id, None)
    await run_in_threadpool(sequencing_store.delete_record, analysis_id)
    return {"deleted": True, "analysis_id": analysis_id}


@router.delete("/sequencing/scope-memory")
async def delete_scope_memory(reference_hash: str = Query(
                                  ..., min_length=16,
                                  description="参考序列 sha256 指纹（64 位小写十六进制）"),
                              user: Optional[User] = Depends(get_current_user)):
    """清除当前用户在该参考上的范围记忆（goal v3.1 X5 §3.5）。

    只删本人那一行（键含 owner），不影响其他用户；幂等——没有记录时
    `deleted=false` 而非报错。前端范围条「沿用上次范围 · 清除」调用它。
    """
    deleted = await run_in_threadpool(
        sequencing_store.clear_scope_memory_hash, reference_hash, _owner_id(user))
    return {"deleted": bool(deleted), "reference_hash": reference_hash}

