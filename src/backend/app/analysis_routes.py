"""
序列分析和导出 API 路由
"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse, JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool
from typing import Annotated, List, Dict, Optional
from pydantic import BaseModel, BeforeValidator, Field, model_validator
import io
import re

from core.sequence_analysis import (
    SequenceAnalyzer,
    RestrictionSiteAnalyzer,
    ORFPredictor,
    GCAnalyzer
)
from core.export_formats import (
    ExportManager,
    ExportData,
    SequenceFeature,
    create_export_data_from_design,
    create_export_data_from_vector
)
from app.auth.jwt_auth import User, get_current_user
from app.filename import content_disposition, sanitize_filename
from app.cache import ANALYSIS_ENZYMES_VERSION, cached

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


# ==================== 请求模型 ====================

# 终审 C-06：分析类接口此前全部不限长、窗口/步长下限为 1——实测 1Mb
# 序列 + window_size=1/step_size=1 → 200 且响应体 108 MB / 3.0 s，单请求
# 即可拖垮服务。这里统一上限（与 C-03 的设计序列上限同数量级）并给窗口
# 类参数设合理下限（窗口 <10bp 无统计意义）。
MAX_ANALYSIS_SEQ_BP = 200_000
MIN_WINDOW_SIZE = 10
# GC 滑窗输出放大：step_size=1 时 200 kb 序列产出 20 万个区间（实测 13 MB
# 响应）。步长不得小于窗口的 1/10，且区间总数封顶
MIN_STEP_WINDOW_RATIO = 10
MAX_GC_REGIONS = 20_000

_WS_RE = re.compile(r"\s+")


def _strip_whitespace(v):
    """粘贴的序列常带换行/空格/制表符（FASTA 分行），原样分析会让位点
    跨行漏检、ORF 坐标与长度偏移、GC 被稀释。在校验长度之前统一去掉"""
    return _WS_RE.sub("", v) if isinstance(v, str) else v


# 全部分析/导出请求的序列字段共用：先去空白，再做 max_length 等校验
SeqStr = Annotated[str, BeforeValidator(_strip_whitespace)]


class SequenceAnalysisRequest(BaseModel):
    """序列分析请求"""
    sequence: SeqStr = Field(..., max_length=MAX_ANALYSIS_SEQ_BP, description="DNA 序列")
    sequence_type: str = Field(default="dna", description="序列类型（dna/amino_acid，当前分析均按 DNA 处理）")
    check_restriction: bool = Field(default=True, description="是否检测限制性位点")
    check_orf: bool = Field(default=True, description="是否预测 ORF")
    check_gc: bool = Field(default=True, description="是否分析 GC 含量")
    enzymes: Optional[List[str]] = Field(default=None, max_length=100,
                                         description="要检测的酶列表")


class RestrictionSitesRequest(BaseModel):
    """限制性酶切位点请求（body 传递，避免长序列进入 URL）"""
    sequence: SeqStr = Field(..., max_length=MAX_ANALYSIS_SEQ_BP, description="DNA 序列")
    enzymes: Optional[List[str]] = Field(default=None, max_length=100,
                                         description="要检测的酶列表，默认全部常用酶")


class ORFRequest(BaseModel):
    """ORF 预测请求"""
    sequence: SeqStr = Field(..., max_length=MAX_ANALYSIS_SEQ_BP, description="DNA 序列")
    min_length: int = Field(default=150, ge=30, description="最小 ORF 长度（碱基数）")


class DigestRequest(BaseModel):
    """酶切消化模拟请求"""
    sequence: SeqStr = Field(..., max_length=MAX_ANALYSIS_SEQ_BP, description="DNA 序列")
    enzymes: List[str] = Field(..., min_length=1, max_length=6, description="用于模拟消化的酶（1-6 个）")
    circular: bool = Field(
        default=False,
        description="是否按环状 DNA（质粒）消化：首尾片段相连、跨原点位点也会被识别；"
                    "拓扑未知时保持默认的线性",
    )


class GCAnalysisRequest(BaseModel):
    """GC 含量分析请求"""
    sequence: SeqStr = Field(..., max_length=MAX_ANALYSIS_SEQ_BP, description="DNA 序列")
    window_size: int = Field(default=100, ge=MIN_WINDOW_SIZE, le=MAX_ANALYSIS_SEQ_BP,
                             description="滑动窗口大小")
    step_size: int = Field(default=50, ge=1, description="步长（≥ 窗口/10）")

    @model_validator(mode="after")
    def _limit_regions(self):
        min_step = -(-self.window_size // MIN_STEP_WINDOW_RATIO)
        if self.step_size < min_step:
            raise ValueError(
                f"step_size 不得小于窗口的 1/{MIN_STEP_WINDOW_RATIO}（当前窗口至少 {min_step}）")
        regions = max(0, len(self.sequence) - self.window_size) // self.step_size + 1
        if regions > MAX_GC_REGIONS:
            raise ValueError(
                f"滑窗区间数 {regions} 超过上限 {MAX_GC_REGIONS}，请增大 step_size")
        return self


class CompatibilityRequest(BaseModel):
    """克隆兼容性检查请求"""
    insert_sequence: SeqStr = Field(..., max_length=MAX_ANALYSIS_SEQ_BP, description="插入片段序列")
    vector_sequence: SeqStr = Field(..., max_length=MAX_ANALYSIS_SEQ_BP, description="载体序列")
    enzymes: List[str] = Field(..., max_length=50, description="计划使用的酶列表")


class ExportRequest(BaseModel):
    """导出请求"""
    name: str = Field(..., max_length=100, description="序列名称")
    sequence: SeqStr = Field(..., max_length=MAX_ANALYSIS_SEQ_BP, description="DNA 序列")
    features: List[Dict] = Field(default=[], max_length=2000, description="序列特征")
    description: str = Field(default="", max_length=2000, description="描述")
    is_circular: bool = Field(default=True, description="是否环状")
    format: str = Field(default="genbank", description="导出格式")


# ==================== 序列分析 API ====================

@router.post("/analyze")
async def analyze_sequence(request: SequenceAnalysisRequest) -> Dict:
    """
    综合序列分析
    
    包括：
    - 限制性酶切位点
    - ORF 预测
    - GC 含量分析

    长序列分析为 CPU 密集计算，移交线程池执行，避免阻塞事件循环
    导致单核机器全站卡死（KNOWN_ISSUES 2.1）。
    """
    analyzer = SequenceAnalyzer()
    result = await run_in_threadpool(
        analyzer.analyze,
        sequence=request.sequence,
        check_restriction=request.check_restriction,
        check_orf=request.check_orf,
        check_gc=request.check_gc,
        enzymes=request.enzymes
    )
    
    return {
        "sequence_length": result.sequence_length,
        "gc_content": result.gc_content,
        "coding_potential": result.coding_potential,
        "restriction_sites": [
            {
                "enzyme": site.enzyme,
                "site": site.site,
                "position": site.recognition_start,
                "strand": site.strand,
                "overhang": site.overhang
            }
            for site in result.restriction_sites
        ],
        "orfs": [
            {
                "start": orf.start,
                "end": orf.end,
                "strand": orf.strand,
                "length": orf.length,
                "frame": orf.frame,
                "protein_length": len(orf.protein_sequence),
                "start_codon": orf.start_codon,
                "stop_codon": orf.stop_codon,
                "gc_content": orf.gc_content,
                "is_complete": orf.is_complete
            }
            for orf in result.orfs[:10]  # 返回前 10 个最长 ORF
        ],
        "gc_distribution": [
            {
                "start": r.start,
                "end": r.end,
                "gc_content": r.gc_content,
                "is_extreme": r.is_extreme
            }
            for r in result.gc_distribution[:50]  # 限制返回数量
        ],
        "warnings": result.warnings
    }


@router.post("/restriction-sites")
async def find_restriction_sites(request: RestrictionSitesRequest) -> Dict:
    """
    查找限制性酶切位点

    Args:
        request: JSON body，含 sequence 与可选 enzymes

    Returns:
        发现的限制性位点列表
    """
    analyzer = RestrictionSiteAnalyzer()
    sites = await run_in_threadpool(analyzer.find_sites, request.sequence, request.enzymes)
    unique_sites = await run_in_threadpool(
        lambda: list(analyzer.find_unique_sites(request.sequence).keys())
    )

    return {
        "total": len(sites),
        "sites": [
            {
                "enzyme": site.enzyme,
                "recognition_sequence": site.site,
                "position": site.recognition_start,
                "end": site.recognition_end,
                "cut_position": site.cut_position,
                "strand": site.strand,
                "overhang_type": site.overhang
            }
            for site in sites
        ],
        "unique_sites": unique_sites
    }


@router.post("/orfs")
async def find_orfs(request: ORFRequest) -> Dict:
    """
    预测开放阅读框 (ORF)

    Args:
        request: JSON body，含 sequence 与 min_length

    Returns:
        ORF 列表（按长度排序）
    """
    predictor = ORFPredictor(min_length=request.min_length)
    orfs = await run_in_threadpool(predictor.find_orfs, request.sequence)
    
    return {
        "total": len(orfs),
        "orfs": [
            {
                "start": orf.start,
                "end": orf.end,
                "strand": orf.strand,
                "length": orf.length,
                "frame": orf.frame,
                "protein_sequence": orf.protein_sequence,
                "start_codon": orf.start_codon,
                "stop_codon": orf.stop_codon,
                "gc_content": round(orf.gc_content, 2),
                "is_complete": orf.is_complete
            }
            for orf in orfs
        ]
    }


@router.post("/digest")
async def simulate_digest(request: DigestRequest) -> Dict:
    """
    酶切消化模拟（完全消化，线性或环状）

    按所选酶在正向链上的切割位置切分序列，返回各片段的起止与大小，
    用于预测电泳条带。切割位置与 Bio.Restriction 同一口径（正向链切点，
    粘性末端的突出差异不计入片段大小）。

    circular=False（默认，拓扑未知时也按线性）：N 个切点 → N+1 个片段；
    切点落在序列之外（线性末端附近的 Type IIS 位点）不计。
    circular=True：首尾相连，N 个切点 → N 个片段，跨原点的片段
    start > end 并带 wraps_origin=True；只切一刀即线性化，得 1 个整长片段。
    """
    from core.enzyme_sites import get_enzyme, scan_sites

    seq = request.sequence.upper().replace("U", "T")
    n = len(seq)
    circular = request.circular

    specs = []
    for enzyme in request.enzymes:
        spec = get_enzyme(enzyme)
        if spec is None:
            raise HTTPException(status_code=400, detail=f"未知限制酶: {enzyme}")
        specs.append(spec)

    def _collect():
        found: Dict[int, List[str]] = {}  # 边界（切在第 b 位之后，1..n）→ 切割酶
        with_sites: List[str] = []
        for spec in specs:
            hit = False
            for _a, _strand, cut_fwd, _cut_rev in scan_sites(seq, spec, circular):
                if circular:
                    b = (cut_fwd - 1) % n + 1
                elif 0 < cut_fwd < n:
                    b = cut_fwd
                else:
                    continue  # 线性：切点在序列外或恰在末端，不产生新片段
                hit = True
                names = found.setdefault(b, [])
                if spec["name"] not in names:  # 回文位点正反链同一切点
                    names.append(spec["name"])
            if hit and spec["name"] not in with_sites:
                with_sites.append(spec["name"])
        return found, with_sites

    cuts, enzymes_with_sites = await run_in_threadpool(_collect)

    if not cuts:
        return {
            "total_fragments": 0,
            "fragments": [],
            "enzymes_with_sites": [],
            "circular": circular,
            "message": "所选酶在序列中均无切割位点",
        }

    bounds = sorted(cuts)
    fragments = []
    if circular:
        # 每个片段从一个切点之后到下一个切点；最后一段绕过原点回到第一个切点
        for i, b in enumerate(bounds):
            nxt = bounds[(i + 1) % len(bounds)]
            start = b % n + 1
            size = (nxt - b) % n or n
            frag = {
                "start": start,
                "end": nxt,
                "size": size,
                "cut_by": list(cuts[nxt]),
            }
            if start > nxt:
                frag["wraps_origin"] = True
            fragments.append(frag)
    else:
        prev = 0
        for b in bounds:
            fragments.append({
                "start": prev + 1,
                "end": b,
                "size": b - prev,
                "cut_by": list(cuts[b]),
            })
            prev = b
        fragments.append({
            "start": prev + 1,
            "end": n,
            "size": n - prev,
            "cut_by": [],
        })

    return {
        "total_fragments": len(fragments),
        "fragments": fragments,
        "enzymes_with_sites": enzymes_with_sites,
        "circular": circular,
    }


@router.post("/gc-analysis")
async def analyze_gc(request: GCAnalysisRequest) -> Dict:
    """
    GC 含量分析

    Args:
        request: JSON body，含 sequence / window_size / step_size

    Returns:
        GC 含量分布
    """
    analyzer = GCAnalyzer(window_size=request.window_size, step_size=request.step_size)
    total_gc, regions = await run_in_threadpool(analyzer.analyze, request.sequence)
    
    extremes = [r for r in regions if r.is_extreme]
    
    return {
        "total_gc_content": round(total_gc, 2),
        "total_regions": len(regions),
        "extreme_regions": len(extremes),
        "distribution": [
            {
                "start": r.start,
                "end": r.end,
                "gc_content": round(r.gc_content, 2),
                "is_extreme": r.is_extreme
            }
            for r in regions
        ],
        "extremes": [
            {
                "start": r.start,
                "end": r.end,
                "gc_content": round(r.gc_content, 2)
            }
            for r in extremes
        ]
    }


# ==================== 导出 API ====================

@router.get("/export/formats")
async def get_export_formats() -> List[Dict]:
    """获取支持的导出格式列表"""
    return ExportManager.get_supported_formats()


@router.post("/export")
async def export_sequence(request: ExportRequest):
    """
    导出序列到指定格式
    
    支持格式：
    - genbank: GenBank 格式 (.gb)
    - snapgene: SnapGene 格式 (.dna)
    - benchling: Benchling JSON 格式
    - fasta: FASTA 格式
    - sbol: SBOL 格式
    """
    # 构建导出数据
    features = [
        SequenceFeature(
            name=f.get("name", "feature"),
            feature_type=f.get("type", "misc_feature"),
            start=f.get("start", 1),
            end=f.get("end", 100),
            strand=f.get("strand", "+"),
            color=f.get("color", "#4A90D9"),
            description=f.get("description", "")
        )
        for f in request.features
    ]
    
    export_data = ExportData(
        name=request.name,
        sequence=request.sequence,
        features=features,
        description=request.description,
        is_circular=request.is_circular
    )
    
    try:
        content, mime_type = await run_in_threadpool(ExportManager.export, export_data, request.format)

        # 确定文件扩展名
        extensions = {
            "genbank": ".gb",
            "snapgene": ".dna",
            "benchling": ".json",
            "fasta": ".fasta",
            "sbol": ".json"
        }
        ext = extensions.get(request.format, ".txt")
        # name 是用户可控输入：白名单过滤后组装（防 Content-Disposition 头注入）
        disposition = content_disposition(sanitize_filename(request.name) + ext)
        if isinstance(content, bytes):
            return StreamingResponse(
                io.BytesIO(content),
                media_type=mime_type,
                headers={"Content-Disposition": disposition}
            )
        else:
            return PlainTextResponse(
                content=content,
                media_type=mime_type,
                headers={"Content-Disposition": disposition}
            )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/export/all")
async def export_all_formats(request: ExportRequest):
    """
    导出所有格式为 ZIP 文件
    """
    features = [
        SequenceFeature(
            name=f.get("name", "feature"),
            feature_type=f.get("type", "misc_feature"),
            start=f.get("start", 1),
            end=f.get("end", 100),
            strand=f.get("strand", "+"),
            color=f.get("color", "#4A90D9"),
            description=f.get("description", "")
        )
        for f in request.features
    ]
    
    export_data = ExportData(
        name=request.name,
        sequence=request.sequence,
        features=features,
        description=request.description,
        is_circular=request.is_circular
    )
    
    zip_content = await run_in_threadpool(ExportManager.export_all, export_data)

    return StreamingResponse(
        io.BytesIO(zip_content),
        media_type="application/zip",
        headers={"Content-Disposition":
                 content_disposition(sanitize_filename(request.name) + "_exports.zip",
                                     ascii_fallback="exports.zip")}
    )


def _export_response(data, format: str, filename_base: str):
    """统一构建导出响应（文本或二进制内容）。"""
    content, mime_type = ExportManager.export(data, format)
    extensions = {
        "genbank": ".gb",
        "snapgene": ".dna",
        "benchling": ".json",
        "fasta": ".fasta",
        "sbol": ".json"
    }
    filename = f"{filename_base}{extensions.get(format, '.txt')}"
    if isinstance(content, bytes):
        return StreamingResponse(
            io.BytesIO(content),
            media_type=mime_type,
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    return PlainTextResponse(
        content=content,
        media_type=mime_type,
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.get("/design/{design_id}/export")
async def export_design(design_id: str, format: str = "genbank",
                        user: Optional[User] = Depends(get_current_user)):
    """
    导出设计结果为指定格式

    Args:
        design_id: 设计任务 ID
        format: genbank / snapgene / benchling / fasta / sbol
    """
    from app.routes.design_routes import _ensure_design_access, _load

    # 属主校验（终审 B-01：此前匿名可导出他人完整构建序列——IDOR）
    result = _ensure_design_access(_load(design_id), user)
    if not result:
        raise HTTPException(status_code=404, detail="Design not found")

    data = create_export_data_from_design(result.model_dump(mode="json"))
    try:
        return await run_in_threadpool(_export_response, data, format, design_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/vector/{vector_id}/export")
async def export_vector(vector_id: str, format: str = "genbank"):
    """
    导出载体序列为指定格式

    Args:
        vector_id: 载体 ID
        format: genbank / snapgene / benchling / fasta / sbol
    """
    from app.vector_library_cache import get_vector_library

    vector = get_vector_library().get_vector(vector_id)
    if not vector:
        raise HTTPException(status_code=404, detail="Vector not found")

    data = create_export_data_from_vector({
        "name": vector.name,
        "sequence": vector.sequence,
        "description": vector.description or "",
        "features": [
            {
                "name": e.name,
                "type": e.element_type.value,
                "start": e.start,
                "end": e.end,
                "strand": e.strand or "+",
                "description": e.description or "",
            }
            for e in vector.elements
        ],
    })
    try:
        return await run_in_threadpool(_export_response, data, format, vector_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ==================== 克隆兼容性检查 ====================

@router.post("/compatibility")
async def check_cloning_compatibility(request: CompatibilityRequest) -> Dict:
    """
    检查克隆兼容性

    Args:
        request: JSON body，含 insert_sequence / vector_sequence / enzymes

    Returns:
        兼容性分析结果
    """
    analyzer = SequenceAnalyzer()
    result = await run_in_threadpool(
        analyzer.check_cloning_compatibility,
        request.insert_sequence,
        request.vector_sequence,
        request.enzymes
    )

    return result


@router.get("/enzymes")
@cached("analysis_enzymes", ttl=604800, version=ANALYSIS_ENZYMES_VERSION)  # 静态数据，缓存 7 天
async def list_enzymes() -> Dict:
    """获取支持的酶列表；golden_gate_enzymes 为 Golden Gate 候选酶（REBASE
    中可商购、切出 4 nt 5' 突出端的 Type IIS 酶，BsaI/BsmBI/BbsI 在前）"""
    from core.enzyme_sites import get_enzyme, golden_gate_enzymes, is_type_iis
    from core.sequence_analysis import RESTRICTION_ENZYMES

    overhang_names = {'5': "5' 粘性末端", '3': "3' 粘性末端", 'b': '平末端'}
    enzymes_info = {}
    for name, (seq, cut, overhang) in RESTRICTION_ENZYMES.items():
        enzymes_info[name] = {
            "recognition_sequence": seq,
            "cut_type": overhang_names.get(overhang, '未知'),
            # Type IIS：非回文且切点落在识别序列同一侧之外
            "is_type_iis": is_type_iis(get_enzyme(name)),
        }
    golden_gate = [
        {"name": n, "recognition_sequence": get_enzyme(n)["site"]}
        for n in golden_gate_enzymes()
    ]

    return {
        "total": len(enzymes_info),
        "enzymes": enzymes_info,
        "golden_gate_enzymes": golden_gate,
    }
