"""共享 Pydantic 模型 — 从 main.py 提取"""

import os
from pydantic import BaseModel, Field, model_validator
from typing import List, Optional, Dict, Literal
from enum import Enum
from datetime import datetime


# ==================== 序列长度上限（终审 C-03） ====================
# 原固定值 100_000 不是防护而是 CPU DoS 入口：_sliding_window_refinement
# 是 O(n²)（每个候选都重拼整条序列并全串扫 motif/polyX），实测 1k aa
# 0.33s / 4k aa 1.7s / 10k aa 19.6s，且无超时不可取消。按序列类型收紧：
# 氨基酸 5000 aa、DNA 20000 nt，均可用环境变量覆盖（自部署可放宽）。
MAX_INPUT_AA = int(os.environ.get("MAX_INPUT_AA", "5000"))
MAX_INPUT_DNA = int(os.environ.get("MAX_INPUT_DNA", "20000"))
# Field 的 max_length 只能给一个绝对值（类型未知），取两者上界兜底；
# 真正的类型化校验在 model_validator 里做
MAX_INPUT_SEQ_CHARS = max(MAX_INPUT_AA, MAX_INPUT_DNA) * 3


def _seq_limit_for(sequence_type) -> int:
    """按序列类型返回长度上限（氨基酸按残基、DNA 按碱基）"""
    return MAX_INPUT_AA if sequence_type == SequenceType.AMINO_ACID else MAX_INPUT_DNA


# ==================== 枚举 ====================

class SequenceType(str, Enum):
    AMINO_ACID = "amino_acid"
    DNA = "dna"


class CloningMethod(str, Enum):
    GIBSON = "gibson"
    GOLDEN_GATE = "golden_gate"
    RESTRICTION = "restriction"
    GENE_SYNTHESIS = "gene_synthesis"


class DesignStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


# ==================== 设计请求/响应 ====================

class DesignOptions(BaseModel):
    """单任务与批量任务共用的设计参数。"""

    sequence_type: SequenceType = Field(default=SequenceType.AMINO_ACID, description="序列类型")
    vector_id: str = Field(default="pET-28a", description="目标载体ID")
    cloning_method: CloningMethod = Field(default=CloningMethod.GIBSON, description="克隆方法")
    # 插入片段来源与克隆方法正交：pcr=设计 PCR 扩增引物；gene_synthesis=设计重叠合成 oligo
    insert_source: Literal["pcr", "gene_synthesis"] = Field(default="pcr", description="插入片段来源")
    optimize_codons: bool = Field(default=True, description="是否进行密码子优化")
    target_species: str = Field(default="ecoli", description="目标物种")
    gc_min: float = Field(default=40.0, ge=20, le=50)
    gc_max: float = Field(default=60.0, ge=50, le=80)
    homology_arm: int = Field(default=20, ge=15, le=40, description="Gibson同源臂长度")
    # 不设默认酶：此前缺省 BsaI，经典双酶切请求会被静默套上一个 Type IIS 酶，
    # 产出完全错误的引物而无人察觉。按克隆方法在下方校验器中强制要求
    enzyme: Optional[str] = Field(default=None, min_length=1, description="克隆酶（Golden Gate Type IIS 酶；restriction 单酶兼容回退）")
    # 双酶切：restriction 方法 5'/3' 端分别用不同酶；缺省回落到 enzyme（兼容单酶切）
    enzyme_5: Optional[str] = Field(default=None, description="双酶切 5' 端限制酶")
    enzyme_3: Optional[str] = Field(default=None, description="双酶切 3' 端限制酶")
    oligo_length: int = Field(default=60, ge=40, le=100, description="[已废弃，改用 oligo_length_min/max] 未提供范围时的固定长度")
    # 寡核苷酸长度范围：合成 oligo 在 [min, max] 内自动均衡切分
    oligo_length_min: Optional[int] = Field(default=None, ge=20, le=100, description="寡核苷酸最短长度")
    oligo_length_max: Optional[int] = Field(default=None, ge=30, le=120, description="寡核苷酸最长长度")
    overlap_length: int = Field(default=20, ge=10, le=40, description="相邻寡核苷酸/克隆片段重叠区长度(bp)")
    # Gibson：指定载体上的酶切位点用于定位同源重组位置（缺省为 MCS 起点）
    gibson_site: Optional[str] = Field(default=None, description="定位同源重组位置的酶切位点名称")
    # 密码子优化时需排除的限制酶位点（优化序列不含这些识别序列）
    exclude_enzymes: List[str] = Field(default_factory=list, description="需从优化序列排除的限制酶")
    protocol_language: Literal["zh", "en"] = Field(default="zh", description="实验方案语言")

    @model_validator(mode="after")
    def validate_ranges(self):
        if self.gc_min > self.gc_max:
            raise ValueError("gc_min 不能大于 gc_max")
        if self.overlap_length >= self.oligo_length:
            raise ValueError("overlap_length 必须小于 oligo_length")
        # 寡核苷酸长度范围校验（未提供时回落到单一 oligo_length）
        eff_min = self.oligo_length_min or self.oligo_length
        eff_max = self.oligo_length_max or self.oligo_length
        if eff_min > eff_max:
            raise ValueError("oligo_length_min 不能大于 oligo_length_max")
        if eff_max <= self.overlap_length:
            raise ValueError("oligo_length_max 必须大于 overlap_length")
        # 兼容旧契约：旧客户端用 cloning_method=gene_synthesis 表达「插入片段由全基因合成获得」。
        # 合成方式与克隆方法正交，归一为 insert_source=gene_synthesis + 默认限制性克隆
        if self.cloning_method == CloningMethod.GENE_SYNTHESIS:
            self.insert_source = "gene_synthesis"
            self.cloning_method = CloningMethod.RESTRICTION
        # 克隆酶必填（不再默认 BsaI）
        if self.cloning_method == CloningMethod.GOLDEN_GATE:
            if not self.enzyme:
                raise ValueError("Golden Gate 需指定 Type IIS 酶（enzyme），如 BsaI/BsmBI/BbsI")
            from core.enzyme_sites import golden_gate_geometry
            if golden_gate_geometry(self.enzyme) is None:
                raise ValueError(
                    f"不支持的 Golden Gate 酶 {self.enzyme}：需为可商购、切出 4 nt 5' 突出端的 "
                    "Type IIS 酶（如 BsaI/BsmBI/BbsI/Esp3I/PaqCI）"
                )
        if self.cloning_method == CloningMethod.RESTRICTION and not (
            (self.enzyme_5 and self.enzyme_3) or self.enzyme
        ):
            raise ValueError(
                "限制性克隆需指定限制酶：enzyme_5 与 enzyme_3（或单酶 enzyme）"
            )
        return self


class DesignRequest(DesignOptions):
    """设计请求"""
    sequence: str = Field(..., min_length=1, max_length=MAX_INPUT_SEQ_CHARS,
                          description="输入序列（氨基酸或DNA）")
    sequence_name: str = Field(default="insert", min_length=1, max_length=100, description="序列名称")
    include_report: bool = Field(default=True, description="生成设计报告")

    @model_validator(mode="after")
    def validate_sequence_length(self):
        # 终审 C-03：max_length=100_000 不是防护而是 CPU DoS 入口——
        # _sliding_window_refinement 是 O(n²)（每个候选重拼整条序列并全串
        # 扫 motif/polyX），实测 1k aa 0.33s / 4k aa 1.7s / 10k aa 19.6s。
        # 按序列类型收紧（氨基酸 5000 aa、DNA 20000 nt，可环境变量覆盖）
        limit = _seq_limit_for(self.sequence_type)
        if len(self.sequence) > limit:
            kind = "氨基酸残基" if self.sequence_type == SequenceType.AMINO_ACID else "碱基"
            raise ValueError(f"序列过长（{len(self.sequence)} > {limit} {kind}），请分段设计")
        return self


class PrimerInfo(BaseModel):
    """引物信息"""
    name: str
    sequence: str
    full_sequence: str
    tm: float
    gc_content: float
    length: int
    overhang: Optional[str] = None
    # 目标区域坐标（0-indexed，半开区间）：交叉杂交审查时用于排除相邻 oligo
    # 的预期 overlap 配对；非合成 oligo（如克隆引物对）为 None
    target_start: Optional[int] = None
    target_end: Optional[int] = None
    notes: Optional[str] = None


class DesignResult(BaseModel):
    """设计结果"""
    design_id: str
    status: DesignStatus
    # 创建者（匿名创建为 None → 公开可读，与测序分析记录同一口径）
    user_id: Optional[str] = None

    # 序列信息
    input_sequence: str
    optimized_sequence: Optional[str] = None

    # 完整构建体（插入 + 载体骨架）
    construct_sequence: Optional[str] = None
    construct_features: List[Dict] = Field(default_factory=list)
    insert_start: Optional[int] = None
    insert_end: Optional[int] = None

    # 优化指标
    cai: Optional[float] = None
    gc_content: Optional[float] = None

    # 载体信息
    vector_id: str
    vector_name: str = ""
    final_length: Optional[int] = None

    # 引物
    primers: List[PrimerInfo] = Field(default_factory=list)

    # 克隆信息
    cloning_method: CloningMethod
    clone_protocol: Optional[str] = None

    # 验证结果
    validation_passed: bool = False
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)

    # 时间戳
    created_at: datetime
    completed_at: Optional[datetime] = None


# ==================== 载体信息 ====================

class VectorMapPreviewFeature(BaseModel):
    name: str
    type: str  # 契约 FeatureType（已归一）
    start: int
    end: int
    strand: Literal[1, -1, 0]


class VectorMapPreview(BaseModel):
    """载体列表缩略图（MapThumbnail）用：不含序列"""
    length: int
    topology: Literal["circular", "linear"]
    features: List[VectorMapPreviewFeature] = Field(default_factory=list)


class VectorInfo(BaseModel):
    """载体信息"""
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
    # 拓扑：导入的线性片段（PCR 产物/Ensembl 片段）不再是"一律环状"
    topology: Literal["circular", "linear"] = "circular"
    map_preview: Optional[VectorMapPreview] = None  # 仅列表接口填充
    # 结构化出处（YAML 的 data_provenance）：内置载体记来源库/集合/日期，
    # 导入载体记来源站 + URL + 许可 + 抓取时间
    data_provenance: Dict[str, str] = Field(default_factory=dict)


# ==================== 批量设计 ====================

class BatchDesignRequest(DesignOptions):
    """批量设计请求

    克隆酶与单条设计同一套校验（继承 DesignOptions）：Golden Gate 缺 enzyme 或
    酶不在 golden_gate_enzymes 候选内时，整个批量请求在提交时即 422，不建任务。
    刻意不在服务端回落 BsaI：v2.6.0 已为单条设计移除默认酶（静默套酶会产出
    错误引物而无人察觉），批量与单条保持同一契约，调用方须显式传 enzyme。
    """
    sequences: List[str] = Field(..., min_length=1, max_length=100)
    sequence_names: Optional[List[str]] = None

    @model_validator(mode="after")
    def validate_batch(self):
        if any(not sequence.strip() for sequence in self.sequences):
            raise ValueError("批量序列不能为空")
        # 终审 C-03：同样按序列类型收紧（单条）与总量上限
        limit = _seq_limit_for(self.sequence_type)
        if any(len(sequence) > limit for sequence in self.sequences):
            kind = "氨基酸残基" if self.sequence_type == SequenceType.AMINO_ACID else "碱基"
            raise ValueError(f"批量中有序列过长（>{limit} {kind}），请分段设计")
        # 批量总量也留一半余量：N 条都顶到上限时单请求计算量仍然过大
        batch_total = max(limit, limit * len(self.sequences) // 2)
        if sum(len(sequence) for sequence in self.sequences) > batch_total:
            raise ValueError(f"批量序列总长度不能超过 {batch_total}")
        if self.sequence_names is not None and len(self.sequence_names) != len(self.sequences):
            raise ValueError("sequence_names 数量必须与 sequences 一致")
        return self


class BatchDesignStatus(BaseModel):
    """批量设计状态"""
    batch_id: str
    total: int
    user_id: Optional[str] = None  # 创建者（匿名创建为 None → 公开可读）
    completed: int
    failed: int
    status: str  # pending, running, completed
    results: List[str] = Field(default_factory=list)  # design_ids
    errors: List[Dict] = Field(default_factory=list)


class BatchProgressResponse(BaseModel):
    """批量设计进度响应"""
    batch_id: str
    total: int
    completed: int
    failed: int
    pending: int
    status: str
    progress_percent: float
    results: List[Dict] = Field(default_factory=list)
    errors: List[Dict] = Field(default_factory=list)


# ==================== 载体管理 ====================

class VectorUpdateRequest(BaseModel):
    """载体更新请求"""
    name: Optional[str] = None
    description: Optional[str] = None
    vector_type: Optional[str] = None
    host: Optional[List[str]] = None
    antibiotic_resistance: Optional[List[str]] = None
    copy_number: Optional[str] = None


class VectorPreviewResponse(BaseModel):
    """载体预览响应"""
    id: str
    name: str
    source: str
    length: int
    description: str
    gc_content: float
    features_count: int
    warnings: List[str] = []


class BatchImportRequest(BaseModel):
    """批量导入请求"""
    ncbi_ids: List[str] = []
    file_paths: List[str] = []


# ==================== 在线序列来源导入 ====================
# 契约与 core/online_sources.py 对齐：这里是请求体与预览响应，落库形状见
# app/vector_import.py。许可文案随 /api/vectors/import/sources 一起下发。

class SnapGeneImportRequest(BaseModel):
    """SnapGene 在线库导入请求"""
    set_id: str = Field(..., min_length=1, max_length=64,
                        description="集合 ID，如 yeast_plasmids")
    plasmid_id: str = Field(..., min_length=1, max_length=120,
                            description="序列 ID（索引里的 url 字段），如 pYES2")


class EnsemblImportRequest(BaseModel):
    """Ensembl 导入请求"""
    identifier: str = Field(..., min_length=1, max_length=64,
                            description="Ensembl ID，如 ENSG00000139618 / ENST00000288602")
    seq_type: Literal["genomic", "cds", "cdna"] = "cds"
    name: Optional[str] = Field(default=None, max_length=80)


class OnlinePreviewResponse(BaseModel):
    """在线来源预览（不落库）"""
    source: str
    id: str
    name: str
    url: str = ""
    length: int
    topology: str
    gc_content: float
    features: List[Dict] = []
    warnings: List[str] = []
    license: str = ""


# ==================== 质粒图谱（契约 plasmid-map/1，docs/MAP.md） ====================
# 路由直接返回 core.plasmid_map 组装的字典（可选字段缺省即省略，符合契约）；
# 这些模型用于请求体校验（编辑后重算、导出）与 OpenAPI 文档。

from core.plasmid_map import types as _map_limits  # noqa: E402  契约条数上限的唯一来源

MAP_MAX_LENGTH = int(os.environ.get("MAP_MAX_LENGTH", "300000"))
MAP_MAX_FEATURES = _map_limits.MAX_FEATURES
MAP_MAX_ENZYMES = 1000

MapFeatureType = Literal[
    "cds", "promoter", "terminator", "rep_origin", "rbs", "marker", "tag",
    "signal_peptide", "polyA_signal", "enhancer", "ltr", "primer_bind",
    "protein_bind", "misc_rna", "misc_feature", "insert", "mcs",
]
MapTopology = Literal["circular", "linear"]


class MapSegment(BaseModel):
    start: int = Field(..., ge=1)
    end: int = Field(..., ge=1)


class MapAutoInfo(BaseModel):
    library: str = Field(..., max_length=80)
    identity: float = Field(..., ge=0, le=1)
    coverage: float = Field(..., ge=0, le=1)
    fragment: bool


class MapFeature(BaseModel):
    id: Optional[str] = Field(default=None, max_length=64)
    name: str = Field(..., max_length=200)
    type: MapFeatureType
    start: int = Field(..., ge=1)
    end: int = Field(..., ge=1)
    strand: Literal[1, -1, 0] = 0
    segments: Optional[List[MapSegment]] = Field(default=None, max_length=_map_limits.MAX_SEGMENTS)
    color: Optional[str] = Field(default=None, max_length=32)
    source: Literal["file", "auto", "design", "vector"] = "file"
    notes: Optional[str] = Field(default=None, max_length=2000)
    qualifiers: Optional[Dict[str, str]] = Field(default=None, max_length=_map_limits.MAX_QUALIFIERS)
    auto: Optional[MapAutoInfo] = None


class MapEnzymeSite(BaseModel):
    position: int
    cut_top: int
    cut_bottom: int
    strand: Literal[1, -1]
    overhang: Optional[str] = Field(default=None, max_length=64)
    blocked_by: Optional[List[Literal["dam", "dcm"]]] = Field(default=None, max_length=2)


class MapEnzyme(BaseModel):
    name: str
    recognition: str
    ends: Literal["5'", "3'", "blunt"]
    overhang: Optional[str] = None
    cut_count: int
    sites: List[MapEnzymeSite] = Field(default_factory=list)
    # 位点过多时 sites 只含部分明细（cut_count 仍为完整计数）
    sites_truncated: Optional[bool] = None
    methylation: Optional[List[str]] = None


class MapOrf(BaseModel):
    start: int
    end: int
    strand: Literal[1, -1]
    frame: Literal[0, 1, 2]
    aa_length: int


class MapPrimer(BaseModel):
    id: Optional[str] = Field(default=None, max_length=64)
    name: str = Field(..., max_length=200)
    start: int = Field(..., ge=1)
    end: int = Field(..., ge=1)
    strand: Literal[1, -1] = 1
    sequence: Optional[str] = Field(default=None, max_length=_map_limits.MAX_PRIMER_SEQ)


class MapMeta(BaseModel):
    source_format: Literal["genbank", "fasta", "snapgene", "raw", "design", "vector"]
    enzyme_set: str = Field(default="common", max_length=40)
    warnings: List[str] = Field(default_factory=list, max_length=_map_limits.MAX_WARNINGS)


class MapDocument(BaseModel):
    schema_: Literal["plasmid-map/1"] = Field(default="plasmid-map/1", alias="schema")
    name: str = Field(..., max_length=200)
    topology: MapTopology
    length: int = Field(..., ge=1)
    sequence: str = Field(..., min_length=1, max_length=MAP_MAX_LENGTH)
    features: List[MapFeature] = Field(default_factory=list, max_length=MAP_MAX_FEATURES)
    enzymes: List[MapEnzyme] = Field(default_factory=list, max_length=MAP_MAX_ENZYMES)
    orfs: List[MapOrf] = Field(default_factory=list, max_length=_map_limits.MAX_ORFS)
    primers: List[MapPrimer] = Field(default_factory=list, max_length=_map_limits.MAX_PRIMERS)
    meta: Optional[MapMeta] = None

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def _length_matches(self):
        if self.length != len(self.sequence):
            raise ValueError("length 与 sequence 长度不一致")
        return self


class MapSaveFeature(BaseModel):
    """图谱页「另存为载体」的特征（比 MapFeature 宽松：不限于图谱类型枚举）"""
    name: str = Field(..., min_length=1, max_length=200)
    type: str = Field(default="misc_feature", max_length=60)
    start: int = Field(..., ge=1)
    end: int = Field(..., ge=1)
    strand: Literal[1, -1, 0] = 0
    notes: Optional[str] = Field(default=None, max_length=2000)
    description: Optional[str] = Field(default=None, max_length=300)
    segments: Optional[List[MapSegment]] = Field(default=None, max_length=200)
    color: Optional[str] = Field(default=None, max_length=32)


class SaveMapAsVectorRequest(BaseModel):
    """把图谱页当前打开的（可编辑）序列与注释存进载体库"""
    name: str = Field(..., min_length=1, max_length=80)
    sequence: str = Field(..., min_length=1, max_length=MAP_MAX_LENGTH)
    topology: MapTopology = "circular"
    features: List[MapSaveFeature] = Field(default_factory=list, max_length=MAP_MAX_FEATURES)
    source: str = Field(default="map", max_length=40,
                        description="出处标签，如 snapgene / ensembl / ncbi / upload / paste")
    url: str = Field(default="", max_length=500)
    description: str = Field(default="", max_length=2000)
