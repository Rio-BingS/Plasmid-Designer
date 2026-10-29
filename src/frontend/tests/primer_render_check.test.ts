// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest'

describe('primer SnapGene style markers', () => {
  it('SequenceMap primers: SnapGene boxes with sense/antisense colors + 3′ arrows', async () => {
    // 模块 B/D：框描边与名牌按正反向分色（走 core/primer_style），
    // 3′ 端画延伸箭头，名牌带方向符号，名牌→框沿有细引导线（模块 A）
    const { readFileSync } = await import('node:fs')
    const src = readFileSync('src/map/components/SequenceMap.vue', 'utf-8')
    expect(src).toContain('function boxRange')           // 引物框区间（± 5′ 突出）
    expect(src).toContain('full_sequence')               // 框内印完整序列
    expect(src).toContain('class="smap-pbox"')           // 框渲染层
    expect(src).toContain('PRIMER_FWD')                  // 正向配色
    expect(src).toContain('PRIMER_REV')                  // 反向配色
    expect(src).toContain('b.tip')                       // 3′ 延伸箭头
    expect(src).toContain('smap-pbox-tip')
    expect(src).toContain('.split(\'\').reverse()')      // 负链框 5′→3′ 反向排布
    expect(src).toContain('isRevPrimer')                 // AS 引物画在下方
    // 名牌悬空在框外，不压框内序列文字
    expect(src).toContain('fwd ? boxY - BADGE_H - 1 : boxY + BOX_H + 1')
    expect(src).not.toContain('nameFits')
  })

  it('CircularMap primers: sense/antisense colors + direction arrows + coordinates', async () => {
    // 视觉规范（模块 B/D）：正向蓝 #2563EB、反向紫 #7C3AED；
    // 名牌内带 → / ← 方向符号；坐标放不下整段省略但绝不截断
    const { readFileSync } = await import('node:fs')
    const src = readFileSync('src/map/components/CircularMap.vue', 'utf-8')
    const chip = readFileSync('src/map/components/PrimerLabel.vue', 'utf-8')
    const mod = readFileSync('src/map/core/primer_labels.ts', 'utf-8')
    const style = readFileSync('src/map/core/primer_style.ts', 'utf-8')
    const arcs = readFileSync('src/map/core/primer_arcs.ts', 'utf-8')
    expect(src).toContain('primerLabelInfo')
    expect(mod).toContain("kind: 'primer'")
    expect(src).toContain('<PrimerLabel')
    // 正反向分色与方向符号（单一来源：core/primer_style）
    expect(style).toContain("base: '#2563EB'")
    expect(style).toContain("base: '#7C3AED'")
    expect(mod).toContain('← ${name}')
    expect(mod).toContain('${name} →')
    expect(chip).toContain('primerPalette')
    // 坐标放不下整段省略，不截断
    expect(mod).toContain('fitsPos ? posText')
    expect(src).toContain('store.show.locations')
    // 弧形轨道保持细弧带 + 3′ 端延伸箭头（几何在 core/primer_arcs）
    expect(src).toContain('laneGap: 6')
    expect(src).toContain('buildPrimerArcs')
    expect(arcs).toContain('arcArrowPath')
    // 不得回退到「整体隐藏」与更早的沿弧方案
    expect(src).not.toContain('MAX_PRIMER_LABELS')
    expect(src).not.toContain('internalArcLabel(p.name')
  })

  it('引物配色满足 WCAG AA（白字对比度 ≥ 4.5）', async () => {
    const { PRIMER_FWD, PRIMER_REV, contrastRatio } = await import('../src/map/core/primer_style')
    // 白字落在名牌底色上：AA 正文阈值 4.5
    expect(contrastRatio('#ffffff', PRIMER_FWD.base)).toBeGreaterThanOrEqual(4.5)
    expect(contrastRatio('#ffffff', PRIMER_REV.base)).toBeGreaterThanOrEqual(4.5)
    // 正反向色相必须可区分（对比度不足 3 就无法靠颜色分辨）
    expect(contrastRatio(PRIMER_FWD.base, PRIMER_REV.base)).toBeGreaterThan(1.1)
  })

  it('MapViewer exposes the Locations toggle (SnapGene Show Locations)', async () => {
    const { readFileSync } = await import('node:fs')
    const src = readFileSync('src/map/components/MapViewer.vue', 'utf-8')
    expect(src).toContain("{ key: 'locations', label: '位置' }")
  })
})
