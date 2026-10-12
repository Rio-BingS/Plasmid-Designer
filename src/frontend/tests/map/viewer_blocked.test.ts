/** MapViewer：dam/dcm 甲基化阻断位点显示与位点级突出端提示 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import type { MapDocument } from '@/map/types'
import pUC19 from '../fixtures/map/pUC19.json'

const apiMocks = vi.hoisted(() => ({ getEnzymeSets: vi.fn(), analyzeMap: vi.fn(), exportGenbank: vi.fn() }))
vi.mock('@/map/api', () => apiMocks)

import MapViewer from '@/map/components/MapViewer.vue'
import { resolveHit } from '@/map/hits'

const puc = () => JSON.parse(JSON.stringify(pUC19)) as MapDocument

/** pUC19 + 一个被 dam 阻断的 ClaI 位点（带位点级突出端） */
function withBlocked(): MapDocument {
  const d = puc()
  d.enzymes.push({
    name: 'ClaI', recognition: 'AT^CGAT', ends: "5'", overhang: 'CG', cut_count: 1, methylation: ['dam'],
    sites: [{ position: 2000, cut_top: 2001, cut_bottom: 2003, strand: 1, blocked_by: ['dam'], overhang: 'CG' }],
  })
  // 夹具里的 BsaI（IIS 型，酶级 overhang = NNNN）补上位点级实际突出端
  const bsa = d.enzymes.find((e) => e.name === 'BsaI')!
  bsa.sites[0] = { ...bsa.sites[0], overhang: 'AATG' }
  return d
}

beforeEach(() => {
  vi.clearAllMocks()
  apiMocks.getEnzymeSets.mockResolvedValue([])
})

describe('甲基化阻断位点', () => {
  it('灰色虚线显示；开关可隐藏；提示含阻断说明与位点级突出端', async () => {
    const w = mount(MapViewer, { props: { doc: withBlocked(), initialView: 'circular' } })
    await flushPromises()
    const blocked = w.findAll('.cmap-cut.is-blocked')
    expect(blocked).toHaveLength(1)
    expect(blocked[0].attributes('stroke-dasharray')).toBe('3 2')
    expect(blocked[0].attributes('stroke')).toBe('#9ca3af')

    const vm = w.vm as unknown as { store: Parameters<typeof resolveHit>[0] }
    const tip = resolveHit(vm.store, 'e:ClaI#0')!
    expect(tip.lines.join('\n')).toContain('被 dam 甲基化阻断')
    const gg = resolveHit(vm.store, 'e:BsaI#0')!
    expect(gg.lines.join('\n')).toContain('本位点突出端 AATG')

    const toggle = w.find('.mv-blocked-toggle input')
    expect(w.find('.mv-blocked-toggle').text()).toContain('显示甲基化阻断位点')
    await toggle.setValue(false)
    expect(w.findAll('.cmap-cut.is-blocked')).toHaveLength(0)
    expect(w.findAll('.cmap-cut').length).toBeGreaterThan(0)
  })

  it('没有阻断数据时不显示开关', async () => {
    const w = mount(MapViewer, { props: { doc: puc() } })
    await flushPromises()
    expect(w.find('.mv-blocked-toggle').exists()).toBe(false)
  })
})
