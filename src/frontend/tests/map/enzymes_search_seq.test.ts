import { describe, it, expect } from 'vitest'
import type { MapDocument, MapEnzyme, MapFeature } from '@/map/types'
import {
  listEnzymes, visibleEnzymes, defaultEnzymeFilter, enzymeStyle, cutMarks, recognitionLength,
  GOLDEN_GATE_ENZYMES, ENZYME_COLORS,
} from '@/map/core/enzymes'
import { searchSequence } from '@/map/core/search'
import {
  reverseComplement, sliceRange, translate, translateFeature, gcPercent, meltingTemp, selectionStats,
} from '@/map/core/seq'
import { visibleRows, basesPerRow, rowOf } from '@/map/core/virtual'
import { featureColor, darken, textOn } from '@/map/core/palette'
import pUC19 from '../fixtures/map/pUC19.json'
import { rng, randInt } from './rand'

const doc = pUC19 as unknown as MapDocument

function enz(name: string, cuts: number, ends: MapEnzyme['ends'] = "5'", rec = 'G^AATTC'): MapEnzyme {
  return {
    name, recognition: rec, ends, overhang: ends === 'blunt' ? null : 'AATT', cut_count: cuts,
    sites: Array.from({ length: cuts }, (_, i) => ({ position: 10 + i * 100, cut_top: 11 + i * 100, cut_bottom: 15 + i * 100, strand: 1 as const })),
  }
}

describe('酶切过滤与样式', () => {
  const all = [enz('EcoRI', 1), enz('PvuII', 2, 'blunt', 'CAG^CTG'), enz('PstI', 3, "3'", 'CTGCA^G'), enz('BsaI', 1, "5'", 'GGTCTCN^NNNN_N'), enz('Zero', 0)]

  it('按切点数过滤', () => {
    const f = defaultEnzymeFilter()
    expect(listEnzymes(all, f).map((e) => e.name)).toEqual(['BsaI', 'EcoRI', 'PvuII', 'PstI'])
    expect(listEnzymes(all, { ...f, maxCuts: 1 }).map((e) => e.name)).toEqual(['BsaI', 'EcoRI'])
    expect(listEnzymes(all, { ...f, maxCuts: 2 }).map((e) => e.name)).toEqual(['BsaI', 'EcoRI', 'PvuII'])
  })
  it('名称/识别序列搜索、单独隐藏、名单（Golden Gate）', () => {
    const f = defaultEnzymeFilter()
    expect(listEnzymes(all, { ...f, query: 'eco' }).map((e) => e.name)).toEqual(['EcoRI'])
    expect(listEnzymes(all, { ...f, query: 'cagctg' }).map((e) => e.name)).toEqual(['PvuII'])
    expect(listEnzymes(all, { ...f, hidden: ['EcoRI'] })).toHaveLength(4)
    expect(visibleEnzymes(all, { ...f, hidden: ['EcoRI'] }).map((e) => e.name)).not.toContain('EcoRI')
    expect(visibleEnzymes(all, { ...f, only: GOLDEN_GATE_ENZYMES }).map((e) => e.name)).toEqual(['BsaI'])
  })
  it('单切点加粗、平端独立颜色', () => {
    expect(enzymeStyle(all[0])).toMatchObject({ unique: true, weight: 700, color: ENZYME_COLORS.fivePrime })
    expect(enzymeStyle(all[1])).toMatchObject({ unique: false, blunt: true, color: ENZYME_COLORS.blunt })
    expect(enzymeStyle(all[2]).color).toBe(ENZYME_COLORS.threePrime)
  })
  it('切点展开排序、识别长度', () => {
    const marks = cutMarks(all)
    expect(marks.length).toBe(7)
    for (let i = 1; i < marks.length; i++) expect(marks[i].cut).toBeGreaterThanOrEqual(marks[i - 1].cut)
    expect(recognitionLength('G^AATTC')).toBe(6)
    expect(recognitionLength('GGTCTCN^NNNN_N')).toBe(6)
  })
  it('真实夹具：pUC19 MCS 单切点', () => {
    const uniq = listEnzymes(doc.enzymes, { ...defaultEnzymeFilter(), maxCuts: 1 }).map((e) => e.name)
    expect(uniq).toEqual(expect.arrayContaining(['EcoRI', 'BamHI', 'HindIII']))
  })
})

describe('序列搜索（错配 + 双链 + 环状）', () => {
  it('精确匹配：EcoRI 位点在 pUC19 的 396', () => {
    const res = searchSequence(doc.sequence, 'GAATTC', { circular: true })
    expect(res.hits.map((h) => h.start)).toEqual([396])
    expect(res.hits[0].strand).toBe(1) // 回文只报一次
  })
  it('反链命中', () => {
    const seq = 'AAAACCCGGGTTTTTTTT'
    const res = searchSequence(seq, 'AAAACC')
    expect(res.hits.some((h) => h.strand === 1 && h.start === 1)).toBe(true)
    const rc = searchSequence(seq, reverseComplement('CCCGGGT'))
    expect(rc.hits).toEqual([
      { start: 4, end: 10, strand: 1, mismatches: 0 },
      { start: 5, end: 11, strand: -1, mismatches: 0 },
    ])
  })
  it('跨原点命中仅在环状时成立', () => {
    const seq = 'GATTCAAAAAAAAAAAAGA' // GA|GATTC... 拼接成 GAGATTC
    expect(searchSequence(seq, 'GAGATT', { circular: true }).hits).toEqual([{ start: 18, end: 4, strand: 1, mismatches: 0 }])
    expect(searchSequence(seq, 'GAGATT', { circular: false }).hits).toEqual([])
  })
  it('错配数：与暴力解一致（属性测试）', () => {
    const r = rng(5)
    const B = 'ACGT'
    for (let round = 0; round < 40; round++) {
      const L = randInt(r, 20, 300)
      const seq = Array.from({ length: L }, () => B[randInt(r, 0, 3)]).join('')
      const m = randInt(r, 4, 9)
      const k = randInt(r, 0, 2)
      const q = Array.from({ length: m }, () => B[randInt(r, 0, 3)]).join('')
      const circular = round % 2 === 0
      const res = searchSequence(seq, q, { maxMismatches: k, circular })
      // 暴力
      const text = circular ? seq + seq.slice(0, m - 1) : seq
      const expected: string[] = []
      for (const [strand, p] of [[1, q], [-1, reverseComplement(q)]] as const) {
        if (strand === -1 && p === q) continue
        for (let i = 0; i + m <= text.length && i < seq.length; i++) {
          let mm = 0
          for (let j = 0; j < m; j++) if (text[i + j] !== p[j]) mm++
          if (mm <= k) expected.push(`${i + 1}:${strand}:${mm}`)
        }
      }
      expect(res.hits.map((h) => `${h.start}:${h.strand}:${h.mismatches}`).sort()).toEqual(expected.sort())
    }
  })
  it('IUPAC 查询与非法输入', () => {
    expect(searchSequence('ACGTAAGT', 'RCG').hits[0]).toMatchObject({ start: 1, mismatches: 0 })
    expect(searchSequence('ACGT', 'XYZ!').error).toBeTruthy()
    expect(searchSequence('ACGT', 'AC', { maxMismatches: 2 }).error).toBeTruthy()
    expect(searchSequence('ACGT', '').hits).toEqual([])
    const many = searchSequence('A'.repeat(500), 'A', { limit: 10 })
    expect(many.hits).toHaveLength(10)
    expect(many.truncated).toBe(true)
  })
  it('300 kb 搜索在合理时间内完成', () => {
    const r = rng(11)
    const seq = Array.from({ length: 300000 }, () => 'ACGT'[randInt(r, 0, 3)]).join('')
    const t0 = performance.now()
    searchSequence(seq, 'GATCGATCGATCGATCGATC', { maxMismatches: 2, circular: true })
    expect(performance.now() - t0).toBeLessThan(3000)
  })
})

describe('序列工具', () => {
  it('切片跨原点 / 互补 / 翻译', () => {
    expect(sliceRange('ABCDEFGH', { start: 7, end: 2 })).toBe('GHAB')
    expect(reverseComplement('ATGCN')).toBe('NGCAT')
    expect(translate('ATGGCCTAA')).toBe('MA*')
    expect(translate('ATGNNN')).toBe('MX')
  })
  it('CDS 翻译：正链、反链、跨原点、join 段', () => {
    const seq = 'ATGAAATAGCC'
    const f: MapFeature = { id: 'a', name: 'a', type: 'cds', start: 1, end: 9, strand: 1, source: 'file' }
    expect(translateFeature(f, seq)).toEqual({ protein: 'MK*', codonCenters: [2, 5, 8] })
    const rc = reverseComplement('ATGAAATAG') + 'CC'
    expect(translateFeature({ ...f, strand: -1 }, rc).protein).toBe('MK*')
    const wrapSeq = 'AAATAGCCCCATG' // ATG|AAATAG 跨原点
    expect(translateFeature({ ...f, start: 11, end: 6 }, wrapSeq).protein).toBe('MK*')
    const joined = 'ATGCCCAAATAG'
    expect(translateFeature({ ...f, start: 1, end: 12, segments: [{ start: 1, end: 3 }, { start: 7, end: 12 }] }, joined).protein).toBe('MK*')
  })
  it('GC% 与 Tm', () => {
    expect(gcPercent('GGCCAATT')).toBe(50)
    expect(meltingTemp('ATGC')).toBe(12) // Wallace
    const m13 = meltingTemp('AGCGGATAACAATTTCACACAGGA') // M13 rev，NN 约 58~64 °C
    expect(m13).toBeGreaterThan(55)
    expect(m13).toBeLessThan(66)
    const long = meltingTemp('ATGC'.repeat(30))
    expect(long).toBeGreaterThan(70)
    const st = selectionStats(doc.sequence, { start: 2680, end: 10 })
    expect(st.length).toBe(17)
  })
})

describe('虚拟滚动与配色', () => {
  it('visibleRows / basesPerRow / rowOf', () => {
    expect(visibleRows(0, 100, 20, 1000, 0)).toEqual([0, 5])
    expect(visibleRows(2000, 100, 20, 50, 2)).toEqual([43, 50])
    expect(basesPerRow(1000, 8, 60)).toBe(110)
    expect(basesPerRow(50, 8, 60)).toBe(10)
    expect(rowOf(1, 100)).toBe(0)
    expect(rowOf(101, 100)).toBe(1)
  })
  it('featureColor 优先文件色', () => {
    expect(featureColor({ type: 'cds', color: '#123456' })).toBe('#123456')
    expect(featureColor({ type: 'cds', color: 'red;x' })).not.toBe('red;x')
    expect(darken('#ffffff', 0.5)).toBe('#808080')
    expect(textOn('#ffffff')).toBe('#10131a')
    expect(textOn('#000000')).toBe('#ffffff')
  })
})
