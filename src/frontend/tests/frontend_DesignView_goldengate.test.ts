/**
 * 回归：单条设计页的 Golden Gate 酶回退必须落在 Type IIS 候选内
 *
 * 此前默认 BsaI 不在酶表时退到全酶表首项（如 EcoRI），Golden Gate 提交即 422。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import DesignView from '@/views/DesignView.vue'

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() })
}))

vi.mock('@/api', async (importOriginal) => ({
  submitDesign: vi.fn(),
  getVectors: vi.fn(),
  getCodonTables: vi.fn(),
  getEnzymes: vi.fn(),
  formatApiError: ((await importOriginal()) as typeof import('@/api')).formatApiError
}))

import { submitDesign, getVectors, getCodonTables, getEnzymes } from '@/api'

describe('DesignView Golden Gate 酶', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getVectors).mockResolvedValue([{ id: 'pET-28a', name: 'pET-28a(+)' } as any])
    vi.mocked(getCodonTables).mockResolvedValue([] as any)
  })

  it('BsaI 不在酶表时回退到 Golden Gate 候选首项而非全酶表首项', async () => {
    vi.mocked(getEnzymes).mockResolvedValue({
      total: 2,
      enzymes: {
        EcoRI: { recognition_sequence: 'GAATTC', cut_type: 'sticky', is_type_iis: false },
        BsmBI: { recognition_sequence: 'CGTCTC', cut_type: 'sticky', is_type_iis: true }
      },
      golden_gate_enzymes: [{ name: 'BsmBI', recognition_sequence: 'CGTCTC' }]
    } as any)
    const wrapper = mount(DesignView, {
      global: { stubs: { 'router-link': { template: '<a><slot /></a>', props: ['to'] } } }
    })
    await flushPromises()
    expect((wrapper.vm as any).enzyme).toBe('BsmBI')
  })

  it('后端 422（数组 detail）展示为可读文案', async () => {
    vi.mocked(getEnzymes).mockResolvedValue({ total: 0, enzymes: {} } as any)
    vi.mocked(submitDesign).mockRejectedValue({
      response: { data: { detail: [{ loc: ['body'], msg: 'Value error, 不支持的 Golden Gate 酶 EcoRI' }] } }
    })
    const wrapper = mount(DesignView, {
      global: { stubs: { 'router-link': { template: '<a><slot /></a>', props: ['to'] } } }
    })
    await flushPromises()
    const vm = wrapper.vm as any
    vm.sequence = 'MKVLWAALLTFLGCAATSG'
    vm.cloningMethod = 'golden_gate'
    await vm.handleSubmit()
    await flushPromises()
    expect(vm.error).toContain('不支持的 Golden Gate 酶 EcoRI')
    expect(vm.error).not.toContain('[object Object]')
  })
})
