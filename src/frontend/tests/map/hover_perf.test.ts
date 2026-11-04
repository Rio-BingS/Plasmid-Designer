/** 悬停性能（审查 M4）：同一元件上移动不重算；行内容不依赖悬停/选区；300 kb + 2000 命中下悬停足够快 */
import { describe, it, expect } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { nextTick } from 'vue'
import type { MapDocument, MapFeature } from '@/map/types'
import SequenceMap from '@/map/components/SequenceMap.vue'
import { rng, randInt } from './rand'

type Store = {
  hover: { value: { id: string; x: number; y: number } | null }
  hoverTarget: { value: unknown }
  select: (r: { start: number; end: number }) => void
  runSearch: (q: string, mm?: number) => void
  search: { hits: unknown[] }
}
const setup = (w: VueWrapper) => (w.vm as unknown as { $: { setupState: Record<string, unknown> } }).$.setupState
const storeOf = (w: VueWrapper) => setup(w).store as Store
const rowsOf = (w: VueWrapper) => (setup(w).rows as unknown[])

function bigDoc(L = 300000, nFeat = 3000): MapDocument {
  const r = rng(7)
  const seq = Array.from({ length: L }, () => 'ACGT'[randInt(r, 0, 3)]).join('')
  const features: MapFeature[] = Array.from({ length: nFeat }, (_, i) => {
    const start = randInt(r, 1, L - 400)
    return { id: `f${i}`, name: `feat${i}`, type: 'misc_feature', start, end: start + randInt(r, 20, 300), strand: 1, source: 'file' }
  })
  return {
    schema: 'plasmid-map/1', name: 'big', topology: 'circular', length: L, sequence: seq,
    features, enzymes: [], orfs: [], primers: [], meta: { source_format: 'raw', enzyme_set: 'common', warnings: [] },
  }
}

describe('悬停不触发行重算', () => {
  it('同一元件上移动只改坐标，hoverTarget 不变', async () => {
    const w = mount(SequenceMap, { props: { doc: bigDoc(30000, 200), height: 400, width: 900 }, attachTo: document.body })
    const bar = w.find('.smap-bar')
    await bar.trigger('pointermove', { clientX: 10, clientY: 10 })
    const store = storeOf(w)
    const t0 = store.hoverTarget.value
    const h0 = store.hover.value
    expect(t0).toBeTruthy()
    await bar.trigger('pointermove', { clientX: 30, clientY: 12 })
    expect(store.hoverTarget.value).toBe(t0)
    expect(store.hover.value).not.toBe(h0)
    expect(store.hover.value).toMatchObject({ x: 30, y: 12 })
    w.unmount()
  })

  it('悬停、选区、搜索变化都不重建每行的静态内容', async () => {
    const w = mount(SequenceMap, { props: { doc: bigDoc(30000, 200), height: 400, width: 900 } })
    const rows0 = rowsOf(w)
    const bars = w.findAll('.smap-bar')
    await bars[0].trigger('pointermove', { clientX: 10, clientY: 10 })
    await bars[bars.length - 1].trigger('pointermove', { clientX: 10, clientY: 10 })
    storeOf(w).select({ start: 5, end: 40 })
    await nextTick()
    expect(rowsOf(w)).toBe(rows0)
    expect(w.findAll('.smap-selection').length).toBeGreaterThan(0)
  })

  it('搜索命中按行显示（与逐条扫描结果一致）', async () => {
    const d = bigDoc(30000, 50)
    const w = mount(SequenceMap, { props: { doc: d, height: 2000, width: 900 } })
    const store = storeOf(w)
    store.runSearch(d.sequence.slice(0, 4), 0)
    await nextTick()
    const shown = new Set(w.findAll('.smap-search').map((e) => e.attributes('data-hit')))
    expect(shown.has('s:0')).toBe(true)
    // 可见行里每个命中至少出现一次
    const perRow = setup(w).perRow as number
    const rowCount = w.findAll('svg.smap-row').length
    const firstRow = Math.min(...w.findAll('svg.smap-row').map((r) => Number(/top: (\d+)/.exec(r.attributes('style')!)![1]))) === 0 ? 0 : -1
    expect(firstRow).toBe(0)
    const lastBase = rowCount * perRow
    const expected = (store.search.hits as { start: number }[]).map((h, i) => ({ h, i })).filter(({ h }) => h.start <= lastBase - 4)
    for (const { i } of expected) expect(shown.has(`s:${i}`)).toBe(true)
  })
})

describe('性能护栏', () => {
  it('300 kb + 3000 特征 + 2000 搜索命中：挂载与 200 次悬停都在预算内', async () => {
    const d = bigDoc()
    const t0 = performance.now()
    const w = mount(SequenceMap, { props: { doc: d, height: 400, width: 900 } })
    const mountMs = performance.now() - t0
    const store = storeOf(w)
    store.runSearch('A', 0) // 命中数被截到 2000 上限
    await nextTick()
    expect(store.search.hits.length).toBe(2000)

    const bars = w.findAll('.smap-bar')
    expect(bars.length).toBeGreaterThan(1)
    const t1 = performance.now()
    for (let i = 0; i < 200; i++) {
      // 交替两个元件（每次都换悬停对象）+ 同一元件上的移动
      await bars[i % 2].trigger('pointermove', { clientX: i, clientY: 10 })
    }
    const hoverMs = performance.now() - t1
    // 本机实测：挂载 ~100 ms、200 次悬停 ~380 ms（修复前悬停约 10.9 s）；阈值留约 4 倍余量防 CI 抖动
    expect(mountMs).toBeLessThan(1000)
    expect(hoverMs).toBeLessThan(1500)
  })
})
