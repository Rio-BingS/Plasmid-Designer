/** 载体库列表缩略图：map_preview → MapThumbnail，缺省时不画 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

const siteApi = vi.hoisted(() => ({ getVectors: vi.fn() }))
vi.mock('@/api', async (orig) => ({ ...(await orig<typeof import('@/api')>()), ...siteApi }))

import VectorsView from '@/views/VectorsView.vue'
import { previewToThumb } from '@/map/preview'

beforeEach(() => { vi.clearAllMocks() })

describe('previewToThumb', () => {
  it('previewToThumb：缺省/非法 → null；越界特征跳过；未知类型归 misc_feature', () => {
    expect(previewToThumb(undefined, 'x')).toBeNull()
    expect(previewToThumb({ length: 0, topology: 'circular', features: [] }, 'x')).toBeNull()
    const t = previewToThumb({ length: 1000, topology: 'circular', features: [
      { name: 'a', type: 'cds', start: 10, end: 200, strand: 1 },
      { name: 'b', type: 'weird', start: 900, end: 50, strand: -1 },
      { name: 'c', type: 'cds', start: 10, end: 5000, strand: 1 },
    ] }, 'v')!
    expect(t.features.map((f) => [f.name, f.type])).toEqual([['a', 'cds'], ['b', 'misc_feature']])
    expect(previewToThumb({ length: 1000, topology: 'linear', features: [{ name: 'b', type: 'cds', start: 900, end: 50, strand: 1 }] }, 'v')!.features).toHaveLength(0)
  })
})
describe('载体列表缩略图', () => {
  it('有 map_preview 画缩略图，缺省时不画', async () => {
    const base = { source: 'common', vector_type: 'cloning', host: ['E. coli'], antibiotic_resistance: ['Amp'], copy_number: 'high', description: '', features: [], mcs_enzymes: [] }
    siteApi.getVectors.mockResolvedValue([
      { ...base, id: 'pUC19', name: 'pUC19', map_preview: { length: 2686, topology: 'circular', features: [{ name: 'AmpR', type: 'marker', start: 885, end: 1745, strand: -1 }] } },
      { ...base, id: 'old', name: 'old' },
    ])
    const w = mount(VectorsView, { global: { stubs: { RouterLink: true }, mocks: { $router: { push: vi.fn() } } } })
    await flushPromises()
    const cards = w.findAll('.vector-card')
    expect(cards).toHaveLength(2)
    expect(cards[0].find('.map-thumb').exists()).toBe(true)
    expect(cards[0].find('.map-thumb path').exists()).toBe(true)
    expect(cards[0].text()).toContain('2,686 bp · 环状')
    expect(cards[1].find('.map-thumb').exists()).toBe(false)
  })
})
