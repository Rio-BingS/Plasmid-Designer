"""
序列分析和导出 API 路由
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse, JSONResponse, StreamingResponse
from typing import List, Dict, Optional
from pydantic import BaseModel, Field
import io

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
from app.cache import cached

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


# ==================== 请求模型 ====================

class SequenceAnalysisRequest(BaseModel):
    """序列分析请求"""
    sequence: str = Field(..., description="DNA 序列")
    sequence_type: str = Field(default="dna", description="序列类型（dna/amino_acid，当前分析均按 DNA 处理）")
    check_restriction: bool = Field(default=True, description="是否检测限制性位点")
    check_orf: bool = Field(default=True, description="是否预测 ORF")
    check_gc: bool = Field(default=True, description="是否分析 GC 含量")
    enzymes: Optional[List[str]] = Field(default=None, description="要检测的酶列表")


class RestrictionSitesRequest(BaseModel):
    """限制性酶切位点请求（body 传递，避免长序列进入 URL）"""
    sequence: str = Field(..., description="DNA 序列")
    enzymes: Optional[List[str]] = Field(default=None, description="要检测的酶列表，默认全部常用酶")


class ORFRequest(BaseModel):
    """ORF 预测请求"""
    sequence: str = Field(..., description="DNA 序列")
    min_length: int = Field(default=150, ge=1, description="最小 ORF 长度（碱基数）")


class DigestRequest(BaseModel):
    """酶切消化模拟请求"""
    sequence: str = Field(..., description="DNA 序列")
    enzymes: List[str] = Field(..., min_length=1, max_length=6, description="用于模拟消化的酶（1-6 个）")


class GCAnalysisRequest(BaseModel):
    """GC 含量分析请求"""
    sequence: str = Field(..., description="DNA 序列")
    window_size: int = Field(default=100, ge=1, description="滑动窗口大小")
    step_size: int = Field(default=50, ge=1, description="步长")


class CompatibilityRequest(BaseModel):
    """克隆兼容性检查请求"""
    insert_sequence: str = Field(..., description="插入片段序列")
    vector_sequence: str = Field(..., description="载体序列")
    enzymes: List[str] = Field(..., description="计划使用的酶列表")


class ExportRequest(BaseModel):
    """导出请求"""
    name: str = Field(..., description="序列名称")
    sequence: str = Field(..., description="DNA 序列")
    features: List[Dict] = Field(default=[], description="序列特征")
    description: str = Field(default="", description="描述")
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
    """
    analyzer = SequenceAnalyzer()
    result = analyzer.analyze(
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
    sites = analyzer.find_sites(request.sequence, request.enzymes)
    
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
        "unique_sites": list(analyzer.find_unique_sites(request.sequence).keys())
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
    orfs = predictor.find_orfs(request.sequence)
    
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
    酶切消化模拟（线性 DNA 完全消化）

    按所选酶的切割位置切分序列，返回各片段的起止与大小，
    用于预测电泳条带。
    """
    from core.sequence_analysis import RESTRICTION_ENZYMES, RestrictionSiteAnalyzer

    seq = request.sequence.upper()
    analyzer = RestrictionSiteAnalyzer()

    cuts = []
    enzymes_with_sites = []
    for enzyme in request.enzymes:
        if enzyme not in RESTRICTION_ENZYMES:
            raise HTTPException(status_code=400, detail=f"未知限制酶: {enzyme}")
        sites = analyzer.find_sites(seq, [enzyme])
        if sites:
            enzymes_with_sites.append(enzyme)
        # 同一识别位点的正反链记录（粘性末端两个切点）合并为一个物理切割点，
        # 取正链切割位置——消化模拟按每识别位点一个边界计
        seen_regions = set()
        for s in sites:
            region = (s.recognition_start, s.recognition_end)
            if region in seen_regions:
                continue
            seen_regions.add(region)
            same_region = [x for x in sites
                           if (x.recognition_start, x.recognition_end) == region]
            cut = next((x.cut_position for x in same_region if x.strand == "+"),
                       same_region[0].cut_position)
            cuts.append((cut, enzyme))

    if not cuts:
        return {
            "total_fragments": 0,
            "fragments": [],
            "enzymes_with_sites": [],
            "message": "所选酶在序列中均无切割位点",
        }

    cuts.sort(key=lambda x: x[0])
    fragments = []
    prev = 0
    for pos, enzyme in cuts:
        if pos <= prev:
            continue  # 同一位置多种酶切割合并为同一边界
        fragments.append({
            "start": prev + 1,
            "end": pos,
            "size": pos - prev,
            "cut_by": [e for p, e in cuts if p == pos],
        })
        prev = pos
    if len(seq) - prev > 0:
        fragments.append({
            "start": prev + 1,
            "end": len(seq),
            "size": len(seq) - prev,
            "cut_by": [],
        })

    return {
        "total_fragments": len(fragments),
        "fragments": fragments,
        "enzymes_with_sites": enzymes_with_sites,
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
    total_gc, regions = analyzer.analyze(request.sequence)
    
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
        content, mime_type = ExportManager.export(export_data, request.format)
        
        # 确定文件扩展名
        extensions = {
            "genbank": ".gb",
            "snapgene": ".dna",
            "benchling": ".json",
            "fasta": ".fasta",
            "sbol": ".json"
        }
        ext = extensions.get(request.format, ".txt")
        
        if isinstance(content, bytes):
            return StreamingResponse(
                io.BytesIO(content),
                media_type=mime_type,
                headers={
                    "Content-Disposition": f"attachment; filename={request.name}{ext}"
                }
            )
        else:
            return PlainTextResponse(
                content=content,
                media_type=mime_type,
                headers={
                    "Content-Disposition": f"attachment; filename={request.name}{ext}"
                }
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
    
    zip_content = ExportManager.export_all(export_data)
    
    return StreamingResponse(
        io.BytesIO(zip_content),
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename={request.name}_exports.zip"
        }
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
async def export_design(design_id: str, format: str = "genbank"):
    """
    导出设计结果为指定格式

    Args:
        design_id: 设计任务 ID
        format: genbank / snapgene / benchling / fasta / sbol
    """
    from app.routes.design_routes import _load

    result = _load(design_id)
    if not result:
        raise HTTPException(status_code=404, detail="Design not found")

    data = create_export_data_from_design(result.model_dump(mode="json"))
    try:
        return _export_response(data, format, design_id)
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
    from app.design_service import get_vector_library

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
        return _export_response(data, format, vector_id)
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
    result = analyzer.check_cloning_compatibility(
        request.insert_sequence,
        request.vector_sequence,
        request.enzymes
    )

    return result


@router.get("/enzymes")
@cached("analysis_enzymes", ttl=604800)  # 酶表为静态数据，缓存 7 天
async def list_enzymes() -> Dict:
    """获取支持的酶列表"""
    from core.sequence_analysis import RESTRICTION_ENZYMES
    
    enzymes_info = {}
    for name, (seq, cut, overhang) in RESTRICTION_ENZYMES.items():
        overhang_names = {'5': "5' 粘性末端", '3': "3' 粘性末端", 'b': '平末端'}
        enzymes_info[name] = {
            "recognition_sequence": seq,
            "cut_type": overhang_names.get(overhang, '未知'),
            "is_type_iis": name in ['BsaI', 'BsmBI', 'BbsI']
        }
    
    return {
        "total": len(enzymes_info),
        "enzymes": enzymes_info
    }
