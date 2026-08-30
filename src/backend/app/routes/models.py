"""共享 Pydantic 模型 — 从 main.py 提取"""

from pydantic import BaseModel, Field, model_validator
from typing import List, Optional, Dict, Literal
from enum import Enum
from datetime import datetime


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
    enzyme: str = Field(default="BsaI", min_length=1, description="克隆酶（Golden Gate Type IIS 酶；restriction 单酶兼容回退）")
    # 双酶切：restriction 方法 5'/3' 端分别用不同酶；缺省回落到 enzyme（兼容单酶切）
    enzyme_5: Optional[str] = Field(default=None, description="双酶切 5' 端限制酶")
    enzyme_3: Optional[str] = Field(default=None, description="双酶切 3' 端限制酶")
    oligo_length: int = Field(default=60, ge=40, le=100, description="[已废弃，改用 oligo_length_min/max] 未提供范围时的固定长度")
    # 寡核苷酸长度范围：合成 oligo 在 [min, max] 内自动均衡切分
    oligo_length_min: Optional[int] = Field(default=None, ge=20, le=100, description="寡核苷酸最短长度")
    oligo_length_max: Optional[int] = Field(default=None, ge=30, le=120, description="寡核苷酸最长长度")
    overlap_length: int = Field(default=20, ge=10, le=30, description="重叠区域长度(bp)")
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
        return self


class DesignRequest(DesignOptions):
    """设计请求"""
    sequence: str = Field(..., min_length=1, max_length=100_000, description="输入序列（氨基酸或DNA）")
    sequence_name: str = Field(default="insert", min_length=1, max_length=100, description="序列名称")
    include_report: bool = Field(default=True, description="生成设计报告")


class PrimerInfo(BaseModel):
    """引物信息"""
    name: str
    sequence: str
    full_sequence: str
    tm: float
    gc_content: float
    length: int
    overhang: Optional[str] = None
    notes: Optional[str] = None


class DesignResult(BaseModel):
    """设计结果"""
    design_id: str
    status: DesignStatus

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


# ==================== 批量设计 ====================

class BatchDesignRequest(DesignOptions):
    """批量设计请求"""
    sequences: List[str] = Field(..., min_length=1, max_length=100)
    sequence_names: Optional[List[str]] = None

    @model_validator(mode="after")
    def validate_batch(self):
        if any(not sequence.strip() for sequence in self.sequences):
            raise ValueError("批量序列不能为空")
        if any(len(sequence) > 100_000 for sequence in self.sequences):
            raise ValueError("单条序列长度不能超过 100000")
        if sum(len(sequence) for sequence in self.sequences) > 1_000_000:
            raise ValueError("批量序列总长度不能超过 1000000")
        if self.sequence_names is not None and len(self.sequence_names) != len(self.sequences):
            raise ValueError("sequence_names 数量必须与 sequences 一致")
        return self


class BatchDesignStatus(BaseModel):
    """批量设计状态"""
    batch_id: str
    total: int
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


# ==================== 质粒图谱 ====================

class PlasmidMapData(BaseModel):
    """质粒图谱数据"""
    name: str
    length: int
    sequence: Optional[str] = None
    features: List[Dict] = []
