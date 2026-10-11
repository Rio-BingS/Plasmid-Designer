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

describe('CircularMap 版式', () => {
  const vb = (w: any) => w.find('.cmap-svg').attributes('viewBox')!.split(' ').map(Number)

  it('标签栏按最长标签留白（环够大），并裁掉上下空白', () => {
    const w = mount(CircularMap, { props: { doc: puc(), size: 640 } })
    const R0 = Number(w.find('.cmap-svg circle').attributes('r'))
    // 固定按 26% 留白时 R0 = 320 - 166 - 26 ≈ 128
    expect(R0).toBeGreaterThan(180)
    const [, , vw, vh] = vb(w)
    expect(vw).toBe(640)
    expect(vh).toBeLessThan(640)
    expect(Number(w.find('.cmap-svg').attributes('height'))).toBe(Math.round(vh))
    // 标签都在可视区内
    const [, y0] = vb(w)
    for (const t of w.findAll('.cmap-label text')) {
      const y = Number(t.attributes('y'))
      expect(y).toBeGreaterThan(y0)
      expect(y).toBeLessThan(y0 + vh)
    }
  })

  it('手机宽度：小环不写刻度数字，中心名称放不下时不画', () => {
    const w = mount(CircularMap, { props: { doc: pet(), size: 300 } })
    const labels = w.findAll('.cmap-ticks text')
    const R0 = Number(w.find('.cmap-svg circle').attributes('r'))
    if (R0 < 90) expect(labels).toHaveLength(0)
    expect(w.html()).not.toMatch(/NaN/)
    // 字号变小
    expect(w.find('.cmap-label text').attributes('font-size')).toBe('10')
  })

  it('拖选结束后发出导航请求（序列视图滚到选区）', async () => {
    const w = mount(MapViewer, { props: { doc: puc(), initialView: 'split', allowAnalyze: false } })
    await flushPromises()
    const store = (w.vm as any).store
    const svg = w.find('.cmap-svg')
    ;(svg.element as SVGSVGElement).getBoundingClientRect = () => ({ left: 0, top: 0, width: 640, height: 640, right: 640, bottom: 640, x: 0, y: 0, toJSON() {} })
    const [x0, y0, vw, vh] = vb(w)
    const R0 = Number(w.find('.cmap-svg circle').attributes('r'))
    const c = Number(w.find('.cmap-svg circle').attributes('cx'))
    const pt = (a: number) => ({ clientX: ((c + R0 * Math.sin(a) - x0) / vw) * 640, clientY: ((c - R0 * Math.cos(a) - y0) / vh) * 640 })
    const n0 = store.navigate.value.n
    await svg.trigger('pointerdown', { button: 0, pointerId: 1, ...pt(3.4) })
    await svg.trigger('pointermove', { pointerId: 1, ...pt(3.6) })
    // 拖动过程中不导航
    expect(store.navigate.value.n).toBe(n0)
    await svg.trigger('pointermove', { pointerId: 1, ...pt(3.8) })
    await svg.trigger('pointerup', { pointerId: 1, ...pt(3.8) })
    expect(store.selection.value).not.toBeNull()
    expect(store.navigate.value.n).toBe(n0 + 1)
    expect(store.navigate.value.range).toEqual(store.selection.value)
  })
})

describe('LinearMap 酶名排布', () => {
  it('单切点酶名排在贴近主轴的一行，文字带白边', () => {
    const w = mount(LinearMap, { props: { doc: pet(), width: 900 } })
    const texts = w.findAll('.lmap-enz-label text')
    expect(texts.length).toBeGreaterThan(5)
    const ys = texts.map((t) => Number(t.attributes('y')))
    const nearest = Math.max(...ys)
    const boldNearest = texts.filter((t, i) => ys[i] === nearest && t.attributes('font-weight') === '700')
    expect(boldNearest.length).toBeGreaterThan(0)
    for (const t of texts) expect(t.attributes('paint-order')).toBe('stroke')
    for (const t of w.findAll('.lmap-feat-out')) expect(t.attributes('paint-order')).toBe('stroke')
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
    const off: Float64Array = (w.vm as any).offsets
    expect(off.length).toBe(Math.ceil(L / perRow) + 1)
    expect(h).toBe(off[off.length - 1])
    // 跨原点特征在首行可见
    expect(w.findAll('.smap-bar').length).toBeGreaterThan(0)
  })

  it('行高按本行内容定：无特征/无酶切的行不为全局分道留白', () => {
    const d = puc()
    const w = mount(SequenceMap, { props: { doc: d, height: 400, width: 900 } })
    const off: Float64Array = (w.vm as any).offsets
    const perRow: number = (w.vm as any).perRow
    const heights = Array.from({ length: off.length - 1 }, (_, i) => off[i + 1] - off[i])
    const featRows = new Set<number>()
    for (const f of d.features) for (let r = Math.floor((f.start - 1) / perRow); r <= Math.floor((f.end - 1) / perRow); r++) featRows.add(r)
    const cutRows = new Set(d.enzymes.flatMap((e) => e.sites.map((s) => Math.floor(s.cut_top / perRow))))
    const plain = heights.filter((_, i) => !featRows.has(i) && !cutRows.has(i))
    const busy = heights.filter((_, i) => featRows.has(i))
    expect(plain.length).toBeGreaterThan(0)
    expect(Math.max(...plain)).toBeLessThan(Math.min(...busy))
    // 渲染出的行顶部与 offsets 一致（行与行不重叠）
    const tops = w.findAll('svg.smap-row').map((r) => Number(/top: (\d+(?:\.\d+)?)px/.exec(r.attributes('style')!)![1]))
    tops.forEach((t, i) => { if (i) expect(t).toBeGreaterThan(tops[i - 1]) })
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

  it('read 名避开变异圆点，放在条内最长的空当里', () => {
    const d = puc()
    // 圆点正好落在 read 中点（名字默认居中处）
    const w = mount(LinearMap, {
      props: {
        doc: d, width: 1000, showEnzymes: false,
        tracks: [{ id: 'reads', kind: 'arrows', items: [{ id: '0', start: 100, end: 900, strand: 1, label: 'F1.ab1', marks: [500] }] }],
      },
    })
    const name = w.find('.lmap-track-name')
    const dot = w.find('.lmap-dot')
    const nx = Number(name.attributes('x'))
    const dx = Number(dot.attributes('cx'))
    const half = 30 // "F1.ab1" 11px 半宽上限
    expect(Math.abs(nx - dx)).toBeGreaterThan(half + 4)
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
