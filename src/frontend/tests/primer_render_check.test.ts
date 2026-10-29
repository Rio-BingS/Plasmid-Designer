// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest'

describe('primer SnapGene style markers', () => {
  it('SequenceMap primers: SnapGene replica — blue primer boxes only (binding arrows removed)', async () => {
    // 按用户要求：第一类（特征行里的引物条/紫箭头）不再显示；第二类（蓝色
    // 引物框）完善——名牌悬在框外（正链框上方/负链框下方）、锚 5′ 端折角旁；
    // AS/R/REV 引物画在下方（GenBank primer_bind 链标注常不一致，按名字补判）
    const { readFileSync } = await import('node:fs')
    const src = readFileSync('src/map/components/SequenceMap.vue', 'utf-8')
    expect(src).toContain('function boxRange')           // ②引物框区间（± 5′ 突出）
    expect(src).toContain('full_sequence')               // 框内印完整序列
    expect(src).toContain('class="smap-pbox"')           // 框渲染层
    expect(src).toContain("fill=\"#4a90d9\"")            // 框描边/名牌（蓝）
    expect(src).toContain('.split(\'\').reverse()')      // 负链框 5′→3′ 反向排布
    expect(src).toContain('function isRevPrimer')        // AS 引物画在下方
    // 第一类（特征行引物条）已移除：p:${p.id} 不再进 bars
    expect(src).not.toContain('bars.push({\n          key: `p:')
    // 名牌悬空在框外，不压框内序列文字
    expect(src).toContain('fwd ? boxY - BADGE_H - 1 : boxY + BOX_H + 1')
    // 悬空名牌压序列行的更早历史方案不得回潮
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
