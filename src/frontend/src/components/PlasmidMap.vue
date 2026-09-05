<script setup lang="ts">
/**
 * SnapGene 风格环形质粒图谱 v2（Canvas 高清自绘）
 *
 * 渲染层次（外→内）：
 *   特征名标签（彩色多轨避让 + 引线）→ 位置刻度/数字 → 特征弧带
 *   （填充扇环 + 方向箭头，重叠特征自动向外分层）→ 序列骨架圆 →
 *   内侧酶切位点刻度 + 酶名多轨（单一酶切位点蓝色优先）→ 中心名称/长度
 *
 * 交互：hover/选中高亮 + tooltip、滚轮缩放、单一/全部酶切位点切换、
 *       PNG 2x 重渲染导出（expose exportPng）、selectFeature（expose）
 */
import { ref, onMounted, computed, watch } from 'vue'

export interface PlasmidFeature {
  name: string
  type: string
  start: number
  end: number
  strand: string
  color?: string
  description?: string
}

export interface EnzymeSite {
  name: string
  position: number
  strand?: string
  cut_fwd: number
  cut_rev: number
  overhang?: string | null
  recognition?: string | null
}

interface Props {
  sequence?: string
  features?: PlasmidFeature[]
  enzymeSites?: EnzymeSite[]
  name?: string
  length?: number
  width?: number
  height?: number
}

const props = withDefaults(defineProps<Props>(), {
  width: 620,
  height: 620,
  features: () => [] as PlasmidFeature[],
  enzymeSites: () => [] as EnzymeSite[]
})

const emit = defineEmits<{
  (e: 'feature-select', feature: PlasmidFeature | null): void
  (e: 'enzyme-click', site: EnzymeSite): void
}>()

const canvasRef = ref<HTMLCanvasElement | null>(null)
const hoveredFeature = ref<PlasmidFeature | null>(null)
const selectedFeature = ref<PlasmidFeature | null>(null)
const hoveredSite = ref<EnzymeSite | null>(null)
const zoom = ref(1)
const tooltip = ref<{ x: number; y: number } | null>(null)
const uniqueOnly = ref(true)

// ==================== 配色（对齐 SnapGene 默认观感） ====================
const featureColors: Record<string, string> = {
  promoter: '#E8656B',
  terminator: '#2FA98C',
  CDS: '#4E79C7',
  gene: '#4E79C7',
  origin: '#8FBF6B',
  rep_origin: '#8FBF6B',
  resistance: '#E8B93E',
  tag: '#B07FD8',
  MCS: '#E8923D',
  multiple_cloning_site: '#E8923D',
  other: '#9AA5B1'
}

const BLUE_UNIQUE = '#2B54C4'   // 单一酶切位点（SnapGene 蓝）
const BLUE_MULTI = '#93A3BD'    // 多位点酶
const FONT = 'Helvetica, Arial, sans-serif'

function featureColor(f: PlasmidFeature): string {
  return f.color || featureColors[f.type] || featureColors.other
}

/** 颜色加深（amount 0~1） */
function darken(hex: string, amount: number): string {
  const n = parseInt(hex.slice(1), 16)
  const r = Math.round(((n >> 16) & 255) * (1 - amount))
  const g = Math.round(((n >> 8) & 255) * (1 - amount))
  const b = Math.round((n & 255) * (1 - amount))
  return `rgb(${r},${g},${b})`
}

const plasmidLength = computed(() => props.length || props.sequence?.length || 5000)

// 每个特征拆分为弧段（处理跨起点 wrap：end < start）
interface ArcSeg {
  feature: PlasmidFeature
  from: number // 1-based 起始位
  to: number   // 1-based 结束位（含）
}
const arcSegments = computed<ArcSeg[]>(() => {
  const L = plasmidLength.value
  const segs: ArcSeg[] = []
  for (const f of props.features) {
    // 越界坐标按环形取模（兼容注释坐标超出序列长度的数据）
    const s = ((Math.round(f.start) - 1) % L + L) % L + 1
    const e = ((Math.round(f.end) - 1) % L + L) % L + 1
    if (e >= s) segs.push({ feature: f, from: s, to: e })
    else { segs.push({ feature: f, from: s, to: L }); segs.push({ feature: f, from: 1, to: e }) }
  }
  return segs
})

// 酶切事件去重（正反链识别同一位置只显示一次）
const dedupedSites = computed<EnzymeSite[]>(() => {
  const seen = new Set<string>()
  return props.enzymeSites.filter((s) => {
    const key = `${s.name}@${s.position}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
})

// 每个酶名的识别位点数（判断"单一酶切位点"）
const siteCountByName = computed<Map<string, number>>(() => {
  const m = new Map<string, Set<number>>()
  for (const s of props.enzymeSites) {
    if (!m.has(s.name)) m.set(s.name, new Set())
    m.get(s.name)!.add(s.position)
  }
  const out = new Map<string, number>()
  for (const [k, v] of m) out.set(k, v.size)
  return out
})

const shownSites = computed<EnzymeSite[]>(() => {
  if (!uniqueOnly.value) return dedupedSites.value
  const counts = siteCountByName.value
  return dedupedSites.value.filter((s) => (counts.get(s.name) || 0) === 1)
})

// 图例：仅显示图中实际出现的类型
const legendTypes = computed<Record<string, string>>(() => {
  const out: Record<string, string> = {}
  for (const f of props.features) {
    const t = featureColors[f.type] ? f.type : 'other'
    out[t] = f.color || featureColors[t]
  }
  return out
})

// ==================== 几何工具 ====================
const TAU = Math.PI * 2
function posToAngle(p: number, L: number): number {
  // 1 bp 在 12 点钟方向，顺时针增加；p 可超出 [1, L]（用于连续弧端点）
  return ((p - 1) / L) * TAU - Math.PI / 2
}
function circDist(a: number, b: number): number {
  const d = Math.abs(a - b) % TAU
  return d > Math.PI ? TAU - d : d
}
function angleInSeg(a: number, a1: number, a2: number): boolean {
  // a1/a2 为未归一化弧端（a2 > a1），a 已归一化到 [0, TAU)
  const rel = ((a - a1) % TAU + TAU) % TAU
  return rel <= a2 - a1
}

// 特征 → 弧带分层（重叠的特征向外错开一层）
const LANE_W = 13
function assignLanes(L: number): Map<PlasmidFeature, number> {
  const laneEnds: Array<Array<[number, number]>> = []
  const map = new Map<PlasmidFeature, number>()
  const items = [...props.features].sort((a, b) => a.start - b.start)
  for (const f of items) {
    const s = ((Math.round(f.start) - 1) % L + L) % L + 1
    let e = ((Math.round(f.end) - 1) % L + L) % L + 1
    if (e < s) e += L
    let lane = 0
    for (;; lane++) {
      const ends = laneEnds[lane] || (laneEnds[lane] = [])
      if (!ends.some(([fs, fe]) => s < fe && fs < e)) break
    }
    laneEnds[lane].push([s, e])
    map.set(f, lane)
  }
  return map
}

interface PlacedLabel { track: number; mid: number; halfWidth: number }
function placeLabel(mid: number, textWidth: number, radius: number, occupied: PlacedLabel[], maxTracks: number): number | null {
  const halfWidth = textWidth / (2 * radius)
  for (let track = 0; track < maxTracks; track++) {
    let ok = true
    for (const p of occupied) {
      if (p.track === track && circDist(p.mid, mid) < p.halfWidth + halfWidth) { ok = false; break }
    }
    if (ok) { occupied.push({ track, mid, halfWidth }); return track }
  }
  return null
}

function truncate(s: string, max: number): string {
  return s.length > max ? s.slice(0, max - 1) + '…' : s
}

// ==================== 渲染 ====================
interface Geom { cx: number; cy: number; Rtick: number; maxLane: number; band: number; laneMap: Map<PlasmidFeature, number> }
let geom: Geom = { cx: 0, cy: 0, Rtick: 0, maxLane: 0, band: 13, laneMap: new Map() }

function arcCenterRadius(Rtick: number, lane: number, band: number): number {
  return Rtick - 8 - lane * LANE_W - band / 2
}

/** 完整渲染一帧。scale 为最终渲染放大倍数（导出 PNG 时传 2×zoom 以重采样） */
function render(ctx: CanvasRenderingContext2D, w: number, h: number, scale: number) {
  const L = plasmidLength.value

  // 布局：按最长特征名动态留白，避免标签溢出画布
  ctx.font = `11.5px ${FONT}`
  let longest = 0
  for (const f of props.features) longest = Math.max(longest, ctx.measureText(truncate(f.name, 20)).width)
  const margin = 100 + Math.min(48, Math.max(0, longest - 70))

  const laneMap = assignLanes(L)
  const maxLane = laneMap.size ? Math.max(...laneMap.values()) : 0
  const band = Math.max(12, Math.min(15, L / 500))
  const cx = w / 2
  const cy = h / 2
  const Rtick = Math.min(w, h) / 2 - margin - maxLane * LANE_W // 特征弧带最外缘
  geom = { cx, cy, Rtick, maxLane, band, laneMap }

  drawBackbone(ctx, cx, cy, Rtick, band, maxLane)
  drawTicks(ctx, cx, cy, L, Rtick)
  drawSegments(ctx, cx, cy, L, Rtick, band, laneMap)
  drawEnzymeLayer(ctx, cx, cy, L)
  drawPositionNumbers(ctx, cx, cy, L, Rtick)
  drawFeatureLabels(ctx, cx, cy, L, Rtick, laneMap)
  drawCenterInfo(ctx, cx, cy, props.name || 'Plasmid', L)
  void scale
}

function draw() {
  const canvas = canvasRef.value
  if (!canvas) return
  const ctx = canvas.getContext('2d')
  if (!ctx) return

  const scale = zoom.value
  const dpr = window.devicePixelRatio || 1
  const w = props.width
  const h = props.height

  // 背衬按最终渲染分辨率建立（缩放后依然清晰）
  canvas.width = w * dpr * scale
  canvas.height = h * dpr * scale
  canvas.style.width = `${w * scale}px`
  canvas.style.height = `${h * scale}px`
  ctx.setTransform(dpr * scale, 0, 0, dpr * scale, 0, 0)
  ctx.clearRect(0, 0, w, h)
  ctx.fillStyle = '#FFFFFF'
  ctx.fillRect(0, 0, w, h)

  render(ctx, w, h, dpr * scale)
}

/** 序列骨架圆：未被特征覆盖处可见的灰色细圆 */
function drawBackbone(ctx: CanvasRenderingContext2D, cx: number, cy: number, Rtick: number, band: number, maxLane: number) {
  ctx.beginPath()
  ctx.arc(cx, cy, arcCenterRadius(Rtick, maxLane, band), 0, TAU)
  ctx.strokeStyle = '#C9CDD3'
  ctx.lineWidth = 2.5
  ctx.stroke()
}

/** 填充扇环特征弧：箭头在 5'→3' 前进方向（+ 顺时针 / − 逆时针） */
function drawSegments(
  ctx: CanvasRenderingContext2D, cx: number, cy: number, L: number,
  Rtick: number, band: number, laneMap: Map<PlasmidFeature, number>
) {
  for (const seg of arcSegments.value) {
    const f = seg.feature
    const lane = laneMap.get(f) || 0
    const rm = arcCenterRadius(Rtick, lane, band)
    const rO = rm + band / 2
    const rI = rm - band / 2
    const a1 = posToAngle(seg.from, L)
    const a2 = posToAngle(seg.to + 1, L) // to+1 可为 L+1，posToAngle 对超界连续
    const color = featureColor(f)
    const active = hoveredFeature.value === f || selectedFeature.value === f

    const span = a2 - a1
    const plus = f.strand !== '-'
    const arrowA = Math.min((band * 0.95) / rm, span / 2)

    ctx.beginPath()
    if (span > arrowA * 2.4) {
      if (plus) {
        // 主体外弧 a1 → aBase，箭头尖在 a2，内弧折回
        const aBase = a2 - arrowA
        ctx.arc(cx, cy, rO, a1, aBase)
        ctx.lineTo(cx + Math.cos(a2) * rm, cy + Math.sin(a2) * rm)
        ctx.arc(cx, cy, rI, aBase, a1, true)
      } else {
        // 箭头尖在 a1（逆时针前进）：外弧 aBase → a2，径向边折入内弧，回到 aBase 后引向尖端
        const aBase = a1 + arrowA
        ctx.arc(cx, cy, rO, aBase, a2)
        ctx.lineTo(cx + Math.cos(a2) * rI, cy + Math.sin(a2) * rI)
        ctx.arc(cx, cy, rI, a2, aBase, true)
        ctx.lineTo(cx + Math.cos(a1) * rm, cy + Math.sin(a1) * rm)
      }
    } else {
      ctx.arc(cx, cy, rO, a1, a2)
      ctx.arc(cx, cy, rI, a2, a1, true)
    }
    ctx.closePath()
    ctx.fillStyle = color
    ctx.fill()
    ctx.strokeStyle = darken(color, active ? 0.38 : 0.24)
    ctx.lineWidth = active ? 1.8 : 1
    if (active) { ctx.shadowColor = color; ctx.shadowBlur = 9 }
    ctx.stroke()
    ctx.shadowBlur = 0
  }
}

/** 位置刻度（特征弧带外侧） */
function drawTicks(ctx: CanvasRenderingContext2D, cx: number, cy: number, L: number, Rtick: number) {
  const steps = [50, 100, 200, 250, 500, 1000, 2000, 2500, 5000, 10000, 20000, 50000]
  const step = steps.find((s) => L / s <= 12) || 100000
  const minor = step / 5

  for (let p = 1; p <= L; p += minor) {
    const isMajor = (p - 1) % step === 0
    const a = posToAngle(p, L)
    const r1 = Rtick + (isMajor ? 2 : 5)
    const r2 = r1 + (isMajor ? 7 : 3)
    ctx.beginPath()
    ctx.moveTo(cx + Math.cos(a) * r1, cy + Math.sin(a) * r1)
    ctx.lineTo(cx + Math.cos(a) * r2, cy + Math.sin(a) * r2)
    ctx.strokeStyle = isMajor ? '#9AA0A8' : '#D5D8DC'
    ctx.lineWidth = 1
    ctx.stroke()
  }
}

function drawPositionNumbers(ctx: CanvasRenderingContext2D, cx: number, cy: number, L: number, Rtick: number) {
  const steps = [100, 200, 250, 500, 1000, 2000, 2500, 5000, 10000, 20000, 50000]
  const step = steps.find((s) => L / s <= 10) || 100000
  ctx.font = `10px ${FONT}`
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.fillStyle = '#8A8F98'
  const rNum = Rtick + 18
  for (let p = 1; p <= L; p += step) {
    const a = posToAngle(p, L)
    ctx.fillText(formatNumber(p - 1), cx + Math.cos(a) * rNum, cy + Math.sin(a) * rNum)
  }
}

function angleMid(f: PlasmidFeature, L: number): number {
  const s = ((Math.round(f.start) - 1) % L + L) % L + 1
  let e = ((Math.round(f.end) - 1) % L + L) % L + 1
  if (e < s) e += L
  return posToAngle((s + e) / 2 % (L + 1), L)
}

/** 特征名标签：弧外多轨避让 + 彩色引线 */
function drawFeatureLabels(
  ctx: CanvasRenderingContext2D, cx: number, cy: number, L: number,
  Rtick: number, laneMap: Map<PlasmidFeature, number>
) {
  const occupied: PlacedLabel[] = []
  ctx.font = `11.5px ${FONT}`
  ctx.textBaseline = 'middle'

  const labels = props.features
    .map((f) => {
      const text = truncate(f.name, 20)
      return { f, mid: angleMid(f, L), text, tw: ctx.measureText(text).width }
    })
    .sort((a, b) => a.mid - b.mid)

  for (const { f, mid, text, tw } of labels) {
    const track = placeLabel(mid, tw + 18, Rtick + 36, occupied, 4)
    if (track === null) continue
    const color = featureColor(f)
    const active = hoveredFeature.value === f || selectedFeature.value === f
    const rLabel = Rtick + 36 + track * 14

    // 引线：从特征弧外缘沿径向到标签轨道
    const lane = laneMap.get(f) || 0
    const rFrom = Rtick - 6 - lane * LANE_W
    ctx.beginPath()
    ctx.moveTo(cx + Math.cos(mid) * rFrom, cy + Math.sin(mid) * rFrom)
    ctx.lineTo(cx + Math.cos(mid) * rLabel, cy + Math.sin(mid) * rLabel)
    ctx.strokeStyle = active ? '#333' : darken(color, 0.1)
    ctx.lineWidth = active ? 1.6 : 1
    ctx.stroke()

    ctx.fillStyle = active ? '#111' : darken(color, 0.22)
    ctx.font = active ? `bold 11.5px ${FONT}` : `11.5px ${FONT}`
    ctx.textAlign = Math.cos(mid) >= 0 ? 'left' : 'right'
    ctx.fillText(text, cx + Math.cos(mid) * (rLabel + 4), cy + Math.sin(mid) * (rLabel + 4))
    ctx.font = `11.5px ${FONT}`
  }
}

/** 内侧酶切位点：切割刻度 + 酶名多轨（单一酶切位点蓝色优先显示） */
function drawEnzymeLayer(ctx: CanvasRenderingContext2D, cx: number, cy: number, L: number) {
  const sites = shownSites.value
  if (!sites.length) return
  const counts = siteCountByName.value
  const band = geom.band
  const innerEdge = arcCenterRadius(geom.Rtick, geom.maxLane, band) - band / 2
  const tickLen = 6

  ctx.font = `9.5px ${FONT}`
  const enriched = sites.map((s) => {
    const a = posToAngle(s.cut_fwd, L)
    return { s, a, tw: ctx.measureText(s.name).width, unique: (counts.get(s.name) || 0) === 1 }
  })

  // 切割刻度线
  for (const { a, unique } of enriched) {
    ctx.beginPath()
    ctx.moveTo(cx + Math.cos(a) * (innerEdge - 1), cy + Math.sin(a) * (innerEdge - 1))
    ctx.lineTo(cx + Math.cos(a) * (innerEdge - 1 - tickLen), cy + Math.sin(a) * (innerEdge - 1 - tickLen))
    ctx.strokeStyle = unique ? BLUE_UNIQUE : BLUE_MULTI
    ctx.lineWidth = unique ? 1.3 : 1
    ctx.stroke()
  }

  // 酶名：单一位点优先进入避让队列；放不下省略（tooltip 兜底）
  const ordered = [...enriched].sort((x, y) => (Number(y.unique) - Number(x.unique)) || (x.a - y.a))
  const occupied: PlacedLabel[] = []
  for (const { s, a, tw, unique } of ordered) {
    if (!unique && uniqueOnly.value) continue // 仅单一模式：多位点酶只画刻度
    const track = placeLabel(a, tw + 10, innerEdge - 18, occupied, 4)
    if (track === null) continue
    const r = innerEdge - 18 - track * 11.5
    ctx.beginPath()
    ctx.moveTo(cx + Math.cos(a) * (innerEdge - 1 - tickLen), cy + Math.sin(a) * (innerEdge - 1 - tickLen))
    ctx.lineTo(cx + Math.cos(a) * r, cy + Math.sin(a) * r)
    ctx.strokeStyle = unique ? 'rgba(43,84,196,0.35)' : 'rgba(147,163,189,0.4)'
    ctx.lineWidth = 0.8
    ctx.stroke()
    ctx.fillStyle = hoveredSite.value === s ? '#111' : unique ? BLUE_UNIQUE : BLUE_MULTI
    ctx.textAlign = Math.cos(a) >= 0 ? 'left' : 'right'
    ctx.fillText(s.name, cx + Math.cos(a) * (r + 3), cy + Math.sin(a) * (r + 3))
  }
}

function drawCenterInfo(ctx: CanvasRenderingContext2D, cx: number, cy: number, name: string, L: number) {
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.font = `bold 16px ${FONT}`
  ctx.fillStyle = '#2B2F36'
  ctx.fillText(truncate(name, 20), cx, cy - 11)
  ctx.font = `12px ${FONT}`
  ctx.fillStyle = '#7A7F87'
  ctx.fillText(`${L} bp`, cx, cy + 10)
}

function formatNumber(num: number): string {
  if (num >= 10000) return `${(num / 1000).toFixed(1).replace(/\.0$/, '')}k`
  return String(num)
}

// ==================== 交互 ====================
function hitTest(x: number, y: number): { feature: PlasmidFeature | null; site: EnzymeSite | null } {
  const L = plasmidLength.value
  const { cx, cy, Rtick, maxLane, band, laneMap } = geom
  const dx = x - cx
  const dy = y - cy
  const dist = Math.sqrt(dx * dx + dy * dy)
  const angle = (Math.atan2(dy, dx) + TAU) % TAU

  // 特征弧带命中：从最外层向内逐层判定
  if (dist >= arcCenterRadius(Rtick, maxLane, band) - band / 2 - 3 && dist <= Rtick - 4) {
    const segs = [...arcSegments.value].sort(
      (p, q) => (laneMap.get(q.feature) || 0) - (laneMap.get(p.feature) || 0)
    )
    for (const seg of segs) {
      const lane = laneMap.get(seg.feature) || 0
      const rm = arcCenterRadius(Rtick, lane, band)
      if (dist < rm - band / 2 - 2 || dist > rm + band / 2 + 2) continue
      const a1u = posToAngle(seg.from, L)
      const a2u = posToAngle(seg.to + 1, L)
      const a1n = ((a1u % TAU) + TAU) % TAU
      if (angleInSeg(angle, a1n, a1n + (a2u - a1u))) {
        return { feature: seg.feature, site: null }
      }
    }
  }

  // 酶位点命中：内区按角度就近判定（像素距离阈值 14px）
  if (dist < arcCenterRadius(Rtick, maxLane, band) - band / 2) {
    let best: { s: EnzymeSite; px: number } | null = null
    for (const s of shownSites.value) {
      const sa = posToAngle(s.cut_fwd, L)
      const d = circDist(angle, ((sa % TAU) + TAU) % TAU) * Math.max(dist, 30)
      if (d < 14 && (!best || d < best.px)) best = { s, px: d }
    }
    if (best) return { feature: null, site: best.s }
  }
  return { feature: null, site: null }
}

function toCanvasXY(event: MouseEvent): { x: number; y: number } {
  const canvas = canvasRef.value!
  const rect = canvas.getBoundingClientRect()
  return {
    x: ((event.clientX - rect.left) / rect.width) * props.width,
    y: ((event.clientY - rect.top) / rect.height) * props.height
  }
}

function handleMouseMove(event: MouseEvent) {
  const canvas = canvasRef.value
  if (!canvas) return
  const rect = canvas.getBoundingClientRect()
  const { x, y } = toCanvasXY(event)
  const { feature, site } = hitTest(x, y)
  const changed = feature !== hoveredFeature.value || site !== hoveredSite.value
  hoveredFeature.value = feature
  hoveredSite.value = site
  canvas.style.cursor = feature || site ? 'pointer' : 'default'
  tooltip.value = feature || site
    ? { x: event.clientX - rect.left, y: event.clientY - rect.top }
    : null
  if (changed) draw()
}

function handleMouseLeave() {
  hoveredFeature.value = null
  hoveredSite.value = null
  tooltip.value = null
  draw()
}

function handleClick(event: MouseEvent) {
  const { x, y } = toCanvasXY(event)
  const { feature, site } = hitTest(x, y)

  if (site) { emit('enzyme-click', site); return }
  if (feature) {
    selectedFeature.value = selectedFeature.value === feature ? null : feature
    emit('feature-select', selectedFeature.value)
    draw()
  } else if (selectedFeature.value) {
    selectedFeature.value = null
    emit('feature-select', null)
    draw()
  }
}

function handleWheel(event: WheelEvent) {
  event.preventDefault()
  const factor = event.deltaY < 0 ? 1.15 : 1 / 1.15
  zoom.value = Math.min(2.5, Math.max(0.6, zoom.value * factor))
}

// 对外暴露：选中/定位特征与导出
function selectFeature(feature: PlasmidFeature | null) {
  selectedFeature.value = feature
  draw()
}

/** PNG 导出：离屏画布按 2× 分辨率完整重渲染（非位图放大） */
function exportPng() {
  const w = props.width
  const h = props.height
  const out = document.createElement('canvas')
  out.width = w * 2
  out.height = h * 2
  const octx = out.getContext('2d')
  if (!octx) return
  octx.setTransform(2, 0, 0, 2, 0, 0)
  octx.fillStyle = '#FFFFFF'
  octx.fillRect(0, 0, w, h)
  render(octx, w, h, 2)
  const link = document.createElement('a')
  link.download = `${(props.name || 'plasmid').replace(/[^\w-]+/g, '_')}-map.png`
  link.href = out.toDataURL('image/png')
  link.click()
}
defineExpose({ selectFeature, exportPng })

watch(() => [props.features, props.enzymeSites, props.sequence, props.length, props.name, uniqueOnly.value], draw, { deep: true })
watch(zoom, draw)
onMounted(draw)
</script>

<template>
  <div class="plasmid-map-container">
    <div class="map-toolbar">
      <div class="seg-control">
        <button :class="{ active: uniqueOnly }" @click="uniqueOnly = true">仅单一酶切位点</button>
        <button :class="{ active: !uniqueOnly }" @click="uniqueOnly = false">全部位点</button>
      </div>
      <button class="tool-btn" @click="exportPng">⬇ 导出 PNG</button>
    </div>

    <canvas
      ref="canvasRef"
      @mousemove="handleMouseMove"
      @mouseleave="handleMouseLeave"
      @click="handleClick"
      @wheel="handleWheel"
    />

    <!-- 悬停提示 -->
    <div
      v-if="(hoveredFeature || hoveredSite) && tooltip"
      class="feature-tooltip"
      :style="{ left: Math.min(tooltip.x + 14, width - 200) + 'px', top: tooltip.y + 14 + 'px' }"
    >
      <template v-if="hoveredFeature">
        <div class="tooltip-header">
          <span class="tooltip-type" :style="{ backgroundColor: featureColor(hoveredFeature) }">
            {{ hoveredFeature.type }}
          </span>
          <span class="tooltip-name">{{ hoveredFeature.name }}</span>
        </div>
        <div class="tooltip-details">
          <span>{{ hoveredFeature.start }} - {{ hoveredFeature.end }} bp</span>
          <span>({{ hoveredFeature.end - hoveredFeature.start + 1 }} bp)</span>
          <span v-if="hoveredFeature.strand" class="tooltip-strand">{{ hoveredFeature.strand === '-' ? '反向链' : '正向链' }}</span>
        </div>
        <div v-if="hoveredFeature.description" class="tooltip-desc">{{ hoveredFeature.description }}</div>
      </template>
      <template v-else-if="hoveredSite">
        <div class="tooltip-header">
          <span class="tooltip-type" style="background-color: #2B54C4"> enzyme </span>
          <span class="tooltip-name">{{ hoveredSite.name }}</span>
        </div>
        <div class="tooltip-details">
          <span>切点 {{ hoveredSite.cut_fwd }}</span>
          <span v-if="hoveredSite.recognition">识别序列 {{ hoveredSite.recognition }}</span>
          <span v-if="hoveredSite.overhang">{{ hoveredSite.overhang === '5prime' ? "5' overhang" : hoveredSite.overhang === '3prime' ? "3' overhang" : 'blunt' }}</span>
        </div>
      </template>
    </div>

    <!-- 特征图例 -->
    <div v-if="Object.keys(legendTypes).length" class="legend">
      <div v-for="(color, type) in legendTypes" :key="type" class="legend-item">
        <span class="legend-color" :style="{ backgroundColor: color }"></span>
        <span class="legend-label">{{ type }}</span>
      </div>
    </div>
    <div class="map-hint">滚轮缩放 · 点击特征/酶位点查看详情</div>
  </div>
</template>

<style scoped>
.plasmid-map-container {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
}

.map-toolbar {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 8px;
  width: 100%;
  margin-bottom: 6px;
}

.seg-control {
  display: flex;
  border: 1px solid #D9DDE3;
  border-radius: 6px;
  overflow: hidden;
}

.seg-control button {
  border: none;
  background: #fff;
  padding: 3px 10px;
  font-size: 11px;
  color: #667085;
  cursor: pointer;
}

.seg-control button.active {
  background: #4E79C7;
  color: #fff;
}

.tool-btn {
  border: 1px solid #D9DDE3;
  background: #fff;
  border-radius: 6px;
  padding: 3px 10px;
  font-size: 11px;
  color: #667085;
  cursor: pointer;
}

.tool-btn:hover {
  background: #F4F6F8;
}

canvas {
  background: #ffffff;
  border-radius: 8px;
  max-width: 100%;
}

.feature-tooltip {
  position: absolute;
  background: white;
  padding: 8px 12px;
  border-radius: 6px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
  pointer-events: none;
  z-index: 10;
  min-width: 150px;
}

.tooltip-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}

.tooltip-type {
  padding: 2px 6px;
  border-radius: 3px;
  font-size: 10px;
  color: white;
  font-weight: 500;
}

.tooltip-name {
  font-weight: 600;
  font-size: 13px;
}

.tooltip-details {
  font-size: 11px;
  color: #666;
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.tooltip-strand { color: #4E79C7; }
.tooltip-desc {
  margin-top: 4px;
  font-size: 11px;
  color: #888;
  max-width: 260px;
}

.legend {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 14px;
  padding: 10px 14px;
  background: #F6F7F9;
  border-radius: 6px;
}

.legend-item {
  display: flex;
  align-items: center;
  gap: 4px;
}

.legend-color {
  width: 12px;
  height: 12px;
  border-radius: 2px;
}

.legend-label {
  font-size: 11px;
  color: #555;
  text-transform: capitalize;
}

.map-hint {
  margin-top: 6px;
  font-size: 11px;
  color: #aaa;
}
</style>
