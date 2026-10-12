<script lang="ts">
let instanceCounter = 0
</script>

<script setup lang="ts">
/**
 * 环形图谱（SVG）：主链 + 刻度、特征分道箭头（内置/外置标签）、ORF/引物环、
 * 酶切刻度与标签、搜索命中、选区扇形与光标。
 * 交互：在环带上拖拽 = 选区（可跨原点）；拖拽中心区 / Shift+拖拽 = 旋转；
 * Ctrl+滚轮 = 以指针为中心缩放；Alt/Shift+滚轮 = 旋转；
 * 按钮 / 滑块缩放以选区（无选区时以环上一点）为锚，见 zoomStep；
 * 放大后拖拽环带以外的任意空白（或中键拖拽）= 平移，平移范围受 clampCenter 限制。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useElementSize } from '@vueuse/core'
import type { MapDocument, Range } from '../types'
import { useMapStore } from '../store'
import {
  TAU, angleToX, arcBandPath, arcPath, headSegmentIndex, pointAngle, polar, rangeFromBoundaries,
  rangeLength, unroll, unwrapNear, xToAngle,
} from '../core/coords'
import { stackLanes } from '../core/lanes'
import {
  enzymePriority, featurePriority, internalArcLabel, layoutCircularLabels, type CircularLabelInput,
  type CircularLabelLayout,
} from '../core/labels'
import { cutStyle, overviewCuts } from '../core/enzymes'
import { darken, featureColor, textOn, tint } from '../core/palette'
import { formatBp, ticks } from '../core/ticks'
import { MAP_FONT, textWidth, truncateText } from '../core/text'
import { hitKeyOf, resolveHit, setHoverFromEvent } from '../hits'
import MapTooltip from './MapTooltip.vue'

const props = withDefaults(defineProps<{
  doc?: MapDocument
  /** 固定边长（px）；缺省随容器宽度 */
  size?: number
  /** 随容器缩放时的上限（px） */
  maxSize?: number
  interactive?: boolean
}>(), { interactive: true, maxSize: 900 })

const { store, own } = useMapStore(() => props.doc)
/** textPath 引用的 id 需全页唯一（页面上可能有多个图谱） */
const uid = `cm${++instanceCounter}`
const wrap = ref<HTMLElement | null>(null)
const svgEl = ref<SVGSVGElement | null>(null)
const { width: wrapW } = useElementSize(wrap)

/** 下限 240：窄容器（手机上的结果页卡片里只有 ~260 px）按实际宽度排版，而不是先按 300 排再被 CSS 缩小、字变得更小 */
const MIN_SIZE = 240
const S = computed(() => Math.round(props.size ?? Math.min(props.maxSize, Math.max(MIN_SIZE, wrapW.value || 560))))
const cx = computed(() => S.value / 2)
const cy = computed(() => S.value / 2)
/** 窄屏（手机）：小一号字、更窄的标签栏，把空间让给环 */
const compact = computed(() => S.value < 440)
const FONT = computed(() => (compact.value ? 10 : 11))
/** 酶标签里切点位置的字号（小一号、灰色） */
const POS_FONT = computed(() => FONT.value - 1.5)
const showPos = computed(() => !compact.value)
/** 纯文字标签行高 / 色块标签（环外特征名）行高与左右内边距 */
const ROW_H = computed(() => FONT.value + 3)
const CHIP_H = computed(() => FONT.value + 6)
const CHIP_PAD = 5
/** 同一切点合并成一行时最多列出几个酶名，其余写 +N */
const GROUP_MAX = 3
/** 概览图最多画这么多个切点（超出时只保留少切点酶，见 overviewCuts） */
const MAX_OVERVIEW_CUTS = 360
const overview = computed(() => overviewCuts(store.cuts.value, MAX_OVERVIEW_CUTS))
const hasPrimers = computed(() => store.primers.value.length > 0)

/** 同一切点的酶合并为一个标签（SnapGene：`EagI - NotI (423)`） */
interface CutGroup { key: string; cut: number; members: CutGeom[] }
const cutGroups = computed<CutGroup[]>(() => {
  const m = new Map<number, CutGeom[]>()
  for (const c of cutGeoms.value) {
    const g = m.get(c.cut)
    if (g) g.push(c)
    else m.set(c.cut, [c])
  }
  return [...m.entries()].map(([cut, members]) => {
    members.sort((a, b) => a.enzyme.cut_count - b.enzyme.cut_count || a.name.localeCompare(b.name))
    return { key: members[0].key, cut, members }
  })
})
/** 酶标签各段（名字可单独悬停）及总宽 */
function groupParts(g: CutGroup) {
  const shown = g.members.slice(0, GROUP_MAX)
  const parts = shown.map((c, i) => ({
    key: c.key, text: (i ? ' - ' : '') + c.name, name: c.name, bold: c.weight > 400, color: c.color,
  }))
  const more = g.members.length - shown.length
  const extra = more > 0 ? ` +${more}` : ''
  // 窄屏不写切点位置（省出标签栏宽度给环；位置在悬停提示里）
  const pos = showPos.value ? ` (${g.cut})` : ''
  let w = 0
  for (const p of parts) w += textWidth(p.text, FONT.value, p.bold)
  w += textWidth(extra, FONT.value) + textWidth(pos, POS_FONT.value)
  return { parts, extra, pos: String(g.cut), w }
}
/**
 * 标签栏宽度按实际最长标签取（上限为边长的 26%/22%，更长的截断）：
 * 固定按 26% 留白会让短标签的质粒环缩到很小（pET-28a 在 640 px 里半径仅 ~130）。
 */
const labelMargin = computed(() => {
  const cap = S.value * (compact.value ? 0.22 : 0.26)
  if (!store.show.labels) return Math.min(cap, compact.value ? 22 : 30)
  let maxW = 0
  for (const f of store.features.value) maxW = Math.max(maxW, textWidth(f.name, FONT.value) + 2 * CHIP_PAD)
  for (const p of store.primers.value) maxW = Math.max(maxW, textWidth(p.name, FONT.value))
  for (const c of overview.value.shown) {
    maxW = Math.max(maxW, textWidth(c.name, FONT.value, c.enzyme.cut_count === 1) + (showPos.value ? textWidth(` (${c.cut})`, POS_FONT.value) : 0))
  }
  return Math.min(cap, Math.max(compact.value ? 44 : 56, maxW + 12))
})
/** 标签曲线半径（不拥挤时标签落在这个圆上） */
const labelR = computed(() => S.value / 2 - labelMargin.value)
/** 主链半径：标签圆向内留出 切线(10) + 引物环 + 引线外伸(6) + 半行高 + 间隙 */
const R0 = computed(() => labelR.value - (compact.value ? 24 : 28) - (hasPrimers.value ? 12 : 0))
/** 环上所有图元的最外半径：引线从这里折向标签，且不再回到圈内 */
const fanR = computed(() => R0.value + 12 + (hasPrimers.value ? 12 : 0))
const laneH = computed(() => Math.min(18, Math.max(10, S.value * 0.027)))
/**
 * 主链两条线的间距（SnapGene 式双链）：随特征厚度缩放，约为其 0.4，
 * 特征第 0 道正好骑在双链中线上（上下各露出约 30%），比例更协调。
 */
const RAIL = computed(() => Math.round(Math.min(7, Math.max(4, laneH.value * 0.4)) * 2) / 2)
/** 双链中线半径：第 0 道特征 [R0 - laneH, R0] 以它为中心 */
const Rb = computed(() => R0.value - laneH.value / 2)
const strandOut = computed(() => Rb.value + RAIL.value / 2)
const strandIn = computed(() => Rb.value - RAIL.value / 2)
const laneGap = 2
const L = computed(() => store.L.value)

// ---------------- 缩放 / 旋转 ----------------
const zoom = ref(1)
const pan = ref({ x: 0, y: 0 })
/** 竖直裁切后的可视区 [top, bottom]（去掉方形画布上下没有内容的空白），见 crop */
const crop = ref<[number, number]>([0, 0])
const cropTop = computed(() => (crop.value[1] > crop.value[0] ? crop.value[0] : 0))
const cropH = computed(() => (crop.value[1] > crop.value[0] ? crop.value[1] - crop.value[0] : S.value))
/** 视图中心（未平移时） */
const vcx = computed(() => cx.value)
const vcy = computed(() => cropTop.value + cropH.value / 2)
const MAX_ZOOM = 20
/**
 * svg 的像素宽度。未放大时是 S×cropH 的图；放大后占满容器整宽——否则在比 maxSize 宽的
 * 面板里，放大的环会被硬裁在 S 宽的方框左右两侧，旁边却是空白。
 */
function svgWidthAt(z: number) {
  return z > 1 && props.size === undefined ? Math.max(S.value, Math.floor(wrapW.value || 0)) : S.value
}
const svgW = computed(() => svgWidthAt(zoom.value))
/** 可视区宽高（用户坐标）：1 个用户单位在缩放 z 时约等于 z 像素 */
const viewW = computed(() => svgW.value / zoom.value)
const viewH = computed(() => cropH.value / zoom.value)
const viewBox = computed(() => {
  const x = vcx.value + pan.value.x - viewW.value / 2
  const y = vcy.value + pan.value.y - viewH.value / 2
  return `${r2(x)} ${r2(y)} ${r2(viewW.value)} ${r2(viewH.value)}`
})
function r2(n: number) { return Math.round(n * 100) / 100 }
/** 当前视图中心（用户坐标） */
function viewCenter(): [number, number] {
  return [vcx.value + pan.value.x, vcy.value + pan.value.y]
}
/**
 * 限制缩放 z 下的视图中心：
 * 1. 环不能整个移出视野——中心到圆心的距离不超过 外圈半径 + 0.45×可视区短边；
 * 2. 不平移到内容框（方形画布 × 竖直裁切）以外；可视区比内容还宽/高时居中。
 */
function clampCenter(c: [number, number], z: number): [number, number] {
  if (z <= 1) return [vcx.value, vcy.value]
  const w = svgWidthAt(z) / z
  const h = cropH.value / z
  let [x, y] = c
  const dmax = fanR.value + 0.45 * Math.min(w, h)
  const dx = x - cx.value
  const dy = y - cy.value
  const d = Math.hypot(dx, dy)
  if (d > dmax) {
    x = cx.value + (dx * dmax) / d
    y = cy.value + (dy * dmax) / d
  }
  const axis = (v: number, lo: number, hi: number, span: number) =>
    (span >= hi - lo ? (lo + hi) / 2 : Math.min(hi - span / 2, Math.max(lo + span / 2, v)))
  return [axis(x, 0, S.value, w), axis(y, cropTop.value, cropTop.value + cropH.value, h)]
}
function setCenter(c: [number, number], z: number) {
  const [x, y] = clampCenter(c, z)
  pan.value = z <= 1 ? { x: 0, y: 0 } : { x: x - vcx.value, y: y - vcy.value }
}
/** 缩放到 z；给定 focus 时保持该点在屏幕上不动（Ctrl+滚轮），否则保持视图中心 */
function setZoom(z: number, focus?: [number, number]) {
  const nz = Math.min(MAX_ZOOM, Math.max(1, z))
  let c = viewCenter()
  if (focus) {
    // 保持指针下的点不动：c' = P - (P - c)·z/z'
    const k = zoom.value / nz
    c = [focus[0] - (focus[0] - c[0]) * k, focus[1] - (focus[1] - c[1]) * k]
  }
  zoom.value = nz
  setCenter(c, nz)
}
/**
 * 按钮 / 滑块放大时的锚点（环上的一点，SnapGene / Benchling 式）：
 * 有选区取选区中点、只有光标取光标处；否则视图中心落在环内空白（名称、刻度数字）时取离它最近的
 * 环上点（正中时取 12 点钟）；视图已在环带或其外侧（用户平移过去的）时返回 null，保持当前中心。
 * 锚点半径取环的外缘 fanR：特征在它内侧、标签在它外侧，放大后两者都在视野里。
 */
function zoomAnchor(): [number, number] | null {
  const s = store.selection.value
  const caret = store.caret.value
  if (s || caret !== null) {
    const u = s ? unroll(s, L.value) : { from: caret!, to: caret! }
    return polar(cx.value, cy.value, fanR.value, ang((u.from + u.to) / 2))
  }
  const [x, y] = viewCenter()
  const d = Math.hypot(x - cx.value, y - cy.value)
  if (zoom.value > 1 && d >= featInner.value) return null
  const a = d < 1 ? 0 : pointAngle(cx.value, cy.value, x, y)
  return polar(cx.value, cy.value, fanR.value, a)
}
/** 按钮 / 滑块缩放：放大时把锚点移到视图中心（不再对着圆心放大、只剩中间的名称）；缩小时保持中心 */
function zoomStep(z: number) {
  const nz = Math.min(MAX_ZOOM, Math.max(1, z))
  const anchor = nz > zoom.value ? zoomAnchor() : null
  if (!anchor) return setZoom(nz)
  zoom.value = nz
  setCenter(anchor, nz)
}
const zoomLog = computed({
  get: () => Math.log2(zoom.value),
  set: (v: number) => zoomStep(2 ** v),
})
const MAX_ZOOM_LOG = Math.log2(MAX_ZOOM)
function resetView() {
  zoom.value = 1
  pan.value = { x: 0, y: 0 }
  store.rotation.value = 0
}
const rotDeg = computed({
  get: () => Math.round(((store.rotation.value * 180) / Math.PI) % 360),
  set: (v: number) => { store.rotation.value = (v * Math.PI) / 180 },
})

const ang = (x: number) => xToAngle(x, L.value, store.rotation.value)

// ---------------- 特征分道 ----------------
const maxLanes = computed(() => Math.max(1, Math.min(8, Math.floor((R0.value * 0.55 - 6) / (laneH.value + laneGap)))))
const featLanes = computed(() => stackLanes(
  store.features.value.map((f) => ({ id: f.id, start: f.start, end: f.end })), L.value,
  // 首尾相接的特征（如 lacI promoter 695..772 与 lacI 773..）留在同一道，与 SnapGene 一致
  { circular: true, gap: 0, maxLanes: maxLanes.value },
))
function laneRadii(lane: number): [number, number] {
  // 第 0 道骑在双链上，更多的道依次向内
  const outer = R0.value - lane * (laneH.value + laneGap)
  return [outer - laneH.value, outer]
}
const featInner = computed(() => laneRadii(Math.max(0, featLanes.value.laneCount - 1))[0])

interface FeatGeom {
  id: string; key: string; name: string; color: string; stroke: string; paths: string[]; connector: string | null
  r1: number; r2: number; mid: number; internal: { path: string; text: string; color: string; size: number } | null
}
const featGeoms = computed<FeatGeom[]>(() => {
  const out: FeatGeom[] = []
  const headLen = laneH.value * 0.9
  for (const f of store.features.value) {
    const lane = featLanes.value.lanes.get(f.id)
    if (lane === undefined) continue
    const [r1, rr2] = laneRadii(lane)
    const rm = (r1 + rr2) / 2
    const color = featureColor(f)
    const headA = headLen / rm
    const segs: Range[] = f.segments?.length ? f.segments : [{ start: f.start, end: f.end }]
    // 箭头只画在 3' 端所在段
    const headSeg = headSegmentIndex(segs, f, f.strand)
    const paths = segs.map((s, i) => {
      const u = unroll(s, L.value)
      const arrow = i === headSeg ? (f.strand === 1 ? 1 : f.strand === -1 ? -1 : 0) : 0
      const a1 = ang(u.from)
      const a2 = ang(Math.max(u.to, u.from + L.value / 2000))
      return arcBandPath(cx.value, cy.value, r1, rr2, a1, a2, arrow as 0 | 1 | -1, headA)
    })
    let connector: string | null = null
    if (segs.length > 1) {
      const u = unroll(f, L.value)
      connector = arcPath(cx.value, cy.value, rm, ang(u.from), ang(u.to))
    }
    const u = unroll(f, L.value)
    const a1 = ang(u.from)
    const a2 = ang(u.to)
    const mid = (a1 + a2) / 2
    let internal: FeatGeom['internal'] = null
    const size = Math.max(8, Math.min(FONT.value, laneH.value - 5))
    const fit = store.show.labels ? internalArcLabel(f.name, size, a1, a2, r1, rr2, f.strand ? headLen : 0) : null
    if (fit) internal = { path: arcPath(cx.value, cy.value, fit.r, a1, a2, fit.flip), text: f.name, color: textOn(color), size }
    out.push({ id: f.id, key: `f:${f.id}`, name: f.name, color, stroke: darken(color), paths, connector, r1, r2: rr2, mid, internal })
  }
  return out
})

// ---------------- ORF / 引物 ----------------
const orfGeoms = computed(() => {
  if (!store.orfs.value.length) return []
  const items = store.orfs.value.map((o, i) => ({ id: String(i), start: o.start, end: o.end }))
  const lanes = stackLanes(items, L.value, { circular: true, maxLanes: 3 })
  const base = featInner.value - 8
  return store.orfs.value.flatMap((o, i) => {
    const lane = lanes.lanes.get(String(i))
    if (lane === undefined) return []
    const r2v = base - lane * 6
    const u = unroll(o, L.value)
    return [{ key: `o:${i}`, d: arcBandPath(cx.value, cy.value, r2v - 3, r2v, ang(u.from), ang(u.to), o.strand === 1 ? 1 : -1, 6 / r2v), color: ['#7a8599', '#8d7aa6', '#6f9a8a'][o.frame] }]
  })
})
const orfInner = computed(() => featInner.value - 8 - (orfGeoms.value.length ? 3 * 6 : 0))

const primerGeoms = computed(() => {
  const items = store.primers.value.map((p) => ({ id: p.id, start: p.start, end: p.end }))
  const lanes = stackLanes(items, L.value, { circular: true, maxLanes: 2 })
  return store.primers.value.flatMap((p) => {
    const lane = lanes.lanes.get(p.id)
    if (lane === undefined) return []
    const r1 = R0.value + 14 + lane * 6
    const u = unroll(p, L.value)
    return [{ id: p.id, name: p.name, key: `p:${p.id}`, mid: (ang(u.from) + ang(u.to)) / 2, r: r1 + 4, d: arcBandPath(cx.value, cy.value, r1, r1 + 4, ang(u.from), ang(Math.max(u.to, u.from + L.value / 1000)), p.strand === 1 ? 1 : -1, 5 / r1) }]
  })
})

// ---------------- 酶切 ----------------
type CutGeom = ReturnType<typeof cutGeomOf>
function cutGeomOf(c: (typeof overview.value.shown)[number]) {
  const st = cutStyle(c)
  const a = ang(c.cut)
  // 切线从外链伸出（画在特征之下，被特征遮住的部分自然隐去）
  const [x1, y1] = polar(cx.value, cy.value, strandOut.value, a)
  const [x2, y2] = polar(cx.value, cy.value, R0.value + 10, a)
  return { ...c, key: `e:${c.id}`, a, x1, y1, x2, y2, color: st.color, weight: st.weight, dash: st.dash }
}
const cutGeoms = computed(() => overview.value.shown.map(cutGeomOf))
// ---------------- 外置标签 ----------------
/** 渲染用：每个外置标签的种类与内容（布局引擎只关心宽高与角度） */
type LabelInfo =
  | { kind: 'chip'; fill: string; stroke: string; ink: string; members: string[] }
  | { kind: 'enzyme'; parts: ReturnType<typeof groupParts>['parts']; extra: string; pos: string; members: string[] }
  | { kind: 'text'; color: string; members: string[] }
const labelData = computed(() => {
  const info = new Map<string, LabelInfo>()
  const inputs: CircularLabelInput[] = []
  if (!store.show.labels) return { info, inputs }
  const maxW = labelMargin.value - 10
  const font = FONT.value
  for (const g of featGeoms.value) {
    if (g.internal) continue
    const f = store.doc.value.features.find((x) => x.id === g.id)!
    const text = truncateText(g.name, maxW - 2 * CHIP_PAD, font)
    if (!text) continue
    inputs.push({
      id: g.key, text, width: textWidth(text, font) + 2 * CHIP_PAD, height: CHIP_H.value,
      angle: g.mid, radius: g.r2, priority: featurePriority(f.type, rangeLength(f, L.value)),
    })
    info.set(g.key, { kind: 'chip', fill: tint(g.color, 0.8), stroke: g.color, ink: darken(g.color, 0.62), members: [g.key] })
  }
  for (const g of cutGroups.value) {
    const gp = groupParts(g)
    const c0 = g.members[0]
    inputs.push({
      id: g.key, text: gp.parts.map((p) => p.text).join('') + gp.extra, width: gp.w, height: ROW_H.value,
      angle: c0.a, radius: R0.value + 10, priority: Math.max(...g.members.map((c) => enzymePriority(c.enzyme.cut_count))),
    })
    info.set(g.key, { kind: 'enzyme', parts: gp.parts, extra: gp.extra, pos: gp.pos, members: g.members.map((c) => c.key) })
  }
  for (const p of primerGeoms.value) {
    const text = truncateText(p.name, maxW, font)
    if (!text) continue
    inputs.push({ id: p.key, text, height: ROW_H.value, angle: p.mid, radius: p.r, priority: 5e7 })
    info.set(p.key, { kind: 'text', color: '#2f6aa8', members: [p.key] })
  }
  return { info, inputs }
})
/** "重要"标签的优先级下限：双切点酶及以上（特征、引物、单切点酶） */
const IMPORTANT = enzymePriority(2)
/** 拥挤时允许画布在竖直方向比宽度高出这么多（标签堆向上/下延伸） */
const extraHalf = computed(() => S.value * (compact.value ? 0.1 : 0.18))
const labelLayout = computed<CircularLabelLayout>(() => {
  const { inputs } = labelData.value
  if (!inputs.length) return { placed: [], hidden: [] }
  const base = {
    cx: cx.value, cy: cy.value, labelRadius: labelR.value, fontSize: FONT.value, lineHeight: ROW_H.value,
    fanRadius: fanR.value, stretch: true, xMin: 2, xMax: S.value - 2, fanSlope: compact.value ? 0.15 : 0.3,
    // 标签离锚点太远时宁可隐藏（列入"无法显示"），也不画横穿全图的长引线
    maxShift: Math.max(80, labelR.value * 0.85),
  }
  const flat = layoutCircularLabels(inputs, { ...base, halfHeight: S.value / 2 - 4 })
  // 只有重要标签（特征、引物、单/双切点酶）放不下时才把画布拉高；拉高后多切点酶只保留
  // 原本就放得下的那些——否则多出的空间会被大量多切点酶填满，图反而更乱
  const prio = new Map(inputs.map((i) => [i.id, i.priority]))
  const important = (id: string) => (prio.get(id) ?? 0) >= IMPORTANT
  const flatHidden = new Set(flat.hidden)
  if (!flat.hidden.some(important)) return flat
  const keep = inputs.filter((i) => i.priority >= IMPORTANT || !flatHidden.has(i.id))
  const tall = layoutCircularLabels(keep, { ...base, halfHeight: S.value / 2 - 4 + extraHalf.value })
  const tallHidden = new Set(tall.hidden)
  const hiddenImportant = (h: Set<string>) => inputs.filter((i) => i.priority >= IMPORTANT && h.has(i.id)).length
  if (hiddenImportant(tallHidden) >= hiddenImportant(flatHidden)) return flat
  const kept = new Set(keep)
  return { placed: tall.placed, hidden: inputs.filter((i) => tallHidden.has(i.id) || !kept.has(i)).map((i) => i.id) }
})

// 无法显示项上报（标签放不下 + 超出分道上限）。旋转时每帧都会重排，但隐藏集合通常不变：
// 集合没变就不重报，免得每帧重建列表、触发查看器重渲染
let lastUnshown = ''
let lastDoc: unknown = null
const titleCache = new Map<string, string>()
const NUM = new Intl.NumberFormat('en-US')
watch([labelLayout, featLanes], () => {
  const sig = labelLayout.value.hidden.join('|') + '#' + featLanes.value.overflow.join('|') + '#' + overview.value.dropped.length
    + '#' + labelLayout.value.placed.filter((l) => (labelData.value.info.get(l.id)?.members.length ?? 0) > GROUP_MAX).map((l) => l.id).join('|')
  if (sig === lastUnshown && lastDoc === store.doc.value) return
  if (lastDoc !== store.doc.value) titleCache.clear()
  lastUnshown = sig
  lastDoc = store.doc.value
  const cutAt = new Map(cutGeoms.value.map((c) => [c.key, c.cut]))
  const info = labelData.value.info
  // 旋转时隐藏集合会随角度变，这里每帧可能跑：标题按键缓存，数字用复用的格式器（toLocaleString 很慢）
  const nameOf = (k: string) => {
    let t = titleCache.get(k)
    if (t === undefined) {
      t = resolveHit(store, k)?.title ?? k
      titleCache.set(k, t)
    }
    const at = cutAt.get(k)
    return at === undefined ? t : `${t} @${NUM.format(at)}`
  }
  const items = labelLayout.value.hidden.flatMap((id) => info.get(id)?.members ?? [id])
    .map((k) => ({ id: k, name: nameOf(k), reason: '标签空间不足' }))
  // 合并标签里写成 +N 的酶也算"未在图上标出"
  for (const l of labelLayout.value.placed) {
    const inf = info.get(l.id)
    if (inf?.kind !== 'enzyme') continue
    for (const k of inf.members.slice(GROUP_MAX)) items.push({ id: k, name: nameOf(k), reason: '同一切点酶过多，合并为 +N' })
  }
  for (const id of featLanes.value.overflow) {
    const f = store.doc.value.features.find((x) => x.id === id)
    items.push({ id: `f:${id}`, name: f?.name ?? id, reason: '特征分道已满' })
  }
  for (const d of overview.value.dropped) {
    items.push({ id: `e:${d.name}`, name: `${d.name}（${d.count} 处）`, reason: '切点过多，概览图略去（序列视图可见）' })
  }
  store.setUnshown('circular', items)
}, { immediate: true })
onBeforeUnmount(() => store.setUnshown('circular', []))

/** 渲染用的外置标签：布局结果 + 内容 */
const labelViews = computed(() => labelLayout.value.placed.map((l) => {
  const inf = labelData.value.info.get(l.id)!
  const hot = hoverKey.value !== null && inf.members.includes(hoverKey.value)
  const leader = l.leader.map((p) => p.map(r2).join(',')).join(' ')
  // 文字起点：色块标签留内边距；右列 start、左列 end
  const tx = inf.kind === 'chip' ? l.x + l.side * CHIP_PAD : l.x
  return { ...l, inf, hot, leaderPts: leader, tx, anchor: l.side === 1 ? 'start' : 'end' }
}))

// ---------------- 刻度 ----------------
/** 主刻度（带数字）画在主链两线之间并向内伸出一点；次刻度只在两线之间 */
const tickGeoms = computed(() => {
  const rNum = Math.min(orfInner.value, featInner.value) - 11
  // 刻度数随数字所在圆周长走；圈太小时只画刻度不写数字（否则数字挤在中心、压住名称）
  const showNum = rNum >= 42
  const target = Math.max(4, Math.min(Math.round(S.value / 70), Math.round((TAU * Math.max(rNum, 20)) / 60)))
  const t = ticks(1, L.value, target)
  const major = t.positions.filter((p) => p < L.value - t.step * 0.3).map((p) => {
    const a = ang(p)
    // 主刻度：从外链穿过双链向内伸出一点
    const [x1, y1] = polar(cx.value, cy.value, strandOut.value, a)
    const [x2, y2] = polar(cx.value, cy.value, strandIn.value - 3.5, a)
    const [tx, ty] = polar(cx.value, cy.value, rNum, a)
    return { p, x1, y1, x2, y2, tx, ty, label: showNum ? formatBp(p) : '' }
  })
  // 次刻度：主步长的 1/5（或 1/2），圈上间隔至少 5 px 才画
  const sub = t.step % 5 === 0 ? t.step / 5 : t.step / 2
  const minor: { p: number; x1: number; y1: number; x2: number; y2: number }[] = []
  if (sub >= 1 && (TAU * R0.value * sub) / L.value >= 5) {
    for (let p = sub; p < L.value; p += sub) {
      if (p % t.step === 0) continue
      const a = ang(p)
      const [x1, y1] = polar(cx.value, cy.value, strandOut.value, a)
      const [x2, y2] = polar(cx.value, cy.value, strandIn.value, a)
      minor.push({ p, x1, y1, x2, y2 })
    }
  }
  const seg = (t: { x1: number; y1: number; x2: number; y2: number }) => `M${r2(t.x1)} ${r2(t.y1)}L${r2(t.x2)} ${r2(t.y2)}`
  return { major, majorPath: major.map(seg).join(''), minorPath: minor.map(seg).join('') }
})
const originMark = computed(() => {
  const a = ang(0)
  const [x1, y1] = polar(cx.value, cy.value, strandIn.value - 5, a)
  const [x2, y2] = polar(cx.value, cy.value, strandOut.value + 5, a)
  return { x1, y1, x2, y2 }
})

// ---------------- 选区 / 光标 / 搜索 / 悬停 ----------------
const selInner = computed(() => Math.max(16, Math.min(orfInner.value, featInner.value) - 4))
const selectionPath = computed(() => {
  const s = store.selection.value
  if (!s) return ''
  const u = unroll(s, L.value)
  return arcBandPath(cx.value, cy.value, selInner.value, R0.value + 12, ang(u.from), ang(u.to))
})
const caretLine = computed(() => {
  if (store.selection.value || store.caret.value === null) return null
  const a = ang(store.caret.value)
  const [x1, y1] = polar(cx.value, cy.value, selInner.value, a)
  const [x2, y2] = polar(cx.value, cy.value, R0.value + 12, a)
  return { x1, y1, x2, y2 }
})
const searchGeoms = computed(() => store.search.hits.slice(0, 500).map((h, i) => {
  const u = unroll(h, L.value)
  const pad = L.value / 1500
  return { key: `s:${i}`, current: i === store.search.index, d: arcBandPath(cx.value, cy.value, R0.value + 1, R0.value + 6, ang(u.from - pad), ang(u.to + pad)) }
}))
const hoverKey = computed(() => store.hoverTarget.value?.id ?? null)
const hoverPath = computed(() => {
  const h = store.hoverTarget.value
  if (!h?.range || h.id.startsWith('f:')) return ''
  const u = unroll(h.range, L.value)
  return arcBandPath(cx.value, cy.value, selInner.value, R0.value + 12, ang(u.from), ang(Math.max(u.to, u.from + L.value / 1000)))
})

// ---------------- 指针交互 ----------------
/**
 * touch-wait：触屏未放大时按下后先等长按，期间手指一动就交给浏览器滚动页面；
 * pinch：双指缩放。
 */
type Mode = 'none' | 'select' | 'rotate' | 'pan' | 'touch-wait' | 'pinch'
let mode: Mode = 'none'
let anchorX = 0
let prevX = 0
let startAngle = 0
let startRot = 0
let startPan = { x: 0, y: 0, px: 0, py: 0 }
let moved = false
let downKey: string | null = null

function svgPoint(e: { clientX: number; clientY: number }): [number, number] {
  const svg = svgEl.value
  const ctm = svg?.getScreenCTM?.()
  if (svg && ctm && typeof ctm.inverse === 'function' && typeof svg.createSVGPoint === 'function') {
    const pt = svg.createSVGPoint()
    // happy-dom 20 的 SVGPoint 没有 matrixTransform，测试环境走下方的包围盒换算
    if (pt && typeof pt.matrixTransform === 'function') {
      pt.x = e.clientX
      pt.y = e.clientY
      const p = pt.matrixTransform(ctm.inverse())
      return [p.x, p.y]
    }
  }
  const rect = svg?.getBoundingClientRect()
  if (!rect || !rect.width) return [cx.value, cy.value - R0.value]
  const w = viewW.value
  const h = viewH.value
  return [vcx.value + pan.value.x - w / 2 + ((e.clientX - rect.left) / rect.width) * w,
    vcy.value + pan.value.y - h / 2 + ((e.clientY - rect.top) / rect.height) * h]
}

// ---------------- 触屏（手机） ----------------
// 未放大时 svg 用 touch-action: pan-y，单指在图上滑动照常滚动页面；
// 长按（LONG_PRESS_MS 内移动不超过 TOUCH_SLOP）后才进入选区/旋转；轻点仍是选中/落光标；
// 双指捏合缩放。放大后（zoom>1）touch-action: none，单指即平移/选区（与桌面一致）。
const LONG_PRESS_MS = 350
const TOUCH_SLOP = 8
const touchPts = new Map<number, { x: number; y: number }>()
let waitMode: Mode = 'none'
let waitStart = { x: 0, y: 0 }
let longPressTimer: ReturnType<typeof setTimeout> | undefined
let pinchStart = { d: 1, z: 1 }
const touchAction = computed(() => (props.interactive && zoom.value > 1 ? 'none' : 'pan-y'))

function clearLongPress() {
  if (longPressTimer !== undefined) clearTimeout(longPressTimer)
  longPressTimer = undefined
}
function pinchDistance(): number {
  const [a, b] = [...touchPts.values()]
  return Math.max(1, Math.hypot(a.x - b.x, a.y - b.y))
}
/** 手势进行中（长按后选区/旋转、放大后平移、双指缩放）阻止浏览器滚动；等待长按期间放行 */
function onTouchMove(e: TouchEvent) {
  if (!e.cancelable) return
  if (e.touches.length >= 2 || (mode !== 'none' && mode !== 'touch-wait')) e.preventDefault()
}
onMounted(() => svgEl.value?.addEventListener('touchmove', onTouchMove, { passive: false }))
onBeforeUnmount(() => {
  clearLongPress()
  svgEl.value?.removeEventListener('touchmove', onTouchMove)
})

function onPointerDown(e: PointerEvent) {
  if (props.interactive && e.pointerType === 'touch') {
    touchPts.set(e.pointerId, { x: e.clientX, y: e.clientY })
    if (touchPts.size === 2) {
      // 第二根手指：放弃单指手势，改为捏合缩放
      clearLongPress()
      mode = 'pinch'
      pinchStart = { d: pinchDistance(), z: zoom.value }
      ;(e.currentTarget as Element | null)?.setPointerCapture?.(e.pointerId)
      return
    }
    if (touchPts.size > 2) return
  }
  // 左键；放大后中键拖拽 = 平移
  const middle = e.button === 1 && zoom.value > 1
  if (!props.interactive || (e.button > 0 && !middle)) return
  if (middle) e.preventDefault()
  const [x, y] = svgPoint(e)
  const r = Math.hypot(x - cx.value, y - cy.value)
  const a = pointAngle(cx.value, cy.value, x, y)
  moved = false
  downKey = hitKeyOf(e.target)
  // 放大后：环带（选区半径到环外缘）以外、不在特征/标签上的任意位置拖拽都是平移（含中心区）
  const offBand = r < selInner.value - 6 || r > fanR.value + 6
  if (middle || (!e.shiftKey && !downKey && zoom.value > 1 && offBand)) {
    mode = 'pan'
    const [pcx, pcy] = viewCenter()
    startPan = { x: pcx, y: pcy, px: e.clientX, py: e.clientY }
  } else if (e.shiftKey || (!downKey && r < selInner.value - 6)) {
    mode = 'rotate'
    startAngle = a
    startRot = store.rotation.value
  } else {
    mode = 'select'
    anchorX = angleToX(a, L.value, store.rotation.value)
    prevX = anchorX
  }
  if (e.pointerType === 'touch' && zoom.value <= 1) {
    // 未放大的触屏：先不接管，等长按；页面滚动不受图谱影响
    waitMode = mode
    mode = 'touch-wait'
    waitStart = { x: e.clientX, y: e.clientY }
    clearLongPress()
    longPressTimer = setTimeout(() => {
      longPressTimer = undefined
      if (mode === 'touch-wait') mode = waitMode
    }, LONG_PRESS_MS)
  }
  ;(e.currentTarget as Element | null)?.setPointerCapture?.(e.pointerId)
}

function onPointerMove(e: PointerEvent) {
  if (e.pointerType === 'touch' && touchPts.has(e.pointerId)) touchPts.set(e.pointerId, { x: e.clientX, y: e.clientY })
  if (mode === 'pinch') {
    if (touchPts.size >= 2) {
      const [a, b] = [...touchPts.values()]
      setZoom(pinchStart.z * (pinchDistance() / pinchStart.d), svgPoint({ clientX: (a.x + b.x) / 2, clientY: (a.y + b.y) / 2 }))
    }
    return
  }
  if (mode === 'touch-wait') {
    // 长按前手指移动：这是滚动，交给浏览器（随后通常会收到 pointercancel）
    if (Math.hypot(e.clientX - waitStart.x, e.clientY - waitStart.y) > TOUCH_SLOP) {
      clearLongPress()
      mode = 'none'
    }
    return
  }
  if (mode === 'none') {
    setHoverFromEvent(store, e)
    return
  }
  const [x, y] = svgPoint(e)
  const a = pointAngle(cx.value, cy.value, x, y)
  if (mode === 'rotate') {
    store.rotation.value = startRot + (a - startAngle)
    moved = true
  } else if (mode === 'pan') {
    const k = viewW.value / Math.max(1, svgEl.value?.getBoundingClientRect().width || svgW.value)
    setCenter([startPan.x - (e.clientX - startPan.px) * k, startPan.y - (e.clientY - startPan.py) * k], zoom.value)
    moved = true
  } else if (mode === 'select') {
    const nx = unwrapNear(angleToX(a, L.value, store.rotation.value), prevX, L.value)
    prevX = nx
    if (Math.abs(nx - anchorX) >= 1) {
      moved = true
      store.select(rangeFromBoundaries(anchorX, nx, L.value, true))
    }
  }
}

function onPointerUp(e: PointerEvent) {
  touchPts.delete(e.pointerId)
  if (mode === 'pinch') {
    // 抬起一根手指即结束捏合；剩下的手指不再触发选区
    if (touchPts.size === 0) mode = 'none'
    return
  }
  if (mode === 'touch-wait') {
    // 长按前抬起 = 轻点：按原手势的"未移动"分支处理（选中特征 / 落光标）
    clearLongPress()
    mode = waitMode
  }
  if (mode === 'select' && !moved) {
    const info = resolveHit(store, downKey ?? hitKeyOf(e.target))
    if (info) info.select()
    else store.setCaret(Math.round(anchorX) % L.value)
  } else if (mode === 'select' && store.selection.value) {
    // 拖选结束：让序列/线性视图滚到选区（拖动过程中不滚，免得视图乱跳）
    store.navigateTo(store.selection.value)
  }
  mode = 'none'
}

/** 浏览器接管手势（触屏滚动/系统手势）或丢失捕获：放弃本次拖拽，不触发选择/光标 */
function onPointerCancel(e?: PointerEvent) {
  // 捏合时先抬起的手指也会丢失捕获，另一根手指还在：不算取消
  if (e?.type === 'lostpointercapture' && mode === 'pinch' && touchPts.size) return
  touchPts.clear()
  clearLongPress()
  if (mode === 'none') return
  mode = 'none'
  moved = false
  downKey = null
  store.hover.value = null
}

function onWheel(e: WheelEvent) {
  if (!props.interactive) return
  if (e.ctrlKey || e.metaKey) {
    e.preventDefault()
    setZoom(zoom.value * Math.exp(-e.deltaY * 0.0015), svgPoint(e))
  } else if (e.altKey || e.shiftKey) {
    e.preventDefault()
    store.rotation.value += (e.deltaY || e.deltaX) * 0.003
  }
}

function onLeave() {
  if (mode === 'none') store.hover.value = null
}

const NAME_FONT = computed(() => (compact.value ? 14 : 16))
const centerName = computed(() => truncateText(store.doc.value.name, Math.max(60, selInner.value * 1.6), NAME_FONT.value, true))
const nameFits = computed(() => selInner.value >= 40 && textWidth(centerName.value, NAME_FONT.value, true) < selInner.value * 2)
const lengthFits = computed(() => selInner.value >= 26)

// ---------------- 竖直裁切 ----------------
/** 内容（环 + 外圈刻度/引物 + 标签框）的竖直范围 */
const contentBounds = computed<[number, number]>(() => {
  const ring = fanR.value + 2
  let top = cy.value - ring
  let bottom = cy.value + ring
  for (const l of labelLayout.value.placed) {
    top = Math.min(top, l.box.y)
    bottom = Math.max(bottom, l.box.y + l.box.h)
  }
  const pad = 8
  // 拥挤时标签可以堆到方形画布之外（画布变高），见 extraHalf
  const lim = extraHalf.value
  return [Math.max(-lim, top - pad), Math.min(S.value + lim, bottom + pad)]
})
// 旋转进行中只扩不缩（标签随角度变化，若跟着收缩，下面的内容会上下跳）
let rotTimer: ReturnType<typeof setTimeout> | undefined
const rotating = ref(false)
watch(() => store.rotation.value, () => {
  rotating.value = true
  clearTimeout(rotTimer)
  rotTimer = setTimeout(() => { rotating.value = false }, 400)
})
onBeforeUnmount(() => clearTimeout(rotTimer))
watch([contentBounds, rotating], ([[t, b]]) => {
  const [ot, ob] = crop.value
  crop.value = rotating.value && ob > ot ? [Math.min(ot, t), Math.max(ob, b)] : [t, b]
}, { immediate: true })
const svgH = computed(() => Math.round(cropH.value))

defineExpose({ svgEl, resetView, setZoom, zoomStep, zoom })
</script>

<template>
  <div ref="wrap" class="cmap">
    <svg
      ref="svgEl" :class="['cmap-svg', { 'has-feat-hover': hoverKey?.startsWith('f:') }]" data-map-export="circular" xmlns="http://www.w3.org/2000/svg"
      :viewBox="viewBox" :width="svgW" :height="svgH" role="img" :aria-label="`${store.doc.value.name} 环形图谱`"
      :font-family="MAP_FONT"
      :style="{ touchAction }"
      @pointerdown="onPointerDown" @pointermove="onPointerMove" @pointerup="onPointerUp"
      @pointercancel="onPointerCancel" @lostpointercapture="onPointerCancel"
      @pointerleave="onLeave" @wheel="onWheel"
    >
      <defs>
        <!-- 悬停/选中特征的柔和外发光（内联在 SVG 里，导出不依赖页面样式） -->
        <filter :id="`${uid}-glow`" x="-20%" y="-20%" width="140%" height="140%" color-interpolation-filters="sRGB">
          <feGaussianBlur in="SourceAlpha" stdDeviation="2.2" result="b" />
          <feFlood flood-color="#4f46e5" flood-opacity="0.35" />
          <feComposite in2="b" operator="in" />
          <feMerge><feMergeNode /><feMergeNode in="SourceGraphic" /></feMerge>
        </filter>
      </defs>

      <!-- 选区与悬停底色（画在最下层） -->
      <path v-if="hoverPath" :d="hoverPath" fill="#f59e0b" fill-opacity="0.12" class="cmap-hover" />
      <path v-if="selectionPath" :d="selectionPath" fill="#4f46e5" fill-opacity="0.12" stroke="#4f46e5" stroke-opacity="0.45" stroke-width="0.8" class="cmap-selection" />

      <!-- 主链：SnapGene 式双线，特征骑在双链上，刻度画在两线之间 -->
      <circle :cx="cx" :cy="cy" :r="strandOut" fill="none" stroke="#3a4048" stroke-width="1.1" class="cmap-strand" />
      <circle :cx="cx" :cy="cy" :r="strandIn" fill="none" stroke="#3a4048" stroke-width="1.1" class="cmap-strand" />
      <line :x1="originMark.x1" :y1="originMark.y1" :x2="originMark.x2" :y2="originMark.y2" stroke="#2b3038" stroke-width="1.8" stroke-linecap="round" />
      <g class="cmap-ticks">
        <!-- 刻度线合成两条 path（几百个 line 元素在旋转时逐个打补丁太慢） -->
        <path v-if="tickGeoms.minorPath" :d="tickGeoms.minorPath" stroke="#8b929b" stroke-width="0.6" fill="none" />
        <path :d="tickGeoms.majorPath" stroke="#2b3038" stroke-width="1" fill="none" />
        <template v-for="t in tickGeoms.major" :key="t.p">
          <text v-if="t.label" :x="t.tx" :y="t.ty" :font-size="compact ? 8.5 : 9.5" fill="#8b929b" text-anchor="middle" dominant-baseline="central">{{ t.label }}</text>
        </template>
      </g>

      <!-- 搜索命中 -->
      <path v-for="h in searchGeoms" :key="h.key" :d="h.d" :data-hit="h.key"
            :fill="h.current ? '#f97316' : '#facc15'" class="cmap-search" />

      <!-- 酶切刻度 -->
      <line v-for="c in cutGeoms" :key="c.key" :data-hit="c.key" :x1="c.x1" :y1="c.y1" :x2="c.x2" :y2="c.y2"
            :stroke="c.color" :stroke-width="c.weight > 400 ? 1.4 : 0.9" :stroke-dasharray="c.dash" stroke-linecap="round"
            :class="['cmap-cut', { 'is-blocked': c.blocked.length }]" />

      <!-- 特征 -->
      <g v-for="g in featGeoms" :key="g.key" :data-hit="g.key"
         :class="['cmap-feat', { 'is-hover': hoverKey === g.key, 'is-selected': store.selectedFeatureId.value === g.id }]"
         :filter="hoverKey === g.key || store.selectedFeatureId.value === g.id ? `url(#${uid}-glow)` : undefined">
        <path v-if="g.connector" :d="g.connector" fill="none" :stroke="g.stroke" stroke-width="1" stroke-dasharray="2 2" />
        <path v-for="(d, i) in g.paths" :key="i" :d="d" :fill="g.color" :stroke="g.stroke" stroke-linejoin="round"
              :stroke-width="hoverKey === g.key || store.selectedFeatureId.value === g.id ? 1.6 : 0.8" />
        <template v-if="g.internal">
          <path :id="`${uid}-tp-${g.id}`" :d="g.internal.path" fill="none" stroke="none" />
          <text :font-size="g.internal.size" :fill="g.internal.color" font-weight="600" letter-spacing="0.2" class="cmap-int-label" pointer-events="none">
            <textPath :href="`#${uid}-tp-${g.id}`" startOffset="50%" text-anchor="middle" dominant-baseline="central">{{ g.internal.text }}</textPath>
          </text>
        </template>
      </g>

      <!-- ORF / 引物 -->
      <path v-for="o in orfGeoms" :key="o.key" :d="o.d" :data-hit="o.key" :fill="o.color" class="cmap-orf" />
      <path v-for="p in primerGeoms" :key="p.key" :d="p.d" :data-hit="p.key" fill="#68ace4" stroke="#2f6aa8" stroke-width="0.6" class="cmap-primer" />

      <!-- 光标 -->
      <line v-if="caretLine" :x1="caretLine.x1" :y1="caretLine.y1" :x2="caretLine.x2" :y2="caretLine.y2"
            stroke="#4f46e5" stroke-width="1.5" class="cmap-caret" />

      <!-- 外置标签 + 引线（SnapGene 式扇形，左右两列）。布局保证引线不压字，文字不需要白边 -->
      <g v-for="l in labelViews" :key="l.id" :data-hit="l.id"
         :class="['cmap-label', `cmap-label-${l.inf.kind}`, { 'is-hover': l.hot }]">
        <polyline :points="l.leaderPts" fill="none" :stroke="l.hot ? '#4f46e5' : '#b4bac2'"
                  :stroke-width="l.hot ? 1 : 0.75" stroke-linejoin="round" />
        <template v-if="l.inf.kind === 'chip'">
          <rect :x="l.box.x" :y="l.box.y + 1" :width="l.box.w" :height="l.box.h - 2" rx="3"
                :fill="l.inf.fill" :stroke="l.hot ? '#4f46e5' : l.inf.stroke" :stroke-width="l.hot ? 1.2 : 0.8" />
          <text :x="l.tx" :y="l.y" :font-size="FONT" dominant-baseline="central" :text-anchor="l.anchor"
                :fill="l.inf.ink" font-weight="500" class="cmap-label-text">{{ l.text }}</text>
        </template>
        <!-- 单个酶：名字直接写在 text 上（少一层 tspan，300 kb 旋转时补丁更少）；data-hit 在外层 g -->
        <text v-else-if="l.inf.kind === 'enzyme' && l.inf.parts.length === 1 && !l.inf.extra" :x="l.tx" :y="l.y" :font-size="FONT"
              dominant-baseline="central" :text-anchor="l.anchor" class="cmap-label-text"
              :fill="l.inf.parts[0].color" :font-weight="l.inf.parts[0].bold ? 700 : 400"><tspan v-if="l.side === -1 && showPos" :font-size="POS_FONT" fill="#8b929b" font-weight="400">({{ l.inf.pos }}) </tspan>{{ l.inf.parts[0].text }}<tspan v-if="l.side === 1 && showPos" :font-size="POS_FONT" fill="#8b929b" font-weight="400"> ({{ l.inf.pos }})</tspan></text>
        <text v-else-if="l.inf.kind === 'enzyme'" :x="l.tx" :y="l.y" :font-size="FONT" dominant-baseline="central"
              :text-anchor="l.anchor" class="cmap-label-text">
          <tspan v-if="l.side === -1 && showPos" :font-size="POS_FONT" fill="#8b929b" font-weight="400">({{ l.inf.pos }}) </tspan>
          <tspan v-for="p in l.inf.parts" :key="p.key" :data-hit="p.key" :fill="p.color"
                 :font-weight="p.bold ? 700 : 400">{{ p.text }}</tspan>
          <tspan v-if="l.inf.extra" fill="#8b929b" font-weight="400">{{ l.inf.extra }}</tspan>
          <tspan v-if="l.side === 1 && showPos" :font-size="POS_FONT" fill="#8b929b" font-weight="400"> ({{ l.inf.pos }})</tspan>
        </text>
        <text v-else :x="l.tx" :y="l.y" :font-size="FONT" dominant-baseline="central" :text-anchor="l.anchor"
              :fill="l.inf.color" class="cmap-label-text">{{ l.text }}</text>
      </g>

      <!-- 中心名称与长度 -->
      <text v-if="nameFits" :x="cx" :y="cy - 7" text-anchor="middle" :font-size="NAME_FONT" font-weight="700" fill="#1f2937" class="cmap-name">{{ centerName }}</text>
      <text v-if="lengthFits" :x="cx" :y="cy + (nameFits ? 13 : 4)" text-anchor="middle" :font-size="compact ? 11 : 12" fill="#6b7280" class="cmap-length">{{ L.toLocaleString('en-US') }} bp</text>
    </svg>

    <div v-if="interactive" class="cmap-controls">
      <button type="button" title="缩小" class="cmap-zoom-out" @click="zoomStep(zoom / 1.4)">−</button>
      <input v-model.number="zoomLog" class="cmap-zoom" type="range" min="0" :max="MAX_ZOOM_LOG" step="0.05"
             :title="`缩放 ${zoom.toFixed(1)}×（以选区为中心；Ctrl+滚轮以指针为中心）`" aria-label="缩放" />
      <button type="button" title="放大（以选区为中心；Ctrl+滚轮以指针为中心）" class="cmap-zoom-in" @click="zoomStep(zoom * 1.4)">+</button>
      <label class="cmap-rot" title="旋转（拖拽中心 / Alt+滚轮）">
        ⟳ <input v-model.number="rotDeg" type="range" min="-180" max="180" step="1" />
      </label>
      <button type="button" title="复位" @click="resetView">复位</button>
    </div>
    <MapTooltip v-if="own" :store="store" />
  </div>
</template>

<style scoped>
.cmap { position: relative; width: 100%; display: flex; flex-direction: column; align-items: center; }
.cmap-svg { display: block; max-width: 100%; height: auto; user-select: none; cursor: crosshair; }
.cmap-feat, .cmap-cut, .cmap-orf, .cmap-primer, .cmap-label, .cmap-search { cursor: pointer; }
.cmap-feat { transition: opacity 0.15s; }
.cmap-feat:hover path { filter: brightness(1.05); }
/* 悬停某个特征时其余特征略微变淡（只在页面上，导出的 SVG 不含这条样式） */
.cmap-svg.has-feat-hover .cmap-feat:not(.is-hover) { opacity: 0.55; }
.cmap-label-text tspan[data-hit]:hover, .cmap-label-text:hover { text-decoration: underline; }
.cmap-controls { display: flex; gap: 6px; align-items: center; font-size: 12px; color: var(--text-secondary); }
.cmap-controls button {
  min-width: 28px; padding: 2px 8px; border: 1px solid var(--border-color); border-radius: 4px;
  background: var(--bg-primary); color: var(--text-primary);
}
.cmap-rot { display: inline-flex; align-items: center; gap: 4px; }
.cmap-rot input { width: 110px; }
.cmap-zoom { width: 90px; }
</style>
