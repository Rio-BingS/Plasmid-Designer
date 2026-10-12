<script setup lang="ts">
/**
 * 序列视图：按行排布碱基（可选互补链）、酶切切口（顶/底链切点 + 折线）与酶名、
 * 特征/引物分道条、选中 CDS 的氨基酸翻译、搜索命中与选区。
 * 只渲染可见行（虚拟滚动），300 kb 也只画几十行。
 * 交互：拖拽选区（可跨行）；点击特征/酶名选中；滚动位置上报给缩略导航。
 */
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useElementSize } from '@vueuse/core'
import type { MapDocument, Range } from '../types'
import { useMapStore } from '../store'
import { rangeFromBoundaries, splitRange } from '../core/coords'
import { stackLanes } from '../core/lanes'
import { enzymePriority, fitsInside, layoutLinearLabels, type LinearLabelLayout } from '../core/labels'
import { cutStyle, type CutMark } from '../core/enzymes'
import { complement, translateFeature } from '../core/seq'
import { darken, featureColor, textOn } from '../core/palette'
import { basesPerRow, rowAtY, rowOf, rowOffsets, visibleRowsVar } from '../core/virtual'
import { hitKeyOf, resolveHit, setHoverFromEvent } from '../hits'
import MapTooltip from './MapTooltip.vue'

const props = withDefaults(defineProps<{
  doc?: MapDocument
  /** 视图高度（px） */
  height?: number
  width?: number
}>(), { height: 380 })

const { store, own } = useMapStore(() => props.doc)
const scroller = ref<HTMLElement | null>(null)
const inner = ref<HTMLElement | null>(null)
const { width: boxW } = useElementSize(scroller)
const W = computed(() => Math.round(props.width ?? Math.max(260, (boxW.value || 820) - 4)))
const CHAR_W = 8
const FONT = 13
const L = computed(() => store.L.value)
const seq = computed(() => store.doc.value.sequence)
const gutter = computed(() => String(L.value).length * 7.5 + 14)
const perRow = computed(() => basesPerRow(W.value, CHAR_W, gutter.value + 8))
const totalRows = computed(() => Math.max(1, Math.ceil(L.value / perRow.value)))

// ---------------- 分道（特征 + 引物，全序列统一分道，每行只占用到的分道） ----------------
const MAX_LANES = 8
const LANE_H = 14
const ENZ_ROW = 12
const ENZ_MAX_ROWS = 3
const laneItems = computed(() => [
  ...store.features.value.map((f) => ({ id: `f:${f.id}`, start: f.start, end: f.end })),
  ...store.primers.value.map((p) => ({ id: `p:${p.id}`, start: p.start, end: p.end })),
])
const lanes = computed(() => stackLanes(laneItems.value, L.value, { circular: store.circular.value, gap: 2, maxLanes: MAX_LANES }))

/** 每行用到的分道（位掩码）：没有特征的行不再为全局最大分道数留白 */
const rowLaneMask = computed(() => {
  const n = totalRows.value
  const pr = perRow.value
  const mask = new Uint16Array(n)
  const mark = (r: Range, lane: number) => {
    const parts = store.circular.value ? splitRange(r, L.value) : [{ start: Math.min(r.start, r.end), end: Math.max(r.start, r.end) }]
    for (const p of parts) {
      const b = Math.min(n - 1, rowOf(p.end, pr))
      for (let i = Math.max(0, rowOf(p.start, pr)); i <= b; i++) mask[i] |= 1 << lane
    }
  }
  for (const f of store.features.value) {
    const lane = lanes.value.lanes.get(`f:${f.id}`)
    if (lane === undefined) continue
    for (const rr of f.segments?.length ? f.segments : [f]) mark(rr, lane)
  }
  for (const p of store.primers.value) {
    const lane = lanes.value.lanes.get(`p:${p.id}`)
    if (lane !== undefined) mark(p, lane)
  }
  return mask
})
function popcount(m: number): number {
  let c = 0
  for (let v = m; v; v &= v - 1) c++
  return c
}
/** 全局分道 → 本行压缩后的分道序号（保持上下顺序） */
function compactLane(mask: number, lane: number): number {
  return popcount(mask & ((1 << lane) - 1))
}

const translation = computed(() => {
  const f = store.selectedFeature.value
  if (!store.show.translations || !f || f.type !== 'cds' || !seq.value) return null
  const t = translateFeature(f, seq.value)
  const byRow = new Map<number, { col: number; aa: string; i: number }[]>()
  t.codonCenters.forEach((p, i) => {
    if (p === undefined) return
    const r = rowOf(p, perRow.value)
    const list = byRow.get(r) ?? []
    list.push({ col: (p - 1) % perRow.value, aa: t.protein[i], i })
    byRow.set(r, list)
  })
  return { byRow, length: t.protein.length }
})

// ---------------- 酶切：按行分桶 + 每行酶名布局（行高要用，一次算完） ----------------
const colX = (col: number) => gutter.value + col * CHAR_W
const showEnz = computed(() => store.show.enzymes && store.enzymes.value.length > 0)
const cutRows = computed(() => {
  const by = new Map<number, CutMark[]>()
  const layouts = new Map<number, LinearLabelLayout>()
  if (!showEnz.value) return { by, layouts }
  const n = totalRows.value
  const pr = perRow.value
  // 切点边界 b 属于第 floor(b / perRow) 行（行首边界归本行，行尾边界归下一行，序列末端归末行）
  for (const c of store.cuts.value) {
    const row = Math.min(n - 1, Math.floor(c.cut / pr))
    const list = by.get(row)
    if (list) list.push(c)
    else by.set(row, [c])
  }
  for (const [row, cs] of by) {
    layouts.set(row, layoutLinearLabels(
      cs.map((c) => ({ id: `e:${c.id}`, text: c.name, bold: cutStyle(c).weight > 400, x: colX(c.cut - row * pr), priority: enzymePriority(c.enzyme.cut_count) })),
      { width: W.value, fontSize: 10, maxRows: ENZ_MAX_ROWS, gap: 6, left: gutter.value - 4, maxShift: 6 * CHAR_W },
    ))
  }
  return { by, layouts }
})

// ---------------- 行内纵向布局（每行按自身内容定高） ----------------
interface RowGeo { enzTop: number; enzRows: number; seqY: number; compY: number; rulerY: number; laneTop: number; transY: number; rowH: number; mask: number }
function rowGeo(row: number): RowGeo {
  const enzRows = cutRows.value.layouts.get(row)?.rows ?? 0
  const mask = rowLaneMask.value[row] ?? 0
  let y = 4
  const enzTop = y
  y += enzRows * ENZ_ROW + (enzRows ? 2 : 0)
  const seqY = y
  y += 16
  const compY = y
  if (store.show.complement) y += 16
  const rulerY = y
  y += 10
  const laneTop = y + 2
  const nl = popcount(mask)
  y += nl * LANE_H + (nl ? 2 : 0)
  const transY = y
  if (translation.value?.byRow.has(row)) y += 15
  return { enzTop, enzRows, seqY, compY, rulerY, laneTop, transY, rowH: y + 8, mask }
}
const offsets = computed(() => {
  const n = totalRows.value
  const h = new Float64Array(n)
  for (let i = 0; i < n; i++) h[i] = rowGeo(i).rowH
  return rowOffsets(h)
})
const totalHeight = computed(() => offsets.value[offsets.value.length - 1])

// ---------------- 虚拟滚动 ----------------
const scrollTop = ref(0)
const rowRange = computed(() => visibleRowsVar(scrollTop.value, props.height, offsets.value, 4))
function onScroll() {
  scrollTop.value = scroller.value?.scrollTop ?? 0
}
watch([rowRange, perRow], () => {
  const off = offsets.value
  const first = rowAtY(off, scrollTop.value)
  const lastRow = rowAtY(off, Math.max(scrollTop.value, scrollTop.value + props.height - 1))
  store.seqVisible.value = { start: first * perRow.value + 1, end: Math.min(L.value, (lastRow + 1) * perRow.value) }
}, { immediate: true })

watch(() => store.navigate.value.n, async () => {
  const nav = store.navigate.value
  const r = Math.min(totalRows.value - 1, rowOf(nav.range ? nav.range.start : nav.pos, perRow.value))
  const off = offsets.value
  const top = off[r]
  const bottom = off[r + 1]
  const el = scroller.value
  if (!el) return
  if (top >= el.scrollTop && bottom <= el.scrollTop + props.height) return
  el.scrollTop = Math.max(0, top - (r > 0 ? off[r] - off[r - 1] : 0))
  await nextTick()
  onScroll()
})

// ---------------- 每行内容 ----------------
interface RowSeg { x1: number; x2: number; headR: boolean; headL: boolean }

/** 区间与第 row 行相交的像素段 */
function rowSegs(r: Range, row: number): RowSeg[] {
  const rs = row * perRow.value + 1
  const re = Math.min(L.value, rs + perRow.value - 1)
  const out: RowSeg[] = []
  const parts = store.circular.value ? splitRange(r, L.value) : [{ start: Math.min(r.start, r.end), end: Math.max(r.start, r.end) }]
  for (const p of parts) {
    const a = Math.max(p.start, rs)
    const b = Math.min(p.end, re)
    if (a > b) continue
    out.push({ x1: colX(a - rs), x2: colX(b - rs + 1), headR: b === p.end, headL: a === p.start })
  }
  return out
}

const rows = computed(() => {
  const [a, b] = rowRange.value
  const out = []
  const off = offsets.value
  for (let row = a; row < b; row++) {
    const g = rowGeo(row)
    const rs = row * perRow.value + 1
    const re = Math.min(L.value, rs + perRow.value - 1)
    const text = seq.value.slice(rs - 1, re)
    const n = re - rs + 1
    const laneY = (lane: number) => g.laneTop + compactLane(g.mask, lane) * LANE_H
    // 特征/引物条
    const bars = []
    for (const f of store.features.value) {
      const lane = lanes.value.lanes.get(`f:${f.id}`)
      if (lane === undefined) continue
      const ranges = f.segments?.length ? f.segments : [{ start: f.start, end: f.end }]
      for (const rr of ranges) {
        for (const s of rowSegs(rr, row)) {
          const color = featureColor(f)
          const head = (f.strand === 1 && s.headR) || (f.strand === -1 && s.headL)
          const w = s.x2 - s.x1
          bars.push({
            key: `f:${f.id}`, k: `${f.id}-${rr.start}-${s.x1}`, x1: s.x1, x2: s.x2, y: laneY(lane), color,
            stroke: darken(color), strand: head ? f.strand : 0, label: fitsInside(f.name, 10, w - 10) ? f.name : '', textColor: textOn(color),
          })
        }
      }
    }
    for (const p of store.primers.value) {
      const lane = lanes.value.lanes.get(`p:${p.id}`)
      if (lane === undefined) continue
      for (const s of rowSegs(p, row)) {
        const head = (p.strand === 1 && s.headR) || (p.strand === -1 && s.headL)
        bars.push({
          key: `p:${p.id}`, k: `p${p.id}-${s.x1}`, x1: s.x1, x2: s.x2, y: laneY(lane), color: '#5aa0e0', stroke: '#2f6aa8',
          strand: head ? p.strand : 0, label: fitsInside(p.name, 10, s.x2 - s.x1 - 10) ? p.name : '', textColor: '#ffffff',
        })
      }
    }
    const cuts = (cutRows.value.by.get(row) ?? []).map((c) => {
      const st = cutStyle(c)
      const xt = colX(c.cut - (rs - 1))
      const bIn = c.cutBottom >= rs - 1 && c.cutBottom <= re
      const xb = bIn ? colX(c.cutBottom - (rs - 1)) : xt
      return { key: `e:${c.id}`, name: c.name, xt, xb, color: st.color, weight: st.weight, dash: st.dash, blocked: st.blocked }
    })
    const cutByKey = new Map(cuts.map((c) => [c.key, c]))
    const lay = cutRows.value.layouts.get(row)
    // 第 0 行酶名贴着序列，往上依次排；错开锚点的酶名画一根细引线
    const enzLabels = (lay?.placed ?? []).map((l) => {
      const y = g.enzTop + (g.enzRows - 1 - l.row) * ENZ_ROW + 10
      const c = cutByKey.get(l.id)
      const mid = l.x + l.w / 2
      return { id: l.id, text: l.text, x: l.x, y, color: c?.color, weight: c?.weight, leader: Math.abs(mid - l.cx) > 3 ? { x1: l.cx, x2: mid, y1: g.seqY, y2: y + 2 } : null }
    })
    const sel = store.selection.value ? rowSegs(store.selection.value, row) : []
    const hits = store.search.hits.flatMap((h, i) => rowSegs(h, row).map((s) => ({ ...s, key: `s:${i}`, current: i === store.search.index })))
    const hv = store.hover.value?.range && !store.hover.value.id.startsWith('t:') ? rowSegs(store.hover.value.range, row) : []
    const caret = !store.selection.value && store.caret.value !== null && store.caret.value >= rs - 1 && store.caret.value <= re
      ? colX(store.caret.value - (rs - 1)) : null
    const aa = translation.value?.byRow.get(row) ?? []
    out.push({ row, top: off[row], g, rs, re, n, text, comp: store.show.complement ? complement(text) : '', bars, cuts, enzLabels, sel, hits, hv, caret, aa })
  }
  return out
})

const unshownLanes = computed(() => lanes.value.overflow)
watch(unshownLanes, (ids) => {
  store.setUnshown('sequence', ids.map((k) => ({ id: k, name: resolveHit(store, k)?.title ?? k, reason: '序列视图分道已满' })))
}, { immediate: true })
onBeforeUnmount(() => store.setUnshown('sequence', []))

// ---------------- 交互 ----------------
let dragging = false
let anchor = 0
let moved = false
let downKey: string | null = null

function boundaryAt(e: { clientX: number; clientY: number }): number {
  const rect = inner.value?.getBoundingClientRect()
  if (!rect) return 0
  const row = rowAtY(offsets.value, e.clientY - rect.top)
  const rs = row * perRow.value
  const n = Math.min(perRow.value, L.value - rs)
  const col = Math.max(0, Math.min(n, Math.round((e.clientX - rect.left - gutter.value) / CHAR_W)))
  return rs + col
}

function onPointerDown(e: PointerEvent) {
  if (e.button > 0) return
  dragging = true
  moved = false
  downKey = hitKeyOf(e.target)
  anchor = boundaryAt(e)
  ;(e.currentTarget as Element | null)?.setPointerCapture?.(e.pointerId)
}
function onPointerMove(e: PointerEvent) {
  if (!dragging) {
    setHoverFromEvent(store, e)
    return
  }
  const b = boundaryAt(e)
  if (b !== anchor) {
    moved = true
    store.select(rangeFromBoundaries(anchor, b, L.value, false))
  }
}
function onPointerUp(e: PointerEvent) {
  if (dragging && !moved) {
    const info = resolveHit(store, downKey ?? hitKeyOf(e.target))
    if (info) info.select()
    else store.setCaret(anchor)
  }
  dragging = false
}
function onLeave() { if (!dragging) store.hover.value = null }
/** 浏览器接管手势（触屏滚动/系统手势）或丢失捕获：放弃本次拖拽，不触发选择/光标 */
function onPointerCancel() {
  if (!dragging) return
  dragging = false
  moved = false
  downKey = null
  store.hover.value = null
}

defineExpose({ scroller, perRow, offsets, totalHeight })
</script>

<template>
  <div class="smap">
    <div ref="scroller" class="smap-scroll" :style="{ height: `${height}px` }" @scroll.passive="onScroll">
      <div ref="inner" class="smap-inner" :style="{ height: `${totalHeight}px` }"
           @pointerdown="onPointerDown" @pointermove="onPointerMove" @pointerup="onPointerUp" @pointerleave="onLeave"
           @pointercancel="onPointerCancel" @lostpointercapture="onPointerCancel">
        <svg v-for="r in rows" :key="r.row" class="smap-row" :width="W" :height="r.g.rowH"
             :style="{ top: `${r.top}px` }" xmlns="http://www.w3.org/2000/svg">
          <!-- 底色：悬停 / 搜索命中 / 选区 -->
          <rect v-for="(s, i) in r.hv" :key="'h' + i" :x="s.x1" :y="r.g.seqY" :width="s.x2 - s.x1" :height="r.g.rulerY - r.g.seqY" fill="#f59e0b" fill-opacity="0.15" />
          <rect v-for="(s, i) in r.hits" :key="'s' + i" :data-hit="s.key" :x="s.x1" :y="r.g.seqY" :width="s.x2 - s.x1" :height="r.g.rulerY - r.g.seqY"
                :fill="s.current ? '#f97316' : '#facc15'" fill-opacity="0.45" class="smap-search" />
          <rect v-for="(s, i) in r.sel" :key="'sel' + i" :x="s.x1" :y="r.g.seqY" :width="s.x2 - s.x1" :height="r.g.rulerY - r.g.seqY + 2"
                fill="#4f46e5" fill-opacity="0.2" class="smap-selection" />

          <text :x="gutter - 8" :y="r.g.seqY + 12" text-anchor="end" font-size="11" fill="#9ca3af" class="smap-num">{{ r.rs.toLocaleString('en-US') }}</text>

          <!-- 酶名与切口 -->
          <g v-for="l in r.enzLabels" :key="l.id" :data-hit="l.id" class="smap-enz">
            <line v-if="l.leader" :x1="l.leader.x1" :x2="l.leader.x2" :y1="l.leader.y1" :y2="l.leader.y2" stroke="#c5c9ce" stroke-width="0.7" />
            <text :x="l.x" :y="l.y" font-size="10" :fill="l.color" :font-weight="l.weight">{{ l.text }}</text>
          </g>
          <g v-for="c in r.cuts" :key="c.key" :data-hit="c.key" :stroke="c.color" stroke-width="1.2" :stroke-dasharray="c.dash"
             :class="['smap-cut', { 'is-blocked': c.blocked }]">
            <line :x1="c.xt" :x2="c.xt" :y1="r.g.seqY" :y2="r.g.seqY + 15" />
            <template v-if="store.show.complement">
              <line :x1="c.xt" :x2="c.xb" :y1="r.g.seqY + 15" :y2="r.g.seqY + 15" />
              <line :x1="c.xb" :x2="c.xb" :y1="r.g.compY + 1" :y2="r.g.compY + 16" />
            </template>
          </g>

          <!-- 碱基：textLength 把每个字符钉在 CHAR_W 网格上，任何等宽字体都对齐 -->
          <text :x="gutter" :y="r.g.seqY + 12" :font-size="FONT" font-family="Consolas, Menlo, 'Courier New', monospace"
                :textLength="r.n * CHAR_W" lengthAdjust="spacing" fill="#111827" class="smap-seq">{{ r.text }}</text>
          <text v-if="store.show.complement" :x="gutter" :y="r.g.compY + 12" :font-size="FONT"
                font-family="Consolas, Menlo, 'Courier New', monospace" :textLength="r.n * CHAR_W" lengthAdjust="spacing"
                fill="#9ca3af" class="smap-comp">{{ r.comp }}</text>

          <!-- 标尺：每 10 bp 一刻 -->
          <line :x1="gutter" :x2="gutter + r.n * CHAR_W" :y1="r.g.rulerY + 2" :y2="r.g.rulerY + 2" stroke="#d1d5db" />
          <line v-for="k in Math.floor(r.n / 10)" :key="'rt' + k" :x1="gutter + k * 10 * CHAR_W" :x2="gutter + k * 10 * CHAR_W"
                :y1="r.g.rulerY - 1" :y2="r.g.rulerY + 5" stroke="#9ca3af" />

          <!-- 特征 / 引物条 -->
          <g v-for="b in r.bars" :key="b.k" :data-hit="b.key"
             :class="['smap-bar', { 'is-selected': store.selectedFeatureId.value && b.key === `f:${store.selectedFeatureId.value}` }]">
            <path :d="b.strand === 1
                    ? `M${b.x1} ${b.y}H${b.x2 - 5}L${b.x2} ${b.y + 5.5}L${b.x2 - 5} ${b.y + 11}H${b.x1}Z`
                    : b.strand === -1
                      ? `M${b.x2} ${b.y}H${b.x1 + 5}L${b.x1} ${b.y + 5.5}L${b.x1 + 5} ${b.y + 11}H${b.x2}Z`
                      : `M${b.x1} ${b.y}H${b.x2}V${b.y + 11}H${b.x1}Z`"
                  :fill="b.color" :stroke="b.stroke" stroke-width="0.6" />
            <text v-if="b.label" :x="(b.x1 + b.x2) / 2" :y="b.y + 8.5" text-anchor="middle" font-size="10" :fill="b.textColor" pointer-events="none">{{ b.label }}</text>
          </g>

          <!-- 选中 CDS 的翻译 -->
          <text v-for="a in r.aa" :key="'aa' + a.i" :x="gutter + (a.col + 0.5) * CHAR_W" :y="r.g.transY + 11" text-anchor="middle"
                font-size="11" font-family="Consolas, Menlo, monospace" :fill="a.aa === '*' ? '#dc2626' : '#7c3aed'" class="smap-aa">{{ a.aa }}</text>

          <line v-if="r.caret !== null" :x1="r.caret" :x2="r.caret" :y1="r.g.seqY - 1" :y2="r.g.rulerY + 2" stroke="#4f46e5" stroke-width="1.5" class="smap-caret" />
        </svg>
      </div>
    </div>
    <MapTooltip v-if="own" :store="store" />
  </div>
</template>

<style scoped>
.smap { position: relative; width: 100%; }
.smap-scroll { overflow-y: auto; overflow-x: hidden; border: 1px solid var(--border-color); border-radius: 6px; background: var(--bg-primary); }
.smap-inner { position: relative; width: 100%; cursor: text; user-select: none; touch-action: pan-y; }
.smap-row { position: absolute; left: 0; display: block; overflow: visible; }
.smap-bar, .smap-enz, .smap-cut, .smap-search { cursor: pointer; }
.smap-bar.is-selected path { stroke: #1e1b4b; stroke-width: 1.6; }
</style>
