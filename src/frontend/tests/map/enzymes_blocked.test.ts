import { describe, it, expect } from 'vitest'
import type { MapDocument } from '@/map/types'
import { blockedSiteCount, cutMarks, cutStyle, BLOCKED_COLOR } from '@/map/core/enzymes'
import pUC19 from '../fixtures/map/pUC19.json'

const puc = () => JSON.parse(JSON.stringify(pUC19)) as MapDocument

describe('甲基化阻断位点', () => {
  const d = puc()
  const e = { ...d.enzymes[0], cut_count: 2, sites: [d.enzymes[0].sites[0], { ...d.enzymes[0].sites[0], position: 900, cut_top: 900, cut_bottom: 904, blocked_by: ['dcm' as const], overhang: 'TTAA' }] }

  it('cutMarks 带 blocked/overhang；showBlocked=false 略去阻断位点', () => {
    const all = cutMarks([e])
    expect(all).toHaveLength(2)
    expect(all[1].blocked).toEqual(['dcm'])
    expect(all[1].overhang).toBe('TTAA')
    expect(all[0].overhang).toBe(e.overhang)
    expect(cutMarks([e], false)).toHaveLength(1)
    expect(blockedSiteCount(e)).toBe(1)
  })

  it('阻断位点灰色虚线、不加粗', () => {
    const [ok, bl] = cutMarks([e])
    expect(cutStyle(ok).dash).toBeUndefined()
    expect(cutStyle(bl)).toMatchObject({ color: BLOCKED_COLOR, dash: '3 2', weight: 400, blocked: true })
  })
})

describe('overviewCuts', () => {
  it('未超限原样返回；超限时按切点数从少到多纳入，单/双切点总保留', async () => {
    const { overviewCuts } = await import('@/map/core/enzymes')
    const mk = (name: string, n: number) => Array.from({ length: n }, (_, i) => ({ id: `${name}#${i}`, name, cut: i }) as any)
    const few = [...mk('A', 1), ...mk('B', 3)]
    expect(overviewCuts(few, 10).shown).toHaveLength(4)
    const many = [...mk('A', 1), ...mk('B', 2), ...mk('C', 5), ...mk('D', 8), ...mk('E', 30)]
    const r = overviewCuts(many, 10)
    expect(new Set(r.shown.map((c: any) => c.name))).toEqual(new Set(['A', 'B', 'C']))
    expect(r.dropped).toEqual([{ name: 'D', count: 8 }, { name: 'E', count: 30 }])
    // 即使单/双切点酶本身就超限也保留（它们是最有用的）
    const dense = [...mk('A', 2), ...mk('B', 2), ...mk('C', 2)]
    expect(overviewCuts(dense, 3).shown).toHaveLength(6)
  })
})
