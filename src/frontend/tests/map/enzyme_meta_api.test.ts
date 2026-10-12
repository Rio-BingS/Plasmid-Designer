/** 酶详情 / 厂商表 / NCBI 导入的 API 封装与纯函数 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))
vi.mock('axios', () => ({
  default: {
    create: vi.fn(() => ({
      ...mocks,
      interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
    })),
  },
}))

import {
  _resetEnzymeMetaCache, describeMapError, getEnzymeDetail, getSuppliers, importNcbi, NCBI_ACCESSION_RE,
  normalizeAccession,
} from '@/map/api'
import { cachedEnzymeDetail, enzymeDetailLines, supplierShortName } from '@/map/enzymeMeta'
import { defaultEnzymeFilter, listEnzymes } from '@/map/core/enzymes'
import type { EnzymeDetail, MapEnzyme } from '@/map/types'

const BSAI: EnzymeDetail = {
  name: 'BsaI', site: 'GGTCTC', recognition: 'GGTCTC(1/5)', ends: "5'", overhang: 'NNNN',
  type_iis: true, golden_gate: true, suppliers: [{ code: 'N', name: 'New England Biolabs' }],
  isoschizomers: ['Bso31I', 'BspTNI', 'Eco31I'], neoschizomers: [], methylation: ['dcm'],
}

beforeEach(() => {
  vi.clearAllMocks()
  _resetEnzymeMetaCache()
})

describe('酶详情与厂商表', () => {
  it('getEnzymeDetail：编码路径、会话内缓存、写入同步缓存', async () => {
    mocks.get.mockResolvedValue({ data: BSAI })
    expect(cachedEnzymeDetail('BsaI')).toBeUndefined()
    const [a, b] = await Promise.all([getEnzymeDetail('BsaI'), getEnzymeDetail('BsaI')])
    expect(a).toEqual(BSAI)
    expect(b).toBe(a)
    await getEnzymeDetail('BsaI')
    expect(mocks.get).toHaveBeenCalledTimes(1)
    expect(mocks.get.mock.calls[0][0]).toBe('/map/enzymes/BsaI')
    expect(cachedEnzymeDetail('BsaI')).toEqual(BSAI)
  })

  it('getEnzymeDetail：失败不缓存，下次重试', async () => {
    mocks.get.mockRejectedValueOnce(new Error('boom')).mockResolvedValueOnce({ data: BSAI })
    await expect(getEnzymeDetail('BsaI')).rejects.toThrow('boom')
    expect(await getEnzymeDetail('BsaI')).toEqual(BSAI)
    expect(mocks.get).toHaveBeenCalledTimes(2)
  })

  it('getSuppliers 只请求一次', async () => {
    mocks.get.mockResolvedValue({ data: [{ code: 'N', name: 'New England Biolabs', enzymes: ['BsaI'] }] })
    await getSuppliers()
    await getSuppliers()
    expect(mocks.get).toHaveBeenCalledTimes(1)
    expect(mocks.get.mock.calls[0][0]).toBe('/map/suppliers')
  })

  it('悬停附加行：Type IIS/Golden Gate 标记与厂商摘要（缩写）', () => {
    expect(enzymeDetailLines(BSAI)).toEqual(['Type IIS · 可用于 Golden Gate', '厂商：NEB'])
    const many: EnzymeDetail = {
      ...BSAI, type_iis: false, golden_gate: false,
      suppliers: ['B', 'C', 'I', 'J', 'N', 'Z'].map((code) => ({ code, name: `Name ${code}` })),
    }
    expect(enzymeDetailLines(many)).toEqual(['厂商：Thermo、Minotech、SibEnzyme、Nippon Gene 等 6 家'])
    expect(supplierShortName({ code: 'Z', name: 'Zeta Bio' })).toBe('Zeta Bio')
  })

  it('厂商过滤：只留名单内的酶，与其他条件叠加', () => {
    const mk = (name: string, n: number): MapEnzyme => ({
      name, recognition: 'GAATTC', ends: "5'", overhang: 'AATT', cut_count: n,
      sites: Array.from({ length: n }, (_, i) => ({ position: i + 1, cut_top: i + 1, cut_bottom: i + 5, strand: 1 as const })),
    })
    const all = [mk('EcoRI', 1), mk('Eco31I', 1), mk('BsaI', 2)]
    const f = { ...defaultEnzymeFilter(), supplier: ['bsai', 'EcoRI'] }
    expect(listEnzymes(all, f).map((e) => e.name)).toEqual(['EcoRI', 'BsaI'])
    expect(listEnzymes(all, { ...f, maxCuts: 1 }).map((e) => e.name)).toEqual(['EcoRI'])
    expect(listEnzymes(all, defaultEnzymeFilter())).toHaveLength(3)
  })
})

describe('NCBI 导入', () => {
  it('importNcbi POST JSON', async () => {
    mocks.post.mockResolvedValue({ data: { ok: 1 } })
    expect(await importNcbi({ accession: 'L09137', enzyme_set: 'common' })).toEqual({ ok: 1 })
    expect(mocks.post).toHaveBeenCalledWith('/map/import/ncbi', { accession: 'L09137', enzyme_set: 'common' })
  })

  it.each([
    ['L09137', true], ['M77789.1', true], ['NC_005213.1', true], ['NZ_CP009072.1', true],
    ['pUC19', false], ['12345', false], ['L09137;x', false],
  ])('accession 格式 %s → %s', (raw, ok) => {
    expect(NCBI_ACCESSION_RE.test(normalizeAccession(raw))).toBe(ok)
  })

  it('normalizeAccession 去空白转大写', () => {
    expect(normalizeAccession(' m77789 .1 ')).toBe('M77789.1')
  })

  it.each([
    [404, 'NCBI 中未找到 accession X99999', 'NCBI 中未找到 accession X99999'],
    [504, '连接 NCBI 超时，请稍后再试', '连接 NCBI 超时'],
    [503, 'NCBI 请求过于频繁，请稍后再试', 'NCBI 请求过于频繁'],
    [422, 'NCBI accession 格式无效：pUC19', 'NCBI accession 格式无效'],
  ])('describeMapError %i 直接给后端中文 detail', (status, detail, text) => {
    const msg = describeMapError({ response: { status, data: { detail } } })
    expect(msg).toContain(text)
    expect(msg).not.toContain('序列无法解析')
  })
})
