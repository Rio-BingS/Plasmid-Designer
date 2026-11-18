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
interface SiteMark { start: number; end: number; label: string; sev: keyof typeof SEV_COLOR; lines: string[]; reads: string[] }
/** 每个参考位点/区段一条：blocking 优先；同位点多条合并说明 */
const siteMarks = computed<SiteMark[]>(() => {
  const by = new Map<string, SiteMark>()
  const add = (start: number, end: number, label: string, sev: SiteMark['sev'], line: string, reads: string[]) => {
    const k = `${start}-${end}`
    const cur = by.get(k)
    const rank = { blocking: 2, warn: 1, info: 0 }
    if (!cur) { by.set(k, { start, end, label, sev, lines: [line], reads: [...reads] }); return }
    if (!cur.lines.includes(line)) cur.lines.push(line)
    for (const r of reads) if (!cur.reads.includes(r)) cur.reads.push(r)
    if (rank[sev] > rank[cur.sev]) { cur.sev = sev; cur.label = label }
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
      add(s, e, `${where} ${name}`, sev === 'blocking' ? 'blocking' : 'info', it.detail, it.refs ?? [])
    }
  }
  for (const u of props.unresolvedSites) {
    const k = `${u.ref_pos}-${u.ref_pos}`
    if (by.has(k)) { const c = by.get(k)!; for (const r of u.reads) if (!c.reads.includes(r)) c.reads.push(r); continue }
    add(u.ref_pos, u.ref_pos, `${u.ref_pos} ${UNRESOLVED_KIND_LABEL[u.kind] ?? u.kind}`, 'info', u.detail, u.reads)
  }
  return [...by.values()].sort((a, b) => a.start - b.start)
})
/** 与差异刻块同位点的结果条目并入差异刻块（不重复画） */
const variantPos = computed(() => new Set(props.variants.map((v) => v.ref_pos)))
const siteAt = computed(() => new Map(siteMarks.value.filter((m) => m.start === m.end).map((m) => [m.start, m])))
/** read 上的结果位点圆点：该 read 参与的结果位点（双峰/未决等），blocking 实心红、其余空心 */
function readSiteDots(r: ReadRow, diffs: number[]): { pos: number; color: string; filled?: boolean }[] {
  const out: { pos: number; color: string; filled?: boolean }[] = []
  for (const m of siteMarks.value) {
    if (m.start !== m.end || !m.reads.includes(r.filename) || diffs.includes(m.start)) continue
    // 箭头本身是深红：阻断位点用黄色实心点，提示位点用灰圈
    out.push({ pos: m.start, color: m.sev === 'blocking' ? '#facc15' : SEV_COLOR[m.sev], filled: m.sev === 'blocking' })
  }
  return out
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
          color: '#b03a2e',
          stroke: '#7e251c',
          marks: readDiffPositions(r),
          dots: readSiteDots(r, readDiffPositions(r)),
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
    items: props.variants.map((v, i) => {
      const site = siteAt.value.get(v.ref_pos)
      const change = v.type === 'substitution' ? `${v.ref_base}>${v.alt_base}` : v.type === 'insertion' ? `ins${v.alt_base}` : `del${v.ref_base}`
      const aa = v.aa_change ? ` ${String(v.aa_change).split(':').pop()}` : ''
      return {
        id: String(i), start: v.ref_pos, end: v.ref_pos, color: '#c0392b', className: 'map-var-tick',
        markLabel: `${v.ref_pos} ${change}${aa}${site ? ` · ${site.label.replace(/^\S+\s/, '')}` : ''}`,
        priority: 100,
        tooltip: {
          title: `${v.ref_pos} ${v.ref_base}→${v.alt_base}`,
          lines: [`${v.type === 'substitution' ? '替换' : v.type === 'insertion' ? '插入' : '缺失'}，${v.support_reads || 1} 条 read`,
                  ...(site ? site.lines : []), '点击在比对峰图中查看'],
        },
      }
    }),
  },
  {
    // 结果位点（判定 blocking/info + 未决位点）：刻块 + 文字标注；与差异同位点的已并入上一轨
    id: 'sites', kind: 'marks',
    items: siteMarks.value.filter((m) => !(m.start === m.end && variantPos.value.has(m.start))).map((m, i) => ({
      id: String(i), start: m.start, end: m.end, color: SEV_COLOR[m.sev], className: `map-site-tick map-site-${m.sev}`,
      markLabel: m.label, priority: m.sev === 'blocking' ? 50 : 10,
      tooltip: { title: m.label, lines: [...m.lines, ...(m.start === m.end ? ['点击在比对峰图中查看'] : [])] },
    })),
  },
])

function onTrackClick(trackId: string, itemId: string) {
  if (trackId === 'reads') emit('open-read', Number(itemId))
  else if (trackId === 'sites') {
    const m = siteMarks.value.filter((x) => !(x.start === x.end && variantPos.value.has(x.start)))[Number(itemId)]
    if (m) emit('jump-site', { ref_pos: m.start, read: m.reads[0] })
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
               @track-click="onTrackClick" @feature-click="onFeatureClick"
               @range-select="onRangeSelect" />
    <p class="map-legend hint">
      <span class="lg-req">▬ 蓝段 = 需测范围（点击元件增删；灰段 = 范围外未评估）</span> ·
      <span class="lg-exp">▬ 黄段 = 引物理论覆盖</span> ·
      <span class="lg-cov">▬ 绿段 = 验证覆盖</span> ·
      <span class="lg-gap">▮ 缺口按原因着色（橙=引物够不到、红=应测到未测到、紫=归因不明）</span> ·
      <span class="lg-read">▬ 测序 read（箭头=方向）</span> ·
      <span class="lg-dot">○ 差异位点</span> ·
      <span v-if="siteMarks.length"><span style="color:#c0392b">▮ 阻断位点</span>（read 上为黄点）/ <span style="color:#9ca3af">▮ 提示位点</span>（read 上为灰圈）；双峰、未决等结果文字标注在主轴上方</span> ·
      <span v-if="focusRanges.length">⫽ 断裂符 = 未覆盖区段压缩显示</span> ·
      <span class="lg-var">▮</span> 变异（点击跳峰图）
      <template v-if="unalignedCount"> · {{ unalignedCount }} 条 read 未比对到参考（见下方 read 表）</template>
      <template v-if="legendTypes.length"> · 特征
        <span v-for="t in legendTypes" :key="t.type" class="lg-type"><i :style="{ background: t.color }"></i>{{ t.type }}</span>
      </template>
    </p>
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
.lg-type i { display: inline-block; width: 9px; height: 9px; border-radius: 2px; margin-right: 3px; vertical-align: -1px; }
</style>
