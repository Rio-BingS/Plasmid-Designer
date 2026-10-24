import axios from 'axios'
import { currentAuthEpoch, notifySessionExpired } from './session'
import type {
  AuthTokenResponse, BatchDesignRequestPayload, BatchImportResult, BatchProgress,
  BatchStatus, CacheClearResult, CacheInfo, DeleteVectorResult, DesignRequest,
  DesignResult, DesignVectorInput, DesignVectorPayload, InsertDirection,
  NcbiIdImportResult, NcbiImportResult, NcbiSearchResult, RestrictionPreview,
  UserInfo, VectorInfo, VectorPreview, VerifyTokenResponse,
  VectorUploadResult, CodonTable
} from '@/types'
import { runChunkedBatch, type ChunkedOptions, type ChunkedTransport } from '@/utils/chunkedUpload'

const API_BASE = '/api'

// 注意：这里不能硬编码 Content-Type。
// axios 1.x 的 transformRequest 见到「Content-Type 含 application/json」且 data 是
// FormData 时，会把 FormData 转成 JSON 字符串（File 全变成 {}）发出去，
// 后端 multipart 接口就会收到空表单并报 "Field required"。
// 交给 axios 自己判断：普通对象发 application/json，FormData 发 multipart（含 boundary）。
//
// 登录态在后端下发的 httpOnly 会话 Cookie 里（前端不保存令牌）：
// withCredentials 让跨源部署（CORS_ORIGINS 显式来源）也带上 Cookie；
// X-Requested-With 是后端 CSRF 校验要求的自定义头——Cookie 会话的
// POST/PUT/PATCH/DELETE 缺它会被 403 拒绝。
const api = axios.create({
  baseURL: API_BASE,
  withCredentials: true,
  headers: { 'X-Requested-With': 'XMLHttpRequest' }
})

/** 共享 axios 实例：供按功能拆分的接口模块（如 map/api.ts）复用拦截器与凭证 */
export { api as http }

// 请求拦截器：记下请求发出时的会话代际（见 api/session.ts）
api.interceptors.request.use(
  (config) => {
    ;(config as any).authEpoch = currentAuthEpoch()
    return config
  },
  (error) => Promise.reject(error)
)

/**
 * 这些端点的 401 表示「凭据/验证码无效」或「匿名令牌失效」，不是会话失效。
 * 用显式清单而不是散落的字符串判断：新增业务端点若用 401 表达业务错误，
 * 往这里加一条即可，不会静默踢掉用户的登录态。
 */
const SESSION_401_EXEMPT_PATHS = [
  '/auth/login',
  '/auth/register',
  '/auth/verify',
  '/auth/verify-email',
  '/auth/resend-verification',
]

/** 该请求的 401 是否应清掉本地登录态 */
export function shouldClearSessionOn401(config: any): boolean {
  if (!config) return true
  const url = String(config.url || '')
  if (SESSION_401_EXEMPT_PATHS.some((p) => url.includes(p))) return false
  // 带匿名测序访问令牌的请求：401 属于那个令牌，与会话无关
  const headers = config.headers
  const token = typeof headers?.get === 'function'
    ? headers.get('X-Access-Token')
    : headers?.['X-Access-Token'] ?? headers?.['x-access-token']
  return !token
}

// 响应拦截器：处理 401 错误
api.interceptors.response.use(
  (response) => response,
  async (error) => {
    if (error.response?.status === 401 && shouldClearSessionOn401(error.config)) {
      // 只清理「发出该请求时」的会话：旧会话发出的请求晚到的 401
      // 不能把期间重新登录建立的新会话一并清掉
      const sentEpoch = error.config?.authEpoch
      if (sentEpoch === currentAuthEpoch()) {
        // 同步清理 Pinia 状态，避免界面仍显示已登录（经 session 模块回调，
        // 避免与 auth store 循环依赖）
        notifySessionExpired()
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
/**
 * Cloudflare 网关错误（520–527，或 502/504 时返回的 Cloudflare 英文说明）翻译成中文。
 * 这类错误不是后端给的业务提示，原文对用户没有可行动的信息。返回空串表示不是网关错误。
 */
export function gatewayErrorText(e: any): string {
  const status = Number(e?.response?.status) || 0
  const data = e?.response?.data
  const raw = typeof data === 'string' ? data : `${data?.title ?? ''} ${data?.detail ?? ''}`
  const fromCf = /cloudflare/i.test(raw)
  if (!(status >= 520 && status <= 527) && !(fromCf && status >= 500)) return ''
  if (status === 524) return '服务器处理超时（Cloudflare 524：超过 100 秒未返回），请稍后重试'
  return `网关错误（HTTP ${status}）：服务器暂时没有返回有效响应，可能正在重启或过载，请稍后重试；持续出现请查看后端日志`
}

export function formatApiError(e: any, fallback = '请求失败'): string {
  const gw = gatewayErrorText(e)
  if (gw) return gw
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
  // 后端没给 detail 时的兜底文案：axios 原文是英文的
  // "Request failed with status code 429"，对用户没有任何可行动信息
  const status = Number(e?.response?.status) || 0
  if (status === 401) return '登录状态已失效，请重新登录'
  if (status === 403) return '没有权限执行该操作：可能需要管理员身份，或登录状态已变化'
  if (status === 429) {
    const retry = Number(e?.response?.headers?.['retry-after'])
    return retry > 0
      ? `请求过于频繁，请 ${Math.ceil(retry)} 秒后重试`
      : '请求过于频繁，请稍后重试'
  }
  if (status >= 500) {
    return `服务器错误（HTTP ${status}）：请稍后重试；持续出现请查看后端日志`
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

/**
 * 双酶切实时预览：选好两个酶就把成品构建体的布局算出来（不提交任务、不落库）。
 * 方向由后端按载体表达盒判定，front 只负责把用户的选择传过去。
 */
export async function getRestrictionPreview(payload: {
  enzymes: string[]
  vector_id?: string
  vector?: DesignVectorInput
  insert_length?: number
  direction?: InsertDirection
}): Promise<RestrictionPreview> {
  const response = await api.post('/design/restriction-preview', payload)
  return response.data
}

export async function getDesign(designId: string): Promise<DesignResult> {
  const response = await api.get(`/design/${encodeURIComponent(designId)}`)
  return response.data
}

export async function downloadGenbank(designId: string): Promise<void> {
  const response = await api.get(`/design/${encodeURIComponent(designId)}/download/genbank`, { responseType: 'blob' })
  await saveBlobResponse(response, `${designId}.gb`)
}

export async function downloadPrimers(designId: string): Promise<void> {
  const response = await api.get(`/design/${encodeURIComponent(designId)}/download/primers`, { responseType: 'blob' })
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
  const response = await api.get(`/vectors/${encodeURIComponent(vectorId)}`)
  return response.data
}

// 密码子表
export async function getCodonTables(): Promise<CodonTable[]> {
  const response = await api.get('/codon-tables')
  return response.data
}

// 载体/设计图谱（MapDocument）见 map/api.ts 的 getVectorMap / getDesignMap

// NCBI 搜索
export async function searchNcbi(query: string, limit: number = 10): Promise<NcbiSearchResult> {
  const response = await api.get('/vectors/search/ncbi', { params: { query, limit } })
  return response.data
}

// NCBI 导入
export async function importFromNcbi(query: string, limit: number = 5): Promise<NcbiImportResult> {
  const response = await api.post('/vectors/import/ncbi', null, { params: { query, limit } })
  return response.data
}

// NCBI ID 直接导入
export async function importFromNcbiId(seqId: string): Promise<NcbiIdImportResult> {
  const response = await api.post('/vectors/import/ncbi-id', null, { params: { seq_id: seqId } })
  return response.data
}

// NCBI 预览
export async function previewNcbi(seqId: string): Promise<VectorPreview> {
  const response = await api.get(`/vectors/preview/ncbi/${encodeURIComponent(seqId)}`)
  return response.data
}

// 删除载体
export async function deleteVector(vectorId: string): Promise<DeleteVectorResult> {
  const response = await api.delete(`/vectors/${encodeURIComponent(vectorId)}`)
  return response.data
}

// 批量设计
export async function submitBatchDesign(request: BatchDesignRequestPayload): Promise<BatchStatus> {
  const response = await api.post('/design/batch', request)
  return response.data
}

export async function getBatchProgress(batchId: string): Promise<BatchProgress> {
  const response = await api.get(`/design/batch/${encodeURIComponent(batchId)}`)
  return response.data
}

export async function getBatchReport(batchId: string): Promise<Record<string, unknown>> {
  const response = await api.get(`/design/batch/${encodeURIComponent(batchId)}/report`)
  return response.data
}

export async function downloadBatchResults(batchId: string): Promise<void> {
  const response = await api.get(`/design/batch/${encodeURIComponent(batchId)}/download`, { responseType: 'blob' })
  await saveBlobResponse(response, `${batchId}_results.zip`)
}


// 载体序列下载
export async function getVectorSequence(vectorId: string, format: string = 'fasta'): Promise<string> {
  const response = await api.get(`/vectors/${encodeURIComponent(vectorId)}/sequence`, {
    params: { format },
    responseType: 'text'
  })
  return response.data
}

// 上传载体文件导入
export async function uploadVectorFile(file: File): Promise<VectorUploadResult> {
  const formData = new FormData()
  formData.append('file', file)
  const response = await api.post('/vectors/import/upload', formData)
  return response.data
}

// ==================== 设计页一次性载体（本地上传 / 在线导入） ====================
// 与 /vectors/import/* 的分工：那条路写**共享**载体库、需管理员；这条路只把
// 载体取回来随本次设计提交（后端不落盘），普通用户也能用自己手里的载体。

/** 在线来源清单与许可摘要（不出网；与载体库/图谱页同一份数据） */
export async function getDesignVectorSources(): Promise<{
  enabled: boolean
  snapgene_online_enabled?: boolean
  disclaimer: string
  sources: OnlineSourceInfo[]
}> {
  const response = await api.get('/design/vector/sources')
  return response.data
}

/** 上传载体文件（GenBank / FASTA / SnapGene .dna）→ 一次性载体载荷 */
export async function uploadDesignVector(file: File): Promise<DesignVectorPayload> {
  const formData = new FormData()
  formData.append('file', file)
  const response = await api.post('/design/vector/upload', formData)
  return response.data
}

export interface DesignVectorOnlineRequest {
  source: 'snapgene' | 'ensembl' | 'ncbi' | 'addgene'
  set_id?: string
  plasmid_id?: string
  identifier?: string
  seq_type?: 'genomic' | 'cds' | 'cdna'
  accession?: string
  addgene_id?: string
  fmt?: 'genbank' | 'dna'
}

/** 按来源在线取一条序列 → 一次性载体载荷（SnapGene 需登录；Addgene 需管理员） */
export async function importDesignVectorOnline(
  payload: DesignVectorOnlineRequest
): Promise<DesignVectorPayload> {
  const response = await api.post('/design/vector/online', payload)
  return response.data
}

/** SnapGene 在线库按质粒名跨集合搜索（只给候选，选中后再取全长） */
export async function searchDesignVectorSnapgene(q: string, limit = 20): Promise<{
  query: string
  count: number
  truncated?: boolean
  plasmids: {
    plasmid_id: string
    name: string
    filename?: string
    set_id?: string
    set_name?: string
  }[]
  failed_sets?: string[]
}> {
  const response = await api.get('/design/vector/snapgene/search', { params: { q, limit } })
  return response.data
}

// 更新载体信息
export async function updateVector(vectorId: string, data: {
  name?: string; description?: string; vector_type?: string;
  host?: string[]; antibiotic_resistance?: string[]; copy_number?: string
}): Promise<any> {
  const response = await api.put(`/vectors/${encodeURIComponent(vectorId)}`, data)
  return response.data
}

// 批量导入载体
export async function batchImportVectors(ncbiIds: string[], filePaths: string[] = []): Promise<BatchImportResult> {
  const response = await api.post('/vectors/import/batch', {
    ncbi_ids: ncbiIds,
    file_paths: filePaths
  })
  return response.data
}

// ==================== 在线序列来源（SnapGene 在线库 / Ensembl / Addgene） ====================
// 许可提示随数据一起下发（见后端 /api/vectors/import/sources）：SnapGene 库学术/非营利/
// 政府可无条件使用但须引用来源、商业用途须获 GraphPad 许可；Addgene 全长序列需登录，
// 后端不代为登录——前端只做展示与提示，不做任何绕过。

export interface OnlineSourceInfo {
  id: string
  name: string
  kind: string
  url: string
  license: string
  importable: boolean
  note: string
  /** 该来源的全长序列是否需要登录（Addgene） */
  requires_login?: boolean
  /** 部署方是否配置了用户自备凭据（Addgene 会话 Cookie） */
  session_configured?: boolean
  /** 该来源是否已由部署方开启（SnapGene 在线库默认开启，部署方可用 SNAPGENE_ONLINE_ENABLED=false 关闭） */
  enabled?: boolean
}

export interface OnlinePreview {
  source: string
  id: string
  name: string
  url: string
  length: number
  topology: string
  gc_content: number
  features: { name?: string; type?: string; start?: number; end?: number; strand?: number }[]
  warnings: string[]
  license: string
}

export async function getOnlineSources(): Promise<{
  enabled: boolean
  /** SnapGene 在线库开关（默认 false）；开启后也只对已登录用户开放 */
  snapgene_online_enabled?: boolean
  disclaimer: string
  sources: OnlineSourceInfo[]
}> {
  const response = await api.get('/vectors/import/sources')
  return response.data
}

export async function getSnapgeneSets(): Promise<{ count: number; sets: { set_id: string; name: string }[]; license: string }> {
  const response = await api.get('/vectors/import/snapgene/sets')
  return response.data
}

export async function getSnapgeneSetIndex(setId: string): Promise<{ set_id: string; count: number; plasmids: any[] }> {
  const response = await api.get(`/vectors/import/snapgene/sets/${encodeURIComponent(setId)}`)
  return response.data
}

export async function searchSnapgene(q: string): Promise<{
  query: string; count: number; truncated: boolean; plasmids: any[]; failed_sets: string[]
}> {
  const response = await api.get('/vectors/import/snapgene/search', { params: { q }, timeout: 90_000 })
  return response.data
}

export async function previewSnapgene(setId: string, plasmidId: string): Promise<OnlinePreview> {
  const response = await api.get('/vectors/import/snapgene/preview', {
    params: { set_id: setId, plasmid_id: plasmidId }
  })
  return response.data
}

export async function importFromSnapgene(setId: string, plasmidId: string): Promise<any> {
  const response = await api.post('/vectors/import/snapgene', { set_id: setId, plasmid_id: plasmidId })
  return response.data
}

export async function lookupEnsembl(identifier: string, expand: boolean = false): Promise<any> {
  const response = await api.get(`/vectors/import/ensembl/lookup/${encodeURIComponent(identifier)}`, {
    params: { expand }
  })
  return response.data
}

export async function previewEnsembl(identifier: string, seqType: string = 'cds'): Promise<OnlinePreview> {
  const response = await api.get('/vectors/import/ensembl/preview', {
    params: { identifier, seq_type: seqType }
  })
  return response.data
}

export async function importFromEnsembl(identifier: string, seqType: string = 'cds', name?: string): Promise<any> {
  const response = await api.post('/vectors/import/ensembl', { identifier, seq_type: seqType, name })
  return response.data
}

export async function searchAddgeneIndex(term: string, limit: number = 20): Promise<any> {
  const response = await api.get('/vectors/import/addgene/search', { params: { term, limit } })
  return response.data
}

export async function getAddgeneMetadata(plasmidId: string, trySequence: boolean = false): Promise<any> {
  const response = await api.get(`/vectors/import/addgene/${encodeURIComponent(plasmidId)}`, {
    params: { try_sequence: trySequence }
  })
  return response.data
}

// Addgene 全长序列导入（需管理员 + 部署方已配置用户自备会话 Cookie）：
// 后端不代登录、不绕登录墙，只带用户自己的凭据取自己有权访问的序列。
export async function importFromAddgene(
  plasmidId: string, fmt: 'genbank' | 'dna' = 'genbank', name?: string
): Promise<any> {
  const response = await api.post(`/vectors/import/addgene/${encodeURIComponent(plasmidId)}`, null, {
    params: { fmt, name }
  })
  return response.data
}

// -------------------- UniProt（蛋白库：只做反查，不给质粒序列） --------------------

export interface UniProtNucleotide {
  source: string
  accession: string
  label: string
  url: string
}

export interface UniProtEntry {
  accession: string
  entry_id: string
  name: string
  gene: string
  organism: string
  length: number
  reviewed: boolean
  cross_references: { database: string; id: string; protein_id?: string; molecule_type?: string }[]
}

export async function searchUniprot(
  query: string, limit: number = 10, reviewedOnly: boolean = false
): Promise<{ query: string; count: number; results: UniProtEntry[]; license: string; note: string }> {
  const response = await api.get('/vectors/import/uniprot/search', {
    params: { query, limit, reviewed_only: reviewedOnly }
  })
  return response.data
}

export async function getUniprotEntry(
  accession: string, withFasta: boolean = false
): Promise<{ accession: string; entry: UniProtEntry; nucleotide: UniProtNucleotide[]; url: string; license: string; note: string; fasta?: string }> {
  const response = await api.get(`/vectors/import/uniprot/${encodeURIComponent(accession)}`, {
    params: { with_fasta: withFasta }
  })
  return response.data
}

// 图谱页「另存为载体」（需管理员）：序列与特征由图谱页提交，后端不再外呼，
// 保证保存的与用户眼前看到的是同一份数据。
export async function saveMapAsVector(payload: {
  name: string
  sequence: string
  topology: string
  features: {
    name: string; type: string; start: number; end: number
    strand?: number; notes?: string; color?: string
  }[]
  source?: string
  url?: string
  description?: string
}): Promise<any> {
  const response = await api.post('/vectors/import/from-map', payload)
  return response.data
}

// 获取缓存信息
export async function getCacheInfo(): Promise<CacheInfo> {
  const response = await api.get('/cache/stats')
  return response.data
}

// 清除缓存
export async function clearCache(): Promise<CacheClearResult> {
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

// 酶切消化模拟（完全消化，返回片段大小）；circular=true 按环状质粒首尾相连
export async function simulateDigest(sequence: string, enzymes: string[], circular: boolean = false): Promise<any> {
  const response = await api.post('/analysis/digest', {
    sequence,
    enzymes,
    circular
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

/** Golden Gate 候选酶（REBASE：可商购、切出 4 nt 5' 突出端的 Type IIS 酶；BsaI/BsmBI/BbsI 在前） */
export interface GoldenGateEnzyme {
  name: string
  recognition_sequence: string
}

// 获取可用酶列表
export async function getEnzymes(): Promise<{
  total: number
  enzymes: Record<string, EnzymeInfo>
  golden_gate_enzymes?: GoldenGateEnzyme[]
}> {
  const response = await api.get('/analysis/enzymes')
  return response.data
}
// 用户认证
export async function login(email: string, password: string): Promise<AuthTokenResponse> {
  const response = await api.post('/auth/login', { email, password })
  return response.data
}

export async function register(email: string, username: string, password: string, confirmPassword: string): Promise<AuthTokenResponse> {
  const response = await api.post('/auth/register', {
    email,
    username,
    password,
    confirm_password: confirmPassword
  })
  return response.data
}

export async function getCurrentUser(): Promise<UserInfo | null> {
  try {
    const response = await api.get('/auth/me')
    return response.data
  } catch {
    return null
  }
}

export async function verifyToken(): Promise<VerifyTokenResponse> {
  const response = await api.get('/auth/verify')
  return response.data
}

// 用户登出
export async function logout(): Promise<void> {
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
  // 当前账号的批量测序上限（后端已按 个人覆盖 → 分组 → 服务器默认 解析；旧后端无此字段）
  batch_read_limits?: BatchReadLimitsInfo | null
}

export interface BatchReadLimitsInfo {
  max_reads_per_group: number
  max_total_reads: number
  /** 单请求入口的文件数 / 总体积上限（批量页走分块上传，不受这两项约束） */
  max_files: number
  max_upload_mb: number
  /** 分块上传：单个分块的文件数 / 体积上限与整批体积上限（旧后端无此字段） */
  chunk_max_files?: number
  chunk_max_mb?: number
  session_max_mb?: number
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

export type LimitTier = 'anonymous' | 'user' | 'admin'

// 批量测序 read 上限的一组取值；null = 跟随上一级（分组 → 服务器默认）
export interface BatchReadLimitPair {
  max_reads_per_group: number | null
  max_total_reads: number | null
}

export interface AdminSettings {
  registration_open: boolean
  email_verification_required: boolean
  anonymous_features: string[]
  user_features: string[]
  feature_keys: string[]
  feature_labels: Record<string, string>
  mail_provider: string
  mail_from: string
  batch_read_limits?: Record<LimitTier, BatchReadLimitPair>
  batch_read_limit_defaults?: { max_reads_per_group: number; max_total_reads: number }
  batch_read_limit_hard_max?: { max_reads_per_group: number; max_total_reads: number }
  batch_max_files?: number
  batch_upload_max_mb?: number
  batch_session_max_mb?: number
}

export type AdminSettingsPatch = Partial<Omit<AdminSettings, 'batch_read_limits'>> & {
  batch_read_limits?: Partial<Record<LimitTier, Partial<BatchReadLimitPair>>>
}

export interface AdminUserInfo {
  id: string
  email: string
  username: string
  is_admin: boolean
  is_active: boolean
  email_verified: boolean
  allowed_features: string[] | null
  batch_max_reads_per_group?: number | null
  batch_max_total_reads?: number | null
  created_at: string | null
}

export async function getAdminSettings(): Promise<AdminSettings> {
  const response = await api.get('/admin/settings')
  return response.data
}

export async function updateAdminSettings(patch: AdminSettingsPatch): Promise<AdminSettings> {
  const response = await api.put('/admin/settings', patch)
  return response.data
}

export async function listAdminUsers(): Promise<AdminUserInfo[]> {
  const response = await api.get('/admin/users')
  return response.data
}

export async function updateAdminUser(
  userId: string,
  patch: {
    is_admin?: boolean
    is_active?: boolean
    email_verified?: boolean
    allowed_features?: string[]
    // null = 清除个人覆盖（跟随所在分组）
    batch_max_reads_per_group?: number | null
    batch_max_total_reads?: number | null
  }
): Promise<AdminUserInfo> {
  const response = await api.put(`/admin/users/${encodeURIComponent(userId)}`, patch)
  return response.data
}

export async function deleteAdminUser(userId: string): Promise<void> {
  await api.delete(`/admin/users/${encodeURIComponent(userId)}`)
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
  const response = await api.post(`/cache/invalidate/design/${encodeURIComponent(designId)}`)
  return response.data
}

// 缓存 — 载体缓存失效
export async function invalidateVectorCache(vectorId: string): Promise<any> {
  const response = await api.post(`/cache/invalidate/vector/${encodeURIComponent(vectorId)}`)
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
  /** 多段 CDS（GenBank join，含跨原点）的各段参考坐标，编码方向顺序 */
  segments?: [number, number][]
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
  /** 跨 read 反证：仅单 read 报告该 indel，列出的 read 干净跨过同一位点未见差异 */
  cross_read_conflict?: { reads: string[] }
  /** 互检定案：其他引物在可靠区读出设计碱基，判为源 read 测序误差（不进结论） */
  cross_read_resolved?: boolean
  /** 低置信的具体错误类型/原因（人工核对用） */
  low_reason?: string
  /** 独立 basecaller（tracy）重 basecall 后报出同一变体：跨 caller 印证 */
  corroborated_by_basecall?: boolean
  /** 峰级证据：替换为突变峰占比/信噪比，插入为插入峰强度比（相对邻峰） */
  peak_evidence?: {
    mutant_pct?: number | null
    snr?: number | null
    insertion_peak_ratio?: number | null
  }
}

export interface ConsensusSegment {
  cons_start: number
  cons_end: number
  ref_start: number
  ref_end: number
  sequence: string
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
    /** 环状参考跨原点时为展开坐标（> reference_length），折回见 ref_segments */
    ref_end: number
    /** 仅环状参考跨原点的 read 返回：true */
    wraps_origin?: boolean
    /** 仅跨原点 read 返回：折回后的覆盖区段 [[s, L], [1, e-L]] */
    ref_segments?: [number, number][] | null
    identity: number
    mixed_positions: number[]
    mixed_detail?: { pos: number; ratio: number; secondary_base: string }[]
    alignment_view?: AlignmentView | null
    grade?: string
    q20_ratio?: number
    /** read 可信区（原始 read 坐标 raw_start–raw_end；start/end 为修剪后坐标），旧记录缺省 */
    reliable_zone?: {
      start: number
      end: number
      raw_start: number
      raw_end: number
      front_reason?: string
      tail_reason?: string
    }
    /** 合并压缩区：该 read 对此段无可分辨证据（峰图条带画灰罩） */
    poly_merged_zones?: {
      base: string
      ref_start: number
      ref_end: number
      positions: number[]
    }[]
  }[]
  variants: SequencingVariant[]
  consensus: {
    sequence: string
    covered_ranges: [number, number][]
    coverage_percent: number
    diffs?: ConsensusDiff[]
    /** 由 read 实测拼出的连续片段（不含未测位置的参考填充；旧记录无此字段）。
     *  cons_start/cons_end 为 sequence 内 1 起始闭区间，ref_start/ref_end 为参考坐标 */
    segments?: ConsensusSegment[]
    assembled_length?: number
  }
  coverage_ranges: [number, number][]
  /** 覆盖缺口；环状参考跨原点的缺口 start > end（折回坐标），带
   *  wraps_origin 与 ref_segments [[x, L], [1, y]]，length 为合并后总长 */
  coverage_gaps?: {
    start: number
    end: number
    length: number
    wraps_origin?: boolean
    ref_segments?: [number, number][] | null
  }[]
  /** 参考是否按环状比对（旧记录无此字段） */
  circular?: boolean
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
      /** Step A 单 read 判读：resolvable 证据完整 / merged 不可判定 /
       *  deficit 可见缺失 / noisy 疑似肩峰 / no_evidence 采样不足 */
      resolution?: string
      reason?: string
      anchor_grade?: string
      anchor_issues?: string[]
      length_estimate?: number | null
      length_ci?: [number, number] | null
    }[]
    /** Step B 综合评判：accepted 互证一致 / contradictory 矛盾不可信 /
     *  undetermined 无可分辨证据 / deficit_observed 可见缺失 / caller_only */
    run_verdict?: string
    verdict_votes?: {
      filename?: string; direction?: string; peak_count?: number | null; anchor?: string
      joint?: boolean; observed_total?: number | null
      reads?: { filename: string; direction: string; peak_count: number | null; anchor?: string }[]
    }[]
    variant: { ref_pos: number; type: string; length: number; confidence: string } | null
  }[]
  mixed_detected: Record<string, number[]>
  /** 匿名创建时下发的访问令牌（终审 B-02：后续读取/导出/删除需携带） */
  access_token?: string
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
  allowDecompose = true,
  /** read 可信区边界 "30,60,700,900"；空串用后端默认 */
  reliableZone = ''
): Promise<SequencingAnalysis> {
  const form = new FormData()
  form.append('reference', reference, reference.name)
  reads.forEach((f) => form.append('reads', f))
  form.append('min_q', String(minQ))
  form.append('allow_decompose', String(allowDecompose))
  if (reliableZone) form.append('reliable_zone', reliableZone)
  const response = await api.post('/sequencing/analyze', form, { timeout: 120000 })
  const data = response.data as SequencingAnalysis
  // 终审 B-02：匿名创建的记录需凭令牌访问——创建响应下发的 token 存入会话
  // （sessionStorage，随标签页生命周期），后续详情/峰图/导出/删除带上
  if (data?.access_token && data.analysis_id) {
    rememberAnalysisToken(data.analysis_id, data.access_token)
  }
  return data
}

const TOKEN_KEY = 'pd_seq_tokens'

/** 匿名分析记录访问令牌表（analysis_id → token），仅本标签页会话内有效 */
export function rememberAnalysisToken(analysisId: string, token: string): void {
  try {
    const raw = sessionStorage.getItem(TOKEN_KEY)
    const map = raw ? JSON.parse(raw) : {}
    map[analysisId] = token
    sessionStorage.setItem(TOKEN_KEY, JSON.stringify(map))
  } catch {
    /* 隐私模式下 sessionStorage 不可用：令牌丢失即不可回看，不影响本次分析 */
  }
}

export function analysisTokenHeaders(analysisId?: string): Record<string, string> | undefined {
  if (!analysisId) return undefined
  try {
    const raw = sessionStorage.getItem(TOKEN_KEY)
    const map = raw ? JSON.parse(raw) : {}
    const tok = map[analysisId]
    return tok ? { 'X-Access-Token': tok } : undefined
  } catch {
    return undefined
  }
}

/** 取设计结果的 GenBank 文件（测序页深链自动带入参考序列用） */
export async function fetchDesignGenbankFile(designId: string): Promise<File> {
  const response = await api.get(`/design/${encodeURIComponent(designId)}/download/genbank`, { responseType: 'blob' })
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
  const response = await api.get(`/vectors/${encodeURIComponent(vectorId)}/sequence`, {
    params: { format: 'genbank' },
    responseType: 'blob'
  })
  assertBlobIsGenbank(response.data, vectorId)
  return new File([response.data], `${vectorId}.gb`, { type: 'application/octet-stream' })
}

export async function getReadTrace(analysisId: string, readIndex: number): Promise<ReadTrace> {
  const response = await api.get(`/sequencing/analyses/${encodeURIComponent(analysisId)}/trace/${encodeURIComponent(readIndex)}`,
    { headers: analysisTokenHeaders(analysisId) })
  return response.data
}

export async function exportConsensus(analysisId: string, format: string, coveredOnly = false): Promise<string> {
  const response = await api.get(`/sequencing/analyses/${encodeURIComponent(analysisId)}/consensus/export`, {
    params: { format, covered_only: coveredOnly },
    headers: analysisTokenHeaders(analysisId),
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
  // 终审 B-02：匿名记录凭创建时下发的令牌访问
  const response = await api.get(`/sequencing/analyses/${encodeURIComponent(analysisId)}`,
    { headers: analysisTokenHeaders(analysisId) })
  return response.data
}

export async function deleteSequencingAnalysis(analysisId: string): Promise<void> {
  await api.delete(`/sequencing/analyses/${encodeURIComponent(analysisId)}`,
    { headers: analysisTokenHeaders(analysisId) })
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
  /** 匿名批量下发的整批访问令牌（终审 B-02/B-03：该批记录共用） */
  access_token?: string
}

/** 批量测序分析：整个交付文件夹（.ab1 + 图谱，可多质粒）+ 可选 Excel 信息表一次上传 */
export async function analyzeSequencingBatch(
  files: File[],
  excel: File | null,
  minQ = 20,
  reliableZone = ''
): Promise<SequencingBatchResult> {
  const form = new FormData()
  files.forEach((f) => form.append('files', f))
  if (excel) form.append('excel', excel, excel.name)
  form.append('min_q', String(minQ))
  if (reliableZone) form.append('reliable_zone', reliableZone)
  const response = await api.post('/sequencing/analyze-batch', form, { timeout: 600000 })
  return rememberBatchTokens(response.data as SequencingBatchResult)
}

function rememberBatchTokens(data: SequencingBatchResult): SequencingBatchResult {
  // 终审 B-02/B-03：匿名批量下发整批令牌——该批每条记录共用它（详情/下载）
  if (data?.access_token) {
    for (const it of data.items || []) {
      if (it.analysis_id) rememberAnalysisToken(it.analysis_id, data.access_token)
    }
    // 匿名批次的整理包同样凭整批令牌下载（以 batch_id 为键存同一张表）
    if (data.batch_id) rememberAnalysisToken(data.batch_id, data.access_token)
  }
  return data
}

function sessionHeaders(token?: string): Record<string, string> | undefined {
  return token ? { 'X-Access-Token': token } : undefined
}

/** 分块上传会话的 axios 传输层（见 utils/chunkedUpload.ts） */
export const batchSessionTransport: ChunkedTransport<SequencingBatchResult> = {
  async create(manifest, excel) {
    const form = new FormData()
    form.append('manifest', JSON.stringify(manifest))
    if (excel) form.append('excel', excel, excel.name)
    const response = await api.post('/sequencing/batch-sessions', form, { timeout: 120000 })
    return response.data
  },
  async upload(sessionId, files, index, token, signal) {
    const form = new FormData()
    files.forEach((f) => form.append('files', f, f.name))
    form.append('chunk_index', String(index))
    // 单块 ≤90 MB：慢速上行（~3 Mbps）也能在 5 分钟内传完
    const response = await api.post(`/sequencing/batch-sessions/${encodeURIComponent(sessionId)}/files`, form, {
      timeout: 300000,
      headers: sessionHeaders(token),
      signal
    })
    return response.data
  },
  async finalize(sessionId, minQ, token, reliableZone) {
    const form = new FormData()
    form.append('min_q', String(minQ))
    if (reliableZone) form.append('reliable_zone', reliableZone)
    // 服务器最多等 60 秒，仍未完成返回 202 转轮询（Cloudflare 源站超时 100 秒）
    const response = await api.post(`/sequencing/batch-sessions/${encodeURIComponent(sessionId)}/finalize`, form, {
      timeout: 95000,
      headers: sessionHeaders(token),
      validateStatus: (s) => s === 200 || s === 202
    })
    return { status: response.status, data: response.data }
  },
  async status(sessionId, token) {
    const response = await api.get(`/sequencing/batch-sessions/${encodeURIComponent(sessionId)}`, {
      timeout: 30000,
      headers: sessionHeaders(token)
    })
    return response.data
  },
  async abort(sessionId, token) {
    await api.delete(`/sequencing/batch-sessions/${encodeURIComponent(sessionId)}`, {
      timeout: 30000,
      headers: sessionHeaders(token)
    })
  }
}

/** 批量测序分析（分块上传）：文件自动按 ≤90 MB / ≤200 个切成多个请求依次上传，
 *  服务器拼成一个批次分析一次；结果与 analyzeSequencingBatch 相同 */
export async function analyzeSequencingBatchChunked(
  files: File[],
  excel: File | null,
  minQ = 20,
  opts: ChunkedOptions = {}
): Promise<SequencingBatchResult> {
  const data = await runChunkedBatch(files, excel, minQ, batchSessionTransport, opts)
  return rememberBatchTokens(data)
}

/** 下载批量分析的整理包：按质粒/克隆归档的原始文件副本 + 各组分析报告 +
 *  整理清单 + 结论回填的信息表（离线脚本产物的网页版；生成 15 分钟后过期） */
export async function downloadBatchSequencingReport(batchId: string): Promise<void> {
  const response = await api.get(`/sequencing/batches/${encodeURIComponent(batchId)}/report`, {
    responseType: 'blob',
    timeout: 300000,
    headers: analysisTokenHeaders(batchId)
  })
  await saveBlobResponse(response, `测序整理_${batchId.slice(-6)}.zip`)
}
