import { describe, it, expect } from 'vitest'
import { cdsCoverageSummary, cdsRelation, findCdsTarget } from '@/utils/cdsMode'
import type { SequencingAnalysis } from '@/api'

type Read = SequencingAnalysis['reads'][number]
type Feat = SequencingAnalysis['features'][number]

const target = { name: 'MX', start: 151, end: 1953 } // CDS 全长 1803bp

function mkRead(over: Partial<Read>): Read {
  return {
    index: 0, filename: 'r.ab1', raw_length: 1000, trimmed_length: 900,
    mean_q: 38, direction: '+', ref_start: 151, ref_end: 700,
    identity: 0.99, mixed_positions: [],
    ...over,
  } as Read
}

function mkFeat(over: Partial<Feat>): Feat {
  return { name: 'MX', type: 'CDS', start: 151, end: 1953, strand: '+', ...over } as Feat
}

describe('findCdsTarget', () => {
  it('识别伪 CDS 特征（description 以「目的基因模式」开头）', () => {
    const feats = [
      mkFeat({ name: '5′侧翼', type: 'misc_feature', start: 1, end: 150 }),
      mkFeat({ description: '目的基因模式：分析目标为「MX」（参考内 5075-6877）' }),
      mkFeat({ name: '3′侧翼', type: 'misc_feature', start: 1954, end: 2103 }),
    ]
    expect(findCdsTarget(feats)).toEqual({ name: 'MX', start: 151, end: 1953 })
  })

  it('机器标记 analysis_target 优先（与后端结论资格门同源）', () => {
    const feats = [mkFeat({ analysis_target: true, description: '' })]
    expect(findCdsTarget(feats)).toEqual({ name: 'MX', start: 151, end: 1953 })
    // 标记 false 时退回 description 判断
    expect(findCdsTarget([
      mkFeat({ analysis_target: false,
               description: '目的基因模式：分析目标为「MX」' }),
    ])).toEqual({ name: 'MX', start: 151, end: 1953 })
  })

  it('普通分析（无伪特征描述）→ null，列自动隐藏', () => {
    expect(findCdsTarget([mkFeat({ description: '目标基因，设计来源' })])).toBeNull()
    expect(findCdsTarget([mkFeat({ analysis_target: false })])).toBeNull()
    expect(findCdsTarget(undefined)).toBeNull()
  })
})

describe('cdsRelation', () => {
  it('完整覆盖：read 区间包住整个 CDS → 100%', () => {
    const rel = cdsRelation(mkRead({ ref_start: 100, ref_end: 2103 }), target)
    expect(rel.covers).toBe(true)
    expect(rel.percent).toBe(100)
    expect(rel.coveredBp).toBe(1803)
  })

  it('5′ 端部分覆盖：交集从 CDS 起点开始', () => {
    const rel = cdsRelation(mkRead({ ref_start: 100, ref_end: 700 }), target)
    expect(rel.covers).toBe(true)
    expect(rel.coveredBp).toBe(550) // 151..700
    expect(rel.overlapStart).toBe(151)
    expect(rel.percent).toBeCloseTo(550 / 1803 * 100, 5)
  })

  it('3′ 端部分覆盖', () => {
    const rel = cdsRelation(mkRead({ ref_start: 1800, ref_end: 2103 }), target)
    expect(rel.covers).toBe(true)
    expect(rel.coveredBp).toBe(154) // 1800..1953
    expect(rel.overlapEnd).toBe(1953)
  })

  it('完全不相交（纯侧翼/载体 read）→ 未覆盖', () => {
    const rel = cdsRelation(mkRead({ ref_start: 1, ref_end: 150 }), target)
    expect(rel.covers).toBe(false)
    expect(rel.percent).toBe(0)
    expect(rel.overlapStart).toBeNull()
  })

  it('恰好触碰 CDS 边界 1bp 也算覆盖', () => {
    expect(cdsRelation(mkRead({ ref_start: 1, ref_end: 151 }), target).coveredBp).toBe(1)
    expect(cdsRelation(mkRead({ ref_start: 1953, ref_end: 2103 }), target).coveredBp).toBe(1)
  })

  it('跨原点 read 用 ref_segments 展开（CDS 模式为线性参考，防御性覆盖）', () => {
    const rel = cdsRelation(
      mkRead({ ref_start: 1900, ref_end: 2203, wraps_origin: true,
               ref_segments: [[1900, 2203], [1, 100]] }),
      target)
    expect(rel.covers).toBe(true)
    expect(rel.coveredBp).toBe(54) // 仅 1900..1953 一段命中
    expect(rel.overlapStart).toBe(1900)
  })
})

describe('cdsCoverageSummary', () => {
  it('统计覆盖条数并点名目标', () => {
    const reads = [
      mkRead({ index: 0, ref_start: 151, ref_end: 700 }),
      mkRead({ index: 1, ref_start: 1, ref_end: 150 }),
      mkRead({ index: 2, ref_start: 1800, ref_end: 2103 }),
    ]
    expect(cdsCoverageSummary(reads, target)).toBe('2/3 条 read 覆盖目的 CDS「MX」')
  })
})
