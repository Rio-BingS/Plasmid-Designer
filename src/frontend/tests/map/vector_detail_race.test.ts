/** 载体详情页（审查 M5）：A→B 快速切换时 A 的慢响应不得覆盖 B；切换时清空出处 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'
import type { MapDocument } from '@/map/types'
import pUC19 from '../fixtures/map/pUC19.json'
import pET28 from '../fixtures/map/pET-28a.json'

const mapApi = vi.hoisted(() => ({
  getVectorMap: vi.fn(), getDesignMap: vi.fn(), getEnzymeSets: vi.fn(), analyzeMap: vi.fn(), exportGenbank: vi.fn(),
}))
vi.mock('@/map/api', () => mapApi)
const siteApi = vi.hoisted(() => ({ getVector: vi.fn(), getVectorSequence: vi.fn() }))
vi.mock('@/api', async (orig) => ({ ...(await orig<typeof import('@/api')>()), ...siteApi }))

import VectorDetailView from '@/views/VectorDetailView.vue'

function deferred<T>() {
  let resolve!: (v: T) => void
  let reject!: (e: unknown) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}
function router() {
  return createRouter({ history: createMemoryHistory(), routes: [
    { path: '/vectors/:id', component: VectorDetailView }, { path: '/:p(.*)*', component: { template: '<div/>' } },
  ] })
}
const info = (id: string, url: string) => ({
  id, name: id, vector_type: 'cloning', host: [], antibiotic_resistance: [], description: '', features: [], mcs_enzymes: [],
  source: 'common', copy_number: 'high', data_provenance: { source: id + '-src', url },
})

beforeEach(() => {
  vi.clearAllMocks()
  mapApi.getEnzymeSets.mockResolvedValue([])
})

describe('VectorDetailView 路由切换竞态', () => {
  it('A 的图谱与出处比 B 晚返回：页面仍显示 B', async () => {
    const mapA = deferred<MapDocument>()
    const infoA = deferred<unknown>()
    mapApi.getVectorMap.mockImplementation((id: string) => (id === 'A' ? mapA.promise : Promise.resolve(pET28)))
    siteApi.getVector.mockImplementation((id: string) => (id === 'A' ? infoA.promise : Promise.resolve(info('B', 'https://b.example/'))))
    const r = router()
    r.push('/vectors/A')
    await r.isReady()
    const w = mount(VectorDetailView, { global: { plugins: [r] } })
    await r.push('/vectors/B')
    await flushPromises()
    expect(w.find('h1').text()).toBe('pET-28a(+)')

    mapA.resolve(pUC19 as MapDocument)
    infoA.resolve(info('A', 'https://a.example/'))
    await flushPromises()
    expect(w.find('h1').text()).toBe('pET-28a(+)')
    expect(w.text()).toContain('B-src')
    expect(w.text()).not.toContain('A-src')
  })

  it('A 的请求失败晚于 B 成功：不显示 A 的错误', async () => {
    const mapA = deferred<MapDocument>()
    mapApi.getVectorMap.mockImplementation((id: string) => (id === 'A' ? mapA.promise : Promise.resolve(pET28)))
    siteApi.getVector.mockResolvedValue(info('B', 'https://b.example/'))
    const r = router()
    r.push('/vectors/A')
    await r.isReady()
    const w = mount(VectorDetailView, { global: { plugins: [r] } })
    await r.push('/vectors/B')
    await flushPromises()
    mapA.reject({ response: { data: { detail: '载体不存在' } } })
    await flushPromises()
    expect(w.find('.error-state').exists()).toBe(false)
    expect(w.find('h1').text()).toBe('pET-28a(+)')
  })

  it('切换后 B 的出处加载失败：不残留 A 的出处', async () => {
    mapApi.getVectorMap.mockImplementation((id: string) => Promise.resolve(id === 'A' ? pUC19 : pET28))
    siteApi.getVector.mockImplementation((id: string) => (id === 'A' ? Promise.resolve(info('A', 'https://a.example/')) : Promise.reject(new Error('x'))))
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    const r = router()
    r.push('/vectors/A')
    await r.isReady()
    const w = mount(VectorDetailView, { global: { plugins: [r] } })
    await flushPromises()
    expect(w.text()).toContain('A-src')
    await r.push('/vectors/B')
    await flushPromises()
    expect(w.text()).not.toContain('A-src')
    expect(w.find('.provenance').exists()).toBe(false)
  })
})
