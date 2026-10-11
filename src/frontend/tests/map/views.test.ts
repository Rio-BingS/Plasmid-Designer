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

describe('CircularMap', () => {
  it('画出特征、酶切刻度、中心名称与长度', () => {
    const w = mount(CircularMap, { props: { doc: puc(), size: 720 } })
    expect(w.findAll('.cmap-feat')).toHaveLength(puc().features.length)
    const cuts = puc().enzymes.reduce((n, e) => n + e.sites.length, 0)
    expect(w.findAll('.cmap-cut')).toHaveLength(cuts)
    expect(w.find('.cmap-name').text()).toBe('pUC19')
    expect(w.find('.cmap-length').text()).toBe('2,686 bp')
    // 长特征内置标签（textPath），短特征外置
    expect(w.findAll('textPath').length).toBeGreaterThan(0)
    expect(w.findAll('.cmap-label').length).toBeGreaterThan(0)
    expect(w.html()).not.toMatch(/NaN/)
  })

  it('点击特征 → 选中整段（选区扇形出现）；悬停显示提示', async () => {
    const w = mount(CircularMap, { props: { doc: puc(), size: 720 }, attachTo: document.body })
    await press(w.find('.cmap-feat path'))
    expect(w.find('.cmap-selection').exists()).toBe(true)
    await w.find('.cmap-feat path').trigger('pointermove', { clientX: 10, clientY: 10 })
    expect(document.body.querySelector('.map-tooltip')?.textContent).toBeTruthy()
    w.unmount()
  })
})

describe('SequenceMap 虚拟滚动', () => {
  it('300 kb 只渲染可见行', () => {
    const L = 300000
    const seq = 'ACGT'.repeat(L / 4)
    const d: MapDocument = {
      schema: 'plasmid-map/1', name: 'big', topology: 'circular', length: L, sequence: seq,
      features: [{ id: 'a', name: 'big CDS', type: 'cds', start: 299000, end: 2000, strand: 1, source: 'file' }],
      enzymes: [], orfs: [], primers: [], meta: { source_format: 'raw', enzyme_set: 'common', warnings: [] },
    }
    const t0 = performance.now()
    const w = mount(SequenceMap, { props: { doc: d, height: 400, width: 900 } })
    expect(performance.now() - t0).toBeLessThan(3000)
    const rows = w.findAll('svg.smap-row')
    expect(rows.length).toBeGreaterThan(0)
    expect(rows.length).toBeLessThan(40)
    const perRow = (w.vm as any).perRow
    const inner = w.find('.smap-inner').attributes('style')!
    const h = Number(/height: (\d+(?:\.\d+)?)px/.exec(inner)![1])
    expect(h).toBe(Math.ceil(L / perRow) * (w.vm as any).rowH)
    // 跨原点特征在首行可见
    expect(w.findAll('.smap-bar').length).toBeGreaterThan(0)
  })

  it('拖拽选区（行内）', async () => {
    const w = mount(SequenceMap, { props: { doc: puc(), height: 300, width: 900 } })
    const inner = w.find('.smap-inner')
    ;(inner.element as HTMLElement).getBoundingClientRect = () => ({ left: 0, top: 0, width: 900, height: 1000, right: 900, bottom: 1000, x: 0, y: 0, toJSON() {} })
    const vm: any = w.vm
    expect(vm.perRow).toBeGreaterThan(10)
    // gutter = 4 位数 * 7.5 + 14 = 44；第 0 行
    await inner.trigger('pointerdown', { button: 0, clientX: 44, clientY: 40, pointerId: 1 })
    await inner.trigger('pointermove', { clientX: 44 + 8 * 10, clientY: 40, pointerId: 1 })
    await inner.trigger('pointerup', { clientX: 44 + 8 * 10, clientY: 40, pointerId: 1 })
    expect(w.findAll('.smap-selection').length).toBe(1)
  })
})

describe('LinearMap 额外轨道', () => {
  it('箭头/轴色段/刻块；点击上报 track-click', async () => {
    const d = puc()
    const w = mount(LinearMap, {
      props: {
        doc: d, width: 1000, showEnzymes: false,
        tracks: [
          { id: 'reads', kind: 'arrows', items: [
            { id: '0', start: 100, end: 900, strand: 1, label: 'long-read.ab1', marks: [200, 300], className: 'rd-fwd' },
            { id: '1', start: 2600, end: 60, strand: -1, label: 'r2', segments: [{ start: 2600, end: 2686 }, { start: 1, end: 60 }], className: 'rd-rev' },
          ] },
          { id: 'cov', kind: 'axis', items: [{ id: 'c0', start: 100, end: 900, color: '#58b368', className: 'cov' }] },
          { id: 'var', kind: 'marks', items: [{ id: 'v0', start: 200, end: 200, className: 'var-tick' }] },
        ],
      },
    })
    expect(w.findAll('path.rd-fwd')).toHaveLength(1)
    expect(w.findAll('path.rd-rev')).toHaveLength(2)
    expect(w.findAll('.lmap-dot')).toHaveLength(2)
    expect(w.findAll('rect.cov')).toHaveLength(1)
    expect(w.findAll('rect.var-tick')).toHaveLength(1)
    expect(w.findAll('.lmap-track-name').map((t) => t.text())).toContain('long-read.ab1')
    expect(w.findAll('.lmap-cut')).toHaveLength(0)
    await press(w.find('path.rd-rev'))
    expect(w.emitted('track-click')![0]).toEqual(['reads', '1'])
    await press(w.find('rect.var-tick'))
    expect(w.emitted('track-click')![1]).toEqual(['var', 'v0'])
  })

  it('缩放按钮改变视口，全长复位', async () => {
    const w = mount(LinearMap, { props: { doc: puc(), width: 1000 } })
    expect(w.find('.lmap-range').text()).toContain('1–2,686')
    await w.findAll('.lmap-controls button')[1].trigger('click')
    expect(w.find('.lmap-range').text()).not.toContain('1–2,686')
    await w.findAll('.lmap-controls button')[2].trigger('click')
    expect(w.find('.lmap-range').text()).toContain('1–2,686')
  })

  it('深度放大后显示碱基', async () => {
    const w = mount(LinearMap, { props: { doc: puc(), width: 1000 } })
    ;(w.vm as any).setViewport(100, 160)
    await w.vm.$nextTick()
    expect(w.findAll('.lmap-base').length).toBeGreaterThan(50)
  })
})

describe('缩略图与缩略导航', () => {
  it('MapThumbnail 无标签，每个特征一块', () => {
    const w = mount(MapThumbnail, { props: { doc: puc() } })
    expect(w.findAll('path')).toHaveLength(puc().features.length)
    expect(w.findAll('text')).toHaveLength(0)
    const lin = mount(MapThumbnail, { props: { doc: { ...puc(), topology: 'linear' as const } } })
    expect(lin.findAll('rect').length).toBe(puc().features.length + 1) // 骨架 + 每个特征
  })
  it('Minimap 渲染特征块', () => {
    const w = mount(Minimap, { props: { doc: puc() } })
    expect(w.findAll('rect').length).toBeGreaterThanOrEqual(6)
  })
})
