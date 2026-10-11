import { describe, it, expect } from 'vitest'
import {
  normPos, rangeLength, splitRange, rangeContains, rangesOverlap, rangeMid, xToAngle, angleToX,
  polar, pointAngle, rangeFromBoundaries, unwrapNear, arcBandPath, barArrowPath, TAU,
} from '@/map/core/coords'
import { stackLanes } from '@/map/core/lanes'
import { niceStep, ticks, formatBp } from '@/map/core/ticks'
import { rng, randInt } from './rand'

describe('coords：跨原点数学', () => {
  it('normPos 折回', () => {
    expect(normPos(0, 100)).toBe(100)
    expect(normPos(101, 100)).toBe(1)
    expect(normPos(-1, 100)).toBe(99)
    expect(normPos(250, 100)).toBe(50)
  })

  it('rangeLength / splitRange', () => {
    expect(rangeLength({ start: 10, end: 20 }, 100)).toBe(11)
    expect(rangeLength({ start: 4800, end: 200 }, 5000)).toBe(401)
    expect(rangeLength({ start: 90, end: 10 }, 100, false)).toBe(0)
    expect(splitRange({ start: 4800, end: 200 }, 5000)).toEqual([{ start: 4800, end: 5000 }, { start: 1, end: 200 }])
  })

  it('contains / overlap 跨原点', () => {
    const w = { start: 95, end: 5 }
    expect(rangeContains(w, 100, 100)).toBe(true)
    expect(rangeContains(w, 3, 100)).toBe(true)
    expect(rangeContains(w, 50, 100)).toBe(false)
    expect(rangesOverlap(w, { start: 1, end: 2 }, 100)).toBe(true)
    expect(rangesOverlap(w, { start: 6, end: 94 }, 100)).toBe(false)
    expect(rangesOverlap({ start: 90, end: 10 }, { start: 99, end: 1 }, 100)).toBe(true)
  })

  it('rangeMid 跨原点落在原点附近', () => {
    expect(rangeMid({ start: 1, end: 10 }, 100)).toBe(5)
    expect(rangeMid({ start: 91, end: 10 }, 100)).toBe(0)
    expect(rangeMid({ start: 81, end: 10 }, 100)).toBe(95)
  })

  it('角度 ↔ 坐标 往返（含旋转）', () => {
    const r = rng(1)
    for (let i = 0; i < 200; i++) {
      const L = randInt(r, 10, 300000)
      const x = r() * L
      const rot = (r() - 0.5) * 20
      const back = angleToX(xToAngle(x, L, rot), L, rot)
      expect(Math.abs(back - x) < 1e-6 || Math.abs(Math.abs(back - x) - L) < 1e-6).toBe(true)
    }
  })

  it('polar / pointAngle：0 在上方顺时针', () => {
    const [x, y] = polar(0, 0, 10, Math.PI / 2)
    expect(x).toBeCloseTo(10)
    expect(y).toBeCloseTo(0)
    expect(pointAngle(0, 0, 0, -5)).toBeCloseTo(0)
    expect(pointAngle(0, 0, 5, 0)).toBeCloseTo(Math.PI / 2)
    expect(pointAngle(0, 0, -5, 0)).toBeCloseTo((3 * Math.PI) / 2)
  })

  it('拖拽选区：正向/反向/拖过原点/线性夹紧', () => {
    expect(rangeFromBoundaries(9, 20, 100, true)).toEqual({ start: 10, end: 20 })
    expect(rangeFromBoundaries(20, 9, 100, true)).toEqual({ start: 10, end: 20 })
    expect(rangeFromBoundaries(95, 105, 100, true)).toEqual({ start: 96, end: 5 })
    expect(rangeFromBoundaries(5, -5, 100, true)).toEqual({ start: 96, end: 5 })
    expect(rangeFromBoundaries(5, -5, 100, false)).toEqual({ start: 1, end: 5 })
    expect(rangeFromBoundaries(10, 10, 100, true)).toBeNull()
    expect(rangeFromBoundaries(0, 250, 100, true)).toEqual({ start: 1, end: 100 })
  })

  it('unwrapNear 让拖过原点连续', () => {
    expect(unwrapNear(2, 99, 100)).toBe(102)
    expect(unwrapNear(98, 1, 100)).toBe(-2)
    expect(unwrapNear(50, 55, 100)).toBe(50)
  })

  it('路径生成不出现 NaN', () => {
    for (const arrow of [0, 1, -1] as const) {
      expect(arcBandPath(0, 0, 10, 20, 0.1, 1, arrow, 0.2)).not.toMatch(/NaN/)
    }
    expect(arcBandPath(0, 0, 10, 20, 0, TAU)).toContain('Z')
    expect(arcBandPath(0, 0, 10, 20, 1, 1)).toBe('')
    expect(barArrowPath(0, 100, 0, 10, 1)).toMatch(/^M0 0H/)
    expect(barArrowPath(0, 100, 0, 10, 0)).not.toMatch(/L/)
  })
})

describe('lanes：分道无重叠（属性测试）', () => {
  it('同道任意两项不重叠（环状 + 线性，带 gap）', () => {
    const r = rng(42)
    for (let round = 0; round < 60; round++) {
      const L = randInt(r, 200, 20000)
      const circular = round % 2 === 0
      const gap = randInt(r, 0, 20)
      const items = Array.from({ length: randInt(r, 1, 40) }, (_, i) => {
        const len = randInt(r, 1, Math.floor(L / 3))
        const start = randInt(r, 1, L)
        const end = circular ? normPos(start + len - 1, L) : Math.min(L, start + len - 1)
        return { id: `i${i}`, start, end }
      })
      const res = stackLanes(items, L, { circular, gap })
      expect(res.lanes.size).toBe(items.length)
      for (const a of items) for (const b of items) {
        if (a.id >= b.id || res.lanes.get(a.id) !== res.lanes.get(b.id)) continue
        expect(rangesOverlap(a, b, L)).toBe(false)
      }
    }
  })

  it('跨原点项与原点两侧的项分道', () => {
    const res = stackLanes([
      { id: 'w', start: 90, end: 10 }, { id: 'a', start: 5, end: 20 }, { id: 'b', start: 30, end: 40 },
    ], 100)
    expect(res.lanes.get('w')).not.toBe(res.lanes.get('a'))
    expect(res.lanes.get('b')).toBe(0)
  })

  it('maxLanes 之外计入 overflow', () => {
    const items = Array.from({ length: 5 }, (_, i) => ({ id: `x${i}`, start: 1, end: 50 }))
    const res = stackLanes(items, 100, { maxLanes: 3 })
    expect(res.laneCount).toBe(3)
    expect(res.overflow).toHaveLength(2)
  })

  it('gap 在原点处同样生效', () => {
    const res = stackLanes([{ id: 'a', start: 1, end: 10 }, { id: 'b', start: 50, end: 98 }], 100, { gap: 6 })
    expect(res.lanes.get('a')).not.toBe(res.lanes.get('b'))
    const res2 = stackLanes([{ id: 'a', start: 1, end: 10 }, { id: 'b', start: 50, end: 98 }], 100, { gap: 0 })
    expect(res2.laneCount).toBe(1)
  })
})

describe('ticks', () => {
  it('好看步长', () => {
    expect(niceStep(2686, 8)).toBe(500)
    expect(niceStep(100, 10)).toBe(10)
    expect(niceStep(300000, 8)).toBe(50000)
    expect(niceStep(7, 10)).toBe(1)
  })
  it('刻度落在范围内且为步长整倍数', () => {
    const t = ticks(101, 2686, 8)
    expect(t.positions[0]).toBe(500)
    expect(t.positions.every((p) => p % t.step === 0 && p >= 101 && p <= 2686)).toBe(true)
  })
  it('formatBp', () => {
    expect(formatBp(2500)).toBe('2500')
    expect(formatBp(12500)).toBe('12.5k')
    expect(formatBp(1_200_000)).toBe('1.2M')
  })
})
