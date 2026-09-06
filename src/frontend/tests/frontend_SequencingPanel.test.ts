import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import SequencingPanel from '@/components/SequencingPanel.vue'
import type { SequencingAnalysis } from '@/api'

vi.mock('@/api', () => ({
  analyzeSequencingFiles: vi.fn(),
  getReadTrace: vi.fn(),
  exportConsensus: vi.fn()
}))

import { analyzeSequencingFiles } from '@/api'

const mockAnalysis: SequencingAnalysis = {
  analysis_id: 'seq_test1',
  sample_name: 'my_construct',
  created_at: '2026-01-01T00:00:00',
  engine: 'internal+biopython',
  conclusion: '共检出 1 处差异（覆盖 12.0%）',
  reads: [{
    index: 0, filename: 'r1.ab1', raw_length: 500, trimmed_length: 480,
    mean_q: 38, direction: '+', ref_start: 100, ref_end: 580,
    identity: 0.998, mixed_positions: []
  }],
  variants: [{
    ref_pos: 200, type: 'substitution', ref_base: 'A', alt_base: 'G', length: 1,
    read_q: 40, support_reads: 1, features: [{ name: 'GFP', type: 'CDS' }],
    aa_change: 'GFP:K5E', frameshift: false,
    enzyme_sites_lost: ['EcoRI'], enzyme_sites_gained: []
  }],
  consensus: { sequence: 'ACGT'.repeat(25), covered_ranges: [[1, 500]], coverage_percent: 12 },
  coverage_ranges: [[100, 580]],
  mixed_detected: {},
  errors: [],
  reference_length: 5000,
  features: []
}

function makeFile(name: string, content = 'x'): File {
  return new File([content], name, { type: 'application/octet-stream' })
}

describe('SequencingPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders upload area initially with nothing staged', () => {
    const wrapper = mount(SequencingPanel)
    expect(wrapper.text()).toContain('Sanger 测序结果验证')
    expect(wrapper.text()).toContain('开始自动分析')
    expect(wrapper.text()).toContain('尚未选择文件')
    expect(wrapper.find('.analyze-btn').attributes('disabled')).toBeDefined()
  })

  it('classifies mixed folder files into reference / reads / ignored', async () => {
    const wrapper = mount(SequencingPanel)
    ;(wrapper.vm as any).addFiles([
      makeFile('r1.ab1'),
      makeFile('construct.gb'),
      makeFile('notes.txt'),
      makeFile('second.dna'),
      makeFile('r2.ab1'),
    ])
    await wrapper.vm.$nextTick()

    const text = wrapper.text()
    expect(text).toContain('construct.gb')      // 参考序列
    expect(text).toContain('测序文件 × 2')       // 两个 .ab1
    expect(text).toContain('r1.ab1')
    expect(text).toContain('r2.ab1')
    expect(text).toContain('已忽略无关文件')      // notes.txt
    expect(text).toContain('已忽略多余的参考文件') // second.dna
    // 参考与 reads 齐备，不应出现缺失提示，分析按钮可用
    expect(text).not.toContain('还差')
    expect(wrapper.find('.analyze-btn').attributes('disabled')).toBeUndefined()
  })

  it('stages initialReference prop automatically (deep link)', async () => {
    const ref = makeFile('design_x.gb')
    const wrapper = mount(SequencingPanel, { props: { initialReference: ref } })
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toContain('design_x.gb')
    expect(wrapper.text()).toContain('还差 .ab1 测序文件')
  })

  it('runs analysis with reference + reads via generic endpoint', async () => {
    vi.mocked(analyzeSequencingFiles).mockResolvedValue(mockAnalysis)
    const wrapper = mount(SequencingPanel)
    ;(wrapper.vm as any).addFiles([makeFile('construct.gb'), makeFile('r1.ab1')])
    ;(wrapper.vm as any).runAnalysis()
    await flushPromises()

    expect(analyzeSequencingFiles).toHaveBeenCalledTimes(1)
    const [ref, reads, minQ, decompose] = vi.mocked(analyzeSequencingFiles).mock.calls[0]
    expect(ref.name).toBe('construct.gb')
    expect(reads.map((f: File) => f.name)).toEqual(['r1.ab1'])
    expect(minQ).toBe(20)
    expect(decompose).toBe(true)

    const text = wrapper.text()
    expect(text).toContain('共检出 1 处差异')
    expect(text).toContain('EcoRI')
    expect(text).toContain('GFP:K5E')
    expect(text).toContain('导出 FASTA')
  })

  it('shows historical analysis when preset injected', async () => {
    const wrapper = mount(SequencingPanel, { props: { preset: mockAnalysis } })
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toContain('共检出 1 处差异')
  })
})
