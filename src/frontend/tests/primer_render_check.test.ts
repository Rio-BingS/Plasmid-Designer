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

  it('CircularMap primers: blue name chip + coordinates, all labeled (SnapGene screenshot replica)', async () => {
    // 按用户提供的 SnapGene 截图复刻：蓝底白字圆角名牌 + 旁侧坐标，
    // 22 条全部标出；坐标放不下时整段省略但绝不截断
    const { readFileSync } = await import('node:fs')
    const src = readFileSync('src/map/components/CircularMap.vue', 'utf-8')
    const chip = readFileSync('src/map/components/PrimerLabel.vue', 'utf-8')
    const mod = readFileSync('src/map/core/primer_labels.ts', 'utf-8')
    expect(src).toContain("kind: 'primer'")
    expect(src).toContain('<PrimerLabel')
    expect(chip).toContain('PRIMER_CHIP_FILL')
    expect(mod).toContain("PRIMER_CHIP_FILL = '#4a90d9'")
    expect(mod).toContain("PRIMER_CHIP_INK = '#ffffff'")
    // 坐标放不下整段省略，不截断
    expect(mod).toContain('fitsPos ? posText')
    expect(src).toContain('store.show.locations')
    // 不得回退到「整体隐藏」与更早的沿弧/必显示外挂方案
    expect(src).not.toContain('MAX_PRIMER_LABELS')
    expect(src).not.toContain('internalArcLabel(p.name')
    // 引物轨道保持原版细弧带
    expect(src).toContain('lane * 6')
  })

  it('MapViewer exposes the Locations toggle (SnapGene Show Locations)', async () => {
    const { readFileSync } = await import('node:fs')
    const src = readFileSync('src/map/components/MapViewer.vue', 'utf-8')
    expect(src).toContain("{ key: 'locations', label: '位置' }")
  })
})
