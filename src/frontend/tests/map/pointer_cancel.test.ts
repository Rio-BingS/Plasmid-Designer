/** 指针被浏览器接管（pointercancel / lostpointercapture）时拖拽状态复位（审查 M2） */
import { describe, it, expect } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import type { MapDocument } from '@/map/types'
import pUC19 from '../fixtures/map/pUC19.json'
import CircularMap from '@/map/components/CircularMap.vue'
import LinearMap from '@/map/components/LinearMap.vue'
import SequenceMap from '@/map/components/SequenceMap.vue'
import Minimap from '@/map/components/Minimap.vue'

const puc = () => JSON.parse(JSON.stringify(pUC19)) as MapDocument
type AnyStore = {
  selection: { value: unknown }
  caret: { value: unknown }
  viewport: { value: { from: number; to: number } }
  navigate: { value: { n: number } }
  setViewport?: unknown
}
const storeOf = (w: VueWrapper) => (w.vm as unknown as { $: { setupState: { store: AnyStore } } }).$.setupState.store

function rect(el: Element, width: number, height: number) {
  ;(el as HTMLElement).getBoundingClientRect = () => ({ left: 0, top: 0, width, height, right: width, bottom: height, x: 0, y: 0, toJSON() {} }) as DOMRect
  ;(el as unknown as { getScreenCTM: () => null }).getScreenCTM = () => null
}

const EVENTS = ['pointercancel', 'lostpointercapture'] as const

describe.each(EVENTS)('%s 复位拖拽', (ev) => {
  it('SequenceMap：取消后再移动不再改选区，也不落光标', async () => {
    const w = mount(SequenceMap, { props: { doc: puc(), height: 300, width: 900 } })
    const inner = w.find('.smap-inner')
    rect(inner.element, 900, 1000)
    await inner.trigger('pointerdown', { button: 0, clientX: 44, clientY: 40, pointerId: 1 })
    await inner.trigger(ev, { pointerId: 1 })
    await inner.trigger('pointermove', { clientX: 44 + 8 * 20, clientY: 40, pointerId: 1 })
    await inner.trigger('pointerup', { clientX: 44 + 8 * 20, clientY: 40, pointerId: 1 })
    expect(storeOf(w).selection.value).toBeNull()
    expect(w.findAll('.smap-selection')).toHaveLength(0)
  })

  it('LinearMap：取消后再移动不再改选区', async () => {
    const w = mount(LinearMap, { props: { doc: puc(), width: 1000 } })
    const svg = w.find('.lmap-svg')
    rect(svg.element, 1000, 300)
    await svg.trigger('pointerdown', { button: 0, clientX: 100, clientY: 50, pointerId: 1 })
    await svg.trigger(ev, { pointerId: 1 })
    await svg.trigger('pointermove', { clientX: 600, clientY: 50, pointerId: 1 })
    await svg.trigger('pointerup', { clientX: 600, clientY: 50, pointerId: 1 })
    expect(storeOf(w).selection.value).toBeNull()
  })

  it('CircularMap：取消后再移动不再改选区', async () => {
    const w = mount(CircularMap, { props: { doc: puc(), size: 600 } })
    const svg = w.find('.cmap-svg')
    rect(svg.element, Number(svg.attributes('width')), Number(svg.attributes('height')))
    await svg.trigger('pointerdown', { button: 0, clientX: 300, clientY: 20, pointerId: 1 })
    await svg.trigger(ev, { pointerId: 1 })
    await svg.trigger('pointermove', { clientX: 580, clientY: 300, pointerId: 1 })
    await svg.trigger('pointerup', { clientX: 580, clientY: 300, pointerId: 1 })
    expect(storeOf(w).selection.value).toBeNull()
    expect(w.find('.cmap-selection').exists()).toBe(false)
  })

  it('Minimap：取消后再移动不再跳转', async () => {
    const w = mount(Minimap, { props: { doc: puc(), follow: 'sequence' } })
    const box = w.find('.minimap')
    rect(box.element, 800, 26)
    await box.trigger('pointerdown', { button: 0, clientX: 100, clientY: 10, pointerId: 1 })
    const n = storeOf(w).navigate.value.n
    await box.trigger(ev, { pointerId: 1 })
    await box.trigger('pointermove', { clientX: 500, clientY: 10, pointerId: 1 })
    expect(storeOf(w).navigate.value.n).toBe(n)
  })
})

describe('正常 pointerup 之后的 lostpointercapture 无副作用', () => {
  it('SequenceMap 拖选结果保留', async () => {
    const w = mount(SequenceMap, { props: { doc: puc(), height: 300, width: 900 } })
    const inner = w.find('.smap-inner')
    rect(inner.element, 900, 1000)
    await inner.trigger('pointerdown', { button: 0, clientX: 44, clientY: 40, pointerId: 1 })
    await inner.trigger('pointermove', { clientX: 44 + 8 * 10, clientY: 40, pointerId: 1 })
    await inner.trigger('pointerup', { clientX: 44 + 8 * 10, clientY: 40, pointerId: 1 })
    await inner.trigger('lostpointercapture', { pointerId: 1 })
    expect(storeOf(w).selection.value).not.toBeNull()
  })
})
