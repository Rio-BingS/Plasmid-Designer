import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createWebHistory } from 'vue-router'
import SequencingView from '@/views/SequencingView.vue'
import SequencingPanel from '@/components/SequencingPanel.vue'

vi.mock('@/api', () => ({
  fetchDesignGenbankFile: vi.fn(),
  fetchVectorGenbankFile: vi.fn(),
  listSequencingAnalyses: vi.fn().mockResolvedValue([]),
  getSequencingAnalysis: vi.fn(),
  deleteSequencingAnalysis: vi.fn(),
}))

import { fetchDesignGenbankFile, fetchVectorGenbankFile } from '@/api'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/sequencing', component: SequencingView },
    { path: '/', component: { template: '<div />' } },
  ],
})

function mountView() {
  return mount(SequencingView, {
    global: {
      plugins: [router],
      // 面板只验证挂载与 props 传递，内部逻辑由 SequencingPanel 自己的用例覆盖
      stubs: { SequencingPanel: true },
    },
  })
}

describe('SequencingView', () => {
  beforeEach(async () => {
    vi.clearAllMocks()
    await router.push('/sequencing')
    await router.isReady()
  })

  it('直接打开即显示分析面板，无参考选择区', () => {
    const wrapper = mountView()
    expect(wrapper.findComponent(SequencingPanel).exists()).toBe(true)
    expect(wrapper.find('.history-card').exists()).toBe(true)
    // 旧版「选择参考序列」区块应已移除
    expect(wrapper.find('.mode-tabs').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('输入设计任务 ID')
  })

  it('深链 mode=design&ref=… 自动带入设计 GenBank 作为参考', async () => {
    const refFile = new File(['LOCUS'], 'design_x.gb')
    vi.mocked(fetchDesignGenbankFile).mockResolvedValue(refFile)
    await router.push({ path: '/sequencing', query: { mode: 'design', ref: 'design_x' } })
    const wrapper = mountView()
    await flushPromises()

    expect(fetchDesignGenbankFile).toHaveBeenCalledWith('design_x')
    const panel = wrapper.findComponent(SequencingPanel)
    expect((panel.props('initialReference') as File | null)?.name).toBe('design_x.gb')
    expect(wrapper.text()).not.toContain('未能自动带入参考序列')
  })

  it('深链 mode=vector&ref=… 走载体 GenBank 带入', async () => {
    const refFile = new File(['LOCUS'], 'pET-28a.gb')
    vi.mocked(fetchVectorGenbankFile).mockResolvedValue(refFile)
    await router.push({ path: '/sequencing', query: { mode: 'vector', ref: 'pET-28a' } })
    const wrapper = mountView()
    await flushPromises()

    expect(fetchVectorGenbankFile).toHaveBeenCalledWith('pET-28a')
    expect((wrapper.findComponent(SequencingPanel).props('initialReference') as File | null)?.name)
      .toBe('pET-28a.gb')
  })

  it('深链带入失败时给出提示且不阻塞手动上传', async () => {
    vi.mocked(fetchDesignGenbankFile).mockRejectedValue(new Error('gone'))
    await router.push({ path: '/sequencing', query: { mode: 'design', ref: 'design_gone' } })
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('未能自动带入参考序列')
    expect(wrapper.findComponent(SequencingPanel).exists()).toBe(true)
  })
})
