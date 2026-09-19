import axios from 'axios'
import type { DesignRequest, DesignResult, VectorInfo, CodonTable } from '@/types'

const API_BASE = '/api'

// 注意：这里不能硬编码 Content-Type。
// axios 1.x 的 transformRequest 见到「Content-Type 含 application/json」且 data 是
// FormData 时，会把 FormData 转成 JSON 字符串（File 全变成 {}）发出去，
// 后端 multipart 接口就会收到空表单并报 "Field required"。
// 交给 axios 自己判断：普通对象发 application/json，FormData 发 multipart（含 boundary）。
const api = axios.create({
  baseURL: API_BASE
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
  async (error) => {
    if (error.response?.status === 401 && !error.config?.url?.includes('/auth/login')) {
      // 登录接口本身的 401 是密码错误，不应清除本地会话状态
      localStorage.removeItem('token')
      localStorage.removeItem('user')
      // 同步清理 Pinia 状态，避免界面仍显示已登录（动态导入避免与 auth store 循环依赖）
      try {
        const { useAuthStore } = await import('@/stores/auth')
        useAuthStore().clearAuth()
      } catch {
        // Pinia 未初始化（如单测环境）时仅清理 localStorage 即可
      }
    }
    return Promise.reject(error)
  }
)

/** 把错误响应转成可读文本（axios 错误 / 后端 detail 都能吃）。
 *
 *  FastAPI 的 422 校验错误 detail 是数组，每条只有 "Field required" 这类 msg，
 *  必须带上 loc 里的字段名，否则界面上只会出现「Field required；Field required」
 *  这种看不出缺了什么的文案。
 */
export function formatApiError(e: any, fallback = '请求失败'): string {
  const detail = e?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail
      .map((d: any) => {
        const msg = d?.msg || JSON.stringify(d)
        const field = Array.isArray(d?.loc)
          ? d.loc.filter((x: any) => x !== 'body' && x !== 'query' && x !== 'path').join('.')
          : ''
        return field ? `${field}: ${msg}` : msg
      })
      .join('；')
  }
  // 浏览器 XHR 在发送阶段失败（文件被其他程序占用、连接被掐断）时，axios 只给
  // 一句笼统的 "Network Error"，翻译成可行动的提示
  const msg = typeof e?.message === 'string' ? e.message : ''
  if (/network error|failed to fetch|load failed/i.test(msg)) {
    return '网络异常：请求未能送达服务器（可能是文件被其他程序占用、网络中断或超出传输大小限制），请检查后重试'
  }
  return msg || fallback
}

/** blob 下载公共路径：错误响应(JSON)直接抛出，成功则触发保存并释放 URL */
async function saveBlobResponse(response: { data: any }, filename: string): Promise<void> {
  const data = response.data as Blob
  if (data.type && data.type.includes('application/json')) {
    // responseType: 'blob' 时后端的 4xx/5xx JSON 错误也以 Blob 返回，不能存成下载文件
    const text = await data.text()
    let detail = text
    try { detail = JSON.parse(text)?.detail ?? text } catch { /* 保留原文 */ }
    throw new Error(typeof detail === 'string' ? detail : '下载失败')
  }
  const url = window.URL.createObjectURL(data)
  const link = document.createElement('a')
  link.href = url
  link.setAttribute('download', filename)
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.URL.revokeObjectURL(url)
}

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
  await saveBlobResponse(response, `${designId}.gb`)
}

export async function downloadPrimers(designId: string): Promise<void> {
  const response = await api.get(`/design/${designId}/download/primers`, { responseType: 'blob' })
  await saveBlobResponse(response, `${designId}_primers.tsv`)
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
  await saveBlobResponse(response, `${batchId}_results.zip`)
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
  const response = await api.post('/vectors/import/upload', formData)
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
  const extensions: Record<string, string> = {
    genbank: 'gb', fasta: 'fasta', snapgene: 'dna', benchling: 'json', sbol: 'json'
  }
  await saveBlobResponse(response, `${name}.${extensions[format] || format}`)
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

// 公开站点配置（注册开关/邮箱验证开关/各级别可用功能）
export interface SiteConfig {
  registration_open: boolean
  email_verification_required: boolean
  tier: 'anonymous' | 'user' | 'admin'
  features: { anonymous: string[]; user: string[] }
  effective_features: string[]
}

export async function getSiteConfig(): Promise<SiteConfig> {
  const response = await api.get('/auth/site-config')
  return response.data
}

// 提交注册邮箱验证码（成功返回登录令牌）
export async function verifyEmail(verifyToken: string, code: string): Promise<any> {
  const response = await api.post('/auth/verify-email', { verify_token: verifyToken, code })
  return response.data
}

// 重发注册验证码（60 秒冷却）
export async function resendVerification(verifyToken: string): Promise<any> {
  const response = await api.post('/auth/resend-verification', { verify_token: verifyToken })
  return response.data
}

// ==================== 管理员 ====================

export interface AdminSettings {
  registration_open: boolean
  email_verification_required: boolean
  anonymous_features: string[]
  user_features: string[]
  feature_keys: string[]
  feature_labels: Record<string, string>
  mail_provider: string
  mail_from: string
}

export interface AdminUserInfo {
  id: string
  email: string
  username: string
  is_admin: boolean
  is_active: boolean
  email_verified: boolean
  allowed_features: string[] | null
  created_at: string | null
}

export async function getAdminSettings(): Promise<AdminSettings> {
  const response = await api.get('/admin/settings')
  return response.data
}

export async function updateAdminSettings(patch: Partial<AdminSettings>): Promise<AdminSettings> {
  const response = await api.put('/admin/settings', patch)
  return response.data
}

export async function listAdminUsers(): Promise<AdminUserInfo[]> {
  const response = await api.get('/admin/users')
  return response.data
}

export async function updateAdminUser(
  userId: string,
  patch: { is_admin?: boolean; is_active?: boolean; email_verified?: boolean; allowed_features?: string[] }
): Promise<AdminUserInfo> {
  const response = await api.put(`/admin/users/${userId}`, patch)
  return response.data
}

export async function deleteAdminUser(userId: string): Promise<void> {
  await api.delete(`/admin/users/${userId}`)
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
  await saveBlobResponse(response, `${name}_exports.zip`)
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
    mixed_detail?: { pos: number; ratio: number; secondary_base: string }[]
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
  homopolymers?: {
    base: string | null
    unit?: string | null
    period?: number
    start: number
    end: number
    length?: number
    tier?: string
    ref_repeat_count: number
    observed_repeat_count: number
    count_reliable: boolean
    peak_count_estimate?: number | null
    peak_missing?: number | null
    peak_inserted?: number | null
    peak_measured?: number | null
    run_covered?: number | null
    length_estimate?: number | null
    length_method?: string | null
    length_ci?: [number, number] | null
    read_counts?: {
      filename: string; direction: string; peak_count: number | null
      coverage?: 'full' | 'partial'
      covered_span?: [number, number] | null
      called_count?: number | null
    }[]
    variant: { ref_pos: number; type: string; length: number; confidence: string } | null
  }[]
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
  /** 峰图采样窗口换算用：修剪偏移（peak_indices 按原始 read 碱基索引） */
  trim_start?: number
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
  const response = await api.post('/sequencing/analyze', form, { timeout: 120000 })
  return response.data
}

/** 取设计结果的 GenBank 文件（测序页深链自动带入参考序列用） */
export async function fetchDesignGenbankFile(designId: string): Promise<File> {
  const response = await api.get(`/design/${designId}/download/genbank`, { responseType: 'blob' })
  assertBlobIsGenbank(response.data, designId)
  return new File([response.data], `${designId}.gb`, { type: 'application/octet-stream' })
}

/** 深链取参考序列时校验响应确实是 GenBank 文本——401/404 时错误 JSON 会被包成
 *  Blob 传给后端解析，用户只看到莫名的解析报错而看不到根因 */
function assertBlobIsGenbank(data: Blob, name: string): void {
  if (data.type && data.type.includes('application/json')) {
    throw new Error(`无法获取 ${name} 的参考序列（请确认其仍然存在）`)
  }
}

/** 取载体库载体的 GenBank 文件（测序页深链自动带入参考序列用） */
export async function fetchVectorGenbankFile(vectorId: string): Promise<File> {
  const response = await api.get(`/vectors/${vectorId}/sequence`, {
    params: { format: 'genbank' },
    responseType: 'blob'
  })
  assertBlobIsGenbank(response.data, vectorId)
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
  /** 克隆号（信息表带「克隆号」列时有值；每个克隆独立分析） */
  clone?: string | null
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
  /** true=信息表带克隆号列，按克隆分组（一个质粒多个克隆独立分析） */
  clone_mode?: boolean
  items: SequencingBatchItem[]
  unmatched: { filename: string; reason: string }[]
  ignored_files: string[]
  /** true=整理包已生成，可经 downloadBatchSequencingReport 下载（15 分钟有效） */
  report_ready?: boolean
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
  const response = await api.post('/sequencing/analyze-batch', form, { timeout: 600000 })
  return response.data
}

/** 下载批量分析的整理包：按质粒/克隆归档的原始文件副本 + 各组分析报告 +
 *  整理清单 + 结论回填的信息表（离线脚本产物的网页版；生成 15 分钟后过期） */
export async function downloadBatchSequencingReport(batchId: string): Promise<void> {
  const response = await api.get(`/sequencing/batches/${batchId}/report`, {
    responseType: 'blob',
    timeout: 300000
  })
  await saveBlobResponse(response, `测序整理_${batchId.slice(-6)}.zip`)
}
