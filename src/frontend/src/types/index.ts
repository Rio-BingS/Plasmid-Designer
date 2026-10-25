// TypeScript 类型定义

export type SequenceType = 'amino_acid' | 'dna'
export type CloningMethod = 'gibson' | 'golden_gate' | 'restriction' | 'gene_synthesis'
// 插入片段来源（与克隆方法正交）：pcr=设计 PCR 扩增引物；gene_synthesis=设计重叠合成 oligo
export type InsertSource = 'pcr' | 'gene_synthesis'
export type DesignStatus = 'pending' | 'running' | 'completed' | 'failed'

/** 一次性载体的元件（与载体 YAML / 图谱契约同形，strand 允许 1/-1/0） */
export interface DesignVectorFeature {
  name: string
  type: string
  start: number
  end: number
  strand: '+' | '-' | 1 | -1 | 0
  description?: string
}

/** 载体 MCS 判定结果：feature=文件注释给出，inferred=后端按唯一位点推测 */
export interface DesignVectorMcs {
  start: number
  end: number
  origin: 'feature' | 'inferred'
  enzymes: string[]
}

/**
 * 一次性载体：设计页本地上传 / 在线导入的载体（不写共享载体库）
 * 后端 /api/design/vector/* 返回它，POST /api/design 的 vector 字段再回传
 */
export interface DesignVectorPayload {
  id: string
  name: string
  source: string
  url?: string
  license?: string
  sequence: string
  topology: 'circular' | 'linear'
  length: number
  features: DesignVectorFeature[]
  mcs?: DesignVectorMcs | null
  unique_enzymes: string[]
  warnings: string[]
}

/** 提交设计时随请求内联的一次性载体（只要设计需要的字段） */
export interface DesignVectorInput {
  id?: string
  name: string
  sequence: string
  topology: 'circular' | 'linear'
  features: DesignVectorFeature[]
  source?: string
}

export interface DesignRequest {
  sequence: string
  sequence_type: SequenceType
  sequence_name: string
  vector_id: string
  /** 一次性载体：给了就忽略载体库（后端不落盘），为空时按 vector_id 查库 */
  vector?: DesignVectorInput
  cloning_method: CloningMethod
  insert_source?: InsertSource
  optimize_codons: boolean
  target_species: string
  gc_min: number
  gc_max: number
  homology_arm: number
  enzyme: string
  /** 双酶切：restriction 方法 5'/3' 端限制酶 */
  enzyme_5?: string
  enzyme_3?: string
  oligo_length_min?: number
  oligo_length_max?: number
  overlap_length?: number
  include_report: boolean
  protocol_language?: 'zh' | 'en'
  /** Gibson：定位同源重组位置的酶切位点名称 */
  gibson_site?: string
  /** 基因合成：需从优化序列排除的限制酶 */
  exclude_enzymes?: string[]
  /** 双酶切插入方向：默认 auto（按载体启动子/RBS 判定） */
  insert_direction?: InsertDirection
  /** NdeI/NcoI 等位点自带起始密码子时自动去除插入序列开头的冗余 ATG（默认 true） */
  trim_redundant_start?: boolean
  /** Gibson 载体线性化方式：双酶切（默认）或单位点 */
  gibson_linearization?: 'double_digest' | 'single_site'
  /** Gibson 双酶切线性化时保留连接处的酶切位点（默认 true；关闭 = 无缝连接） */
  keep_restriction_sites?: boolean
  /** 质粒名加载体名前缀（载体名-序列名） */
  vector_name_prefix?: boolean
}

/**
 * 双酶切实时预览：后端用占位基因跑一遍真实组装，返回**成品构建体**的布局。
 * 坐标一律是构建体上的 1-based 闭区间（被切走的载体片段不在其中）。
 */
export interface RestrictionPreviewSite {
  role: 'five' | 'three'
  enzyme: string
  position: number
  cut: number
  end: number
}

export interface RestrictionPreviewFeature {
  name: string
  type: string
  start: number
  end: number
  strand: '+' | '-'
}

export interface RestrictionPreviewEvidence {
  kind: 'promoter' | 'rbs' | 'terminator'
  side: '+' | '-'
  name: string
  gap?: number
  start?: number
  end?: number
  strand?: string
}

export interface RestrictionPreview {
  vector: { name: string; length: number; topology: 'circular' | 'linear' }
  sites: RestrictionPreviewSite[]
  gene: { start: number; end: number; length: number; estimated: boolean }
  removed_bp: number
  construct_length: number
  strand: '+' | '-'
  source: 'annotation' | 'auto_detected' | 'ambiguous' | 'default' | 'user'
  ambiguous: boolean
  gene_5prime_enzyme: string
  gene_3prime_enzyme: string
  evidence: RestrictionPreviewEvidence[]
  features: RestrictionPreviewFeature[]
  junctions: { five: string; three: string }
  warnings: string[]
}

/** 插入方向：auto=按载体表达盒；reverse=反义构建；plus/minus=用户手动指定 */
export type InsertDirection = 'auto' | 'reverse' | 'plus' | 'minus'


export interface PrimerInfo {
  name: string
  sequence: string
  full_sequence: string
  tm: number
  gc_content: number
  length: number
  overhang?: string
  notes?: string
}

export interface DesignResult {
  /** 成品质粒名：序列名或「载体名-序列名」 */
  plasmid_name?: string
  design_id: string
  status: DesignStatus
  input_sequence: string
  optimized_sequence?: string
  construct_sequence?: string
  construct_features?: { name: string; type: string; start: number; end: number; strand?: string; description?: string }[]
  insert_start?: number
  insert_end?: number
  cai?: number
  gc_content?: number
  vector_id: string
  vector_name: string
  final_length?: number
  primers: PrimerInfo[]
  cloning_method: CloningMethod
  clone_protocol?: string
  validation_passed: boolean
  warnings: string[]
  errors: string[]
  created_at: string
  completed_at?: string
}

export interface VectorInfo {
  id: string
  name: string
  source: string
  vector_type: string
  host: string[]
  antibiotic_resistance: string[]
  copy_number: string
  description: string
  features: { name: string; type: string }[]
  mcs_enzymes: string[]
  /** 拓扑：导入的线性片段不会显示成环状质粒 */
  topology?: 'circular' | 'linear'
  /** 轻量图谱预览（后端可省略；缺省时列表不画缩略图） */
  map_preview?: import('@/map/types').MapPreview | null
  /** 结构化出处（YAML 的 data_provenance）：来源站/URL/许可/抓取时间 */
  data_provenance?: Record<string, string>
}

export interface CodonTable {
  id: string
  name: string
  file: string
  species?: string
}

// ==================== 认证 / 用户（对齐 app/auth/jwt_auth.py 与 auth/routes.py） ====================

export interface UserInfo {
  id: string
  email: string
  username: string
  is_active?: boolean
  is_admin?: boolean
  email_verified?: boolean
  /** 个人功能权限覆盖（null = 跟随层级默认） */
  allowed_features?: string[] | null
  batch_max_reads_per_group?: number | null
  batch_max_total_reads?: number | null
}

/** 登录 / 注册 / 邮箱验证的令牌响应（RegisterResponse 与 Token 同形） */
export interface AuthTokenResponse {
  access_token: string | null
  token_type: string
  expires_in: number
  user: UserInfo | null
  requires_verification?: boolean
  verify_token?: string | null
  email?: string | null
  mail_sent?: boolean
}

// ==================== 批量设计（对齐 BatchDesignRequest / BatchProgressResponse） ====================

export type BatchDesignRequestPayload = Partial<Omit<DesignRequest, 'sequence' | 'sequence_name'>> & {
  sequences: string[]
  sequence_names?: string[] | null
}

/** POST /vectors/import/ncbi（按关键词导入共享载体库） */
export interface NcbiImportResult {
  imported: number
  query: string
}

/** POST /vectors/import/ncbi-id（按 ID 导入单条） */
export interface NcbiIdImportResult {
  success: boolean
  seq_id?: string
  error?: string
}

/** GET /auth/verify（令牌复查：无效不 401，返回 valid=false） */
export interface VerifyTokenResponse {
  valid: boolean
  user: UserInfo | null
}

export interface BatchDesignError {
  index?: number
  sequence_name?: string
  error: string
}

/** POST /api/batch/design 的响应（BatchDesignStatus） */
export interface BatchStatus {
  batch_id: string
  total: number
  completed: number
  failed: number
  status: string
  results: string[]
  errors: BatchDesignError[]
}

/** GET /api/batch/design/{batch_id}/progress 的响应（BatchProgressResponse） */
export interface BatchProgress {
  batch_id: string
  total: number
  completed: number
  failed: number
  pending: number
  status: string
  progress_percent: number
  results: Record<string, unknown>[]
  errors: BatchDesignError[]
}

// ==================== 载体导入 / 搜索（对齐 routes/vector_routes.py） ====================

export interface NcbiSearchResult {
  query: string
  count: number
  results: { id: string; url: string }[]
}

export interface VectorPreview {
  id: string
  name: string
  source: string
  length: number
  description: string
  gc_content: number
  features_count: number
  warnings: string[]
}

export interface VectorUploadResult {
  success: boolean
  vector: { id: string; name: string; length: number; features: DesignVectorFeature[] }
  replaced?: boolean
  warnings: string[]
}

export interface BatchImportResult {
  ncbi: string[]
  files: string[]
  errors: string[]
}

export interface DeleteVectorResult {
  success: boolean
  message?: string
}

// ==================== 缓存（对齐 app/cache_routes.py） ====================

export interface CacheInfo {
  backend?: string
  [key: string]: unknown
}

export interface CacheClearResult {
  cleared?: number
  pattern?: string
  [key: string]: unknown
}
