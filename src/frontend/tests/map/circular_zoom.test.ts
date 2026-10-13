import { describe, it, expect, vi } from 'vitest'
import { ref, nextTick } from 'vue'
import { mount, type VueWrapper } from '@vue/test-utils'
import type { MapDocument } from '@/map/types'
import pUC19 from '../fixtures/map/pUC19.json'

// 比 maxSize（900）更宽的面板：放大后 svg 应占满这 1200 px，而不是被裁在 900 的方框里
vi.mock('@vueuse/core', () => ({ useElementSize: () => ({ width: ref(1200), height: ref(0) }) }))

import CircularMap from '@/map/components/CircularMap.vue'

const puc = () => JSON.parse(JSON.stringify(pUC19)) as MapDocument

interface View { x: number; y: number; w: number; h: number }
function view(w: VueWrapper): View {
  const [x, y, vw, vh] = w.find('.cmap-svg').attributes('viewBox')!.split(/\s+/).map(Number)
  return { x, y, w: vw, h: vh }
}
/** 外链圆（圆心与半径） */
function ring(w: VueWrapper) {
  const c = w.findAll('.cmap-strand')[0]
  return { cx: Number(c.attributes('cx')), cy: Number(c.attributes('cy')), r: Number(c.attributes('r')) }
}
/** 环上落在可视区内的部分占整圈的比例 */
function ringVisibleFraction(w: VueWrapper) {
  const v = view(w)
  const { cx, cy, r } = ring(w)
  let n = 0
  for (let i = 0; i < 720; i++) {
    const a = (i / 720) * Math.PI * 2
    const x = cx + r * Math.sin(a)
    const y = cy - r * Math.cos(a)
    if (x >= v.x && x <= v.x + v.w && y >= v.y && y <= v.y + v.h) n++
  }
  return n / 720
}
const inView = (v: View, x: number, y: number) => x >= v.x && x <= v.x + v.w && y >= v.y && y <= v.y + v.h

async function clickZoomIn(w: VueWrapper, times: number) {
  for (let i = 0; i < times; i++) await w.find('.cmap-zoom-in').trigger('click')
  await nextTick()
}

/** happy-dom 不做布局：给 svg 一个与其 width/height 属性一致的包围盒，让指针坐标换算可用 */
function stubRect(w: VueWrapper) {
  const el = w.find('.cmap-svg').element as SVGSVGElement
  el.getBoundingClientRect = () => {
    const width = Number(el.getAttribute('width'))
    const height = Number(el.getAttribute('height'))
    return { left: 0, top: 0, right: width, bottom: height, width, height, x: 0, y: 0, toJSON: () => ({}) } as DOMRect
  }
  // 强制走包围盒换算
  ;(el as unknown as { getScreenCTM: () => null }).getScreenCTM = () => null
  return el
}
/** 用户坐标 → 客户端像素（与 stubRect 对应） */
function toClient(w: VueWrapper, x: number, y: number) {
  const v = view(w)
  const el = w.find('.cmap-svg').element
  return {
    clientX: ((x - v.x) / v.w) * Number(el.getAttribute('width')),
    clientY: ((y - v.y) / v.h) * Number(el.getAttribute('height')),
  }
}

describe('CircularMap 按钮/滑块缩放', () => {
  it('无选区时放大锚在 12 点钟的环上：环留在视野里，中心名称不再填满视图', async () => {
    const w = mount(CircularMap, { props: { doc: puc() } })
    const { cx, cy, r } = ring(w)
    await clickZoomIn(w, 4) // 1.4^4 ≈ 3.8×
    const v = view(w)
    expect(v.w).toBeLessThan(1200 / 3)
    // 12 点钟的环上点在视野内，且大致在水平中央
    expect(inView(v, cx, cy - r)).toBe(true)
    expect(Math.abs(v.x + v.w / 2 - cx)).toBeLessThan(1)
    // 圆心（名称 + bp 长度）不在视野内
    expect(inView(v, cx, cy)).toBe(false)
    expect(ringVisibleFraction(w)).toBeGreaterThan(0.05)
  })

  it('有选区时放大锚在选区中点（3 点钟方向的选区 → 视图移到环的右侧）', async () => {
    const doc = puc()
    const L = doc.length
    const w = mount(CircularMap, { props: { doc } })
    const { cx, cy, r } = ring(w)
    // 选区中点在 L/4（旋转 0 时位于 3 点钟方向）
    const store = (w.vm as unknown as { $: { setupState: { store: { select: (r: { start: number; end: number }) => void } } } }).$.setupState.store
    store.select({ start: Math.round(L / 4) - 50, end: Math.round(L / 4) + 50 })
    await nextTick()
    await clickZoomIn(w, 4)
    const v = view(w)
    expect(inView(v, cx + r, cy)).toBe(true)
    expect(inView(v, cx, cy)).toBe(false)
    expect(inView(v, cx, cy - r)).toBe(false)
  })

  it('滑块缩放同样锚在环上；缩小回 1× 时复位平移', async () => {
    const w = mount(CircularMap, { props: { doc: puc() } })
    const { cx, cy, r } = ring(w)
    const slider = w.find('input.cmap-zoom')
    await slider.setValue('2') // 4×
    let v = view(w)
    expect(v.w).toBeCloseTo(1200 / 4, 0)
    expect(inView(v, cx, cy - r)).toBe(true)
    expect(inView(v, cx, cy)).toBe(false)
    await slider.setValue('0')
    v = view(w)
    expect(Number(w.find('.cmap-svg').attributes('width'))).toBe(900)
    expect(v.x).toBe(0)
    expect(v.w).toBe(900)
  })

  it('放大后 svg 占满容器整宽（不再裁在 maxSize 方框的左右两侧）', async () => {
    const w = mount(CircularMap, { props: { doc: puc() } })
    expect(Number(w.find('.cmap-svg').attributes('width'))).toBe(900)
    const h0 = Number(w.find('.cmap-svg').attributes('height'))
    await clickZoomIn(w, 1)
    expect(Number(w.find('.cmap-svg').attributes('width'))).toBe(1200)
    // 高度不变（不引起下方内容跳动），宽高比与 viewBox 一致（不变形）
    expect(Number(w.find('.cmap-svg').attributes('height'))).toBe(h0)
    const v = view(w)
    expect(v.w / v.h).toBeCloseTo(1200 / h0, 2)
  })

  it('固定 size 的图（缩略图等）放大后仍保持原宽度', async () => {
    const w = mount(CircularMap, { props: { doc: puc(), size: 600 } })
    await clickZoomIn(w, 2)
    expect(Number(w.find('.cmap-svg').attributes('width'))).toBe(600)
  })
})

describe('CircularMap 放大后平移', () => {
  it('拖拽中心空白区 = 平移（不再旋转）', async () => {
    const w = mount(CircularMap, { props: { doc: puc() } })
    stubRect(w)
    const { cx, cy } = ring(w)
    await clickZoomIn(w, 2)
    const v0 = view(w)
    const rot0 = (w.find('.cmap-rot input').element as HTMLInputElement).value
    // 在视野底部、环内侧的中心空白区按下（12 点钟锚点正下方）
    const p = toClient(w, cx, Math.min(v0.y + v0.h * 0.95, cy - 10))
    await w.find('.cmap-svg').trigger('pointerdown', { ...p, button: 0, pointerId: 1 })
    // 向右上拖：视野往左下移（视野顶边已贴着内容框，再往上会被限制）
    await w.find('.cmap-svg').trigger('pointermove', { clientX: p.clientX + 40, clientY: p.clientY - 30, pointerId: 1 })
    await w.find('.cmap-svg').trigger('pointerup', { clientX: p.clientX + 40, clientY: p.clientY - 30, button: 0, pointerId: 1 })
    const v1 = view(w)
    expect(v1.x).toBeLessThan(v0.x)
    expect(v1.y).toBeGreaterThan(v0.y)
    expect((w.find('.cmap-rot input').element as HTMLInputElement).value).toBe(rot0)
  })

  it('平移受限：怎么拖环都不会整个移出视野，也不会拖到内容框外', async () => {
    const w = mount(CircularMap, { props: { doc: puc() } })
    stubRect(w)
    const { cx, cy, r } = ring(w)
    await clickZoomIn(w, 6) // ≈7.5×
    for (const [dx, dy] of [[5000, 0], [-5000, 0], [0, 5000], [0, -5000], [4000, 4000], [-4000, -4000]]) {
      // 每次从视野角落（环带外的空白）开始拖
      const v0 = view(w)
      const start = { clientX: 3, clientY: 3 }
      const ux = v0.x + (3 / Number(w.find('.cmap-svg').attributes('width'))) * v0.w
      const uy = v0.y + (3 / Number(w.find('.cmap-svg').attributes('height'))) * v0.h
      const rr = Math.hypot(ux - cx, uy - cy)
      // 角落若恰好落在环带上就跳过这次（那里是选区拖拽）
      if (rr > r - 60 && rr < r + 40) continue
      await w.find('.cmap-svg').trigger('pointerdown', { ...start, button: 0, pointerId: 1 })
      await w.find('.cmap-svg').trigger('pointermove', { clientX: 3 + dx, clientY: 3 + dy, pointerId: 1 })
      await w.find('.cmap-svg').trigger('pointerup', { clientX: 3 + dx, clientY: 3 + dy, button: 0, pointerId: 1 })
      const v = view(w)
      expect(ringVisibleFraction(w)).toBeGreaterThan(0)
      // 视野中心不出方形画布
      expect(v.x + v.w / 2).toBeGreaterThanOrEqual(0)
      expect(v.x + v.w / 2).toBeLessThanOrEqual(2 * cx)
    }
    // 确实平移过（不是每次都被跳过）
    const v = view(w)
    expect(Math.hypot(v.x + v.w / 2 - cx, v.y + v.h / 2 - cy)).toBeGreaterThan(r * 0.5)
  })

  it('Ctrl+滚轮放大后同样受平移限制', async () => {
    const w = mount(CircularMap, { props: { doc: puc() } })
    stubRect(w)
    // 在画布左上角（远离环）Ctrl+滚轮猛放大
    for (let i = 0; i < 20; i++) await w.find('.cmap-svg').trigger('wheel', { clientX: 1, clientY: 1, deltaY: -400, ctrlKey: true })
    expect(ringVisibleFraction(w)).toBeGreaterThan(0)
  })
})

describe('CircularMap 放大后 svg 高度', () => {
  it('放大后 svg 随缩放长高（不超过窗口 75%），可视区不再只剩一条矮横带', async () => {
    const old = window.innerHeight
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: 1000 })
    const w = mount(CircularMap, { props: { doc: puc(), maxSize: 640 } })
    const h0 = Number(w.find('.cmap-svg').attributes('height'))
    await clickZoomIn(w, 3) // ≈ 2.7×
    const h1 = Number(w.find('.cmap-svg').attributes('height'))
    expect(h1).toBeGreaterThan(h0)
    expect(h1).toBeLessThanOrEqual(Math.max(h0, 750))
    // 可视区高度 = 像素高 / 缩放，而不是裁切高 / 缩放
    const v = view(w)
    expect(v.h).toBeGreaterThan(h0 / 2.744 + 1)
    // 复位后回到原高
    await w.find('.cmap-controls button[title="复位"]').trigger('click')
    await nextTick()
    expect(Number(w.find('.cmap-svg').attributes('height'))).toBe(h0)
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: old })
    w.unmount()
  })

  it('指定 size（导出/缩略图）时高度不变', async () => {
    const w = mount(CircularMap, { props: { doc: puc(), size: 400 } })
    const h0 = Number(w.find('.cmap-svg').attributes('height'))
    await clickZoomIn(w, 3)
    expect(Number(w.find('.cmap-svg').attributes('height'))).toBe(h0)
  })
})
