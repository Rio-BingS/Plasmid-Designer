/** utils/seqPanelModel 的单元测试：Sanger 融合视图纯函数模型 */

import { describe, expect, it } from 'vitest'
import {
  buildSeqCols, packLanes, readOverlaps, readSegments, readSpanText, shortName,
} from '@/utils/seqPanelModel'

const base = {
  trimmed_length: 6,
  direction: '+',
  alignment_view: {
    ref_start: 10,
    ref_aligned: 'ACGTAC',
    read_aligned: 'ACGTAC',
    q_aligned: [40, 40, 40, 40, 40, 40],
  },
}

describe('buildSeqCols', () => {
  it('正向 read：常规列落参考中心，xEnd 相邻衔接', () => {
    const cols = buildSeqCols(base)!
    expect(cols).toHaveLength(6)
    expect(cols[0]).toMatchObject({ ref: 'A', read: 'A', q: 40, mm: false, ins: false, refPos: 10, origIdx: 0 })
    expect(cols[0].xu).toBe(9.5)     // refPos 10 → 中心 9.5
    expect(cols[0].xEnd).toBe(10.5)  // 下一列 xu
    expect(cols[5].xu).toBe(14.5)
    expect(cols[5].xEnd).toBe(15.5)  // 末列 = xu + 1
  })

  it('替换列 mm=true；缺失列（read 缺口）origIdx=-1、mm=false', () => {
    const cols = buildSeqCols({
      ...base,
      alignment_view: { ...base.alignment_view, read_aligned: 'ACGAAC' },
    })!
    expect(cols[3]).toMatchObject({ ref: 'T', read: 'A', mm: true, ins: false, origIdx: 3 })

    const del = buildSeqCols({
      ...base,
      alignment_view: { ...base.alignment_view, read_aligned: 'AC-TAC' },
    })!
    expect(del[2]).toMatchObject({ ref: 'G', read: '-', ins: false, origIdx: -1 })
    expect(del[2].mm).toBe(false)
  })

  it('缺失列不推进 read 下标：后续 origIdx 连续', () => {
    const cols = buildSeqCols({
      ...base,
      alignment_view: { ...base.alignment_view, read_aligned: 'AC-TAC' },
    })!
    // read 碱基 A C T A C → origIdx 0,1,2,3,4；第 3 列（read 缺口）为 -1
    expect(cols.map((c) => c.origIdx)).toEqual([0, 1, -1, 2, 3, 4])
  })

  it('反向 read：origIdx 按 L-1-qi 镜像（与后端同式）', () => {
    const cols = buildSeqCols({ ...base, direction: '-' })!
    expect(cols.map((c) => c.origIdx)).toEqual([5, 4, 3, 2, 1, 0])
  })

  it('终审 A-20：软剪切偏移——正向 origIdx 从 query_start-1 起算', () => {
    // read 长 10，对齐块只覆盖原始 [5..10]（前 4bp junk 被软剪）
    const cols = buildSeqCols({
      ...base,
      trimmed_length: 10,
      query_start: 5,
      query_end: 10,
    })!
    expect(cols.map((c) => c.origIdx)).toEqual([4, 5, 6, 7, 8, 9])
  })

  it('终审 A-20：反向 read 对齐块第 0 列在原始 query_end 位置，向左递减', () => {
    const cols = buildSeqCols({
      ...base,
      direction: '-',
      trimmed_length: 10,
      query_start: 5,
      query_end: 10,
    })!
    expect(cols.map((c) => c.origIdx)).toEqual([9, 8, 7, 6, 5, 4])
  })

  it('无 query_start/end 的旧记录回退原行为（块首=1）', () => {
    const cols = buildSeqCols(base)!
    expect(cols.map((c) => c.origIdx)).toEqual([0, 1, 2, 3, 4, 5])
  })

  it('插入列（参考缺口）在左右两列之间等分插缝', () => {
    const cols = buildSeqCols({
      ...base,
      alignment_view: { ...base.alignment_view, ref_aligned: 'AC-GTAC', read_aligned: 'ACGGTAC' },
    })!
    expect(cols).toHaveLength(7)
    // ref_start=10：A→9.5、C→10.5，插入列在 C(10.5) 与 G(11.5) 之间 → 11.0
    expect(cols[1].xu).toBe(10.5)
    expect(cols[2].ins).toBe(true)
    expect(cols[2].xu).toBeCloseTo(11.0)
    expect(cols[2].origIdx).toBe(2)
    // 后续 read 碱基 origIdx 顺延 3,4,5,6
    expect(cols.slice(3).map((c) => c.origIdx)).toEqual([3, 4, 5, 6])
  })

  it('alignment_view 缺失返回 null', () => {
    expect(buildSeqCols({ ...base, alignment_view: null })).toBeNull()
  })
})

describe('packLanes', () => {
  it('重叠 read 分入不同泳道，不重叠复用同泳道', () => {
    const { lanes, laneOf } = packLanes([
      { ref_start: 100, ref_end: 300 },
      { ref_start: 200, ref_end: 400 },
      { ref_start: 500, ref_end: 600 },
      { ref_start: 350, ref_end: 450 },
    ])
    expect(laneOf).toEqual([0, 1, 0, 0])
    // 泳道内按装箱次序：按 ref_start 排序处理 0,1,3,2 → 3、2 依次推入泳道 0
    expect(lanes).toEqual([[0, 3, 2], [1]])
  })

  it('互不重叠的 read 共用同一条泳道（泳道内按装箱顺序）', () => {
    const { lanes, laneOf } = packLanes([
      { ref_start: 500, ref_end: 600 },
      { ref_start: 100, ref_end: 200 },
    ])
    expect(laneOf).toEqual([0, 0])
    expect(lanes).toEqual([[1, 0]])
  })

  it('无对齐 read（ref_end<=0）不上图，laneOf 保持 -1', () => {
    const { lanes, laneOf } = packLanes([
      { ref_start: 0, ref_end: 0 },
      { ref_start: 100, ref_end: 200 },
    ])
    expect(laneOf).toEqual([-1, 0])
    expect(lanes).toEqual([[1]])
  })

  it('空输入返回空泳道', () => {
    expect(packLanes([])).toEqual({ lanes: [], laneOf: [] })
  })
})

describe('shortName', () => {
  it('≤18 字符原样返回，>18 截断为前 17 位加省略号', () => {
    expect(shortName('r1.ab1')).toBe('r1.ab1')
    expect(shortName('S99680-M13F-75.ab1')).toHaveLength(18)  // 恰好 18 不截
    expect(shortName('S99680-M13F-75.ab1x')).toBe('S99680-M13F-75.ab…')  // 19 → 截断（17+…）
    expect(shortName('S99680-10855-1seqF1.ab1')).toBe('S99680-10855-1seq…')
  })
})

describe('环状参考跨原点 read', () => {
  // 参考长 20：read 落在 18..23（展开坐标），折回为 18-20 与 1-3
  const wrapped = {
    ref_start: 18, ref_end: 23, ref_segments: [[18, 20], [1, 3]] as [number, number][],
  }

  it('readSegments / readSpanText / readOverlaps 按折回区段', () => {
    expect(readSegments(wrapped)).toEqual([[18, 20], [1, 3]])
    expect(readSegments({ ref_start: 5, ref_end: 9 })).toEqual([[5, 9]])
    expect(readSegments({ ref_start: 0, ref_end: 0 })).toEqual([])
    expect(readSpanText(wrapped)).toBe('18–20、1–3')
    expect(readSpanText({ ref_start: 5, ref_end: 9 }, ' - ')).toBe('5 - 9')
    expect(readOverlaps(wrapped, 2, 2)).toBe(true)
    expect(readOverlaps(wrapped, 5, 15)).toBe(false)
  })

  it('buildSeqCols 传参考长度时折回原点后的列，xEnd 不跨折回点', () => {
    const cols = buildSeqCols({
      trimmed_length: 6, direction: '+',
      alignment_view: { ref_start: 18, ref_aligned: 'ACGTAC', read_aligned: 'ACGTAC' },
    }, 20)!
    expect(cols.map((c) => c.refPos)).toEqual([18, 19, 20, 1, 2, 3])
    expect(cols[2].xu).toBe(19.5)
    expect(cols[2].xEnd).toBe(20.5)   // 折回处按 1bp 宽
    expect(cols[3].xu).toBe(0.5)
    // 不传参考长度（线性）维持展开坐标
    const lin = buildSeqCols({
      trimmed_length: 6, direction: '+',
      alignment_view: { ref_start: 18, ref_aligned: 'ACGTAC', read_aligned: 'ACGTAC' },
    })!
    expect(lin.map((c) => c.refPos)).toEqual([18, 19, 20, 21, 22, 23])
  })

  it('原点处插入列落在左翼之后', () => {
    const cols = buildSeqCols({
      trimmed_length: 5, direction: '+',
      alignment_view: { ref_start: 19, ref_aligned: 'AC-GT', read_aligned: 'ACTGT' },
    }, 20)!
    expect(cols.map((c) => c.refPos)).toEqual([19, 20, 0, 1, 2])
    expect(cols[2].ins).toBe(true)
    expect(cols[2].xu).toBeGreaterThan(cols[1].xu)
  })

  it('packLanes：跨原点 read 与参考起点处的 read 不同道', () => {
    const { laneOf } = packLanes([
      wrapped,
      { ref_start: 2, ref_end: 8 },
      { ref_start: 10, ref_end: 15 },
    ])
    expect(laneOf[0]).not.toBe(laneOf[1])
    expect(laneOf[2]).toBe(0)
  })
})
