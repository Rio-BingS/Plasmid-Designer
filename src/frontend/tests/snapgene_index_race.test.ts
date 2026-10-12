/**
 * SnapGene 集合索引请求乱序（审查 M6）：/map 页与载体库页快速切换集合时，
 * 先发后到的旧集合响应不得覆盖当前集合的列表，也不得提前结束"加载中"。
 * 直接调用组件内的 loadSnapgeneIndex（不依赖标签页 UI，便于和标签页改动合并）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

const api = vi.hoisted(() => ({ getVectors: vi.fn(), getOnlineSources: vi.fn(), getSnapgeneSetIndex: vi.fn() }))
vi.mock('@/api', async (orig) => ({ ...(await orig<typeof import('@/api')>()), ...api }))
const mapApi = vi.hoisted(() => ({ getEnzymeSets: vi.fn(), getOnlineSnapgeneIndex: vi.fn() }))
vi.mock('@/map/api', async (orig) => ({ ...(await orig<typeof import('@/map/api')>()), ...mapApi }))

import VectorsView from '@/views/VectorsView.vue'
import MapView from '@/views/MapView.vue'

function deferred<T>() {
  let resolve!: (v: T) => void
  let reject!: (e: unknown) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}
type SG = { sgSetId: string; sgRows: { plasmid_id: string }[]; sgLoading: boolean; sgError: string; loadSnapgeneIndex: () => Promise<void> }
const state = (w: VueWrapper) => (w.vm as unknown as { $: { setupState: SG } }).$.setupState

beforeEach(() => {
  vi.clearAllMocks()
  sessionStorage.clear()
  setActivePinia(createPinia())
  api.getVectors.mockResolvedValue([])
  api.getOnlineSources.mockResolvedValue({ enabled: true, disclaimer: '', sources: [] })
  mapApi.getEnzymeSets.mockResolvedValue([])
})

const cases = [
  { name: 'MapView', comp: MapView, fn: () => mapApi.getOnlineSnapgeneIndex, opts: {} },
  { name: 'VectorsView', comp: VectorsView, fn: () => api.getSnapgeneSetIndex, opts: { global: { stubs: { RouterLink: true } } } },
] as const

describe.each(cases)('$name：集合索引请求守卫', ({ comp, fn, opts }) => {
  it('旧集合的响应晚到：不覆盖当前集合，加载态跟随最后一次请求', async () => {
    const a = deferred<{ plasmids: { plasmid_id: string }[] }>()
    const b = deferred<{ plasmids: { plasmid_id: string }[] }>()
    fn().mockImplementation((id: string) => (id === 'setA' ? a.promise : b.promise))
    const w = mount(comp as never, opts as never)
    await flushPromises()
    const s = state(w)
    s.sgSetId = 'setA'
    void s.loadSnapgeneIndex()
    s.sgSetId = 'setB'
    void s.loadSnapgeneIndex()

    b.resolve({ plasmids: [{ plasmid_id: 'b1' }] })
    await flushPromises()
    expect(s.sgRows.map((r) => r.plasmid_id)).toEqual(['b1'])
    expect(s.sgLoading).toBe(false)

    a.resolve({ plasmids: [{ plasmid_id: 'a1' }, { plasmid_id: 'a2' }] })
    await flushPromises()
    expect(s.sgRows.map((r) => r.plasmid_id)).toEqual(['b1'])
  })

  it('旧请求先结束：不提前把"加载中"置为 false，也不显示旧集合的错误', async () => {
    const a = deferred<{ plasmids: { plasmid_id: string }[] }>()
    const b = deferred<{ plasmids: { plasmid_id: string }[] }>()
    fn().mockImplementation((id: string) => (id === 'setA' ? a.promise : b.promise))
    const w = mount(comp as never, opts as never)
    await flushPromises()
    const s = state(w)
    s.sgSetId = 'setA'
    void s.loadSnapgeneIndex()
    s.sgSetId = 'setB'
    void s.loadSnapgeneIndex()
    a.reject({ response: { status: 502, data: { detail: '上游失败' } } })
    await flushPromises()
    expect(s.sgLoading).toBe(true)
    expect(s.sgError).toBe('')
    b.resolve({ plasmids: [{ plasmid_id: 'b1' }] })
    await flushPromises()
    expect(s.sgLoading).toBe(false)
    expect(s.sgRows.map((r) => r.plasmid_id)).toEqual(['b1'])
  })
})
