import axios from 'axios'
import type { DesignRequest, DesignResult, VectorInfo, CodonTable } from '@/types'

const API_BASE = '/api'

const api = axios.create({
  baseURL: API_BASE,
  headers: { 'Content-Type': 'application/json' }
})

// 请求拦截器：自动添加 token
api.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem('token')
    if (token) {
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  },
  (error) => Promise.reject(error)
)

// 响应拦截器：处理 401 错误
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('token')
      localStorage.removeItem('user')
    }
    return Promise.reject(error)
  }
)

// 设计任务
export async function submitDesign(request: DesignRequest): Promise<{ design_id: string; status: string }> {
  const response = await api.post('/design', request)
  return response.data
}

export async function getDesign(designId: string): Promise<DesignResult> {
  const response = await api.get(`/design/${designId}`)
  return response.data
}

export async function downloadGenbank(designId: string): Promise<void> {
  const response = await api.get(`/design/${designId}/download/genbank`, { responseType: 'blob' })
  const url = window.URL.createObjectURL(response.data)
  const link = document.createElement('a')
  link.href = url
  link.setAttribute('download', `${designId}.gb`)
  document.body.appendChild(link)
  link.click()
  link.remove()
}

export async function downloadPrimers(designId: string): Promise<void> {
  const response = await api.get(`/design/${designId}/download/primers`, { responseType: 'blob' })
  const url = window.URL.createObjectURL(response.data)
  const link = document.createElement('a')
  link.href = url
  link.setAttribute('download', `${designId}_primers.tsv`)
  document.body.appendChild(link)
  link.click()
  link.remove()
}

// 载体库
export async function getVectors(
  vectorType?: string,
  host?: string
): Promise<VectorInfo[]> {
  const params: Record<string, string> = {}
  if (vectorType) params.vector_type = vectorType
  if (host) params.host = host
  const response = await api.get('/vectors', { params })
  return response.data
}

export async function getVector(vectorId: string): Promise<VectorInfo> {
  const response = await api.get(`/vectors/${vectorId}`)
  return response.data
}

// 密码子表
export async function getCodonTables(): Promise<CodonTable[]> {
  const response = await api.get('/codon-tables')
  return response.data
}

// 载体图谱
export async function getVectorMapData(vectorId: string): Promise<any> {
  const response = await api.get(`/vectors/${vectorId}/map`)
  return response.data
}

// 设计图谱
export async function getDesignMapData(designId: string): Promise<any> {
  const response = await api.get(`/design/${designId}/map`)
  return response.data
}

// NCBI 搜索
export async function searchNcbi(query: string, limit: number = 10): Promise<any> {
  const response = await api.get('/vectors/search/ncbi', { params: { query, limit } })
  return response.data
}

// NCBI 导入
export async function importFromNcbi(query: string, limit: number = 5): Promise<any> {
  const response = await api.post('/vectors/import/ncbi', null, { params: { query, limit } })
  return response.data
}

// NCBI ID 直接导入
export async function importFromNcbiId(seqId: string): Promise<any> {
  const response = await api.post('/vectors/import/ncbi-id', null, { params: { seq_id: seqId } })
  return response.data
}

// NCBI 预览
export async function previewNcbi(seqId: string): Promise<any> {
  const response = await api.get(`/vectors/preview/ncbi/${seqId}`)
  return response.data
}

// 删除载体
export async function deleteVector(vectorId: string): Promise<any> {
  const response = await api.delete(`/vectors/${vectorId}`)
  return response.data
}

// 批量设计
export async function submitBatchDesign(request: any): Promise<any> {
  const response = await api.post('/design/batch', request)
  return response.data
}

export async function getBatchProgress(batchId: string): Promise<any> {
  const response = await api.get(`/design/batch/${batchId}`)
  return response.data
}

export async function getBatchReport(batchId: string): Promise<any> {
  const response = await api.get(`/design/batch/${batchId}/report`)
  return response.data
}

export async function downloadBatchResults(batchId: string): Promise<void> {
  const response = await api.get(`/design/batch/${batchId}/download`, { responseType: 'blob' })
  const url = window.URL.createObjectURL(response.data)
  const link = document.createElement('a')
  link.href = url
  link.setAttribute('download', `${batchId}_results.zip`)
  document.body.appendChild(link)
  link.click()
  link.remove()
}


// 载体序列下载
export async function getVectorSequence(vectorId: string, format: string = 'fasta'): Promise<string> {
  const response = await api.get(`/vectors/${vectorId}/sequence`, {
    params: { format },
    responseType: 'text'
  })
  return response.data
}

// 上传载体文件导入
export async function uploadVectorFile(file: File): Promise<any> {
  const formData = new FormData()
  formData.append('file', file)
  const response = await api.post('/vectors/import/upload', formData, {
    headers: { 'Content-Type': 'multipart/form-data' }
  })
  return response.data
}

// 更新载体信息
export async function updateVector(vectorId: string, data: {
  name?: string; description?: string; vector_type?: string;
  host?: string[]; antibiotic_resistance?: string[]; copy_number?: string
}): Promise<any> {
  const response = await api.put(`/vectors/${vectorId}`, data)
  return response.data
}

// 批量导入载体
export async function batchImportVectors(ncbiIds: string[], filePaths: string[] = []): Promise<any> {
  const response = await api.post('/vectors/import/batch', {
    ncbi_ids: ncbiIds,
    file_paths: filePaths
  })
  return response.data
}

// 获取缓存信息
export async function getCacheInfo(): Promise<any> {
  const response = await api.get('/cache/stats')
  return response.data
}

// 清除缓存
export async function clearCache(): Promise<any> {
  const response = await api.post('/cache/clear')
  return response.data
}

// 综合序列分析
export async function analyzeSequence(sequence: string, sequenceType: string = 'dna'): Promise<any> {
  const response = await api.post('/analysis/analyze', {
    sequence,
    sequence_type: sequenceType
  })
  return response.data
}

// 限制性位点分析
export async function findRestrictionSites(sequence: string, enzymes?: string[]): Promise<any> {
  const response = await api.post('/analysis/restriction-sites', {
    sequence,
    enzymes
  })
  return response.data
}

// ORF 预测
export async function predictORFs(sequence: string, minLength: number = 100): Promise<any> {
  const response = await api.post('/analysis/orfs', {
    sequence,
    min_length: minLength
  })
  return response.data
}

// 酶切消化模拟（线性完全消化，返回片段大小）
export async function simulateDigest(sequence: string, enzymes: string[]): Promise<any> {
  const response = await api.post('/analysis/digest', {
    sequence,
    enzymes
  })
  return response.data
}

// GC 分析
export async function analyzeGC(sequence: string, windowSize: number = 100): Promise<any> {
  const response = await api.post('/analysis/gc-analysis', {
    sequence,
    window_size: windowSize
  })
  return response.data
}

// 序列导出
export async function exportSequence(sequence: string, features: any[], format: string, name: string = 'sequence'): Promise<void> {
  const response = await api.post('/analysis/export', {
    sequence,
    features,
    format,
    name
  }, { responseType: 'blob' })
  const url = window.URL.createObjectURL(response.data)
  const link = document.createElement('a')
  link.href = url
  const extensions: Record<string, string> = {
    genbank: 'gb', fasta: 'fasta', snapgene: 'dna', benchling: 'json', sbol: 'json'
  }
  link.setAttribute('download', `${name}.${extensions[format] || format}`)
  document.body.appendChild(link)
  link.click()
  link.remove()
}

// 酶信息（对应后端 GET /analysis/enzymes 返回结构）
export interface EnzymeInfo {
  recognition_sequence: string
  cut_type: string
  is_type_iis: boolean
}

// 获取可用酶列表
export async function getEnzymes(): Promise<{ total: number; enzymes: Record<string, EnzymeInfo> }> {
  const response = await api.get('/analysis/enzymes')
  return response.data
}
// 用户认证
export async function login(email: string, password: string): Promise<any> {
  const response = await api.post('/auth/login', { email, password })
  return response.data
}

export async function register(email: string, username: string, password: string, confirmPassword: string): Promise<any> {
  const response = await api.post('/auth/register', {
    email,
    username,
    password,
    confirm_password: confirmPassword
  })
  return response.data
}

export async function getCurrentUser(): Promise<any> {
  const token = localStorage.getItem('token')
  if (!token) return null
  try {
    const response = await api.get('/auth/me')
    return response.data
  } catch {
    return null
  }
}

export async function verifyToken(): Promise<any> {
  const response = await api.get('/auth/verify')
  return response.data
}

// 用户登出
export async function logout(): Promise<any> {
  const response = await api.post('/auth/logout')
  return response.data
}

// 获取导出格式列表
export async function getExportFormats(): Promise<any[]> {
  const response = await api.get('/analysis/export/formats')
  return response.data
}

// 导出所有格式为 ZIP
export async function exportAllFormats(
  sequence: string,
  features: any[],
  name: string,
  description: string = '',
  isCircular: boolean = true
): Promise<void> {
  const response = await api.post('/analysis/export/all', {
    sequence,
    features,
    name,
    description,
    is_circular: isCircular
  }, { responseType: 'blob' })
  const url = window.URL.createObjectURL(response.data)
  const link = document.createElement('a')
  link.href = url
  link.setAttribute('download', `${name}_exports.zip`)
  document.body.appendChild(link)
  link.click()
  link.remove()
}

// 克隆兼容性检查（JSON body，与后端 CompatibilityRequest 对齐）
export async function checkCompatibility(
  insertSequence: string,
  vectorSequence: string,
  enzymes: string[]
): Promise<any> {
  const response = await api.post('/analysis/compatibility', {
    insert_sequence: insertSequence,
    vector_sequence: vectorSequence,
    enzymes
  })
  return response.data
}

// 缓存 — 设计缓存失效
export async function invalidateDesignCache(designId: string): Promise<any> {
  const response = await api.post(`/cache/invalidate/design/${designId}`)
  return response.data
}

// 缓存 — 载体缓存失效
export async function invalidateVectorCache(vectorId: string): Promise<any> {
  const response = await api.post(`/cache/invalidate/vector/${vectorId}`)
  return response.data
}

// 缓存 — 健康检查
export async function getCacheHealth(): Promise<any> {
  const response = await api.get('/cache/health')
  return response.data
}

// 速率限制 — 状态
export async function getRateLimitStatus(): Promise<any> {
  const response = await api.get('/rate-limit/status')
  return response.data
}

// 速率限制 — 配置
export async function getRateLimitConfig(): Promise<any> {
  const response = await api.get('/rate-limit/config')
  return response.data
}

// ==================== Sanger 测序分析 ====================

/** 逐列对齐视图：read 与参考的原始比对证据（供人工核对）
 *  ref_aligned/read_aligned 等长，'-' 为该列缺失（插入/缺失）；read 以参考方向展示 */
export interface AlignmentView {
  ref_start: number
  ref_aligned: string
  read_aligned: string
  q_aligned: number[]
}

/** 共识序列与参考的差异位；cons_base '-' 表示缺失，ref_base '-' 表示插入 */
export interface ConsensusDiff {
  ref_pos: number
  ref_base: string
  cons_base: string
  cons_index?: number
}

/** CDS 级别测序结论：覆盖完整性 + 共识重建翻译产物与参考比对 */
export interface CdsReport {
  name: string
  start: number
  end: number
  strand: string
  covered_percent: number
  coverage_status: 'full' | 'partial' | 'uncovered'
  ref_protein_length: number | null
  alt_protein_length: number | null
  protein_identical: boolean | null
  premature_stop_aa: number | null
  frameshift_count: number
  aa_changes: string[]
  /** Sequence Ontology 标准后果词表（与 VEP/snpEff/bcftools csq 对齐） */
  consequences?: string[]
  synonymous_count?: number
  /** 低置信（疑似测序噪声）未计入判定的变体数 */
  pending_low_confidence?: number
  verdict: string
}

export interface SequencingVariant {
  ref_pos: number
  read_pos?: number
  type: string
  ref_base: string
  alt_base: string
  length: number
  quality?: number
  read_q?: number
  support_reads?: number
  read?: string
  features?: { name: string; type: string }[]
  codon_change?: string | null
  aa_change?: string | null
  frameshift?: boolean
  enzyme_sites_lost?: string[]
  enzyme_sites_gained?: string[]
  confidence?: string
  /** 独立 basecaller（tracy）重 basecall 后报出同一变体：跨 caller 印证 */
  corroborated_by_basecall?: boolean
  /** 峰级证据：替换为突变峰占比/信噪比，插入为插入峰强度比（相对邻峰） */
  peak_evidence?: {
    mutant_pct?: number | null
    snr?: number | null
    insertion_peak_ratio?: number | null
  }
}

export interface SequencingAnalysis {
  analysis_id: string
  sample_name: string
  created_at: string
  engine: string
  conclusion: string
  reads: {
    index: number
    filename: string
    sample_name?: string
    raw_length: number
    trimmed_length: number
    mean_q: number
    direction: string
    ref_start: number
    ref_end: number
    identity: number
    mixed_positions: number[]
    alignment_view?: AlignmentView | null
    grade?: string
    q20_ratio?: number
  }[]
  variants: SequencingVariant[]
  consensus: {
    sequence: string
    covered_ranges: [number, number][]
    coverage_percent: number
    diffs?: ConsensusDiff[]
  }
  coverage_ranges: [number, number][]
  coverage_gaps?: { start: number; end: number; length: number }[]
  cds_reports?: CdsReport[]
  mixed_detected: Record<string, number[]>
  decomposed_alleles?: Record<string, { sequence: string; source: string }[]>
  errors: { filename: string; error: string }[]
  reference_length: number
  features: { name: string; type: string; start: number; end: number; strand: string; description?: string }[]
}

export interface ReadTrace {
  filename: string
  bases: string
  quality: number[]
  channels: Record<'A' | 'T' | 'G' | 'C', number[]>
  peak_indices: number[]
}

/** 通用测序分析：参考序列文件（.gb/.fasta/.dna）+ .ab1 测序文件一起上传 */
export async function analyzeSequencingFiles(
  reference: File,
  reads: File[],
  minQ = 20,
  allowDecompose = true
): Promise<SequencingAnalysis> {
  const form = new FormData()
  form.append('reference', reference, reference.name)
  reads.forEach((f) => form.append('reads', f))
  form.append('min_q', String(minQ))
  form.append('allow_decompose', String(allowDecompose))
  const response = await api.post('/sequencing/analyze', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 120000
  })
  return response.data
}

/** 取设计结果的 GenBank 文件（测序页深链自动带入参考序列用） */
export async function fetchDesignGenbankFile(designId: string): Promise<File> {
  const response = await api.get(`/design/${designId}/download/genbank`, { responseType: 'blob' })
  return new File([response.data], `${designId}.gb`, { type: 'application/octet-stream' })
}

/** 取载体库载体的 GenBank 文件（测序页深链自动带入参考序列用） */
export async function fetchVectorGenbankFile(vectorId: string): Promise<File> {
  const response = await api.get(`/vectors/${vectorId}/sequence`, {
    params: { format: 'genbank' },
    responseType: 'blob'
  })
  return new File([response.data], `${vectorId}.gb`, { type: 'application/octet-stream' })
}

export async function getReadTrace(analysisId: string, readIndex: number): Promise<ReadTrace> {
  const response = await api.get(`/sequencing/analyses/${analysisId}/trace/${readIndex}`)
  return response.data
}

export async function exportConsensus(analysisId: string, format: string): Promise<string> {
  const response = await api.get(`/sequencing/analyses/${analysisId}/consensus/export?format=${format}`, {
    responseType: 'text',
    transformResponse: [(data) => data]
  })
  return response.data
}

export interface SequencingAnalysisSummary {
  analysis_id: string
  sample_name: string
  created_at: string
  engine: string
  conclusion: string
  read_count: number
  variant_count: number
  coverage_percent: number
  reference_length: number
}

export async function listSequencingAnalyses(): Promise<SequencingAnalysisSummary[]> {
  const response = await api.get('/sequencing/analyses')
  return response.data
}

export async function getSequencingAnalysis(analysisId: string): Promise<SequencingAnalysis> {
  const response = await api.get(`/sequencing/analyses/${analysisId}`)
  return response.data
}

export async function deleteSequencingAnalysis(analysisId: string): Promise<void> {
  await api.delete(`/sequencing/analyses/${analysisId}`)
}
// ==================== 批量测序分析（独立入口，与单样品 /sequencing/analyze 平级） ====================

export interface SequencingBatchItem {
  plasmid: string
  /** analyzed=已分析 / no_reads=只有图谱 / no_reference=缺图谱 / failed=分析失败 / not_found=无文件 */
  status: 'analyzed' | 'no_reads' | 'no_reference' | 'failed' | 'not_found'
  conclusion: string
  analysis_id?: string | null
  reference_name?: string | null
  reference_length?: number | null
  read_count: number
  /** 确证差异数（低置信噪声不计入） */
  variant_count: number
  /** 低置信（疑似测序噪声）变异数 */
  pending_count: number
  coverage_percent?: number | null
}

export interface SequencingBatchResult {
  batch_id: string
  created_at: string
  min_q: number
  /** true=按信息表引物列/质粒名归组；false=按图谱文件名包含关系归组 */
  excel_mode: boolean
  items: SequencingBatchItem[]
  unmatched: { filename: string; reason: string }[]
  ignored_files: string[]
}

/** 批量测序分析：整个交付文件夹（.ab1 + 图谱，可多质粒）+ 可选 Excel 信息表一次上传 */
export async function analyzeSequencingBatch(
  files: File[],
  excel: File | null,
  minQ = 20
): Promise<SequencingBatchResult> {
  const form = new FormData()
  files.forEach((f) => form.append('files', f))
  if (excel) form.append('excel', excel, excel.name)
  form.append('min_q', String(minQ))
  const response = await api.post('/sequencing/analyze-batch', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 600000
  })
  return response.data
}
