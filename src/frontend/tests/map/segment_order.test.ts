/**
 * segments 按生物学顺序（负链沿负链 5'→3'，坐标降序）时的前端取序列 / 翻译。
 * 夹具由后端 GenBank 解析器 + parsers.extract_feature 生成
 * （tests/fixtures/map/generate_segment_order.py），前后端须逐字一致。
 */
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import type { MapDocument, MapFeature } from '@/map/types'
import LinearMap from '@/map/components/LinearMap.vue'
import { headSegmentIndex } from '@/map/core/coords'
import { extractFeature, featurePositions, reverseComplement, translate, translateFeature } from '@/map/core/seq'
import fixture from '../fixtures/map/segment_order.json'

const SEQ: string = fixture.sequence

function feat(f: Partial<MapFeature>): MapFeature {
  return { id: 'x', name: 'x', type: 'cds', start: 1, end: 1, strand: 1, source: 'file', ...f } as MapFeature
}

describe('translateFeature：segments 生物学顺序', () => {
  for (const c of fixture.cases) {
    it(`与后端 extract_feature 一致：${c.name}（${c.location}）`, () => {
      const f = feat(c.feature as Partial<MapFeature>)
      expect(extractFeature(f, SEQ)).toBe(c.extract)
      expect(translateFeature(f, SEQ).protein).toBe(c.protein)
    })
  }

  it('complement(join(1..100,900..1000))：先 1000→900 再 100→1', () => {
    const f = feat({ start: 900, end: 100, strand: -1, segments: [{ start: 900, end: 1000 }, { start: 1, end: 100 }] })
    const pos = featurePositions(f, SEQ.length)
    expect(pos.slice(0, 2)).toEqual([1000, 999])
    expect(pos[100]).toBe(900)
    expect(pos[101]).toBe(100)
    expect(pos[pos.length - 1]).toBe(1)
    const expected = reverseComplement(SEQ.slice(0, 100) + SEQ.slice(899, 1000))
    expect(extractFeature(f, SEQ)).toBe(expected)
    expect(translateFeature(f, SEQ).protein).toBe(translate(expected))
  })

  it('负链单段跨原点：99→1 再 1000→901', () => {
    const f = feat({ start: 901, end: 99, strand: -1 })
    const expected = reverseComplement(SEQ.slice(900) + SEQ.slice(0, 99))
    expect(extractFeature(f, SEQ)).toBe(expected)
    const t = translateFeature(f, SEQ)
    expect(t.protein).toBe(translate(expected))
    expect(t.codonCenters[0]).toBe(98)
  })

  it('负链跨原点特征拆成两段（生物学顺序 1..99 在前）与单段写法结果相同', () => {
    const one = feat({ start: 901, end: 99, strand: -1 })
    const two = feat({ start: 901, end: 99, strand: -1, segments: [{ start: 1, end: 99 }, { start: 901, end: 1000 }] })
    expect(translateFeature(two, SEQ)).toEqual(translateFeature(one, SEQ))
  })

  it('codonCenters 沿负链递减并跨段衔接', () => {
    const f = feat({ start: 11, end: 199, strand: -1, segments: [{ start: 101, end: 199 }, { start: 11, end: 50 }] })
    const c = translateFeature(f, SEQ).codonCenters
    expect(c[0]).toBe(198)
    // 101..199 共 99 bp = 33 个密码子，第 34 个从 50 起
    expect(c[32]).toBe(102)
    expect(c[33]).toBe(49)
  })
})

describe('headSegmentIndex：箭头画在 3\' 端所在段', () => {
  it('负链 segments 生物学顺序（降序）：箭头在起点所在的末段', () => {
    const segs = [{ start: 101, end: 199 }, { start: 11, end: 50 }]
    expect(headSegmentIndex(segs, { start: 11, end: 199 }, -1)).toBe(1)
  })
  it('负链跨原点三段（1..50, 951..1000, 801..900）：箭头在 801..900', () => {
    const segs = [{ start: 1, end: 50 }, { start: 951, end: 1000 }, { start: 801, end: 900 }]
    expect(headSegmentIndex(segs, { start: 801, end: 50 }, -1)).toBe(2)
  })
  it('测序轨道按坐标升序给段时，负链箭头仍在起点段', () => {
    expect(headSegmentIndex([{ start: 900, end: 1000 }, { start: 1, end: 80 }], { start: 900, end: 80 }, -1)).toBe(0)
  })
  it('正链在终点段；无方向取末段', () => {
    const segs = [{ start: 801, end: 900 }, { start: 951, end: 1000 }, { start: 1, end: 50 }]
    expect(headSegmentIndex(segs, { start: 801, end: 50 }, 1)).toBe(2)
    expect(headSegmentIndex(segs, { start: 801, end: 50 }, 0)).toBe(2)
  })
})

describe('LinearMap：负链多段特征的箭头', () => {
  it('segments 按生物学顺序（降序）时箭头画在左侧（起点）段', () => {
    const doc: MapDocument = {
      schema: 'plasmid-map/1', name: 't', length: 1000, topology: 'linear', sequence: SEQ,
      features: [{ id: 'f1', name: 'cdsX', type: 'cds', start: 11, end: 199, strand: -1, source: 'file',
        segments: [{ start: 101, end: 199 }, { start: 11, end: 50 }] }],
      enzymes: [], orfs: [], primers: [], meta: { source_format: 'raw', enzyme_set: '', warnings: [] },
    } as MapDocument
    const w = mount(LinearMap, { props: { doc, width: 1000 } })
    const paths = w.findAll('[data-hit="f:f1"] path').map((p) => p.attributes('d') || '')
    const bars = paths.filter((d) => /^M[\d.]+ [\d.]+H[\d.]+L/.test(d))
    expect(bars.length).toBe(2)
    // 负链条：M x2 y H bx L x1 …；有箭头时 bx > x1
    const parsed = bars.map((d) => {
      const m = d.match(/^M([\d.]+) [\d.]+H([\d.]+)L([\d.]+)/)!
      return { x2: Number(m[1]), bx: Number(m[2]), x1: Number(m[3]) }
    }).sort((a, b) => a.x1 - b.x1)
    expect(parsed[0].bx).toBeGreaterThan(parsed[0].x1) // 11..50 带箭头
    expect(parsed[1].bx).toBe(parsed[1].x1) // 101..199 不带
    w.unmount()
  })
})
