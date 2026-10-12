/**
 * 坐标数学（纯函数，无 DOM）。
 *
 * 两套坐标：
 * - 碱基位置 pos：1-based，闭区间 Range{start,end}；环状上 start > end 表示跨原点。
 * - 边界坐标 x：0..L 的实数，碱基 p 占据 [p-1, p)。区间 [start,end] 对应
 *   [start-1, end]，跨原点时展开为 [start-1, end+L]（"unrolled"）。
 * 角度：弧度，0 在 12 点方向，顺时针为正（与 SnapGene 一致）。
 */
import type { Range } from '../types'

export const TAU = Math.PI * 2

/** 任意整数位置折回到 1..L */
export function normPos(p: number, L: number): number {
  if (L <= 0) return p
  return ((((p - 1) % L) + L) % L) + 1
}

export function isWrap(r: Range): boolean {
  return r.start > r.end
}

/** 区间长度（bp）；线性分子上 start > end 视为非法，返回 0 */
export function rangeLength(r: Range, L: number, circular = true): number {
  if (r.end >= r.start) return r.end - r.start + 1
  return circular ? L - r.start + 1 + r.end : 0
}

/** 跨原点区间拆成不跨原点的段（按序列方向：先尾段后首段） */
export function splitRange(r: Range, L: number): Range[] {
  if (!isWrap(r)) return [{ start: r.start, end: r.end }]
  return [{ start: r.start, end: L }, { start: 1, end: r.end }]
}

/** 区间展开为边界坐标 [from, to]，to 可能 > L（跨原点） */
export function unroll(r: Range, L: number): { from: number; to: number } {
  return { from: r.start - 1, to: isWrap(r) ? r.end + L : r.end }
}

export function rangeContains(r: Range, pos: number, L: number): boolean {
  const p = normPos(pos, L)
  return isWrap(r) ? p >= r.start || p <= r.end : p >= r.start && p <= r.end
}

/** 两区间是否共享至少一个碱基（跨原点感知） */
export function rangesOverlap(a: Range, b: Range, L: number): boolean {
  for (const s1 of splitRange(a, L)) {
    for (const s2 of splitRange(b, L)) {
      if (s1.start <= s2.end && s2.start <= s1.end) return true
    }
  }
  return false
}

/** 区间中点（边界坐标，0..L） */
export function rangeMid(r: Range, L: number): number {
  const u = unroll(r, L)
  const m = (u.from + u.to) / 2
  return m >= L ? m - L : m
}

/** 边界坐标 → 角度（rotation 为弧度，正值使原点顺时针转动） */
export function xToAngle(x: number, L: number, rotation = 0): number {
  return (x / L) * TAU + rotation
}

/** 角度 → 边界坐标（0..L） */
export function angleToX(a: number, L: number, rotation = 0): number {
  let t = (a - rotation) / TAU
  t -= Math.floor(t)
  return t * L
}

/** 极坐标 → 屏幕坐标（0 在上方，顺时针） */
export function polar(cx: number, cy: number, r: number, a: number): [number, number] {
  return [cx + r * Math.sin(a), cy - r * Math.cos(a)]
}

/** 屏幕点 → 角度（0..2π，0 在上方，顺时针） */
export function pointAngle(cx: number, cy: number, x: number, y: number): number {
  const a = Math.atan2(x - cx, cy - y)
  return a < 0 ? a + TAU : a
}

/** 边界坐标 → 它右侧的碱基位置（1..L） */
export function xToPos(x: number, L: number): number {
  return normPos(Math.floor(x) + 1, L)
}

/**
 * 两个边界坐标之间的选区。b 可在 a 左侧（反向拖拽）或展开到 < 0 / > L
 * （环状拖过原点）。相等时返回 null（只是光标）。线性分子会被夹到 0..L。
 */
export function rangeFromBoundaries(a: number, b: number, L: number, circular: boolean): Range | null {
  let lo = Math.round(Math.min(a, b))
  let hi = Math.round(Math.max(a, b))
  if (!circular) {
    lo = Math.max(0, lo)
    hi = Math.min(L, hi)
  }
  if (hi <= lo) return null
  if (hi - lo >= L) return { start: 1, end: L }
  return { start: normPos(lo + 1, L), end: normPos(hi, L) }
}

/**
 * 环状拖拽时把当前角度展开成相对锚点连续变化的边界坐标：
 * 每次移动取与上一个展开值最近的等价表示，因此拖过原点也连续。
 */
export function unwrapNear(x: number, prev: number, L: number): number {
  const k = Math.round((prev - x) / L)
  return x + k * L
}

/** 环形扇环（可选箭头）路径。a1 < a2（可 > 2π），r1 < r2 */
export function arcBandPath(
  cx: number, cy: number, r1: number, r2: number, a1: number, a2: number,
  arrow: 0 | 1 | -1 = 0, headAngle = 0,
): string {
  const span = a2 - a1
  if (span <= 0) return ''
  if (span >= TAU - 1e-6) {
    // 整圈：两个半圆拼接，避免起止点重合导致 arc 退化
    const [ox1, oy1] = polar(cx, cy, r2, 0)
    const [ox2, oy2] = polar(cx, cy, r2, Math.PI)
    const [ix1, iy1] = polar(cx, cy, r1, 0)
    const [ix2, iy2] = polar(cx, cy, r1, Math.PI)
    return `M${f(ox1)} ${f(oy1)}A${f(r2)} ${f(r2)} 0 1 1 ${f(ox2)} ${f(oy2)}A${f(r2)} ${f(r2)} 0 1 1 ${f(ox1)} ${f(oy1)}Z`
      + `M${f(ix1)} ${f(iy1)}A${f(r1)} ${f(r1)} 0 1 0 ${f(ix2)} ${f(iy2)}A${f(r1)} ${f(r1)} 0 1 0 ${f(ix1)} ${f(iy1)}Z`
  }
  const h = arrow === 0 ? 0 : Math.min(headAngle, span * 0.6)
  const b1 = arrow === -1 ? a1 + h : a1
  const b2 = arrow === 1 ? a2 - h : a2
  const rm = (r1 + r2) / 2
  const large = b2 - b1 > Math.PI ? 1 : 0
  const p: string[] = []
  const [s2x, s2y] = polar(cx, cy, r2, b1)
  const [e2x, e2y] = polar(cx, cy, r2, b2)
  const [e1x, e1y] = polar(cx, cy, r1, b2)
  const [s1x, s1y] = polar(cx, cy, r1, b1)
  if (arrow === -1) {
    const [tx, ty] = polar(cx, cy, rm, a1)
    p.push(`M${f(tx)} ${f(ty)}L${f(s2x)} ${f(s2y)}`)
  } else {
    p.push(`M${f(s2x)} ${f(s2y)}`)
  }
  p.push(`A${f(r2)} ${f(r2)} 0 ${large} 1 ${f(e2x)} ${f(e2y)}`)
  if (arrow === 1) {
    const [tx, ty] = polar(cx, cy, rm, a2)
    p.push(`L${f(tx)} ${f(ty)}`)
  }
  p.push(`L${f(e1x)} ${f(e1y)}`)
  p.push(`A${f(r1)} ${f(r1)} 0 ${large} 0 ${f(s1x)} ${f(s1y)}Z`)
  return p.join('')
}

/** 圆弧（用于 textPath 等），a1 < a2 */
export function arcPath(cx: number, cy: number, r: number, a1: number, a2: number, reverse = false): string {
  const [x1, y1] = polar(cx, cy, r, reverse ? a2 : a1)
  const [x2, y2] = polar(cx, cy, r, reverse ? a1 : a2)
  const large = a2 - a1 > Math.PI ? 1 : 0
  return `M${f(x1)} ${f(y1)}A${f(r)} ${f(r)} 0 ${large} ${reverse ? 0 : 1} ${f(x2)} ${f(y2)}`
}

/** 线性块状箭头路径（strand 1 头朝右，-1 朝左，0 无头） */
export function barArrowPath(x1: number, x2: number, y: number, h: number, strand: number, head = true): string {
  const w = Math.max(1, x2 - x1)
  const aw = head && strand !== 0 ? Math.min(h * 0.8, w * 0.45) : 0
  if (strand === 1) {
    const bx = x2 - aw
    return `M${f(x1)} ${f(y)}H${f(bx)}L${f(x2)} ${f(y + h / 2)}L${f(bx)} ${f(y + h)}H${f(x1)}Z`
  }
  if (strand === -1) {
    const bx = x1 + aw
    return `M${f(x2)} ${f(y)}H${f(bx)}L${f(x1)} ${f(y + h / 2)}L${f(bx)} ${f(y + h)}H${f(x2)}Z`
  }
  return `M${f(x1)} ${f(y)}H${f(x2)}V${f(y + h)}H${f(x1)}Z`
}

function f(n: number): string {
  return (Math.round(n * 100) / 100).toString()
}

/**
 * 多段特征里画箭头（3' 端）的那一段的下标。按坐标定位而非段顺序：正链是终点等于
 * 整体 end 的段，负链是起点等于整体 start 的段——图谱契约的 segments 按生物学顺序
 * （负链坐标降序），测序比对的轨道段按坐标升序，两种顺序都适用。找不到时取末段。
 */
export function headSegmentIndex(ranges: Range[], whole: Range, strand: number): number {
  const n = ranges.length
  if (!n) return -1
  if (strand === 1) {
    for (let i = n - 1; i >= 0; i--) if (ranges[i].end === whole.end) return i
  } else if (strand === -1) {
    for (let i = 0; i < n; i++) if (ranges[i].start === whole.start) return i
  }
  return n - 1
}
