/**
 * 批量设计模式切换（v2.29.1）：简单/复杂模式分离
 *
 * 简单 = 之前的方案（统一参数，行级参数忽略）；复杂 = 表格参数列逐行覆盖。
 * 默认简单模式；选择记忆在 localStorage。
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
  downloadBatchPrimers: vi.fn(),
  downloadBatchTemplate: vi.fn(),
  parseSpreadsheetFile: vi.fn(),
  getBatchReport: vi.fn(),
  getVectors: vi.fn(),
  getCodonTables: vi.fn(),
  getEnzymes: vi.fn(),
  formatApiError: ((await importOriginal()) as typeof import('@/api')).formatApiError
}))

import { submitBatchDesign, getVectors, getCodonTables, getEnzymes } from '@/api'

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  vi.mocked(getVectors).mockResolvedValue([{ id: 'pET-28a', name: 'pET-28a(+)' } as any])
  vi.mocked(getCodonTables).mockResolvedValue([] as any)
  vi.mocked(getEnzymes).mockResolvedValue({ total: 0, enzymes: {}, golden_gate_enzymes: [] } as any)
  vi.mocked(submitBatchDesign).mockResolvedValue({ batch_id: 'batch_m1', total: 1 })
})

async function mountView() {
  const wrapper = mount(BatchDesignView, {
    global: { stubs: { 'router-link': { template: '<a><slot /></a>', props: ['to'] } } }
  })
  await flushPromises()
  return wrapper
}

describe('批量设计模式切换', () => {
  it('默认简单模式，切换按钮存在', async () => {
    const wrapper = await mountView()
    const vm = wrapper.vm as any
    expect(vm.designMode).toBe('simple')
    const btns = wrapper.findAll('.mode-btn')
    expect(btns).toHaveLength(2)
    expect(btns[0].text()).toBe('简单批量')
    expect(btns[0].classes()).toContain('active')
    expect(btns[1].classes()).not.toContain('active')
  })

  it('简单模式：即使行带参数也不发 row_params', async () => {
    const wrapper = await mountView()
    const vm = wrapper.vm as any
    vm.sequencesText = '>g1\nMGEKTKTEKTKTEKTKTEKT'
    vm.spreadsheetRows = [
      { name: 'g2', sequence: 'MGDYKDDDDKGGGG', params: { cloning_method: 'gibson' } },
    ]
    await flushPromises()
    await vm.handleSubmit()
    await flushPromises()

    const payload = vi.mocked(submitBatchDesign).mock.calls[0][0]
    expect(payload.row_params).toBeUndefined()
    expect(payload.sequences).toHaveLength(2)   // 表格行 + 文本行都在
  })

  it('复杂模式：行参数作为 row_params 发送', async () => {
    const wrapper = await mountView()
    const vm = wrapper.vm as any
    vm.setDesignMode('complex')
    vm.sequencesText = '>g1\nMGEKTKTEKTKTEKTKTEKT'
    vm.spreadsheetRows = [
      { name: 'g2', sequence: 'MGDYKDDDDKGGGG', params: { cloning_method: 'gibson' } },
    ]
    await flushPromises()
    await vm.handleSubmit()
    await flushPromises()

    const payload = vi.mocked(submitBatchDesign).mock.calls[0][0]
    expect(payload.row_params).toEqual([
      { cloning_method: 'gibson' },           // 表格行（预览表中在前）
      null,                                   // 文本行：继承批级
    ])
  })

  it('模式选择记忆在 localStorage', async () => {
    localStorage.setItem('plasmid-batch-design-mode', 'complex')
    const wrapper = await mountView()
    const vm = wrapper.vm as any
    expect(vm.designMode).toBe('complex')
    expect((wrapper.findAll('.mode-btn')[1].classes())).toContain('active')

    vm.setDesignMode('simple')
    expect(localStorage.getItem('plasmid-batch-design-mode')).toBe('simple')
  })

  it('简单模式下表格行带参数时显示忽略提示', async () => {
    const wrapper = await mountView()
    const vm = wrapper.vm as any
    vm.spreadsheetRows = [
      { name: 'g2', sequence: 'MGDYKDDDDKGGGG', params: { cloning_method: 'gibson' } },
    ]
    await flushPromises()
    expect(wrapper.find('.ignored-hint').exists()).toBe(true)

    vm.setDesignMode('complex')
    await flushPromises()
    expect(wrapper.find('.ignored-hint').exists()).toBe(false)
  })
})
