/**
 * 设计页双酶切：用户只选两个位点，方向交给后端
 *
 * 之前两个输入框叫「5′/3′ 端限制酶」，用户按载体上从左到右的顺序填，
 * 结果基因反着插进质粒、还能顺利导出——只有把 .gb 拖进 SnapGene 才看得出。
 * 现在表单只提交两把酶 + 一个方向开关（自动/反向/手动），顺序无关。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia } from 'pinia'
import DesignView from '@/views/DesignView.vue'

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() })
}))

vi.mock('@/api', async (importOriginal) => ({
  submitDesign: vi.fn(),
  getVectors: vi.fn(),
  getCodonTables: vi.fn(),
  getEnzymes: vi.fn(),
  getRestrictionPreview: vi.fn(),
  uploadDesignVector: vi.fn(),
  importDesignVectorOnline: vi.fn(),
  searchDesignVectorSnapgene: vi.fn(),
  getDesignVectorSources: vi.fn(),
  formatApiError: ((await importOriginal()) as typeof import('@/api')).formatApiError
}))

import { submitDesign, getVectors, getCodonTables, getEnzymes, getRestrictionPreview } from '@/api'

async function mountView() {
  vi.mocked(getVectors).mockResolvedValue([{ id: 'pET-28a', name: 'pET-28a(+)' } as any])
  vi.mocked(getCodonTables).mockResolvedValue([] as any)
  vi.mocked(getEnzymes).mockResolvedValue({ total: 0, enzymes: {}, golden_gate_enzymes: [] } as any)
  vi.mocked(submitDesign).mockResolvedValue({ design_id: 'design_x', status: 'pending' } as any)
  vi.mocked(getRestrictionPreview).mockResolvedValue({
    vector: { name: 'pET-28a(+)', length: 5369, topology: 'circular' },
    sites: [
      { role: 'three', enzyme: 'XhoI', position: 158, cut: 158, end: 163 },
      { role: 'five', enzyme: 'BamHI', position: 198, cut: 198, end: 203 },
    ],
    gene: { start: 164, end: 223, length: 60, estimated: false },
    removed_bp: 40,
    construct_length: 5395,
    strand: '-',
    source: 'annotation',
    ambiguous: false,
    gene_5prime_enzyme: 'BamHI',
    gene_3prime_enzyme: 'XhoI',
    evidence: [{ kind: 'promoter', side: '-', name: 'T7 promoter', gap: 170 }],
    features: [],
    junctions: { five: 'TCGAGATG', three: 'TAA' },
    warnings: [],
  } as any)
  const wrapper = mount(DesignView, {
    global: {
      plugins: [createPinia()],
      stubs: { 'router-link': { template: '<a><slot /></a>', props: ['to'] } }
    }
  })
  await flushPromises()
  return wrapper
}

async function setupRestriction() {
  const wrapper = await mountView()
  const vm = wrapper.vm as any
  vm.cloningMethod = 'restriction'
  vm.sequenceType = 'amino_acid'
  vm.sequence = 'MKVLWAALLTF'
  vm.enzyme5 = 'BamHI'
  vm.enzyme3 = 'XhoI'
  await flushPromises()
  return wrapper
}

describe('DesignView 双酶切方向', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    sessionStorage.clear()
  })

  it('默认把方向交给后端（auto）', async () => {
    const wrapper = await setupRestriction()
    await (wrapper.vm as any).handleSubmit()
    await flushPromises()
    const req = vi.mocked(submitDesign).mock.calls[0][0] as any
    expect(req.enzyme_5).toBe('BamHI')
    expect(req.enzyme_3).toBe('XhoI')
    expect(req.insert_direction).toBe('auto')
  })

  it('勾选反向 → reverse（反义构建）', async () => {
    const wrapper = await setupRestriction()
    ;(wrapper.vm as any).reverseInsert = true
    await (wrapper.vm as any).handleSubmit()
    await flushPromises()
    expect((vi.mocked(submitDesign).mock.calls[0][0] as any).insert_direction).toBe('reverse')
  })

  it('手动指定负链 → minus', async () => {
    const wrapper = await setupRestriction()
    ;(wrapper.vm as any).manualStrand = 'minus'
    await (wrapper.vm as any).handleSubmit()
    await flushPromises()
    expect((vi.mocked(submitDesign).mock.calls[0][0] as any).insert_direction).toBe('minus')
  })

  it('预览请求带上两把酶与估算的基因长度', async () => {
    await setupRestriction()
    await new Promise((r) => setTimeout(r, 320))
    await flushPromises()
    expect(getRestrictionPreview).toHaveBeenCalled()
    const payload = vi.mocked(getRestrictionPreview).mock.calls.at(-1)![0] as any
    expect(payload.enzymes).toEqual(['BamHI', 'XhoI'])
    expect(payload.insert_length).toBe(33)  // 11 aa × 3
  })
})
