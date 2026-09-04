<script setup lang="ts">
/**
 * SnapGene 风格环形质粒图谱（Canvas 高清自绘）
 * - 特征弧：支持跨起点 wrap、正负链方向箭头、弧外名称标签自动分轨避让
 * - 酶切位点：骨架内侧刻度 + 酶名标签错开布局
 * - 自适应主/次刻度、hover/选中、滚轮缩放、PNG 导出（expose exportPng）
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
  width: 560,
  height: 560,
  features: () => [],
  enzymeSites: () => []
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

const featureColors: Record<string, string> = {
  promoter: '#FF6B6B',
  terminator: '#4ECDC4',
  CDS: '#45B7D1',
  gene: '#45B7D1',
  origin: '#96CEB4',
  resistance: '#F2C94C',
  tag: '#DDA0DD',
  MCS: '#FFA500',
  multiple_cloning_site: '#FFA500',
  rep_origin: '#96CEB4',
  other: '#CCCCCC'
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
    const s = Math.min(Math.max(f.start, 1), L)
    const e = Math.min(Math.max(f.end, 1), L)
    if (e >= s) segs.push({ feature: f, from: s, to: e })
    else { segs.push({ feature: f, from: s, to: L }); segs.push({ feature: f, from: 1, to: e }) }
  }
  return segs
})

// ==================== 几何工具 ====================
const TAU = Math.PI * 2
function posToAngle(p: number, L: number): number {
  // 1 bp 在 12 点钟方向，顺时针增加
  return ((p - 1) / L) * TAU - Math.PI / 2
}
function circDist(a: number, b: number): number {
  const d = Math.abs(a - b) % TAU
  return d > Math.PI ? TAU - d : d
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

// ==================== 绘制 ====================
function draw() {
  const canvas = canvasRef.value
  if (!canvas) return
  const ctx = canvas.getContext('2d')
  if (!ctx) return

  const L = plasmidLength.value
  const scale = zoom.value
  const dpr = window.devicePixelRatio || 1
  const w = props.width
  const h = props.height
  canvas.width = w * dpr
  canvas.height = h * dpr
  canvas.style.width = `${w * scale}px`
  canvas.style.height = `${h * scale}px`

  ctx.setTransform(dpr * scale, 0, 0, dpr * scale, 0, 0)
  ctx.clearRect(0, 0, w, h)
  ctx.fillStyle = '#FFFFFF'
  ctx.fillRect(0, 0, w, h)

  const cx = w / 2
  const cy = h / 2
  const labelSpace = 90
  const R = Math.min(w, h) / 2 - labelSpace          // 特征环外半径
  const band = Math.max(14, Math.min(22, L / 400))   // 特征环厚度
  const Ri = R - band                                 // 内半径（酶位点层）
  const rm = R - band / 2                             // 特征弧中线半径

  drawBackbone(ctx, cx, cy, Ri, R)
  drawTicks(ctx, cx, cy, L, R, Ri)
  drawSegments(ctx, cx, cy, L, rm, band, R)
  drawEnzymeLayer(ctx, cx, cy, L, Ri)
  drawFeatureLabels(ctx, cx, cy, L, rm, band, R)
  drawCenterInfo(ctx, cx, cy, props.name || 'Plasmid', L)
}

function drawBackbone(ctx: CanvasRenderingContext2D, cx: number, cy: number, Ri: number, R: number) {
  ctx.beginPath()
  ctx.arc(cx, cy, (R + Ri) / 2, 0, TAU)
  ctx.strokeStyle = '#DDDDDD'
  ctx.lineWidth = R - Ri
  ctx.stroke()
}

function drawTicks(ctx: CanvasRenderingContext2D, cx: number, cy: number, L: number, R: number, Ri: number) {
  // 自适应主刻度步长
  const steps = [50, 100, 200, 250, 500, 1000, 2000, 2500, 5000, 10000, 20000]
  const step = steps.find((s) => L / s <= 10) || 50000
  const minor = step / 5
  ctx.font = '10px Arial, sans-serif'
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'

  for (let p = 1; p <= L; p += minor) {
    const isMajor = (p - 1) % step === 0
    const a = posToAngle(p, L)
    const rOut = R + (isMajor ? 6 : 3)
    ctx.beginPath()
    ctx.moveTo(cx + Math.cos(a) * rOut, cy + Math.sin(a) * rOut)
    ctx.lineTo(cx + Math.cos(a) * (rOut + (isMajor ? 5 : 2)), cy + Math.sin(a) * (rOut + (isMajor ? 5 : 2)))
    ctx.strokeStyle = isMajor ? '#888' : '#CCC'
    ctx.lineWidth = 1
    ctx.stroke()
    if (isMajor) {
      const lr = R + 20
      ctx.fillStyle = '#666'
      ctx.fillText(formatNumber(p - 1), cx + Math.cos(a) * lr, cy + Math.sin(a) * lr)
    }
  }
  void Ri
}

function drawSegments(ctx: CanvasRenderingContext2D, cx: number, cy: number, L: number, rm: number, band: number, R: number) {
  for (const seg of arcSegments.value) {
    const f = seg.feature
    const a1 = posToAngle(seg.from, L)
    const a2 = posToAngle(seg.to + 1 > L ? L + 1 : seg.to + 1, L)
    const color = f.color || featureColors[f.type] || featureColors.other
    const active = hoveredFeature.value === f || selectedFeature.value === f

    ctx.beginPath()
    ctx.arc(cx, cy, rm, a1, a2)
    ctx.strokeStyle = color
    ctx.lineWidth = band + (active ? 3 : 0)
    ctx.lineCap = 'butt'
    if (active) { ctx.shadowColor = color; ctx.shadowBlur = 8 }
    ctx.stroke()
    ctx.shadowBlur = 0

    // 方向箭头：+ 链指向弧末端（顺时针方向），- 链指向弧起点（逆时针方向）
    const arcLen = seg.to - seg.from + 1
    if (arcLen > 40) {
      const dir = f.strand === '-' ? -1 : 1
      const tipA = dir > 0 ? a2 : a1
      const baseA = tipA - dir * (12 / rm)
      const tip = { x: cx + Math.cos(tipA) * rm, y: cy + Math.sin(tipA) * rm }
      ctx.beginPath()
      ctx.moveTo(tip.x, tip.y)
      ctx.lineTo(cx + Math.cos(baseA) * (rm - band / 2 - 1), cy + Math.sin(baseA) * (rm - band / 2 - 1))
      ctx.lineTo(cx + Math.cos(baseA) * (rm + band / 2 + 1), cy + Math.sin(baseA) * (rm + band / 2 + 1))
      ctx.closePath()
      ctx.fillStyle = color
      ctx.fill()
    }
  }
  void R
}

function drawFeatureLabels(ctx: CanvasRenderingContext2D, cx: number, cy: number, L: number, rm: number, band: number, R: number) {
  const occupied: PlacedLabel[] = []
  ctx.font = '11px Arial, sans-serif'
  ctx.textBaseline = 'middle'

  const labels = props.features
    .map((f) => ({ f, mid: angleMid(f, L) }))
    .map(({ f, mid }) => ({ f, mid, tw: ctx.measureText(f.name).width }))
    .sort((a, b) => a.mid - b.mid)

  for (const { f, mid, tw } of labels) {
    const track = placeLabel(mid, tw + 16, R + 12, occupied, 3)
    if (track === null) continue
    const color = f.color || featureColors[f.type] || featureColors.other
    const active = hoveredFeature.value === f || selectedFeature.value === f
    const rLabel = R + 12 + track * 15

    // 引线：从弧外缘沿径向到标签轨道
    ctx.beginPath()
    ctx.moveTo(cx + Math.cos(mid) * (R + band * 0 + 4), cy + Math.sin(mid) * (R + 4))
    ctx.lineTo(cx + Math.cos(mid) * rLabel, cy + Math.sin(mid) * rLabel)
    ctx.strokeStyle = active ? '#333' : color
    ctx.lineWidth = active ? 2 : 1.2
    ctx.stroke()

    ctx.fillStyle = active ? '#111' : '#333'
    ctx.textAlign = Math.cos(mid) >= 0 ? 'left' : 'right'
    const px = cx + Math.cos(mid) * (rLabel + 4)
    const py = cy + Math.sin(mid) * (rLabel + 4)
    ctx.fillText(f.name, px, py)
  }
  void rm
}

function angleMid(f: PlasmidFeature, L: number): number {
  const s = Math.min(Math.max(f.start, 1), L)
  let e = Math.min(Math.max(f.end, 1), L)
  if (e < s) e += L
  return posToAngle((s + e) / 2 % (L + 1), L)
}

function drawEnzymeLayer(ctx: CanvasRenderingContext2D, cx: number, cy: number, L: number, Ri: number) {
  if (!props.enzymeSites.length) return
  // 同名酶去重（正反链识别同一位置只显示一次）
  const seen = new Set<string>()
  const sites = props.enzymeSites.filter((s) => {
    const key = `${s.name}@${s.position}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })

  const tickLen = 6
  const occupied: PlacedLabel[] = []
  ctx.font = '9px Arial, sans-serif'

  const enriched = sites.map((s) => {
    const a = posToAngle(s.cut_fwd, L)
    return { s, a, tw: ctx.measureText(s.name).width }
  })

  for (const { a } of enriched) {
    // 刻度线
    ctx.beginPath()
    ctx.moveTo(cx + Math.cos(a) * (Ri - 2), cy + Math.sin(a) * (Ri - 2))
    ctx.lineTo(cx + Math.cos(a) * (Ri - 2 - tickLen), cy + Math.sin(a) * (Ri - 2 - tickLen))
    ctx.strokeStyle = '#555'
    ctx.lineWidth = 1
    ctx.stroke()
  }

  // 酶名标签：内圈分轨避让，放不下则省略（tooltip 兜底）
  for (const { s, a, tw } of [...enriched].sort((x, y) => x.a - y.a)) {
    const track = placeLabel(a, tw + 8, Ri - 20, occupied, 2)
    if (track === null) continue
    const r = Ri - 20 - track * 12
    ctx.beginPath()
    ctx.moveTo(cx + Math.cos(a) * (Ri - 2 - tickLen), cy + Math.sin(a) * (Ri - 2 - tickLen))
    ctx.lineTo(cx + Math.cos(a) * r, cy + Math.sin(a) * r)
    ctx.strokeStyle = '#BBB'
    ctx.lineWidth = 0.8
    ctx.stroke()
    ctx.fillStyle = '#555'
    ctx.textAlign = Math.cos(a) >= 0 ? 'left' : 'right'
    ctx.fillText(s.name, cx + Math.cos(a) * (r + 3), cy + Math.sin(a) * (r + 3))
  }
}

function drawCenterInfo(ctx: CanvasRenderingContext2D, cx: number, cy: number, name: string, L: number) {
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.font = 'bold 15px Arial, sans-serif'
  ctx.fillStyle = '#333'
  ctx.fillText(name.length > 18 ? name.slice(0, 17) + '…' : name, cx, cy - 10)
  ctx.font = '12px Arial, sans-serif'
  ctx.fillStyle = '#777'
  ctx.fillText(`${L} bp`, cx, cy + 10)
}

function formatNumber(num: number): string {
  if (num >= 10000) return `${(num / 1000).toFixed(0)}k`
  if (num >= 1000) return `${(num / 1000).toFixed(1)}k`
  return String(num)
}

// ==================== 交互 ====================
function hitTest(x: number, y: number): { feature: PlasmidFeature | null; site: EnzymeSite | null } {
  const L = plasmidLength.value
  const w = props.width
  const h = props.height
  const cx = w / 2
  const cy = h / 2
  const R = Math.min(w, h) / 2 - 90
  const band = Math.max(14, Math.min(22, L / 400))
  const Ri = R - band
  const dx = x - cx
  const dy = y - cy
  const dist = Math.sqrt(dx * dx + dy * dy)

  // 特征环命中
  if (dist >= Ri - 2 && dist <= R + 2) {
    let angle = Math.atan2(dy, dx) + Math.PI / 2
    if (angle < 0) angle += TAU
    const pos = 1 + (angle / TAU) * L
    for (const f of props.features) {
      const s = Math.min(f.start, L)
      const e = Math.min(f.end, L)
      const inRange = e >= s ? pos >= s && pos <= e + 1 : pos >= s || pos <= e + 1
      if (inRange) return { feature: f, site: null }
    }
  }

  // 酶位点命中（内侧标签区，按屏幕像素半径 8px 判定）
  for (const s of props.enzymeSites) {
    const a = posToAngle(s.cut_fwd, L)
    const r = Ri - 20
    const sx = cx + Math.cos(a) * r
    const sy = cy + Math.sin(a) * r
    if ((sx - x) ** 2 + (sy - y) ** 2 < 100) return { feature: null, site: s }
  }
  return { feature: null, site: null }
}

function handleMouseMove(event: MouseEvent) {
  const canvas = canvasRef.value
  if (!canvas) return
  const rect = canvas.getBoundingClientRect()
  // canvas 被 CSS 缩放，换算回内部坐标
  const x = ((event.clientX - rect.left) / rect.width) * props.width
  const y = ((event.clientY - rect.top) / rect.height) * props.height

  const { feature, site } = hitTest(x, y)
  hoveredFeature.value = feature
  hoveredSite.value = site
  canvas.style.cursor = feature || site ? 'pointer' : 'default'
  tooltip.value = feature || site ? { x: event.clientX - rect.left, y: event.clientY - rect.top } : null
}

function handleMouseLeave() {
  hoveredFeature.value = null
  hoveredSite.value = null
  tooltip.value = null
}

function handleClick(event: MouseEvent) {
  const canvas = canvasRef.value
  if (!canvas) return
  const rect = canvas.getBoundingClientRect()
  const x = ((event.clientX - rect.left) / rect.width) * props.width
  const y = ((event.clientY - rect.top) / rect.height) * props.height
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
function exportPng() {
  const canvas = canvasRef.value
  if (!canvas) return
  const link = document.createElement('a')
  link.download = `${(props.name || 'plasmid').replace(/[^\w-]+/g, '_')}-map.png`
  link.href = canvas.toDataURL('image/png')
  link.click()
}
defineExpose({ selectFeature, exportPng })

watch(() => [props.features, props.enzymeSites, props.sequence, props.length, props.name], draw, { deep: true })
watch(zoom, draw)
onMounted(draw)
</script>

<template>
  <div class="plasmid-map-container">
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
      :style="{ left: Math.min(tooltip.x + 14, width - 180) + 'px', top: tooltip.y + 14 + 'px' }"
    >
      <template v-if="hoveredFeature">
        <div class="tooltip-header">
          <span class="tooltip-type" :style="{ backgroundColor: featureColors[hoveredFeature.type] || '#CCC' }">
            {{ hoveredFeature.type }}
          </span>
          <span class="tooltip-name">{{ hoveredFeature.name }}</span>
        </div>
        <div class="tooltip-details">
          <span>{{ hoveredFeature.start }} - {{ hoveredFeature.end }} bp</span>
          <span>({{ hoveredFeature.end - hoveredFeature.start + 1 }} bp)</span>
          <span v-if="hoveredFeature.strand" class="tooltip-strand">{{ hoveredFeature.strand }} 链</span>
        </div>
        <div v-if="hoveredFeature.description" class="tooltip-desc">{{ hoveredFeature.description }}</div>
      </template>
      <template v-else-if="hoveredSite">
        <div class="tooltip-header">
          <span class="tooltip-type" style="background-color: #6C7A89"> enzyme </span>
          <span class="tooltip-name">{{ hoveredSite.name }}</span>
        </div>
        <div class="tooltip-details">
          <span>位置 {{ hoveredSite.cut_fwd }}</span>
          <span v-if="hoveredSite.overhang">{{ hoveredSite.overhang === '5prime' ? "5' overhang" : hoveredSite.overhang === '3prime' ? "3' overhang" : 'blunt' }}</span>
        </div>
      </template>
    </div>

    <!-- 特征图例 -->
    <div class="legend">
      <div v-for="(color, type) in featureColors" :key="type" class="legend-item">
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

.tooltip-strand { color: #45B7D1; }
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
  margin-top: 16px;
  padding: 12px;
  background: #F5F5F5;
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
