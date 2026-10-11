import { describe, it, expect } from 'vitest'
import {
  layoutCircularLabels, layoutLinearLabels, spacedPositions, boxesOverlap, featurePriority,
  enzymePriority, fitsInside, labelSide, type CircularLabelInput,
} from '@/map/core/labels'
import { textWidth, truncateText } from '@/map/core/text'
import { TAU } from '@/map/core/coords'
import { rng, randInt } from './rand'

const NAMES = ['AmpR', 'lacZα', 'pUC ori', 'T7 promoter', 'EcoRI', 'BamHI', 'f1 ori', 'CMV enhancer',
  'SV40 NLS', '6xHis', 'KanR', '启动子', 'lac operator', 'rrnB T1 terminator', 'NotI', 'XhoI']

function randomInputs(r: () => number, n: number): CircularLabelInput[] {
  return Array.from({ length: n }, (_, i) => ({
    id: `L${i}`, text: NAMES[randInt(r, 0, NAMES.length - 1)] + (r() < 0.3 ? ` ${i}` : ''),
    angle: r() < 0.2 ? 0.5 : r() * TAU, // 制造大量同角度扎堆
    radius: 150, priority: randInt(r, 0, 1e8),
  }))
}

describe('spacedPositions（PAV）', () => {
  it('间距 ≥ h，保持顺序，在边界内', () => {
    const r = rng(7)
    for (let k = 0; k < 200; k++) {
      const n = randInt(r, 1, 30)
      const h = 12
      const lo = 0, hi = 600
      const ideal = Array.from({ length: n }, () => r() * 700 - 50).sort((a, b) => a - b)
      const ys = spacedPositions(ideal, h, lo, hi)
      for (let i = 1; i < n; i++) expect(ys[i] - ys[i - 1]).toBeGreaterThanOrEqual(h - 1e-9)
      expect(ys[0]).toBeGreaterThanOrEqual(lo - 1e-9)
      expect(ys[n - 1]).toBeLessThanOrEqual(hi + 1e-9)
    }
  })
  it('无冲突时不移动', () => {
    expect(spacedPositions([10, 40, 90], 12, 0, 100)).toEqual([10, 40, 90])
  })
  it('冲突时对称散开（最小位移）', () => {
    const ys = spacedPositions([50, 50], 10, 0, 100)
    expect(ys).toEqual([45, 55])
  })
})

describe('环形外置标签：无碰撞（属性测试）', () => {
  it('任意输入下放置的标签框两两不相交，hidden + placed = 全部', () => {
    const r = rng(2026)
    for (let round = 0; round < 120; round++) {
      const n = randInt(r, 0, 120)
      const inputs = randomInputs(r, n)
      const layout = layoutCircularLabels(inputs, { cx: 400, cy: 400, labelRadius: 230, fontSize: 11, lineHeight: 14 })
      expect(layout.placed.length + layout.hidden.length).toBe(n)
      const ids = new Set([...layout.placed.map((p) => p.id), ...layout.hidden])
      expect(ids.size).toBe(n)
      for (let i = 0; i < layout.placed.length; i++) {
        for (let j = i + 1; j < layout.placed.length; j++) {
          expect(boxesOverlap(layout.placed[i].box, layout.placed[j].box)).toBe(false)
        }
      }
      // 标签不越过竖直可用区
      for (const p of layout.placed) {
        expect(p.box.y).toBeGreaterThanOrEqual(400 - (230 + 28) - 1e-6)
        expect(p.box.y + p.box.h).toBeLessThanOrEqual(400 + 230 + 28 + 1e-6)
        expect(p.leader).toHaveLength(3)
        expect(p.leader.flat().every(Number.isFinite)).toBe(true)
      }
    }
  })

  it('放不下时按优先级保留', () => {
    const inputs: CircularLabelInput[] = Array.from({ length: 40 }, (_, i) => ({
      id: `p${i}`, text: 'X', angle: 1, radius: 100, priority: i,
    }))
    const layout = layoutCircularLabels(inputs, { cx: 0, cy: 0, labelRadius: 50, fontSize: 10, lineHeight: 10, halfHeight: 50 })
    expect(layout.placed).toHaveLength(10)
    expect(layout.hidden).toHaveLength(30)
    expect(layout.placed.map((p) => p.id).sort()).toEqual(Array.from({ length: 10 }, (_, i) => `p${39 - i}`).sort())
  })

  it('左右分列：右侧标签框在中线右边，左侧在左边', () => {
    const layout = layoutCircularLabels([
      { id: 'r', text: 'Right', angle: 1, radius: 100, priority: 1 },
      { id: 'l', text: 'Left', angle: 5, radius: 100, priority: 1 },
      { id: 'top', text: 'Top', angle: 0, radius: 100, priority: 1 },
    ], { cx: 0, cy: 0, labelRadius: 120, fontSize: 10, lineHeight: 12 })
    const by = Object.fromEntries(layout.placed.map((p) => [p.id, p]))
    expect(by.r.box.x).toBeGreaterThan(0)
    expect(by.l.box.x + by.l.box.w).toBeLessThan(0)
    expect(by.top.side).toBe(1)
    expect(labelSide(Math.PI)).toBe(-1)
  })

  it('标签保持角度顺序（引线不交叉的前提）', () => {
    const inputs = [0.3, 0.5, 0.7, 0.9, 1.1].map((a, i) => ({ id: `a${i}`, text: 'Feature', angle: a, radius: 100, priority: 1 }))
    const layout = layoutCircularLabels(inputs, { cx: 0, cy: 0, labelRadius: 120, fontSize: 10, lineHeight: 30 })
    const ys = inputs.map((l) => layout.placed.find((p) => p.id === l.id)!.y)
    for (let i = 1; i < ys.length; i++) expect(ys[i]).toBeGreaterThan(ys[i - 1])
  })
})

describe('环形外置标签：位移上限', () => {
  it('maxShift：超限时在拥挤簇里丢弃低优先级标签，保留的都在上限内且不重叠', () => {
    const r = rng(31)
    for (let round = 0; round < 60; round++) {
      const inputs = randomInputs(r, randInt(r, 10, 120))
      const o = { cx: 400, cy: 400, labelRadius: 230, fontSize: 11, lineHeight: 14, maxShift: 80 }
      const layout = layoutCircularLabels(inputs, o)
      expect(layout.placed.length + layout.hidden.length).toBe(inputs.length)
      for (const p of layout.placed) {
        const inp = inputs.find((i) => i.id === p.id)!
        const ideal = 400 - 230 * Math.cos(inp.angle)
        // 理想位置在可用区外的会被夹回边界，只对区内的检查位移
        if (Math.abs(ideal - 400) < 230 + 28 - 7) expect(Math.abs(p.y - ideal)).toBeLessThanOrEqual(80 + 1e-6)
      }
      for (let i = 0; i < layout.placed.length; i++) for (let j = i + 1; j < layout.placed.length; j++) {
        expect(boxesOverlap(layout.placed[i].box, layout.placed[j].box)).toBe(false)
      }
    }
  })
  it('扎堆时先丢优先级低的（酶），保留高优先级（特征）', () => {
    const inputs: CircularLabelInput[] = [
      ...Array.from({ length: 30 }, (_, i) => ({ id: `e${i}`, text: 'EcoRI', angle: 1.2, radius: 100, priority: 1e7 })),
      { id: 'f', text: 'AmpR', angle: 1.2, radius: 100, priority: 1e8 },
    ]
    const layout = layoutCircularLabels(inputs, { cx: 0, cy: 0, labelRadius: 200, fontSize: 11, lineHeight: 14, maxShift: 42 })
    expect(layout.placed.map((p) => p.id)).toContain('f')
    expect(layout.placed.length).toBeLessThanOrEqual(7)
    expect(layout.hidden.length).toBe(31 - layout.placed.length)
  })
})

describe('线性外置标签：无碰撞（属性测试）', () => {
  it('同行标签不相交、在宽度内、行数 ≤ maxRows', () => {
    const r = rng(99)
    for (let round = 0; round < 150; round++) {
      const width = randInt(r, 200, 1400)
      const maxRows = randInt(r, 1, 5)
      const inputs = Array.from({ length: randInt(r, 0, 60) }, (_, i) => ({
        id: `x${i}`, text: NAMES[randInt(r, 0, NAMES.length - 1)], x: r() * width, priority: randInt(r, 0, 100),
      }))
      const maxShift = r() < 0.5 ? 0 : randInt(r, 10, 150)
      const res = layoutLinearLabels(inputs, { width, fontSize: 11, maxRows, gap: 4, maxShift })
      expect(res.placed.length + res.hidden.length).toBe(inputs.length)
      expect(res.rows).toBeLessThanOrEqual(maxRows)
      for (const p of res.placed) {
        expect(p.x).toBeGreaterThanOrEqual(-1e-9)
        expect(p.x + p.w).toBeLessThanOrEqual(width + 1e-9)
      }
      for (let i = 0; i < res.placed.length; i++) for (let j = i + 1; j < res.placed.length; j++) {
        const a = res.placed[i], b = res.placed[j]
        if (a.row !== b.row) continue
        expect(a.x + a.w <= b.x || b.x + b.w <= a.x).toBe(true)
      }
      for (const p of res.placed) {
        const inp = inputs.find((i) => i.id === p.id)!
        const x0 = Math.min(Math.max(0, inp.x - p.w / 2), width - p.w)
        expect(Math.abs(p.x - x0)).toBeLessThanOrEqual(maxShift + 1e-6)
      }
    }
  })

  it('maxShift：密集位点横向错开，少占行、少隐藏', () => {
    // 10 个酶挤在 30 px 内（MCS）
    const inputs = Array.from({ length: 10 }, (_, i) => ({ id: `m${i}`, text: 'BamHI', x: 300 + i * 3, priority: 1 }))
    const strict = layoutLinearLabels(inputs, { width: 800, fontSize: 11, maxRows: 3, gap: 6 })
    const shifted = layoutLinearLabels(inputs, { width: 800, fontSize: 11, maxRows: 3, gap: 6, maxShift: 120 })
    expect(strict.hidden.length).toBe(7)
    expect(shifted.hidden.length).toBe(0)
    expect(shifted.rows).toBeLessThanOrEqual(3)
  })
})

describe('优先级与内置判定', () => {
  it('CDS > misc_feature；长者优先；单切点酶 > 多切点', () => {
    expect(featurePriority('cds', 100)).toBeGreaterThan(featurePriority('misc_feature', 5000))
    expect(featurePriority('promoter', 300)).toBeGreaterThan(featurePriority('promoter', 100))
    expect(enzymePriority(1)).toBeGreaterThan(enzymePriority(2))
    expect(enzymePriority(1)).toBeLessThan(featurePriority('promoter', 1))
    expect(enzymePriority(2)).toBeLessThan(featurePriority('misc_feature', 1))
  })
  it('fitsInside / 文本宽度', () => {
    expect(fitsInside('AmpR', 11, 200)).toBe(true)
    expect(fitsInside('AmpR', 11, 20)).toBe(false)
    expect(fitsInside('', 11, 200)).toBe(false)
    expect(textWidth('启动子', 10)).toBeGreaterThan(textWidth('abc', 10))
    expect(truncateText('rrnB T1 terminator', 40, 10).endsWith('…')).toBe(true)
    expect(truncateText('ab', 200, 10)).toBe('ab')
  })
})
