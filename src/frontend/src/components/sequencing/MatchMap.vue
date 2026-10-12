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
import type { SequencingAnalysis } from '@/api'
import type { FeatureType, MapDocument, MapFeature } from '@/map/types'
import type { MapTrack } from '@/map/tracks'
import LinearMap from '@/map/components/LinearMap.vue'
import { FEATURE_COLORS, FEATURE_TYPE_LABELS } from '@/map/core/palette'
import { readSegments, readSpanText, shortName } from '@/utils/seqPanelModel'

type ReadRow = SequencingAnalysis['reads'][number]
type Feature = SequencingAnalysis['features'][number]
type Variant = SequencingAnalysis['variants'][number]

const props = withDefaults(defineProps<{
  reads: ReadRow[]
  variants: SequencingAnalysis['variants']
  features: Feature[]
  coverageRanges: [number, number][]
  referenceLength: number
  /** 参考是否按环状比对（旧记录无此字段时由跨原点 read 推断） */
  circular?: boolean
}>(), { circular: undefined })

const emit = defineEmits<{
  (e: 'open-read', index: number): void
  (e: 'jump-variant', v: Variant): void
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
    id: 'coverage', kind: 'axis',
    items: props.coverageRanges.map(([s, e], i) => ({ id: String(i), start: s, end: e, color: '#58b368', className: 'map-axis-cov' })),
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
    <h4 class="section-title">匹配简图<span class="map-sub">（read 落位与参考特征一览；点击 read 或红块在比对峰图中查看；Ctrl+滚轮缩放）</span>
      <label class="map-dedup-toggle"
             title="图谱文件里同名且位置重叠或相邻（≤50bp）的重复注释合并为一条显示（如成对的 5 UTR、邻接的 miscellaneous），方向不敏感；相距远的同名特征不受影响；默认按文件原样显示">
        <input v-model="dedupMapFeats" type="checkbox" /> 特征去重
      </label>
      <span v-if="dedupMergedCount > 0" class="map-sub">已合并 {{ dedupMergedCount }} 条重复注释</span>
    </h4>
    <LinearMap :doc="doc" :tracks="tracks" :show-enzymes="false" @track-click="onTrackClick" />
    <p class="map-legend hint">
      <span class="lg-read">▬ 测序 read（箭头=方向，点击在比对峰图中查看）</span> ·
      <span class="lg-dot">○ 差异位点</span> ·
      <span class="lg-var">▮</span> 变异（点击跳峰图） ·
      <span class="lg-cov">▬</span> 轴上绿段 = 已测序覆盖
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
.lg-type { margin-left: 6px; white-space: nowrap; }
.lg-type i { display: inline-block; width: 9px; height: 9px; border-radius: 2px; margin-right: 3px; vertical-align: -1px; }
</style>
