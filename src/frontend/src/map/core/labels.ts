/**
 * 标签布局引擎。
 *
 * 环形图外置标签（SnapGene 式左右两列 + 引线）：
 *   1. 按锚点角度分到左/右半边，理想 y = 锚点在标签圆上的投影；
 *   2. 一侧放不下（行数 > 可用高度/行高）时按优先级保留，其余计入 hidden
 *      （界面显示"N 项无法显示"并列出）——同 SnapGene 8 的提示；
 *   3. 同侧标签按理想 y 排序，求"相邻间距 ≥ 行高、总位移平方和最小"的位置：
 *      令 z_i = y_i - i·h 化为单调回归（PAV，pool-adjacent-violators），再夹到
 *      可用范围。结果对任意输入都无重叠（由构造保证，见属性测试）。
 *   思路参考 TeselaGen OVE（MIT）relaxLabelAngles 的"按象限排序、逐个下推"，
 *   这里换成全局最优的 PAV，避免逐个下推时的累积偏移。
 *
 * 线性外置标签：按优先级逐个放入最低的、水平不冲突的行（带间隙），
 * 超过 maxRows 的计入 hidden。
 *
 * 内置标签：文字宽度 + 内边距 ≤ 弧长（或条宽）减箭头时画在元件内部。
 */
import type { FeatureType } from '../types'
import { polar } from './coords'
import { textWidth } from './text'

export interface Box { x: number; y: number; w: number; h: number }

/** 两框是否相交（贴边不算；容差吸收浮点误差） */
export function boxesOverlap(a: Box, b: Box, eps = 1e-6): boolean {
  return a.x < b.x + b.w - eps && b.x < a.x + a.w - eps && a.y < b.y + b.h - eps && b.y < a.y + a.h - eps
}

// ---------------- 优先级 ----------------

const TYPE_RANK: Record<FeatureType, number> = {
  cds: 10, marker: 10, insert: 10, promoter: 9, rep_origin: 9, terminator: 7,
  polyA_signal: 6, enhancer: 6, ltr: 6, mcs: 6, tag: 6, rbs: 5, signal_peptide: 5,
  protein_bind: 4, primer_bind: 4, misc_rna: 4, misc_feature: 2,
}

/** 特征标签优先级：先看类型，同类型长者优先 */
export function featurePriority(type: FeatureType, lengthBp: number): number {
  return (TYPE_RANK[type] ?? 2) * 1e7 + Math.min(lengthBp, 9_999_999)
}

/** 酶标签优先级：单切点排在启动子/CDS 之后、终止子之前，多切点最低 */
export function enzymePriority(cutCount: number): number {
  return (cutCount === 1 ? 8 : cutCount === 2 ? 1.5 : 1) * 1e7
}

// ---------------- 内置标签判定 ----------------

export function fitsInside(text: string, fontSize: number, available: number, pad = 4): boolean {
  return text.length > 0 && textWidth(text, fontSize) + 2 * pad <= available
}

// ---------------- 环形外置标签 ----------------

export interface CircularLabelInput {
  id: string
  text: string
  /** 粗体显示（单切点酶），按粗体量宽 */
  bold?: boolean
  /** 锚点角度（弧度，0 在上，顺时针） */
  angle: number
  /** 锚点半径（引线起点） */
  radius: number
  priority: number
}

export interface PlacedCircularLabel {
  id: string
  text: string
  side: 1 | -1
  /** 文字锚点（右侧 text-anchor=start，左侧 end），y 为文字垂直中心 */
  x: number
  y: number
  box: Box
  /** 引线折线：锚点 → 肘点 → 标签端 */
  leader: [number, number][]
}

export interface CircularLabelOptions {
  cx: number
  cy: number
  /** 标签圆半径（> 最外环） */
  labelRadius: number
  fontSize: number
  /** 行高（≥ 字号），同侧相邻标签中心的最小间距 */
  lineHeight: number
  /** 标签可占的竖直半高（默认 labelRadius + 2 行） */
  halfHeight?: number
  /** 两列距竖直中线的最小水平间距 */
  minX?: number
  /**
   * 标签离理想位置的最大竖直位移。超过时在该拥挤簇里逐个去掉优先级最低的
   * 标签（计入 hidden），避免标签被挤到远处、引线横穿整个图（SnapGene 式
   * 宁缺毋滥）。缺省不限。
   */
  maxShift?: number
}

export interface CircularLabelLayout {
  placed: PlacedCircularLabel[]
  hidden: string[]
}

export function layoutCircularLabels(inputs: CircularLabelInput[], o: CircularLabelOptions): CircularLabelLayout {
  const h = o.lineHeight
  const half = o.halfHeight ?? o.labelRadius + 2 * h
  const minX = o.minX ?? 6
  const top = o.cy - half + h / 2
  const bottom = o.cy + half - h / 2
  const capacity = Math.max(0, Math.floor((bottom - top) / h) + 1)
  const placed: PlacedCircularLabel[] = []
  const hidden: string[] = []
  for (const side of [1, -1] as const) {
    const own = inputs.filter((l) => labelSide(l.angle) === side)
    let keep = own
    if (own.length > capacity) {
      const ranked = [...own].sort((a, b) => b.priority - a.priority || (a.id < b.id ? -1 : 1))
      keep = ranked.slice(0, capacity)
      hidden.push(...ranked.slice(capacity).map((l) => l.id))
    }
    if (!keep.length) continue
    const withIdeal = keep
      .map((l) => ({ l, ideal: o.cy - o.labelRadius * Math.cos(l.angle) }))
      .sort((a, b) => a.ideal - b.ideal || (a.l.id < b.l.id ? -1 : 1))
    const ys = pruneDisplaced(withIdeal, h, top, bottom, o.maxShift ?? Infinity, hidden)
    withIdeal.forEach(({ l }, i) => {
      const y = ys[i]
      const dy = y - o.cy
      const xOff = Math.max(minX, Math.sqrt(Math.max(0, o.labelRadius * o.labelRadius - dy * dy)))
      const x = o.cx + side * xOff
      const w = textWidth(l.text, o.fontSize, l.bold)
      const box: Box = { x: side === 1 ? x : x - w, y: y - h / 2, w, h }
      const anchor = polar(o.cx, o.cy, l.radius, l.angle)
      const elbowR = l.radius + (o.labelRadius - l.radius) * 0.55
      const elbow = polar(o.cx, o.cy, elbowR, l.angle)
      placed.push({ id: l.id, text: l.text, side, x, y, box, leader: [anchor, elbow, [x - side * 2, y]] })
    })
  }
  return { placed, hidden }
}

/**
 * 放置并剔除位移过大的标签：找位移最大且超限的标签，在它所在的"紧贴簇"
 * （相邻间距恰为 h 的连续段）里去掉优先级最低的一个，重排，直到都不超限。
 * 原地修改 items（去掉被剔除项），被剔除 id 追加到 hidden。
 */
function pruneDisplaced(
  items: { l: CircularLabelInput; ideal: number }[], h: number, top: number, bottom: number,
  maxShift: number, hidden: string[],
): number[] {
  for (;;) {
    const ys = spacedPositions(items.map((w) => w.ideal), h, top, bottom)
    if (!Number.isFinite(maxShift)) return ys
    let worst = -1
    let worstD = maxShift
    ys.forEach((y, i) => {
      const d = Math.abs(y - items[i].ideal)
      if (d > worstD + 1e-6) { worst = i; worstD = d }
    })
    if (worst < 0) return ys
    let a = worst
    let b = worst
    while (a > 0 && ys[a] - ys[a - 1] <= h + 1e-6) a--
    while (b < ys.length - 1 && ys[b + 1] - ys[b] <= h + 1e-6) b++
    let drop = a
    for (let i = a + 1; i <= b; i++) {
      const p = items[i].l
      const q = items[drop].l
      if (p.priority < q.priority || (p.priority === q.priority && Math.abs(ys[i] - items[i].ideal) > Math.abs(ys[drop] - items[drop].ideal))) drop = i
    }
    hidden.push(items[drop].l.id)
    items.splice(drop, 1)
  }
}

/** 标签归属：右半边 1、左半边 -1；正上方归右、正下方归左 */
export function labelSide(angle: number): 1 | -1 {
  const s = Math.sin(angle)
  if (Math.abs(s) < 1e-9) return Math.cos(angle) > 0 ? 1 : -1
  return s > 0 ? 1 : -1
}

/**
 * 在 [lo, hi] 内给已排序的理想位置找间距 ≥ h、位移平方和最小的解（PAV）。
 * 调用方保证 n 个点放得下（(n-1)·h ≤ hi - lo）。
 */
export function spacedPositions(ideal: number[], h: number, lo: number, hi: number): number[] {
  const n = ideal.length
  if (!n) return []
  // z_i = y_i - i·h 必须单调不减；对 t_i = ideal_i - i·h 做保序回归
  const blocks: { sum: number; cnt: number }[] = []
  for (let i = 0; i < n; i++) {
    blocks.push({ sum: ideal[i] - i * h, cnt: 1 })
    while (blocks.length > 1) {
      const b = blocks[blocks.length - 1]
      const a = blocks[blocks.length - 2]
      if (a.sum / a.cnt <= b.sum / b.cnt) break
      a.sum += b.sum
      a.cnt += b.cnt
      blocks.pop()
    }
  }
  const zLo = lo
  const zHi = hi - (n - 1) * h
  const out: number[] = []
  let i = 0
  for (const b of blocks) {
    const z = Math.min(Math.max(b.sum / b.cnt, zLo), Math.max(zLo, zHi))
    for (let k = 0; k < b.cnt; k++, i++) out.push(z + i * h)
  }
  return out
}

// ---------------- 线性外置标签 ----------------

export interface LinearLabelInput {
  id: string
  text: string
  /** 粗体显示（单切点酶），按粗体量宽 */
  bold?: boolean
  /** 标签理想中心 x（像素） */
  x: number
  priority: number
}

export interface PlacedLinearLabel { id: string; text: string; row: number; x: number; w: number; cx: number }

export interface LinearLabelLayout { placed: PlacedLinearLabel[]; hidden: string[]; rows: number }

export function layoutLinearLabels(
  inputs: LinearLabelInput[],
  o: { width: number; fontSize: number; maxRows: number; gap?: number; left?: number },
): LinearLabelLayout {
  const gap = o.gap ?? 6
  const left = o.left ?? 0
  const rows: [number, number][][] = []
  const placed: PlacedLinearLabel[] = []
  const hidden: string[] = []
  const order = [...inputs].sort((a, b) => b.priority - a.priority || a.x - b.x || (a.id < b.id ? -1 : 1))
  for (const l of order) {
    const w = textWidth(l.text, o.fontSize)
    if (w > o.width - left) {
      hidden.push(l.id)
      continue
    }
    const x = Math.min(Math.max(left, l.x - w / 2), o.width - w)
    const iv: [number, number] = [x - gap / 2, x + w + gap / 2]
    let row = rows.findIndex((r) => !r.some(([a, b]) => iv[0] < b && a < iv[1]))
    if (row === -1) {
      if (rows.length >= o.maxRows) {
        hidden.push(l.id)
        continue
      }
      rows.push([])
      row = rows.length - 1
    }
    rows[row].push(iv)
    placed.push({ id: l.id, text: l.text, row, x, w, cx: l.x })
  }
  return { placed, hidden, rows: rows.length }
}
