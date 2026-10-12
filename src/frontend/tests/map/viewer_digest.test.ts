/** MapViewer 侧栏「消化」：片段列表与虚拟凝胶 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import type { MapDocument } from '@/map/types'
import pUC19 from '../fixtures/map/pUC19.json'

const apiMocks = vi.hoisted(() => ({ getEnzymeSets: vi.fn(), analyzeMap: vi.fn(), exportGenbank: vi.fn() }))
vi.mock('@/map/api', () => apiMocks)

import MapViewer from '@/map/components/MapViewer.vue'

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

describe('酶切消化面板', () => {
  it('选两个酶 → 片段列表 + 凝胶条带；最多选 3 个；点片段选中区域', async () => {
    const w = mount(MapViewer, { props: { doc: puc() } })
    await flushPromises()
    await w.findAll('.mv-tab').find((t) => t.text() === '消化')!.trigger('click')
    expect(w.find('.dg-empty').exists()).toBe(true)
    await w.find('input[data-enzyme="EcoRI"]').setValue(true)
    await w.find('input[data-enzyme="HindIII"]').setValue(true)
    const frags = w.findAll('.dg-frag')
    expect(frags).toHaveLength(2)
    expect(frags[0].text()).toContain('2,635 bp')
    expect(frags[0].text()).toContain('跨原点')
    expect(frags[1].text()).toContain('51 bp')
    expect(w.findAll('.dg-band')).toHaveLength(2)
    expect(w.findAll('.dg-ladder rect').length).toBe(10)

    await w.find('input[data-enzyme="BamHI"]').setValue(true)
    const fourth = w.findAll('.dg-enz-list input').find((i) => !(i.element as HTMLInputElement).checked)!
    expect((fourth.element as HTMLInputElement).disabled).toBe(true)
    expect(w.findAll('.dg-frag')).toHaveLength(3)

    await w.findAll('.dg-frag').find((f) => f.text().includes('21 bp'))!.trigger('click')
    expect(w.find('.mv-selection-text').text()).toContain('397..417')
  })

  it('阻断位点可选择跳过', async () => {
    const w = mount(MapViewer, { props: { doc: withBlocked() } })
    await flushPromises()
    await w.findAll('.mv-tab').find((t) => t.text() === '消化')!.trigger('click')
    await w.find('input[data-enzyme="ClaI"]').setValue(true)
    expect(w.find('.dg-blocked').text()).toContain('dam')
    expect(w.find('.dg-summary').text()).toContain('未切开')
    await w.find('.dg-blocked input').setValue(false)
    expect(w.findAll('.dg-frag')).toHaveLength(1)
    expect(w.find('.dg-summary').text()).toContain('1 个切口')
  })
})
