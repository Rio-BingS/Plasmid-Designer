/** MapViewer 异步分析竞态（审查 H1）：换文档 / 请求期间编辑时，过期结果不得覆盖 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import type { MapDocument } from '@/map/types'
import pUC19 from '../fixtures/map/pUC19.json'
import pET28 from '../fixtures/map/pET-28a.json'

const apiMocks = vi.hoisted(() => ({ getEnzymeSets: vi.fn(), analyzeMap: vi.fn(), exportGenbank: vi.fn() }))
vi.mock('@/map/api', () => apiMocks)

import MapViewer from '@/map/components/MapViewer.vue'

const puc = () => JSON.parse(JSON.stringify(pUC19)) as MapDocument
const pet = () => JSON.parse(JSON.stringify(pET28)) as MapDocument

function deferred<T>() {
  let resolve!: (v: T) => void
  let reject!: (e: unknown) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

beforeEach(() => {
  vi.clearAllMocks()
  apiMocks.getEnzymeSets.mockResolvedValue([
    { id: 'common', name: '常用酶', description: '', enzymes: [] },
    { id: 'golden_gate', name: 'Golden Gate', description: '', enzymes: ['BsaI'] },
  ])
})

function lastEmitted(w: ReturnType<typeof mount>): MapDocument | undefined {
  const e = w.emitted('doc-change')
  return e ? (e[e.length - 1][0] as MapDocument) : undefined
}

describe('MapViewer：异步分析竞态', () => {
  it('切换酶组请求未返回时换了文档：丢弃旧结果，不发 doc-change', async () => {
    const d = puc()
    const pending = deferred<MapDocument>()
    apiMocks.analyzeMap.mockReturnValue(pending.promise)
    const w = mount(MapViewer, { props: { doc: d, initialView: 'circular' } })
    await flushPromises()
    await w.find('.mv-enzymes select').setValue('golden_gate')
    expect(apiMocks.analyzeMap).toHaveBeenCalledTimes(1)

    const other = pet()
    await w.setProps({ doc: other })
    pending.resolve({ ...d, enzymes: [d.enzymes[0]], meta: { ...d.meta, enzyme_set: 'golden_gate' } })
    await flushPromises()

    expect(w.emitted('doc-change')).toBeUndefined()
    expect(w.text()).toContain(other.name)
    expect(w.text()).not.toContain('2,686 bp')
    // 下拉框跟随新文档，不卡在"计算中"
    expect((w.find('.mv-enzymes select').element as HTMLSelectElement).value).toBe(other.meta.enzyme_set || 'common')
    expect(w.text()).not.toContain('计算中')
  })

  it('切换酶组请求失败但已换文档：不显示旧文档的错误', async () => {
    const pending = deferred<MapDocument>()
    apiMocks.analyzeMap.mockReturnValue(pending.promise)
    const w = mount(MapViewer, { props: { doc: puc(), initialView: 'circular' } })
    await flushPromises()
    await w.find('.mv-enzymes select').setValue('golden_gate')
    await w.setProps({ doc: pet() })
    pending.reject({ response: { data: { detail: '功能未开放' } } })
    await flushPromises()
    expect(w.find('.mv-banner.mv-error').exists()).toBe(false)
  })

  it('切换酶组请求期间编辑了特征：编辑保留，只合并酶切结果', async () => {
    const d = puc()
    const pending = deferred<MapDocument>()
    apiMocks.analyzeMap.mockReturnValue(pending.promise)
    const w = mount(MapViewer, { props: { doc: d, editable: true, initialView: 'circular' } })
    await flushPromises()
    await w.find('.mv-enzymes select').setValue('golden_gate')
    await w.findAll('.mv-feature-list .mv-row').find((r) => r.text().includes('AmpR (bla)'))!.trigger('click')
    const name = w.find('.mv-edit-name')
    await name.setValue('EDITED')
    await name.trigger('change')

    pending.resolve({ ...d, enzymes: [d.enzymes[0]], meta: { ...d.meta, enzyme_set: 'golden_gate' } })
    await flushPromises()
    const last = lastEmitted(w)!
    expect(last.features.some((f) => f.name === 'EDITED')).toBe(true)
    expect(last.enzymes).toHaveLength(1)
    expect(last.meta.enzyme_set).toBe('golden_gate')
    expect(w.find('.mv-feature-list').text()).toContain('EDITED')
  })

  it('重新分析请求期间编辑了特征：编辑不被覆盖，酶切/ORF 合并进当前文档', async () => {
    const d = puc()
    const pending = deferred<MapDocument>()
    apiMocks.analyzeMap.mockReturnValue(pending.promise)
    const w = mount(MapViewer, { props: { doc: d, editable: true } })
    await flushPromises()
    await w.find('.mv-reanalyze').trigger('click')
    await w.findAll('.mv-feature-list .mv-row').find((r) => r.text().includes('AmpR (bla)'))!.trigger('click')
    const name = w.find('.mv-edit-name')
    await name.setValue('EDITED')
    await name.trigger('change')
    const editsBefore = w.emitted('doc-change')!.length

    // 后端结果基于请求时的旧特征（AmpR 原名），且没有酶切、ORF 为空
    pending.resolve({ ...puc(), enzymes: [], orfs: [] })
    await flushPromises()

    const all = w.emitted('doc-change')!
    expect(all.length).toBe(editsBefore + 1)
    const last = all[all.length - 1][0] as MapDocument
    expect(last.features.some((f) => f.name === 'EDITED')).toBe(true)
    expect(last.features.some((f) => f.name === 'AmpR (bla)')).toBe(false)
    expect(last.enzymes).toEqual([])
    expect(last.orfs).toEqual([])
    expect(w.find('.mv-feature-list').text()).toContain('EDITED')
    // 撤销一步回到"编辑后、分析前"，编辑仍在
    await w.findAll('.mv-edit-tools button').find((b) => b.text() === '撤销')!.trigger('click')
    expect(w.find('.mv-feature-list').text()).toContain('EDITED')
  })

  it('重新分析（自动注释）请求期间编辑：新识别的自动特征合并进来，编辑保留', async () => {
    const d = puc()
    const pending = deferred<MapDocument>()
    apiMocks.analyzeMap.mockReturnValue(pending.promise)
    const w = mount(MapViewer, { props: { doc: d, editable: true } })
    await flushPromises()
    await w.findAll('label.mv-chip').find((l) => l.text().includes('重新自动注释'))!.find('input').setValue(true)
    await w.find('.mv-reanalyze').trigger('click')
    await w.findAll('.mv-feature-list .mv-row').find((r) => r.text().includes('AmpR (bla)'))!.trigger('click')
    const name = w.find('.mv-edit-name')
    await name.setValue('EDITED')
    await name.trigger('change')

    const res = puc()
    res.features.push({ id: 'f1', name: 'NEW-AUTO', type: 'misc_feature', start: 10, end: 40, strand: 1, source: 'auto' })
    pending.resolve(res)
    await flushPromises()
    const last = lastEmitted(w)!
    expect(last.features.some((f) => f.name === 'EDITED')).toBe(true)
    const auto = last.features.find((f) => f.name === 'NEW-AUTO')!
    expect(auto).toBeTruthy()
    expect(new Set(last.features.map((f) => f.id)).size).toBe(last.features.length)
  })

  it('重新分析请求未返回时换了文档：丢弃结果，不发 doc-change', async () => {
    const pending = deferred<MapDocument>()
    apiMocks.analyzeMap.mockReturnValue(pending.promise)
    const w = mount(MapViewer, { props: { doc: puc(), editable: true } })
    await flushPromises()
    await w.find('.mv-reanalyze').trigger('click')
    const other = pet()
    await w.setProps({ doc: other })
    pending.resolve({ ...puc(), enzymes: [], orfs: [] })
    await flushPromises()
    expect(w.emitted('doc-change')).toBeUndefined()
    expect(w.text()).toContain(other.name)
    expect(w.find('.mv-reanalyze').text()).toBe('重新分析')
  })
})
