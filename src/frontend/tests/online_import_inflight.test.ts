/** 在线导入防双击 + /map 页并发解析只认最后一次（审查 03-Low 2） */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import type { MapDocument } from '@/map/types'
import pUC19 from './fixtures/map/pUC19.json'
import pET28 from './fixtures/map/pET-28a.json'

const api = vi.hoisted(() => ({
  getVectors: vi.fn(), getOnlineSources: vi.fn(), importFromSnapgene: vi.fn(), importFromEnsembl: vi.fn(), importFromAddgene: vi.fn(),
}))
vi.mock('@/api', async (orig) => ({ ...(await orig<typeof import('@/api')>()), ...api }))
const mapApi = vi.hoisted(() => ({ getEnzymeSets: vi.fn(), parseMapFile: vi.fn(), analyzeMap: vi.fn(), exportGenbank: vi.fn() }))
vi.mock('@/map/api', async (orig) => ({ ...(await orig<typeof import('@/map/api')>()), ...mapApi }))

import VectorsView from '@/views/VectorsView.vue'
import MapView from '@/views/MapView.vue'

function deferred<T>() {
  let resolve!: (v: T) => void
  const promise = new Promise<T>((res) => { resolve = res })
  return { promise, resolve }
}
type S = Record<string, any>
const state = (w: VueWrapper) => (w.vm as unknown as { $: { setupState: S } }).$.setupState
const ok = { vector: { name: 'v', length: 100, features: 1 } }

beforeEach(() => {
  vi.clearAllMocks()
  sessionStorage.clear()
  setActivePinia(createPinia())
  api.getVectors.mockResolvedValue([])
  api.getOnlineSources.mockResolvedValue({ enabled: true, disclaimer: '', sources: [] })
  mapApi.getEnzymeSets.mockResolvedValue([])
})

describe('载体库在线导入在途锁', () => {
  async function mountV() {
    const w = mount(VectorsView, { global: { stubs: { RouterLink: true } } })
    await flushPromises()
    return state(w)
  }

  it('SnapGene 列表导入连点两次只发一次请求', async () => {
    const d = deferred<typeof ok>()
    api.importFromSnapgene.mockReturnValue(d.promise)
    const s = await mountV()
    s.sgSetId = 'set1'
    void s.importSnapgeneRow({ plasmid_id: 'p1' })
    void s.importSnapgeneRow({ plasmid_id: 'p1' })
    d.resolve(ok)
    await flushPromises()
    expect(api.importFromSnapgene).toHaveBeenCalledTimes(1)
    expect(s.onlineImporting).toBe(false)
  })

  it('预览弹窗"确认导入"连点两次只发一次请求', async () => {
    const d = deferred<typeof ok>()
    api.importFromEnsembl.mockReturnValue(d.promise)
    const s = await mountV()
    s.onlinePreview = { source: 'ensembl', id: 'ENST1', features: [] }
    void s.confirmOnlineImport()
    void s.confirmOnlineImport()
    d.resolve(ok)
    await flushPromises()
    expect(api.importFromEnsembl).toHaveBeenCalledTimes(1)
  })

  it('Addgene 导入连点两次只发一次请求', async () => {
    const d = deferred<typeof ok>()
    api.importFromAddgene.mockReturnValue(d.promise)
    const s = await mountV()
    void s.doAddgeneImport('123')
    void s.doAddgeneImport('123')
    d.resolve(ok)
    await flushPromises()
    expect(api.importFromAddgene).toHaveBeenCalledTimes(1)
  })
})

describe('/map 页并发解析', () => {
  it('解析中又拖入第二个文件：先发后到的第一个结果不覆盖第二个', async () => {
    const first = deferred<MapDocument>()
    const second = deferred<MapDocument>()
    mapApi.parseMapFile.mockImplementation((f: File) => (f.name === 'a.gb' ? first.promise : second.promise))
    const w = mount(MapView)
    await flushPromises()
    await w.find('.map-drop').trigger('drop', { dataTransfer: { files: [new File(['LOCUS a'], 'a.gb')] } })
    await w.find('.map-drop').trigger('drop', { dataTransfer: { files: [new File(['LOCUS b'], 'b.gb')] } })
    second.resolve(JSON.parse(JSON.stringify(pET28)))
    await flushPromises()
    expect(state(w).loading).toBe(false)
    first.resolve(JSON.parse(JSON.stringify(pUC19)))
    await flushPromises()
    expect(state(w).doc?.name).toBe('pET-28a(+)')
  })
})
