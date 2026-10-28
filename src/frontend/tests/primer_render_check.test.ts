// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest'

describe('primer SnapGene style markers', () => {
  it('SequenceMap primers: SnapGene replica — purple binding arrows + blue primer boxes', async () => {
    // 按用户提供的 SnapGene 截图复刻序列视图的两类引物标签：
    //   ①紫色结合位点箭头（特征行内，程序导出的 primer_bind 注释）
    //   ②蓝色引物框（正链框在序列上方、负链框在下方，框内印完整序列、
    //     名牌骑框沿、5′ 端折角），5′ 突出悬在结合区外
    const { readFileSync } = await import('node:fs')
    const src = readFileSync('src/map/components/SequenceMap.vue', 'utf-8')
    expect(src).toContain("color: '#a855f7'")            // ①结合位点箭头（紫）
    expect(src).toContain('function boxRange')           // ②引物框区间（± 5′ 突出）
    expect(src).toContain('full_sequence')               // 框内印完整序列
    expect(src).toContain('class="smap-pbox"')           // 框渲染层
    expect(src).toContain("fill=\"#4a90d9\"")            // 框描边/名牌（蓝）
    expect(src).toContain(".split('').reverse()")       // 负链框 5′→3′ 反向排布
    // 悬空名牌压序列的历史方案不得回潮
    expect(src).not.toContain('nameFits')
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
