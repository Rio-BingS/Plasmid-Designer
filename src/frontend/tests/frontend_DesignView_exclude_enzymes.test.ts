/**
 * 设计页「排除限制酶位点」的适用范围
 *
 * 排除只能靠同义密码子实现：只对「氨基酸输入 + 启用密码子优化 + 全基因合成」
 * 有意义。DNA 输入或关闭优化时必须既不展示、也不提交该参数——避免用户以为
 * 已经避开了位点，或误以为该参数会作用到载体上。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { nextTick } from 'vue'
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

import { submitDesign, getVectors, getCodonTables, getEnzymes } from '@/api'

async function mountView() {
  vi.mocked(getVectors).mockResolvedValue([{ id: 'pET-28a', name: 'pET-28a(+)' } as any])
  vi.mocked(getCodonTables).mockResolvedValue([] as any)
  vi.mocked(getEnzymes).mockResolvedValue({ total: 0, enzymes: {}, golden_gate_enzymes: [] } as any)
  vi.mocked(submitDesign).mockResolvedValue({ design_id: 'design_x', status: 'pending' } as any)
  const wrapper = mount(DesignView, {
    global: {
      plugins: [createPinia()],
      stubs: { 'router-link': { template: '<a><slot /></a>', props: ['to'] } }
    }
  })
  await flushPromises()
  return wrapper
}

describe('DesignView 排除限制酶位点的适用范围', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    sessionStorage.clear()
  })

  it('DNA 输入 + 全基因合成：即使选了排除酶也不提交', async () => {
    const wrapper = await mountView()
    const vm = wrapper.vm as any
    vm.sequenceType = 'dna'
    vm.sequence = 'ATGGAATTCTAA'
    vm.insertSource = 'gene_synthesis'
    vm.excludeEnzymes = ['EcoRI']

    await vm.handleSubmit()
    await flushPromises()

    const req = vi.mocked(submitDesign).mock.calls[0][0]
    expect(req.exclude_enzymes).toBeUndefined()
  })

  it('氨基酸输入但关闭密码子优化：即使选了排除酶也不提交', async () => {
    const wrapper = await mountView()
    const vm = wrapper.vm as any
    vm.sequenceType = 'amino_acid'
    vm.sequence = 'MKVLWAALLTF'
    vm.optimizeCodons = false
    vm.insertSource = 'gene_synthesis'
    vm.excludeEnzymes = ['BamHI']

    await vm.handleSubmit()
    await flushPromises()

    const req = vi.mocked(submitDesign).mock.calls[0][0]
    expect(req.exclude_enzymes).toBeUndefined()
  })

  it('氨基酸 + 密码子优化 + 全基因合成：正常提交排除酶', async () => {
    const wrapper = await mountView()
    const vm = wrapper.vm as any
    vm.sequenceType = 'amino_acid'
    vm.sequence = 'MKVLWAALLTF'
    vm.optimizeCodons = true
    vm.insertSource = 'gene_synthesis'
    vm.excludeEnzymes = ['BamHI', 'EcoRI']

    await vm.handleSubmit()
    await flushPromises()

    const req = vi.mocked(submitDesign).mock.calls[0][0]
    expect(req.exclude_enzymes).toEqual(['BamHI', 'EcoRI'])
  })

  it('不适用时隐藏选择器，适用时显示', async () => {
    const wrapper = await mountView()
    const vm = wrapper.vm as any
    vm.insertSource = 'gene_synthesis'
    vm.sequenceType = 'dna'
    await nextTick()
    expect(wrapper.text()).not.toContain('排除的限制酶位点')

    vm.sequenceType = 'amino_acid'
    vm.optimizeCodons = false
    await nextTick()
    expect(wrapper.text()).not.toContain('排除的限制酶位点')

    vm.optimizeCodons = true
    await nextTick()
    expect(wrapper.text()).toContain('排除的限制酶位点')
  })
})
