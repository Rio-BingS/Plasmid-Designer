/** MapViewer 编辑（editable）：重命名/改色/删除/新增/撤销/重新分析 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import type { MapDocument } from '@/map/types'
import pUC19 from '../fixtures/map/pUC19.json'

const apiMocks = vi.hoisted(() => ({ getEnzymeSets: vi.fn(), analyzeMap: vi.fn(), exportGenbank: vi.fn() }))
vi.mock('@/map/api', () => apiMocks)

import MapViewer from '@/map/components/MapViewer.vue'

const puc = () => JSON.parse(JSON.stringify(pUC19)) as MapDocument

beforeEach(() => {
  vi.clearAllMocks()
  apiMocks.getEnzymeSets.mockResolvedValue([])
})

describe('MapViewer 编辑（editable）', () => {
  it('只读模式没有编辑工具', async () => {
    const w = mount(MapViewer, { props: { doc: puc() } })
    await flushPromises()
    expect(w.find('.mv-edit-tools').exists()).toBe(false)
    expect(w.find('.mv-editor').exists()).toBe(false)
  })

  it('重命名、改色、删除特征并发出 doc-change；撤销恢复；导出 GenBank 用编辑后的文档', async () => {
    apiMocks.exportGenbank.mockResolvedValue(new Blob(['LOCUS']))
    const w = mount(MapViewer, { props: { doc: puc(), editable: true } })
    await flushPromises()
    await w.findAll('.mv-feature-list .mv-row').find((r) => r.text().includes('AmpR'))!.trigger('click')
    const name = w.find('.mv-edit-name')
    await name.setValue('bla')
    await name.trigger('change')
    let emitted = w.emitted('doc-change')!
    let last = emitted[emitted.length - 1][0] as MapDocument
    expect(last.features.some((f) => f.name === 'bla')).toBe(true)
    expect(w.find('.mv-feature-list').text()).toContain('bla')

    const color = w.find('.mv-editor input[type="color"]')
    await color.setValue('#123456')
    await color.trigger('change')
    emitted = w.emitted('doc-change')!
    last = emitted[emitted.length - 1][0] as MapDocument
    expect(last.features.find((f) => f.name === 'bla')!.color).toBe('#123456')

    const n = last.features.length
    await w.find('.mv-delete').trigger('click')
    emitted = w.emitted('doc-change')!
    last = emitted[emitted.length - 1][0] as MapDocument
    expect(last.features).toHaveLength(n - 1)
    expect(w.find('.mv-feature-list').text()).not.toContain('bla')

    await w.find('.mv-export button:last-child').trigger('click')
    await flushPromises()
    const exported = apiMocks.exportGenbank.mock.calls[0][0] as MapDocument
    expect(exported.features).toHaveLength(n - 1)

    await w.findAll('.mv-edit-tools button').find((b) => b.text() === '撤销')!.trigger('click')
    expect(w.find('.mv-feature-list').text()).toContain('bla')
  })

  it('从选区新增特征（名称、类型、方向）', async () => {
    const w = mount(MapViewer, { props: { doc: puc(), editable: true } })
    await flushPromises()
    const vm = w.vm as unknown as { store: { select: (r: { start: number; end: number }) => void } }
    vm.store.select({ start: 100, end: 160 })
    await flushPromises()
    expect(w.find('.mv-editor').text()).toContain('100..160')
    await w.find('.mv-new-name').setValue('my-site')
    await w.find('select[aria-label="新特征类型"]').setValue('protein_bind')
    await w.find('select[aria-label="新特征方向"]').setValue('-1')
    await w.find('.mv-add').trigger('click')
    const emitted = w.emitted('doc-change')!
    const d = emitted[emitted.length - 1][0] as MapDocument
    const f = d.features.find((x) => x.name === 'my-site')!
    expect(f).toMatchObject({ start: 100, end: 160, type: 'protein_bind', strand: -1, source: 'file' })
    // 新增后进入该特征的编辑态
    expect((w.find('.mv-edit-name').element as HTMLInputElement).value).toBe('my-site')
  })

  it('重新分析：把当前特征与引物发给 /api/map/analyze 并替换文档', async () => {
    const res = { ...puc(), orfs: [], enzymes: [] }
    apiMocks.analyzeMap.mockResolvedValue(res)
    const w = mount(MapViewer, { props: { doc: puc(), editable: true } })
    await flushPromises()
    await w.find('.mv-reanalyze').trigger('click')
    await flushPromises()
    const req = apiMocks.analyzeMap.mock.calls[0][0]
    expect(req).toMatchObject({ annotate: false, enzyme_set: 'common', topology: 'circular' })
    expect(req.features.length).toBe(puc().features.length)
    expect(w.emitted('doc-change')).toBeTruthy()
    expect(w.find('.cmap-cut').exists()).toBe(false)
  })

  it('重新分析失败给出中文提示', async () => {
    apiMocks.analyzeMap.mockRejectedValue({ response: { status: 422, data: { detail: '序列含非法字符' } } })
    const w = mount(MapViewer, { props: { doc: puc(), editable: true } })
    await flushPromises()
    await w.find('.mv-reanalyze').trigger('click')
    await flushPromises()
    expect(w.find('.mv-banner.mv-error').text()).toContain('重新分析失败：序列含非法字符')
  })
})
