import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import ConsensusCard from '@/components/sequencing/ConsensusCard.vue'
import type { SequencingAnalysis } from '@/api'

// 参考 20 bp，read 只测了 6–15；全长共识的其余位置是参考填充
const full = 'AAAAACCCCCGGGGGTTTTT'
const analysis = {
  consensus: {
    sequence: full,
    covered_ranges: [[6, 15]],
    coverage_percent: 50,
    diffs: [],
    segments: [{ cons_start: 6, cons_end: 15, ref_start: 6, ref_end: 15, sequence: 'CCCCCGGGGG' }],
    assembled_length: 10,
  },
} as unknown as SequencingAnalysis

describe('ConsensusCard 拼接结果', () => {
  it('默认只显示 read 实测拼出的片段，不显示参考填充', () => {
    const w = mount(ConsensusCard, { props: { analysis } })
    const text = w.findAll('.consensus-pre').map((p) => p.text()).join('')
    expect(text).toBe('CCCCCGGGGG')
    expect(w.find('.section-title').text()).toContain('实测 10 bp')
    expect(w.find('.cons-seg-title').text()).toContain('参考 6–15')
  })

  it('关闭「仅实测覆盖区」显示全长，未测位置标为参考填充', async () => {
    const w = mount(ConsensusCard, { props: { analysis, coveredOnly: false } })
    expect(w.find('.consensus-pre').text()).toBe(full)
    const filled = w.findAll('.cons-filled').map((s) => s.text())
    expect(filled).toEqual(['AAAAA', 'TTTTT'])
  })

  it('旧记录没有 segments 时按原样显示全长', () => {
    const old = { consensus: { sequence: full, covered_ranges: [], coverage_percent: 0 } } as unknown as SequencingAnalysis
    const w = mount(ConsensusCard, { props: { analysis: old } })
    expect(w.find('.consensus-pre').text()).toBe(full)
    expect(w.findAll('.cons-filled').length).toBe(0)
  })
})
