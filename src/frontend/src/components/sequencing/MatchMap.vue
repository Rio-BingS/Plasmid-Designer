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
import type { ScopeInfo, SequencingAnalysis } from '@/api'
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
}>(), { circular: undefined, scope: null, expectedRanges: () => [], verifiedRanges: () => [], coverageGaps: () => [] })

const emit = defineEmits<{
  (e: 'open-read', index: number): void
  (e: 'jump-variant', v: Variant): void
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
    items: props.variants.map((v, i) => ({
      id: String(i), start: v.ref_pos, end: v.ref_pos, color: '#c0392b', className: 'map-var-tick',
      tooltip: {
        title: `${v.ref_pos} ${v.ref_base}→${v.alt_base}`,
        lines: [`${v.type === 'substitution' ? '替换' : v.type === 'insertion' ? '插入' : '缺失'}，${v.support_reads || 1} 条 read`, '点击在比对峰图中查看'],
      },
    })),
  },
])

function onTrackClick(trackId: string, itemId: string) {
  if (trackId === 'reads') emit('open-read', Number(itemId))
  else if (trackId === 'variants') {
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
    </h4>
    <p v-if="selectionText" class="scope-selection-bar">
      {{ selectionText }}
      <button type="button" class="scope-btn sm" @click="addSelectionToScope">加入验证范围</button>
      <button type="button" class="scope-btn sm ghost" @click="selectionRange = null">取消</button>
    </p>
    <LinearMap :doc="doc" :tracks="tracks" :show-enzymes="false"
               @track-click="onTrackClick" @feature-click="onFeatureClick"
               @range-select="onRangeSelect" />
    <p class="map-legend hint">
      <span class="lg-req">▬ 蓝段 = 需测范围（点击元件增删；灰段 = 范围外未评估）</span> ·
      <span class="lg-exp">▬ 黄段 = 引物理论覆盖</span> ·
      <span class="lg-cov">▬ 绿段 = 验证覆盖</span> ·
      <span class="lg-gap">▮ 缺口按原因着色（橙=引物够不到、红=应测到未测到、紫=归因不明）</span> ·
      <span class="lg-read">▬ 测序 read（箭头=方向）</span> ·
      <span class="lg-dot">○ 差异位点</span> ·
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
