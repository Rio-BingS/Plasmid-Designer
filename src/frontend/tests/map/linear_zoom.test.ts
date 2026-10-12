import { describe, it, expect } from 'vitest'
import { nextTick } from 'vue'
import { mount } from '@vue/test-utils'
import type { MapDocument } from '@/map/types'
import pUC19 from '../fixtures/map/pUC19.json'
import LinearMap from '@/map/components/LinearMap.vue'

const puc = () => JSON.parse(JSON.stringify(pUC19)) as MapDocument
type Store = { select: (r: { start: number; end: number }) => void; viewport: { value: { from: number; to: number } } }
const storeOf = (w: ReturnType<typeof mount>) => (w.vm as unknown as { $: { setupState: { store: Store } } }).$.setupState.store

describe('LinearMap 按钮缩放', () => {
  it('有选区时放大以选区为中心（选区不被挤出视口）', async () => {
    const w = mount(LinearMap, { props: { doc: puc(), width: 1000 } })
    const store = storeOf(w)
    store.select({ start: 2400, end: 2500 })
    await nextTick()
    for (let i = 0; i < 4; i++) await w.find('.lmap-zoom-in').trigger('click')
    const { from, to } = store.viewport.value
    expect(to - from).toBeLessThan(2686 / 5)
    expect(from).toBeLessThanOrEqual(2399)
    expect(to).toBeGreaterThanOrEqual(2500)
    expect(Math.abs((from + to) / 2 - 2449.5)).toBeLessThan(1)
  })

  it('无选区时仍以视口中心缩放', async () => {
    const w = mount(LinearMap, { props: { doc: puc(), width: 1000 } })
    await w.find('.lmap-zoom-in').trigger('click')
    const { from, to } = storeOf(w).viewport.value
    expect(Math.abs((from + to) / 2 - 1343)).toBeLessThan(1)
  })
})
