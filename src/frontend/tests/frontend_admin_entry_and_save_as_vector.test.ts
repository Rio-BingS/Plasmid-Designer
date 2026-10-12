/**
 * 管理员写入口 + 图谱页「另存为载体」：
 * 载体库是共享库，导入/上传/编辑/删除都需管理员——非管理员不显示这些入口
 * （点了也只会 403）；后端 403 仍是最外层防线，这里只保证界面诚实。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import type { MapDocument } from '@/map/types'
import pUC19 from './fixtures/map/pUC19.json'
import { saveMapSession } from '@/map/session'

const api = vi.hoisted(() => ({
  getVectors: vi.fn(),
  getOnlineSources: vi.fn(),
  saveMapAsVector: vi.fn(),
}))
vi.mock('@/api', async (orig) => ({ ...(await orig<typeof import('@/api')>()), ...api }))

const mapApi = vi.hoisted(() => ({ getEnzymeSets: vi.fn() }))
vi.mock('@/map/api', async (orig) => ({ ...(await orig<typeof import('@/map/api')>()), ...mapApi }))

import VectorsView from '@/views/VectorsView.vue'
import MapView from '@/views/MapView.vue'
import { useAuthStore } from '@/stores/auth'

function adminPinia() {
  const pinia = createPinia()
  setActivePinia(pinia)
  useAuthStore().user = { id: 'u1', username: 'admin', is_admin: true }
  return pinia
}

const puc = () => JSON.parse(JSON.stringify(pUC19)) as MapDocument

beforeEach(() => {
  vi.clearAllMocks()
  sessionStorage.clear()
  setActivePinia(undefined)
  api.getVectors.mockResolvedValue([{
    id: 'pUC19', name: 'pUC19', source: 'common', vector_type: 'cloning',
    host: [], antibiotic_resistance: [], copy_number: 'high', description: '',
    features: [], mcs_enzymes: [],
  }])
  api.getOnlineSources.mockResolvedValue({
    enabled: true, disclaimer: 'd',
    sources: [{
      id: 'addgene', name: 'Addgene', kind: 'online_library', url: '',
      license: 'Addgene 许可', importable: false, note: '',
      requires_login: true, session_configured: false,
    }],
  })
  mapApi.getEnzymeSets.mockResolvedValue([])
})

describe('载体库的写入口按管理员显示', () => {
  it('非管理员：没有上传/批量导入/编辑/删除，只留下载与提示', async () => {
    const w = mount(VectorsView, { global: { stubs: { RouterLink: true } } })
    await flushPromises()
    expect(w.findAll('.vector-card')).toHaveLength(1)
    expect(w.text()).toContain('导入需管理员')
    expect(w.text()).not.toContain('上传文件')
    expect(w.text()).not.toContain('批量导入')
    expect(w.find('button[title^="编辑"]').exists()).toBe(false)
    expect(w.find('button[title^="删除"]').exists()).toBe(false)
    expect(w.find('button[title="下载 FASTA"]').exists()).toBe(true)
  })

  it('管理员：上传/批量导入/编辑/删除都出现', async () => {
    const pinia = adminPinia()
    const w = mount(VectorsView, { global: { plugins: [pinia], stubs: { RouterLink: true } } })
    await flushPromises()
    expect(w.text()).toContain('上传文件')
    expect(w.text()).toContain('批量导入')
    expect(w.find('button[title^="编辑"]').exists()).toBe(true)
    expect(w.find('button[title^="删除"]').exists()).toBe(true)
    expect(w.text()).not.toContain('导入需管理员')
  })

  it('Addgene：未配置会话时提示自备凭据而不是给一个必然失败的按钮', async () => {
    const pinia = adminPinia()
    const w = mount(VectorsView, { global: { plugins: [pinia], stubs: { RouterLink: true } } })
    await flushPromises()
    await w.findAll('.source-tab').find((b) => b.text() === 'Addgene')!.trigger('click')
    await flushPromises()
    expect(w.text()).toContain('ADDGENE_SESSION_COOKIE')
    expect(w.text()).not.toContain('用我的登录态导入为本地载体')
  })

  it('Addgene：配置了会话且是管理员时给出导入按钮', async () => {
    const pinia = adminPinia()
    api.getOnlineSources.mockResolvedValue({
      enabled: true, disclaimer: 'd',
      sources: [{
        id: 'addgene', name: 'Addgene', kind: 'online_library', url: '',
        license: 'Addgene 许可', importable: true, note: '已配置 ADDGENE_SESSION_COOKIE',
        requires_login: true, session_configured: true,
      }],
    })
    const w = mount(VectorsView, { global: { plugins: [pinia], stubs: { RouterLink: true } } })
    await flushPromises()
    await w.findAll('.source-tab').find((b) => b.text() === 'Addgene')!.trigger('click')
    await flushPromises()
    expect(w.text()).toContain('用我的登录态导入为本地载体')
  })

  it('UniProt 页签：只做反查，列出可去的核酸来源并给出去 NCBI 的入口', async () => {
    const pinia = adminPinia()
    const w = mount(VectorsView, { global: { plugins: [pinia], stubs: { RouterLink: true } } })
    await flushPromises()
    await w.findAll('.source-tab').find((b) => b.text() === 'UniProt')!.trigger('click')
    expect(w.text()).toContain('UniProt')
    expect(w.find('input[placeholder^="基因/蛋白名"]').exists()).toBe(true)
  })
})

describe('图谱页「另存为载体」', () => {
  it('非管理员：不显示入口', async () => {
    saveMapSession(puc())
    const w = mount(MapView)
    await flushPromises()
    expect(w.text()).not.toContain('另存为载体')
  })

  it('管理员：把当前文档（含编辑后的特征）提交给后端', async () => {
    const pinia = adminPinia()
    const doc = puc()
    // 模拟用户在图谱编辑器里改过：改一个特征名
    doc.features[0].name = 'editedFeature'
    saveMapSession(doc)
    api.saveMapAsVector.mockResolvedValue({
      vector: { id: 'vector_pUC19', name: 'pUC19', length: doc.length, features: doc.features.length },
    })

    const w = mount(MapView, { global: { plugins: [pinia] } })
    await flushPromises()
    const open = w.findAll('button').find((b) => b.text().includes('另存为载体'))!
    expect(open).toBeTruthy()
    await open.trigger('click')
    await w.find('input[aria-label="载体名称"]').setValue('myVector')
    await w.findAll('button').find((b) => b.text() === '存入载体库')!.trigger('click')
    await flushPromises()

    const payload = api.saveMapAsVector.mock.calls[0][0]
    expect(payload.name).toBe('myVector')
    expect(payload.sequence).toBe(doc.sequence)
    expect(payload.topology).toBe(doc.topology)
    expect(payload.features).toHaveLength(doc.features.length)
    expect(payload.features[0]).toMatchObject({ name: 'editedFeature', type: doc.features[0].type })
    expect(payload.source).toBe(doc.meta.source_format)
    expect(w.text()).toContain('已存进载体库')
  })

  it('管理员：后端报错时显示失败原因且面板保留', async () => {
    const pinia = adminPinia()
    saveMapSession(puc())
    api.saveMapAsVector.mockRejectedValue({ response: { status: 403, data: { detail: '需要管理员' } } })

    const w = mount(MapView, { global: { plugins: [pinia] } })
    await flushPromises()
    await w.findAll('button').find((b) => b.text().includes('另存为载体'))!.trigger('click')
    await w.findAll('button').find((b) => b.text() === '存入载体库')!.trigger('click')
    await flushPromises()
    expect(w.find('.map-error').text()).toContain('保存失败')
    expect(w.find('input[aria-label="载体名称"]').exists()).toBe(true)
  })
})
