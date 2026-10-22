/**
 * 设计页「自有载体」接线：本地上传 / 在线导入的一次性载体随设计请求内联提交
 *
 * 此前设计页只有载体库下拉框：用户手里的 .dna/.gb 或在线来源的质粒无法参与设计。
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

import { submitDesign, getVectors, getCodonTables, getEnzymes } from '@/api'

const ADHOC = {
  id: 'adhoc_upload_pTEST',
  name: 'pTEST',
  source: 'genbank',
  sequence: 'ATGC'.repeat(150),
  topology: 'circular' as const,
  length: 600,
  features: [{ name: 'bla', type: 'CDS', start: 10, end: 90, strand: '+' as const }],
  mcs: { start: 158, end: 158, origin: 'inferred' as const, enzymes: ['BamHI'] },
  unique_enzymes: ['BamHI', 'EcoRI'],
  warnings: []
}

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

describe('DesignView 一次性载体', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('提交时把上传的载体内联进设计请求', async () => {
    const wrapper = await mountView()
    const vm = wrapper.vm as any
    vm.sequence = 'MKVLWAALLTF'
    vm.setVectorMode('upload')
    vm.adhocVector = ADHOC
    await vm.handleSubmit()
    await flushPromises()
    expect(submitDesign).toHaveBeenCalledTimes(1)
    const req = vi.mocked(submitDesign).mock.calls[0][0]
    expect(req.vector_id).toBe('adhoc_upload_pTEST')
    expect(req.vector).toEqual({
      id: 'adhoc_upload_pTEST',
      name: 'pTEST',
      sequence: ADHOC.sequence,
      topology: 'circular',
      features: ADHOC.features,
      source: 'genbank'
    })
  })

  it('只有载体库模式才发 vector_id 查库（不带 vector 字段）', async () => {
    const wrapper = await mountView()
    const vm = wrapper.vm as any
    vm.sequence = 'MKVLWAALLTF'
    await vm.handleSubmit()
    await flushPromises()
    const req = vi.mocked(submitDesign).mock.calls[0][0]
    expect(req.vector_id).toBe('pET-28a')
    expect(req.vector).toBeUndefined()
  })

  it('选了上传/在线模式但没选载体时拦在提交前', async () => {
    const wrapper = await mountView()
    const vm = wrapper.vm as any
    vm.sequence = 'MKVLWAALLTF'
    vm.setVectorMode('online')
    await vm.handleSubmit()
    await flushPromises()
    expect(submitDesign).not.toHaveBeenCalled()
    expect(vm.error).toContain('请先在线导入载体')
  })

  it('切换来源会清掉已选的一次性载体（切回载体库也一样）', async () => {
    const wrapper = await mountView()
    const vm = wrapper.vm as any
    vm.setVectorMode('upload')
    vm.adhocVector = ADHOC
    vm.setVectorMode('online')
    expect(vm.adhocVector).toBeNull()

    vm.setVectorMode('upload')
    vm.adhocVector = ADHOC
    vm.setVectorMode('library')
    expect(vm.adhocVector).toBeNull()
    // 回到载体库模式后提交的必须是库内载体
    vm.sequence = 'MKVLWAALLTF'
    await vm.handleSubmit()
    await flushPromises()
    const req = vi.mocked(submitDesign).mock.calls[0][0]
    expect(req.vector_id).toBe('pET-28a')
    expect(req.vector).toBeUndefined()
  })

  it('Gibson 定位酶候选来自一次性载体的唯一位点', async () => {
    const wrapper = await mountView()
    const vm = wrapper.vm as any
    expect(vm.currentVectorMcsEnzymes).toEqual([])
    vm.setVectorMode('online')
    vm.adhocVector = ADHOC
    expect(vm.currentVectorMcsEnzymes).toEqual(['BamHI', 'EcoRI'])
  })
})
