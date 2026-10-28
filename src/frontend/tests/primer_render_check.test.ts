// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest'

describe('primer SnapGene style markers', () => {
  it('SequenceMap primer bar carries name badge fields', async () => {
    const { readFileSync } = await import('node:fs')
    const src = readFileSync('src/map/components/SequenceMap.vue', 'utf-8')
    expect(src).toContain("kind: 'primer'")
    expect(src).toContain('nameY')
    expect(src).toContain('#4a90d9')
  })

  it('CircularMap primer names rendered along arc, not external labels', async () => {
    // 引物名沿自己的弧书写（textPath），不外挂标签环——20+ oligo 全部
    // 外挂会塞爆布局引擎、引线飞满全图（v2.33.0 回归）
    const { readFileSync } = await import('node:fs')
    const src = readFileSync('src/map/components/CircularMap.vue', 'utf-8')
    expect(src).toContain('internalArcLabel(p.name')
    expect(src).toContain("p.key + ':label'")
    const start = src.indexOf('const labelData = computed')
    const labelData = src.slice(start, src.indexOf('\n})', start))
    expect(labelData).not.toContain('primerGeoms.value)')
  })
})
