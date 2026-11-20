<script setup lang="ts">
/**
 * 匹配简图：测序 read 落位 + 参考特征一览，基于通用图谱组件 LinearMap。
 *
 * 测序分析结果不是 MapDocument：这里把参考长度/拓扑/特征适配成一份
 * MapDocument（无序列、无酶切），read / 覆盖 / 变异作为 LinearMap 的额外轨道：
 * - read：深红块状箭头（方向 = 箭头），跨原点 read 折回成两段，差异位点画空心圆；
 * - 覆盖：主轴上的绿色已测段；变异：主轴上方红色刻块；
 * - 特征：按类型配色的箭头，名字放不下时引线外置（LinearMap 统一标签布局）。
 * 点击 read / 变异只上报事件，峰图联动留在父组件。特征去重开关是纯 UI 状态。
 */
import { computed, ref } from 'vue'
import type { ScopeInfo, SequencingAnalysis, Verdict } from '@/api'
import type { FeatureType, MapDocument, MapFeature } from '@/map/types'
import type { MapTrack } from '@/map/tracks'
import LinearMap from '@/map/components/LinearMap.vue'
import { FEATURE_COLORS, FEATURE_TYPE_LABELS } from '@/map/core/palette'
import { readSegments, readSpanText, shortName } from '@/utils/seqPanelModel'

type ReadRow = SequencingAnalysis['reads'][number]
type Feature = SequencingAnalysis['features'][number]
type Variant = SequencingAnalysis['variants'][number]
type GapRow = NonNullable<SequencingAnalysis['coverage_gaps']>[number]

const props = withDefaults(defineProps<{
  reads: ReadRow[]
  variants: SequencingAnalysis['variants']
  features: Feature[]
  coverageRanges: [number, number][]
  referenceLength: number
  /** 参考是否按环状比对（旧记录无此字段时由跨原点 read 推断） */
  circular?: boolean
  /** 需测范围 R（goal v3 W13）：声明时画蓝色主段带 + 范围外置灰 */
  scope?: ScopeInfo | null
  /** 理论覆盖 E（引物窗并集；W6 随 coverage 下发，旧记录缺省） */
  expectedRanges?: [number, number][]
  /** 验证覆盖 V（verified 口径；旧记录回退实测 coverage_ranges） */
  verifiedRanges?: [number, number][]
  /** 覆盖缺口（W6 起带 cause：beyond_primer/read_shortfall/unknown） */
  coverageGaps?: GapRow[]
  /** 结构化判定：blocking/info 中带参考坐标的条目在图上逐点标注（用户 2026-11-18） */
  verdict?: Verdict | null
  /** 未决位点（无法互检双峰、N 调用等）：按 read 画到 read 箭头上 */
  unresolvedSites?: NonNullable<SequencingAnalysis['unresolved_sites']>
}>(), { circular: undefined, scope: null, expectedRanges: () => [], verifiedRanges: () => [], coverageGaps: () => [], verdict: null, unresolvedSites: () => [] })

const emit = defineEmits<{
  (e: 'open-read', index: number): void
  (e: 'jump-variant', v: Variant): void
  /** 点击结果位点刻块/标注：跳峰图到该位点（read = 涉及该位点的第一条 read） */
  (e: 'jump-site', s: { ref_pos: number; read?: string }): void
  /** 点击元件加入/移出需测范围（goal W13；父组件重组 scope 文本重算） */
  (e: 'toggle-feature-scope', name: string): void
  /** 坐标轴拖拽框选后把区间加入范围 */
  (e: 'add-scope-range', range: [number, number]): void
}>()

/** 测序参考特征类型（GenBank/载体库原样）→ 图谱契约类型 */
const TYPE_MAP: Record<string, FeatureType> = {
  cds: 'cds', gene: 'cds', promoter: 'promoter', terminator: 'terminator', origin: 'rep_origin',
  rep_origin: 'rep_origin', resistance: 'marker', marker: 'marker', tag: 'tag', mcs: 'mcs',
  multiple_cloning_site: 'mcs', rbs: 'rbs', regulatory: 'protein_bind', protein_bind: 'protein_bind',
  primer_bind: 'primer_bind', polya_signal: 'polyA_signal', enhancer: 'enhancer', ltr: 'ltr',
  sig_peptide: 'signal_peptide', signal_peptide: 'signal_peptide', misc_rna: 'misc_rna', ncrna: 'misc_rna',
}
function normType(t: string): FeatureType {
  return TYPE_MAP[(t || '').toLowerCase()] ?? 'misc_feature'
}

/** 该 read 全部差异（替换/插入/缺失）在参考上的位置（环状跨原点按参考长度折回） */
function readDiffPositions(r: ReadRow): number[] {
  const av = r.alignment_view
  if (!av) return []
  const L = props.referenceLength
  const fold = (p: number) => (r.wraps_origin && L > 0 && p > L ? p - L : p)
  const out = new Set<number>()
  let pos = av.ref_start
  for (let i = 0; i < av.ref_aligned.length; i++) {
    const rb = av.ref_aligned[i]
    const qb = av.read_aligned[i]
    if (rb !== '-') {
      if (qb === '-' || qb !== rb) out.add(fold(pos))
      pos++
    } else if (qb !== '-') {
      out.add(Math.max(1, fold(pos - 1)))  // 插入列：记在左翼参考位置
    }
  }
  return [...out].sort((x, y) => x - y)
}

/** 特征去重（默认关 = 按文件原样）：同名且重叠或相邻（≤50bp）的合并为一条，方向不敏感；
 *  相距远的同名特征（如两端各一个 3 UTR）不合并 */
const DEDUP_GAP_BP = 50
const dedupMapFeats = ref(false)
const shownFeatures = computed<Feature[]>(() => {
  const feats = [...props.features].sort((x, y) => x.start - y.start || x.end - y.end)
  if (!dedupMapFeats.value) return feats
  const merged: Feature[] = []
  for (const f of feats) {
    const hit = merged.find((m) => m.name === f.name && f.start - m.end <= DEDUP_GAP_BP)
    if (hit) hit.end = Math.max(hit.end, f.end)
    else merged.push({ ...f })
  }
  return merged
})
const dedupMergedCount = computed(() => (dedupMapFeats.value ? props.features.length - shownFeatures.value.length : 0))

const isCircular = computed(() => props.circular ?? props.reads.some((r) => r.wraps_origin))

const doc = computed<MapDocument>(() => ({
  schema: 'plasmid-map/1',
  name: '参考序列',
  topology: isCircular.value ? 'circular' : 'linear',
  length: Math.max(1, props.referenceLength),
  sequence: '',
  features: shownFeatures.value.map((f, i): MapFeature => ({
    id: `s${i}`, name: f.name, type: normType(f.type), start: f.start, end: f.end,
    strand: f.strand === '-' ? -1 : 1, source: 'file', notes: f.description,
  })),
  enzymes: [], orfs: [], primers: [],
  meta: { source_format: 'raw', enzyme_set: '', warnings: [] },
}))

const alignedReads = computed(() => props.reads.filter((r) => readSegments(r).length > 0))

// ==================== 突出 read 覆盖（分段比例尺） ====================
/**
 * 焦点 = read 覆盖段 ∪ 紧邻 read 的元件（用户 2026-11-18：突出覆盖是为了更好地呈现
 * 测序结果，不能牺牲标注）。read 两端各外扩 max(30bp, 5%)；与 read 重叠或距 read
 * 端点 ≤ FLANK_BP 的元件整体并入焦点（启动子/操纵子/RBS/终止子等侧翼元件
 * 放大到能在箭头内写名字），过长元件（> 焦点核心长度）只并入与核心重叠的部分，
 * 避免一个大 CDS 把焦点稀释回全长。焦点约占 60% 宽，其余区段压缩；
 * 焦点本已占全长 ≥60% 时 LinearMap 自动退回线性比例尺。
 */
const FOCUS_SHARE = 0.6
const FLANK_BP = 200
const focusReads = ref(true)
const focusRanges = computed(() => {
  if (!focusReads.value) return []
  const L = props.referenceLength
  const core = alignedReads.value.flatMap((r) => readSegments(r)).map(([s, e]) => {
    const pad = Math.max(30, Math.round((e - s + 1) * 0.05))
    return { start: Math.max(1, s - pad), end: Math.min(L, e + pad) }
  })
  if (!core.length) return []
  const coreLen = core.reduce((t, c) => t + c.end - c.start + 1, 0)
  const extra: { start: number; end: number }[] = []
  for (const f of shownFeatures.value) {
    const fs = Math.min(f.start, f.end)
    const fe = Math.max(f.start, f.end)
    if (fe - fs + 1 >= L) continue
    for (const c of core) {
      const near = fe >= c.start - FLANK_BP && fs <= c.end + FLANK_BP
      if (!near) continue
      if (fe - fs + 1 <= coreLen) extra.push({ start: fs, end: fe })
      else if (fe >= c.start && fs <= c.end) extra.push({ start: Math.max(fs, c.start), end: Math.min(fe, c.end) })
    }
  }
  return [...core, ...extra]
})
const unalignedCount = computed(() => props.reads.length - alignedReads.value.length)

// ==================== R/E/V 三层色带（goal v3 W13） ====================
const GAP_CAUSE_COLORS: Record<string, string> = {
  beyond_primer: '#e67e22',     // 引物够不到 → 橙（补引物可解）
  read_shortfall: '#c0392b',    // 应测到没测到 → 红（优先复核）
  unknown: '#8e44ad',           // 归因不明 → 紫
}

/** R 主段带 + 声明时范围外灰段（1..L 中不在 R ranges 的部分） */
const requiredBand = computed(() => {
  const L = props.referenceLength
  const sc = props.scope
  if (!sc?.declared) return []
  const items = (sc.ranges ?? []).map(([s, e], i) => ({
    id: `r${i}`, start: s, end: e, color: '#4a90d9', className: 'map-axis-req',
    tooltip: { title: '需测范围', lines: [`元件：${(sc.elements ?? []).join('、') || '—'}（${s}–${e}）`] },
  }))
  // 范围外置灰：把 R 覆盖的 bp 集合取补集（普通段逐段；跨原点段按两段计）
  const covered = new Set<number>()
  for (const [s, e] of (sc.ranges ?? [])) {
    if (s <= e) for (let p = s; p <= e; p++) covered.add(p)
    else { for (let p = s; p <= L; p++) covered.add(p); for (let p = 1; p <= e; p++) covered.add(p) }
  }
  const out: { id: string; start: number; end: number; color: string; className: string }[] = []
  let run: number | null = null
  for (let p = 1; p <= L + 1; p++) {
    const inCov = covered.has(p)
    if (!inCov && run === null) run = p
    if (inCov && run !== null) {
      out.push({ id: `o${out.length}`, start: run, end: p - 1, color: '#d5d8dc', className: 'map-axis-out' })
      run = null
    }
  }
  return [...out, ...items]
})

/** V 带（verified 口径；旧记录回退实测）+ 缺口段按 cause 着色 */
const verifiedBand = computed(() => {
  const v = props.verifiedRanges.length ? props.verifiedRanges : props.coverageRanges
  const items = v.map(([s, e], i) => ({
    id: `v${i}`, start: s, end: e, color: '#58b368', className: 'map-axis-cov',
    tooltip: { title: '验证覆盖', lines: [`${s}–${e}（双链可信/一致区）`] },
  }))
  for (const [i, g] of (props.coverageGaps ?? []).entries()) {
    const segs: [number, number][] = (g.ref_segments?.length ? g.ref_segments : [[g.start, g.end]]) as [number, number][]
    for (const [j, [s, e]] of segs.entries()) {
      items.push({
        id: `g${i}_${j}`, start: s, end: e,
        color: GAP_CAUSE_COLORS[(g as { cause?: string }).cause ?? 'unknown'] ?? '#8e44ad',
        className: 'map-axis-gap',
        tooltip: {
          title: '覆盖缺口',
          lines: [`${s}–${e}（${g.length}bp）`,
                  `原因：${({ beyond_primer: '引物够不到', read_shortfall: '应测到未测到', unknown: '归因不明' } as Record<string, string>)[(g as { cause?: string }).cause ?? 'unknown'] ?? '归因不明'}`],
        },
      })
    }
  }
  return items
})

// ==================== 结果位点标注（用户 2026-11-18：图要呈现测序结果） ====================
/** 判定条目 kind → 图上短标签 */
const SITE_KIND_LABEL: Record<string, string> = {
  mixed_variant: '疑似混合', mixed_sample: '疑似混合', unresolved_site: '双峰待核', confirmed_variant: '确证差异',
  critical_element_unverified: '未完全验证', critical_element_variant: '元件差异', orf_annotation_suspect: 'ORF 边界待核',
  dye_blob: '染料团', poly_undetermined: 'poly 待定', scope_read_shortfall: '应测未测', scope_beyond_primer: '引物未覆盖',
}
const UNRESOLVED_KIND_LABEL: Record<string, string> = {
  mixed_uncorroborated: '双峰', corroboration_conflict: '互检冲突', n_call: 'N', iupac: '简并碱基',
  out_of_zone_diff: '可信区外差异', poly_undetermined: 'poly 待定', dropout: '信号缺失',
}
const SEV_COLOR = { blocking: '#c0392b', warn: '#d97706', info: '#9ca3af' } as const
interface SiteMark {
  start: number; end: number; label: string; sev: keyof typeof SEV_COLOR; lines: string[]; reads: string[]
  /** 决定该位点编号排序的判定种类（同位点多条时取最严重的） */
  kind: string; element?: string | null; action?: string
}
/** 问题排序：决定结论的在前（混合 > 确证差异 > 双峰待核 > 元件未验证 > 覆盖缺口），同级按位置 */
const KIND_RANK: Record<string, number> = {
  mixed_variant: 0, mixed_sample: 0, confirmed_variant: 1, critical_element_variant: 1, unresolved_site: 2,
  critical_element_unverified: 3, scope_read_shortfall: 4, scope_beyond_primer: 5,
}
const kindRank = (k: string) => KIND_RANK[k] ?? 6
/** 参考注释类（与测序无关）：灰色 i，不计入需处理 */
const ANNOTATION_KINDS = new Set(['orf_annotation_suspect'])
/** 每个参考位点/区段一条：blocking 优先；同位点多条合并说明 */
const siteMarks = computed<SiteMark[]>(() => {
  const by = new Map<string, SiteMark>()
  const add = (start: number, end: number, label: string, sev: SiteMark['sev'], line: string, reads: string[],
    kind: string, element?: string | null, action?: string) => {
    const k = `${start}-${end}`
    const cur = by.get(k)
    const rank = { blocking: 2, warn: 1, info: 0 }
    if (!cur) { by.set(k, { start, end, label, sev, lines: [line], reads: [...reads], kind, element, action }); return }
    if (!cur.lines.includes(line)) cur.lines.push(line)
    for (const r of reads) if (!cur.reads.includes(r)) cur.reads.push(r)
    if (rank[sev] > rank[cur.sev] || (rank[sev] === rank[cur.sev] && kindRank(kind) < kindRank(cur.kind))) {
      Object.assign(cur, { sev, label, kind, element, action })
    }
  }
  const v = props.verdict
  for (const [list, sev] of [[v?.blocking ?? [], 'blocking'], [v?.info ?? [], 'info']] as const) {
    for (const it of list) {
      if (it.ref_start == null || it.ref_start < 1) continue
      if (it.kind === 'primer_unverified') continue
      const s = it.ref_start
      const e = it.ref_end ?? s
      const name = SITE_KIND_LABEL[it.kind] ?? it.kind
      const where = s === e ? `${s}` : (it.element ? String(it.element) : `${s}–${e}`)
      add(s, e, `${where} ${name}`, sev === 'blocking' ? 'blocking' : 'info', it.detail, it.refs ?? [], it.kind, it.element, it.action)
    }
  }
  for (const u of props.unresolvedSites) {
    const k = `${u.ref_pos}-${u.ref_pos}`
    if (by.has(k)) { const c = by.get(k)!; for (const r of u.reads) if (!c.reads.includes(r)) c.reads.push(r); continue }
    add(u.ref_pos, u.ref_pos, `${u.ref_pos} ${UNRESOLVED_KIND_LABEL[u.kind] ?? u.kind}`, 'info', u.detail, u.reads, u.kind)
  }
  return [...by.values()].sort((a, b) => a.start - b.start)
})
/** 与差异刻块同位点的结果条目并入差异刻块（不重复画） */
const variantPos = computed(() => new Set(props.variants.map((v) => v.ref_pos)))
const siteAt = computed(() => new Map(siteMarks.value.filter((m) => m.start === m.end).map((m) => [m.start, m])))

// ==================== read 测序情况（用户 2026-11-18：一眼看出哪里有情况） ====================
/** 逐碱基质量分档：≥30 实色；20–29 半淡；<20 很淡；可信区外斜线灰 */
const READ_FWD = '#5b86c9'   // 正向：柔和蓝
const READ_REV = '#8476c9'   // 反向：柔和紫
const Q_HI = 30
const Q_LO = 20
/** 比对列 → 参考坐标（环状折回）+ 质量 + 是否错配/插入缺失 */
function readColumns(r: ReadRow): { pos: number; q: number; mismatch: boolean; indel: boolean }[] {
  const av = r.alignment_view as (ReadRow['alignment_view'] & { q_aligned?: number[] }) | null | undefined
  if (!av) return []
  const L = props.referenceLength
  const fold = (p: number) => (L > 0 && p > L ? p - L : p)
  const out: { pos: number; q: number; mismatch: boolean; indel: boolean }[] = []
  let pos = av.ref_start
  for (let i = 0; i < av.ref_aligned.length; i++) {
    const rb = av.ref_aligned[i]
    const qb = av.read_aligned[i]
    const q = Number(av.q_aligned?.[i] ?? 40)
    if (rb !== '-') {
      out.push({ pos: fold(pos), q, mismatch: qb !== '-' && qb !== rb, indel: qb === '-' })
      pos++
    } else if (qb !== '-' && out.length) {
      out[out.length - 1].indel = true
    }
  }
  return out
}
/** read 坐标 → 参考坐标（忽略 indel 的线性换算；反向 read 坐标按原始方向计） */
function readToRef(r: ReadRow, p: number): number {
  const qs = (r as { query_start?: number }).query_start ?? 0
  const L = props.referenceLength
  const v = r.direction === '+' ? r.ref_start + (p - qs) : r.ref_end - (p - qs)
  return L > 0 ? ((v - 1 + L) % L) + 1 : v
}
/** 可信区（reliable_zone，修剪后坐标）换到参考坐标 */
function reliableRef(r: ReadRow): [number, number] | null {
  const z = (r as { reliable_zone?: { start: number; end: number } | null }).reliable_zone
  if (!z) return null
  const a = readToRef(r, z.start)
  const b = readToRef(r, z.end)
  return [Math.min(a, b), Math.max(a, b)]
}
function inZone(zone: [number, number] | null, p: number) {
  return !zone || (p >= zone[0] && p <= zone[1])
}
type QClass = 'hi' | 'mid' | 'lo' | 'out'
function readQuality(r: ReadRow): { start: number; end: number; cls: QClass }[] {
  const zone = reliableRef(r)
  const runs: { start: number; end: number; cls: QClass }[] = []
  for (const c of readColumns(r)) {
    // 可信区外整体淡化（仍画出位置，不加斜线，不抢眼；其中的双峰/错配不标）
    const cls: QClass = !inZone(zone, c.pos) ? 'out' : c.q >= Q_HI ? 'hi' : c.q >= Q_LO ? 'mid' : 'lo'
    const last = runs[runs.length - 1]
    if (last && last.cls === cls && c.pos === last.end + 1) last.end = c.pos
    else runs.push({ start: c.pos, end: c.pos, cls })
  }
  return runs
}
const Q_BAND: Record<Exclude<QClass, 'hi'>, { color: string; opacity: number; hatch?: boolean }> = {
  mid: { color: '#ffffff', opacity: 0.35 },
  lo: { color: '#ffffff', opacity: 0.65 },
  out: { color: '#ffffff', opacity: 0.72 },
}
type MixedRow = { pos: number; ratio: number }
function readTicks(r: ReadRow): { pos: number; color: string; width: number }[] {
  const ticks: { pos: number; color: string; width: number }[] = []
  const zone = r.wraps_origin ? null : reliableRef(r)
  for (const m of ((r as { mixed_detail?: MixedRow[] }).mixed_detail ?? [])) {
    // 只画可信区内的明显双峰（次峰 ≥30%）；弱双峰留给结果位点/峰图，不在 read 上堆线
    const p = readToRef(r, m.pos)
    if (m.ratio >= 0.3 && inZone(zone, p)) ticks.push({ pos: p, color: '#f59e0b', width: 2.4 })
  }
  for (const c of readColumns(r)) if (c.indel && inZone(zone, c.pos)) ticks.push({ pos: c.pos, color: '#111827', width: 2.4 })
  return ticks
}
function readBadge(r: ReadRow): string {
  const x = r as { grade?: string; q20_ratio?: number; mixed_count?: number }
  const parts: string[] = []
  if (x.grade) parts.push(x.grade)
  if (x.q20_ratio != null) parts.push(`Q20 ${(x.q20_ratio * 100).toFixed(0)}%`)
  // 一致性与双峰数在悬停说明里；条内只留等级与 Q20（设计稿 v6）
  return parts.join(' · ')
}

/** 测序确认色带：逐参考位点看高质量（可信区内且 Q≥20）的 read 方向与一致性 */
type ConfClass = 'conflict' | 'both' | 'single' | 'low'
const CONF_COLOR: Record<ConfClass, string> = { conflict: '#dc2626', both: '#15803d', single: '#86efac', low: '#fcd34d' }
const CONF_TEXT: Record<ConfClass, string> = { conflict: '有差异/双峰待核', both: '双向高质量确认', single: '单向高质量', low: '仅低质量覆盖' }
const confirmRuns = computed(() => {
  const per = new Map<number, { fwd: boolean; rev: boolean; any: boolean; bad: boolean }>()
  const get = (p: number) => { let v = per.get(p); if (!v) { v = { fwd: false, rev: false, any: false, bad: false }; per.set(p, v) } return v }
  for (const r of alignedReads.value) {
    const zone = reliableRef(r)
    for (const c of readColumns(r)) {
      if (!inZone(zone, c.pos)) continue
      const v = get(c.pos)
      v.any = true
      const hq = c.q >= Q_LO
      if (hq) { if (r.direction === '+') v.fwd = true; else v.rev = true }
      if (hq && (c.mismatch || c.indel)) v.bad = true
    }
    for (const m of ((r as { mixed_detail?: MixedRow[] }).mixed_detail ?? [])) {
      const p = readToRef(r, m.pos)
      if (m.ratio >= 0.3 && inZone(zone, p)) get(p).bad = true
    }
  }
  for (const m of siteMarks.value) if (m.sev === 'blocking' && m.start === m.end && per.has(m.start)) get(m.start).bad = true
  const runs: { start: number; end: number; cls: ConfClass }[] = []
  for (const p of [...per.keys()].sort((a, b) => a - b)) {
    const v = per.get(p)!
    const cls: ConfClass = v.bad ? 'conflict' : v.fwd && v.rev ? 'both' : v.fwd || v.rev ? 'single' : 'low'
    const last = runs[runs.length - 1]
    if (last && last.cls === cls && p === last.end + 1) last.end = p
    else runs.push({ start: p, end: p, cls })
  }
  return runs
})

// ==================== 问题编号与清单（用户 2026-11-19 设计稿 v6） ====================
function varChange(v: Variant): string {
  const change = v.type === 'substitution' ? `${v.ref_base}>${v.alt_base}` : v.type === 'insertion' ? `ins${v.alt_base}` : `del${v.ref_base}`
  const aa = v.aa_change ? ` ${String(v.aa_change).split(':').pop()}` : ''
  return `${change}${aa}`
}
/** 参考坐标 → read 坐标（与 readToRef 互逆，忽略 indel） */
function refToRead(r: ReadRow, ref: number): number {
  const qs = (r as { query_start?: number }).query_start ?? 0
  return r.direction === '+' ? qs + (ref - r.ref_start) : qs + (r.ref_end - ref)
}
const NEAR_EDGE_BP = 30
/** 位点/区段落在哪条 read 的第几位、与可信区的关系（贴近或越出可信区时才写） */
function readNote(m: SiteMark): string {
  const notes: string[] = []
  const rs = alignedReads.value.filter((r) => !r.wraps_origin
    && (m.reads.length ? m.reads.includes(r.filename) : r.ref_start <= m.end && r.ref_end >= m.start))
  for (const r of rs) {
    const z = (r as { reliable_zone?: { start: number; end: number } | null }).reliable_zone
    if (!z) continue
    const a = refToRead(r, m.start)
    const b = refToRead(r, m.end)
    const lo = Math.min(a, b)
    const hi = Math.max(a, b)
    const nm = shortName(r.filename)
    if (lo === hi) {
      if (lo < z.start || lo > z.end) notes.push(`${nm} 第 ${lo} 位，在可信区 ${z.start}–${z.end} 外`)
      else if (lo - z.start <= NEAR_EDGE_BP) notes.push(`${nm} 第 ${lo} 位，可信区从第 ${z.start} 位起`)
      else if (z.end - lo <= NEAR_EDGE_BP) notes.push(`${nm} 第 ${lo} 位，可信区到第 ${z.end} 位止`)
    } else {
      if (lo < z.start) { const e = Math.min(hi, z.start - 1); notes.push(`${nm} 第 ${lo}–${e} 位（${e - lo + 1} bp）在可信区外，可信区从第 ${z.start} 位起`) }
      if (hi > z.end) { const s0 = Math.max(lo, z.end + 1); notes.push(`${nm} 第 ${s0}–${hi} 位（${hi - s0 + 1} bp）在可信区外，可信区到第 ${z.end} 位止`) }
    }
  }
  return notes.length ? `（${notes.join('；')}）` : ''
}
function problemTitle(m: SiteMark): string {
  const pos = m.start === m.end ? `${m.start}` : `${m.start}–${m.end}`
  const v = m.start === m.end ? props.variants.find((x) => x.ref_pos === m.start) : undefined
  if (m.kind === 'mixed_variant' || m.kind === 'mixed_sample') return `${pos}${v ? ` ${varChange(v)}` : ''} 疑似混合`
  if (m.kind === 'confirmed_variant' && v) return `${pos} ${varChange(v)} 确证差异`
  if (m.kind === 'critical_element_unverified') {
    const ev = (props.verdict?.element_verdicts ?? []).find((e) => e.name === m.element)
    const nm = `${m.element ?? ''}${ev?.type ? ` ${ev.type}` : ''}`
    return ev?.coverage != null ? `${pos} ${nm} 仅验证 ${Math.floor(ev.coverage)}%` : `${pos} ${nm} 未完全验证`
  }
  if (m.kind === 'orf_annotation_suspect') {
    const f = props.features.find((x) => x.start === m.start || x.end === m.start)
    return `${pos} 参考注释：${f ? `${f.name} ` : 'CDS '}${f && f.end === m.start && f.start !== m.start ? '终点' : '起点'}存疑`
  }
  return `${pos} ${SITE_KIND_LABEL[m.kind] ?? m.kind}`
}
interface Problem { no: string; mark: SiteMark; title: string; reason: string; action: string; color: string; annotation: boolean }
const problems = computed<Problem[]>(() => {
  const blocking = siteMarks.value.filter((m) => m.sev === 'blocking')
  const real = blocking.filter((m) => !ANNOTATION_KINDS.has(m.kind))
    .sort((a, b) => kindRank(a.kind) - kindRank(b.kind) || a.start - b.start)
  const ann = blocking.filter((m) => ANNOTATION_KINDS.has(m.kind))
  return [
    ...real.map((m, i) => ({
      no: String(i + 1), mark: m, title: problemTitle(m), reason: `${m.lines.join('；')}${readNote(m)}`,
      action: m.action ?? '核对峰图', color: kindRank(m.kind) <= 2 ? '#dc2626' : '#d97706', annotation: false,
    })),
    ...ann.map((m) => ({
      no: 'i', mark: m, title: problemTitle(m), reason: `与测序无关；${m.lines.join('；')}`,
      action: '核对参考文件的 CDS 标注', color: '#94a3b8', annotation: true,
    })),
  ]
})
const problemCount = computed(() => problems.value.filter((p) => !p.annotation).length)
const annotationCount = computed(() => problems.value.length - problemCount.value)
/** 提示级（未计入判定）：默认收起，展开后图上画灰色刻块 */
const hints = computed(() => siteMarks.value.filter((m) => m.sev !== 'blocking'))
const showHints = ref(false)
const pinnedPos = computed(() => new Set(problems.value.filter((p) => p.mark.start === p.mark.end).map((p) => p.mark.start)))

/** 区段底带：与 read 重叠的 CDS（短 CDS 如标签另用紫色），贯穿 read 层到参考层 */
const regionItems = computed(() => {
  const segs = alignedReads.value.flatMap((r) => readSegments(r))
  return shownFeatures.value
    .filter((f) => normType(f.type) === 'cds' && f.start <= f.end && segs.some(([s, e]) => f.end >= s && f.start <= e))
    .map((f, i) => {
      const short = f.end - f.start + 1 < 90
      return {
        id: String(i), start: f.start, end: f.end, color: short ? '#7c3aed' : '#2563eb',
        label: short ? `${f.name} ${f.start}–${f.end}` : `CDS ${f.name} · ${f.start}–${f.end}`,
        className: 'map-region-cds', tooltip: { title: f.name, lines: [`${f.start}–${f.end}（${f.end - f.start + 1} bp）`] },
      }
    })
})
function jumpTo(m: SiteMark) {
  emit('jump-site', { ref_pos: m.start, read: m.reads[0] })
}

const tracks = computed<MapTrack[]>(() => [
  {
    id: 'reads', kind: 'arrows', height: 22,
    items: [...alignedReads.value]
      .sort((x, y) => x.ref_start - y.ref_start || x.index - y.index)
      .map((r) => {
        const segs = readSegments(r).map(([s, e]) => ({ start: s, end: e }))
        const fwd = r.direction === '+'
        return {
          id: String(r.index),
          start: segs[0].start,
          end: segs[segs.length - 1].end,
          segments: segs.length > 1 ? segs : undefined,
          strand: (fwd ? 1 : -1) as 1 | -1,
          label: shortName(r.filename),
          color: fwd ? READ_FWD : READ_REV,
          stroke: fwd ? '#3d64a8' : '#5b4fa3',
          marks: readDiffPositions(r).filter((p) => inZone(r.wraps_origin ? null : reliableRef(r), p)),
          bands: readQuality(r).filter((b) => b.cls !== 'hi').map((b) => ({ start: b.start, end: b.end, ...Q_BAND[b.cls as Exclude<QClass, 'hi'>] })),
          ticks: readTicks(r),
          badge: readBadge(r),
          className: fwd ? 'map-arrow-fwd' : 'map-arrow-rev',
          tooltip: {
            title: r.filename,
            lines: [
              `${readSpanText(r, '-')}${r.wraps_origin ? '，跨越环状参考原点' : ''}`,
              `${fwd ? '正向' : '反向'} · 一致性 ${(r.identity * 100).toFixed(1)}%`,
              '点击在比对峰图中查看',
            ],
          },
        }
      }),
  },
  {
    // R 轴（goal v3 W13）：需测范围主段（蓝）——junctions 不渲染（W8 口径：
    // 头行/区间渲染只用 ranges）；声明时范围外区段置灰
    id: 'required', kind: 'axis',
    items: requiredBand.value,
  },
  {
    // E 轴：引物理论覆盖（黄；旧记录无 expected_ranges 时整轨省略）
    id: 'expected', kind: 'axis',
    items: props.expectedRanges.map(([s, e], i) => ({ id: String(i), start: s, end: e, color: '#e0b64d', className: 'map-axis-exp' })),
  },
  {
    // V 轴：验证覆盖（绿）+ 缺口段按原因着色（W6 cause）
    id: 'verified', kind: 'axis',
    items: verifiedBand.value,
  },
  {
    id: 'variants', kind: 'marks',
    // 已有问题编号的差异不再重复画刻块（同一信息只编码一次，设计稿 v6）
    items: props.variants.flatMap((v, i) => {
      if (pinnedPos.value.has(v.ref_pos)) return []
      const site = siteAt.value.get(v.ref_pos)
      return [{
        id: String(i), start: v.ref_pos, end: v.ref_pos, color: '#c0392b', className: 'map-var-tick',
        markLabel: `${v.ref_pos} ${varChange(v)}${site ? ` · ${site.label.replace(/^\S+\s/, '')}` : ''}`,
        priority: 100,
        tooltip: {
          title: `${v.ref_pos} ${v.ref_base}→${v.alt_base}`,
          lines: [`${v.type === 'substitution' ? '替换' : v.type === 'insertion' ? '插入' : '缺失'}，${v.support_reads || 1} 条 read`,
                  ...(site ? site.lines : []), '点击在比对峰图中查看'],
        },
      }]
    }),
  },
  {
    id: 'confirm', kind: 'strip', label: '测序确认',
    items: confirmRuns.value.map((c, i) => ({
      id: String(i), start: c.start, end: c.end, color: CONF_COLOR[c.cls], className: `map-confirm map-confirm-${c.cls}`,
      tooltip: { title: CONF_TEXT[c.cls], lines: [c.start === c.end ? `${c.start}` : `${c.start}–${c.end}（${c.end - c.start + 1} bp）`] },
    })),
  },
  {
    // 问题编号（设计稿 v6）：顶部圆标 + 竖向引导线，编号按严重程度；参考注释为灰色 i
    id: 'sites', kind: 'pins',
    items: problems.value.map((p, i) => ({
      id: String(i), start: p.mark.start, end: p.mark.end, color: p.color, markLabel: p.no,
      className: `map-pin ${p.annotation ? 'map-pin-ann' : 'map-pin-issue'}`,
      tooltip: { title: `${p.no === 'i' ? 'ⓘ' : p.no} ${p.title}`, lines: [p.reason, `→ ${p.action}`, '点击在比对峰图中查看'] },
    })),
  },
  {
    id: 'regions', kind: 'regions', items: regionItems.value,
  },
  {
    // 提示级位点：展开时才画灰色刻块（不写字）
    id: 'hints', kind: 'marks',
    items: showHints.value ? hints.value.filter((m) => !variantPos.value.has(m.start)).map((m, i) => ({
      id: String(i), start: m.start, end: m.end, color: SEV_COLOR.info, className: 'map-site-tick map-site-info',
      tooltip: { title: m.label, lines: [...m.lines, '点击在比对峰图中查看'] },
    })) : [],
  },
])

function onTrackClick(trackId: string, itemId: string) {
  if (trackId === 'reads') emit('open-read', Number(itemId))
  else if (trackId === 'sites') {
    const p = problems.value[Number(itemId)]
    if (p) jumpTo(p.mark)
  } else if (trackId === 'hints') {
    const m = hints.value.filter((x) => !variantPos.value.has(x.start))[Number(itemId)]
    if (m) jumpTo(m)
  } else if (trackId === 'variants') {
    const v = props.variants[Number(itemId)]
    if (v) emit('jump-variant', v)
  }
}

/** 点击元件 → 加入/移出需测范围（Panel 重组 scope 文本后经 W11 端点重算） */
function onFeatureClick(featureId: string) {
  const f = doc.value.features.find((x) => x.id === featureId)
  if (f?.name) emit('toggle-feature-scope', f.name)
}

// 坐标轴拖拽框选（LinearMap range-select 事件）→ 提示条 + 加入范围
const selectionRange = ref<[number, number] | null>(null)
const selectionText = computed(() => {
  const r = selectionRange.value
  return r ? `已框选 ${r[0]}–${r[1]}（${r[1] - r[0] + 1}bp）` : ''
})
function onRangeSelect(r: { start: number; end: number }) {
  selectionRange.value = [Math.max(1, Math.round(r.start)), Math.min(props.referenceLength, Math.round(r.end))]
}
function addSelectionToScope() {
  const r = selectionRange.value
  if (r) {
    emit('add-scope-range', r)
    selectionRange.value = null
  }
}

/** 图例里出现的特征类型（按出现顺序去重） */
const legendTypes = computed(() => {
  const seen = new Set<FeatureType>()
  return doc.value.features.filter((f) => !seen.has(f.type) && seen.add(f.type))
    .map((f) => ({ type: FEATURE_TYPE_LABELS[f.type], color: FEATURE_COLORS[f.type] }))
})

defineExpose({ tracks, doc })
</script>

<template>
  <div class="map-box">
    <h4 class="section-title">匹配简图<span class="map-sub">（read 落位与参考特征一览；点击 read 或红块在比对峰图中查看，点击元件加入/移出验证范围，沿坐标轴拖拽框选区间；Ctrl+滚轮缩放）</span>
      <label class="map-dedup-toggle"
             title="图谱文件里同名且位置重叠或相邻（≤50bp）的重复注释合并为一条显示（如成对的 5 UTR、邻接的 miscellaneous），方向不敏感；相距远的同名特征不受影响；默认按文件原样显示">
        <input v-model="dedupMapFeats" type="checkbox" /> 特征去重
      </label>
      <span v-if="dedupMergedCount > 0" class="map-sub">已合并 {{ dedupMergedCount }} 条重复注释</span>
      <label class="map-dedup-toggle"
             title="read 覆盖区及其两侧元件放大显示（约占图宽 60%），其余区段按比例压缩但保留，主轴上以断裂符标出；关闭则全长等比例显示">
        <input v-model="focusReads" type="checkbox" /> 突出 read 覆盖
      </label>
    </h4>
    <p v-if="selectionText" class="scope-selection-bar">
      {{ selectionText }}
      <button type="button" class="scope-btn sm" @click="addSelectionToScope">加入验证范围</button>
      <button type="button" class="scope-btn sm ghost" @click="selectionRange = null">取消</button>
    </p>
    <LinearMap :doc="doc" :tracks="tracks" :show-enzymes="false"
               :focus="focusRanges" :focus-share="FOCUS_SHARE"
               :lanes="{ reads: '测序 read', reference: '参考序列' }"
               @track-click="onTrackClick" @feature-click="onFeatureClick"
               @range-select="onRangeSelect" />
    <p class="map-legend hint lg-reads">
      <b>read</b>
      <span class="lg-sw" :style="{ background: READ_FWD }"></span>正向
      <span class="lg-sw" :style="{ background: READ_REV }"></span>反向（颜色越浅质量越低，两端最浅为可信区外，不参与判定）
      <span class="lg-dot">○</span> 错配
      <span class="lg-tick" style="background:#f59e0b"></span>双峰
      <span class="lg-tick" style="background:#111827"></span>插入/缺失 ·
      <b>测序确认</b>
      <span class="lg-sw" style="background:#15803d"></span>双向
      <span class="lg-sw" style="background:#86efac"></span>单向
      <span class="lg-sw" style="background:#fcd34d"></span>低质量
      <span class="lg-sw" style="background:#dc2626"></span>有情况
    </p>
    <p class="map-legend hint">
      <span class="lg-req">▬ 需测范围</span> ·
      <span class="lg-exp">▬ 引物理论覆盖</span> ·
      <span class="lg-cov">▬ 验证覆盖</span> ·
      <span class="lg-gap">▮ 缺口</span>（橙=引物够不到、红=应测未测、紫=不明）
      <template v-if="focusRanges.length"> · ⫽ 压缩显示的区段</template>
      <template v-if="unalignedCount"> · {{ unalignedCount }} 条 read 未比对到参考（见下方 read 表）</template>
      <template v-if="legendTypes.length"> · 特征
        <span v-for="t in legendTypes" :key="t.type" class="lg-type"><i :style="{ background: t.color }"></i>{{ t.type }}</span>
      </template>
    </p>
    <div v-if="problems.length || hints.length" class="map-problems">
      <p v-if="problems.length" class="mp-head">需处理 {{ problemCount }} 处<template v-if="annotationCount"> · 参考注释 {{ annotationCount }} 处</template></p>
      <ol class="mp-list">
        <li v-for="p in problems" :key="p.no + p.mark.start" :class="['mp-row', { 'mp-ann': p.annotation }]" @click="jumpTo(p.mark)">
          <span class="mp-no" :style="{ background: p.color }">{{ p.no }}</span>
          <span class="mp-text"><b class="mp-title">{{ p.title }}</b><span class="mp-why"> · {{ p.reason }} → {{ p.action }}</span></span>
        </li>
      </ol>
      <p v-if="hints.length" class="mp-hint">
        另有 {{ hints.length }} 处提示级（弱双峰/近端噪声，未计入判定），
        <button type="button" class="mp-toggle" @click="showHints = !showHints">{{ showHints ? '收起' : '展开' }}</button>
      </p>
      <ul v-if="showHints" class="mp-hints">
        <li v-for="m in hints" :key="m.start + '-' + m.end" class="mp-hint-row" @click="jumpTo(m)">{{ m.label }} · {{ m.lines[0] }}</li>
      </ul>
    </div>
  </div>
</template>

<style scoped>
.map-box { background: var(--bg-primary, #fff); border: 1px solid var(--border-color, #eee); border-radius: 10px; padding: 0.75rem 1rem; }
.section-title { font-size: 0.95rem; margin: 0 0 0.5rem; }
.hint { color: #999; font-size: 0.85rem; }
.map-sub { font-size: 0.75rem; color: #999; font-weight: 400; }
.map-dedup-toggle {
  font-size: 0.75rem; color: #555; font-weight: 400; margin-left: 0.75rem;
  display: inline-flex; align-items: center; gap: 0.25rem; cursor: pointer; user-select: none;
}
.lg-read { color: #b03a2e; font-weight: 600; }
.lg-dot { color: #c0392b; font-weight: 600; }
.lg-reads { margin-bottom: 0; }
.lg-sw { display: inline-block; width: 14px; height: 8px; border-radius: 2px; margin: 0 3px 0 8px; vertical-align: 0; }
.lg-hatch { background: repeating-linear-gradient(45deg, #d1d5db 0 2px, #9ca3af 2px 4px); }
.lg-tick { display: inline-block; width: 3px; height: 11px; margin: 0 3px 0 8px; vertical-align: -1px; }
.lg-var { color: #c0392b; font-weight: 700; }
.lg-cov { color: #58b368; font-weight: 700; }
.lg-req { color: #4a90d9; font-weight: 700; }
.lg-exp { color: #e0b64d; font-weight: 700; }
.lg-gap { color: #c0392b; font-weight: 700; }
.scope-selection-bar {
  display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap;
  background: #eef4fb; border: 1px solid #b9d3ee; border-radius: 6px;
  padding: 0.3rem 0.6rem; margin: 0 0 0.4rem; font-size: 0.85rem; color: #2c5f8a;
}
.lg-type { margin-left: 6px; white-space: nowrap; }
.map-problems { border-top: 1px solid #e2e8f0; margin-top: 0.4rem; padding-top: 0.4rem; font-size: 0.85rem; }
.mp-head { margin: 0 0 0.3rem; color: #475569; font-weight: 600; }
.mp-list { list-style: none; margin: 0; padding: 0; }
.mp-row { display: flex; gap: 0.5rem; align-items: baseline; padding: 0.2rem 0; cursor: pointer; line-height: 1.5; }
.mp-row:hover .mp-title { text-decoration: underline; }
.mp-row.mp-ann { margin-top: 0.3rem; color: #475569; }
.mp-row.mp-ann .mp-title { font-weight: 400; }
.mp-no { flex: none; display: inline-flex; align-items: center; justify-content: center; width: 18px; height: 18px; border-radius: 50%;
  color: #fff; font-size: 11px; font-weight: 700; transform: translateY(3px); }
.mp-title { color: #0f172a; }
.mp-ann .mp-title { color: #475569; }
.mp-why { color: #64748b; }
.mp-hint { margin: 0.3rem 0 0 26px; color: #94a3b8; }
.mp-toggle { border: none; background: none; color: #2563eb; cursor: pointer; padding: 0; font-size: inherit; }
.mp-hints { margin: 0.2rem 0 0 26px; padding: 0; list-style: none; color: #94a3b8; }
.mp-hint-row { cursor: pointer; padding: 0.1rem 0; }
.lg-type i { display: inline-block; width: 9px; height: 9px; border-radius: 2px; margin-right: 3px; vertical-align: -1px; }
</style>
