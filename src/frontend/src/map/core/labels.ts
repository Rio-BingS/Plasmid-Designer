/**
 * 标签布局引擎。
 *
 * 环形图外置标签（SnapGene 式有序扇形，详见 layoutCircularLabels）：
 *   左右两列、按角度排序；标签沿一条椭圆曲线排开（拥挤时上下拉长），
 *   竖直位置用变行高 PAV（保序回归）求最小位移解，x 取曲线横坐标形成阶梯；
 *   引线 = 径向外伸 + 直线 + 短尾。修复环保证框不重叠、引线不交叉、不压字、
 *   不扎回环内；放不下的计入 hidden（界面显示"N 项无法显示"，同 SnapGene 8）。
 *
 * 线性外置标签：按优先级逐个放入最低的、水平不冲突的行（带间隙），
 * 超过 maxRows 的计入 hidden。
 *
 * 内置标签：文字宽度 + 内边距 ≤ 弧长（或条宽）减箭头时画在元件内部
 * （环形弧带用 internalArcLabel 判定，textPath 沿弧书写）。
 */
import type { FeatureType } from '../types'
import { TAU, polar } from './coords'
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

export interface ArcLabelFit {
  /** 文字基线所在半径（textPath 路径半径） */
  r: number
  /** 下半圈反向走（逆时针），避免文字倒置 */
  flip: boolean
}

/**
 * 弧带内置标签（textPath）能否放下：
 *   - 条宽要容得下字高（字号 + 2 px）；
 *   - 文字宽 + 2×内边距 ≤ 文字所在半径上的弧长 − 箭头长。
 * 文字所在半径：上半圈字在路径上方，路径取条中线偏内 0.35 字号；下半圈反向走，偏外。
 * 放得下返回路径半径与方向，否则 null（改为环外标签）。
 */
export function internalArcLabel(
  text: string, fontSize: number, a1: number, a2: number, r1: number, r2: number, headLen = 0, pad = 4,
): ArcLabelFit | null {
  if (!text || a2 <= a1) return null
  if (r2 - r1 < fontSize + 2) return null
  const rm = (r1 + r2) / 2
  const mid = (((a1 + a2) / 2) % (2 * Math.PI) + 2 * Math.PI) % (2 * Math.PI)
  const flip = mid > Math.PI / 2 && mid < (3 * Math.PI) / 2
  const r = flip ? rm + fontSize * 0.35 : rm - fontSize * 0.35
  // 量弧长取字身最靠内的一侧（更短），保证整行都在条内
  const rInner = Math.max(1, rm - fontSize / 2)
  const avail = (a2 - a1) * rInner - headLen
  return fitsInside(text, fontSize, avail, pad) ? { r, flip } : null
}

// ---------------- 环形外置标签 ----------------

export interface CircularLabelInput {
  id: string
  text: string
  /** 粗体显示（单切点酶），按粗体量宽 */
  bold?: boolean
  /** 标签框宽（含内边距）；缺省按 text 量宽。富文本标签（酶名 + 位置、色块标签）由调用方量好传入 */
  width?: number
  /** 标签框高（行高）；缺省 lineHeight。色块标签比纯文字高 */
  height?: number
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
  /**
   * 标签框靠环一侧的竖边 x（右列 = 框左边，text-anchor=start；左列 = 框右边，text-anchor=end），
   * y 为框的垂直中心
   */
  x: number
  y: number
  box: Box
  /** 引线折线：锚点 → 外圈径向外伸点（肘）→ 短尾起点 → 标签端 */
  leader: [number, number][]
}

export interface CircularLabelOptions {
  cx: number
  cy: number
  /** 标签曲线的横半轴（不拥挤时标签就落在这个圆上） */
  labelRadius: number
  fontSize: number
  /** 默认行高（≥ 字号），同侧相邻标签中心的最小间距 */
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
  /**
   * 环上所有图元的最外半径（切点刻线、引物环之外）。引线在这里外伸一小段后
   * 才折向标签，且折线不得再进入这个圆（否则会压在环上）。缺省 labelRadius − lineHeight。
   */
  fanRadius?: number
  /**
   * 拥挤时允许标签曲线在上下方向拉长成椭圆（纵半轴 > 横半轴，最多到 halfHeight），
   * 让顶部/底部的标签堆向外延伸（SnapGene 式），而不是被挤到环的侧面。
   */
  stretch?: boolean
  /**
   * 引线扇形段的最小水平/竖直比（0 = 不限）。标签相对肘点的竖直落差为 d 时，
   * 至少向外摆 fanSlope·d，使扎堆处的引线呈扇形展开。
   */
  fanSlope?: number
  /** 标签框允许的水平范围（缺省不限） */
  xMin?: number
  xMax?: number
}

export interface CircularLabelLayout {
  placed: PlacedCircularLabel[]
  hidden: string[]
}

/** 引线径向外伸长度、标签端短尾长度、引线末端与文字的间隙 */
const KNEE = 6
const TAIL = 5
const TEXT_GAP = 2
/** 引线与其它标签框之间至少留的水平/竖直空隙 */
const CLEAR_X = 3
const CLEAR_Y = 1

interface Work {
  l: CircularLabelInput
  w: number
  h: number
  /** 侧内从上到下的排序键 */
  order: number
}

/**
 * 环形外置标签布局（SnapGene 式有序扇形）：
 *   1. 按锚点角度分左右两列，同列按角度从上到下排序；
 *   2. 标签曲线为椭圆（横半轴 = labelRadius，拥挤时纵半轴拉长），理想位置 = 锚点射线
 *      与椭圆的交点（不拥挤时引线纯径向）；
 *   3. 变行高 PAV 求"相邻不重叠、总位移平方和最小"的 y；超容量按优先级保留；
 *   4. x 取椭圆在该 y 处的横坐标，自然形成阶梯；
 *   5. 修复：引线穿过同列其它标签框 → 把该标签水平外推到引线外侧（单调，必收敛）；
 *      仍有引线交叉 / 引线扎回环内 / 标签越出水平边界 → 丢掉相关标签中优先级最低者重排。
 * 结果保证（属性测试）：标签框两两不重叠，引线两两不交叉，引线不穿过其它标签框，
 * 引线的扇形段不进入 fanRadius 圆。
 * 思路参考 SnapGene 成图、SeqViz（MIT）的分叉展开与 TeselaGen OVE（MIT）的按象限排序；
 * 未复制其代码。
 */
export function layoutCircularLabels(inputs: CircularLabelInput[], o: CircularLabelOptions): CircularLabelLayout {
  const a = o.labelRadius
  const H = o.halfHeight ?? a + 2 * o.lineHeight
  const minX = o.minX ?? 6
  const rFan = Math.min(o.fanRadius ?? a - o.lineHeight, a - 1)
  const hidden: string[] = []
  const work = (l: CircularLabelInput): Work => {
    const m = ((l.angle % TAU) + TAU) % TAU
    const side = labelSide(l.angle)
    return {
      l, w: l.width ?? textWidth(l.text, o.fontSize, l.bold), h: l.height ?? o.lineHeight,
      // 右列：0 → π；左列：2π → π（都是从上往下）
      order: side === 1 ? m : TAU - m,
    }
  }
  const bySide = new Map<1 | -1, Work[]>([[1, []], [-1, []]])
  for (const l of inputs) bySide.get(labelSide(l.angle))!.push(work(l))
  // 容量：同列框高之和 ≤ 2H，超出按优先级保留
  for (const side of [1, -1] as const) {
    let own = bySide.get(side)!
    const total = own.reduce((s, x) => s + x.h, 0)
    if (total > 2 * H + 1e-9) {
      const ranked = [...own].sort((p, q) => q.l.priority - p.l.priority || (p.l.id < q.l.id ? -1 : 1))
      let used = 0
      const keep = new Set<Work>()
      for (const x of ranked) {
        if (used + x.h <= 2 * H + 1e-9) { keep.add(x); used += x.h } else hidden.push(x.l.id)
      }
      own = own.filter((x) => keep.has(x))
    }
    own.sort((p, q) => p.order - q.order || (p.l.id < q.l.id ? -1 : 1))
    bySide.set(side, own)
  }

  // 椭圆纵半轴（左右列各自）：先按圆排一遍，标签堆越过环顶/环底时拉长到刚好容纳；
  // 各列独立，免得一侧扎堆把另一侧不拥挤的标签也推离环
  const placed: PlacedCircularLabel[] = []
  for (const side of [1, -1] as const) {
    const own = bySide.get(side)!
    let bTop = a
    let bBot = a
    if (o.stretch && own.length) {
      const ys = spacedVar(own.map((x) => idealY(x.l.angle, a, a, a, o.cy)), own.map((x) => x.h), o.cy - H, o.cy + H)
      const minY = ys[0] - own[0].h / 2
      const maxY = ys[ys.length - 1] + own[own.length - 1].h / 2
      bTop = Math.min(H, Math.max(a, o.cy - minY + o.lineHeight))
      bBot = Math.min(H, Math.max(a, maxY - o.cy + o.lineHeight))
    }
    // 剔除冲突标签直到无冲突（每轮至少去掉一个）
    for (;;) {
      if (!own.length) break
      const res = placeSide(own, side, { ...o, a, H, minX, rFan, bTop, bBot }, hidden)
      if (!res.drops.length) {
        placed.push(...res.placed)
        break
      }
      // drops 已按下标降序，逐个删除不影响前面的下标
      for (const d of res.drops) {
        hidden.push(own[d].l.id)
        own.splice(d, 1)
      }
    }
  }
  return { placed, hidden }
}

interface SideOpts extends CircularLabelOptions {
  a: number; H: number; minX: number; rFan: number; bTop: number; bBot: number
}

/** 射线（角度 θ）与椭圆（横半轴 a，上/下纵半轴 bTop/bBot）交点的 y */
function idealY(angle: number, a: number, bTop: number, bBot: number, cy: number): number {
  const s = Math.sin(angle)
  const c = Math.cos(angle)
  const b = c > 0 ? bTop : bBot
  const t = 1 / Math.sqrt((s / a) ** 2 + (c / b) ** 2)
  return cy - t * c
}

/** 椭圆在 y 处的横半宽 */
function ellipseX(y: number, o: SideOpts): number {
  const dy = y - o.cy
  const b = dy < 0 ? o.bTop : o.bBot
  const q = 1 - (dy / b) ** 2
  return Math.max(o.minX, q > 0 ? o.a * Math.sqrt(q) : 0)
}

function placeSide(
  own: Work[], side: 1 | -1, o: SideOpts, hidden: string[],
): { placed: PlacedCircularLabel[]; drops: number[] } {
  const heights = own.map((x) => x.h)
  const ideal = own.map((x) => idealY(x.l.angle, o.a, o.bTop, o.bBot, o.cy))
  // 位移上限：在拥挤簇里逐个去掉低优先级者（原地修改 own）
  const ys = pruneDisplaced(own, ideal, heights, o.cy - o.H, o.cy + o.H, o.maxShift ?? Infinity, hidden)
  const n = own.length
  const X = ys.map((y) => ellipseX(y, o))
  const knee = own.map((x) => {
    const r = Math.max(x.l.radius, o.rFan + KNEE)
    return polar(o.cx, o.cy, r, x.l.angle)
  })
  // 框的竖直范围
  const band = (i: number): [number, number] => [ys[i] - heights[i] / 2 - CLEAR_Y, ys[i] + heights[i] / 2 + CLEAR_Y]
  // 自身：短尾起点必须在肘点外侧（引线单调外伸，不压自己的框）
  // 再加最小斜率：竖直落差越大，标签越往外摆，扇形展开而不是一束几乎重合的竖线
  const slope = o.fanSlope ?? 0
  const selfMin = (i: number) => side * (knee[i][0] - o.cx) + TAIL + TEXT_GAP + slope * Math.abs(knee[i][1] - ys[i])
  const pushedBy: number[] = new Array(n).fill(-1)
  // 外推上限（侧向坐标）：到水平边界为止，无边界时 3 倍标签半径
  const limitU = side === 1 ? (o.xMax !== undefined ? o.xMax - o.cx : 3 * o.a) : (o.xMin !== undefined ? o.cx - o.xMin : 3 * o.a)
  for (let i = 0; i < n; i++) X[i] = Math.max(X[i], selfMin(i))
  // 引线（扇形段 + 短尾）在侧向坐标 u = side·(x − cx) 下的端点
  const seg = (i: number): [number, number, number, number] => {
    const ku = side * (knee[i][0] - o.cx)
    return [ku, knee[i][1], X[i] - TAIL - TEXT_GAP, ys[i]]
  }
  // 修复：其它引线穿过框 → 框外推。X 只增不减，有上界 → 收敛
  for (let pass = 0; pass < 2 * n + 8; pass++) {
    let changed = false
    for (let j = 0; j < n; j++) {
      const [y0, y1] = band(j)
      let need = X[j]
      let by = -1
      for (let i = 0; i < n; i++) {
        if (i === j) continue
        const [u0, v0, u1, v1] = seg(i)
        // 引线 i 在 j 的竖直带内的最大侧向坐标（扇形段与短尾，短尾在 v1 处水平）
        const m = maxUInBand(u0, v0, u1, v1, y0, y1)
        if (m === null) continue
        const tailEnd = X[i] - TEXT_GAP
        const mm = v1 >= y0 && v1 <= y1 ? Math.max(m, tailEnd) : m
        if (mm + CLEAR_X > need) { need = mm + CLEAR_X; by = i }
      }
      if (need > X[j] + 1e-6 && X[j] < limitU) {
        X[j] = Math.min(need, limitU)
        pushedBy[j] = by
        changed = true
      }
    }
    // 引线扇形段扎进环内（标签被挤到锚点"背后"）→ 标签外推，直线变缓
    for (let i = 0; i < n; i++) {
      const [u0, v0, u1, v1] = seg(i)
      const d = segDistToPoint([u0, v0 - o.cy], [u1, v1 - o.cy], [0, 0])
      if (d < o.rFan - 1e-6 && X[i] < limitU) {
        X[i] = Math.min(limitU, X[i] + Math.max(2, (o.rFan - d) * 2))
        changed = true
      }
    }
    if (!changed) break
  }
  const placed: PlacedCircularLabel[] = own.map((x, i) => {
    const xx = o.cx + side * X[i]
    const box: Box = { x: side === 1 ? xx : xx - x.w, y: ys[i] - heights[i] / 2, w: x.w, h: heights[i] }
    const anchor = polar(o.cx, o.cy, x.l.radius, x.l.angle)
    const tail0: [number, number] = [xx - side * (TAIL + TEXT_GAP), ys[i]]
    const tail1: [number, number] = [xx - side * TEXT_GAP, ys[i]]
    return { id: x.l.id, text: x.l.text, side, x: xx, y: ys[i], box, leader: [anchor, knee[i], tail0, tail1] }
  })
  // 校验：越界 / 引线扎回环内 / 引线交叉 / 引线压框（修复环未收敛时）。
  // 一轮收集全部冲突，每个冲突丢优先级最低的一方（一次可丢多个，减少重排次数）
  const lowest = (cands: number[]) => cands.reduce((p, q) => (own[q].l.priority < own[p].l.priority
    || (own[q].l.priority === own[p].l.priority && own[q].l.id > own[p].l.id) ? q : p))
  const drops = new Set<number>()
  for (let i = 0; i < n; i++) {
    const b = placed[i].box
    const out = (o.xMin !== undefined && b.x < o.xMin - 1e-6) || (o.xMax !== undefined && b.x + b.w > o.xMax + 1e-6)
    if (out) drops.add(pushedBy[i] >= 0 ? lowest([i, pushedBy[i]]) : i)
    else if (segDistToPoint(placed[i].leader[1], placed[i].leader[2], [o.cx, o.cy]) < o.rFan - 1e-6) drops.add(i)
  }
  if (!drops.size) {
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        if (drops.has(i) || drops.has(j)) continue
        if (leadersCross(placed[i].leader, placed[j].leader)) drops.add(lowest([i, j]))
      }
    }
    for (let j = 0; j < n; j++) {
      for (let i = 0; i < n; i++) {
        if (i === j || drops.has(i) || drops.has(j)) continue
        if (leaderHitsBox(placed[i].leader, placed[j].box)) drops.add(lowest([i, j]))
      }
    }
  }
  return { placed, drops: [...drops].sort((p, q) => q - p) }
}

/** 线段 (u0,v0)-(u1,v1) 落在竖直带 [y0,y1] 内部分的最大 u；不相交返回 null */
function maxUInBand(u0: number, v0: number, u1: number, v1: number, y0: number, y1: number): number | null {
  const lo = Math.min(v0, v1)
  const hi = Math.max(v0, v1)
  if (hi < y0 || lo > y1) return null
  if (Math.abs(v1 - v0) < 1e-9) return Math.max(u0, u1)
  const at = (y: number) => u0 + ((y - v0) / (v1 - v0)) * (u1 - u0)
  const ya = Math.max(lo, y0)
  const yb = Math.min(hi, y1)
  return Math.max(at(ya), at(yb))
}

function segDistToPoint(p: [number, number], q: [number, number], c: [number, number]): number {
  const dx = q[0] - p[0]
  const dy = q[1] - p[1]
  const L2 = dx * dx + dy * dy
  const t = L2 ? Math.max(0, Math.min(1, ((c[0] - p[0]) * dx + (c[1] - p[1]) * dy) / L2)) : 0
  return Math.hypot(p[0] + t * dx - c[0], p[1] + t * dy - c[1])
}

/** 两线段是否严格相交（共端点/仅接触不算） */
export function segmentsCross(p1: [number, number], p2: [number, number], q1: [number, number], q2: [number, number]): boolean {
  const d = (a: [number, number], b: [number, number], c: [number, number]) => (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
  const d1 = d(q1, q2, p1)
  const d2 = d(q1, q2, p2)
  const d3 = d(p1, p2, q1)
  const d4 = d(p1, p2, q2)
  const eps = 1e-7
  return ((d1 > eps && d2 < -eps) || (d1 < -eps && d2 > eps)) && ((d3 > eps && d4 < -eps) || (d3 < -eps && d4 > eps))
}

/** 两条引线折线是否交叉（只看环外部分：肘点之后；肘点之前是各自角度上的径向线，互不相交） */
export function leadersCross(a: [number, number][], b: [number, number][]): boolean {
  for (let i = 1; i < a.length - 1; i++) {
    for (let j = 1; j < b.length - 1; j++) {
      if (segmentsCross(a[i], a[i + 1], b[j], b[j + 1])) return true
    }
  }
  return false
}

/** 引线（肘点之后）是否穿过某标签框 */
export function leaderHitsBox(l: [number, number][], b: Box): boolean {
  const corners: [number, number][] = [[b.x, b.y], [b.x + b.w, b.y], [b.x + b.w, b.y + b.h], [b.x, b.y + b.h]]
  for (let i = 1; i < l.length - 1; i++) {
    const [p, q] = [l[i], l[i + 1]]
    const inside = (pt: [number, number]) => pt[0] > b.x + 1e-6 && pt[0] < b.x + b.w - 1e-6 && pt[1] > b.y + 1e-6 && pt[1] < b.y + b.h - 1e-6
    if (inside(p) || inside(q)) return true
    for (let k = 0; k < 4; k++) if (segmentsCross(p, q, corners[k], corners[(k + 1) % 4])) return true
  }
  return false
}

/**
 * 放置并剔除位移过大的标签：找位移最大且超限的标签，在它所在的"紧贴簇"
 * （相邻框恰好相接的连续段）里去掉优先级最低的一个，重排，直到都不超限。
 * 原地修改 own / ideal / heights（去掉被剔除项），被剔除 id 追加到 hidden。
 */
function pruneDisplaced(
  own: Work[], ideal: number[], heights: number[], lo: number, hi: number, maxShift: number, hidden: string[],
): number[] {
  for (;;) {
    const ys = spacedVar(ideal, heights, lo, hi)
    if (!Number.isFinite(maxShift)) return ys
    let worst = -1
    let worstD = maxShift
    ys.forEach((y, i) => {
      const d = Math.abs(y - ideal[i])
      if (d > worstD + 1e-6) { worst = i; worstD = d }
    })
    if (worst < 0) return ys
    const touch = (i: number) => ys[i + 1] - ys[i] <= (heights[i] + heights[i + 1]) / 2 + 1e-6
    let s = worst
    let e = worst
    while (s > 0 && touch(s - 1)) s--
    while (e < ys.length - 1 && touch(e)) e++
    let drop = s
    for (let i = s + 1; i <= e; i++) {
      const p = own[i].l
      const q = own[drop].l
      if (p.priority < q.priority || (p.priority === q.priority && Math.abs(ys[i] - ideal[i]) > Math.abs(ys[drop] - ideal[drop]))) drop = i
    }
    hidden.push(own[drop].l.id)
    own.splice(drop, 1)
    ideal.splice(drop, 1)
    heights.splice(drop, 1)
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
 * 调用方保证 n 个点放得下（(n-1)·h ≤ hi - lo）。lo/hi 是中心的范围。
 */
export function spacedPositions(ideal: number[], h: number, lo: number, hi: number): number[] {
  return spacedVar(ideal, ideal.map(() => h), lo - h / 2, hi + h / 2)
}

/**
 * 变行高版：第 i 个框高 heights[i]，相邻中心间距 ≥ (h_i + h_{i+1})/2，
 * 框整体落在 [lo, hi]（框边的范围）。令 z_i = y_i − off_i（off 为累计最小间距）
 * 化为保序回归（pool-adjacent-violators），再整体夹到范围内。
 */
export function spacedVar(ideal: number[], heights: number[], lo: number, hi: number): number[] {
  const n = ideal.length
  if (!n) return []
  const off: number[] = [0]
  for (let i = 1; i < n; i++) off.push(off[i - 1] + (heights[i - 1] + heights[i]) / 2)
  const blocks: { sum: number; cnt: number }[] = []
  for (let i = 0; i < n; i++) {
    blocks.push({ sum: ideal[i] - off[i], cnt: 1 })
    while (blocks.length > 1) {
      const b = blocks[blocks.length - 1]
      const p = blocks[blocks.length - 2]
      if (p.sum / p.cnt <= b.sum / b.cnt) break
      p.sum += b.sum
      p.cnt += b.cnt
      blocks.pop()
    }
  }
  const zLo = lo + heights[0] / 2
  const zHi = hi - heights[n - 1] / 2 - off[n - 1]
  const out: number[] = []
  let i = 0
  for (const b of blocks) {
    const z = Math.min(Math.max(b.sum / b.cnt, zLo), Math.max(zLo, zHi))
    for (let k = 0; k < b.cnt; k++, i++) out.push(z + off[i])
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
  o: {
    width: number; fontSize: number; maxRows: number; gap?: number; left?: number
    /**
     * 允许标签离锚点水平错开的最大像素（引线斜向连回锚点）。0 = 只能居中于锚点
     * （再夹到边界内）。密集位点（MCS）错开后能少占行、少隐藏。
     */
    maxShift?: number
    /** 换到更高一行的代价（折算成像素位移），越大越倾向于横向错开 */
    rowCost?: number
  },
): LinearLabelLayout {
  const gap = o.gap ?? 6
  const left = o.left ?? 0
  const maxShift = o.maxShift ?? 0
  const rowCost = o.rowCost ?? 30
  const rows: [number, number][][] = []
  const placed: PlacedLinearLabel[] = []
  const hidden: string[] = []
  const order = [...inputs].sort((a, b) => b.priority - a.priority || a.x - b.x || (a.id < b.id ? -1 : 1))
  const free = (r: [number, number][], x: number, w: number) => !r.some(([a, b]) => x - gap / 2 < b && a < x + w + gap / 2)
  for (const l of order) {
    const w = textWidth(l.text, o.fontSize, l.bold)
    if (w > o.width - left) {
      hidden.push(l.id)
      continue
    }
    const x0 = Math.min(Math.max(left, l.x - w / 2), o.width - w)
    let best: { row: number; x: number; cost: number } | null = null
    const tryRows = Math.min(rows.length + 1, o.maxRows)
    for (let row = 0; row < tryRows; row++) {
      const r = rows[row] ?? []
      const cands = [x0]
      if (maxShift > 0) for (const [a, b] of r) cands.push(b + gap / 2, a - w - gap / 2)
      for (const x of cands) {
        if (x < left - 1e-9 || x + w > o.width + 1e-9 || Math.abs(x - x0) > maxShift + 1e-9 || !free(r, x, w)) continue
        const cost = Math.abs(x - x0) + row * rowCost
        if (!best || cost < best.cost) best = { row, x, cost }
      }
    }
    if (!best) {
      hidden.push(l.id)
      continue
    }
    if (best.row === rows.length) rows.push([])
    rows[best.row].push([best.x - gap / 2, best.x + w + gap / 2])
    placed.push({ id: l.id, text: l.text, row: best.row, x: best.x, w, cx: l.x })
  }
  return { placed, hidden, rows: rows.length }
}
