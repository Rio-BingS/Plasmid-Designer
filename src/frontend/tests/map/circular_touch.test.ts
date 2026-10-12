/** 环形图触屏（审查 M3）：未放大时不压住页面滚动；长按才选区；轻点照常；双指缩放 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import type { MapDocument } from '@/map/types'
import pUC19 from '../fixtures/map/pUC19.json'
import CircularMap from '@/map/components/CircularMap.vue'

const puc = () => JSON.parse(JSON.stringify(pUC19)) as MapDocument
type S = { selection: { value: unknown }; caret: { value: unknown } }
const storeOf = (w: VueWrapper) => (w.vm as unknown as { $: { setupState: { store: S } } }).$.setupState.store

function setup(props: Record<string, unknown> = {}) {
  const w = mount(CircularMap, { props: { doc: puc(), size: 600, ...props }, attachTo: document.body })
  const svg = w.find('.cmap-svg')
  const el = svg.element as SVGSVGElement
  el.getBoundingClientRect = () => {
    const width = Number(el.getAttribute('width'))
    const height = Number(el.getAttribute('height'))
    return { left: 0, top: 0, right: width, bottom: height, width, height, x: 0, y: 0, toJSON: () => ({}) } as DOMRect
  }
  ;(el as unknown as { getScreenCTM: () => null }).getScreenCTM = () => null
  return { w, svg, el }
}
/** 环上角度 a（弧度，12 点为 0）对应的客户端坐标 */
function ringPt(w: VueWrapper, a: number) {
  const [x0, y0, vw, vh] = w.find('.cmap-svg').attributes('viewBox')!.split(/\s+/).map(Number)
  const c = w.findAll('.cmap-strand')[0]
  const cx = Number(c.attributes('cx')); const cy = Number(c.attributes('cy')); const r = Number(c.attributes('r'))
  const el = w.find('.cmap-svg').element
  return {
    clientX: ((cx + r * Math.sin(a) - x0) / vw) * Number(el.getAttribute('width')),
    clientY: ((cy - r * Math.cos(a) - y0) / vh) * Number(el.getAttribute('height')),
  }
}
const touch = (pointerId: number, p: { clientX: number; clientY: number }) => ({ pointerType: 'touch', button: 0, pointerId, ...p })

afterEach(() => { vi.useRealTimers(); document.body.innerHTML = '' })

describe('CircularMap 触屏', () => {
  it('未放大时 touch-action 为 pan-y（页面可滚动），放大后为 none', async () => {
    const { w, svg } = setup()
    expect((svg.element as SVGElement).style.touchAction).toBe('pan-y')
    await w.find('.cmap-zoom-in').trigger('click')
    expect((w.find('.cmap-svg').element as SVGElement).style.touchAction).toBe('none')
  })

  it('单指直接滑动（未长按）不选区，交给浏览器滚动；touchmove 不被阻止', async () => {
    vi.useFakeTimers()
    const { w, svg, el } = setup()
    await svg.trigger('pointerdown', touch(1, ringPt(w, 3.4)))
    await svg.trigger('pointermove', touch(1, ringPt(w, 3.8)))
    const tm = new Event('touchmove', { cancelable: true }) as TouchEvent
    Object.defineProperty(tm, 'touches', { value: [{}] })
    el.dispatchEvent(tm)
    expect(tm.defaultPrevented).toBe(false)
    vi.advanceTimersByTime(1000)
    await svg.trigger('pointermove', touch(1, ringPt(w, 4.2)))
    await svg.trigger('pointerup', touch(1, ringPt(w, 4.2)))
    expect(storeOf(w).selection.value).toBeNull()
  })

  it('长按后拖动 = 选区，期间阻止页面滚动', async () => {
    vi.useFakeTimers()
    const { w, svg, el } = setup()
    await svg.trigger('pointerdown', touch(1, ringPt(w, 3.4)))
    vi.advanceTimersByTime(400)
    const tm = new Event('touchmove', { cancelable: true }) as TouchEvent
    Object.defineProperty(tm, 'touches', { value: [{}] })
    el.dispatchEvent(tm)
    expect(tm.defaultPrevented).toBe(true)
    await svg.trigger('pointermove', touch(1, ringPt(w, 3.6)))
    await svg.trigger('pointermove', touch(1, ringPt(w, 3.8)))
    await svg.trigger('pointerup', touch(1, ringPt(w, 3.8)))
    expect(storeOf(w).selection.value).not.toBeNull()
  })

  it('轻点特征仍是选中整段', async () => {
    vi.useFakeTimers()
    const { w } = setup()
    const feat = w.find('.cmap-feat path')
    await feat.trigger('pointerdown', touch(1, { clientX: 0, clientY: 0 }))
    await feat.trigger('pointerup', touch(1, { clientX: 0, clientY: 0 }))
    expect(storeOf(w).selection.value).not.toBeNull()
    expect(w.find('.cmap-selection').exists()).toBe(true)
  })

  it('双指捏合放大，抬起后不残留选区', async () => {
    const { w, svg } = setup()
    const vb0 = w.find('.cmap-svg').attributes('viewBox')
    await svg.trigger('pointerdown', touch(1, { clientX: 250, clientY: 300 }))
    await svg.trigger('pointerdown', touch(2, { clientX: 350, clientY: 300 }))
    await svg.trigger('pointermove', touch(1, { clientX: 150, clientY: 300 }))
    await svg.trigger('pointermove', touch(2, { clientX: 450, clientY: 300 }))
    expect(w.find('.cmap-svg').attributes('viewBox')).not.toBe(vb0)
    const vw = Number(w.find('.cmap-svg').attributes('viewBox')!.split(/\s+/)[2])
    expect(vw).toBeLessThan(Number(vb0!.split(/\s+/)[2]))
    await svg.trigger('pointerup', touch(1, { clientX: 150, clientY: 300 }))
    await svg.trigger('lostpointercapture', touch(1, { clientX: 150, clientY: 300 }))
    await svg.trigger('pointermove', touch(2, { clientX: 500, clientY: 320 }))
    await svg.trigger('pointerup', touch(2, { clientX: 500, clientY: 320 }))
    expect(storeOf(w).selection.value).toBeNull()
  })

  it('鼠标交互不受影响：按下即拖选', async () => {
    const { w, svg } = setup()
    await svg.trigger('pointerdown', { pointerType: 'mouse', button: 0, pointerId: 1, ...ringPt(w, 3.4) })
    await svg.trigger('pointermove', { pointerType: 'mouse', pointerId: 1, ...ringPt(w, 3.8) })
    await svg.trigger('pointerup', { pointerType: 'mouse', pointerId: 1, ...ringPt(w, 3.8) })
    expect(storeOf(w).selection.value).not.toBeNull()
  })
})
