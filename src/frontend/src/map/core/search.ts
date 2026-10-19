/**
 * 序列搜索：最多 k 个错配（替换，不含插入/缺失），双链，环状跨原点，
 * 查询可含 IUPAC 模糊码（模糊码匹配其代表的任一碱基不算错配）。
 * 朴素逐位比较 + 超过 k 即提前退出：300 kb × 30 nt × 双链在 10 ms 量级。
 */
import type { MapFeature, Range } from '../types'
import { normPos } from './coords'
import { reverseComplement } from './seq'

export interface SearchHit extends Range { strand: 1 | -1; mismatches: number }

/** 元件名称命中（CDS 的基因名、启动子名等），用于序列搜索之外的名称搜索 */
export interface FeatureMatch extends Range { id: string; name: string; type: string }

export interface SearchResult { hits: SearchHit[]; error: string | null; truncated: boolean }

const MASK: Record<string, number> = {
  A: 1, C: 2, G: 4, T: 8, U: 8, R: 5, Y: 10, S: 6, W: 9, K: 12, M: 3, B: 14, D: 13, H: 11, V: 7, N: 15,
}

export function normalizeQuery(q: string): string {
  return q.replace(/\s+/g, '').toUpperCase()
}

/**
 * 按元件名称搜索：名称大小写不敏感的子串匹配，按文档顺序返回（最多 limit 条）。
 * 查询不是 DNA 碱基串时（如基因名 "PNPM5"），序列搜索会报「只能包含 DNA
 * 碱基」——名称搜索就是这类查询的落点。
 */
export function searchFeatures(
  features: readonly MapFeature[], query: string, limit = 200,
): FeatureMatch[] {
  const q = query.trim().toLowerCase()
  if (!q) return []
  const out: FeatureMatch[] = []
  for (const f of features) {
    if (out.length >= limit) break
    if (!(f.name ?? '').toLowerCase().includes(q)) continue
    out.push({ id: f.id, name: f.name, type: f.type, start: f.start, end: f.end })
  }
  return out
}

export function searchSequence(
  seq: string, query: string,
  opts: { maxMismatches?: number; circular?: boolean; limit?: number } = {},
): SearchResult {
  const q = normalizeQuery(query)
  const k = Math.max(0, Math.floor(opts.maxMismatches ?? 0))
  const limit = opts.limit ?? 2000
  if (!q) return { hits: [], error: null, truncated: false }
  if (!/^[ACGTURYSWKMBDHVN]+$/.test(q)) return { hits: [], error: '查询只能包含 DNA 碱基（含 IUPAC 模糊码）', truncated: false }
  const L = seq.length
  const m = q.length
  if (!L || m > L) return { hits: [], error: null, truncated: false }
  if (k >= m) return { hits: [], error: '错配数必须小于查询长度', truncated: false }
  const circular = opts.circular ?? false
  const text = circular ? seq + seq.slice(0, m - 1) : seq
  const tmask = new Uint8Array(text.length)
  for (let i = 0; i < text.length; i++) tmask[i] = MASK[text[i]] ?? 15
  const hits: SearchHit[] = []
  const seen = new Set<number>()
  let truncated = false
  const strands: [1 | -1, string][] = [[1, q]]
  const rc = reverseComplement(q)
  if (rc !== q) strands.push([-1, rc])
  outer: for (const [strand, pat] of strands) {
    const pmask = Array.from(pat, (c) => MASK[c] ?? 15)
    const lastStart = circular ? L - 1 : L - m
    for (let i = 0; i <= lastStart; i++) {
      let mm = 0
      for (let j = 0; j < m; j++) {
        // 序列碱基须落在查询碱基代表的集合内（序列里的 N 只与查询 N 匹配）
        const t = tmask[i + j]
        if ((t & pmask[j]) !== t) {
          if (++mm > k) break
        }
      }
      if (mm <= k) {
        const key = i * 2 + (strand === 1 ? 0 : 1)
        if (seen.has(key)) continue
        seen.add(key)
        hits.push({ start: i + 1, end: normPos(i + m, L), strand, mismatches: mm })
        if (hits.length >= limit) { truncated = true; break outer }
      }
    }
  }
  hits.sort((a, b) => a.start - b.start || b.strand - a.strand)
  return { hits, error: null, truncated }
}
