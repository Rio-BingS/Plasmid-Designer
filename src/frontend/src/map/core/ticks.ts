/** 刻度：1/2/2.5/5 × 10^k 的"好看"步长 */

export function niceStep(span: number, target = 8): number {
  if (span <= 0 || target <= 0) return 1
  const raw = span / target
  const mag = Math.pow(10, Math.floor(Math.log10(raw)))
  for (const m of [1, 2, 2.5, 5, 10]) {
    const s = m * mag
    if (s >= raw) return Math.max(1, Math.round(s))
  }
  return Math.max(1, Math.round(10 * mag))
}

/** [from, to] 内（含端点）步长整倍数的位置；位置为 1-based 碱基序号 */
export function ticks(from: number, to: number, target = 8): { step: number; positions: number[] } {
  const step = niceStep(Math.max(1, to - from), target)
  const out: number[] = []
  let p = Math.ceil(Math.max(1, from) / step) * step
  for (; p <= to; p += step) out.push(p)
  return { step, positions: out }
}

/** 位置数字：≥10 000 用 k，≥1 000 000 用 M（去掉多余的 0） */
export function formatBp(n: number): string {
  const trim = (v: number) => String(Math.round(v * 10) / 10)
  if (Math.abs(n) >= 1_000_000) return trim(n / 1_000_000) + 'M'
  if (Math.abs(n) >= 10_000) return trim(n / 1000) + 'k'
  return String(n)
}

/** 长度文本："2,686 bp" */
export function formatLength(n: number): string {
  return `${n.toLocaleString('en-US')} bp`
}
