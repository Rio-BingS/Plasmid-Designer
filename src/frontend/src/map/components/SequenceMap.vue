<script setup lang="ts">
/**
 * 序列视图：按行排布碱基（可选互补链）、酶切切口（顶/底链切点 + 折线）与酶名、
 * 特征/引物分道条、选中 CDS 的氨基酸翻译、搜索命中与选区。
 * 只渲染可见行（虚拟滚动），300 kb 也只画几十行。
 * 交互：拖拽选区（可跨行）；点击特征/酶名选中；滚动位置上报给缩略导航。
 */
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useElementSize } from '@vueuse/core'
import type { MapDocument, MapPrimer, Range } from '../types'
import { useMapStore } from '../store'
import { rangeFromBoundaries, splitRange } from '../core/coords'
import { stackLanes } from '../core/lanes'
import { enzymePriority, fitsInside, layoutLinearLabels, type LinearLabelLayout } from '../core/labels'
import { cutStyle, type CutMark } from '../core/enzymes'
import { complement, translateFeature } from '../core/seq'
import { darken, featureColor, textOn } from '../core/palette'
import { textWidth } from '../core/text'
import { PRIMER_FWD, PRIMER_REV, isRevPrimer } from '../core/primer_style'
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

// ---------------- 分道（只特征；引物改画 SnapGene 式蓝框，不占特征道） ----------------
const MAX_LANES = 8
const LANE_H = 14
const ENZ_ROW = 12
const ENZ_MAX_ROWS = 3
const lanes = computed(() => stackLanes(
  store.features.value.map((f) => ({ id: `f:${f.id}`, start: f.start, end: f.end })),
  L.value, { circular: store.circular.value, gap: 2, maxLanes: MAX_LANES }))

// ---------------- SnapGene 式引物框：正链框在序列上方、负链框在下方 ----------------
// 框 = 引物对象（完整序列含 5′ 突出 + 名牌 + 5′ 折角），按用户提供的
// SnapGene 截图复刻；特征行里不再画引物条（第一类标签已按用户要求移除）
const BOX_H = 16
const BOX_MAX_LANES = 6
/** 引物名牌高（悬在框外，行高要为它留位） */
const BADGE_H = 12
const boxedPrimers = computed(() => (store.show.primers ? store.primers.value : []))
// 反义引物判定统一走 core/primer_style（与环形图、导出同一口径）
const fwdPrimers = computed(() => boxedPrimers.value.filter((p) => !isRevPrimer(p)))
const revPrimers = computed(() => boxedPrimers.value.filter((p) => isRevPrimer(p)))
/** 引物框区间：结合区 ± 5′ 突出（正链向左伸、负链向右伸）；跨原点引物不外伸 */
function boxRange(p: MapPrimer, rev = false): Range {
  const full = p.full_sequence || p.sequence || ''
  const spanLen = p.start <= p.end ? p.end - p.start + 1 : L.value - p.start + p.end + 1
  const tail = Math.max(0, full.length - spanLen)
  if (!tail) return { start: p.start, end: p.end }
  return !rev
    ? { start: Math.max(1, p.start - tail), end: p.end }
    : { start: p.start, end: Math.min(L.value, p.end + tail) }
}
const boxLanes = computed(() => ({
  fwd: stackLanes(fwdPrimers.value.map((p) => ({ id: p.id, ...boxRange(p) })), L.value, { circular: store.circular.value, gap: 2, maxLanes: BOX_MAX_LANES }),
  rev: stackLanes(revPrimers.value.map((p) => ({ id: p.id, ...boxRange(p, true) })), L.value, { circular: store.circular.value, gap: 2, maxLanes: BOX_MAX_LANES }),
}))
/** 每行用到的引物框分道（正链/负链各一份位掩码） */
const boxMasks = computed(() => {
  const n = totalRows.value
  const pr = perRow.value
  const mf = new Uint8Array(n)
  const mr = new Uint8Array(n)
  const mark = (m: Uint8Array, r: Range, lane: number) => {
    const parts = store.circular.value ? splitRange(r, L.value) : [{ start: Math.min(r.start, r.end), end: Math.max(r.start, r.end) }]
    for (const p of parts) {
      const b = Math.min(n - 1, rowOf(p.end, pr))
      for (let i = Math.max(0, rowOf(p.start, pr)); i <= b; i++) m[i] |= 1 << lane
    }
  }
  for (const p of fwdPrimers.value) {
    const lane = boxLanes.value.fwd.lanes.get(p.id)
    if (lane !== undefined) mark(mf, boxRange(p), lane)
  }
  for (const p of revPrimers.value) {
    const lane = boxLanes.value.rev.lanes.get(p.id)
    if (lane !== undefined) mark(mr, boxRange(p, true), lane)
  }
  return { mf, mr }
})

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
      cs.map((c) => ({
        id: `e:${c.id}`, text: c.name, bold: cutStyle(c).weight > 400, x: colX(c.cut - row * pr),
        // 克隆酶标签与单切点同档优先级：多切点克隆酶曾被拥挤规则最先藏掉
        priority: store.cloneEnzymes.value.has(c.name) ? enzymePriority(1) : enzymePriority(c.enzyme.cut_count),
      })),
      { width: W.value, fontSize: 10, maxRows: ENZ_MAX_ROWS, gap: 6, left: gutter.value - 4, maxShift: 6 * CHAR_W },
    ))
  }
  return { by, layouts }
})

// ---------------- 行内纵向布局（每行按自身内容定高） ----------------
interface RowGeo {
  enzTop: number; enzRows: number; fwdTop: number; fwdMask: number
  seqY: number; compY: number; rulerY: number; laneTop: number; transY: number; revTop: number; revMask: number
  rowH: number; mask: number
}
function rowGeo(row: number): RowGeo {
  const enzRows = cutRows.value.layouts.get(row)?.rows ?? 0
  const mask = rowLaneMask.value[row] ?? 0
  const fwdMask = boxMasks.value.mf[row] ?? 0
  const revMask = boxMasks.value.mr[row] ?? 0
  let y = 4
  const enzTop = y
  y += enzRows * ENZ_ROW + (enzRows ? 2 : 0)
  // 正链引物框在酶名与序列之间；名牌悬在框上方，预留名牌高度 + 间隙
  const fwdTop = y + (fwdMask ? BADGE_H + 1 : 0)
  y += popcount(fwdMask) * BOX_H + (fwdMask ? BADGE_H + 2 : 0)
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
  // 负链引物框垫在最底下（SnapGene：反义引物在序列区最下方）；名牌悬在框下方
  const revTop = y + 2
  y += popcount(revMask) * BOX_H + (revMask ? BADGE_H + 4 : 0)
  return { enzTop, enzRows, fwdTop, fwdMask, seqY, compY, rulerY, laneTop, transY, revTop, revMask, rowH: y + 8, mask }
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

/** 区间与第 row 行相交的像素段（附行内列号，供引物框逐列对齐文字） */
function boxSegs(r: Range, row: number): { x1: number; x2: number; c1: number; c2: number }[] {
  const rs = row * perRow.value + 1
  const re = Math.min(L.value, rs + perRow.value - 1)
  const out: { x1: number; x2: number; c1: number; c2: number }[] = []
  const parts = store.circular.value ? splitRange(r, L.value) : [{ start: Math.min(r.start, r.end), end: Math.max(r.start, r.end) }]
  for (const p of parts) {
    const a = Math.max(p.start, rs)
    const b = Math.min(p.end, re)
    if (a > b) continue
    out.push({ x1: colX(a - rs), x2: colX(b - rs + 1), c1: a, c2: b })
  }
  return out
}

/** 区间 r 是否与 [lo, hi]（1-based 闭区间）相交 */
function touchesWindow(r: Range, lo: number, hi: number): boolean {
  const parts = store.circular.value ? splitRange(r, L.value) : [{ start: Math.min(r.start, r.end), end: Math.max(r.start, r.end) }]
  return parts.some((p) => p.start <= hi && p.end >= lo)
}
/** 可见窗口内的特征 / 引物：每次滚动只全量筛一遍，而不是每行各扫一遍 */
const visibleItems = computed(() => {
  const [a, b] = rowRange.value
  const lo = a * perRow.value + 1
  const hi = Math.min(L.value, b * perRow.value)
  const features = store.features.value.filter((f) => (f.segments?.length ? f.segments : [f]).some((r) => touchesWindow(r, lo, hi)))
  const primers = store.primers.value.filter((p) => touchesWindow(p, lo, hi))
  return { features, primers }
})
/** 搜索命中按行分桶（最多 2000 条），每行只取自己的命中 */
const hitsByRow = computed(() => {
  const by = new Map<number, number[]>()
  const pr = perRow.value
  store.search.hits.forEach((h, i) => {
    const parts = store.circular.value ? splitRange(h, L.value) : [{ start: Math.min(h.start, h.end), end: Math.max(h.start, h.end) }]
    for (const p of parts) {
      for (let row = Math.floor((p.start - 1) / pr); row <= Math.floor((p.end - 1) / pr); row++) {
        const arr = by.get(row)
        if (arr) { if (arr[arr.length - 1] !== i) arr.push(i) } else by.set(row, [i])
      }
    }
  })
  return by
})

/** 每行的静态内容（序列、特征条、酶切、翻译）：不依赖悬停 / 选区 / 搜索，指针移动不重算 */
const rows = computed(() => {
  const [a, b] = rowRange.value
  const { features: visFeatures, primers: visPrimers } = visibleItems.value
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
    for (const f of visFeatures) {
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
    // 引物不再画在特征行里（第一类标签已移除），只画 SnapGene 式引物框（见 pboxes）
    // 引物框：正向框在序列上方、反向框在最下方；框内逐列对齐印引物序列，
    // 名牌悬在框外、5′ 端一个折角、3′ 端一个延伸箭头（模块 B）
    interface PBox {
      key: string; id: string; name: string; x1: number; x2: number; y: number; chars: string
      badge: { x: number; y: number; w: number } | null
      fold: string
      /** 框体一体轮廓（3′ 端收成箭头尖，跨行非 3′ 段平收） */
      outline: string
      /** 框描边/文字色（正/反向分色） */
      color: string
      dark: string
    }
    const pboxes: PBox[] = []
    for (const p of visPrimers) {
      const fwd = !isRevPrimer(p)
      const lane = boxLanes.value[fwd ? 'fwd' : 'rev'].lanes.get(p.id)
      if (lane === undefined) continue
      const r = boxRange(p, !fwd)
      const full = (p.full_sequence || p.sequence || '').toUpperCase()
      if (!full) continue
      const boxText = fwd ? full : full.split('').reverse().join('')
      const boxY = fwd ? g.fwdTop + compactLane(g.fwdMask, lane) * BOX_H : g.revTop + compactLane(g.revMask, lane) * BOX_H
      const segs = boxSegs(r, row)
      if (!segs.length) continue
      const end5 = fwd ? r.start : r.end
      // 3′ 端：正向在框右端、反向在框左端
      const end3 = fwd ? r.end : r.start
      const foldSegIdx = Math.max(0, segs.findIndex((s) => s.c1 <= end5 && end5 <= s.c2))
      const tipSegIdx = Math.max(0, segs.findIndex((s) => s.c1 <= end3 && end3 <= s.c2))
      const yMid = boxY + (BOX_H - 3) / 2
      const pal = fwd ? PRIMER_FWD : PRIMER_REV
      segs.forEach((s, si) => {
        const chars = boxText.slice(s.c1 - r.start, s.c2 - r.start + 1)
        if (!chars) return
        // 名牌悬在框外（正向在框上方、反向在框下方），锚在 5′ 端折角旁；
        // 名牌文字带方向符号（正向 `名字 →`、反向 `← 名字`），冗余编码颜色
        const badgeText = fwd ? `${p.name} →` : `← ${p.name}`
        const badgeW = textWidth(badgeText, 9, true) + 8
        const bx = Math.min(foldSegIdx === si ? s.x1 : (s.x1 + s.x2) / 2 - badgeW / 2, W.value - badgeW - 4)
        // SnapGene 式一体轮廓：圆角矩形在 3′ 端直接收成箭头尖，描边连续。
        // 曾用「圆角矩形 + 框外贴小三角」，接缝生硬像掉出来的碎片（用户截图指正）。
        // 跨行分段时只有含 3′ 端的段带箭头，其余段平收
        const yTop = boxY
        const yBot = boxY + BOX_H - 3
        const flatClose = (l: number, r: number) =>
          `M${l + 2} ${yTop}H${r - 2}Q${r} ${yTop} ${r} ${yTop + 2}V${yBot - 2}Q${r} ${yBot} ${r - 2} ${yBot}H${l}Z`
        const arrowClose = (l: number, r: number, dir: 1 | -1) =>
          dir === 1
            ? `M${l + 2} ${yTop}H${r - 1}L${r + 6} ${yMid}L${r - 1} ${yBot}H${l + 2}`
              + `Q${l} ${yBot} ${l} ${yBot - 2}V${yTop + 2}Q${l} ${yTop} ${l + 2} ${yTop}Z`
            : `M${r - 2} ${yTop}H${l + 1}L${l - 6} ${yMid}L${l + 1} ${yBot}H${r - 2}`
              + `Q${r} ${yBot} ${r} ${yBot - 2}V${yTop + 2}Q${r} ${yTop} ${r - 2} ${yTop}Z`
        pboxes.push({
            key: `p:${p.id}`, id: `p:${p.id}`, name: badgeText,
            x1: s.x1, x2: s.x2, y: boxY, chars,
            color: pal.base, dark: pal.dark,
            badge: si === foldSegIdx
              ? { x: Math.max(gutter.value - 2, bx), y: fwd ? boxY - BADGE_H - 1 : boxY + BOX_H + 1, w: badgeW }
              : null,
            fold: si === foldSegIdx
              ? (fwd
                  ? `M${s.x1} ${yMid - 4}V${yMid + 4}`
                  : `M${s.x2} ${yMid - 4}V${yMid + 4}`)
              : '',
            outline: si === tipSegIdx
              ? (fwd ? arrowClose(s.x1 - 1, s.x2 + 1, 1) : arrowClose(s.x1 - 1, s.x2 + 1, -1))
              : (fwd ? flatClose(s.x1 - 1, s.x2 + 1) : flatClose(s.x1 - 1, s.x2 + 1)),
          })
      })
    }
    const cuts = (cutRows.value.by.get(row) ?? []).map((c) => {
      const st = cutStyle(c)
      const isClone = store.cloneEnzymes.value.has(c.name)
      const xt = colX(c.cut - (rs - 1))
      const bIn = c.cutBottom >= rs - 1 && c.cutBottom <= re
      const xb = bIn ? colX(c.cutBottom - (rs - 1)) : xt
      return { key: `e:${c.id}`, name: c.name, xt, xb, color: isClone ? '#ea580c' : st.color, weight: isClone ? 1.8 : st.weight, dash: st.dash, blocked: st.blocked }
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
    const aa = translation.value?.byRow.get(row) ?? []
    out.push({ row, top: off[row], g, rs, re, n, text, comp: store.show.complement ? complement(text) : '', bars, pboxes, cuts, enzLabels, aa })
  }
  return out
})

type Seg = RowSeg & { key?: string; current?: boolean }
interface Overlay { sel: Seg[]; caret: number | null }
const NO_SEGS: Seg[] = []
/** 选区 / 光标叠加层（只算可见行） */
const selRows = computed(() => {
  const [a, b] = rowRange.value
  const out = new Map<number, Overlay>()
  const s = store.selection.value
  const c = store.caret.value
  for (let row = a; row < b; row++) {
    const rs = row * perRow.value + 1
    const re = Math.min(L.value, rs + perRow.value - 1)
    const sel = s ? rowSegs(s, row) : NO_SEGS
    const caret = !s && c !== null && c >= rs - 1 && c <= re ? colX(c - (rs - 1)) : null
    if (sel.length || caret !== null) out.set(row, { sel, caret })
  }
  return out
})
/** 搜索命中叠加层（按行分桶后只取可见行） */
const hitRows = computed(() => {
  const [a, b] = rowRange.value
  const out = new Map<number, Seg[]>()
  for (let row = a; row < b; row++) {
    const idx = hitsByRow.value.get(row)
    if (!idx) continue
    out.set(row, idx.flatMap((i) => rowSegs(store.search.hits[i], row).map((sg) => ({ ...sg, key: `s:${i}`, current: i === store.search.index }))))
  }
  return out
})
/** 悬停底色（只随悬停元件变化） */
const hoverRows = computed(() => {
  const h = store.hoverTarget.value
  const out = new Map<number, Seg[]>()
  if (!h?.range || h.id.startsWith('t:')) return out
  const [a, b] = rowRange.value
  for (let row = a; row < b; row++) {
    const segs = rowSegs(h.range, row)
    if (segs.length) out.set(row, segs)
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

defineExpose({ scroller, perRow, offsets, totalHeight, rows })
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
          <rect v-for="(s, i) in hoverRows.get(r.row) ?? NO_SEGS" :key="'h' + i" :x="s.x1" :y="r.g.seqY" :width="s.x2 - s.x1" :height="r.g.rulerY - r.g.seqY" fill="#f59e0b" fill-opacity="0.15" />
          <rect v-for="(s, i) in hitRows.get(r.row) ?? NO_SEGS" :key="'s' + i" :data-hit="s.key" :x="s.x1" :y="r.g.seqY" :width="s.x2 - s.x1" :height="r.g.rulerY - r.g.seqY"
                :fill="s.current ? '#f97316' : '#facc15'" fill-opacity="0.45" class="smap-search" />
          <rect v-for="(s, i) in selRows.get(r.row)?.sel ?? NO_SEGS" :key="'sel' + i" :x="s.x1" :y="r.g.seqY" :width="s.x2 - s.x1" :height="r.g.rulerY - r.g.seqY + 2"
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

          <!-- SnapGene 式引物框：正链框在序列上方、负链框在最下方 -->
          <g v-for="b in r.pboxes" :key="'pb' + b.key + b.x1" :data-hit="b.id" class="smap-pbox">
            <path :d="b.outline" fill="#ffffff" fill-opacity="0.8" :stroke="b.color" stroke-width="1" stroke-linejoin="round" />
            <text :x="b.x1" :y="b.y + 10" :font-size="FONT - 2" font-family="Consolas, Menlo, 'Courier New', monospace"
                  :textLength="b.x2 - b.x1" lengthAdjust="spacing" :fill="b.dark" pointer-events="none"
                  style="letter-spacing: -0.02em">{{ b.chars }}</text>
            <!-- 5′ 端竖直短线起头记号 -->
            <path v-if="b.fold" :d="b.fold" :stroke="b.color" stroke-width="1.5" pointer-events="none" />
            <g v-if="b.badge">
              <!-- 名牌到框沿的细引导线（模块 A）：名牌悬在框外，用 1px 线连回 5′ 端 -->
              <line :x1="b.badge.x + (b.badge.w > 40 ? b.badge.w / 2 : 2)" :x2="b.badge.x + (b.badge.w > 40 ? b.badge.w / 2 : 2)"
                    :y1="b.badge.y + (b.badge.y < b.y ? 12 : 0)" :y2="b.badge.y < b.y ? b.y : b.y + BOX_H - 3"
                    :stroke="b.color" stroke-width="0.8" stroke-opacity="0.55" />
              <rect :x="b.badge.x" :y="b.badge.y" :width="b.badge.w" height="12" rx="3"
                    :fill="b.color" :stroke="b.dark" stroke-width="0.8" />
              <text :x="b.badge.x + b.badge.w / 2" :y="b.badge.y + 9" text-anchor="middle" font-size="9"
                    font-weight="600" fill="#ffffff" pointer-events="none" style="letter-spacing: -0.01em">{{ b.name }}</text>
            </g>
          </g>

          <!-- 选中 CDS 的翻译 -->
          <text v-for="a in r.aa" :key="'aa' + a.i" :x="gutter + (a.col + 0.5) * CHAR_W" :y="r.g.transY + 11" text-anchor="middle"
                font-size="11" font-family="Consolas, Menlo, monospace" :fill="a.aa === '*' ? '#dc2626' : '#7c3aed'" class="smap-aa">{{ a.aa }}</text>

          <line v-if="selRows.get(r.row)?.caret != null" :x1="selRows.get(r.row)!.caret!" :x2="selRows.get(r.row)!.caret!" :y1="r.g.seqY - 1" :y2="r.g.rulerY + 2" stroke="#4f46e5" stroke-width="1.5" class="smap-caret" />
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
.smap-bar, .smap-enz, .smap-cut, .smap-search, .smap-pbox { cursor: pointer; }
.smap-bar.is-selected path { stroke: #1e1b4b; stroke-width: 1.6; }
</style>
