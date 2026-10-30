"""共享 Pydantic 模型 — 从 main.py 提取"""

import os
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import List, Optional, Dict, Literal, Tuple
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
# 图谱/载体序列长度上限（bp）：图谱契约与本文件的一次性载体模型共用
# （此处提前定义：DesignRequest 的内联载体在文件上部就要用到它）
MAP_MAX_LENGTH = int(os.environ.get("MAP_MAX_LENGTH", "300000"))
# 上传文件体积上限：图谱页 /api/map/parse 与设计页载体上传（本地上传）共用
MAP_MAX_UPLOAD_MB = float(os.environ.get("MAP_MAX_UPLOAD_MB", "5"))
MAP_MAX_UPLOAD_BYTES = int(MAP_MAX_UPLOAD_MB * 1024 * 1024)


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
    enzyme_5: Optional[str] = Field(default=None, description="双酶切位点之一（方向由载体表达盒判定，顺序无关）")
    enzyme_3: Optional[str] = Field(default=None, description="双酶切位点之二（方向由载体表达盒判定，顺序无关）")
    # 插入方向：auto=按载体启动子/RBS 判定；forward=基因 5′ 端接上游切点；
    # reverse=反向（反义构建）。只在 auto 判不出来或用户明确要反义时用得上
    # auto=按载体表达盒判定；reverse=与判定相反（反义构建）；
    # plus/minus=用户看预览图后手动指定基因落在哪条链
    insert_direction: Literal["auto", "reverse", "plus", "minus"] = Field(
        default="auto", description="插入方向：auto/reverse/plus/minus")
    # 多切点酶切知情确认：所选酶在保留骨架上还有切点时（预览有
    # backbone_cut_risk 警告），默认拒绝按位点建模；勾选后按用户的
    # 有意选择正常组装（结果警告里明确记录该知情决定）
    allow_backbone_cuts: bool = Field(
        default=False,
        description="知情确认：接受所选酶在保留骨架上的额外切点（多切点酶切）")
    # 显式选择要胶回收的骨架片段（预览 cut_options 之一）；缺省 = 自动最外侧
    cut_pair: Optional[Tuple[int, int]] = Field(default=None, min_length=2, max_length=2,
                                                description="用户选择的切点对 (left_cut, right_cut)")
    # NdeI/NcoI 这类位点自带 ATG 且在连接处复原后充当起始密码子；插入序列
    # 再以 ATG 开头会让蛋白 N 端出现连续两个 Met。默认自动去掉插入序列开头
    # 的 ATG（第一个甲硫氨酸由位点提供，蛋白与输入氨基酸一致）
    trim_redundant_start: bool = Field(
        default=True,
        description="限制性克隆位点自带起始密码子时，自动去除插入序列开头的冗余 ATG")
    # Gibson 载体线性化方式：双酶切（默认，主流实践——两把酶切走区间，
    # 组装几何与限制性克隆一致，方向/起始密码子/CDS 逻辑全套复用）或
    # 单位点（单切点插入，旧模型）。双酶切需要 enzyme_5 + enzyme_3
    gibson_linearization: Literal["double_digest", "single_site", "custom_arms"] = Field(
        default="double_digest", description="Gibson 载体线性化方式")
    # 自定义同源臂（custom_arms 模式）：两条臂按同一方向粘贴即可，工具按
    # 整条插入分子两种取向试装配；精确唯一匹配，臂长 15-200bp
    homology_arm_5: str = Field(
        default="", description="自定义 5′ 重组臂序列（custom_arms 模式必填）")
    homology_arm_3: str = Field(
        default="", description="自定义 3′ 重组臂序列（custom_arms 模式必填）")
    # 双酶切线性化时同源臂是否把位点重建回连接处：保留（默认）= 构建体里
    # 位点完整可再次酶切；关闭 = 无缝连接，位点碱基被同源臂重组消费掉
    keep_restriction_sites: bool = Field(
        default=True,
        description="Gibson 双酶切线性化时保留连接处的酶切位点（关闭为无缝连接）")
    # 质粒命名：默认用序列名；开启后加载体名前缀（载体名-序列名）
    vector_name_prefix: bool = Field(
        default=False, description="质粒名加载体名前缀（载体名-序列名）")
    oligo_length: int = Field(default=60, ge=40, le=100, description="[已废弃，改用 oligo_length_min/max] 未提供范围时的固定长度")
    # 寡核苷酸长度范围：合成 oligo 在 [min, max] 内自动均衡切分
    oligo_length_min: Optional[int] = Field(default=None, ge=20, le=100, description="寡核苷酸最短长度")
    oligo_length_max: Optional[int] = Field(default=None, ge=30, le=120, description="寡核苷酸最长长度")
    overlap_length: int = Field(default=20, ge=10, le=40, description="相邻寡核苷酸/克隆片段重叠区长度(bp)")
    # Gibson：指定载体上的酶切位点用于定位同源重组位置（缺省为 MCS 起点）
    gibson_site: Optional[str] = Field(default=None, description="定位同源重组位置的酶切位点名称")
    # 需从优化序列排除的限制酶位点。只在「氨基酸输入 + 启用密码子优化」时生效；
    # DNA 输入或关闭优化时后端直接忽略（不校验、不告警，也不作用于载体/组装）。
    exclude_enzymes: List[str] = Field(
        default_factory=list,
        description="需从优化序列排除的限制酶（仅氨基酸输入且启用密码子优化时生效）",
    )
    # 5′ 前置 / 3′ 尾随固定 DNA 接头（相对基因读法；如 Kozak GCCACC）：
    # 按基因方向拼进插入序列后再走组装，负链插入时随整段反向互补，
    # 位置自然正确。既定序列不参与密码子优化与排除位点避让
    upstream_flank: Optional[str] = Field(
        default=None, max_length=3000, description="5′ 前置 DNA 序列（基因方向，如 Kozak）")
    downstream_flank: Optional[str] = Field(
        default=None, max_length=3000, description="3′ 尾随 DNA 序列（基因方向）")
    protocol_language: Literal["zh", "en"] = Field(default="zh", description="实验方案语言")

    @field_validator("upstream_flank", "downstream_flank", mode="before")
    @classmethod
    def _normalize_flank(cls, value):
        """接头是既定 DNA：只做归一（U→T、小写转大写、去空白/全角），
        非 IUPAC 碱基直接报字段错误——接头里混进非法字符多半是粘贴错序列"""
        if not isinstance(value, str):
            return value
        from core.seq_input import normalize_dna_text

        seq = normalize_dna_text(value)
        if not seq:
            return None
        from core.plasmid_map.parsers import _IUPAC_DNA

        bad = sorted(set(seq) - _IUPAC_DNA)
        if bad:
            raise ValueError(
                f"接头序列含非法字符：{''.join(bad[:10])}（只接受 IUPAC DNA 碱基）")
        return seq

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


# ==================== 一次性载体（设计页本地上传 / 在线导入） ====================
# 载体库是共享库、写入口只对管理员开放，普通用户手里的 .dna/.gb 或在线来源的
# 质粒没法进库。设计页的上传/在线导入因此把载体**内联**提交（DesignRequest.vector），
# 只在本次设计里用，不写载体库（见 app/adhoc_vector.py）。


class DesignVectorFeature(BaseModel):
    """一次性载体的元件（形状与载体 YAML/图谱契约兼容，strand 允许 1/-1/0）"""
    name: str = Field(default="feature", max_length=80)
    type: str = Field(default="misc_feature", max_length=40)
    start: int = Field(..., ge=1)
    end: int = Field(..., ge=1)
    strand: Literal["+", "-", 1, -1, 0] = "+"
    description: str = Field(default="", max_length=300)


class DesignVectorMcs(BaseModel):
    """载体 MCS 判定结果（feature=文件注释给出，inferred=按唯一位点推测）"""
    start: int
    end: int
    origin: Literal["feature", "inferred"] = "inferred"
    enzymes: List[str] = Field(default_factory=list)


class DesignVectorInput(BaseModel):
    """设计请求内联的一次性载体"""
    id: Optional[str] = Field(default=None, max_length=120)
    name: str = Field(default="vector", min_length=1, max_length=80)
    sequence: str = Field(..., min_length=1, max_length=MAP_MAX_LENGTH)
    topology: Literal["circular", "linear"] = "circular"
    features: List[DesignVectorFeature] = Field(default_factory=list, max_length=5000)
    source: Optional[str] = Field(default=None, max_length=40)
    url: Optional[str] = Field(default=None, max_length=300)

    @field_validator("sequence")
    @classmethod
    def _clean_sequence(cls, value: str) -> str:
        # 与图谱/上传入口同一套清洗（空白与数字剔除、U→T、非法碱基拒绝），
        # 但错误落成 422 的字段校验错误，而不是把设计任务跑成 failed
        from core.plasmid_map.parsers import MapParseError, clean_sequence

        try:
            return clean_sequence(value, MAP_MAX_LENGTH)
        except MapParseError as exc:
            raise ValueError(str(exc)) from exc


class DesignVectorOnlineRequest(BaseModel):
    """在线导入一次性载体：按来源只取需要的参数（缺项由端点按来源校验）"""
    source: Literal["snapgene", "ensembl", "ncbi", "addgene"]
    # SnapGene 在线库
    set_id: Optional[str] = Field(default=None, max_length=64)
    plasmid_id: Optional[str] = Field(default=None, max_length=120)
    # Ensembl
    identifier: Optional[str] = Field(default=None, max_length=64)
    seq_type: Literal["genomic", "cds", "cdna"] = "cds"
    # NCBI
    accession: Optional[str] = Field(default=None, max_length=24)
    # Addgene（仅管理员，且需部署方配置 ADDGENE_SESSION_COOKIE）
    addgene_id: Optional[str] = Field(default=None, max_length=10)
    fmt: Literal["genbank", "dna"] = "genbank"


class DesignVectorPayload(BaseModel):
    """上传/在线导入的回执：可直接作为 DesignRequest.vector 回传"""
    id: str
    name: str
    source: str = ""
    url: str = Field(default="", max_length=300)
    license: str = ""
    sequence: str
    topology: Literal["circular", "linear"] = "circular"
    length: int
    features: List[DesignVectorFeature] = Field(default_factory=list)
    mcs: Optional[DesignVectorMcs] = None
    unique_enzymes: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class RestrictionPreviewRequest(BaseModel):
    """双酶切实时预览：两把酶 + 载体 → 成品构建体的布局（用于表单里的示意图）

    cloning_method="gibson" 时按 Gibson 双酶切线性化预览（几何与限制性
    克隆相同，连接语义走 join="gibson"，保留位点开关生效）；
    cloning_method="custom_arms" 时按自定义同源臂定位预览——臂定位的
    ValueError（无匹配/多处匹配/围不出区间）直接返回给前端展示。
    """

    enzymes: List[str] = Field(default_factory=list, max_length=2,
                               description="两个酶切位点（顺序无关）")
    vector_id: Optional[str] = Field(default=None, max_length=120)
    vector: Optional[DesignVectorInput] = Field(default=None, description="内联一次性载体")
    insert_length: Optional[int] = Field(default=None, ge=30, le=30000,
                                        description="基因长度（氨基酸输入按 aa×3 估）")
    direction: Literal["auto", "reverse", "plus", "minus"] = "auto"
    cloning_method: Literal["restriction", "gibson", "custom_arms"] = "restriction"
    keep_restriction_sites: bool = True
    # 显式选择要胶回收的骨架片段（切点对，多切点酶时预览的 cut_options 之一）；
    # 缺省 = 自动选最外侧切点对（内部同酶位点吸进废片段）
    cut_pair: Optional[Tuple[int, int]] = Field(default=None, min_length=2, max_length=2,
                                                description="用户选择的切点对 (left_cut, right_cut)")
    homology_arm_5: Optional[str] = Field(default=None, max_length=200,
                                          description="5′ 重组臂（custom_arms 模式必填）")
    homology_arm_3: Optional[str] = Field(default=None, max_length=200,
                                          description="3′ 重组臂（custom_arms 模式必填）")


class DesignRequest(DesignOptions):
    """设计请求"""
    sequence: str = Field(..., min_length=1, max_length=MAX_INPUT_SEQ_CHARS,
                          description="输入序列（氨基酸或DNA）")
    sequence_name: str = Field(default="insert", min_length=1, max_length=100, description="序列名称")
    include_report: bool = Field(default=True, description="生成设计报告")
    # 一次性载体：给了就用它（忽略 vector_id 是否在库里），不写载体库
    vector: Optional[DesignVectorInput] = Field(
        default=None, description="内联载体（设计页本地上传/在线导入）；为空时按 vector_id 查载体库")

    @field_validator("sequence", mode="before")
    @classmethod
    def _normalize_sequence(cls, value):
        """先归一（全角/异体星号/空白）再校验长度与内容

        中文输入法下 Shift+8 打出的是全角＊（U+FF0A），直接进密码子优化会被
        报成「无效的氨基酸代码」——用户看到的就是「* 无法识别」。
        """
        from core.seq_input import normalize_sequence_text

        return normalize_sequence_text(value) if isinstance(value, str) else value

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
    # 克隆工作序列（组装与引物退火用的就是它）：位点自带 ATG 充当起始密码子时
    # 已去除开头的 ATG，比 optimized_sequence 短 3 bp；未裁剪时与之一致
    cloned_sequence: Optional[str] = None

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
    # 成品质粒名：默认为序列名；开启 vector_name_prefix 后为「载体名-序列名」。
    # GenBank 导出的 LOCUS 与下载文件名都以它为准（缺省回退 design_id）
    plasmid_name: Optional[str] = None
    final_length: Optional[int] = None
    # 优化序列开头的 ATG 已被去除（NdeI 等位点自带 ATG 充当起始密码子）：
    # 前端据此在优化序列旁挂提示，解释「为什么比输入蛋白少 3 bp」
    start_codon_removed: bool = False
    # 本设计实际使用的克隆酶（组装解析后的规范拼写）。图谱据此强制显示
    # 并高亮克隆酶标签——用户选的酶可能是多切点酶（如 pcDNA3.1 上的
    # NcoI），标签曾按「切点数多优先隐藏」被挤掉，图谱读起来像插入落在
    # 别的巧合单切点酶之间
    cloning_enzymes: Optional[List[str]] = None

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

class RowParams(BaseModel):
    """批量设计中单条序列的参数覆盖（全 Optional）。

    非 None 字段覆盖批量级统一参数（Excel 参数列经 alias 归一后填进来）；
    None = 继承批级。合法性不在本模型校验——行级 DesignRequest 构造时
    复用 DesignOptions 的同一套校验器，失败落该行 errors 而非炸整批。
    vector 字段是载体库的载体名/ID 字符串（Excel 列），合并时解析成
    vector_id；一次性载体体积大不进表格，仍走批量级 vector 字段整批共用。
    """
    vector: Optional[str] = Field(default=None, max_length=120,
                                  description="载体名/ID（行级覆盖；按 vector_source 解析）")
    vector_source: Optional[Literal["library", "inline"]] = Field(
        default=None,
        description="载体来源：library=载体库；inline=自己上传载体（批量页上传的载体池）。留空先库后池")
    cloning_method: Optional[CloningMethod] = None
    insert_source: Optional[Literal["pcr", "gene_synthesis"]] = None
    gibson_linearization: Optional[Literal[
        "double_digest", "single_site", "custom_arms"]] = None
    enzyme: Optional[str] = Field(default=None, min_length=1, max_length=40)
    enzyme_5: Optional[str] = Field(default=None, max_length=40)
    enzyme_3: Optional[str] = Field(default=None, max_length=40)
    insert_direction: Optional[Literal[
        "auto", "reverse", "plus", "minus"]] = None
    target_species: Optional[str] = Field(default=None, max_length=20)
    exclude_enzymes: Optional[List[str]] = Field(
        default=None, max_length=10,
        description="需从该行优化序列排除的限制酶（覆盖批量级 exclude_enzymes）")


class BatchDesignRequest(DesignOptions):
    """批量设计请求

    克隆酶与单条设计同一套校验（继承 DesignOptions）：Golden Gate 缺 enzyme 或
    酶不在 golden_gate_enzymes 候选内时，整个批量请求在提交时即 422，不建任务。
    刻意不在服务端回落 BsaI：v2.6.0 已为单条设计移除默认酶（静默套酶会产出
    错误引物而无人察觉），批量与单条保持同一契约，调用方须显式传 enzyme。
    """
    sequences: List[str] = Field(..., min_length=1, max_length=100)
    sequence_names: Optional[List[str]] = None
    # 一次性载体：与单条设计的 DesignRequest.vector 同语义——给了就整批用
    # 它（忽略 vector_id），不写载体库。批量页的上传/在线导入经此前传
    vector: Optional[DesignVectorInput] = Field(
        default=None, description="内联一次性载体（整批共用）；为空时按 vector_id 查载体库")
    # 行级参数覆盖（Excel 参数列 / 前端逐行覆盖）：与 sequences 对齐，
    # 元素可为 None（该行完全继承批级）；RowParams 内非 None 字段覆盖批级。
    # 行级合法性在后台任务构造每条 DesignRequest 时校验，失败落该行 errors
    row_params: Optional[List[Optional[RowParams]]] = Field(
        default=None, description="每条序列的参数覆盖（与 sequences 等长，元素可为 null）")
    # 载体池（复杂批量「批量上传载体」区块）：一次性载体不入库，行级
    # vector_source=inline 时按载体名/文件名从这里取
    vectors: Optional[List[DesignVectorInput]] = Field(
        default=None, max_length=100,
        description="批量上传的一次性载体池（行级「自己上传载体」来源引用）")

    @field_validator("sequences", mode="before")
    @classmethod
    def _normalize_sequences(cls, value):
        """与单条设计同一套字符归一（全角＊/全角空格/零宽字符）"""
        from core.seq_input import normalize_sequence_text

        if not isinstance(value, list):
            return value
        return [normalize_sequence_text(v) if isinstance(v, str) else v for v in value]

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
        if self.row_params is not None and len(self.row_params) != len(self.sequences):
            raise ValueError("row_params 数量必须与 sequences 一致")
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

# MAP_MAX_LENGTH 定义见文件开头（一次性载体模型先用到它）
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
    # 完整引物序列（含 5′ 突出）：序列视图 SnapGene 式名牌框用；缺省无突出
    full_sequence: Optional[str] = Field(default=None, max_length=_map_limits.MAX_PRIMER_SEQ)
    # 退火区物理属性：渲染层直接读（悬停卡片展示），后端算出后下发
    tm: Optional[float] = Field(default=None, description="最近邻法 Tm（°C）")
    gc: Optional[float] = Field(default=None, ge=0, le=100, description="GC 含量（%）")


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
    segments: Optional[List[MapSegment]] = Field(default=None, max_length=_map_limits.MAX_SEGMENTS)
    color: Optional[str] = Field(default=None, max_length=32)


class SaveMapAsVectorRequest(BaseModel):
    """把图谱页当前打开的（可编辑）序列与注释存进载体库"""
    name: str = Field(..., min_length=1, max_length=80)
    sequence: str = Field(..., min_length=1, max_length=MAP_MAX_LENGTH)
    topology: MapTopology = "circular"
    features: List[MapSaveFeature] = Field(default_factory=list, max_length=MAP_MAX_FEATURES)
    source: str = Field(default="map", max_length=40,
                        description="出处标签，如 snapgene / ensembl / ncbi / upload / paste")
    url: str = Field(default="", max_length=500,
                     description="出处链接：只接受 http(s)://（空串表示无）")
    description: str = Field(default="", max_length=2000)

    @field_validator("url")
    @classmethod
    def _http_url_only(cls, v: str) -> str:
        """出处 URL 只收 http(s)：详情页把它渲染成链接，javascript: 等是存储型 XSS（审查 M5）"""
        v = (v or "").strip()
        if not v:
            return ""
        from core.vector_library import safe_http_url
        if not safe_http_url(v):
            raise ValueError("url 只接受 http:// 或 https:// 开头的链接")
        return v
