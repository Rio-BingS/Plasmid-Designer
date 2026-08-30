"""
Plasmid Designer - HuggingFace Docker 适配版 FastAPI 入口

与完整版 main.py 的区别:
- 移除数据库/Redis/Celery 依赖（HF Spaces 不可用）
- 使用内存存储替代数据库
- 移除认证、缓存、限流等需要外部服务的模块
- 核心 API 完全保留
"""

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Optional, Dict
from enum import Enum
import uuid
import os
import sys
import io
from datetime import datetime

# 确保后端路径可导入
BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend")
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from core.codon_optimizer import CodonOptimizer
from core.primer_designer import PrimerDesigner, PrimerType
from core.clone_strategy import generate_cloning_strategy, CloningMethod as CM
from core.sequence_validator import SequenceValidator
from core.vector_library import VectorLibrary
from core.sequence_analysis import (
    SequenceAnalyzer, RestrictionSiteAnalyzer, ORFPredictor, GCAnalyzer,
    RESTRICTION_ENZYMES,
)
from core.export_formats import ExportManager, ExportData, SequenceFeature
from core.output_generator import (
    GenBankGenerator, PrimerOrderGenerator, DesignReportGenerator, PlasmidDesign,
)

# 数据目录
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
VECTORS_DIR = os.path.join(DATA_DIR, "vectors")
CODON_TABLES_DIR = os.path.join(DATA_DIR, "codon_tables")

app = FastAPI(
    title="Plasmid Designer API",
    description="自动化质粒构建设计平台 API (HF Spaces 版)",
    version="2.0.0",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==================== 数据模型 ====================

class SequenceType(str, Enum):
    AMINO_ACID = "amino_acid"
    DNA = "dna"


class CloningMethod(str, Enum):
    GIBSON = "gibson"
    GOLDEN_GATE = "golden_gate"
    RESTRICTION = "restriction"


class DesignStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class DesignRequest(BaseModel):
    sequence: str = Field(..., description="输入序列")
    sequence_type: SequenceType = Field(default=SequenceType.AMINO_ACID)
    sequence_name: str = Field(default="insert")
    vector_id: str = Field(default="pET-28a")
    cloning_method: CloningMethod = Field(default=CloningMethod.GIBSON)
    optimize_codons: bool = Field(default=True)
    target_species: str = Field(default="ecoli")
    gc_min: float = Field(default=40.0, ge=20, le=50)
    gc_max: float = Field(default=60.0, ge=50, le=80)
    homology_arm: int = Field(default=20, ge=15, le=40)
    enzyme: str = Field(default="BsaI")


class PrimerInfo(BaseModel):
    name: str
    sequence: str
    full_sequence: str
    tm: float
    gc_content: float
    length: int
    overhang: Optional[str] = None
    notes: Optional[str] = None


class DesignResult(BaseModel):
    design_id: str
    status: DesignStatus
    input_sequence: str
    optimized_sequence: Optional[str] = None
    cai: Optional[float] = None
    gc_content: Optional[float] = None
    vector_id: str
    vector_name: str = ""
    final_length: Optional[int] = None
    primers: List[PrimerInfo] = []
    cloning_method: CloningMethod
    clone_protocol: Optional[str] = None
    validation_passed: bool = False
    warnings: List[str] = []
    errors: List[str] = []
    created_at: datetime
    completed_at: Optional[datetime] = None


class VectorInfo(BaseModel):
    id: str
    name: str
    source: str
    vector_type: str
    host: List[str]
    antibiotic_resistance: List[str]
    copy_number: str
    description: str
    features: List[Dict]
    mcs_enzymes: List[str]


class PlasmidMapData(BaseModel):
    name: str
    length: int
    sequence: Optional[str] = None
    features: List[Dict] = []


# ==================== 内存存储 ====================

designs_db: Dict[str, DesignResult] = {}


# ==================== 载体库 ====================

def _get_vector_library() -> VectorLibrary:
    library = VectorLibrary()
    library.load_from_directory(VECTORS_DIR)
    return library


# ==================== API 路由 ====================

@app.get("/")
async def root():
    return {"name": "Plasmid Designer API", "version": "2.0.0", "status": "running"}


@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}


# ----- 设计任务 -----

@app.post("/api/design", response_model=Dict)
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
    designs_db[design_id] = result
    background_tasks.add_task(run_design_task, design_id, request)
    return {"design_id": design_id, "status": "pending", "message": "设计任务已提交"}


@app.get("/api/design/{design_id}", response_model=DesignResult)
async def get_design(design_id: str):
    if design_id not in designs_db:
        raise HTTPException(status_code=404, detail="Design not found")
    return designs_db[design_id]


@app.get("/api/design/{design_id}/map", response_model=PlasmidMapData)
async def get_design_map_data(design_id: str):
    if design_id not in designs_db:
        raise HTTPException(status_code=404, detail="Design not found")
    result = designs_db[design_id]
    if result.status != DesignStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Design not completed")
    features = [
        {
            "name": "Insert",
            "type": "CDS",
            "start": 1,
            "end": len(result.optimized_sequence or ""),
            "strand": "+",
            "description": f"Optimized insert",
        }
    ]
    return PlasmidMapData(
        name=result.vector_name or "Construct",
        length=result.final_length or 0,
        sequence=result.optimized_sequence,
        features=features,
    )


@app.get("/api/design/{design_id}/download/genbank")
async def download_genbank(design_id: str):
    if design_id not in designs_db:
        raise HTTPException(status_code=404, detail="Design not found")
    result = designs_db[design_id]
    if result.status != DesignStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Design not completed")
    from fastapi.responses import PlainTextResponse
    content = _generate_genbank_content(result)
    return PlainTextResponse(
        content=content,
        media_type="text/plain",
        headers={"Content-Disposition": f"attachment; filename={design_id}.gb"},
    )


@app.get("/api/design/{design_id}/download/primers")
async def download_primers(design_id: str):
    if design_id not in designs_db:
        raise HTTPException(status_code=404, detail="Design not found")
    result = designs_db[design_id]
    if result.status != DesignStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Design not completed")
    from fastapi.responses import PlainTextResponse
    content = _generate_primer_tsv(result.primers)
    return PlainTextResponse(
        content=content,
        media_type="text/tab-separated-values",
        headers={"Content-Disposition": f"attachment; filename={design_id}_primers.tsv"},
    )


@app.get("/api/design/{design_id}/download/report")
async def download_report(design_id: str):
    if design_id not in designs_db:
        raise HTTPException(status_code=404, detail="Design not found")
    result = designs_db[design_id]
    if result.status != DesignStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Design not completed")
    from fastapi.responses import PlainTextResponse
    primers_data = [
        {"name": p.name, "sequence": p.sequence, "full_sequence": p.full_sequence,
         "length": p.length, "tm": p.tm, "gc_content": p.gc_content,
         "overhang": p.overhang, "notes": p.notes or ""}
        for p in result.primers
    ]
    design = PlasmidDesign(
        design_id=result.design_id, design_name=result.input_sequence[:30],
        insert_sequence=result.input_sequence, insert_name="insert",
        vector_id=result.vector_id, vector_name=result.vector_name,
        final_sequence=result.optimized_sequence or "", primers=primers_data,
        cloning_method=result.cloning_method.value,
        design_date=result.created_at.strftime("%Y-%m-%d"),
    )
    gen = DesignReportGenerator()
    content = gen.generate(design)
    return PlainTextResponse(
        content=content, media_type="text/markdown",
        headers={"Content-Disposition": f"attachment; filename={design_id}_report.md"},
    )


# ----- 批量设计 -----

class BatchDesignRequest(BaseModel):
    sequences: List[str] = Field(..., description="序列列表")
    sequence_type: SequenceType = Field(default=SequenceType.AMINO_ACID)
    vector_id: str = Field(default="pET-28a")
    cloning_method: CloningMethod = Field(default=CloningMethod.GIBSON)
    optimize_codons: bool = Field(default=True)
    target_species: str = Field(default="ecoli")
    gc_min: float = Field(default=40.0, ge=20, le=50)
    gc_max: float = Field(default=60.0, ge=50, le=80)
    homology_arm: int = Field(default=20, ge=15, le=40)
    enzyme: str = Field(default="BsaI")


class BatchDesignResultItem(BaseModel):
    index: int
    sequence: str
    design_id: str
    status: str
    cai: Optional[float] = None
    gc_content: Optional[float] = None
    forward_tm: Optional[float] = None
    product_size: Optional[int] = None
    error: Optional[str] = None


class BatchDesignResponse(BaseModel):
    batch_id: str
    total: int
    completed: int
    failed: int
    results: List[BatchDesignResultItem]


@app.post("/api/design/batch", response_model=BatchDesignResponse)
async def batch_design(request: BatchDesignRequest):
    batch_id = f"batch_{uuid.uuid4().hex[:8]}"
    items = []
    completed = 0
    failed = 0

    library = _get_vector_library()
    vector = library.get_vector(request.vector_id)

    for i, seq in enumerate(request.sequences):
        seq = seq.upper().replace("\n", "").replace(" ", "").replace("\r", "")
        if not seq:
            continue
        item = BatchDesignResultItem(index=i, sequence=seq[:50], design_id=f"bd_{batch_id}_{i}", status="pending")
        try:
            if request.sequence_type == SequenceType.AMINO_ACID:
                optimizer = CodonOptimizer(species=request.target_species)
                opt_result = optimizer.optimize(seq, gc_target=(request.gc_min / 100, request.gc_max / 100))
                optimized_dna = opt_result.dna_sequence
                item.cai = opt_result.cai
                item.gc_content = opt_result.gc_content
            else:
                optimized_dna = seq
                item.gc_content = 0.0

            designer = PrimerDesigner()
            if request.cloning_method == CloningMethod.GIBSON:
                insert_pos = vector.mcs.start if vector and vector.mcs else 100
                vector_seq = vector.sequence if vector and vector.sequence else "N" * 5000
                pair, _ = designer.design_gibson_primers(
                    optimized_dna, vector_seq, insert_pos,
                    homology_arm=request.homology_arm, primer_name=f"seq_{i+1}",
                )
            elif request.cloning_method == CloningMethod.GOLDEN_GATE:
                pair = designer.design_golden_gate_primers(
                    optimized_dna, enzyme_name=request.enzyme,
                    overhang_seq_5="AATG", overhang_seq_3="GCTT",
                    primer_name=f"seq_{i+1}",
                )
            else:
                pair = designer.design_pcr_primers(optimized_dna, primer_name=f"seq_{i+1}")

            item.forward_tm = pair.forward.tm
            item.product_size = pair.product_size
            item.status = "completed"
            completed += 1
        except Exception as e:
            item.status = "failed"
            item.error = str(e)
            failed += 1
        items.append(item)

    return BatchDesignResponse(
        batch_id=batch_id, total=len(items), completed=completed, failed=failed, results=items,
    )


# ----- 载体库 -----

@app.get("/api/vectors", response_model=List[VectorInfo])
async def list_vectors(vector_type: Optional[str] = None, host: Optional[str] = None):
    library = _get_vector_library()
    vectors = library.list_vectors(filter_type=vector_type)
    result = []
    for v in vectors:
        if host and host not in v.host:
            continue
        mcs_enzymes = v.mcs.get_unique_enzymes() if v.mcs else []
        result.append(
            VectorInfo(
                id=v.id,
                name=v.name,
                source=v.source,
                vector_type=v.vector_type,
                host=v.host,
                antibiotic_resistance=v.antibiotic_resistance,
                copy_number=v.copy_number,
                description=v.description,
                features=[{"name": e.name, "type": e.element_type.value} for e in v.elements],
                mcs_enzymes=mcs_enzymes,
            )
        )
    return result


@app.get("/api/vectors/{vector_id}", response_model=VectorInfo)
async def get_vector(vector_id: str):
    library = _get_vector_library()
    vector = library.get_vector(vector_id)
    if not vector:
        raise HTTPException(status_code=404, detail="Vector not found")
    mcs_enzymes = vector.mcs.get_unique_enzymes() if vector.mcs else []
    return VectorInfo(
        id=vector.id,
        name=vector.name,
        source=vector.source,
        vector_type=vector.vector_type,
        host=vector.host,
        antibiotic_resistance=vector.antibiotic_resistance,
        copy_number=vector.copy_number,
        description=vector.description,
        features=[{"name": e.name, "type": e.element_type.value} for e in vector.elements],
        mcs_enzymes=mcs_enzymes,
    )


@app.get("/api/vectors/{vector_id}/map", response_model=PlasmidMapData)
async def get_vector_map_data(vector_id: str):
    library = _get_vector_library()
    vector = library.get_vector(vector_id)
    if not vector:
        raise HTTPException(status_code=404, detail="Vector not found")
    features = [
        {"name": e.name, "type": e.element_type.value, "start": e.start, "end": e.end, "strand": e.strand}
        for e in vector.elements
    ]
    if not features and vector.mcs:
        features.append(
            {"name": vector.mcs.name, "type": "multiple_cloning_site", "start": vector.mcs.start, "end": vector.mcs.end, "strand": "+"}
        )
    return PlasmidMapData(
        name=vector.name,
        length=vector.length,
        sequence=vector.sequence[:1000] if len(vector.sequence) > 1000 else vector.sequence,
        features=features,
    )


@app.get("/api/vectors/{vector_id}/sequence")
async def get_vector_sequence(vector_id: str, format: str = "fasta"):
    library = _get_vector_library()
    vector = library.get_vector(vector_id)
    if not vector:
        raise HTTPException(status_code=404, detail="Vector not found")
    from fastapi.responses import PlainTextResponse
    if format.lower() == "genbank":
        lines = [f"LOCUS {vector.name[:16]:<16} {len(vector.sequence)} bp DNA"]
        lines.append(f"DEFINITION {vector.description}")
        lines.append(f"ACCESSION {vector.id}")
        lines.append("FEATURES Location/Qualifiers")
        for elem in vector.elements:
            lines.append(f" {elem.element_type.value:<12} {elem.start}..{elem.end}")
        lines.append("ORIGIN")
        seq = vector.sequence.upper()
        for i in range(0, len(seq), 60):
            chunk = seq[i : i + 60]
            groups = " ".join([chunk[j : j + 10] for j in range(0, len(chunk), 10)])
            lines.append(f"{i+1:>9} {groups}")
        lines.append("//")
        content = "\n".join(lines)
    else:
        content = f">{vector.id} {vector.name}\n{vector.sequence.upper()}"
    return PlainTextResponse(
        content=content,
        media_type="text/plain",
        headers={"Content-Disposition": f"attachment; filename={vector_id}.{format.lower()}"},
    )


# ----- 密码子表 -----

@app.get("/api/codon-tables")
async def list_codon_tables():
    tables = []
    if os.path.isdir(CODON_TABLES_DIR):
        for file in os.listdir(CODON_TABLES_DIR):
            if file.endswith(".yaml"):
                name = file.replace(".yaml", "")
                tables.append({"id": name, "name": name.replace("_", " "), "file": file})
    return tables


# ==================== 后台任务 ====================

def run_design_task(design_id: str, request: DesignRequest):
    try:
        designs_db[design_id].status = DesignStatus.RUNNING
        sequence = request.sequence.upper().replace("\n", "").replace(" ", "")

        if request.sequence_type == SequenceType.DNA:
            optimized_dna = sequence
        else:
            optimizer = CodonOptimizer(species=request.target_species)
            opt_result = optimizer.optimize(sequence, gc_target=(request.gc_min / 100, request.gc_max / 100))
            optimized_dna = opt_result.dna_sequence
            designs_db[design_id].cai = opt_result.cai
            designs_db[design_id].gc_content = opt_result.gc_content
            designs_db[design_id].warnings.extend(opt_result.warnings)

        designs_db[design_id].optimized_sequence = optimized_dna

        # 验证
        validator = SequenceValidator()
        val_result = validator.validate(optimized_dna, sequence_type="dna")
        designs_db[design_id].validation_passed = val_result.is_valid
        designs_db[design_id].errors.extend(val_result.errors)
        designs_db[design_id].warnings.extend(val_result.warnings)

        # 加载载体
        library = _get_vector_library()
        vector = library.get_vector(request.vector_id)
        if vector:
            designs_db[design_id].vector_name = vector.name

        # 设计引物
        designer = PrimerDesigner()
        if request.cloning_method == CloningMethod.GIBSON:
            insert_pos = vector.mcs.start if vector and vector.mcs else 100
            vector_seq = vector.sequence if vector and vector.sequence else "N" * 5000
            pair, _ = designer.design_gibson_primers(
                optimized_dna, vector_seq, insert_pos,
                homology_arm=request.homology_arm, primer_name=request.sequence_name,
            )
        elif request.cloning_method == CloningMethod.GOLDEN_GATE:
            pair = designer.design_golden_gate_primers(
                optimized_dna, enzyme_name=request.enzyme,
                overhang_seq_5="AATG", overhang_seq_3="GCTT",
                primer_name=request.sequence_name,
            )
        else:
            pair = designer.design_pcr_primers(optimized_dna, primer_name=request.sequence_name)

        designs_db[design_id].primers = [
            PrimerInfo(
                name=pair.forward.name, sequence=pair.forward.sequence,
                full_sequence=pair.forward.full_sequence, tm=pair.forward.tm,
                gc_content=pair.forward.gc_content, length=pair.forward.length,
                overhang=pair.forward.overhang, notes=pair.forward.notes,
            ),
            PrimerInfo(
                name=pair.reverse.name, sequence=pair.reverse.sequence,
                full_sequence=pair.reverse.full_sequence, tm=pair.reverse.tm,
                gc_content=pair.reverse.gc_content, length=pair.reverse.length,
                overhang=pair.reverse.overhang, notes=pair.reverse.notes,
            ),
        ]

        # 克隆策略
        clone_method_map = {
            CloningMethod.GIBSON: CM.GIBSON,
            CloningMethod.GOLDEN_GATE: CM.GOLDEN_GATE,
            CloningMethod.RESTRICTION: CM.RESTRICTION,
        }
        strategy = generate_cloning_strategy(
            method=clone_method_map[request.cloning_method],
            insert_seq=optimized_dna, insert_name=request.sequence_name,
            vector_seq=vector.sequence if vector and vector.sequence else "",
            vector_name=vector.name if vector else request.vector_id,
            homology_arm=request.homology_arm, enzyme=request.enzyme,
        )
        designs_db[design_id].clone_protocol = strategy.to_protocol()
        designs_db[design_id].final_length = len(optimized_dna) + (vector.length if vector else 0)
        designs_db[design_id].status = DesignStatus.COMPLETED
        designs_db[design_id].completed_at = datetime.now()

    except Exception as e:
        designs_db[design_id].status = DesignStatus.FAILED
        designs_db[design_id].errors.append(str(e))


# ==================== 辅助函数 ====================

def _generate_genbank_content(result: DesignResult) -> str:
    lines = []
    locus_name = result.design_id[:16].replace("-", "_")
    length = result.final_length or len(result.optimized_sequence or "")
    date_str = datetime.now().strftime("%d-%b-%Y").upper()
    lines.append(f"LOCUS {locus_name:<16} {length} bp DNA circular SYN {date_str}")
    lines.append(f"DEFINITION {result.vector_name} with {result.design_id}")
    lines.append(f"ACCESSION {result.design_id}")
    lines.append("FEATURES Location/Qualifiers")
    lines.append(f" source 1..{length}")
    lines.append(' /organism="synthetic construct"')
    if result.optimized_sequence:
        lines.append(f" CDS 1..{len(result.optimized_sequence)}")
        lines.append(' /label="insert"')
    lines.append("ORIGIN")
    seq = (result.optimized_sequence or "").upper()
    for i in range(0, len(seq), 60):
        chunk = seq[i : i + 60]
        groups = " ".join([chunk[j : j + 10] for j in range(0, len(chunk), 10)])
        lines.append(f"{i+1:>9} {groups}")
    lines.append("//")
    return "\n".join(lines)


def _generate_primer_tsv(primers: List[PrimerInfo]) -> str:
    lines = ["Name\tSequence\tFull Sequence\tLength\tTm\tGC%\tNotes"]
    for p in primers:
        lines.append(f"{p.name}\t{p.sequence}\t{p.full_sequence}\t{p.length}\t{p.tm:.1f}\t{p.gc_content:.1f}\t{p.notes or ''}")
    return "\n".join(lines)


# ==================== 序列分析 & 导出 API ====================


class SequenceAnalysisRequest(BaseModel):
    sequence: str = Field(..., description="DNA 序列")
    check_restriction: bool = Field(default=True)
    check_orf: bool = Field(default=True)
    check_gc: bool = Field(default=True)
    enzymes: Optional[List[str]] = Field(default=None)


class ExportRequest(BaseModel):
    name: str = Field(..., description="序列名称")
    sequence: str = Field(..., description="DNA 序列")
    features: List[Dict] = Field(default=[])
    description: str = Field(default="")
    is_circular: bool = Field(default=True)
    format: str = Field(default="genbank")


@app.post("/api/analysis/analyze")
async def analyze_sequence(request: SequenceAnalysisRequest) -> Dict:
    analyzer = SequenceAnalyzer()
    result = analyzer.analyze(
        sequence=request.sequence,
        check_restriction=request.check_restriction,
        check_orf=request.check_orf,
        check_gc=request.check_gc,
        enzymes=request.enzymes,
    )
    return {
        "sequence_length": result.sequence_length,
        "gc_content": result.gc_content,
        "coding_potential": result.coding_potential,
        "restriction_sites": [
            {"enzyme": s.enzyme, "site": s.site, "position": s.recognition_start,
             "strand": s.strand, "overhang": s.overhang}
            for s in result.restriction_sites
        ],
        "orfs": [
            {"start": o.start, "end": o.end, "strand": o.strand, "length": o.length,
             "frame": o.frame, "start_codon": o.start_codon, "stop_codon": o.stop_codon,
             "gc_content": o.gc_content, "is_complete": o.is_complete}
            for o in result.orfs[:10]
        ],
        "gc_distribution": [
            {"start": r.start, "end": r.end, "gc_content": r.gc_content, "is_extreme": r.is_extreme}
            for r in result.gc_distribution[:50]
        ],
        "warnings": result.warnings,
    }


@app.post("/api/analysis/restriction-sites")
async def find_restriction_sites(sequence: str, enzymes: Optional[List[str]] = None) -> Dict:
    analyzer = RestrictionSiteAnalyzer()
    sites = analyzer.find_sites(sequence, enzymes)
    return {
        "total": len(sites),
        "sites": [
            {"enzyme": s.enzyme, "recognition_sequence": s.site, "position": s.recognition_start,
             "end": s.recognition_end, "cut_position": s.cut_position,
             "strand": s.strand, "overhang_type": s.overhang}
            for s in sites
        ],
        "unique_sites": list(analyzer.find_unique_sites(sequence).keys()),
    }


@app.post("/api/analysis/orfs")
async def find_orfs(sequence: str, min_length: int = 150) -> Dict:
    predictor = ORFPredictor(min_length=min_length)
    orfs = predictor.find_orfs(sequence)
    return {
        "total": len(orfs),
        "orfs": [
            {"start": o.start, "end": o.end, "strand": o.strand, "length": o.length,
             "frame": o.frame, "protein_sequence": o.protein_sequence,
             "start_codon": o.start_codon, "stop_codon": o.stop_codon,
             "gc_content": round(o.gc_content, 2), "is_complete": o.is_complete}
            for o in orfs
        ],
    }


@app.post("/api/analysis/gc-analysis")
async def analyze_gc(sequence: str, window_size: int = 100, step_size: int = 50) -> Dict:
    analyzer = GCAnalyzer(window_size=window_size, step_size=step_size)
    total_gc, regions = analyzer.analyze(sequence)
    extremes = [r for r in regions if r.is_extreme]
    return {
        "total_gc_content": round(total_gc, 2),
        "total_regions": len(regions),
        "extreme_regions": len(extremes),
        "distribution": [
            {"start": r.start, "end": r.end, "gc_content": round(r.gc_content, 2), "is_extreme": r.is_extreme}
            for r in regions
        ],
    }


@app.get("/api/analysis/export/formats")
async def get_export_formats() -> List[Dict]:
    return ExportManager.get_supported_formats()


@app.post("/api/analysis/export")
async def export_sequence(request: ExportRequest):
    features = [
        SequenceFeature(
            name=f.get("name", "feature"), feature_type=f.get("type", "misc_feature"),
            start=f.get("start", 1), end=f.get("end", 100), strand=f.get("strand", "+"),
            color=f.get("color", "#4A90D9"), description=f.get("description", ""),
        )
        for f in request.features
    ]
    export_data = ExportData(
        name=request.name, sequence=request.sequence, features=features,
        description=request.description, is_circular=request.is_circular,
    )
    try:
        content, mime_type = ExportManager.export(export_data, request.format)
        extensions = {"genbank": ".gb", "snapgene": ".dna", "benchling": ".json", "fasta": ".fasta", "sbol": ".json"}
        ext = extensions.get(request.format, ".txt")
        from fastapi.responses import PlainTextResponse, StreamingResponse
        if isinstance(content, bytes):
            return StreamingResponse(
                io.BytesIO(content), media_type=mime_type,
                headers={"Content-Disposition": f"attachment; filename={request.name}{ext}"},
            )
        else:
            return PlainTextResponse(
                content=content, media_type=mime_type,
                headers={"Content-Disposition": f"attachment; filename={request.name}{ext}"},
            )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/analysis/export/all")
async def export_all_formats(request: ExportRequest):
    features = [
        SequenceFeature(
            name=f.get("name", "feature"), feature_type=f.get("type", "misc_feature"),
            start=f.get("start", 1), end=f.get("end", 100), strand=f.get("strand", "+"),
            color=f.get("color", "#4A90D9"), description=f.get("description", ""),
        )
        for f in request.features
    ]
    export_data = ExportData(
        name=request.name, sequence=request.sequence, features=features,
        description=request.description, is_circular=request.is_circular,
    )
    zip_content = ExportManager.export_all(export_data)
    from fastapi.responses import StreamingResponse
    return StreamingResponse(
        io.BytesIO(zip_content), media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={request.name}_exports.zip"},
    )


@app.post("/api/analysis/compatibility")
async def check_cloning_compatibility(insert_sequence: str, vector_sequence: str, enzymes: List[str]) -> Dict:
    analyzer = SequenceAnalyzer()
    result = analyzer.check_cloning_compatibility(insert_sequence, vector_sequence, enzymes)
    return result


@app.get("/api/analysis/enzymes")
async def list_enzymes() -> Dict:
    enzymes_info = {}
    for name, (seq, cut, overhang) in RESTRICTION_ENZYMES.items():
        overhang_names = {"5": "5' overhang", "3": "3' overhang", "b": "blunt"}
        enzymes_info[name] = {
            "recognition_sequence": seq,
            "cut_type": overhang_names.get(overhang, "unknown"),
            "is_type_iis": name in ["BsaI", "BsmBI", "BbsI"],
        }
    return {"total": len(enzymes_info), "enzymes": enzymes_info}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
