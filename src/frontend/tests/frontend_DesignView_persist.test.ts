/**
 * 设计页表单草稿 + 序列字符归一
 *
 * 用户报障（两项）：
 * 1. 氨基酸序列里的 * 无法被识别（中文输入法打出的是全角＊）；
 * 2. 跳到别的页面再回来，整张表单被清空。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import DesignView from '@/views/DesignView.vue'
import { DESIGN_DRAFT_KEY } from '@/utils/designDraft'

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
    global: { stubs: { 'router-link': { template: '<a><slot /></a>', props: ['to'] } } }
  })
  await flushPromises()
  return wrapper
}

describe('DesignView 表单草稿', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    sessionStorage.clear()
  })
  afterEach(() => { vi.useRealTimers() })

  it('重新进入页面时从草稿恢复输入与参数', async () => {
    sessionStorage.setItem(DESIGN_DRAFT_KEY, JSON.stringify({
      sequence: 'MKVLAW', sequenceName: 'myGene', sequenceType: 'amino_acid',
      cloningMethod: 'restriction', enzyme5: 'BamHI', enzyme3: 'XhoI',
      gcMin: 35, gcMax: 65, optimizeCodons: false, targetSpecies: 'human'
    }))
    const wrapper = await mountView()
    const vm = wrapper.vm as any
    expect(vm.sequence).toBe('MKVLAW')
    expect(vm.sequenceName).toBe('myGene')
    expect(vm.cloningMethod).toBe('restriction')
    expect(vm.enzyme3).toBe('XhoI')
    expect(vm.gcMin).toBe(35)
    expect(vm.optimizeCodons).toBe(false)
    expect(vm.targetSpecies).toBe('human')
  })

  it('坏草稿不会把页面带进非法状态', async () => {
    sessionStorage.setItem(DESIGN_DRAFT_KEY, '{"sequence": 42, "cloningMethod": "nope"}')
    const wrapper = await mountView()
    const vm = wrapper.vm as any
    expect(vm.sequence).toBe('')
    expect(vm.cloningMethod).toBe('gibson')
  })

  it('输完立刻跳走（卸载）也会把改动落盘', async () => {
    const wrapper = await mountView()
    const vm = wrapper.vm as any
    vm.sequence = 'MKVLW'
    wrapper.unmount()
    const raw = sessionStorage.getItem(DESIGN_DRAFT_KEY)
    expect(raw).toBeTruthy()
    expect(JSON.parse(raw as string).sequence).toBe('MKVLW')
  })

  it('清空表单同时清掉草稿', async () => {
    const wrapper = await mountView()
    const vm = wrapper.vm as any
    vm.sequence = 'MKVLW'
    vm.clearForm()
    expect(vm.sequence).toBe('')
    expect(sessionStorage.getItem(DESIGN_DRAFT_KEY)).toBeFalsy()
  })
})

describe('DesignView 序列字符归一', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    sessionStorage.clear()
  })

  it('全角＊在提交前归一为半角 *，随后端照常提交', async () => {
    const wrapper = await mountView()
    const vm = wrapper.vm as any
    vm.sequence = 'MKVLW＊'
    await vm.handleSubmit()
    await flushPromises()
    expect(submitDesign).toHaveBeenCalledTimes(1)
    const req = vi.mocked(submitDesign).mock.calls[0][0]
    expect(req.sequence).toBe('MKVLW*')
  })

  it('真正非法的字符直接拦在提交前，并指出位置', async () => {
    const wrapper = await mountView()
    const vm = wrapper.vm as any
    vm.sequence = 'MKV#W'
    await vm.handleSubmit()
    await flushPromises()
    expect(submitDesign).not.toHaveBeenCalled()
    expect(vm.error).toContain('#')
    expect(vm.error).toContain('第 4 位')
  })

  it('FASTA 分行的空白不算非法字符', async () => {
    const wrapper = await mountView()
    const vm = wrapper.vm as any
    vm.sequence = 'MKVLW\nAALLT\nFLGCA'
    await vm.handleSubmit()
    await flushPromises()
    expect(submitDesign).toHaveBeenCalledTimes(1)
    expect(vm.error).toBe('')
  })
})
