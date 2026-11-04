import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import type { MapDocument } from '@/map/types'
import pUC19 from '../fixtures/map/pUC19.json'
import pET28 from '../fixtures/map/pET-28a.json'

const apiMocks = vi.hoisted(() => ({
  getEnzymeSets: vi.fn(),
  analyzeMap: vi.fn(),
  exportGenbank: vi.fn(),
}))
vi.mock('@/map/api', () => apiMocks)

import MapViewer from '@/map/components/MapViewer.vue'
import CircularMap from '@/map/components/CircularMap.vue'
import LinearMap from '@/map/components/LinearMap.vue'
import SequenceMap from '@/map/components/SequenceMap.vue'
import { translate } from '@/map/core/seq'
import MapThumbnail from '@/map/components/MapThumbnail.vue'
import Minimap from '@/map/components/Minimap.vue'

const puc = () => JSON.parse(JSON.stringify(pUC19)) as MapDocument
const pet = () => JSON.parse(JSON.stringify(pET28)) as MapDocument

async function press(el: any) {
  await el.trigger('pointerdown', { button: 0, clientX: 0, clientY: 0, pointerId: 1 })
  await el.trigger('pointerup', { button: 0, clientX: 0, clientY: 0, pointerId: 1 })
}

beforeEach(() => {
  vi.clearAllMocks()
  apiMocks.getEnzymeSets.mockResolvedValue([
    { id: 'common', name: '常用酶', description: '', enzymes: [] },
    { id: 'golden_gate', name: 'Golden Gate', description: '', enzymes: ['BsaI'] },
  ])
})

describe('MapViewer：无法显示项', () => {
  it('小尺寸下放不下的标签计数而不是重叠', async () => {
    const d = pet()
    // 再加一批挤在一起的特征
    for (let i = 0; i < 40; i++) d.features.push({ id: `x${i}`, name: `feature-${i}`, type: 'misc_feature', start: 100 + i * 3, end: 102 + i * 3, strand: 1, source: 'file' })
    const w = mount(MapViewer, { props: { doc: d, initialView: 'circular', allowAnalyze: false } })
    await flushPromises()
    expect(w.find('.mv-unshown').text()).toMatch(/\d+ 项无法显示/)
    await w.find('.mv-unshown button').trigger('click')
    expect(w.findAll('.mv-unshown-list li').length).toBeGreaterThan(0)
    // 同名酶的多个位点注明位置，不再列出一串同名项
    const names = w.findAll('.mv-unshown-list li b').map((b) => b.text())
    expect(new Set(names).size).toBe(names.length)
  })

  it('详情列表最多 200 条，其余汇总一行', async () => {
    const d = pet()
    for (let i = 0; i < 400; i++) d.features.push({ id: `y${i}`, name: `f${i}`, type: 'misc_feature', start: 100 + (i % 50), end: 101 + (i % 50), strand: 1, source: 'file' })
    const w = mount(MapViewer, { props: { doc: d, initialView: 'circular', allowAnalyze: false } })
    await flushPromises()
    await w.find('.mv-unshown button').trigger('click')
    const items = w.findAll('.mv-unshown-list li')
    expect(items.length).toBe(201)
    expect(w.find('.mv-unshown-more').text()).toMatch(/另有 \d+ 项/)
  })
})

describe('MapViewer：切点极多时', () => {
  function manyCuts(): MapDocument {
    const d = puc()
    // 两个各切 600 次的 4 碱基酶 + 一个单切点酶
    const sites = (n: number, off: number) => Array.from({ length: n }, (_, i) => ({ position: off + i * 4, cut_top: off + i * 4, cut_bottom: off + i * 4, strand: 1 as const }))
    d.enzymes = [
      { name: 'Multi1', recognition: 'GA^TC', ends: "5'", overhang: 'AT', cut_count: 600, sites: sites(600, 3) },
      { name: 'Multi2', recognition: 'C^TAG', ends: "5'", overhang: 'TA', cut_count: 600, sites: sites(600, 5) },
      { name: 'Solo', recognition: 'G^AATTC', ends: "5'", overhang: 'AATT', cut_count: 1, sites: sites(1, 400) },
    ]
    return d
  }

  it('概览图只画少切点酶，略去的酶整体列入"无法显示"', async () => {
    const w = mount(MapViewer, { props: { doc: manyCuts(), initialView: 'circular', allowAnalyze: false } })
    await flushPromises()
    // 1200 个切点超出概览上限：两个多切点酶整体略去，单切点酶保留
    expect(w.findAll('.cmap-cut')).toHaveLength(1)
    await w.find('.mv-unshown button').trigger('click')
    const text = w.find('.mv-unshown-list').text()
    expect(text).toContain('Multi1（600 处）')
    expect(text).toContain('切点过多')
  })

  it('线性图视口内切点过多同样只画少切点酶', async () => {
    const w = mount(MapViewer, { props: { doc: manyCuts(), initialView: 'linear', allowAnalyze: false } })
    await flushPromises()
    expect(w.findAll('.lmap-cut')).toHaveLength(1)
  })
})

describe('MapViewer：多视图共享选区', () => {
  it('侧栏点击特征 → 状态栏统计 + 各视图同步选区；切线性视图仍保持', async () => {
    const w = mount(MapViewer, { props: { doc: puc() } })
    await flushPromises()
    expect(w.find('.cmap-svg').exists()).toBe(true)
    expect(w.find('.smap-scroll').exists()).toBe(true)
    const row = w.findAll('.mv-feature-list .mv-row').find((r) => r.text().includes('AmpR'))!
    await row.trigger('click')
    expect(w.find('.mv-selection-text').text()).toContain('1,626..2,486')
    expect(w.find('.mv-selection-text').text()).toContain('861 bp')
    expect(w.find('.mv-selection-text').text()).toMatch(/GC \d+\.\d% · Tm \d+\.\d °C/)
    expect(w.find('.cmap-selection').exists()).toBe(true)
    expect(w.findAll('.smap-selection').length).toBeGreaterThan(0)
    await w.findAll('.mv-views button').find((b) => b.text() === '线性')!.trigger('click')
    expect(w.find('.lmap-svg').exists()).toBe(true)
    expect(w.findAll('.lmap-selection').length).toBeGreaterThan(0)
    expect(w.find('.minimap').exists()).toBe(true)
  })

  it('跨原点选区在环形与序列视图中都正确显示', async () => {
    const w = mount(MapViewer, { props: { doc: puc() } })
    await flushPromises()
    const store = (w.vm as any).store
    store.select({ start: 2600, end: 50 })
    await w.vm.$nextTick()
    expect(w.find('.mv-selection-text').text()).toContain('（跨原点）')
    expect(w.find('.mv-selection-text').text()).toContain('137 bp')
    expect(w.find('.cmap-selection').attributes('d')).toBeTruthy()
    // 序列视图第 1 行显示 1..50 的那段
    expect(w.findAll('.smap-selection').length).toBeGreaterThan(0)
  })

  it('选中 CDS 后序列视图出现翻译', async () => {
    const w = mount(MapViewer, { props: { doc: puc(), initialView: 'sequence' } })
    await flushPromises()
    expect(w.findAll('.smap-aa')).toHaveLength(0)
    ;(w.vm as any).store.selectFeature('f1') // lacZ-alpha（反链 146..469）
    await w.vm.$nextTick()
    const aa = w.findAll('.smap-aa').map((x) => x.text())
    expect(aa.length).toBeGreaterThan(10)
    // 完整 ORF：翻译里含起始 M 与终止 *
    expect(aa).toContain('M')
    expect(aa).toContain('*')
  })

  it('图层开关：关闭特征/酶切后视图不再绘制', async () => {
    const w = mount(MapViewer, { props: { doc: puc(), initialView: 'circular' } })
    await flushPromises()
    const chip = (t: string) => w.findAll('.mv-layers label').find((l) => l.text() === t)!.find('input')
    await chip('特征').setValue(false)
    expect(w.findAll('.cmap-feat')).toHaveLength(0)
    await chip('酶切').setValue(false)
    expect(w.findAll('.cmap-cut')).toHaveLength(0)
  })
})

describe('MapViewer：搜索与 Ctrl+F', () => {
  it('Ctrl+F 聚焦搜索框；回车搜索、命中选区；错配搜索', async () => {
    const w = mount(MapViewer, { props: { doc: puc() }, attachTo: document.body })
    await flushPromises()
    await w.find('.mv').trigger('pointerenter')
    const ev = new KeyboardEvent('keydown', { key: 'f', ctrlKey: true, cancelable: true })
    window.dispatchEvent(ev)
    expect(ev.defaultPrevented).toBe(true)
    const input = w.find('.mv-search input')
    expect(document.activeElement).toBe(input.element)
    await input.setValue('GAATTC')
    await input.trigger('keydown', { key: 'Enter' })
    expect(w.find('.mv-hitcount').text()).toBe('1/1')
    expect(w.find('.mv-selection-text').text()).toContain('396..401')
    expect(w.findAll('.cmap-search')).toHaveLength(1)
    // 1 个错配命中更多
    await w.find('.mv-search select').findAll('option')[1].setSelected()
    await flushPromises()
    expect(Number(w.find('.mv-hitcount').text().split('/')[1])).toBeGreaterThan(1)
    // 序列搜不到时改按元件名称找，"hello!" 既不是碱基串也没有同名元件：
    // 显示「无匹配」而不是碱基语法错（搜索框现在同时接受序列与名称）
    await input.setValue('hello!')
    await input.trigger('keydown', { key: 'Enter' })
    expect(w.find('.mv-search .mv-error').exists()).toBe(false)
    expect(w.find('.mv-search').text()).toContain('无匹配')
    w.unmount()
  })

  it('指针不在查看器内时不拦截 Ctrl+F', async () => {
    const w = mount(MapViewer, { props: { doc: puc() }, attachTo: document.body })
    await flushPromises()
    const ev = new KeyboardEvent('keydown', { key: 'f', ctrlKey: true, cancelable: true })
    window.dispatchEvent(ev)
    expect(ev.defaultPrevented).toBe(false)
    w.unmount()
  })
})

describe('MapViewer：酶切过滤与酶组', () => {
  it('仅单切点 / 名称筛选 / 单独隐藏 / Golden Gate', async () => {
    const w = mount(MapViewer, { props: { doc: puc(), initialView: 'circular' } })
    await flushPromises()
    await w.findAll('.mv-tabs button')[1].trigger('click')
    const total = w.findAll('.mv-enzyme-list .mv-row').length
    await w.findAll('.mv-enzymes select')[1].setValue('1')
    const uniq = w.findAll('.mv-enzyme-list .mv-row')
    expect(uniq.length).toBeLessThan(total)
    expect(uniq.every((r) => r.text().includes('1 切点'))).toBe(true)
    const before = w.findAll('.cmap-cut').length
    const eco = uniq.find((r) => r.text().startsWith('EcoRI'))!
    await eco.find('.mv-eye').trigger('click')
    expect(w.findAll('.cmap-cut').length).toBe(before - 1)
    // 隐藏的酶仍在列表中（可恢复）
    expect(w.findAll('.mv-enzyme-list .mv-row.hidden')).toHaveLength(1)
    await w.find('.mv-enz-query').setValue('bam')
    expect(w.findAll('.mv-enzyme-list .mv-row').map((r) => r.find('.mv-name').text())).toEqual(['BamHI'])
    await w.find('.mv-enz-query').setValue('')
    await w.findAll('.mv-enzymes .mv-chip input')[0].setValue(true)
    const names = w.findAll('.mv-enzyme-list .mv-row').map((r) => r.find('.mv-name').text())
    expect(names.every((n) => ['BsaI', 'BsmBI', 'BbsI', 'SapI', 'Esp3I', 'PaqCI'].includes(n))).toBe(true)
  })

  it('单切点酶加粗、平端酶独立颜色', async () => {
    const w = mount(MapViewer, { props: { doc: puc(), initialView: 'circular' } })
    await flushPromises()
    await w.findAll('.mv-tabs button')[1].trigger('click')
    const rows = w.findAll('.mv-enzyme-list .mv-row')
    const eco = rows.find((r) => r.find('.mv-name').text() === 'EcoRI')!.find('.mv-name')
    expect(eco.attributes('style')).toContain('font-weight: 700')
    const pvu = rows.find((r) => r.find('.mv-name').text() === 'PvuII')!.find('.mv-name')
    expect(pvu.attributes('style')).toMatch(/#c0392b|rgb\(192, 57, 43\)/)
  })

  it('切换酶组调用 analyze 并替换酶切结果', async () => {
    const d = puc()
    apiMocks.analyzeMap.mockResolvedValue({ ...d, enzymes: [d.enzymes[0]], meta: { ...d.meta, enzyme_set: 'golden_gate' } })
    const w = mount(MapViewer, { props: { doc: d, initialView: 'circular' } })
    await flushPromises()
    expect(apiMocks.getEnzymeSets).toHaveBeenCalled()
    await w.find('.mv-enzymes select').setValue('golden_gate')
    await flushPromises()
    expect(apiMocks.analyzeMap).toHaveBeenCalledWith(expect.objectContaining({ enzyme_set: 'golden_gate', annotate: false, sequence: d.sequence }))
    expect(w.findAll('.cmap-cut')).toHaveLength(d.enzymes[0].sites.length)
    expect(w.emitted('doc-change')).toHaveLength(1)
  })

  it('analyze 失败时提示并保留原结果', async () => {
    apiMocks.analyzeMap.mockRejectedValue({ response: { data: { detail: '功能未开放' } } })
    const w = mount(MapViewer, { props: { doc: puc(), initialView: 'circular' } })
    await flushPromises()
    const before = w.findAll('.cmap-cut').length
    await w.find('.mv-enzymes select').setValue('golden_gate')
    await flushPromises()
    expect(w.find('.mv-banner.mv-error').text()).toContain('功能未开放')
    expect(w.findAll('.cmap-cut')).toHaveLength(before)
  })
})

describe('MapViewer：导出', () => {
  it('SVG 下载为独立 SVG 文件；GenBank 走后端导出', async () => {
    apiMocks.exportGenbank.mockResolvedValue(new Blob(['LOCUS']))
    const w = mount(MapViewer, { props: { doc: puc(), initialView: 'circular' } })
    await flushPromises()
    const create = vi.mocked(URL.createObjectURL)
    create.mockClear()
    await w.findAll('.mv-export button').find((b) => b.text() === 'SVG')!.trigger('click')
    const blob = create.mock.calls[0][0] as Blob
    expect(blob.type).toBe('image/svg+xml')
    const text = await blob.text()
    expect(text).toContain('<svg')
    expect(text).toContain('xmlns="http://www.w3.org/2000/svg"')
    expect(text).toContain('pUC19')
    await w.findAll('.mv-export button').find((b) => b.text() === 'GenBank')!.trigger('click')
    await flushPromises()
    expect(apiMocks.exportGenbank).toHaveBeenCalledWith(expect.objectContaining({ name: 'pUC19' }))
  })

  it('仅序列视图时图片导出不可用', async () => {
    const w = mount(MapViewer, { props: { doc: puc(), initialView: 'sequence' } })
    await flushPromises()
    expect(w.findAll('.mv-export button').find((b) => b.text() === 'SVG')!.attributes('disabled')).toBeDefined()
  })

  it('线性分子禁用环形视图，分屏用线性图', async () => {
    const d = { ...puc(), topology: 'linear' as const }
    const w = mount(MapViewer, { props: { doc: d } })
    await flushPromises()
    expect(w.findAll('.mv-views button')[0].attributes('disabled')).toBeDefined()
    expect(w.find('.cmap-svg').exists()).toBe(false)
    expect(w.find('.lmap-svg').exists()).toBe(true)
  })
})
