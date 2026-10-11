import { describe, it, expect } from 'vitest'
import { shallowRef } from 'vue'
import { mount } from '@vue/test-utils'
import MapTooltip from '@/map/components/MapTooltip.vue'

function storeAt(x: number, y: number) {
  return { hover: shallowRef({ kind: 'feature', id: 'f:1', title: 'AmpR', lines: ['抗性 · 正链', '1..861'], range: null, x, y }) } as any
}

describe('MapTooltip 定位', () => {
  it('默认在指针右下方', () => {
    const w = mount(MapTooltip, { props: { store: storeAt(100, 100) } })
    expect(w.find('.map-tooltip').attributes('style')).toContain('top: 114px')
  })
  it('贴近视口底边时翻到指针上方，不被裁掉', () => {
    const y = window.innerHeight - 20
    const w = mount(MapTooltip, { props: { store: storeAt(100, y) } })
    const top = Number(/top: (-?\d+(?:\.\d+)?)px/.exec(w.find('.map-tooltip').attributes('style')!)![1])
    expect(top).toBeLessThan(y)
    expect(top).toBeGreaterThanOrEqual(4)
  })
})
