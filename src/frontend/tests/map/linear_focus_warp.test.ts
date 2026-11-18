import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import type { MapDocument } from '@/map/types'
import pUC19 from '../fixtures/map/pUC19.json'
import LinearMap from '@/map/components/LinearMap.vue'
import { buildWarp } from '@/map/core/warp'

const puc = () => JSON.parse(JSON.stringify(pUC19)) as MapDocument

describe('分段比例尺（突出 read 覆盖）', () => {
  it('焦点区占 share 宽度，全长仍在图内，X/toB 互逆且单调', () => {
    // 6238bp 质粒，read 覆盖 5000–6100（与用户截图同形）
    const w = buildWarp(0, 6238, [{ start: 5000, end: 6100 }], 10, 1000, 0.6)!
    expect(w).not.toBeNull()
    const f = w.segs.find((s) => s.focus)!
    expect(f.x2 - f.x1).toBeCloseTo(600, 5)
    expect(w.X(0)).toBeCloseTo(10, 5)
    expect(w.X(6238)).toBeCloseTo(1010, 5)
    let last = -Infinity
    for (let b = 0; b <= 6238; b += 97) {
      const x = w.X(b)
      expect(x).toBeGreaterThan(last)
      last = x
      expect(w.toB(x)).toBeCloseTo(b, 6)
    }
  })

  it('焦点本已够宽 / 无焦点 → 线性（null）', () => {
    expect(buildWarp(0, 1000, [{ start: 1, end: 900 }], 0, 500, 0.6)).toBeNull()
    expect(buildWarp(0, 1000, [], 0, 500, 0.6)).toBeNull()
  })

  it('多段焦点合并重叠区间', () => {
    const w = buildWarp(0, 6000, [{ start: 100, end: 600 }, { start: 500, end: 900 }, { start: 4000, end: 4500 }], 0, 1000, 0.6)!
    const fs = w.segs.filter((s) => s.focus)
    expect(fs.length).toBe(2)
    expect(fs.reduce((t, s) => t + s.x2 - s.x1, 0)).toBeCloseTo(600, 5)
  })

  it('LinearMap 传 focus 时画断裂符，焦点区的刻度间距大于压缩区', async () => {
    const w = mount(LinearMap, { props: { doc: puc(), width: 1000, focus: [{ start: 1800, end: 2300 }] } })
    expect(w.findAll('.lmap-break').length).toBeGreaterThan(0)
    const lin = mount(LinearMap, { props: { doc: puc(), width: 1000 } })
    expect(lin.findAll('.lmap-break').length).toBe(0)
    const xOf = (wr: ReturnType<typeof mount>) => (wr.vm as unknown as { $: { setupState: { X: (b: number) => number } } }).$.setupState.X
    const X = xOf(w)
    expect(X(2300) - X(1799)).toBeGreaterThan(500)          // 501bp ≈ 0.6×980px
    expect(xOf(lin)(2300) - xOf(lin)(1799)).toBeLessThan(200) // 线性下约 183px
  })
})

describe('匹配简图结果位点标注', () => {
  it('判定条目与未决位点在主轴上方逐点标注，与差异同位点合并，read 上画位点圆点', async () => {
    const MatchMap = (await import('@/components/sequencing/MatchMap.vue')).default
    const read = (i: number, fn: string, dir: string, s: number, e: number) => ({ index: i, filename: fn, direction: dir, ref_start: s, ref_end: e, identity: 0.99, wraps_origin: false, alignment_view: null })
    const w = mount(MatchMap, { props: {
      reads: [read(0, 'a.ab1', '+', 1000, 1900), read(1, 'b.ab1', '-', 1100, 2000)] as never,
      variants: [{ ref_pos: 1500, type: 'substitution', ref_base: 'C', alt_base: 'A', length: 1, aa_change: 'X:P1Q' }] as never,
      features: [], coverageRanges: [[1000, 2000]], referenceLength: 6000, circular: true,
      verdict: { status: 'mixed', info: [{ kind: 'unresolved_site', ref_start: 1950, ref_end: 1950, detail: '弱双峰', refs: ['b.ab1'] }],
        blocking: [{ kind: 'mixed_variant', ref_start: 1500, ref_end: 1500, detail: '1500 双峰', refs: ['a.ab1', 'b.ab1'] },
                   { kind: 'unresolved_site', ref_start: 1050, ref_end: 1050, detail: '无法互检', refs: ['a.ab1'] }] } as never,
      unresolvedSites: [{ ref_pos: 1200, kind: 'mixed_uncorroborated', reads: ['a.ab1'], detail: 'x' }] as never,
    } })
    const labels = w.findAll('.lmap-mark-label').map((t) => t.text())
    expect(labels).toContain('1500 C>A P1Q · 疑似混合')
    expect(labels).toContain('1050 双峰待核')
    expect(labels).not.toContain('1950 双峰待核')   // 提示级只留刻块不写字
    expect(labels).not.toContain('1200 双峰')
    expect(w.findAll('.map-var-tick').length).toBe(1)
    expect(w.findAll('.map-site-tick').length).toBe(3)
    expect(w.findAll('.lmap-dot2').length).toBe(0) // read 上不再叠结果圆点（2026-11-19 去杂）
  })
})

describe('匹配简图 read 测序情况', () => {
  it('read 按质量分档着色、可信区外淡化、明显双峰竖线、摘要小字、测序确认色带', async () => {
    const MatchMap = (await import('@/components/sequencing/MatchMap.vue')).default
    const ref = 'ACGTACGTAC'.repeat(10)
    const q = Array.from({ length: 100 }, (_, i) => (i < 10 ? 10 : i < 20 ? 25 : 50))
    const readSeq = ref.slice(0, 50) + 'T' + ref.slice(51)   // 51 错配（参考 C→T? 任意不同即可）
    const fwd = { index: 0, filename: 'f.ab1', direction: '+', ref_start: 1001, ref_end: 1100, query_start: 0, identity: 0.99,
      wraps_origin: false, grade: 'A', q20_ratio: 0.9, mixed_count: 1,
      reliable_zone: { start: 5, end: 95 }, mixed_detail: [{ pos: 60, ratio: 0.45 }],
      alignment_view: { ref_start: 1001, ref_aligned: ref, read_aligned: readSeq, q_aligned: q } }
    const rev = { index: 1, filename: 'r.ab1', direction: '-', ref_start: 1041, ref_end: 1100, query_start: 0, identity: 1,
      wraps_origin: false, alignment_view: { ref_start: 1041, ref_aligned: ref.slice(40), read_aligned: ref.slice(40), q_aligned: Array(60).fill(50) } }
    const w = mount(MatchMap, { props: {
      reads: [fwd, rev] as never, variants: [], features: [], coverageRanges: [[1001, 1100]], referenceLength: 5000, circular: true,
    } })
    expect(w.findAll('.lmap-band').length).toBeGreaterThanOrEqual(2)        // Q<20 与 Q20–29 两档
    expect(w.findAll('.lmap-band-hatch').length).toBe(0)                    // 不再斜线重点标出
    expect(w.findAll('.lmap-band').filter((b) => b.attributes('fill-opacity') === '0.72').length).toBeGreaterThanOrEqual(2) // 两端可信区外淡化
    expect(w.findAll('.lmap-read-tick').length).toBe(1)                     // 1060 双峰
    expect(w.find('.lmap-track-badge').text()).toBe('A · Q20 90% · 一致 99.0% · 双峰 1')
    const cls = w.findAll('rect.map-confirm').map((r) => r.classes().find((c) => c.startsWith('map-confirm-')))
    expect(cls).toContain('map-confirm-both')
    expect(cls).toContain('map-confirm-single')
    expect(cls).toContain('map-confirm-conflict')
  })
})
