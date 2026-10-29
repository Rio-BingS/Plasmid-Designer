/**
 * 密集引物布局回归（模块 A / Step 5 验收）：
 *   - 环形图的标签布局（core/labels.layoutCircularLabels）在密集输入下
 *     输出**两两不重叠**的框——这是属性级保证，与缩放无关（缩放只改变
 *     渲染尺寸，布局在 SVG 坐标系里算完再缩放，相对关系不变）；
 *   - 线性视图的布局（layoutLinearLabels）同样逐行不重叠。
 *
 * 样本与后端 tests/test_map_dense_primers.py 同一生成规则
 * （40 条、起点 +12、长 20、正反向交替），保证前后端覆盖同一密集图。
 */
import { describe, expect, it } from 'vitest'
import { layoutCircularLabels, layoutLinearLabels, boxesOverlap } from '../src/map/core/labels'
import { buildPrimerLabel, primerLabelWidth } from '../src/map/core/primer_labels'

const FONT = 11
const POS_FONT = 9.5
const ROW_H = 14

/** 与后端一致的密集引物样本 */
function densePrimers(n = 40, start = 30, step = 12, span = 20) {
  return Array.from({ length: n }, (_, i) => {
    const s = start + i * step
    const rev = i % 2 === 1
    return {
      key: `p:${i}`,
      name: `D_${String(i + 1).padStart(2, '0')}${rev ? '_AS' : ''}`,
      pos: `${s} .. ${s + span - 1}`,
      mid: (s / 600) * Math.PI * 2,
      r: 210,
      rev,
    }
  })
}

describe('密集引物标签布局（环形）', () => {
  it('40 条引物：布局输出的标签框两两不重叠', () => {
    const maxW = 300
    const inputs = densePrimers().flatMap((p) => {
      // maxW 故意给得足够宽，让布局而不是宽度成为唯一约束
      const lab = buildPrimerLabel(p, { maxW, font: FONT, posFont: POS_FONT, showPos: true, rowH: ROW_H })
      return lab ? [lab.input] : []
    })
    expect(inputs.length).toBeGreaterThan(0)
    const lay = layoutCircularLabels(inputs, {
      cx: 300, cy: 300, labelRadius: 260, fontSize: FONT, lineHeight: ROW_H,
      fanRadius: 240, stretch: true, xMin: 2, xMax: 598, maxShift: 200,
      halfHeight: 296,
    })
    const placed = lay.placed
    expect(placed.length).toBeGreaterThan(10)
    // 关键断言：任何两个已放置标签的框不相交
    let overlaps = 0
    const pairs: string[] = []
    for (let i = 0; i < placed.length; i++) {
      for (let j = i + 1; j < placed.length; j++) {
        if (boxesOverlap(placed[i].box, placed[j].box)) {
          overlaps++
          if (pairs.length < 3) pairs.push(`${placed[i].text} ✕ ${placed[j].text}`)
        }
      }
    }
    expect(overlaps, `重叠标签：${pairs.join('；')}`).toBe(0)
    // 隐藏的必须上报，不能静默消失
    expect(lay.hidden.length + placed.length).toBe(inputs.length)
  })

  it('字体放大 1.5×（等效放大查看）后仍不重叠', () => {
    const maxW = 420
    const inputs = densePrimers(30).flatMap((p) => {
      const lab = buildPrimerLabel(p, { maxW, font: FONT * 1.5, posFont: POS_FONT * 1.5, showPos: true, rowH: ROW_H * 1.5 })
      return lab ? [lab.input] : []
    })
    const lay = layoutCircularLabels(inputs, {
      cx: 300, cy: 300, labelRadius: 260, fontSize: FONT * 1.5, lineHeight: ROW_H * 1.5,
      fanRadius: 240, stretch: true, xMin: 2, xMax: 598, maxShift: 200, halfHeight: 296,
    })
    let overlaps = 0
    for (let i = 0; i < lay.placed.length; i++) {
      for (let j = i + 1; j < lay.placed.length; j++) {
        if (boxesOverlap(lay.placed[i].box, lay.placed[j].box)) overlaps++
      }
    }
    expect(overlaps).toBe(0)
  })
})

describe('密集引物标签布局（线性）', () => {
  it('40 条引物：逐行不重叠且不丢条目', () => {
    const inputs = densePrimers().map((p, i) => ({
      id: p.key,
      text: p.rev ? `← ${p.name}` : `${p.name} →`,
      bold: true,
      x: 20 + i * 12,
      priority: 3e7,
    }))
    const lay = layoutLinearLabels(inputs, {
      width: 600, fontSize: 10, maxRows: 20, gap: 4, left: 2, maxShift: 40, rowCost: 18,
    })
    // 同行的框不相交
    const byRow = new Map<number, typeof lay.placed>()
    for (const l of lay.placed) {
      const arr = byRow.get(l.row) ?? []
      arr.push(l)
      byRow.set(l.row, arr)
    }
    for (const [row, items] of byRow) {
      for (let i = 0; i < items.length; i++) {
        for (let j = i + 1; j < items.length; j++) {
          const a = items[i], b = items[j]
          expect(a.x + a.w <= b.x + 1e-6 || b.x + b.w <= a.x + 1e-6,
            `第 ${row} 行标签重叠：${a.text} ✕ ${b.text}`).toBe(true)
        }
      }
    }
    expect(lay.placed.length + lay.hidden.length).toBe(inputs.length)
    // 40 条密集引物必须真的分多行（不是全挤一行硬撑）
    expect(byRow.size).toBeGreaterThan(1)
  })
})

describe('标签宽度计算（含方向符号与坐标）', () => {
  it('primerLabelWidth 覆盖正向与反向最长标签', () => {
    const prims = [
      { name: 'C01_S01', start: 678, end: 726 },
      { name: 'C01_AS22', start: 1434, end: 1491 },
    ]
    const w = primerLabelWidth(prims, { font: FONT, posFont: POS_FONT, showPos: true })
    // 反向标签是 `← C01_AS22 (1434 .. 1491)`，比正向长
    expect(w).toBeGreaterThan(60)
    // 关掉坐标后变窄
    const w2 = primerLabelWidth(prims, { font: FONT, posFont: POS_FONT, showPos: false })
    expect(w2).toBeLessThan(w)
  })
})

/**
 * 名牌与坐标必须落在布局预留框**内**。
 *
 * 布局只保证「框」之间不重叠、框不越界；渲染层若把文字画出框外，布局层查不出来。
 * 线上 40 条密集引物曾因此把左列坐标画到框左侧，整段溢出 SVG 被裁掉
 * （9 条只剩后半截 `475 .. 494)`）。这里直接校验渲染几何与框的关系。
 */
describe('名牌/坐标渲染几何不越出预留框', () => {
  const maxW = 300
  const BOX_X = 100

  it('左右两列的坐标与名牌都落在框内', async () => {
    const { primerChipGeometry, PRIMER_CHIP_PAD } = await import('../src/map/core/primer_labels')
    const { textWidth } = await import('../src/map/core/text')
    for (const rev of [false, true]) {
      const p = { key: 'p:1', name: rev ? 'C01_AS22' : 'C01_S01', pos: '1434 .. 1491', mid: 0, r: 210, rev }
      const lab = buildPrimerLabel(p, { maxW, font: FONT, posFont: POS_FONT, showPos: true, rowH: ROW_H })!
      expect(lab.pos, '前提：该宽度下坐标应显示').not.toBe('')
      const chipW = lab.nameW + 2 * PRIMER_CHIP_PAD
      const posW = textWidth(lab.pos, POS_FONT)
      const box = { x: BOX_X, w: lab.input.width!, y: 0, h: ROW_H }
      for (const side of [1, -1] as const) {
        const g = primerChipGeometry(box, side, chipW, true)
        const right = box.x + box.w
        // 名牌完整落在框内
        expect(g.chipX).toBeGreaterThanOrEqual(box.x - 1e-6)
        expect(g.chipX + g.chipW).toBeLessThanOrEqual(right + 1e-6)
        // 坐标完整落在框内（布局按「起点 + 实测宽度」预留，两者都要校验）
        expect(g.posX).not.toBeNull()
        expect(g.posX!).toBeGreaterThanOrEqual(box.x - 1e-6)
        expect(g.posX! + posW).toBeLessThanOrEqual(right + 1e-6)
        // 名牌与坐标不互相重叠
        const posEnd = g.posX! + posW
        expect(posEnd <= g.chipX + 1e-6 || g.chipX + g.chipW <= g.posX! + 1e-6).toBe(true)
      }
    }
  })

  it('无坐标时名牌仍完整落在框内', async () => {
    const { primerChipGeometry, PRIMER_CHIP_PAD } = await import('../src/map/core/primer_labels')
    const lab = buildPrimerLabel({ key: 'p:2', name: 'D_01', pos: '31 .. 50', mid: 0, r: 210, rev: false },
      { maxW, font: FONT, posFont: POS_FONT, showPos: false, rowH: ROW_H })!
    const chipW = lab.nameW + 2 * PRIMER_CHIP_PAD
    const box = { x: BOX_X, w: lab.input.width!, y: 0, h: ROW_H }
    for (const side of [1, -1] as const) {
      const g = primerChipGeometry(box, side, chipW, false)
      expect(g.posX).toBeNull()
      expect(g.chipX).toBeGreaterThanOrEqual(box.x - 1e-6)
      expect(g.chipX + chipW).toBeLessThanOrEqual(box.x + box.w + 1e-6)
    }
  })
})
