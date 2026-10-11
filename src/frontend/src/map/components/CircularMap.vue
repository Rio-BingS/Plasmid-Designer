<script lang="ts">
let instanceCounter = 0
</script>

<script setup lang="ts">
/**
 * 环形图谱（SVG）：主链 + 刻度、特征分道箭头（内置/外置标签）、ORF/引物环、
 * 酶切刻度与标签、搜索命中、选区扇形与光标。
 * 交互：在环带上拖拽 = 选区（可跨原点）；拖拽中心区 / Shift+拖拽 = 旋转；
 * Ctrl+滚轮 = 以指针为中心缩放；Alt/Shift+滚轮 = 旋转；放大后拖拽外围空白 = 平移。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useElementSize } from '@vueuse/core'
import type { MapDocument, Range } from '../types'
import { useMapStore } from '../store'
import {
  TAU, angleToX, arcBandPath, arcPath, pointAngle, polar, rangeFromBoundaries, rangeLength,
  unroll, unwrapNear, xToAngle,
} from '../core/coords'
import { stackLanes } from '../core/lanes'
import {
  enzymePriority, featurePriority, fitsInside, layoutCircularLabels, type CircularLabelInput,
} from '../core/labels'
import { enzymeStyle } from '../core/enzymes'
import { darken, featureColor, textOn } from '../core/palette'
import { formatBp, ticks } from '../core/ticks'
import { textWidth, truncateText } from '../core/text'
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

const S = computed(() => Math.round(props.size ?? Math.min(props.maxSize, Math.max(300, wrapW.value || 560))))
const cx = computed(() => S.value / 2)
const cy = computed(() => S.value / 2)
const FONT = 11
const labelMargin = computed(() => Math.min(175, Math.max(72, S.value * 0.26)))
const labelR = computed(() => S.value / 2 - labelMargin.value)
const R0 = computed(() => labelR.value - 26)
const laneH = computed(() => Math.min(14, Math.max(9, S.value * 0.02)))
const laneGap = 2
const L = computed(() => store.L.value)

// ---------------- 缩放 / 旋转 ----------------
const zoom = ref(1)
const pan = ref({ x: 0, y: 0 })
const viewBox = computed(() => {
  const w = S.value / zoom.value
  const x = cx.value + pan.value.x - w / 2
  const y = cy.value + pan.value.y - w / 2
  return `${r2(x)} ${r2(y)} ${r2(w)} ${r2(w)}`
})
function r2(n: number) { return Math.round(n * 100) / 100 }
function setZoom(z: number, focus?: [number, number]) {
  const nz = Math.min(20, Math.max(1, z))
  if (focus) {
    // 保持指针下的点不动：c' = P - (P - c)·z/z'
    const c = [cx.value + pan.value.x, cy.value + pan.value.y]
    const k = zoom.value / nz
    pan.value = { x: focus[0] - (focus[0] - c[0]) * k - cx.value, y: focus[1] - (focus[1] - c[1]) * k - cy.value }
  }
  zoom.value = nz
  if (nz === 1) pan.value = { x: 0, y: 0 }
}
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
  { circular: true, gap: Math.ceil((L.value * 3) / (TAU * R0.value)), maxLanes: maxLanes.value },
))
function laneRadii(lane: number): [number, number] {
  const outer = R0.value - 6 - lane * (laneH.value + laneGap)
  return [outer - laneH.value, outer]
}
const featInner = computed(() => laneRadii(Math.max(0, featLanes.value.laneCount - 1))[0])

interface FeatGeom {
  id: string; key: string; name: string; color: string; stroke: string; paths: string[]; connector: string | null
  r1: number; r2: number; mid: number; internal: { path: string; text: string; color: string; size: number } | null
}
const featGeoms = computed<FeatGeom[]>(() => {
  const out: FeatGeom[] = []
  for (const f of store.features.value) {
    const lane = featLanes.value.lanes.get(f.id)
    if (lane === undefined) continue
    const [r1, rr2] = laneRadii(lane)
    const rm = (r1 + rr2) / 2
    const color = featureColor(f)
    const headA = (laneH.value * 0.8) / rm
    const segs: Range[] = f.segments?.length ? f.segments : [{ start: f.start, end: f.end }]
    // 箭头只画在 3' 端所在段
    const headSeg = f.strand === -1 ? 0 : segs.length - 1
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
    const size = Math.max(8, laneH.value - 3)
    const arcLen = (a2 - a1) * rm - laneH.value * 0.8
    if (store.show.labels && fitsInside(f.name, size, arcLen)) {
      // 下半圈文字反向走，避免倒置
      const m = ((mid % TAU) + TAU) % TAU
      const flip = m > Math.PI / 2 && m < (3 * Math.PI) / 2
      const rText = flip ? rm + size * 0.35 : rm - size * 0.35
      internal = { path: arcPath(cx.value, cy.value, rText, a1, a2, flip), text: f.name, color: textOn(color), size }
    }
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
    const r1 = R0.value + 13 + lane * 6
    const u = unroll(p, L.value)
    return [{ id: p.id, name: p.name, key: `p:${p.id}`, mid: (ang(u.from) + ang(u.to)) / 2, r: r1 + 4, d: arcBandPath(cx.value, cy.value, r1, r1 + 4, ang(u.from), ang(Math.max(u.to, u.from + L.value / 1000)), p.strand === 1 ? 1 : -1, 5 / r1) }]
  })
})

// ---------------- 酶切 ----------------
const cutGeoms = computed(() => store.cuts.value.map((c) => {
  const st = enzymeStyle(c.enzyme)
  const a = ang(c.cut)
  const [x1, y1] = polar(cx.value, cy.value, R0.value, a)
  const [x2, y2] = polar(cx.value, cy.value, R0.value + 10, a)
  return { ...c, key: `e:${c.id}`, a, x1, y1, x2, y2, color: st.color, weight: st.weight }
}))

// ---------------- 外置标签 ----------------
const labelLayout = computed(() => {
  if (!store.show.labels) return { placed: [], hidden: [] as string[] }
  const maxW = labelMargin.value - 14
  const inputs: CircularLabelInput[] = []
  for (const g of featGeoms.value) {
    if (g.internal) continue
    const f = store.doc.value.features.find((x) => x.id === g.id)!
    inputs.push({ id: g.key, text: truncateText(g.name, maxW, FONT), angle: g.mid, radius: g.r2, priority: featurePriority(f.type, rangeLength(f, L.value)) })
  }
  for (const c of cutGeoms.value) {
    inputs.push({ id: c.key, text: c.name, angle: c.a, radius: R0.value + 10, priority: enzymePriority(c.enzyme.cut_count) })
  }
  for (const p of primerGeoms.value) {
    inputs.push({ id: p.key, text: truncateText(p.name, maxW, FONT), angle: p.mid, radius: p.r, priority: 5e7 })
  }
  return layoutCircularLabels(inputs.filter((i) => i.text), {
    cx: cx.value, cy: cy.value, labelRadius: labelR.value, fontSize: FONT, lineHeight: FONT + 3, halfHeight: S.value / 2 - 6,
  })
})
const labelStyle = computed(() => {
  const m = new Map<string, { color: string; weight: number }>()
  for (const c of cutGeoms.value) m.set(c.key, { color: c.color, weight: c.weight })
  return m
})

// 无法显示项上报（标签放不下 + 超出分道上限）
watch([labelLayout, featLanes], () => {
  const items = labelLayout.value.hidden.map((k) => ({ id: k, name: resolveHit(store, k)?.title ?? k, reason: '标签空间不足' }))
  for (const id of featLanes.value.overflow) {
    const f = store.doc.value.features.find((x) => x.id === id)
    items.push({ id: `f:${id}`, name: f?.name ?? id, reason: '特征分道已满' })
  }
  store.setUnshown('circular', items)
}, { immediate: true })
onBeforeUnmount(() => store.setUnshown('circular', []))

// ---------------- 刻度 ----------------
const tickGeoms = computed(() => {
  const target = Math.max(6, Math.round(S.value / 70))
  const t = ticks(1, L.value, target)
  const rNum = Math.max(20, Math.min(orfInner.value, featInner.value) - 12)
  return t.positions.filter((p) => p < L.value - t.step * 0.3).map((p) => {
    const a = ang(p)
    const [x1, y1] = polar(cx.value, cy.value, R0.value - 1, a)
    const [x2, y2] = polar(cx.value, cy.value, R0.value - 5, a)
    const [tx, ty] = polar(cx.value, cy.value, rNum, a)
    return { p, x1, y1, x2, y2, tx, ty, label: formatBp(p) }
  })
})
const originMark = computed(() => {
  const a = ang(0)
  const [x1, y1] = polar(cx.value, cy.value, R0.value - 6, a)
  const [x2, y2] = polar(cx.value, cy.value, R0.value + 6, a)
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
const hoverKey = computed(() => store.hover.value?.id ?? null)
const hoverPath = computed(() => {
  const h = store.hover.value
  if (!h?.range || h.id.startsWith('f:')) return ''
  const u = unroll(h.range, L.value)
  return arcBandPath(cx.value, cy.value, selInner.value, R0.value + 12, ang(u.from), ang(Math.max(u.to, u.from + L.value / 1000)))
})

// ---------------- 指针交互 ----------------
type Mode = 'none' | 'select' | 'rotate' | 'pan'
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
    pt.x = e.clientX
    pt.y = e.clientY
    const p = pt.matrixTransform(ctm.inverse())
    return [p.x, p.y]
  }
  const rect = svg?.getBoundingClientRect()
  if (!rect || !rect.width) return [cx.value, cy.value - R0.value]
  const w = S.value / zoom.value
  return [cx.value + pan.value.x - w / 2 + ((e.clientX - rect.left) / rect.width) * w,
    cy.value + pan.value.y - w / 2 + ((e.clientY - rect.top) / rect.height) * w]
}

function onPointerDown(e: PointerEvent) {
  if (!props.interactive || e.button > 0) return
  const [x, y] = svgPoint(e)
  const r = Math.hypot(x - cx.value, y - cy.value)
  const a = pointAngle(cx.value, cy.value, x, y)
  moved = false
  downKey = hitKeyOf(e.target)
  if (e.shiftKey || (!downKey && r < selInner.value - 6)) {
    mode = 'rotate'
    startAngle = a
    startRot = store.rotation.value
  } else if (!downKey && r > labelR.value + 4 && zoom.value > 1) {
    mode = 'pan'
    startPan = { x: pan.value.x, y: pan.value.y, px: e.clientX, py: e.clientY }
  } else {
    mode = 'select'
    anchorX = angleToX(a, L.value, store.rotation.value)
    prevX = anchorX
  }
  ;(e.currentTarget as Element | null)?.setPointerCapture?.(e.pointerId)
}

function onPointerMove(e: PointerEvent) {
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
    const k = S.value / zoom.value / Math.max(1, svgEl.value?.getBoundingClientRect().width || S.value)
    pan.value = { x: startPan.x - (e.clientX - startPan.px) * k, y: startPan.y - (e.clientY - startPan.py) * k }
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
  if (mode === 'select' && !moved) {
    const info = resolveHit(store, downKey ?? hitKeyOf(e.target))
    if (info) info.select()
    else store.setCaret(Math.round(anchorX) % L.value)
  }
  mode = 'none'
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

const centerName = computed(() => truncateText(store.doc.value.name, Math.max(60, selInner.value * 1.6), 15))
const nameFits = computed(() => textWidth(centerName.value, 15) < selInner.value * 2)

defineExpose({ svgEl, resetView, setZoom, zoom })
</script>

<template>
  <div ref="wrap" class="cmap">
    <svg
      ref="svgEl" class="cmap-svg" data-map-export="circular" xmlns="http://www.w3.org/2000/svg"
      :viewBox="viewBox" :width="S" :height="S" role="img" :aria-label="`${store.doc.value.name} 环形图谱`"
      font-family="Inter, -apple-system, 'Segoe UI', Roboto, sans-serif"
      @pointerdown="onPointerDown" @pointermove="onPointerMove" @pointerup="onPointerUp"
      @pointerleave="onLeave" @wheel="onWheel"
    >
      <!-- 选区与悬停底色（画在最下层） -->
      <path v-if="hoverPath" :d="hoverPath" fill="#f59e0b" fill-opacity="0.12" class="cmap-hover" />
      <path v-if="selectionPath" :d="selectionPath" fill="#4f46e5" fill-opacity="0.16" stroke="#4f46e5" stroke-opacity="0.5" class="cmap-selection" />

      <!-- 主链：SnapGene 式双线 -->
      <circle :cx="cx" :cy="cy" :r="R0" fill="none" stroke="#3a3f45" stroke-width="1.4" />
      <circle :cx="cx" :cy="cy" :r="R0 - 3" fill="none" stroke="#3a3f45" stroke-width="0.7" />
      <line :x1="originMark.x1" :y1="originMark.y1" :x2="originMark.x2" :y2="originMark.y2" stroke="#3a3f45" stroke-width="1.6" />
      <g class="cmap-ticks">
        <g v-for="t in tickGeoms" :key="t.p">
          <line :x1="t.x1" :y1="t.y1" :x2="t.x2" :y2="t.y2" stroke="#6b7280" stroke-width="1" />
          <text :x="t.tx" :y="t.ty" font-size="9.5" fill="#6b7280" text-anchor="middle" dominant-baseline="central">{{ t.label }}</text>
        </g>
      </g>

      <!-- 搜索命中 -->
      <path v-for="h in searchGeoms" :key="h.key" :d="h.d" :data-hit="h.key"
            :fill="h.current ? '#f97316' : '#facc15'" class="cmap-search" />

      <!-- 特征 -->
      <g v-for="g in featGeoms" :key="g.key" :data-hit="g.key"
         :class="['cmap-feat', { 'is-hover': hoverKey === g.key, 'is-selected': store.selectedFeatureId.value === g.id }]">
        <path v-if="g.connector" :d="g.connector" fill="none" :stroke="g.stroke" stroke-width="1" stroke-dasharray="2 2" />
        <path v-for="(d, i) in g.paths" :key="i" :d="d" :fill="g.color" :stroke="g.stroke"
              :stroke-width="hoverKey === g.key || store.selectedFeatureId.value === g.id ? 2 : 0.8" />
        <template v-if="g.internal">
          <path :id="`${uid}-tp-${g.id}`" :d="g.internal.path" fill="none" stroke="none" />
          <text :font-size="g.internal.size" :fill="g.internal.color" class="cmap-int-label" pointer-events="none">
            <textPath :href="`#${uid}-tp-${g.id}`" startOffset="50%" text-anchor="middle" dominant-baseline="central">{{ g.internal.text }}</textPath>
          </text>
        </template>
      </g>

      <!-- ORF / 引物 -->
      <path v-for="o in orfGeoms" :key="o.key" :d="o.d" :data-hit="o.key" :fill="o.color" class="cmap-orf" />
      <path v-for="p in primerGeoms" :key="p.key" :d="p.d" :data-hit="p.key" fill="#5aa0e0" stroke="#2f6aa8" stroke-width="0.6" class="cmap-primer" />

      <!-- 酶切刻度 -->
      <line v-for="c in cutGeoms" :key="c.key" :data-hit="c.key" :x1="c.x1" :y1="c.y1" :x2="c.x2" :y2="c.y2"
            :stroke="c.color" :stroke-width="c.weight > 400 ? 1.6 : 1" class="cmap-cut" />

      <!-- 光标 -->
      <line v-if="caretLine" :x1="caretLine.x1" :y1="caretLine.y1" :x2="caretLine.x2" :y2="caretLine.y2"
            stroke="#4f46e5" stroke-width="1.5" class="cmap-caret" />

      <!-- 外置标签 + 引线 -->
      <g v-for="l in labelLayout.placed" :key="l.id" :data-hit="l.id"
         :class="['cmap-label', { 'is-hover': hoverKey === l.id }]">
        <polyline :points="l.leader.map((p) => p.map(r2).join(',')).join(' ')" fill="none"
                  :stroke="hoverKey === l.id ? '#4f46e5' : '#a5a9ae'" stroke-width="0.8" />
        <text :x="l.x" :y="l.y" :font-size="FONT" dominant-baseline="central"
              :text-anchor="l.side === 1 ? 'start' : 'end'"
              :fill="labelStyle.get(l.id)?.color ?? '#1f2937'"
              :font-weight="labelStyle.get(l.id)?.weight ?? 400">{{ l.text }}</text>
      </g>

      <!-- 中心名称与长度 -->
      <text v-if="nameFits" :x="cx" :y="cy - 6" text-anchor="middle" font-size="15" font-weight="700" fill="#1f2937" class="cmap-name">{{ centerName }}</text>
      <text :x="cx" :y="cy + (nameFits ? 14 : 4)" text-anchor="middle" font-size="12" fill="#6b7280" class="cmap-length">{{ L.toLocaleString('en-US') }} bp</text>
    </svg>

    <div v-if="interactive" class="cmap-controls">
      <button type="button" title="缩小" @click="setZoom(zoom / 1.4)">−</button>
      <button type="button" title="放大（Ctrl+滚轮）" @click="setZoom(zoom * 1.4)">+</button>
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
.cmap-svg { display: block; max-width: 100%; height: auto; touch-action: none; user-select: none; cursor: crosshair; }
.cmap-feat, .cmap-cut, .cmap-orf, .cmap-primer, .cmap-label, .cmap-search { cursor: pointer; }
.cmap-feat:hover path { filter: brightness(1.06); }
.cmap-label:hover text { text-decoration: underline; }
.cmap-controls { display: flex; gap: 6px; align-items: center; font-size: 12px; color: var(--text-secondary); }
.cmap-controls button {
  min-width: 28px; padding: 2px 8px; border: 1px solid var(--border-color); border-radius: 4px;
  background: var(--bg-primary); color: var(--text-primary);
}
.cmap-rot { display: inline-flex; align-items: center; gap: 4px; }
.cmap-rot input { width: 110px; }
</style>
