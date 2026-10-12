/**
 * SnapGene 在线库页签的显示条件（v2.12 审查 H2，用户决定：默认关闭、仅登录用户可用）：
 * 后端 sources 下发 snapgene_online_enabled，且当前已登录，才显示 SnapGene 页签/选项，
 * 否则连集合列表都不去请求。后端仍是权威（关 → 404，匿名 → 401）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

const api = vi.hoisted(() => ({
  getVectors: vi.fn(),
  getOnlineSources: vi.fn(),
  getSnapgeneSets: vi.fn(),
  getSnapgeneSetIndex: vi.fn(),
}))
vi.mock('@/api', async (orig) => ({ ...(await orig<typeof import('@/api')>()), ...api }))

const mapApi = vi.hoisted(() => ({
  getEnzymeSets: vi.fn(),
  getOnlineSources: vi.fn(),
  getOnlineSnapgeneSets: vi.fn(),
  getOnlineSnapgeneIndex: vi.fn(),
}))
vi.mock('@/map/api', async (orig) => ({ ...(await orig<typeof import('@/map/api')>()), ...mapApi }))

import VectorsView from '@/views/VectorsView.vue'
import MapView from '@/views/MapView.vue'
import { useAuthStore } from '@/stores/auth'

function sources(enabled: boolean) {
  return {
    enabled: true, snapgene_online_enabled: enabled, disclaimer: 'd',
    sources: [
      { id: 'snapgene', name: 'SnapGene 在线质粒库', kind: 'online_library', url: '',
        license: 'SnapGene 许可', importable: enabled, enabled, requires_login: true, note: '' },
      { id: 'ensembl', name: 'Ensembl REST', kind: 'online_sequence', url: '',
        license: 'Ensembl 许可', importable: true, note: '' },
    ],
  }
}

function userPinia(isAdmin = false) {
  const pinia = createPinia()
  setActivePinia(pinia)
  useAuthStore().user = { id: 'u1', username: 'u', is_admin: isAdmin }
  return pinia
}

function anonPinia() {
  const pinia = createPinia()
  setActivePinia(pinia)
  return pinia
}

beforeEach(() => {
  vi.clearAllMocks()
  sessionStorage.clear()
  setActivePinia(undefined)
  api.getVectors.mockResolvedValue([])
  api.getSnapgeneSets.mockResolvedValue({ count: 1, sets: [{ set_id: 'yeast_plasmids', name: 'Yeast' }], license: '' })
  api.getSnapgeneSetIndex.mockResolvedValue({ set_id: 'yeast_plasmids', count: 0, plasmids: [] })
  mapApi.getEnzymeSets.mockResolvedValue([])
  mapApi.getOnlineSnapgeneSets.mockResolvedValue({ count: 1, sets: [{ set_id: 'yeast_plasmids', name: 'Yeast' }], license: '' })
  mapApi.getOnlineSnapgeneIndex.mockResolvedValue({ set_id: 'yeast_plasmids', count: 0, plasmids: [] })
})

const tabNames = (w: any) => w.findAll('.source-tab').map((b: any) => b.text())

describe('载体库页：SnapGene 在线库页签', () => {
  it('开关关闭：即使已登录也不显示，且不请求集合', async () => {
    api.getOnlineSources.mockResolvedValue(sources(false))
    const w = mount(VectorsView, { global: { plugins: [userPinia(true)], stubs: { RouterLink: true } } })
    await flushPromises()
    expect(tabNames(w)).not.toContain('SnapGene 在线库')
    expect(tabNames(w)).toContain('Ensembl')
    expect(api.getSnapgeneSets).not.toHaveBeenCalled()
  })

  it('开关打开但未登录：不显示', async () => {
    api.getOnlineSources.mockResolvedValue(sources(true))
    const w = mount(VectorsView, { global: { plugins: [anonPinia()], stubs: { RouterLink: true } } })
    await flushPromises()
    expect(tabNames(w)).not.toContain('SnapGene 在线库')
    expect(api.getSnapgeneSets).not.toHaveBeenCalled()
  })

  it('开关打开且已登录：显示，点开后加载集合', async () => {
    api.getOnlineSources.mockResolvedValue(sources(true))
    const w = mount(VectorsView, { global: { plugins: [userPinia()], stubs: { RouterLink: true } } })
    await flushPromises()
    const tab = w.findAll('.source-tab').find((b) => b.text() === 'SnapGene 在线库')
    expect(tab).toBeTruthy()
    await tab!.trigger('click')
    await flushPromises()
    expect(api.getSnapgeneSets).toHaveBeenCalledTimes(1)
    expect(w.text()).toContain('SnapGene 许可')
  })
})

async function openOnline(w: any) {
  const tab = w.findAll('.map-tab').find((b: any) => b.text() === '在线导入')
  await tab!.trigger('click')
  await flushPromises()
}

describe('图谱页「在线导入」：SnapGene 选项', () => {
  it('开关关闭：只有 Ensembl，不请求集合', async () => {
    mapApi.getOnlineSources.mockResolvedValue(sources(false))
    const w = mount(MapView, { global: { plugins: [userPinia()] } })
    await flushPromises()
    await openOnline(w)
    expect(w.find('.map-online-src').text()).not.toContain('SnapGene')
    expect(w.find('.map-online-src').text()).toContain('Ensembl')
    expect(w.find('input[aria-label="Ensembl ID"]').exists()).toBe(true)
    expect(mapApi.getOnlineSnapgeneSets).not.toHaveBeenCalled()
  })

  it('开关打开但未登录：不显示 SnapGene', async () => {
    mapApi.getOnlineSources.mockResolvedValue(sources(true))
    const w = mount(MapView, { global: { plugins: [anonPinia()] } })
    await flushPromises()
    await openOnline(w)
    expect(w.find('.map-online-src').text()).not.toContain('SnapGene')
    expect(mapApi.getOnlineSnapgeneSets).not.toHaveBeenCalled()
  })

  it('开关打开且已登录：可选 SnapGene，选中后才加载集合', async () => {
    mapApi.getOnlineSources.mockResolvedValue(sources(true))
    const w = mount(MapView, { global: { plugins: [userPinia()] } })
    await flushPromises()
    await openOnline(w)
    expect(w.find('.map-online-src').text()).toContain('SnapGene 在线库')
    expect(mapApi.getOnlineSnapgeneSets).not.toHaveBeenCalled()
    await w.find('.map-online-src input[value="snapgene"]').setValue(true)
    await flushPromises()
    expect(mapApi.getOnlineSnapgeneSets).toHaveBeenCalledTimes(1)
    expect(w.find('input[aria-label^="SnapGene 集合"]').exists()).toBe(true)
  })
})
