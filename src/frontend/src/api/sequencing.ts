// 本文件由 api/index.ts 按域拆分而来（纯搬运）：index.ts 保留 re-export 兼容层，
// 新代码请直接从 '@/api/<域>' 导入。

import { api, saveBlobResponse } from './client'
import { runChunkedBatch, type ChunkedOptions, type ChunkedTransport } from '@/utils/chunkedUpload'

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
const TOKEN_KEY = 'pd_seq_tokens'

/** 匿名分析记录访问令牌表（analysis_id → token），仅本标签页会话内有效 */
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
