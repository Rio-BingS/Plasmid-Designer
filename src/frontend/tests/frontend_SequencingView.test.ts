import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createWebHistory } from 'vue-router'
import SequencingView from '@/views/SequencingView.vue'
import type { DesignResult } from '@/types'

vi.mock('@/api', () => ({
  getVectors: vi.fn().mockResolvedValue([]),
  getDesign: vi.fn(),
  listSequencingAnalyses: vi.fn().mockResolvedValue([]),
  getSequencingAnalysis: vi.fn(),
  deleteSequencingAnalysis: vi.fn(),
}))

import { getDesign } from '@/api'
import SequencingPanel from '@/components/SequencingPanel.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/sequencing', component: SequencingView },
    { path: '/', component: { template: '<div />' } },
  ],
})

const completedDesign: DesignResult = {
  design_id: 'design_abc123',
  status: 'completed',
  input_sequence: 'ATG',
  construct_sequence: 'ACGT'.repeat(1343),
  vector_id: 'pet-28a',
  vector_name: 'pET-28a',
  final_length: 5372,
  primers: [],
  cloning_method: 'restriction' as any,
  validation_passed: true,
  warnings: [],
  errors: [],
  created_at: '2026-09-06T10:00:00',
}

function mountView() {
  return mount(SequencingView, {
    global: {
      plugins: [router],
      // 面板只验证挂载与 props，内部逻辑由 SequencingPanel 自己的用例覆盖
      stubs: { SequencingPanel: true },
    },
  })
}

describe('SequencingView 参考序列选择', () => {
  beforeEach(async () => {
    vi.clearAllMocks()
    localStorage.clear()
    vi.mocked(getDesign).mockResolvedValue(completedDesign)
    await router.push('/sequencing')
    await router.isReady()
  })

  it('默认选中「设计结果」模式', () => {
    const wrapper = mountView()
    const active = wrapper.find('.mode-tabs button.active')
    expect(active.text()).toBe('设计结果')
    // 设计模式默认不展示分析面板
    expect(wrapper.find('.panel-card').exists()).toBe(false)
    expect(wrapper.text()).toContain('选择或输入一个已完成的设计')
  })

  it('切换到载体库模式仍可正常选择', async () => {
    const wrapper = mountView()
    const tabs = wrapper.findAll('.mode-tabs button')
    await tabs[1].trigger('click')
    await flushPromises()
    expect(wrapper.find('.mode-tabs button.active').text()).toBe('载体库')
    expect(wrapper.find('input[placeholder^="搜索载体名称"]').exists()).toBe(true)
  })

  it('最近设计卡片可选，选中后面板出现且 URL 带上 ref', async () => {
    localStorage.setItem('recent_designs_v1', JSON.stringify([{
      design_id: 'design_abc123',
      vector_name: 'pET-28a',
      length: 5372,
      time: '2026-09-06T10:00:00',
    }]))
    const wrapper = mountView()
    await flushPromises()

    const card = wrapper.find('.vector-card')
    expect(card.exists()).toBe(true)
    expect(card.text()).toContain('pET-28a')
    expect(card.text()).toContain('5372 bp')

    await card.trigger('click')
    await flushPromises()

    expect(wrapper.find('.panel-card').exists()).toBe(true)
    expect(wrapper.text()).toContain('参考：pET-28a · 5372 bp')
    expect(router.currentRoute.value.query.ref).toBe('design_abc123')
    const panel = wrapper.findComponent(SequencingPanel)
    expect(panel.props('mode')).toBe('design')
    expect(panel.props('referenceId')).toBe('design_abc123')
  })

  it('手输有效设计 ID 校验通过后面板出现，并写入最近设计', async () => {
    const wrapper = mountView()
    const input = wrapper.find('.design-row input')
    await input.setValue('design_abc123')
    await wrapper.find('.btn-confirm').trigger('click')
    await flushPromises()

    expect(getDesign).toHaveBeenCalledWith('design_abc123')
    expect(wrapper.find('.panel-card').exists()).toBe(true)
    expect(router.currentRoute.value.query.ref).toBe('design_abc123')

    const recent = JSON.parse(localStorage.getItem('recent_designs_v1') || '[]')
    expect(recent.some((d: any) => d.design_id === 'design_abc123')).toBe(true)
  })

  it('手输无效设计 ID 给出明确错误且不展示面板', async () => {
    vi.mocked(getDesign).mockRejectedValue({ response: { data: { detail: 'Design not found' } } })
    const wrapper = mountView()
    const input = wrapper.find('.design-row input')
    await input.setValue('design_nope')
    await wrapper.find('.btn-confirm').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('未找到该设计 ID')
    expect(wrapper.find('.panel-card').exists()).toBe(false)
  })

  it('深链 mode=design&ref=… 自动校验并直接展示面板', async () => {
    await router.push({ path: '/sequencing', query: { mode: 'design', ref: 'design_abc123' } })
    const wrapper = mountView()
    await flushPromises()

    expect(getDesign).toHaveBeenCalledWith('design_abc123')
    expect(wrapper.find('.panel-card').exists()).toBe(true)
    expect(wrapper.text()).toContain('参考：pET-28a · 5372 bp')
  })
})
