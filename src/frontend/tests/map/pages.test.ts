/** 结果页与载体详情页改用 MapViewer（MapDocument 契约） */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'
import pUC19 from '../fixtures/map/pUC19.json'

const mapApi = vi.hoisted(() => ({
  getVectorMap: vi.fn(), getDesignMap: vi.fn(), getEnzymeSets: vi.fn(), analyzeMap: vi.fn(), exportGenbank: vi.fn(),
}))
vi.mock('@/map/api', () => mapApi)
const siteApi = vi.hoisted(() => ({
  getVector: vi.fn(), getVectorSequence: vi.fn(), getDesign: vi.fn(), downloadGenbank: vi.fn(), downloadPrimers: vi.fn(),
}))
vi.mock('@/api', async (orig) => ({ ...(await orig<typeof import('@/api')>()), ...siteApi }))

import VectorDetailView from '@/views/VectorDetailView.vue'
import ResultView from '@/views/ResultView.vue'

function router() {
  return createRouter({ history: createMemoryHistory(), routes: [
    { path: '/vectors/:id', component: VectorDetailView }, { path: '/vectors', component: { template: '<div/>' } },
    { path: '/:p(.*)*', component: { template: '<div/>' } },
  ] })
}

beforeEach(() => {
  vi.clearAllMocks()
  mapApi.getEnzymeSets.mockResolvedValue([])
})

describe('VectorDetailView', () => {
  it('加载 MapDocument 并渲染查看器', async () => {
    mapApi.getVectorMap.mockResolvedValue(pUC19)
    siteApi.getVector.mockResolvedValue({ id: 'pUC19', name: 'pUC19', vector_type: 'cloning', host: ['E.coli'], antibiotic_resistance: ['Amp'], description: 'd', features: [], mcs_enzymes: [], source: 'common', copy_number: 'high' })
    const r = router()
    r.push('/vectors/pUC19')
    await r.isReady()
    const w = mount(VectorDetailView, { global: { plugins: [r] } })
    await flushPromises()
    expect(mapApi.getVectorMap).toHaveBeenCalledWith('pUC19')
    expect(w.find('h1').text()).toBe('pUC19')
    expect(w.find('.mv').exists()).toBe(true)
    expect(w.find('.cmap-svg').exists()).toBe(true)
    expect(w.text()).toContain('Amp')
  })

  it('加载失败显示后端中文 detail', async () => {
    mapApi.getVectorMap.mockRejectedValue({ response: { data: { detail: '载体不存在' } } })
    siteApi.getVector.mockRejectedValue(new Error('x'))
    const r = router()
    r.push('/vectors/nope')
    await r.isReady()
    const w = mount(VectorDetailView, { global: { plugins: [r] } })
    await flushPromises()
    expect(w.find('.error-state').text()).toContain('载体不存在')
  })
})

describe('ResultView', () => {
  it('点击"查看质粒图谱"后用 MapViewer 展示设计图谱', async () => {
    siteApi.getDesign.mockResolvedValue({ design_id: 'd1', status: 'completed', primers: [], warnings: [], errors: [], optimized_sequence: 'ATG', cloning_method: 'gibson', clone_protocol: '', validation_passed: true })
    mapApi.getDesignMap.mockResolvedValue({ ...pUC19, meta: { ...pUC19.meta, source_format: 'design' } })
    const r = router()
    const w = mount(ResultView, { props: { designId: 'd1' }, global: { plugins: [r] } })
    await flushPromises()
    const btn = w.findAll('button').find((b) => b.text().includes('查看质粒图谱'))!
    await btn.trigger('click')
    await flushPromises()
    expect(mapApi.getDesignMap).toHaveBeenCalledWith('d1')
    expect(w.find('.mv').exists()).toBe(true)
  })

  it('图谱加载失败给出提示', async () => {
    siteApi.getDesign.mockResolvedValue({ design_id: 'd1', status: 'completed', primers: [], warnings: [], errors: [], optimized_sequence: 'ATG', cloning_method: 'gibson', clone_protocol: '', validation_passed: true })
    mapApi.getDesignMap.mockRejectedValue({ response: { data: { detail: '设计不存在' } } })
    const w = mount(ResultView, { props: { designId: 'd1' }, global: { plugins: [router()] } })
    await flushPromises()
    await w.findAll('button').find((b) => b.text().includes('查看质粒图谱'))!.trigger('click')
    await flushPromises()
    expect(w.find('.map-error').text()).toContain('设计不存在')
    expect(w.find('.mv').exists()).toBe(false)
  })
})
