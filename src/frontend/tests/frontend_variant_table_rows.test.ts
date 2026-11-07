import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import VariantTable from '@/components/sequencing/VariantTable.vue'
import type { SequencingVariant } from '@/api'

// T22（goal A 5.4）：unresolved_sites 与 MixedVariant 进差异明细列表可跳转
//（rowKind='site'/'mixed' 行）；点击行上报整行给父组件（跳峰图+证据包同位出图）

const variant: SequencingVariant = {
  ref_pos: 500, type: 'substitution', ref_base: 'C', alt_base: 'A',
  length: 1, confidence: 'high', status: 'confirmed', action: 'accept',
  p_real: 0.997,
}

const base = {
  variants: [variant],
  unresolvedSites: [
    { ref_pos: 300, kind: 'n_call', reads: ['r1.ab1'], detail: 'read 调用 N（未解析）' },
  ],
  mixedVariants: [
    { ref_pos: 700, ref_base: 'A', called_base: 'G', secondary_base: 'T', af: 0.35, reads: ['r2.ab1'] },
  ],
}

describe('VariantTable T22 rows', () => {
  it('未决位点与混合位点出现在列表中，类型/变化列可辨', () => {
    const w = mount(VariantTable, { props: base })
    const rows = w.findAll('tbody tr')
    expect(rows).toHaveLength(3)
    const text = rows.map((r) => r.text()).join('\n')
    expect(text).toContain('N/模糊调用')
    expect(text).toContain('混合位点')
    expect(text).toContain('G / T（AF 0.35）')
  })

  it('点击未决/混合行上报整行（ref_pos + rowKind），点击变体行照旧', async () => {
    const w = mount(VariantTable, { props: base })
    const rows = w.findAll('tbody tr')
    await rows[0].trigger('click')   // 未决位点 300
    expect(w.emitted('jump')![0][0]).toMatchObject({ rowKind: 'site', ref_pos: 300 })
    await rows[1].trigger('click')   // 混合位点 700
    expect(w.emitted('jump')![1][0]).toMatchObject({ rowKind: 'mixed', ref_pos: 700 })
    await rows[2].trigger('click')   // 变体 500
    expect(w.emitted('jump')![2][0]).toMatchObject({ rowKind: 'variant', ref_pos: 500 })
  })

  it('旧记录无未决/混合数据时只显示变体行（向后兼容）', () => {
    const w = mount(VariantTable, { props: { variants: [variant] } })
    expect(w.findAll('tbody tr')).toHaveLength(1)
  })
})
