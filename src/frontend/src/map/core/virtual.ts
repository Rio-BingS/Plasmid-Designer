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
