import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import SequencingPanel from '@/components/SequencingPanel.vue'
import type { SequencingAnalysis } from '@/api'

vi.mock('@/api', () => ({
  analyzeVectorSequencing: vi.fn(),
  analyzeDesignSequencing: vi.fn(),
  getReadTrace: vi.fn(),
  exportConsensus: vi.fn()
}))

import { analyzeVectorSequencing } from '@/api'

const mockAnalysis: SequencingAnalysis = {
  analysis_id: 'seq_test1',
  sample_name: 'pET-28a',
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

describe('SequencingPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders upload area initially', () => {
    const wrapper = mount(SequencingPanel, { props: { referenceId: 'v1', mode: 'vector' } })
    expect(wrapper.text()).toContain('Sanger 测序结果验证')
    expect(wrapper.text()).toContain('开始自动分析')
  })

  it('renders full analysis result', async () => {
    vi.mocked(analyzeVectorSequencing).mockResolvedValue(mockAnalysis)
    const wrapper = mount(SequencingPanel, { props: { referenceId: 'v1', mode: 'vector' } })

    // 直接调用内部状态：模拟上传后分析完成
    ;(wrapper.vm as any).analysis = await analyzeVectorSequencing('v1', [])
    await flushPromises()

    const text = wrapper.text()
    expect(text).toContain('共检出 1 处差异')
    expect(text).toContain('12%')          // 覆盖率
    expect(text).toContain('EcoRI')        // 破坏酶切位点
    expect(text).toContain('GFP:K5E')      // 氨基酸变化
    expect(text).toContain('导出 FASTA')    // 共识导出
  })
})
