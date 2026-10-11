/**
 * 特征分道：把互相重叠的区间堆到不同道，道数尽量少（first-fit，跨原点感知）。
 * 思路同 SeqViz（MIT）/ OVE（MIT）的 yOffset 计算：按起点排序、长者优先、
 * 依次放进第一个不冲突的道。gap 为同道相邻两项之间至少留出的碱基数
 * （调用方按像素/比例尺换算，避免视觉上首尾相贴）。
 */
import type { Range } from '../types'
import { rangeLength, splitRange } from './coords'

export interface LaneItem extends Range { id: string }
export interface LaneResult {
  /** id → 道号（0 起） */
  lanes: Map<string, number>
  laneCount: number
  /** 超出 maxLanes 而无法放置的项 */
  overflow: string[]
}

export function stackLanes(
  items: LaneItem[], L: number,
  opts: { circular?: boolean; gap?: number; maxLanes?: number } = {},
): LaneResult {
  const circular = opts.circular ?? true
  const gap = Math.max(0, opts.gap ?? 0)
  const maxLanes = opts.maxLanes ?? Infinity
  const sorted = [...items].sort((a, b) =>
    a.start - b.start || rangeLength(b, L, circular) - rangeLength(a, L, circular) || (a.id < b.id ? -1 : 1))
  const occ: [number, number][][] = []
  const lanes = new Map<string, number>()
  const overflow: string[] = []
  for (const it of sorted) {
    const segs = intervals(it, L, circular, gap)
    let lane = occ.findIndex((o) => !o.some(([s1, e1]) => segs.some(([s2, e2]) => s1 < e2 && s2 < e1)))
    if (lane === -1) {
      if (occ.length >= maxLanes) {
        overflow.push(it.id)
        continue
      }
      occ.push([])
      lane = occ.length - 1
    }
    occ[lane].push(...segs)
    lanes.set(it.id, lane)
  }
  return { lanes, laneCount: occ.length, overflow }
}

/** 半开边界区间（含 gap 外扩），环状时把越过原点的外扩部分复制到另一端 */
function intervals(r: Range, L: number, circular: boolean, gap: number): [number, number][] {
  const out: [number, number][] = []
  const segs = circular ? splitRange(r, L) : [{ start: Math.min(r.start, r.end), end: Math.max(r.start, r.end) }]
  for (const s of segs) {
    const a = s.start - 1 - gap / 2
    const b = s.end + gap / 2
    out.push([a, b])
    if (circular) {
      if (a < 0) out.push([a + L, b + L])
      if (b > L) out.push([a - L, b - L])
    }
  }
  return out
}
