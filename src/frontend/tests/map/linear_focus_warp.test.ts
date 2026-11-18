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
