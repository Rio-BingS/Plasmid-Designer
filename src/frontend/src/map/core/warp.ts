/**
 * 分段比例尺（焦点 + 上下文）：视口 [from, to]（0-based 碱基边界）内，焦点区段
 * （如测序 read 覆盖）按 share 占像素宽度，其余区段压缩但保留，全长仍可见。
 * 各段内部线性；焦点占比本来就 ≥ share 时退回普通线性比例尺（返回 null）。
 */
export interface WarpSeg { from: number; to: number; focus: boolean; x1: number; x2: number }
export interface Warp { segs: WarpSeg[]; X: (b: number) => number; toB: (x: number) => number; minScale: number }

/** 合并后的焦点区段（1-based 闭区间 → 0-based 边界 [s-1, e]），裁到视口内 */
export function mergeFocus(ranges: { start: number; end: number }[], from: number, to: number): [number, number][] {
  const iv = ranges
    .map((r) => [Math.max(from, Math.min(r.start, r.end) - 1), Math.min(to, Math.max(r.start, r.end))] as [number, number])
    .filter(([a, b]) => b > a)
    .sort((x, y) => x[0] - y[0])
  const out: [number, number][] = []
  for (const [a, b] of iv) {
    const last = out[out.length - 1]
    if (last && a <= last[1]) last[1] = Math.max(last[1], b)
    else out.push([a, b])
  }
  return out
}

export function buildWarp(from: number, to: number, focus: { start: number; end: number }[],
                          x0: number, width: number, share = 0.6): Warp | null {
  const span = to - from
  if (!(span > 0) || !(width > 0) || !focus.length) return null
  const iv = mergeFocus(focus, from, to)
  const F = iv.reduce((s, [a, b]) => s + (b - a), 0)
  if (F <= 0 || F / span >= share || span - F <= 0) return null
  const kf = (share * width) / F
  const kn = ((1 - share) * width) / (span - F)
  const segs: WarpSeg[] = []
  let b = from
  let x = x0
  const push = (a: number, c: number, isF: boolean) => {
    if (c <= a) return
    const w = (c - a) * (isF ? kf : kn)
    segs.push({ from: a, to: c, focus: isF, x1: x, x2: x + w })
    x += w
  }
  for (const [a, c] of iv) { push(b, a, false); push(a, c, true); b = c }
  push(b, to, false)
  const X = (p: number) => {
    if (p <= segs[0].from) return segs[0].x1 + (p - segs[0].from) * (segs[0].focus ? kf : kn)
    for (const s of segs) if (p <= s.to) return s.x1 + (p - s.from) * (s.focus ? kf : kn)
    const l = segs[segs.length - 1]
    return l.x2 + (p - l.to) * (l.focus ? kf : kn)
  }
  const toB = (px: number) => {
    if (px <= segs[0].x1) return segs[0].from + (px - segs[0].x1) / (segs[0].focus ? kf : kn)
    for (const s of segs) if (px <= s.x2) return s.from + (px - s.x1) / (s.focus ? kf : kn)
    const l = segs[segs.length - 1]
    return l.to + (px - l.x2) / (l.focus ? kf : kn)
  }
  return { segs, X, toB, minScale: Math.min(kf, kn) }
}
