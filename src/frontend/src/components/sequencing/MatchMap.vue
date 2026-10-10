<script setup lang="ts">
/**
 * 匹配简图（SnapGene 风格线性图谱）
 *
 * 从 SequencingPanel.vue 拆出的展示子组件：上方 read 深红块状箭头（按落位
 * 首次适配分道），中间刻度轴（绿段=已测覆盖、轴上红块=变异），下方参考特征
 * 彩色块状箭头（类型配色与环形图谱一致，放不下的名字引线外置）。
 * 点击 read / 轴上变异块只上报事件，峰图联动（勾选、选中、滚动）留在父组件。
 * 特征去重开关（dedupMapFeats）是纯 UI 状态，留在本组件内。
 */
import { computed, ref } from 'vue'
import type { SequencingAnalysis } from '@/api'
import { readSegments, readSpanText, shortName } from '@/utils/seqPanelModel'

type ReadRow = SequencingAnalysis['reads'][number]
type Feature = SequencingAnalysis['features'][number]

const props = defineProps<{
  reads: ReadRow[]
  variants: SequencingAnalysis['variants']
  features: Feature[]
  coverageRanges: [number, number][]
  referenceLength: number
}>()

const emit = defineEmits<{
  (e: 'open-read', index: number): void
  (e: 'jump-variant', v: SequencingAnalysis['variants'][number]): void
}>()

const MAP_W = 1000        // viewBox 宽度
const MAP_GUTTER = 150    // 左侧文件名栏宽
const READS_TOP = 8       // read 区顶部
const READ_LANE_H = 36    // read 行高（read 是本图主角，行高/箭头约为特征的 2 倍）
const READ_H = 26         // read 箭头高度
const VAR_H = 14          // 轴上方变异标记带高度
const FEAT_LANE_H = 19    // 特征行高
const FEAT_H = 14         // 特征箭头高度
const LABEL_LANE_H = 16   // 特征外置标签行高

// 特征类型配色（与 PlasmidMap 环形图谱一致）
const FEATURE_COLORS: Record<string, string> = {
  promoter: '#E8656B', terminator: '#2FA98C', CDS: '#4E79C7', gene: '#4E79C7',
  origin: '#8FBF6B', rep_origin: '#8FBF6B', resistance: '#E8B93E', tag: '#B07FD8',
  MCS: '#E8923D', multiple_cloning_site: '#E8923D', regulatory: '#E8656B',
  other: '#9AA5B1'
}
function mapFeatureColor(type: string): string {
  return FEATURE_COLORS[type] || FEATURE_COLORS.other
}
function darkenColor(hex: string, amount: number): string {
  const n = parseInt(hex.slice(1), 16)
  const r = Math.round(((n >> 16) & 255) * (1 - amount))
  const g = Math.round(((n >> 8) & 255) * (1 - amount))
  const b = Math.round((n & 255) * (1 - amount))
  return `rgb(${r},${g},${b})`
}

/** 一段箭头：跨原点 read 折回成两段，head 标记 3' 端所在段（只在该段画箭头头部），
 *  nameSeg 为印名字的最长段；线性 read 恰一段 */
interface MapSeg { x1: number; w: number; head: boolean; nameSeg: boolean }
type MapRead = ReadRow & { diffs: number[]; lane: number; nameInside: boolean; segs: MapSeg[] }

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

/** 首次适配分道：把互不重叠的区间堆进尽量少的行，返回每项道号 */
function assignLanes<T>(items: T[], startOf: (t: T) => number, endOf: (t: T) => number): number[] {
  const laneEnds: number[] = []
  return items.map((it) => {
    const s = startOf(it)
    const e = endOf(it)
    let li = laneEnds.findIndex((le) => s > le)
    if (li === -1) {
      laneEnds.push(e)
      li = laneEnds.length - 1
    } else {
      laneEnds[li] = e
    }
    return li
  })
}

const mapScale = computed(() =>
  props.referenceLength ? (MAP_W - 10 - MAP_GUTTER) / props.referenceLength : 0)

function mapX(pos: number): number {
  return MAP_GUTTER + (pos - 1) * mapScale.value
}

/** 箭头头部宽度（随条带宽度自适应，封顶限制扁条不被头部吃满） */
function arrowHeadW(w: number): number {
  return Math.min(26, Math.max(8, w * 0.2))
}

function segWidth(s: number, e: number): number {
  return Math.max(6, (e - s + 1) * mapScale.value)
}

/** read 的箭头段（折回坐标）：正向 read 头部在最后一段右端，反向在第一段左端 */
function readMapSegs(r: ReadRow): MapSeg[] {
  const segs = readSegments(r)
  if (!segs.length) segs.push([r.ref_start, r.ref_end])  // 无对齐：与旧版同样按原坐标画
  const longest = segs.reduce((m, sg, k) => (sg[1] - sg[0] > segs[m][1] - segs[m][0] ? k : m), 0)
  return segs.map(([s, e], k) => ({
    x1: mapX(s), w: segWidth(s, e),
    head: r.direction === '+' ? k === segs.length - 1 : k === 0,
    nameSeg: k === longest,
  }))
}

/** 首次适配分道（按区段判重叠）：跨原点 read 的两段都要与同道其他 read 不相交；
 *  线性 read 按起点排序时与 assignLanes 的「起点 > 道尾」判据等价 */
function assignReadLanes(items: ReadRow[]): number[] {
  const laneSegs: [number, number][][] = []
  return items.map((it) => {
    const segs = readSegments(it)
    if (!segs.length) segs.push([it.ref_start, it.ref_end])
    let li = laneSegs.findIndex((occ) =>
      !occ.some(([s1, e1]) => segs.some(([s2, e2]) => e1 >= s2 && e2 >= s1)))
    if (li === -1) {
      laneSegs.push([])
      li = laneSegs.length - 1
    }
    laneSegs[li].push(...segs)
    return li
  })
}

/** 按落位排序的 read 行（附差异位置、堆叠道号；名字放得下画进箭头内） */
const mapRows = computed<MapRead[]>(() => {
  const sorted = [...props.reads]
    .sort((x, y) => x.ref_start - y.ref_start || x.index - y.index)
    .map((r) => ({ ...r, diffs: readDiffPositions(r) }))
  const lanes = assignReadLanes(sorted)
  return sorted.map((r, i) => {
    const segs = readMapSegs(r)
    const ns = segs.find((g) => g.nameSeg)!
    const nameW = shortName(r.filename).length * 7 + 8
    const nameInside = ns.w - (ns.head ? arrowHeadW(ns.w) : 0) - 10 >= nameW
    return { ...r, lane: lanes[i], nameInside, segs }
  })
})

const readLaneCount = computed(() => mapRows.value.reduce((m, r) => Math.max(m, r.lane + 1), 0))
const axisY = computed(() => READS_TOP + readLaneCount.value * READ_LANE_H + VAR_H + 6)
const featsTop = computed(() => axisY.value + 24)

function readY(lane: number): number {
  return READS_TOP + lane * READ_LANE_H
}
function featY(lane: number): number {
  return featsTop.value + lane * FEAT_LANE_H
}

interface MapFeat {
  name: string
  type: string
  start: number
  end: number
  strand: string
  x1: number
  x2: number
  lane: number
  labelInside: boolean
  labelX: number
  labelLane: number
}

/** 参考特征（彩色块状箭头；名字放不下时引线外置到下方标签道）。
 *  去重开关（默认关=按文件原样显示）：图谱文件里常见同名注释标了多条
 *  （如成对的 5 UTR / 邻接拼接的 miscellaneous），开启后同名且位置重叠
 *  或相邻（≤50bp）的合并为一条；方向不敏感（同一元件常被标在两条链上）。
 *  相距远的同名特征（如分布在两端的两个 3 UTR）不合并 */
const DEDUP_GAP_BP = 50
const dedupMapFeats = ref(false)
const mapFeats = computed<MapFeat[]>(() => {
  if (!props.features.length) return []
  let feats = [...props.features].sort((x, y) => x.start - y.start || x.end - y.end)
  if (dedupMapFeats.value) {
    const merged: typeof feats = []
    for (const f of feats) {
      // 按 start 升序扫描：f.start ≥ hit.start 恒成立，只需扩右端；
      // f.start − hit.end ≤ 阈值 同时覆盖重叠（负数）与邻接（0/1）情形
      const hit = merged.find((m) => m.name === f.name
        && f.start - m.end <= DEDUP_GAP_BP)
      if (hit) {
        hit.end = Math.max(hit.end, f.end)
      } else {
        merged.push({ ...f })
      }
    }
    feats = merged
  }
  const lanes = assignLanes(feats, (f) => f.start, (f) => f.end)
  const labelLaneEnds: number[] = []
  return feats.map((f, i) => {
    const x1 = mapX(f.start)
    const x2 = x1 + Math.max(8, (f.end - f.start + 1) * mapScale.value)
    const labelW = f.name.length * 6.6 + 8
    const bodyW = x2 - x1 - arrowHeadW(x2 - x1) - 4
    const inside = f.name.length > 0 && labelW <= bodyW
    const cx = Math.min(MAP_W - 10 - labelW / 2, Math.max(MAP_GUTTER + labelW / 2, (x1 + x2) / 2))
    let labelLane = -1
    if (!inside) {
      let li = labelLaneEnds.findIndex((le) => cx - labelW / 2 > le + 8)
      if (li === -1) {
        labelLaneEnds.push(cx + labelW / 2)
        li = labelLaneEnds.length - 1
      } else {
        labelLaneEnds[li] = cx + labelW / 2
      }
      labelLane = li
    }
    return {
      name: f.name, type: f.type, start: f.start, end: f.end, strand: f.strand || '+',
      x1, x2, lane: lanes[i], labelInside: inside, labelX: cx, labelLane
    }
  })
})

/** 开启去重后实际合并掉的注释条数（0 = 图里没有满足合并条件的同名邻近注释） */
const dedupMergedCount = computed(() => {
  if (!dedupMapFeats.value || !props.features.length) return 0
  return props.features.length - mapFeats.value.length
})
const featLaneCount = computed(() => mapFeats.value.reduce((m, f) => Math.max(m, f.lane + 1), 0))
const labelLaneCount = computed(() =>
  mapFeats.value.reduce((m, f) => Math.max(m, f.labelInside ? 0 : f.labelLane + 1), 0))

function labelY(lane: number): number {
  return featsTop.value + featLaneCount.value * FEAT_LANE_H + 14 + lane * LABEL_LANE_H
}

const mapHeight = computed(() => Math.max(
  axisY.value + 28,
  featsTop.value + featLaneCount.value * FEAT_LANE_H + labelLaneCount.value * LABEL_LANE_H + 4
))

/** 参考条覆盖段（轴上叠加绿色已测段） */
const mapCovered = computed(() => props.coverageRanges)

/** 轴刻度（自适应步长，形如 SnapGene 的 2000/4000/6000） */
const mapTicks = computed(() => {
  const L = props.referenceLength
  if (!L) return []
  const steps = [100, 200, 250, 500, 1000, 2000, 2500, 5000, 10000, 20000, 50000]
  const step = steps.find((s) => L / s <= 9) ?? 100000
  const out: { pos: number; label: string }[] = []
  for (let p = step; p <= L; p += step) {
    out.push({ pos: p, label: p >= 10000 ? `${Math.round(p / 1000)}k` : String(p) })
  }
  return out
})

/** 图例里出现的特征类型（按出现顺序去重） */
const mapLegendTypes = computed(() => {
  const seen = new Set<string>()
  const out: { type: string; color: string }[] = []
  for (const f of mapFeats.value) {
    const key = FEATURE_COLORS[f.type] ? f.type : 'other'
    if (!seen.has(key)) {
      seen.add(key)
      out.push({ type: key === 'other' ? '其他' : key, color: FEATURE_COLORS[key] })
    }
  }
  return out
})

/** 块状箭头路径（forward 箭头朝右，否则朝左）；head=false 画无头的矩形段
 *  （跨原点 read 折回后不含 3' 端的那一段） */
function arrowPath(x1: number, x2: number, y: number, h: number, forward: boolean,
  head = true): string {
  const w = Math.max(2, x2 - x1)
  const aw = head ? arrowHeadW(w) : 0
  if (forward) {
    const bx = Math.max(x1, x2 - aw)
    return `M ${x1} ${y} L ${bx} ${y} L ${x2} ${y + h / 2} L ${bx} ${y + h} L ${x1} ${y + h} Z`
  }
  const bx = Math.min(x2, x1 + aw)
  return `M ${x2} ${y} L ${bx} ${y} L ${x1} ${y + h / 2} L ${bx} ${y + h} L ${x2} ${y + h} Z`
}
</script>

<template>
  <div class="map-box">
    <h4 class="section-title">匹配简图<span class="map-sub">（read 落位与参考特征一览；点击 read 或红块在比对峰图中查看）</span>
      <label class="map-dedup-toggle"
             title="图谱文件里同名且位置重叠或相邻（≤50bp）的重复注释合并为一条显示（如成对的 5 UTR、邻接的 miscellaneous），方向不敏感；相距远的同名特征不受影响；默认按文件原样显示">
        <input type="checkbox" v-model="dedupMapFeats" /> 特征去重
      </label>
      <span v-if="dedupMergedCount > 0" class="map-sub">已合并 {{ dedupMergedCount }} 条重复注释</span>
    </h4>
    <svg class="map-svg" :viewBox="`0 0 ${MAP_W} ${mapHeight}`" preserveAspectRatio="xMidYMid meet" role="img">
      <!-- 刻度网格线 -->
      <line v-for="t in mapTicks" :key="'g' + t.pos" :x1="mapX(t.pos)" :x2="mapX(t.pos)"
            :y1="READS_TOP - 4" :y2="mapHeight - 2" class="map-grid" />
      <!-- read 行：深红块状箭头（方向见箭头），名字放得下画进箭头内，差异位点空心圆 -->
      <g v-for="r in mapRows" :key="r.index" class="map-row" @click="emit('open-read', r.index)">
        <title>{{ r.filename }}：{{ readSpanText(r, '-') }}{{ r.wraps_origin ? '，跨越环状参考原点' : '' }}（{{ r.direction === '+' ? '正向' : '反向' }}，一致性 {{ (r.identity * 100).toFixed(1) }}%）——点击在比对峰图中查看</title>
        <text v-if="!r.nameInside" :x="MAP_GUTTER - 8" :y="readY(r.lane) + READ_H / 2 + 4" text-anchor="end" class="map-label">
          {{ r.direction === '+' ? '→' : '←' }} {{ shortName(r.filename) }}
        </text>
        <path v-for="(g, k) in r.segs" :key="k"
              :d="arrowPath(g.x1, g.x1 + g.w, readY(r.lane), READ_H, r.direction === '+', g.head)"
              :class="r.direction === '+' ? 'map-arrow-fwd' : 'map-arrow-rev'" />
        <template v-for="(g, k) in r.segs" :key="'n' + k">
          <text v-if="r.nameInside && g.nameSeg" :x="g.x1 + (g.w - (g.head ? arrowHeadW(g.w) : 0)) / 2"
                :y="readY(r.lane) + READ_H / 2 + 4" text-anchor="middle" class="map-read-name">{{ shortName(r.filename) }}</text>
        </template>
        <circle v-for="p in r.diffs" :key="p" :cx="mapX(p) + 1" :cy="readY(r.lane) + READ_H / 2" r="4.5" class="map-dot" />
      </g>
      <!-- 刻度轴：灰底 + 绿色已测覆盖段 + 黑轴线 + 刻度数字 -->
      <rect :x="MAP_GUTTER" :y="axisY - 4" :width="MAP_W - 10 - MAP_GUTTER" height="8" rx="4" class="map-axis-bg" />
      <rect v-for="(seg, i) in mapCovered" :key="'c' + i" :x="mapX(seg[0])" :y="axisY - 4"
            :width="Math.max(1.5, (seg[1] - seg[0] + 1) * mapScale)" height="8" class="map-axis-cov" />
      <line :x1="MAP_GUTTER" :x2="MAP_W - 10" :y1="axisY" :y2="axisY" class="map-axis-line" />
      <g v-for="t in mapTicks" :key="'t' + t.pos">
        <line :x1="mapX(t.pos)" :x2="mapX(t.pos)" :y1="axisY" :y2="axisY + 6" class="map-tick-line" />
        <text :x="mapX(t.pos)" :y="axisY + 18"
              :text-anchor="mapX(t.pos) > MAP_W - 45 ? 'end' : (mapX(t.pos) < MAP_GUTTER + 45 ? 'start' : 'middle')"
              class="map-tick-num">{{ t.label }}</text>
      </g>
      <!-- 轴上变异红块（点击跳峰图） -->
      <g v-for="v in variants" :key="'v' + v.ref_pos + v.type" class="map-var" @click.stop="emit('jump-variant', v)">
        <title>{{ v.ref_pos }} {{ v.ref_base }}→{{ v.alt_base }}（{{ v.type === 'substitution' ? '替换' : v.type === 'insertion' ? '插入' : '缺失' }}，{{ v.support_reads || 1 }} 条 read）——点击在比对峰图中查看</title>
        <rect :x="mapX(v.ref_pos) - 3.5" :y="axisY - VAR_H + 2" width="7" height="10" rx="1" class="map-var-tick" />
      </g>
      <!-- 参考特征：彩色块状箭头，名字放不下时引线外置 -->
      <g v-for="(f, i) in mapFeats" :key="'f' + i" class="map-feat">
        <title>{{ f.name }}（{{ f.type }}，{{ f.start }}-{{ f.end }}，{{ f.strand === '-' ? '反向' : '正向' }}）</title>
        <path :d="arrowPath(f.x1, f.x2, featY(f.lane), FEAT_H, f.strand !== '-')"
              :fill="mapFeatureColor(f.type)" :stroke="darkenColor(mapFeatureColor(f.type), 0.28)" stroke-width="0.8" />
        <text v-if="f.labelInside" :x="(f.x1 + f.x2) / 2" :y="featY(f.lane) + FEAT_H / 2 + 3.8"
              text-anchor="middle" class="map-feat-label">{{ f.name }}</text>
        <template v-else>
          <line :x1="(f.x1 + f.x2) / 2" :x2="(f.x1 + f.x2) / 2" :y1="featY(f.lane) + FEAT_H"
                :y2="labelY(f.labelLane) - 10" class="map-leader" />
          <text :x="f.labelX" :y="labelY(f.labelLane)" text-anchor="middle" class="map-feat-out">{{ f.name }}</text>
        </template>
      </g>
    </svg>
    <p class="map-legend hint">
      <span class="lg-read">▬ 测序 read（箭头=方向，点击在比对峰图中查看）</span> ·
      <span class="lg-dot">○ 差异位点</span> ·
      <span class="lg-var">▮</span> 变异（点击跳峰图） ·
      <span class="lg-cov">▬</span> 轴上绿段 = 已测序覆盖
      <template v-if="mapLegendTypes.length"> · 特征
        <span v-for="t in mapLegendTypes" :key="t.type" class="lg-type"><i :style="{ background: t.color }"></i>{{ t.type }}</span>
      </template>
    </p>
  </div>
</template>

<style scoped>
/* 匹配简图样式：随模板从 SequencingPanel.vue 迁入 */
.map-box { background: #fff; border: 1px solid var(--border-color, #eee); border-radius: 10px; padding: 0.75rem 1rem; }
.section-title { font-size: 0.95rem; margin: 0 0 0.5rem; }
.hint { color: #999; font-size: 0.85rem; }
.map-sub { font-size: 0.75rem; color: #999; font-weight: 400; }
.map-dedup-toggle {
  font-size: 0.75rem; color: #555; font-weight: 400; margin-left: 0.75rem;
  display: inline-flex; align-items: center; gap: 0.25rem; cursor: pointer; user-select: none;
}
.map-dedup-toggle input { vertical-align: middle; }
.map-svg { width: 100%; height: auto; display: block; user-select: none; }
.map-label { font-size: 11.5px; fill: #444; font-family: Consolas, monospace; }
.map-grid { stroke: #ECEEF0; stroke-width: 1; stroke-dasharray: 3 4; }
.map-row { cursor: pointer; }
.map-read-name { font-size: 11.5px; fill: #fff; font-weight: 700; pointer-events: none; }
.map-arrow-fwd, .map-arrow-rev { fill: #B03A2E; stroke: #7E251C; stroke-width: 0.8; opacity: 0.92; }
.map-row:hover .map-arrow-fwd, .map-row:hover .map-arrow-rev { opacity: 1; fill: #C74A3C; }
.map-dot { fill: #fff; stroke: #C0392B; stroke-width: 1.5; pointer-events: none; }
.map-var { cursor: pointer; }
.map-var-tick { fill: #C0392B; }
.map-var:hover .map-var-tick { fill: #E74C3C; }
.map-axis-bg { fill: #D5D9DE; }
.map-axis-cov { fill: #58B368; }
.map-axis-line { stroke: #3A3F45; stroke-width: 1.6; }
.map-tick-line { stroke: #3A3F45; stroke-width: 1; }
.map-tick-num { font-size: 11px; fill: #555; }
.map-feat { fill-opacity: 0.95; }
.map-feat:hover { fill-opacity: 1; }
.map-feat-label { font-size: 10.5px; fill: #10131A; pointer-events: none; }
.map-feat-out { font-size: 11px; fill: #333; }
.map-leader { stroke: #A5A9AE; stroke-width: 0.8; }
.lg-read { color: #B03A2E; font-weight: 600; }
.lg-dot { color: #C0392B; font-weight: 600; }
.lg-var { color: #C0392B; font-weight: 700; }
.lg-cov { color: #58B368; font-weight: 700; }
.lg-type { margin-left: 6px; white-space: nowrap; }
.lg-type i { display: inline-block; width: 9px; height: 9px; border-radius: 2px; margin-right: 3px; vertical-align: -1px; }
</style>
