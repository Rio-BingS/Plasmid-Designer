/**
 * 虚拟酶切消化与凝胶模拟（纯函数，Benchling "Digest" 的简化版）。
 *
 * - 切点取顶链切口（cut_top，边界坐标 0..L：切在第 cut_top 个碱基之后）；
 *   不同酶落在同一边界只算一次切口，片段两端记录所有相关酶名。
 * - 环状：n 个切口 → n 个片段，最后一段跨原点；0 个切口 → 未切开的环。
 *   线性：落在 0 或 L 上的切口不产生片段；n 个切口 → n+1 个片段。
 * - 片段坐标 1-based 闭区间（环状 start > end 表示跨原点），与契约一致。
 * - 凝胶：迁移距离 ∝ -log10(bp)（琼脂糖凝胶在 0.25–10 kb 的经典近似），
 *   条带亮度 ∝ 质量（长度 × 份数），共迁移的片段合并成一条带。
 */
import type { MapEnzyme, MethylationKind } from '../types'

export interface DigestCut {
  /** 边界坐标 0..L */
  at: number
  enzymes: string[]
}

export interface DigestFragment {
  /** 1-based 闭区间；环状 start > end 跨原点 */
  start: number
  end: number
  length: number
  /** 左端（start 之前）切口的酶名；线性分子末端为 null */
  left: string[] | null
  /** 右端（end 之后）切口的酶名 */
  right: string[] | null
  wraps: boolean
}

export interface DigestResult {
  cuts: DigestCut[]
  fragments: DigestFragment[]
  /** 环状分子没有任何切口（未切开） */
  uncut: boolean
  /** 因甲基化阻断被跳过的位点数 */
  skippedBlocked: number
}

export interface DigestOptions {
  /** 跳过被 dam/dcm 阻断的位点（模拟 dam+/dcm+ 菌株来源的质粒） */
  skipBlocked?: boolean
}

export function digest(
  length: number,
  circular: boolean,
  enzymes: Pick<MapEnzyme, 'name' | 'sites'>[],
  opts: DigestOptions = {},
): DigestResult {
  const L = Math.max(0, Math.floor(length))
  const byAt = new Map<number, Set<string>>()
  let skippedBlocked = 0
  for (const e of enzymes) {
    for (const s of e.sites) {
      if (opts.skipBlocked && s.blocked_by?.length) { skippedBlocked++; continue }
      let at = Math.round(s.cut_top)
      if (circular) {
        at = ((at % L) + L) % L // L 与 0 是同一个边界
      } else if (at <= 0 || at >= L) {
        continue
      }
      if (!byAt.has(at)) byAt.set(at, new Set())
      byAt.get(at)!.add(e.name)
    }
  }
  const cuts: DigestCut[] = [...byAt.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([at, names]) => ({ at, enzymes: [...names].sort() }))

  const fragments: DigestFragment[] = []
  if (L === 0) return { cuts, fragments, uncut: false, skippedBlocked }

  if (circular) {
    if (!cuts.length) {
      return {
        cuts, uncut: true, skippedBlocked,
        fragments: [{ start: 1, end: L, length: L, left: null, right: null, wraps: false }],
      }
    }
    for (let i = 0; i < cuts.length; i++) {
      const a = cuts[i]
      const b = cuts[(i + 1) % cuts.length]
      const len = cuts.length === 1 ? L : (b.at - a.at + L) % L
      const start = (a.at % L) + 1
      const end = b.at === 0 ? L : b.at
      fragments.push({ start, end, length: len, left: a.enzymes, right: b.enzymes, wraps: start > end })
    }
    return { cuts, fragments, uncut: false, skippedBlocked }
  }

  const bounds: (DigestCut | null)[] = [null, ...cuts, null]
  for (let i = 0; i < bounds.length - 1; i++) {
    const a = bounds[i]
    const b = bounds[i + 1]
    const from = a ? a.at : 0
    const to = b ? b.at : L
    fragments.push({ start: from + 1, end: to, length: to - from, left: a?.enzymes ?? null, right: b?.enzymes ?? null, wraps: false })
  }
  return { cuts, fragments, uncut: false, skippedBlocked }
}

/** 片段按长度从大到小（凝胶阅读顺序） */
export function sortBySize(fragments: DigestFragment[]): DigestFragment[] {
  return [...fragments].sort((a, b) => b.length - a.length || a.start - b.start)
}

export function blockedKinds(e: Pick<MapEnzyme, 'sites'>): MethylationKind[] {
  const s = new Set<MethylationKind>()
  for (const site of e.sites) for (const k of site.blocked_by ?? []) s.add(k)
  return [...s].sort()
}

// ---------------------------------------------------------------- 凝胶

/** 1 kb DNA ladder（NEB N3232 条带，bp） */
export const LADDER_1KB = [10000, 8000, 6000, 5000, 4000, 3000, 2000, 1500, 1000, 500]

export interface GelScale {
  /** 凝胶可分辨的最小/最大片段（超出范围的条带压在边缘） */
  minBp: number
  maxBp: number
  /** 像素：大片段在 top，小片段在 bottom */
  top: number
  bottom: number
}

export const DEFAULT_GEL: GelScale = { minBp: 250, maxBp: 12000, top: 18, bottom: 250 }

/** 片段长度 → 条带纵坐标（越小跑得越远，单调递增） */
export function gelY(bp: number, scale: GelScale = DEFAULT_GEL): number {
  const lo = Math.log10(scale.minBp)
  const hi = Math.log10(scale.maxBp)
  const v = Math.log10(Math.min(scale.maxBp, Math.max(scale.minBp, bp)))
  const t = (hi - v) / (hi - lo)
  return scale.top + t * (scale.bottom - scale.top)
}

export interface GelBand {
  y: number
  /** 合并后的代表长度（质量加权） */
  bp: number
  count: number
  /** 0..1，按质量归一 */
  intensity: number
  outOfRange: boolean
}

/** 片段 → 条带：纵向相距 < mergePx 的片段合并（共迁移），亮度按质量归一 */
export function gelBands(sizes: number[], scale: GelScale = DEFAULT_GEL, mergePx = 2.5): GelBand[] {
  const items = sizes.filter((n) => n > 0).map((bp) => ({ bp, y: gelY(bp, scale) })).sort((a, b) => a.y - b.y)
  const groups: { y: number; mass: number; count: number; bps: number[] }[] = []
  for (const it of items) {
    const g = groups[groups.length - 1]
    if (g && it.y - g.y < mergePx) {
      g.mass += it.bp
      g.count++
      g.bps.push(it.bp)
    } else {
      groups.push({ y: it.y, mass: it.bp, count: 1, bps: [it.bp] })
    }
  }
  const maxMass = Math.max(1, ...groups.map((g) => g.mass))
  return groups.map((g) => {
    const bp = Math.round(g.bps.reduce((s, x) => s + x * x, 0) / g.mass)
    return {
      y: gelY(bp, scale), bp, count: g.count,
      intensity: Math.max(0.25, Math.min(1, g.mass / maxMass)),
      outOfRange: bp < scale.minBp || bp > scale.maxBp,
    }
  })
}

export function formatBp(bp: number): string {
  if (bp < 1000) return `${bp} bp`
  const kb = (bp / 1000).toFixed(2)
  return `${kb.replace(/\.(\d*?)0+$/, (_m, d: string) => (d ? `.${d}` : ''))} kb`
}
