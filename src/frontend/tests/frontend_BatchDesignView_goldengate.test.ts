/**
 * 回归：批量设计页选 Golden Gate 时必须带上 Type IIS 酶
 *
 * bug：页面从不发送 enzyme，而后端 Golden Gate 缺酶直接 422，整批提交必失败。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import BatchDesignView from '@/views/BatchDesignView.vue'

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() })
}))

vi.mock('@/api', async (importOriginal) => ({
  submitBatchDesign: vi.fn(),
  getBatchProgress: vi.fn(),
  downloadBatchResults: vi.fn(),
  getBatchReport: vi.fn(),
  getVectors: vi.fn(),
  getCodonTables: vi.fn(),
  getEnzymes: vi.fn(),
  // 错误文案走真实实现：界面展示的就是后端 detail 原文
  formatApiError: ((await importOriginal()) as typeof import('@/api')).formatApiError
}))

import {
  submitBatchDesign, getBatchProgress, getVectors, getCodonTables, getEnzymes
} from '@/api'

const ENZYMES = {
  total: 3,
  enzymes: {
    EcoRI: { recognition_sequence: 'GAATTC', cut_type: 'sticky', is_type_iis: false },
    BamHI: { recognition_sequence: 'GGATCC', cut_type: 'sticky', is_type_iis: false },
    BsaI: { recognition_sequence: 'GGTCTC', cut_type: 'sticky', is_type_iis: true },
    BsmBI: { recognition_sequence: 'CGTCTC', cut_type: 'sticky', is_type_iis: true }
  },
  golden_gate_enzymes: [
    { name: 'BsaI', recognition_sequence: 'GGTCTC' },
    { name: 'BsmBI', recognition_sequence: 'CGTCTC' }
  ]
}

async function mountView() {
  const wrapper = mount(BatchDesignView, {
    global: { stubs: { 'router-link': { template: '<a><slot /></a>', props: ['to'] } } }
  })
  await flushPromises()
  return wrapper
}

describe('BatchDesignView Golden Gate 酶', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getVectors).mockResolvedValue([{ id: 'pET-28a', name: 'pET-28a(+)' } as any])
    vi.mocked(getCodonTables).mockResolvedValue([] as any)
    vi.mocked(getEnzymes).mockResolvedValue(ENZYMES as any)
    vi.mocked(submitBatchDesign).mockResolvedValue({ batch_id: 'batch_x1', total: 1 })
    vi.mocked(getBatchProgress).mockResolvedValue({
      batch_id: 'batch_x1', total: 1, completed: 0, failed: 0, pending: 1,
      status: 'running', progress_percent: 0, results: [], errors: []
    })
  })

  it('Golden Gate 提交时带上默认 BsaI', async () => {
    const wrapper = await mountView()
    const vm = wrapper.vm as any
    vm.sequencesText = 'MKVLWAALLTFLGCAATSG'
    vm.cloningMethod = 'golden_gate'
    await flushPromises()

    await vm.handleSubmit()
    await flushPromises()

    expect(submitBatchDesign).toHaveBeenCalledTimes(1)
    const payload = vi.mocked(submitBatchDesign).mock.calls[0][0]
    expect(payload.cloning_method).toBe('golden_gate')
    expect(payload.enzyme).toBe('BsaI')
  })

  it('选择其它候选酶时按所选发送', async () => {
    const wrapper = await mountView()
    const vm = wrapper.vm as any
    vm.sequencesText = 'MKVLWAALLTFLGCAATSG'
    vm.cloningMethod = 'golden_gate'
    vm.enzyme = 'BsmBI'
    await flushPromises()

    await vm.handleSubmit()
    await flushPromises()

    expect(vi.mocked(submitBatchDesign).mock.calls[0][0].enzyme).toBe('BsmBI')
  })

  it('非 Golden Gate 方法不发送 enzyme（限制性克隆走双酶）', async () => {
    const wrapper = await mountView()
    const vm = wrapper.vm as any
    vm.sequencesText = 'MKVLWAALLTFLGCAATSG'
    vm.cloningMethod = 'restriction'
    await flushPromises()

    await vm.handleSubmit()
    await flushPromises()

    const payload = vi.mocked(submitBatchDesign).mock.calls[0][0]
    expect(payload.enzyme).toBeUndefined()
    expect(payload.enzyme_5).toBe('BamHI')
    expect(payload.enzyme_3).toBe('EcoRI')
  })

  it('后端候选不含 BsaI 时回退到候选首项', async () => {
    vi.mocked(getEnzymes).mockResolvedValue({
      total: 1,
      enzymes: { BbsI: { recognition_sequence: 'GAAGAC', cut_type: 'sticky', is_type_iis: true } },
      golden_gate_enzymes: [{ name: 'BbsI', recognition_sequence: 'GAAGAC' }]
    } as any)
    const wrapper = await mountView()
    const vm = wrapper.vm as any
    expect(vm.enzyme).toBe('BbsI')
  })

  it('后端 422（数组 detail）展示为可读文案', async () => {
    vi.mocked(submitBatchDesign).mockRejectedValue({
      response: { data: { detail: [{ loc: ['body'], msg: 'Value error, Golden Gate 需指定 Type IIS 酶（enzyme），如 BsaI/BsmBI/BbsI' }] } }
    })
    const wrapper = await mountView()
    const vm = wrapper.vm as any
    vm.sequencesText = 'MKVLWAALLTFLGCAATSG'
    vm.cloningMethod = 'golden_gate'
    await flushPromises()

    await vm.handleSubmit()
    await flushPromises()

    expect(vm.error).toContain('Golden Gate 需指定 Type IIS 酶')
    expect(vm.error).not.toContain('[object Object]')
  })
})
