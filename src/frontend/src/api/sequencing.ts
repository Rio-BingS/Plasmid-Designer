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

/** 证据包单 read 行（goal T22；A 5.4）：调用/Q/峰强比/评分/伪迹 + 小图 */
export interface EvidencePackRead {
  read: string
  read_pos?: number
  call?: string
  q?: number | null
  mutant_pct?: number | null
  p_real?: number | null
  /** 可信度三档（goal v3 W15）：high/medium/low；旧记录缺省 null */
  tier?: string | null
  artifact?: string | null
  svg?: string | null
}

/** 证据包位点（blocking 项 + 建议核对/复测变体，goal T22） */
export interface EvidencePackSite {
  ref_pos: number
  kind: string
  label: string
  detail: string
  reads: EvidencePackRead[]
}

export interface EvidencePackData {
  sample: string
  verdict_status?: string
  algo_version?: string
  sites: EvidencePackSite[]
}

/** 单条支持 read 的峰级证据（goal T14 EvidenceItem） */
export interface SequencingEvidenceItem {
  read: string
  read_pos?: number
  strand?: string
  q?: number
  mutant_pct?: number
  snr?: number
  mixed_ratio?: number
  mixed_z?: number
  in_zone?: boolean
  called_base?: string
  /** 位点所在伪迹区段 kind（goal T20：compression/dye_blob/short_slippage） */
  artifact?: string
}

export interface SequencingVariant {
  ref_pos: number
  /** 双坐标（goal v3 W9，CM P1-D）：质粒坐标 + 命中 CDS 内 c./p.（负链
   *  按编码方向换算）；旧记录无此字段 → 变异表只显示质粒坐标 */
  coords?: { plasmid: number; cds?: { name: string; c: string; p?: string } }
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
  /** 酶位提示（goal T25.4；旧记录缺省）：±10bp 内落在参考酶识别位点上的
   *  未决 indel——位点有无随 indel 归属待定，结论带「位点归属待定」 */
  enzyme_sites_pending?: string[]
  /** 同密码子多替换联合注释（goal T25.1；旧记录缺省）：成员位/合并碱基变化 */
  mnv?: { codon: number; members: number[]; nt_change: string; aa_change?: string | null }
  confidence?: string
  /** 证据三态（goal T1；旧记录缺省）：rejected 仅互检定案，unresolved=证据不足 */
  status?: 'rejected' | 'confirmed' | 'unresolved'
  /** T15 规则评分 0-1（锚点+层内修正；2026-11-08 复审 R5：与旧 if 树
   * 逐位等价的手设系数，非校准概率——前端展示为 0-100 评分，旧记录缺省） */
  p_real?: number
  /** 四档行动（goal T15；旧记录缺省）：采信/排除/核对峰图/建议重测 */
  action?: 'accept' | 'exclude' | 'review_trace' | 'resequence'
  /** 机器可读原因码（goal T1/T3）：cross_check_clean/front_hard_zone/
   *  tail_zone/all_evidence_out_of_zone/poly_compressed 等 */
  reason_codes?: string[]
  /** 跨 read 反证：仅单 read 报告该 indel，列出的 read 干净跨过同一位点未见差异 */
  cross_read_conflict?: { reads: string[] }
  /** 互检定案：其他引物在可靠区读出设计碱基，判为源 read 测序误差（不进结论） */
  cross_read_resolved?: boolean
  /** 逐 read 证据（goal T14）：每条支持 read 的峰强比/信噪比/Q/链向/可信区 */
  evidence_by_read?: SequencingEvidenceItem[]
  /** 覆盖该位点但未支持该变体的其他 read（goal T14；只记覆盖事实，非反证） */
  covering_reads_not_supporting?: { read: string; read_pos?: number; q?: number }[]
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
    mixed_detail?: { pos: number; ratio: number; secondary_base: string; call_disagrees_with_trace?: boolean }[]
    alignment_view?: AlignmentView | null
    grade?: string
    q20_ratio?: number
    /** 质量值来源（goal T24.5；旧记录缺省）：pbas/tracy_basecall/peak_estimate */
    quality_source?: string
    /** PLOC 比 PBAS 短的条数（goal T24.2；0/缺省=正常），尾端峰位缺失 */
    ploc_shortfall?: number
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
   *  wraps_origin 与 ref_segments [[x, L], [1, y]]，length 为合并后总长；
   *  cause（goal v3 W6）：beyond_primer=引物够不到 / read_shortfall=
   *  应测到未测到 / unknown=归因不明——前端缺口按原因着色 */
  coverage_gaps?: {
    start: number
    end: number
    length: number
    cause?: string
    wraps_origin?: boolean
    ref_segments?: [number, number][] | null
  }[]
  /** 覆盖分级（goal v3 W6/W13）：expected_ranges=引物理论覆盖 E、
   *  verified_ranges=验证覆盖 V——MatchMap 三层色带数据源（旧记录缺省） */
  coverage?: {
    measured_ranges?: [number, number][]
    verified_ranges?: [number, number][]
    expected_ranges?: [number, number][]
    verified_percent?: number
    measured_percent?: number
  }
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
  decomposed_alleles?: Record<string, {
    sequence: string; source: string
    /** 等位各自走比对+CDS 判读（goal T10；A 3.6，旧记录缺省） */
    cds_reports?: { name: string; verdict: string; coverage_status: string }[]
    variants?: { ref_pos: number; type: string; length: number; alt_base: string }[]
    identity?: number
  }[]>
  errors: { filename: string; error: string }[]
  /** 解析层告警（goal T24；旧记录缺省 []）：PLOC 截短 / 峰型估 Q 降级等 */
  warnings?: string[]
  reference_length: number
  features: { name: string; type: string; start: number; end: number; strand: string; description?: string; analysis_target?: boolean }[]
  /** 结构化判定（goal T2；旧记录缺省）：status 驱动所有出口，blocking 为阻断项清单 */
  verdict?: Verdict
  /** 区间裁决行结构化形态（互检排除差异，前端高亮；旧记录缺省） */
  resolved_regions?: ResolvedRegion[]
  /** 结论行结构化分组（2026-11-08 复审 R6；旧记录缺省 null → 前端回退
   * 文案匹配）：low=低置信折叠组、mixed_detail=逐 read 双峰行、
   * resolved=区间裁决行——前端折叠不再依赖「低置信度（/↳+双峰」匹配 */
  conclusion_groups?: { low: string[]; mixed_detail: string[]; resolved: string[] } | null
  /** 未决位点（N/模糊调用、poly 不可判定、dropout、无法互检双峰；T1） */
  unresolved_sites?: UnresolvedSite[]
  /** 单点双峰次等位（goal T7 MixedVariant；T22 起下发供差异表/证据包跳转） */
  mixed_variants?: { ref_pos: number; ref_base?: string; called_base?: string;
    secondary_base?: string; af?: number; reads?: string[] }[]
  mixed_corroboration?: Record<string, unknown> | null
  /** 复核队列（goal v3 W14）：确证差异/范围内缺口/未决位点；旧记录惰性派生 */
  review_queue?: ReviewQueueItem[]
  /** goal v3.1 X2：结论正文只留要点，逐条明细在此（折叠展开/导出用） */
  conclusion_detail?: string[]
  /** 人工确认留痕（goal v3 W14，PL F3）：ref_pos/decision/by/at/note */
  review_decisions?: ReviewDecision[]
  /** Sanger 判读算法版本（口径变更递增；旧记录缺省） */
  algo_version?: string
}

/** verdict.blocking/info 条目（goal §3） */
export interface VerdictItem {
  kind: string
  ref_start?: number | null
  ref_end?: number | null
  element?: string | null
  detail: string
  action: string
  refs?: string[]
}

/** 需测范围 R（goal v3 §3.1；W2 起随 verdict 下发，未声明为合成 measured） */
export interface ScopeInfo {
  ranges: [number, number][]
  source: 'user' | 'design_insert' | 'cds_target' | 'filename_match' | 'primer_window' | 'measured'
  declared: boolean
  require_double_strand?: boolean
  elements?: string[]
  reason?: string
  /** 接合区窗口（W8）：属需测范围但不并入 ranges（头行/区间渲染只显示主段） */
  junctions?: [number, number][]
  /** 沿用上次范围（X5）：source 保留原来源，此时 reason=沿用上次范围、
   *  头行带注记、范围条显示「清除」 */
  remembered?: boolean
  /** 参考序列指纹（X5）：清除范围记忆的 DELETE 参数 */
  reference_hash?: string
}

/** 元件级判定（goal v3 W5）：范围内元件逐行，范围外 not_assessed */
export interface ElementVerdict {
  name: string
  type: string
  start: number
  end: number
  status: 'ok' | 'issue' | 'not_assessed'
  coverage: number | null
  double_strand?: number | null
  issues: number
  in_scope: boolean
}

/** 结构化判定（goal T2）：网页/Excel/归档/API 出口统一由 status 驱动 */
export interface Verdict {
  status: 'pass' | 'fail' | 'pending' | 'mixed' | 'partial' | 'insufficient' | 'failed_reads'
  blocking: VerdictItem[]
  info: VerdictItem[]
  /** 三段式头行（goal v3 W5/W12）：三出口唯一文案来源 */
  headline?: string
  scope?: ScopeInfo | null
  element_verdicts?: ElementVerdict[]
  roi?: {
    start: number
    end: number
    source: string
    verified_fraction?: number | null
    measured_fraction?: number | null
    double_strand_fraction?: number | null
  }
  reads: { total: number; analyzed: number; failed: { file: string; error: string }[] }
  /** 人工确认计数（goal v3 W14）：headline 自带「人工确认 N 处」 */
  reviewed_count?: number
  algo_version?: string
}

/** 复核队列项（goal v3 W14，PL §F）：确证差异 / 范围内缺口 / 未决位点；
 * beyond_primer 只给动作建议不入队 */
/**
 * 复核队列条目（goal v3.1 X2 三级聚合）
 *
 * level：read（整条 read 折叠，位点收在下面）/ segment（相邻 ≤20bp 合并）/
 * site（孤立位点）/ confirmed（确证差异）/ junction（接合区）。sites 为聚合
 * 的位点数（>1 时给出）。
 */
export interface ReviewQueueItem {
  prio: 'fail' | 'read_shortfall' | 'pending'
  level?: 'read' | 'segment' | 'site' | 'confirmed' | 'junction'
  kind: string
  ref_start: number | null
  ref_end: number | null
  element?: string | null
  detail: string
  action: string
  sites?: number
  evidence: {
    refs?: string[]
    af?: number
    max_af?: number
    min_q?: number
    read?: string
    reason?: string
    positions?: (number | null)[]
    zone?: { start?: number; end?: number }
    gap?: { start: number; end: number; length?: number; cause?: string; detail?: string }
    expected_ranges?: number[][]
  }
}

/** 人工确认留痕（PL F3） */
export interface ReviewDecision {
  ref_pos: number
  decision: 'artifact' | 'mutation'
  by: string
  at: string
  note?: string
}

/** 区间裁决行（互检排除差异的 read×区段 聚合） */
export interface ResolvedRegion {
  ref_range: string
  cds_offset?: number | null
  source_read: string
  direction: string
  primer_range?: string | null
  variant_count: number
  verifier_reads: string[]
  verifier_quality?: string | null
  positions: number[]
}

/** 未决位点（「没读清」的地方，✓ 资格门输入） */
export interface UnresolvedSite {
  ref_pos: number
  kind: 'n_call' | 'iupac' | 'out_of_zone_diff' | 'poly_undetermined' | 'dropout' | 'mixed_uncorroborated' | 'corroboration_conflict'
  reads: string[]
  detail: string
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
  reliableZone = '',
  /** 目的基因模式：以参考内指定 CDS 的编码序列为参考分析（坐标即 CDS 内坐标） */
  cdsOptions?: { mode: boolean; name?: string },
  /** 需测范围（goal v3 W10）：元件名 / 301-900 / 多段(;) / 「全质粒」等；
   * 声明后结论只对该范围负责。留空走来源推断（记忆/CDS 模式/文件名） */
  scope?: string
): Promise<SequencingAnalysis> {
  const form = new FormData()
  form.append('reference', reference, reference.name)
  reads.forEach((f) => form.append('reads', f))
  form.append('min_q', String(minQ))
  form.append('allow_decompose', String(allowDecompose))
  if (reliableZone) form.append('reliable_zone', reliableZone)
  if (cdsOptions?.mode) {
    form.append('cds_mode', 'true')
    if (cdsOptions.name) form.append('cds_name', cdsOptions.name)
  }
  if (scope) form.append('scope', scope)
  const response = await api.post('/sequencing/analyze', form, { timeout: 120000 })
  const data = response.data as SequencingAnalysis
  // 终审 B-02：匿名创建的记录需凭令牌访问——创建响应下发的 token 存入会话
  // （sessionStorage，随标签页生命周期），后续详情/峰图/导出/删除带上
  if (data?.access_token && data.analysis_id) {
    rememberAnalysisToken(data.analysis_id, data.access_token)
  }
  return data
}

/** 目的基因模式前置解析：参考图谱内候选 CDS 清单 + 自动匹配建议 */
export interface SequencingReferenceParse {
  reference_length: number
  circular: boolean
  candidates: { name: string; start: number; end: number; strand: string; length: number }[]
  /** 文件名自动匹配命中的候选（唯一命中才有）；note 为匹配依据 */
  auto_match: { name: string; note: string } | null
  note: string
  /** 范围预设（goal v3 W10/W13）：「全质粒」+ 各 CDS 候选，附 reason——
   * 范围选择条的数据源；default_preset 是按文件名匹配推导的默认项 */
  scope_presets?: { value: string; label: string; reason: string }[]
  default_preset?: { value: string; reason: string } | null
}

/** 解析参考文件特征，供「目的基因模式」下拉预选（轻量只读，不落库不分析） */
export async function parseSequencingReference(
  reference: File,
  readNames: string[] = []
): Promise<SequencingReferenceParse> {
  const form = new FormData()
  form.append('reference', reference, reference.name)
  readNames.forEach((n) => form.append('read_names', n))
  const response = await api.post('/sequencing/parse-reference', form)
  return response.data as SequencingReferenceParse
}

/** 按新范围从存档重算判定（goal v3 W11/W13）：不重跑比对，毫秒级返回；
 * 更新 verdict/conclusion 并追加 scope_history。响应含重算后的三件套 */
export async function recomputeVerdict(
  analysisId: string,
  scope: string,
): Promise<{ verdict: Verdict; conclusion: string; scope_history: unknown[] }> {
  const response = await api.post(
    `/sequencing/analyses/${encodeURIComponent(analysisId)}/verdict`,
    { scope },
    { headers: analysisTokenHeaders(analysisId) },
  )
  return response.data
}

/** 清除范围记忆（goal v3.1 X5）：按参考指纹删掉本人那一行，之后的同参考
 * 分析回到未声明口径；幂等（没有记录时 deleted=false，不报错） */
export async function deleteScopeMemory(
  referenceHash: string,
): Promise<{ deleted: boolean; reference_hash: string }> {
  const response = await api.delete('/sequencing/scope-memory', {
    params: { reference_hash: referenceHash },
  })
  return response.data
}

/** 复核队列一键确认（goal v3 W14，PL F3）：伪影移出阻断 / 突变升级确证，
 * 留痕 review_decisions（by/at）并重算 verdict——headline 自带「人工确认 N 处」 */
export async function confirmReview(
  analysisId: string,
  refPos: number,
  decision: 'artifact' | 'mutation',
  note = '',
): Promise<{ verdict: Verdict; conclusion: string;
             review_decisions: ReviewDecision[]; review_queue: ReviewQueueItem[] }> {
  const response = await api.post(
    `/sequencing/analyses/${encodeURIComponent(analysisId)}/review`,
    { ref_pos: refPos, decision, note },
    { headers: analysisTokenHeaders(analysisId) },
  )
  return response.data
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

/** 证据包（goal T22；A 5.4）：blocking 项与建议核对/复测变体的逐 read 峰图 */
export async function getEvidencePack(analysisId: string): Promise<EvidencePackData> {
  const response = await api.get(
    `/sequencing/analyses/${encodeURIComponent(analysisId)}/evidence-pack`,
    { params: { format: 'json' }, headers: analysisTokenHeaders(analysisId) })
  return response.data
}

export async function downloadEvidencePack(analysisId: string, sample: string): Promise<void> {
  const response = await api.get(
    `/sequencing/analyses/${encodeURIComponent(analysisId)}/evidence-pack`,
    { params: { format: 'md' }, headers: analysisTokenHeaders(analysisId),
      responseType: 'blob' })
  await saveBlobResponse(response, `${sample || 'analysis'}-evidence-pack.md`)
}

export async function exportConsensus(analysisId: string, format: string, coveredOnly = false, markUnverified = true): Promise<string> {
  const response = await api.get(`/sequencing/analyses/${encodeURIComponent(analysisId)}/consensus/export`, {
    params: { format, covered_only: coveredOnly, mark_unverified: markUnverified },
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
  /** 复核汇总（goal v3 W14，PL F4）：需复核位点数与最高优先级 */
  review_count?: number
  review_prio?: 'fail' | 'read_shortfall' | 'pending' | null
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
