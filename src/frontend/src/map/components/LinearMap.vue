<script setup lang="ts">
/**
 * 线性图谱（SVG，可缩放/平移）：酶切标签与刻度、主轴（刻度、深度放大时显示碱基）、
 * 引物/特征/ORF 分道、特征标签（内置或外置引线）、搜索命中、选区，
 * 以及调用方传入的额外轨道（tracks，如测序 read/覆盖/变异）。
 * 交互：拖拽 = 选区；Ctrl+滚轮 = 以指针为中心缩放；Shift+滚轮/横向滚动 = 平移；
 * Shift+拖拽 = 平移。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useElementSize } from '@vueuse/core'
import type { MapDocument, Range } from '../types'
import { useMapStore } from '../store'
import { barArrowPath, headSegmentIndex, primerBarPath, rangeFromBoundaries, rangeLength, splitRange } from '../core/coords'
import { stackLanes } from '../core/lanes'
import { enzymePriority, featurePriority, fitsInside, layoutLinearLabels } from '../core/labels'
import { cutStyle, overviewCuts } from '../core/enzymes'
import { darken, featureColor, textOn } from '../core/palette'
import { formatBp, ticks } from '../core/ticks'
import { MAP_FONT, textWidth } from '../core/text'
import { PRIMER_FWD, PRIMER_REV, isRevPrimer } from '../core/primer_style'
import { hitKeyOf, resolveHit, setHoverFromEvent } from '../hits'
import type { MapTrack, TrackItem } from '../tracks'
import MapTooltip from './MapTooltip.vue'

const props = withDefaults(defineProps<{
  doc?: MapDocument
  width?: number
  tracks?: MapTrack[]
  interactive?: boolean
  /** 隐藏酶切（如测序简图） */
  showEnzymes?: boolean
}>(), { tracks: () => [], interactive: true, showEnzymes: true })

const emit = defineEmits<{ (e: 'track-click', trackId: string, itemId: string): void }>()

const { store, own } = useMapStore(() => props.doc)
const wrap = ref<HTMLElement | null>(null)
const svgEl = ref<SVGSVGElement | null>(null)
const { width: wrapW } = useElementSize(wrap)
const W = computed(() => Math.round(props.width ?? Math.max(280, wrapW.value || 900)))
const PAD = 10
const FONT = 11
const L = computed(() => store.L.value)

// ---------------- 视口 ----------------
const vp = computed(() => store.viewport.value)
const span = computed(() => Math.max(1e-6, vp.value.to - vp.value.from))
const scale = computed(() => (W.value - 2 * PAD) / span.value)
const X = (b: number) => PAD + (b - vp.value.from) * scale.value
const toB = (x: number) => vp.value.from + (x - PAD) / scale.value

function setViewport(from: number, to: number) {
  const minSpan = Math.min(L.value, Math.max(10, (W.value - 2 * PAD) / 14))
  let s = Math.min(L.value, Math.max(minSpan, to - from))
  let f = Math.max(0, Math.min(L.value - s, from))
  if (!Number.isFinite(f)) { f = 0; s = L.value }
  store.viewport.value = { from: f, to: f + s }
}
function zoomBy(k: number, centerB = (vp.value.from + vp.value.to) / 2) {
  const ns = span.value / k
  const t = (centerB - vp.value.from) / span.value
  setViewport(centerB - t * ns, centerB - t * ns + ns)
}
/**
 * 按钮缩放：有选区（不跨原点）放大/缩小时以选区中点为中心，只有光标时以光标为中心
 * （SnapGene 式，选区不会被挤出视野）；都没有时以视口中心缩放。
 */
function zoomStep(k: number) {
  const s = store.selection.value
  const caret = store.caret.value
  let c: number | null = null
  if (s) {
    const segs = splitRange(s, L.value)
    if (segs.length === 1) c = (segs[0].start - 1 + segs[0].end) / 2
  } else if (caret !== null) c = caret
  if (c === null) return zoomBy(k)
  const ns = span.value / k
  setViewport(c - ns / 2, c + ns / 2)
}
function fitAll() { setViewport(0, L.value) }
const zoomed = computed(() => span.value < L.value - 0.5)

watch(() => store.navigate.value.n, () => {
  const nav = store.navigate.value
  const r = nav.range ?? { start: nav.pos, end: nav.pos }
  const segs = splitRange(r, L.value)
  const from = segs[0].start - 1
  const to = segs[segs.length - 1].end
  if (segs.length > 1) return fitAll()
  if (from >= vp.value.from && to <= vp.value.to) return
  const len = to - from
  if (len < span.value * 0.9) {
    const c = (from + to) / 2
    setViewport(c - span.value / 2, c + span.value / 2)
  } else {
    setViewport(from - len * 0.05, to + len * 0.05)
  }
})

/** 区间在视口内的可见像素段（跨原点拆段） */
function visSegs(r: Range): { x1: number; x2: number; s: Range; clippedL: boolean; clippedR: boolean }[] {
  const out: { x1: number; x2: number; s: Range; clippedL: boolean; clippedR: boolean }[] = []
  const segs = store.circular.value ? splitRange(r, L.value) : [{ start: Math.min(r.start, r.end), end: Math.max(r.start, r.end) }]
  for (const s of segs) {
    const a = s.start - 1
    const b = s.end
    if (b <= vp.value.from || a >= vp.value.to) continue
    const x1 = X(Math.max(a, vp.value.from))
    const x2 = X(Math.min(b, vp.value.to))
    out.push({ x1, x2: Math.max(x2, x1 + 1.5), s, clippedL: a < vp.value.from, clippedR: b > vp.value.to })
  }
  return out
}

// ---------------- 酶切 ----------------
/** 视口内最多画这么多切点；超出时只保留少切点酶（放大后自然显示更多） */
const MAX_VIEW_CUTS = 300
const cutOverview = computed(() => overviewCuts(
  (props.showEnzymes ? store.cuts.value : []).filter((c) => c.cut >= vp.value.from && c.cut <= vp.value.to), MAX_VIEW_CUTS))
const cutsInView = computed(() => cutOverview.value.shown.map((c) => ({ ...c, key: `e:${c.id}`, x: X(c.cut), style: cutStyle(c) })))
const ENZ_MAX_ROWS = 5
const enzLabels = computed(() => {
  if (!store.show.labels || !cutsInView.value.length) return { placed: [], hidden: [] as string[], rows: 0 }
  // 允许酶名横向错开（斜引线连回切点），MCS 一类密集位点不必全部堆成高塔或被隐藏
  return layoutLinearLabels(cutsInView.value.map((c) => ({ id: c.key, text: c.name, bold: c.style.weight > 400, x: c.x, priority: enzymePriority(c.enzyme.cut_count) })),
    { width: W.value - 2, fontSize: FONT, maxRows: ENZ_MAX_ROWS, gap: 6, left: 2, maxShift: 90 })
})
/** 酶名行的 y：第 0 行（优先级最高）贴近主轴，往上依次排，引线最短 */
const enzRowY = (row: number) => layout.value.enzLabelTop + (enzLabels.value.rows - 1 - row) * ENZ_ROW
const enzStyleByKey = computed(() => new Map(cutsInView.value.map((c) => [c.key, c])))

// ---------------- 额外轨道 ----------------
/** [a, b] 内去掉各标记 ±r 后最长的空当 */
function longestFreeGap(a: number, b: number, marks: number[], r: number): [number, number] {
  let best: [number, number] = [a, a]
  let cur = a
  for (const m of [...marks].sort((x, y) => x - y)) {
    if (m + r < a || m - r > b) continue
    if (m - r - cur > best[1] - best[0]) best = [cur, m - r]
    cur = Math.max(cur, m + r)
  }
  if (b - cur > best[1] - best[0]) best = [cur, b]
  return best
}
interface TrackGeom {
  track: MapTrack; y: number; height: number; laneH: number
  items: { item: TrackItem; key: string; lane: number; segs: { x1: number; x2: number; head: boolean }[]; labelInside: boolean; labelX: number; labelRight: boolean; marks: number[] }[]
}
function layoutArrowTrack(t: MapTrack, y0: number): TrackGeom {
  const h = t.height ?? 18
  const laneH = h + 6
  const rows: [number, number][][] = []
  const items: TrackGeom['items'] = []
  const sorted = [...t.items].sort((a, b) => a.start - b.start || a.id.localeCompare(b.id))
  for (const it of sorted) {
    const ranges = it.segments?.length ? it.segments : [{ start: it.start, end: it.end }]
    const segs = ranges.flatMap((r, i) => visSegs(r).map((v) => ({ x1: v.x1, x2: v.x2, i })))
    if (!segs.length) continue
    const headIdx = headSegmentIndex(ranges, it, it.strand ?? 0)
    const geomSegs = segs.map((s) => ({ x1: s.x1, x2: s.x2, head: s.i === headIdx }))
    const longest = geomSegs.reduce((m, s) => (s.x2 - s.x1 > m.x2 - m.x1 ? s : m), geomSegs[0])
    const lw = it.label ? textWidth(it.label, FONT) + 10 : 0
    const marks = (it.marks ?? []).filter((p) => p - 1 >= vp.value.from && p <= vp.value.to).map((p) => X(p - 0.5))
    // 名字放在条内最长的、不压标记圆点的空当里（否则变异圆点会盖住 read 名）
    const gap = longestFreeGap(longest.x1 + (it.strand === -1 ? h * 0.8 : 0), longest.x2 - (it.strand === 1 ? h * 0.8 : 0), marks, 6)
    const inside = !!it.label && gap[1] - gap[0] >= lw
    let labelX = inside ? (gap[0] + gap[1]) / 2 : (longest.x1 + longest.x2) / 2
    let labelRight = true
    if (it.label && !inside) {
      // 名字放不下：依次尝试最长段右侧、左侧，再试其余段，放不下就贴边
      const order = [longest, ...geomSegs.filter((s) => s !== longest)]
      const spot = order.flatMap((s) => [
        s.x2 + 4 + lw <= W.value ? { x: s.x2 + 4, right: true } : null,
        s.x1 - 4 - lw >= 0 ? { x: s.x1 - 4, right: false } : null,
      ]).find((c) => c !== null) ?? { x: Math.max(2, Math.min(W.value - lw, longest.x1)), right: true }
      labelX = spot.x
      labelRight = spot.right
    }
    const iv: [number, number][] = geomSegs.map((s) => [s.x1, s.x2])
    if (it.label && !inside) iv.push([labelRight ? labelX : labelX - lw, labelRight ? labelX + lw : labelX])
    let lane = rows.findIndex((r) => !r.some(([a, b]) => iv.some(([c, d]) => c < b + 3 && a < d + 3)))
    if (lane === -1) { rows.push([]); lane = rows.length - 1 }
    rows[lane].push(...iv)
    items.push({
      item: it, key: `t:${t.id}:${it.id}`, lane, segs: geomSegs, labelInside: inside, labelX, labelRight,
      marks,
    })
  }
  return { track: t, y: y0, height: Math.max(1, rows.length) * laneH, laneH, items }
}

// ---------------- 纵向布局 ----------------
const ENZ_ROW = 13
const layout = computed(() => {
  let y = 6
  const enzLabelTop = y
  y += enzLabels.value.rows * ENZ_ROW + (enzLabels.value.rows ? 4 : 0)
  const above: TrackGeom[] = []
  for (const t of props.tracks.filter((t) => t.kind === 'arrows' && t.position !== 'below')) {
    const g = layoutArrowTrack(t, y)
    above.push(g)
    y += g.height
  }
  const hasMarks = props.tracks.some((t) => t.kind === 'marks')
  const marksY = y
  if (hasMarks) y += 14
  const cutTop = y
  y += props.showEnzymes && store.enzymes.value.length ? 10 : 2
  const axisY = y + 4
  y = axisY + 8
  const tickY = y + 10
  y += 14
  const showBases = scale.value >= 7 && !!store.doc.value.sequence
  const basesY = y + 10
  if (showBases) y += 16
  return { enzLabelTop, above, hasMarks, marksY, cutTop, axisY, tickY, showBases, basesY, afterAxis: y }
})

const featLanes = computed(() => stackLanes(
  store.features.value.map((f) => ({ id: f.id, start: f.start, end: f.end })), L.value,
  { circular: store.circular.value, gap: 4 / scale.value, maxLanes: 10 },
))
const FEAT_H = 14
const FEAT_LANE = 18
/**
 * 引物条尺寸：带高 10px、最小宽 12px（只保证锥形可辨）、车道间距 18px。
 * 行数优先：每方向最多 2 道（典型全长视野各 1 道，共上下 2 行）。
 * 最小宽给太大（28px）会把密集区挤到每方向 3 道、共 6 行，更乱（用户指正）
 */
const MIN_PRIMER_W = 12
const PRIMER_H = 10
const PRIMER_LANE = 18
/**
 * 引物分道：先按 bp 把短引物占位扩到最小渲染宽度（扩宽必须在分道前，
 * 否则同道相邻引物 px 级撑宽后互相叠上），再**按方向分组分道**——
 * 正向（蓝）占上面的道、反向（紫）占下面的道，每道方向一致；
 * 混排时正反箭头首尾相接成毛虫，方向语义也糊（与序列视图
 * 「正向框在上、负链框在下」同一布局语言）。
 */
const primerLanes = computed(() => {
  const minWbp = MIN_PRIMER_W / scale.value
  const expand = (p: (typeof store.primers.value)[number]) => {
    if (p.start > p.end) return p // 跨原点引物足够长，不扩
    const len = p.end - p.start + 1
    if (len >= minWbp) return p
    const extra = (minWbp - len) / 2
    return {
      id: p.id,
      start: Math.max(1, Math.round(p.start - extra)),
      end: Math.min(L.value, Math.round(p.end + extra)),
    }
  }
  const opts = { circular: store.circular.value, gap: 2 / scale.value, maxLanes: 2 }
  const fwd = stackLanes(store.primers.value.filter((p) => !isRevPrimer(p)).map(expand), L.value, opts)
  const rev = stackLanes(store.primers.value.filter((p) => isRevPrimer(p)).map(expand), L.value, opts)
  const lanes = new Map<string, number>()
  for (const [id, ln] of fwd.lanes) lanes.set(id, ln)
  for (const [id, ln] of rev.lanes) lanes.set(id, ln + fwd.laneCount)
  return { lanes, laneCount: fwd.laneCount + rev.laneCount, overflow: [...fwd.overflow, ...rev.overflow] }
})
const orfLanes = computed(() => stackLanes(store.orfs.value.map((o, i) => ({ id: String(i), start: o.start, end: o.end })), L.value,
  { circular: store.circular.value, maxLanes: 4 }))

const lower = computed(() => {
  let y = layout.value.afterAxis
  // 引物标签在块上方最多 3 行（模块 A 避让），预留 3×12 px
  const primerLabelRows = primerLanes.value.laneCount ? 3 : 0
  y += primerLabelRows * 15
  const primerTop = y
  // 车道间距 = PRIMER_LANE（带高 10 + 呼吸空间）
  y += primerLanes.value.laneCount * PRIMER_LANE + (primerLanes.value.laneCount ? 4 : 0)
  const featTop = y
  y += featLanes.value.laneCount * FEAT_LANE
  const orfTop = y
  y += orfLanes.value.laneCount * 7 + (orfLanes.value.laneCount ? 4 : 0)
  return { primerTop, featTop, orfTop, afterOrf: y, primerLabelRows }
})

const featGeoms = computed(() => store.features.value.flatMap((f) => {
  const lane = featLanes.value.lanes.get(f.id)
  if (lane === undefined) return []
  const ranges = f.segments?.length ? f.segments : [{ start: f.start, end: f.end }]
  const headIdx = headSegmentIndex(ranges, f, f.strand)
  const segs = ranges.flatMap((r, i) => visSegs(r).map((v) => ({ ...v, i })))
  if (!segs.length) return []
  const y = lower.value.featTop + lane * FEAT_LANE
  const color = featureColor(f)
  const paths = segs.map((s) => {
    const headVisible = s.i === headIdx && (f.strand === 1 ? !s.clippedR : !s.clippedL)
    return barArrowPath(s.x1, s.x2, y, FEAT_H, f.strand, headVisible)
  })
  const longest = segs.reduce((m, s) => (s.x2 - s.x1 > m.x2 - m.x1 ? s : m), segs[0])
  const inside = store.show.labels && fitsInside(f.name, 10, longest.x2 - longest.x1 - FEAT_H * 0.8)
  const minX = Math.min(...segs.map((s) => s.x1))
  const maxX = Math.max(...segs.map((s) => s.x2))
  const connector = segs.length > 1 ? { x1: minX, x2: maxX, y: y + FEAT_H / 2 } : null
  return [{
    f, key: `f:${f.id}`, y, color, stroke: darken(color), paths, connector, inside,
    textX: (longest.x1 + longest.x2) / 2 + (f.strand === 1 ? -FEAT_H * 0.25 : f.strand === -1 ? FEAT_H * 0.25 : 0),
    textColor: textOn(color), cx: (minX + maxX) / 2,
  }]
}))

const featLabels = computed(() => {
  if (!store.show.labels) return { placed: [], hidden: [] as string[], rows: 0 }
  return layoutLinearLabels(featGeoms.value.filter((g) => !g.inside).map((g) => ({
    id: g.key, text: g.f.name, x: g.cx, priority: featurePriority(g.f.type, rangeLength(g.f, L.value, store.circular.value)),
  })), { width: W.value - 2, fontSize: FONT, maxRows: 5, gap: 8, left: 2, maxShift: 60 })
})
const featByKey = computed(() => new Map(featGeoms.value.map((g) => [g.key, g])))

const below = computed(() => {
  let y = lower.value.afterOrf
  const labelTop = y + 6
  y += featLabels.value.rows * 14 + (featLabels.value.rows ? 8 : 0)
  const tracks: TrackGeom[] = []
  for (const t of props.tracks.filter((t) => t.kind === 'arrows' && t.position === 'below')) {
    const g = layoutArrowTrack(t, y)
    tracks.push(g)
    y += g.height
  }
  return { labelTop, tracks, height: y + 4 }
})
const H = computed(() => below.value.height)

const primerGeoms = computed(() => store.primers.value.flatMap((p) => {
  const lane = primerLanes.value.lanes.get(p.id)
  if (lane === undefined) return []
  const rev = isRevPrimer(p)
  const pal = rev ? PRIMER_REV : PRIMER_FWD
  // 3′ 端长锥渐收（primerBarPath）：正向朝右、反向朝左（模块 B）。
  // 跨原点分段时只有含 3′ 端的段带锥头，其余段平收——曾每段都画箭头。
  // 分道占位已按 MIN_PRIMER_W 的 bp 等效宽度扩过（primerLanes），这里把渲染
  // 宽度扩到同一 footprint——分道看到的占位与渲染宽度一致，同道不会相撞。
  // 视口边缘被裁剪的段按可见部分中心扩，可能略有出入（SVG 裁掉越界部分）
  const segList = visSegs(p).map((s) => {
    const w = s.x2 - s.x1
    if (w >= MIN_PRIMER_W) return s
    const cx = (s.x1 + s.x2) / 2
    return { ...s, x1: cx - MIN_PRIMER_W / 2, x2: cx + MIN_PRIMER_W / 2 }
  })
  // 线性视图直接展示原坐标（环状拆段后段序即坐标序），3′ 端在正向末段/反向首段
  const headIdx = rev ? 0 : segList.length - 1
  return segList.map((s, i) => ({
    key: `p:${p.id}`, k: `${p.id}-${i}`,
    d: primerBarPath(s.x1, s.x2, lower.value.primerTop + lane * PRIMER_LANE, PRIMER_H, rev ? -1 : 1, i === headIdx),
    fill: pal.base, stroke: pal.dark,
    x1: s.x1, x2: s.x2, lane,
  }))
}))

/**
 * 线性视图的引物名标签 + 引导线（模块 A）：
 *   - 文字宽度用 layoutLinearLabels 做 AABB 避让：同区域密集引物自动换行错位，
 *     放不下才隐藏（计入「无法显示」），绝不叠字；
 *   - 标签中心与引物块中心不一致时画一根细折线连回去，避免「标签飘在别处」。
 */
interface PrimerLabelGeom { id: string; text: string; x: number; y: number; color: string }
const primerLabels = computed(() => {
  const empty = { placed: [] as PrimerLabelGeom[], leaders: [] as { id: string; d: string }[] }
  if (!store.show.labels || !primerGeoms.value.length) return empty
  const inputs = primerGeoms.value.map((g) => {
    const p = store.primers.value.find((x) => `p:${x.id}` === g.key)!
    const rev = isRevPrimer(p)
    const text = rev ? `← ${p.name}` : `${p.name} →`
    return { id: g.key, text, bold: true, x: (g.x1 + g.x2) / 2, priority: 3e7, rev, g }
  })
  const lay = layoutLinearLabels(
    inputs.map(({ id, text, bold, x, priority }) => ({ id, text, bold, x, priority })),
    { width: W.value, fontSize: 10, maxRows: 3, gap: 4, left: 2, maxShift: 40, rowCost: 18 },
  )
  const byId = new Map(inputs.map((i) => [i.id, i]))
  const placed: PrimerLabelGeom[] = []
  const leaders: { id: string; d: string }[] = []
  // 行高 = 实际文字行高（10px 字号 14px 框高），标签字号很小，行距略收
  const ROW_PX = 15
  for (const l of lay.placed) {
    const src = byId.get(l.id)!
    // y 只由布局分配的行号决定（同区域的引物已由 layoutLinearLabels 分行错开）；
    // 再把整组标签整体上移到各自引物块上方——lane 只影响块，不参与标签分行，
    // 否则同一 lane 的多条标签会挤在同一 y 上互相压字
    const y = lower.value.primerTop - 4 - l.row * ROW_PX
    const center = l.x + l.w / 2
    placed.push({ id: l.id, text: l.text, x: l.x, y, color: src.rev ? PRIMER_REV.dark : PRIMER_FWD.dark })
    // 标签中心离块中心 > 3px 就画引导线（折线：块中心 → 标签端点）
    if (Math.abs(center - l.cx) > 3) {
      const bx = l.cx
      const by = y + 10
      const tx = center < l.cx ? l.x + l.w : l.x
      leaders.push({ id: l.id, d: `M${bx} ${by}L${bx} ${by - 5}L${tx} ${by - 5}` })
    }
  }
  return { placed, leaders }
})
const orfGeoms = computed(() => store.orfs.value.flatMap((o, i) => {
  const lane = orfLanes.value.lanes.get(String(i))
  if (lane === undefined) return []
  return visSegs(o).map((s, j) => ({
    key: `o:${i}`, k: `${i}-${j}`, color: ['#7a8599', '#8d7aa6', '#6f9a8a'][o.frame],
    d: barArrowPath(s.x1, s.x2, lower.value.orfTop + lane * 7, 4, o.strand),
  }))
}))

// 主轴上的色段（axis 轨道）与小刻块（marks 轨道）
const axisBlocks = computed(() => props.tracks.filter((t) => t.kind === 'axis').flatMap((t) =>
  t.items.flatMap((it) => visSegs(it).map((s, i) => ({ k: `${t.id}-${it.id}-${i}`, x: s.x1, w: Math.max(1.5, s.x2 - s.x1), color: it.color ?? '#58b368', cls: it.className })))))
const markGeoms = computed(() => props.tracks.filter((t) => t.kind === 'marks').flatMap((t) =>
  t.items.filter((it) => it.start - 1 <= vp.value.to && it.end >= vp.value.from).map((it) => ({
    key: `t:${t.id}:${it.id}`, x: X(it.start - 0.5), color: it.color ?? '#c0392b', cls: it.className,
  }))))

const tickGeoms = computed(() => {
  const target = Math.max(3, Math.round(W.value / 110))
  const t = ticks(Math.ceil(vp.value.from) + 1, Math.floor(vp.value.to), target)
  return t.positions.map((p) => ({ p, x: X(p - 0.5), label: formatBp(p) }))
})
const bases = computed(() => {
  if (!layout.value.showBases) return []
  const seq = store.doc.value.sequence
  const out: { p: number; x: number; b: string }[] = []
  for (let p = Math.floor(vp.value.from) + 1; p <= Math.ceil(vp.value.to) && p <= L.value; p++) out.push({ p, x: X(p - 0.5), b: seq[p - 1] })
  return out
})

const selRects = computed(() => {
  const s = store.selection.value
  if (!s) return []
  return visSegs(s).map((v, i) => ({ k: i, x: v.x1, w: Math.max(1.5, v.x2 - v.x1) }))
})
const caretX = computed(() => {
  const c = store.caret.value
  if (store.selection.value || c === null || c < vp.value.from || c > vp.value.to) return null
  return X(c)
})
const searchRects = computed(() => store.search.hits.slice(0, 1000).flatMap((h, i) => visSegs(h).map((v, j) => ({
  key: `s:${i}`, k: `${i}-${j}`, x: v.x1, w: Math.max(2, v.x2 - v.x1), current: i === store.search.index,
}))))
const hoverKey = computed(() => store.hoverTarget.value?.id ?? null)
const hoverRects = computed(() => {
  const h = store.hoverTarget.value
  if (!h?.range || h.id.startsWith('t:')) return []
  return visSegs(h.range).map((v, i) => ({ k: i, x: v.x1, w: Math.max(1.5, v.x2 - v.x1) }))
})

// 无法显示项上报
watch([featLanes, featLabels, enzLabels, cutOverview], () => {
  const cutAt = new Map(cutsInView.value.map((c) => [c.key, c.cut]))
  const items = [...featLabels.value.hidden, ...enzLabels.value.hidden].map((k) => {
    const title = resolveHit(store, k)?.title ?? k
    const at = cutAt.get(k)
    return { id: k, name: at === undefined ? title : `${title} @${at.toLocaleString('en-US')}`, reason: '标签空间不足' }
  })
  for (const id of featLanes.value.overflow) {
    items.push({ id: `f:${id}`, name: store.doc.value.features.find((f) => f.id === id)?.name ?? id, reason: '特征分道已满' })
  }
  for (const d of cutOverview.value.dropped) {
    items.push({ id: `e:${d.name}`, name: `${d.name}（视口内 ${d.count} 处）`, reason: '切点过多，放大或在序列视图中查看' })
  }
  store.setUnshown('linear', items)
}, { immediate: true })
onBeforeUnmount(() => store.setUnshown('linear', []))

// ---------------- 交互 ----------------
let mode: 'none' | 'select' | 'pan' = 'none'
let anchor = 0
let moved = false
let downKey: string | null = null
let panStart = { x: 0, from: 0, to: 0 }

function localX(e: { clientX: number }): number {
  const rect = svgEl.value?.getBoundingClientRect()
  if (!rect || !rect.width) return PAD
  return ((e.clientX - rect.left) / rect.width) * W.value
}

function trackItem(key: string): { track: MapTrack; item: TrackItem } | null {
  const [, tid, ...rest] = key.split(':')
  const iid = rest.join(':')
  const track = props.tracks.find((t) => t.id === tid)
  const item = track?.items.find((i) => i.id === iid)
  return track && item ? { track, item } : null
}

function onPointerDown(e: PointerEvent) {
  if (!props.interactive || e.button > 0) return
  moved = false
  downKey = hitKeyOf(e.target)
  if (e.shiftKey) {
    mode = 'pan'
    panStart = { x: e.clientX, from: vp.value.from, to: vp.value.to }
  } else {
    mode = 'select'
    anchor = toB(localX(e))
  }
  ;(e.currentTarget as Element | null)?.setPointerCapture?.(e.pointerId)
}

function onPointerMove(e: PointerEvent) {
  if (mode === 'none') {
    const key = hitKeyOf(e.target)
    if (key?.startsWith('t:')) {
      const ti = trackItem(key)
      store.hover.value = ti?.item.tooltip
        ? { kind: 'track', id: key, title: ti.item.tooltip.title, lines: ti.item.tooltip.lines, range: null, x: e.clientX, y: e.clientY }
        : null
    } else {
      setHoverFromEvent(store, e)
    }
    return
  }
  if (mode === 'pan') {
    const rect = svgEl.value?.getBoundingClientRect()
    const k = (panStart.to - panStart.from) / Math.max(1, (rect?.width || W.value) - 2 * PAD)
    const d = (e.clientX - panStart.x) * k
    setViewport(panStart.from - d, panStart.to - d)
    moved = true
    return
  }
  const b = toB(localX(e))
  if (Math.abs(b - anchor) * scale.value >= 3) {
    moved = true
    store.select(rangeFromBoundaries(anchor, b, L.value, false))
  }
}

function onPointerUp(e: PointerEvent) {
  if (mode === 'select' && !moved) {
    const key = downKey ?? hitKeyOf(e.target)
    if (key?.startsWith('t:')) {
      const ti = trackItem(key)
      if (ti) emit('track-click', ti.track.id, ti.item.id)
    } else {
      const info = resolveHit(store, key)
      if (info) info.select()
      else store.setCaret(Math.max(0, Math.min(L.value, Math.round(anchor))))
    }
  } else if (mode === 'select' && store.selection.value) {
    store.navigateTo(store.selection.value)
  }
  mode = 'none'
}

function onWheel(e: WheelEvent) {
  if (!props.interactive) return
  if (e.ctrlKey || e.metaKey) {
    e.preventDefault()
    zoomBy(Math.exp(-e.deltaY * 0.002), toB(localX(e)))
  } else if (zoomed.value && (e.shiftKey || Math.abs(e.deltaX) > Math.abs(e.deltaY))) {
    e.preventDefault()
    const d = ((e.shiftKey ? e.deltaY : e.deltaX) / scale.value)
    setViewport(vp.value.from + d, vp.value.to + d)
  }
}

function onLeave() { if (mode === 'none') store.hover.value = null }
/** 浏览器接管手势（触屏滚动/系统手势）或丢失捕获：放弃本次拖拽，不触发选择/光标 */
function onPointerCancel() {
  if (mode === 'none') return
  mode = 'none'
  moved = false
  downKey = null
  store.hover.value = null
}

defineExpose({ svgEl, zoomBy, zoomStep, fitAll, setViewport })
</script>

<template>
  <div ref="wrap" class="lmap">
    <div v-if="interactive" class="lmap-controls">
      <button type="button" title="缩小" @click="zoomStep(1 / 1.6)">−</button>
      <button type="button" title="放大（以选区为中心；Ctrl+滚轮以指针为中心）" class="lmap-zoom-in" @click="zoomStep(1.6)">+</button>
      <button type="button" title="显示全长" :disabled="!zoomed" @click="fitAll">全长</button>
      <span class="lmap-range">{{ Math.round(vp.from + 1).toLocaleString() }}–{{ Math.round(vp.to).toLocaleString() }} / {{ L.toLocaleString() }} bp</span>
    </div>
    <svg
      ref="svgEl" class="lmap-svg" data-map-export="linear" xmlns="http://www.w3.org/2000/svg"
      :viewBox="`0 0 ${W} ${H}`" :width="W" :height="H" role="img" :aria-label="`${store.doc.value.name} 线性图谱`"
      :font-family="MAP_FONT"
      @pointerdown="onPointerDown" @pointermove="onPointerMove" @pointerup="onPointerUp"
      @pointercancel="onPointerCancel" @lostpointercapture="onPointerCancel"
      @pointerleave="onLeave" @wheel="onWheel"
    >
      <rect v-for="r in hoverRects" :key="'h' + r.k" :x="r.x" y="0" :width="r.w" :height="H" fill="#f59e0b" fill-opacity="0.1" />
      <rect v-for="r in selRects" :key="'sel' + r.k" :x="r.x" y="0" :width="r.w" :height="H" fill="#4f46e5" fill-opacity="0.13" class="lmap-selection" />

      <!-- 刻度网格 -->
      <line v-for="t in tickGeoms" :key="'g' + t.p" :x1="t.x" :x2="t.x" y1="0" :y2="H" stroke="#eceef0" stroke-dasharray="3 4" />

      <!-- 酶切标签 + 引线 -->
      <g v-for="l in enzLabels.placed" :key="l.id" :data-hit="l.id" class="lmap-enz-label">
        <line :x1="l.cx" :x2="l.x + l.w / 2" :y1="layout.cutTop" :y2="enzRowY(l.row) + ENZ_ROW - 2" stroke="#c5c9ce" stroke-width="0.7" />
        <text :x="l.x" :y="enzRowY(l.row) + 10" :font-size="FONT" class="lmap-enz-text"
              stroke="#ffffff" stroke-width="3" stroke-linejoin="round" paint-order="stroke"
              :fill="enzStyleByKey.get(l.id)?.style.color" :font-weight="enzStyleByKey.get(l.id)?.style.weight">{{ l.text }}</text>
      </g>

      <!-- 额外轨道（主轴上方） -->
      <template v-for="g in layout.above" :key="g.track.id">
        <g v-for="it in g.items" :key="it.key" :data-hit="it.key" :class="['lmap-track-item', it.item.className && `${it.item.className}-row`]">
          <path v-for="(s, k) in it.segs" :key="k" :d="barArrowPath(s.x1, s.x2, g.y + it.lane * g.laneH, g.laneH - 6, it.item.strand ?? 0, s.head)"
                :fill="it.item.color ?? '#b03a2e'" :stroke="it.item.stroke ?? darken(it.item.color ?? '#b03a2e')" stroke-width="0.8"
                :class="it.item.className" />
          <text v-if="it.item.label" :x="it.labelX" :y="g.y + it.lane * g.laneH + (g.laneH - 6) / 2" dominant-baseline="central"
                :text-anchor="it.labelInside ? 'middle' : (it.labelRight ? 'start' : 'end')" :font-size="FONT"
                :fill="it.labelInside ? '#ffffff' : '#374151'" :font-weight="it.labelInside ? 700 : 400"
                :class="it.labelInside ? 'lmap-track-name' : 'lmap-track-label'" pointer-events="none">{{ it.item.label }}</text>
          <circle v-for="(mx, k) in it.marks" :key="'m' + k" :cx="mx" :cy="g.y + it.lane * g.laneH + (g.laneH - 6) / 2" r="4"
                  fill="#ffffff" stroke="#c0392b" stroke-width="1.5" class="lmap-dot" pointer-events="none" />
        </g>
      </template>

      <!-- 主轴上方小刻块（如变异） -->
      <rect v-for="m in markGeoms" :key="m.key" :data-hit="m.key" :x="m.x - 3.5" :y="layout.marksY + 2" width="7" height="10" rx="1"
            :fill="m.color" :class="['lmap-mark', m.cls]" />

      <!-- 酶切刻度 -->
      <line v-for="c in cutsInView" :key="c.key" :data-hit="c.key" :x1="c.x" :x2="c.x" :y1="layout.cutTop" :y2="layout.axisY"
            :stroke="c.style.color" :stroke-width="c.style.unique ? 1.6 : 1" :stroke-dasharray="c.style.dash"
            :class="['lmap-cut', { 'is-blocked': c.style.blocked }]" />

      <!-- 主轴 -->
      <rect :x="X(Math.max(0, vp.from))" :y="layout.axisY - 4" :width="Math.max(0, X(Math.min(L, vp.to)) - X(Math.max(0, vp.from)))" height="8" rx="3" fill="#d5d9de" class="lmap-axis-bg" />
      <rect v-for="b in axisBlocks" :key="b.k" :x="b.x" :y="layout.axisY - 4" :width="b.w" height="8" :fill="b.color" :class="b.cls" />
      <line :x1="X(Math.max(0, vp.from))" :x2="X(Math.min(L, vp.to))" :y1="layout.axisY" :y2="layout.axisY" stroke="#3a3f45" stroke-width="1.4" />
      <rect v-for="r in searchRects" :key="r.k" :data-hit="r.key" :x="r.x" :y="layout.axisY - 6" :width="r.w" height="12"
            :fill="r.current ? '#f97316' : '#facc15'" fill-opacity="0.85" class="lmap-search" />
      <g v-for="t in tickGeoms" :key="'t' + t.p">
        <line :x1="t.x" :x2="t.x" :y1="layout.axisY" :y2="layout.axisY + 6" stroke="#3a3f45" />
        <text :x="t.x" :y="layout.tickY + 2" font-size="10.5" fill="#555" class="lmap-tick-num"
              :text-anchor="t.x > W - 30 ? 'end' : t.x < 30 ? 'start' : 'middle'">{{ t.label }}</text>
      </g>
      <text v-for="b in bases" :key="'b' + b.p" :x="b.x" :y="layout.basesY + 2" font-size="11" text-anchor="middle"
            font-family="Consolas, Menlo, monospace" fill="#1f2937" class="lmap-base">{{ b.b }}</text>
      <line v-if="caretX !== null" :x1="caretX" :x2="caretX" y1="0" :y2="H" stroke="#4f46e5" stroke-width="1.5" class="lmap-caret" />

      <!-- 引物 / 特征 / ORF -->
      <path v-for="p in primerGeoms" :key="p.k" :data-hit="p.key" :d="p.d" :fill="p.fill" :stroke="p.stroke" stroke-width="0.6" class="lmap-primer" />
      <!-- 引物名标签 + 引导线（AABB 避让后仍可能偏离块中心，用折线连回） -->
      <path v-for="l in primerLabels.leaders" :key="'pl' + l.id" :d="l.d" fill="none" stroke="#c5c9ce" stroke-width="0.7" pointer-events="none" />
      <text v-for="l in primerLabels.placed" :key="'pt' + l.id" :data-hit="l.id" :x="l.x" :y="l.y" font-size="10"
            font-weight="600" :fill="l.color" class="lmap-primer-label" style="letter-spacing: -0.01em" pointer-events="none">{{ l.text }}</text>
      <g v-for="g in featGeoms" :key="g.key" :data-hit="g.key"
         :class="['lmap-feat', { 'is-hover': hoverKey === g.key, 'is-selected': store.selectedFeatureId.value === g.f.id }]">
        <line v-if="g.connector" :x1="g.connector.x1" :x2="g.connector.x2" :y1="g.connector.y" :y2="g.connector.y" :stroke="g.stroke" stroke-dasharray="2 2" />
        <path v-for="(d, i) in g.paths" :key="i" :d="d" :fill="g.color" :stroke="g.stroke"
              :stroke-width="hoverKey === g.key || store.selectedFeatureId.value === g.f.id ? 2 : 0.8" />
        <text v-if="g.inside" :x="g.textX" :y="g.y + FEAT_H / 2" dominant-baseline="central" text-anchor="middle"
              font-size="10" :fill="g.textColor" class="lmap-feat-label" pointer-events="none">{{ g.f.name }}</text>
      </g>
      <path v-for="o in orfGeoms" :key="o.k" :data-hit="o.key" :d="o.d" :fill="o.color" class="lmap-orf" />

      <!-- 特征外置标签 -->
      <g v-for="l in featLabels.placed" :key="l.id" :data-hit="l.id" class="lmap-feat-out-g">
        <line :x1="l.cx" :x2="l.x + l.w / 2" :y1="(featByKey.get(l.id)?.y ?? 0) + FEAT_H" :y2="below.labelTop + l.row * 14 + 1"
              stroke="#a5a9ae" stroke-width="0.8" class="lmap-leader" />
        <text :x="l.x" :y="below.labelTop + l.row * 14 + 11" :font-size="FONT" fill="#333" class="lmap-feat-out"
              stroke="#ffffff" stroke-width="3" stroke-linejoin="round" paint-order="stroke">{{ l.text }}</text>
      </g>

      <!-- 额外轨道（下方） -->
      <template v-for="g in below.tracks" :key="g.track.id">
        <g v-for="it in g.items" :key="it.key" :data-hit="it.key" class="lmap-track-item">
          <path v-for="(s, k) in it.segs" :key="k" :d="barArrowPath(s.x1, s.x2, g.y + it.lane * g.laneH, g.laneH - 6, it.item.strand ?? 0, s.head)"
                :fill="it.item.color ?? '#6b7280'" :class="it.item.className" />
        </g>
      </template>
    </svg>
    <MapTooltip v-if="own" :store="store" />
  </div>
</template>

<style scoped>
.lmap { position: relative; width: 100%; }
.lmap-svg { display: block; width: 100%; height: auto; touch-action: pan-y; user-select: none; cursor: crosshair; }
.lmap-feat, .lmap-cut, .lmap-orf, .lmap-primer, .lmap-enz-label, .lmap-feat-out-g, .lmap-track-item, .lmap-mark, .lmap-search { cursor: pointer; }
.lmap-feat:hover path, .lmap-track-item:hover path { filter: brightness(1.08); }
.lmap-controls { display: flex; gap: 6px; align-items: center; font-size: 12px; color: var(--text-secondary); margin-bottom: 4px; }
.lmap-controls button {
  min-width: 28px; padding: 2px 8px; border: 1px solid var(--border-color); border-radius: 4px;
  background: var(--bg-primary); color: var(--text-primary);
}
.lmap-controls button:disabled { opacity: 0.5; cursor: default; }
.lmap-range { margin-left: auto; font-variant-numeric: tabular-nums; }
</style>
