// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest'

describe('primer SnapGene style markers', () => {
  it('SequenceMap primers: in-bar label, SnapGene blue, no floating badge', async () => {
    const { readFileSync } = await import('node:fs')
    const src = readFileSync('src/map/components/SequenceMap.vue', 'utf-8')
    expect(src).toContain("'#4a90d9'")
    // 名牌悬空结构不应存在（曾压住序列行）
    expect(src).not.toContain("kind: 'primer'")
    expect(src).not.toContain('nameFits')
    // 名字在条形内（fitsInside 判定）
    expect(src).toContain('fitsInside(p.name, 10')
  })

  it('CircularMap primers: chip label with normal priority (hideable)', async () => {
    // v2.33.0 教训：名牌标成必显示外挂，20+ oligo 引线飞满全图；
    // v2.33.1 沿弧书写轨道太宽更糟。最终形态：细弧带 + 外部名牌 chip，
    // 优先级与普通特征同级、放不下自动隐藏
    const { readFileSync } = await import('node:fs')
    const src = readFileSync('src/map/components/CircularMap.vue', 'utf-8')
    expect(src).toContain("kind: 'chip', fill: '#4a90d9'")
    expect(src).toContain("featurePriority('misc_feature', 1)")
    expect(src).not.toContain('priority: 5e7')
    expect(src).not.toContain('internalArcLabel(p.name')
    // 引物轨道回原版细弧带（无 13px 加高）
    expect(src).toContain('lane * 6')
  })
})
