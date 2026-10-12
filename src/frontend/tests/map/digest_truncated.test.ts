/**
 * 位点明细被截断的酶（后端 sites_truncated / 总量封顶时清空明细）：
 * 酶切列表给出提示，消化面板禁用这类酶，避免少算切口得出错误片段；
 * 酶详情显示修饰依赖型标记。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import type { EnzymeDetail, MapDocument, MapEnzyme } from '@/map/types'
import pUC19 from '../fixtures/map/pUC19.json'
import { sitesIncomplete } from '@/map/core/digest'

const apiMocks = vi.hoisted(() => ({
  getEnzymeSets: vi.fn(), analyzeMap: vi.fn(), exportGenbank: vi.fn(),
  getSuppliers: vi.fn(), getEnzymeDetail: vi.fn(),
}))
vi.mock('@/map/api', async (orig) => ({ ...(await orig<typeof import('@/map/api')>()), ...apiMocks }))

import MapViewer from '@/map/components/MapViewer.vue'
import { clearEnzymeDetails } from '@/map/enzymeMeta'

const site = (p: number) => ({ position: p, cut_top: p, cut_bottom: p, strand: 1 as const })

/** MseI：900 个切点只给 3 个明细（按酶截断）；AluI：总量封顶时明细被清空 */
function withTruncated(): MapDocument {
  const d = JSON.parse(JSON.stringify(pUC19)) as MapDocument
  d.enzymes.push(
    { name: 'MseI', recognition: 'T^TAA', ends: "5'", overhang: 'TA', cut_count: 900, sites_truncated: true,
      sites: [site(100), site(500), site(1500)] },
    { name: 'AluI', recognition: 'AG^CT', ends: 'blunt', overhang: null, cut_count: 16, sites_truncated: true, sites: [] },
  )
  return d
}

async function openDigest(w: ReturnType<typeof mount>) {
  await w.findAll('.mv-tab').find((t) => t.text() === '消化')!.trigger('click')
}

beforeEach(() => {
  vi.clearAllMocks()
  clearEnzymeDetails()
  apiMocks.getEnzymeSets.mockResolvedValue([])
  apiMocks.getSuppliers.mockResolvedValue([])
})

describe('sitesIncomplete', () => {
  it('后端标记截断或明细少于计数时为 true', () => {
    const base: Pick<MapEnzyme, 'sites' | 'cut_count' | 'sites_truncated'> = { cut_count: 2, sites: [site(1), site(5)] }
    expect(sitesIncomplete(base)).toBe(false)
    expect(sitesIncomplete({ ...base, sites_truncated: true })).toBe(true)
    expect(sitesIncomplete({ ...base, sites: [site(1)] })).toBe(true)
    expect(sitesIncomplete({ cut_count: 7, sites: [] })).toBe(true)
  })
})

describe('消化面板：明细被截断的酶', () => {
  it('勾选框禁用并提示，不会算出错误片段；普通酶照常', async () => {
    const w = mount(MapViewer, { props: { doc: withTruncated() } })
    await flushPromises()
    await openDigest(w)
    for (const name of ['MseI', 'AluI']) {
      const box = w.find(`input[data-enzyme="${name}"]`)
      expect((box.element as HTMLInputElement).disabled).toBe(true)
      expect(box.element.closest('label')!.textContent).toContain('明细不全')
    }
    expect(w.find('.dg-truncated').text()).toContain('无法模拟消化')
    // 强行触发 change 也不选中
    await w.find('input[data-enzyme="AluI"]').trigger('change')
    expect(w.find('.dg-result').exists()).toBe(false)

    await w.find('input[data-enzyme="EcoRI"]').setValue(true)
    expect(w.find('.dg-summary').text()).toContain('EcoRI')
    expect(w.find('.dg-summary').text()).not.toContain('AluI')
  })

  it('已选的酶在重新分析后变成截断时被移出消化', async () => {
    const d = JSON.parse(JSON.stringify(pUC19)) as MapDocument
    const w = mount(MapViewer, { props: { doc: d } })
    await flushPromises()
    await openDigest(w)
    await w.find('input[data-enzyme="EcoRI"]').setValue(true)
    expect(w.findAll('.dg-frag').length).toBeGreaterThan(0)
    const d2 = JSON.parse(JSON.stringify(pUC19)) as MapDocument
    const eco = d2.enzymes.find((e) => e.name === 'EcoRI')!
    eco.sites_truncated = true
    await w.setProps({ doc: d2 })
    await flushPromises()
    expect(w.find('.dg-result').exists()).toBe(false)
    expect((w.find('input[data-enzyme="EcoRI"]').element as HTMLInputElement).disabled).toBe(true)
  })

  it('没有截断的酶时不显示提示', async () => {
    const w = mount(MapViewer, { props: { doc: JSON.parse(JSON.stringify(pUC19)) as MapDocument } })
    await flushPromises()
    await openDigest(w)
    expect(w.find('.dg-truncated').exists()).toBe(false)
  })
})

describe('酶切列表与酶详情', () => {
  it('截断的酶显示“明细不全”，其余不显示', async () => {
    const w = mount(MapViewer, { props: { doc: withTruncated() } })
    await flushPromises()
    await w.findAll('.mv-tabs button')[1].trigger('click')
    const row = (n: string) => w.findAll('.mv-enzyme-list .mv-row').find((r) => r.find('.mv-name').text() === n)!
    expect(row('MseI').find('.mv-truncated-badge').exists()).toBe(true)
    expect(row('MseI').find('.mv-truncated-badge').attributes('title')).toContain('3 个位点明细')
    expect(row('AluI').find('.mv-meta').text()).toContain('…')
    expect(row('EcoRI').find('.mv-truncated-badge').exists()).toBe(false)
  })

  it('修饰依赖型酶的详情带标记', async () => {
    const detail: EnzymeDetail = {
      name: 'MspJI', site: 'CNNR', recognition: 'CNNR(9/13)', ends: "5'", overhang: 'NNNN', type_iis: true,
      golden_gate: false, suppliers: [], isoschizomers: [], neoschizomers: [], modification_dependent: true,
    }
    apiMocks.getEnzymeDetail.mockResolvedValue(detail)
    const w = mount(MapViewer, { props: { doc: withTruncated(), allowAnalyze: true } })
    await flushPromises()
    await w.findAll('.mv-tabs button')[1].trigger('click')
    await flushPromises()
    await w.findAll('.mv-enzyme-list .mv-row').find((r) => r.find('.mv-name').text() === 'MseI')!.trigger('click')
    await flushPromises()
    expect(w.find('.mv-enz-detail .mv-badges').text()).toContain('修饰依赖型')
  })
})
