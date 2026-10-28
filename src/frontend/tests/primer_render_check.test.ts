// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest'

describe('primer SnapGene style markers', () => {
  it('SequenceMap primer bar carries name badge fields', async () => {
    // 静态检查：渲染代码包含名牌（rect + nameX/nameY）结构
    const { readFileSync } = await import('node:fs')
    const src = readFileSync('src/map/components/SequenceMap.vue', 'utf-8')
    expect(src).toContain("kind: 'primer'")
    expect(src).toContain('nameY')
    expect(src).toContain('#4a90d9')
  })
  it('CircularMap primer labels use chip (badge) style', async () => {
    const { readFileSync } = await import('node:fs')
    const src = readFileSync('src/map/components/CircularMap.vue', 'utf-8')
    const m = src.match(/for \(const p of primerGeoms\.value\)[\s\S]{0,500}?\n  \}/)
    expect(m![0]).toContain("kind: 'chip'")
    expect(m![0]).toContain("'#4a90d9'")
  })
})
