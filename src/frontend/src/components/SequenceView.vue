<script setup lang="ts">
/**
 * SnapGene 风格线性序列视图（Canvas 虚拟滚动）
 * - 分行显示碱基（10bp 分组交替着色）
 * - CDS 特征翻译氨基酸行（+ 链在碱基上方 / - 链在下方）
 * - 特征彩色区间条与名称、酶切位点标注与识别序列高亮
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
const ROW_H = 78         // 行高（含翻译行、特征条、酶标注）
const MARGIN_L = 46      // 左侧行号区
const MARGIN_R = 8

const scrollEl = ref<HTMLElement | null>(null)
const canvasEl = ref<HTMLCanvasElement | null>(null)
const scrollTop = ref(0)
const zoomLevel = ref(0) // 0:60bp 1:30bp 2:15bp

const bpRow = computed(() => [60, 30, 15][zoomLevel.value])
const totalRows = computed(() => Math.ceil(props.sequence.length / bpRow.value))
const innerHeight = computed(() => totalRows.value * ROW_H)
const canvasW = computed(() => MARGIN_L + bpRow.value * COL_W + MARGIN_R)

const BASE_COLORS = ['#333', '#333', '#333', '#333']
const BLOCK_COLORS = ['#FFFFFF', '#F2F7FF'] // 10bp 分组交替底色

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

function featureColor(type: string, color?: string): string {
  if (color) return color
  const map: Record<string, string> = {
    promoter: '#FF6B6B', terminator: '#4ECDC4', CDS: '#45B7D1', gene: '#45B7D1',
    origin: '#96CEB4', resistance: '#F2C94C', tag: '#DDA0DD', MCS: '#FFA500',
    multiple_cloning_site: '#FFA500', other: '#CCCCCC'
  }
  return map[type] || '#CCCCCC'
}

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
  ctx.clearRect(0, 0, canvasW.value, viewH)

  const L = props.sequence.length
  const bpr = bpRow.value
  const startRow = Math.floor(scrollTop.value / ROW_H)
  const endRow = Math.min(totalRows.value, Math.ceil((scrollTop.value + viewH) / ROW_H))

  for (let row = startRow; row < endRow; row++) {
    const rowTop = row * ROW_H - scrollTop.value
    const rowStart = row * bpr // 0-based

    // 行号
    ctx.font = '10px Arial, sans-serif'
    ctx.fillStyle = '#999'
    ctx.textAlign = 'left'
    ctx.textBaseline = 'top'
    ctx.fillText(String(rowStart + 1), 6, rowTop + 36)

    // 10bp 分组底色
    for (let i = 0; i < bpr; i++) {
      const pos = rowStart + i
      if (pos >= L) break
      const blockIdx = Math.floor(pos / 10) % 2
      ctx.fillStyle = BLOCK_COLORS[blockIdx]
      ctx.fillRect(MARGIN_L + i * COL_W, rowTop + 36, COL_W, 14)
    }

    // 碱基
    ctx.font = '11px Consolas, monospace'
    ctx.textAlign = 'center'
    ctx.textBaseline = 'middle'
    for (let i = 0; i < bpr; i++) {
      const pos = rowStart + i
      if (pos >= L) break
      const base = props.sequence[pos].toUpperCase()
      let bg = BLOCK_COLORS[Math.floor(pos / 10) % 2]
      // 高亮区间
      if (props.highlight && pos + 1 >= props.highlight.start && pos + 1 <= props.highlight.end) {
        bg = '#FFF3B8'
      }
      const x = MARGIN_L + i * COL_W
      ctx.fillStyle = bg
      ctx.fillRect(x, rowTop + 36, COL_W, 14)
      ctx.fillStyle = BASE_COLORS[0]
      ctx.fillText(base, x + COL_W / 2, rowTop + 43)
    }

    // 每 10bp 位置刻度
    ctx.fillStyle = '#BBB'
    ctx.font = '9px Arial, sans-serif'
    for (let i = 0; i < bpr; i += 10) {
      const pos = rowStart + i
      if (pos >= L) break
      if ((pos + 1) % 10 === 0 && i > 0) {
        ctx.fillText(String(pos + 1), MARGIN_L + i * COL_W + 4, rowTop + 56)
      }
    }

    drawFeatureRow(ctx, rowStart, bpr, rowTop)
    drawTranslation(ctx, rowStart, bpr, L, rowTop)
    drawEnzymeMarks(ctx, rowStart, bpr, L, rowTop)
  }
}

function drawFeatureRow(ctx: CanvasRenderingContext2D, rowStart: number, bpr: number, rowTop: number) {
  // 特征彩色条（跨行连续）
  for (const f of props.features) {
    const fs = f.start - 1 // 0-based
    const fe = f.end
    if (fe <= rowStart || fs >= rowStart + bpr) continue
    const from = Math.max(fs, rowStart)
    const to = Math.min(fe, rowStart + bpr)
    const x1 = MARGIN_L + (from - rowStart) * COL_W
    const x2 = MARGIN_L + (to - rowStart) * COL_W
    const color = featureColor(f.type, f.color)
    ctx.fillStyle = color
    ctx.fillRect(x1, rowTop + 22, x2 - x1, 6)
    // 行内首段绘制名称
    if (fs >= rowStart && fs < rowStart + bpr) {
      ctx.fillStyle = '#444'
      ctx.font = '9px Arial, sans-serif'
      ctx.textAlign = 'left'
      ctx.fillText(f.name, x1 + 2, rowTop + 12)
    }
    // 箭头方向指示
    if (to === Math.min(fe, rowStart + bpr) && f.strand === '-') {
      ctx.beginPath()
      ctx.moveTo(x1, rowTop + 19)
      ctx.lineTo(x1 + 5, rowTop + 25)
      ctx.lineTo(x1, rowTop + 31)
      ctx.closePath()
      ctx.fill()
    } else if (to === Math.min(fe, rowStart + bpr) && f.strand !== '-') {
      ctx.beginPath()
      ctx.moveTo(x2, rowTop + 19)
      ctx.lineTo(x2 - 5, rowTop + 25)
      ctx.lineTo(x2, rowTop + 31)
      ctx.closePath()
      ctx.fill()
    }
  }
}

function drawTranslation(ctx: CanvasRenderingContext2D, rowStart: number, bpr: number, L: number, rowTop: number) {
  for (const f of props.features) {
    const isCDS = (f.type === 'CDS' || f.type === 'gene') && f.end - f.start + 1 >= 3
    if (!isCDS) continue
    const frame = (f.start - 1) % 3 // 特征内的密码子相位
    const plus = f.strand !== '-'
    for (let i = 0; i < bpr; i++) {
      const pos = rowStart + i
      if (pos >= L) break
      const inFeat = pos + 1 >= f.start && pos + 1 <= f.end
      if (!inFeat) continue
      const offset = pos + 1 - f.start
      // (offset - frame) % 3 === 0 时 pos 恰为密码子第一个碱基
      if ((offset - frame) % 3 !== 0 || offset + 3 > f.end - f.start + 1) continue
      const codonStart = pos
      const codon = plus
        ? props.sequence.slice(codonStart, codonStart + 3)
        : revcomp(props.sequence.slice(codonStart, codonStart + 3))
      const aa = (CODON_TABLE[codon.toUpperCase()] || 'x') + ''
      ctx.font = '9px Arial, sans-serif'
      ctx.textAlign = 'center'
      ctx.textBaseline = 'middle'
      ctx.fillStyle = plus ? '#2C7FB8' : '#D95F02'
      const y = plus ? rowTop + 32 : rowTop + 58
      if (i + 1 < bpr) ctx.fillText(aa, MARGIN_L + i * COL_W + COL_W * 1.5, y)
    }
  }
}

function drawEnzymeMarks(ctx: CanvasRenderingContext2D, rowStart: number, bpr: number, L: number, rowTop: number) {
  ctx.font = '8px Arial, sans-serif'
  ctx.textAlign = 'center'
  ctx.textBaseline = 'top'
  for (const s of props.enzymeSites) {
    const cut = s.cut_fwd // 0-based：切在第 cut-1 与 cut 位之间
    if (cut <= rowStart || cut > rowStart + bpr || cut > L) continue
    const i = cut - rowStart
    const x = MARGIN_L + i * COL_W
    // 交替上下错开重名密集
    const tier = s.name.length % 2
    const y = tier === 0 ? rowTop + 64 : rowTop + 72
    ctx.beginPath()
    ctx.moveTo(x, rowTop + 36)
    ctx.lineTo(x, rowTop + 63 - tier * 6)
    ctx.strokeStyle = '#B0413E'
    ctx.lineWidth = 1
    ctx.stroke()
    ctx.fillStyle = '#B0413E'
    ctx.fillText(s.name, x, y)
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
  background: #45B7D1;
  border-color: #45B7D1;
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
