<script setup lang="ts">
/**
 * SnapGene 风格线性序列视图 v2（Canvas 虚拟滚动）
 * 每行自上而下：酶名（两档错层）+ 切点标记 → 特征条（重叠分层）与名称 →
 * + 链氨基酸翻译 → 碱基（10bp 分组着色 + 识别序列高亮）→ − 链翻译 → 位置刻度
 * - 暴露 scrollTo(pos) 供环形图联动
 */
import { ref, onMounted, computed, watch } from 'vue'
import type { PlasmidFeature, EnzymeSite } from './PlasmidMap.vue'

interface Props {
  sequence: string
  features?: PlasmidFeature[]
  enzymeSites?: EnzymeSite[]
  highlight?: { start: number; end: number } | null
  bpPerRow?: number
  height?: number
}

const props = withDefaults(defineProps<Props>(), {
  features: () => [],
  enzymeSites: () => [],
  highlight: null,
  bpPerRow: 60,
  height: 320
})

const COL_W = 8          // 每碱基像素宽
const ROW_H = 106        // 行高
const MARGIN_L = 46      // 左侧行号区
const MARGIN_R = 16

const scrollEl = ref<HTMLElement | null>(null)
const canvasEl = ref<HTMLCanvasElement | null>(null)
const scrollTop = ref(0)
const zoomLevel = ref(0) // 0:60bp 1:30bp 2:15bp

const bpRow = computed(() => [60, 30, 15][zoomLevel.value])
const totalRows = computed(() => Math.ceil(props.sequence.length / bpRow.value))
const innerHeight = computed(() => totalRows.value * ROW_H)
const canvasW = computed(() => MARGIN_L + bpRow.value * COL_W + MARGIN_R)

// 行内垂直布局
const Y_ENZ = [4, 16]        // 酶名两档基线
const Y_FEAT_BAR = 38        // 特征条 lane0 顶
const FEAT_BAR_H = 7
const LANE_H = 8             // 特征条层距
const Y_AA_PLUS = 55         // + 链翻译
const Y_BASES = 66           // 碱基带顶
const BASE_H = 15
const Y_AA_MINUS = 84        // − 链翻译
const Y_SCALE = 99           // 位置刻度

const BLOCK_COLORS = ['#FFFFFF', '#F2F5FA']
const RECOG_BG = '#DCE7FA'   // 识别序列底纹
const HL_BG = '#FFF3B8'      // 外部联动高亮

const CODON_TABLE: Record<string, string> = {
  TTT: 'F', TTC: 'F', TTA: 'L', TTG: 'L', CTT: 'L', CTC: 'L', CTA: 'L', CTG: 'L',
  ATT: 'I', ATC: 'I', ATA: 'I', ATG: 'M', GTT: 'V', GTC: 'V', GTA: 'V', GTG: 'V',
  TCT: 'S', TCC: 'S', TCA: 'S', TCG: 'S', CCT: 'P', CCC: 'P', CCA: 'P', CCG: 'P',
  ACT: 'T', ACC: 'T', ACA: 'T', ACG: 'T', GCT: 'A', GCC: 'A', GCA: 'A', GCG: 'A',
  TAT: 'Y', TAC: 'Y', TAA: '*', TAG: '*', CAT: 'H', CAC: 'H', CAA: 'Q', CAG: 'Q',
  AAT: 'N', AAC: 'N', AAA: 'K', AAG: 'K', GAT: 'D', GAC: 'D', GAA: 'E', GAG: 'E',
  TGT: 'C', TGC: 'C', TGA: '*', TGG: 'W', CGT: 'R', CGC: 'R', CGA: 'R', CGG: 'R',
  AGT: 'S', AGC: 'S', AGA: 'R', AGG: 'R', GGT: 'G', GGC: 'G', GGA: 'G', GGG: 'G'
}

const COMPLEMENT: Record<string, string> = { A: 'T', T: 'A', G: 'C', C: 'G', N: 'N' }

function revcomp(s: string): string {
  return s.split('').reverse().map((c) => COMPLEMENT[c] || 'N').join('')
}

function translate(codon: string): string {
  return CODON_TABLE[codon.toUpperCase()] || 'x'
}

function featureColor(type: string, color?: string): string {
  if (color) return color
  const map: Record<string, string> = {
    promoter: '#E8656B', terminator: '#2FA98C', CDS: '#4E79C7', gene: '#4E79C7',
    origin: '#8FBF6B', rep_origin: '#8FBF6B', resistance: '#E8B93E', tag: '#B07FD8',
    MCS: '#E8923D', multiple_cloning_site: '#E8923D', other: '#9AA5B1'
  }
  return map[type] || '#9AA5B1'
}

function darken(hex: string, amount: number): string {
  const n = parseInt(hex.slice(1), 16)
  const r = Math.round(((n >> 16) & 255) * (1 - amount))
  const g = Math.round(((n >> 8) & 255) * (1 - amount))
  const b = Math.round((n & 255) * (1 - amount))
  return `rgb(${r},${g},${b})`
}

interface FeatRange { f: PlasmidFeature; from: number; to: number; lane: number }

/** 特征展开为区间（跨起点或越界坐标按环形取模拆段）并做行内重叠分层 */
function buildRanges(rowStart: number, bpr: number): FeatRange[] {
  const L = props.sequence.length
  const raw: Array<{ f: PlasmidFeature; from: number; to: number; lane: number }> = []
  for (const f of props.features) {
    const s = ((Math.round(f.start) - 1) % L + L) % L + 1
    const e = ((Math.round(f.end) - 1) % L + L) % L + 1
    if (e >= s) raw.push({ f, from: s, to: e, lane: 0 })
    else { raw.push({ f, from: s, to: L, lane: 0 }); raw.push({ f, from: 1, to: e, lane: 0 }) }
  }
  const visible = raw.filter((r) => r.to > rowStart && r.from <= rowStart + bpr)
  // 贪心分层：起始位置排序，区间重叠则换层（最多 3 层）
  visible.sort((a, b) => a.from - b.from)
  const laneEnds: number[] = []
  for (const r of visible) {
    let lane = 0
    while (lane < 3 && laneEnds[lane] !== undefined && r.from <= laneEnds[lane]) lane++
    r.lane = lane
    laneEnds[lane] = r.to
  }
  return visible as FeatRange[]
}

/** 行内酶名两档错层（按切割位置排序贪心放置，避免同名相邻遮挡） */
function tierEnzymes(rowStart: number, bpr: number): Array<{ s: EnzymeSite; x: number; tier: number }> {
  const items: Array<{ s: EnzymeSite; x: number; tier: number }> = []
  const tmpCtx = measureCtx
  tmpCtx.font = '9px Arial, sans-serif'
  for (const s of props.enzymeSites) {
    const cut = s.cut_fwd
    if (cut <= rowStart || cut > rowStart + bpr) continue
    items.push({ s, x: MARGIN_L + (cut - rowStart) * COL_W, tier: 0 })
  }
  items.sort((a, b) => a.x - b.x)
  const placed: Array<{ x: number; w: number; tier: number }> = []
  for (const it of items) {
    const w = tmpCtx.measureText(it.s.name).width
    let tier = -1
    for (const t of [0, 1]) {
      const clash = placed.some((p) => p.tier === t && Math.abs(p.x - it.x) < (p.w + w) / 2 + 6)
      if (!clash) { tier = t; break }
    }
    if (tier === -1) continue // 两档都放不下：仅画切点标记
    it.tier = tier
    placed.push({ x: it.x, w, tier })
  }
  return items
}

const measureCtx = document.createElement('canvas').getContext('2d')!

function draw() {
  const canvas = canvasEl.value
  const container = scrollEl.value
  if (!canvas || !container) return
  const ctx = canvas.getContext('2d')
  if (!ctx) return

  const dpr = window.devicePixelRatio || 1
  const viewH = container.clientHeight
  canvas.width = canvasW.value * dpr
  canvas.height = viewH * dpr
  canvas.style.width = `${canvasW.value}px`
  canvas.style.height = `${viewH}px`
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.fillStyle = '#FFFFFF'
  ctx.fillRect(0, 0, canvasW.value, viewH)

  const L = props.sequence.length
  const bpr = bpRow.value
  const startRow = Math.floor(scrollTop.value / ROW_H)
  const endRow = Math.min(totalRows.value, Math.ceil((scrollTop.value + viewH) / ROW_H))

  for (let row = startRow; row < endRow; row++) {
    const rowTop = row * ROW_H - scrollTop.value
    const rowStart = row * bpr // 0-based

    drawBases(ctx, rowStart, bpr, L, rowTop)
    drawEnzymeMarks(ctx, rowStart, bpr, rowTop)
    drawFeatureRow(ctx, rowStart, bpr, rowTop)
    drawTranslations(ctx, rowStart, bpr, L, rowTop)

    // 行号（左右两端）
    ctx.font = '10px Arial, sans-serif'
    ctx.fillStyle = '#8A8F98'
    ctx.textAlign = 'right'
    ctx.textBaseline = 'middle'
    ctx.fillText(String(rowStart + 1), MARGIN_L - 6, rowTop + Y_BASES + BASE_H / 2)
    if (rowStart + bpr <= L) {
      ctx.textAlign = 'left'
      ctx.fillText(String(rowStart + bpr), MARGIN_L + bpr * COL_W + 4, rowTop + Y_BASES + BASE_H / 2)
    }
  }
}

/** 碱基带：10bp 分组底色 + 识别序列底纹 + 外部高亮 + 碱基字母 */
function drawBases(ctx: CanvasRenderingContext2D, rowStart: number, bpr: number, L: number, rowTop: number) {
  const by = rowTop + Y_BASES
  // 10bp 分组底色
  for (let i = 0; i < bpr; i++) {
    const pos = rowStart + i
    if (pos >= L) break
    ctx.fillStyle = BLOCK_COLORS[Math.floor(pos / 10) % 2]
    ctx.fillRect(MARGIN_L + i * COL_W, by, COL_W, BASE_H)
  }
  // 识别序列底纹（position 为识别序列 5' 端的 1-based 正向链坐标）
  for (const s of props.enzymeSites) {
    if (!s.recognition) continue
    const from = s.position - 1 // 0-based
    const to = from + s.recognition.length
    const fromI = Math.max(from, rowStart)
    const toI = Math.min(to, rowStart + bpr)
    for (let pos = fromI; pos < toI; pos++) {
      ctx.fillStyle = RECOG_BG
      ctx.fillRect(MARGIN_L + (pos - rowStart) * COL_W, by, COL_W, BASE_H)
    }
  }
  // 外部联动高亮
  if (props.highlight) {
    const fromI = Math.max(props.highlight.start - 1, rowStart)
    const toI = Math.min(props.highlight.end, rowStart + bpr)
    for (let pos = fromI; pos < toI; pos++) {
      ctx.fillStyle = HL_BG
      ctx.fillRect(MARGIN_L + (pos - rowStart) * COL_W, by, COL_W, BASE_H)
    }
  }
  // 碱基字母
  ctx.font = '11px Consolas, "Courier New", monospace'
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.fillStyle = '#33363C'
  for (let i = 0; i < bpr; i++) {
    const pos = rowStart + i
    if (pos >= L) break
    ctx.fillText(props.sequence[pos].toUpperCase(), MARGIN_L + i * COL_W + COL_W / 2, by + BASE_H / 2)
  }
  // 位置刻度（每 10bp 的末位）
  ctx.font = '8.5px Arial, sans-serif'
  ctx.fillStyle = '#A8ADB5'
  for (let i = 9; i < bpr; i += 10) {
    const pos = rowStart + i
    if (pos >= L) break
    ctx.fillText(String(pos + 1), MARGIN_L + i * COL_W + COL_W / 2, rowTop + Y_SCALE)
  }
}

/** 酶名 + 切点标记（两档错层，标记线从酶名下方延伸到碱基带） */
function drawEnzymeMarks(ctx: CanvasRenderingContext2D, rowStart: number, bpr: number, rowTop: number) {
  const items = tierEnzymes(rowStart, bpr)
  ctx.font = '9px Arial, sans-serif'
  ctx.textBaseline = 'middle'
  for (const { s, x, tier } of items) {
    const named = tier >= 0
    const yName = rowTop + Y_ENZ[named ? tier : 0]
    // 切点竖线 + 底部三角
    ctx.beginPath()
    ctx.moveTo(x + 0.5, yName + 5)
    ctx.lineTo(x + 0.5, rowTop + Y_BASES - 3)
    ctx.strokeStyle = '#B0413E'
    ctx.lineWidth = 1
    ctx.stroke()
    ctx.beginPath()
    ctx.moveTo(x - 3, rowTop + Y_BASES - 3)
    ctx.lineTo(x + 3, rowTop + Y_BASES - 3)
    ctx.lineTo(x, rowTop + Y_BASES + 2)
    ctx.closePath()
    ctx.fillStyle = '#B0413E'
    ctx.fill()
    if (named) {
      ctx.fillStyle = '#B0413E'
      ctx.textAlign = 'center'
      ctx.fillText(s.name, x, yName)
    }
  }
}

/** 特征条（重叠分层）+ 方向箭头 + 名称 */
function drawFeatureRow(ctx: CanvasRenderingContext2D, rowStart: number, bpr: number, rowTop: number) {
  const ranges = buildRanges(rowStart, bpr)
  const named = new Set<PlasmidFeature>()
  for (const { f, from, to, lane } of ranges) {
    const x1 = MARGIN_L + (from - 1 - rowStart) * COL_W
    const x2 = MARGIN_L + (to - rowStart) * COL_W
    const color = featureColor(f.type, f.color)
    const y = rowTop + Y_FEAT_BAR + lane * LANE_H

    ctx.beginPath()
    if (f.strand === '-') {
      // 左端箭头
      ctx.moveTo(x2, y)
      ctx.lineTo(x1 + 5, y + FEAT_BAR_H / 2)
      ctx.lineTo(x2, y + FEAT_BAR_H)
    } else {
      // 右端箭头
      ctx.moveTo(x1, y)
      ctx.lineTo(x2 - 5, y + FEAT_BAR_H / 2)
      ctx.lineTo(x1, y + FEAT_BAR_H)
    }
    ctx.closePath()
    ctx.fillStyle = color
    ctx.fill()

    // 名称：每行内每特征只画一次
    if (!named.has(f)) {
      named.add(f)
      ctx.fillStyle = darken(color, 0.25)
      ctx.font = '9px Arial, sans-serif'
      ctx.textAlign = 'left'
      ctx.textBaseline = 'alphabetic'
      ctx.fillText(f.name, x1 + 2, y - 3)
    }
  }
}

/** 翻译行：+ 链在碱基上方、− 链在下方，颜色随特征 */
function drawTranslations(ctx: CanvasRenderingContext2D, rowStart: number, bpr: number, L: number, rowTop: number) {
  ctx.font = '10px Consolas, monospace'
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  for (const f of props.features) {
    const isCDS = (f.type === 'CDS' || f.type === 'gene') && f.end - f.start + 1 >= 3
    if (!isCDS) continue
    const color = darken(featureColor(f.type, f.color), 0.15)
    const plus = f.strand !== '-'
    ctx.fillStyle = color

    if (plus) {
      const codonCount = Math.floor((f.end - f.start + 1) / 3)
      for (let k = 0; k < codonCount; k++) {
        const first = f.start + 3 * k      // 密码子首碱基（1-based）
        const mid = first + 1
        if (mid - 1 < rowStart || mid - 1 >= rowStart + bpr || mid > L) continue
        if (first - 1 < rowStart || first + 1 > rowStart + bpr) continue // 跨行密码子略过
        const aa = translate(props.sequence.slice(first - 1, first + 2))
        ctx.fillText(aa, MARGIN_L + (mid - 1 - rowStart) * COL_W + COL_W / 2, rowTop + Y_AA_PLUS)
      }
    } else {
      const codonCount = Math.floor((f.end - f.start + 1) / 3)
      for (let k = 0; k < codonCount; k++) {
        const hi = f.end - 3 * k           // 该密码子在正向链的最高坐标
        const lo = hi - 2
        if (lo < f.start) break
        const mid = hi - 1
        if (mid - 1 < rowStart || mid - 1 >= rowStart + bpr || mid > L) continue
        if (hi > rowStart + bpr || lo <= rowStart) continue
        const codon = revcomp(props.sequence.slice(lo - 1, hi))
        ctx.fillText(translate(codon), MARGIN_L + (mid - 1 - rowStart) * COL_W + COL_W / 2, rowTop + Y_AA_MINUS)
      }
    }
  }
}

function onScroll() {
  const container = scrollEl.value
  if (!container) return
  scrollTop.value = container.scrollTop
  if (canvasEl.value) canvasEl.value.style.top = `${container.scrollTop}px`
  draw()
}

function scrollTo(pos: number) {
  const container = scrollEl.value
  if (!container) return
  const row = Math.floor((pos - 1) / bpRow.value)
  container.scrollTop = Math.max(0, row * ROW_H - ROW_H)
  onScroll()
}
defineExpose({ scrollTo })

watch(() => [props.sequence, props.features, props.enzymeSites, props.highlight, zoomLevel.value], draw, { deep: true })
onMounted(draw)
</script>

<template>
  <div class="sequence-view">
    <div class="sv-toolbar">
      <span class="sv-title">Sequence</span>
      <div class="sv-zoom">
        <button
          v-for="(label, i) in ['60 bp', '30 bp', '15 bp']"
          :key="i"
          class="sv-zoom-btn"
          :class="{ active: zoomLevel === i }"
          @click="zoomLevel = i"
        >
          {{ label }}
        </button>
      </div>
    </div>
    <div ref="scrollEl" class="sv-scroll" :style="{ height: height + 'px' }" @scroll="onScroll">
      <div class="sv-inner" :style="{ height: innerHeight + 'px' }"></div>
      <canvas ref="canvasEl" class="sv-canvas" @click.self.prevent></canvas>
    </div>
  </div>
</template>

<style scoped>
.sequence-view {
  border: 1px solid #E5E5E5;
  border-radius: 8px;
  background: #fff;
  overflow: hidden;
}

.sv-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 12px;
  background: #F7F7F7;
  border-bottom: 1px solid #E5E5E5;
}

.sv-title {
  font-size: 12px;
  font-weight: 600;
  color: #666;
}

.sv-zoom { display: flex; gap: 4px; }

.sv-zoom-btn {
  border: 1px solid #DDD;
  background: #fff;
  border-radius: 4px;
  font-size: 11px;
  padding: 2px 8px;
  cursor: pointer;
  color: #666;
}

.sv-zoom-btn.active {
  background: #4E79C7;
  border-color: #4E79C7;
  color: #fff;
}

.sv-scroll {
  position: relative;
  overflow-y: auto;
  overflow-x: auto;
}

.sv-inner { width: 100%; }

.sv-canvas {
  position: absolute;
  left: 0;
  top: 0;
}
</style>
