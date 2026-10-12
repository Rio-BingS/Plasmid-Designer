/** 序列视图虚拟滚动与分行（纯函数） */

/** 可见行区间 [first, last)（含上下预渲染） */
export function visibleRows(scrollTop: number, viewportH: number, rowH: number, total: number, overscan = 3): [number, number] {
  if (rowH <= 0 || total <= 0) return [0, 0]
  const inView = Math.ceil(viewportH / rowH)
  // 内容变短而滚动位置还停在后面时，退回显示末尾几行
  const first = Math.max(0, Math.min(Math.floor(scrollTop / rowH) - overscan, total - inView - overscan))
  const last = Math.min(total, Math.ceil((scrollTop + viewportH) / rowH) + overscan)
  return [first, Math.max(first, last)]
}

/** 每行碱基数：按 10 的倍数取整，至少 10 */
export function basesPerRow(widthPx: number, charW: number, gutterPx: number): number {
  const n = Math.floor((widthPx - gutterPx) / charW)
  return Math.max(10, Math.floor(n / 10) * 10)
}

/** 位置所在行（0 起） */
export function rowOf(pos: number, perRow: number): number {
  return Math.floor((pos - 1) / perRow)
}

// ---------------- 变高行（每行按自身内容定高） ----------------

/** 行高数组 → 前缀和 offsets（长度 n+1，offsets[i] = 第 i 行顶部，offsets[n] = 总高） */
export function rowOffsets(heights: ArrayLike<number>): Float64Array {
  const out = new Float64Array(heights.length + 1)
  for (let i = 0; i < heights.length; i++) out[i + 1] = out[i] + heights[i]
  return out
}

/** y 所在行（二分）；越界夹到 [0, n-1] */
export function rowAtY(offsets: Float64Array, y: number): number {
  const n = offsets.length - 1
  if (n <= 0) return 0
  let lo = 0
  let hi = n - 1
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1
    if (offsets[mid] <= y) lo = mid
    else hi = mid - 1
  }
  return lo
}

/** 变高行的可见区间 [first, last)（含上下预渲染 overscan 行） */
export function visibleRowsVar(scrollTop: number, viewportH: number, offsets: Float64Array, overscan = 3): [number, number] {
  const n = offsets.length - 1
  if (n <= 0) return [0, 0]
  const total = offsets[n]
  // 内容变短而滚动位置还停在后面时，退回显示末尾
  const top = Math.max(0, Math.min(scrollTop, total - viewportH))
  const first = Math.max(0, rowAtY(offsets, top) - overscan)
  const last = Math.min(n, rowAtY(offsets, top + viewportH - 1e-6) + 1 + overscan)
  return [first, Math.max(first, last)]
}
