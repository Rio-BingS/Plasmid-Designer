/** tablist 方向键切换（审查 03-Low 9） */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import type { MapDocument } from '@/map/types'
import pUC19 from './fixtures/map/pUC19.json'

const mapApi = vi.hoisted(() => ({ getEnzymeSets: vi.fn(), analyzeMap: vi.fn(), exportGenbank: vi.fn() }))
vi.mock('@/map/api', async (orig) => ({ ...(await orig<typeof import('@/map/api')>()), ...mapApi }))

import MapViewer from '@/map/components/MapViewer.vue'
import MapView from '@/views/MapView.vue'

beforeEach(() => {
  vi.clearAllMocks()
  sessionStorage.clear()
  mapApi.getEnzymeSets.mockResolvedValue([])
})

const selected = (w: ReturnType<typeof mount>, list: string) =>
  w.findAll(`${list} [role="tab"]`).findIndex((t) => t.attributes('aria-selected') === 'true')

describe('tablist 键盘导航', () => {
  it('MapViewer 侧栏：→ 下一个、← 循环到最后、Home/End', async () => {
    const w = mount(MapViewer, { props: { doc: JSON.parse(JSON.stringify(pUC19)) as MapDocument, allowAnalyze: false }, attachTo: document.body })
    await flushPromises()
    const tabs = () => w.findAll('.mv-tabs [role="tab"]')
    expect(selected(w, '.mv-tabs')).toBe(0)
    await tabs()[0].trigger('keydown', { key: 'ArrowRight' })
    expect(selected(w, '.mv-tabs')).toBe(1)
    expect(document.activeElement).toBe(tabs()[1].element)
    await tabs()[1].trigger('keydown', { key: 'End' })
    expect(selected(w, '.mv-tabs')).toBe(2)
    await tabs()[2].trigger('keydown', { key: 'ArrowRight' })
    expect(selected(w, '.mv-tabs')).toBe(0)
    await tabs()[0].trigger('keydown', { key: 'ArrowLeft' })
    expect(selected(w, '.mv-tabs')).toBe(2)
    await tabs()[2].trigger('keydown', { key: 'Home' })
    expect(selected(w, '.mv-tabs')).toBe(0)
    // 其他按键不切换
    await tabs()[0].trigger('keydown', { key: 'a' })
    expect(selected(w, '.mv-tabs')).toBe(0)
    w.unmount()
  })

  it('/map 页来源标签：→ 从"上传文件"切到"粘贴序列"', async () => {
    const w = mount(MapView, { attachTo: document.body })
    await flushPromises()
    const tabs = w.findAll('.map-tabs [role="tab"]')
    expect(selected(w, '.map-tabs')).toBe(0)
    await tabs[0].trigger('keydown', { key: 'ArrowRight' })
    expect(selected(w, '.map-tabs')).toBe(1)
    expect(w.find('textarea').exists()).toBe(true)
    w.unmount()
  })
})
