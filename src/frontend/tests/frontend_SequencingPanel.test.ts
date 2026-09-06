import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import SequencingPanel from '@/components/SequencingPanel.vue'
import type { SequencingAnalysis, AlignmentView, ReadTrace } from '@/api'

vi.mock('@/api', () => ({
  analyzeSequencingFiles: vi.fn(),
  getReadTrace: vi.fn(),
  exportConsensus: vi.fn()
}))

import { analyzeSequencingFiles, getReadTrace } from '@/api'

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

/** 100 列对齐：col 5 一处错配（参考 A → read G，Q=12 低质量） */
function makeAlignmentView(): AlignmentView {
  const ref = 'ACGTA'.repeat(20)
  const read = ref.split('')
  read[5] = 'G'
  return {
    ref_start: 101,
    ref_aligned: ref,
    read_aligned: read.join(''),
    q_aligned: Array.from({ length: 100 }, (_, i) => (i === 5 ? 12 : 40))
  }
}

const mockTrace: ReadTrace = {
  filename: 'r1.ab1',
  bases: 'ACGTACGTAC',
  quality: [40, 40, 40, 40, 40, 12, 40, 40, 40, 40],
  channels: { A: [], T: [], G: [], C: [] },
  peak_indices: []
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

  it('renders alignment view with mismatch and low-Q highlighting', async () => {
    const analysis = {
      ...mockAnalysis,
      reads: [{ ...mockAnalysis.reads[0], alignment_view: makeAlignmentView() }],
      variants: [{
        ...mockAnalysis.variants[0],
        ref_pos: 106, // col 5 → 参考位置 101+5
        read: 'r1.ab1',
        read_pos: 6,
      }],
    }
    const wrapper = mount(SequencingPanel, { props: { preset: analysis } })
    await wrapper.vm.$nextTick()

    expect(wrapper.text()).toContain('比对校验')
    // 唯一错配列红底高亮，且该列 Q<20 橙色提示
    expect(wrapper.findAll('.cell.mm').length).toBe(1)
    const qLow = wrapper.findAll('.cell.qLow')
    expect(qLow.length).toBeGreaterThanOrEqual(2) // read 行 + Q 行
    expect(wrapper.text()).toContain('红底 = 与参考不同')
    // 该 read 无峰图数据时比对区仍正常展示参考行
    expect(wrapper.find('.aln-scroll').exists()).toBe(true)
  })

  it('clicking a variant row focuses the alignment column and requests trace', async () => {
    vi.mocked(getReadTrace).mockResolvedValue(mockTrace)
    vi.stubGlobal('requestAnimationFrame', () => 0)
    const analysis = {
      ...mockAnalysis,
      reads: [{ ...mockAnalysis.reads[0], alignment_view: makeAlignmentView() }],
      variants: [{
        ...mockAnalysis.variants[0],
        ref_pos: 106,
        read: 'r1.ab1',
        read_pos: 6,
      }],
    }
    const wrapper = mount(SequencingPanel, { props: { preset: analysis } })
    await wrapper.vm.$nextTick()

    await wrapper.find('.seq-table.clickable tbody tr').trigger('click')
    await flushPromises()
    await wrapper.vm.$nextTick()

    expect(getReadTrace).toHaveBeenCalledWith('seq_test1', 0)
    // 参考行与 read 行的对应列同时获得焦点高亮
    expect(wrapper.findAll('.cell.focus').length).toBe(2)
    vi.unstubAllGlobals()
  })

  it('highlights consensus positions that differ from reference', async () => {
    const analysis = {
      ...mockAnalysis,
      consensus: {
        ...mockAnalysis.consensus,
        diffs: [{ ref_pos: 3, ref_base: 'G', cons_base: 'A', cons_index: 2 }],
      },
    }
    const wrapper = mount(SequencingPanel, { props: { preset: analysis } })
    await wrapper.vm.$nextTick()

    expect(wrapper.findAll('.cons-diff').length).toBe(1)
    expect(wrapper.text()).toContain('黄色高亮 = 共识序列与参考不同的位点')
  })

  it('shows fallback hint when a read has no alignment view', async () => {
    const wrapper = mount(SequencingPanel, { props: { preset: mockAnalysis } })
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toContain('该 read 无对齐数据')
  })

  it('clears all staged reads with one click', async () => {
    const wrapper = mount(SequencingPanel)
    ;(wrapper.vm as any).addFiles([makeFile('a.ab1'), makeFile('b.ab1'), makeFile('c.gb')])
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toContain('测序文件 × 2')

    await wrapper.find('.clear-btn').trigger('click')
    await wrapper.vm.$nextTick()

    // reads 全部清空、参考保留、回到缺 ab1 提示且按钮禁用
    expect(wrapper.text()).toContain('还差 .ab1 测序文件')
    expect(wrapper.text()).toContain('c.gb')
    expect(wrapper.find('.analyze-btn').attributes('disabled')).toBeDefined()
    expect(wrapper.find('.clear-btn').exists()).toBe(false)
  })

  it('clamps minQ into 0-60 before sending analysis request', async () => {
    vi.mocked(analyzeSequencingFiles).mockResolvedValue(mockAnalysis)
    const wrapper = mount(SequencingPanel)
    ;(wrapper.vm as any).addFiles([makeFile('c.gb'), makeFile('a.ab1')])
    await wrapper.vm.$nextTick()

    const input = wrapper.find('.advanced input[type="number"]')
    await input.setValue('100')
    await input.trigger('change')   // 失焦钳制到 60
    ;(wrapper.vm as any).runAnalysis()
    await flushPromises()

    const [, , minQ] = vi.mocked(analyzeSequencingFiles).mock.calls[0]
    expect(minQ).toBe(60)
  })
})
