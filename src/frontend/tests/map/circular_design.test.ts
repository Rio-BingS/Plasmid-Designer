import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import type { MapDocument, MapEnzyme } from '@/map/types'
import pUC19 from '../fixtures/map/pUC19.json'
import CircularMap from '@/map/components/CircularMap.vue'
import { boxesOverlap, leadersCross } from '@/map/core/labels'

const puc = () => JSON.parse(JSON.stringify(pUC19)) as MapDocument

/** 两个酶切在同一位置（同裂酶），另加一个单切点酶 */
function withIsoschizomers(): MapDocument {
  const d = puc()
  const mk = (name: string, cut: number, count = 1): MapEnzyme => ({
    name, recognition: 'G^AATTC', ends: "5'", overhang: 'AATT', cut_count: count,
    sites: Array.from({ length: count }, (_, i) => ({ position: cut + i * 300, cut_top: cut + i * 300, cut_bottom: cut + i * 300 + 4, strand: 1 as const })),
  })
  d.enzymes = [mk('EcoRI', 396), mk('Zzz1I', 396), mk('NdeI', 184)]
  return d
}

describe('CircularMap 视觉：同切点合并、切点位置、色块标签', () => {
  it('同一切点的酶合并成一行，各酶名可单独悬停；右列名字后写切点位置', () => {
    const w = mount(CircularMap, { props: { doc: withIsoschizomers(), size: 720 } })
    const enz = w.findAll('.cmap-label-enzyme')
    // 3 个切点 → 2 个标签（396 合并）
    expect(enz).toHaveLength(2)
    const merged = enz.find((e) => e.text().includes('EcoRI'))!
    expect(merged.text()).toMatch(/EcoRI - Zzz1I/)
    expect(merged.text()).toContain('(396)')
    const hits = merged.findAll('tspan[data-hit]').map((t) => t.attributes('data-hit'))
    expect(hits).toEqual(['e:EcoRI#0', 'e:Zzz1I#0'])
    // 单切点粗体
    expect(merged.find('tspan[data-hit]').attributes('font-weight')).toBe('700')
    // 切点刻线仍是每个酶一条
    expect(w.findAll('.cmap-cut')).toHaveLength(3)
  })

  it('窄屏（compact）不写切点位置，把宽度让给环', () => {
    const w = mount(CircularMap, { props: { doc: withIsoschizomers(), size: 320 } })
    for (const t of w.findAll('.cmap-label-enzyme')) expect(t.text()).not.toMatch(/\(\d+\)/)
  })

  it('长特征名写在弧内（textPath），放不下的特征用色块标签', () => {
    const w = mount(CircularMap, { props: { doc: puc(), size: 720 } })
    const inside = w.findAll('textPath').map((t) => t.text())
    expect(inside).toContain('ori')
    const chips = w.findAll('.cmap-label-chip')
    expect(chips.length).toBeGreaterThan(0)
    for (const c of chips) {
      expect(c.find('rect').attributes('fill')).toMatch(/^#[0-9a-f]{6}$/)
      expect(inside).not.toContain(c.text())
    }
  })

  it('渲染出的外置标签框不重叠、引线不交叉（pUC19 MCS 扎堆）', () => {
    const w = mount(CircularMap, { props: { doc: puc(), size: 640 } })
    const groups = w.findAll('.cmap-label')
    expect(groups.length).toBeGreaterThan(8)
    const leaders = groups.map((g) => g.find('polyline').attributes('points')!.split(' ').map((p) => p.split(',').map(Number) as [number, number]))
    for (let i = 0; i < leaders.length; i++) {
      expect(leaders[i]).toHaveLength(4)
      for (let j = i + 1; j < leaders.length; j++) expect(leadersCross(leaders[i], leaders[j])).toBe(false)
    }
    // 色块标签的框（rect）两两不重叠
    const rects = w.findAll('.cmap-label-chip rect').map((r) => ({
      x: Number(r.attributes('x')), y: Number(r.attributes('y')), w: Number(r.attributes('width')), h: Number(r.attributes('height')),
    }))
    for (let i = 0; i < rects.length; i++) for (let j = i + 1; j < rects.length; j++) expect(boxesOverlap(rects[i], rects[j])).toBe(false)
  })

  it('悬停特征：其余特征变淡（类名），被悬停者加外发光滤镜', async () => {
    const w = mount(CircularMap, { props: { doc: puc(), size: 720 }, attachTo: document.body })
    const g = w.findAll('.cmap-feat')[0]
    await g.find('path').trigger('pointermove', { clientX: 10, clientY: 10 })
    expect(w.find('.cmap-svg').classes()).toContain('has-feat-hover')
    expect(w.find('.cmap-feat.is-hover').attributes('filter')).toMatch(/url\(#cm\d+-glow\)/)
    w.unmount()
  })
})
