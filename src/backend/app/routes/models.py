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
    enzyme: str = Field(default="BsaI", min_length=1, description="克隆酶（Golden Gate Type IIS 酶；restriction 单酶兼容回退）")
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


# ==================== 质粒图谱 ====================

class EnzymeSite(BaseModel):
    """酶切位点（1-based）"""
    name: str
    position: int
    strand: str = "+"
    cut_fwd: int
    cut_rev: int
    overhang: Optional[str] = None
    recognition: Optional[str] = None


class PlasmidMapData(BaseModel):
    """质粒图谱数据"""
    name: str
    length: int
    sequence: Optional[str] = None
    features: List[Dict] = []
    enzyme_sites: List[EnzymeSite] = Field(default_factory=list)
