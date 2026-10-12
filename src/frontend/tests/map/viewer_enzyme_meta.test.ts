/** MapViewer 酶切面板：厂商过滤、酶详情；/map 页 NCBI accession 导入 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import type { EnzymeDetail, MapDocument } from '@/map/types'
import pUC19 from '../fixtures/map/pUC19.json'

const apiMocks = vi.hoisted(() => ({
  getEnzymeSets: vi.fn(), analyzeMap: vi.fn(), exportGenbank: vi.fn(),
  getSuppliers: vi.fn(), getEnzymeDetail: vi.fn(), importNcbi: vi.fn(),
  parseMapFile: vi.fn(), parseMapText: vi.fn(), getVectorMap: vi.fn(),
}))
vi.mock('@/map/api', async (orig) => ({ ...(await orig<typeof import('@/map/api')>()), ...apiMocks }))

import MapViewer from '@/map/components/MapViewer.vue'
import MapView from '@/views/MapView.vue'
import { resolveHit } from '@/map/hits'
import { clearEnzymeDetails, rememberEnzymeDetail } from '@/map/enzymeMeta'

const puc = () => JSON.parse(JSON.stringify(pUC19)) as MapDocument

const BSAI: EnzymeDetail = {
  name: 'BsaI', site: 'GGTCTC', recognition: 'GGTCTC(1/5)', ends: "5'", overhang: 'NNNN',
  type_iis: true, golden_gate: true, suppliers: [{ code: 'N', name: 'New England Biolabs' }],
  isoschizomers: ['Bso31I', 'BspTNI', 'Eco31I'], neoschizomers: [], methylation: ['dcm'],
}

beforeEach(() => {
  vi.clearAllMocks()
  sessionStorage.clear()
  clearEnzymeDetails()
  apiMocks.getEnzymeSets.mockResolvedValue([{ id: 'common', name: '常用酶', description: '', enzymes: [] }])
  apiMocks.getSuppliers.mockResolvedValue([
    { code: 'N', name: 'New England Biolabs', enzymes: ['BsaI', 'EcoRI', 'BamHI'] },
    { code: 'B', name: 'Thermo Fisher Scientific', enzymes: ['EcoRI', 'Eco31I'] },
  ])
  apiMocks.getEnzymeDetail.mockResolvedValue(BSAI)
})

const rowNames = (w: ReturnType<typeof mount>) => w.findAll('.mv-enzyme-list .mv-row').map((r) => r.find('.mv-name').text())

describe('MapViewer 酶切面板：厂商与详情', () => {
  it('打开酶切面板才取厂商表；选厂商只留其可供的酶', async () => {
    const w = mount(MapViewer, { props: { doc: puc(), initialView: 'circular' } })
    await flushPromises()
    expect(apiMocks.getSuppliers).not.toHaveBeenCalled()
    await w.findAll('.mv-tabs button')[1].trigger('click')
    await flushPromises()
    expect(apiMocks.getSuppliers).toHaveBeenCalledTimes(1)
    const sel = w.find('select[aria-label="按厂商筛选"]')
    expect(sel.findAll('option').map((o) => o.text())).toEqual(['全部厂商', 'NEB（3）', 'Thermo（2）'])
    await sel.setValue('N')
    expect(rowNames(w).sort()).toEqual(['BamHI', 'BsaI', 'EcoRI'])
    await sel.setValue('B')
    expect(rowNames(w)).toEqual(['EcoRI'])
    await sel.setValue('')
    expect(rowNames(w).length).toBe(puc().enzymes.length)
  })

  it('厂商在当前酶组里没有切点时给出提示', async () => {
    apiMocks.getSuppliers.mockResolvedValue([{ code: 'E', name: 'Agilent Technologies', enzymes: ['NoSuch'] }])
    const w = mount(MapViewer, { props: { doc: puc(), initialView: 'circular' } })
    await flushPromises()
    await w.findAll('.mv-tabs button')[1].trigger('click')
    await flushPromises()
    await w.find('select[aria-label="按厂商筛选"]').setValue('E')
    expect(w.find('.mv-enzyme-list').text()).toContain('全部商品酶')
  })

  it('点酶打开详情：Type IIS、Golden Gate、厂商、同切酶、甲基化', async () => {
    const w = mount(MapViewer, { props: { doc: puc(), initialView: 'circular' } })
    await flushPromises()
    await w.findAll('.mv-tabs button')[1].trigger('click')
    await flushPromises()
    const row = w.findAll('.mv-enzyme-list .mv-row').find((r) => r.find('.mv-name').text() === 'BsaI')!
    await row.trigger('click')
    await flushPromises()
    expect(apiMocks.getEnzymeDetail).toHaveBeenCalledWith('BsaI')
    const box = w.find('.mv-enz-detail')
    expect(box.text()).toContain('GGTCTC(1/5)')
    expect(box.text()).toContain('Type IIS')
    expect(box.text()).toContain('可用于 Golden Gate')
    expect(box.text()).toContain('New England Biolabs')
    expect(box.text()).toContain('Eco31I')
    expect(box.text()).toContain('dcm')
    expect(box.text()).toContain('REBASE')
    await box.find('button[aria-label="关闭酶详情"]').trigger('click')
    expect(w.find('.mv-enz-detail').exists()).toBe(false)
  })

  it('详情加载失败显示错误', async () => {
    apiMocks.getEnzymeDetail.mockRejectedValue({ response: { status: 404, data: { detail: '未知或不支持的酶：X' } } })
    const w = mount(MapViewer, { props: { doc: puc(), initialView: 'circular' } })
    await flushPromises()
    await w.findAll('.mv-tabs button')[1].trigger('click')
    await w.findAll('.mv-enzyme-list .mv-row')[0].trigger('click')
    await flushPromises()
    expect(w.find('.mv-enz-detail .mv-error').text()).toContain('酶详情加载失败')
  })

  it('只读/离线查看器（allowAnalyze=false）不请求厂商与详情', async () => {
    const w = mount(MapViewer, { props: { doc: puc(), initialView: 'circular', allowAnalyze: false } })
    await flushPromises()
    await w.findAll('.mv-tabs button')[1].trigger('click')
    await w.findAll('.mv-enzyme-list .mv-row')[0].trigger('click')
    await flushPromises()
    expect(apiMocks.getSuppliers).not.toHaveBeenCalled()
    expect(apiMocks.getEnzymeDetail).not.toHaveBeenCalled()
  })

  it('悬停提示附带已取到的酶详情', async () => {
    const w = mount(MapViewer, { props: { doc: puc(), initialView: 'circular' } })
    await flushPromises()
    const store = (w.vm as unknown as { store: Parameters<typeof resolveHit>[0] }).store
    expect(resolveHit(store, 'e:BsaI#0')!.lines.join('\n')).not.toContain('厂商')
    rememberEnzymeDetail(BSAI)
    const lines = resolveHit(store, 'e:BsaI#0')!.lines
    expect(lines).toContain('Type IIS · 可用于 Golden Gate')
    expect(lines).toContain('厂商：NEB')
  })
})

describe('/map 页：NCBI 导入', () => {
  it('输入 accession → importNcbi（带酶组/注释选项）→ 查看器', async () => {
    apiMocks.importNcbi.mockResolvedValue(puc())
    const w = mount(MapView)
    await flushPromises()
    await w.findAll('.map-tab')[2].trigger('click')
    await w.find('select[aria-label="酶组"]').setValue('common')
    await w.find('select[aria-label="自动注释"]').setValue('off')
    await w.find('input[aria-label="NCBI accession"]').setValue(' m77789.1 ')
    await w.find('.map-ncbi-open').trigger('click')
    await flushPromises()
    expect(apiMocks.importNcbi).toHaveBeenCalledWith({ accession: 'M77789.1', enzyme_set: 'common', annotate: false })
    expect(w.find('.mv').exists()).toBe(true)
  })

  it('格式不对：本地提示，不发请求；后端 404 显示中文', async () => {
    const w = mount(MapView)
    await flushPromises()
    await w.findAll('.map-tab')[2].trigger('click')
    await w.find('input[aria-label="NCBI accession"]').setValue('pUC19')
    expect(w.find('.map-ncbi-hint').exists()).toBe(true)
    await w.find('input[aria-label="NCBI accession"]').trigger('keydown', { key: 'Enter' })
    await flushPromises()
    expect(w.find('.map-error').text()).toContain('accession 格式无效')
    expect(apiMocks.importNcbi).not.toHaveBeenCalled()

    apiMocks.importNcbi.mockRejectedValue({ response: { status: 404, data: { detail: 'NCBI 中未找到 accession X99999' } } })
    await w.find('input[aria-label="NCBI accession"]').setValue('X99999')
    await w.find('.map-ncbi-open').trigger('click')
    await flushPromises()
    expect(w.find('.map-error').text()).toContain('NCBI 中未找到 accession X99999')
  })
})
