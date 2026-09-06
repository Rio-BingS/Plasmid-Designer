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

  it('user reference file replaces the deep-link auto-filled one', async () => {
    const auto = makeFile('pET-21a.gb')
    const wrapper = mount(SequencingPanel, { props: { initialReference: auto } })
    await wrapper.vm.$nextTick()

    ;(wrapper.vm as any).addFiles([makeFile('my_ref.gb'), makeFile('a.ab1')])
    await wrapper.vm.$nextTick()

    // 用户文件里的参考直接顶掉深链带入的，不再要求手动清除
    expect(wrapper.text()).toContain('my_ref.gb')
    expect(wrapper.text()).toContain('已用文件里的参考替换深链带入的 pET-21a.gb')
    expect(wrapper.text()).not.toContain('已忽略多余的参考文件')
    expect(wrapper.text()).not.toContain('还差')
  })

  it('clears auto-filled reference when deep link disappears, keeps manual one', async () => {
    const wrapper = mount(SequencingPanel, { props: { initialReference: makeFile('pET-21a.gb') } })
    await wrapper.vm.$nextTick()
    await wrapper.setProps({ initialReference: null })
    await wrapper.vm.$nextTick()
    // 深链带入的参考随参数消失清空，回到空白状态
    expect(wrapper.text()).toContain('尚未选择文件')
    expect(wrapper.text()).not.toContain('pET-21a.gb')

    // 手动选择的参考不受影响
    const w2 = mount(SequencingPanel)
    ;(w2.vm as any).addFiles([makeFile('mine.gb')])
    await w2.vm.$nextTick()
    await w2.setProps({ initialReference: null })
    await w2.vm.$nextTick()
    expect(w2.text()).toContain('mine.gb')
    expect(w2.text()).not.toContain('还差参考序列文件')
  })

  it('renders SnapGene-style match map with feature arrows and diff dots', async () => {
    const analysis = {
      ...mockAnalysis,
      reads: [
        { ...mockAnalysis.reads[0], alignment_view: makeAlignmentView() },
        { ...mockAnalysis.reads[0], index: 1, filename: 'r2.ab1', direction: '-', ref_start: 300, ref_end: 400, alignment_view: makeAlignmentView() },
      ],
      variants: [{ ...mockAnalysis.variants[0], ref_pos: 106, read: 'r1.ab1', read_pos: 6 }],
      features: [
        { name: 'MX', type: 'CDS', start: 120, end: 1180, strand: '+' },
        { name: 'lacI', type: 'CDS', start: 2600, end: 3500, strand: '-' },
        { name: 'T7 promoter', type: 'promoter', start: 4923, end: 4938, strand: '+' },
      ],
    }
    const wrapper = mount(SequencingPanel, { props: { preset: analysis } })
    await wrapper.vm.$nextTick()

    expect(wrapper.find('.map-box').exists()).toBe(true)
    // read 箭头一正一反；长 read 名字画进箭头内，短 read 退回左侧栏
    expect(wrapper.findAll('.map-arrow-fwd').length).toBe(1)
    expect(wrapper.findAll('.map-arrow-rev').length).toBe(1)
    expect(wrapper.findAll('.map-read-name').length).toBe(1)
    expect(wrapper.findAll('.map-label').filter(t => t.text().includes('r2.ab1')).length).toBe(1)
    // 每条 read 的对齐视图各有 1 处错配 → 各 1 个差异点
    expect(wrapper.findAll('.map-dot').length).toBe(2)
    // 轴上方的合并变异红块
    expect(wrapper.findAll('.map-var-tick').length).toBe(1)
    // 参考特征箭头：名字放得下的画在箭头内，放不下的引线外置
    expect(wrapper.findAll('.map-feat path').length).toBe(3)
    expect(wrapper.findAll('.map-feat-label').length).toBe(2)
    expect(wrapper.findAll('.map-feat-out').length).toBe(1)
    // 自适应刻度数字
    expect(wrapper.findAll('.map-tick-num').length).toBeGreaterThan(0)

    // 点击 read 箭头 → 比对校验切换到该 read
    await wrapper.find('.map-arrow-rev').trigger('click')
    await wrapper.vm.$nextTick()
    expect((wrapper.vm as any).alignReadIdx).toBe(1)
  })

  it('renders CDS-level sequencing verdicts with coverage badges', async () => {
    const analysis = {
      ...mockAnalysis,
      cds_reports: [
        {
          name: 'MX', start: 5007, end: 6929, strand: '+',
          covered_percent: 100, coverage_status: 'full',
          ref_protein_length: 640, alt_protein_length: 640,
          protein_identical: true, premature_stop_aa: null,
          frameshift_count: 0, aa_changes: [],
          consequences: ['synonymous_variant'], synonymous_count: 2,
          verdict: 'CDS 完整覆盖，翻译产物与参考一致（640 aa）',
        },
        {
          name: 'KanR', start: 1200, end: 2066, strand: '-',
          covered_percent: 62.5, coverage_status: 'partial',
          ref_protein_length: 288, alt_protein_length: 271,
          protein_identical: false, premature_stop_aa: 33,
          frameshift_count: 2, aa_changes: ['F11S', 'G12D', 'Y13T', 'A14L', 'F15S', 'T16P'],
          consequences: ['stop_gained', 'frameshift_variant'], synonymous_count: 0,
          verdict: '翻译产物与参考不一致；移码 2 处',
        },
      ],
    }
    const wrapper = mount(SequencingPanel, { props: { preset: analysis } })
    await wrapper.vm.$nextTick()

    expect(wrapper.text()).toContain('编码区（CDS）测序结论')
    expect(wrapper.findAll('.cds-dot.pass').length).toBe(1)
    expect(wrapper.findAll('.cds-dot.fail').length).toBe(1)
    // SO 标准后果徽章（按影响分级着色）
    expect(wrapper.find('.cds-so.high').text()).toBe('无义突变')
    expect(wrapper.findAll('.cds-so.high').length).toBe(2)
    expect(wrapper.find('.cds-so.low').text()).toBe('同义')
    expect(wrapper.find('.cds-cov.full').text()).toBe('完整覆盖')
    expect(wrapper.find('.cds-cov.partial').text()).toBe('覆盖 62.5%')
    const detail = wrapper.find('.cds-detail').text()
    expect(detail).toContain('提前终止于第 33 aa')
    expect(detail).toContain('移码 2 处')
    expect(detail).toContain('蛋白长度 288 → 271 aa')
    expect(detail).toContain('F11S、G12D、Y13T、A14L、F15S…')

    // 没有 CDS 报告时整卡隐藏（如 FASTA 参考）
    const w2 = mount(SequencingPanel, { props: { preset: mockAnalysis } })
    await w2.vm.$nextTick()
    expect(w2.text()).not.toContain('编码区（CDS）测序结论')
  })

  it('shows read quality grades, variant confidence and coverage gaps', async () => {
    const analysis = {
      ...mockAnalysis,
      reads: [{ ...mockAnalysis.reads[0], grade: 'A', q20_ratio: 0.98 }],
      variants: [{ ...mockAnalysis.variants[0], confidence: 'low' }],
      coverage_gaps: [{ start: 600, end: 4900, length: 4301 }, { start: 4950, end: 5000, length: 51 }],
    }
    const wrapper = mount(SequencingPanel, { props: { preset: analysis } })
    await wrapper.vm.$nextTick()

    // read 表质量评级（含 Q20 tooltip）
    expect(wrapper.find('.grade-chip').text()).toBe('A')
    expect(wrapper.find('.grade-chip').attributes('title')).toContain('98%')
    // 变异表置信度分级
    // happy-dom 的 querySelector 对复合类选择器（.a.b）不可靠，用单类选择器断言
    expect(wrapper.find('.conf-low').text()).toBe('低')
    // 覆盖缺口提示（按长度排序，取最长 3 段）
    const gaps = wrapper.find('.coverage-gaps').text()
    expect(gaps).toContain('覆盖缺口 2 段')
    expect(gaps).toContain('600-4900（4301bp）')
  })

  it('shows peak evidence in confidence tooltip and pending CDS chip', async () => {
    const analysis = {
      ...mockAnalysis,
      variants: [{
        ...mockAnalysis.variants[0],
        confidence: 'high',
        peak_evidence: { mutant_pct: 99.6, snr: 250 },
      }],
      cds_reports: [{
        ...mockAnalysis.cds_reports?.[0],
        name: 'MX', start: 5075, end: 6877, strand: '+',
        coverage_status: 'full', covered_percent: 100,
        ref_protein_length: 600, alt_protein_length: 600, protein_identical: true,
        premature_stop_aa: null, frameshift_count: 0, aa_changes: [],
        consequences: [], synonymous_count: 1,
        pending_low_confidence: 5,
        verdict: 'CDS 完整覆盖，翻译产物与参考一致；另有 5 处低置信变异未计入判定（经核对其不改变翻译产物判定），建议人工核对峰图',
      }],
    }
    const wrapper = mount(SequencingPanel, { props: { preset: analysis } })
    await wrapper.vm.$nextTick()

    // 置信度 tooltip 附峰级证据
    expect(wrapper.find('.conf-high').attributes('title')).toContain('突变峰占比 99.6%')
    expect(wrapper.find('.conf-high').attributes('title')).toContain('信噪比 250')
    // CDS 卡：绿色（一致）+ 待复核徽章
    expect(wrapper.findAll('.cds-dot.pass').length).toBe(1)
    const pending = wrapper.findAll('.cds-so.mid').find((w) => w.text().includes('待复核'))
    expect(pending).toBeTruthy()
    expect(pending!.text()).toContain('5 处')
    expect(wrapper.find('.cds-verdict').text()).toContain('未计入判定')
  })

  it('shows insertion peak ratio in confidence tooltip', async () => {
    const analysis = {
      ...mockAnalysis,
      variants: [{
        ...mockAnalysis.variants[0],
        type: 'insertion', alt_base: 'C', confidence: 'medium',
        peak_evidence: { insertion_peak_ratio: 0.69 },
      }],
    }
    const wrapper = mount(SequencingPanel, { props: { preset: analysis } })
    await wrapper.vm.$nextTick()
    const chip = wrapper.find('.conf-medium')
    expect(chip.text()).toBe('中')
    expect(chip.attributes('title')).toContain('插入峰强度为邻峰的 69%')
    expect(chip.attributes('title')).toContain('真实存在')
  })
})
