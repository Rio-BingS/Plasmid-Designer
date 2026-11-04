import { describe, it, expect } from 'vitest'
import type { MapDocument, MapEnzyme } from '@/map/types'
import { blockedKinds, digest, formatBp, gelBands, gelY, LADDER_1KB, sortBySize } from '@/map/core/digest'
import pUC19 from '../fixtures/map/pUC19.json'
import { rng, randInt } from './rand'

const enz = (name: string, cuts: number[], blocked: number[] = []): Pick<MapEnzyme, 'name' | 'sites'> => ({
  name,
  sites: cuts.map((c, i) => ({ position: c, cut_top: c, cut_bottom: c, strand: 1 as const, ...(blocked.includes(i) ? { blocked_by: ['dam' as const] } : {}) })),
})

describe('digest：片段计算', () => {
  it('线性：n 个切口 → n+1 个片段，坐标 1-based 闭区间，总长守恒', () => {
    const r = digest(1000, false, [enz('A', [100, 600])])
    expect(r.fragments.map((f) => [f.start, f.end, f.length])).toEqual([[1, 100, 100], [101, 600, 500], [601, 1000, 400]])
    expect(r.fragments[0].left).toBeNull()
    expect(r.fragments[0].right).toEqual(['A'])
    expect(r.fragments[2].right).toBeNull()
  })

  it('线性：落在 0 或 L 的切口不产生片段', () => {
    const r = digest(1000, false, [enz('A', [0, 1000, 500])])
    expect(r.fragments.map((f) => f.length)).toEqual([500, 500])
  })

  it('环状：单切口 → 一个跨原点的全长线性片段', () => {
    const r = digest(5000, true, [enz('A', [1200])])
    expect(r.fragments).toHaveLength(1)
    expect(r.fragments[0]).toMatchObject({ start: 1201, end: 1200, length: 5000, wraps: true })
  })

  it('环状：两个酶，最后一段跨原点，总长守恒', () => {
    const r = digest(5000, true, [enz('A', [4000]), enz('B', [300])])
    expect(r.fragments.map((f) => [f.start, f.end, f.length, f.wraps])).toEqual([[301, 4000, 3700, false], [4001, 300, 1300, true]])
    expect(r.fragments[1].left).toEqual(['A'])
    expect(r.fragments[1].right).toEqual(['B'])
    expect(r.fragments.reduce((s, f) => s + f.length, 0)).toBe(5000)
  })

  it('环状：切在 L（=0）与切在 0 是同一边界；同点多酶只算一个切口', () => {
    const r = digest(3000, true, [enz('A', [3000, 1000]), enz('B', [1000])])
    expect(r.cuts).toEqual([{ at: 0, enzymes: ['A'] }, { at: 1000, enzymes: ['A', 'B'] }])
    expect(r.fragments.map((f) => [f.start, f.end, f.length])).toEqual([[1, 1000, 1000], [1001, 3000, 2000]])
  })

  it('环状无切口 = 未切开', () => {
    const r = digest(2686, true, [enz('A', [])])
    expect(r.uncut).toBe(true)
    expect(r.fragments[0].length).toBe(2686)
  })

  it('任意切口下片段总长 = 分子长度（属性测试）', () => {
    const r0 = rng(20261012)
    for (let k = 0; k < 200; k++) {
      const L = randInt(r0, 50, 5000)
      const circ = r0() < 0.5
      const n = randInt(r0, 0, 5)
      const cuts = Array.from({ length: n }, () => randInt(r0, 0, L))
      const r = digest(L, circ, [enz('X', cuts)])
      expect(r.fragments.reduce((s, f) => s + f.length, 0)).toBe(L)
      for (const f of r.fragments) {
        expect(f.length).toBeGreaterThan(0)
        expect(f.start).toBeGreaterThanOrEqual(1)
        expect(f.end).toBeLessThanOrEqual(L)
      }
    }
  })

  it('skipBlocked 跳过 dam/dcm 阻断位点并计数', () => {
    const e = enz('ClaI', [100, 700], [1])
    expect(blockedKinds(e)).toEqual(['dam'])
    expect(digest(1000, false, [e]).fragments).toHaveLength(3)
    const r = digest(1000, false, [e], { skipBlocked: true })
    expect(r.fragments).toHaveLength(2)
    expect(r.skippedBlocked).toBe(1)
  })

  it('真实夹具：pUC19 EcoRI + HindIII 双切', () => {
    const doc = pUC19 as unknown as MapDocument
    const sel = doc.enzymes.filter((e) => e.name === 'EcoRI' || e.name === 'HindIII')
    const r = digest(doc.length, true, sel)
    const sizes = sortBySize(r.fragments).map((f) => f.length)
    expect(sizes).toEqual([2635, 51])
  })
})

describe('gel：迁移与条带', () => {
  it('越小迁移越远（单调），超出范围夹在边缘', () => {
    const ys = LADDER_1KB.map((bp) => gelY(bp))
    for (let i = 1; i < ys.length; i++) expect(ys[i]).toBeGreaterThan(ys[i - 1])
    expect(gelY(50)).toBe(gelY(250))
    expect(gelY(50000)).toBe(gelY(12000))
  })

  it('共迁移片段合并，亮度按质量归一', () => {
    const bands = gelBands([3000, 3010, 500])
    expect(bands).toHaveLength(2)
    expect(bands[0].count).toBe(2)
    expect(bands[0].intensity).toBe(1)
    expect(bands[1].intensity).toBeLessThan(1)
  })

  it('formatBp', () => {
    expect(formatBp(500)).toBe('500 bp')
    expect(formatBp(1500)).toBe('1.5 kb')
    expect(formatBp(10000)).toBe('10 kb')
    expect(formatBp(2686)).toBe('2.69 kb')
  })
})
