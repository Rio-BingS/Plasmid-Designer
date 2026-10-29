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

/** primerBarPath 的 d → { y, xMin, xMax }（正向/反向统一取 numbers[0] 与 numbers[3] 为两端） */
function barRect(d: string): { y: number; xMin: number; xMax: number } {
  const nums = (d.match(/-?[\d.]+/g) ?? []).map(Number)
  return { y: nums[1], xMin: Math.min(nums[0], nums[3]), xMax: Math.max(nums[0], nums[3]) }
}

describe('LinearMap 引物分道', () => {
  it('40 条密集短引物：同一车道内引物条两两不重叠', () => {
    const w = mount(LinearMap, { props: { doc: docWithDensePrimers(), width: 1000 } })
    const rects = w.findAll('.lmap-primer').map((p) => barRect(p.attributes('d')!))
    expect(rects.length).toBeGreaterThan(20)
    const byLane = new Map<number, typeof rects>()
    for (const r of rects) {
      const key = Math.round(r.y * 10) / 10
      if (!byLane.has(key)) byLane.set(key, [])
      byLane.get(key)!.push(r)
    }
    expect(byLane.size).toBeGreaterThanOrEqual(2)
    for (const [lane, rs] of byLane) {
      rs.sort((a, b) => a.xMin - b.xMin)
      for (let i = 1; i < rs.length; i++) {
        expect(rs[i].xMin, `车道 ${lane} 第 ${i} 条压到前一条`).toBeGreaterThanOrEqual(rs[i - 1].xMax - 0.5)
      }
    }
  })
})
