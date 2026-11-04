/**
 * 悬停高亮区间（模块 C）。
 *
 * 引物在同一构建体上可能有多处结合位点（同一条 oligo 命中多个位置，图谱里
 * 是多个 MapPrimer 条目）。悬停其中一条时，同名的其余位点也要一起高亮——
 * 只看 id 会漏掉其余结合位置。特征/酶等同理按 range 单点高亮。
 */
import type { MapPrimer, Range } from '../types'

/**
 * 悬停对象 → 需要高亮的全部区间。
 * @param kindId 命中键（`p:<id>` 表示引物）
 * @param range  命中对象的区间
 * @param primers 当前文档的引物列表（用于按名字找同名位点）
 */
export function hoverHighlightRanges(
  kindId: string,
  range: Range | null,
  primers: ReadonlyArray<MapPrimer>,
): Range[] {
  if (!range) return []
  if (!kindId.startsWith('p:')) return [range]
  const cur = primers.find((p) => `p:${p.id}` === kindId)
  if (!cur) return [range]
  return primers
    .filter((p) => p.name === cur.name)
    .map((p) => ({ start: p.start, end: p.end }))
}

/** 把高亮区间映射成环形弧带 path（视图只提供几何函数） */
export function hoverHighlightPaths(
  kindId: string,
  range: Range | null,
  primers: ReadonlyArray<MapPrimer>,
  env: { L: number; unroll: (r: Range) => { from: number; to: number }; ang: (x: number) => number; arc: (a1: number, a2: number) => string },
): string[] {
  return hoverHighlightRanges(kindId, range, primers).map((r) => {
    const u = env.unroll(r)
    return env.arc(env.ang(u.from), env.ang(Math.max(u.to, u.from + env.L / 1000)))
  })
}
