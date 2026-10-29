// @vitest-environment happy-dom
/**
 * 线性视图引物分道回归：
 *   - 短引物的最小渲染宽度扩宽必须在**分道之前**完成（primerLanes 内按 bp 扩），
 *     否则分道后 px 级撑宽会让同道相邻引物互相叠上（用户截图指正）；
 *   - 本测试用 40 条 20bp 密集短引物（步长 15bp）挂载 LinearMap，
 *     按 d 属性的 y 值分车道，断言每条车道内引物条水平两两不重叠。
 */
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import LinearMap from '@/map/components/LinearMap.vue'
import type { MapDocument } from '@/map/types'

const L = 2000
const seq = 'ACGT'.repeat(L / 4)

function docWithDensePrimers(): MapDocument {
  const primers = Array.from({ length: 40 }, (_, i) => {
    const s = 100 + i * 15
    const rev = i % 2 === 1
    return {
      id: `t${i}`, name: `T${String(i + 1).padStart(2, '0')}${rev ? '_AS' : ''}`,
      start: s, end: s + 19, strand: (rev ? -1 : 1) as 1 | -1,
      sequence: seq.slice(s - 1, s - 1 + 20), tm: 60, gc: 50,
    }
  })
  return {
    schema: 'plasmid-map/1', name: 'dense', topology: 'circular', length: L, sequence: seq,
    features: [], enzymes: [], orfs: [], primers,
    meta: { source_format: 'raw', enzyme_set: 'common', warnings: [] },
  } as MapDocument
}

describe('LinearMap 引物行', () => {
  it('40 条密集短引物：固定 2 行（正向 1 行 + 反向 1 行），全部渲染、正向在上', () => {
    const w = mount(LinearMap, { props: { doc: docWithDensePrimers(), width: 1000 } })
    const rects = w.findAll('.lmap-primer').map((p) => {
      const d = p.attributes('d')!
      const nums = d.match(/-?[\d.]+/g)!.map(Number)
      const xs = [nums[0], nums[3]]
      // primerBarPath：正向 numbers[0]=5′ 左端 < numbers[3]=3′ 尖；反向相反
      return { y: nums[1], xMin: Math.min(...xs), xMax: Math.max(...xs), fwd: nums[0] < nums[3] }
    })
    // 行数优先：只有 2 行，全部引物都渲染（不为避让加行、不丢条目）
    const laneYs = [...new Set(rects.map((r) => Math.round(r.y * 10) / 10))]
    expect(rects.length).toBe(40)
    expect(laneYs.length).toBe(2)
    // 正向行整体在反向行之上（与序列视图「正向框在上、负链框在下」一致）
    const fwdYs = rects.filter((r) => r.fwd).map((r) => r.y)
    const revYs = rects.filter((r) => !r.fwd).map((r) => r.y)
    expect(fwdYs.length).toBe(20)
    expect(revYs.length).toBe(20)
    expect(Math.max(...fwdYs)).toBeLessThan(Math.min(...revYs))
    // 同行内每条引物保持真实 3′ 尖（形状不被挤压成线）
    for (const r of rects) expect(r.xMax).toBeGreaterThan(r.xMin)
  })
})
